from __future__ import annotations

from collections import Counter
from dataclasses import replace
from typing import Iterable

from .types.config import OrderStreamsBy
from .types.plan import (
    ChannelPlan,
    DroppedRecord,
    MemberPlan,
    StreamRecord,
    VariableEvent,
    WaybillPlan,
)


def _most_common(values: "Iterable[str | None]") -> "str | None":
    """Return the most frequently occurring non-None value, or None if all are absent."""
    counts: Counter[str] = Counter(v for v in values if v)
    if not counts:
        return None
    return counts.most_common(1)[0][0]


def _quality_key(stream_stats: "dict | None") -> "tuple[int, float]":
    """Return a (height, bitrate) sort key for quality ordering.

    Streams with missing or unparseable stats return (0, 0.0) so they sort
    to the end when the caller reverses the sort (descending).
    """
    if not stream_stats:
        return (0, 0.0)
    resolution = stream_stats.get("resolution", "")
    try:
        height = (
            int(str(resolution).split("x")[1])
            if resolution and "x" in str(resolution)
            else 0
        )
    except (IndexError, ValueError):
        height = 0
    try:
        bitrate = float(stream_stats.get("video_bitrate", 0) or 0)
    except (TypeError, ValueError):
        bitrate = 0.0
    return (height, bitrate)


def _quality_order_reason(stream_stats: "dict | None") -> str:
    """Return a human-readable ordering reason string for the plan output."""
    if not stream_stats:
        return "quality: no stats"
    resolution = stream_stats.get("resolution", "")
    if not resolution:
        return "quality: no stats"
    try:
        height = int(str(resolution).split("x")[1])
        bitrate = float(stream_stats.get("video_bitrate", 0) or 0)
        return f"quality: {height}p, {bitrate:.0f}kbps"
    except (IndexError, ValueError):
        return "quality: no stats"


def assemble_member_plan(
    member_name: str,
    matcher_descs: "list[str]",
    transformer_descs: "list[str]",
    raw_groups: "dict[str, list[tuple[dict | None, StreamRecord]]]",
    dropped: "list[DroppedRecord]",
    effective_stream_profile: "str | None",
    effective_order_streams_by: "OrderStreamsBy | None",
) -> MemberPlan:
    """Assemble a MemberPlan from raw per-channel groups produced by the pipeline."""
    needs_quality = effective_order_streams_by is OrderStreamsBy.QUALITY
    channels = sorted(
        (
            _assemble_channel_plan(
                name,
                entries,
                effective_stream_profile,
                effective_order_streams_by,
                needs_quality,
            )
            for name, entries in raw_groups.items()
        ),
        key=lambda c: c.name,
    )
    return MemberPlan(
        member_name=member_name,
        matcher_descs=matcher_descs,
        transformer_descs=transformer_descs,
        channels=channels,
        dropped=dropped,
    )


def _assemble_channel_plan(
    name: str,
    entries: "list[tuple[dict | None, StreamRecord]]",
    effective_stream_profile: "str | None",
    effective_order_streams_by: "OrderStreamsBy | None",
    needs_quality: bool,
) -> ChannelPlan:
    # Higher streamPriorities first; quality (when enabled) only orders streams of
    # equal priority. Python's sort is stable, so remaining ties keep pipeline order.
    keyed: list[tuple[StreamRecord, tuple[int, int, float]]] = []
    for stats, r in entries:
        quality = _quality_key(stats) if needs_quality else (0, 0.0)
        reasons = []
        if r.priority:
            reasons.append(f"priority {r.priority}")
        if needs_quality:
            reasons.append(_quality_order_reason(stats))
        reason = ", ".join(reasons) or None
        keyed.append((replace(r, order_reason=reason), (r.priority, *quality)))
    streams = [r for r, _ in sorted(keyed, key=lambda e: e[1], reverse=True)]
    return ChannelPlan(
        name=name,
        epg_id=_most_common(r.tvg_id for r in streams),
        logo_url=_most_common(r.logo_url for r in streams),
        stream_profile=effective_stream_profile,
        order_streams_by=effective_order_streams_by,
        streams=streams,
    )


