from __future__ import annotations

from typing import TYPE_CHECKING, Protocol

from django.db import transaction
from django.db.models import Max

from apps.channels.models import (
    Channel,
    ChannelGroup,
    ChannelProfile,
    ChannelProfileMembership,
    ChannelStream,
    Logo,
)
from apps.epg.models import EPGData
from core.models import StreamProfile

from .types.plan import WaybillPlan

if TYPE_CHECKING:
    from .types.plan import ChannelPlan

_MODE_UPSERT = "upsert"
_MODE_OVERWRITE = "overwrite"


class WaybillApplier:
    def __init__(self, plan: WaybillPlan, mode: str, logger: "LoggerProtocol") -> None:
        self._plan = plan
        self._mode = mode if mode in (_MODE_UPSERT, _MODE_OVERWRITE) else _MODE_UPSERT
        self._logger = logger

    def apply(self) -> dict[str, int]:
        """
        Walk the plan and write it to the database.

        Returns a summary dict with keys:
            groups_created, channels_created, channels_updated,
            channels_deleted, streams_assigned.
        """
        summary: dict[str, int] = {
            "groups_created": 0,
            "channels_created": 0,
            "channels_updated": 0,
            "channels_deleted": 0,
            "streams_assigned": 0,
        }

        with transaction.atomic():
            for profile in self._plan.profiles:
                channel_profile, profile_created = self._get_or_create_profile(
                    profile.key
                )
                if profile_created:
                    self._logger.info(
                        f"[apply] Created channel profile: {profile.key!r}"
                    )

                for plan_group in profile.groups:
                    group, group_created = ChannelGroup.objects.get_or_create(
                        name=plan_group.name
                    )
                    if group_created:
                        summary["groups_created"] += 1
                        self._logger.info(
                            f"[apply] Created channel group: {plan_group.name!r}"
                        )

                    if self._mode == _MODE_OVERWRITE:
                        plan_channel_names = {
                            channel.name
                            for member in plan_group.members
                            for channel in member.channels
                        }
                        deleted_qs = Channel.objects.filter(
                            channel_group=group
                        ).exclude(name__in=plan_channel_names)
                        deleted_count, _ = deleted_qs.delete()
                        if deleted_count:
                            summary["channels_deleted"] += deleted_count
                            self._logger.info(
                                f"[apply] Deleted {deleted_count} unlisted channel(s) from group {plan_group.name!r}"
                            )

                    for member in plan_group.members:
                        for channel_plan in member.channels:
                            created, updated, assigned = self._apply_channel(
                                channel_plan, group, channel_profile
                            )
                            summary["channels_created"] += created
                            summary["channels_updated"] += updated
                            summary["streams_assigned"] += assigned

        self._log_summary(summary)
        return summary

    def _get_or_create_profile(self, name: str) -> tuple[ChannelProfile, bool]:
        existing = ChannelProfile.objects.filter(name=name).first()
        if existing is not None:
            return existing, False

        channel_profile = ChannelProfile(name=name)
        # Dispatcharr >= 0.31 skips auto-adding every existing channel to a new
        # profile when this flag is set; older versions ignore it.
        channel_profile._start_empty = True  # type: ignore[attr-defined]
        channel_profile.save()
        # Older Dispatcharr versions still auto-add every channel with enabled=True.
        # Disable them so only manifest channels are enabled.
        ChannelProfileMembership.objects.filter(channel_profile=channel_profile).update(
            enabled=False
        )
        return channel_profile, True

    def _rollup_catchup(self, channel: Channel) -> None:
        """
        Mirror Dispatcharr's ChannelStream signal (0.31+), which bulk_create bypasses:
        roll catch-up flags up from the channel's active streams.
        """
        if not hasattr(channel, "is_catchup"):
            return
        catchup_qs = channel.streams.filter(
            is_catchup=True,
            m3u_account__is_active=True,
        )
        max_days = catchup_qs.aggregate(max_days=Max("catchup_days"))["max_days"]
        Channel.objects.filter(pk=channel.pk).update(
            is_catchup=catchup_qs.exists(),
            catchup_days=max_days or 0,
        )

    def _resolve_stream_profile(self, name: str | None) -> StreamProfile | None:
        if not name:
            return None
        sp = StreamProfile.objects.filter(name=name).first()
        if sp is None:
            self._logger.warning(
                f"[apply] StreamProfile {name!r} not found; channel will be created without one."
            )
        return sp

    def _resolve_epg_data(self, tvg_id: str | None) -> EPGData | None:
        if not tvg_id:
            return None
        return EPGData.objects.filter(tvg_id=tvg_id).first()

    def _resolve_logo(self, logo_url: str | None, channel_name: str) -> Logo | None:
        if not logo_url:
            return None
        logo, _ = Logo.objects.get_or_create(
            url=logo_url,
            defaults={"name": channel_name},
        )
        return logo

    def _apply_channel(
        self,
        channel_plan: "ChannelPlan",
        group: ChannelGroup,
        channel_profile: ChannelProfile,
    ) -> tuple[int, int, int]:
        """
        Upsert a single channel and reconcile its stream assignments.

        Returns (created, updated, streams_assigned).
        """
        if channel_plan.placeholder:
            return self._apply_placeholder(channel_plan, group, channel_profile)

        stream_profile = self._resolve_stream_profile(channel_plan.stream_profile)
        logo = self._resolve_logo(channel_plan.logo_url, channel_plan.name)
        epg_data = self._resolve_epg_data(channel_plan.epg_id)

        new_logo_id: int | None = logo.pk if logo else None
        new_stream_profile_id: int | None = (
            stream_profile.pk if stream_profile else None
        )
        new_epg_data_id: int | None = epg_data.pk if epg_data else None

        channel, created = Channel.objects.get_or_create(
            name=channel_plan.name,
            channel_group=group,
            defaults={
                "channel_number": (
                    channel_plan.channel_number
                    if channel_plan.channel_number is not None
                    else Channel.get_next_available_channel_number()
                ),
                "tvg_id": channel_plan.epg_id,
                "logo_id": new_logo_id,
                "epg_data_id": new_epg_data_id,
                "stream_profile_id": new_stream_profile_id,
            },
        )

        if created:
            self._logger.info(
                f"[apply] Created channel: {channel_plan.name!r} in group {group.name!r}"
            )
        else:
            # Update mutable fields. channel_number only changes when the profile
            # declares startChannelNumber; otherwise the existing number is kept.
            # Use field names (not attnames) so Dispatcharr's post_save signal
            # recognises the EPG change and refreshes programmes / output cache.
            update_fields: list[str] = []
            if channel.tvg_id != channel_plan.epg_id:
                channel.tvg_id = channel_plan.epg_id
                update_fields.append("tvg_id")
            if channel.logo_id != new_logo_id:
                channel.logo_id = new_logo_id
                update_fields.append("logo")
            if channel.epg_data_id != new_epg_data_id:
                channel.epg_data_id = new_epg_data_id
                update_fields.append("epg_data")
            if channel.stream_profile_id != new_stream_profile_id:
                channel.stream_profile_id = new_stream_profile_id
                update_fields.append("stream_profile")
            if (
                channel_plan.channel_number is not None
                and channel.channel_number != channel_plan.channel_number
            ):
                channel.channel_number = channel_plan.channel_number
                update_fields.append("channel_number")
            if update_fields:
                channel.save(update_fields=update_fields)
                self._logger.info(
                    f"[apply] Updated channel: {channel_plan.name!r} in group {group.name!r}"
                )

        # Ensure this channel is a member of the profile and is enabled.
        ChannelProfileMembership.objects.update_or_create(
            channel_profile=channel_profile,
            channel=channel,
            defaults={"enabled": True},
        )

        # Reconcile stream assignments: remove stale, bulk-create from plan.
        ChannelStream.objects.filter(channel=channel).delete()
        stream_rows = [
            ChannelStream(channel=channel, stream_id=sr.id, order=i)
            for i, sr in enumerate(channel_plan.streams)
        ]
        if stream_rows:
            ChannelStream.objects.bulk_create(stream_rows, ignore_conflicts=True)
        self._rollup_catchup(channel)

        return (1 if created else 0, 0 if created else 1, len(stream_rows))

    def _apply_placeholder(
        self,
        channel_plan: "ChannelPlan",
        group: ChannelGroup,
        channel_profile: ChannelProfile,
    ) -> tuple[int, int, int]:
        """
        Keep a channel that currently has no matching streams (keepEmptyChannels).

        The channel is created if missing, its streams are cleared and it is
        disabled in the profile. Its name, number, logo and EPG are left as they
        are, so clients keep the same channel; it is re-enabled with streams on
        the first apply that finds a match again.
        """
        channel, created = Channel.objects.get_or_create(
            name=channel_plan.name,
            channel_group=group,
            defaults={
                "channel_number": (
                    channel_plan.channel_number
                    if channel_plan.channel_number is not None
                    else Channel.get_next_available_channel_number()
                ),
            },
        )
        if created:
            self._logger.info(
                f"[apply] Created empty channel: {channel_plan.name!r} in group {group.name!r}"
            )
        elif (
            channel_plan.channel_number is not None
            and channel.channel_number != channel_plan.channel_number
        ):
            channel.channel_number = channel_plan.channel_number
            channel.save(update_fields=["channel_number"])

        ChannelProfileMembership.objects.update_or_create(
            channel_profile=channel_profile,
            channel=channel,
            defaults={"enabled": False},
        )
        removed, _ = ChannelStream.objects.filter(channel=channel).delete()
        self._rollup_catchup(channel)
        if not created:
            self._logger.info(
                f"[apply] No streams for {channel_plan.name!r}; kept it disabled"
                + (f" and removed {removed} stream(s)" if removed else "")
            )
        return (1 if created else 0, 0 if created else 1, 0)

    def _log_summary(self, summary: dict[str, int]) -> None:
        self._logger.info(
            "[apply] Done — "
            f"{summary['groups_created']} group(s) created, "
            f"{summary['channels_created']} channel(s) created, "
            f"{summary['channels_updated']} channel(s) updated, "
            f"{summary['channels_deleted']} channel(s) deleted, "
            f"{summary['streams_assigned']} stream(s) assigned."
        )


class LoggerProtocol(Protocol):
    def info(self, message: str) -> None: ...

    def warning(self, message: str) -> None: ...
