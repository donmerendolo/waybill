from __future__ import annotations

from collections import defaultdict
from copy import copy
from dataclasses import replace

from apps.channels.models import Stream

from ._jinja import extract_template_variables
from .matchers import build_matcher, build_q_filter
from .matchers.base import WaybillMatcherBase
from .transformers import build_transformer
from .validators import build_validator
from .validators.base import (
    WaybillChannelValidatorBase,
    WaybillMemberValidatorBase,
    WaybillStreamValidatorBase,
)
from .types.config import (
    ConfigMember,
    ConfigProfile,
    ConfigVariable,
    OrderStreamsBy,
    WaybillConfig,
)
from .plan import assemble_member_plan
from .types.plan import (
    ChannelPlan,
    GroupPlan,
    DroppedRecord,
    MemberPlan,
    ProfilePlan,
    StreamRecord,
    TransformStep,
    ValidatorViolation,
    VariableEvent,
    WaybillPlan,
)

CHUNK_SIZE = 1000


class MemberPipeline:
    """Encapsulates matcher and transformer instances for a single ConfigMember."""

    def __init__(
        self,
        member: ConfigMember,
        inherited_stream_profile: "str | None" = None,
        inherited_order_streams_by: "OrderStreamsBy | None" = None,
        inherited_variables: "dict[str, ConfigVariable] | None" = None,
    ) -> None:
        self._member = member
        self._effective_stream_profile: str | None = (
            member.stream_profile or inherited_stream_profile
        )
        self._effective_order_streams_by: OrderStreamsBy | None = (
            member.order_streams_by or inherited_order_streams_by
        )
        # Outer scope variables; shadowed by member-level definitions.
        self._predefined_vars_config: dict[str, ConfigVariable] = {
            **(inherited_variables or {}),
            **member.variables,
        }
        self._matchers: list[WaybillMatcherBase] = [
            build_matcher(cfg) for cfg in member.matchers
        ]
        self._transformers = [build_transformer(cfg) for cfg in member.transformers]
        # Fields actually needed by matchers and their pre-transformers — drives .only() to keep ORM objects lean
        self._required_fields: set[str] = (
            {cfg.field for cfg in member.matchers}
            | {t.field for cfg in member.matchers for t in cfg.transformers}
            | {"id", "name", "tvg_id", "logo_url"}
        )
        if self._effective_order_streams_by is OrderStreamsBy.QUALITY:
            self._required_fields.add("stream_stats")
        self._stream_priorities: dict[str, int] = member.stream_priorities
        if self._stream_priorities:
            self._required_fields |= {"stream_hash", "url"}
        self._matcher_descs: list[str] = [m.describe() for m in self._matchers]
        self._transformer_descs: list[str] = [t.describe() for t in self._transformers]

        _all_validators = [build_validator(cfg) for cfg in member.validators]
        self._validator_descs: list[str] = [v.describe() for v in _all_validators]
        # Partition into stream-level and channel-level, preserving 1-based original indices
        self._stream_validator_pairs: list[
            tuple[WaybillStreamValidatorBase, str, int]
        ] = [
            (v, self._validator_descs[i], i + 1)
            for i, v in enumerate(_all_validators)
            if isinstance(v, WaybillStreamValidatorBase)
        ]
        self._channel_validator_pairs: list[
            tuple[WaybillChannelValidatorBase, str, int]
        ] = [
            (v, self._validator_descs[i], i + 1)
            for i, v in enumerate(_all_validators)
            if isinstance(v, WaybillChannelValidatorBase)
        ]
        self._member_validator_pairs: list[
            tuple[WaybillMemberValidatorBase, str, int]
        ] = [
            (v, self._validator_descs[i], i + 1)
            for i, v in enumerate(_all_validators)
            if isinstance(v, WaybillMemberValidatorBase)
        ]

    def required_fields(self) -> set[str]:
        return self._required_fields

    def _match_and_collect_captures(
        self,
        stream: "Stream",
        predefined_values: "dict[str, str]",
        matcher_descs: "list[str]",
    ) -> "tuple[bool, dict[str, str], dict[str, str], list[VariableEvent]]":
        """Run all matchers against *stream*, collecting named capture groups.

        Returns ``(True, declared_vars, acc_vars, events)`` when all matchers
        accept the stream.

        - ``declared_vars`` contains only captures whose group name matches a
          key declared in the member's resolved variables block (profile/group/
          member scope).  This is the dict passed to main transformers.
        - ``acc_vars`` is the full accumulated scope (predefined + **all**
          captures regardless of declaration).  It is threaded through
          subsequent matcher ``match_and_capture`` calls so pre-transformers
          can reference any earlier capture.
        - ``events`` is a list of :class:`VariableEvent` records for plan logging.

        Returns ``(False, {}, {}, [])`` as soon as any matcher rejects the
        stream.

        Raises ``ValueError`` if a capture group name collides with an
        immutable predefined variable.
        """
        events: list[VariableEvent] = []

        # Emit init events for all predefined variables.
        for name, value in predefined_values.items():
            events.append(
                VariableEvent(
                    name=name,
                    event_type="init",
                    value=value,
                    source="predefined",
                )
            )

        acc_vars: dict[str, str] = dict(predefined_values)

        for idx, matcher in enumerate(self._matchers, 1):
            accepted, captures = matcher.match_and_capture(stream, variables=acc_vars)
            if not accepted:
                return False, {}, {}, []
            for name, value in captures.items():
                existing = self._predefined_vars_config.get(name)
                if existing is not None and not existing.mutable:
                    raise ValueError(
                        f"Named capture group {name!r} conflicts with immutable "
                        f"predefined variable in member {self._member.name!r}"
                    )
                desc = (
                    matcher_descs[idx - 1]
                    if idx - 1 < len(matcher_descs)
                    else f"matcher #{idx}"
                )
                if name in self._predefined_vars_config:
                    events.append(
                        VariableEvent(
                            name=name,
                            event_type="capture_override",
                            value=value,
                            old_value=acc_vars.get(name),
                            source=f"matcher #{idx} ({desc})",
                        )
                    )
                else:
                    events.append(
                        VariableEvent(
                            name=name,
                            event_type="capture_discarded",
                            value=value,
                            source=f"matcher #{idx} ({desc})",
                        )
                    )
                acc_vars[name] = value

        # Only captures whose group name is declared in a variables block at any
        # scope level (profile/group/member) are forwarded to main transformers.
        declared_vars: dict[str, str] = {
            k: v for k, v in acc_vars.items() if k in self._predefined_vars_config
        }
        return True, declared_vars, acc_vars, events

    def matches(self, stream: "Stream") -> bool:
        """Return True only if ALL matchers accept the stream."""
        return all(m.match(stream) for m in self._matchers)

    def process(self, chunk_size: int = CHUNK_SIZE) -> MemberPlan:
        """
        Query, match, and transform streams for this member.

        Uses an ORM pre-filter (iregex) to reduce the result set before
        applying Python matchers for precise correctness. Streams are consumed
        via an iterator to avoid loading the full queryset into memory.
        Each transformer step is recorded for verbose plan output.
        """
        matched_priority_keys: set[str] = set()
        q_filter = build_q_filter(self._member.matchers)
        qs = (
            Stream.objects.filter(q_filter)
            .only(*self._required_fields)
            .iterator(chunk_size=chunk_size)
        )

        # Each group holds (stream_stats, StreamRecord); stream_stats is a raw dict captured
        # while the ORM object is still in scope and passed to the plan assembler.
        groups: defaultdict[str, list[tuple["dict | None", StreamRecord]]] = (
            defaultdict(list)
        )
        dropped_records: list[DroppedRecord] = []
        all_violations: list[ValidatorViolation] = []

        transformer_pairs = list(
            zip(
                self._transformers,
                self._transformer_descs,
                range(1, len(self._transformers) + 1),
            )
        )

        # Resolve predefined variable values once per process() call.
        predefined_values: dict[str, str] = {
            name: var.value for name, var in self._predefined_vars_config.items()
        }

        for stream in qs:
            # Precise Python match — guards against ORM iregex false positives.
            # _match_and_collect_captures also builds the per-stream variable context.
            matched, declared_vars, _acc_vars, var_events = (
                self._match_and_collect_captures(
                    stream, predefined_values, self._matcher_descs
                )
            )
            if not matched:
                continue

            original_name = stream.name
            # Work on a shallow copy so transformers can mutate .name freely
            working = copy(stream)
            steps: list[TransformStep] = []
            was_dropped = False

            for transformer, desc, idx in transformer_pairs:
                # Emit template_read events for variables referenced in this transformer's
                # template fields, using the current declared_vars snapshot.
                for field_context, template_str in transformer.template_field_strings():
                    for var_name in extract_template_variables(template_str):
                        if var_name in declared_vars:
                            var_events.append(
                                VariableEvent(
                                    name=var_name,
                                    event_type="template_read",
                                    value=declared_vars[var_name],
                                    source=f"transformer #{idx} ({field_context})",
                                )
                            )

                name_in = working.name
                result = transformer.transform(working, variables=declared_vars)
                if result is None:
                    steps.append(
                        TransformStep(
                            transformer_index=idx,
                            transformer_desc=desc,
                            name_in=name_in,
                            name_out=None,
                        )
                    )
                    dropped_records.append(
                        DroppedRecord(original_name=original_name, steps=steps)
                    )
                    was_dropped = True
                    break
                steps.append(
                    TransformStep(
                        transformer_index=idx,
                        transformer_desc=desc,
                        name_in=name_in,
                        name_out=result.name,
                    )
                )
                working = result

            if not was_dropped:
                stream_stats = getattr(stream, "stream_stats", None)
                priority_key = self._priority_key(stream)
                if priority_key is not None:
                    matched_priority_keys.add(priority_key)
                groups[working.name].append(
                    (
                        stream_stats,
                        StreamRecord(
                            id=stream.pk,
                            original_name=original_name,
                            transformed_name=working.name,
                            # Use the transformed copy so setMetadata / set
                            # overrides of tvg_id and logo_url reach the channel.
                            tvg_id=working.tvg_id,
                            logo_url=working.logo_url,
                            priority=(
                                self._stream_priorities[priority_key]
                                if priority_key is not None
                                else 0
                            ),
                            captures=declared_vars,
                            steps=steps,
                            variable_events=var_events,
                        ),
                    )
                )
                # Stream-level validators run after transformation; violations are observational
                for sv, desc, idx in self._stream_validator_pairs:
                    if not sv.validate(working):
                        all_violations.append(
                            ValidatorViolation(
                                validator_index=idx,
                                validator_desc=desc,
                                action=sv.action,
                                scope="stream",
                                target=working.name,
                            )
                        )

        member_plan = assemble_member_plan(
            member_name=self._member.name,
            matcher_descs=self._matcher_descs,
            transformer_descs=self._transformer_descs,
            raw_groups=groups,
            dropped=dropped_records,
            effective_stream_profile=self._effective_stream_profile,
            effective_order_streams_by=self._effective_order_streams_by,
        )

        # Channel-level validators run post-assembly against each assembled channel
        for cv, desc, idx in self._channel_validator_pairs:
            for channel in member_plan.channels:
                if not cv.validate(channel):
                    all_violations.append(
                        ValidatorViolation(
                            validator_index=idx,
                            validator_desc=desc,
                            action=cv.action,
                            scope="channel",
                            target=channel.name,
                        )
                    )

        # Member-level validators run once per member, including when 0 channels were assembled.
        for mv, desc, idx in self._member_validator_pairs:
            if not mv.validate(member_plan):
                all_violations.append(
                    ValidatorViolation(
                        validator_index=idx,
                        validator_desc=desc,
                        action=mv.action,
                        scope="member",
                        target=member_plan.member_name,
                    )
                )

        return replace(
            member_plan,
            validator_descs=self._validator_descs,
            violations=all_violations,
            unmatched_priorities=[
                key
                for key in self._stream_priorities
                if key not in matched_priority_keys
            ],
        )

    def _priority_key(self, stream: "Stream") -> "str | None":
        """First streamPriorities key (in manifest order) matching this stream.

        A key matches when it is part of the stream's URL (e.g. an AceStream id)
        or equals its stream hash. Priorities rank individual feeds, which can
        share a name, so names are deliberately not used; a key whose feed has
        gone away simply stops matching and is reported in the plan.
        """
        if not self._stream_priorities:
            return None
        stream_hash = getattr(stream, "stream_hash", None) or ""
        url = getattr(stream, "url", None) or ""
        for key in self._stream_priorities:
            if key in url or key == stream_hash:
                return key
        return None


