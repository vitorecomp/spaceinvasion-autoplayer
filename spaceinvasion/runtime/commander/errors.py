"""Error classifiers, rate-limit backoff, API reconnect polling, and safe scalar helpers."""

from __future__ import annotations

from collections.abc import Callable, Mapping
import math
import re
import time

import requests

from spaceinvasion.core.coercion import cap_json_int, coerce_int, coerce_optional_int
from spaceinvasion.core.redaction import redact_sensitive
from spaceinvasion.runtime._logs_helpers import safe_summary_text
from spaceinvasion.runtime.commander._types import (
    CAPTCHA_STOP_MARKERS,
    CONNECTION_REFUSED_MARKERS,
    LOOP_BOUNDARY_ERRORS,
    RATE_LIMIT_MARKERS,
    CommanderApiLike,
)

_RETRY_AFTER_PATTERNS: tuple[str, ...] = (
    r"(?:retry[-_ ]?after|cooldown(?:\s+active)?|wait(?:ing)?)\D{0,24}(\d+(?:\.\d+)?)\s*s?",
    r"(\d+(?:\.\d+)?)\s*s(?:ec(?:onds?)?)?\b",
)


def safe_finite_float(val: object) -> float | None:
    """Return ``float(val)`` when ``val`` is a finite int/float (excluding ``bool``), else ``None``."""
    if isinstance(val, bool) or not isinstance(val, (int, float)):
        return None
    try:
        parsed = float(val)
    except (TypeError, ValueError, OverflowError):
        return None
    return parsed if math.isfinite(parsed) else None


def safe_cmd_int(val: object, default: int = 0) -> int:
    """Coerce ``val`` to a JSON-safe bounded integer."""
    return cap_json_int(coerce_int(val, default))


def safe_cmd_opt_int(val: object) -> int | None:
    """Coerce ``val`` to an optional JSON-safe bounded integer."""
    parsed = coerce_optional_int(val, None)
    if parsed is None:
        return None
    return cap_json_int(parsed)


def safe_cmd_str(val: object, default: str = "", *, cap: int = 1024) -> str:
    """Coerce ``val`` into a redacted, length-bounded text string."""
    if val is None:
        return default
    raw = safe_summary_text(val, default)
    red = redact_sensitive(raw)
    txt = red if isinstance(red, str) else safe_summary_text(red, default)
    return txt[:cap] if len(txt) > cap else txt


def compute_rate_limit_backoff(
    error_text: object,
    health_payload: Mapping[str, object] | None = None,
    *,
    min_backoff: float = 45.0,
) -> float | None:
    """Return backoff seconds (>= ``min_backoff``) when ``error_text`` or ``health_payload`` signals throttling."""
    low = safe_cmd_str(error_text).lower()
    is_rl = any(marker in low for marker in RATE_LIMIT_MARKERS) or (
        isinstance(health_payload, Mapping) and bool(health_payload.get("rate_limited"))
    )
    if not is_rl:
        return None

    min_f = safe_finite_float(min_backoff)
    base_min = min_f if (min_f is not None and min_f > 0) else 45.0
    candidates: list[float] = [min(3600.0, max(1.0, base_min))]
    if isinstance(health_payload, Mapping):
        ra = safe_finite_float(health_payload.get("retry_after"))
        if ra is not None and ra > 0:
            candidates.append(min(3600.0, ra))

    for pat in _RETRY_AFTER_PATTERNS:
        for match in re.finditer(pat, low, re.ASCII):
            try:
                val = float(match.group(1))
            except (ValueError, OverflowError):
                val = 0.0
            if math.isfinite(val) and val > 0:
                candidates.append(min(3600.0, val))
    return min(3600.0, max(candidates))


def is_captcha_error(
    error_text: object = "",
    *,
    bot_ctrl: Mapping[str, object] | None = None,
) -> bool:
    """Return True when an error message or ``/bot_status`` payload indicates a CAPTCHA / anti-bot challenge."""
    if isinstance(bot_ctrl, Mapping) and bool(bot_ctrl.get("captcha_blocked")):
        return True
    low = safe_cmd_str(error_text).lower()
    return any(marker in low for marker in CAPTCHA_STOP_MARKERS)


def is_galaxy_scan_busy_error(
    error_text: object = "",
    *,
    bot_ctrl: Mapping[str, object] | None = None,
) -> bool:
    """Return True when ``/bot_status`` or an API response indicates an active galaxy scan is blocking actions."""
    if (
        isinstance(bot_ctrl, Mapping)
        and bool(bot_ctrl.get("galaxy_scan_blocking"))
        and not bool(bot_ctrl.get("p0_escape_flight_ready", False))
    ):
        return True
    low = safe_cmd_str(error_text).lower()
    return "galaxy scan in progress" in low or "galaxy sweep in progress" in low


def is_connection_refused_error(exc: BaseException, exc_txt: str = "") -> bool:
    """Return True when an error indicates the local API server is restarting or unreachable."""
    if isinstance(exc, requests.exceptions.ConnectionError):
        return True
    low = (exc_txt or safe_cmd_str(exc)).lower()
    return any(marker in low for marker in CONNECTION_REFUSED_MARKERS)


def wait_for_api_reconnect(
    api: CommanderApiLike,
    *,
    max_wait_s: float = 6.0,
    poll_s: float = 0.5,
    sleep_fn: Callable[[float], None] = time.sleep,
    monotonic_fn: Callable[[], float] = time.monotonic,
) -> bool:
    """Poll ``api.health()`` briefly while the local API restarts (e.g. hot-reload)."""
    step_s = max(0.05, min(1.0, float(poll_s)))
    bounded_wait = max(0.1, min(15.0, float(max_wait_s)))
    max_attempts = max(1, min(20, int(math.ceil(bounded_wait / step_s))))
    deadline = monotonic_fn() + bounded_wait
    for attempt in range(max_attempts):
        if attempt > 0:
            sleep_fn(step_s)
        try:
            health_payload = api.health()
            if isinstance(health_payload, Mapping):
                return True
        except StopIteration:
            return False
        except LOOP_BOUNDARY_ERRORS:
            health_payload = {}
        if monotonic_fn() >= deadline:
            break
    return False
