"""Pipeline-level regression tests for validator scoping.

These tests stub the minimal Django/Dispatcharr surfaces needed to exercise
MemberPipeline.process() without importing the full runtime.
"""

from __future__ import annotations

import sys
import types
from types import SimpleNamespace


class _Q:
    def __init__(self, **kwargs: object) -> None:
        self.kwargs = kwargs

    def __and__(self, other: "_Q") -> "_Q":
        return self

    def __or__(self, other: "_Q") -> "_Q":
        return self


class _StreamQuerySet:
    def __init__(self, streams: list[object]) -> None:
        self._streams = streams

    def only(self, *fields: object) -> "_StreamQuerySet":
        del fields
        return self

    def iterator(self, chunk_size: int = 1000):
        del chunk_size
        return iter(self._streams)


class _StreamManager:
    def __init__(self, streams: list[object]) -> None:
        self._streams = streams

    def filter(self, q_filter: object) -> _StreamQuerySet:
        del q_filter
        return _StreamQuerySet(self._streams)


class _StreamStub:
    objects = _StreamManager([])

    def __init__(
        self,
        *,
        pk: int,
        name: str,
        tvg_id: str | None = None,
        logo_url: str | None = None,
        stream_stats: dict | None = None,
        url: str | None = None,
        stream_hash: str | None = None,
    ) -> None:
        self.pk = pk
        self.name = name
        self.tvg_id = tvg_id
        self.logo_url = logo_url
        self.stream_stats = stream_stats
        self.url = url
        self.stream_hash = stream_hash


_channels_models = types.ModuleType("apps.channels.models")
_channels_models.Stream = _StreamStub  # type: ignore[attr-defined]

_django_models = types.ModuleType("django.db.models")
_django_models.Q = _Q  # type: ignore[attr-defined]

_num2words_mod = types.ModuleType("num2words")
_num2words_mod.num2words = lambda *args, **kwargs: ""  # type: ignore[attr-defined]

_w2n_mod = types.ModuleType("word2number")
_w2n_mod.w2n = SimpleNamespace(word_to_num=lambda s: int(s))  # type: ignore[attr-defined]

for _mod_name, _mod in (
    ("apps", types.ModuleType("apps")),
    ("apps.channels", types.ModuleType("apps.channels")),
    ("apps.channels.models", _channels_models),
    ("django", types.ModuleType("django")),
    ("django.db", types.ModuleType("django.db")),
    ("django.db.models", _django_models),
    ("num2words", _num2words_mod),
    ("word2number", _w2n_mod),
):
    sys.modules[_mod_name] = _mod

import src.pipeline as pipeline_module  # noqa: E402
from src.types.config import ConfigMember, OrderStreamsBy  # noqa: E402

pipeline_module.Stream = _StreamStub
MemberPipeline = pipeline_module.MemberPipeline


def _set_streams(*streams: _StreamStub) -> None:
    pipeline_module.Stream = _StreamStub
    _StreamStub.objects = _StreamManager(list(streams))


def test_member_scope_count_violates_when_no_channels_are_built() -> None:
    _set_streams()
    member = ConfigMember(
        name="Arena Sports",
        validators=[
            {
                "type": "count",
                "operator": "gt",
                "value": 0,
                "scope": "member",
                "action": "warn",
            }
        ],
    )

    result = MemberPipeline(member).process()

    assert result.channels == []
    assert len(result.violations) == 1
    assert result.violations[0].scope == "member"
    assert result.violations[0].target == "Arena Sports"


def test_channel_scope_non_empty_uses_assembled_epg_id() -> None:
    _set_streams(
        _StreamStub(pk=1, name="NBS One", tvg_id="bbc.one"),
        _StreamStub(pk=2, name="NBS One", tvg_id="bbc.one"),
        _StreamStub(pk=3, name="NBS One", tvg_id=""),
    )
    member = ConfigMember(
        name="NBS One",
        validators=[
            {
                "type": "nonEmpty",
                "field": "tvg_id",
                "scope": "channel",
                "action": "warn",
            }
        ],
    )

    result = MemberPipeline(member).process()

    assert [channel.name for channel in result.channels] == ["NBS One"]
    assert result.channels[0].epg_id == "bbc.one"
    assert result.violations == []


