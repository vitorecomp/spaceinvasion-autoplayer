"""Shared redaction, JSON sanitization, integer coercion, and browser helpers for the bridge."""

from __future__ import annotations

from collections.abc import Mapping
import math
from pathlib import Path
import secrets
import time

from selenium.common.exceptions import WebDriverException

from spaceinvasion.core import catalog as game_data
from spaceinvasion.core.catalog.json_sanitize import sanitize_json_tree
from spaceinvasion.core.redaction import format_clean_error, redact_text
from spaceinvasion.core.redaction import redact_api_payload as _core_redact_api_payload
from spaceinvasion.runtime.api.validation import ApiRequestError
from spaceinvasion.session.auth import SpaceInvasionAuthError, load_credentials
from spaceinvasion.session.browser import (
    DEFAULT_DEBUG_HOST,
    DEFAULT_DEBUG_PORT,
    DEFAULT_PROFILE_ROOT,
    BrowserConfig,
    is_debug_port_open,
    login_with_browser,
)
from spaceinvasion.session.client import GameBrowserClient, RateLimitError

format_api_exception = format_clean_error

BRIDGE_RUNTIME_ERRORS: tuple[type[Exception], ...] = (
    WebDriverException,
    OSError,
    RuntimeError,
    ValueError,
    TypeError,
    KeyError,
    IndexError,
    LookupError,
    AttributeError,
    ArithmeticError,
    UnicodeError,
    AssertionError,
    RecursionError,
)

REPO_ROOT_DIR = Path(__file__).resolve().parents[3]
UI_DIR = Path(__file__).resolve().parents[2] / "ui"
MAX_REQUEST_BYTES = 1_048_576
SERVER_BOOT_ID = secrets.token_hex(4)

UI_ASSETS: dict[str, tuple[str, str]] = {
    "/": ("index.html", "text/html; charset=utf-8"),
    "/index.html": ("index.html", "text/html; charset=utf-8"),
    "/styles.css": ("styles.css", "text/css; charset=utf-8"),
    "/app.js": ("app.js", "application/javascript; charset=utf-8"),
    "/favicon.svg": ("favicon.svg", "image/svg+xml"),
    "/bell.svg": ("bell.svg", "image/svg+xml"),
}

ALLOWED_MENU_ACTIONS: frozenset[str] = frozenset(
    {
        "internalHome",
        "internalBuildings",
        "internalEconomy",
        "internalResearch",
        "internalWeaponFactory",
        "internalDefense",
        "internalFleet",
        "internalGalaxy",
        "internalMessages",
        "internalTransmitter",
        "internalAlien",
        "internalSynth",
        "internalSpecs",
        "internalUniverse",
        "internalStatistics",
        "internalToplist",
        "internalSettings",
    }
)


class GalaxyScanInProgressError(RuntimeError):
    """Raised when a non-emergency live browser action is requested during an active galaxy scan."""


def scrub_api_str(text: str) -> str:
    """Scrub secret tokens and session IDs from an API string."""
    return redact_text(text) if text else text


def redact_api_payload(value: object) -> object:
    """Recursively scrub session tokens and secret fields from API payloads."""
    return _core_redact_api_payload(value)


def sanitize_finite_json(obj: object) -> object:
    """Redact secrets and convert arbitrary object trees into finite JSON primitives."""
    return sanitize_json_tree(_core_redact_api_payload(obj))


def sanitize_json_dict(obj: Mapping[str, object]) -> dict[str, object]:
    """Redact and sanitize a mapping into a plain JSON-safe dictionary."""
    out = sanitize_finite_json(dict(obj))
    return dict(out) if isinstance(out, Mapping) else {}


def bounded_api_str(val: object, default: str = "<unprintable>", max_len: int = 1024) -> str:
    """Convert any value or exception into a bounded, redacted string."""
    if isinstance(val, BaseException):
        txt = format_clean_error(val, max_chars=max_len)
        return txt if txt else default
    if isinstance(val, (bytes, bytearray)):
        raw = bytes(val).decode("utf-8", errors="replace")
    elif isinstance(val, str):
        raw = val
    else:
        try:
            raw = game_data.safe_key(val)
        except BRIDGE_RUNTIME_ERRORS:
            return default
    scrubbed = scrub_api_str(raw)
    return scrubbed[:max_len] if len(scrubbed) > max_len else scrubbed


def safe_api_int(val: object, default: int = 0) -> int:
    """Safely coerce an untrusted value into an integer."""
    return int(game_data.safe_int(val, default=default))


def safe_api_float(val: object, default: float = 0.0) -> float:
    """Safely coerce an untrusted value into a finite float."""
    return float(game_data.safe_finite_float(val, default=default))


def reject_nonfinite_json(token: str) -> object:
    """Reject non-standard JSON constants (`NaN`, `Infinity`, `-Infinity`)."""
    raise ValueError(f"Non-standard JSON constant {token!r} is not allowed.")


