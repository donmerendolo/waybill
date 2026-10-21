"""epgSchedule: switch a channel on or off around the EPG programmes that match.

The guide is read from Dispatcharr's EPG tables (EPGData / ProgramData). A
programme "counts" when every matcher of the rule accepts it; matchers see a
programme as an object with these fields:

    name / title, sub_title, description

so the default matcher field ("name") means the programme title. With action
keep (default) the programmes that match turn the channel on; with action drop
the ones that do not match do (e.g. anything but filler). The channel is
enabled while now falls between `before` minutes ahead of such a programme's
start and `after` minutes past its end, and disabled otherwise.

When the guide has nothing to decide on (no EPG id, no EPG entry, or a guide
with no programme still to end, e.g. a source that stopped refreshing), the
channel is left enabled and the reason is reported: a missing guide must not
switch a whole lineup off.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .types.config import ConfigEpgSchedule


@dataclass(frozen=True)
class Programme:
    title: str
    sub_title: str
    description: str
    start: datetime
    end: datetime

    @property
    def name(self) -> str:
        return self.title


@dataclass(frozen=True)
class ScheduleDecision:
    enabled: bool
    reason: str


def _now() -> datetime:
    from django.utils import timezone

    return timezone.now()


def _clock(value: datetime, fmt: str = "%H:%M") -> str:
    """Local wall-clock time for plan output (Dispatcharr's TIME_ZONE)."""
    try:
        from django.utils import timezone

        value = timezone.localtime(value)
    except Exception:  # noqa: BLE001 - formatting only; fall back to the stored zone
        pass
    return value.strftime(fmt)


class EpgGuide:
    """Reads programmes per EPG id once per plan, at one fixed "now"."""

    def __init__(self, now: "datetime | None" = None) -> None:
        self._now = now
        self._windows: dict[tuple[str, datetime, datetime], list[Programme]] = {}
        self._has_guide: dict[str, bool] = {}
        self._entries: dict[str, object] = {}

    @property
    def now(self) -> datetime:
        """Fixed for the whole plan; read from Django on first use."""
        if self._now is None:
            self._now = _now()
        return self._now

    def has_guide(self, tvg_id: str) -> bool:
        """Whether the EPG entry for *tvg_id* has any programme still to end.

        False when there is no such entry, or its guide ran out (e.g. the EPG
        source has not refreshed): then there is nothing to decide on.
        """
        if tvg_id not in self._has_guide:
            self._has_guide[tvg_id] = self._load_has_guide(tvg_id)
        return self._has_guide[tvg_id]

    def programmes(
        self, tvg_id: str, start: datetime, end: datetime
    ) -> list[Programme]:
        """Programmes of the EPG entry for *tvg_id* overlapping [start, end], by start."""
        key = (tvg_id, start, end)
        if key not in self._windows:
            self._windows[key] = self._load_window(tvg_id, start, end)
        return self._windows[key]

    def _entry(self, tvg_id: str) -> object:
        # The same entry apply assigns to the channel: the first with that tvg-id.
        if tvg_id not in self._entries:
            from apps.epg.models import EPGData

            self._entries[tvg_id] = EPGData.objects.filter(tvg_id=tvg_id).first()
        return self._entries[tvg_id]

    def _load_has_guide(self, tvg_id: str) -> bool:
        from apps.epg.models import ProgramData

        entry = self._entry(tvg_id)
        if entry is None:
            return False
        return ProgramData.objects.filter(epg=entry, end_time__gt=self.now).exists()

    def _load_window(
        self, tvg_id: str, start: datetime, end: datetime
    ) -> list[Programme]:
        from apps.epg.models import ProgramData

        entry = self._entry(tvg_id)
        if entry is None:
            return []
        rows = (
            ProgramData.objects.filter(
                epg=entry, start_time__lte=end, end_time__gte=start
            )
            .order_by("start_time")
            .values("title", "sub_title", "description", "start_time", "end_time")
        )
        return [
            Programme(
                title=row["title"] or "",
                sub_title=row["sub_title"] or "",
                description=row["description"] or "",
                start=row["start_time"],
                end=row["end_time"],
            )
            for row in rows
        ]


def evaluate(
    rule: "ConfigEpgSchedule", guide: EpgGuide, epg_id: "str | None"
) -> ScheduleDecision:
    """Decide whether a channel with EPG id *epg_id* is enabled right now."""
    from .matchers import build_matcher
    from .types.config import MatcherAction

    if not epg_id:
        return ScheduleDecision(True, "no EPG id, left enabled")
    if not guide.has_guide(epg_id):
        return ScheduleDecision(True, f'no guide data for "{epg_id}", left enabled')
    # The channel is on while now is in [start - before, end + after] of a
    # programme that turns it on: one that matches (keep) or one that does not
    # (drop). That programme starts by now + before and ends no earlier than
    # now - after, so the margins always widen the time around real programmes.
    window_start = guide.now - timedelta(minutes=rule.after)
    window_end = guide.now + timedelta(minutes=rule.before)
    matchers = [build_matcher(m) for m in rule.matchers]
    keep = rule.action is MatcherAction.KEEP
    for programme in guide.programmes(epg_id, window_start, window_end):
        if all(m.match(programme) for m in matchers) == keep:
            return ScheduleDecision(True, f"on: {_describe(programme)}")
    if keep:
        return ScheduleDecision(False, "off: no matching programme")
    airing = guide.programmes(epg_id, guide.now, guide.now)
    return ScheduleDecision(
        False, f"off: {_describe(airing[0])}" if airing else "off: nothing airing"
    )


def _describe(programme: Programme) -> str:
    return (
        f'"{programme.title}" '
        f"{_clock(programme.start, '%a %H:%M')}-{_clock(programme.end)}"
    )
