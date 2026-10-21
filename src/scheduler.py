"""Optional timer that runs Apply every N minutes (plugin setting auto_apply_minutes).

Dispatcharr has no timer hook for plugins, so the plugin runs one background
thread per container. Several Dispatcharr processes load the plugin; a file
lock makes sure only one of them runs the timer. The interval and the plugin's
enabled flag are re-read from the database on every tick, so changing the
setting needs no restart, and 0 turns the timer off.

Each run goes through Dispatcharr's own PluginManager.run_action, exactly like
pressing Apply: same settings, same logger, same database cleanup.
"""

from __future__ import annotations

import contextlib
import fcntl
import logging
import os
import tempfile
import threading
import time
from collections.abc import Iterator

_LOCK_PATH = os.path.join(tempfile.gettempdir(), "waybill-scheduler.lock")
_APPLY_LOCK_PATH = os.path.join(tempfile.gettempdir(), "waybill-apply.lock")
_TICK_SECONDS = 30
SETTING = "auto_apply_minutes"

_logger = logging.getLogger("plugins.waybill.scheduler")
_thread: "threading.Thread | None" = None
_stop = threading.Event()
_lock_fh = None


def ensure_running(plugin_key: str) -> None:
    """Start the timer thread unless this or another process already runs it."""
    global _thread, _stop, _lock_fh
    if _thread is not None and _thread.is_alive():
        return
    fh = open(_LOCK_PATH, "w")
    try:
        fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        fh.close()  # another process runs the timer
        return
    _lock_fh = fh
    _stop = threading.Event()
    _thread = threading.Thread(
        target=_loop, args=(plugin_key, _stop), name="waybill-scheduler", daemon=True
    )
    _thread.start()
    _logger.debug("waybill: auto-apply timer started")


def stop() -> None:
    """Stop the timer thread of this process, if it runs one, and free the lock."""
    global _thread, _lock_fh
    _stop.set()
    if _thread is not None and _thread.is_alive():
        _thread.join(timeout=5)
    _thread = None
    if _lock_fh is not None:
        with contextlib.suppress(OSError):
            fcntl.flock(_lock_fh, fcntl.LOCK_UN)
        _lock_fh.close()
        _lock_fh = None


@contextlib.contextmanager
def apply_lock() -> Iterator[None]:
    """Serialise applies across processes (a timed run and a manual one)."""
    with open(_APPLY_LOCK_PATH, "w") as fh:
        fcntl.flock(fh, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(fh, fcntl.LOCK_UN)


def interval_minutes(settings: "dict | None") -> int:
    """The auto_apply_minutes setting as a whole number of minutes; 0 when off or invalid."""
    raw = (settings or {}).get(SETTING, 0)
    if isinstance(raw, bool):
        return 0
    try:
        return max(0, int(float(raw)))
    except (TypeError, ValueError):
        return 0


def _read_interval(plugin_key: str) -> "int | None":
    """Minutes between runs (0 = off), or None when the plugin was removed."""
    from django.db import close_old_connections

    from apps.plugins.models import PluginConfig

    try:
        cfg = PluginConfig.objects.filter(key=plugin_key).first()
    finally:
        close_old_connections()
    if cfg is None:
        return None
    if not cfg.enabled:
        return 0
    return interval_minutes(cfg.settings)


def _loop(plugin_key: str, stop_event: threading.Event) -> None:
    last_run: "float | None" = None
    while not stop_event.wait(_TICK_SECONDS):
        try:
            minutes = _read_interval(plugin_key)
        except Exception:  # noqa: BLE001 - database not ready yet; try next tick
            _logger.debug("waybill: could not read auto-apply setting", exc_info=True)
            continue
        if minutes is None:
            _logger.info("waybill: plugin removed, stopping auto-apply timer")
            return
        if minutes == 0:
            last_run = None
            continue
        now = time.monotonic()
        if last_run is not None and now - last_run < minutes * 60:
            continue
        last_run = now
        try:
            from apps.plugins.loader import PluginManager

            PluginManager.get().run_action(plugin_key, "apply", {})
        except Exception:  # noqa: BLE001 - logged; the next tick tries again
            _logger.exception("waybill: auto-apply failed")
