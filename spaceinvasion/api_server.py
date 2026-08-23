"""Local HTTP JSON API wrapping the live SpaceInvasion Chrome debug session."""

from __future__ import annotations

import contextlib
from dataclasses import asdict, replace
import hashlib
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from collections.abc import Mapping
import inspect
import json
import math
import os
from pathlib import Path
import re
import secrets
import threading
import time
from typing import Any, Callable, Optional, Sequence
from urllib.parse import parse_qs, urlparse

from bs4 import BeautifulSoup

from spaceinvasion import game_data
from spaceinvasion.auth import SpaceInvasionAuthError, load_credentials
from spaceinvasion.browser import (
    DEFAULT_DEBUG_PORT,
    BrowserConfig,
    is_debug_port_open,
    login_with_browser,
)
from spaceinvasion.client import GameBrowserClient, PageViewLimiter, RateLimitError
from spaceinvasion.config import (
    StrategyConfig,
    load_strategy_config,
    normalize_planner_mode,
    save_strategy_config,
    strategy_config_from_dict,
)
from spaceinvasion.executor import ActionExecutor
from spaceinvasion.fleet import (
    SHIP_SLUG_BY_KEY,
    SpyReport,
    extract_galaxy_system_coords,
    normalize_coords,
    parse_galaxy_system_html,
    parse_spy_messages_html,
    plan_escape_flight,
    simulate_raid_outcome,
)
from spaceinvasion.logging_utils import (
    _SECRET_DICT_KEYS,
    _SID_RE as _LOG_SID_RE,
    _sub_sid_kv as _sub_log_sid_kv,
    append_api_log,
    is_secret_dict_key,
    redact_sensitive,
)
from spaceinvasion.optimizer import get_last_optimizer_summary
from spaceinvasion.ranking import (
    attach_action_etas,
    build_buildings_eta,
    build_combat_overview,
    build_ranking_evolution,
    load_rank_history,
    record_rank_snapshot,
    simulate_combat_matchup,
)
from spaceinvasion.state import (
    GameState,
    PlanetState,
    Resources,
    is_building_allowed_on_planet_type,
    normalize_planet_type,
    parse_economy_rates,
    parse_home_metadata,
    parse_tutorial_from_home,
)
from spaceinvasion.storage import GameDatabase
from spaceinvasion.strategy import PlannedAction, plan_actions

UI_DIR = Path(__file__).resolve().parent / "ui"
MAX_REQUEST_BYTES = 1_048_576
_SERVER_BOOT_ID = secrets.token_hex(4)
_GLOBAL_OPTIMIZER_LOCK = threading.Lock()
_API_SID_RE = re.compile(_LOG_SID_RE.pattern, _LOG_SID_RE.flags)


def _sub_api_sid(match: re.Match[str]) -> str:
    return _sub_log_sid_kv(match)
_UI_ASSETS: dict[str, tuple[str, str]] = {
    "/": ("index.html", "text/html; charset=utf-8"),
    "/index.html": ("index.html", "text/html; charset=utf-8"),
    "/styles.css": ("styles.css", "text/css; charset=utf-8"),
    "/app.js": ("app.js", "application/javascript; charset=utf-8"),
}


def compute_ui_assets_state(ui_dir: Path = UI_DIR) -> dict[str, str]:
    """Return lightweight digest tokens for hot-reloading UI CSS and code assets."""
    def _file_sig(name: str) -> str:
        p = ui_dir / name
        try:
            st = p.stat()
            return f"{st.st_mtime_ns:x}-{st.st_size:x}"
        except OSError:
            return "missing"

    css_version = _file_sig("styles.css")
    code_raw = f"{_file_sig('index.html')}|{_file_sig('app.js')}".encode("utf-8")
    code_version = hashlib.sha1(code_raw).hexdigest()[:12]
    ui_version = f"{code_version}:{css_version}"
    return {
        "ui_version": ui_version,
        "code_version": code_version,
        "css_version": css_version,
    }

ALLOWED_MENU_ACTIONS = frozenset(
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
    }
)


class ApiRequestError(ValueError):
    """Raised when an API request payload is malformed or unsupported."""


_API_SECRET_DICT_KEYS: frozenset[str] = _SECRET_DICT_KEYS
_MAX_API_JSON_INT: int = game_data.MAX_JSON_SAFE_INT


def _scrub_api_str(text: str) -> str:
    if not text:
        return text
    low = text.lower()
    if (
        "sid" not in low
        and "pass" not in low
        and "cookie" not in low
        and "api_key" not in low
        and "api-key" not in low
        and "secret" not in low
        and "auth" not in low
        and "bearer" not in low
        and (
            text.isascii()
            or (
                "\u0130" not in text
                and "\u0131" not in text
                and "\u017f" not in text
                and "\u212a" not in text
            )
        )
    ):
        return text
    scrubbed = redact_sensitive(text)
    s = scrubbed if isinstance(scrubbed, str) else _bounded_api_str(scrubbed)
    if (
        "sid" in low
        or "\u0130" in s
        or "\u0131" in s
        or "\u017f" in s
        or "sid" in s.lower()
    ):
        return _API_SID_RE.sub(_sub_api_sid, s)
    return s


_MAX_API_KEY_NODES = 256
_MAX_API_KEY_CHARS = 4096
_MAX_API_TREE_NODES = 32_768
_MAX_API_TREE_DEPTH = 64


def _clip_api_str(text: str, limit: int = _MAX_API_KEY_CHARS) -> str:
    if len(text) <= limit:
        return text
    return text[:limit] + "...[truncated]"


def _redact_api_key(
    key: Any,
    *,
    _depth: int = 0,
    _seen: Optional[set[int]] = None,
    _budget: Optional[list[int]] = None,
) -> Any:
    if isinstance(key, str):
        return _scrub_api_str(key)
    if key is None or isinstance(key, (bool, int, float)):
        return key
    if isinstance(key, (bytes, bytearray)):
        return _scrub_api_str(bytes(key).decode("utf-8", errors="replace"))
    seen = _seen if _seen is not None else set()
    budget = _budget if _budget is not None else [_MAX_API_KEY_NODES]
    if isinstance(key, os.PathLike):
        vid = id(key)
        if vid in seen or _depth >= 16 or budget[0] <= 0:
            return "<unprintable>"
        budget[0] -= 1
        seen.add(vid)
        try:
            try:
                fsp = os.fspath(key)
            except Exception:
                return "<unprintable>"
            return _redact_api_key(fsp, _depth=_depth + 1, _seen=seen, _budget=budget)
        finally:
            seen.discard(vid)
    if isinstance(key, BaseException):
        return _format_api_exception(key, max_len=512, _depth=_depth, _seen=seen, _budget=budget)
    if isinstance(key, (tuple, frozenset)):
        vid = id(key)
        if vid in seen or _depth >= 16 or budget[0] <= 0:
            return "(...)" if isinstance(key, tuple) else "frozenset({...})"
        budget[0] -= 1
        seen.add(vid)
        try:
            items: list[Any] = []
            for idx, item in enumerate(key):
                if idx >= 64 or budget[0] <= 0:
                    items.append("...")
                    break
                items.append(_redact_api_key(item, _depth=_depth + 1, _seen=seen, _budget=budget))
            return tuple(items) if isinstance(key, tuple) else frozenset(items)
        except TypeError:
            pass
        finally:
            seen.discard(vid)
    return _bounded_api_str(key)


def _redact_api_payload(
    value: Any,
    *,
    _depth: int = 0,
    _seen: Optional[set[int]] = None,
    _budget: Optional[list[int]] = None,
) -> Any:
    """Recursively scrub session tokens and secret key-value fields from API responses."""
    if isinstance(value, str):
        return _scrub_api_str(value)
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, (bytes, bytearray)):
        return _scrub_api_str(bytes(value).decode("utf-8", errors="replace"))
    if isinstance(value, BaseException):
        return _format_api_exception(value, max_len=2048)
    if isinstance(value, (Mapping, list, tuple, set, frozenset, os.PathLike)):
        seen = _seen if _seen is not None else set()
        budget = _budget if _budget is not None else [_MAX_API_TREE_NODES]
        vid = id(value)
        if vid in seen:
            return "<unprintable>" if isinstance(value, os.PathLike) else "[Cycle]"
        if _depth >= _MAX_API_TREE_DEPTH or budget[0] <= 0:
            return "<unprintable>" if isinstance(value, os.PathLike) else "[Truncated]"
        budget[0] -= 1
        seen.add(vid)
        try:
            if isinstance(value, os.PathLike):
                try:
                    raw = os.fspath(value)
                except Exception:
                    return "<unprintable>"
                if isinstance(raw, (bytes, bytearray)):
                    return _scrub_api_str(bytes(raw).decode("utf-8", errors="replace"))
                if isinstance(raw, str):
                    return _scrub_api_str(raw)
                if isinstance(raw, os.PathLike):
                    return "<unprintable>"
                return _redact_api_payload(raw, _depth=_depth + 1, _seen=seen, _budget=budget)
            if isinstance(value, Mapping):
                out: dict[Any, Any] = {}
                for k, v in value.items():
                    if budget[0] <= 0:
                        out["[Truncated]"] = "[Truncated]"
                        break
                    clean_k = _redact_api_key(k)
                    if isinstance(clean_k, str) and is_secret_dict_key(clean_k):
                        if v is None or v == "":
                            out[clean_k] = v
                        elif isinstance(v, (bytes, bytearray)) and len(v) == 0:
                            out[clean_k] = ""
                        else:
                            out[clean_k] = "REDACTED"
                    else:
                        out[clean_k] = _redact_api_payload(
                            v, _depth=_depth + 1, _seen=seen, _budget=budget
                        )
                return out
            out_list: list[Any] = []
            for v in value:
                if budget[0] <= 0:
                    out_list.append("[Truncated]")
                    break
                out_list.append(
                    _redact_api_payload(v, _depth=_depth + 1, _seen=seen, _budget=budget)
                )
            return out_list
        finally:
            seen.discard(vid)
    return value


def _parse_command_int(
    name: str,
    val: Any,
    *,
    allow_none: bool = False,
    min_val: Optional[int] = None,
    max_val: Optional[int] = None,
) -> Optional[int]:
    if allow_none and val is None:
        return None
    if isinstance(val, bool) or val is None or isinstance(val, (Mapping, list, tuple, set, frozenset)):
        raise ApiRequestError(f"Command parameter {name!r} must be an integer.")
    if isinstance(val, int):
        parsed = val
    elif isinstance(val, float):
        if not math.isfinite(val) or not val.is_integer() or abs(val) > (2**53 - 1):
            raise ApiRequestError(f"Command parameter {name!r} must be a safe finite integer.")
        parsed = int(val)
    elif isinstance(val, str):
        txt = val.strip()
        if not txt or not txt.isascii():
            raise ApiRequestError(f"Command parameter {name!r} must be an integer.")
        try:
            parsed = int(txt)
        except ValueError as exc:
            raise ApiRequestError(f"Command parameter {name!r} must be an integer.") from exc
    else:
        raise ApiRequestError(f"Command parameter {name!r} must be an integer.")
    if min_val is not None and parsed < min_val:
        raise ApiRequestError(f"Command parameter {name!r} must be >= {min_val}.")
    if max_val is not None and parsed > max_val:
        raise ApiRequestError(f"Command parameter {name!r} must be <= {max_val}.")
    return parsed


def _validate_strict_int(
    name: str,
    val: Any,
    *,
    min_val: Optional[int] = None,
    max_val: Optional[int] = None,
    allow_none: bool = False,
) -> Optional[int]:
    if allow_none and val is None:
        return None
    if isinstance(val, bool) or val is None or isinstance(val, (Mapping, list, tuple, set, frozenset)):
        raise ApiRequestError(f"Config field {name!r} must be an integer.")
    if isinstance(val, int):
        parsed = val
    elif isinstance(val, float):
        if not math.isfinite(val) or not val.is_integer() or abs(val) > (2**53 - 1):
            raise ApiRequestError(f"Config field {name!r} must be a safe finite integer.")
        parsed = int(val)
    elif isinstance(val, str):
        txt = val.strip()
        if not txt or not txt.isascii():
            raise ApiRequestError(f"Config field {name!r} must be an integer.")
        try:
            parsed = int(txt)
        except ValueError as exc:
            raise ApiRequestError(f"Config field {name!r} must be an integer.") from exc
    else:
        raise ApiRequestError(f"Config field {name!r} must be an integer.")

    if min_val is not None and parsed < min_val:
        raise ApiRequestError(f"Config field {name!r} must be >= {min_val}.")
    if max_val is not None and parsed > max_val:
        raise ApiRequestError(f"Config field {name!r} must be <= {max_val}.")
    return parsed


def _safe_api_int(val: Any, default: int = 0) -> int:
    return game_data.safe_int(val, default=default)


def _safe_api_float(val: Any, default: float = 0.0) -> float:
    return game_data.safe_finite_float(val, default=default)


def _reject_nonfinite_json(token: str) -> Any:
    raise ValueError(f"Non-standard JSON constant {token!r} is not allowed.")


def _validate_strict_float(
    name: str,
    val: Any,
    *,
    min_val: Optional[float] = None,
    max_val: Optional[float] = None,
) -> float:
    if isinstance(val, bool) or val is None or isinstance(val, (Mapping, list, tuple, set, frozenset)):
        raise ApiRequestError(f"Config field {name!r} must be a finite number.")
    if isinstance(val, str):
        txt = val.strip()
        if not txt or not txt.isascii():
            raise ApiRequestError(f"Config field {name!r} must be a finite number.")
        val = txt
    try:
        parsed = float(val)
    except Exception as exc:
        raise ApiRequestError(f"Config field {name!r} must be a finite number.") from exc
    if not math.isfinite(parsed):
        raise ApiRequestError(f"Config field {name!r} must be a finite number.")
    if min_val is not None and parsed < min_val:
        raise ApiRequestError(f"Config field {name!r} must be >= {min_val}.")
    if max_val is not None and parsed > max_val:
        raise ApiRequestError(f"Config field {name!r} must be <= {max_val}.")
    return parsed


def _validate_config_patch(raw_cfg: Mapping[str, Any]) -> None:
    if "planner_mode" in raw_cfg:
        raw_mode = raw_cfg["planner_mode"]
        if not isinstance(raw_mode, str) or not raw_mode.strip():
            raise ApiRequestError(
                "'planner_mode' must be a non-empty string ('priority' or 'ga_nn')."
            )
        try:
            normalize_planner_mode(raw_mode, strict=True)
        except ValueError as exc:
            raise ApiRequestError(_redact_api_payload(exc)) from exc

    if "playstyle" in raw_cfg:
        raw_playstyle = raw_cfg["playstyle"]
        if not isinstance(raw_playstyle, str) or not raw_playstyle.strip():
            raise ApiRequestError("'playstyle' must be a non-empty string.")

    int_specs: dict[str, tuple[Optional[int], Optional[int], bool]] = {
        "ga_population_size": (4, 200, False),
        "ga_generations": (1, 200, False),
        "ga_horizon_steps": (1, 24, False),
        "ga_seed": (-2_147_483_648, 2_147_483_647, True),
        "max_actions_per_hour": (1, 100_000, False),
        "min_free_fields_warning": (1, 1000, False),
        "hot_planet_solar_temp_threshold": (-273, 1000, False),
        "fleet_save_fuel_reserve_spice": (0, 1_000_000_000_000, False),
    }
    for key, (lo, hi, allow_none) in int_specs.items():
        if key in raw_cfg:
            _validate_strict_int(key, raw_cfg[key], min_val=lo, max_val=hi, allow_none=allow_none)

    float_specs: dict[str, tuple[Optional[float], Optional[float]]] = {
        "ga_mutation_rate": (0.0, 1.0),
        "ga_nn_weight": (0.0, 1.0),
        "storage_warn_ratio": (0.1, 0.99),
        "blast_pig_iron_max_share": (0.1, 1.0),
        "combat_safety_margin": (1.0, 1000.0),
        "raid_min_loot_spice_eq": (0.0, 1e12),
    }
    for key, (lo, hi) in float_specs.items():
        if key in raw_cfg:
            _validate_strict_float(key, raw_cfg[key], min_val=lo, max_val=hi)


def _parse_sim_count_map(field_name: str, raw: Any, *, max_val: int = 1_000_000_000) -> dict[str, int]:
    if raw is None:
        return {}
    if not isinstance(raw, Mapping):
        raise ApiRequestError(
            f"'simulate_combat' field {field_name!r} must be a mapping of unit keys to integer counts."
        )
    out: dict[str, int] = {}
    for k, v in raw.items():
        if not isinstance(k, str) or not k.strip():
            raise ApiRequestError(
                f"'simulate_combat' field {field_name!r} keys must be non-empty strings."
            )
        parsed = _parse_command_int(
            f"{field_name}[{k!r}]",
            v,
            min_val=0,
            max_val=max_val,
        )
        assert parsed is not None
        out[k.strip()] = parsed
    return out


def _format_api_exception(
    exc: BaseException,
    *,
    default: str = "<unprintable>",
    max_len: int = 1024,
    _depth: int = 0,
    _seen: Optional[set[int]] = None,
    _budget: Optional[list[int]] = None,
) -> str:
    seen = _seen if _seen is not None else set()
    budget = _budget if _budget is not None else [_MAX_API_KEY_NODES]
    str_fn = getattr(type(exc), "__str__", BaseException.__str__)
    used_custom_str = False
    raw = ""
    if str_fn not in (BaseException.__str__, KeyError.__str__):
        try:
            raw = _clip_api_str(str(exc), 16_384)
            used_custom_str = True
        except Exception:
            raw = ""
    if not used_custom_str:
        args = getattr(exc, "args", ())
        if not isinstance(args, (tuple, list)) or not args:
            raw = "" if str_fn is BaseException.__str__ else type(exc).__name__
        else:
            vid = id(exc)
            if vid in seen or _depth >= 16 or budget[0] <= 0:
                raw = f"<{type(exc).__name__}>"
            else:
                budget[0] -= 1
                seen.add(vid)
                try:
                    if len(args) == 1:
                        a0 = args[0]
                        if isinstance(a0, str):
                            raw = repr(a0) if isinstance(exc, KeyError) else a0
                        elif isinstance(a0, BaseException):
                            raw = _format_api_exception(
                                a0,
                                default=default,
                                max_len=4096,
                                _depth=_depth + 1,
                                _seen=seen,
                                _budget=budget,
                            )
                        else:
                            raw = _bounded_api_str(a0, default=default, max_len=4096)
                    else:
                        parts: list[str] = []
                        for a in args[:8]:
                            if budget[0] <= 0:
                                parts.append("...")
                                break
                            if isinstance(a, str):
                                parts.append(repr(a))
                            elif isinstance(a, BaseException):
                                parts.append(
                                    _format_api_exception(
                                        a,
                                        default=default,
                                        max_len=4096,
                                        _depth=_depth + 1,
                                        _seen=seen,
                                        _budget=budget,
                                    )
                                )
                            else:
                                parts.append(_bounded_api_str(a, default=default, max_len=4096))
                        if len(args) > 8:
                            parts.append(f"...+{len(args) - 8}")
                        raw = _clip_api_str("(" + ", ".join(parts) + ")")
                finally:
                    seen.discard(vid)
    scrubbed = _scrub_api_str(raw)
    return scrubbed[:max_len] if len(scrubbed) > max_len else scrubbed


