"""Local HTTP JSON API wrapping the live SpaceInvasion Chrome debug session."""

from __future__ import annotations

import contextlib
from dataclasses import asdict, replace
import hashlib
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from collections import deque
from collections.abc import Mapping
import inspect
import json
import math
import os
from pathlib import Path
import random
import re
import secrets
import threading
import time
from typing import Any, Callable, Deque, Optional, Sequence
from urllib.parse import parse_qs, urlparse

from bs4 import BeautifulSoup

from spaceinvasion.core import game_data
from spaceinvasion.planner.action_graph import (
    build_action_graph,
    merge_strategy_config_overrides,
)
from spaceinvasion.session.auth import AntiBotChallengeError, SpaceInvasionAuthError, load_credentials
from spaceinvasion.session.browser import (
    DEFAULT_DEBUG_PORT,
    BrowserConfig,
    is_debug_port_open,
    login_with_browser,
)
from spaceinvasion.session.client import GameBrowserClient, PageViewLimiter, RateLimitError, is_server_rate_limit_html
from spaceinvasion.core.config import (
    StrategyConfig,
    load_strategy_config,
    normalize_planner_mode,
    save_strategy_config,
    strategy_config_from_dict,
)
from spaceinvasion.session.executor import ActionExecutor
from spaceinvasion.combat.fleet import (
    SHIP_SLUG_BY_KEY,
    InboxMessage,
    SpyReport,
    extract_galaxy_system_coords,
    extract_message_folder_links,
    normalize_coords,
    parse_galaxy_system_html,
    parse_inbox_messages_html,
    parse_spy_messages_html,
    plan_escape_flight,
    simulate_raid_outcome,
)
from spaceinvasion.core.logging_utils import (
    _SECRET_DICT_KEYS,
    _SID_RE as _LOG_SID_RE,
    _sub_sid_kv as _sub_log_sid_kv,
    append_api_log,
    append_crash_debug_log,
    is_secret_dict_key,
    redact_sensitive,
)
from spaceinvasion.planner.optimizer import get_last_optimizer_summary, run_optimizer
from spaceinvasion.combat.ranking import (
    attach_action_etas,
    build_buildings_eta,
    build_combat_overview,
    build_ranking_evolution,
    load_rank_history,
    record_rank_snapshot,
    simulate_combat_matchup,
)
from spaceinvasion.core.state import (
    GameState,
    PlanetState,
    Resources,
    count_allocated_normal_planets,
    is_building_allowed_on_planet_type,
    normalize_planet_type,
    parse_economy_rates,
    parse_home_metadata,
    parse_tutorial_from_home,
    parse_unread_banner_count,
)
from spaceinvasion.db.migrations import DatabaseMigrationRegistry, LATEST_SCHEMA_VERSION
from spaceinvasion.db.storage import GameDatabase, server_day_key
from spaceinvasion.planner.strategy import (
    PlannedAction,
    _tutorial_colonize_task,
    merge_manual_queue_actions,
    plan_actions,
)

UI_DIR = Path(__file__).resolve().parents[1] / "ui"
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
    "/favicon.svg": ("favicon.svg", "image/svg+xml"),
    "/bell.svg": ("bell.svg", "image/svg+xml"),
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
        digits = txt[1:] if txt[0] in ("+", "-") else txt
        if not digits or not digits.isdigit():
            raise ApiRequestError(f"Command parameter {name!r} must be an integer.")
        trimmed = digits.lstrip("0") or "0"
        if len(trimmed) > 4000:
            raise ApiRequestError(f"Command parameter {name!r} integer is out of range.")
        parsed = -int(trimmed) if txt[0] == "-" else int(trimmed)
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
        digits = txt[1:] if txt[0] in ("+", "-") else txt
        if not digits or not digits.isdigit():
            raise ApiRequestError(f"Config field {name!r} must be an integer.")
        trimmed = digits.lstrip("0") or "0"
        if len(trimmed) > 4000:
            raise ApiRequestError(f"Config field {name!r} integer is out of range.")
        parsed = -int(trimmed) if txt[0] == "-" else int(trimmed)
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
        if not txt or not txt.isascii() or "_" in txt:
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


def _validate_config_patch(raw_cfg: Mapping[str, Any], *, strict_bools: bool = True) -> None:
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
        "action_batch_interval_seconds": (5, 86400, False),
        "max_actions_per_hour": (1, 100_000, False),
        "min_free_fields_warning": (1, 1000, False),
        "min_colony_fields": (50, 600, False),
        "max_planets": (1, 50, False),
        "galaxy_sweep_interval_seconds": (30, 86400, False),
        "galaxy_sweep_pages": (1, 400, False),
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

    bool_keys: tuple[str, ...] = (
        "merchant_mode",
        "auto_throttle_blast_furnace",
        "allow_research",
        "allow_shipyard",
        "allow_multi_planet",
        "allow_fleetsave",
        "allow_galaxy_scan",
        "allow_raids",
        "allow_pvp_in_az",
        "allow_colonize",
        "allow_transmitter",
        "forbid_planetary_solar",
    )
    if strict_bools:
        for key in bool_keys:
            if key in raw_cfg and not isinstance(raw_cfg[key], bool):
                raise ApiRequestError(f"Config field {key!r} must be a boolean.")

    if "shipyard_targets" in raw_cfg:
        raw_targets = raw_cfg["shipyard_targets"]
        if not isinstance(raw_targets, Mapping):
            raise ApiRequestError(
                "Config field 'shipyard_targets' must be a mapping of ship keys to integer counts."
            )
        for sk, sv in raw_targets.items():
            if not isinstance(sk, str) or not sk.strip():
                raise ApiRequestError(
                    "Config field 'shipyard_targets' keys must be non-empty strings."
                )
            _validate_strict_int(
                f"shipyard_targets.{sk.strip()}",
                sv,
                min_val=0,
                max_val=1_000_000_000,
                allow_none=False,
            )


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


def _redact_action_graph_strings(graph: dict[str, Any]) -> dict[str, Any]:
    for lane in graph.get("lanes", ()):
        if isinstance(lane, dict):
            for k in ("label", "coords"):
                if isinstance(lane.get(k), str) and lane[k]:
                    lane[k] = _scrub_api_str(lane[k])
    for node in graph.get("nodes", ()):
        if isinstance(node, dict):
            for k in (
                "planet_coords",
                "planet_name",
                "target_name",
                "reason",
                "wait_eta",
                "duration_text",
                "total_eta",
                "eta_bottleneck",
            ):
                if isinstance(node.get(k), str) and node[k]:
                    node[k] = _scrub_api_str(node[k])
    for edge in graph.get("edges", ()):
        if isinstance(edge, dict) and isinstance(edge.get("label"), str) and edge["label"]:
            edge["label"] = _scrub_api_str(edge["label"])
    summary = graph.get("summary")
    if isinstance(summary, dict):
        for k in ("critical_path_eta", "primary_bottleneck"):
            if isinstance(summary.get(k), str) and summary[k]:
                summary[k] = _scrub_api_str(summary[k])
    if graph.get("ga_summary") is not None:
        graph["ga_summary"] = _redact_api_payload(graph["ga_summary"])
    return graph


def _safe_sig_eq(a: Any, b: Any) -> bool:
    if a is b:
        return True
    if a is None or b is None:
        return False
    try:
        return bool(a == b)
    except Exception:
        return False


def _summary_without_elapsed(summary: Any) -> Any:
    if isinstance(summary, Mapping):
        return {k: v for k, v in summary.items() if k != "elapsed_ms"}
    return summary


def _freeze_sig_tree(
    val: Any,
    _depth: int = 0,
    _seen: Optional[set[int]] = None,
) -> Any:
    tv = type(val)
    if val is None or tv is int or tv is str or tv is bytes:
        return val
    if tv is bool:
        return ("__bool__", val)
    if tv is float or isinstance(val, float):
        fval = float(val)
        if math.isnan(fval):
            return ("__nan__",)
        if math.isinf(fval):
            return ("__inf__", 1 if fval > 0 else -1)
        return fval
    if _depth >= 32:
        return repr(val)
    if tv is dict or isinstance(val, Mapping):
        seen = _seen if _seen is not None else set()
        vid = id(val)
        if vid in seen:
            return ("__cycle__",)
        seen.add(vid)
        try:
            return tuple(
                sorted(
                    (
                        (
                            k if type(k) is str else f"{type(k).__name__}:{k}",
                            _freeze_sig_tree(v, _depth + 1, seen),
                        )
                        for k, v in val.items()
                    ),
                    key=lambda kv: kv[0],
                )
            )
        finally:
            seen.discard(vid)
    if tv is list or tv is tuple or isinstance(val, (list, tuple)):
        seen = _seen if _seen is not None else set()
        vid = id(val)
        if vid in seen:
            return ("__cycle__",)
        seen.add(vid)
        try:
            return tuple(_freeze_sig_tree(x, _depth + 1, seen) for x in val)
        finally:
            seen.discard(vid)
    if isinstance(val, (set, frozenset)):
        seen = _seen if _seen is not None else set()
        vid = id(val)
        if vid in seen:
            return ("__cycle__",)
        seen.add(vid)
        try:
            return (
                "__set__",
                tuple(_freeze_sig_tree(x, _depth + 1, seen) for x in sorted(val, key=repr)),
            )
        finally:
            seen.discard(vid)
    return val


