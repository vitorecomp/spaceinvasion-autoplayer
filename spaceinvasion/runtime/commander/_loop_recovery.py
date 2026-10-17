"""Bot-control gating, CAPTCHA pausing, reconnect polling, and API error recovery for commander loop."""

from __future__ import annotations

from collections.abc import Callable, Mapping
import logging

import requests

from spaceinvasion.core.coercion import fmt_compact_int
from spaceinvasion.core.redaction import format_clean_error, redact_sensitive
from spaceinvasion.runtime.commander._types import (
    LOOP_BOUNDARY_ERRORS,
    RATE_LIMIT_MARKERS,
    SESSION_RECOVERY_MARKERS,
    BotControlSnapshot,
    CommanderApiLike,
    CommanderOptions,
    LoopRuntimeState,
)
from spaceinvasion.runtime.commander.errors import (
    compute_rate_limit_backoff,
    is_captcha_error,
    is_connection_refused_error,
    is_galaxy_scan_busy_error,
    safe_cmd_str,
    wait_for_api_reconnect,
)
from spaceinvasion.runtime.commander_client import GameApiError
from spaceinvasion.runtime.logs import append_crash_debug_log


def fetch_bot_control(
    api: CommanderApiLike,
    *,
    run_loop: bool,
    sleep_fn: Callable[[float], None],
    monotonic_fn: Callable[[], float],
) -> Mapping[str, object]:
    """Fetch `/bot_status`, polling for API reconnect once if restarting in loop mode."""
    try:
        bot_ctrl = api.get_bot_control()
    except (requests.RequestException, GameApiError) as bc_exc:
        if run_loop and is_connection_refused_error(bc_exc):
            if wait_for_api_reconnect(api, max_wait_s=6.0, poll_s=0.5, sleep_fn=sleep_fn, monotonic_fn=monotonic_fn):
                bot_ctrl = api.get_bot_control()
            else:
                raise
        else:
            raise
    if not isinstance(bot_ctrl, Mapping):
        raise GameApiError(f"Expected JSON object from /bot_status, got {type(bot_ctrl).__name__}")
    return bot_ctrl


def evaluate_control_gates(
    snapshot: BotControlSnapshot,
    rt_state: LoopRuntimeState,
    *,
    run_loop: bool,
    eff_min_interval_s: float,
    logger: logging.Logger,
) -> tuple[bool, float | None]:
    """Check CAPTCHA, UI pause, and galaxy-scan gates without triggering browser calls."""
    if snapshot.captcha_blocked:
        if not rt_state.captcha_alert_logged:
            logger.warning(
                "[CMD][ALERT] CAPTCHA / anti-bot gate active (%s) — "
                "automation paused; waiting for operator to solve in Chrome (:9222) and click Resume...",
                redact_sensitive(snapshot.captcha_message),
            )
            rt_state.captcha_alert_logged = True
        return True, min(5.0, eff_min_interval_s)
    if run_loop and snapshot.paused:
        logger.info("[CMD tick %s] Paused via control UI; waiting.", fmt_compact_int(rt_state.tick))
        return True, max(3.0, min(8.0, eff_min_interval_s))
    if snapshot.galaxy_scan_blocking:
        logger.info(
            "[CMD tick %s] Galaxy scan in progress; deferring actions until sweep finishes.",
            fmt_compact_int(rt_state.tick),
        )
        return True, max(2.0, min(4.0, eff_min_interval_s))
    if rt_state.captcha_alert_logged and not snapshot.paused:
        logger.info("[CMD] CAPTCHA gate cleared and automation resumed.")
        rt_state.captcha_alert_logged = False
    return False, None


def pause_on_captcha(
    api: CommanderApiLike,
    rt_state: LoopRuntimeState,
    err_txt: str,
    *,
    eff_min_interval_s: float,
    logger: logging.Logger,
) -> float:
    """Best-effort pause via `/command bot_control` and log the persistent CAPTCHA alert once."""
    try:
        api.send_command({"command": "bot_control", "paused": True})
    except LOOP_BOUNDARY_ERRORS as pause_exc:
        logger.debug("Failed to signal bot_control pause after CAPTCHA: %s", format_clean_error(pause_exc))
    if not rt_state.captcha_alert_logged:
        logger.warning(
            "[CMD][ALERT] CAPTCHA / anti-bot challenge detected (%s) — "
            "automation paused; waiting for operator to solve in Chrome (:9222) and click Resume...",
            format_clean_error(err_txt),
        )
        rt_state.captcha_alert_logged = True
    return min(5.0, eff_min_interval_s)