class GroupPipeline:
    """Encapsulates processing for a single ConfigGroup (→ Dispatcharr ChannelGroup)."""

    def __init__(
        self,
        key: str,
        name: str,
        members: list[ConfigMember],
        inherited_stream_profile: "str | None" = None,
        group_stream_profile: "str | None" = None,
        inherited_order_streams_by: "OrderStreamsBy | None" = None,
        group_order_streams_by: "OrderStreamsBy | None" = None,
        inherited_variables: "dict[str, ConfigVariable] | None" = None,
        group_variables: "dict[str, ConfigVariable] | None" = None,
    ) -> None:
        self._key = key
        self._name = name
        effective_sp = group_stream_profile or inherited_stream_profile
        effective_osb = group_order_streams_by or inherited_order_streams_by
        # Merge in precedence order: outer scope first, group-level shadows it.
        effective_variables: dict[str, ConfigVariable] = {
            **(inherited_variables or {}),
            **(group_variables or {}),
        }
        self._pipelines = [
            MemberPipeline(
                m,
                inherited_stream_profile=effective_sp,
                inherited_order_streams_by=effective_osb,
                inherited_variables=effective_variables,
            )
            for m in members
        ]

    def process(self, chunk_size: int = CHUNK_SIZE) -> GroupPlan:
        return GroupPlan(
            key=self._key,
            name=self._name,
            members=[p.process(chunk_size=chunk_size) for p in self._pipelines],
        )