def test_set_metadata_tvg_id_and_logo_reach_channel_plan() -> None:
    _set_streams(
        _StreamStub(pk=1, name="CANAL+ SPORT", tvg_id="", logo_url=""),
        _StreamStub(pk=2, name="CANAL+ SPORT 3", tvg_id=None, logo_url=None),
    )
    member = ConfigMember(
        name="Canal+ Sport",
        transformers=[
            {
                "type": "setMetadata",
                "name": "Canal+ Sport",
                "tvgId": "canal.sport",
                "logoUrl": "https://example.com/canal-sport.png",
            }
        ],
    )

    result = MemberPipeline(member).process()

    assert [channel.name for channel in result.channels] == ["Canal+ Sport"]
    assert result.channels[0].epg_id == "canal.sport"
    assert result.channels[0].logo_url == "https://example.com/canal-sport.png"
    assert [s.tvg_id for s in result.channels[0].streams] == [
        "canal.sport",
        "canal.sport",
    ]


def test_stream_metadata_used_when_not_overridden() -> None:
    _set_streams(
        _StreamStub(pk=1, name="DAZN 1 FHD", tvg_id="DAZN 1 HD", logo_url="a.png"),
    )
    member = ConfigMember(
        name="DAZN 1",
        transformers=[{"type": "setMetadata", "name": "DAZN 1"}],
    )

    result = MemberPipeline(member).process()

    assert result.channels[0].epg_id == "DAZN 1 HD"
    assert result.channels[0].logo_url == "a.png"


def _numbering_config(start: object) -> "object":
    from src.types.config import WaybillConfig

    return WaybillConfig(
        kind="WaybillConfig",
        version="v1alpha1",
        metadata={"name": "numbering"},
        spec={
            "profiles": {
                "todo": {
                    "name": "Todo",
                    "startChannelNumber": start,
                    "groups": {
                        "dazn": {
                            "name": "DAZN",
                            "members": [
                                {
                                    "name": "DAZN F1",
                                    "matchers": [
                                        {"type": "exactMatch", "values": ["DAZN F1"]}
                                    ],
                                },
                                {
                                    "name": "DAZN 1",
                                    "matchers": [
                                        {"type": "exactMatch", "values": ["DAZN 1"]}
                                    ],
                                },
                            ],
                        },
                        "movistar": {
                            "name": "Movistar",
                            "members": [
                                {
                                    "name": "M+ LaLiga",
                                    "matchers": [
                                        {"type": "exactMatch", "values": ["M+ LaLiga"]}
                                    ],
                                },
                            ],
                        },
                    },
                }
            }
        },
    )


def _numbered_channels(start: object) -> "list[tuple[str, int | None]]":
    # Deliberately not in manifest order, to prove numbering follows the manifest.
    _set_streams(
        _StreamStub(pk=1, name="M+ LaLiga"),
        _StreamStub(pk=2, name="DAZN 1"),
        _StreamStub(pk=3, name="DAZN F1"),
    )
    plan = pipeline_module.WaybillPipeline(_numbering_config(start)).compute_plan()
    return [
        (channel.name, channel.channel_number)
        for group in plan.profiles[0].groups
        for member in group.members
        for channel in member.channels
    ]


def test_start_channel_number_numbers_channels_in_manifest_order() -> None:
    assert _numbered_channels(100) == [
        ("DAZN F1", 100),
        ("DAZN 1", 101),
        ("M+ LaLiga", 102),
    ]


def test_channels_unnumbered_without_start_channel_number() -> None:
    assert [n for _, n in _numbered_channels(None)] == [None, None, None]


def _pinned_config(keep_empty: bool) -> "object":
    from src.types.config import WaybillConfig

    def member(name: str, number: int) -> dict:
        return {
            "name": name,
            "channelNumber": number,
            "matchers": [{"type": "exactMatch", "values": [name]}],
        }

    return WaybillConfig(
        kind="WaybillConfig",
        version="v1alpha1",
        metadata={"name": "pinned"},
        spec={
            "profiles": {
                "todo": {
                    "keepEmptyChannels": keep_empty,
                    "groups": {
                        "g": {
                            "name": "G",
                            # Channel 2 was deleted from the manifest: no renumbering.
                            "members": [
                                member("One", 1),
                                member("Three", 3),
                                member("Four", 4),
                            ],
                        }
                    },
                }
            }
        },
    )


