"""Tests for the orderStreamsBy configuration property.

Covers:
- OrderStreamsBy enum coercion at all three scope levels
- Invalid values raise ValueError
- null / None clears the value
- Inheritance chain: profile → group → member
"""

from __future__ import annotations

import pytest

from src.types.config import (
    OrderStreamsBy,
    WaybillConfig,
)


# Helpers


def _make_config(spec_payload: dict) -> WaybillConfig:
    return WaybillConfig(
        kind="WaybillConfig",
        version="v1alpha1",
        metadata={"name": "test"},
        spec=spec_payload,
    )


def _profile_dict(
    *,
    profile_osb: object = None,
    group_osb: object = None,
    member_osb: object = None,
) -> dict:
    return {
        "profiles": {
            "p": {
                "name": "P",
                "orderStreamsBy": profile_osb,
                "groups": {
                    "g": {
                        "name": "G",
                        "orderStreamsBy": group_osb,
                        "members": [
                            {
                                "name": "m",
                                "orderStreamsBy": member_osb,
                            }
                        ],
                    }
                },
            }
        }
    }


def _at_scope(cfg: WaybillConfig, scope: str):
    profile = cfg.spec.profiles["p"]
    if scope == "profile":
        return profile
    group = profile.groups["g"]
    if scope == "group":
        return group
    return group.members[0]


_OSB_KWARG = {
    "profile": "profile_osb",
    "group": "group_osb",
    "member": "member_osb",
}


# Per-scope coercion


@pytest.mark.parametrize("scope", ["profile", "group", "member"])
def test_order_streams_by_quality(scope: str) -> None:
    cfg = _make_config(_profile_dict(**{_OSB_KWARG[scope]: "quality"}))
    assert _at_scope(cfg, scope).order_streams_by is OrderStreamsBy.QUALITY


@pytest.mark.parametrize("scope", ["profile", "group", "member"])
def test_order_streams_by_none_when_absent(scope: str) -> None:
    cfg = _make_config(_profile_dict())
    assert _at_scope(cfg, scope).order_streams_by is None


@pytest.mark.parametrize("scope", ["profile", "group", "member"])
def test_order_streams_by_null_yaml(scope: str) -> None:
    cfg = _make_config(_profile_dict(**{_OSB_KWARG[scope]: None}))
    assert _at_scope(cfg, scope).order_streams_by is None


@pytest.mark.parametrize("scope", ["profile", "group", "member"])
def test_order_streams_by_invalid_raises(scope: str) -> None:
    with pytest.raises(ValueError):
        _make_config(_profile_dict(**{_OSB_KWARG[scope]: "random"}))


# Inheritance chain


def test_inheritance_profile_to_group_to_member() -> None:
    """Profile sets quality; group and member inherit (both absent)."""
    cfg = _make_config(_profile_dict(profile_osb="quality"))
    assert cfg.spec.profiles["p"].order_streams_by is OrderStreamsBy.QUALITY
    # The config dataclasses store their own declared values only;
    # inheritance resolution is the pipeline's responsibility.
    assert cfg.spec.profiles["p"].groups["g"].order_streams_by is None
    assert cfg.spec.profiles["p"].groups["g"].members[0].order_streams_by is None


def test_group_null_clears_profile_value() -> None:
    """Group explicitly sets null; even if profile has quality, group clears it."""
    cfg = _make_config(_profile_dict(profile_osb="quality", group_osb=None))
    assert cfg.spec.profiles["p"].order_streams_by is OrderStreamsBy.QUALITY
    assert cfg.spec.profiles["p"].groups["g"].order_streams_by is None


def test_member_re_enables_when_group_cleared() -> None:
    """Member sets quality even though the group cleared the inherited value."""
    cfg = _make_config(
        _profile_dict(profile_osb="quality", group_osb=None, member_osb="quality")
    )
    assert cfg.spec.profiles["p"].groups["g"].order_streams_by is None
    assert (
        cfg.spec.profiles["p"].groups["g"].members[0].order_streams_by
        is OrderStreamsBy.QUALITY
    )


# ---------------------------------------------------------------------------
# List form: an explicit, ordered set of sort criteria
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("scope", ["profile", "group", "member"])
def test_order_streams_by_list_at_every_scope(scope: str) -> None:
    kwargs = {f"{scope}_osb": ["priority", "quality", "m3u"]}
    cfg = _make_config(_profile_dict(**kwargs))

    assert _at_scope(cfg, scope).order_streams_by == [
        OrderStreamsBy.PRIORITY,
        OrderStreamsBy.QUALITY,
        OrderStreamsBy.M3U,
    ]


@pytest.mark.parametrize(
    ("value", "message"),
    [
        ([], "at least one criterion"),
        (["quality", "quality"], "criterion twice"),
        (["quality", "speed"], "speed"),
    ],
)
def test_order_streams_by_list_rejects_invalid(value: object, message: str) -> None:
    with pytest.raises(ValueError, match=message):
        _make_config(_profile_dict(profile_osb=value))


def test_stream_sort_criteria_resolution() -> None:
    from src.types.config import stream_sort_criteria

    p, m, q = OrderStreamsBy.PRIORITY, OrderStreamsBy.M3U, OrderStreamsBy.QUALITY
    assert stream_sort_criteria(None) == (p, m)
    assert stream_sort_criteria(q) == (p, m, q)  # legacy meaning of "quality"
    assert stream_sort_criteria(p) == (p,)
    assert stream_sort_criteria([q, m]) == (q, m)  # a list states everything
