"""Auto-apply timer: due logic, the beat tick, and the apply lock."""

from __future__ import annotations

import sys
import types
from types import SimpleNamespace

import pytest

from src import scheduler


class TestIsDue:
    def test_off(self) -> None:
        assert not scheduler.is_due(None, 1000.0, 0)

    def test_first_run(self) -> None:
        assert scheduler.is_due(None, 1000.0, 5)

    def test_waits_for_the_interval_with_tick_slack(self) -> None:
        assert not scheduler.is_due(1000.0, 1000.0 + 269, 5)
        assert scheduler.is_due(1000.0, 1000.0 + 270, 5)


class _Cache:
    def __init__(self) -> None:
        self.data: dict[str, object] = {}

    def get(self, key):
        return self.data.get(key)

    def set(self, key, value, timeout=None):
        self.data[key] = value

    def add(self, key, value, timeout=None):
        if key in self.data:
            return False
        self.data[key] = value
        return True

    def delete(self, key):
        self.data.pop(key, None)


@pytest.fixture()
def dispatcharr(monkeypatch):
    """Stub the Dispatcharr/Django pieces the tick touches."""
    cache = _Cache()
    configs: dict[str, SimpleNamespace] = {}
    runs: list[tuple[str, str]] = []

    def stub(name, **attrs):
        mod = types.ModuleType(name)
        for k, v in attrs.items():
            setattr(mod, k, v)
        monkeypatch.setitem(sys.modules, name, mod)

    stub("django.core.cache", cache=cache)
    stub(
        "apps.plugins.models",
        PluginConfig=SimpleNamespace(
            objects=SimpleNamespace(
                filter=lambda key: SimpleNamespace(first=lambda: configs.get(key))
            )
        ),
    )
    manager = SimpleNamespace(run_action=lambda key, action, params: runs.append((key, action)))
    stub("apps.plugins.loader", PluginManager=SimpleNamespace(get=lambda: manager))
    monkeypatch.setattr(scheduler, "plugin_key", lambda: "waybill")
    return SimpleNamespace(cache=cache, configs=configs, runs=runs, manager=manager)


class TestTick:
    def test_plugin_missing_or_disabled_or_off(self, dispatcharr) -> None:
        assert scheduler.auto_apply_tick() == "off"
        dispatcharr.configs["waybill"] = SimpleNamespace(enabled=False, settings={"auto_apply_minutes": 5})
        assert scheduler.auto_apply_tick() == "off"
        dispatcharr.configs["waybill"] = SimpleNamespace(enabled=True, settings={"auto_apply_minutes": 0})
        assert scheduler.auto_apply_tick() == "off"
        assert dispatcharr.runs == []

    def test_runs_then_waits(self, dispatcharr, monkeypatch) -> None:
        dispatcharr.configs["waybill"] = SimpleNamespace(enabled=True, settings={"auto_apply_minutes": 5})
        now = [1000.0]
        monkeypatch.setattr(scheduler.time, "time", lambda: now[0])
        assert scheduler.auto_apply_tick() == "applied"
        now[0] += 60
        assert scheduler.auto_apply_tick() == "not due"
        now[0] += 240
        assert scheduler.auto_apply_tick() == "applied"
        assert dispatcharr.runs == [("waybill", "apply"), ("waybill", "apply")]

    def test_failure_is_logged_and_waits_for_next_interval(self, dispatcharr, monkeypatch) -> None:
        dispatcharr.configs["waybill"] = SimpleNamespace(enabled=True, settings={"auto_apply_minutes": 5})

        def boom(*_):
            raise RuntimeError("db down")

        dispatcharr.manager.run_action = boom
        assert scheduler.auto_apply_tick() == "failed"
        assert scheduler.auto_apply_tick() == "not due"


class TestApplyLock:
    def test_serialises_and_releases(self, dispatcharr, monkeypatch) -> None:
        with scheduler.apply_lock():
            assert scheduler._APPLY_LOCK_KEY in dispatcharr.cache.data
            monkeypatch.setattr(scheduler.time, "sleep", lambda s: None)
            with pytest.raises(TimeoutError):
                with scheduler.apply_lock(wait_seconds=0):
                    pass
        assert scheduler._APPLY_LOCK_KEY not in dispatcharr.cache.data

    def test_no_cache_means_no_lock(self, monkeypatch) -> None:
        monkeypatch.setitem(sys.modules, "django.core.cache", None)
        with scheduler.apply_lock():
            pass
