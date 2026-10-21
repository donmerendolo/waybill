"""WaybillApplier behaviour.

Dispatcharr 0.32 changes:

- Stream profiles are resolved ignoring case, since 0.32 renamed the built-in
  "ffmpeg"/"streamlink" profiles to "FFmpeg"/"Streamlink".
- Channels copy is_radio from their first stream, like Dispatcharr's own
  create-from-stream and auto-sync.
- Catch-up rollup uses Dispatcharr's rollup_catchup_for_channels when present.

Logos: a channel takes the plan's logo, else its EPG icon, and an existing logo
is never cleared when neither has one.

The Django/Dispatcharr modules are stubbed; src/apply.py is loaded fresh per test.
"""

from __future__ import annotations

import importlib
import importlib.util
import pathlib
import sys
import types
import uuid
from types import SimpleNamespace

import pytest

from src.types.plan import ChannelPlan


class _QuerySet:
    def __init__(self, rows: list[object]) -> None:
        self._rows = rows

    def first(self) -> object | None:
        return self._rows[0] if self._rows else None


class _StreamProfileManager:
    def __init__(self, names: list[str]) -> None:
        self._profiles = [SimpleNamespace(pk=i, name=n) for i, n in enumerate(names, 1)]

    def filter(self, **kwargs: str) -> _QuerySet:
        if "name" in kwargs:
            rows = [p for p in self._profiles if p.name == kwargs["name"]]
        else:
            wanted = kwargs["name__iexact"].lower()
            rows = [p for p in self._profiles if p.name.lower() == wanted]
        return _QuerySet(rows)


class _ChannelStreamQuerySet:
    def __init__(self, flags: list[bool]) -> None:
        self._flags = flags

    def order_by(self, *fields: str) -> "_ChannelStreamQuerySet":
        return self

    def values_list(self, field: str, flat: bool = False) -> _QuerySet:
        assert (field, flat) == ("stream__is_radio", True)
        return _QuerySet(self._flags)


class _ChannelStreamManager:
    def __init__(self) -> None:
        self.flags: list[bool] = []

    def filter(self, **kwargs: object) -> _ChannelStreamQuerySet:
        return _ChannelStreamQuerySet(self.flags)


class _ChannelManager:
    def __init__(self) -> None:
        self.updates: list[dict[str, object]] = []

    def filter(self, **kwargs: object) -> "_ChannelManager":
        return self

    def update(self, **kwargs: object) -> int:
        self.updates.append(kwargs)
        return 1


class _Logger:
    def __init__(self) -> None:
        self.warnings: list[str] = []

    def info(self, message: str) -> None:
        pass

    def warning(self, message: str) -> None:
        self.warnings.append(message)