def _bounded_api_str(val: Any, default: str = "<unprintable>", max_len: int = 1024) -> str:
    if isinstance(val, BaseException):
        return _format_api_exception(val, default=default, max_len=max_len)
    if isinstance(val, os.PathLike):
        try:
            val = os.fspath(val)
        except Exception:
            return default
        if isinstance(val, os.PathLike):
            return default
    if isinstance(val, (bytes, bytearray)):
        txt = bytes(val).decode("utf-8", errors="replace")
    elif isinstance(val, str):
        txt = val
    else:
        try:
            txt = game_data.safe_key(val)
        except Exception:
            return default
    scrubbed = _scrub_api_str(txt)
    return scrubbed[:max_len] if len(scrubbed) > max_len else scrubbed


def _sanitize_api_json_key(key: Any) -> str:
    if isinstance(key, str):
        return _scrub_api_str(key)
    if key is None or isinstance(key, bool):
        return str(key)
    if isinstance(key, int):
        if -_MAX_API_JSON_INT <= key <= _MAX_API_JSON_INT:
            return str(key)
        return str(_MAX_API_JSON_INT if key > 0 else -_MAX_API_JSON_INT)
    if isinstance(key, float):
        return str(key) if math.isfinite(key) else "0.0"
    if isinstance(key, BaseException):
        return _format_api_exception(key, max_len=512)
    if isinstance(key, os.PathLike):
        try:
            key = os.fspath(key)
        except Exception:
            return "<unprintable>"
        return _sanitize_api_json_key(key) if not isinstance(key, os.PathLike) else "<unprintable>"
    if isinstance(key, (bytes, bytearray)):
        return _scrub_api_str(bytes(key).decode("utf-8", errors="replace"))
    red_k = _redact_api_key(key)
    try:
        k_txt = game_data.safe_key(red_k, max_int=_MAX_API_JSON_INT)
    except Exception:
        k_txt = _bounded_api_str(red_k, default="<unprintable>", max_len=512)
    return _scrub_api_str(k_txt)


def _sanitize_finite_json(
    obj: Any,
    *,
    _depth: int = 0,
    _seen: Optional[set[int]] = None,
    _budget: Optional[list[int]] = None,
) -> Any:
    seen = _seen if _seen is not None else set()
    budget = _budget if _budget is not None else [_MAX_API_TREE_NODES]

    def _walk(val: Any, d: int) -> Any:
        tv = type(val)
        if val is None or tv is bool:
            return val
        if tv is str:
            return val if len(val) < 5 else _scrub_api_str(val)
        if tv is int:
            if -_MAX_API_JSON_INT <= val <= _MAX_API_JSON_INT:
                return val
            return _MAX_API_JSON_INT if val > 0 else -_MAX_API_JSON_INT
        if tv is float:
            return val if math.isfinite(val) else 0.0
        is_dict = tv is dict
        is_seq = tv is list or tv is tuple
        if not (is_dict or is_seq):
            if isinstance(val, bool):
                return bool(val)
            if isinstance(val, str):
                s = str(val)
                return s if len(s) < 5 else _scrub_api_str(s)
            if isinstance(val, int):
                iv = int(val)
                if -_MAX_API_JSON_INT <= iv <= _MAX_API_JSON_INT:
                    return iv
                return _MAX_API_JSON_INT if iv > 0 else -_MAX_API_JSON_INT
            if isinstance(val, float):
                fv = float(val)
                return fv if math.isfinite(fv) else 0.0
            if isinstance(val, (bytes, bytearray)):
                return _scrub_api_str(bytes(val).decode("utf-8", errors="replace"))
            if isinstance(val, BaseException):
                return _format_api_exception(val, default="<unprintable>", max_len=1024)
        if is_dict or is_seq or isinstance(val, (Mapping, list, tuple, set, frozenset, os.PathLike)):
            is_pathlike = (not (is_dict or is_seq)) and isinstance(val, os.PathLike)
            vid = id(val)
            if vid in seen:
                return "<unprintable>" if is_pathlike else "[Cycle]"
            if d >= _MAX_API_TREE_DEPTH or budget[0] <= 0:
                return "<unprintable>" if is_pathlike else "[Truncated]"
            budget[0] -= 1
            seen.add(vid)
            try:
                if is_pathlike:
                    try:
                        raw = os.fspath(val)
                    except Exception:
                        return "<unprintable>"
                    if isinstance(raw, (bytes, bytearray)):
                        return _scrub_api_str(bytes(raw).decode("utf-8", errors="replace"))
                    if isinstance(raw, str):
                        return _scrub_api_str(raw)
                    if isinstance(raw, os.PathLike):
                        return "<unprintable>"
                    return _bounded_api_str(raw)
                if is_dict:
                    out_d: dict[str, Any] = {}
                    for k, v in val.items():
                        if budget[0] <= 0:
                            out_d["[Truncated]"] = "[Truncated]"
                            break
                        if type(k) is str:
                            clean_k = k if len(k) < 5 else _scrub_api_str(k)
                        else:
                            clean_k = _sanitize_api_json_key(k)
                        if is_secret_dict_key(clean_k):
                            if v is None or v == "":
                                out_d[clean_k] = v
                            elif isinstance(v, (bytes, bytearray)) and len(v) == 0:
                                out_d[clean_k] = ""
                            else:
                                out_d[clean_k] = "REDACTED"
                        else:
                            out_d[clean_k] = _walk(v, d + 1)
                    return out_d
                if isinstance(val, Mapping):
                    out: dict[str, Any] = {}
                    for k, v in val.items():
                        if budget[0] <= 0:
                            out["[Truncated]"] = "[Truncated]"
                            break
                        clean_k = _sanitize_api_json_key(k)
                        if is_secret_dict_key(clean_k):
                            if v is None or v == "":
                                out[clean_k] = v
                            elif isinstance(v, (bytes, bytearray)) and len(v) == 0:
                                out[clean_k] = ""
                            else:
                                out[clean_k] = "REDACTED"
                        else:
                            out[clean_k] = _walk(v, d + 1)
                    return out
                out_list: list[Any] = []
                for v in val:
                    if budget[0] <= 0:
                        out_list.append("[Truncated]")
                        break
                    out_list.append(_walk(v, d + 1))
                return out_list
            finally:
                seen.discard(vid)
        return _bounded_api_str(val, default="<unprintable>", max_len=1024)

    return _walk(obj, _depth)


def _sanitize_optimizer_summary(raw: Optional[Mapping[str, Any]]) -> Optional[dict[str, Any]]:
    if not isinstance(raw, Mapping) or not raw:
        return None
    clean = _sanitize_finite_json(dict(raw))
    if not isinstance(clean, dict):
        return None
    if "improvement_pct" in clean:
        try:
            imp = float(clean["improvement_pct"])
        except Exception:
            imp = 0.0
        if not math.isfinite(imp):
            imp = 0.0
        clean["improvement_pct"] = round(max(-999.9, min(999.9, imp)), 4)
    return clean


def ensure_debug_browser_ready(
    *,
    debug_port: int = DEFAULT_DEBUG_PORT,
    env_file: Path = Path(".env"),
    account: str = "default",
) -> None:
    """Ensure Chrome stays open on `debug_port` and logged into the configured universe."""
    if not is_debug_port_open(debug_port):
        creds = load_credentials(env_path=env_file, account=account)
        cfg = BrowserConfig(
            profile_dir=Path(".browser_profile") / creds.account_name,
            debug_port=debug_port,
            detach=True,
        )
        login_with_browser(creds=creds, config=cfg, require_in_game=True)
        return

    try:
        with GameBrowserClient.attach(port=debug_port) as client:
            client.ensure_in_game()
    except RateLimitError:
        raise
    except SpaceInvasionAuthError:
        creds = load_credentials(env_path=env_file, account=account)
        cfg = BrowserConfig(
            profile_dir=Path(".browser_profile") / creds.account_name,
            debug_port=debug_port,
            attach=True,
            detach=True,
        )
        login_with_browser(creds=creds, config=cfg, require_in_game=True)


def _safe_limiter_wait(
    raw: Any,
    default: float = 8.0,
    *,
    min_val: float = 0.0,
    max_val: float = 3600.0,
) -> float:
    if isinstance(raw, bool) or raw is None or isinstance(raw, (dict, list, tuple, set, frozenset)):
        return default
    if isinstance(raw, int):
        if raw.bit_length() > 128:
            return max_val if raw > 0 else min_val
        val = float(raw)
    elif isinstance(raw, float):
        val = raw
    else:
        if isinstance(raw, (bytes, bytearray)):
            raw = bytes(raw).decode("utf-8", errors="replace")
        if isinstance(raw, str):
            txt = raw.strip()
            if not txt or not txt.isascii():
                return default
            raw = txt
        try:
            val = float(raw)
        except Exception:
            return default
    if not math.isfinite(val):
        return default
    return max(min_val, min(max_val, val))


def _safe_debug_port(raw: Any, default: int = DEFAULT_DEBUG_PORT) -> int:
    port = game_data.safe_int(raw, default)
    return max(0, min(65535, port))


def _safe_hourly_budget(raw: Any, default: int = 60) -> int:
    cap = game_data.safe_int(raw, default)
    return max(0, min(1_000_000, cap))


