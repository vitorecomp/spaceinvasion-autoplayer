"""Background galaxy sweep worker loop and one-shot background launch helper."""

from __future__ import annotations

from collections.abc import Callable
import logging
import random
import threading
import time

from spaceinvasion.core.logging_utils import append_crash_debug_log
from spaceinvasion.runtime.bridge._context import BridgeContext
from spaceinvasion.runtime.bridge._helpers import BRIDGE_RUNTIME_ERRORS, sanitize_json_dict

_LOGGER = logging.getLogger(__name__)


def launch_background_galaxy_sweep(
    ctx: BridgeContext,
    *,
    manual: bool,
    pages: int | None,
    start_galaxy: int | None,
    start_system: int | None,
    get_state_fn: Callable[..., dict[str, object]],
    run_sweep_fn: Callable[..., dict[str, object]],
) -> dict[str, object]:
    """Start an asynchronous galaxy sweep thread when neither CAPTCHA nor another sweep blocks it."""
    if ctx.bot_control.get("captcha_blocked"):
        return sanitize_json_dict(
            {
                "ok": True,
                "command": "galaxy_sweep",
                "started": False,
                "executed": False,
                "captcha_blocked": True,
                "message": "Paused for CAPTCHA — confirm solved in UI before sweeping galaxy.",
                "galaxy_sweep": get_state_fn(include_smart=False),
            }
        )
    if ctx.sweep_in_progress:
        return sanitize_json_dict(
            {
                "ok": True,
                "command": "galaxy_sweep",
                "started": False,
                "executed": False,
                "in_progress": True,
                "message": "Galaxy sweep is already in progress.",
                "galaxy_sweep": get_state_fn(include_smart=False),
            }
        )

    def _bg_runner() -> None:
        try:
            run_sweep_fn(
                pages=pages,
                galaxy=start_galaxy,
                start_system=start_system,
                dry_run=False,
                priority="interactive" if manual else "normal",
                automatic=not manual,
                manual=manual,
            )
        except BRIDGE_RUNTIME_ERRORS as exc:
            _LOGGER.warning("Background galaxy sweep failed", exc_info=True)
            if ctx.log_dir is not None:
                append_crash_debug_log(component="api_server.galaxy_sweep_bg", event="sweep_error", error=exc)

    worker = threading.Thread(
        target=_bg_runner, name="galaxy-sweep-manual" if manual else "galaxy-sweep-bg", daemon=True
    )
    worker.start()
    return sanitize_json_dict(
        {
            "ok": True,
            "command": "galaxy_sweep",
            "started": True,
            "manual": bool(manual),
            "in_progress": True,
            "galaxy_sweep": get_state_fn(include_smart=False),
            "message": "Started manual galaxy sweep in background." if manual else "Started automatic galaxy sweep.",
        }
    )


def run_galaxy_sweep_worker_loop(
    ctx: BridgeContext,
    *,
    get_state_fn: Callable[..., dict[str, object]],
    has_p0_escape_fn: Callable[[], tuple[bool, str]],
    run_sweep_fn: Callable[..., dict[str, object]],
) -> None:
    """Periodic daemon loop triggering automatic galaxy sweeps when due and safe."""
    if ctx.sweep_stop_event.wait(random.uniform(4.0, 12.0)):
        return
    while not ctx.sweep_stop_event.is_set():
        should_run = False
        with ctx.lock:
            sw = get_state_fn()
            p0_urg, _ = has_p0_escape_fn()
            cmd_idle_ok = (time.monotonic() - ctx.last_live_command_mono) >= 3.0
            if (
                bool(ctx.strategy_config.allow_galaxy_scan)
                and not bool(ctx.bot_control.get("paused"))
                and not bool(ctx.bot_control.get("captcha_blocked"))
                and ctx.bot_control.get("mode_override") != "dry-run"
                and not p0_urg
                and cmd_idle_ok
                and bool(sw.get("due", True))
                and ctx.rate_limit_remaining() <= 0.0
                and ctx.is_browser_port_open()
            ):
                should_run = True
        if should_run:
            try:
                run_sweep_fn(priority="normal", automatic=True, manual=False)
            except BRIDGE_RUNTIME_ERRORS as exc:
                _LOGGER.warning("Automatic galaxy sweep iteration failed", exc_info=True)
                if ctx.log_dir is not None:
                    append_crash_debug_log(component="api_server.galaxy_sweep_worker", event="sweep_error", error=exc)
        if ctx.sweep_stop_event.wait(random.uniform(2.0, 5.0)):
            break
