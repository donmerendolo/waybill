"""epgSchedule: config parsing, programme matching, plan and auto-apply settings."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from src.pipeline import _effective_epg_schedule, _finalise_channels
from src.plan import WaybillPlanFormatter
from src.scheduler import interval_minutes
from src.schedule import EpgGuide, Programme, evaluate
from src.types.config import (
    EPG_SCHEDULE_OFF,
    ConfigProfile,
    _to_epg_schedule,
    _to_minutes,
)
from src.types.plan import ChannelPlan, GroupPlan, MemberPlan, ProfilePlan, WaybillPlan

NOW = datetime(2026, 10, 21, 20, 0, tzinfo=timezone.utc)


def _at(hours: float) -> datetime:
    return NOW + timedelta(hours=hours)


class _Guide(EpgGuide):
    """EpgGuide over an in-memory guide: {tvg_id: [Programme, ...]}."""

    def __init__(self, guide: dict[str, list[Programme]]) -> None:
        super().__init__(now=NOW)
        self._guide = guide
        self.queries: list[str] = []

    def _load_has_guide(self, tvg_id):
        self.queries.append(tvg_id)
        return any(p.end > NOW for p in self._guide.get(tvg_id, []))

    def _load_window(self, tvg_id, start, end):
        self.queries.append(tvg_id)
        return [
            p for p in self._guide.get(tvg_id, []) if p.start <= end and p.end >= start
        ]


def _prog(title: str, start_h: float, end_h: float, **kw: str) -> Programme:
    return Programme(
        title=title,
        sub_title=kw.get("sub_title", ""),
        description=kw.get("description", ""),
        start=_at(start_h),
        end=_at(end_h),
    )


F1_DAY = [
    _prog("Programación", -3, 1),
    _prog("Fórmula 1: GP de México", 1, 3),
    _prog("Programación", 3, 6),
]


def _rule(**raw):
    return _to_epg_schedule(raw, "member 'x'")


class TestDurations:
    @pytest.mark.parametrize(
        ("raw", "minutes"),
        [(None, 0), (45, 45), ("45m", 45), ("2h", 120), ("1h30m", 90), ("1h 5min", 65)],
    )
    def test_accepted(self, raw, minutes) -> None:
        assert _to_minutes(raw, "w", "before") == minutes

    @pytest.mark.parametrize("raw", ["soon", "30s", -5, True, "h"])
    def test_rejected(self, raw) -> None:
        with pytest.raises(ValueError, match="before"):
            _to_minutes(raw, "w", "before")


class TestConfig:
    def test_parses_rule(self) -> None:
        rule = _rule(
            before="1h",
            after=15,
            matchers=[{"type": "containsAny", "substrings": ["Fórmula 1"]}],
        )
        assert (rule.before, rule.after, len(rule.matchers)) == (60, 15, 1)
        assert rule.active

    def test_false_clears(self) -> None:
        assert _to_epg_schedule(False, "x") is EPG_SCHEDULE_OFF

    def test_absent_inherits(self) -> None:
        assert _to_epg_schedule(None, "x") is None

    def test_action(self) -> None:
        assert _rule().action.value == "keep"
        assert _rule(action="drop").action.value == "drop"
        with pytest.raises(ValueError, match="keep or drop"):
            _rule(action="disable")

    def test_unknown_key(self) -> None:
        with pytest.raises(ValueError, match="unknown keys: start"):
            _rule(start="1h")

    def test_inheritance(self) -> None:
        profile_rule = _rule(before="1h")
        member_rule = _rule(after="1h")
        assert _effective_epg_schedule(None, None, profile_rule) is profile_rule
        assert _effective_epg_schedule(member_rule, None, profile_rule) is member_rule
        assert _effective_epg_schedule(EPG_SCHEDULE_OFF, None, profile_rule) is None
        assert _effective_epg_schedule(None, None, None) is None


class TestEvaluate:
    F1 = [{"type": "containsAny", "substrings": ["Fórmula 1"]}]

    def test_off_between_programmes(self) -> None:
        decision = evaluate(_rule(matchers=self.F1), _Guide({"F1": F1_DAY}), "F1")
        assert not decision.enabled
        assert decision.reason == "off: no matching programme"

    def test_on_inside_before_window(self) -> None:
        # The race starts in 1h; "before: 1h" switches the channel on now.
        decision = evaluate(
            _rule(before="1h", matchers=self.F1), _Guide({"F1": F1_DAY}), "F1"
        )
        assert decision.enabled
        assert "Fórmula 1: GP de México" in decision.reason

    def test_gap_in_guide_is_off_not_unknown(self) -> None:
        # Nothing airs right now, but the guide goes on: the channel is off.
        guide = _Guide({"F1": [_prog("Fórmula 1: GP", 2, 4)]})
        assert not evaluate(_rule(matchers=self.F1), guide, "F1").enabled

    def test_just_outside_before_window(self) -> None:
        decision = evaluate(
            _rule(before="59m", matchers=self.F1), _Guide({"F1": F1_DAY}), "F1"
        )
        assert not decision.enabled

    def test_on_inside_after_window(self) -> None:
        # The race ended 30 min ago; "after: 30m" keeps the channel on until now.
        day = [_prog("Fórmula 1: GP", -2, -0.5), _prog("Programación", -0.5, 3)]
        assert evaluate(
            _rule(after="30m", matchers=self.F1), _Guide({"F1": day}), "F1"
        ).enabled
        assert not evaluate(
            _rule(after="29m", matchers=self.F1), _Guide({"F1": day}), "F1"
        ).enabled

    def test_drop_matcher_excludes_filler(self) -> None:
        rule = _rule(
            matchers=[
                {"type": "exactMatch", "action": "drop", "values": ["Programación"]}
            ]
        )
        guide = _Guide({"F1": F1_DAY})
        assert not evaluate(rule, guide, "F1").enabled
        assert evaluate(_rule(before="1h", matchers=rule.matchers), guide, "F1").enabled

    def test_matchers_see_other_fields(self) -> None:
        rule = _rule(
            matchers=[
                {
                    "type": "containsAny",
                    "field": "description",
                    "substrings": ["directo"],
                }
            ]
        )
        guide = _Guide({"X": [_prog("Fútbol", -1, 1, description="En directo")]})
        assert evaluate(rule, guide, "X").enabled

    def test_no_matchers_means_any_programme(self) -> None:
        guide = _Guide({"X": [_prog("Lo que sea", -1, 1)]})
        assert evaluate(_rule(), guide, "X").enabled

    @pytest.mark.parametrize(
        ("guide", "epg_id", "reason"),
        [
            ({}, None, "no EPG id, left enabled"),
            ({}, "F1", 'no guide data for "F1", left enabled'),
            # The guide ran out: its last programme ended an hour ago.
            (
                {"F1": [_prog("Fórmula 1", -3, -1)]},
                "F1",
                'no guide data for "F1", left enabled',
            ),
        ],
    )
    def test_missing_guide_leaves_enabled(self, guide, epg_id, reason) -> None:
        decision = evaluate(_rule(matchers=self.F1), _Guide(guide), epg_id)
        assert decision.enabled
        assert decision.reason == reason

    def test_guide_queries_are_cached(self) -> None:
        guide = _Guide({"F1": F1_DAY})
        rule = _rule(matchers=self.F1)
        evaluate(rule, guide, "F1")
        evaluate(rule, guide, "F1")
        assert len(guide.queries) == 2  # one guide check, one window


class TestEvaluateDrop:
    """action: drop switches the channel off while a matching programme is near."""

    FILLER = [
        {"type": "containsAny", "substrings": ["Disfruta de DAZN", "Programación"]}
    ]

    def test_off_during_filler(self) -> None:
        decision = evaluate(
            _rule(action="drop", matchers=self.FILLER), _Guide({"F1": F1_DAY}), "F1"
        )
        assert not decision.enabled
        assert decision.reason.startswith('off: "Programación"')

    def test_on_during_real_programme(self) -> None:
        guide = _Guide(
            {"F1": [_prog("Fórmula 1: GP", -1, 1), _prog("Programación", 1, 4)]}
        )
        decision = evaluate(_rule(action="drop", matchers=self.FILLER), guide, "F1")
        assert decision.enabled
        assert decision.reason == "on: no matching programme"

    def test_before_switches_off_ahead_of_filler(self) -> None:
        guide = _Guide(
            {"F1": [_prog("Fórmula 1: GP", -1, 0.5), _prog("Programación", 0.5, 4)]}
        )
        rule = _rule(action="drop", before="30m", matchers=self.FILLER)
        assert not evaluate(rule, guide, "F1").enabled
        rule = _rule(action="drop", before="29m", matchers=self.FILLER)
        assert evaluate(rule, _Guide({"F1": guide._guide["F1"]}), "F1").enabled

    def test_missing_guide_still_leaves_enabled(self) -> None:
        decision = evaluate(
            _rule(action="drop", matchers=self.FILLER), _Guide({}), "F1"
        )
        assert decision.enabled


class TestPlan:
    def _profile(self, member_rule=None, profile_rule=None, keep_empty=False):
        return ConfigProfile(
            name="todo",
            keep_empty_channels=keep_empty,
            epg_schedule=profile_rule,
            groups={
                "dazn": {
                    "name": "DAZN",
                    "members": [
                        {"name": "DAZN F1", "matchers": []},
                        {"name": "DAZN 1", "matchers": []},
                    ],
                }
            },
        )

    def _groups(self, f1_channels):
        return [
            GroupPlan(
                key="dazn",
                name="DAZN",
                members=[
                    MemberPlan(member_name="DAZN F1", channels=f1_channels),
                    MemberPlan(
                        member_name="DAZN 1",
                        channels=[ChannelPlan(name="DAZN 1", epg_id="DAZN1")],
                    ),
                ],
            )
        ]

    def test_profile_rule_applies_to_every_channel(self) -> None:
        rule = _rule(matchers=[{"type": "containsAny", "substrings": ["Fórmula 1"]}])
        guide = _Guide({"F1": F1_DAY, "DAZN1": [_prog("Fútbol", -1, 1)]})
        groups = _finalise_channels(
            self._groups([ChannelPlan(name="DAZN F1", epg_id="F1")]),
            self._profile(profile_rule=rule),
            guide,
        )
        f1, dazn1 = (m.channels[0] for m in groups[0].members)
        assert (f1.enabled, dazn1.enabled) == (False, False)
        assert f1.schedule == "off: no matching programme"

    def test_without_rule_nothing_changes(self) -> None:
        guide = _Guide({})
        groups = _finalise_channels(
            self._groups([ChannelPlan(name="DAZN F1", epg_id="F1")]),
            self._profile(),
            guide,
        )
        channel = groups[0].members[0].channels[0]
        assert (channel.enabled, channel.schedule) == (True, None)
        assert guide.queries == []

    def test_placeholders_are_not_evaluated(self) -> None:
        guide = _Guide({"F1": F1_DAY})
        groups = _finalise_channels(
            self._groups([]),
            self._profile(profile_rule=_rule(), keep_empty=True),
            guide,
        )
        placeholder = groups[0].members[0].channels[0]
        assert placeholder.placeholder and placeholder.schedule is None

    def test_formatter_shows_schedule(self) -> None:
        channel = ChannelPlan(
            name="DAZN F1",
            epg_id="F1",
            enabled=False,
            schedule="off: no matching programme",
        )
        plan = WaybillPlan(
            manifest_name="m",
            profiles=[
                ProfilePlan(
                    key="todo",
                    name="todo",
                    groups=[
                        GroupPlan(
                            key="dazn",
                            name="DAZN",
                            members=[
                                MemberPlan(member_name="DAZN F1", channels=[channel])
                            ],
                        )
                    ],
                )
            ],
        )
        lines = WaybillPlanFormatter().format(plan)
        assert any("disabled by epgSchedule" in line for line in lines)
        assert any("Schedule: off: no matching programme" in line for line in lines)


class TestAutoApplySetting:
    @pytest.mark.parametrize(
        ("settings", "minutes"),
        [
            (None, 0),
            ({}, 0),
            ({"auto_apply_minutes": 5}, 5),
            ({"auto_apply_minutes": "15"}, 15),
            ({"auto_apply_minutes": 2.5}, 2),
            ({"auto_apply_minutes": -3}, 0),
            ({"auto_apply_minutes": "x"}, 0),
            ({"auto_apply_minutes": True}, 0),
        ],
    )
    def test_interval_minutes(self, settings, minutes) -> None:
        assert interval_minutes(settings) == minutes