class GameApiBridge:
    """Thread-safe facade connecting HTTP API calls to the Chrome debug port."""

    SNAPSHOT_CACHE_TTL_S = 6.0
    RESEARCH_SYNC_TTL_S = 60.0
    SHIPYARD_SYNC_TTL_S = 120.0
    SPIO_SYNC_TTL_S = 30.0

    def __init__(
        self,
        *,
        debug_port: int = DEFAULT_DEBUG_PORT,
        env_file: Path = Path(".env"),
        account: str = "default",
        config_path: Path = Path("config.yaml"),
        max_actions_per_hour: Optional[int] = None,
        log_dir: Optional[Path] = Path("logs"),
        db_path: Optional[Path] = None,
        client_factory: Optional[Callable[[int], Any]] = None,
        ensure_session_fn: Optional[Callable[[], None]] = None,
        page_limiter: Optional[PageViewLimiter] = None,
        max_limiter_wait: float = 8.0,
        executor_max_limiter_wait: Optional[float] = 8.0,
    ) -> None:
        self.debug_port = _safe_debug_port(debug_port, DEFAULT_DEBUG_PORT)
        self.env_file = env_file
        self.account = account
        self.config_path = config_path
        self.strategy_config: StrategyConfig = load_strategy_config(config_path)
        self.log_dir = log_dir
        self._lock = threading.Lock()
        self._page_limiter = page_limiter or PageViewLimiter()
        self._max_limiter_wait = _safe_limiter_wait(max_limiter_wait, 8.0)
        if executor_max_limiter_wait is None:
            self._executor_max_limiter_wait: Optional[float] = None
        elif isinstance(executor_max_limiter_wait, (int, float)) and not isinstance(
            executor_max_limiter_wait, bool
        ):
            val = _safe_limiter_wait(executor_max_limiter_wait, self._max_limiter_wait)
            self._executor_max_limiter_wait = val if val != 8.0 else self._max_limiter_wait
        else:
            self._executor_max_limiter_wait = self._max_limiter_wait
        self._last_state: Optional[GameState] = None
        self._last_optimizer_summary: Optional[dict[str, Any]] = None
        self._last_url: Optional[str] = None
        self._last_snapshot_at: float = 0.0
        self._last_buildings_fetched: bool = False
        self._last_home_fetched: bool = False
        self._last_home_at: Optional[float] = None
        self._last_recorded_state_obj: Optional[GameState] = None
        self._active_planet_confirmed: bool = False
        self._last_research_sync_at: float = 0.0
        self._last_research_sync_sig: Optional[tuple[int, tuple[tuple[str, int], ...], bool]] = None
        self._last_shipyard_sync_at: float = 0.0
        self._last_shipyard_sync_sig: Optional[tuple[int, int, tuple[tuple[str, int], ...]]] = None
        self._last_spio_sync_at: float = 0.0
        self._economy_rates: dict[int, dict[str, int]] = {}
        self._scanned_systems: dict[str, float] = {}
        self._throttle_timestamps: dict[int, float] = {}
        self._rank_history_path: Optional[Path] = (
            (log_dir.parent / "state" / "rank_history.json") if log_dir is not None else None
        )
        resolved_db = (
            db_path
            if db_path is not None
            else ((log_dir.parent / "state" / "spaceinvasion.db") if log_dir is not None else None)
        )
        self._db: Optional[GameDatabase] = (
            GameDatabase(resolved_db, legacy_rank_json=self._rank_history_path)
            if resolved_db is not None
            else None
        )
        if self._db is not None:
            self._rank_samples = self._db.get_rank_samples()
            self._economy_rates.update(self._db.get_all_economy_rates())
            self._raid_targets: tuple[dict[str, Any], ...] = self._enrich_raid_targets(
                self._db.get_spy_targets(limit=100)
            )
            self._scanned_systems.update(self._db.get_galaxy_scans())
            self._throttle_timestamps.update(self._db.get_last_throttle_timestamps())
        else:
            self._rank_samples = load_rank_history(self._rank_history_path)
            self._raid_targets = ()
        self._bot_control: dict[str, Any] = {"paused": False, "mode_override": None}
        raw_budget = (
            max_actions_per_hour
            if max_actions_per_hour is not None
            else self.strategy_config.max_actions_per_hour
        )
        budget = _safe_hourly_budget(raw_budget, 60)
        self._live_executor = ActionExecutor(
            dry_run=False,
            max_actions_per_hour=budget,
            allow_fleetsave=self.strategy_config.allow_fleetsave,
            allow_raids=self.strategy_config.allow_raids,
            page_limiter=self._page_limiter,
            max_limiter_wait=self._executor_max_limiter_wait,
        )
        self._dry_executor = ActionExecutor(
            dry_run=True,
            max_actions_per_hour=budget,
            allow_fleetsave=self.strategy_config.allow_fleetsave,
            allow_raids=self.strategy_config.allow_raids,
            page_limiter=self._page_limiter,
            max_limiter_wait=self._executor_max_limiter_wait,
        )
        self._client_factory = client_factory or (
            lambda port: GameBrowserClient.attach(
                port=port,
                page_limiter=self._page_limiter,
                max_limiter_wait=self._max_limiter_wait,
            )
        )
        self._ensure_session_fn = ensure_session_fn or (
            lambda: ensure_debug_browser_ready(
                debug_port=self.debug_port,
                env_file=self.env_file,
                account=self.account,
            )
        )

    def close(self) -> None:
        """Close owned database resources idempotently."""
        lock = getattr(self, "_lock", None)
        with (lock if lock is not None else contextlib.nullcontext()):
            db = getattr(self, "_db", None)
            if db is not None:
                self._db = None
                close_fn = getattr(db, "close", None)
                if callable(close_fn):
                    close_fn()

    def __enter__(self) -> "GameApiBridge":
        return self

    def __exit__(self, *_exc: Any) -> None:
        self.close()

    def record_rate_limit(self, retry_after: Optional[float] = None) -> float:
        return self._page_limiter.record_rate_limit(retry_after)

    def rate_limit_remaining(self) -> float:
        return self._page_limiter.rate_limit_remaining()

    def _record_server_rate_limit(self, exc: RateLimitError) -> None:
        if not getattr(exc, "server_throttle", True):
            return
        if getattr(exc, "_recorded_limiter_id", None) == id(self._page_limiter):
            return
        rem = _safe_limiter_wait(self._page_limiter.rate_limit_remaining(), 0.0, min_val=0.0)
        req = _safe_limiter_wait(getattr(exc, "retry_after", 45.0), 45.0, min_val=1.0)
        if rem > 0.0 and req <= rem + 1.0:
            setattr(exc, "_recorded_limiter_id", id(self._page_limiter))
            return
        self._page_limiter.record_rate_limit(req)
        setattr(exc, "_recorded_limiter_id", id(self._page_limiter))

    def _raise_if_rate_limited(self) -> None:
        rem = self._page_limiter.rate_limit_remaining()
        if rem > 0.0:
            err = RateLimitError(
                f"SpaceInvasion rate-limit cooldown active ({rem:.1f}s remaining; retry after {rem:.1f}s).",
                retry_after=rem,
                server_throttle=True,
            )
            setattr(err, "_recorded_limiter_id", id(self._page_limiter))
            raise err

    def _get_scanned_systems(self) -> dict[str, float]:
        if self._db is not None:
            return self._db.get_galaxy_scans()
        return dict(self._scanned_systems)

    def _get_throttle_cooldowns(self) -> dict[int, float]:
        if self._db is not None:
            return self._db.get_last_throttle_timestamps()
        return dict(self._throttle_timestamps)

    def _attach_raid_stats(self, state: GameState) -> GameState:
        if self._db is None:
            return state
        get_stats = getattr(self._db, "get_raid_stats", None)
        if not callable(get_stats):
            return state
        stats = get_stats()
        if isinstance(stats, Mapping):
            return replace(state, raid_stats=dict(stats))
        return state

    def _serialize_action(
        self,
        action: PlannedAction,
        state: Optional[GameState] = None,
    ) -> dict[str, Any]:
        enriched = attach_action_etas(state, [action])[0] if state is not None else action
        raw = asdict(enriched)
        for key in ("wait_hours", "wait_seconds", "duration_seconds", "total_hours", "total_seconds"):
            val = raw.get(key)
            if isinstance(val, float) and not math.isfinite(val):
                raw[key] = None if key.startswith(("wait_", "total_")) else 0.0
        return _sanitize_finite_json(_redact_api_payload(raw))

    def _plan_with_summary(
        self,
        state: GameState,
        *,
        config: Optional[StrategyConfig] = None,
    ) -> tuple[list[PlannedAction], Optional[dict[str, Any]]]:
        cfg = config or self.strategy_config
        enriched_state = self._attach_raid_stats(state)
        eff_mode = normalize_planner_mode(getattr(cfg, "planner_mode", "priority"))
        if eff_mode == "ga_nn":
            with _GLOBAL_OPTIMIZER_LOCK:
                actions = plan_actions(
                    enriched_state,
                    config=cfg,
                    scanned_systems=self._get_scanned_systems(),
                    throttle_cooldowns=self._get_throttle_cooldowns(),
                )
                raw_summary = get_last_optimizer_summary()
            summary = _sanitize_optimizer_summary(raw_summary)
            if config is None or normalize_planner_mode(self.strategy_config.planner_mode) == "ga_nn":
                self._last_optimizer_summary = summary
            return actions, summary
        actions = plan_actions(
            enriched_state,
            config=cfg,
            scanned_systems=self._get_scanned_systems(),
            throttle_cooldowns=self._get_throttle_cooldowns(),
        )
        return actions, None

    def _plan_with_context(
        self,
        state: GameState,
        *,
        config: Optional[StrategyConfig] = None,
    ) -> list[PlannedAction]:
        actions, _ = self._plan_with_summary(state, config=config)
        return actions

    @staticmethod
    def _supports_kwarg(fn: Callable[..., Any], kwarg_name: str) -> bool:
        targets: list[Any] = [fn]
        for attr in ("side_effect", "_mock_wraps"):
            wrapped = getattr(fn, attr, None)
            if callable(wrapped) and not isinstance(wrapped, type):
                targets.append(wrapped)
        for target in targets:
            try:
                sig = inspect.signature(target)
            except (TypeError, ValueError):
                return False
            if kwarg_name not in sig.parameters and not any(
                p.kind == inspect.Parameter.VAR_KEYWORD for p in sig.parameters.values()
            ):
                return False
        return True

    @staticmethod
    def _call_with_optional_priority(
        fn: Callable[..., Any],
        *args: Any,
        priority: str = "interactive",
        **kwargs: Any,
    ) -> Any:
        call_kwargs = dict(kwargs)
        if GameApiBridge._supports_kwarg(fn, "priority"):
            call_kwargs["priority"] = priority
        return fn(*args, **call_kwargs)

    @staticmethod
    def _read_snapshot(
        client: Any,
        *,
        include_buildings: bool = True,
        include_research: bool = True,
        include_shipyard: bool = True,
        include_home: bool = True,
        return_home: bool = False,
        priority: str = "normal",
    ) -> GameState:
        fn = getattr(client, "read_full_snapshot")
        extra: dict[str, Any] = {}
        if not include_home and GameApiBridge._supports_kwarg(fn, "include_home"):
            extra["include_home"] = False
        return GameApiBridge._call_with_optional_priority(
            fn,
            include_buildings=include_buildings,
            include_research=include_research,
            include_shipyard=include_shipyard,
            return_home=return_home,
            priority=priority,
            **extra,
        )

    @staticmethod
    def _open_galaxy(
        client: Any,
        *,
        galaxy: Optional[int] = None,
        system: Optional[int] = None,
        priority: str = "interactive",
    ) -> str:
        fn = getattr(client, "open_galaxy")
        return GameApiBridge._call_with_optional_priority(
            fn,
            galaxy=galaxy,
            system=system,
            priority=priority,
        )

    def health(self) -> dict[str, Any]:
        with self._lock:
            port_open = is_debug_port_open(self.debug_port)
            rate_rem = self._page_limiter.rate_limit_remaining()
            in_game = port_open and (self._last_state is not None or rate_rem > 0.0)
            current_url: Optional[str] = self._last_url
            if port_open and rate_rem <= 0.0:
                try:
                    with self._client_factory(self.debug_port) as client:
                        try:
                            client.ensure_in_game()
                            in_game = True
                        except RateLimitError as exc:
                            self._record_server_rate_limit(exc)
                            in_game = port_open
                        except SpaceInvasionAuthError:
                            in_game = False
                        current_url = str(getattr(client.driver, "current_url", "") or "")
                except RateLimitError as exc:
                    self._record_server_rate_limit(exc)
                    in_game = port_open
                except SpaceInvasionAuthError:
                    in_game = False
            elif port_open and rate_rem > 0.0:
                in_game = True
            self._last_url = current_url
            rate_rem = self._page_limiter.rate_limit_remaining()
            return _sanitize_finite_json(
                _redact_api_payload(
                    {
                        "ok": port_open,
                        "debug_port": self.debug_port,
                        "debug_port_open": port_open,
                        "in_game": in_game,
                        "rate_limited": rate_rem > 0.0,
                        "retry_after": round(rate_rem, 1),
                        "page_views_last_60s": self._page_limiter.views_last_60s(),
                        "page_view_limit_per_minute": self._page_limiter.max_views_per_minute,
                        "rate_limit_hits": self._page_limiter.rate_limit_hits,
                        "url": current_url if current_url else None,
                        "remaining_hourly_actions": self._live_executor.remaining_hourly_budget(),
                        "bot_control": dict(self._bot_control),
                    }
                )
            )

    def get_bot_control(self) -> dict[str, Any]:
        with self._lock:
            return _sanitize_finite_json(
                _redact_api_payload(
                    {
                        "ok": True,
                        **dict(self._bot_control),
                        "scanned_systems": self._get_scanned_systems(),
                        "throttle_cooldowns": self._get_throttle_cooldowns(),
                    }
                )
            )

    def _read_log_tail(self, filename: str, lines: int = 40) -> str:
        if self.log_dir is None:
            return ""
        target = self.log_dir / filename
        if not target.exists():
            return ""
        raw_lines = target.read_text(encoding="utf-8", errors="replace").splitlines()
        return str(_redact_api_payload("\n".join(raw_lines[-lines:])))

    def _merge_cached_offers(self, state: GameState) -> GameState:
        prev = self._last_state
        if prev is None:
            return state
        prev_by_id = {p.planet_id: p for p in prev.planets}
        prev_b_jobs = sum(1 for q in prev.active_queues if q.category == "building")
        cur_b_jobs = sum(1 for q in state.active_queues if q.category == "building")
        queues_same = prev_b_jobs == cur_b_jobs

        changed_planets = False
        new_planets: list[PlanetState] = []
        for planet in state.planets:
            old_p = prev_by_id.get(planet.planet_id)
            if old_p is None:
                new_planets.append(planet)
                continue
            b_offers = planet.building_offers
            s_offers = planet.ship_offers
            if (
                not b_offers
                and old_p.building_offers
                and planet.buildings == old_p.buildings
                and state.researches == prev.researches
                and (queues_same or planet.planet_id != state.active_planet_id)
            ):
                b_offers = old_p.building_offers
                changed_planets = True
            if (
                not s_offers
                and old_p.ship_offers
                and planet.building_level(9) == old_p.building_level(9)
                and state.researches == prev.researches
            ):
                s_offers = old_p.ship_offers
                changed_planets = True
            if b_offers is not planet.building_offers or s_offers is not planet.ship_offers:
                new_planets.append(
                    replace(planet, building_offers=b_offers, ship_offers=s_offers)
                )
            else:
                new_planets.append(planet)

        prev_b8 = {p.planet_id: p.building_level(8) for p in prev.planets}
        cur_b8 = {p.planet_id: p.building_level(8) for p in state.planets}
        res_offers = state.research_offers
        changed_state = False
        if (
            not res_offers
            and prev.research_offers
            and state.researches == prev.researches
            and state.active_planet_id == prev.active_planet_id
            and cur_b8 == prev_b8
        ):
            if any(q.category == "research" for q in state.active_queues):
                res_offers = prev.research_offers
            elif any(o.active for o in prev.research_offers):
                from spaceinvasion import game_data
                from spaceinvasion.strategy import _resources_from_dict

                active_p = state.active_planet()
                has_lab = active_p is not None and active_p.building_level(8) >= 1
                rebuilt_offers = []
                for o in prev.research_offers:
                    st_low = str(o.status_text or "").lower()
                    was_suppressed = o.active or any(
                        tok in st_low
                        for tok in ("in research", "queue busy", "busy", "active", "cancel", "unavailable")
                    )
                    if not was_suppressed:
                        rebuilt_offers.append(o)
                        continue
                    unmet = game_data.missing_prerequisites(
                        "research",
                        o.research_key,
                        buildings=active_p.buildings if active_p is not None else {},
                        researches=state.researches,
                    )
                    check_cost = o.cost
                    if check_cost.is_zero() and o.research_key:
                        unit = game_data.resolve_unit("research", o.research_key)
                        raw_cost = ((unit or {}).get("levels") or {}).get(o.target_level, {}).get("cost")
                        if isinstance(raw_cost, dict):
                            check_cost = _resources_from_dict(raw_cost)
                    can_afford = active_p is not None and active_p.resources.can_afford(check_cost)
                    if has_lab and not unmet and can_afford and not check_cost.is_zero():
                        rebuilt_offers.append(
                            replace(o, cost=check_cost, active=False, can_research=True, status_text="Available")
                        )
                    elif has_lab and not unmet and not can_afford:
                        rebuilt_offers.append(
                            replace(
                                o,
                                cost=check_cost,
                                active=False,
                                can_research=False,
                                status_text="Not enough resources",
                            )
                        )
                    else:
                        rebuilt_offers.append(
                            replace(
                                o,
                                cost=check_cost,
                                active=False,
                                can_research=False,
                                status_text="Prerequisites unmet" if unmet else "Unavailable",
                            )
                        )
                res_offers = tuple(rebuilt_offers)
            else:
                res_offers = prev.research_offers
            changed_state = True
        if changed_planets or changed_state:
            return replace(
                state,
                planets=tuple(new_planets) if changed_planets else state.planets,
                research_offers=res_offers,
            )
        return state

    def _enrich_raid_targets(self, rows: Sequence[dict[str, Any]]) -> tuple[dict[str, Any], ...]:
        if not rows:
            return ()
        per_target: dict[str, Any] = {}
        if self._db is not None:
            get_stats = getattr(self._db, "get_raid_stats", None)
            if callable(get_stats):
                stats = get_stats()
                if isinstance(stats, Mapping) and isinstance(stats.get("per_target"), Mapping):
                    per_target = stats["per_target"]
        enriched: list[dict[str, Any]] = []
        for raw in rows:
            if not isinstance(raw, Mapping):
                continue
            item = dict(raw)
            coords = normalize_coords(item.get("coords") or item.get("target_coords") or "")
            pt = per_target.get(coords) if coords else None
            if isinstance(pt, Mapping) and _safe_api_int(pt.get("attempts", 0)) > 0:
                item.update(
                    {
                        "attack_attempts": game_data.cap_json_int(_safe_api_int(pt.get("attempts", 0))),
                        "attack_wins": game_data.cap_json_int(_safe_api_int(pt.get("wins", 0))),
                        "attack_losses": game_data.cap_json_int(_safe_api_int(pt.get("losses", 0))),
                        "attack_success_rate": _safe_api_float(pt.get("success_rate", 0.0)),
                        "avg_attack_loot": _sanitize_finite_json(dict(pt.get("avg_loot") or {}))
                        if isinstance(pt.get("avg_loot"), Mapping)
                        else {},
                        "avg_attack_loot_spice_eq": _safe_api_float(pt.get("avg_loot_spice_eq", 0.0)),
                        "total_attack_loot": _sanitize_finite_json(dict(pt.get("total_loot") or {}))
                        if isinstance(pt.get("total_loot"), Mapping)
                        else {},
                        "total_attack_loot_spice_eq": _safe_api_float(pt.get("total_loot_spice_eq", 0.0)),
                        "recency_weighted_loot_spice_eq": _safe_api_float(
                            pt.get("recency_weighted_loot_spice_eq", 0.0)
                        ),
                        "last_attack_outcome": _bounded_api_str(pt.get("last_outcome") or "", default=""),
                        "last_attack_loot": _sanitize_finite_json(dict(pt.get("last_loot") or {}))
                        if isinstance(pt.get("last_loot"), Mapping)
                        else {},
                        "last_attack_loot_spice_eq": _safe_api_float(pt.get("last_loot_spice_eq", 0.0)),
                        "last_attack_ts": _safe_api_float(pt.get("last_attacked_ts", 0.0)),
                    }
                )
            else:
                item.update(
                    {
                        "attack_attempts": 0,
                        "attack_wins": 0,
                        "attack_losses": 0,
                        "attack_success_rate": None,
                        "avg_attack_loot": None,
                        "avg_attack_loot_spice_eq": None,
                        "total_attack_loot": None,
                        "total_attack_loot_spice_eq": None,
                        "recency_weighted_loot_spice_eq": None,
                        "last_attack_outcome": "",
                        "last_attack_loot": None,
                        "last_attack_loot_spice_eq": None,
                        "last_attack_ts": 0.0,
                    }
                )
            enriched.append(item)
        return tuple(enriched)

    def _merge_economy_rates(self, state: GameState) -> GameState:
        for pid, rates in getattr(self._live_executor, "last_economy_rates", {}).items():
            if rates:
                self._economy_rates[int(pid)] = dict(rates)

        changed = False
        updated_planets: list[PlanetState] = []
        for planet in state.planets:
            if planet.economy_rates:
                self._economy_rates[planet.planet_id] = dict(planet.economy_rates)
                updated_planets.append(planet)
            elif planet.planet_id in self._economy_rates:
                changed = True
                updated_planets.append(
                    replace(planet, economy_rates=dict(self._economy_rates[planet.planet_id]))
                )
            else:
                updated_planets.append(planet)
        if self._db is not None:
            self._raid_targets = self._enrich_raid_targets(self._db.get_spy_targets(limit=100))
        out_state = replace(state, planets=tuple(updated_planets)) if changed else state
        if self._raid_targets and getattr(out_state, "raid_targets", ()) != self._raid_targets:
            out_state = replace(out_state, raid_targets=self._raid_targets)
        return out_state

    def _sync_observed_active_planet_state(
        self,
        fallback_state: GameState,
        *,
        obs_pid: Optional[int],
        obs_confirmed: Optional[bool],
    ) -> None:
        target_state = self._last_state or fallback_state
        planet_ids = {p.planet_id for p in target_state.planets}
        if obs_confirmed is False:
            if len(target_state.planets) > 1:
                self._active_planet_confirmed = False
        elif obs_confirmed is True:
            self._active_planet_confirmed = True
            if (
                isinstance(obs_pid, int)
                and not isinstance(obs_pid, bool)
                and obs_pid in planet_ids
                and target_state.active_planet_id != obs_pid
            ):
                was_recorded = target_state is self._last_recorded_state_obj
                upd_fields: dict[str, Any] = {"active_planet_id": obs_pid}
                if len(target_state.planets) > 1:
                    upd_fields["research_offers"] = ()
                self._last_state = replace(target_state, **upd_fields)
                if was_recorded:
                    self._last_recorded_state_obj = self._last_state
                active_p = self._last_state.active_planet()
                if active_p is None or not active_p.building_offers:
                    self._last_buildings_fetched = False
        elif len(target_state.planets) <= 1:
            self._active_planet_confirmed = True

    def _refresh_cached_economy(self, state: GameState) -> GameState:
        was_recorded = state is self._last_recorded_state_obj
        merged = self._merge_economy_rates(state)
        self._last_state = merged
        if was_recorded:
            self._last_recorded_state_obj = merged
        return merged

    def _is_home_fresh(self, now_mono: float) -> bool:
        if self._last_state is None:
            return False
        if (
            self._last_home_fetched
            and self._last_home_at is not None
            and (now_mono - self._last_home_at) <= 15.0
        ):
            return True
        return (
            self._last_state is not self._last_recorded_state_obj
            and self._last_state.tutorial is not None
        )

    def _record_state(
        self,
        state: GameState,
        *,
        fetched_buildings: bool = False,
        fetched_research: bool = False,
        fetched_shipyard: bool = False,
        fetched_home: bool = False,
        observed_active_planet: bool = False,
        update_snapshot_ts: bool = True,
    ) -> GameState:
        prev = self._last_state
        if prev is None:
            self._last_home_fetched = False
            self._last_home_at = None
            self._active_planet_confirmed = False
        if prev is not None and (not fetched_home or not observed_active_planet):
            planet_ids = {p.planet_id for p in state.planets}
            upd_kwargs: dict[str, Any] = {}
            if (
                not observed_active_planet
                and prev.active_planet_id is not None
                and prev.active_planet_id in planet_ids
            ):
                upd_kwargs["active_planet_id"] = prev.active_planet_id
            if not fetched_home:
                if state.tutorial is None and prev.tutorial is not None:
                    upd_kwargs["tutorial"] = prev.tutorial
                if state.speedup_factor is None and prev.speedup_factor is not None:
                    upd_kwargs["speedup_factor"] = prev.speedup_factor
                if not state.newbie_protection and prev.newbie_protection:
                    upd_kwargs["newbie_protection"] = True
                if not state.fleet_alerts and prev.fleet_alerts:
                    upd_kwargs["fleet_alerts"] = prev.fleet_alerts
                if prev.active_queues:
                    refreshed_q_cats: set[str] = set()
                    if fetched_buildings:
                        refreshed_q_cats.add("building")
                    if fetched_research and state.research_offers:
                        refreshed_q_cats.add("research")
                    cur_cats = {q.category for q in state.active_queues} | refreshed_q_cats
                    extra_q = [q for q in prev.active_queues if q.category not in cur_cats]
                    if extra_q:
                        upd_kwargs["active_queues"] = tuple(list(state.active_queues) + extra_q)
            if upd_kwargs:
                state = replace(state, **upd_kwargs)
        now = time.monotonic()
        if fetched_home:
            self._last_home_fetched = True
            self._last_home_at = now
        elif update_snapshot_ts and (
            prev is None
            or not self._last_home_fetched
            or self._last_home_at is None
            or (now - self._last_home_at) > 15.0
        ):
            self._last_home_fetched = False
        if observed_active_planet or len(state.planets) <= 1:
            self._active_planet_confirmed = True

        merged = self._merge_economy_rates(self._merge_cached_offers(state))
        self._last_state = merged
        self._last_recorded_state_obj = merged
        if update_snapshot_ts:
            self._last_snapshot_at = now
        if fetched_buildings:
            self._last_buildings_fetched = True
        else:
            active_p_chk = merged.active_planet()
            if active_p_chk is None or not active_p_chk.building_offers:
                self._last_buildings_fetched = False
        active_p = merged.active_planet()
        b8_lvl = active_p.building_level(8) if active_p is not None else 0
        b9_lvl = active_p.building_level(9) if active_p is not None else 0
        pid_sig = _safe_api_int(active_p.planet_id) if active_p is not None else 0
        res_sig = tuple(
            sorted((game_data.safe_key(k), _safe_api_int(v)) for k, v in (merged.researches or {}).items())
        )
        res_busy = any(q.category == "research" for q in merged.active_queues)
        if fetched_research:
            self._last_research_sync_at = now
            self._last_research_sync_sig = (b8_lvl, res_sig, res_busy)
        if fetched_shipyard:
            self._last_shipyard_sync_at = now
            self._last_shipyard_sync_sig = (pid_sig, b9_lvl, res_sig)
        if self._db is not None:
            self._rank_samples = self._db.record_state_snapshot(merged)
        else:
            self._rank_samples = record_rank_snapshot(self._rank_history_path, merged)
        return merged

    def dashboard(self, *, refresh: bool = False) -> dict[str, Any]:
        """Return combined UI telemetry, plan, buildings ETAs, combat, ranking, config, and logs."""
        with self._lock:
            port_open = is_debug_port_open(self.debug_port)
            rate_rem = self._page_limiter.rate_limit_remaining()
            in_game = port_open and (self._last_state is not None or rate_rem > 0.0)
            current_url = self._last_url
            if port_open and rate_rem <= 0.0 and (refresh or self._last_state is None):
                try:
                    with self._client_factory(self.debug_port) as client:
                        snap = self._read_snapshot(
                            client,
                            include_buildings=True,
                            include_research=True,
                            include_shipyard=True,
                            return_home=False,
                            priority="normal",
                        )
                        obs_active = bool(getattr(client, "last_snapshot_observed_active_planet", True))
                        self._record_state(
                            snap,
                            fetched_buildings=True,
                            fetched_research=True,
                            fetched_shipyard=True,
                            fetched_home=True,
                            observed_active_planet=obs_active,
                        )
                        current_url = str(getattr(client.driver, "current_url", "") or "")
                        self._last_url = current_url
                        in_game = True
                except RateLimitError as exc:
                    self._record_server_rate_limit(exc)
                    in_game = port_open
                except SpaceInvasionAuthError:
                    in_game = False

            dash_state = (
                self._attach_raid_stats(self._last_state)
                if self._last_state is not None
                else None
            )
            eff_mode = normalize_planner_mode(self.strategy_config.planner_mode)
            if dash_state is not None:
                actions, opt_summary = self._plan_with_summary(dash_state)
            else:
                actions = []
                opt_summary = self._last_optimizer_summary if eff_mode == "ga_nn" else None
            ranking = (
                build_ranking_evolution(dash_state, self._rank_samples)
                if dash_state is not None
                else None
            )
            buildings_eta = (
                build_buildings_eta(dash_state)
                if dash_state is not None
                else None
            )
            combat = (
                build_combat_overview(
                    dash_state,
                    safety_margin=self.strategy_config.combat_safety_margin,
                )
                if dash_state is not None
                else None
            )
            rate_rem = self._page_limiter.rate_limit_remaining()
            return _sanitize_finite_json(
                {
                    "ok": True,
                    "server_boot_id": _SERVER_BOOT_ID,
                    **compute_ui_assets_state(),
                    "health": {
                        "ok": port_open,
                        "debug_port": self.debug_port,
                        "debug_port_open": port_open,
                        "in_game": in_game,
                        "rate_limited": rate_rem > 0.0,
                        "retry_after": round(rate_rem, 1),
                        "page_views_last_60s": self._page_limiter.views_last_60s(),
                        "page_view_limit_per_minute": self._page_limiter.max_views_per_minute,
                        "rate_limit_hits": self._page_limiter.rate_limit_hits,
                        "url": current_url if current_url else None,
                        "remaining_hourly_actions": self._live_executor.remaining_hourly_budget(),
                    },
                    "bot_control": dict(self._bot_control),
                    "planner_mode": eff_mode,
                    "config": asdict(self.strategy_config),
                    "state": asdict(dash_state) if dash_state else None,
                    "plan": [self._serialize_action(a) for a in actions],
                    "optimizer_summary": opt_summary if eff_mode == "ga_nn" else None,
                    "buildings_eta": buildings_eta,
                    "combat": combat,
                    "ranking": ranking,
                    "db_summary": self._db.summary() if self._db is not None else None,
                    "logs": {
                        "commander": self._read_log_tail("commander.log"),
                        "game_api": self._read_log_tail("game_api.log"),
                    },
                }
            )

    def get_state(self, *, include_buildings: bool = True) -> dict[str, Any]:
        with self._lock:
            self._raise_if_rate_limited()
            if self._last_state is None:
                self._active_planet_confirmed = False
                self._last_home_fetched = False
                self._last_home_at = None
            now = time.monotonic()
            if (
                self._last_state is not None
                and (now - self._last_snapshot_at) <= self.SNAPSHOT_CACHE_TTL_S
                and (not include_buildings or self._last_buildings_fetched)
                and (
                    self._is_home_fresh(now)
                    or self._last_state is not self._last_recorded_state_obj
                )
            ):
                cached = self._refresh_cached_economy(self._last_state)
                return _sanitize_finite_json(_redact_api_payload(asdict(self._attach_raid_stats(cached))))

            research_due = self._last_research_sync_at <= 0.0 or (now - self._last_research_sync_at) >= self.RESEARCH_SYNC_TTL_S
            shipyard_due = self._last_shipyard_sync_at <= 0.0 or (now - self._last_shipyard_sync_at) >= self.SHIPYARD_SYNC_TTL_S
            need_research = bool(include_buildings and (self._last_state is None or research_due))
            need_shipyard = bool(include_buildings and (self._last_state is None or shipyard_due))
            if self._last_state is not None and include_buildings:
                prev_active = self._last_state.active_planet()
                cur_pid = _safe_api_int(prev_active.planet_id) if prev_active is not None else 0
                cur_b8 = prev_active.building_level(8) if prev_active is not None else 0
                cur_b9 = prev_active.building_level(9) if prev_active is not None else 0
                cur_res_sig = tuple(
                    sorted((game_data.safe_key(k), _safe_api_int(v)) for k, v in (self._last_state.researches or {}).items())
                )
                cur_res_busy = any(q.category == "research" for q in self._last_state.active_queues)
                if (
                    prev_active is not None
                    and cur_b9 > 0
                    and not prev_active.ship_offers
                    and self._last_shipyard_sync_sig != (cur_pid, cur_b9, cur_res_sig)
                ):
                    need_shipyard = True
                if (
                    cur_b8 > 0
                    and not self._last_state.research_offers
                    and self._last_research_sync_sig != (cur_b8, cur_res_sig, cur_res_busy)
                ):
                    need_research = True

            try:
                with self._client_factory(self.debug_port) as client:
                    state = self._read_snapshot(
                        client,
                        include_buildings=include_buildings,
                        include_research=need_research,
                        include_shipyard=need_shipyard,
                        return_home=False,
                        priority="normal",
                    )
                    if not include_buildings:
                        self._last_buildings_fetched = False
                    obs_active = bool(getattr(client, "last_snapshot_observed_active_planet", True))
                    merged = self._record_state(
                        state,
                        fetched_buildings=include_buildings,
                        fetched_research=need_research,
                        fetched_shipyard=need_shipyard,
                        fetched_home=True,
                        observed_active_planet=obs_active,
                    )
            except RateLimitError as exc:
                self._record_server_rate_limit(exc)
                raise
            return _sanitize_finite_json(_redact_api_payload(asdict(self._attach_raid_stats(merged))))

    def _pick_executor(self, dry_run: bool) -> ActionExecutor:
        return self._dry_executor if dry_run else self._live_executor

    @staticmethod
    def _resolve_planet(state: GameState, planet_id: Optional[int]) -> PlanetState:
        planet = (
            next((p for p in state.planets if p.planet_id == planet_id), None)
            if planet_id is not None
            else state.active_planet()
        )
        if planet is None:
            pid_txt = game_data.fmt_compact_int(planet_id) if planet_id is not None else "None"
            raise ApiRequestError(f"Planet {pid_txt} not found in state.")
        return planet

    def _find_build_action(
        self,
        state: GameState,
        building_id: int,
        planet_id: Optional[int],
        *,
        allow_live_verify: bool = False,
    ) -> PlannedAction:
        from spaceinvasion import game_data
        from spaceinvasion.strategy import _resources_from_dict

        planet = self._resolve_planet(state, planet_id)
        nxt_lvl = planet.building_level(building_id) + 1
        offer = next((o for o in planet.building_offers if o.building_id == building_id), None)
        bid_int = int(building_id)
        ptype_norm = normalize_planet_type(planet)
        planet_type_allowed = is_building_allowed_on_planet_type(
            planet,
            bid_int,
            has_b8_offer=(offer is not None and bid_int == 8),
        )
        if offer is None:
            wiki_cost = _resources_from_dict(game_data.building_cost(building_id, nxt_lvl))
            can_fallback = planet.planet_id != state.active_planet_id or (
                allow_live_verify and not planet.building_offers
            )
            prereqs_met = not game_data.missing_prerequisites(
                "buildings",
                bid_int,
                buildings=planet.buildings,
                researches=state.researches,
            )
            fallback_ok = (
                can_fallback
                and planet_type_allowed
                and prereqs_met
                and not wiki_cost.is_zero()
                and planet.resources.can_afford(wiki_cost)
            )
            if not planet_type_allowed:
                reason = f"API build command (building b{bid_int} not allowed on planet_type={ptype_norm})"
            elif planet.planet_id != state.active_planet_id and fallback_ok:
                reason = "API build command (cross-planet check)"
            elif fallback_ok:
                reason = "API build command (live verify)"
            else:
                reason = "API build command (offer unavailable/not unlocked)"
            return PlannedAction(
                priority="P2",
                kind="BUILD",
                planet_id=planet.planet_id,
                planet_coords=planet.coords,
                target_id=building_id,
                target_name=planet.building_names.get(f"b{building_id}", f"Building {building_id}"),
                target_level=nxt_lvl,
                cost=wiki_cost,
                missing=planet.resources.missing(wiki_cost),
                reason=reason,
                executable_now=fallback_ok,
                action_href=None,
            )
        if offer.target_level != nxt_lvl:
            wiki_cost = _resources_from_dict(game_data.building_cost(building_id, nxt_lvl))
            prereqs_met = not game_data.missing_prerequisites(
                "buildings",
                bid_int,
                buildings=planet.buildings,
                researches=state.researches,
            )
            can_exec = (
                allow_live_verify
                and planet_type_allowed
                and prereqs_met
                and not wiki_cost.is_zero()
                and planet.resources.can_afford(wiki_cost)
            )
            if not planet_type_allowed:
                reason = f"API build command (building b{bid_int} not allowed on planet_type={ptype_norm})"
            elif not prereqs_met:
                reason = "API build command (prerequisites unmet)"
            elif allow_live_verify:
                reason = "API build command (live verify)"
            else:
                reason = "API build command (offer unavailable/not unlocked)"
            return PlannedAction(
                priority="P2",
                kind="BUILD",
                planet_id=planet.planet_id,
                planet_coords=planet.coords,
                target_id=offer.building_id,
                target_name=offer.name,
                target_level=nxt_lvl,
                cost=wiki_cost,
                missing=planet.resources.missing(wiki_cost),
                reason=reason,
                executable_now=can_exec,
                action_href=None,
            )
        can_exec = planet_type_allowed and offer.can_build and planet.resources.can_afford(offer.cost)
        if (
            planet_type_allowed
            and not can_exec
            and allow_live_verify
            and "not enough" in (offer.status_text or "").lower()
            and planet.resources.can_afford(offer.cost)
        ):
            can_exec = True
        reason = (
            "API build command"
            if planet_type_allowed
            else f"API build command (building b{bid_int} not allowed on planet_type={ptype_norm})"
        )
        return PlannedAction(
            priority="P2",
            kind="BUILD",
            planet_id=planet.planet_id,
            planet_coords=planet.coords,
            target_id=offer.building_id,
            target_name=offer.name,
            target_level=offer.target_level,
            cost=offer.cost,
            missing=planet.resources.missing(offer.cost),
            reason=reason,
            executable_now=can_exec,
            action_href=offer.build_href if can_exec else None,
        )

    def _find_research_action(
        self,
        state: GameState,
        research_id: int,
        planet_id: Optional[int],
        *,
        allow_live_verify: bool = False,
    ) -> PlannedAction:
        from spaceinvasion.strategy import _resources_from_dict

        planet = self._resolve_planet(state, planet_id)
        rkey = f"r{research_id}"
        nxt_lvl = _safe_api_int(state.researches.get(rkey, 0)) + 1
        ptype_norm = normalize_planet_type(planet)
        if ptype_norm == "raid":
            return PlannedAction(
                priority="P2",
                kind="RESEARCH",
                planet_id=planet.planet_id,
                planet_coords=planet.coords,
                target_id=research_id,
                target_name=state.research_names.get(rkey, f"Research {research_id}"),
                target_level=nxt_lvl,
                reason=f"API research command (research not permitted on planet_type={ptype_norm})",
                executable_now=False,
                action_href=None,
            )
        res_busy = (
            any(q.category == "research" for q in state.active_queues)
            or any(o.active for o in state.research_offers)
        )
        offer = next((o for o in state.research_offers if o.research_id == research_id), None)
        missing_prereqs = game_data.missing_prerequisites(
            "research",
            rkey,
            buildings=planet.buildings,
            researches=state.researches,
        )
        missing_b_prereqs = [m for m in missing_prereqs if m.get("category") == "buildings"]
        if offer is None:
            if (
                allow_live_verify
                and not res_busy
                and planet.building_level(8) >= 1
                and (
                    (not state.research_offers and not missing_b_prereqs)
                    or (planet.planet_id != state.active_planet_id and not missing_prereqs)
                )
            ):
                unit = game_data.resolve_unit("research", rkey)
                raw_cost = ((unit or {}).get("levels") or {}).get(nxt_lvl, {}).get("cost")
                wiki_cost = (
                    _resources_from_dict(raw_cost)
                    if isinstance(raw_cost, dict)
                    else Resources()
                )
                can_exec = not wiki_cost.is_zero() and planet.resources.can_afford(wiki_cost)
                rname = state.research_names.get(
                    rkey,
                    str((unit or {}).get("name") or f"Research {research_id}"),
                )
                return PlannedAction(
                    priority="P2",
                    kind="RESEARCH",
                    planet_id=planet.planet_id,
                    planet_coords=planet.coords,
                    target_id=research_id,
                    target_name=rname,
                    target_level=nxt_lvl,
                    cost=wiki_cost,
                    missing=planet.resources.missing(wiki_cost),
                    reason=(
                        "API research command (live verify)"
                        if can_exec
                        else "API research command (insufficient resources)"
                    ),
                    executable_now=can_exec,
                )
            return PlannedAction(
                priority="P2",
                kind="RESEARCH",
                planet_id=planet.planet_id,
                planet_coords=planet.coords,
                target_id=research_id,
                target_name=state.research_names.get(rkey, f"Research {research_id}"),
                target_level=nxt_lvl,
                reason="API research command (offer unavailable/slot busy)",
                executable_now=False,
            )
        if (
            allow_live_verify
            and offer.target_level != nxt_lvl
            and not offer.active
            and not res_busy
            and planet.building_level(8) >= 1
            and not missing_prereqs
        ):
            unit = game_data.resolve_unit("research", rkey)
            raw_cost = ((unit or {}).get("levels") or {}).get(nxt_lvl, {}).get("cost")
            wiki_cost = (
                _resources_from_dict(raw_cost)
                if isinstance(raw_cost, dict)
                else offer.cost
            )
            can_exec = not wiki_cost.is_zero() and planet.resources.can_afford(wiki_cost)
            return PlannedAction(
                priority="P2",
                kind="RESEARCH",
                planet_id=planet.planet_id,
                planet_coords=planet.coords,
                target_id=offer.research_id,
                target_name=offer.name,
                target_level=nxt_lvl,
                cost=wiki_cost,
                missing=planet.resources.missing(wiki_cost),
                reason="API research command (live verify)",
                executable_now=can_exec,
            )
        can_exec = (
            offer.can_research
            and not res_busy
            and planet.building_level(8) >= 1
            and not missing_prereqs
            and planet.resources.can_afford(offer.cost)
        )
        if (
            not can_exec
            and allow_live_verify
            and not offer.active
            and not res_busy
            and planet.building_level(8) >= 1
            and not missing_prereqs
        ):
            if (
                "not enough" in (offer.status_text or "").lower()
                and planet.resources.can_afford(offer.cost)
            ):
                can_exec = True
            elif planet.planet_id != state.active_planet_id and not missing_prereqs:
                unit = game_data.resolve_unit("research", rkey)
                raw_cost = ((unit or {}).get("levels") or {}).get(nxt_lvl, {}).get("cost")
                wiki_cost = (
                    _resources_from_dict(raw_cost)
                    if isinstance(raw_cost, dict)
                    else Resources()
                )
                live_cost = offer.cost if not offer.cost.is_zero() else wiki_cost
                live_ok = not live_cost.is_zero() and planet.resources.can_afford(live_cost)
                if live_ok:
                    return PlannedAction(
                        priority="P2",
                        kind="RESEARCH",
                        planet_id=planet.planet_id,
                        planet_coords=planet.coords,
                        target_id=offer.research_id,
                        target_name=offer.name,
                        target_level=nxt_lvl,
                        cost=live_cost,
                        missing=planet.resources.missing(live_cost),
                        reason="API research command (cross-planet check)",
                        executable_now=True,
                    )
        return PlannedAction(
            priority="P2",
            kind="RESEARCH",
            planet_id=planet.planet_id,
            planet_coords=planet.coords,
            target_id=offer.research_id,
            target_name=offer.name,
            target_level=offer.target_level,
            cost=offer.cost,
            missing=planet.resources.missing(offer.cost),
            reason="API research command",
            executable_now=can_exec,
            action_href=offer.research_href if can_exec else None,
        )

    def _find_ship_action(
        self,
        state: GameState,
        ship_id: int,
        quantity: int,
        planet_id: Optional[int],
        *,
        allow_live_verify: bool = False,
    ) -> PlannedAction:
        from spaceinvasion import game_data
        from spaceinvasion.strategy import _resources_from_dict

        planet = self._resolve_planet(state, planet_id)
        qty = max(1, quantity)
        slug = SHIP_SLUG_BY_KEY.get(f"s{ship_id}")
        ptype_norm = normalize_planet_type(planet)
        if ptype_norm == "raid" or slug is None:
            unit = game_data.resolve_unit("ships", slug) if slug else None
            return PlannedAction(
                priority="P2",
                kind="BUILD_SHIP",
                planet_id=planet.planet_id,
                planet_coords=planet.coords,
                target_id=ship_id,
                target_name=str((unit or {}).get("name") or f"Ship {ship_id}"),
                target_level=qty,
                reason=(
                    f"API build_ship command (shipyard not permitted on planet_type={ptype_norm})"
                    if ptype_norm == "raid"
                    else f"API build_ship command (unknown ship_id={ship_id})"
                ),
                executable_now=False,
                action_href=None,
            )
        missing_prereqs = (
            game_data.missing_prerequisites(
                "ships",
                slug,
                buildings=planet.buildings,
                researches=state.researches,
            )
            if slug
            else []
        )
        offer = next((o for o in planet.ship_offers if o.unit_id == ship_id), None)
        if offer is None:
            unit = game_data.resolve_unit("ships", slug) if slug else None
            raw_cost = (unit or {}).get("base_cost")
            can_fallback = planet.planet_id != state.active_planet_id or (
                allow_live_verify and not planet.ship_offers
            )
            if (
                isinstance(raw_cost, dict)
                and can_fallback
                and planet.building_level(9) >= 1
                and not missing_prereqs
            ):
                unit_cost = _resources_from_dict(raw_cost)
                total_cost = Resources(
                    pig_iron=unit_cost.pig_iron * qty,
                    metal=unit_cost.metal * qty,
                    kryptonite=unit_cost.kryptonite * qty,
                    spice=unit_cost.spice * qty,
                )
                can_exec = not unit_cost.is_zero() and planet.resources.can_afford(total_cost)
                sname = str((unit or {}).get("name") or f"Ship {ship_id}")
                return PlannedAction(
                    priority="P2",
                    kind="BUILD_SHIP",
                    planet_id=planet.planet_id,
                    planet_coords=planet.coords,
                    target_id=ship_id,
                    target_name=sname,
                    target_level=qty,
                    cost=unit_cost,
                    missing=planet.resources.missing(total_cost),
                    reason=(
                        "API build_ship command (live verify)"
                        if can_exec
                        else "API build_ship command (insufficient resources)"
                    ),
                    executable_now=can_exec,
                )
            return PlannedAction(
                priority="P2",
                kind="BUILD_SHIP",
                planet_id=planet.planet_id,
                planet_coords=planet.coords,
                target_id=ship_id,
                target_name=str((unit or {}).get("name") or f"Ship {ship_id}"),
                target_level=qty,
                reason="API build_ship command (offer unavailable/not unlocked)",
                executable_now=False,
            )
        total_offer_cost = Resources(
            pig_iron=offer.cost.pig_iron * qty,
            metal=offer.cost.metal * qty,
            kryptonite=offer.cost.kryptonite * qty,
            spice=offer.cost.spice * qty,
        )
        can_exec = (
            offer.can_build
            and not missing_prereqs
            and planet.resources.can_afford(total_offer_cost)
        )
        status_l = (offer.status_text or "").lower()
        if (
            not offer.can_build
            and allow_live_verify
            and planet.building_level(9) >= 1
            and not missing_prereqs
            and ("not enough" in status_l or "insufficient" in status_l)
            and planet.resources.can_afford(total_offer_cost)
        ):
            can_exec = True
        if not offer.can_build and not can_exec:
            return PlannedAction(
                priority="P2",
                kind="BUILD_SHIP",
                planet_id=planet.planet_id,
                planet_coords=planet.coords,
                target_id=offer.unit_id,
                target_name=offer.name,
                target_level=qty,
                cost=offer.cost,
                missing=planet.resources.missing(total_offer_cost),
                reason="API build_ship command (offer unavailable/not unlocked)",
                executable_now=False,
            )
        return PlannedAction(
            priority="P2",
            kind="BUILD_SHIP",
            planet_id=planet.planet_id,
            planet_coords=planet.coords,
            target_id=offer.unit_id,
            target_name=offer.name,
            target_level=qty,
            cost=offer.cost,
            missing=planet.resources.missing(total_offer_cost),
            reason="API build_ship command",
            executable_now=can_exec,
        )

    def _persist_galaxy_scan(
        self,
        galaxy: int,
        system: int,
        slots: list[Any] | tuple[Any, ...] = (),
    ) -> None:
        now_ts = round(time.time(), 3)
        skey = f"{int(galaxy):02d}:{int(system):03d}"
        self._scanned_systems[skey] = now_ts
        if self._db is not None:
            self._db.record_galaxy_scan(int(galaxy), int(system), slots, ts=now_ts)

    def _persist_galaxy_slots(self, slots: list[Any] | tuple[Any, ...]) -> int:
        if not slots:
            return 0
        if self._db is not None:
            saved = self._db.record_galaxy_slots(slots)
            self._raid_targets = self._enrich_raid_targets(self._db.get_spy_targets(limit=100))
            return saved
        by_coords: dict[str, dict[str, Any]] = {
            normalize_coords(r.get("coords") or r.get("target_coords") or ""): dict(r)
            for r in self._raid_targets
            if (r.get("coords") or r.get("target_coords"))
        }
        now_ts = round(time.time(), 3)
        saved = 0
        for s in slots:
            if getattr(s, "status_color", "free") == "free" and not getattr(s, "is_spacia", False):
                continue
            status_color = _bounded_api_str(getattr(s, "status_color", "yellow") or "yellow", default="yellow")
            is_spacia = bool(getattr(s, "is_spacia", False))
            norm_sc = normalize_coords(s.coords)
            prev = by_coords.get(norm_sc, {})
            by_coords[norm_sc] = {
                "coords": norm_sc,
                "owner": _bounded_api_str(getattr(s, "owner", "") or "", default=""),
                "planet_name": _bounded_api_str(getattr(s, "name", "") or "", default=""),
                "status_color": status_color,
                "is_inactive": bool(status_color == "yellow" or is_spacia),
                "is_spacia": is_spacia,
                "points": prev.get("points"),
                "resources": prev.get("resources") or {"pig_iron": 0, "metal": 0, "kryptonite": 0, "spice": 0},
                "fleet": prev.get("fleet") or {},
                "defense": prev.get("defense") or {},
                "dug_in": prev.get("dug_in", False),
                "last_scanned_ts": now_ts,
                "last_spied_ts": prev.get("last_spied_ts", 0.0),
                "has_spy_intel": bool(prev.get("has_spy_intel", False)),
            }
            saved += 1
        self._raid_targets = self._enrich_raid_targets(list(by_coords.values())[:100])
        return saved

    def _persist_spy_reports(self, reports: tuple[SpyReport, ...] | list[SpyReport]) -> int:
        if not reports:
            return 0
        now_ts = round(time.time(), 3)
        if self._db is not None:
            for rep in reports:
                self._db.record_spy_report(rep, ts=now_ts)
            self._raid_targets = self._enrich_raid_targets(self._db.get_spy_targets(limit=100))
            return len(reports)
        by_coords: dict[str, dict[str, Any]] = {
            normalize_coords(r.get("coords") or r.get("target_coords") or ""): dict(r)
            for r in self._raid_targets
            if (r.get("coords") or r.get("target_coords"))
        }
        for rep in reports:
            norm_tc = normalize_coords(rep.target_coords)
            prev = by_coords.get(norm_tc, {})
            is_spacia = bool(rep.is_spacia or prev.get("is_spacia", False))
            status_color = prev.get("status_color") or ("yellow" if is_spacia else "light_red")
            by_coords[norm_tc] = {
                "coords": norm_tc,
                "owner": rep.owner or prev.get("owner", ""),
                "planet_name": prev.get("planet_name", ""),
                "status_color": status_color,
                "is_inactive": bool(prev.get("is_inactive", False) or is_spacia or status_color == "yellow"),
                "is_spacia": is_spacia,
                "points": prev.get("points"),
                "resources": asdict(rep.resources),
                "fleet": dict(rep.fleet),
                "defense": dict(rep.defense),
                "dug_in": bool(rep.dug_in),
                "last_scanned_ts": max(float(prev.get("last_scanned_ts") or 0.0), now_ts),
                "last_spied_ts": now_ts,
                "has_spy_intel": True,
            }
        self._raid_targets = self._enrich_raid_targets(list(by_coords.values())[:100])
        return len(reports)

    def _sync_spio_if_unread(
        self,
        client: Any,
        state: GameState,
        *,
        priority: str = "normal",
        get_client: Optional[Callable[[], Any]] = None,
        ignore_auth_error: bool = False,
    ) -> int:
        if _safe_api_int((state.unread_messages or {}).get("spio", 0)) <= 0:
            return 0
        if self.rate_limit_remaining() > 0.0:
            return 0
        now = time.monotonic()
        if (now - self._last_spio_sync_at) < self.SPIO_SYNC_TTL_S:
            return 0
        if client is None and get_client is not None:
            try:
                client = get_client()
            except RateLimitError:
                raise
            except SpaceInvasionAuthError:
                if ignore_auth_error:
                    return 0
                raise
        open_msg = getattr(client, "open_messages", None)
        if not callable(open_msg):
            return 0
        self._last_spio_sync_at = now
        try:
            html = self._call_with_optional_priority(open_msg, "spio", priority=priority)
        except RateLimitError:
            raise
        except Exception:
            return 0
        if not isinstance(html, str):
            return 0
        reports = parse_spy_messages_html(html)
        return self._persist_spy_reports(reports)

    def _execute_and_summarize(
        self,
        client: Any,
        state: GameState,
        cmd: str,
        planned: PlannedAction,
        dry_run: bool,
        *,
        return_home: bool = False,
    ) -> dict[str, Any]:
        planned = attach_action_etas(state, [planned])[0]
        executor = self._pick_executor(dry_run)
        res = None
        try:
            if GameApiBridge._supports_kwarg(executor.execute_action, "active_planet_confirmed"):
                res = executor.execute_action(
                    client,
                    state,
                    planned,
                    active_planet_confirmed=self._active_planet_confirmed,
                )
            else:
                res = executor.execute_action(client, state, planned)
        finally:
            if res is None and not dry_run:
                raw_obs_pid = getattr(executor, "last_observed_active_planet_id", None)
                raw_obs_conf = getattr(executor, "last_observed_active_planet_confirmed", None)
                self._sync_observed_active_planet_state(
                    state,
                    obs_pid=(
                        raw_obs_pid
                        if isinstance(raw_obs_pid, int) and not isinstance(raw_obs_pid, bool)
                        else None
                    ),
                    obs_confirmed=(
                        raw_obs_conf if isinstance(raw_obs_conf, bool) else None
                    ),
                )
        if not dry_run:
            for pid, rates in getattr(executor, "last_economy_rates", {}).items():
                if rates:
                    self._economy_rates[int(pid)] = dict(rates)
                    if self._db is not None:
                        coords = next((p.coords for p in state.planets if p.planet_id == int(pid)), "")
                        self._db.save_economy_rates(int(pid), coords, rates)
            gal_slots = getattr(executor, "last_galaxy_slots", None)
            if gal_slots:
                self._persist_galaxy_slots(gal_slots)
                executor.last_galaxy_slots = []
            if res.executed and res.action.kind == "SCAN_GALAXY":
                m_sys = re.search(
                    r"(\d{1,2}):(\d{1,3})",
                    f"{_bounded_api_str(res.message, default='')} {_bounded_api_str(res.action.target_name, default='')}",
                    re.ASCII,
                )
                if m_sys:
                    self._persist_galaxy_scan(
                        int(m_sys.group(1)),
                        int(m_sys.group(2)),
                        gal_slots or (),
                    )
            if res.executed and res.action.kind == "THROTTLE_BUILDING":
                pid_i = game_data.cap_json_int(_safe_api_int(res.action.planet_id, 0))
                self._throttle_timestamps[pid_i] = time.time()
            if res.executed and res.action.kind == "ATTACK" and self._db is not None:
                tname_s = (
                    game_data.safe_key(res.action.target_name)
                    if res.action.target_name is not None
                    else ""
                )
                reason_s = (
                    game_data.safe_key(res.action.reason)
                    if res.action.reason is not None
                    else ""
                )
                m_coords = re.search(
                    r"(\d{1,2}:\d{1,3}:\d{1,2})",
                    tname_s,
                    re.ASCII,
                )
                raw_t_coords = m_coords.group(1) if m_coords else tname_s
                t_coords = normalize_coords(raw_t_coords)
                reason_l = f"{tname_s} {reason_s}".lower()
                target_row = next(
                    (
                        r
                        for r in (self._raid_targets or ())
                        if normalize_coords(r.get("coords") or r.get("target_coords") or "") == t_coords
                    ),
                    None,
                )
                if target_row is None:
                    get_by_coords = getattr(self._db, "get_spy_target_by_coords", None)
                    if callable(get_by_coords):
                        target_row = get_by_coords(t_coords)
                if target_row is None:
                    target_row = next(
                        (
                            r
                            for r in self._db.get_spy_targets(limit=200)
                            if normalize_coords(r.get("coords") or r.get("target_coords") or "") == t_coords
                        ),
                        None,
                    )
                row_exempt = bool(
                    target_row is not None
                    and (
                        target_row.get("is_spacia")
                        or target_row.get("is_inactive")
                        or game_data.safe_key(target_row.get("status_color") or "").lower() == "yellow"
                    )
                )
                is_exempt = row_exempt or (
                    "spacia" in reason_l or "inactive" in reason_l or "yellow" in reason_l
                )
                self._db.record_raid(t_coords, is_exempt=is_exempt, outcome=res.message)
                self._raid_targets = self._enrich_raid_targets(self._db.get_spy_targets(limit=100))
        if self._db is not None:
            self._db.record_action(
                source="api",
                command=cmd,
                action=res.action,
                executed=res.executed,
                dry_run=res.dry_run,
                message=res.message,
            )

        after_state: Optional[GameState] = None
        res_updated = getattr(res, "updated_state", None)
        res_obs_pid = getattr(res, "observed_active_planet_id", None)
        res_obs_conf = getattr(res, "observed_active_planet_confirmed", None)
        valid_obs_pid = (
            res_obs_pid
            if isinstance(res_obs_pid, int) and not isinstance(res_obs_pid, bool)
            else None
        )
        obs_conf_bool = res_obs_conf if isinstance(res_obs_conf, bool) else None
        if res.executed and res.action.kind == "SCAN_GALAXY":
            after_state = self._record_state(
                state,
                fetched_buildings=self._last_buildings_fetched,
                fetched_home=False,
                observed_active_planet=False,
                update_snapshot_ts=False,
            )
            after_plan = self._plan_with_context(after_state)
        elif res.executed and isinstance(res_updated, GameState):
            if res.action.kind == "BUILD" and res.action.target_id == 9:
                self._last_shipyard_sync_at = 0.0
                self._last_shipyard_sync_sig = None
            elif res.action.kind == "BUILD" and res.action.target_id == 8:
                self._last_research_sync_at = 0.0
                self._last_research_sync_sig = None
                res_updated = replace(res_updated, research_offers=())
            state_to_record = res_updated
            if (
                obs_conf_bool is True
                and valid_obs_pid is not None
                and any(p.planet_id == valid_obs_pid for p in res_updated.planets)
                and res_updated.active_planet_id != valid_obs_pid
            ):
                state_to_record = replace(res_updated, active_planet_id=valid_obs_pid)
            if res.action.kind != "RESEARCH" and (
                state_to_record.active_planet_id != state.active_planet_id
                or {p.planet_id: p.building_level(8) for p in state_to_record.planets}
                != {p.planet_id: p.building_level(8) for p in state.planets}
            ):
                state_to_record = replace(state_to_record, research_offers=())
            after_state = self._record_state(
                state_to_record,
                fetched_buildings=(res.action.kind == "BUILD"),
                fetched_research=(res.action.kind == "RESEARCH"),
                fetched_shipyard=(res.action.kind == "BUILD_SHIP"),
                fetched_home=(res.action.kind == "CLAIM_TUTORIAL"),
                observed_active_planet=(obs_conf_bool is True),
            )
            if obs_conf_bool is False and len(after_state.planets) > 1:
                self._active_planet_confirmed = False
            after_plan = self._plan_with_context(after_state)
        elif res.executed and res.action.kind == "SPY":
            state_to_record = state
            obs_flag = False
            if obs_conf_bool is True:
                obs_flag = True
                if (
                    valid_obs_pid is not None
                    and any(p.planet_id == valid_obs_pid for p in state.planets)
                    and state.active_planet_id != valid_obs_pid
                ):
                    state_to_record = replace(
                        state,
                        active_planet_id=valid_obs_pid,
                        research_offers=(),
                    )
            after_state = self._record_state(
                state_to_record,
                fetched_buildings=(
                    self._last_buildings_fetched
                    and state_to_record.active_planet_id == state.active_planet_id
                ),
                fetched_home=False,
                observed_active_planet=obs_flag,
                update_snapshot_ts=False,
            )
            if obs_conf_bool is False and len(after_state.planets) > 1:
                self._active_planet_confirmed = False
            after_plan = self._plan_with_context(after_state)
        elif res.executed:
            self._last_snapshot_at = 0.0
            if res.action.kind == "BUILD" and res.action.target_id == 9:
                self._last_shipyard_sync_at = 0.0
                self._last_shipyard_sync_sig = None
            elif res.action.kind == "BUILD" and res.action.target_id == 8:
                self._last_research_sync_at = 0.0
                self._last_research_sync_sig = None
            inc_b = res.action.kind in ("BUILD", "CLAIM_TUTORIAL")
            inc_r = res.action.kind == "RESEARCH"
            inc_h = res.action.kind == "CLAIM_TUTORIAL"
            snap = self._read_snapshot(
                client,
                include_buildings=inc_b,
                include_research=inc_r,
                include_shipyard=False,
                include_home=inc_h,
                return_home=False,
                priority="interactive",
            )
            obs_active = bool(getattr(client, "last_snapshot_observed_active_planet", True))
            if obs_conf_bool is True:
                obs_active = True
                if (
                    valid_obs_pid is not None
                    and any(p.planet_id == valid_obs_pid for p in snap.planets)
                    and snap.active_planet_id != valid_obs_pid
                ):
                    snap = replace(snap, active_planet_id=valid_obs_pid)
            if (res.action.kind == "BUILD" and res.action.target_id == 8) or (
                not inc_r
                and (
                    snap.active_planet_id != state.active_planet_id
                    or {p.planet_id: p.building_level(8) for p in snap.planets}
                    != {p.planet_id: p.building_level(8) for p in state.planets}
                )
            ):
                snap = replace(snap, research_offers=())
            after_state = self._record_state(
                snap,
                fetched_buildings=inc_b,
                fetched_research=inc_r,
                fetched_home=inc_h,
                observed_active_planet=obs_active,
            )
            if obs_conf_bool is False and len(after_state.planets) > 1:
                self._active_planet_confirmed = False
            after_plan = self._plan_with_context(after_state)
        else:
            state_to_record = state
            obs_flag = False
            if not dry_run and obs_conf_bool is True:
                obs_flag = True
                if (
                    valid_obs_pid is not None
                    and any(p.planet_id == valid_obs_pid for p in state.planets)
                    and state.active_planet_id != valid_obs_pid
                ):
                    state_to_record = replace(
                        state,
                        active_planet_id=valid_obs_pid,
                        research_offers=(),
                    )
            merged_state = self._record_state(
                state_to_record,
                fetched_buildings=(
                    self._last_buildings_fetched
                    and state_to_record.active_planet_id == state.active_planet_id
                ),
                fetched_home=False,
                observed_active_planet=obs_flag,
                update_snapshot_ts=False,
            )
            if not dry_run and obs_conf_bool is False and len(merged_state.planets) > 1:
                self._active_planet_confirmed = False
            after_plan = self._plan_with_context(merged_state)

        if (
            return_home
            and res.executed
            and not (res.action.kind == "CLAIM_TUTORIAL" and isinstance(res_updated, GameState))
        ):
            self._call_with_optional_priority(
                client.navigate_menu,
                "internalHome",
                priority="interactive",
            )

        next_action = self._serialize_action(after_plan[0]) if after_plan else None
        summary: dict[str, Any] = {
            "ok": True,
            "command": cmd,
            "executed": res.executed,
            "dry_run": res.dry_run,
            "message": _redact_api_payload(res.message),
            "action": self._serialize_action(res.action),
            "next_action": next_action,
        }
        if after_state is not None:
            summary["state"] = _sanitize_finite_json(_redact_api_payload(asdict(self._attach_raid_stats(after_state))))
        return _sanitize_finite_json(summary)

    def handle_command(self, payload: dict[str, Any]) -> dict[str, Any]:
        if not isinstance(payload, Mapping) or not payload.get("command"):
            raise ApiRequestError("Request JSON must include a non-empty 'command' field.")

        raw_cmd = payload["command"]
        if not isinstance(raw_cmd, str):
            raise ApiRequestError("Request JSON 'command' field must be a non-empty string.")
        cmd = raw_cmd.strip().lower()
        if not cmd:
            raise ApiRequestError("Request JSON must include a non-empty 'command' field.")
        dry_run = bool(payload.get("dry_run", False))

        if cmd == "config":
            with self._lock:
                return _sanitize_finite_json(
                    {
                        "ok": True,
                        "command": cmd,
                        "config": _redact_api_payload(asdict(self.strategy_config)),
                    }
                )

        if cmd == "db_summary":
            with self._lock:
                return _sanitize_finite_json(
                    {
                        "ok": True,
                        "command": cmd,
                        "db": _redact_api_payload(self._db.summary()) if self._db is not None else None,
                    }
                )

        if cmd == "bot_control":
            with self._lock:
                if "paused" in payload:
                    self._bot_control["paused"] = bool(payload["paused"])
                if "mode_override" in payload:
                    raw_mode = payload["mode_override"]
                    if raw_mode not in (None, "live", "dry-run"):
                        raise ApiRequestError("mode_override must be null, 'live', or 'dry-run'.")
                    self._bot_control["mode_override"] = raw_mode
                return _sanitize_finite_json(
                    _redact_api_payload(
                        {"ok": True, "command": cmd, "bot_control": dict(self._bot_control)}
                    )
                )

        if cmd == "update_config":
            raw_cfg = payload.get("config")
            if not isinstance(raw_cfg, Mapping):
                raise ApiRequestError("'update_config' requires a 'config' dictionary.")
            _validate_config_patch(raw_cfg)
            with self._lock:
                prev_cfg = self.strategy_config
                merged = {**asdict(prev_cfg), **raw_cfg}
                new_cfg = strategy_config_from_dict(merged)
                save_strategy_config(new_cfg, self.config_path)
                self.strategy_config = new_cfg
                ga_params_changed = any(
                    getattr(new_cfg, k) != getattr(prev_cfg, k)
                    for k in (
                        "ga_population_size",
                        "ga_generations",
                        "ga_horizon_steps",
                        "ga_mutation_rate",
                        "ga_nn_weight",
                        "ga_seed",
                    )
                )
                if normalize_planner_mode(new_cfg.planner_mode) != "ga_nn" or ga_params_changed:
                    self._last_optimizer_summary = None
                for ex in (self._live_executor, self._dry_executor):
                    ex.allow_fleetsave = new_cfg.allow_fleetsave
                    ex.allow_raids = new_cfg.allow_raids
                    ex.max_actions_per_hour = _safe_hourly_budget(new_cfg.max_actions_per_hour, 60)
                return _sanitize_finite_json(
                    _redact_api_payload(
                        {
                            "ok": True,
                            "command": cmd,
                            "config": asdict(new_cfg),
                        }
                    )
                )

        if cmd == "simulate_combat":
            with self._lock:
                state_for_sim = self._last_state or GameState(
                    universe_id=0,
                    user_id=0,
                    nickname="simulator",
                    timestamp=0.0,
                    planets=(),
                )
                atk_ships = _parse_sim_count_map("attacker_ships", payload.get("attacker_ships"))
                def_ships = _parse_sim_count_map("defender_ships", payload.get("defender_ships"))
                def_defense = _parse_sim_count_map("defender_defense", payload.get("defender_defense"))
                def_researches = _parse_sim_count_map("defender_researches", payload.get("defender_researches"))
                tgt_resources = _parse_sim_count_map(
                    "target_resources",
                    payload.get("target_resources"),
                    max_val=1_000_000_000_000,
                )
                raw_coords = payload.get("target_coords")
                if raw_coords is None:
                    tgt_coords = "01:001:01"
                elif not isinstance(raw_coords, str):
                    raise ApiRequestError("'simulate_combat' field 'target_coords' must be a string.")
                else:
                    tgt_coords = raw_coords.strip() or "01:001:01"
                if "safety_margin" in payload:
                    margin = _validate_strict_float(
                        "safety_margin",
                        payload["safety_margin"],
                        min_val=1.0,
                        max_val=1000.0,
                    )
                else:
                    margin = float(self.strategy_config.combat_safety_margin)
                sim = simulate_combat_matchup(
                    state_for_sim,
                    attacker_ships=atk_ships,
                    defender_ships=def_ships,
                    defender_defense=def_defense,
                    defender_researches=def_researches,
                    target_resources=tgt_resources,
                    target_coords=tgt_coords,
                    safety_margin=margin,
                )
            return _sanitize_finite_json(
                {
                    "ok": True,
                    "command": cmd,
                    "simulation": sim,
                }
            )

        with self._lock:
            if cmd == "ensure_session":
                self._raise_if_rate_limited()
                try:
                    self._ensure_session_fn()
                except RateLimitError as exc:
                    self._record_server_rate_limit(exc)
                    raise
                return _sanitize_finite_json({"ok": True, "command": cmd, "message": "Session verified."})

            if cmd == "buildings_eta" and not bool(payload.get("refresh", False)) and self._last_state is not None:
                pid = _parse_command_int(
                    "planet_id",
                    payload.get("planet_id"),
                    allow_none=True,
                    min_val=1,
                    max_val=2**63 - 1,
                )
                planet = self._resolve_planet(self._last_state, pid) if pid is not None else self._last_state.active_planet()
                return _sanitize_finite_json(
                    {
                        "ok": True,
                        "command": cmd,
                        "buildings_eta": _redact_api_payload(build_buildings_eta(self._last_state, planet)),
                    }
                )

            if cmd == "combat_overview" and not bool(payload.get("refresh", False)) and self._last_state is not None:
                pid = _parse_command_int(
                    "planet_id",
                    payload.get("planet_id"),
                    allow_none=True,
                    min_val=1,
                    max_val=2**63 - 1,
                )
                planet = self._resolve_planet(self._last_state, pid) if pid is not None else self._last_state.active_planet()
                return _sanitize_finite_json(
                    {
                        "ok": True,
                        "command": cmd,
                        "combat": _redact_api_payload(
                            build_combat_overview(
                                self._last_state,
                                planet,
                                safety_margin=self.strategy_config.combat_safety_margin,
                            )
                        ),
                    }
                )

            self._raise_if_rate_limited()
            if self._last_state is None:
                self._active_planet_confirmed = False
                self._last_home_fetched = False
                self._last_home_at = None
            now_mono = time.monotonic()
            force_refresh = bool(payload.get("refresh", False))
            try:
                with contextlib.ExitStack() as stack:
                    _client_state: list[Any] = [None]

                    def _get_client() -> Any:
                        if _client_state[0] is None:
                            _client_state[0] = stack.enter_context(
                                self._client_factory(self.debug_port)
                            )
                        return _client_state[0]

                    def _exec_client(planned: PlannedAction) -> Any:
                        if dry_run or not planned.executable_now:
                            return None
                        if planned.kind == "ATTACK" and not self._live_executor.allow_raids:
                            return None
                        return _get_client()

                    def _cached_or_fetch(
                        *,
                        include_buildings: bool,
                        include_research: bool,
                        include_shipyard: bool,
                        include_home: bool = True,
                        priority: str = "interactive",
                    ) -> GameState:
                        if (
                            not force_refresh
                            and self._last_state is not None
                            and (now_mono - self._last_snapshot_at) <= 15.0
                            and (not include_buildings or self._last_buildings_fetched)
                            and (not include_research or bool(self._last_state.research_offers))
                            and (
                                not include_shipyard
                                or bool(
                                    self._last_state.active_planet()
                                    and self._last_state.active_planet().ship_offers
                                )
                            )
                            and (
                                not include_home
                                or self._is_home_fresh(now_mono)
                            )
                        ):
                            return self._refresh_cached_economy(self._last_state)
                        live_client = _get_client()
                        snap = self._read_snapshot(
                            live_client,
                            include_buildings=include_buildings,
                            include_research=include_research,
                            include_shipyard=include_shipyard,
                            include_home=include_home,
                            return_home=False,
                            priority=priority,
                        )
                        obs_active = bool(
                            getattr(live_client, "last_snapshot_observed_active_planet", True)
                        )
                        return self._record_state(
                            snap,
                            fetched_buildings=include_buildings,
                            fetched_research=include_research,
                            fetched_shipyard=include_shipyard,
                            fetched_home=include_home,
                            observed_active_planet=obs_active,
                        )

                    def _resolve_fleet_command_state(
                        target_pid: Optional[int],
                        *,
                        is_spy: bool,
                    ) -> GameState:
                        if dry_run or force_refresh:
                            return _cached_or_fetch(
                                include_buildings=False,
                                include_research=False,
                                include_shipyard=False,
                                include_home=False,
                                priority="interactive",
                            )
                        if self._last_state is not None:
                            is_warm = (now_mono - self._last_snapshot_at) <= 15.0
                            is_cross_planet = (
                                target_pid is not None
                                and target_pid != self._last_state.active_planet_id
                            )
                            unconfirmed_multi = (
                                not self._active_planet_confirmed
                                and len(self._last_state.planets) > 1
                            )
                            if (
                                is_warm
                                or is_cross_planet
                                or unconfirmed_multi
                                or (is_spy and target_pid is None)
                            ):
                                return self._refresh_cached_economy(self._last_state)
                            return _cached_or_fetch(
                                include_buildings=False,
                                include_research=False,
                                include_shipyard=False,
                                include_home=False,
                                priority="interactive",
                            )

                        live_client = _get_client()
                        self._call_with_optional_priority(
                            live_client.ensure_in_game,
                            priority="interactive",
                        )
                        read_ov = getattr(live_client, "read_planets_overview", None)
                        ov: Any = None
                        if callable(read_ov):
                            try:
                                ov = self._call_with_optional_priority(
                                    read_ov,
                                    refresh=False,
                                    priority="interactive",
                                )
                            except TypeError:
                                ov = None
                        if not isinstance(ov, GameState):
                            return _cached_or_fetch(
                                include_buildings=False,
                                include_research=False,
                                include_shipyard=False,
                                include_home=False,
                                priority="interactive",
                            )

                        unread = dict(ov.unread_messages)
                        account_points = ov.account_points
                        account_rank = ov.account_rank
                        read_menu_fn = getattr(live_client, "read_menu_status", None)
                        if callable(read_menu_fn):
                            try:
                                menu_res = read_menu_fn()
                                if (
                                    isinstance(menu_res, tuple)
                                    and len(menu_res) == 3
                                    and isinstance(menu_res[0], dict)
                                ):
                                    unread = dict(menu_res[0])
                                    account_points = menu_res[1]
                                    account_rank = menu_res[2]
                            except Exception:
                                pass

                        planet_ids = {p.planet_id for p in ov.planets}
                        active_pid: Optional[int] = None
                        obs_active = False
                        fetched_home = False
                        tutorial = ov.tutorial
                        speedup = ov.speedup_factor
                        newbie = ov.newbie_protection
                        queues = ov.active_queues
                        alerts = ov.fleet_alerts

                        drv = getattr(live_client, "driver", None)
                        switch_fn = getattr(live_client, "_switch_frame", None)
                        if drv is not None and callable(switch_fn):
                            drv_lock = getattr(live_client, "_driver_lock", None)
                            try:
                                with (drv_lock if drv_lock is not None else contextlib.nullcontext()):
                                    switch_fn("F_Hauptframe")
                                    cur_url = str(drv.execute_script("return location.href;") or "")
                                    cur_html = str(getattr(drv, "page_source", "") or "")
                                    try:
                                        drv.switch_to.default_content()
                                    except Exception:
                                        pass
                                if "action=internalHome" in cur_url and cur_html:
                                    low_h = cur_html.lower()
                                    if (
                                        "made more than" not in low_h
                                        and "bitmeup-login" not in low_h
                                        and "sip-tab-universe" not in low_h
                                    ):
                                        home_pid, h_spd, h_newbie, h_q, h_al = parse_home_metadata(
                                            cur_html
                                        )
                                        if home_pid is not None and home_pid in planet_ids:
                                            active_pid = home_pid
                                            obs_active = True
                                        tutorial = parse_tutorial_from_home(cur_html)
                                        speedup = h_spd
                                        newbie = h_newbie
                                        queues = h_q
                                        alerts = h_al
                                        fetched_home = True
                                elif cur_html:
                                    sel_pid = GameBrowserClient._extract_selected_planet_id(
                                        cur_html,
                                        cur_url,
                                    )
                                    if sel_pid is not None and sel_pid in planet_ids:
                                        active_pid = sel_pid
                                        obs_active = True
                            except Exception:
                                pass

                        ov = replace(
                            ov,
                            active_planet_id=active_pid or ov.active_planet_id,
                            unread_messages=unread,
                            tutorial=tutorial,
                            active_queues=queues,
                            fleet_alerts=alerts,
                            speedup_factor=speedup,
                            newbie_protection=newbie,
                            account_points=account_points,
                            account_rank=account_rank,
                        )
                        return self._record_state(
                            ov,
                            fetched_home=fetched_home,
                            observed_active_planet=obs_active,
                        )

                    if cmd == "plan":
                        override_cfg: Optional[StrategyConfig] = None
                        if "planner_mode" in payload and payload["planner_mode"] is not None:
                            raw_mode = payload["planner_mode"]
                            if not isinstance(raw_mode, str) or not raw_mode.strip():
                                raise ApiRequestError(
                                    "'planner_mode' override must be a non-empty string ('priority' or 'ga_nn')."
                                )
                            try:
                                req_mode = normalize_planner_mode(raw_mode, strict=True)
                            except ValueError as exc:
                                raise ApiRequestError(_redact_api_payload(exc)) from exc
                            override_cfg = replace(self.strategy_config, planner_mode=req_mode)
                        effective_cfg = override_cfg or self.strategy_config
                        effective_mode = normalize_planner_mode(effective_cfg.planner_mode)
                        state = _cached_or_fetch(
                            include_buildings=True,
                            include_research=False,
                            include_shipyard=False,
                            priority="normal",
                        )
                        if (
                            self._sync_spio_if_unread(
                                None,
                                state,
                                priority="normal",
                                get_client=_get_client,
                                ignore_auth_error=not force_refresh,
                            )
                            > 0
                        ):
                            state = self._record_state(
                                state,
                                fetched_buildings=self._last_buildings_fetched,
                                fetched_home=False,
                                observed_active_planet=False,
                                update_snapshot_ts=False,
                            )
                        actions, opt_summary = self._plan_with_summary(state, config=effective_cfg)
                        return _sanitize_finite_json(
                            _redact_api_payload(
                                {
                                    "ok": True,
                                    "command": cmd,
                                    "planner_mode": effective_mode,
                                    "actions": [self._serialize_action(a) for a in actions],
                                    "optimizer_summary": opt_summary,
                                }
                            )
                        )

                    if cmd == "buildings_eta":
                        state = _cached_or_fetch(
                            include_buildings=True,
                            include_research=False,
                            include_shipyard=False,
                            priority="normal",
                        )
                        pid = _parse_command_int(
                            "planet_id",
                            payload.get("planet_id"),
                            allow_none=True,
                            min_val=1,
                            max_val=2**63 - 1,
                        )
                        planet = self._resolve_planet(state, pid) if pid is not None else state.active_planet()
                        return _sanitize_finite_json(
                            {
                                "ok": True,
                                "command": cmd,
                                "buildings_eta": _redact_api_payload(build_buildings_eta(state, planet)),
                            }
                        )

                    if cmd == "combat_overview":
                        state = _cached_or_fetch(
                            include_buildings=False,
                            include_research=False,
                            include_shipyard=True,
                            priority="normal",
                        )
                        pid = _parse_command_int(
                            "planet_id",
                            payload.get("planet_id"),
                            allow_none=True,
                            min_val=1,
                            max_val=2**63 - 1,
                        )
                        planet = self._resolve_planet(state, pid) if pid is not None else state.active_planet()
                        return _sanitize_finite_json(
                            {
                                "ok": True,
                                "command": cmd,
                                "combat": _redact_api_payload(
                                    build_combat_overview(
                                        state,
                                        planet,
                                        safety_margin=self.strategy_config.combat_safety_margin,
                                    )
                                ),
                            }
                        )

                    if cmd == "ranking_evolution":
                        state = _cached_or_fetch(
                            include_buildings=False,
                            include_research=False,
                            include_shipyard=False,
                            priority="normal",
                        )
                        return _sanitize_finite_json(
                            {
                                "ok": True,
                                "command": cmd,
                                "ranking": _redact_api_payload(build_ranking_evolution(state, self._rank_samples)),
                            }
                        )

                    if cmd == "tick_once":
                        client = _get_client()
                        inc_r = (
                            self._last_research_sync_at <= 0.0
                            or (now_mono - self._last_research_sync_at) >= self.RESEARCH_SYNC_TTL_S
                        )
                        state = self._read_snapshot(
                            client,
                            include_buildings=True,
                            include_research=inc_r,
                            include_shipyard=False,
                            return_home=False,
                            priority="normal",
                        )
                        obs_active = bool(getattr(client, "last_snapshot_observed_active_planet", True))
                        state = self._record_state(
                            state,
                            fetched_buildings=True,
                            fetched_research=inc_r,
                            fetched_home=True,
                            observed_active_planet=obs_active,
                        )
                        if self._sync_spio_if_unread(client, state, priority="normal") > 0:
                            state = self._record_state(
                                state,
                                fetched_buildings=self._last_buildings_fetched,
                                fetched_research=inc_r,
                                fetched_home=False,
                                observed_active_planet=False,
                                update_snapshot_ts=False,
                            )
                        actions = self._plan_with_context(state)
                        ready = next((a for a in actions if a.executable_now), None)
                        if ready is None:
                            return _sanitize_finite_json(
                                _redact_api_payload(
                                    {
                                        "ok": True,
                                        "command": cmd,
                                        "executed": False,
                                        "dry_run": dry_run,
                                        "message": (
                                            f"Waiting: {_bounded_api_str(actions[0].target_name, default='')} ({_bounded_api_str(actions[0].reason, default='')})"
                                            if actions
                                            else "No pending actions."
                                        ),
                                        "action": self._serialize_action(actions[0], state) if actions else None,
                                        "next_action": self._serialize_action(actions[1], state) if len(actions) > 1 else None,
                                    }
                                )
                            )
                        return self._execute_and_summarize(client, state, cmd, ready, dry_run)

                    if cmd == "menu":
                        client = _get_client()
                        self._call_with_optional_priority(client.ensure_in_game, priority="interactive")
                        client._switch_frame("F_Navi")
                        nav_soup = BeautifulSoup(client.driver.page_source or "", "html.parser")
                        items = [
                            {
                                "text": _redact_api_payload(a.get_text(" ", strip=True)),
                                "href": _redact_api_payload(
                                    re.sub(
                                        r"(?i)([?&;])sid(?:=|%3d)(?:(?!%26|%23)[^&\s\"'<>#)])*(&?)",
                                        lambda m: (
                                            "?"
                                            if (m.group(1) == "?" and m.group(2))
                                            else ("" if m.group(1) == "?" else m.group(2))
                                        ),
                                        str(a.get("href") or ""),
                                    ).rstrip("?&;")
                                ),
                                "onclick": _redact_api_payload(str(a.get("onclick") or "")),
                            }
                            for a in nav_soup.find_all("a", href=True)
                        ]
                        return _sanitize_finite_json(
                            _redact_api_payload(
                                {
                                    "ok": True,
                                    "command": cmd,
                                    "items": items,
                                }
                            )
                        )

                    if cmd == "navigate":
                        client = _get_client()
                        raw_action = payload.get("action")
                        if not isinstance(raw_action, str):
                            raise ApiRequestError(
                                f"Unsupported menu action; allowed: {sorted(ALLOWED_MENU_ACTIONS)}"
                            )
                        action = raw_action.strip()
                        if action not in ALLOWED_MENU_ACTIONS:
                            raise ApiRequestError(
                                f"Unsupported menu action {action!r}; allowed: {sorted(ALLOWED_MENU_ACTIONS)}"
                            )
                        gal_int = _parse_command_int(
                            "galaxy", payload.get("galaxy"), allow_none=True, min_val=1, max_val=1000
                        )
                        sys_int = _parse_command_int(
                            "system", payload.get("system"), allow_none=True, min_val=1, max_val=1000
                        )

                        self._call_with_optional_priority(client.ensure_in_game, priority="interactive")
                        if action == "internalGalaxy" and (gal_int is not None or sys_int is not None):
                            html = self._open_galaxy(
                                client,
                                galaxy=gal_int,
                                system=sys_int,
                                priority="interactive",
                            )
                        else:
                            html = self._call_with_optional_priority(
                                client.navigate_menu,
                                action,
                                priority="interactive",
                            )

                        obs_nav_pid: Optional[int] = None
                        if isinstance(html, str) and html:
                            if action == "internalHome":
                                low_h = html.lower()
                                if (
                                    "made more than" not in low_h
                                    and "bitmeup-login" not in low_h
                                    and "sip-tab-universe" not in low_h
                                ):
                                    obs_nav_pid = parse_home_metadata(html)[0]
                            elif action in (
                                "internalBuildings",
                                "internalEconomy",
                                "internalResearch",
                                "internalWeaponFactory",
                            ):
                                obs_nav_pid = GameBrowserClient._extract_selected_planet_id(
                                    html,
                                    action,
                                )

                        handled_econ_sync = False
                        if action == "internalEconomy":
                            rates = parse_economy_rates(html)
                            if rates and self._last_state is not None:
                                valid_nav_pid = (
                                    obs_nav_pid
                                    if isinstance(obs_nav_pid, int)
                                    and not isinstance(obs_nav_pid, bool)
                                    and any(p.planet_id == obs_nav_pid for p in self._last_state.planets)
                                    else None
                                )
                                active_p = self._last_state.active_planet()
                                target_rate_pid = valid_nav_pid or (active_p.planet_id if active_p is not None else None)
                                if target_rate_pid is not None:
                                    self._economy_rates[target_rate_pid] = dict(rates)
                                    switched_pid = (
                                        valid_nav_pid is not None
                                        and self._last_state.active_planet_id != valid_nav_pid
                                    )
                                    st_econ = (
                                        replace(
                                            self._last_state,
                                            active_planet_id=valid_nav_pid,
                                            research_offers=() if len(self._last_state.planets) > 1 else self._last_state.research_offers,
                                        )
                                        if switched_pid
                                        else self._last_state
                                    )
                                    self._record_state(
                                        st_econ,
                                        fetched_buildings=(self._last_buildings_fetched and not switched_pid),
                                        fetched_home=False,
                                        observed_active_planet=(valid_nav_pid is not None),
                                        update_snapshot_ts=False,
                                    )
                                    handled_econ_sync = True
                        if (
                            not handled_econ_sync
                            and self._last_state is not None
                            and isinstance(obs_nav_pid, int)
                            and not isinstance(obs_nav_pid, bool)
                            and any(p.planet_id == obs_nav_pid for p in self._last_state.planets)
                        ):
                            self._sync_observed_active_planet_state(
                                self._last_state,
                                obs_pid=obs_nav_pid,
                                obs_confirmed=True,
                            )

                        saved_slots = 0
                        actual_gal: Optional[int] = None
                        actual_sys: Optional[int] = None
                        if action == "internalGalaxy":
                            coords_attr = getattr(client, "last_galaxy_coords", None)
                            if (
                                isinstance(coords_attr, tuple)
                                and len(coords_attr) == 2
                                and all(isinstance(v, int) and not isinstance(v, bool) for v in coords_attr)
                            ):
                                actual_gal = max(1, min(1000, game_data.cap_json_int(coords_attr[0])))
                                actual_sys = max(1, min(1000, game_data.cap_json_int(coords_attr[1])))
                            else:
                                def_gal = gal_int or 1
                                def_sys = sys_int or 1
                                if gal_int is None and sys_int is None and self._last_state is not None:
                                    ap = self._last_state.active_planet()
                                    if ap is not None:
                                        m_ap = re.search(r"(\d{1,2}):(\d{1,3})", ap.coords, re.ASCII)
                                        if m_ap:
                                            def_gal = int(m_ap.group(1))
                                            def_sys = int(m_ap.group(2))
                                actual_gal, actual_sys = extract_galaxy_system_coords(
                                    html,
                                    default=(def_gal, def_sys),
                                )
                            slots = parse_galaxy_system_html(
                                html,
                                galaxy=actual_gal,
                                system=actual_sys,
                            )
                            self._persist_galaxy_scan(actual_gal, actual_sys, slots)
                            saved_slots = self._persist_galaxy_slots(slots)
                            if self._last_state is not None:
                                self._refresh_cached_economy(self._last_state)

                        res_payload: dict[str, Any] = {
                            "ok": True,
                            "command": cmd,
                            "action": action,
                            "html_bytes": len(html),
                        }
                        if action == "internalGalaxy":
                            res_payload["scanned_targets"] = saved_slots
                            res_payload["galaxy"] = actual_gal
                            res_payload["system"] = actual_sys
                        if payload.get("include_html"):
                            res_payload["html"] = _redact_api_payload(html)
                        if payload.get("return_home", False):
                            self._call_with_optional_priority(
                                client.navigate_menu,
                                "internalHome",
                                priority="interactive",
                            )
                        return _sanitize_finite_json(_redact_api_payload(res_payload))

                    if cmd == "scan_galaxy":
                        pid = _parse_command_int(
                            "planet_id",
                            payload.get("planet_id"),
                            allow_none=True,
                            min_val=1,
                            max_val=2**63 - 1,
                        )
                        if self._last_state is not None and not force_refresh:
                            state = self._last_state
                        else:
                            live_client = _get_client()
                            state = self._read_snapshot(
                                live_client,
                                include_buildings=False,
                                include_research=False,
                                include_shipyard=False,
                                include_home=False,
                                return_home=False,
                                priority="interactive",
                            )
                            obs_active = bool(
                                getattr(live_client, "last_snapshot_observed_active_planet", True)
                            )
                            state = self._record_state(
                                state,
                                fetched_home=False,
                                observed_active_planet=obs_active,
                            )
                        planet = self._resolve_planet(state, pid)
                        gal_arg = _parse_command_int(
                            "galaxy", payload.get("galaxy"), allow_none=True, min_val=1, max_val=1000
                        )
                        sys_arg = _parse_command_int(
                            "system", payload.get("system"), allow_none=True, min_val=1, max_val=1000
                        )
                        if gal_arg is not None and sys_arg is not None:
                            target_lbl = f"Galaxy [{gal_arg:02d}:{sys_arg:03d}]"
                        else:
                            parts = [p.strip() for p in planet.coords.split(":")]
                            p0 = (parts[0].lstrip("0") or "0") if len(parts) >= 2 else ""
                            p1 = (parts[1].lstrip("0") or "0") if len(parts) >= 2 else ""
                            if (
                                len(parts) >= 2
                                and parts[0].isascii()
                                and parts[0].isdigit()
                                and len(p0) <= 3
                                and parts[1].isascii()
                                and parts[1].isdigit()
                                and len(p1) <= 3
                            ):
                                gal_p = max(1, min(999, int(p0)))
                                sys_p = max(1, min(999, int(p1)))
                                target_lbl = f"Galaxy [{gal_p:02d}:{sys_p:03d}]"
                            else:
                                target_lbl = "Galaxy [01:001]"
                        planned = PlannedAction(
                            priority="P2",
                            kind="SCAN_GALAXY",
                            planet_id=planet.planet_id,
                            planet_coords=planet.coords,
                            target_name=target_lbl,
                            reason="API scan_galaxy command",
                            executable_now=True,
                        )
                        return self._execute_and_summarize(_exec_client(planned), state, cmd, planned, dry_run)

                    if cmd == "sync_spy_reports":
                        client = _get_client()
                        self._call_with_optional_priority(client.ensure_in_game, priority="interactive")
                        html = self._call_with_optional_priority(
                            client.open_messages,
                            "spio",
                            priority="interactive",
                        )
                        reports = parse_spy_messages_html(html)
                        synced = self._persist_spy_reports(reports)
                        if self._last_state is not None:
                            self._refresh_cached_economy(self._last_state)
                        return _sanitize_finite_json(
                            {
                                "ok": True,
                                "command": cmd,
                                "synced_reports": synced,
                                "reports": _redact_api_payload([asdict(r) for r in reports]),
                            }
                        )

                    if cmd == "spy":
                        raw_target_coords = payload.get("target_coords")
                        if not isinstance(raw_target_coords, str):
                            raise ApiRequestError(
                                "'spy' command requires 'target_coords' formatted as GG:SSS:PP."
                            )
                        target_coords = raw_target_coords.strip()
                        if not re.fullmatch(r"\d{1,2}:\d{1,3}:\d{1,2}", target_coords, re.ASCII):
                            raise ApiRequestError(
                                "'spy' command requires 'target_coords' formatted as GG:SSS:PP."
                            )
                        norm_target = normalize_coords(target_coords)
                        qty_parsed = _parse_command_int(
                            "quantity",
                            payload.get("quantity", 1),
                            min_val=1,
                            max_val=1_000_000_000,
                        )
                        assert qty_parsed is not None
                        qty = qty_parsed
                        pid = _parse_command_int(
                            "planet_id",
                            payload.get("planet_id"),
                            allow_none=True,
                            min_val=1,
                            max_val=2**63 - 1,
                        )
                        state = _resolve_fleet_command_state(pid, is_spy=True)
                        if pid is None:
                            probe_p = next(
                                (p for p in state.planets if _safe_api_int(p.ships.get("s10", 0)) > 0),
                                None,
                            )
                            planet = probe_p or self._resolve_planet(state, None)
                        else:
                            planet = self._resolve_planet(state, pid)
                        if any(normalize_coords(p.coords) == norm_target for p in state.planets):
                            planned_own = PlannedAction(
                                priority="P2",
                                kind="SPY",
                                planet_id=planet.planet_id,
                                planet_coords=planet.coords,
                                target_id=10,
                                target_level=qty,
                                target_name=norm_target,
                                reason="Own colony safety check",
                                executable_now=False,
                            )
                            return _sanitize_finite_json(
                                _redact_api_payload(
                                    {
                                        "ok": True,
                                        "command": cmd,
                                        "executed": False,
                                        "dry_run": dry_run,
                                        "message": f"Aborted: {norm_target} is one of your own planets.",
                                        "action": self._serialize_action(planned_own, state),
                                        "next_action": None,
                                    }
                                )
                            )
                        planned = PlannedAction(
                            priority="P2",
                            kind="SPY",
                            planet_id=planet.planet_id,
                            planet_coords=planet.coords,
                            target_id=10,
                            target_level=qty,
                            target_name=norm_target,
                            reason="API spy command",
                            executable_now=True,
                        )
                        return self._execute_and_summarize(_exec_client(planned), state, cmd, planned, dry_run)

                    if cmd == "attack":
                        raw_target_coords = payload.get("target_coords")
                        if not isinstance(raw_target_coords, str):
                            raise ApiRequestError(
                                "'attack' command requires 'target_coords' formatted as GG:SSS:PP."
                            )
                        target_coords = raw_target_coords.strip()
                        if not re.fullmatch(r"\d{1,2}:\d{1,3}:\d{1,2}", target_coords, re.ASCII):
                            raise ApiRequestError(
                                "'attack' command requires 'target_coords' formatted as GG:SSS:PP."
                            )
                        norm_target = normalize_coords(target_coords)
                        pid = _parse_command_int(
                            "planet_id",
                            payload.get("planet_id"),
                            allow_none=True,
                            min_val=1,
                            max_val=2**63 - 1,
                        )
                        state = _resolve_fleet_command_state(pid, is_spy=False)
                        planet = self._resolve_planet(state, pid)
                        planned = PlannedAction(
                            priority="P2",
                            kind="ATTACK",
                            planet_id=planet.planet_id,
                            planet_coords=planet.coords,
                            target_name=norm_target,
                            reason="API attack command",
                            executable_now=True,
                        )
                        # Check allow_raids guard first so `"allow_raids"` error message contract holds
                        if not dry_run and not self._live_executor.allow_raids:
                            return self._execute_and_summarize(_exec_client(planned), state, cmd, planned, dry_run)

                        if any(normalize_coords(p.coords) == norm_target for p in state.planets):
                            return _sanitize_finite_json(
                                _redact_api_payload(
                                    {
                                        "ok": True,
                                        "command": cmd,
                                        "executed": False,
                                        "dry_run": dry_run,
                                        "message": f"Aborted: {norm_target} is one of your own planets.",
                                        "action": self._serialize_action(planned, state),
                                        "next_action": None,
                                    }
                                )
                            )

                        target_row = next(
                            (
                                r
                                for r in (self._raid_targets or ())
                                if normalize_coords(r.get("coords") or r.get("target_coords") or "") == norm_target
                            ),
                            None,
                        )
                        if target_row is None and self._db is not None:
                            get_by_coords = getattr(self._db, "get_spy_target_by_coords", None)
                            if callable(get_by_coords):
                                target_row = get_by_coords(norm_target)
                        is_inactive = bool((target_row or {}).get("is_inactive", False))
                        is_spacia = bool((target_row or {}).get("is_spacia", False))
                        status_color = game_data.safe_key((target_row or {}).get("status_color") or "").strip().lower()
                        if status_color == "yellow":
                            is_inactive = True
                        if status_color in ("blue", "green", "white", "purple", "dark_red") and not is_spacia:
                            return _sanitize_finite_json(
                                _redact_api_payload(
                                    {
                                        "ok": True,
                                        "command": cmd,
                                        "executed": False,
                                        "dry_run": dry_run,
                                        "message": f"Aborted: target {norm_target} is protected ({status_color}).",
                                        "action": self._serialize_action(planned, state),
                                        "next_action": None,
                                    }
                                )
                            )
                        if (
                            status_color == "light_red"
                            and not is_inactive
                            and not is_spacia
                            and not self.strategy_config.allow_pvp_in_az
                        ):
                            return _sanitize_finite_json(
                                _redact_api_payload(
                                    {
                                        "ok": True,
                                        "command": cmd,
                                        "executed": False,
                                        "dry_run": dry_run,
                                        "message": f"Aborted: target {norm_target} is an active player and allow_pvp_in_az=false.",
                                        "action": self._serialize_action(planned, state),
                                        "next_action": None,
                                    }
                                )
                            )

                        if self._db is not None:
                            bash = self._db.load_bash_tracker()
                            if not bash.can_attack(
                                norm_target,
                                is_inactive=is_inactive,
                                is_spacia=is_spacia,
                            ):
                                return _sanitize_finite_json(
                                    _redact_api_payload(
                                        {
                                            "ok": True,
                                            "command": cmd,
                                            "executed": False,
                                            "dry_run": dry_run,
                                            "message": f"Aborted: BashTracker 3/24h limit reached for {norm_target}.",
                                            "action": self._serialize_action(planned, state),
                                            "next_action": None,
                                        }
                                    )
                                )

                        if target_row is not None:
                            res_d = target_row.get("resources") if isinstance(target_row.get("resources"), Mapping) else {}
                            rep_res = Resources(
                                pig_iron=max(0, _safe_api_int(res_d.get("pig_iron", 0), 0)),
                                metal=max(0, _safe_api_int(res_d.get("metal", 0), 0)),
                                kryptonite=max(0, _safe_api_int(res_d.get("kryptonite", 0), 0)),
                                spice=max(0, _safe_api_int(res_d.get("spice", 0), 0)),
                            )
                            raw_fleet = target_row.get("fleet")
                            rep_fleet = (
                                {
                                    game_data.safe_key(k): cnt
                                    for k, v in raw_fleet.items()
                                    if (cnt := max(0, _safe_api_int(v, 0))) > 0
                                }
                                if isinstance(raw_fleet, Mapping)
                                else {}
                            )
                            raw_def = target_row.get("defense")
                            rep_def = (
                                {
                                    game_data.safe_key(k): cnt
                                    for k, v in raw_def.items()
                                    if (cnt := max(0, _safe_api_int(v, 0))) > 0
                                }
                                if isinstance(raw_def, Mapping)
                                else {}
                            )
                            if bool(target_row.get("has_spy_intel")) or not rep_res.is_zero() or rep_fleet or rep_def:
                                attack_keys = ("s4", "s5", "s6", "s7", "s13", "s14", "s15", "s16", "s1", "s2", "s3")
                                atk_ships = {
                                    k: cnt
                                    for k in attack_keys
                                    if (cnt := max(0, _safe_api_int(planet.ships.get(k, 0), 0))) > 0
                                }
                                if atk_ships:
                                    rep_obj = SpyReport(
                                        target_coords=norm_target,
                                        owner=game_data.safe_key(target_row.get("owner") or ""),
                                        resources=rep_res,
                                        fleet=rep_fleet,
                                        defense=rep_def,
                                        dug_in=bool(target_row.get("dug_in", False)),
                                        is_spacia=is_spacia,
                                    )
                                    sim = simulate_raid_outcome(
                                        atk_ships,
                                        rep_obj,
                                        attacker_researches=state.researches,
                                        safety_margin=self.strategy_config.combat_safety_margin,
                                    )
                                    if not sim.safe_to_attack:
                                        return _sanitize_finite_json(
                                            _redact_api_payload(
                                                {
                                                    "ok": True,
                                                    "command": cmd,
                                                    "executed": False,
                                                    "dry_run": dry_run,
                                                    "message": f"Aborted: combat safety check failed ({sim.reason}).",
                                                    "action": self._serialize_action(planned, state),
                                                    "next_action": None,
                                                }
                                            )
                                        )

                        return self._execute_and_summarize(_exec_client(planned), state, cmd, planned, dry_run)

                    if cmd == "build":
                        raw_bid = payload.get("building_id")
                        if raw_bid is None:
                            raise ApiRequestError("'build' command requires 'building_id'.")
                        bid = _parse_command_int(
                            "building_id", raw_bid, min_val=1, max_val=2**31 - 1
                        )
                        pid = _parse_command_int(
                            "planet_id",
                            payload.get("planet_id"),
                            allow_none=True,
                            min_val=1,
                            max_val=2**63 - 1,
                        )
                        state = _cached_or_fetch(
                            include_buildings=bool(dry_run or force_refresh),
                            include_research=False,
                            include_shipyard=False,
                            include_home=False,
                            priority="interactive",
                        )
                        planned = self._find_build_action(
                            state,
                            bid,
                            pid,
                            allow_live_verify=not dry_run,
                        )
                        return self._execute_and_summarize(_exec_client(planned), state, cmd, planned, dry_run)

                    if cmd == "research":
                        raw_rid = payload.get("research_id")
                        if raw_rid is None:
                            raise ApiRequestError("'research' command requires 'research_id'.")
                        rid = _parse_command_int(
                            "research_id", raw_rid, min_val=1, max_val=2**31 - 1
                        )
                        pid = _parse_command_int(
                            "planet_id",
                            payload.get("planet_id"),
                            allow_none=True,
                            min_val=1,
                            max_val=2**63 - 1,
                        )
                        state = _cached_or_fetch(
                            include_buildings=False,
                            include_research=bool(dry_run or force_refresh),
                            include_shipyard=False,
                            include_home=False,
                            priority="interactive",
                        )
                        planned = self._find_research_action(
                            state,
                            rid,
                            pid,
                            allow_live_verify=not dry_run,
                        )
                        return self._execute_and_summarize(_exec_client(planned), state, cmd, planned, dry_run)

                    if cmd == "build_ship":
                        raw_sid = payload.get("ship_id")
                        if raw_sid is None:
                            raise ApiRequestError("'build_ship' command requires 'ship_id'.")
                        sid_int = _parse_command_int(
                            "ship_id", raw_sid, min_val=1, max_val=2**31 - 1
                        )
                        qty_parsed = _parse_command_int(
                            "quantity",
                            payload.get("quantity", 1),
                            min_val=1,
                            max_val=1_000_000_000,
                        )
                        assert qty_parsed is not None
                        qty = qty_parsed
                        pid = _parse_command_int(
                            "planet_id",
                            payload.get("planet_id"),
                            allow_none=True,
                            min_val=1,
                            max_val=2**63 - 1,
                        )
                        state = _cached_or_fetch(
                            include_buildings=False,
                            include_research=False,
                            include_shipyard=bool(dry_run or force_refresh),
                            include_home=False,
                            priority="interactive",
                        )
                        planned = self._find_ship_action(
                            state,
                            sid_int,
                            qty,
                            pid,
                            allow_live_verify=not dry_run,
                        )
                        return self._execute_and_summarize(_exec_client(planned), state, cmd, planned, dry_run)

                    if cmd == "throttle_building":
                        bid = _parse_command_int(
                            "building_id",
                            payload.get("building_id", 2),
                            min_val=1,
                            max_val=2**31 - 1,
                        )
                        if payload.get("percentage") is None:
                            raise ApiRequestError("'throttle_building' command requires 'percentage' (0..100).")
                        pct_raw = _parse_command_int(
                            "percentage", payload["percentage"], min_val=0, max_val=100
                        )
                        assert pct_raw is not None
                        pct = max(0, min(100, (pct_raw // 10) * 10))
                        pid = _parse_command_int(
                            "planet_id",
                            payload.get("planet_id"),
                            allow_none=True,
                            min_val=1,
                            max_val=2**63 - 1,
                        )
                        state = _cached_or_fetch(
                            include_buildings=False,
                            include_research=False,
                            include_shipyard=False,
                            include_home=False,
                            priority="interactive",
                        )
                        planet = self._resolve_planet(state, pid)
                        ptype_norm = normalize_planet_type(planet)
                        bid_allowed = ptype_norm not in ("asteroid", "raid") and bid in (1, 2, 3, 4, 5, 6)
                        label = "Blast furnace" if bid == 2 else planet.building_names.get(f"b{bid}", f"Building b{bid}")
                        if ptype_norm in ("asteroid", "raid"):
                            t_reason = f"Throttle not permitted on planet_type={ptype_norm}"
                        elif bid not in (1, 2, 3, 4, 5, 6):
                            t_reason = f"Building b{bid} does not support internalEconomy rate control"
                        else:
                            t_reason = f"Set {label} rate to {pct}% in internalEconomy"
                        planned = PlannedAction(
                            priority="P1",
                            kind="THROTTLE_BUILDING",
                            planet_id=planet.planet_id,
                            planet_coords=planet.coords,
                            target_id=bid,
                            target_name=f"{label} {pct}%",
                            target_level=pct,
                            reason=t_reason,
                            executable_now=bid_allowed,
                        )
                        return self._execute_and_summarize(_exec_client(planned), state, cmd, planned, dry_run)

                    if cmd == "fleetsave_plan":
                        pid = _parse_command_int(
                            "planet_id",
                            payload.get("planet_id"),
                            allow_none=True,
                            min_val=1,
                            max_val=2**63 - 1,
                        )
                        reserve_override = _parse_command_int(
                            "fuel_reserve_spice",
                            payload.get("fuel_reserve_spice"),
                            allow_none=True,
                            min_val=0,
                            max_val=game_data.MAX_JSON_SAFE_INT,
                        )
                        state = _cached_or_fetch(
                            include_buildings=False,
                            include_research=False,
                            include_shipyard=False,
                            include_home=False,
                            priority="interactive",
                        )
                        planet = self._resolve_planet(state, pid)
                        raw_target_coords = payload.get("target_coords")
                        if raw_target_coords is None:
                            target_coords = ""
                        elif not isinstance(raw_target_coords, str):
                            raise ApiRequestError("'fleetsave_plan' field 'target_coords' must be a string.")
                        else:
                            target_coords = raw_target_coords.strip()
                        if not target_coords:
                            target_coords = next(
                                (p.coords for p in state.planets if p.planet_id != planet.planet_id),
                                planet.coords,
                            )
                        fuel_reserve = (
                            reserve_override
                            if reserve_override is not None
                            else self.strategy_config.fleet_save_fuel_reserve_spice
                        )
                        escape = plan_escape_flight(
                            planet,
                            target_coords=target_coords,
                            fuel_reserve_spice=fuel_reserve,
                        )
                        return _sanitize_finite_json(
                            {"ok": True, "command": cmd, "escape_plan": _redact_api_payload(asdict(escape))}
                        )

                    if cmd == "claim_tutorial":
                        state = _cached_or_fetch(
                            include_buildings=False,
                            include_research=False,
                            include_shipyard=False,
                            include_home=True,
                            priority="interactive",
                        )
                        if not (state.tutorial and state.tutorial.can_complete):
                            return _sanitize_finite_json(
                                {
                                    "ok": True,
                                    "command": cmd,
                                    "executed": False,
                                    "dry_run": dry_run,
                                    "message": "Tutorial step is not ready to claim.",
                                }
                            )
                        planet = state.active_planet()
                        planned = PlannedAction(
                            priority="P2",
                            kind="CLAIM_TUTORIAL",
                            planet_id=planet.planet_id if planet else 0,
                            planet_coords=planet.coords if planet else "",
                            target_name=(
                                f"Tutorial {game_data.fmt_compact_int(state.tutorial.step)}"
                                f"/{game_data.fmt_compact_int(state.tutorial.total_steps)} Reward"
                            ),
                            reason="API claim_tutorial command",
                            executable_now=True,
                        )
                        return self._execute_and_summarize(
                            _exec_client(planned),
                            state,
                            cmd,
                            planned,
                            dry_run,
                            return_home=True,
                        )

                    raise ApiRequestError(f"Unknown command {cmd!r}.")
            except RateLimitError as exc:
                self._record_server_rate_limit(exc)
                raise


class _RequestTooLargeError(Exception):
    """Raised when an HTTP POST request exceeds `MAX_REQUEST_BYTES`."""


class _ReusableThreadingHTTPServer(ThreadingHTTPServer):
    allow_reuse_address = True
    daemon_threads = True


def create_api_server(
    bridge: GameApiBridge,
    *,
    host: str = "127.0.0.1",
    port: int = 8787,
) -> ThreadingHTTPServer:
    """Create a local `ThreadingHTTPServer` bound to `host:port` for `bridge`."""

    def _build_rate_limit_response(exc: RateLimitError) -> tuple[dict[str, Any], dict[str, str]]:
        limiter_obj = getattr(bridge, "_page_limiter", None)
        already_recorded = (
            limiter_obj is not None
            and getattr(exc, "_recorded_limiter_id", None) == id(limiter_obj)
        )
        exc_retry = _safe_limiter_wait(getattr(exc, "retry_after", 5.0), 5.0, min_val=1.0)
        if getattr(exc, "server_throttle", True) and not already_recorded:
            rem_raw = bridge.rate_limit_remaining()
            rem_now = (
                _safe_limiter_wait(rem_raw, 0.0, min_val=0.0)
                if isinstance(rem_raw, (int, float)) and not isinstance(rem_raw, bool)
                else 0.0
            )
            req = _safe_limiter_wait(getattr(exc, "retry_after", 0.0), 0.0, min_val=0.0)
            if rem_now <= 0.0 or req > rem_now + 1.0:
                bridge.record_rate_limit(exc_retry)
        rem_raw_final = bridge.rate_limit_remaining()
        rem = (
            _safe_limiter_wait(rem_raw_final, exc_retry, min_val=0.0)
            if isinstance(rem_raw_final, (int, float))
            and not isinstance(rem_raw_final, bool)
            and rem_raw_final > 0.0
            else exc_retry
        )
        err_txt = str(_redact_api_payload(exc))
        if "retry after" not in err_txt.lower():
            err_txt = f"{err_txt} (retry after {rem:.1f}s)"
        body = {
            "ok": False,
            "rate_limited": True,
            "retry_after": round(rem, 1),
            "error": _redact_api_payload(err_txt),
        }
        headers = {"Retry-After": str(max(1, int(math.ceil(rem))))}
        return body, headers

    class Handler(BaseHTTPRequestHandler):
        def _send_json(
            self,
            status: int,
            body: dict[str, Any],
            *,
            extra_headers: Optional[dict[str, str]] = None,
        ) -> None:
            clean_body = _sanitize_finite_json(body)
            raw = json.dumps(clean_body, allow_nan=False).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            if extra_headers:
                for hk, hv in extra_headers.items():
                    self.send_header(str(hk), str(hv))
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

        def _send_static(self, filename: str, content_type: str) -> None:
            asset_path = UI_DIR / filename
            if not asset_path.exists():
                self._send_json(HTTPStatus.NOT_FOUND, {"ok": False, "error": f"Missing UI asset {filename}"})
                return
            raw = asset_path.read_bytes()
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", content_type)
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

        def log_message(self, format: str, *args: Any) -> None:
            return

        def do_GET(self) -> None:
            parsed = urlparse(self.path)
            try:
                if parsed.path in _UI_ASSETS:
                    filename, ctype = _UI_ASSETS[parsed.path]
                    self._send_static(filename, ctype)
                    return
                if parsed.path == "/favicon.ico":
                    self.send_response(HTTPStatus.NO_CONTENT)
                    self.send_header("Cache-Control", "no-store")
                    self.end_headers()
                    return
                if parsed.path == "/ui-version":
                    self._send_json(
                        HTTPStatus.OK,
                        {
                            "ok": True,
                            "server_boot_id": _SERVER_BOOT_ID,
                            "ts": round(time.time(), 3),
                            **compute_ui_assets_state(),
                        },
                    )
                    return
                if parsed.path == "/health":
                    self._send_json(HTTPStatus.OK, bridge.health())
                    return
                if parsed.path == "/bot_status":
                    self._send_json(HTTPStatus.OK, bridge.get_bot_control())
                    return
                if parsed.path == "/dashboard":
                    qs = parse_qs(parsed.query)
                    ref = qs.get("refresh", ["0"])[0].lower() in ("1", "true", "yes")
                    self._send_json(HTTPStatus.OK, bridge.dashboard(refresh=ref))
                    return
                if parsed.path == "/state":
                    qs = parse_qs(parsed.query)
                    inc = qs.get("include_buildings", ["1"])[0].lower() not in ("0", "false", "no")
                    self._send_json(HTTPStatus.OK, bridge.get_state(include_buildings=inc))
                    return
                self._send_json(HTTPStatus.NOT_FOUND, {"ok": False, "error": _redact_api_payload(f"Unknown route {parsed.path}")})
            except RateLimitError as exc:
                body, headers = _build_rate_limit_response(exc)
                self._send_json(
                    HTTPStatus.TOO_MANY_REQUESTS,
                    body,
                    extra_headers=headers,
                )
            except SpaceInvasionAuthError as exc:
                self._send_json(HTTPStatus.SERVICE_UNAVAILABLE, {"ok": False, "error": _redact_api_payload(exc)})
            except Exception as exc:
                self._send_json(HTTPStatus.INTERNAL_SERVER_ERROR, {"ok": False, "error": _redact_api_payload(exc)})

        def do_POST(self) -> None:
            parsed = urlparse(self.path)
            if parsed.path != "/command":
                self._send_json(HTTPStatus.NOT_FOUND, {"ok": False, "error": _redact_api_payload(f"Unknown route {parsed.path}")})
                return
            payload: Optional[dict[str, Any]] = None
            status_code = HTTPStatus.OK
            body: dict[str, Any] = {}
            extra_headers: Optional[dict[str, str]] = None
            try:
                raw_len_hdr = self.headers.get("Content-Length")
                if raw_len_hdr is None:
                    length = 0
                else:
                    raw_len = str(raw_len_hdr).strip()
                    if not raw_len:
                        length = 0
                    elif not (raw_len.isascii() and raw_len.isdigit()) or len(raw_len) > 18:
                        raise ApiRequestError("Invalid Content-Length header")
                    else:
                        length = int(raw_len)
                if length > MAX_REQUEST_BYTES:
                    raise _RequestTooLargeError(
                        f"Request body too large (max {MAX_REQUEST_BYTES} bytes)"
                    )
                raw = self.rfile.read(length) if length > 0 else b"{}"
                payload = json.loads(raw.decode("utf-8"), parse_constant=_reject_nonfinite_json)
                body = bridge.handle_command(payload)
            except _RequestTooLargeError as exc:
                status_code = HTTPStatus.REQUEST_ENTITY_TOO_LARGE
                body = {"ok": False, "error": _redact_api_payload(exc)}
            except (ValueError, ApiRequestError) as exc:
                status_code = HTTPStatus.BAD_REQUEST
                body = {"ok": False, "error": _redact_api_payload(exc)}
            except RateLimitError as exc:
                body, extra_headers = _build_rate_limit_response(exc)
                status_code = HTTPStatus.TOO_MANY_REQUESTS
            except SpaceInvasionAuthError as exc:
                status_code = HTTPStatus.SERVICE_UNAVAILABLE
                body = {"ok": False, "error": _redact_api_payload(exc)}
            except Exception as exc:
                status_code = HTTPStatus.INTERNAL_SERVER_ERROR
                body = {"ok": False, "error": _redact_api_payload(exc)}

            log_dir = getattr(bridge, "log_dir", None)
            if isinstance(log_dir, (str, Path)):
                log_payload = (
                    payload
                    if isinstance(payload, Mapping)
                    else ({"raw_payload": payload} if payload is not None else None)
                )
                try:
                    append_api_log(
                        Path(log_dir),
                        method="POST",
                        path=parsed.path,
                        status=int(status_code),
                        payload=_sanitize_finite_json(log_payload),
                        response=_sanitize_finite_json(body),
                    )
                except Exception:
                    pass
            self._send_json(status_code, body, extra_headers=extra_headers)

    return _ReusableThreadingHTTPServer((host, port), Handler)