def _dashboard_state_sig(
    last_state: Optional[GameState],
    dash_state: Optional[GameState],
    rank_samples: Sequence[Any],
) -> Any:
    if rank_samples:
        first_map = next((s for s in rank_samples if isinstance(s, Mapping)), None)
        rank_sig: Any = (
            len(rank_samples),
            _freeze_sig_tree(rank_samples[0]),
            _freeze_sig_tree(first_map) if first_map is not rank_samples[0] else None,
            _freeze_sig_tree(rank_samples[-20:]),
        )
    else:
        rank_sig = (0,)
    if dash_state is None:
        return (None, rank_sig)
    return (
        id(last_state),
        dash_state.universe_id,
        dash_state.user_id,
        dash_state.nickname,
        _freeze_sig_tree(dash_state.timestamp),
        dash_state.active_planet_id,
        _freeze_sig_tree(dash_state.researches),
        _freeze_sig_tree(dash_state.research_names),
        _freeze_sig_tree(dash_state.specs),
        _freeze_sig_tree(dash_state.unread_messages),
        dash_state.tutorial,
        dash_state.active_queues,
        dash_state.fleet_alerts,
        dash_state.research_offers,
        _freeze_sig_tree(dash_state.speedup_factor),
        dash_state.newbie_protection,
        _freeze_sig_tree(dash_state.account_points),
        dash_state.account_rank,
        _freeze_sig_tree(dash_state.raid_targets or ()),
        _freeze_sig_tree(dash_state.raid_stats),
        tuple(
            (
                p.planet_id,
                p.name,
                p.coords,
                p.planet_type,
                p.temperature,
                p.fields_used,
                p.fields_max,
                p.resources,
                p.storage,
                p.energy,
                _freeze_sig_tree(p.buildings),
                _freeze_sig_tree(p.building_names),
                _freeze_sig_tree(p.ships),
                _freeze_sig_tree(p.defense),
                p.building_offers,
                p.ship_offers,
                p.defense_offers,
                _freeze_sig_tree(p.economy_rates),
            )
            for p in (dash_state.planets or ())
        ),
        rank_sig,
    )


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
    FLEET_SLOT_COOLDOWN_S = 180.0
    _FLEET_SLOT_BUSY_MARKERS: tuple[str, ...] = (
        "no ship input fields found on internalfleet",
        "no free fleet slot",
        "too many fleets",
        "fleeterr.toomanyfleets",
        "fleeterr.nofreeslot",
    )

    def __init__(
        self,
        *,
        debug_port: int = DEFAULT_DEBUG_PORT,
        env_file: Path = Path(".env"),
        account: str = "default",
        config_path: Optional[Path] = None,
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
        self._config_file_explicit = bool(
            config_path is not None and config_path != Path("config.yaml")
        )
        if config_path is not None:
            self.config_path = config_path
            self.strategy_config: StrategyConfig = load_strategy_config(config_path)
        elif log_dir is not None:
            self.config_path = Path("config.yaml")
            self.strategy_config = load_strategy_config(None)
        else:
            self.config_path = Path("config.yaml")
            self.strategy_config = StrategyConfig()
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
        self._pending_spy_sync: bool = False
        self._last_spy_dispatched_at: float = 0.0
        self._last_synced_unread_total: int = 0
        self._economy_rates: dict[int, dict[str, int]] = {}
        self._economy_sync_timestamps: dict[int, float] = {}
        self._scanned_systems: dict[str, float] = {}
        self._throttle_timestamps: dict[int, float] = {}
        self._fleet_slot_cooldown_until: dict[int, float] = {}
        self._target_blocks: dict[str, tuple[float, str]] = {}
        self._dash_cache_state_ref: Optional[GameState] = None
        self._dash_static_cache_key: Optional[Any] = None
        self._dash_static_cache_json: Optional[str] = None
        self._dash_ag_cache_key: Optional[Any] = None
        self._dash_ag_cache_json: Optional[str] = None
        resolved_db = (
            db_path
            if db_path is not None
            else ((log_dir.parent / "state" / "spaceinvasion.db") if log_dir is not None else None)
        )
        legacy_rank_path: Optional[Path] = (
            (resolved_db.parent / "rank_history.json")
            if (
                resolved_db is not None
                and str(resolved_db) != ":memory:"
                and (resolved_db.parent / "rank_history.json").is_file()
            )
            else None
        )
        self._rank_history_path: Optional[Path] = None
        self._db: Optional[GameDatabase] = (
            GameDatabase(resolved_db, legacy_rank_json=legacy_rank_path)
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
            self._rank_samples = []
            self._raid_targets = ()
        self._bot_control: dict[str, Any] = {
            "paused": False,
            "mode_override": None,
            "captcha_blocked": False,
            "captcha_message": None,
        }
        self._transmitter_state_mem: dict[str, Any] = {
            "last_activated_day": "",
            "last_checked_day": "",
            "last_activated_ts": 0.0,
            "last_checked_ts": 0.0,
            "free_spins_remaining": None,
            "last_reward": "",
            "total_activations": 0,
        }
        self._galaxy_sweep_mem: dict[str, Any] = {
            "cursor_galaxy": 1,
            "cursor_system": 1,
            "last_sweep_started_ts": 0.0,
            "last_sweep_completed_ts": 0.0,
            "next_sweep_due_ts": 0.0,
            "last_pages_scanned": 0,
            "last_players_found": 0,
            "last_colonies_found": 0,
            "last_debris_found": 0,
            "total_sweeps": 0,
            "total_pages_scanned": 0,
        }
        self._sweep_xhr_timestamps: Deque[float] = deque()
        self._sweep_run_lock = threading.Lock()
        self._sweep_in_progress = False
        self._sweep_stop_event = threading.Event()
        self._sweep_thread: Optional[threading.Thread] = None
        self._hydrate_persisted_bridge_state()
        enforce_hourly = max_actions_per_hour is not None
        raw_budget = (
            max_actions_per_hour
            if max_actions_per_hour is not None
            else self.strategy_config.max_actions_per_hour
        )
        budget = _safe_hourly_budget(raw_budget, 60)
        self._live_executor = ActionExecutor(
            dry_run=False,
            max_actions_per_hour=budget,
            enforce_hourly_cap=enforce_hourly,
            allow_fleetsave=self.strategy_config.allow_fleetsave,
            allow_raids=self.strategy_config.allow_raids,
            allow_colonize=self.strategy_config.allow_colonize,
            allow_transmitter=self.strategy_config.allow_transmitter,
            page_limiter=self._page_limiter,
            max_limiter_wait=self._executor_max_limiter_wait,
        )
        self._dry_executor = ActionExecutor(
            dry_run=True,
            max_actions_per_hour=budget,
            enforce_hourly_cap=enforce_hourly,
            allow_fleetsave=self.strategy_config.allow_fleetsave,
            allow_raids=self.strategy_config.allow_raids,
            allow_colonize=self.strategy_config.allow_colonize,
            allow_transmitter=self.strategy_config.allow_transmitter,
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
        self.stop_galaxy_sweep_worker()
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

    def _hydrate_persisted_bridge_state(self) -> None:
        if self._db is None:
            return
        load_cfg_fn = getattr(self._db, "load_strategy_config_snapshot", None)
        save_cfg_fn = getattr(self._db, "save_strategy_config_snapshot", None)
        if not (self._config_file_explicit and self.config_path.exists()) and callable(load_cfg_fn):
            saved_cfg = load_cfg_fn()
            if isinstance(saved_cfg, Mapping) and saved_cfg:
                try:
                    self.strategy_config = strategy_config_from_dict(dict(saved_cfg))
                except Exception:
                    pass
        if callable(save_cfg_fn):
            save_cfg_fn(asdict(self.strategy_config))

        load_rt_fn = getattr(self._db, "load_runtime_state", None)
        rt = load_rt_fn() if callable(load_rt_fn) else {}
        if isinstance(rt, Mapping) and rt:
            bc = rt.get("bot_control")
            if isinstance(bc, Mapping):
                self._bot_control["paused"] = bool(bc.get("paused", False))
                raw_mo = bc.get("mode_override")
                self._bot_control["mode_override"] = (
                    raw_mo if raw_mo in (None, "live", "dry-run") else None
                )
                self._bot_control["captcha_blocked"] = bool(bc.get("captcha_blocked", False))
                raw_cm = bc.get("captcha_message")
                self._bot_control["captcha_message"] = (
                    _bounded_api_str(raw_cm, default="") if raw_cm else None
                )
            econ_ts = rt.get("economy_sync_timestamps")
            if isinstance(econ_ts, Mapping):
                for raw_pid, raw_ts in econ_ts.items():
                    pid_i = _safe_api_int(raw_pid, 0)
                    ts_f = _safe_api_float(raw_ts, 0.0)
                    if pid_i > 0 and ts_f > 0.0:
                        self._economy_sync_timestamps[pid_i] = ts_f
            self._last_synced_unread_total = max(
                0, _safe_api_int(rt.get("last_synced_unread_total", 0), 0)
            )
            self._pending_spy_sync = bool(rt.get("pending_spy_sync", False))
            if isinstance(rt.get("last_url"), str) and rt["last_url"]:
                self._last_url = _bounded_api_str(rt["last_url"], default="")
            if isinstance(rt.get("last_optimizer_summary"), Mapping):
                self._last_optimizer_summary = dict(rt["last_optimizer_summary"])

            now_wall = time.time()
            now_mono = time.monotonic()
            fsc_wall = rt.get("fleet_slot_cooldown_until_wall")
            if isinstance(fsc_wall, Mapping):
                for raw_pid, raw_until in fsc_wall.items():
                    pid_i = _safe_api_int(raw_pid, 0)
                    until_w = _safe_api_float(raw_until, 0.0)
                    rem = until_w - now_wall
                    if pid_i > 0 and 0.0 < rem <= 86400.0:
                        self._fleet_slot_cooldown_until[pid_i] = now_mono + rem

            def _wall_to_mono(wall_key: str) -> float:
                wv = _safe_api_float(rt.get(wall_key, 0.0), 0.0)
                if wv <= 0.0:
                    return 0.0
                age = now_wall - wv
                if 0.0 <= age <= 86400.0:
                    return max(0.001, now_mono - age)
                return 0.0

            self._last_spio_sync_at = _wall_to_mono("spio_sync_at_wall")
            self._last_research_sync_at = _wall_to_mono("research_sync_at_wall")
            self._last_shipyard_sync_at = _wall_to_mono("shipyard_sync_at_wall")
            self._last_spy_dispatched_at = _wall_to_mono("spy_dispatched_at_wall")

        load_gs_fn = getattr(self._db, "load_latest_game_state", None)
        if callable(load_gs_fn):
            persisted_gs = load_gs_fn()
            if isinstance(persisted_gs, GameState):
                self._last_state = self._merge_economy_rates(persisted_gs)
                self._last_recorded_state_obj = self._last_state
                self._last_snapshot_at = -1e9
                self._active_planet_confirmed = False
                self._last_home_fetched = False
                ap0 = self._last_state.active_planet()
                self._last_buildings_fetched = bool(ap0 is not None and ap0.building_offers)

    def _persist_runtime_state(self) -> None:
        if self._db is None:
            return
        save_rt_fn = getattr(self._db, "save_runtime_state", None)
        if not callable(save_rt_fn):
            return
        now_wall = time.time()
        now_mono = time.monotonic()

        def _mono_to_wall(mono_val: float) -> float:
            if not isinstance(mono_val, (int, float)) or not math.isfinite(mono_val) or mono_val <= 0.0:
                return 0.0
            age = max(0.0, now_mono - float(mono_val))
            return round(now_wall - age, 3)

        fsc_wall: dict[str, float] = {}
        for pid, until_m in list(self._fleet_slot_cooldown_until.items()):
            if isinstance(until_m, (int, float)) and math.isfinite(until_m):
                rem = float(until_m) - now_mono
                if rem > 0.0:
                    fsc_wall[str(int(pid))] = round(now_wall + rem, 3)

        payload: dict[str, Any] = {
            "bot_control": dict(self._bot_control),
            "economy_sync_timestamps": {
                str(int(k)): round(float(v), 3)
                for k, v in self._economy_sync_timestamps.items()
                if isinstance(v, (int, float)) and math.isfinite(v) and v > 0.0
            },
            "fleet_slot_cooldown_until_wall": fsc_wall,
            "last_synced_unread_total": max(0, int(self._last_synced_unread_total)),
            "pending_spy_sync": bool(self._pending_spy_sync),
            "spio_sync_at_wall": _mono_to_wall(self._last_spio_sync_at),
            "research_sync_at_wall": _mono_to_wall(self._last_research_sync_at),
            "shipyard_sync_at_wall": _mono_to_wall(self._last_shipyard_sync_at),
            "spy_dispatched_at_wall": _mono_to_wall(self._last_spy_dispatched_at),
            "last_url": _bounded_api_str(self._last_url, default="") if self._last_url else None,
            "last_optimizer_summary": self._last_optimizer_summary,
        }
        save_rt_fn(payload, ts=now_wall)

    def _record_antibot_pause(self, exc: BaseException) -> None:
        msg = _bounded_api_str(exc, default="CAPTCHA / anti-bot challenge detected.")
        if not any(
            tok in msg.lower()
            for tok in ("captcha", "anti-bot", "antibot", "turnstile", "sicherheitspr")
        ):
            msg = f"CAPTCHA / anti-bot challenge: {msg}" if msg else "CAPTCHA / anti-bot challenge detected."
        self._bot_control["paused"] = True
        self._bot_control["captcha_blocked"] = True
        self._bot_control["captcha_message"] = msg
        if self._db is not None and hasattr(self._db, "create_or_bump_notification"):
            try:
                self._db.create_or_bump_notification(
                    title="Anti-Bot / CAPTCHA Verification Required",
                    message=msg,
                    help_prompt=(
                        "Automation paused immediately for safety. Please complete the challenge manually in Chrome "
                        "(port 9222), then click Resume Auto or restart ./starter.sh."
                    ),
                    severity="error",
                    category="antibot",
                    action_kind="SECURITY_CHECK",
                    dedupe_key="antibot:captcha_challenge",
                )
            except Exception:
                pass
        self._persist_runtime_state()

    def _get_notifications_summary_locked(self, *, limit: int = 30) -> dict[str, Any]:
        if self._db is None or not hasattr(self._db, "get_notifications"):
            return {"unread_count": 0, "items": []}
        items = self._db.get_notifications(status="active", limit=limit)
        unread_cnt = self._db.unread_notification_count()
        return {
            "unread_count": int(unread_cnt),
            "items": _redact_api_payload(items),
        }

    def _record_action_validation_notification_locked(
        self,
        cmd: str,
        res: Any,
    ) -> Optional[dict[str, Any]]:
        if self._db is None or bool(getattr(res, "dry_run", False)):
            return None
        create_fn = getattr(self._db, "create_or_bump_notification", None)
        if not callable(create_fn):
            return None
        executed = bool(getattr(res, "executed", False))
        validated = bool(getattr(res, "validated", True))
        if executed and validated:
            return None
        msg_s = _bounded_api_str(getattr(res, "message", ""), default="")
        msg_l = msg_s.lower()
        # Skip benign non-errors (fleet slot capacity waits, no-ops, hourly caps)
        if (
            msg_l.startswith("no-op:")
            or "hourly action rate cap" in msg_l
            or "no free fleet slot" in msg_l
            or "too many fleets" in msg_l
            or any(m in msg_l for m in self._FLEET_SLOT_BUSY_MARKERS)
        ):
            return None
        act = getattr(res, "action", None)
        kind_s = _bounded_api_str(getattr(act, "kind", cmd), default=cmd).upper()
        pcoords_s = _bounded_api_str(getattr(act, "planet_coords", ""), default="")
        tname_s = _bounded_api_str(getattr(act, "target_name", ""), default=kind_s)
        val_err = _bounded_api_str(getattr(res, "validation_error", None) or "", default="")
        is_prot = any(
            kw in f"{msg_l} {val_err.lower()}"
            for kw in ("can't be attacked", "cannot be attacked", "nicht angegriffen", "vacation", "protection", "attack zone")
        )
        if is_prot:
            severity = "warning"
            category = "fleet_protection"
            title = f"{kind_s} blocked: Target protected ({tname_s})"
            help_prompt = (
                f"The game server rejected {kind_s} on {tname_s} ('{val_err or msg_s}'). "
                "A 6-hour target cooldown was recorded automatically so the bot stops retrying this target. "
                "Please check the target in Chrome (:9222) if you pinned it manually, or acknowledge this alert."
            )
            dkey = f"prot:{kind_s}:{tname_s}"
        elif executed and not validated:
            severity = "error"
            category = "action_validation"
            title = f"Validation failed after {kind_s}: {tname_s}"
            help_prompt = (
                f"{val_err or msg_s} Please inspect [{pcoords_s or 'active colony'}] in Chrome (:9222) "
                "to verify whether the action took effect or needs manual intervention."
            )
            dkey = f"valfail:{kind_s}:{pcoords_s}:{tname_s}"
        else:
            severity = "warning"
            category = "action_aborted"
            title = f"{kind_s} could not execute: {tname_s}"
            help_prompt = (
                f"{msg_s} Please check [{pcoords_s or 'active colony'}] in Chrome (:9222) if operator action is needed, "
                "or acknowledge to dismiss."
            )
            dkey = f"abort:{kind_s}:{pcoords_s}:{tname_s}"
        try:
            return create_fn(
                title=title,
                message=msg_s or val_err or "Action did not pass post-execution validation.",
                help_prompt=help_prompt,
                severity=severity,
                category=category,
                action_kind=kind_s,
                planet_coords=pcoords_s,
                target_name=tname_s,
                dedupe_key=dkey,
                details={
                    "command": cmd,
                    "executed": executed,
                    "validated": validated,
                    "validation_error": val_err or None,
                },
            )
        except Exception:
            return None

    def _get_scanned_systems(self) -> dict[str, float]:
        if self._db is not None:
            return self._db.get_galaxy_scans()
        return dict(self._scanned_systems)

    def _get_throttle_cooldowns(self) -> dict[int, float]:
        if self._db is not None:
            return self._db.get_last_throttle_timestamps()
        return dict(self._throttle_timestamps)

    def _get_fleet_slot_cooldowns(self) -> dict[str, float]:
        now_m = time.monotonic()
        active: dict[str, float] = {}
        expired: list[int] = []
        for pid, until_m in list(self._fleet_slot_cooldown_until.items()):
            if not isinstance(until_m, (int, float)) or not math.isfinite(until_m):
                expired.append(pid)
                continue
            rem = float(until_m) - now_m
            if rem > 0.0:
                active[str(int(pid))] = round(rem, 2)
            else:
                expired.append(pid)
        for pid in expired:
            self._fleet_slot_cooldown_until.pop(pid, None)
        return active

    def _apply_fleet_slot_cooldowns(
        self,
        actions: list[PlannedAction],
        *,
        re_sort_priority: bool = False,
    ) -> list[PlannedAction]:
        active_cd = self._get_fleet_slot_cooldowns()
        if not active_cd or not actions:
            return actions
        busy_pids = {int(k) for k in active_cd}
        hold_suffix = " [Hold: fleet slots busy / returning]"
        changed = False
        out: list[PlannedAction] = []
        for act in actions:
            if act.kind in ("ATTACK", "SPY", "RECYCLE", "ESCAPE_FLIGHT", "COLONIZE") and act.planet_id in busy_pids:
                cur_reason = str(act.reason or "")
                new_reason = (
                    cur_reason
                    if "[Hold: fleet slots busy / returning]" in cur_reason
                    else f"{cur_reason}{hold_suffix}"
                )
                if act.executable_now or new_reason != cur_reason:
                    changed = True
                    out.append(replace(act, executable_now=False, reason=new_reason))
                    continue
            out.append(act)
        if changed and re_sort_priority:
            prio_rank = {"P0": 0, "P1": 1, "P2": 2, "P3": 3}
            out.sort(
                key=lambda a: (
                    prio_rank.get(a.priority, 9),
                    -1 if a.kind == "ALERT_HOSTILE" else (0 if a.executable_now else 1),
                )
            )
        return out

    def _get_transmitter_status_locked(self, *, now_ts: Optional[float] = None) -> dict[str, Any]:
        eff_now = _safe_api_float(now_ts, time.time()) if now_ts is not None else time.time()
        if self._db is not None and hasattr(self._db, "get_transmitter_status"):
            return dict(self._db.get_transmitter_status(now_ts=eff_now))
        today = server_day_key(eff_now)
        mem = self._transmitter_state_mem
        last_act_day = str(mem.get("last_activated_day") or "")
        last_chk_day = str(mem.get("last_checked_day") or "")
        free_rem = mem.get("free_spins_remaining")
        already_done = (last_act_day == today) or (last_chk_day == today and free_rem == 0)
        return {
            "today_day_key": today,
            "due": not already_done,
            "last_activated_day": last_act_day,
            "last_checked_day": last_chk_day,
            "last_activated_ts": _safe_api_float(mem.get("last_activated_ts"), 0.0),
            "last_checked_ts": _safe_api_float(mem.get("last_checked_ts"), 0.0),
            "free_spins_remaining": free_rem,
            "last_reward": str(mem.get("last_reward") or ""),
            "total_activations": _safe_api_int(mem.get("total_activations"), 0),
        }

    def _record_transmitter_state_locked(
        self,
        *,
        activated: bool,
        free_spins_remaining: Optional[int] = None,
        reward: str = "",
        ts: Optional[float] = None,
    ) -> dict[str, Any]:
        eff_now = round(_safe_api_float(ts, time.time()) if ts is not None else time.time(), 3)
        if self._db is not None and hasattr(self._db, "record_transmitter_state"):
            return dict(
                self._db.record_transmitter_state(
                    activated=activated,
                    free_spins_remaining=free_spins_remaining,
                    reward=reward,
                    ts=eff_now,
                )
            )
        today = server_day_key(eff_now)
        mem = self._transmitter_state_mem
        if activated:
            mem["last_activated_day"] = today
            mem["last_activated_ts"] = eff_now
            mem["total_activations"] = _safe_api_int(mem.get("total_activations"), 0) + 1
        mem["last_checked_day"] = today
        mem["last_checked_ts"] = eff_now
        if free_spins_remaining is not None:
            mem["free_spins_remaining"] = max(0, _safe_api_int(free_spins_remaining, 0))
        if reward:
            mem["last_reward"] = _bounded_api_str(reward, default="")[:160]
        return self._get_transmitter_status_locked(now_ts=eff_now)

    def _get_galaxy_sweep_state_locked(self, *, now_ts: Optional[float] = None) -> dict[str, Any]:
        eff_now = _safe_api_float(now_ts, time.time()) if now_ts is not None else time.time()
        if self._db is not None and hasattr(self._db, "get_galaxy_sweep_state"):
            st = dict(self._db.get_galaxy_sweep_state(now_ts=eff_now))
        else:
            st = dict(self._galaxy_sweep_mem)
            st["due"] = eff_now >= _safe_api_float(st.get("next_sweep_due_ts"), 0.0)
        st["in_progress"] = bool(self._sweep_in_progress)
        return st

    def _save_galaxy_sweep_state_locked(
        self,
        updates: Mapping[str, Any],
        *,
        ts: Optional[float] = None,
    ) -> dict[str, Any]:
        eff_now = round(_safe_api_float(ts, time.time()) if ts is not None else time.time(), 3)
        if self._db is not None and hasattr(self._db, "save_galaxy_sweep_state"):
            st = dict(self._db.save_galaxy_sweep_state(updates, ts=eff_now))
        else:
            for k, v in updates.items():
                if k == "due":
                    continue
                self._galaxy_sweep_mem[str(k)] = v
            st = dict(self._galaxy_sweep_mem)
            st["due"] = eff_now >= _safe_api_float(st.get("next_sweep_due_ts"), 0.0)
        st["in_progress"] = bool(self._sweep_in_progress)
        return st

    def _sweep_views_last_60s(self) -> int:
        cutoff = time.monotonic() - 60.0
        while self._sweep_xhr_timestamps and self._sweep_xhr_timestamps[0] <= cutoff:
            self._sweep_xhr_timestamps.popleft()
        return len(self._sweep_xhr_timestamps)

    def _merge_colony_candidates_if_needed(
        self,
        state: GameState,
        cfg: StrategyConfig,
    ) -> GameState:
        if self._db is None:
            return state
        max_p = max(1, _safe_api_int(getattr(cfg, "max_planets", 11), 11))
        allocated_normal = count_allocated_normal_planets(state)
        if allocated_normal >= max_p:
            return state
        if not (
            bool(getattr(cfg, "allow_colonize", False))
            or _tutorial_colonize_task(state) is not None
        ):
            return state
        get_col_fn = getattr(self._db, "get_colony_candidates", None)
        if not callable(get_col_fn):
            return state
        ap = state.active_planet()
        origin_c = ap.coords if ap is not None else ""
        min_f = max(50, _safe_api_int(getattr(cfg, "min_colony_fields", 300), 300))
        col_cands = get_col_fn(
            origin_coords=origin_c,
            min_fields_target=min_f,
            exclude_coords=self._own_planet_coords(state),
            limit=120,
        )
        if not col_cands:
            return state
        existing_rt = list(getattr(state, "raid_targets", ()) or ())
        by_c: dict[str, dict[str, Any]] = {}
        ordered_rt: list[dict[str, Any]] = []
        for r_item in existing_rt:
            if not isinstance(r_item, Mapping):
                continue
            d_item = dict(r_item)
            nc = normalize_coords(d_item.get("coords") or d_item.get("target_coords") or "")
            if nc:
                by_c[nc] = d_item
            ordered_rt.append(d_item)
        added_any = False
        for cand in col_cands:
            if not isinstance(cand, Mapping):
                continue
            nc = normalize_coords(cand.get("coords") or "")
            if not nc:
                continue
            if nc in by_c:
                cur_entry = by_c[nc]
                uf = cand.get("uninhabited_fields")
                if uf is not None and cur_entry.get("uninhabited_fields") is None:
                    cur_entry["uninhabited_fields"] = _safe_api_int(uf, 0)
                    added_any = True
                continue
            free_entry = {
                "coords": nc,
                "owner": "",
                "planet_name": "",
                "status_color": "free",
                "is_inactive": False,
                "is_spacia": False,
                "has_debris": False,
                "points": None,
                "uninhabited_fields": cand.get("uninhabited_fields"),
                "last_scanned_ts": _safe_api_float(cand.get("last_scanned_ts", 0.0)),
                "last_spied_ts": _safe_api_float(cand.get("last_spied_ts", 0.0)),
                "has_spy_intel": bool(cand.get("probed", False)),
            }
            by_c[nc] = free_entry
            ordered_rt.append(free_entry)
            added_any = True
        return replace(state, raid_targets=tuple(ordered_rt)) if added_any else state

    def _attach_raid_stats(self, state: GameState) -> GameState:
        existing_rs = getattr(state, "raid_stats", None)
        base_rs: dict[str, Any] = dict(existing_rs) if isinstance(existing_rs, Mapping) else {}
        if self._db is not None:
            get_stats = getattr(self._db, "get_raid_stats", None)
            if callable(get_stats):
                stats = get_stats()
                if isinstance(stats, Mapping):
                    base_rs.update(stats)
            base_rs["transmitter_status"] = self._get_transmitter_status_locked()
        elif bool(getattr(self.strategy_config, "allow_transmitter", False)):
            base_rs["transmitter_status"] = self._get_transmitter_status_locked()
        enriched = replace(state, raid_stats=base_rs) if base_rs != existing_rs else state
        return self._merge_colony_candidates_if_needed(enriched, self.strategy_config)

    def _invalidate_dashboard_cache(self) -> None:
        self._dash_cache_state_ref = None
        self._dash_static_cache_key = None
        self._dash_static_cache_json = None
        self._dash_ag_cache_key = None
        self._dash_ag_cache_json = None

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
        return _sanitize_finite_json(raw)

    def _plan_with_summary(
        self,
        state: GameState,
        *,
        config: Optional[StrategyConfig] = None,
        record_summary: bool = True,
        _already_enriched: bool = False,
    ) -> tuple[list[PlannedAction], Optional[dict[str, Any]]]:
        cfg = config or self.strategy_config
        enriched_state = state if _already_enriched else self._attach_raid_stats(state)
        if config is not None:
            enriched_state = self._merge_colony_candidates_if_needed(enriched_state, cfg)
        eff_mode = normalize_planner_mode(getattr(cfg, "planner_mode", "priority"))
        if eff_mode == "ga_nn":
            if not record_summary:
                raid_stats_kw: dict[str, Any] = {}
                state_raid_stats = getattr(enriched_state, "raid_stats", None)
                if isinstance(state_raid_stats, Mapping) and state_raid_stats:
                    raid_stats_kw["raid_stats"] = state_raid_stats
                opt_res = run_optimizer(
                    enriched_state,
                    storage_warn_ratio=cfg.storage_warn_ratio,
                    config=cfg,
                    scanned_systems=self._get_scanned_systems(),
                    throttle_cooldowns=self._get_throttle_cooldowns(),
                    record_global_summary=False,
                    **raid_stats_kw,
                )
                actions = attach_action_etas(enriched_state, list(opt_res.actions))
                summary = _sanitize_optimizer_summary(opt_res.to_dict())
                actions = self._apply_fleet_slot_cooldowns(actions, re_sort_priority=False)
                return self._apply_manual_queue_to_plan(
                    enriched_state,
                    actions,
                    cfg=cfg,
                    persist_auto_satisfied=False,
                ), summary
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
                if _summary_without_elapsed(summary) != _summary_without_elapsed(
                    self._last_optimizer_summary
                ):
                    self._last_optimizer_summary = summary
                    self._persist_runtime_state()
                else:
                    self._last_optimizer_summary = summary
            actions = self._apply_fleet_slot_cooldowns(actions, re_sort_priority=False)
            return self._apply_manual_queue_to_plan(
                enriched_state,
                actions,
                cfg=cfg,
                persist_auto_satisfied=bool(record_summary and config is None),
            ), summary
        actions = plan_actions(
            enriched_state,
            config=cfg,
            scanned_systems=self._get_scanned_systems(),
            throttle_cooldowns=self._get_throttle_cooldowns(),
        )
        actions = self._apply_fleet_slot_cooldowns(actions, re_sort_priority=True)
        return self._apply_manual_queue_to_plan(
            enriched_state,
            actions,
            cfg=cfg,
            persist_auto_satisfied=bool(record_summary and config is None),
        ), None

    def _apply_manual_queue_to_plan(
        self,
        state: GameState,
        actions: list[PlannedAction],
        *,
        cfg: StrategyConfig,
        persist_auto_satisfied: bool = True,
    ) -> list[PlannedAction]:
        if self._db is None:
            return actions
        get_mq = getattr(self._db, "get_manual_queue", None)
        if not callable(get_mq):
            return actions
        pending_mq = get_mq(status="pending", limit=200)
        if not pending_mq:
            return actions
        merged, auto_done_ids = merge_manual_queue_actions(
            state,
            actions,
            pending_mq,
            config=cfg,
        )
        if auto_done_ids and persist_auto_satisfied:
            comp_fn = getattr(self._db, "complete_manual_queue_item", None)
            if callable(comp_fn):
                for qid in auto_done_ids:
                    comp_fn(qid, message="Auto-satisfied by current empire state")
        return merged

    def _review_queue_metal_status_locked(
        self,
        planet_id: int,
        pending_mq: Sequence[Mapping[str, Any]],
    ) -> dict[str, Any]:
        """Inspect Blast furnace Metal production vs pinned queue position on `planet_id`."""
        if self._last_state is None or not pending_mq:
            return {"checked": False, "summary": ""}
        enriched = self._attach_raid_stats(self._last_state)
        planet = (
            next((p for p in enriched.planets if p.planet_id == planet_id), None)
            if planet_id > 0
            else enriched.active_planet()
        )
        if planet is None:
            return {"checked": False, "summary": ""}
        b2_lvl = planet.building_level(2)
        cur_b2_pct = max(0, min(100, _safe_api_int((planet.economy_rates or {}).get("resource2", 100), 100)))
        merged, _ = merge_manual_queue_actions(
            enriched,
            [],
            pending_mq,
            config=self.strategy_config,
        )
        p_acts = [a for a in merged if a.planet_id == planet.planet_id]
        bf_throttle = next(
            (
                a
                for a in p_acts
                if a.manual_queue_id is None and a.kind == "THROTTLE_BUILDING" and a.target_id == 2
            ),
            None,
        )
        deferred_metal = next(
            (
                a
                for a in p_acts
                if a.manual_queue_id is not None and "[Metal production queued after" in str(a.reason or "")
            ),
            None,
        )
        pinned_needs_metal = any(
            a.manual_queue_id is not None and _safe_api_int(a.missing.metal, 0) > 0
            for a in p_acts
        )
        summary_txt = ""
        scheduled_pct: Optional[int] = None
        if bf_throttle is not None:
            scheduled_pct = _safe_api_int(bf_throttle.target_level, cur_b2_pct)
            if scheduled_pct > 0:
                summary_txt = f"Metal production scheduled: Blast furnace {cur_b2_pct}% -> {scheduled_pct}%"
            else:
                summary_txt = f"Blast furnace held at 0% until earlier Pig iron item completes"
        elif deferred_metal is not None:
            summary_txt = "Metal production queued after earlier Pig iron item completes"
        elif pinned_needs_metal:
            if b2_lvl <= 0:
                summary_txt = "Warning: pinned item needs Metal, and Colony has no Blast furnace (b2=0)"
            elif cur_b2_pct > 0:
                summary_txt = f"Metal production active (Blast furnace {cur_b2_pct}%)"
        return {
            "checked": True,
            "planet_id": planet.planet_id,
            "planet_coords": planet.coords,
            "blast_furnace_level": b2_lvl,
            "current_blast_furnace_pct": cur_b2_pct,
            "scheduled_blast_furnace_pct": scheduled_pct,
            "pinned_needs_metal": pinned_needs_metal,
            "summary": summary_txt,
        }

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
                        except AntiBotChallengeError as exc:
                            self._record_antibot_pause(exc)
                            in_game = False
                        except SpaceInvasionAuthError:
                            in_game = False
                        current_url = str(getattr(client.driver, "current_url", "") or "")
                except RateLimitError as exc:
                    self._record_server_rate_limit(exc)
                    in_game = port_open
                except AntiBotChallengeError as exc:
                    self._record_antibot_pause(exc)
                    in_game = False
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
                        "action_batch_interval_seconds": self.strategy_config.action_batch_interval_seconds,
                        "remaining_hourly_actions": self._live_executor.remaining_hourly_budget(),
                        "bot_control": dict(self._bot_control),
                    }
                )
            )

    def get_bot_control(self) -> dict[str, Any]:
        with self._lock:
            metal_prod = {
                str(pid): _safe_api_int(rates.get("resource2", 100), 100) > 0
                for pid, rates in self._economy_rates.items()
                if isinstance(rates, Mapping)
            }
            return _sanitize_finite_json(
                _redact_api_payload(
                    {
                        "ok": True,
                        **dict(self._bot_control),
                        "scanned_systems": self._get_scanned_systems(),
                        "throttle_cooldowns": self._get_throttle_cooldowns(),
                        "fleet_slot_cooldowns": self._get_fleet_slot_cooldowns(),
                        "economy_sync_timestamps": dict(self._economy_sync_timestamps),
                        "metal_production_by_planet": metal_prod,
                        "manual_queue": (
                            self._db.get_manual_queue(status="pending", limit=200)
                            if self._db is not None and hasattr(self._db, "get_manual_queue")
                            else []
                        ),
                        "transmitter_status": self._get_transmitter_status_locked(),
                        "galaxy_sweep": self._get_galaxy_sweep_state_locked(),
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
                from spaceinvasion.core import game_data
                from spaceinvasion.planner.strategy import _resources_from_dict

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
            if coords and coords in self._target_blocks:
                mem_until, mem_reason = self._target_blocks[coords]
                if mem_until > _safe_api_float(item.get("blocked_until_ts", 0.0)):
                    item["blocked_until_ts"] = round(float(mem_until), 3)
                    item["block_reason"] = _bounded_api_str(mem_reason, default="")
            item["has_debris"] = bool(item.get("has_debris", False))
            item["blocked_until_ts"] = _safe_api_float(item.get("blocked_until_ts", 0.0))
            item["block_reason"] = _bounded_api_str(item.get("block_reason") or "", default="")
            item["bash_hits"] = game_data.cap_json_int(_safe_api_int(item.get("bash_hits", 0)))
            item["last_blocked_ts"] = _safe_api_float(item.get("last_blocked_ts", 0.0))
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
        live_exec = getattr(self, "_live_executor", None)
        for pid, rates in getattr(live_exec, "last_economy_rates", {}).items():
            if rates:
                self._economy_rates[int(pid)] = dict(rates)

        changed = False
        updated_planets: list[PlanetState] = []
        for planet in state.planets:
            if planet.economy_rates:
                prev_cached = self._economy_rates.get(planet.planet_id)
                new_rates = dict(planet.economy_rates)
                self._economy_rates[planet.planet_id] = new_rates
                if self._db is not None and prev_cached != new_rates:
                    self._db.save_economy_rates(planet.planet_id, planet.coords, new_rates)
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
        self._invalidate_dashboard_cache()
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
            self._db.record_full_state_snapshot(
                merged,
                (),
                source="bridge",
                min_interval_seconds=600.0,
            )
            self._persist_runtime_state()
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
            if port_open and rate_rem <= 0.0 and (refresh or self._last_state is None or self._last_snapshot_at < 0.0):
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
                except AntiBotChallengeError as exc:
                    self._record_antibot_pause(exc)
                    in_game = False
                except SpaceInvasionAuthError:
                    in_game = False

            dash_state = (
                self._attach_raid_stats(self._last_state)
                if self._last_state is not None
                else None
            )
            eff_mode = normalize_planner_mode(self.strategy_config.planner_mode)
            if dash_state is not None:
                actions, opt_summary = self._plan_with_summary(dash_state, _already_enriched=True)
            else:
                actions = []
                opt_summary = self._last_optimizer_summary if eff_mode == "ga_nn" else None

            dash_sig = _dashboard_state_sig(self._last_state, dash_state, self._rank_samples)
            cfg_raw = asdict(self.strategy_config)
            cfg_sig = _freeze_sig_tree(cfg_raw)
            static_key = (
                dash_sig,
                cfg_sig,
                build_ranking_evolution,
                build_buildings_eta,
                build_combat_overview,
            )
            if self._dash_static_cache_json is None or not _safe_sig_eq(
                self._dash_static_cache_key, static_key
            ):
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
                static_clean = _sanitize_finite_json(
                    {
                        "config": cfg_raw,
                        "state": asdict(dash_state) if dash_state else None,
                        "buildings_eta": buildings_eta,
                        "combat": combat,
                        "ranking": ranking,
                    }
                )
                self._dash_cache_state_ref = self._last_state
                self._dash_static_cache_key = static_key
                self._dash_static_cache_json = json.dumps(static_clean, allow_nan=False)
            static_val = json.loads(self._dash_static_cache_json)

            manual_queue_pending = (
                self._db.get_manual_queue(status="pending", limit=200)
                if self._db is not None and hasattr(self._db, "get_manual_queue")
                else []
            )
            ag_opt = opt_summary if eff_mode == "ga_nn" else None
            ag_opt_sig = _freeze_sig_tree(_summary_without_elapsed(ag_opt))
            mq_sig = _freeze_sig_tree(manual_queue_pending)
            ag_key = (
                dash_sig,
                cfg_sig,
                eff_mode,
                ag_opt_sig,
                mq_sig,
                tuple(actions),
                build_action_graph,
            )
            if self._dash_ag_cache_json is None or not _safe_sig_eq(
                self._dash_ag_cache_key, ag_key
            ):
                plan_json = [self._serialize_action(a) for a in actions]
                raw_ag = build_action_graph(
                    dash_state,
                    actions,
                    effective_config=self.strategy_config,
                    active_config=self.strategy_config,
                    simulated=False,
                    optimizer_summary=ag_opt,
                    sanitize=False,
                )
                ag_json = _sanitize_finite_json(_redact_action_graph_strings(raw_ag))
                ag_clean = {
                    "plan": plan_json,
                    "action_graph": ag_json,
                }
                self._dash_cache_state_ref = self._last_state
                self._dash_ag_cache_key = ag_key
                self._dash_ag_cache_json = json.dumps(ag_clean, allow_nan=False)
            ag_val = json.loads(self._dash_ag_cache_json)

            rate_rem = self._page_limiter.rate_limit_remaining()
            ui_assets_clean = _sanitize_finite_json(compute_ui_assets_state())
            envelope = _sanitize_finite_json(
                {
                    "ok": True,
                    "server_boot_id": _SERVER_BOOT_ID,
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
                        "action_batch_interval_seconds": self.strategy_config.action_batch_interval_seconds,
                        "remaining_hourly_actions": self._live_executor.remaining_hourly_budget(),
                    },
                    "bot_control": dict(self._bot_control),
                    "planner_mode": eff_mode,
                    "optimizer_summary": ag_opt,
                    "manual_queue": manual_queue_pending,
                    "notifications": self._get_notifications_summary_locked(),
                    "transmitter_status": self._get_transmitter_status_locked(),
                    "galaxy_sweep": self._get_galaxy_sweep_state_locked(),
                    "db_summary": self._db.summary() if self._db is not None else None,
                    "logs": {
                        "commander": self._read_log_tail("commander.log"),
                        "game_api": self._read_log_tail("game_api.log"),
                    },
                }
            )
            env_opt = envelope.get("optimizer_summary")
            ag_obj = ag_val.get("action_graph")
            if (
                isinstance(env_opt, dict)
                and "elapsed_ms" in env_opt
                and isinstance(ag_obj, dict)
                and isinstance(ag_obj.get("ga_summary"), dict)
            ):
                ag_obj["ga_summary"]["elapsed_ms"] = env_opt["elapsed_ms"]
            return {
                "ok": envelope["ok"],
                "server_boot_id": envelope["server_boot_id"],
                **(ui_assets_clean if isinstance(ui_assets_clean, dict) else {}),
                "health": envelope["health"],
                "bot_control": envelope["bot_control"],
                "planner_mode": envelope["planner_mode"],
                "config": static_val["config"],
                "state": static_val["state"],
                "plan": ag_val["plan"],
                "action_graph": ag_val["action_graph"],
                "manual_queue": envelope["manual_queue"],
                "notifications": envelope["notifications"],
                "transmitter_status": envelope["transmitter_status"],
                "galaxy_sweep": envelope["galaxy_sweep"],
                "optimizer_summary": envelope["optimizer_summary"],
                "buildings_eta": static_val["buildings_eta"],
                "combat": static_val["combat"],
                "ranking": static_val["ranking"],
                "db_summary": envelope["db_summary"],
                "logs": envelope["logs"],
            }

    get_dashboard = dashboard

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

            batch_iv = max(5.0, float(self.strategy_config.action_batch_interval_seconds))
            has_cached_res = bool(self._last_state is not None and self._last_state.research_offers)
            has_cached_ships = bool(
                self._last_state is not None
                and self._last_state.active_planet() is not None
                and self._last_state.active_planet().ship_offers
            )
            eff_res_ttl = max(self.RESEARCH_SYNC_TTL_S, batch_iv * 3.0) if has_cached_res else self.RESEARCH_SYNC_TTL_S
            eff_ship_ttl = max(self.SHIPYARD_SYNC_TTL_S, batch_iv * 3.0) if has_cached_ships else self.SHIPYARD_SYNC_TTL_S
            research_due = self._last_research_sync_at <= 0.0 or (now - self._last_research_sync_at) >= eff_res_ttl
            shipyard_due = self._last_shipyard_sync_at <= 0.0 or (now - self._last_shipyard_sync_at) >= eff_ship_ttl
            cold_boot = self._last_state is None or self._last_snapshot_at < 0.0
            need_research = bool(
                include_buildings
                and (
                    (cold_boot and not has_cached_res)
                    or (self.strategy_config.allow_research and research_due)
                )
            )
            need_shipyard = bool(
                include_buildings
                and (
                    (cold_boot and not has_cached_ships)
                    or (self.strategy_config.allow_shipyard and shipyard_due)
                )
            )
            if self._last_state is not None and include_buildings:
                prev_active = self._last_state.active_planet()
                cur_pid = _safe_api_int(prev_active.planet_id) if prev_active is not None else 0
                cur_b8 = prev_active.building_level(8) if prev_active is not None else 0
                cur_b9 = prev_active.building_level(9) if prev_active is not None else 0
                cur_res_sig = tuple(
                    sorted((game_data.safe_key(k), _safe_api_int(v)) for k, v in (self._last_state.researches or {}).items())
                )
                cur_res_busy = any(q.category == "research" for q in self._last_state.active_queues)
                if cur_b8 <= 0 or cur_res_busy:
                    need_research = False
                if cur_b9 <= 0:
                    need_shipyard = False
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
                    if self._sync_spio_if_unread(client, merged, priority="normal") > 0:
                        merged = self._record_state(
                            merged,
                            fetched_buildings=include_buildings,
                            fetched_research=need_research,
                            fetched_shipyard=need_shipyard,
                            fetched_home=False,
                            observed_active_planet=False,
                            update_snapshot_ts=False,
                        )
            except RateLimitError as exc:
                self._record_server_rate_limit(exc)
                if self.log_dir is not None:
                    append_crash_debug_log(
                        component="api_server.get_state",
                        event="rate_limit",
                        error=exc,
                    )
                raise
            except AntiBotChallengeError as exc:
                self._record_antibot_pause(exc)
                if self.log_dir is not None:
                    append_crash_debug_log(
                        component="api_server.get_state",
                        event="antibot_challenge",
                        error=exc,
                    )
                raise
            except (SpaceInvasionAuthError, Exception) as exc:
                if self.log_dir is not None and not isinstance(exc, ApiRequestError):
                    append_crash_debug_log(
                        component="api_server.get_state",
                        event="state_error",
                        error=exc,
                    )
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
        from spaceinvasion.core import game_data
        from spaceinvasion.planner.strategy import _resources_from_dict

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
        from spaceinvasion.planner.strategy import _resources_from_dict

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
        from spaceinvasion.core import game_data
        from spaceinvasion.planner.strategy import _resources_from_dict

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
            self._raid_targets = self._enrich_raid_targets(self._db.get_spy_targets(limit=250))
            return saved
        by_coords: dict[str, dict[str, Any]] = {
            normalize_coords(r.get("coords") or r.get("target_coords") or ""): dict(r)
            for r in self._raid_targets
            if (r.get("coords") or r.get("target_coords"))
        }
        now_ts = round(time.time(), 3)
        saved = 0
        for s in slots:
            has_debris = bool(getattr(s, "has_debris", False))
            if (
                getattr(s, "status_color", "free") == "free"
                and not getattr(s, "is_spacia", False)
                and not has_debris
            ):
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
                "has_debris": has_debris,
                "points": prev.get("points"),
                "resources": prev.get("resources") or {"pig_iron": 0, "metal": 0, "kryptonite": 0, "spice": 0},
                "fleet": prev.get("fleet") or {},
                "defense": prev.get("defense") or {},
                "dug_in": prev.get("dug_in", False),
                "last_scanned_ts": now_ts,
                "last_spied_ts": prev.get("last_spied_ts", 0.0),
                "has_spy_intel": bool(prev.get("has_spy_intel", False)),
                "blocked_until_ts": _safe_api_float(prev.get("blocked_until_ts", 0.0)),
                "block_reason": _bounded_api_str(prev.get("block_reason") or "", default=""),
                "bash_hits": _safe_api_int(prev.get("bash_hits", 0)),
                "last_blocked_ts": _safe_api_float(prev.get("last_blocked_ts", 0.0)),
            }
            saved += 1
        self._raid_targets = self._enrich_raid_targets(list(by_coords.values())[:250])
        return saved

    def _owned_sweep_galaxies_locked(self) -> list[int]:
        gals: list[int] = []
        if self._last_state is not None:
            for p in self._last_state.planets:
                if normalize_planet_type(p) == "raid":
                    continue
                m = re.search(r"(\d{1,2}):(\d{1,3})", str(p.coords or ""), re.ASCII)
                if m:
                    g = max(1, min(14, int(m.group(1))))
                    if g not in gals:
                        gals.append(g)
        return gals or [1]

    def run_galaxy_sweep(
        self,
        *,
        pages: Optional[int] = None,
        galaxy: Optional[int] = None,
        start_system: Optional[int] = None,
        delay_range: Optional[tuple[float, float]] = None,
        dry_run: bool = False,
        priority: str = "normal",
    ) -> dict[str, Any]:
        """Scan `pages` (default `100`) solar systems with randomized intervals, releasing `_lock` between steps."""
        if not self._sweep_run_lock.acquire(blocking=False):
            with self._lock:
                cur_st = self._get_galaxy_sweep_state_locked()
            return _sanitize_finite_json(
                _redact_api_payload(
                    {
                        "ok": True,
                        "command": "galaxy_sweep",
                        "executed": False,
                        "dry_run": dry_run,
                        "in_progress": True,
                        "message": "Galaxy sweep is already in progress.",
                        "galaxy_sweep": cur_st,
                    }
                )
            )

        sweep_started_ts = round(time.time(), 3)
        pages_scanned = 0
        players_found = 0
        colonies_found = 0
        debris_found = 0
        aborted_reason: Optional[str] = None

        try:
            with self._lock:
                self._sweep_in_progress = True
                cfg = self.strategy_config
                total_pages = max(
                    1,
                    min(400, int(pages if pages is not None else getattr(cfg, "galaxy_sweep_pages", 100))),
                )
                owned_gals = self._owned_sweep_galaxies_locked()
                sw_state = self._get_galaxy_sweep_state_locked(now_ts=sweep_started_ts)
                cur_gal = int(galaxy) if galaxy is not None else int(sw_state.get("cursor_galaxy") or owned_gals[0])
                if galaxy is None and cur_gal not in owned_gals:
                    cur_gal = owned_gals[0]
                if start_system is not None:
                    cur_sys = max(1, min(400, int(start_system)))
                elif int(sw_state.get("total_sweeps") or 0) == 0 and self._last_state is not None:
                    ap = self._last_state.active_planet() or (
                        self._last_state.planets[0] if self._last_state.planets else None
                    )
                    m_ap = re.search(r"(\d{1,2}):(\d{1,3})", str(getattr(ap, "coords", "") or ""), re.ASCII)
                    if m_ap:
                        cur_gal = int(m_ap.group(1)) if galaxy is None else cur_gal
                        own_sys = int(m_ap.group(2))
                        jitter = random.randint(-3, 3)
                        cur_sys = ((own_sys - 1 - (total_pages // 2) + jitter) % 400) + 1
                    else:
                        cur_sys = int(sw_state.get("cursor_system") or 1)
                else:
                    cur_sys = max(1, min(400, int(sw_state.get("cursor_system") or 1)))

                allocated_normal = count_allocated_normal_planets(self._last_state)
                max_planets_cap = max(1, _safe_api_int(getattr(cfg, "max_planets", 11), 11))
                collect_colonies = bool(allocated_normal < max_planets_cap)
                self._save_galaxy_sweep_state_locked(
                    {"last_sweep_started_ts": sweep_started_ts},
                    ts=sweep_started_ts,
                )

            if dry_run:
                end_sys = ((cur_sys - 1 + total_pages) % 400) + 1
                return _sanitize_finite_json(
                    _redact_api_payload(
                        {
                            "ok": True,
                            "command": "galaxy_sweep",
                            "executed": False,
                            "dry_run": True,
                            "pages_planned": total_pages,
                            "start_galaxy": cur_gal,
                            "start_system": cur_sys,
                            "end_system": end_sys,
                            "collect_colonies": collect_colonies,
                            "allocated_normal_planets": allocated_normal,
                            "max_planets": max_planets_cap,
                            "message": (
                                f"[DRY-RUN] Would sweep {total_pages} galaxy pages starting at "
                                f"[{cur_gal:02d}:{cur_sys:03d}] (colonies={'ON' if collect_colonies else 'FULL'})."
                            ),
                        }
                    )
                )

            eff_delay = delay_range if delay_range is not None else (1.75, 2.55)
            lo_d = max(0.0, float(min(eff_delay[0], eff_delay[1])))
            hi_d = max(lo_d, float(max(eff_delay[0], eff_delay[1])))

            for step_idx in range(total_pages):
                if self._sweep_stop_event.is_set():
                    aborted_reason = "Sweep stopped on shutdown."
                    break

                if step_idx > 0 and hi_d > 0.0:
                    wait_jitter = random.uniform(lo_d, hi_d)
                    if self._sweep_stop_event.wait(wait_jitter):
                        aborted_reason = "Sweep stopped on shutdown."
                        break

                if hi_d > 0.0:
                    budget_deadline = time.monotonic() + 75.0
                    while not self._sweep_stop_event.is_set() and time.monotonic() < budget_deadline:
                        rem_rl = self.rate_limit_remaining()
                        total_60s = self._page_limiter.views_last_60s() + self._sweep_views_last_60s()
                        if rem_rl <= 0.0 and total_60s < 24:
                            break
                        sleep_s = max(1.0, min(5.0, rem_rl if rem_rl > 0.0 else random.uniform(1.1, 2.2)))
                        if self._sweep_stop_event.wait(sleep_s):
                            break

                with self._lock:
                    if self._bot_control.get("captcha_blocked"):
                        aborted_reason = str(
                            self._bot_control.get("captcha_message") or "CAPTCHA active"
                        )
                        break
                    if self._bot_control.get("paused") and priority != "interactive":
                        aborted_reason = "Bot paused via operator UI."
                        break
                    self._raise_if_rate_limited()
                    cfg_now = self.strategy_config
                    allocated_normal = count_allocated_normal_planets(self._last_state)
                    max_planets_cap = max(1, _safe_api_int(getattr(cfg_now, "max_planets", 11), 11))
                    collect_colonies = bool(allocated_normal < max_planets_cap)

                    try:
                        with self._client_factory(self.debug_port) as client:
                            if step_idx == 0:
                                self._call_with_optional_priority(
                                    client.ensure_in_game,
                                    priority="interactive" if priority == "interactive" else "normal",
                                )
                            if self._is_real_client_method(client, "step_galaxy_system"):
                                html, actual_gal, actual_sys = client.step_galaxy_system(
                                    galaxy=cur_gal,
                                    system=cur_sys,
                                    pace=False,
                                    priority=priority,
                                )
                            elif self._is_real_client_method(client, "open_galaxy_with_coords"):
                                html, actual_gal, actual_sys = client.open_galaxy_with_coords(
                                    galaxy=cur_gal,
                                    system=cur_sys,
                                    priority=priority,
                                )
                            else:
                                html = self._open_galaxy(
                                    client,
                                    galaxy=cur_gal,
                                    system=cur_sys,
                                    priority=priority,
                                )
                                actual_gal, actual_sys = extract_galaxy_system_coords(
                                    html,
                                    default=(cur_gal, cur_sys),
                                )
                    except RateLimitError as rl_exc:
                        self._record_server_rate_limit(rl_exc)
                        aborted_reason = f"Rate-limit cooldown triggered ({rl_exc.retry_after:.0f}s)."
                        break
                    except AntiBotChallengeError as ab_exc:
                        self._record_antibot_pause(ab_exc)
                        aborted_reason = f"Anti-bot challenge detected: {ab_exc}"
                        break

                    self._sweep_xhr_timestamps.append(time.monotonic())
                    slots = parse_galaxy_system_html(html, galaxy=actual_gal, system=actual_sys)
                    players_on_page = sum(
                        1
                        for s in slots
                        if s.status_color in ("yellow", "light_red") or s.is_spacia
                    )
                    debris_on_page = sum(1 for s in slots if bool(getattr(s, "has_debris", False)))
                    free_on_page = sum(1 for s in slots if s.status_color == "free")

                    slots_for_scan = (
                        slots
                        if collect_colonies
                        else [
                            s
                            for s in slots
                            if s.status_color != "free"
                            or bool(getattr(s, "has_debris", False))
                            or bool(getattr(s, "is_spacia", False))
                        ]
                    )
                    self._persist_galaxy_scan(actual_gal, actual_sys, slots_for_scan)
                    self._persist_galaxy_slots(slots_for_scan)

                    pages_scanned += 1
                    players_found += players_on_page
                    debris_found += debris_on_page
                    if collect_colonies:
                        colonies_found += free_on_page

                    if cur_sys >= 400:
                        cur_sys = 1
                        if galaxy is None and len(owned_gals) > 1:
                            idx_g = owned_gals.index(cur_gal) if cur_gal in owned_gals else 0
                            cur_gal = owned_gals[(idx_g + 1) % len(owned_gals)]
                    else:
                        cur_sys += 1

            now_done = round(time.time(), 3)
            with self._lock:
                self._sweep_in_progress = False
                cfg_end = self.strategy_config
                cfg_iv = max(
                    30.0,
                    min(300.0, float(getattr(cfg_end, "galaxy_sweep_interval_seconds", 270) or 270)),
                )
                rand_cycle_s = random.uniform(max(60.0, cfg_iv * 0.78), min(296.0, cfg_iv))
                next_due = min(
                    sweep_started_ts + min(296.0, cfg_iv),
                    max(now_done + random.uniform(8.0, 25.0), sweep_started_ts + rand_cycle_s),
                )
                cur_prev = self._get_galaxy_sweep_state_locked(now_ts=now_done)
                updated_sw = self._save_galaxy_sweep_state_locked(
                    {
                        "cursor_galaxy": cur_gal,
                        "cursor_system": cur_sys,
                        "last_sweep_started_ts": sweep_started_ts,
                        "last_sweep_completed_ts": now_done,
                        "next_sweep_due_ts": round(next_due, 3),
                        "last_pages_scanned": pages_scanned,
                        "last_players_found": players_found,
                        "last_colonies_found": colonies_found,
                        "last_debris_found": debris_found,
                        "total_sweeps": int(cur_prev.get("total_sweeps") or 0) + (1 if pages_scanned > 0 else 0),
                        "total_pages_scanned": int(cur_prev.get("total_pages_scanned") or 0) + pages_scanned,
                    },
                    ts=now_done,
                )
                self._invalidate_dashboard_cache()
                msg_summary = (
                    f"Swept {pages_scanned}/{total_pages} galaxy pages "
                    f"(players={players_found}, colonies={colonies_found if collect_colonies else 'SKIP:full'}, "
                    f"debris={debris_found}); next cursor [{cur_gal:02d}:{cur_sys:03d}]."
                )
                if aborted_reason:
                    msg_summary += f" ({aborted_reason})"
                if self._db is not None and pages_scanned > 0:
                    ap_log = (
                        self._last_state.active_planet()
                        if self._last_state is not None
                        else None
                    )
                    self._db.record_action(
                        source="sweep",
                        command="galaxy_sweep",
                        action=PlannedAction(
                            priority="P2",
                            kind="SCAN_GALAXY",
                            planet_id=ap_log.planet_id if ap_log is not None else 0,
                            planet_coords=ap_log.coords if ap_log is not None else "",
                            target_name=f"Sweep {pages_scanned} pages -> [{cur_gal:02d}:{cur_sys:03d}]",
                            reason="Periodic randomized 100-page galaxy sweep",
                            executable_now=True,
                        ),
                        executed=True,
                        dry_run=False,
                        message=msg_summary,
                    )
            return _sanitize_finite_json(
                _redact_api_payload(
                    {
                        "ok": True,
                        "command": "galaxy_sweep",
                        "executed": pages_scanned > 0,
                        "dry_run": False,
                        "pages_scanned": pages_scanned,
                        "players_found": players_found,
                        "colonies_found": colonies_found,
                        "debris_found": debris_found,
                        "collect_colonies": collect_colonies,
                        "aborted_reason": aborted_reason,
                        "message": msg_summary,
                        "galaxy_sweep": updated_sw,
                    }
                )
            )
        finally:
            with self._lock:
                self._sweep_in_progress = False
            self._sweep_run_lock.release()

    def _galaxy_sweep_worker_loop(self) -> None:
        # Initial randomized startup jitter (4..12s) so first snapshot finishes cleanly
        if self._sweep_stop_event.wait(random.uniform(4.0, 12.0)):
            return
        while not self._sweep_stop_event.is_set():
            should_run = False
            with self._lock:
                cfg = self.strategy_config
                sw = self._get_galaxy_sweep_state_locked()
                paused = bool(self._bot_control.get("paused"))
                cap_blk = bool(self._bot_control.get("captcha_blocked"))
                mode_ov = self._bot_control.get("mode_override")
                is_dry = mode_ov == "dry-run"
                if (
                    bool(getattr(cfg, "allow_galaxy_scan", False))
                    and not paused
                    and not cap_blk
                    and not is_dry
                    and bool(sw.get("due", True))
                    and self.rate_limit_remaining() <= 0.0
                    and is_debug_port_open(self.debug_port)
                ):
                    should_run = True
            if should_run:
                try:
                    self.run_galaxy_sweep(priority="normal")
                except Exception as exc:
                    if self.log_dir is not None:
                        append_crash_debug_log(
                            component="api_server.galaxy_sweep_worker",
                            event="sweep_error",
                            error=exc,
                        )
            if self._sweep_stop_event.wait(random.uniform(2.0, 5.0)):
                break

    def start_galaxy_sweep_worker(self) -> None:
        """Start the background randomized `<= 5 min` 100-page galaxy sweep worker thread."""
        if self._sweep_thread is not None and self._sweep_thread.is_alive():
            return
        self._sweep_stop_event.clear()
        t = threading.Thread(
            target=self._galaxy_sweep_worker_loop,
            name="galaxy-sweep-worker",
            daemon=True,
        )
        self._sweep_thread = t
        t.start()

    def stop_galaxy_sweep_worker(self) -> None:
        """Signal and join the background galaxy sweep worker if active."""
        ev = getattr(self, "_sweep_stop_event", None)
        if ev is not None:
            ev.set()
        t = getattr(self, "_sweep_thread", None)
        if t is not None and t.is_alive() and t is not threading.current_thread():
            t.join(timeout=3.0)
        self._sweep_thread = None

    @staticmethod
    def _is_real_client_method(client: Any, attr_name: str) -> bool:
        if client is None:
            return False
        fn = getattr(client, attr_name, None)
        if not callable(fn):
            return False
        if "unittest.mock" in type(client).__module__:
            inst_dict = getattr(client, "__dict__", {})
            if attr_name in inst_dict:
                return True
            wraps = getattr(client, "_mock_wraps", None)
            return wraps is not None and callable(getattr(wraps, attr_name, None))
        return True

    def _own_planet_coords(self, state: Optional[GameState] = None) -> set[str]:
        coords: set[str] = set()
        for src in (state, self._last_state):
            if src is None:
                continue
            for p in src.planets:
                nc = normalize_coords(getattr(p, "coords", "") or "")
                if nc:
                    coords.add(nc)
        return coords

    def _persist_spy_reports(
        self,
        reports: tuple[SpyReport, ...] | list[SpyReport],
        *,
        exclude_coords: Optional[set[str]] = None,
    ) -> int:
        if not reports:
            return 0
        excl = exclude_coords if exclude_coords is not None else self._own_planet_coords()
        filtered = [
            rep
            for rep in reports
            if normalize_coords(rep.target_coords)
            and normalize_coords(rep.target_coords) not in excl
        ]
        if not filtered:
            return 0
        now_ts = round(time.time(), 3)
        if self._db is not None:
            for rep in filtered:
                self._db.record_spy_report(rep, ts=now_ts)
            self._raid_targets = self._enrich_raid_targets(self._db.get_spy_targets(limit=100))
            return len(filtered)
        by_coords: dict[str, dict[str, Any]] = {
            normalize_coords(r.get("coords") or r.get("target_coords") or ""): dict(r)
            for r in self._raid_targets
            if (r.get("coords") or r.get("target_coords"))
        }
        for rep in filtered:
            norm_tc = normalize_coords(rep.target_coords)
            prev = by_coords.get(norm_tc, {})
            is_spacia = bool(rep.is_spacia or prev.get("is_spacia", False))
            status_color = prev.get("status_color") or ("yellow" if is_spacia else "light_red")
            by_coords[norm_tc] = {
                "coords": norm_tc,
                "owner": rep.owner or prev.get("owner", ""),
                "planet_name": getattr(rep, "planet_name", "") or prev.get("planet_name", ""),
                "status_color": status_color,
                "is_inactive": bool(prev.get("is_inactive", False) or is_spacia or status_color == "yellow"),
                "is_spacia": is_spacia,
                "has_debris": bool(prev.get("has_debris", False)),
                "points": prev.get("points"),
                "resources": asdict(rep.resources),
                "fleet": dict(rep.fleet),
                "defense": dict(rep.defense),
                "dug_in": bool(rep.dug_in),
                "last_scanned_ts": max(float(prev.get("last_scanned_ts") or 0.0), now_ts),
                "last_spied_ts": now_ts,
                "has_spy_intel": True,
                "blocked_until_ts": _safe_api_float(prev.get("blocked_until_ts", 0.0)),
                "block_reason": _bounded_api_str(prev.get("block_reason") or "", default=""),
                "bash_hits": _safe_api_int(prev.get("bash_hits", 0)),
                "last_blocked_ts": _safe_api_float(prev.get("last_blocked_ts", 0.0)),
            }
        self._raid_targets = self._enrich_raid_targets(list(by_coords.values())[:100])
        return len(filtered)

    def _ingest_and_delete_page_messages(
        self,
        client: Any,
        html: Any,
        *,
        folder_type: str = "",
        own_coords: Optional[set[str]] = None,
        priority: str = "normal",
        always_persist_spy: bool = False,
        delete_after_persist: bool = True,
    ) -> tuple[list[SpyReport], int, bool, str]:
        if not isinstance(html, str) or not html.strip():
            return ([], 0, False, "")
        excl = own_coords if own_coords is not None else self._own_planet_coords()

        if self._is_real_client_method(client, "expand_collapsed_messages_on_page"):
            try:
                expanded_html = client.expand_collapsed_messages_on_page()
                if isinstance(expanded_html, str) and expanded_html.strip():
                    html = expanded_html
            except (RateLimitError, AntiBotChallengeError):
                raise
            except Exception:
                pass

        messages = parse_inbox_messages_html(
            html,
            folder_type=folder_type,
            exclude_coords=excl,
        )
        page_spy_reports = list(
            parse_spy_messages_html(html, exclude_coords=excl)
        )
        seen_spy_keys = {
            (normalize_coords(r.target_coords) or r.target_coords, r.server_msg_id)
            for r in page_spy_reports
        }
        for msg in messages:
            if msg.spy_report is not None:
                rk = (
                    normalize_coords(msg.spy_report.target_coords) or msg.spy_report.target_coords,
                    msg.spy_report.server_msg_id,
                )
                if rk not in seen_spy_keys:
                    seen_spy_keys.add(rk)
                    page_spy_reports.append(msg.spy_report)

        persisted_uids: list[str] = []
        newly_inserted: list[InboxMessage] = list(messages)
        if self._db is not None and messages:
            persisted_uids, newly_inserted = self._db.record_game_messages(messages)

        if self._db is not None and not always_persist_spy and messages:
            reports_to_persist: list[SpyReport] = []
            seen_new_spy: set[tuple[str, str]] = set()
            for msg in newly_inserted:
                if msg.spy_report is not None:
                    rk = (
                        normalize_coords(msg.spy_report.target_coords) or msg.spy_report.target_coords,
                        msg.spy_report.server_msg_id,
                    )
                    if rk not in seen_new_spy:
                        seen_new_spy.add(rk)
                        reports_to_persist.append(msg.spy_report)
            if not any(m.spy_report is not None for m in messages) and page_spy_reports:
                reports_to_persist = page_spy_reports
        else:
            reports_to_persist = page_spy_reports

        if reports_to_persist:
            self._persist_spy_reports(reports_to_persist, exclude_coords=excl)

        debris_changed = False
        if self._db is not None:
            for msg in newly_inserted:
                mc = normalize_coords(msg.coords or "")
                if not mc or mc in excl:
                    continue
                if msg.category == "battle_report" and bool((msg.parsed_data or {}).get("has_debris")):
                    self._db.set_target_debris(mc, True)
                    debris_changed = True
                elif msg.category == "recycle_report":
                    self._db.set_target_debris(mc, False)
                    debris_changed = True
            if debris_changed:
                self._raid_targets = self._enrich_raid_targets(self._db.get_spy_targets(limit=100))

        complete_msgs = [m for m in messages if bool(getattr(m, "body_complete", True))]
        all_complete = len(complete_msgs) == len(messages)
        deleted = False
        post_html = html
        can_delete = (
            delete_after_persist
            and bool(complete_msgs)
            and (
                self._db is None
                or (all_complete and len(persisted_uids) == len(messages))
                or (not all_complete and len(persisted_uids) >= len(complete_msgs))
            )
            and self._is_real_client_method(client, "delete_messages_on_current_page")
        )
        if can_delete:
            server_ids = [m.server_msg_id for m in complete_msgs if m.server_msg_id]
            if all_complete or server_ids:
                try:
                    del_ok, after_html = self._call_with_optional_priority(
                        client.delete_messages_on_current_page,
                        server_ids,
                        all_on_page_persisted=all_complete,
                        priority=priority,
                    )
                    deleted = bool(del_ok)
                    if isinstance(after_html, str) and after_html:
                        post_html = after_html
                except (RateLimitError, AntiBotChallengeError):
                    raise
                except Exception:
                    deleted = False
                if deleted and self._db is not None and persisted_uids:
                    if all_complete:
                        self._db.mark_game_messages_deleted(persisted_uids)
                    else:
                        complete_uids = [m.msg_uid for m in complete_msgs if m.msg_uid]
                        if complete_uids:
                            self._db.mark_game_messages_deleted(complete_uids)

        ingested_count = max(len(reports_to_persist), len(newly_inserted))
        return (page_spy_reports, ingested_count, deleted, post_html)

    def _sync_spio_if_unread(
        self,
        client: Any,
        state: GameState,
        *,
        priority: str = "normal",
        get_client: Optional[Callable[[], Any]] = None,
        ignore_auth_error: bool = False,
    ) -> int:
        unread_map = state.unread_messages or {}
        unread_spio = _safe_api_int(unread_map.get("spio", 0))
        unread_total = _safe_api_int(unread_map.get("total", 0))
        now = time.monotonic()
        pending_ready = self._pending_spy_sync and (now - self._last_spy_dispatched_at) >= 8.0
        new_inbox_unread = unread_total > self._last_synced_unread_total
        if unread_spio <= 0 and not pending_ready and not new_inbox_unread:
            if unread_total < self._last_synced_unread_total:
                self._last_synced_unread_total = max(0, unread_total)
            return 0
        if self.rate_limit_remaining() > 0.0:
            return 0
        if (now - self._last_spio_sync_at) < self.SPIO_SYNC_TTL_S:
            return 0
        if client is None and get_client is not None:
            try:
                client = get_client()
            except (RateLimitError, AntiBotChallengeError):
                raise
            except SpaceInvasionAuthError:
                if ignore_auth_error:
                    return 0
                raise
        open_msg = getattr(client, "open_messages", None)
        if not callable(open_msg):
            return 0
        self._last_spio_sync_at = now
        own_coords = self._own_planet_coords(state)
        total_ingested = 0
        any_deleted = False
        last_post_html = ""

        try:
            html = self._call_with_optional_priority(open_msg, "spio", priority=priority)
        except (RateLimitError, AntiBotChallengeError):
            raise
        except Exception:
            return 0

        spio_reports, spio_ingested, spio_del, spio_post = self._ingest_and_delete_page_messages(
            client,
            html,
            folder_type="spio",
            own_coords=own_coords,
            priority=priority,
        )
        total_ingested += spio_ingested
        if spio_del:
            any_deleted = True
            last_post_html = spio_post

        spio_folder_links = [
            (ftype, unread_cnt)
            for ftype, _href, unread_cnt, _tot in extract_message_folder_links(spio_post or html)
            if ftype and ftype != "spio" and unread_cnt > 0
        ]
        if spio_folder_links:
            try:
                for ftype, _ucnt in spio_folder_links[:3]:
                    if self.rate_limit_remaining() > 0.0:
                        break
                    f_html = self._call_with_optional_priority(open_msg, ftype, priority=priority)
                    if isinstance(f_html, str):
                        _, f_ing, f_del, f_post = self._ingest_and_delete_page_messages(
                            client,
                            f_html,
                            folder_type=ftype,
                            own_coords=own_coords,
                            priority=priority,
                        )
                        total_ingested += f_ing
                        if f_del:
                            any_deleted = True
                            last_post_html = f_post
            except (RateLimitError, AntiBotChallengeError):
                raise
            except Exception:
                pass
        elif (
            not spio_reports
            and spio_ingested == 0
            and unread_spio <= 0
            and (unread_total > 0 or pending_ready)
        ):
            try:
                inbox_html = self._call_with_optional_priority(open_msg, "", priority=priority)
                if isinstance(inbox_html, str):
                    _, inb_ingested, inb_del, inb_post = self._ingest_and_delete_page_messages(
                        client,
                        inbox_html,
                        folder_type="inbox",
                        own_coords=own_coords,
                        priority=priority,
                    )
                    total_ingested += inb_ingested
                    if inb_del:
                        any_deleted = True
                        last_post_html = inb_post
                    if inb_ingested == 0:
                        folder_links = [
                            (ftype, unread_cnt)
                            for ftype, _href, unread_cnt, _tot in extract_message_folder_links(inbox_html)
                            if ftype and ftype != "spio" and unread_cnt > 0
                        ]
                        for ftype, _ucnt in folder_links[:3]:
                            if self.rate_limit_remaining() > 0.0:
                                break
                            f_html = self._call_with_optional_priority(open_msg, ftype, priority=priority)
                            if isinstance(f_html, str):
                                _, f_ing, f_del, f_post = self._ingest_and_delete_page_messages(
                                    client,
                                    f_html,
                                    folder_type=ftype,
                                    own_coords=own_coords,
                                    priority=priority,
                                )
                                total_ingested += f_ing
                                if f_del:
                                    any_deleted = True
                                    last_post_html = f_post
            except (RateLimitError, AntiBotChallengeError):
                raise
            except Exception:
                pass

        self._pending_spy_sync = False
        if any_deleted:
            # Reset synced counter so any remaining unread pages are archived & deleted on subsequent ticks
            self._last_synced_unread_total = 0
            if last_post_html:
                _ = parse_unread_banner_count(last_post_html)
        else:
            self._last_synced_unread_total = max(0, unread_total)
        self._persist_runtime_state()
        return total_ingested

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
                    self._economy_sync_timestamps[int(pid)] = time.time()
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
            if res.action.kind == "ACTIVATE_TRANSMITTER":
                tm_info = getattr(executor, "last_transmitter_info", None)
                free_rem_val = (
                    tm_info.get("free_spins")
                    if isinstance(tm_info, Mapping) and tm_info.get("free_spins") is not None
                    else None
                )
                rew_val = (
                    str(tm_info.get("latest_reward") or "")
                    if isinstance(tm_info, Mapping)
                    else ""
                )
                if res.executed:
                    self._record_transmitter_state_locked(
                        activated=True,
                        free_spins_remaining=free_rem_val,
                        reward=rew_val,
                    )
                elif (
                    isinstance(tm_info, Mapping)
                    and _safe_api_int(tm_info.get("free_spins"), 1) <= 0
                    and bool(getattr(res, "validated", True))
                ):
                    self._record_transmitter_state_locked(
                        activated=False,
                        free_spins_remaining=0,
                    )
            if res.executed and res.action.kind == "THROTTLE_BUILDING":
                pid_i = game_data.cap_json_int(_safe_api_int(res.action.planet_id, 0))
                self._throttle_timestamps[pid_i] = time.time()
            if res.action.kind in ("ATTACK", "SPY", "RECYCLE", "ESCAPE_FLIGHT", "COLONIZE"):
                pid_f = game_data.cap_json_int(_safe_api_int(res.action.planet_id, 0))
                if pid_f > 0:
                    if res.executed:
                        self._fleet_slot_cooldown_until.pop(pid_f, None)
                    else:
                        msg_l = _bounded_api_str(res.message, default="").lower()
                        if any(marker in msg_l for marker in self._FLEET_SLOT_BUSY_MARKERS):
                            self._fleet_slot_cooldown_until[pid_f] = (
                                time.monotonic() + self.FLEET_SLOT_COOLDOWN_S
                            )
                tname_f = (
                    game_data.safe_key(res.action.target_name)
                    if res.action.target_name is not None
                    else ""
                )
                m_fcoords = re.search(r"(\d{1,2}:\d{1,3}:\d{1,2})", tname_f, re.ASCII)
                f_coords = normalize_coords(m_fcoords.group(1)) if m_fcoords else ""
                raw_cd_s = getattr(res, "target_cooldown_seconds", None)
                if f_coords and raw_cd_s is not None:
                    cd_s = _safe_api_float(raw_cd_s, 0.0)
                    if cd_s > 0.0:
                        now_wall = time.time()
                        until_ts = round(now_wall + cd_s, 3)
                        b_reason = _bounded_api_str(
                            getattr(res, "target_block_reason", None) or res.message,
                            default="Mission blocked by server protection",
                        )
                        self._target_blocks[f_coords] = (until_ts, b_reason)
                        if self._db is not None:
                            self._db.record_target_block(
                                f_coords,
                                blocked_until_ts=until_ts,
                                reason=b_reason,
                                is_bash=bool(getattr(res, "target_is_bash_block", False)),
                                ts=now_wall,
                            )
                            self._raid_targets = self._enrich_raid_targets(
                                self._db.get_spy_targets(limit=100)
                            )
                        else:
                            self._raid_targets = self._enrich_raid_targets(self._raid_targets)
                if f_coords and bool(getattr(res, "clear_target_debris", False)):
                    if self._db is not None:
                        self._db.set_target_debris(f_coords, False)
                        self._raid_targets = self._enrich_raid_targets(
                            self._db.get_spy_targets(limit=100)
                        )
                    else:
                        updated_rt: list[dict[str, Any]] = []
                        for r_item in self._raid_targets:
                            c_item = dict(r_item)
                            if normalize_coords(c_item.get("coords") or c_item.get("target_coords") or "") == f_coords:
                                c_item["has_debris"] = False
                            updated_rt.append(c_item)
                        self._raid_targets = tuple(updated_rt)
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
            if res.executed and not dry_run:
                comp_match_fn = getattr(self._db, "complete_matching_manual_command", None)
                if callable(comp_match_fn):
                    act_tname = game_data.safe_key(res.action.target_name) if res.action.target_name is not None else ""
                    m_tc = re.search(r"(\d{1,2}:\d{1,3}(?::\d{1,2})?)", act_tname, re.ASCII)
                    exec_lvl = game_data.safe_optional_int(res.action.target_level, None)
                    comp_match_fn(
                        kind=res.action.kind,
                        planet_id=game_data.safe_int(res.action.planet_id, 0),
                        target_id=game_data.safe_optional_int(res.action.target_id, None),
                        target_coords=m_tc.group(1) if m_tc else act_tname,
                        executed_level=exec_lvl if res.action.kind in ("BUILD", "DEMOLISH_BUILDING", "RESEARCH") else None,
                        executed_qty=exec_lvl if res.action.kind == "BUILD_SHIP" else None,
                        message=res.message,
                    )
                    self._dash_ag_cache_json = None
            if not dry_run:
                self._record_action_validation_notification_locked(cmd, res)
                self._persist_runtime_state()

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
                fetched_buildings=(res.action.kind in ("BUILD", "DEMOLISH_BUILDING")),
                fetched_research=(res.action.kind == "RESEARCH"),
                fetched_shipyard=(res.action.kind == "BUILD_SHIP"),
                fetched_home=(res.action.kind == "CLAIM_TUTORIAL"),
                observed_active_planet=(obs_conf_bool is True),
            )
            if obs_conf_bool is False and len(after_state.planets) > 1:
                self._active_planet_confirmed = False
            after_plan = self._plan_with_context(after_state)
        elif res.executed and res.action.kind == "SPY":
            self._pending_spy_sync = True
            self._last_spy_dispatched_at = time.monotonic()
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
            inc_b = res.action.kind in ("BUILD", "DEMOLISH_BUILDING", "CLAIM_TUTORIAL")
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
            and client is not None
            and not (res.action.kind == "CLAIM_TUTORIAL" and isinstance(res_updated, GameState))
        ):
            ret_html = self._call_with_optional_priority(
                client.navigate_menu,
                "internalHome",
                priority="interactive",
            )
            if (
                isinstance(ret_html, str)
                and ret_html
                and after_state is not None
                and not is_server_rate_limit_html(ret_html)
                and "bitmeup-login" not in ret_html.lower()
                and "sip-tab-universe" not in ret_html.lower()
            ):
                home_pid, h_spd, h_newbie, h_q, h_al = parse_home_metadata(ret_html)
                tut_home = parse_tutorial_from_home(ret_html)
                p_ids = {p.planet_id for p in after_state.planets}
                upd_kw: dict[str, Any] = {
                    "tutorial": tut_home if tut_home is not None else after_state.tutorial,
                    "active_queues": h_q,
                    "fleet_alerts": h_al,
                    "newbie_protection": bool(h_newbie),
                }
                if h_spd is not None:
                    upd_kw["speedup_factor"] = h_spd
                if home_pid is not None and home_pid in p_ids:
                    upd_kw["active_planet_id"] = home_pid
                after_state = self._record_state(
                    replace(after_state, **upd_kw),
                    fetched_buildings=self._last_buildings_fetched,
                    fetched_home=True,
                    observed_active_planet=(home_pid is not None and home_pid in p_ids),
                )
                after_plan = self._plan_with_context(after_state)

        next_action = self._serialize_action(after_plan[0]) if after_plan else None
        summary: dict[str, Any] = {
            "ok": True,
            "command": cmd,
            "executed": res.executed,
            "validated": bool(getattr(res, "validated", True)),
            "validation_error": _redact_api_payload(getattr(res, "validation_error", None)),
            "dry_run": res.dry_run,
            "message": _redact_api_payload(res.message),
            "action": self._serialize_action(res.action),
            "next_action": next_action,
        }
        if after_state is not None:
            summary["state"] = _sanitize_finite_json(_redact_api_payload(asdict(self._attach_raid_stats(after_state))))
        if self._db is not None and hasattr(self._db, "get_manual_queue"):
            summary["manual_queue"] = _sanitize_finite_json(
                _redact_api_payload(self._db.get_manual_queue(status="pending", limit=200))
            )
        if self._db is not None:
            summary["notifications"] = _sanitize_finite_json(self._get_notifications_summary_locked())
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

        if cmd == "notifications_list":
            n_limit = _parse_command_int("limit", payload.get("limit", 50), min_val=1, max_val=200) or 50
            n_status = _bounded_api_str(payload.get("status") or "all", default="all").strip().lower()
            with self._lock:
                items = (
                    self._db.get_notifications(status=n_status, limit=int(n_limit))
                    if self._db is not None and hasattr(self._db, "get_notifications")
                    else []
                )
                unread_cnt = (
                    self._db.unread_notification_count()
                    if self._db is not None and hasattr(self._db, "unread_notification_count")
                    else 0
                )
                return _sanitize_finite_json(
                    {
                        "ok": True,
                        "command": cmd,
                        "unread_count": int(unread_cnt),
                        "items": _redact_api_payload(items),
                    }
                )

        if cmd == "notification_ack":
            nid = _parse_command_int("notification_id", payload.get("notification_id"), min_val=1, max_val=2**62)
            ack_st = _bounded_api_str(payload.get("status") or "read", default="read").strip().lower()
            if ack_st not in ("read", "dismissed", "unread"):
                raise ApiRequestError("status must be 'read', 'dismissed', or 'unread'.")
            with self._lock:
                if self._db is None or not hasattr(self._db, "acknowledge_notification"):
                    raise ApiRequestError("Database notifications are not configured.")
                updated_item = self._db.acknowledge_notification(int(nid or 0), status=ack_st)
                if updated_item is None:
                    raise ApiRequestError(f"Notification #{nid} not found.")
                return _sanitize_finite_json(
                    {
                        "ok": True,
                        "command": cmd,
                        "item": _redact_api_payload(updated_item),
                        "notifications": self._get_notifications_summary_locked(),
                    }
                )

        if cmd == "notifications_ack_all":
            ack_st = _bounded_api_str(payload.get("status") or "read", default="read").strip().lower()
            if ack_st not in ("read", "dismissed"):
                raise ApiRequestError("status must be 'read' or 'dismissed'.")
            with self._lock:
                changed = (
                    self._db.acknowledge_all_notifications(status=ack_st)
                    if self._db is not None and hasattr(self._db, "acknowledge_all_notifications")
                    else 0
                )
                return _sanitize_finite_json(
                    {
                        "ok": True,
                        "command": cmd,
                        "updated_count": int(changed),
                        "notifications": self._get_notifications_summary_locked(),
                    }
                )

        if cmd == "queue_list":
            limit = _parse_command_int("limit", payload.get("limit", 150), min_val=1, max_val=500) or 150
            with self._lock:
                pending = (
                    self._db.get_manual_queue(status="pending", limit=int(limit))
                    if self._db is not None
                    else []
                )
                all_items = (
                    self._db.get_manual_queue(status="all", limit=int(limit))
                    if self._db is not None
                    else []
                )
                history = [x for x in all_items if x.get("status") != "pending"][:50]
                return _sanitize_finite_json(
                    _redact_api_payload(
                        {
                            "ok": True,
                            "command": cmd,
                            "manual_queue": pending,
                            "history": history,
                        }
                    )
                )

        if cmd == "queue_add":
            raw_kind = payload.get("kind")
            if not isinstance(raw_kind, str) or not raw_kind.strip():
                raise ApiRequestError("'queue_add' requires a non-empty string 'kind'.")
            norm_kind = raw_kind.strip().upper()
            allowed_kinds = {
                "BUILD",
                "DEMOLISH_BUILDING",
                "RESEARCH",
                "BUILD_SHIP",
                "THROTTLE_BUILDING",
                "SPY",
                "ATTACK",
                "RECYCLE",
                "COLONIZE",
                "SCAN_GALAXY",
                "CLAIM_TUTORIAL",
            }
            if norm_kind not in allowed_kinds:
                raise ApiRequestError(f"Unsupported queue command kind '{norm_kind}'.")
            raw_prio = str(payload.get("priority", "P1") or "P1").strip().upper()
            if raw_prio not in ("P0", "P1", "P2", "P3"):
                raise ApiRequestError("'priority' must be one of P0, P1, P2, P3.")
            raw_pos = str(payload.get("position", "top") or "top").strip().lower()
            if raw_pos not in ("top", "bottom"):
                raw_pos = "top"
            pid = _parse_command_int(
                "planet_id",
                payload.get("planet_id", 0),
                allow_none=True,
                min_val=0,
                max_val=2**63 - 1,
            ) or 0
            tid = _parse_command_int(
                "target_id",
                payload.get("target_id"),
                allow_none=True,
                min_val=1,
                max_val=2**31 - 1,
            )
            tlvl_raw = payload.get("target_level")
            if tlvl_raw is None:
                tlvl_raw = payload.get("quantity")
            if tlvl_raw is None:
                tlvl_raw = payload.get("percentage")
            tlvl = _parse_command_int(
                "target_level",
                tlvl_raw,
                allow_none=True,
                min_val=0,
                max_val=100_000,
            )
            tname = _bounded_api_str(payload.get("target_name") or "", default="")[:160]
            tcoords = _bounded_api_str(payload.get("target_coords") or "", default="")[:64]
            note = _bounded_api_str(payload.get("note") or "", default="")[:280]
            dedup_flag = bool(payload.get("deduplicate_pending", False))

            if norm_kind in ("BUILD", "DEMOLISH_BUILDING", "RESEARCH", "BUILD_SHIP", "THROTTLE_BUILDING") and tid is None:
                raise ApiRequestError(f"'{norm_kind}' requires 'target_id'.")
            if norm_kind in ("SPY", "ATTACK", "RECYCLE", "COLONIZE") and not (tcoords.strip() or tname.strip()):
                raise ApiRequestError(f"'{norm_kind}' requires 'target_coords'.")

            with self._lock:
                if self._db is None:
                    raise ApiRequestError("Database is not configured on this API bridge.")
                pcoords = ""
                if self._last_state is not None:
                    p_match = (
                        next((p for p in self._last_state.planets if p.planet_id == pid), None)
                        if pid > 0
                        else self._last_state.active_planet()
                    )
                    if p_match is not None:
                        if pid <= 0:
                            pid = p_match.planet_id
                        pcoords = p_match.coords
                if not tname:
                    if norm_kind in ("BUILD", "DEMOLISH_BUILDING", "THROTTLE_BUILDING") and tid is not None:
                        tname = str((game_data.resolve_unit("buildings", tid) or {}).get("name") or f"Building b{tid}")
                    elif norm_kind == "RESEARCH" and tid is not None:
                        tname = str((game_data.resolve_unit("research", f"r{tid}") or {}).get("name") or f"r{tid}")
                    elif norm_kind == "BUILD_SHIP" and tid is not None:
                        s_slug = SHIP_SLUG_BY_KEY.get(f"s{tid}", f"s{tid}")
                        s_inf = (
                            game_data.resolve_unit("ships", s_slug)
                            or game_data.resolve_unit("ships", f"s{tid}")
                            or game_data.resolve_unit("ships", tid)
                            or {}
                        )
                        tname = str(s_inf.get("name") or f"Ship s{tid}")
                    elif tcoords:
                        tname = tcoords
                item = self._db.enqueue_manual_command(
                    kind=norm_kind,
                    priority=raw_prio,
                    position=raw_pos,
                    planet_id=int(pid),
                    planet_coords=pcoords,
                    target_id=tid,
                    target_name=tname,
                    target_coords=tcoords or tname,
                    target_level=tlvl,
                    note=note,
                    deduplicate_pending=dedup_flag,
                )
                self._dash_ag_cache_json = None
                pending = self._db.get_manual_queue(status="pending", limit=200)
                metal_review = self._review_queue_metal_status_locked(int(item.get("planet_id") or 0), pending)
                msg_suffix = f" ({metal_review['summary']})" if metal_review.get("summary") else ""
                return _sanitize_finite_json(
                    _redact_api_payload(
                        {
                            "ok": True,
                            "command": cmd,
                            "message": f"Queued {norm_kind} ({tname}) at {item['priority']}{msg_suffix}.",
                            "item": item,
                            "manual_queue": pending,
                            "metal_production_review": metal_review,
                        }
                    )
                )

        if cmd == "queue_update":
            qid = _parse_command_int("queue_id", payload.get("queue_id"), min_val=1, max_val=2**63 - 1)
            if qid is None:
                raise ApiRequestError("'queue_update' requires 'queue_id'.")
            prio_arg: Optional[str] = None
            if "priority" in payload and payload["priority"] is not None:
                prio_arg = str(payload["priority"]).strip().upper()
                if prio_arg not in ("P0", "P1", "P2", "P3"):
                    raise ApiRequestError("'priority' must be one of P0, P1, P2, P3.")
            move_arg: Optional[str] = None
            if "move" in payload and payload["move"] is not None:
                move_arg = str(payload["move"]).strip().lower()
                if move_arg not in ("top", "bottom", "up", "down"):
                    raise ApiRequestError("'move' must be one of top, bottom, up, down.")
            srank_arg = _parse_command_int(
                "sort_rank",
                payload.get("sort_rank"),
                allow_none=True,
                min_val=-1_000_000,
                max_val=1_000_000,
            )
            tlvl_arg: Any = ...
            if "target_level" in payload:
                tlvl_arg = _parse_command_int(
                    "target_level",
                    payload.get("target_level"),
                    allow_none=True,
                    min_val=0,
                    max_val=100_000,
                )
            note_arg = (
                _bounded_api_str(payload.get("note") or "", default="")[:280]
                if "note" in payload and payload["note"] is not None
                else None
            )
            with self._lock:
                if self._db is None:
                    raise ApiRequestError("Database is not configured on this API bridge.")
                updated = self._db.update_manual_queue_item(
                    int(qid),
                    priority=prio_arg,
                    move=move_arg,
                    sort_rank=srank_arg,
                    target_level=tlvl_arg,
                    note=note_arg,
                )
                if updated is None:
                    raise ApiRequestError(f"Queue item #{qid} not found.")
                self._dash_ag_cache_json = None
                pending = self._db.get_manual_queue(status="pending", limit=200)
                metal_review = self._review_queue_metal_status_locked(int(updated.get("planet_id") or 0), pending)
                msg_suffix = f" ({metal_review['summary']})" if metal_review.get("summary") else ""
                return _sanitize_finite_json(
                    _redact_api_payload(
                        {
                            "ok": True,
                            "command": cmd,
                            "message": f"Updated queue item #{qid} ({updated['priority']}){msg_suffix}.",
                            "item": updated,
                            "manual_queue": pending,
                            "metal_production_review": metal_review,
                        }
                    )
                )

        if cmd == "queue_cancel":
            qid = _parse_command_int("queue_id", payload.get("queue_id"), min_val=1, max_val=2**63 - 1)
            if qid is None:
                raise ApiRequestError("'queue_cancel' requires 'queue_id'.")
            with self._lock:
                if self._db is None:
                    raise ApiRequestError("Database is not configured on this API bridge.")
                ok_c = self._db.cancel_manual_queue_item(int(qid))
                self._dash_ag_cache_json = None
                pending = self._db.get_manual_queue(status="pending", limit=200)
                return _sanitize_finite_json(
                    _redact_api_payload(
                        {
                            "ok": True,
                            "command": cmd,
                            "canceled": ok_c,
                            "message": f"Canceled queue item #{qid}." if ok_c else f"Queue item #{qid} already inactive.",
                            "manual_queue": pending,
                        }
                    )
                )

        if cmd == "queue_clear":
            with self._lock:
                if self._db is None:
                    raise ApiRequestError("Database is not configured on this API bridge.")
                cnt = self._db.clear_manual_queue()
                self._dash_ag_cache_json = None
                return _sanitize_finite_json(
                    _redact_api_payload(
                        {
                            "ok": True,
                            "command": cmd,
                            "cleared_count": cnt,
                            "message": f"Cleared {cnt} pending command(s) from queue.",
                            "manual_queue": [],
                        }
                    )
                )

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

        if cmd == "db_migrations":
            ev_limit = _parse_command_int(
                "limit",
                payload.get("limit", 50),
                min_val=1,
                max_val=500,
            ) or 50
            include_all = bool(payload.get("include_all_stores", False))
            with self._lock:
                if self._db is None:
                    return _sanitize_finite_json(
                        {
                            "ok": True,
                            "command": cmd,
                            "latest_schema_version": LATEST_SCHEMA_VERSION,
                            "migrations": None,
                        }
                    )
                status = self._db.get_migration_status(include_events=True, event_limit=ev_limit)
                resp: dict[str, Any] = {
                    "ok": True,
                    "command": cmd,
                    "latest_schema_version": LATEST_SCHEMA_VERSION,
                    "migrations": _redact_api_payload(status),
                }
                if include_all:
                    root_dir = (
                        self._db.db_path.parent.parent
                        if str(self._db.db_path) != ":memory:"
                        else Path(".")
                    )
                    reg = DatabaseMigrationRegistry(root_dir)
                    if str(self._db.db_path) != ":memory:":
                        reg.register_database("primary", self._db.db_path, schema_family="game")
                    resp["stores"] = _redact_api_payload(reg.status_all(include_events=False))
                return _sanitize_finite_json(resp)

        if cmd == "db_migrate":
            dry_run_flag = bool(payload.get("dry_run", False))
            backup_flag = bool(payload.get("backup", False))
            tgt_ver = _parse_command_int(
                "target_version",
                payload.get("target_version"),
                allow_none=True,
                min_val=1,
                max_val=LATEST_SCHEMA_VERSION,
            )
            with self._lock:
                if self._db is None:
                    raise ApiRequestError("Database is not configured on this API bridge.")
                res = self._db.run_migrations(
                    target_version=tgt_ver,
                    dry_run=dry_run_flag,
                    backup=backup_flag,
                )
                return _sanitize_finite_json(
                    {
                        "ok": True,
                        "command": cmd,
                        "migration": _redact_api_payload(res),
                    }
                )

        if cmd == "db_backup":
            raw_lbl = _bounded_api_str(payload.get("label") or "manual", default="manual")[:40]
            with self._lock:
                if self._db is None:
                    raise ApiRequestError("Database is not configured on this API bridge.")
                try:
                    bkp = self._db.create_backup(label=raw_lbl)
                except ValueError as exc:
                    raise ApiRequestError(str(exc)) from exc
                return _sanitize_finite_json(
                    {
                        "ok": True,
                        "command": cmd,
                        "backup": _redact_api_payload(bkp),
                    }
                )

        if cmd == "db_verify":
            full_chk = bool(payload.get("full_integrity", False))
            with self._lock:
                if self._db is None:
                    raise ApiRequestError("Database is not configured on this API bridge.")
                ver = self._db.verify_schema(full_integrity=full_chk)
                return _sanitize_finite_json(
                    {
                        "ok": bool(ver.get("ok", False)),
                        "command": cmd,
                        "verification": _redact_api_payload(ver),
                    }
                )

        if cmd == "state_snapshots":
            snap_limit = _parse_command_int(
                "limit",
                payload.get("limit", 20),
                min_val=1,
                max_val=200,
            ) or 20
            inc_payload = bool(payload.get("include_payload", False))
            with self._lock:
                snaps = (
                    self._db.get_state_snapshots(limit=snap_limit, include_payload=inc_payload)
                    if self._db is not None
                    else []
                )
                return _sanitize_finite_json(
                    {
                        "ok": True,
                        "command": cmd,
                        "count": len(snaps),
                        "snapshots": _redact_api_payload(snaps),
                    }
                )

        if cmd == "game_messages":
            limit = _parse_command_int(
                "limit",
                payload.get("limit", 100),
                min_val=1,
                max_val=1000,
            )
            cat = payload.get("category")
            cat_str = str(cat).strip() if isinstance(cat, str) and cat.strip() else None
            coords_raw = payload.get("coords")
            coords_str = str(coords_raw).strip() if isinstance(coords_raw, str) and coords_raw.strip() else None
            with self._lock:
                msgs = (
                    self._db.get_game_messages(
                        category=cat_str,
                        coords=coords_str,
                        limit=int(limit or 100),
                    )
                    if self._db is not None
                    else []
                )
                return _sanitize_finite_json(
                    {
                        "ok": True,
                        "command": cmd,
                        "count": len(msgs),
                        "messages": _redact_api_payload(msgs),
                    }
                )

        if cmd == "espionage_intel":
            limit = _parse_command_int(
                "limit",
                payload.get("limit", 150),
                min_val=1,
                max_val=1000,
            ) or 150
            coords_raw = payload.get("coords")
            coords_str = str(coords_raw).strip() if isinstance(coords_raw, str) and coords_raw.strip() else None
            with self._lock:
                raw_targets = (
                    self._enrich_raid_targets(
                        self._db.get_spy_targets(limit=int(limit), include_own=False)
                    )
                    if self._db is not None
                    else list(self._raid_targets)
                )
                targets: list[dict[str, Any]] = []
                for t in raw_targets:
                    td = dict(t)
                    res_d = td.get("resources") if isinstance(td.get("resources"), Mapping) else {}
                    pig = _safe_api_float(res_d.get("pig_iron", 0.0))
                    met = _safe_api_float(res_d.get("metal", 0.0))
                    kryp = _safe_api_float(res_d.get("kryptonite", 0.0))
                    spc = _safe_api_float(res_d.get("spice", 0.0))
                    td["spice_eq"] = round(pig / 6.0 + met / 2.5 + kryp / 3.0 + spc, 1)
                    targets.append(td)
                raw_reports = (
                    self._db.get_spy_reports(coords=coords_str, limit=int(limit))
                    if self._db is not None
                    else []
                )
                reports: list[dict[str, Any]] = []
                for rep in raw_reports:
                    rd = dict(rep)
                    res_d = rd.get("resources") if isinstance(rd.get("resources"), Mapping) else {}
                    pig = _safe_api_float(res_d.get("pig_iron", 0.0))
                    met = _safe_api_float(res_d.get("metal", 0.0))
                    kryp = _safe_api_float(res_d.get("kryptonite", 0.0))
                    spc = _safe_api_float(res_d.get("spice", 0.0))
                    rd["spice_eq"] = round(pig / 6.0 + met / 2.5 + kryp / 3.0 + spc, 1)
                    reports.append(rd)
                spied_targets_cnt = sum(
                    1
                    for t in targets
                    if bool(t.get("has_spy_intel")) or _safe_api_float(t.get("last_spied_ts", 0.0)) > 0.0
                )
                summary = {
                    "total_targets": len(targets),
                    "spied_targets": spied_targets_cnt,
                    "total_reports": len(reports),
                    "spacia_count": sum(1 for t in targets if bool(t.get("is_spacia"))),
                    "inactive_count": sum(1 for t in targets if bool(t.get("is_inactive"))),
                    "debris_count": sum(1 for t in targets if bool(t.get("has_debris"))),
                }
                return _sanitize_finite_json(
                    _redact_api_payload(
                        {
                            "ok": True,
                            "command": cmd,
                            "summary": summary,
                            "targets": targets,
                            "reports": reports,
                        }
                    )
                )

        if cmd == "colony_candidates":
            limit = _parse_command_int(
                "limit",
                payload.get("limit", 200),
                min_val=1,
                max_val=1000,
            ) or 200
            with self._lock:
                ap = self._last_state.active_planet() if self._last_state is not None else None
                origin_coords = ap.coords if ap is not None else ""
                min_fields = max(50, _safe_api_int(self.strategy_config.min_colony_fields, 300))
                candidates = (
                    self._db.get_colony_candidates(
                        origin_coords=origin_coords,
                        min_fields_target=min_fields,
                        exclude_coords=self._own_planet_coords(),
                        limit=int(limit),
                    )
                    if self._db is not None
                    else []
                )
                grade_counts: dict[str, int] = {"S": 0, "A+": 0, "A": 0, "B": 0, "C": 0, "D": 0, "?": 0}
                for c in candidates:
                    g = str(c.get("grade") or "?")
                    grade_counts[g] = grade_counts.get(g, 0) + 1
                probed_count = sum(1 for c in candidates if bool(c.get("probed")))
                unprobed_count = len(candidates) - probed_count
                qualified_count = sum(1 for c in candidates if bool(c.get("meets_target")))
                colony_ships = 0
                spy_probes = 0
                planet_count = 0
                allocated_normal_planets = 0
                max_planets = max(1, _safe_api_int(getattr(self.strategy_config, "max_planets", 11), 11))
                if self._last_state is not None:
                    planet_count = len(self._last_state.planets)
                    allocated_normal_planets = count_allocated_normal_planets(self._last_state)
                    for p in self._last_state.planets:
                        colony_ships += max(0, _safe_api_int(p.ships.get("s8", 0)))
                        spy_probes += max(0, _safe_api_int(p.ships.get("s10", 0)))
                return _sanitize_finite_json(
                    _redact_api_payload(
                        {
                            "ok": True,
                            "command": cmd,
                            "origin_coords": origin_coords,
                            "min_colony_fields": min_fields,
                            "allow_colonize": bool(self.strategy_config.allow_colonize),
                            "allow_galaxy_scan": bool(self.strategy_config.allow_galaxy_scan),
                            "colony_ships": colony_ships,
                            "spy_probes": spy_probes,
                            "planet_count": planet_count,
                            "allocated_normal_planets": allocated_normal_planets,
                            "max_planets": max_planets,
                            "all_planets_allocated": allocated_normal_planets >= max_planets,
                            "grade_counts": grade_counts,
                            "probed_count": probed_count,
                            "unprobed_count": unprobed_count,
                            "qualified_count": qualified_count,
                            "candidates": candidates,
                        }
                    )
                )

        if cmd == "attack_results":
            limit = _parse_command_int(
                "limit",
                payload.get("limit", 150),
                min_val=1,
                max_val=1000,
            ) or 150
            coords_raw = payload.get("coords")
            coords_str = str(coords_raw).strip() if isinstance(coords_raw, str) and coords_raw.strip() else None
            with self._lock:
                battles = (
                    self._db.get_battle_reports(coords=coords_str, limit=int(limit))
                    if self._db is not None
                    else []
                )
                raid_stats = dict(self._db.get_raid_stats() if self._db is not None else {})
                now_ts = time.time()
                hist_24h = raid_stats.get("history")
                if isinstance(hist_24h, Sequence) and not isinstance(hist_24h, (str, bytes)):
                    raid_stats["total_24h"] = sum(
                        1
                        for h in hist_24h
                        if isinstance(h, Mapping)
                        and _safe_api_float(h.get("attacked_ts", 0.0)) >= (now_ts - 86400.0)
                    )
                else:
                    raid_stats["total_24h"] = 0
                raw_targets = (
                    self._enrich_raid_targets(
                        self._db.get_spy_targets(limit=300, include_own=False)
                    )
                    if self._db is not None
                    else list(self._raid_targets)
                )
                blocked_targets: list[dict[str, Any]] = []
                for t in raw_targets:
                    b_until = _safe_api_float(t.get("blocked_until_ts", 0.0))
                    b_hits = _safe_api_int(t.get("bash_hits", 0))
                    if b_until > now_ts or b_hits > 0:
                        bt = dict(t)
                        bt["remaining_seconds"] = max(0.0, round(b_until - now_ts, 1))
                        bt["active_block"] = b_until > now_ts
                        blocked_targets.append(bt)
                blocked_targets.sort(
                    key=lambda x: (
                        0 if x.get("active_block") else 1,
                        -_safe_api_float(x.get("remaining_seconds", 0.0)),
                        -_safe_api_int(x.get("bash_hits", 0)),
                    )
                )
                wins = sum(1 for b in battles if b.get("outcome") == "win")
                losses = sum(1 for b in battles if b.get("outcome") == "loss")
                draws = sum(1 for b in battles if b.get("outcome") not in ("win", "loss"))
                total_loot_sp = round(
                    sum(_safe_api_float((b.get("loot") or {}).get("spice_eq", 0.0)) for b in battles),
                    1,
                )
                total_loss_sp = round(
                    sum(
                        _safe_api_float((b.get("attacker_losses") or {}).get("spice_eq", 0.0))
                        for b in battles
                    ),
                    1,
                )
                summary = {
                    "total_battles": len(battles),
                    "wins": wins,
                    "losses": losses,
                    "draws": draws,
                    "total_loot_spice_eq": total_loot_sp,
                    "total_losses_spice_eq": total_loss_sp,
                    "active_blocks_count": sum(1 for bt in blocked_targets if bt.get("active_block")),
                }
                return _sanitize_finite_json(
                    _redact_api_payload(
                        {
                            "ok": True,
                            "command": cmd,
                            "summary": summary,
                            "raid_stats": raid_stats,
                            "battles": battles,
                            "blocked_targets": blocked_targets,
                        }
                    )
                )

        if cmd == "recycler_results":
            limit = _parse_command_int(
                "limit",
                payload.get("limit", 150),
                min_val=1,
                max_val=1000,
            ) or 150
            cat_raw = payload.get("category")
            cat_str = (
                str(cat_raw).strip()
                if isinstance(cat_raw, str) and cat_raw.strip() and str(cat_raw).strip().lower() != "all"
                else None
            )
            with self._lock:
                recycles = (
                    self._db.get_recycle_reports(limit=int(limit))
                    if self._db is not None
                    else []
                )
                raw_targets = (
                    self._enrich_raid_targets(
                        self._db.get_spy_targets(limit=200, include_own=False)
                    )
                    if self._db is not None
                    else list(self._raid_targets)
                )
                debris_targets = [dict(t) for t in raw_targets if bool(t.get("has_debris"))]
                other_emails = (
                    self._db.get_game_messages(
                        category=cat_str,
                        exclude_categories=("spy_report", "battle_report", "recycle_report") if not cat_str else (),
                        limit=int(limit),
                    )
                    if self._db is not None
                    else []
                )
                db_sum = self._db.summary() if self._db is not None else {}
                cat_counts = dict(db_sum.get("message_categories") or {})
                cat_counts["general"] = sum(
                    _safe_api_int(cat_counts.get(k, 0))
                    for k in ("general", "player", "system", "other")
                )
                recyclers_owned = 0
                if self._last_state is not None:
                    for p in self._last_state.planets:
                        recyclers_owned += max(0, _safe_api_int(p.ships.get("s11", 0)))
                tot_pig = sum(_safe_api_int(r.get("harvested_pig_iron", 0)) for r in recycles)
                tot_met = sum(_safe_api_int(r.get("harvested_metal", 0)) for r in recycles)
                tot_kry = sum(_safe_api_int(r.get("harvested_kryptonite", 0)) for r in recycles)
                tot_spi = sum(_safe_api_int(r.get("harvested_spice", 0)) for r in recycles)
                tot_sp = round(sum(_safe_api_float(r.get("spice_eq", 0.0)) for r in recycles), 1)
                summary = {
                    "total_runs": len(recycles),
                    "total_pig_iron": tot_pig,
                    "total_metal": tot_met,
                    "total_kryptonite": tot_kry,
                    "total_spice": tot_spi,
                    "total_spice_eq": tot_sp,
                    "active_debris_count": len(debris_targets),
                    "other_email_count": len(other_emails),
                    "recyclers_owned": recyclers_owned,
                    "category_counts": cat_counts,
                }
                return _sanitize_finite_json(
                    _redact_api_payload(
                        {
                            "ok": True,
                            "command": cmd,
                            "summary": summary,
                            "recycles": recycles,
                            "debris_targets": debris_targets,
                            "other_emails": other_emails,
                        }
                    )
                )

        if cmd == "bot_control":
            with self._lock:
                if "paused" in payload:
                    paused_flag = bool(payload["paused"])
                    self._bot_control["paused"] = paused_flag
                    if not paused_flag:
                        self._bot_control["captcha_blocked"] = False
                        self._bot_control["captcha_message"] = None
                if "mode_override" in payload:
                    raw_mode = payload["mode_override"]
                    if raw_mode not in (None, "live", "dry-run"):
                        raise ApiRequestError("mode_override must be null, 'live', or 'dry-run'.")
                    self._bot_control["mode_override"] = raw_mode
                self._persist_runtime_state()
                return _sanitize_finite_json(
                    _redact_api_payload(
                        {"ok": True, "command": cmd, "bot_control": dict(self._bot_control)}
                    )
                )

        if cmd == "update_config":
            raw_cfg = payload.get("config")
            if not isinstance(raw_cfg, Mapping):
                raise ApiRequestError("'update_config' requires a 'config' dictionary.")
            _validate_config_patch(raw_cfg, strict_bools=False)
            inc_ag = False
            if "include_action_graph" in payload and payload["include_action_graph"] is not None:
                if not isinstance(payload["include_action_graph"], bool):
                    raise ApiRequestError("'update_config' field 'include_action_graph' must be a boolean.")
                inc_ag = bool(payload["include_action_graph"])
            upd_pid = _parse_command_int(
                "planet_id",
                payload.get("planet_id"),
                allow_none=True,
                min_val=1,
                max_val=2**63 - 1,
            )
            with self._lock:
                prev_cfg = self.strategy_config
                merged = {**asdict(prev_cfg), **raw_cfg}
                if isinstance(raw_cfg.get("shipyard_targets"), Mapping):
                    merged["shipyard_targets"] = {
                        **dict(prev_cfg.shipyard_targets or {}),
                        **dict(raw_cfg["shipyard_targets"]),
                    }
                new_cfg = strategy_config_from_dict(merged)
                if self._config_file_explicit or self._db is None:
                    save_strategy_config(new_cfg, self.config_path)
                self.strategy_config = new_cfg
                if self._db is not None:
                    save_cfg_fn = getattr(self._db, "save_strategy_config_snapshot", None)
                    if callable(save_cfg_fn):
                        save_cfg_fn(asdict(new_cfg))
                self._invalidate_dashboard_cache()
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
                self._persist_runtime_state()
                for ex in (self._live_executor, self._dry_executor):
                    ex.allow_fleetsave = new_cfg.allow_fleetsave
                    ex.allow_raids = new_cfg.allow_raids
                    ex.allow_colonize = new_cfg.allow_colonize
                    ex.allow_transmitter = new_cfg.allow_transmitter
                    ex.max_actions_per_hour = _safe_hourly_budget(new_cfg.max_actions_per_hour, 60)
                    if "max_actions_per_hour" in raw_cfg:
                        ex.enforce_hourly_cap = True
                upd_resp: dict[str, Any] = {
                    "ok": True,
                    "command": cmd,
                    "config": asdict(new_cfg),
                }
                if inc_ag:
                    ag_state = (
                        self._attach_raid_stats(self._last_state)
                        if self._last_state is not None
                        else None
                    )
                    if ag_state is not None and upd_pid is not None:
                        self._resolve_planet(ag_state, upd_pid)
                    if ag_state is not None:
                        ag_actions, ag_opt = self._plan_with_summary(
                            ag_state,
                            config=new_cfg,
                            record_summary=True,
                            _already_enriched=True,
                        )
                    else:
                        ag_actions, ag_opt = [], None
                    upd_resp["action_graph"] = _redact_action_graph_strings(
                        build_action_graph(
                            ag_state,
                            ag_actions,
                            effective_config=new_cfg,
                            active_config=new_cfg,
                            simulated=False,
                            optimizer_summary=ag_opt,
                            planet_filter=upd_pid,
                            sanitize=False,
                        )
                    )
                return _sanitize_finite_json(upd_resp)

        if cmd == "action_graph":
            raw_ag_cfg = payload.get("config")
            if raw_ag_cfg is not None:
                if not isinstance(raw_ag_cfg, Mapping):
                    raise ApiRequestError("'action_graph' field 'config' must be a dictionary.")
                _validate_config_patch(raw_ag_cfg)
            ag_req_mode: Optional[str] = None
            if "planner_mode" in payload and payload["planner_mode"] is not None:
                raw_ag_mode = payload["planner_mode"]
                if not isinstance(raw_ag_mode, str) or not raw_ag_mode.strip():
                    raise ApiRequestError(
                        "'planner_mode' override must be a non-empty string ('priority' or 'ga_nn')."
                    )
                try:
                    ag_req_mode = normalize_planner_mode(raw_ag_mode, strict=True)
                except ValueError as exc:
                    raise ApiRequestError(_redact_api_payload(exc)) from exc
            has_ag_overrides = bool(raw_ag_cfg) or (ag_req_mode is not None)
            if "simulated" in payload and payload["simulated"] is not None:
                if not isinstance(payload["simulated"], bool):
                    raise ApiRequestError("'action_graph' field 'simulated' must be a boolean.")
                ag_simulated = bool(payload["simulated"])
            else:
                ag_simulated = has_ag_overrides
            ag_pid = _parse_command_int(
                "planet_id",
                payload.get("planet_id"),
                allow_none=True,
                min_val=1,
                max_val=2**63 - 1,
            )
            ag_refresh_raw = payload.get("refresh", False)
            if ag_refresh_raw is not None and not isinstance(ag_refresh_raw, bool):
                raise ApiRequestError("'action_graph' field 'refresh' must be a boolean.")
            ag_force_refresh = bool(ag_refresh_raw)
            if not ag_force_refresh:
                with self._lock:
                    effective_ag_cfg, _ = merge_strategy_config_overrides(
                        self.strategy_config,
                        raw_ag_cfg,
                        planner_mode_override=ag_req_mode,
                    )
                    ag_state = (
                        self._attach_raid_stats(self._last_state)
                        if self._last_state is not None
                        else None
                    )
                    if ag_state is not None and ag_pid is not None:
                        self._resolve_planet(ag_state, ag_pid)
                    if ag_state is not None:
                        ag_actions, ag_opt_summary = self._plan_with_summary(
                            ag_state,
                            config=effective_ag_cfg,
                            record_summary=(not ag_simulated and not has_ag_overrides),
                            _already_enriched=True,
                        )
                    else:
                        ag_actions, ag_opt_summary = [], None
                    graph_obj = _redact_action_graph_strings(
                        build_action_graph(
                            ag_state,
                            ag_actions,
                            effective_config=effective_ag_cfg,
                            active_config=self.strategy_config,
                            simulated=ag_simulated,
                            optimizer_summary=ag_opt_summary,
                            planet_filter=ag_pid,
                            sanitize=False,
                        )
                    )
                    return _sanitize_finite_json(
                        {
                            "ok": True,
                            "command": cmd,
                            "action_graph": graph_obj,
                        }
                    )

        if cmd == "galaxy_sweep":
            sweep_pages = _parse_command_int(
                "pages",
                payload.get("pages"),
                allow_none=True,
                min_val=1,
                max_val=400,
            )
            sweep_gal = _parse_command_int(
                "galaxy",
                payload.get("galaxy"),
                allow_none=True,
                min_val=1,
                max_val=14,
            )
            sweep_sys = _parse_command_int(
                "start_system",
                payload.get("start_system"),
                allow_none=True,
                min_val=1,
                max_val=400,
            )
            delay_min_ms = _parse_command_int(
                "delay_min_ms",
                payload.get("delay_min_ms"),
                allow_none=True,
                min_val=0,
                max_val=60_000,
            )
            delay_max_ms = _parse_command_int(
                "delay_max_ms",
                payload.get("delay_max_ms"),
                allow_none=True,
                min_val=0,
                max_val=60_000,
            )
            custom_delay: Optional[tuple[float, float]] = None
            if delay_min_ms is not None or delay_max_ms is not None:
                d_lo = float(delay_min_ms if delay_min_ms is not None else 0) / 1000.0
                d_hi = float(delay_max_ms if delay_max_ms is not None else delay_min_ms or 0) / 1000.0
                custom_delay = (min(d_lo, d_hi), max(d_lo, d_hi))
            return self.run_galaxy_sweep(
                pages=sweep_pages,
                galaxy=sweep_gal,
                start_system=sweep_sys,
                delay_range=custom_delay,
                dry_run=dry_run,
                priority="normal",
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
                        if planned.kind == "ESCAPE_FLIGHT" and not self._live_executor.allow_fleetsave:
                            return None
                        if planned.kind == "COLONIZE" and not self._live_executor.allow_colonize:
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
                        if self._last_state is not None and self._last_snapshot_at >= 0.0:
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
                                        not is_server_rate_limit_html(cur_html)
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

                    if cmd == "action_graph":
                        effective_ag_cfg, _ = merge_strategy_config_overrides(
                            self.strategy_config,
                            raw_ag_cfg,
                            planner_mode_override=ag_req_mode,
                        )
                        state = _cached_or_fetch(
                            include_buildings=True,
                            include_research=False,
                            include_shipyard=False,
                            priority="normal",
                        )
                        if ag_pid is not None:
                            self._resolve_planet(state, ag_pid)
                        actions, ag_opt_summary = self._plan_with_summary(
                            state,
                            config=effective_ag_cfg,
                            record_summary=(not ag_simulated and not has_ag_overrides),
                        )
                        graph_obj = build_action_graph(
                            state,
                            actions,
                            effective_config=effective_ag_cfg,
                            active_config=self.strategy_config,
                            simulated=ag_simulated,
                            optimizer_summary=ag_opt_summary,
                            planet_filter=ag_pid,
                        )
                        return _sanitize_finite_json(
                            _redact_api_payload(
                                {
                                    "ok": True,
                                    "command": cmd,
                                    "action_graph": graph_obj,
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
                        elif action == "internalMessages" and isinstance(payload.get("message_type"), str):
                            html = self._call_with_optional_priority(
                                client.open_messages,
                                payload["message_type"].strip(),
                                priority="interactive",
                            )
                        elif action == "internalBuildings" and payload.get("building_detail_id") is not None:
                            b_det_id = _parse_command_int(
                                "building_detail_id",
                                payload.get("building_detail_id"),
                                min_val=1,
                                max_val=99,
                            )
                            assert b_det_id is not None
                            html = self._call_with_optional_priority(
                                client.open_building_detail,
                                b_det_id,
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
                                    not is_server_rate_limit_html(html)
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
                                    self._economy_sync_timestamps[target_rate_pid] = time.time()
                                    if self._db is not None:
                                        p_coords = next(
                                            (p.coords for p in self._last_state.planets if p.planet_id == target_rate_pid),
                                            "",
                                        )
                                        self._db.save_economy_rates(target_rate_pid, p_coords, rates)
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
                        if self._last_state is not None and self._last_snapshot_at >= 0.0 and not force_refresh:
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

                    if cmd == "activate_transmitter":
                        pid = _parse_command_int(
                            "planet_id",
                            payload.get("planet_id"),
                            allow_none=True,
                            min_val=1,
                            max_val=2**63 - 1,
                        )
                        if self._last_state is not None and self._last_snapshot_at >= 0.0 and not force_refresh:
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
                        planned = PlannedAction(
                            priority="P2",
                            kind="ACTIVATE_TRANSMITTER",
                            planet_id=planet.planet_id,
                            planet_coords=planet.coords,
                            target_name="Interdimensional Transmitter",
                            reason="Activate daily free Interdimensional Transmitter spin (0 Urplasma)",
                            executable_now=True,
                        )
                        res_out = self._execute_and_summarize(
                            _exec_client(planned),
                            state,
                            cmd,
                            planned,
                            dry_run,
                        )
                        if isinstance(res_out, dict):
                            res_out["transmitter_status"] = self._get_transmitter_status_locked()
                        return res_out

                    if cmd in ("sync_spy_reports", "sync_messages"):
                        client = _get_client()
                        self._call_with_optional_priority(client.ensure_in_game, priority="interactive")
                        own_coords = self._own_planet_coords()
                        html = self._call_with_optional_priority(
                            client.open_messages,
                            "spio",
                            priority="interactive",
                        )
                        reports, ingested_cnt, del_ok, spio_post = self._ingest_and_delete_page_messages(
                            client,
                            html,
                            folder_type="spio",
                            own_coords=own_coords,
                            priority="interactive",
                            always_persist_spy=True,
                        )
                        folder_links = extract_message_folder_links(spio_post or html)
                        folder_counts: dict[str, dict[str, int]] = {
                            ftype: {"unread": int(ucnt), "total": int(tot)}
                            for ftype, _href, ucnt, tot in folder_links
                            if ftype
                        }
                        if cmd == "sync_messages":
                            spio_rem = folder_counts.get("spio", {}).get("total", 0)
                            if del_ok and spio_rem > 0 and self.rate_limit_remaining() <= 0.0:
                                spio2_html = self._call_with_optional_priority(
                                    client.open_messages,
                                    "spio",
                                    priority="interactive",
                                )
                                rep2, ing2, del2, spio_post2 = self._ingest_and_delete_page_messages(
                                    client,
                                    spio2_html,
                                    folder_type="spio",
                                    own_coords=own_coords,
                                    priority="interactive",
                                    always_persist_spy=True,
                                )
                                if rep2:
                                    reports.extend(rep2)
                                ingested_cnt += ing2
                                del_ok = del_ok or del2
                                for ft2, _h2, u2, t2 in extract_message_folder_links(spio_post2 or spio2_html):
                                    if ft2:
                                        folder_counts[ft2] = {"unread": int(u2), "total": int(t2)}
                            if not folder_links and not reports and ingested_cnt == 0:
                                inbox_html = self._call_with_optional_priority(
                                    client.open_messages,
                                    "",
                                    priority="interactive",
                                )
                                inb_reports, inb_cnt, inb_del, inb_post = self._ingest_and_delete_page_messages(
                                    client,
                                    inbox_html,
                                    folder_type="inbox",
                                    own_coords=own_coords,
                                    priority="interactive",
                                    always_persist_spy=True,
                                )
                                if inb_reports:
                                    reports.extend(inb_reports)
                                ingested_cnt += inb_cnt
                                del_ok = del_ok or inb_del
                                for ft_i, _hi, ui, ti in extract_message_folder_links(inb_post or inbox_html):
                                    if ft_i:
                                        folder_counts[ft_i] = {"unread": int(ui), "total": int(ti)}
                            for ftype in ("battle", "fleet", "normal", "alliance", "admin"):
                                ft_tot = folder_counts.get(ftype, {}).get("total", 0)
                                if ft_tot <= 0 or self.rate_limit_remaining() > 0.0:
                                    continue
                                for _pass in range(2 if ft_tot > 12 else 1):
                                    if self.rate_limit_remaining() > 0.0:
                                        break
                                    f_html = self._call_with_optional_priority(
                                        client.open_messages,
                                        ftype,
                                        priority="interactive",
                                    )
                                    f_rep, f_ing, f_del, f_post = self._ingest_and_delete_page_messages(
                                        client,
                                        f_html,
                                        folder_type=ftype,
                                        own_coords=own_coords,
                                        priority="interactive",
                                        always_persist_spy=True,
                                    )
                                    if f_rep:
                                        reports.extend(f_rep)
                                    ingested_cnt += f_ing
                                    del_ok = del_ok or f_del
                                    for ftu, _hu, uu, tu in extract_message_folder_links(f_post or f_html):
                                        if ftu == ftype:
                                            folder_counts[ftype] = {"unread": int(uu), "total": int(tu)}
                                    if not f_del or folder_counts.get(ftype, {}).get("total", 0) <= 0:
                                        break
                        else:
                            last_unread = (
                                (self._last_state.unread_messages or {})
                                if self._last_state is not None
                                else {}
                            )
                            should_check_inbox = (
                                _safe_api_int(last_unread.get("spio", 0)) <= 0
                                and _safe_api_int(last_unread.get("total", 0)) > 0
                            )
                            if not reports and ingested_cnt == 0 and should_check_inbox:
                                inbox_html = self._call_with_optional_priority(
                                    client.open_messages,
                                    "",
                                    priority="interactive",
                                )
                                inb_reports, inb_cnt, inb_del, _ = self._ingest_and_delete_page_messages(
                                    client,
                                    inbox_html,
                                    folder_type="inbox",
                                    own_coords=own_coords,
                                    priority="interactive",
                                    always_persist_spy=True,
                                )
                                reports = inb_reports
                                ingested_cnt += inb_cnt
                                del_ok = del_ok or inb_del
                        if del_ok:
                            self._last_synced_unread_total = 0
                        if self._last_state is not None:
                            self._refresh_cached_economy(self._last_state)
                        return _sanitize_finite_json(
                            {
                                "ok": True,
                                "command": cmd,
                                "synced_reports": len(reports),
                                "archived_messages": ingested_cnt,
                                "deleted_in_game": del_ok,
                                "folder_counts": folder_counts,
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

                    if cmd == "recycle":
                        raw_target_coords = payload.get("target_coords")
                        if not isinstance(raw_target_coords, str):
                            raise ApiRequestError(
                                "'recycle' command requires 'target_coords' formatted as GG:SSS:PP."
                            )
                        target_coords = raw_target_coords.strip()
                        m_rec = re.search(r"(\d{1,2}:\d{1,3}:\d{1,2})", target_coords, re.ASCII)
                        if not m_rec:
                            raise ApiRequestError(
                                "'recycle' command requires 'target_coords' formatted as GG:SSS:PP."
                            )
                        norm_target = normalize_coords(m_rec.group(1))
                        pid = _parse_command_int(
                            "planet_id",
                            payload.get("planet_id"),
                            allow_none=True,
                            min_val=1,
                            max_val=2**63 - 1,
                        )
                        state = _resolve_fleet_command_state(pid, is_spy=True)
                        if pid is None:
                            rec_p = next(
                                (p for p in state.planets if _safe_api_int(p.ships.get("s11", 0)) > 0),
                                None,
                            )
                            planet = rec_p or self._resolve_planet(state, None)
                        else:
                            planet = self._resolve_planet(state, pid)
                        default_rec_qty = max(1, _safe_api_int(planet.ships.get("s11", 0), 1))
                        qty_parsed = _parse_command_int(
                            "quantity",
                            payload.get("quantity", default_rec_qty),
                            min_val=1,
                            max_val=1_000_000_000,
                        )
                        assert qty_parsed is not None
                        planned = PlannedAction(
                            priority="P2",
                            kind="RECYCLE",
                            planet_id=planet.planet_id,
                            planet_coords=planet.coords,
                            target_id=11,
                            target_level=qty_parsed,
                            target_name=f"Rubble -> {norm_target}",
                            reason="API recycle command",
                            executable_now=True,
                        )
                        return self._execute_and_summarize(_exec_client(planned), state, cmd, planned, dry_run)

                    if cmd == "colonize":
                        raw_target_coords = payload.get("target_coords")
                        if not isinstance(raw_target_coords, str):
                            raise ApiRequestError(
                                "'colonize' command requires 'target_coords' formatted as GG:SSS:PP."
                            )
                        target_coords = raw_target_coords.strip()
                        m_col = re.search(r"(\d{1,2}:\d{1,3}:\d{1,2})", target_coords, re.ASCII)
                        if not m_col:
                            raise ApiRequestError(
                                "'colonize' command requires 'target_coords' formatted as GG:SSS:PP."
                            )
                        norm_target = normalize_coords(m_col.group(1))
                        pid = _parse_command_int(
                            "planet_id",
                            payload.get("planet_id"),
                            allow_none=True,
                            min_val=1,
                            max_val=2**63 - 1,
                        )
                        state = _resolve_fleet_command_state(pid, is_spy=True)
                        if pid is None:
                            col_p = next(
                                (p for p in state.planets if _safe_api_int(p.ships.get("s8", 0)) > 0),
                                None,
                            )
                            planet = col_p or self._resolve_planet(state, None)
                        else:
                            planet = self._resolve_planet(state, pid)
                        planned = PlannedAction(
                            priority="P2",
                            kind="COLONIZE",
                            planet_id=planet.planet_id,
                            planet_coords=planet.coords,
                            target_id=8,
                            target_level=1,
                            target_name=f"Colonize -> {norm_target}",
                            reason="API colonize command",
                            executable_now=True,
                        )
                        if not dry_run and not self._live_executor.allow_colonize:
                            return self._execute_and_summarize(_exec_client(planned), state, cmd, planned, dry_run)
                        if any(normalize_coords(p.coords) == norm_target for p in state.planets):
                            planned_own = replace(
                                planned,
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
                                        "message": f"Aborted: {norm_target} is already one of your own planets.",
                                        "action": self._serialize_action(planned_own, state),
                                        "next_action": None,
                                    }
                                )
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
                                until_cd = bash.blocked_until(norm_target)
                                rem_cd = max(0.0, until_cd - time.time()) if until_cd > 0.0 else 0.0
                                cd_note = f" ({rem_cd:.0f}s cooldown remaining)" if rem_cd > 0.0 else ""
                                return _sanitize_finite_json(
                                    _redact_api_payload(
                                        {
                                            "ok": True,
                                            "command": cmd,
                                            "executed": False,
                                            "dry_run": dry_run,
                                            "message": f"Aborted: BashTracker 3/24h limit reached for {norm_target}{cd_note}.",
                                            "action": self._serialize_action(planned, state),
                                            "next_action": None,
                                        }
                                    )
                                )
                        elif norm_target in self._target_blocks:
                            mem_until, _ = self._target_blocks[norm_target]
                            rem_cd = mem_until - time.time()
                            if rem_cd > 0.0:
                                return _sanitize_finite_json(
                                    _redact_api_payload(
                                        {
                                            "ok": True,
                                            "command": cmd,
                                            "executed": False,
                                            "dry_run": dry_run,
                                            "message": f"Aborted: BashTracker 3/24h limit reached for {norm_target} ({rem_cd:.0f}s cooldown remaining).",
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

                    if cmd == "demolish_building":
                        raw_bid = payload.get("building_id", 6)
                        bid = _parse_command_int(
                            "building_id", raw_bid, min_val=1, max_val=2**31 - 1
                        )
                        assert bid is not None
                        pid = _parse_command_int(
                            "planet_id",
                            payload.get("planet_id"),
                            allow_none=True,
                            min_val=1,
                            max_val=2**63 - 1,
                        )
                        state = _cached_or_fetch(
                            include_buildings=bool(force_refresh or (dry_run and self._last_state is None)),
                            include_research=False,
                            include_shipyard=False,
                            include_home=False,
                            priority="interactive",
                        )
                        planet = self._resolve_planet(state, pid)
                        binfo = game_data.BUILDING_BY_ID.get(bid)
                        bname = (
                            str(binfo.get("name") or f"Building {bid}")
                            if isinstance(binfo, Mapping)
                            else f"Building {bid}"
                        )
                        cur_lvl = planet.building_level(bid)
                        ptype_norm = normalize_planet_type(planet)
                        active_p = state.active_planet()
                        is_active_p = active_p is not None and planet.planet_id == active_p.planet_id
                        specs_map = state.specs if isinstance(state.specs, Mapping) else {}
                        default_slots = (
                            1
                            if ptype_norm == "asteroid"
                            else (6 if _safe_api_int(specs_map.get("construction_commander", 0), 0) > 0 else 5)
                        )
                        max_slots = max(
                            1,
                            _safe_api_int(specs_map.get("max_build_slots", default_slots), default_slots),
                        )
                        planet_b_jobs = [
                            q
                            for q in (state.active_queues or ())
                            if getattr(q, "category", None) == "building"
                            and (
                                (q_pid == planet.planet_id)
                                if (q_pid := game_data.safe_optional_int(getattr(q, "planet_id", None), None))
                                is not None
                                else is_active_p
                            )
                        ]
                        q_busy = len(planet_b_jobs) >= max_slots
                        b_in_queue = any(
                            game_data.safe_optional_int(getattr(q, "building_id", None), None) == bid
                            or bname.lower() in str(getattr(q, "name", "") or "").lower()
                            or (bid == 6 and "thermal" in str(getattr(q, "name", "") or "").lower())
                            for q in planet_b_jobs
                        )
                        can_dem = True
                        if cur_lvl < 1:
                            can_dem = False
                            reason = f"{bname} is already at Level 0"
                        elif ptype_norm in ("asteroid", "raid"):
                            can_dem = False
                            reason = f"Building demolition unavailable on {ptype_norm}"
                        elif q_busy or b_in_queue:
                            can_dem = False
                            reason = "Building queue busy"
                        else:
                            reason = f"Demolish {bname} L{cur_lvl} -> L{cur_lvl - 1} to reclaim 1 field"
                            raw_loss = max(0, game_data.building_energy_delta(bid, cur_lvl))
                            if raw_loss > 0:
                                r2 = max(
                                    0,
                                    min(
                                        200,
                                        _safe_api_int(
                                            (
                                                state.researches
                                                if isinstance(state.researches, Mapping)
                                                else {}
                                            ).get("r2", 0),
                                            0,
                                        ),
                                    ),
                                )
                                safe_loss = int(math.ceil(raw_loss * (1.0 + 0.01 * r2)))
                                throttled_res = 0
                                econ_map = planet.economy_rates if isinstance(planet.economy_rates, Mapping) else {}
                                for mbid, rkey in (
                                    (1, "resource1"),
                                    (2, "resource2"),
                                    (3, "resource3"),
                                    (4, "resource4"),
                                ):
                                    mlvl = planet.building_level(mbid)
                                    if mlvl <= 0:
                                        continue
                                    mpct = max(0, min(100, _safe_api_int(econ_map.get(rkey, 100), 100)))
                                    if mpct < 100:
                                        full_e = game_data.mine_energy_needed(mbid, mlvl)
                                        throttled_res += max(0, int(math.ceil(full_e * (100 - mpct) / 100.0)))
                                if _safe_api_int(planet.energy.net, 0) - throttled_res - safe_loss < 0:
                                    can_dem = False
                                    reason = (
                                        f"Insufficient spare energy to demolish {bname} safely "
                                        f"(upgrade Fusion plant first)"
                                    )
                        planned = PlannedAction(
                            priority="P1",
                            kind="DEMOLISH_BUILDING",
                            planet_id=planet.planet_id,
                            planet_coords=planet.coords,
                            target_id=bid,
                            target_name=bname,
                            target_level=max(0, cur_lvl - 1),
                            reason=reason,
                            executable_now=can_dem,
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

                    if cmd == "sync_economy":
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
                            priority="normal",
                        )
                        planet = self._resolve_planet(state, pid)
                        ptype_norm = normalize_planet_type(planet)
                        if ptype_norm in ("asteroid", "raid"):
                            return _sanitize_finite_json(
                                _redact_api_payload(
                                    {
                                        "ok": True,
                                        "command": cmd,
                                        "planet_id": planet.planet_id,
                                        "economy_rates": {},
                                        "blast_furnace_pct": 0,
                                        "metal_production_enabled": False,
                                        "state": asdict(self._attach_raid_stats(state)),
                                    }
                                )
                            )
                        client = _get_client()
                        e_html = self._live_executor._navigate_and_select_planet(
                            client,
                            state,
                            "internalEconomy",
                            planet.planet_id,
                        )
                        rates = parse_economy_rates(e_html)
                        if rates:
                            self._economy_rates[planet.planet_id] = dict(rates)
                            self._economy_sync_timestamps[planet.planet_id] = time.time()
                            if self._db is not None:
                                self._db.save_economy_rates(planet.planet_id, planet.coords, rates)
                        synced_state = self._refresh_cached_economy(self._last_state or state)
                        bf_pct = _safe_api_int(
                            (self._economy_rates.get(planet.planet_id) or rates or {}).get("resource2", 100),
                            100,
                        )
                        return _sanitize_finite_json(
                            _redact_api_payload(
                                {
                                    "ok": True,
                                    "command": cmd,
                                    "planet_id": planet.planet_id,
                                    "economy_rates": dict(self._economy_rates.get(planet.planet_id) or rates or {}),
                                    "blast_furnace_pct": bf_pct,
                                    "metal_production_enabled": bf_pct > 0,
                                    "state": asdict(self._attach_raid_stats(synced_state)),
                                }
                            )
                        )

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

                    if cmd == "escape_flight":
                        pid = _parse_command_int(
                            "planet_id",
                            payload.get("planet_id"),
                            allow_none=True,
                            min_val=1,
                            max_val=2**63 - 1,
                        )
                        raw_tc = payload.get("target_coords")
                        if raw_tc is not None and not isinstance(raw_tc, str):
                            raise ApiRequestError(
                                "'escape_flight' field 'target_coords' must be a string."
                            )
                        raw_tn = payload.get("target_name")
                        if raw_tn is not None and not isinstance(raw_tn, str):
                            raise ApiRequestError(
                                "'escape_flight' field 'target_name' must be a string."
                            )
                        raw_reason = payload.get("reason")
                        if raw_reason is not None and not isinstance(raw_reason, str):
                            raise ApiRequestError(
                                "'escape_flight' field 'reason' must be a string."
                            )

                        state = _resolve_fleet_command_state(pid, is_spy=False)
                        planet = self._resolve_planet(state, pid)

                        t_coords = ""
                        tc_str = raw_tc.strip() if isinstance(raw_tc, str) else ""
                        tn_str = raw_tn.strip() if isinstance(raw_tn, str) else ""
                        if tc_str:
                            m_tc = re.search(r"(\d{1,2}):(\d{1,3}):(\d{1,2})", tc_str, re.ASCII)
                            if not m_tc:
                                raise ApiRequestError(
                                    "'escape_flight' field 'target_coords' must contain GG:SSS:PP."
                                )
                            t_coords = normalize_coords(m_tc.group(0))
                        elif tn_str:
                            m_tn = re.search(r"(\d{1,2}):(\d{1,3}):(\d{1,2})", tn_str, re.ASCII)
                            if m_tn:
                                t_coords = normalize_coords(m_tn.group(0))

                        if not t_coords:
                            m_origin = re.search(
                                r"(\d{1,2}):(\d{1,3}):(\d{1,2})",
                                str(planet.coords or ""),
                                re.ASCII,
                            )
                            if m_origin:
                                gal_o = int(m_origin.group(1))
                                sys_o = int(m_origin.group(2))
                                pos_o = int(m_origin.group(3))
                                t_coords = f"{gal_o:02d}:{sys_o:03d}:{(pos_o % 16) + 1:02d}"
                            else:
                                t_coords = "01:001:02"

                        esc_reason = (
                            _bounded_api_str(raw_reason.strip(), default="API escape_flight command")
                            if isinstance(raw_reason, str) and raw_reason.strip()
                            else "API escape_flight command"
                        )
                        planned = PlannedAction(
                            priority="P2",
                            kind="ESCAPE_FLIGHT",
                            planet_id=planet.planet_id,
                            planet_coords=planet.coords,
                            target_name=f"Save -> {t_coords}",
                            reason=esc_reason,
                            executable_now=bool(self.strategy_config.allow_fleetsave),
                        )
                        return self._execute_and_summarize(
                            _exec_client(planned),
                            state,
                            cmd,
                            planned,
                            dry_run,
                            return_home=True,
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
                if self.log_dir is not None:
                    append_crash_debug_log(
                        component="api_server.handle_command",
                        event=f"rate_limit:{cmd}",
                        error=exc,
                        details={"command": cmd, "payload": payload},
                    )
                raise
            except AntiBotChallengeError as exc:
                self._record_antibot_pause(exc)
                if self.log_dir is not None:
                    append_crash_debug_log(
                        component="api_server.handle_command",
                        event=f"antibot_challenge:{cmd}",
                        error=exc,
                        details={"command": cmd, "payload": payload},
                    )
                raise
            except (SpaceInvasionAuthError, Exception) as exc:
                if self.log_dir is not None and not isinstance(exc, ApiRequestError):
                    append_crash_debug_log(
                        component="api_server.handle_command",
                        event=f"command_error:{cmd}",
                        error=exc,
                        details={"command": cmd, "payload": payload},
                    )
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

    def _build_antibot_response(exc: AntiBotChallengeError) -> dict[str, Any]:
        rec_fn = getattr(bridge, "_record_antibot_pause", None)
        if callable(rec_fn):
            try:
                rec_fn(exc)
            except Exception:
                pass
        err_txt = str(_redact_api_payload(exc)).strip()
        if not any(
            tok in err_txt.lower()
            for tok in ("captcha", "anti-bot", "antibot", "turnstile", "sicherheitspr")
        ):
            err_txt = (
                f"CAPTCHA / anti-bot challenge: {err_txt}"
                if err_txt
                else "CAPTCHA / anti-bot challenge detected."
            )
        return {
            "ok": False,
            "captcha_detected": True,
            "paused": True,
            "error": _redact_api_payload(err_txt),
        }

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
                    ico_path = UI_DIR / "favicon.ico"
                    if ico_path.is_file():
                        self._send_static("favicon.ico", "image/x-icon")
                        return
                    svg_path = UI_DIR / "favicon.svg"
                    if svg_path.is_file():
                        self._send_static("favicon.svg", "image/svg+xml")
                        return
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
            except AntiBotChallengeError as exc:
                self._send_json(HTTPStatus.SERVICE_UNAVAILABLE, _build_antibot_response(exc))
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
            except AntiBotChallengeError as exc:
                status_code = HTTPStatus.SERVICE_UNAVAILABLE
                body = _build_antibot_response(exc)
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
