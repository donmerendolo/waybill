"""enabled, tags, includeTags/excludeTags and `from:` across profiles."""

from __future__ import annotations

import pytest

from src.pipeline import _finalise_channels
from src.plan import WaybillPlanFormatter
from src.types.config import ConfigSpec
from src.types.plan import ChannelPlan, GroupPlan, MemberPlan, ProfilePlan, WaybillPlan


def _spec(**profiles) -> ConfigSpec:
    return ConfigSpec(profiles=profiles)


GROUPS = {
    "deportes": {
        "name": "Deportes",
        "members": [
            {"name": "DAZN F1"},
            {"name": "Teledeporte", "tags": ["tdt"]},
        ],
    },
    "generalistas": {
        "name": "Generalistas",
        "tags": ["tdt"],
        "members": [{"name": "La 1"}, {"name": "La 2", "enabled": True}],
    },
}


def _plan_groups(profile):
    return [
        GroupPlan(
            key=key,
            name=group.name,
            members=[
                MemberPlan(member_name=m.name, channels=[ChannelPlan(name=m.name)])
                for m in group.members
            ],
        )
        for key, group in profile.groups.items()
    ]


def _decisions(profile) -> dict[str, "str | None"]:
    groups = _finalise_channels(_plan_groups(profile), profile)
    out = {}
    for group in groups:
        for member in group.members:
            channel = member.channels[0]
            assert channel.enabled is (channel.disabled_by is None)
            out[channel.name] = channel.disabled_by
    return out


class TestConfig:
    def test_tags_and_enabled_parse(self) -> None:
        profile = _spec(todo={"groups": GROUPS}).profiles["todo"]
        group = profile.groups["generalistas"]
        assert group.tags == ["tdt"]
        assert group.members[1].enabled is True
        assert group.members[0].enabled is None

    def test_single_tag_string(self) -> None:
        profile = _spec(
            todo={"groups": {"g": {"name": "G", "members": [{"name": "A", "tags": "tdt"}]}}}
        ).profiles["todo"]
        assert profile.groups["g"].members[0].tags == ["tdt"]

    @pytest.mark.parametrize("bad", [{"enabled": "no"}, {"tags": [""]}, {"tags": {"a": 1}}])
    def test_rejects_bad_values(self, bad) -> None:
        with pytest.raises(ValueError):
            _spec(todo={"groups": {"g": {"name": "G", "members": [{"name": "A", **bad}]}}})

    def test_include_tags_absent_is_none(self) -> None:
        assert _spec(todo={"groups": GROUPS}).profiles["todo"].include_tags is None


class TestFrom:
    def test_copies_groups_and_settings_and_overrides(self) -> None:
        spec = _spec(
            todo={"name": "Todo", "keepEmptyChannels": True, "excludeTags": ["tdt"], "groups": GROUPS},
            tv={"from": "todo", "name": "TV", "excludeTags": []},
        )
        tv = spec.profiles["tv"]
        assert tv.name == "TV"
        assert tv.from_profile == "todo"
        assert tv.keep_empty_channels is True
        assert tv.exclude_tags == []
        assert list(tv.groups) == ["deportes", "generalistas"]
        # Separate objects: one profile's groups never alias the other's.
        assert tv.groups["deportes"] is not spec.profiles["todo"].groups["deportes"]

    def test_name_is_not_inherited(self) -> None:
        spec = _spec(todo={"name": "Todo", "groups": GROUPS}, tv={"from": "todo"})
        assert spec.profiles["tv"].name == ""

    def test_chains(self) -> None:
        spec = _spec(
            a={"groups": GROUPS, "excludeTags": ["tdt"]},
            b={"from": "a"},
            c={"from": "b", "includeTags": ["deportes"]},
        )
        assert spec.profiles["c"].exclude_tags == ["tdt"]
        assert spec.profiles["c"].include_tags == ["deportes"]

    def test_unknown_source(self) -> None:
        with pytest.raises(ValueError, match="unknown profile 'nope'"):
            _spec(tv={"from": "nope"})

    def test_loop(self) -> None:
        with pytest.raises(ValueError, match="loops"):
            _spec(a={"from": "b"}, b={"from": "a"})


class TestDecisions:
    def test_no_rules_keeps_everything(self) -> None:
        profile = _spec(todo={"groups": GROUPS}).profiles["todo"]
        assert set(_decisions(profile).values()) == {None}

    def test_exclude_tags_uses_member_and_group_tags(self) -> None:
        profile = _spec(todo={"groups": GROUPS, "excludeTags": ["tdt"]}).profiles["todo"]
        assert _decisions(profile) == {
            "DAZN F1": None,
            "Teledeporte": "excludeTags: tdt",
            "La 1": "excludeTags: tdt",
            # enabled: true does not beat a tag rule; it only overrides enabled: false.
            "La 2": "excludeTags: tdt",
        }

    def test_group_key_is_a_tag(self) -> None:
        profile = _spec(todo={"groups": GROUPS, "includeTags": ["deportes"]}).profiles["todo"]
        assert _decisions(profile) == {
            "DAZN F1": None,
            "Teledeporte": None,
            "La 1": "not in includeTags",
            "La 2": "not in includeTags",
        }

    def test_enabled_false_and_member_override(self) -> None:
        groups = {
            "g": {
                "name": "G",
                "enabled": False,
                "members": [{"name": "A"}, {"name": "B", "enabled": True}],
            }
        }
        profile = _spec(todo={"groups": groups}).profiles["todo"]
        assert _decisions(profile) == {"A": "enabled: false", "B": None}

    def test_profiles_from_same_channels_decide_separately(self) -> None:
        spec = _spec(
            todo={"groups": GROUPS, "excludeTags": ["tdt"]},
            tv={"from": "todo", "excludeTags": []},
        )
        assert _decisions(spec.profiles["todo"])["La 1"] == "excludeTags: tdt"
        assert _decisions(spec.profiles["tv"])["La 1"] is None

    def test_placeholders_keep_numbers(self) -> None:
        profile = _spec(
            todo={"groups": GROUPS, "excludeTags": ["tdt"], "keepEmptyChannels": True}
        ).profiles["todo"]
        groups = _plan_groups(profile)
        groups[1].members[0].channels.clear()  # La 1 has no streams
        out = _finalise_channels(groups, profile)
        placeholder = out[1].members[0].channels[0]
        assert placeholder.placeholder and not placeholder.enabled


def test_formatter_shows_reason() -> None:
    channel = ChannelPlan(name="La 1", enabled=False, disabled_by="excludeTags: tdt")
    plan = WaybillPlan(
        manifest_name="m",
        profiles=[
            ProfilePlan(
                key="todo",
                name="Todo",
                groups=[
                    GroupPlan(
                        key="g",
                        name="G",
                        members=[MemberPlan(member_name="La 1", channels=[channel])],
                    )
                ],
            )
        ],
    )
    lines = WaybillPlanFormatter().format(plan)
    assert any("disabled in this profile (excludeTags: tdt)" in line for line in lines)