def attempt_session_recovery(
    api: CommanderApiLike,
    rt_state: LoopRuntimeState,
    *,
    tick_s: str,
    eff_min_interval_s: float,
    logger: logging.Logger,
) -> float:
    """Attempt ``ensure_session`` recovery, pausing immediately if a CAPTCHA challenge appears."""
    try:
        api.send_command({"command": "ensure_session"})
        logger.info("[CMD tick %s] Recovered browser session via ensure_session.", tick_s)
        return 1.0
    except (requests.RequestException, GameApiError) as rec_exc:
        rec_txt = safe_cmd_str(rec_exc)
        if is_captcha_error(rec_txt):
            return pause_on_captcha(api, rt_state, rec_txt, eff_min_interval_s=eff_min_interval_s, logger=logger)
        logger.info("[CMD tick %s] Session recovery pending: %s", tick_s, format_clean_error(rec_txt))
        return 15.0 if any(gate in rec_txt.lower() for gate in ("2-factor", "2fa")) else 3.0


def handle_api_tick_error(
    exc: requests.RequestException | GameApiError,
    api: CommanderApiLike,
    opts: CommanderOptions,
    rt_state: LoopRuntimeState,
    *,
    eff_min_interval_s: float,
    run_loop: bool,
    logger: logging.Logger,
    jitter_fn: Callable[[float, float], float],
    sleep_fn: Callable[[float], None],
    monotonic_fn: Callable[[], float],
) -> tuple[int | None, float | None]:
    """Classify and recover from HTTP/API tick exceptions, returning ``(exit_code, sleep_s)``."""
    exc_txt = safe_cmd_str(exc)
    tick_s = fmt_compact_int(rt_state.tick)
    if is_captcha_error(exc_txt):
        wait_s = pause_on_captcha(api, rt_state, exc_txt, eff_min_interval_s=eff_min_interval_s, logger=logger)
        return (None, wait_s) if run_loop else (0, None)
    if is_galaxy_scan_busy_error(exc_txt):
        logger.info("[CMD tick %s] Galaxy scan in progress; deferring actions until sweep finishes.", tick_s)
        return (None, max(2.0, min(4.0, eff_min_interval_s))) if run_loop else (0, None)
    if run_loop and is_connection_refused_error(exc, exc_txt):
        reconnected = wait_for_api_reconnect(
            api,
            max_wait_s=min(5.0, max(0.5, eff_min_interval_s)),
            poll_s=0.5,
            sleep_fn=sleep_fn,
            monotonic_fn=monotonic_fn,
        )
        if reconnected:
            logger.info(
                "[CMD tick %s] API unavailable (%s restarted); retrying immediately.",
                tick_s,
                redact_sensitive(opts.api_url),
            )
            return None, 0.5
        logger.info(
            "[CMD tick %s] API unavailable (waiting for %s to come up); retrying shortly.",
            tick_s,
            redact_sensitive(opts.api_url),
        )
        return None, min(3.0, max(0.5, eff_min_interval_s))

    logger.warning("[CMD tick %s] API unavailable (%s); retrying shortly.", tick_s, format_clean_error(exc_txt))
    if not run_loop:
        return 1, None
    err_lower = exc_txt.lower()
    health_info: dict[str, object] | None = None
    if any(marker in err_lower for marker in RATE_LIMIT_MARKERS):
        try:
            h_raw = api.health()
            health_info = {str(k): v for k, v in h_raw.items()} if isinstance(h_raw, Mapping) else None
        except LOOP_BOUNDARY_ERRORS as h_exc:
            logger.debug("Health probe failed during rate-limit backoff: %s", format_clean_error(h_exc))
    rl_backoff = compute_rate_limit_backoff(exc_txt, health_payload=health_info)
    if rl_backoff is not None:
        wait_s = rl_backoff + jitter_fn(1.0, 3.0)
        logger.warning("[CMD tick %s] Rate-limit cooldown active; backing off %.1fs.", tick_s, wait_s)
        return None, wait_s
    if (
        isinstance(exc, GameApiError)
        and any(marker in err_lower for marker in SESSION_RECOVERY_MARKERS)
        and (monotonic_fn() - rt_state.last_recovery_at) >= 25.0
    ):
        rt_state.last_recovery_at = monotonic_fn()
        return None, attempt_session_recovery(
            api, rt_state, tick_s=tick_s, eff_min_interval_s=eff_min_interval_s, logger=logger
        )
    return None, 3.0


def handle_unexpected_tick_error(
    exc: BaseException,
    rt_state: LoopRuntimeState,
    *,
    effective_live: bool,
    run_loop: bool,
    logger: logging.Logger,
) -> tuple[int | None, float | None]:
    """Log and record an unexpected process-boundary tick exception without crashing loop mode."""
    tick_s = fmt_compact_int(rt_state.tick)
    logger.error(
        "[CMD tick %s] Unexpected error (%s: %s); %s",
        tick_s,
        type(exc).__name__,
        format_clean_error(safe_cmd_str(exc)),
        "retrying shortly." if run_loop else "aborting.",
    )
    append_crash_debug_log(
        component="commander.loop",
        event="unexpected_tick_error",
        error=exc,
        details={"tick": rt_state.tick, "live": effective_live},
    )
    return (None, 5.0) if run_loop else (1, None)