def _plan_channels(config: object) -> "list[tuple[str, int | None, bool, int]]":
    plan = pipeline_module.WaybillPipeline(config).compute_plan()
    return [
        (c.name, c.channel_number, c.placeholder, len(c.streams))
        for g in plan.profiles[0].groups
        for m in g.members
        for c in m.channels
    ]


def test_pinned_channel_numbers_leave_gaps_and_do_not_shift() -> None:
    _set_streams(
        _StreamStub(pk=1, name="One"),
        _StreamStub(pk=3, name="Three"),
        _StreamStub(pk=4, name="Four"),
    )

    assert _plan_channels(_pinned_config(False)) == [
        ("One", 1, False, 1),
        ("Three", 3, False, 1),
        ("Four", 4, False, 1),
    ]


def test_keep_empty_channels_keeps_numbered_placeholder() -> None:
    _set_streams(_StreamStub(pk=1, name="One"), _StreamStub(pk=4, name="Four"))

    assert _plan_channels(_pinned_config(True)) == [
        ("One", 1, False, 1),
        ("Three", 3, True, 0),
        ("Four", 4, False, 1),
    ]


def test_empty_member_dropped_without_keep_empty_channels() -> None:
    _set_streams(_StreamStub(pk=1, name="One"), _StreamStub(pk=4, name="Four"))

    assert [name for name, *_ in _plan_channels(_pinned_config(False))] == [
        "One",
        "Four",
    ]


def test_channel_number_pin_continues_sequence() -> None:
    from src.types.config import WaybillConfig

    _set_streams(*(_StreamStub(pk=i, name=n) for i, n in enumerate("ABCD", 1)))
    config = WaybillConfig(
        kind="WaybillConfig",
        version="v1alpha1",
        metadata={"name": "mixed"},
        spec={
            "profiles": {
                "p": {
                    "startChannelNumber": 1,
                    "groups": {
                        "g": {
                            "name": "G",
                            "members": [
                                {
                                    "name": "A",
                                    "matchers": [
                                        {"type": "exactMatch", "values": ["A"]}
                                    ],
                                },
                                {
                                    "name": "B",
                                    "channelNumber": 10,
                                    "matchers": [
                                        {"type": "exactMatch", "values": ["B"]}
                                    ],
                                },
                                {
                                    "name": "C",
                                    "matchers": [
                                        {"type": "exactMatch", "values": ["C"]}
                                    ],
                                },
                            ],
                        }
                    },
                }
            }
        },
    )

    assert [(n, num) for n, num, *_ in _plan_channels(config)] == [
        ("A", 1),
        ("B", 10),
        ("C", 11),
    ]


def _hd(height: int) -> dict:
    return {"resolution": f"1920x{height}", "video_bitrate": 1000}


def _priority_member(priorities: dict[str, int], quality: bool = True) -> ConfigMember:
    from src.types.config import OrderStreamsBy

    return ConfigMember(
        name="DAZN 1",
        matchers=[{"type": "exactMatch", "values": ["DAZN 1"]}],
        stream_priorities=priorities,
        order_streams_by=OrderStreamsBy.QUALITY if quality else None,
    )


def _ordered(member: ConfigMember) -> "tuple[list[int], list[str | None], list[str]]":
    result = MemberPipeline(member).process()
    streams = result.channels[0].streams
    return (
        [s.id for s in streams],
        [s.order_reason for s in streams],
        result.unmatched_priorities,
    )


def _ace(pk: int, ace_id: str, height: int) -> _StreamStub:
    return _StreamStub(
        pk=pk,
        name="DAZN 1",
        url=f"http://x/ace/getstream?id={ace_id}",
        stream_stats=_hd(height),
    )