class WaybillPlanFormatter:
    """Renders a WaybillPlan as a human-readable tree of log lines."""

    def format(self, plan: WaybillPlan) -> list[str]:
        lines: list[str] = []
        lines.append(f"=== Waybill Plan: {plan.manifest_name} ===")

        total_channels = 0
        for profile in plan.profiles:
            lines.append(f"Profile: {profile.key}")
            for group in profile.groups:
                lines.append(f"  [{group.key}] {group.name}")
                for member in group.members:
                    lines.append(f"    {member.member_name}")
                    for i, desc in enumerate(member.matcher_descs, 1):
                        lines.append(f"      Matcher [{i}]: {desc}")
                    for i, desc in enumerate(member.transformer_descs, 1):
                        lines.append(f"      Transformer [T{i}]: {desc}")
                    for i, desc in enumerate(member.validator_descs, 1):
                        lines.append(f"      Validator [V{i}]: {desc}")
                    for channel in member.channels:
                        stream_count = len(channel.streams)
                        total_channels += 1
                        suffix = (
                            "  — no streams, kept disabled"
                            if channel.placeholder
                            else ""
                        )
                        lines.append(
                            f"      Channel: {channel.name}  ({stream_count} stream(s)){suffix}"
                        )
                        if channel.channel_number is not None:
                            lines.append(f"        Number: {channel.channel_number}")
                        if channel.epg_id:
                            lines.append(f"        EPG-ID: {channel.epg_id}")
                        if channel.logo_url:
                            lines.append(f"        Logo:   {channel.logo_url}")
                        if channel.stream_profile:
                            lines.append(
                                f"        Stream Profile: {channel.stream_profile}"
                            )
                        if channel.order_streams_by:
                            lines.append(
                                f"        Order Streams By: {channel.order_streams_by.value}"
                            )
                        for stream in channel.streams:
                            if stream.original_name != stream.transformed_name:
                                suffix = (
                                    f"  [{stream.order_reason}]"
                                    if stream.order_reason
                                    else ""
                                )
                                lines.append(
                                    f'        "{stream.original_name}"  \u2192  "{stream.transformed_name}"{suffix}'
                                )
                            else:
                                suffix = (
                                    f"  [{stream.order_reason}]"
                                    if stream.order_reason
                                    else ""
                                )
                                lines.append(
                                    f'        "{stream.original_name}"{suffix}'
                                )
                            for step in stream.steps:
                                if step.name_in != step.name_out:
                                    lines.append(
                                        f'          [T{step.transformer_index}] "{step.name_in}" → "{step.name_out}"'
                                    )
                            if stream.variable_events:
                                lines.append("          Variable log:")
                                for ev in stream.variable_events:
                                    lines.extend(self._format_variable_event(ev))
                        # Channel-scoped violations for this channel
                        for v in member.violations:
                            if v.scope == "channel" and v.target == channel.name:
                                tag = "[FAIL]" if v.action == "fail" else "[WARN]"
                                lines.append(
                                    f"        {tag} [V{v.validator_index}]: {v.validator_desc}"
                                )
                    for key in member.unmatched_priorities:
                        lines.append(
                            f'      [WARN] streamPriorities entry matched no stream: "{key}"'
                        )
                    if member.dropped:
                        lines.append(f"      Dropped: {member.dropped_count} stream(s)")
                        for rec in member.dropped:
                            drop_step = rec.steps[-1] if rec.steps else None
                            if drop_step:
                                lines.append(
                                    f'        "{rec.original_name}"  →  dropped by [T{drop_step.transformer_index}]: {drop_step.transformer_desc}'
                                )
                            else:
                                lines.append(
                                    f'        "{rec.original_name}"  →  dropped'
                                )
                            for step in rec.steps[:-1]:
                                if step.name_in != step.name_out:
                                    lines.append(
                                        f'          [T{step.transformer_index}] "{step.name_in}" → "{step.name_out}"'
                                    )
                    # Stream-scoped violations for this member (grouped)
                    stream_violations = [
                        v for v in member.violations if v.scope == "stream"
                    ]
                    if stream_violations:
                        lines.append(
                            f"      Violations: {len(stream_violations)} stream validation(s)"
                        )
                        for v in stream_violations:
                            tag = "[FAIL]" if v.action == "fail" else "[WARN]"
                            lines.append(
                                f'        {tag} [V{v.validator_index}] "{v.target}": {v.validator_desc}'
                            )
                    member_violations = [
                        v for v in member.violations if v.scope == "member"
                    ]
                    if member_violations:
                        lines.append(
                            f"      Violations: {len(member_violations)} member validation(s)"
                        )
                        for v in member_violations:
                            tag = "[FAIL]" if v.action == "fail" else "[WARN]"
                            lines.append(
                                f'        {tag} [V{v.validator_index}] "{v.target}": {v.validator_desc}'
                            )

        profile_count = len(plan.profiles)
        lines.append(
            f"=== Summary: {total_channels} channel(s) across {profile_count} profile(s) ==="
        )
        return lines

    @staticmethod
    def _format_variable_event(ev: "VariableEvent") -> "list[str]":
        """Return one or two log lines for a single :class:`VariableEvent`."""
        prefix = "            "
        match ev.event_type:
            case "init":
                return [f'{prefix}[init]      {ev.name} = "{ev.value}"  ({ev.source})']
            case "capture_override":
                old = ev.old_value if ev.old_value is not None else ""
                return [
                    f'{prefix}[override]  {ev.name}: "{old}" \u2192 "{ev.value}"  ({ev.source})'
                ]
            case "capture_discarded":
                return [
                    f'{prefix}[discarded] {ev.name} = "{ev.value}"  ({ev.source}, not in scope)'
                ]
            case "template_read":
                return [f'{prefix}[read]      {ev.name} = "{ev.value}"  ({ev.source})']
            case _:
                return [
                    f'{prefix}[{ev.event_type}] {ev.name} = "{ev.value}"  ({ev.source})'
                ]