class ProfilePipeline:
    """Encapsulates processing for a single ConfigProfile."""

    def __init__(self, key: str, profile: ConfigProfile) -> None:
        self._key = key
        self._name = profile.name
        self._profile = profile
        self._pipelines = [
            GroupPipeline(
                key=cat_key,
                name=cat.name,
                members=cat.members,
                inherited_stream_profile=profile.stream_profile,
                group_stream_profile=cat.stream_profile,
                inherited_order_streams_by=profile.order_streams_by,
                group_order_streams_by=cat.order_streams_by,
                inherited_variables=profile.variables,
                group_variables=cat.variables,
            )
            for cat_key, cat in profile.groups.items()
        ]

    def process(self, chunk_size: int = CHUNK_SIZE) -> ProfilePlan:
        groups = [c.process(chunk_size=chunk_size) for c in self._pipelines]
        groups = _finalise_channels(groups, self._profile)
        return ProfilePlan(key=self._key, name=self._name, groups=groups)


def _finalise_channels(
    groups: list[GroupPlan], profile: ConfigProfile
) -> list[GroupPlan]:
    """Add keepEmptyChannels placeholders and assign channel numbers in manifest order.

    Numbering runs over groups, then members, then channels. A member's
    channelNumber pins its first channel to that number and numbering continues
    from there; startChannelNumber seeds the counter for members before any pin.
    Without either, channels stay unnumbered.
    """
    number = profile.start_channel_number
    finalised: list[GroupPlan] = []
    for group, group_cfg in zip(groups, profile.groups.values()):
        members = []
        for member, member_cfg in zip(group.members, group_cfg.members):
            channels = member.channels
            if not channels and profile.keep_empty_channels:
                channels = [ChannelPlan(name=member_cfg.name, placeholder=True)]
            if member_cfg.channel_number is not None:
                number = member_cfg.channel_number
            numbered = []
            for channel in channels:
                numbered.append(replace(channel, channel_number=number))
                if number is not None:
                    number += 1
            members.append(replace(member, channels=numbered))
        finalised.append(replace(group, members=members))
    return finalised


class WaybillPipeline:
    """Top-level pipeline that processes all profiles in the manifest."""

    def __init__(self, config: WaybillConfig) -> None:
        self._config = config
        self._profiles = [
            ProfilePipeline(key=prof_key, profile=prof)
            for prof_key, prof in config.spec.profiles.items()
        ]

    def compute_plan(self, chunk_size: int = CHUNK_SIZE) -> WaybillPlan:
        return WaybillPlan(
            manifest_name=self._config.metadata.name,
            profiles=[p.process(chunk_size=chunk_size) for p in self._profiles],
        )
