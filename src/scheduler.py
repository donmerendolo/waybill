"""Auto-apply timer (plugin setting auto_apply_minutes) on Dispatcharr's Celery beat.

A django-celery-beat PeriodicTask runs the `waybill.auto_apply_tick` task every
minute. Each tick re-reads the setting and the plugin's enabled flag, and runs
Apply through Dispatcharr's PluginManager.run_action (exactly like pressing
Apply) once the interval has passed since the last timed run. Changing the
setting needs no restart, and 0 turns the timer off.

The task is defined in the plugin's own module, so Dispatcharr 0.33+ treats the
schedule as the plugin's: it pauses it while the plugin is disabled and deletes
it with the plugin. On older versions the tick checks the enabled flag itself.

Applies are serialised through a Redis lock (Dispatcharr's Django cache), so a
timed run never overlaps a manual or API one, even across containers.
"""

from __future__ import annotations

import contextlib
import logging
import os
import time
import uuid
from collections.abc import Iterator
from typing import Any

try:
    from celery import shared_task
except ImportError:  # outside Dispatcharr (tests): keep the function plain

    def shared_task(*_args: Any, **_kwargs: Any):  # type: ignore[no-redef]
        def decorate(func):
            return func

        return decorate


SETTING = "auto_apply_minutes"
TASK_NAME = "waybill.auto_apply_tick"
SCHEDULE_NAME = "waybill: auto-apply"
_LAST_RUN_KEY = "waybill:auto_apply:last_run"
_APPLY_LOCK_KEY = "waybill:apply:lock"
_APPLY_LOCK_TTL = 30 * 60  # seconds; frees the lock if a worker dies mid-apply
# Beat ticks are about a minute apart; run when the interval is nearly up so a
# 5-minute setting does not slip to 6 minutes.
_TICK_SLACK_SECONDS = 30

_logger = logging.getLogger("plugins.waybill.scheduler")


def plugin_key() -> str:
    """The plugin's key in Dispatcharr: the name of its folder."""
    return os.path.basename(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def interval_minutes(settings: "dict | None") -> int:
    """The auto_apply_minutes setting as a whole number of minutes; 0 when off or invalid."""
    raw = (settings or {}).get(SETTING, 0)
    if isinstance(raw, bool):
        return 0
    try:
        return max(0, int(float(raw)))
    except (TypeError, ValueError):
        return 0


def ensure_schedule() -> None:
    """Create the every-minute PeriodicTask for the tick if it does not exist.

    An existing row is left as it is, so a schedule Dispatcharr paused (plugin
    disabled) is not switched back on here.
    """
    from django_celery_beat.models import IntervalSchedule, PeriodicTask

    interval, _ = IntervalSchedule.objects.get_or_create(
        every=1, period=IntervalSchedule.MINUTES
    )
    PeriodicTask.objects.get_or_create(
        name=SCHEDULE_NAME,
        defaults={"task": TASK_NAME, "interval": interval, "enabled": True},
    )


def is_due(last_run: "float | None", now: float, minutes: int) -> bool:
    """Whether a timed apply is due, given the last one's time (epoch seconds)."""
    if minutes <= 0:
        return False
    if last_run is None:
        return True
    return now - last_run >= minutes * 60 - _TICK_SLACK_SECONDS


def _read_interval(key: str) -> "int | None":
    """Minutes between runs (0 = off or plugin disabled), or None if the plugin is gone."""
    from apps.plugins.models import PluginConfig

    cfg = PluginConfig.objects.filter(key=key).first()
    if cfg is None:
        return None
    if not cfg.enabled:
        return 0
    return interval_minutes(cfg.settings)


@shared_task(name=TASK_NAME, ignore_result=True)
def auto_apply_tick() -> str:
    """Celery beat tick: run Apply when auto_apply_minutes have passed."""
    from django.core.cache import cache

    key = plugin_key()
    minutes = _read_interval(key)
    if not minutes:
        return "off"
    now = time.time()
    last = cache.get(_LAST_RUN_KEY)
    if not is_due(float(last) if last is not None else None, now, minutes):
        return "not due"
    # Record the attempt first: a failing apply is retried after the next
    # interval instead of on every tick.
    cache.set(_LAST_RUN_KEY, now, timeout=None)
    from apps.plugins.loader import PluginManager

    try:
        PluginManager.get().run_action(key, "apply", {})
    except Exception:  # noqa: BLE001 - logged; the next interval tries again
        _logger.exception("waybill: auto-apply failed")
        return "failed"
    return "applied"


@contextlib.contextmanager
def apply_lock(wait_seconds: float = 600) -> Iterator[None]:
    """Serialise applies (a timed run and a manual or API one) through Redis.

    Waits up to *wait_seconds* for a running apply to finish. Without a usable
    Django cache (e.g. outside Dispatcharr) it does not lock.
    """
    token = uuid.uuid4().hex
    try:
        from django.core.cache import cache

        acquired = cache.add(_APPLY_LOCK_KEY, token, timeout=_APPLY_LOCK_TTL)
    except Exception:  # noqa: BLE001 - no cache configured: nothing to lock against
        yield
        return
    deadline = time.monotonic() + wait_seconds
    while not acquired:
        if time.monotonic() > deadline:
            raise TimeoutError("another Waybill apply is still running")
        time.sleep(1)
        acquired = cache.add(_APPLY_LOCK_KEY, token, timeout=_APPLY_LOCK_TTL)
    try:
        yield
    finally:
        if cache.get(_APPLY_LOCK_KEY) == token:
            cache.delete(_APPLY_LOCK_KEY)