def _load_apply(
    monkeypatch: pytest.MonkeyPatch,
    profile_names: list[str],
    rollup: object | None = None,
    **models: object,
) -> types.ModuleType:
    repo_root = pathlib.Path(__file__).resolve().parents[2]
    package = f"waybill_applytest_{uuid.uuid4().hex}"
    package_mod = types.ModuleType(package)
    package_mod.__path__ = [str(repo_root / "src")]
    monkeypatch.setitem(sys.modules, package, package_mod)
    for name in ("src.types", "src.types.plan"):
        monkeypatch.setitem(
            sys.modules,
            f"{package}.{name.removeprefix('src.')}",
            importlib.import_module(name),
        )

    def stub(name: str, **attrs: object) -> None:
        mod = types.ModuleType(name)
        for key, value in attrs.items():
            setattr(mod, key, value)
        monkeypatch.setitem(sys.modules, name, mod)

    stub("django.db", transaction=SimpleNamespace())
    stub("django.db.models", Max=object)
    channel_models = {
        "Channel": SimpleNamespace(objects=_ChannelManager()),
        "ChannelGroup": object,
        "ChannelProfile": object,
        "ChannelProfileMembership": object,
        "ChannelStream": SimpleNamespace(objects=_ChannelStreamManager()),
        "Logo": object,
    }
    channel_models.update({k: v for k, v in models.items() if k != "EPGData"})
    stub("apps.channels.models", **channel_models)
    stub("apps.epg.models", EPGData=models.get("EPGData", object))
    stub(
        "core.models",
        StreamProfile=SimpleNamespace(objects=_StreamProfileManager(profile_names)),
    )
    if rollup is None:
        monkeypatch.setitem(sys.modules, "apps.channels.utils", None)
    else:
        stub("apps.channels.utils", rollup_catchup_for_channels=rollup)

    spec = importlib.util.spec_from_file_location(
        f"{package}.apply", repo_root / "src" / "apply.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, spec.name, module)
    spec.loader.exec_module(module)
    return module


def _applier(module: types.ModuleType) -> object:
    plan = SimpleNamespace(profiles=[])
    return module.WaybillApplier(plan, "upsert", _Logger())


class TestStreamProfileLookup:
    def test_old_lowercase_name_finds_renamed_profile(self, monkeypatch) -> None:
        module = _load_apply(monkeypatch, ["Proxy", "FFmpeg", "Streamlink"])
        applier = _applier(module)
        assert applier._resolve_stream_profile("ffmpeg").name == "FFmpeg"
        assert applier._resolve_stream_profile("streamlink").name == "Streamlink"

    def test_exact_name_wins_over_case_insensitive_match(self, monkeypatch) -> None:
        module = _load_apply(monkeypatch, ["FFmpeg", "ffmpeg"])
        applier = _applier(module)
        assert applier._resolve_stream_profile("ffmpeg").pk == 2

    def test_unknown_profile_warns(self, monkeypatch) -> None:
        module = _load_apply(monkeypatch, ["Proxy"])
        applier = _applier(module)
        assert applier._resolve_stream_profile("vlc") is None
        assert applier._logger.warnings


class TestSyncRadio:
    def _run(self, monkeypatch, flags: list[bool], current: bool) -> list[dict]:
        module = _load_apply(monkeypatch, [])
        module.ChannelStream.objects.flags = flags
        channel = SimpleNamespace(pk=7, is_radio=current)
        _applier(module)._sync_radio(channel)
        return module.Channel.objects.updates

    def test_copies_radio_flag_from_first_stream(self, monkeypatch) -> None:
        assert self._run(monkeypatch, [True, False], current=False) == [
            {"is_radio": True}
        ]

    def test_no_write_when_already_equal(self, monkeypatch) -> None:
        assert self._run(monkeypatch, [False], current=False) == []

    def test_channel_without_streams_keeps_value(self, monkeypatch) -> None:
        assert self._run(monkeypatch, [], current=True) == []

    def test_older_dispatcharr_without_field_is_skipped(self, monkeypatch) -> None:
        module = _load_apply(monkeypatch, [])
        module.ChannelStream.objects.flags = [True]
        _applier(module)._sync_radio(SimpleNamespace(pk=7))
        assert module.Channel.objects.updates == []


class TestCatchupRollup:
    def test_uses_dispatcharr_helper_when_available(self, monkeypatch) -> None:
        calls: list[list[int]] = []
        module = _load_apply(monkeypatch, [], rollup=calls.append)
        _applier(module)._rollup_catchup(SimpleNamespace(pk=3, is_catchup=False))
        assert calls == [[3]]
        assert module.Channel.objects.updates == []


class _LogoManager:
    def __init__(self) -> None:
        self.logos: dict[str, SimpleNamespace] = {}

    def get_or_create(self, url: str, defaults: dict[str, str]):
        if url in self.logos:
            return self.logos[url], False
        logo = SimpleNamespace(pk=len(self.logos) + 1, url=url, **defaults)
        self.logos[url] = logo
        return logo, True


class _EPGDataManager:
    def __init__(self, entries: list[SimpleNamespace]) -> None:
        self._entries = entries

    def filter(self, tvg_id: str) -> _QuerySet:
        return _QuerySet([e for e in self._entries if e.tvg_id == tvg_id])


class _ExistingChannel:
    def __init__(self, logo_id: int | None) -> None:
        self.pk = 1
        self.name = "DAZN F1"
        self.tvg_id = "DAZN F1 HD"
        self.logo_id = logo_id
        self.epg_data_id = 1
        self.stream_profile_id = None
        self.channel_number = 1
        self.saved_fields: list[str] = []

    def save(self, update_fields: list[str]) -> None:
        self.saved_fields.extend(update_fields)


class _ApplyChannelManager(_ChannelManager):
    def __init__(self, channel: _ExistingChannel) -> None:
        super().__init__()
        self.channel = channel

    def get_or_create(self, **kwargs: object):
        return self.channel, False


class _Deletable:
    def delete(self) -> tuple[int, dict]:
        return 0, {}


class _ApplyChannelStreamManager(_ChannelStreamManager):
    def filter(self, **kwargs: object):
        return _Deletable()

    def bulk_create(self, rows: list[object], ignore_conflicts: bool = False) -> None:
        pass


class TestChannelLogo:
    def _load(self, monkeypatch, epg_entries: list[SimpleNamespace], channel=None):
        logos = _LogoManager()
        models: dict[str, object] = {
            "Logo": SimpleNamespace(objects=logos),
            "EPGData": SimpleNamespace(objects=_EPGDataManager(epg_entries)),
        }
        if channel is not None:
            models["Channel"] = SimpleNamespace(objects=_ApplyChannelManager(channel))
            memberships: list[dict] = []
            models["ChannelProfileMembership"] = SimpleNamespace(
                objects=SimpleNamespace(
                    update_or_create=lambda **kw: (
                        memberships.append(kw) or (None, False)
                    )
                ),
                calls=memberships,
            )
            stream_manager = _ApplyChannelStreamManager()
            models["ChannelStream"] = type(
                "ChannelStream",
                (),
                {"objects": stream_manager, "__init__": _channel_stream_init},
            )
        return _load_apply(monkeypatch, [], **models), logos

    def test_plan_logo_wins_over_epg_icon(self, monkeypatch) -> None:
        epg = SimpleNamespace(pk=1, tvg_id="X", name="X", icon_url="http://epg/x.png")
        module, _ = self._load(monkeypatch, [epg])
        logo = _applier(module)._resolve_channel_logo("http://m/x.png", epg, "X")
        assert logo.url == "http://m/x.png"

    def test_falls_back_to_epg_icon(self, monkeypatch) -> None:
        epg = SimpleNamespace(
            pk=1, tvg_id="X", name="X HD", icon_url=" http://epg/x.png "
        )
        module, _ = self._load(monkeypatch, [epg])
        logo = _applier(module)._resolve_channel_logo(None, epg, "X")
        assert (logo.url, logo.name) == ("http://epg/x.png", "X HD")

    def test_no_logo_anywhere(self, monkeypatch) -> None:
        epg = SimpleNamespace(pk=1, tvg_id="X", name="X", icon_url=None)
        module, _ = self._load(monkeypatch, [epg])
        applier = _applier(module)
        assert applier._resolve_channel_logo(None, epg, "X") is None
        assert applier._resolve_channel_logo(None, None, "X") is None

    def _apply(self, monkeypatch, epg_entries, current_logo_id, **plan_fields):
        channel = _ExistingChannel(logo_id=current_logo_id)
        module, logos = self._load(monkeypatch, epg_entries, channel=channel)
        channel_plan = ChannelPlan(
            name="DAZN F1", epg_id="DAZN F1 HD", channel_number=1, **plan_fields
        )
        _applier(module)._apply_channel(
            channel_plan, SimpleNamespace(name="DAZN"), None
        )
        self.memberships = module.ChannelProfileMembership.calls
        return channel, logos

    def test_apply_keeps_existing_logo_when_nothing_provides_one(
        self, monkeypatch
    ) -> None:
        epg = SimpleNamespace(pk=1, tvg_id="DAZN F1 HD", name="DAZN F1", icon_url="")
        channel, _ = self._apply(monkeypatch, [epg], current_logo_id=42)
        assert channel.logo_id == 42
        assert "logo" not in channel.saved_fields

    def test_apply_sets_epg_icon_as_logo(self, monkeypatch) -> None:
        epg = SimpleNamespace(
            pk=1, tvg_id="DAZN F1 HD", name="DAZN F1", icon_url="http://epg/f1.png"
        )
        channel, logos = self._apply(monkeypatch, [epg], current_logo_id=42)
        assert channel.logo_id == logos.logos["http://epg/f1.png"].pk
        assert "logo" in channel.saved_fields


def _channel_stream_init(self, **kwargs: object) -> None:
    self.__dict__.update(kwargs)


class TestEpgScheduleApply(TestChannelLogo):
    """The profile membership follows the plan's epgSchedule decision."""

    EPG = [SimpleNamespace(pk=1, tvg_id="DAZN F1 HD", name="DAZN F1", icon_url="")]

    def test_disabled_channel_is_disabled_in_profile(self, monkeypatch) -> None:
        self._apply(
            monkeypatch,
            self.EPG,
            1,
            enabled=False,
            schedule="off: no matching programme",
        )
        assert [m["defaults"] for m in self.memberships] == [{"enabled": False}]

    def test_enabled_by_default(self, monkeypatch) -> None:
        self._apply(monkeypatch, self.EPG, 1)
        assert [m["defaults"] for m in self.memberships] == [{"enabled": True}]