def test_priority_beats_quality_and_negative_sinks_to_bottom() -> None:
    _set_streams(
        _ace(1, "aaa", 1080),  # unlisted: priority 0
        _ace(2, "bbb", 480),  # trusted but low quality
        _ace(3, "ccc", 2160),  # best quality but unreliable
        _ace(4, "ddd", 720),  # unlisted: priority 0
    )

    ids, reasons, unmatched = _ordered(_priority_member({"bbb": 5, "ccc": -1}))

    # 5 first; the two 0s by quality; -1 last despite being 4K.
    assert ids == [2, 1, 4, 3]
    assert reasons[0] == "priority 5, quality: 480p, 1000kbps"
    assert reasons[-1] == "priority -1, quality: 2160p, 1000kbps"
    assert unmatched == []


def test_equal_priorities_fall_back_to_quality() -> None:
    _set_streams(_ace(1, "aaa", 720), _ace(2, "bbb", 1080))

    ids, _, _ = _ordered(_priority_member({"aaa": 3, "bbb": 3}))

    assert ids == [2, 1]


def test_priorities_without_quality_keep_pipeline_order_on_ties() -> None:
    _set_streams(_ace(1, "aaa", 0), _ace(2, "bbb", 0), _ace(3, "ccc", 0))

    ids, reasons, _ = _ordered(_priority_member({"ccc": 1}, quality=False))

    assert ids == [3, 1, 2]
    assert reasons == ["priority 1", None, None]


def test_priority_matches_stream_hash_exactly() -> None:
    _set_streams(
        _StreamStub(pk=1, name="DAZN 1", url="http://x/1", stream_hash="h1"),
        _StreamStub(pk=2, name="DAZN 1", url="http://x/2", stream_hash="h2"),
    )

    ids, _, _ = _ordered(_priority_member({"h2": 1}, quality=False))

    assert ids == [2, 1]


def test_first_matching_priority_key_wins() -> None:
    _set_streams(_ace(1, "abc123", 0), _ace(2, "zzz", 0))

    # Both keys match stream 1's URL; the first listed (-5) applies.
    ids, reasons, unmatched = _ordered(
        _priority_member({"abc": -5, "abc123": 9}, quality=False)
    )

    assert ids == [2, 1]
    assert reasons == [None, "priority -5"]
    assert unmatched == ["abc123"]


def test_priority_keys_matching_nothing_are_reported() -> None:
    _set_streams(_ace(1, "aaa", 0))

    _, _, unmatched = _ordered(_priority_member({"zzz": 1, "aaa": 1}, quality=False))

    assert unmatched == ["zzz"]


def test_without_priorities_order_is_unchanged() -> None:
    _set_streams(_ace(1, "aaa", 720), _ace(2, "bbb", 1080))

    ids, reasons, unmatched = _ordered(_priority_member({}))

    assert ids == [2, 1]
    assert reasons == ["quality: 1080p, 1000kbps", "quality: 720p, 1000kbps"]
    assert unmatched == []


def test_priority_matches_stream_name_ignoring_case() -> None:
    _set_streams(
        _StreamStub(pk=1, name="DAZN 1 FHD", stream_stats=_hd(1080)),
        _StreamStub(pk=2, name="DAZN 1", stream_stats=_hd(2160)),
        _StreamStub(pk=3, name="DAZN 1 FHD", stream_stats=_hd(720)),
    )
    member = ConfigMember(
        name="DAZN 1",
        matchers=[{"type": "exactMatch", "values": ["DAZN 1", "DAZN 1 FHD"]}],
        transformers=[{"type": "setMetadata", "name": "DAZN 1"}],
        stream_priorities={"dazn 1 fhd": 5},
        order_streams_by=OrderStreamsBy.QUALITY,
    )

    ids, reasons, unmatched = _ordered(member)

    # Both "DAZN 1 FHD" feeds get priority 5 (ordered by quality between them),
    # ahead of the higher-quality "DAZN 1" feed at priority 0.
    assert ids == [1, 3, 2]
    assert reasons[0].startswith("priority 5")
    assert unmatched == []


def test_priority_key_never_adds_a_stream_to_a_channel() -> None:
    _set_streams(
        _StreamStub(pk=1, name="DAZN 1"),
        _StreamStub(pk=2, name="DAZN 2"),
    )
    member = ConfigMember(
        name="DAZN 1",
        matchers=[{"type": "exactMatch", "values": ["DAZN 1"]}],
        stream_priorities={"DAZN 2": 99},
    )

    ids, _, unmatched = _ordered(member)

    assert ids == [1]
    assert unmatched == ["DAZN 2"]