def _parse_int_with_label(
    label: str,
    name: str,
    val: object,
    *,
    allow_none: bool = False,
    min_val: int | None = None,
    max_val: int | None = None,
) -> int | None:
    if allow_none and val is None:
        return None
    if isinstance(val, bool) or val is None or isinstance(val, (Mapping, list, tuple, set, frozenset)):
        raise ApiRequestError(f"{label} {name!r} must be an integer.", field=name)
    if isinstance(val, int):
        parsed = val
    elif isinstance(val, float):
        if not math.isfinite(val) or not val.is_integer() or abs(val) > (2**53 - 1):
            raise ApiRequestError(f"{label} {name!r} must be a safe finite integer.", field=name)
        parsed = int(val)
    elif isinstance(val, str):
        txt = val.strip()
        if not txt or not txt.isascii():
            raise ApiRequestError(f"{label} {name!r} must be an integer.", field=name)
        digits = txt[1:] if txt[0] in ("+", "-") else txt
        if not digits or not digits.isdigit():
            raise ApiRequestError(f"{label} {name!r} must be an integer.", field=name)
        trimmed = digits.lstrip("0") or "0"
        if len(trimmed) > 4000:
            raise ApiRequestError(f"{label} {name!r} integer is out of range.", field=name)
        parsed = -int(trimmed) if txt[0] == "-" else int(trimmed)
    else:
        raise ApiRequestError(f"{label} {name!r} must be an integer.", field=name)
    if min_val is not None and parsed < min_val:
        raise ApiRequestError(f"{label} {name!r} must be >= {min_val}.", field=name)
    if max_val is not None and parsed > max_val:
        raise ApiRequestError(f"{label} {name!r} must be <= {max_val}.", field=name)
    return parsed


def parse_command_int(
    name: str,
    val: object,
    *,
    allow_none: bool = False,
    min_val: int | None = None,
    max_val: int | None = None,
) -> int | None:
    """Strictly parse a command parameter integer with legacy error wording."""
    return _parse_int_with_label(
        "Command parameter", name, val, allow_none=allow_none, min_val=min_val, max_val=max_val
    )


def validate_strict_int(
    name: str,
    val: object,
    *,
    min_val: int | None = None,
    max_val: int | None = None,
    allow_none: bool = False,
) -> int | None:
    """Strictly parse a config integer field with legacy error wording."""
    return _parse_int_with_label("Config field", name, val, allow_none=allow_none, min_val=min_val, max_val=max_val)


def safe_limiter_wait(raw: object, default: float = 8.0, *, min_val: float = 0.0, max_val: float = 3600.0) -> float:
    """Coerce a limiter wait duration into `[min_val, max_val]` safely."""
    if isinstance(raw, bool) or raw is None or isinstance(raw, (dict, list, tuple, set, frozenset)):
        return default
    if isinstance(raw, int):
        if raw.bit_length() > 128:
            return max_val if raw > 0 else min_val
        val = float(raw)
    elif isinstance(raw, float):
        val = raw
    else:
        txt_src = bytes(raw).decode("utf-8", errors="replace") if isinstance(raw, (bytes, bytearray)) else raw
        if isinstance(txt_src, str):
            txt = txt_src.strip()
            if not txt or not txt.isascii():
                return default
            try:
                val = float(txt)
            except (ValueError, OverflowError):
                return default
        else:
            return default
    if not math.isfinite(val):
        return default
    return max(min_val, min(max_val, val))


def safe_debug_port(raw: object, default: int = DEFAULT_DEBUG_PORT) -> int:
    """Clamp a Chrome debug port into `[0, 65535]`."""
    return max(0, min(65535, safe_api_int(raw, default)))


def safe_hourly_budget(raw: object, default: int = 60) -> int:
    """Clamp an hourly action budget into `[0, 1_000_000]`."""
    return max(0, min(1_000_000, safe_api_int(raw, default)))


def ensure_debug_browser_ready(
    *,
    debug_port: int = DEFAULT_DEBUG_PORT,
    debug_host: str = DEFAULT_DEBUG_HOST,
    env_file: Path = Path(".env"),
    account: str = "default",
) -> None:
    """Ensure Chrome stays open on `debug_host:debug_port` and logged into the configured universe."""
    eff_host = str(debug_host or DEFAULT_DEBUG_HOST).strip() or "127.0.0.1"
    is_remote_browser = eff_host not in ("127.0.0.1", "localhost", "::1")

    if is_remote_browser:
        deadline = time.monotonic() + 30.0
        port_open = False
        while time.monotonic() < deadline:
            if is_debug_port_open(debug_port, eff_host):
                port_open = True
                break
            time.sleep(0.5)
        if not port_open:
            raise SpaceInvasionAuthError(f"Remote browser container at {eff_host}:{debug_port} is not reachable.")
    else:
        port_open = is_debug_port_open(debug_port)
        if not port_open:
            creds = load_credentials(env_path=env_file, account=account)
            cfg = BrowserConfig(
                profile_dir=DEFAULT_PROFILE_ROOT / creds.account_name,
                debug_port=debug_port,
                debug_host=eff_host,
                detach=True,
            )
            login_with_browser(creds=creds, config=cfg, require_in_game=True)
            return

    try:
        attach_ctx = (
            GameBrowserClient.attach(port=debug_port)
            if eff_host == "127.0.0.1"
            else GameBrowserClient.attach(port=debug_port, host=eff_host)
        )
        with attach_ctx as client:
            client.ensure_in_game()
    except RateLimitError:
        raise
    except SpaceInvasionAuthError:
        creds = load_credentials(env_path=env_file, account=account)
        cfg = BrowserConfig(
            profile_dir=DEFAULT_PROFILE_ROOT / creds.account_name,
            debug_port=debug_port,
            debug_host=eff_host,
            attach=True,
            detach=True,
        )
        login_with_browser(creds=creds, config=cfg, require_in_game=True)
