"""Local HTTP JSON API wrapping the live SpaceInvasion Chrome debug session."""

from __future__ import annotations

import contextlib
from dataclasses import asdict, replace
from datetime import datetime
import errno
import functools
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
import sys
import threading
import time
from typing import Any, Callable, Deque, Optional, Sequence
from urllib.parse import parse_qs, urlparse

from spaceinvasion.core import game_data
from spaceinvasion.planner.action_graph import (
    build_action_graph,
    merge_strategy_config_overrides,
)
from spaceinvasion.session.auth import AntiBotChallengeError, SpaceInvasionAuthError, load_credentials
from spaceinvasion.session.browser import (
    DEFAULT_DEBUG_HOST,
    DEFAULT_DEBUG_PORT,
    DEFAULT_PROFILE_ROOT,
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
    ARROW_NAV_MAX_DISTANCE,
    SHIP_SLUG_BY_KEY,
    InboxMessage,
    SpyReport,
    _parse_coords_distance,
    extract_galaxy_system_coords,
    extract_message_folder_links,
    fleet_cargo_capacity,
    format_station_ships_spec,
    normalize_coords,
    parse_galaxy_system_html,
    parse_inbox_messages_html,
    parse_spy_messages_html,
    parse_station_ships_spec,
    plan_escape_flight,
    score_galaxy_scan_sectors,
    select_smart_galaxy_scan_batch,
    simulate_raid_outcome,
)
from spaceinvasion.core.logging_utils import (
    _SID_RE as _LOG_SID_RE,
    _sub_sid_kv as _sub_log_sid_kv,
    append_api_log,
    append_crash_debug_log,
    format_clean_error,
    is_secret_dict_key,
    redact_sensitive,
)
from spaceinvasion.combat.ranking import (
    attach_action_etas,
    build_buildings_eta,
    build_combat_overview,
    build_ranking_evolution,
    estimate_hourly_economy,
    estimate_planet_hourly_economy,
    record_rank_snapshot,
    simulate_combat_matchup,
)
from spaceinvasion.core.state import (
    BUILDING_ID_BY_NAME,
    GameState,
    PlanetState,
    QueueJob,
    Resources,
    count_allocated_normal_planets,
    count_in_flight_fleets_for_planet,
    count_in_flight_ships_by_planet,
    count_in_flight_spy_fleets_for_planet,
    extract_in_flight_recycle_coords,
    homeworld_planet_id,
    is_building_allowed_on_planet_type,
    is_in_flight_fleet_job,
    is_undersized_fresh_colony,
    normalize_planet_type,
    parse_daily_login_bonus,
    parse_economy_rates,
    parse_home_metadata,
    parse_si_int,
    parse_tutorial_from_home,
    parse_universe_research_records,
    parse_unread_banner_count,
)
from spaceinvasion.db.backend import (
    ResolvedDatabaseConfig,
    build_default_postgres_url,
    ensure_postgres_container_ready,
    get_podman_postgres_status,
    is_container_runtime,
    is_postgres_dsn,
    normalize_backend_name,
    ping_postgres,
    redact_db_url,
    resolve_database_config,
)
from spaceinvasion.db.storage import GameDatabase, server_day_key
from spaceinvasion.planner.strategy import (
    AI_INFRA_CARGO_TAG,
    PlannedAction,
    _tutorial_colonize_task,
    compute_spare_ship_budget,
    merge_manual_queue_actions,
    plan_actions,
    plan_ai_blast_furnace_throttles,
    plan_fleet_and_attack_actions,
)

REPO_ROOT_DIR = Path(__file__).resolve().parents[2]
UI_DIR = Path(__file__).resolve().parents[1] / "ui"
MAX_REQUEST_BYTES = 1_048_576
_SERVER_BOOT_ID = secrets.token_hex(4)
_API_SID_RE = re.compile(_LOG_SID_RE.pattern, _LOG_SID_RE.flags)


def _fleet_subrank(a: PlannedAction) -> int:
    if a.priority == "P1":
        if a.kind == "ABANDON_COLONY":
            return 0
        if a.kind == "SPY":
            return 1 if "(free slot)" in str(a.target_name or "").lower() else 2
        if a.kind == "TRANSPORT":
            return 3
        if a.kind == "COLONIZE":
            return 4
        return 5
    if a.priority == "P2":
        if a.kind == "CLAIM_TUTORIAL":
            return 0
        if a.kind == "SPY":
            return 1
        if a.kind == "TRANSPORT":
            return 2
        if a.kind == "COLONIZE":
            return 3
        if a.kind == "STATION":
            return 4
        return 5
    return 0


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
        "internalStatistics",
        "internalToplist",
        "internalSettings",
    }
)


class ApiRequestError(ValueError):
    """Raised when an API request payload is malformed or unsupported."""


class GalaxyScanInProgressError(RuntimeError):
    """Raised when a non-emergency live browser action is requested during an active galaxy scan."""


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


@functools.lru_cache(maxsize=512)
def _is_clean_api_key(key: str) -> bool:
    """Fast cached check for short ASCII dict keys containing no secret tokens."""
    if not key or len(key) > 64 or not key.isascii():
        return False
    return _scrub_api_str(key) == key and not is_secret_dict_key(key)


def _redact_api_payload(
    value: Any,
    *,
    _depth: int = 0,
    _seen: Optional[set[int]] = None,
    _budget: Optional[list[int]] = None,
) -> Any:
    """Recursively scrub session tokens and secret key-value fields from API responses."""
    tv = type(value)
    if value is None or tv is bool or tv is int or tv is float:
        return value
    if tv is str:
        return value if (len(value) < 3 and value.isascii()) else _scrub_api_str(value)
    if tv is dict:
        seen = _seen if _seen is not None else set()
        budget = _budget if _budget is not None else [_MAX_API_TREE_NODES]
        vid = id(value)
        if vid in seen:
            return "[Cycle]"
        if _depth >= _MAX_API_TREE_DEPTH or budget[0] <= 0:
            return "[Truncated]"
        budget[0] -= 1
        seen.add(vid)
        try:
            out: dict[Any, Any] = {}
            next_depth = _depth + 1
            for k, v in value.items():
                if budget[0] <= 0:
                    out["[Truncated]"] = "[Truncated]"
                    break
                if type(k) is str and _is_clean_api_key(k):
                    tv_v = type(v)
                    if v is None or tv_v is bool or tv_v is int or tv_v is float:
                        out[k] = v
                    elif tv_v is str:
                        out[k] = v if (len(v) < 3 and v.isascii()) else _scrub_api_str(v)
                    else:
                        out[k] = _redact_api_payload(
                            v, _depth=next_depth, _seen=seen, _budget=budget
                        )
                    continue
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
                        v, _depth=next_depth, _seen=seen, _budget=budget
                    )
            return out
        finally:
            seen.discard(vid)
    if tv is list:
        seen = _seen if _seen is not None else set()
        budget = _budget if _budget is not None else [_MAX_API_TREE_NODES]
        vid = id(value)
        if vid in seen:
            return "[Cycle]"
        if _depth >= _MAX_API_TREE_DEPTH or budget[0] <= 0:
            return "[Truncated]"
        budget[0] -= 1
        seen.add(vid)
        try:
            out_list: list[Any] = []
            next_depth = _depth + 1
            for v in value:
                if budget[0] <= 0:
                    out_list.append("[Truncated]")
                    break
                tv_v = type(v)
                if v is None or tv_v is bool or tv_v is int or tv_v is float:
                    out_list.append(v)
                elif tv_v is str:
                    out_list.append(v if (len(v) < 3 and v.isascii()) else _scrub_api_str(v))
                else:
                    out_list.append(
                        _redact_api_payload(v, _depth=next_depth, _seen=seen, _budget=budget)
                    )
            return out_list
        finally:
            seen.discard(vid)
    if isinstance(value, str):
        return _scrub_api_str(value)
    if isinstance(value, (bool, int, float)):
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
                out_map: dict[Any, Any] = {}
                for k, v in value.items():
                    if budget[0] <= 0:
                        out_map["[Truncated]"] = "[Truncated]"
                        break
                    clean_k = _redact_api_key(k)
                    if isinstance(clean_k, str) and is_secret_dict_key(clean_k):
                        if v is None or v == "":
                            out_map[clean_k] = v
                        elif isinstance(v, (bytes, bytearray)) and len(v) == 0:
                            out_map[clean_k] = ""
                        else:
                            out_map[clean_k] = "REDACTED"
                    else:
                        out_map[clean_k] = _redact_api_payload(
                            v, _depth=_depth + 1, _seen=seen, _budget=budget
                        )
                return out_map
            out_seq: list[Any] = []
            for v in value:
                if budget[0] <= 0:
                    out_seq.append("[Truncated]")
                    break
                out_seq.append(
                    _redact_api_payload(v, _depth=_depth + 1, _seen=seen, _budget=budget)
                )
            return out_seq
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
                "'planner_mode' must be a non-empty string ('priority' or 'ai_planner')."
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
        "action_batch_interval_seconds": (5, 86400, False),
        "max_actions_per_hour": (1, 100_000, False),
        "page_view_limit_per_minute": (6, 600, False),
        "min_free_fields_warning": (1, 1000, False),
        "min_colony_fields": (50, 600, False),
        "max_planets": (1, 50, False),
        "galaxy_sweep_interval_seconds": (30, 86400, False),
        "galaxy_sweep_pages": (1, 400, False),
        "galaxy_scan_refresh_pct": (0, 100, False),
        "reserved_spy_slots": (0, 10, False),
        "hot_planet_solar_temp_threshold": (-273, 1000, False),
        "fleet_save_fuel_reserve_spice": (0, 1_000_000_000_000, False),
    }
    for key, (lo, hi, allow_none) in int_specs.items():
        if key in raw_cfg:
            _validate_strict_int(key, raw_cfg[key], min_val=lo, max_val=hi, allow_none=allow_none)
    if "spy_dispatch_planet_id" in raw_cfg:
        raw_spy_pid = raw_cfg["spy_dispatch_planet_id"]
        if raw_spy_pid not in (None, ""):
            _validate_strict_int(
                "spy_dispatch_planet_id",
                raw_spy_pid,
                min_val=0,
                max_val=2**63 - 1,
                allow_none=False,
            )

    float_specs: dict[str, tuple[Optional[float], Optional[float]]] = {
        "ai_resource_spare_pct": (0.0, 100.0),
        "ga_mutation_rate": (0.0, 1.0),
        "storage_warn_ratio": (0.1, 0.99),
        "blast_pig_iron_max_share": (0.1, 1.0),
        "combat_safety_margin": (1.0, 1000.0),
        "raid_min_loot_spice_eq": (0.0, 1e12),
        "attack_min_res_per_hour": (0.0, 1e12),
        "recycle_min_res_per_hour": (0.0, 1e12),
        "galaxy_scan_click_delay_seconds": (0.2, 60.0),
        "galaxy_scan_click_jitter_seconds": (0.0, 30.0),
        "galaxy_scan_near_weight": (0.0, 20.0),
        "galaxy_scan_attack_weight": (0.0, 20.0),
    }
    for key, (lo, hi) in float_specs.items():
        if key in raw_cfg:
            _validate_strict_float(key, raw_cfg[key], min_val=lo, max_val=hi)

    bool_keys: tuple[str, ...] = (
        "merchant_mode",
        "auto_throttle_blast_furnace",
        "allow_research",
        "allow_fleet_research",
        "allow_shipyard",
        "allow_multi_planet",
        "allow_fleetsave",
        "allow_galaxy_scan",
        "galaxy_scan_block_other_actions",
        "allow_raids",
        "allow_pvp_in_az",
        "allow_colonize",
        "allow_transport",
        "allow_transmitter",
        "forbid_planetary_solar",
        "galaxy_scan_explore_first",
        "galaxy_scan_all_galaxies",
        "spy_reserved_slots_only",
        "ai_adaptive_execution",
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

    if "fleet_research_targets" in raw_cfg:
        raw_fr = raw_cfg["fleet_research_targets"]
        if not isinstance(raw_fr, Mapping):
            raise ApiRequestError(
                "Config field 'fleet_research_targets' must be a mapping of research keys to integer target levels."
            )
        for rk, rv in raw_fr.items():
            if not isinstance(rk, str) or not rk.strip():
                raise ApiRequestError(
                    "Config field 'fleet_research_targets' keys must be non-empty strings."
                )
            _validate_strict_int(
                f"fleet_research_targets.{rk.strip()}",
                rv,
                min_val=0,
                max_val=100,
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
                    next_d = d + 1
                    for k, v in val.items():
                        if budget[0] <= 0:
                            out_d["[Truncated]"] = "[Truncated]"
                            break
                        if type(k) is str and _is_clean_api_key(k):
                            tv_v = type(v)
                            if v is None or tv_v is bool:
                                out_d[k] = v
                            elif tv_v is str:
                                out_d[k] = v if len(v) < 5 else _scrub_api_str(v)
                            elif tv_v is int:
                                if -_MAX_API_JSON_INT <= v <= _MAX_API_JSON_INT:
                                    out_d[k] = v
                                else:
                                    out_d[k] = _MAX_API_JSON_INT if v > 0 else -_MAX_API_JSON_INT
                            elif tv_v is float:
                                out_d[k] = v if math.isfinite(v) else 0.0
                            else:
                                out_d[k] = _walk(v, next_d)
                            continue
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
                            out_d[clean_k] = _walk(v, next_d)
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
            raise SpaceInvasionAuthError(
                f"Remote browser container at {eff_host}:{debug_port} is not reachable."
            )
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


_COORDS_INLINE_RE = re.compile(r"(\d{1,2}:\d{1,3}:\d{1,2})", re.ASCII)
_SHIP_NUM_FIRST_RE = re.compile(r"^\s*([0-9][0-9.,']*)\s*(?:x\s*)?([A-Za-zÀ-ÿ0-9_\-\s]{1,40})\s*$")
_SHIP_NAME_FIRST_RE = re.compile(r"^\s*([A-Za-zÀ-ÿ0-9_\-\s]{1,40}?)\s*[:=x]\s*([0-9][0-9.,']*)\s*$")
_BLDG_LVL_HINT_RE = re.compile(
    r"(?:early\s+pre-ship\s*#\d+\s*:\s*)?([A-Za-z][A-Za-z\s\-]{1,48}?)\s*(?:->\s*)?(?:L|level\s*)(\d{1,3})\b",
    re.IGNORECASE,
)


def _build_ship_name_to_key_map() -> dict[str, str]:
    mapping: dict[str, str] = {}
    for skey, slug in SHIP_SLUG_BY_KEY.items():
        mapping[skey.lower()] = skey
        mapping[slug.lower()] = skey
        mapping[slug.lower().replace("-", " ")] = skey
        info = game_data.resolve_unit("ships", slug)
        if isinstance(info, Mapping):
            nm = str(info.get("name") or "").strip().lower()
            if nm:
                mapping[nm] = skey
                if not nm.endswith("s"):
                    mapping[nm + "s"] = skey
    mapping.update(
        {
            "small transports": "s1",
            "kleiner transporter": "s1",
            "large transports": "s2",
            "großer transporter": "s2",
            "grosser transporter": "s2",
            "transmitters": "s3",
            "small fighters": "s4",
            "kleiner jäger": "s4",
            "kleiner jaeger": "s4",
            "warships": "s5",
            "kriegsschiff": "s5",
            "kriegsschiffe": "s5",
            "starcruisers": "s6",
            "sternkreuzer": "s6",
            "phoenixes": "s7",
            "phönix": "s7",
            "phonix": "s7",
            "nixe": "s7",
            "colony ships": "s8",
            "kolonieschiff": "s8",
            "solar cell": "s9",
            "solarzelle": "s9",
            "solarzellen": "s9",
            "spy probes": "s10",
            "spionagesonde": "s10",
            "spionagesonden": "s10",
            "recyclers": "s11",
            "battleships": "s13",
            "schlachtschiff": "s13",
            "schlachtschiffe": "s13",
            "stealth bombers": "s14",
            "tarnkappenbomber": "s14",
            "destroyers": "s15",
            "zerstörer": "s15",
            "zerstoerer": "s15",
            "imperial starbases": "s16",
            "imperiale sternenbasis": "s16",
        }
    )
    return mapping


_SHIP_NAME_TO_KEY: dict[str, str] = _build_ship_name_to_key_map()


def _extract_coords_from_text(text: Any) -> str:
    if not text:
        return ""
    m = _COORDS_INLINE_RE.search(str(text))
    return normalize_coords(m.group(1)) if m else ""


def _parse_ships_from_queue_detail(detail: str) -> dict[str, int]:
    """Parse ship counts from `QueueJob.detail` (e.g. `'Outbound · 12 Recycler, 4 Large transport'`)."""
    txt = str(detail or "").strip()
    if not txt:
        return {}
    if "·" in txt:
        head, tail = txt.split("·", 1)
        if head.strip().lower() in ("outbound", "returning", "in flight", "flight"):
            txt = tail.strip()
    if not txt:
        return {}
    ships: dict[str, int] = {}
    for seg in re.split(r"[,;|\n]+", txt):
        part = " ".join(seg.strip().split())
        if not part:
            continue
        m_nf = _SHIP_NUM_FIRST_RE.match(part)
        qty = 0
        raw_name = ""
        if m_nf:
            qty = parse_si_int(m_nf.group(1))
            raw_name = m_nf.group(2)
        else:
            m_lf = _SHIP_NAME_FIRST_RE.match(part)
            if m_lf:
                raw_name = m_lf.group(1)
                qty = parse_si_int(m_lf.group(2))
        if qty <= 0 or not raw_name:
            continue
        norm_name = " ".join(raw_name.strip().lower().split())
        skey = _SHIP_NAME_TO_KEY.get(norm_name)
        if skey is None and norm_name.endswith("s"):
            skey = _SHIP_NAME_TO_KEY.get(norm_name[:-1])
        if skey is not None:
            ships[skey] = ships.get(skey, 0) + qty
    return ships


def _cap_resources_by_capacity(
    raw: Resources,
    capacity: Optional[int],
    *,
    mode: str = "value",
) -> Resources:
    """Cap expected loot or debris harvest to fleet cargo capacity."""
    if capacity is None or capacity <= 0:
        return raw
    total = raw.total()
    if total <= 0 or total <= capacity:
        return raw
    if mode == "proportional":
        scale = float(capacity) / float(total)
        vals = {
            "pig_iron": min(raw.pig_iron, int(raw.pig_iron * scale)),
            "metal": min(raw.metal, int(raw.metal * scale)),
            "kryptonite": min(raw.kryptonite, int(raw.kryptonite * scale)),
            "spice": min(raw.spice, int(raw.spice * scale)),
        }
        rem = capacity - sum(vals.values())
        if rem > 0:
            for rk in ("metal", "pig_iron", "kryptonite", "spice"):
                if rem <= 0:
                    break
                headroom = max(0, getattr(raw, rk) - vals[rk])
                if headroom > 0:
                    add = min(headroom, rem)
                    vals[rk] += add
                    rem -= add
        return Resources(
            pig_iron=vals["pig_iron"],
            metal=vals["metal"],
            kryptonite=vals["kryptonite"],
            spice=vals["spice"],
        )
    # Default 'value' priority matches `simulate_raid_outcome`: Spice -> Metal -> Kryptonite -> Pig iron
    rem = capacity
    spi = min(raw.spice, rem)
    rem -= spi
    met = min(raw.metal, rem)
    rem -= met
    kry = min(raw.kryptonite, rem)
    rem -= kry
    pig = min(raw.pig_iron, rem)
    return Resources(pig_iron=pig, metal=met, kryptonite=kry, spice=spi)


def _parse_building_target_hint(label: str) -> tuple[str, Optional[int], Optional[int]]:
    """Extract `(target_building, target_level, building_id)` from a human label or transport reason."""
    txt = " ".join(str(label or "").strip().split())
    if not txt:
        return "", None, None
    m = _BLDG_LVL_HINT_RE.search(txt)
    if m:
        name = " ".join(m.group(1).strip().split())
        lvl = _safe_api_int(m.group(2), 0)
        bid = BUILDING_ID_BY_NAME.get(name.lower())
        return name, (lvl if lvl > 0 else None), bid
    low = txt.lower()
    for bname_low, bid in BUILDING_ID_BY_NAME.items():
        if bname_low in low:
            info = game_data.BUILDING_BY_ID.get(bid)
            canon = str(info.get("name") or bname_low.title()) if isinstance(info, Mapping) else bname_low.title()
            return canon, None, bid
    return txt[:64], None, None


class GameApiBridge:
    """Thread-safe facade connecting HTTP API calls to the Chrome debug port."""

    SNAPSHOT_CACHE_TTL_S = 6.0
    RESEARCH_SYNC_TTL_S = 60.0
    SHIPYARD_SYNC_TTL_S = 120.0
    SPIO_SYNC_TTL_S = 30.0
    FLEET_SLOT_COOLDOWN_S = 180.0
    ADAPTIVE_IDLE_BUCKET_SECONDS = 600.0
    ADAPTIVE_IDLE_MAX_BUCKETS = 144
    AI_DRIFT_REPLAN_COOLDOWN_SECONDS = 1800.0
    AI_DRIFT_REPLAN_MAX_AGE_SECONDS = 14400.0
    AI_DRIFT_CHECK_MIN_INTERVAL_SECONDS = 60.0
    AI_DRIFT_LOOT_SHIFT_RATIO = 0.25
    TRANSPORT_UTIL_MIN = 0.2
    TRANSPORT_UTIL_MAX = 0.9
    TRANSPORT_UTIL_DEFAULT = 0.5
    TRANSPORT_UTIL_EWMA_ALPHA = 0.15
    DELIVERED_TRANSPORT_WINDOW_SECONDS = 86400.0
    DELIVERED_TRANSPORT_MAX_EVENTS = 500
    DELIVERED_TRANSPORT_MIN_SAMPLES = 3
    DELIVERED_TRANSPORT_MIN_SPAN_HOURS = 1.0
    INFRA_TRANSPORT_SPEND_WINDOW_SECONDS = 3600.0
    INFRA_TRANSPORT_MAX_CARRYOVER_HOURS = 2.0
    # Adaptive execution: the spare % ship budget is an hourly rate that may save up for this many
    # hours (so a hull costing more than one hour's allowance can still be afforded); a ledger of
    # what it paid for over the same window is subtracted.
    SPARE_SHIP_BUDGET_WINDOW_HOURS = 4.0
    # Adaptive execution: raid income is the loot and recycling actually received over this window.
    REALIZED_LOOT_WINDOW_HOURS = 24
    REALIZED_INCOME_BASIS = "realized_24h"  # mirrors `ai_planner.REALIZED_INCOME_BASIS`
    _ADAPTIVE_IDLE_STATE_KEYS: tuple[str, ...] = (
        "building",
        "ready_not_started",
        "field_conflict",
        "storage_blocked",
        "transport_inbound",
        "waiting_metal",
        "waiting_pig_iron",
        "waiting_kryptonite",
        "waiting_spice",
        "idle_complete",
    )
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
        debug_host: str = DEFAULT_DEBUG_HOST,
        env_file: Path = Path(".env"),
        account: str = "default",
        config_path: Optional[Path] = None,
        max_actions_per_hour: Optional[int] = None,
        log_dir: Optional[Path] = Path("logs"),
        db_path: Optional[Path | str] = None,
        db_backend: Optional[str] = None,
        db_url: Optional[str] = None,
        client_factory: Optional[Callable[[int], Any]] = None,
        ensure_session_fn: Optional[Callable[[], None]] = None,
        page_limiter: Optional[PageViewLimiter] = None,
        max_limiter_wait: float = 8.0,
        executor_max_limiter_wait: Optional[float] = 8.0,
    ) -> None:
        self.debug_port = _safe_debug_port(debug_port, DEFAULT_DEBUG_PORT)
        self.debug_host = str(debug_host or DEFAULT_DEBUG_HOST).strip() or "127.0.0.1"
        raw_novnc = (os.environ.get("SPACEINVASION_NOVNC_URL") or "").strip()
        self.novnc_url: Optional[str] = raw_novnc or (
            "http://127.0.0.1:7900/vnc.html?autoconnect=true&resize=scale"
            if (is_container_runtime() or self.debug_host not in ("127.0.0.1", "localhost", "::1"))
            else None
        )
        self.env_file = env_file
        self.account = account
        self._config_file_explicit = config_path is not None
        self.config_path: Optional[Path] = config_path
        if config_path is not None:
            self.strategy_config: StrategyConfig = load_strategy_config(config_path)
        elif log_dir is not None or db_backend is not None or db_url is not None or is_container_runtime():
            self.strategy_config = load_strategy_config(None)
        else:
            self.strategy_config = StrategyConfig()
        self.log_dir = log_dir
        self._lock = threading.Lock()
        # Dedicated lightweight lock for read-only SQLite intel/colony views so
        # UI polls never block behind Chrome/Selenium actions holding `_lock`.
        self._intel_read_lock = threading.RLock()
        self._custom_page_limiter_injected = page_limiter is not None
        self._page_limiter = page_limiter or PageViewLimiter()
        if not self._custom_page_limiter_injected:
            self._sync_page_limiter_from_config(self.strategy_config)
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
        self._metal_overflow_paused_planets: dict[int, float] = {}
        self._fleet_slot_cooldown_until: dict[int, float] = {}
        self._observed_fleet_slots_by_pid: dict[int, tuple[int, int]] = {}
        self._observed_fleet_slot_base_offset: Optional[int] = None
        self._target_blocks: dict[str, tuple[float, str]] = {}
        # Colony trial ledger (`colony_attempts`): dedupe keys for kept/settled observations,
        # secondary planets seen without a parsed field count yet, and recent failed-dispatch throttle.
        self._colony_attempt_seen: set[tuple[int, str]] = set()
        self._colony_attempt_pending_pids: set[int] = set()
        self._colony_attempt_failed_recent: dict[str, tuple[str, float]] = {}
        self._dash_cache_state_ref: Optional[GameState] = None
        self._dash_static_cache_key: Optional[Any] = None
        self._dash_static_cache_json: Optional[str] = None
        self._dash_plan_cache_key: Optional[Any] = None
        self._dash_plan_cache_val: Optional[
            tuple[list[PlannedAction], Optional[dict[str, Any]], dict[int, dict[str, Any]]]
        ] = None
        self._dash_ag_cache_key: Optional[Any] = None
        self._dash_ag_cache_json: Optional[str] = None
        self._db_summary_cache_key: Optional[Any] = None
        self._db_summary_cache: Optional[dict[str, Any]] = None
        self._raid_stats_cache_key: Optional[Any] = None
        self._raid_stats_cache: Optional[dict[str, Any]] = None
        self._raid_stats_cache_ts: float = 0.0
        self._raid_targets_cache_key: Optional[Any] = None
        self._co_goals_cache_key: Optional[Any] = None
        self._co_goals_cache: Optional[dict[str, Any]] = None
        self._active_ai_cache_key: Optional[Any] = None
        self._active_ai_cache: Optional[dict[str, Any]] = None
        self._last_bot_control_payload: Optional[dict[str, Any]] = None
        self._last_dashboard_payload: Optional[dict[str, Any]] = None
        self._rank_history_path: Optional[Path] = None
        self._db_selection_source: str = "explicit"
        self._resolved_postgres_url: str = build_default_postgres_url()

        if db_backend is not None and str(db_backend).strip().lower() not in ("", "auto"):
            normalize_backend_name(db_backend)

        is_live_repo_default_db = False
        if db_path is None and (db_backend is not None or db_url is not None or is_container_runtime()):
            is_live_repo_default_db = True
        elif db_path is None and log_dir is not None:
            try:
                is_live_repo_default_db = Path(log_dir).resolve() == (REPO_ROOT_DIR / "logs").resolve()
            except OSError:
                is_live_repo_default_db = False

        if db_path is not None and is_postgres_dsn(str(db_path)):
            self._db_selection_source = "explicit_dsn"
            self._resolved_postgres_url = str(db_path).strip()
            self._db: Optional[GameDatabase] = GameDatabase(self._resolved_postgres_url, backend="postgres")
        elif is_live_repo_default_db:
            resolved_cfg: ResolvedDatabaseConfig = resolve_database_config(
                explicit_backend=db_backend,
                explicit_db_url=db_url,
                root_dir=REPO_ROOT_DIR,
            )
            self._db_selection_source = resolved_cfg.selection_source
            self._resolved_postgres_url = resolved_cfg.postgres_url
            ensure_postgres_container_ready(
                resolved_cfg.postgres_url,
                root_dir=REPO_ROOT_DIR,
                auto_start=resolved_cfg.auto_start_podman,
            )
            self._db = GameDatabase(resolved_cfg.postgres_url, backend="postgres")
        elif db_path is not None or log_dir is not None:
            eff_path = db_path if db_path is not None else (log_dir.parent / "state" / "postgres")  # type: ignore[union-attr]
            self._db = GameDatabase(eff_path)
            pg_dsn = getattr(self._db, "postgres_url", "")
            if is_postgres_dsn(pg_dsn):
                self._resolved_postgres_url = pg_dsn
        else:
            self._db = None
        if self._db is not None:
            self._rank_samples = self._db.get_rank_samples()
            self._economy_rates.update(self._db.get_all_economy_rates())
            self._raid_targets: tuple[dict[str, Any], ...] = ()
            self._refresh_raid_targets_locked(limit=100)
            self._scanned_systems.update(self._db.get_galaxy_scans())
            self._throttle_timestamps.update(self._db.get_last_throttle_timestamps())
            self._hydrate_colony_attempt_seen()
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
            "daily_bonus_checked_day": "",
            "daily_bonus_collected_day": "",
            "daily_bonus_available": False,
            "daily_bonus_available_day": "",
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
            "click_delay_seconds": 1.5,
            "click_jitter_seconds": 0.6,
        }
        self._sweep_xhr_timestamps: Deque[float] = deque()
        self._last_galaxy_browser_coords: Optional[tuple[int, int]] = None
        self._sweep_run_lock = threading.Lock()
        self._sweep_in_progress = False
        self._sweep_stop_event = threading.Event()
        self._sweep_wake_event = threading.Event()
        self._sweep_preempt_event = threading.Event()
        self._sweep_preempt_reason: Optional[str] = None
        self._sweep_done_event = threading.Event()
        self._sweep_done_event.set()
        self._last_live_command_mono: float = 0.0
        self._sweep_thread: Optional[threading.Thread] = None
        # Cached Smart 2-Phase Galaxy Scanner telemetry (`scan_phase`, coverage, `sample_targets`)
        self._smart_sweep_summary: dict[str, Any] = {}
        self._smart_sweep_summary_ts: float = 0.0
        # Non-blocking background Config Optimizer & AI-Planner job runner state (decoupled from `_lock`)
        self._planner_jobs_lock = threading.Lock()
        self._planner_closed: bool = False
        self._planner_job_seq: int = 0
        self._config_opt_job: dict[str, Any] = self._new_idle_planner_job("config_optimizer")
        self._ai_planner_job: dict[str, Any] = self._new_idle_planner_job("ai_planner")
        self._active_config_opt_mem: Optional[dict[str, Any]] = None
        self._active_ai_plan_mem: Optional[dict[str, Any]] = None
        self._config_opt_runs_mem: list[dict[str, Any]] = []
        self._ai_planner_runs_mem: list[dict[str, Any]] = []
        self._reserved_floors_mem: dict[str, dict[str, int]] = {}
        self._planet_specializations_mem: dict[str, str] = {}
        self._custom_planet_names: dict[str, str] = {}
        self._co_yearly_proj_cache: dict[tuple[Any, ...], dict[str, Any]] = {}
        self._recent_transport_dispatches: list[dict[str, Any]] = []
        self._galaxy_debris_amounts: dict[str, dict[str, int]] = {}
        self._last_early_transport_needs: dict[int, dict[str, Any]] = {}
        self._last_early_transport_needs_sig: Optional[Any] = None
        self._universe_max_galaxies: Optional[int] = None
        self._last_recorded_prod_minute: Optional[int] = None
        self._hourly_prod_cache_key: Optional[Any] = None
        self._hourly_prod_cache: list[dict[str, Any]] = []
        self._loot_recycle_cache_key: Optional[Any] = None
        self._loot_recycle_cache: Optional[dict[str, Any]] = None
        self._field_conflicts_mem: dict[str, dict[str, Any]] = {}
        self._notified_field_conflict_keys: set[str] = set()
        self._adaptive_idle_buckets: dict[int, dict[str, Any]] = {}
        self._transport_realized_utilization: float = self.TRANSPORT_UTIL_DEFAULT
        self._delivered_transport_events: list[dict[str, Any]] = []
        self._infra_transport_spend_ledger: list[dict[str, Any]] = []
        self._infra_transport_last_spend_ts: float = time.time()
        self._spare_ship_spend_ledger: list[dict[str, Any]] = []
        self._last_auto_ai_replan_ts: float = 0.0
        self._last_auto_ai_replan_reason: str = ""
        self._last_drift_check_mono: float = 0.0
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
            allow_transport=self.strategy_config.allow_transport,
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
            allow_transport=self.strategy_config.allow_transport,
            allow_transmitter=self.strategy_config.allow_transmitter,
            page_limiter=self._page_limiter,
            max_limiter_wait=self._executor_max_limiter_wait,
        )
        self._client_factory = client_factory or (
            lambda port: (
                GameBrowserClient.attach(
                    port=port,
                    page_limiter=self._page_limiter,
                    max_limiter_wait=self._max_limiter_wait,
                )
                if self.debug_host == "127.0.0.1"
                else GameBrowserClient.attach(
                    port=port,
                    host=self.debug_host,
                    page_limiter=self._page_limiter,
                    max_limiter_wait=self._max_limiter_wait,
                )
            )
        )
        self._ensure_session_fn = ensure_session_fn or (
            lambda: (
                ensure_debug_browser_ready(
                    debug_port=self.debug_port,
                    env_file=self.env_file,
                    account=self.account,
                )
                if self.debug_host == "127.0.0.1"
                else ensure_debug_browser_ready(
                    debug_port=self.debug_port,
                    debug_host=self.debug_host,
                    env_file=self.env_file,
                    account=self.account,
                )
            )
        )

    def _is_browser_port_open(self) -> bool:
        if self.debug_host == "127.0.0.1":
            return is_debug_port_open(self.debug_port)
        return is_debug_port_open(self.debug_port, self.debug_host)

    def close(self) -> None:
        """Close owned database resources idempotently."""
        planner_lock = getattr(self, "_planner_jobs_lock", None)
        with (planner_lock if planner_lock is not None else contextlib.nullcontext()):
            self._planner_closed = True
        self.stop_galaxy_sweep_worker()
        lock = getattr(self, "_lock", None)
        with (lock if lock is not None else contextlib.nullcontext()):
            if not getattr(self, "_db_closed", False):
                self._db_closed = True
                db = getattr(self, "_db", None)
                if db is not None:
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

    def _build_db_backend_status(self, *, include_ping: bool = False) -> dict[str, Any]:
        db = self._db
        backend = str(getattr(db, "backend_name", "postgres")) if db is not None else "none"
        target = str(getattr(db, "display_target", "")) if db is not None else ""
        out: dict[str, Any] = {
            "backend": backend,
            "target": target,
            "selection_source": self._db_selection_source,
            "container_mode": is_container_runtime(),
            "postgres_target": redact_db_url(self._resolved_postgres_url),
        }
        if include_ping:
            podman_info = get_podman_postgres_status(force=False)
            out["podman"] = podman_info
            pg_ok, pg_ms, pg_msg = ping_postgres(self._resolved_postgres_url, timeout=1.5)
            out["postgres_reachable"] = pg_ok
            out["postgres_latency_ms"] = pg_ms
            out["postgres_server"] = pg_msg if pg_ok else ""
            out["postgres_error"] = "" if pg_ok else pg_msg
        return out

    def _hydrate_persisted_bridge_state(self) -> None:
        if self._db is None:
            return
        load_cfg_fn = getattr(self._db, "load_strategy_config_snapshot", None)
        save_cfg_fn = getattr(self._db, "save_strategy_config_snapshot", None)
        has_explicit_file = bool(
            self._config_file_explicit and self.config_path is not None and self.config_path.exists()
        )
        if not has_explicit_file and callable(load_cfg_fn):
            saved_cfg = load_cfg_fn()
            if isinstance(saved_cfg, Mapping) and saved_cfg:
                try:
                    self.strategy_config = strategy_config_from_dict(dict(saved_cfg))
                except Exception:
                    pass
        if callable(save_cfg_fn):
            save_cfg_fn(asdict(self.strategy_config))
        if not self._custom_page_limiter_injected:
            self._sync_page_limiter_from_config(self.strategy_config)

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
            overflow_paused = rt.get("metal_overflow_paused_planets")
            if isinstance(overflow_paused, Mapping):
                for raw_pid, raw_ts in overflow_paused.items():
                    pid_i = _safe_api_int(raw_pid, 0)
                    ts_f = _safe_api_float(raw_ts, 0.0)
                    if pid_i > 0 and ts_f > 0.0:
                        self._metal_overflow_paused_planets[pid_i] = ts_f
            self._last_synced_unread_total = max(
                0, _safe_api_int(rt.get("last_synced_unread_total", 0), 0)
            )
            self._pending_spy_sync = bool(rt.get("pending_spy_sync", False))
            if isinstance(rt.get("last_url"), str) and rt["last_url"]:
                self._last_url = _bounded_api_str(rt["last_url"], default="")

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
            raw_obs_off = rt.get("observed_fleet_slot_base_offset")
            if isinstance(raw_obs_off, int) and not isinstance(raw_obs_off, bool) and raw_obs_off in (0, 1):
                self._observed_fleet_slot_base_offset = raw_obs_off
            raw_obs_pid_slots = rt.get("observed_fleet_slots_by_pid")
            if isinstance(raw_obs_pid_slots, Mapping):
                for raw_pid_s, raw_pair in raw_obs_pid_slots.items():
                    pid_i = _safe_api_int(raw_pid_s, 0)
                    if (
                        pid_i > 0
                        and isinstance(raw_pair, Sequence)
                        and not isinstance(raw_pair, (str, bytes))
                        and len(raw_pair) == 2
                    ):
                        b10_v = _safe_api_int(raw_pair[0], -1)
                        av_v = _safe_api_int(raw_pair[1], -1)
                        if b10_v >= 0 and av_v >= 0:
                            self._observed_fleet_slots_by_pid[pid_i] = (b10_v, av_v)

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

            raw_deliv = rt.get("delivered_transport_events")
            loaded_deliv: list[dict[str, Any]] = []
            seen_deliv_keys: set[str] = set()
            if isinstance(raw_deliv, Sequence) and not isinstance(raw_deliv, (str, bytes)):
                for ent in raw_deliv:
                    if not isinstance(ent, Mapping):
                        continue
                    ek = str(ent.get("key") or "").strip()[:160]
                    ets = _safe_api_float(ent.get("ts"), 0.0)
                    ecargo = max(0, _safe_api_int(ent.get("cargo"), 0))
                    efleet = max(0, _safe_api_int(ent.get("fleet_cargo"), 0))
                    if not ek or ek in seen_deliv_keys or ecargo <= 0 or ets <= 0.0:
                        continue
                    seen_deliv_keys.add(ek)
                    loaded_deliv.append(
                        {"key": ek, "ts": round(ets, 3), "cargo": ecargo, "fleet_cargo": efleet}
                    )
            self._delivered_transport_events = loaded_deliv
            self._prune_delivered_transport_events_locked(now_ts=now_wall)
            # Recompute realized throughput utilization from persisted 24h deliveries (keeps 0.5 baseline
            # until sufficient real throughput samples exist, self-healing any legacy fill-ratio values).
            self._recompute_transport_utilization_locked(now_ts=now_wall)

            raw_spend = rt.get("infra_transport_spend_ledger")
            loaded_spend: list[dict[str, Any]] = []
            if isinstance(raw_spend, Sequence) and not isinstance(raw_spend, (str, bytes)):
                for ent in raw_spend:
                    if not isinstance(ent, Mapping):
                        continue
                    sts = _safe_api_float(ent.get("ts"), 0.0)
                    smet = max(0, _safe_api_int(ent.get("metal"), 0))
                    skry = max(0, _safe_api_int(ent.get("kryptonite"), 0))
                    if sts > 0.0 and (smet > 0 or skry > 0):
                        spend_row: dict[str, Any] = {"ts": round(sts, 3), "metal": smet, "kryptonite": skry}
                        scargo = max(0, _safe_api_int(ent.get("cargo"), 0))
                        if scargo > 0:
                            spend_row["cargo"] = scargo
                        loaded_spend.append(spend_row)
            self._infra_transport_spend_ledger = loaded_spend
            self._prune_infra_transport_spend_locked(now_ts=now_wall)
            raw_last_spend = _safe_api_float(rt.get("infra_transport_last_spend_ts", 0.0), 0.0)
            if math.isfinite(raw_last_spend) and raw_last_spend > 0.0:
                self._infra_transport_last_spend_ts = raw_last_spend

            raw_spare_spend = rt.get("spare_ship_spend_ledger")
            loaded_spare: list[dict[str, Any]] = []
            if isinstance(raw_spare_spend, Sequence) and not isinstance(raw_spare_spend, (str, bytes)):
                for ent in raw_spare_spend:
                    if not isinstance(ent, Mapping):
                        continue
                    sp_ts = _safe_api_float(ent.get("ts"), 0.0)
                    sp_row = {
                        rk: max(0, _safe_api_int(ent.get(rk), 0))
                        for rk in ("pig_iron", "metal", "kryptonite", "spice")
                    }
                    if sp_ts > 0.0 and any(v > 0 for v in sp_row.values()):
                        loaded_spare.append({"ts": round(sp_ts, 3), **sp_row})
            self._spare_ship_spend_ledger = loaded_spare
            self._prune_spare_ship_spend_locked(now_ts=now_wall)

            raw_replan_ts = _safe_api_float(rt.get("last_auto_ai_replan_ts", 0.0), 0.0)
            if math.isfinite(raw_replan_ts) and raw_replan_ts > 0.0:
                self._last_auto_ai_replan_ts = raw_replan_ts
            raw_replan_reason = rt.get("last_auto_ai_replan_reason")
            if isinstance(raw_replan_reason, str) and raw_replan_reason.strip():
                self._last_auto_ai_replan_reason = _bounded_api_str(raw_replan_reason, default="")[:128]

            raw_buckets = rt.get("adaptive_idle_buckets")
            loaded_buckets: dict[int, dict[str, Any]] = {}
            bucket_items: list[tuple[Any, Any]] = []
            if isinstance(raw_buckets, Mapping):
                bucket_items = list(raw_buckets.items())
            elif isinstance(raw_buckets, Sequence) and not isinstance(raw_buckets, (str, bytes)):
                bucket_items = [
                    (ent.get("bucket"), ent)
                    for ent in raw_buckets
                    if isinstance(ent, Mapping) and ent.get("bucket") is not None
                ]
            for raw_bk, raw_ent in bucket_items:
                if not isinstance(raw_ent, Mapping):
                    continue
                bk_i = _safe_api_int(raw_bk, 0)
                if bk_i <= 0:
                    bk_i = _safe_api_int(raw_ent.get("bucket"), 0)
                if bk_i <= 0:
                    continue
                raw_cnts = raw_ent.get("counts")
                clean_cnts: dict[str, int] = {}
                if isinstance(raw_cnts, Mapping):
                    for ck, cv in raw_cnts.items():
                        ck_s = str(ck or "").strip()
                        if ck_s:
                            clean_cnts[ck_s] = max(0, _safe_api_int(cv, 0))
                tot_col = max(0, _safe_api_int(raw_ent.get("total_colonies", 0), 0))
                if tot_col <= 0 and clean_cnts:
                    tot_col = sum(clean_cnts.values())
                if tot_col <= 0:
                    continue
                loaded_buckets[bk_i] = {
                    "bucket": bk_i,
                    "ts": round(_safe_api_float(raw_ent.get("ts"), float(bk_i * 600)), 3),
                    "total_colonies": tot_col,
                    "active_builders": max(
                        0,
                        _safe_api_int(
                            raw_ent.get("active_builders", clean_cnts.get("building", 0)),
                            0,
                        ),
                    ),
                    "idle_with_affordable": max(0, _safe_api_int(raw_ent.get("idle_with_affordable", 0), 0)),
                    "ready_not_started": max(
                        0,
                        _safe_api_int(
                            raw_ent.get("ready_not_started", clean_cnts.get("ready_not_started", 0)),
                            0,
                        ),
                    ),
                    "counts": clean_cnts,
                }
            self._adaptive_idle_buckets = loaded_buckets
            self._prune_adaptive_idle_buckets_locked(now_ts=now_wall)

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

        get_names_fn = getattr(self._db, "get_custom_planet_names", None)
        if callable(get_names_fn):
            try:
                loaded_names = get_names_fn()
                if isinstance(loaded_names, Mapping):
                    self._custom_planet_names = {
                        str(k): str(v) for k, v in loaded_names.items() if str(k).strip() and str(v).strip()
                    }
            except Exception:
                pass

        get_deb_fn = getattr(self._db, "get_galaxy_debris_amounts", None)
        if callable(get_deb_fn):
            try:
                loaded_deb = get_deb_fn()
                if isinstance(loaded_deb, Mapping):
                    self._galaxy_debris_amounts = {
                        str(k): dict(v) for k, v in loaded_deb.items() if isinstance(v, Mapping)
                    }
            except Exception:
                pass

        hydrated_dispatches: list[dict[str, Any]] = []
        get_tr_fn = getattr(self._db, "get_recent_transport_dispatches", None)
        if callable(get_tr_fn):
            try:
                rows_tr = get_tr_fn(max_age_seconds=86400.0, limit=120)
                if isinstance(rows_tr, Sequence):
                    hydrated_dispatches.extend(dict(r) for r in rows_tr if isinstance(r, Mapping))
            except Exception:
                pass
        get_tr_fb = getattr(self._db, "get_recent_transport_actions_fallback", None)
        if callable(get_tr_fb):
            try:
                rows_fb = get_tr_fb(max_age_seconds=86400.0, limit=60)
                if isinstance(rows_fb, Sequence):
                    existing_sigs = {
                        (
                            str(r.get("origin_coords") or ""),
                            str(r.get("dest_coords") or ""),
                            int(round(_safe_api_float(r.get("ts"), 0.0) / 60.0)),
                        )
                        for r in hydrated_dispatches
                    }
                    for fb in rows_fb:
                        if not isinstance(fb, Mapping):
                            continue
                        sig = (
                            str(fb.get("origin_coords") or ""),
                            str(fb.get("dest_coords") or ""),
                            int(round(_safe_api_float(fb.get("ts"), 0.0) / 60.0)),
                        )
                        if sig not in existing_sigs:
                            hydrated_dispatches.append(dict(fb))
                            existing_sigs.add(sig)
            except Exception:
                pass
        hydrated_dispatches.sort(key=lambda r: _safe_api_float(r.get("ts"), 0.0))
        self._recent_transport_dispatches = hydrated_dispatches[-120:]

        load_meta_fn = getattr(self._db, "load_meta_json", None) or getattr(self._db, "get_meta", None)
        if callable(load_meta_fn):
            try:
                raw_mg = load_meta_fn("universe_max_galaxies")
                mg_val = _safe_api_int(raw_mg, 0)
                if 1 <= mg_val <= 14:
                    self._universe_max_galaxies = mg_val
            except Exception:
                pass

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

        self._prune_adaptive_idle_buckets_locked(now_ts=now_wall)
        self._prune_delivered_transport_events_locked(now_ts=now_wall)
        self._prune_infra_transport_spend_locked(now_ts=now_wall)
        self._prune_spare_ship_spend_locked(now_ts=now_wall)
        serialized_buckets: dict[str, dict[str, Any]] = {
            str(bk): dict(self._adaptive_idle_buckets[bk])
            for bk in sorted(self._adaptive_idle_buckets.keys())
        }
        payload: dict[str, Any] = {
            "bot_control": dict(self._bot_control),
            "economy_sync_timestamps": {
                str(int(k)): round(float(v), 3)
                for k, v in self._economy_sync_timestamps.items()
                if isinstance(v, (int, float)) and math.isfinite(v) and v > 0.0
            },
            "metal_overflow_paused_planets": {
                str(int(k)): round(float(v), 3)
                for k, v in self._metal_overflow_paused_planets.items()
                if isinstance(v, (int, float)) and math.isfinite(v) and v > 0.0
            },
            "fleet_slot_cooldown_until_wall": fsc_wall,
            "observed_fleet_slot_base_offset": self._observed_fleet_slot_base_offset,
            "observed_fleet_slots_by_pid": {
                str(int(k)): [int(v[0]), int(v[1])]
                for k, v in self._observed_fleet_slots_by_pid.items()
            },
            "last_synced_unread_total": max(0, int(self._last_synced_unread_total)),
            "pending_spy_sync": bool(self._pending_spy_sync),
            "spio_sync_at_wall": _mono_to_wall(self._last_spio_sync_at),
            "research_sync_at_wall": _mono_to_wall(self._last_research_sync_at),
            "shipyard_sync_at_wall": _mono_to_wall(self._last_shipyard_sync_at),
            "spy_dispatched_at_wall": _mono_to_wall(self._last_spy_dispatched_at),
            "last_url": _bounded_api_str(self._last_url, default="") if self._last_url else None,
            "adaptive_idle_buckets": serialized_buckets,
            "delivered_transport_events": [dict(e) for e in self._delivered_transport_events],
            "infra_transport_spend_ledger": [dict(e) for e in self._infra_transport_spend_ledger],
            "spare_ship_spend_ledger": [dict(e) for e in self._spare_ship_spend_ledger],
            "infra_transport_last_spend_ts": (
                round(float(self._infra_transport_last_spend_ts), 3)
                if isinstance(self._infra_transport_last_spend_ts, (int, float))
                and math.isfinite(self._infra_transport_last_spend_ts)
                and self._infra_transport_last_spend_ts > 0.0
                else 0.0
            ),
            "transport_realized_utilization": round(
                min(self.TRANSPORT_UTIL_MAX, max(self.TRANSPORT_UTIL_MIN, float(self._transport_realized_utilization))),
                4,
            ),
            "last_auto_ai_replan_ts": (
                round(float(self._last_auto_ai_replan_ts), 3)
                if isinstance(self._last_auto_ai_replan_ts, (int, float))
                and math.isfinite(self._last_auto_ai_replan_ts)
                and self._last_auto_ai_replan_ts > 0.0
                else 0.0
            ),
            "last_auto_ai_replan_reason": str(self._last_auto_ai_replan_reason or "")[:128],
        }
        save_rt_fn(payload, ts=now_wall)

    def _record_antibot_pause(self, exc: BaseException) -> None:
        already_blocked = bool(self._bot_control.get("captcha_blocked"))
        msg = _bounded_api_str(exc, default="CAPTCHA / anti-bot challenge detected.")
        if not any(
            tok in msg.lower()
            for tok in ("captcha", "anti-bot", "antibot", "turnstile", "sicherheitspr")
        ):
            msg = f"CAPTCHA / anti-bot challenge: {msg}" if msg else "CAPTCHA / anti-bot challenge detected."
        self._bot_control["paused"] = True
        self._bot_control["captcha_blocked"] = True
        self._bot_control["captcha_message"] = msg
        if not already_blocked and self._db is not None and hasattr(self._db, "create_or_bump_notification"):
            try:
                self._db.create_or_bump_notification(
                    title="CAPTCHA / Security Verification Required",
                    message=msg,
                    help_prompt=(
                        "Page refreshes and automation are completely frozen so the CAPTCHA is not interrupted. "
                        "Solve the verification check manually in Chrome (:9222), then click 'Solved in Chrome — Resume Bot' in the UI."
                    ),
                    severity="error",
                    category="captcha_gate",
                    action_kind="SECURITY_CHECK",
                    dedupe_key="captcha:gate",
                )
            except Exception:
                pass
        if not already_blocked:
            self._persist_runtime_state()

    def _ack_captcha_notifications_locked(self) -> None:
        if self._db is None:
            return
        get_fn = getattr(self._db, "get_notifications", None)
        ack_fn = getattr(self._db, "acknowledge_notification", None)
        if not (callable(get_fn) and callable(ack_fn)):
            return
        try:
            for item in get_fn(status="unread", limit=50):
                if not isinstance(item, Mapping):
                    continue
                if item.get("dedupe_key") in ("captcha:gate", "antibot:captcha_challenge") or item.get("category") in ("captcha_gate", "antibot"):
                    nid = _safe_api_int(item.get("id"), 0)
                    if nid > 0:
                        ack_fn(nid)
        except Exception:
            pass

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
        # Skip benign non-errors (fleet slot capacity waits, no-ops, validated skips, hourly caps)
        if (
            msg_l.startswith("no-op:")
            or (validated and msg_l.startswith("skipped:"))
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
        combined_l = f"{msg_l} {val_err.lower()}"
        raw_tcd = getattr(res, "target_cooldown_seconds", 0.0)
        has_tcd = bool(isinstance(raw_tcd, (int, float)) and not isinstance(raw_tcd, bool) and math.isfinite(raw_tcd) and raw_tcd > 0.0)
        is_prot = has_tcd or any(
            kw in combined_l
            for kw in (
                "can't be attacked",
                "cannot be attacked",
                "nicht angegriffen",
                "vacation",
                "urlaubsmodus",
                "protection",
                "attack zone",
                "attackzone",
                "angriffszone",
                "forbidden",
                "newbie",
                "noob",
                "bash",
            )
        )
        if is_prot:
            # Target protection / AZ / vacation / bash rejections are normal in SpaceInvasion
            # when cached galaxy targets despawn (e.g. Spacia) or change status between scans,
            # and are handled automatically via target cooldowns — log only, no UI popup spam.
            cd_hours = round(float(raw_tcd) / 3600.0, 2) if has_tcd else 6.0
            print(
                redact_sensitive(
                    f"[API] {kind_s} target protected ({tname_s}): {val_err or msg_s} "
                    f"(cooldown={cd_hours:g}h)"
                ),
                flush=True,
            )
            return None
        if executed and not validated:
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
            if act.kind in ("ATTACK", "SPY", "TRANSPORT", "STATION", "RECYCLE", "ESCAPE_FLIGHT", "COLONIZE") and act.planet_id in busy_pids:
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
                    _fleet_subrank(a),
                )
            )
        return out

    def _max_fleet_slots_for_planet_locked(self, planet: Optional[PlanetState]) -> int:
        if planet is None:
            return 1
        b10_lvl = max(0, planet.building_level(10))
        obs_pair = self._observed_fleet_slots_by_pid.get(int(planet.planet_id))
        if obs_pair is not None and obs_pair[0] == b10_lvl:
            return max(0, int(obs_pair[1]))
        if self._observed_fleet_slot_base_offset is not None:
            return max(0, b10_lvl + int(self._observed_fleet_slot_base_offset))
        return 1 + b10_lvl

    def _record_observed_fleet_slots_locked(
        self,
        planet: Optional[PlanetState],
        available: Optional[int],
    ) -> None:
        if planet is None or available is None or isinstance(available, bool):
            return
        avail_i = _safe_api_int(available, -1)
        if avail_i < 0:
            return
        pid_i = int(planet.planet_id)
        b10_lvl = max(0, planet.building_level(10))
        changed = False
        if pid_i > 0 and self._observed_fleet_slots_by_pid.get(pid_i) != (b10_lvl, avail_i):
            self._observed_fleet_slots_by_pid[pid_i] = (b10_lvl, avail_i)
            changed = True
        if b10_lvl >= 1 and avail_i in (b10_lvl, b10_lvl + 1):
            new_off = avail_i - b10_lvl
            if self._observed_fleet_slot_base_offset != new_off:
                self._observed_fleet_slot_base_offset = new_off
                changed = True
        if changed:
            self._invalidate_dashboard_cache()

    def _compute_fleet_slot_busy_cooldown_s(
        self,
        state: GameState,
        planet: Optional[PlanetState],
    ) -> float:
        if planet is None:
            return self.FLEET_SLOT_COOLDOWN_S
        norm_p = normalize_coords(getattr(planet, "coords", "") or "")
        earliest_rem: Optional[float] = None
        for q in getattr(state, "active_queues", ()) or ():
            if not is_in_flight_fleet_job(q):
                continue
            q_orig = normalize_coords(getattr(q, "origin_coords", "") or "")
            if q_orig and norm_p and q_orig != norm_p:
                continue
            rem_s = getattr(q, "remaining_seconds", None)
            if (
                isinstance(rem_s, (int, float))
                and not isinstance(rem_s, bool)
                and math.isfinite(rem_s)
                and rem_s > 0.0
            ):
                if earliest_rem is None or float(rem_s) < earliest_rem:
                    earliest_rem = float(rem_s)
        if earliest_rem is not None:
            return max(30.0, min(earliest_rem + 2.0, 1800.0))
        return self.FLEET_SLOT_COOLDOWN_S

    def _apply_fleet_slot_holds(
        self,
        state: GameState,
        actions: list[PlannedAction],
        *,
        cfg: Optional[StrategyConfig] = None,
        re_sort_priority: bool = False,
    ) -> list[PlannedAction]:
        if not actions or not getattr(state, "planets", ()):
            return actions
        eff_cfg = cfg or self.strategy_config
        res_spy_cfg = max(0, _safe_api_int(getattr(eff_cfg, "reserved_spy_slots", 1), 1))
        spy_only_res = bool(getattr(eff_cfg, "spy_reserved_slots_only", False))
        planets_by_id = {p.planet_id: p for p in state.planets}
        default_p = state.active_planet() or state.planets[0]
        used_by_pid: dict[int, int] = {
            p.planet_id: count_in_flight_fleets_for_planet(state, p) for p in state.planets
        }
        spy_used_by_pid: dict[int, int] = {
            p.planet_id: count_in_flight_spy_fleets_for_planet(state, p) for p in state.planets
        }

        def _used_for(p_o: PlanetState) -> int:
            return used_by_pid.get(p_o.planet_id, count_in_flight_fleets_for_planet(state, p_o))

        def _spy_used_for(p_o: PlanetState) -> int:
            return spy_used_by_pid.get(p_o.planet_id, count_in_flight_spy_fleets_for_planet(state, p_o))

        spy_pids = {
            a.planet_id
            for a in actions
            if a.kind == "SPY" and (a.executable_now or eff_cfg.allow_galaxy_scan or eff_cfg.allow_colonize)
        }

        def _spy_res_for(p_o: PlanetState) -> int:
            max_s = self._max_fleet_slots_for_planet_locked(p_o)
            has_dem = (
                res_spy_cfg > 0
                and _safe_api_int(p_o.ships.get("s10", 0), 0) > 0
                and p_o.planet_id in spy_pids
            )
            if not has_dem:
                return 0
            base_res = min(res_spy_cfg, max(0, max_s - 1))
            if spy_only_res:
                return max(0, base_res - _spy_used_for(p_o))
            return base_res

        # Per-planet Tier #2 (TRANSPORT), Tier #3 (COLONIZE), and Tier #3.5 (STATION) reservations
        tr_count_by_pid: dict[int, int] = {}
        col_count_by_pid: dict[int, int] = {}
        st_count_by_pid: dict[int, int] = {}
        for act in actions:
            if act.priority == "P3":
                continue
            if act.kind == "TRANSPORT" and getattr(eff_cfg, "allow_transport", False):
                p_o = planets_by_id.get(act.planet_id, default_p)
                if any(_safe_api_int(p_o.ships.get(k, 0), 0) > 0 for k in ("s1", "s2", "s3")):
                    tr_count_by_pid[p_o.planet_id] = tr_count_by_pid.get(p_o.planet_id, 0) + 1
            elif act.kind == "COLONIZE" and getattr(eff_cfg, "allow_colonize", False):
                p_o = planets_by_id.get(act.planet_id, default_p)
                if _safe_api_int(p_o.ships.get("s8", 0), 0) > 0:
                    col_count_by_pid[p_o.planet_id] = col_count_by_pid.get(p_o.planet_id, 0) + 1
            elif act.kind == "STATION" and getattr(eff_cfg, "allow_transport", False):
                p_o = planets_by_id.get(act.planet_id, default_p)
                if any(_safe_api_int(v, 0) > 0 for k, v in (p_o.ships or {}).items() if k != "s9"):
                    st_count_by_pid[p_o.planet_id] = st_count_by_pid.get(p_o.planet_id, 0) + 1

        tr_res_by_pid: dict[int, int] = {}
        for pid_t, n_t in tr_count_by_pid.items():
            p_o = planets_by_id.get(pid_t, default_p)
            max_s = self._max_fleet_slots_for_planet_locked(p_o)
            tr_res_by_pid[pid_t] = min(n_t, max(0, max_s - _spy_res_for(p_o)))

        col_res_by_pid: dict[int, int] = {}
        for pid_c, n_c in col_count_by_pid.items():
            p_o = planets_by_id.get(pid_c, default_p)
            max_s = self._max_fleet_slots_for_planet_locked(p_o)
            col_res_by_pid[pid_c] = min(
                n_c,
                max(0, _safe_api_int(p_o.ships.get("s8", 0), 0)),
                max(0, max_s - _spy_res_for(p_o) - tr_res_by_pid.get(pid_c, 0)),
            )

        st_res_by_pid: dict[int, int] = {}
        for pid_st, n_st in st_count_by_pid.items():
            p_o = planets_by_id.get(pid_st, default_p)
            max_s = self._max_fleet_slots_for_planet_locked(p_o)
            st_res_by_pid[pid_st] = min(
                n_st,
                max(0, max_s - _spy_res_for(p_o) - tr_res_by_pid.get(pid_st, 0) - col_res_by_pid.get(pid_st, 0)),
            )

        changed = False
        spy_dispatched_by_pid: dict[int, int] = {}
        out: list[PlannedAction] = []
        for act in actions:
            if act.kind not in ("SPY", "TRANSPORT", "COLONIZE", "STATION", "ATTACK", "RECYCLE") or act.priority == "P3":
                out.append(act)
                continue
            p_obj = planets_by_id.get(act.planet_id, default_p)
            max_slots = self._max_fleet_slots_for_planet_locked(p_obj)
            used_slots = _used_for(p_obj)
            if act.kind == "SPY":
                if used_slots >= max_slots:
                    cur_r = str(act.reason or "")
                    new_r = (
                        cur_r
                        if ("in flight" in cur_r.lower() or "fleet slots" in cur_r.lower())
                        else f"{cur_r} [Hold: Fleet slots full ({used_slots}/{max_slots} in flight)]"
                    )
                    if act.executable_now or new_r != cur_r:
                        changed = True
                        out.append(replace(act, executable_now=False, reason=new_r))
                        continue
                elif spy_only_res:
                    spy_cap = min(max_slots, res_spy_cfg)
                    spy_used_here = _spy_used_for(p_obj) + spy_dispatched_by_pid.get(p_obj.planet_id, 0)
                    if spy_used_here >= spy_cap:
                        cur_r = str(act.reason or "")
                        new_r = (
                            cur_r
                            if "restricted to reserved" in cur_r.lower()
                            else f"{cur_r} [Hold: Spy restricted to reserved slot(s) ({spy_used_here}/{spy_cap} spy slots used)]"
                        )
                        if act.executable_now or new_r != cur_r:
                            changed = True
                            out.append(replace(act, executable_now=False, reason=new_r))
                            continue
                    elif act.executable_now:
                        spy_dispatched_by_pid[p_obj.planet_id] = spy_dispatched_by_pid.get(p_obj.planet_id, 0) + 1
                out.append(act)
                continue

            eff_res = _spy_res_for(p_obj)
            tr_res = tr_res_by_pid.get(p_obj.planet_id, 0)
            col_res = col_res_by_pid.get(p_obj.planet_id, 0)
            st_res = st_res_by_pid.get(p_obj.planet_id, 0)
            if used_slots >= max_slots:
                cur_r = str(act.reason or "")
                new_r = (
                    cur_r
                    if ("in flight" in cur_r.lower() or "in transit" in cur_r.lower() or "fleet slots" in cur_r.lower())
                    else f"{cur_r} [Hold: Fleet slots full ({used_slots}/{max_slots} in flight)]"
                )
                if act.executable_now or new_r != cur_r:
                    changed = True
                    out.append(replace(act, executable_now=False, reason=new_r))
                    continue
            elif eff_res > 0 and used_slots >= max_slots - eff_res:
                cur_r = str(act.reason or "")
                new_r = (
                    cur_r
                    if "reserved for spy" in cur_r.lower()
                    else f"{cur_r} [Hold: Fleet slot reserved for Spy probes ({used_slots}/{max_slots} in flight, {eff_res} reserved)]"
                )
                if act.executable_now or new_r != cur_r:
                    changed = True
                    out.append(replace(act, executable_now=False, reason=new_r))
                    continue
            elif act.kind in ("COLONIZE", "STATION", "ATTACK", "RECYCLE") and tr_res > 0 and used_slots >= max_slots - eff_res - tr_res:
                cur_r = str(act.reason or "")
                new_r = (
                    cur_r
                    if "reserved for transport" in cur_r.lower()
                    else f"{cur_r} [Hold: Fleet slot reserved for Transport ({used_slots}/{max_slots} in flight, {tr_res} reserved)]"
                )
                if act.executable_now or new_r != cur_r:
                    changed = True
                    out.append(replace(act, executable_now=False, reason=new_r))
                    continue
            elif act.kind in ("STATION", "ATTACK", "RECYCLE") and col_res > 0 and used_slots >= max_slots - eff_res - tr_res - col_res:
                cur_r = str(act.reason or "")
                new_r = (
                    cur_r
                    if "reserved for colony" in cur_r.lower()
                    else f"{cur_r} [Hold: Fleet slot reserved for Colony ship ({used_slots}/{max_slots} in flight, {col_res} reserved)]"
                )
                if act.executable_now or new_r != cur_r:
                    changed = True
                    out.append(replace(act, executable_now=False, reason=new_r))
                    continue
            elif act.kind in ("ATTACK", "RECYCLE") and st_res > 0 and used_slots >= max_slots - eff_res - tr_res - col_res - st_res:
                cur_r = str(act.reason or "")
                new_r = (
                    cur_r
                    if "reserved for station" in cur_r.lower()
                    else f"{cur_r} [Hold: Fleet slot reserved for Station deployment ({used_slots}/{max_slots} in flight, {st_res} reserved)]"
                )
                if act.executable_now or new_r != cur_r:
                    changed = True
                    out.append(replace(act, executable_now=False, reason=new_r))
                    continue
            out.append(act)
        if changed and re_sort_priority:
            prio_rank = {"P0": 0, "P1": 1, "P2": 2, "P3": 3}
            out.sort(
                key=lambda a: (
                    prio_rank.get(a.priority, 9),
                    -1 if a.kind == "ALERT_HOSTILE" else (0 if a.executable_now else 1),
                    _fleet_subrank(a),
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
        db_chk_day = str(mem.get("daily_bonus_checked_day") or "")
        db_col_day = str(mem.get("daily_bonus_collected_day") or "")
        db_avail_day = str(mem.get("daily_bonus_available_day") or "")
        db_avail = bool(mem.get("daily_bonus_available", False)) and (db_avail_day in ("", today))
        free_rem = mem.get("free_spins_remaining")
        already_done = (last_act_day == today) or (
            last_chk_day == today and free_rem == 0 and db_chk_day == today and not db_avail
        )
        return {
            "today_day_key": today,
            "due": not already_done,
            "last_activated_day": last_act_day,
            "last_checked_day": last_chk_day,
            "daily_bonus_checked_day": db_chk_day,
            "daily_bonus_collected_day": db_col_day,
            "daily_bonus_available": db_avail,
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
        daily_bonus_checked: Optional[bool] = None,
        daily_bonus_collected: Optional[bool] = None,
        daily_bonus_available: Optional[bool] = None,
        ts: Optional[float] = None,
    ) -> dict[str, Any]:
        eff_now = round(_safe_api_float(ts, time.time()) if ts is not None else time.time(), 3)
        if self._db is not None and hasattr(self._db, "record_transmitter_state"):
            return dict(
                self._db.record_transmitter_state(
                    activated=activated,
                    free_spins_remaining=free_spins_remaining,
                    reward=reward,
                    daily_bonus_checked=daily_bonus_checked,
                    daily_bonus_collected=daily_bonus_collected,
                    daily_bonus_available=daily_bonus_available,
                    ts=eff_now,
                )
            )
        today = server_day_key(eff_now)
        mem = self._transmitter_state_mem
        if activated:
            mem["last_activated_day"] = today
            mem["last_activated_ts"] = eff_now
            mem["total_activations"] = _safe_api_int(mem.get("total_activations"), 0) + 1
        if daily_bonus_checked is True or daily_bonus_collected is True:
            mem["daily_bonus_checked_day"] = today
        if daily_bonus_collected is True:
            mem["daily_bonus_collected_day"] = today
        if daily_bonus_available is not None:
            mem["daily_bonus_available"] = bool(daily_bonus_available)
            mem["daily_bonus_available_day"] = today if daily_bonus_available else ""
        elif activated or daily_bonus_collected is True or daily_bonus_checked is True:
            mem["daily_bonus_available"] = False
            mem["daily_bonus_available_day"] = ""
        mem["last_checked_day"] = today
        mem["last_checked_ts"] = eff_now
        if free_spins_remaining is not None:
            mem["free_spins_remaining"] = max(0, _safe_api_int(free_spins_remaining, 0))
        if reward:
            mem["last_reward"] = _bounded_api_str(reward, default="")[:160]
        return self._get_transmitter_status_locked(now_ts=eff_now)

    def _note_daily_login_bonus_seen_locked(
        self,
        *,
        present: bool,
        ts: Optional[float] = None,
    ) -> dict[str, Any]:
        eff_now = round(_safe_api_float(ts, time.time()) if ts is not None else time.time(), 3)
        if self._db is not None and hasattr(self._db, "note_daily_login_bonus_seen"):
            return dict(self._db.note_daily_login_bonus_seen(present=present, ts=eff_now))
        today = server_day_key(eff_now)
        mem = self._transmitter_state_mem
        if present:
            if (
                str(mem.get("last_activated_day") or "") != today
                and str(mem.get("daily_bonus_collected_day") or "") != today
            ):
                mem["daily_bonus_available"] = True
                mem["daily_bonus_available_day"] = today
        else:
            mem["daily_bonus_available"] = False
            mem["daily_bonus_available_day"] = ""
        return self._get_transmitter_status_locked(now_ts=eff_now)

    def _get_galaxy_sweep_state_locked(
        self,
        *,
        now_ts: Optional[float] = None,
        include_smart: bool = True,
    ) -> dict[str, Any]:
        eff_now = _safe_api_float(now_ts, time.time()) if now_ts is not None else time.time()
        if self._db is not None and hasattr(self._db, "get_galaxy_sweep_state"):
            st = dict(self._db.get_galaxy_sweep_state(now_ts=eff_now))
        else:
            st = dict(self._galaxy_sweep_mem)
            st["due"] = eff_now >= _safe_api_float(st.get("next_sweep_due_ts"), 0.0)
        st["click_delay_seconds"] = round(
            max(0.2, min(60.0, _safe_api_float(getattr(self.strategy_config, "galaxy_scan_click_delay_seconds", 1.5), 1.5))),
            3,
        )
        st["click_jitter_seconds"] = round(
            max(0.0, min(30.0, _safe_api_float(getattr(self.strategy_config, "galaxy_scan_click_jitter_seconds", 0.6), 0.6))),
            3,
        )
        st["in_progress"] = bool(self._sweep_in_progress)
        st["blocking_other_actions"] = bool(self._is_galaxy_scan_blocking_locked())
        if include_smart:
            self._refresh_smart_sweep_summary_locked(now_ts=eff_now)
        return self._merge_smart_sweep_summary_locked(st)

    def _is_galaxy_scan_blocking_locked(self) -> bool:
        return bool(
            self._sweep_in_progress
            and getattr(self.strategy_config, "galaxy_scan_block_other_actions", True)
        )

    @staticmethod
    def _galaxy_scan_busy_error_message() -> str:
        return (
            "Galaxy scan in progress — wait for scan to finish, "
            "or Pause / disable Galaxy Scan first."
        )

    def _has_urgent_p0_escape_flight_locked(self) -> tuple[bool, str]:
        if not bool(getattr(self.strategy_config, "allow_fleetsave", False)):
            return False, ""
        st = self._last_state
        if st is not None and any(bool(getattr(fa, "hostile", False)) for fa in (st.fleet_alerts or ())):
            active = st.active_planet()
            if active is not None:
                other_coords = next(
                    (p.coords for p in st.planets if p.planet_id != active.planet_id),
                    active.coords,
                )
                escape = plan_escape_flight(
                    active,
                    target_coords=other_coords,
                    fuel_reserve_spice=self.strategy_config.fleet_save_fuel_reserve_spice,
                )
                if escape.feasible:
                    return True, "Preempted galaxy scan for P0 hostile Escape Flight."
        if self._db is not None and hasattr(self._db, "get_manual_queue"):
            try:
                pending = self._db.get_manual_queue(status="pending", limit=50)
            except Exception:
                pending = []
            for item in pending or ():
                if (
                    isinstance(item, Mapping)
                    and str(item.get("priority") or "").strip().upper() == "P0"
                    and str(item.get("kind") or "").strip().upper() == "ESCAPE_FLIGHT"
                ):
                    return True, "Preempted galaxy scan for pinned P0 Escape Flight."
        return False, ""

    def _preempt_galaxy_sweep_and_wait(
        self,
        reason: str = "Preempted by P0 emergency Escape Flight.",
        *,
        timeout: float = 10.0,
    ) -> None:
        if not self._sweep_in_progress:
            return
        self._sweep_preempt_reason = reason
        self._sweep_preempt_event.set()
        wake_ev = getattr(self, "_sweep_wake_event", None)
        if wake_ev is not None:
            wake_ev.set()
        done_ev = getattr(self, "_sweep_done_event", None)
        if done_ev is not None and threading.current_thread() is not getattr(self, "_sweep_thread", None):
            done_ev.wait(timeout=max(0.1, float(timeout)))

    # ---- Smart 2-Phase Galaxy Scanner (exploratory -> weighted targeted refresh) ----

    _SMART_SWEEP_SUMMARY_TTL_S: float = 60.0
    _SMART_SWEEP_STATE_KEYS: tuple[str, ...] = (
        "scan_phase",
        "selection_mode",
        "explored_systems",
        "unscanned_systems",
        "total_systems",
        "coverage_pct",
        "universe_explored_systems",
        "universe_total_systems",
        "universe_coverage_pct",
        "refresh_pct",
        "discovery_pct",
        "exploratory_count",
        "refresh_count",
        "scan_galaxies",
        "sample_targets",
    )

    def _own_sweep_coords_locked(self, *, state_override: Optional[GameState] = None) -> list[str]:
        out: list[str] = []
        st = state_override if state_override is not None else self._last_state
        if st is None:
            return out
        ap = st.active_planet()
        ordered = ([ap] if ap is not None else []) + [p for p in st.planets if ap is None or p.planet_id != ap.planet_id]
        for p in ordered:
            if normalize_planet_type(p) == "raid":
                continue
            c = str(getattr(p, "coords", "") or "").strip()
            if c and c not in out:
                out.append(c)
        return out

    def _max_galaxies_locked(self) -> int:
        mg = getattr(self, "_universe_max_galaxies", None)
        if isinstance(mg, int) and not isinstance(mg, bool) and 1 <= mg <= 14:
            return mg
        return 14

    def _observe_galaxy_universe_bounds_locked(
        self,
        *,
        client: Any = None,
        html: str = "",
    ) -> int:
        obs: Optional[int] = None
        if client is not None:
            raw_c = getattr(client, "last_galaxy_max_galaxies", None)
            if isinstance(raw_c, int) and not isinstance(raw_c, bool) and 1 <= raw_c <= 14:
                obs = raw_c
        if obs is None and isinstance(html, str) and html and "SI_TPL.galaxy" in html:
            m_mg = re.search(r"\bmaxGalaxies\b\s*[:=]\s*(\d{1,2})\b", html, re.ASCII)
            if m_mg:
                cand = _safe_api_int(m_mg.group(1), 0)
                if 1 <= cand <= 14:
                    obs = cand
        if obs is not None and obs != getattr(self, "_universe_max_galaxies", None):
            self._universe_max_galaxies = obs
            if self._db is not None:
                save_meta_fn = getattr(self._db, "save_meta_json", None)
                if callable(save_meta_fn):
                    try:
                        save_meta_fn("universe_max_galaxies", obs)
                    except Exception:
                        pass
                else:
                    set_meta_fn = getattr(self._db, "set_meta", None)
                    if callable(set_meta_fn):
                        try:
                            set_meta_fn("universe_max_galaxies", str(obs))
                        except Exception:
                            pass
            self._smart_sweep_summary = None
            self._smart_sweep_summary_ts = 0.0
            self._invalidate_dashboard_cache()
        return self._max_galaxies_locked()

    def _call_get_galaxy_sector_intel_locked(
        self,
        db_obj: Any,
        *,
        galaxies: Optional[Sequence[int]],
        now_ts: float,
        max_galaxies: int,
    ) -> Optional[Mapping[str, Any]]:
        get_intel = getattr(db_obj, "get_galaxy_sector_intel", None) if db_obj is not None else None
        if not callable(get_intel):
            return None
        kwargs: dict[str, Any] = {"galaxies": galaxies, "now_ts": now_ts}
        if GameApiBridge._supports_kwarg(get_intel, "max_galaxies"):
            kwargs["max_galaxies"] = max(1, min(14, int(max_galaxies)))
        raw_intel = get_intel(**kwargs)
        return raw_intel if isinstance(raw_intel, Mapping) else None

    def _current_galaxy_cursor_locked(
        self,
        *,
        galaxies: Sequence[int] = (),
    ) -> Optional[tuple[int, int]]:
        """Return the active `(galaxy, system)` cursor for contiguous arrow-first sweep anchoring."""
        max_g = self._max_galaxies_locked()
        gal_set = {max(1, min(max_g, _safe_api_int(g, 0))) for g in galaxies if _safe_api_int(g, 0) > 0}
        live_coords = getattr(self, "_last_galaxy_browser_coords", None)
        if (
            isinstance(live_coords, tuple)
            and len(live_coords) == 2
            and all(isinstance(v, int) and not isinstance(v, bool) for v in live_coords)
        ):
            lg, ls = live_coords
            if 1 <= lg <= max_g and 1 <= ls <= 400 and (not gal_set or lg in gal_set):
                return (lg, ls)
        sw_st = self._get_galaxy_sweep_state_locked(include_smart=False)
        if _safe_api_int(sw_st.get("total_sweeps"), 0) > 0:
            cg = _safe_api_int(sw_st.get("cursor_galaxy"), 0)
            cs = _safe_api_int(sw_st.get("cursor_system"), 0)
            if 1 <= cg <= max_g and 1 <= cs <= 400 and (not gal_set or cg in gal_set):
                return (cg, cs)
        return None

    def _plan_smart_galaxy_sweep_locked(
        self,
        *,
        batch_size: int,
        galaxies: Sequence[int],
        collect_colonies: bool,
        now_ts: float,
        sector_intel_override: Optional[Mapping[str, Any]] = None,
        explore_first_override: Optional[bool] = None,
        refresh_pct_override: Optional[int] = None,
        scanned_systems_override: Optional[Mapping[str, float]] = None,
        state_override: Optional[GameState] = None,
        raid_targets_override: Optional[Sequence[Mapping[str, Any]]] = None,
        current_cursor: Optional[tuple[int, int]] = None,
    ) -> Optional[dict[str, Any]]:
        """Select the next smart sweep batch via SQLite `get_galaxy_sector_intel` + `select_smart_galaxy_scan_batch`."""
        cfg = self.strategy_config
        max_g = self._max_galaxies_locked()
        st_ref = state_override if state_override is not None else self._last_state
        gal_list = sorted({max(1, min(max_g, _safe_api_int(x, 1))) for x in galaxies if _safe_api_int(x, 0) > 0})
        eff_cursor = (
            current_cursor
            if current_cursor is not None
            else self._current_galaxy_cursor_locked(galaxies=gal_list)
        )
        intel: Optional[Mapping[str, Any]] = (
            sector_intel_override if isinstance(sector_intel_override, Mapping) else None
        )
        if intel is None and self._db is not None:
            try:
                intel = self._call_get_galaxy_sector_intel_locked(
                    self._db,
                    galaxies=gal_list or None,
                    now_ts=now_ts,
                    max_galaxies=max_g,
                )
            except Exception as exc:  # pragma: no cover - defensive: in-memory fallback below
                intel = None
                if self.log_dir is not None:
                    append_crash_debug_log(
                        component="api_server.smart_galaxy_sweep",
                        event="sector_intel_error",
                        error=exc,
                    )
        raid_stats = getattr(st_ref, "raid_stats", None) if st_ref is not None else None
        scanned_sys = (
            scanned_systems_override
            if scanned_systems_override is not None
            else self._get_scanned_systems()
        )
        raid_src = raid_targets_override if raid_targets_override is not None else (self._raid_targets or ())
        eff_explore_first = (
            bool(explore_first_override)
            if explore_first_override is not None
            else bool(getattr(cfg, "galaxy_scan_explore_first", True))
        )
        eff_refresh_pct = max(
            0,
            min(
                100,
                _safe_api_int(
                    refresh_pct_override
                    if refresh_pct_override is not None
                    else getattr(cfg, "galaxy_scan_refresh_pct", 0),
                    0,
                ),
            ),
        )
        try:
            plan = select_smart_galaxy_scan_batch(
                own_coords=self._own_sweep_coords_locked(state_override=st_ref),
                sector_intel=intel,
                galaxies=gal_list or None,
                scanned_systems=scanned_sys,
                raid_targets=[r for r in raid_src if isinstance(r, Mapping)],
                raid_stats=raid_stats if isinstance(raid_stats, Mapping) else None,
                batch_size=max(1, min(400 * max(1, len(gal_list)), int(batch_size))),
                explore_first=eff_explore_first,
                refresh_pct=eff_refresh_pct,
                near_weight=max(0.0, min(20.0, _safe_api_float(getattr(cfg, "galaxy_scan_near_weight", 2.0), 2.0))),
                attack_weight=max(0.0, min(20.0, _safe_api_float(getattr(cfg, "galaxy_scan_attack_weight", 3.0), 3.0))),
                collect_colonies=bool(collect_colonies),
                current_cursor=eff_cursor,
                now_ts=now_ts,
            )
        except Exception as exc:  # pragma: no cover - defensive: contiguous cursor fallback
            if self.log_dir is not None:
                append_crash_debug_log(
                    component="api_server.smart_galaxy_sweep",
                    event="select_batch_error",
                    error=exc,
                )
            return None
        if not isinstance(plan, Mapping):
            return None
        out_plan = dict(plan)
        uni_tot = max_g * 400
        if isinstance(intel, Mapping) and intel.get("universe_explored_systems") is not None:
            uni_exp = max(0, _safe_api_int(intel.get("universe_explored_systems"), 0))
            uni_tot = max(1, _safe_api_int(intel.get("universe_total_systems"), uni_tot))
        else:
            scanned_all = scanned_sys or {}
            uni_keys: set[tuple[int, int]] = set()
            for sk, ts_v in scanned_all.items():
                if _safe_api_float(ts_v, 0.0) <= 0.0:
                    continue
                m_s = re.search(r"(\d{1,2})\s*:\s*(\d{1,3})", str(sk), re.ASCII)
                if m_s:
                    gi_u, si_u = int(m_s.group(1)), int(m_s.group(2))
                    if 1 <= gi_u <= max_g and 1 <= si_u <= 400:
                        uni_keys.add((gi_u, si_u))
            uni_exp = max(len(uni_keys), _safe_api_int(out_plan.get("explored_systems"), 0))
        out_plan["universe_explored_systems"] = uni_exp
        out_plan["universe_total_systems"] = uni_tot
        out_plan["universe_coverage_pct"] = round(min(100.0, (uni_exp / uni_tot) * 100.0), 2)
        return out_plan

    def _store_smart_sweep_summary_locked(
        self,
        plan: Optional[Mapping[str, Any]],
        *,
        selection_mode: str,
        now_ts: float,
        sample_targets: Optional[Sequence[Mapping[str, Any]]] = None,
    ) -> dict[str, Any]:
        if not isinstance(plan, Mapping):
            return dict(getattr(self, "_smart_sweep_summary", {}) or {})
        default_uni_tot = self._max_galaxies_locked() * 400
        samples = sample_targets if sample_targets is not None else (plan.get("sample_targets") or [])
        summary = {
            "scan_phase": str(plan.get("scan_phase") or plan.get("phase") or "exploratory"),
            "selection_mode": selection_mode,
            "explored_systems": max(0, _safe_api_int(plan.get("explored_systems"), 0)),
            "unscanned_systems": max(0, _safe_api_int(plan.get("unscanned_systems"), 0)),
            "total_systems": max(0, _safe_api_int(plan.get("total_systems"), 0)),
            "coverage_pct": round(max(0.0, min(100.0, _safe_api_float(plan.get("coverage_pct"), 0.0))), 2),
            "universe_explored_systems": max(0, _safe_api_int(plan.get("universe_explored_systems"), 0)),
            "universe_total_systems": max(1, _safe_api_int(plan.get("universe_total_systems"), default_uni_tot)),
            "universe_coverage_pct": round(
                max(0.0, min(100.0, _safe_api_float(plan.get("universe_coverage_pct"), 0.0))),
                2,
            ),
            "refresh_pct": round(
                max(0.0, min(100.0, _safe_api_float(plan.get("refresh_pct"), 0.0))),
                1,
            ),
            "discovery_pct": round(
                max(0.0, min(100.0, _safe_api_float(plan.get("discovery_pct"), 100.0))),
                1,
            ),
            "exploratory_count": max(0, _safe_api_int(plan.get("exploratory_count"), 0)),
            "refresh_count": max(0, _safe_api_int(plan.get("refresh_count"), 0)),
            "scan_galaxies": [
                _safe_api_int(g, 1) for g in (plan.get("galaxies") or []) if _safe_api_int(g, 0) > 0
            ],
            "sample_targets": [dict(s) for s in list(samples)[:5] if isinstance(s, Mapping)],
            "smart_summary_ts": round(now_ts, 3),
        }
        self._smart_sweep_summary = summary
        self._smart_sweep_summary_ts = now_ts
        return dict(summary)

    def _refresh_smart_sweep_summary_locked(self, *, now_ts: float, force: bool = False) -> None:
        last_ts = _safe_api_float(getattr(self, "_smart_sweep_summary_ts", 0.0), 0.0)
        if not force and getattr(self, "_smart_sweep_summary", None) and (now_ts - last_ts) < self._SMART_SWEEP_SUMMARY_TTL_S:
            return
        if self._sweep_in_progress and getattr(self, "_smart_sweep_summary", None) and not force:
            return
        cfg = self.strategy_config
        pages = max(1, min(400, _safe_api_int(getattr(cfg, "galaxy_sweep_pages", 100), 100)))
        allocated_normal = count_allocated_normal_planets(self._last_state)
        max_planets_cap = max(1, _safe_api_int(getattr(cfg, "max_planets", 11), 11))
        plan = self._plan_smart_galaxy_sweep_locked(
            batch_size=pages,
            galaxies=self._sweep_target_galaxies_locked(),
            collect_colonies=bool(allocated_normal < max_planets_cap),
            now_ts=now_ts,
        )
        if plan is not None:
            self._store_smart_sweep_summary_locked(plan, selection_mode="smart", now_ts=now_ts)
        else:
            self._smart_sweep_summary_ts = now_ts

    def _merge_smart_sweep_summary_locked(self, st: dict[str, Any]) -> dict[str, Any]:
        summary = getattr(self, "_smart_sweep_summary", None) or {}
        default_uni_tot = self._max_galaxies_locked() * 400
        st.setdefault("scan_phase", summary.get("scan_phase", "exploratory"))
        st.setdefault("selection_mode", summary.get("selection_mode", "smart"))
        st.setdefault("explored_systems", summary.get("explored_systems", 0))
        st.setdefault("unscanned_systems", summary.get("unscanned_systems", 0))
        st.setdefault("total_systems", summary.get("total_systems", 0))
        st.setdefault("coverage_pct", summary.get("coverage_pct", 0.0))
        st.setdefault("universe_explored_systems", summary.get("universe_explored_systems", 0))
        st.setdefault("universe_total_systems", summary.get("universe_total_systems", default_uni_tot))
        st.setdefault("universe_coverage_pct", summary.get("universe_coverage_pct", 0.0))
        st.setdefault("refresh_pct", summary.get("refresh_pct", 0.0))
        st.setdefault("discovery_pct", summary.get("discovery_pct", 100.0))
        st.setdefault("exploratory_count", summary.get("exploratory_count", 0))
        st.setdefault("refresh_count", summary.get("refresh_count", 0))
        st.setdefault("scan_galaxies", list(summary.get("scan_galaxies") or []))
        st.setdefault("sample_targets", [dict(s) for s in (summary.get("sample_targets") or [])])
        return st

    @staticmethod
    def _contiguous_sweep_targets(
        start_gal: int,
        start_sys: int,
        count: int,
        *,
        rotate_galaxies: Sequence[int] = (),
    ) -> list[tuple[int, int]]:
        """Legacy contiguous cursor walk (`start_sys..400 -> 1..`), rotating owned galaxies on wrap."""
        out: list[tuple[int, int]] = []
        g, s = int(start_gal), max(1, min(400, int(start_sys)))
        gals = list(rotate_galaxies)
        for _ in range(max(0, int(count))):
            out.append((g, s))
            if s >= 400:
                s = 1
                if len(gals) > 1:
                    idx_g = gals.index(g) if g in gals else 0
                    g = gals[(idx_g + 1) % len(gals)]
            else:
                s += 1
        return out

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
        st["click_delay_seconds"] = round(
            max(0.2, min(60.0, _safe_api_float(getattr(self.strategy_config, "galaxy_scan_click_delay_seconds", 1.5), 1.5))),
            3,
        )
        st["click_jitter_seconds"] = round(
            max(0.0, min(30.0, _safe_api_float(getattr(self.strategy_config, "galaxy_scan_click_jitter_seconds", 0.6), 0.6))),
            3,
        )
        st["in_progress"] = bool(self._sweep_in_progress)
        return self._merge_smart_sweep_summary_locked(st)

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
            or bool(getattr(cfg, "allow_galaxy_scan", False))
            or _tutorial_colonize_task(state) is not None
        ):
            return state
        get_col_fn = getattr(self._db, "get_colony_candidates", None)
        if not callable(get_col_fn):
            return state
        get_disc_fn = getattr(self._db, "get_discarded_colony_coords", None)
        discarded_set: set[str] = set(get_disc_fn()) if callable(get_disc_fn) else set()
        ap = state.active_planet()
        origin_c = ap.coords if ap is not None else ""
        min_f = max(50, _safe_api_int(getattr(cfg, "min_colony_fields", 300), 300))
        col_cands = get_col_fn(
            origin_coords=origin_c,
            min_fields_target=min_f,
            exclude_coords=self._own_planet_coords(state),
            limit=200,
        )
        existing_rt = list(getattr(state, "raid_targets", ()) or ())
        by_c: dict[str, dict[str, Any]] = {}
        ordered_rt: list[dict[str, Any]] = []
        changed_rt = False
        for r_item in existing_rt:
            if not isinstance(r_item, Mapping):
                continue
            d_item = dict(r_item)
            nc = normalize_coords(d_item.get("coords") or d_item.get("target_coords") or "")
            if nc and nc in discarded_set and str(d_item.get("status_color") or "").lower() == "free":
                changed_rt = True
                continue
            if nc:
                by_c[nc] = d_item
            ordered_rt.append(d_item)
        if not col_cands:
            return replace(state, raid_targets=tuple(ordered_rt)) if changed_rt else state
        added_any = changed_rt
        for cand in col_cands:
            if not isinstance(cand, Mapping):
                continue
            nc = normalize_coords(cand.get("coords") or "")
            if not nc or nc in discarded_set:
                continue
            cand_blk = _safe_api_float(cand.get("blocked_until_ts", 0.0))
            cand_rsn = str(cand.get("block_reason") or "")
            if nc in self._target_blocks and float(self._target_blocks[nc][0]) > cand_blk:
                cand_blk = float(self._target_blocks[nc][0])
                cand_rsn = str(self._target_blocks[nc][1])
            if nc in by_c:
                cur_entry = by_c[nc]
                uf = cand.get("uninhabited_fields")
                if uf is not None and cur_entry.get("uninhabited_fields") is None:
                    cur_entry["uninhabited_fields"] = _safe_api_int(uf, 0)
                    added_any = True
                for ek in ("colonize_min_fields", "colonize_max_fields", "temperature"):
                    ev = cand.get(ek)
                    if ev is not None and cur_entry.get(ek) is None:
                        cur_entry[ek] = _safe_api_int(ev, 0)
                        added_any = True
                if cand_blk > _safe_api_float(cur_entry.get("blocked_until_ts", 0.0)):
                    cur_entry["blocked_until_ts"] = cand_blk
                    cur_entry["block_reason"] = cand_rsn
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
                "colonize_min_fields": cand.get("colonize_min_fields"),
                "colonize_max_fields": cand.get("colonize_max_fields"),
                "temperature": cand.get("temperature"),
                "last_scanned_ts": _safe_api_float(cand.get("last_scanned_ts", 0.0)),
                "last_spied_ts": _safe_api_float(cand.get("last_spied_ts", 0.0)),
                "blocked_until_ts": cand_blk,
                "block_reason": cand_rsn,
                "has_spy_intel": bool(cand.get("probed", False)),
            }
            by_c[nc] = free_entry
            ordered_rt.append(free_entry)
            added_any = True
        return replace(state, raid_targets=tuple(ordered_rt)) if added_any else state

    def _get_custom_planet_names_locked(self) -> dict[str, str]:
        return dict(self._custom_planet_names)

    def _sync_page_limiter_from_config(self, cfg: StrategyConfig) -> None:
        if getattr(self, "_custom_page_limiter_injected", False):
            return
        limit = max(6, min(600, _safe_api_int(getattr(cfg, "page_view_limit_per_minute", 60), 60)))
        if limit <= 24:
            delay_range = (0.9, 1.8)
        else:
            scale = min(1.0, 24.0 / float(limit))
            delay_range = (max(0.25, round(0.9 * scale, 2)), max(0.45, round(1.8 * scale, 2)))
        reserve = min(6, max(1, limit // 4))
        configure_fn = getattr(self._page_limiter, "configure", None)
        if callable(configure_fn):
            configure_fn(
                max_views_per_minute=limit,
                min_delay_range=delay_range,
                reserved_interactive_views=reserve,
            )

    def _patch_last_dashboard_custom_names_locked(self) -> None:
        prev = self._last_dashboard_payload
        if isinstance(prev, Mapping):
            patched = dict(prev)
            patched["custom_planet_names"] = dict(self._custom_planet_names)
            self._last_dashboard_payload = patched

    def _set_custom_planet_name_locked(self, planet_id: int, raw_name: Any) -> dict[str, str]:
        pid = _safe_api_int(planet_id, 0)
        clean_name = GameDatabase.sanitize_custom_planet_name(raw_name)
        if self._db is not None and hasattr(self._db, "set_custom_planet_name"):
            updated = self._db.set_custom_planet_name(pid, clean_name)
            self._custom_planet_names = dict(updated)
        else:
            if pid > 0:
                if clean_name:
                    self._custom_planet_names[str(pid)] = clean_name
                else:
                    self._custom_planet_names.pop(str(pid), None)
        self._invalidate_dashboard_cache()
        self._patch_last_dashboard_custom_names_locked()
        return dict(self._custom_planet_names)

    def _set_custom_planet_names_batch_locked(self, updates: Mapping[int, Any]) -> dict[str, str]:
        if self._db is not None and hasattr(self._db, "set_custom_planet_names"):
            updated = self._db.set_custom_planet_names(updates)
            self._custom_planet_names = dict(updated)
        else:
            for raw_pid, raw_name in updates.items():
                pid = _safe_api_int(raw_pid, 0)
                if pid <= 0:
                    continue
                clean_name = GameDatabase.sanitize_custom_planet_name(raw_name)
                if clean_name:
                    self._custom_planet_names[str(pid)] = clean_name
                else:
                    self._custom_planet_names.pop(str(pid), None)
        self._invalidate_dashboard_cache()
        self._patch_last_dashboard_custom_names_locked()
        return dict(self._custom_planet_names)

    def _apply_custom_planet_names_to_state(
        self,
        state: Optional[GameState],
        custom_names: Optional[Mapping[str, str]] = None,
    ) -> Optional[GameState]:
        if state is None:
            return None
        names_map = custom_names if custom_names is not None else self._custom_planet_names
        if not names_map:
            return state
        changed = False
        new_planets: list[PlanetState] = []
        owned_custom_by_coords: dict[str, tuple[str, str]] = {}
        for p in state.planets:
            custom = names_map.get(str(p.planet_id))
            if custom:
                nc = normalize_coords(p.coords)
                if nc:
                    owned_custom_by_coords[nc] = (str(p.name or ""), custom)
            if custom and custom != p.name:
                changed = True
                new_planets.append(replace(p, name=custom))
            else:
                new_planets.append(p)

        new_queues: Optional[tuple[QueueJob, ...]] = None
        if owned_custom_by_coords and state.active_queues:
            q_changed = False
            patched_q: list[QueueJob] = []
            for q in state.active_queues:
                q_name = str(getattr(q, "name", "") or "")
                if q_name and is_in_flight_fleet_job(q):
                    dest_nc = _extract_coords_from_text(q_name)
                    if dest_nc and dest_nc in owned_custom_by_coords:
                        raw_nm, cust_nm = owned_custom_by_coords[dest_nc]
                        alts = [re.escape(cust_nm), "Colony"]
                        if raw_nm and raw_nm.lower() != "colony":
                            alts.append(re.escape(raw_nm))
                        pat = r"(\[\s*\d{1,2}:\d{1,3}:\d{1,2}\s*\]\s*)(?:" + "|".join(alts) + r")\s*$"
                        updated_q_name = re.sub(
                            pat,
                            lambda m, cn=cust_nm: f"{m.group(1)}{cn}",
                            q_name,
                            flags=re.IGNORECASE,
                        )
                        if updated_q_name != q_name:
                            q = replace(q, name=updated_q_name)
                            q_changed = True
                patched_q.append(q)
            if q_changed:
                new_queues = tuple(patched_q)
                changed = True

        if not changed:
            return state
        upd: dict[str, Any] = {"planets": tuple(new_planets)}
        if new_queues is not None:
            upd["active_queues"] = new_queues
        return replace(state, **upd)

    def _serialize_state_with_custom_names(
        self,
        raw_state: GameState,
        display_state: Optional[GameState] = None,
        custom_names: Optional[Mapping[str, str]] = None,
    ) -> dict[str, Any]:
        names_map = custom_names if custom_names is not None else self._custom_planet_names
        eff_state = (
            display_state
            if display_state is not None
            else (self._apply_custom_planet_names_to_state(raw_state, names_map) or raw_state)
        )
        raw_by_pid = {int(p.planet_id): str(p.name or "") for p in (raw_state.planets or ())}
        out = asdict(eff_state)
        planets_list = out.get("planets")
        if isinstance(planets_list, (list, tuple)):
            planets_out: list[Any] = []
            for p_dict in planets_list:
                if not isinstance(p_dict, dict):
                    planets_out.append(p_dict)
                    continue
                pid = _safe_api_int(p_dict.get("planet_id"), 0)
                raw_name = raw_by_pid.get(pid, str(p_dict.get("name") or ""))
                custom = names_map.get(str(pid)) if pid > 0 else None
                p_dict["game_name"] = raw_name
                p_dict["custom_name"] = custom if custom else None
                if custom:
                    p_dict["name"] = custom
                planets_out.append(p_dict)
            out["planets"] = planets_out
        return out

    def _overlay_custom_names_on_config_optimizer(
        self,
        active_co: Optional[dict[str, Any]],
        snap_state: Optional[GameState],
        custom_names: Mapping[str, str],
    ) -> Optional[dict[str, Any]]:
        if not isinstance(active_co, Mapping):
            return active_co
        out = dict(active_co)
        res = out.get("result")
        if not isinstance(res, Mapping):
            return out
        raw_by_pid: dict[int, str] = {}
        pid_by_coords: dict[str, int] = {}
        if snap_state is not None:
            for p in snap_state.planets:
                raw_by_pid[int(p.planet_id)] = str(p.name or "")
                nc = normalize_coords(p.coords)
                if nc:
                    pid_by_coords[nc] = int(p.planet_id)

        def _patch_planet_row(row: Any) -> Any:
            if not isinstance(row, Mapping):
                return row
            d = dict(row)
            pid = _safe_api_int(d.get("planet_id"), 0)
            if pid <= 0:
                nc = normalize_coords(str(d.get("coords") or ""))
                if nc and nc in pid_by_coords:
                    pid = pid_by_coords[nc]
            raw_game = str(
                (raw_by_pid.get(pid) if pid in raw_by_pid and raw_by_pid.get(pid) else "")
                or d.get("game_name")
                or d.get("name")
                or ""
            )
            custom = custom_names.get(str(pid)) if pid > 0 else None
            d["game_name"] = raw_game
            d["custom_name"] = custom if custom else None
            if custom:
                d["name"] = custom
            elif pid > 0 and pid in raw_by_pid and raw_by_pid[pid]:
                d["name"] = raw_by_pid[pid]
            return d

        res_copy = dict(res)
        patched_planets: Optional[list[Any]] = None
        if isinstance(res_copy.get("planets"), Sequence) and not isinstance(res_copy.get("planets"), (str, bytes)):
            patched_planets = [_patch_planet_row(r) for r in res_copy["planets"]]
            res_copy["planets"] = patched_planets
        if isinstance(res_copy.get("inputs"), Mapping):
            inp_copy = dict(res_copy["inputs"])
            if isinstance(inp_copy.get("planets"), Sequence) and not isinstance(inp_copy.get("planets"), (str, bytes)):
                inp_copy["planets"] = [_patch_planet_row(r) for r in inp_copy["planets"]]
            res_copy["inputs"] = inp_copy
        if patched_planets is not None:
            res_copy["planet_names"] = {
                str(_safe_api_int(pr.get("planet_id"), 0)): str(pr.get("name") or "")
                for pr in patched_planets
                if isinstance(pr, Mapping) and _safe_api_int(pr.get("planet_id"), 0) > 0
            }
            if isinstance(res_copy.get("summary"), Mapping):
                central_row = next(
                    (
                        pr
                        for pr in patched_planets
                        if isinstance(pr, Mapping) and bool(pr.get("is_central_planet", False))
                    ),
                    None,
                )
                if central_row is not None:
                    sum_copy = dict(res_copy["summary"])
                    c_name = str(central_row.get("name") or "")
                    c_coords = str(central_row.get("coords") or "")
                    c_slot = _safe_api_int(central_row.get("slot_index", central_row.get("slot")), 1)
                    sum_copy["central_planet_name"] = c_name
                    if c_coords:
                        sum_copy["central_planet"] = (
                            f"{c_name} [{c_coords}] (slot {c_slot})"
                            if c_name and c_name != c_coords
                            else f"{c_coords} (slot {c_slot})"
                        )
                    res_copy["summary"] = sum_copy
        out["result"] = res_copy
        return out

    def _overlay_custom_names_on_ai_items(
        self,
        items: Sequence[Mapping[str, Any]],
        snap_state: Optional[GameState],
        custom_names: Mapping[str, str],
    ) -> list[dict[str, Any]]:
        raw_by_pid: dict[int, str] = {}
        pid_by_coords: dict[str, int] = {}
        if snap_state is not None:
            for p in snap_state.planets:
                raw_by_pid[int(p.planet_id)] = str(p.name or "")
                nc = normalize_coords(p.coords)
                if nc:
                    pid_by_coords[nc] = int(p.planet_id)
        out: list[dict[str, Any]] = []
        for item in items:
            d = dict(item)
            pid = _safe_api_int(d.get("planet_id"), 0)
            if pid <= 0:
                nc = normalize_coords(str(d.get("planet_coords") or ""))
                if nc and nc in pid_by_coords:
                    pid = pid_by_coords[nc]
            if pid > 0:
                custom = custom_names.get(str(pid))
                if custom:
                    d["planet_name"] = custom
                elif pid in raw_by_pid:
                    d["planet_name"] = raw_by_pid[pid]
            out.append(d)
        return out

    def _record_transport_dispatch_locked(
        self,
        *,
        origin_coords: str,
        dest_coords: str,
        cargo: Any,
        ships: Any = "",
        source: str = "dispatch",
        ts: Optional[float] = None,
    ) -> None:
        orig = normalize_coords(origin_coords)
        dest = normalize_coords(dest_coords)
        if not orig or not dest:
            return
        cargo_d = GameDatabase._normalize_cargo_dict(cargo)
        if sum(cargo_d.values()) <= 0:
            return
        now_ts = round(_safe_api_float(ts, time.time()) if ts is not None else time.time(), 3)
        ships_s = _bounded_api_str(ships, default="")[:140] if ships else ""
        src_s = str(source or "dispatch").strip().lower()[:32] or "dispatch"

        # Fast in-memory dedupe before touching SQLite
        dedupe_window = 7200.0 if src_s == "observed_outbound" else 5.0
        for prev in reversed(self._recent_transport_dispatches):
            if (now_ts - _safe_api_float(prev.get("ts"), 0.0)) > dedupe_window:
                break
            if (
                prev.get("origin_coords") == orig
                and prev.get("dest_coords") == dest
                and prev.get("cargo") == cargo_d
                and (src_s == "observed_outbound" or prev.get("source") == src_s)
            ):
                if ships_s and not prev.get("ships"):
                    prev["ships"] = ships_s
                    if self._db is not None and hasattr(self._db, "record_transport_dispatch"):
                        self._recent_transport_dispatches = self._db.record_transport_dispatch(
                            origin_coords=orig,
                            dest_coords=dest,
                            cargo=cargo_d,
                            ships=ships_s,
                            source=src_s,
                            ts=now_ts,
                        )
                return

        if self._db is not None and hasattr(self._db, "record_transport_dispatch"):
            self._recent_transport_dispatches = self._db.record_transport_dispatch(
                origin_coords=orig,
                dest_coords=dest,
                cargo=cargo_d,
                ships=ships_s,
                source=src_s,
                ts=now_ts,
            )
        else:
            self._recent_transport_dispatches.append(
                {
                    "origin_coords": orig,
                    "dest_coords": dest,
                    "cargo": cargo_d,
                    "ships": ships_s,
                    "ts": now_ts,
                    "source": src_s,
                }
            )
            self._recent_transport_dispatches = self._recent_transport_dispatches[-120:]

    def _capture_observed_outbound_transports_locked(self, state: GameState) -> None:
        """Remember cargo of live outbound `TRANSPORT` flights so returning legs still show `DELIVERED` values."""
        for q in getattr(state, "active_queues", ()) or ():
            if not is_in_flight_fleet_job(q):
                continue
            name_s = str(getattr(q, "name", "") or "")
            det_s = str(getattr(q, "detail", "") or "")
            name_l = name_s.lower()
            det_l = det_s.lower()
            is_ret = ("returning" in det_l) or ("↩" in name_s) or ("rück" in det_l) or ("rueck" in det_l)
            if is_ret:
                continue
            if not (name_l.startswith("transport") or "transport →" in name_l or "transport ->" in name_l):
                continue
            cargo_obj = getattr(q, "cargo", None) or getattr(q, "expected_cargo", None)
            if not isinstance(cargo_obj, Resources) or cargo_obj.is_zero():
                continue
            orig = normalize_coords(str(getattr(q, "origin_coords", "") or ""))
            dest = _extract_coords_from_text(name_s)
            if not orig or not dest:
                continue
            ships_part = det_s.split("·", 1)[1].strip() if "·" in det_s else ""
            self._record_transport_dispatch_locked(
                origin_coords=orig,
                dest_coords=dest,
                cargo=cargo_obj,
                ships=ships_part,
                source="observed_outbound",
            )

    def _match_recent_transport_dispatch_locked(
        self,
        origin_coords: str,
        dest_coords: str,
        *,
        ships_detail: str = "",
        used_indices: Optional[set[int]] = None,
        max_age_seconds: float = 86400.0,
    ) -> Optional[tuple[int, Resources]]:
        if not self._recent_transport_dispatches:
            return None
        orig = normalize_coords(origin_coords)
        dest = normalize_coords(dest_coords)
        if not dest and not orig:
            return None
        now_ts = time.time()
        ships_norm = " ".join(str(ships_detail or "").strip().lower().split())
        used = used_indices if used_indices is not None else set()

        eligible: list[tuple[int, dict[str, Any]]] = []
        for idx in range(len(self._recent_transport_dispatches) - 1, -1, -1):
            if idx in used:
                continue
            ent = self._recent_transport_dispatches[idx]
            if not isinstance(ent, Mapping):
                continue
            ent_ts = _safe_api_float(ent.get("ts"), 0.0)
            if ent_ts > 0.0 and (now_ts - ent_ts) > max_age_seconds:
                continue
            eligible.append((idx, ent))

        if not eligible:
            return None

        def _entry_to_res(ent: Mapping[str, Any]) -> Optional[Resources]:
            c = ent.get("cargo")
            if not isinstance(c, Mapping):
                return None
            r = Resources(
                pig_iron=max(0, _safe_api_int(c.get("pig_iron", 0), 0)),
                metal=max(0, _safe_api_int(c.get("metal", 0), 0)),
                kryptonite=max(0, _safe_api_int(c.get("kryptonite", 0), 0)),
                spice=max(0, _safe_api_int(c.get("spice", 0), 0)),
            )
            return None if r.is_zero() else r

        # 1. Exact (origin, dest) + matching ships summary
        if orig and dest and ships_norm:
            for idx, ent in eligible:
                if ent.get("origin_coords") == orig and ent.get("dest_coords") == dest:
                    es = " ".join(str(ent.get("ships") or "").strip().lower().split())
                    if es and (es == ships_norm or es in ships_norm or ships_norm in es):
                        res = _entry_to_res(ent)
                        if res is not None:
                            return idx, res
        # 2. Exact (origin, dest)
        if orig and dest:
            for idx, ent in eligible:
                if ent.get("origin_coords") == orig and ent.get("dest_coords") == dest:
                    res = _entry_to_res(ent)
                    if res is not None:
                        return idx, res
        # 3. Defensive swapped (dest, origin) in case server swaps endpoints on return
        if orig and dest:
            for idx, ent in eligible:
                if ent.get("origin_coords") == dest and ent.get("dest_coords") == orig:
                    res = _entry_to_res(ent)
                    if res is not None:
                        return idx, res
        # 4. Destination-only fallback if origin_coords was absent on QueueJob
        if dest and not orig:
            for idx, ent in eligible:
                if ent.get("dest_coords") == dest:
                    res = _entry_to_res(ent)
                    if res is not None:
                        return idx, res
        return None

    def _enrich_active_queues_locked(self, state: GameState) -> GameState:
        """Enrich ongoing fleet `QueueJob` entries with `expected_cargo`, `delivered_cargo`, and `delivered`."""
        queues = getattr(state, "active_queues", ()) or ()
        if not queues:
            return state
        has_fleet = any(is_in_flight_fleet_job(q) for q in queues)
        if not has_fleet:
            return state

        raid_by_coords: dict[str, Mapping[str, Any]] = {}
        for row in self._raid_targets or ():
            if isinstance(row, Mapping):
                nc = normalize_coords(row.get("coords") or row.get("target_coords") or "")
                if nc:
                    raid_by_coords[nc] = row
        for row in getattr(state, "raid_targets", ()) or ():
            if isinstance(row, Mapping):
                nc = normalize_coords(row.get("coords") or row.get("target_coords") or "")
                if nc:
                    raid_by_coords[nc] = row

        miner_lvl = _safe_api_int((getattr(state, "specs", None) or {}).get("miner", 0), 0)
        used_dispatch_idxs: set[int] = set()
        if self._recent_transport_dispatches:
            # Reserve dispatch records belonging to currently outbound transports that already
            # report live cargo so an earlier returning transport on the same route does not
            # mis-match the still-outbound fleet's record.
            for q0 in queues:
                if not is_in_flight_fleet_job(q0):
                    continue
                n0 = str(getattr(q0, "name", "") or "")
                d0 = str(getattr(q0, "detail", "") or "")
                n0_l = n0.lower()
                d0_l = d0.lower()
                is_ret0 = ("returning" in d0_l) or ("↩" in n0) or ("rück" in d0_l) or ("rueck" in d0_l)
                if "transport" not in n0_l or is_ret0:
                    continue
                c0 = getattr(q0, "cargo", None)
                if not (isinstance(c0, Resources) and not c0.is_zero()):
                    continue
                o0 = normalize_coords(getattr(q0, "origin_coords", "") or "")
                t0 = _extract_coords_from_text(n0)
                if not t0:
                    continue
                c0_norm = GameDatabase._normalize_cargo_dict(c0)
                for idx in range(len(self._recent_transport_dispatches) - 1, -1, -1):
                    if idx in used_dispatch_idxs:
                        continue
                    ent = self._recent_transport_dispatches[idx]
                    if not isinstance(ent, Mapping):
                        continue
                    if ent.get("dest_coords") != t0:
                        continue
                    if o0 and ent.get("origin_coords") and ent.get("origin_coords") != o0:
                        continue
                    if GameDatabase._normalize_cargo_dict(ent.get("cargo")) == c0_norm:
                        used_dispatch_idxs.add(idx)
                        break
        changed = False
        enriched_jobs: list[QueueJob] = []

        for q in queues:
            if not is_in_flight_fleet_job(q):
                enriched_jobs.append(q)
                continue
            name_s = str(getattr(q, "name", "") or "")
            det_s = str(getattr(q, "detail", "") or "")
            name_l = name_s.lower()
            det_l = det_s.lower()
            is_ret = ("returning" in det_l) or ("↩" in name_s) or ("rück" in det_l) or ("rueck" in det_l)
            is_attack = name_l.startswith("attack") or "attack →" in name_l or "attack ↩" in name_l or "attack ->" in name_l
            is_recycle = any(tok in name_l for tok in ("recycl", "rubble", "debris", "harvest", "abbau", "trümmer", "truemmer"))
            is_transport = name_l.startswith("transport") or "transport →" in name_l or "transport ↩" in name_l or "transport ->" in name_l

            orig_coords = normalize_coords(str(getattr(q, "origin_coords", "") or ""))
            dest_coords = _extract_coords_from_text(name_s)
            ships_part = det_s.split("·", 1)[1].strip() if "·" in det_s else det_s

            exp_cargo = getattr(q, "expected_cargo", None)
            del_cargo = getattr(q, "delivered_cargo", None)
            delivered = bool(getattr(q, "delivered", False))

            if is_attack and not is_ret and exp_cargo is None and dest_coords:
                trow = raid_by_coords.get(dest_coords)
                if isinstance(trow, Mapping):
                    res_src = trow.get("resources") if isinstance(trow.get("resources"), Mapping) else trow
                    raw_pig = max(0, _safe_api_int(res_src.get("pig_iron", 0), 0))
                    raw_met = max(0, _safe_api_int(res_src.get("metal", 0), 0))
                    raw_kry = max(0, _safe_api_int(res_src.get("kryptonite", 0), 0))
                    raw_spi = max(0, _safe_api_int(res_src.get("spice", 0), 0))
                    plunder = Resources(
                        pig_iron=raw_pig // 2,
                        metal=raw_met // 2,
                        kryptonite=raw_kry // 2,
                        spice=raw_spi // 2,
                    )
                    if not plunder.is_zero():
                        ships_map = _parse_ships_from_queue_detail(det_s)
                        cap = (
                            fleet_cargo_capacity(ships_map, miner_spec_level=miner_lvl)
                            if ships_map
                            else None
                        )
                        capped = _cap_resources_by_capacity(plunder, cap, mode="value")
                        if not capped.is_zero():
                            exp_cargo = capped

            elif is_recycle and not is_ret and exp_cargo is None and dest_coords:
                deb_map = self._galaxy_debris_amounts.get(dest_coords)
                dp = dm = dk = ds = 0
                if isinstance(deb_map, Mapping):
                    dp = max(0, _safe_api_int(deb_map.get("pig_iron", 0), 0))
                    dm = max(0, _safe_api_int(deb_map.get("metal", 0), 0))
                    dk = max(0, _safe_api_int(deb_map.get("kryptonite", 0), 0))
                    ds = max(0, _safe_api_int(deb_map.get("spice", 0), 0))
                if dp + dm + dk + ds <= 0:
                    trow = raid_by_coords.get(dest_coords)
                    if isinstance(trow, Mapping):
                        deb_sub = trow.get("debris") if isinstance(trow.get("debris"), Mapping) else None
                        if deb_sub is not None:
                            dp = max(0, _safe_api_int(deb_sub.get("pig_iron", 0), 0))
                            dm = max(0, _safe_api_int(deb_sub.get("metal", 0), 0))
                            dk = max(0, _safe_api_int(deb_sub.get("kryptonite", 0), 0))
                            ds = max(0, _safe_api_int(deb_sub.get("spice", 0), 0))
                        else:
                            dp = max(0, _safe_api_int(trow.get("debris_pig_iron", 0), 0))
                            dm = max(0, _safe_api_int(trow.get("debris_metal", 0), 0))
                            dk = max(0, _safe_api_int(trow.get("debris_kryptonite", 0), 0))
                            ds = max(0, _safe_api_int(trow.get("debris_spice", 0), 0))
                debris_res = Resources(pig_iron=dp, metal=dm, kryptonite=dk, spice=ds)
                if not debris_res.is_zero():
                    ships_map = _parse_ships_from_queue_detail(det_s)
                    if ships_map.get("s11", 0) > 0:
                        cap = fleet_cargo_capacity({"s11": ships_map["s11"]}, miner_spec_level=miner_lvl)
                    elif ships_map:
                        cap = fleet_cargo_capacity(ships_map, miner_spec_level=miner_lvl)
                    else:
                        cap = None
                    capped = _cap_resources_by_capacity(debris_res, cap, mode="proportional")
                    if not capped.is_zero():
                        exp_cargo = capped

            elif is_transport and not is_ret:
                if exp_cargo is None or (isinstance(exp_cargo, Resources) and exp_cargo.is_zero()):
                    cur_cargo = getattr(q, "cargo", None)
                    if isinstance(cur_cargo, Resources) and not cur_cargo.is_zero():
                        exp_cargo = cur_cargo
                    else:
                        matched = self._match_recent_transport_dispatch_locked(
                            orig_coords,
                            dest_coords,
                            ships_detail=ships_part,
                            used_indices=used_dispatch_idxs,
                        )
                        if matched is not None:
                            m_idx, m_res = matched
                            used_dispatch_idxs.add(m_idx)
                            exp_cargo = m_res

            elif is_transport and is_ret:
                delivered = True
                if del_cargo is None:
                    matched = self._match_recent_transport_dispatch_locked(
                        orig_coords,
                        dest_coords,
                        ships_detail=ships_part,
                        used_indices=used_dispatch_idxs,
                    )
                    if matched is not None:
                        m_idx, m_res = matched
                        used_dispatch_idxs.add(m_idx)
                        del_cargo = m_res

            if (
                exp_cargo != getattr(q, "expected_cargo", None)
                or del_cargo != getattr(q, "delivered_cargo", None)
                or delivered != bool(getattr(q, "delivered", False))
            ):
                changed = True
                enriched_jobs.append(
                    replace(
                        q,
                        expected_cargo=exp_cargo,
                        delivered_cargo=del_cargo,
                        delivered=delivered,
                    )
                )
            else:
                enriched_jobs.append(q)

        out_state = replace(state, active_queues=tuple(enriched_jobs)) if changed else state
        if self._observe_transport_utilization_locked(out_state) and self._db is not None:
            self._persist_runtime_state()
        return out_state

    def _prune_delivered_transport_events_locked(self, *, now_ts: Optional[float] = None) -> None:
        if not self._delivered_transport_events:
            return
        eff_now = _safe_api_float(now_ts, time.time()) if now_ts is not None else time.time()
        if eff_now <= 0.0:
            eff_now = time.time()
        cutoff = eff_now - self.DELIVERED_TRANSPORT_WINDOW_SECONDS
        kept = [
            e
            for e in self._delivered_transport_events
            if isinstance(e, Mapping)
            and _safe_api_float(e.get("ts"), 0.0) >= cutoff
            and _safe_api_int(e.get("cargo"), 0) > 0
        ]
        if len(kept) > self.DELIVERED_TRANSPORT_MAX_EVENTS:
            kept = kept[-self.DELIVERED_TRANSPORT_MAX_EVENTS :]
        self._delivered_transport_events = kept

    def _compute_delivered_transport_stats_locked(
        self,
        *,
        now_ts: Optional[float] = None,
    ) -> dict[str, Any]:
        eff_now = _safe_api_float(now_ts, time.time()) if now_ts is not None else time.time()
        if eff_now <= 0.0:
            eff_now = time.time()
        self._prune_delivered_transport_events_locked(now_ts=eff_now)
        events = self._delivered_transport_events
        if not events:
            return {
                "delivered_cargo_24h": 0,
                "delivered_cargo_per_hour": 0.0,
                "delivered_flights_24h": 0,
                "span_hours": 0.0,
                "avg_fleet_cargo": 0.0,
            }
        total_cargo = sum(max(0, _safe_api_int(e.get("cargo"), 0)) for e in events)
        oldest_ts = min(_safe_api_float(e.get("ts"), eff_now) for e in events)
        span_hours = max(0.0, (eff_now - oldest_ts) / 3600.0)
        obs_hours = max(1.0, min(24.0, span_hours))
        cph = round(float(total_cargo) / obs_hours, 2) if total_cargo > 0 else 0.0
        fleet_caps = [
            max(0, _safe_api_int(e.get("fleet_cargo"), 0))
            for e in events
            if _safe_api_int(e.get("fleet_cargo"), 0) > 0
        ]
        avg_fc = (float(sum(fleet_caps)) / float(len(fleet_caps))) if fleet_caps else 0.0
        return {
            "delivered_cargo_24h": int(total_cargo),
            "delivered_cargo_per_hour": cph,
            "delivered_flights_24h": len(events),
            "span_hours": round(span_hours, 4),
            "avg_fleet_cargo": avg_fc,
        }

    def _recompute_transport_utilization_locked(
        self,
        *,
        rt_per_hour: float = 2.5,
        fallback_fleet_cargo: int = 0,
        now_ts: Optional[float] = None,
    ) -> float:
        """Compute realized transport throughput utilization from persisted 24h deliveries.

        Keeps the Plan v2 0.5 baseline until at least `DELIVERED_TRANSPORT_MIN_SAMPLES`
        completed deliveries spanning >= `DELIVERED_TRANSPORT_MIN_SPAN_HOURS` have been recorded,
        then compares actual delivered cargo/h against theoretical `fleet_cargo * rt_per_hour`,
        clamped to `[0.2, 0.9]`.
        """
        stats = self._compute_delivered_transport_stats_locked(now_ts=now_ts)
        if (
            stats["delivered_flights_24h"] < self.DELIVERED_TRANSPORT_MIN_SAMPLES
            or stats["span_hours"] < self.DELIVERED_TRANSPORT_MIN_SPAN_HOURS
        ):
            self._transport_realized_utilization = self.TRANSPORT_UTIL_DEFAULT
            return self._transport_realized_utilization
        eff_fc = stats["avg_fleet_cargo"] if stats["avg_fleet_cargo"] > 0.0 else float(max(0, fallback_fleet_cargo))
        eff_rt = max(0.25, _safe_api_float(rt_per_hour, 2.5))
        theoretical_cph = max(1.0, eff_fc * eff_rt) if eff_fc > 0.0 else 0.0
        if theoretical_cph <= 0.0:
            self._transport_realized_utilization = self.TRANSPORT_UTIL_DEFAULT
            return self._transport_realized_utilization
        raw_util = float(stats["delivered_cargo_per_hour"]) / theoretical_cph
        clamped = round(min(self.TRANSPORT_UTIL_MAX, max(self.TRANSPORT_UTIL_MIN, raw_util)), 4)
        self._transport_realized_utilization = clamped
        return clamped

    def _observe_transport_utilization_locked(
        self,
        state: Optional[GameState],
        *,
        now_ts: Optional[float] = None,
    ) -> bool:
        """Record each completed returning transport delivery once (persisted 24h ring) and update throughput utilization."""
        if state is None:
            return False
        queues = getattr(state, "active_queues", ()) or ()
        if not queues:
            return False
        eff_now = _safe_api_float(now_ts, time.time()) if now_ts is not None else time.time()
        if eff_now <= 0.0:
            eff_now = time.time()
        miner_lvl = _safe_api_int((getattr(state, "specs", None) or {}).get("miner", 0), 0)
        seen_keys = {
            str(e.get("key") or "")
            for e in self._delivered_transport_events
            if isinstance(e, Mapping)
        }
        total_fleet_cargo: Optional[int] = None
        added = False
        for q in queues:
            if not is_in_flight_fleet_job(q):
                continue
            name_s = str(getattr(q, "name", "") or "")
            det_s = str(getattr(q, "detail", "") or "")
            name_l = name_s.lower()
            det_l = det_s.lower()
            if not (
                name_l.startswith("transport")
                or "transport →" in name_l
                or "transport ↩" in name_l
                or "transport ->" in name_l
            ):
                continue
            # Sample strictly on the returning leg (after delivery) so outbound + returning are never double-counted.
            is_ret = (
                bool(getattr(q, "delivered", False))
                or ("returning" in det_l)
                or ("↩" in name_s)
                or ("rück" in det_l)
                or ("rueck" in det_l)
            )
            if not is_ret:
                continue
            del_cargo = getattr(q, "delivered_cargo", None)
            if not isinstance(del_cargo, Resources) or del_cargo.is_zero():
                continue
            delivered_total = max(
                0,
                _safe_api_int(del_cargo.pig_iron, 0)
                + _safe_api_int(del_cargo.metal, 0)
                + _safe_api_int(del_cargo.kryptonite, 0)
                + _safe_api_int(del_cargo.spice, 0),
            )
            if delivered_total <= 0:
                continue
            orig_c = normalize_coords(str(getattr(q, "origin_coords", "") or ""))
            dest_c = _extract_coords_from_text(name_s)
            fin_ts = _safe_api_float(getattr(q, "finish_ts", 0.0), 0.0)
            fin_bucket = int(round(fin_ts / 180.0)) if fin_ts > 0.0 else int(eff_now // 600.0)
            ev_key = f"{orig_c}|{dest_c}|{delivered_total}|{fin_bucket}"
            if ev_key in seen_keys:
                continue
            if total_fleet_cargo is None:
                parked_s: dict[str, int] = {"s1": 0, "s2": 0, "s3": 0}
                for p in getattr(state, "planets", ()) or ():
                    if normalize_planet_type(p) == "raid":
                        continue
                    sm = p.ships if isinstance(getattr(p, "ships", None), Mapping) else {}
                    for sk in ("s1", "s2", "s3"):
                        parked_s[sk] += max(0, _safe_api_int(sm.get(sk, 0), 0))
                if_info = count_in_flight_ships_by_planet(state)
                emp_if = (
                    if_info.get("empire_in_flight")
                    if isinstance(if_info, Mapping) and isinstance(if_info.get("empire_in_flight"), Mapping)
                    else {}
                )
                for sk in ("s1", "s2", "s3"):
                    parked_s[sk] += max(0, _safe_api_int(emp_if.get(sk, 0), 0))
                total_fleet_cargo = int(fleet_cargo_capacity(parked_s, miner_spec_level=miner_lvl))
                if total_fleet_cargo <= 0:
                    ships_m = _parse_ships_from_queue_detail(det_s)
                    total_fleet_cargo = int(fleet_cargo_capacity(ships_m, miner_spec_level=miner_lvl)) if ships_m else 0
            seen_keys.add(ev_key)
            self._delivered_transport_events.append(
                {
                    "key": ev_key,
                    "ts": round(eff_now, 3),
                    "cargo": int(delivered_total),
                    "fleet_cargo": int(total_fleet_cargo or 0),
                }
            )
            added = True
        if added:
            self._recompute_transport_utilization_locked(
                fallback_fleet_cargo=int(total_fleet_cargo or 0),
                now_ts=eff_now,
            )
        return added

    def _prune_infra_transport_spend_locked(self, *, now_ts: Optional[float] = None) -> None:
        if not self._infra_transport_spend_ledger:
            return
        eff_now = _safe_api_float(now_ts, time.time()) if now_ts is not None else time.time()
        if eff_now <= 0.0:
            eff_now = time.time()
        cutoff = eff_now - self.INFRA_TRANSPORT_SPEND_WINDOW_SECONDS
        self._infra_transport_spend_ledger = [
            e
            for e in self._infra_transport_spend_ledger
            if isinstance(e, Mapping)
            and _safe_api_float(e.get("ts"), 0.0) >= cutoff
            and (_safe_api_int(e.get("metal"), 0) > 0 or _safe_api_int(e.get("kryptonite"), 0) > 0)
        ][-200:]

    def _get_infra_transport_spend_last_hour_locked(
        self,
        *,
        now_ts: Optional[float] = None,
    ) -> tuple[dict[str, float], float]:
        eff_now = _safe_api_float(now_ts, time.time()) if now_ts is not None else time.time()
        if eff_now <= 0.0:
            eff_now = time.time()
        self._prune_infra_transport_spend_locked(now_ts=eff_now)
        spent_m = float(sum(max(0, _safe_api_int(e.get("metal"), 0)) for e in self._infra_transport_spend_ledger))
        spent_k = float(sum(max(0, _safe_api_int(e.get("kryptonite"), 0)) for e in self._infra_transport_spend_ledger))
        carryover_h = 1.0
        if spent_m <= 0.0 and spent_k <= 0.0 and self._infra_transport_last_spend_ts > 0.0:
            idle_h = (eff_now - self._infra_transport_last_spend_ts) / 3600.0
            if idle_h > 1.0:
                carryover_h = round(min(self.INFRA_TRANSPORT_MAX_CARRYOVER_HOURS, idle_h), 4)
        return {"metal": spent_m, "kryptonite": spent_k}, carryover_h

    def _record_infra_transport_spend_locked(
        self,
        action: PlannedAction,
        *,
        now_ts: Optional[float] = None,
        quantity: Optional[int] = None,
    ) -> None:
        """Debit a transport build from the 25%/h infra ledger.

        `action.cost` is taken as the amount spent. Pass `quantity` when `action.cost` is the cost
        of one unit (as on actions built by `_find_ship_action`), so the whole order is debited.
        """
        eff_now = _safe_api_float(now_ts, time.time()) if now_ts is not None else time.time()
        if eff_now <= 0.0:
            eff_now = time.time()
        cost_obj = getattr(action, "cost", None)
        mult = max(1, _safe_api_int(quantity, 1)) if quantity is not None else 1
        met = (max(0, _safe_api_int(getattr(cost_obj, "metal", 0), 0)) if cost_obj is not None else 0) * mult
        kry = (max(0, _safe_api_int(getattr(cost_obj, "kryptonite", 0), 0)) if cost_obj is not None else 0) * mult
        sid = game_data.safe_optional_int(getattr(action, "target_id", None), None)
        units = max(1, _safe_api_int(getattr(action, "target_level", 1), 1))
        if met <= 0 and kry <= 0:
            if sid is not None:
                slug = SHIP_SLUG_BY_KEY.get(f"s{sid}")
                info = game_data.resolve_unit("ships", slug) if slug else None
                base_c = (info or {}).get("base_cost") if isinstance(info, Mapping) else {}
                if isinstance(base_c, Mapping):
                    met = max(0, _safe_api_int(base_c.get("metal", 0), 0)) * units
                    kry = max(0, _safe_api_int(base_c.get("kryptonite", 0), 0)) * units
        if met <= 0 and kry <= 0:
            return
        entry: dict[str, Any] = {"ts": round(eff_now, 3), "metal": int(met), "kryptonite": int(kry)}
        if sid in (1, 2, 3):
            # Cargo of the hulls ordered: they may sit in the Weapons factory queue, uncounted, for a while.
            try:
                ordered_cargo = int(fleet_cargo_capacity({f"s{sid}": units}))
            except Exception:  # noqa: BLE001 - never let a catalog gap block the ledger
                ordered_cargo = 0
            if ordered_cargo > 0:
                entry["cargo"] = ordered_cargo
        self._infra_transport_spend_ledger.append(entry)
        self._infra_transport_last_spend_ts = eff_now
        self._prune_infra_transport_spend_locked(now_ts=eff_now)

    def _get_infra_cargo_ordered_last_hour_locked(self, *, now_ts: Optional[float] = None) -> int:
        """Cargo capacity of the transport hulls ordered in the last hour (may still be in production)."""
        eff_now = _safe_api_float(now_ts, time.time()) if now_ts is not None else time.time()
        if eff_now <= 0.0:
            eff_now = time.time()
        self._prune_infra_transport_spend_locked(now_ts=eff_now)
        return int(sum(max(0, _safe_api_int(e.get("cargo"), 0)) for e in self._infra_transport_spend_ledger))

    def _prune_spare_ship_spend_locked(self, *, now_ts: Optional[float] = None) -> None:
        if not self._spare_ship_spend_ledger:
            return
        eff_now = _safe_api_float(now_ts, time.time()) if now_ts is not None else time.time()
        if eff_now <= 0.0:
            eff_now = time.time()
        cutoff = eff_now - self.SPARE_SHIP_BUDGET_WINDOW_HOURS * 3600.0
        self._spare_ship_spend_ledger = [
            e
            for e in self._spare_ship_spend_ledger
            if isinstance(e, Mapping) and _safe_api_float(e.get("ts"), 0.0) >= cutoff
        ][-400:]

    def _get_spare_ship_spend_in_window_locked(self, *, now_ts: Optional[float] = None) -> dict[str, float]:
        """What the spare % ship budget paid for over `SPARE_SHIP_BUDGET_WINDOW_HOURS`, per resource."""
        eff_now = _safe_api_float(now_ts, time.time()) if now_ts is not None else time.time()
        if eff_now <= 0.0:
            eff_now = time.time()
        self._prune_spare_ship_spend_locked(now_ts=eff_now)
        return {
            rk: float(sum(max(0, _safe_api_int(e.get(rk), 0)) for e in self._spare_ship_spend_ledger))
            for rk in ("pig_iron", "metal", "kryptonite", "spice")
        }

    def _record_spare_ship_spend_locked(
        self,
        action: PlannedAction,
        *,
        now_ts: Optional[float] = None,
        quantity: Optional[int] = None,
    ) -> None:
        """Debit a non-transport ship order (`action.cost` per unit times `quantity`) from the spare % ledger."""
        eff_now = _safe_api_float(now_ts, time.time()) if now_ts is not None else time.time()
        if eff_now <= 0.0:
            eff_now = time.time()
        cost_obj = getattr(action, "cost", None)
        mult = max(1, _safe_api_int(quantity, 1)) if quantity is not None else 1
        row = {
            rk: (max(0, _safe_api_int(getattr(cost_obj, rk, 0), 0)) if cost_obj is not None else 0) * mult
            for rk in ("pig_iron", "metal", "kryptonite", "spice")
        }
        if not any(v > 0 for v in row.values()):
            sid = game_data.safe_optional_int(getattr(action, "target_id", None), None)
            units = max(1, _safe_api_int(getattr(action, "target_level", 1), 1))
            slug = SHIP_SLUG_BY_KEY.get(f"s{sid}") if sid is not None else None
            info = game_data.resolve_unit("ships", slug) if slug else None
            base_c = (info or {}).get("base_cost") if isinstance(info, Mapping) else {}
            if isinstance(base_c, Mapping):
                row = {rk: max(0, _safe_api_int(base_c.get(rk, 0), 0)) * units for rk in row}
        if not any(v > 0 for v in row.values()):
            return
        self._spare_ship_spend_ledger.append({"ts": round(eff_now, 3), **{rk: int(v) for rk, v in row.items()}})
        self._prune_spare_ship_spend_locked(now_ts=eff_now)

    def _get_realized_loot_hourly_locked(self) -> Optional[dict[str, Any]]:
        """Loot plus recycling actually received per hour over the trailing window.

        Returns `{"hourly_loot": {resource: per hour}, "missions_per_hour": float, "window_hours": float}`,
        or None when no loot ledger is available (the modelled raid income then stays in use).
        """
        if self._db is None or not callable(getattr(self._db, "get_loot_recycle_hourly", None)):
            return None
        tel = self._get_loot_recycle_telemetry_locked(self.REALIZED_LOOT_WINDOW_HOURS)
        summ = tel.get("summary") if isinstance(tel, Mapping) else None
        if not isinstance(summ, Mapping) or "stolen_metal" not in summ or "recycled_metal" not in summ:
            return None
        win_h = float(max(1, _safe_api_int(summ.get("window_hours"), self.REALIZED_LOOT_WINDOW_HOURS)))
        hourly: dict[str, float] = {}
        for rk in ("pig_iron", "metal", "kryptonite", "spice"):
            got = max(0.0, _safe_api_float(summ.get(f"stolen_{rk}"), 0.0)) + max(
                0.0, _safe_api_float(summ.get(f"recycled_{rk}"), 0.0)
            )
            hourly[rk] = round(got / win_h, 2)
        missions = max(0, _safe_api_int(summ.get("attack_count"), 0)) + max(
            0, _safe_api_int(summ.get("recycle_count"), 0)
        )
        return {
            "hourly_loot": hourly,
            "missions_per_hour": round(float(missions) / win_h, 4),
            "window_hours": win_h,
        }

    def _overlay_realized_loot_locked(self, atk_tel: Any) -> Any:
        """Return `atk_tel` with `hourly_loot` replaced by realized loot (unchanged without a loot ledger)."""
        realized = self._get_realized_loot_hourly_locked()
        if realized is None:
            return atk_tel
        out: dict[str, Any] = dict(atk_tel) if isinstance(atk_tel, Mapping) else {}
        if str(out.get("income_basis") or "") != self.REALIZED_INCOME_BASIS and isinstance(
            out.get("hourly_loot"), Mapping
        ):
            out["modelled_hourly_loot"] = dict(out["hourly_loot"])
        hourly = dict(realized["hourly_loot"])
        hourly["spice_eq"] = round(float(game_data.spice_equivalent(realized["hourly_loot"])), 2)
        out["hourly_loot"] = hourly
        out["income_basis"] = self.REALIZED_INCOME_BASIS
        return out

    @classmethod
    def _telemetry_income_basis(cls, tel: Any) -> str:
        if not isinstance(tel, Mapping):
            return ""
        return str(tel.get("income_basis") or "").strip().lower()

    def _prune_adaptive_idle_buckets_locked(self, *, now_ts: Optional[float] = None) -> None:
        if not self._adaptive_idle_buckets:
            return
        eff_now = _safe_api_float(now_ts, time.time()) if now_ts is not None else time.time()
        if eff_now <= 0.0:
            eff_now = time.time()
        cur_bucket = int(eff_now // self.ADAPTIVE_IDLE_BUCKET_SECONDS)
        min_bucket = cur_bucket - (self.ADAPTIVE_IDLE_MAX_BUCKETS - 1)
        kept = {
            bk: ent
            for bk, ent in self._adaptive_idle_buckets.items()
            if isinstance(bk, int) and min_bucket <= bk <= cur_bucket + 1
        }
        if len(kept) > self.ADAPTIVE_IDLE_MAX_BUCKETS:
            top_keys = sorted(kept.keys())[-self.ADAPTIVE_IDLE_MAX_BUCKETS :]
            kept = {bk: kept[bk] for bk in top_keys}
        self._adaptive_idle_buckets = kept

    def _record_adaptive_idle_bucket_locked(
        self,
        state: Optional[GameState],
        active_plan_result: Optional[Mapping[str, Any]],
        *,
        ai_actions: Sequence[PlannedAction] = (),
        cfg: Optional[StrategyConfig] = None,
        field_conflicts: Sequence[Mapping[str, Any]] = (),
        inbound_cargo_by_planet: Optional[Mapping[int, Resources]] = None,
        ai_mod: Any = None,
        now_ts: Optional[float] = None,
    ) -> bool:
        """Record at most one 10-minute colony-idle diagnostic tally per bucket (last 24h)."""
        if state is None or not getattr(state, "planets", None):
            return False
        if not isinstance(active_plan_result, Mapping):
            return False
        eff_now = _safe_api_float(now_ts, time.time()) if now_ts is not None else time.time()
        if eff_now <= 0.0:
            eff_now = time.time()
        bucket_idx = int(eff_now // self.ADAPTIVE_IDLE_BUCKET_SECONDS)
        if bucket_idx in self._adaptive_idle_buckets:
            return False
        try:
            mod = ai_mod if ai_mod is not None else self._load_ai_planner_mod()
        except ApiRequestError:
            return False
        diag_fn = getattr(mod, "diagnose_ai_colony_idle_states", None)
        if not callable(diag_fn):
            return False
        try:
            diag = diag_fn(
                state,
                active_plan_result,
                ai_actions=ai_actions,
                config=cfg or self.strategy_config,
                field_conflicts=field_conflicts,
                inbound_cargo_by_planet=inbound_cargo_by_planet,
            )
        except Exception:
            return False
        if not isinstance(diag, Mapping):
            return False
        total_col = max(0, _safe_api_int(diag.get("total_colonies", 0), 0))
        if total_col <= 0:
            return False
        raw_cnts = diag.get("counts")
        clean_cnts: dict[str, int] = {k: 0 for k in self._ADAPTIVE_IDLE_STATE_KEYS}
        if isinstance(raw_cnts, Mapping):
            for ck, cv in raw_cnts.items():
                ck_s = str(ck or "").strip()
                if ck_s:
                    clean_cnts[ck_s] = max(0, _safe_api_int(cv, 0))
        self._adaptive_idle_buckets[bucket_idx] = {
            "bucket": bucket_idx,
            "ts": round(eff_now, 3),
            "total_colonies": total_col,
            "active_builders": max(0, _safe_api_int(diag.get("active_builders", 0), 0)),
            "idle_with_affordable": max(0, _safe_api_int(diag.get("idle_with_affordable_count", 0), 0)),
            "ready_not_started": max(0, _safe_api_int(diag.get("ready_not_started_count", 0), 0)),
            "counts": clean_cnts,
        }
        self._prune_adaptive_idle_buckets_locked(now_ts=eff_now)
        return True

    def _summarize_adaptive_idle_window_24h_locked(
        self,
        *,
        now_ts: Optional[float] = None,
    ) -> dict[str, Any]:
        self._prune_adaptive_idle_buckets_locked(now_ts=now_ts)
        buckets = list(self._adaptive_idle_buckets.values())
        colony_samples = 0
        idle_aff_sum = 0
        ready_sum = 0
        state_totals: dict[str, int] = {k: 0 for k in self._ADAPTIVE_IDLE_STATE_KEYS}
        for ent in buckets:
            if not isinstance(ent, Mapping):
                continue
            colony_samples += max(0, _safe_api_int(ent.get("total_colonies", 0), 0))
            idle_aff_sum += max(0, _safe_api_int(ent.get("idle_with_affordable", 0), 0))
            ready_sum += max(0, _safe_api_int(ent.get("ready_not_started", 0), 0))
            raw_c = ent.get("counts")
            if isinstance(raw_c, Mapping):
                for ck, cv in raw_c.items():
                    ck_s = str(ck or "").strip()
                    if ck_s:
                        state_totals[ck_s] = state_totals.get(ck_s, 0) + max(0, _safe_api_int(cv, 0))
        if colony_samples > 0:
            idle_aff_pct = round(100.0 * float(idle_aff_sum) / float(colony_samples), 2)
            ready_pct = round(100.0 * float(ready_sum) / float(colony_samples), 2)
            shares = {
                st: round(100.0 * float(cnt) / float(colony_samples), 2)
                for st, cnt in state_totals.items()
            }
        else:
            idle_aff_pct = 0.0
            ready_pct = 0.0
            shares = {st: 0.0 for st in state_totals}
        return {
            "bucket_count": len(buckets),
            "colony_samples": colony_samples,
            "idle_with_affordable_share_pct": idle_aff_pct,
            "ready_not_started_share_pct": ready_pct,
            "state_shares_pct": shares,
        }

    def _build_transport_fleet_summary_locked(
        self,
        state: Optional[GameState],
        transport_demand: Optional[Mapping[str, Any]],
    ) -> Optional[dict[str, Any]]:
        if state is None:
            return None
        miner_lvl = _safe_api_int((getattr(state, "specs", None) or {}).get("miner", 0), 0)
        parked_ships: dict[str, int] = {"s1": 0, "s2": 0, "s3": 0}
        for p in getattr(state, "planets", ()) or ():
            if normalize_planet_type(p) == "raid":
                continue
            ships_m = p.ships if isinstance(getattr(p, "ships", None), Mapping) else {}
            for sk in ("s1", "s2", "s3"):
                parked_ships[sk] += max(0, _safe_api_int(ships_m.get(sk, 0), 0))
        owned_parked_cargo = int(fleet_cargo_capacity(parked_ships, miner_spec_level=miner_lvl))
        in_flight_info = count_in_flight_ships_by_planet(state)
        emp_if = (
            in_flight_info.get("empire_in_flight")
            if isinstance(in_flight_info, Mapping) and isinstance(in_flight_info.get("empire_in_flight"), Mapping)
            else {}
        )
        if_ships = {sk: max(0, _safe_api_int(emp_if.get(sk, 0), 0)) for sk in ("s1", "s2", "s3")}
        in_flight_cargo = int(fleet_cargo_capacity(if_ships, miner_spec_level=miner_lvl))
        total_fleet_cargo = owned_parked_cargo + in_flight_cargo
        planned_cph = 0.0
        rt_per_h = 2.5
        if isinstance(transport_demand, Mapping):
            planned_cph = round(max(0.0, _safe_api_float(transport_demand.get("cargo_per_hour_24h", 0.0), 0.0)), 2)
            rt_per_h = max(0.25, _safe_api_float(transport_demand.get("round_trips_per_hour", 2.5), 2.5))
        util = self._recompute_transport_utilization_locked(
            rt_per_hour=rt_per_h,
            fallback_fleet_cargo=total_fleet_cargo,
        )
        deliv_stats = self._compute_delivered_transport_stats_locked()
        # Sizing never uses a utilization below the assumed baseline: an idle fleet is not evidence
        # that more hulls are needed (matches `strategy.INFRA_TRANSPORT_MIN_UTILIZATION`).
        sizing_util = max(self.TRANSPORT_UTIL_DEFAULT, util)
        target_cargo = int(math.ceil(planned_cph / rt_per_h / sizing_util)) if planned_cph > 0.0 else 0
        delivered_vs_planned = (
            round(100.0 * float(deliv_stats["delivered_cargo_per_hour"]) / planned_cph, 2)
            if planned_cph > 0.0 and deliv_stats["delivered_flights_24h"] > 0
            else None
        )
        return {
            "owned_parked_cargo": owned_parked_cargo,
            "in_flight_cargo": in_flight_cargo,
            "total_fleet_cargo": total_fleet_cargo,
            "target_fleet_cargo": target_cargo,
            "planned_cargo_per_hour_24h": planned_cph,
            "realized_utilization": util,
            "sizing_utilization": sizing_util,
            "ordered_cargo_last_hour": self._get_infra_cargo_ordered_last_hour_locked(),
            "delivered_cargo_24h": deliv_stats["delivered_cargo_24h"],
            "delivered_cargo_per_hour": deliv_stats["delivered_cargo_per_hour"],
            "delivered_cargo_per_hour_24h": deliv_stats["delivered_cargo_per_hour"],
            "delivered_flights_24h": deliv_stats["delivered_flights_24h"],
            "delivered_vs_planned_pct": delivered_vs_planned,
        }

    @staticmethod
    def _extract_record_timestamp(
        rec: Optional[Mapping[str, Any]],
        result: Optional[Mapping[str, Any]] = None,
    ) -> float:
        for src in (rec, result):
            if not isinstance(src, Mapping):
                continue
            for key in ("updated_ts", "created_ts", "generated_ts"):
                val = _safe_api_float(src.get(key), 0.0)
                if math.isfinite(val) and val > 0.0:
                    return val
            summary_m = src.get("summary")
            if isinstance(summary_m, Mapping):
                ls_ts = _safe_api_float(summary_m.get("live_snapshot_ts"), 0.0)
                if math.isfinite(ls_ts) and ls_ts > 0.0:
                    return ls_ts
            raw_iso = src.get("generated_at")
            if isinstance(raw_iso, str) and raw_iso.strip():
                try:
                    dt = datetime.fromisoformat(raw_iso.strip().replace("Z", "+00:00"))
                    ts_v = dt.timestamp()
                    if math.isfinite(ts_v) and ts_v > 0.0:
                        return float(ts_v)
                except ValueError:
                    pass
        return 0.0

    @staticmethod
    def _extract_telemetry_median_spice_eq(tel: Optional[Mapping[str, Any]]) -> float:
        if not isinstance(tel, Mapping):
            return 0.0
        ml = tel.get("median_loot")
        if isinstance(ml, Mapping) and ml.get("spice_eq") is not None:
            return max(0.0, _safe_api_float(ml.get("spice_eq"), 0.0))
        if tel.get("spice_eq") is not None:
            return max(0.0, _safe_api_float(tel.get("spice_eq"), 0.0))
        return 0.0

    @staticmethod
    def _extract_plan_colony_count(active_result: Mapping[str, Any]) -> int:
        summary_m = active_result.get("summary")
        if isinstance(summary_m, Mapping):
            for key in ("colonies_founded", "planet_count", "colonies_count", "colonies_target"):
                val = _safe_api_int(summary_m.get(key), 0)
                if val > 0:
                    return val
        raw_planets = active_result.get("planets")
        if isinstance(raw_planets, Sequence) and not isinstance(raw_planets, (str, bytes)):
            cnt = sum(
                1
                for p in raw_planets
                if isinstance(p, Mapping) and not bool(p.get("is_placeholder", False))
            )
            if cnt > 0:
                return cnt
        return 0

    def _evaluate_ai_drift_replan_locked(
        self,
        state: Optional[GameState],
        *,
        cfg: Optional[StrategyConfig] = None,
        now_ts: Optional[float] = None,
        gate: Optional[Mapping[str, Any]] = None,
    ) -> dict[str, Any]:
        eff_cfg = cfg or self.strategy_config
        eff_now = _safe_api_float(now_ts, time.time()) if now_ts is not None else time.time()
        if eff_now <= 0.0:
            eff_now = time.time()
        with self._planner_jobs_lock:
            co_running = self._config_opt_job.get("status") == "running"
            ai_running = self._ai_planner_job.get("status") == "running"
        any_running = bool(co_running or ai_running)
        last_auto_ok = (
            self._last_auto_ai_replan_ts <= 0.0
            or (eff_now - self._last_auto_ai_replan_ts) >= self.AI_DRIFT_REPLAN_COOLDOWN_SECONDS
        )
        adaptive_on = bool(getattr(eff_cfg, "ai_adaptive_execution", False))
        mode_is_ai = normalize_planner_mode(getattr(eff_cfg, "planner_mode", "priority")) == "ai_planner"

        # Fast-exit before loading the full active AI plan from SQLite when caller did not supply `gate`.
        if gate is None and (
            not adaptive_on
            or not mode_is_ai
            or self._planner_closed
            or state is None
            or self._bot_control.get("paused")
            or self._bot_control.get("captcha_blocked")
            or any_running
            or not last_auto_ok
        ):
            return {
                "eligible": False,
                "cooldown_ok": bool(last_auto_ok),
                "drift_ready": bool(last_auto_ok and not any_running),
                "reason": None,
                "warm_genes": None,
                "active_plan_ts": 0.0,
                "now_ts": eff_now,
            }

        eff_gate = gate if isinstance(gate, Mapping) else self._evaluate_ai_planner_gate_locked(state, cfg=eff_cfg)
        active_rec = eff_gate.get("active_rec")
        active_result = eff_gate.get("active_result")
        active_plan_ts = self._extract_record_timestamp(active_rec, active_result)
        plan_cd_ok = (
            active_plan_ts <= 0.0
            or (eff_now - active_plan_ts) >= self.AI_DRIFT_REPLAN_COOLDOWN_SECONDS
        )
        cooldown_ok = bool(last_auto_ok and plan_cd_ok)
        drift_ready = bool(cooldown_ok and not any_running)

        gates_ok = bool(
            adaptive_on
            and eff_gate.get("effective_planner_mode") == "ai_planner"
            and not self._planner_closed
            and state is not None
            and eff_gate.get("live_ai_unlocked")
            and isinstance(active_rec, Mapping)
            and isinstance(active_result, Mapping)
            and not self._bot_control.get("paused")
            and not self._bot_control.get("captcha_blocked")
            and not any_running
            and cooldown_ok
        )
        if not gates_ok:
            return {
                "eligible": False,
                "cooldown_ok": cooldown_ok,
                "drift_ready": drift_ready,
                "reason": None,
                "warm_genes": None,
                "active_plan_ts": active_plan_ts,
                "now_ts": eff_now,
            }

        reason: Optional[str] = None
        # (a) Active Config Optimizer run_id / generated_at changed
        active_co = self._get_active_config_optimizer_locked()
        if isinstance(active_co, Mapping):
            co_run_id = _safe_api_int(active_co.get("run_id"), 0)
            ai_co_run_id = _safe_api_int(active_rec.get("config_run_id"), 0)
            co_res = active_co.get("result") if isinstance(active_co.get("result"), Mapping) else {}
            co_gen_at = str(co_res.get("generated_at") or "").strip()
            ai_co_gen_at = str(
                active_result.get("config_generated_at")
                or active_result.get("target_config_generated_at")
                or ""
            ).strip()
            if (co_run_id > 0 and ai_co_run_id > 0 and co_run_id != ai_co_run_id) or (
                co_gen_at and ai_co_gen_at and co_gen_at != ai_co_gen_at
            ):
                reason = "config_optimizer_changed"

        # (b) Normal colony count changed
        if reason is None and state is not None:
            live_colonies = count_allocated_normal_planets(state)
            plan_colonies = self._extract_plan_colony_count(active_result)
            if plan_colonies > 0 and live_colonies != plan_colonies:
                reason = "colony_count_changed"

        # (c) Live attack telemetry median_loot spice_eq shifted by >= 25%. A plan computed on the
        #     realized basis does not depend on the median loot, so it is not re-tuned for it; the
        #     4-hour age rule picks up changes in realized loot.
        live_tel: Optional[Mapping[str, Any]] = None
        if reason is None and state is not None:
            plan_tel = active_result.get("attack_telemetry")
            plan_sp = self._extract_telemetry_median_spice_eq(plan_tel)
            live_tel = self._compute_live_attack_telemetry_locked(state)
            live_sp = self._extract_telemetry_median_spice_eq(live_tel)
            plan_on_realized = self._telemetry_income_basis(plan_tel) == self.REALIZED_INCOME_BASIS
            if not plan_on_realized and (
                (plan_sp <= 0.0 and live_sp > 0.0)
                or (
                    plan_sp > 0.0
                    and live_sp > 0.0
                    and (abs(live_sp - plan_sp) / plan_sp) >= self.AI_DRIFT_LOOT_SHIFT_RATIO
                )
            ):
                reason = "loot_shift_25pct"

        # (c2) The active plan assumed modelled raid income while live telemetry now carries the
        #      loot and recycling actually received: re-tune once on the realized figure. A plan
        #      pinned to `attacks_per_day` reports its own basis and is left alone.
        if reason is None and live_tel is not None:
            live_basis = self._telemetry_income_basis(live_tel)
            plan_basis = self._telemetry_income_basis(active_result.get("attack_telemetry"))
            if live_basis == self.REALIZED_INCOME_BASIS and plan_basis in ("", "modelled"):
                reason = "income_basis_changed"

        # (d) Active AI plan age >= 4 hours (14400s)
        if (
            reason is None
            and active_plan_ts > 0.0
            and (eff_now - active_plan_ts) >= self.AI_DRIFT_REPLAN_MAX_AGE_SECONDS
        ):
            reason = "plan_age_4h"

        warm_genes: Optional[dict[str, Any]] = None
        if reason is not None:
            search_m = active_result.get("search")
            if isinstance(search_m, Mapping):
                raw_warm = search_m.get("tuned_genes") or search_m.get("best_parameters")
                if isinstance(raw_warm, Mapping) and raw_warm:
                    warm_genes = dict(raw_warm)

        return {
            "eligible": True,
            "cooldown_ok": cooldown_ok,
            "drift_ready": drift_ready,
            "reason": reason,
            "warm_genes": warm_genes,
            "active_plan_ts": active_plan_ts,
            "now_ts": eff_now,
        }

    def _maybe_trigger_ai_drift_replan(self, *, now_ts: Optional[float] = None) -> Optional[dict[str, Any]]:
        """Check Step 6 drift triggers non-blockingly under `_lock`, then launch background AI replan OUTSIDE `_lock`."""
        cfg_ref = self.strategy_config
        if (
            not bool(getattr(cfg_ref, "ai_adaptive_execution", False))
            or self._planner_closed
            or normalize_planner_mode(getattr(cfg_ref, "planner_mode", "priority")) != "ai_planner"
            or self._bot_control.get("paused")
            or self._bot_control.get("captcha_blocked")
        ):
            return None
        eff_now = _safe_api_float(now_ts, time.time()) if now_ts is not None else time.time()
        if eff_now <= 0.0:
            eff_now = time.time()
        if (
            self._last_auto_ai_replan_ts > 0.0
            and (eff_now - self._last_auto_ai_replan_ts) < self.AI_DRIFT_REPLAN_COOLDOWN_SECONDS
        ):
            return None
        now_mono = time.monotonic()
        if (
            now_ts is None
            and self._last_drift_check_mono > 0.0
            and (now_mono - self._last_drift_check_mono) < self.AI_DRIFT_CHECK_MIN_INTERVAL_SECONDS
        ):
            return None
        with self._planner_jobs_lock:
            if (
                self._config_opt_job.get("status") == "running"
                or self._ai_planner_job.get("status") == "running"
            ):
                return None

        if not self._lock.acquire(blocking=False):
            return None
        reason: Optional[str] = None
        warm_genes: Optional[dict[str, Any]] = None
        try:
            if now_ts is None:
                now_mono_in = time.monotonic()
                if (
                    self._last_drift_check_mono > 0.0
                    and (now_mono_in - self._last_drift_check_mono) < self.AI_DRIFT_CHECK_MIN_INTERVAL_SECONDS
                ):
                    return None
                self._last_drift_check_mono = now_mono_in
            ev = self._evaluate_ai_drift_replan_locked(
                self._last_state,
                cfg=self.strategy_config,
                now_ts=now_ts,
            )
            if not ev.get("eligible") or not ev.get("reason"):
                return None
            reason = str(ev["reason"])
            warm_genes = ev.get("warm_genes")
            self._last_auto_ai_replan_ts = float(ev["now_ts"])
            self._last_auto_ai_replan_reason = reason
            self._persist_runtime_state()
        finally:
            self._lock.release()
        try:
            return self._start_ai_planner_job(
                params={"warm_start_genes": warm_genes} if warm_genes else None,
                sync=False,
                refresh=False,
                trigger_source=f"auto_drift:{reason}",
            )
        except Exception:
            return None

    def _build_building_resource_requests_locked(
        self,
        display_state: Optional[GameState],
        actions: Sequence[PlannedAction],
        early_needs_map: Optional[Mapping[int, Mapping[str, Any]]] = None,
    ) -> list[dict[str, Any]]:
        """Build structured resource requests for the Dashboard Building Queue (`in_flight`, `planned`, `requested`)."""
        if display_state is None or not display_state.planets:
            return []

        planet_by_id: dict[int, PlanetState] = {int(p.planet_id): p for p in display_state.planets}
        planet_by_coords: dict[str, PlanetState] = {}
        for p in display_state.planets:
            nc = normalize_coords(p.coords)
            if nc:
                planet_by_coords[nc] = p

        # First building action hint per planet (prefer stalled/waiting building with missing resources)
        next_bldg_by_pid: dict[int, tuple[str, Optional[int], Optional[int], PlannedAction]] = {}
        for act in actions:
            if act.kind not in ("BUILD", "WAIT_RESOURCES"):
                continue
            pid = _safe_api_int(act.planet_id, 0)
            if pid <= 0:
                continue
            is_stalled = (not act.executable_now) and (not act.missing.is_zero())
            prev = next_bldg_by_pid.get(pid)
            if prev is None or (is_stalled and prev[3].executable_now):
                tname = str(act.target_name or "")
                tlvl = act.target_level
                bid = act.target_id if act.target_id is not None else BUILDING_ID_BY_NAME.get(tname.lower())
                next_bldg_by_pid[pid] = (tname, tlvl, bid, act)

        early_map: Mapping[int, Mapping[str, Any]] = early_needs_map if isinstance(early_needs_map, Mapping) else {}
        items: list[dict[str, Any]] = []
        in_flight_by_pid: dict[int, list[int]] = {}
        planned_by_pid: dict[int, list[int]] = {}

        def _make_item(
            *,
            item_id: str,
            dest: PlanetState,
            target_building: str,
            target_level: Optional[int],
            building_id: Optional[int],
            label: str,
            status: str,
            pig: int,
            met: int,
            kry: int,
            spi: int,
            src: Optional[PlanetState] = None,
            source_coords: str = "",
            eta_seconds: Optional[float] = None,
            finish_ts: Optional[float] = None,
            reason: str = "",
        ) -> dict[str, Any]:
            src_pid = int(src.planet_id) if src is not None else None
            src_coords = normalize_coords(source_coords) or (str(src.coords) if src is not None else "")
            src_name = str(src.name) if src is not None else ""
            eta_clean = round(float(eta_seconds), 1) if (eta_seconds is not None and math.isfinite(float(eta_seconds))) else None
            fin_clean = round(float(finish_ts), 3) if (finish_ts is not None and math.isfinite(float(finish_ts)) and float(finish_ts) > 0.0) else None
            return {
                "id": item_id,
                "planet_id": int(dest.planet_id),
                "planet_coords": str(dest.coords),
                "planet_name": str(dest.name or dest.coords),
                "target_building": target_building,
                "target_level": int(target_level) if target_level is not None else None,
                "building_id": int(building_id) if building_id is not None else None,
                "label": label,
                "status": status,
                "resources": {
                    "pig_iron": pig,
                    "metal": met,
                    "kryptonite": kry,
                    "spice": spi,
                },
                "pig_iron": pig,
                "metal": met,
                "kryptonite": kry,
                "spice": spi,
                "source_planet_id": src_pid,
                "source_coords": src_coords,
                "source_name": src_name,
                "eta_seconds": eta_clean,
                "finish_ts": fin_clean,
                "reason": str(reason or ""),
            }

        # 1. Active in-flight outbound TRANSPORT flights heading to an owned colony
        for idx, q in enumerate(display_state.active_queues or ()):
            if not is_in_flight_fleet_job(q):
                continue
            name_s = str(getattr(q, "name", "") or "")
            det_s = str(getattr(q, "detail", "") or "")
            name_l = name_s.lower()
            det_l = det_s.lower()
            is_ret = ("returning" in det_l) or ("↩" in name_s) or ("rück" in det_l) or ("rueck" in det_l)
            if is_ret:
                continue
            if not (name_l.startswith("transport") or "transport →" in name_l or "transport ->" in name_l):
                continue
            dest_coords = _extract_coords_from_text(name_s)
            dest = planet_by_coords.get(dest_coords) if dest_coords else None
            if dest is None:
                continue
            res = getattr(q, "expected_cargo", None) or getattr(q, "cargo", None)
            if not isinstance(res, Resources) or res.is_zero():
                continue
            orig_coords = normalize_coords(str(getattr(q, "origin_coords", "") or ""))
            src = planet_by_coords.get(orig_coords) if orig_coords else None
            pid = int(dest.planet_id)
            buf = in_flight_by_pid.setdefault(pid, [0, 0, 0, 0])
            buf[0] += res.pig_iron
            buf[1] += res.metal
            buf[2] += res.kryptonite
            buf[3] += res.spice

            tb, tl, bid = "", None, None
            lbl = "Incoming transport"
            early_ent = early_map.get(pid)
            if isinstance(early_ent, Mapping) and early_ent.get("label"):
                tb, tl, bid = _parse_building_target_hint(str(early_ent.get("label")))
                lbl = f"{tb} L{tl}" if tb and tl else (tb or str(early_ent.get("label"))[:64])
            elif pid in next_bldg_by_pid:
                tb, tl, bid, _ = next_bldg_by_pid[pid]
                lbl = f"{tb} L{tl}" if tb and tl else (tb or lbl)

            items.append(
                _make_item(
                    item_id=f"inflight:{orig_coords or 'src'}->{dest_coords}:{int(q.finish_ts or 0)}:{idx}",
                    dest=dest,
                    target_building=tb,
                    target_level=tl,
                    building_id=bid,
                    label=lbl,
                    status="in_flight",
                    pig=res.pig_iron,
                    met=res.metal,
                    kry=res.kryptonite,
                    spi=res.spice,
                    src=src,
                    source_coords=orig_coords,
                    eta_seconds=q.remaining_seconds,
                    finish_ts=q.finish_ts,
                    reason=det_s or "Transport in flight",
                )
            )

        # 2. Planned TRANSPORT actions in the current plan
        for idx, act in enumerate(actions):
            if act.kind != "TRANSPORT":
                continue
            res = act.cost
            if not isinstance(res, Resources) or res.is_zero():
                continue
            dest = planet_by_id.get(_safe_api_int(act.target_id, 0))
            if dest is None:
                dest_coords = _extract_coords_from_text(act.target_name)
                dest = planet_by_coords.get(dest_coords) if dest_coords else None
            if dest is None:
                continue
            src = planet_by_id.get(_safe_api_int(act.planet_id, 0))
            pid = int(dest.planet_id)
            buf = planned_by_pid.setdefault(pid, [0, 0, 0, 0])
            buf[0] += res.pig_iron
            buf[1] += res.metal
            buf[2] += res.kryptonite
            buf[3] += res.spice

            tb, tl, bid = "", None, None
            rsn = str(act.reason or "")
            m_for = re.search(r"\bfor\s+(.+?)\s*$", rsn)
            hint_str = m_for.group(1).strip() if m_for else ""
            if hint_str:
                tb, tl, bid = _parse_building_target_hint(hint_str)
            if not tb and pid in early_map and early_map[pid].get("label"):
                tb, tl, bid = _parse_building_target_hint(str(early_map[pid].get("label")))
            if not tb and pid in next_bldg_by_pid:
                tb, tl, bid, _ = next_bldg_by_pid[pid]
            lbl = f"{tb} L{tl}" if tb and tl else (tb or hint_str[:64] or "Scheduled transport")
            eta_s = act.duration_seconds if (act.duration_seconds is not None and act.duration_seconds > 0) else act.wait_seconds

            items.append(
                _make_item(
                    item_id=f"planned:{act.planet_id}->{pid}:{idx}",
                    dest=dest,
                    target_building=tb,
                    target_level=tl,
                    building_id=bid,
                    label=lbl,
                    status="planned",
                    pig=res.pig_iron,
                    met=res.metal,
                    kry=res.kryptonite,
                    spi=res.spice,
                    src=src,
                    source_coords=str(act.planet_coords or ""),
                    eta_seconds=eta_s,
                    finish_ts=None,
                    reason=rsn or "Planned inter-planet transport",
                )
            )

        # 3. Pending / early resource requests from AI-Planner `early_transport_needs`
        requested_pids: set[int] = set()
        for raw_pid, entry in early_map.items():
            if not isinstance(entry, Mapping):
                continue
            pid = _safe_api_int(raw_pid, 0)
            dest = planet_by_id.get(pid)
            if dest is None:
                continue
            planned_buf = planned_by_pid.get(pid, [0, 0, 0, 0])
            pig = max(0, _safe_api_int(entry.get("pig_iron", 0), 0) - planned_buf[0])
            met = max(0, _safe_api_int(entry.get("metal", 0), 0) - planned_buf[1])
            kry = max(0, _safe_api_int(entry.get("kryptonite", 0), 0) - planned_buf[2])
            spi = max(0, _safe_api_int(entry.get("spice", 0), 0) - planned_buf[3])
            requested_pids.add(pid)
            if pig + met + kry + spi <= 0:
                continue
            raw_lbl = str(entry.get("label") or "").strip()
            tb, tl, bid = _parse_building_target_hint(raw_lbl)
            if not tb and pid in next_bldg_by_pid:
                tb, tl, bid, _ = next_bldg_by_pid[pid]
            lbl = f"{tb} L{tl}" if tb and tl else (raw_lbl[:64] or "Resource request")
            need_by = _safe_api_float(entry.get("need_by_sec"), 0.0)
            items.append(
                _make_item(
                    item_id=f"req:{pid}:{bid or 'b'}:{tl or 0}",
                    dest=dest,
                    target_building=tb,
                    target_level=tl,
                    building_id=bid,
                    label=lbl,
                    status="requested",
                    pig=pig,
                    met=met,
                    kry=kry,
                    spi=spi,
                    eta_seconds=need_by if need_by > 0.0 else None,
                    finish_ts=None,
                    reason=raw_lbl or "AI-Planner early resource request",
                )
            )

        # 4. Stalled building actions in multi-planet empires (covers priority mode too)
        if len(display_state.planets) >= 2:
            for pid, (tb, tl, bid, act) in next_bldg_by_pid.items():
                if pid in requested_pids:
                    continue
                if act.executable_now or act.missing.is_zero():
                    continue
                dest = planet_by_id.get(pid)
                if dest is None:
                    continue
                inb = in_flight_by_pid.get(pid, [0, 0, 0, 0])
                pln = planned_by_pid.get(pid, [0, 0, 0, 0])
                pig = max(0, act.missing.pig_iron - inb[0] - pln[0])
                met = max(0, act.missing.metal - inb[1] - pln[1])
                kry = max(0, act.missing.kryptonite - inb[2] - pln[2])
                spi = max(0, act.missing.spice - inb[3] - pln[3])
                if pig + met + kry + spi <= 0:
                    continue
                lbl = f"{tb} L{tl}" if tb and tl else (tb or "Building resource request")
                items.append(
                    _make_item(
                        item_id=f"req:{pid}:{bid or 'b'}:{tl or 0}",
                        dest=dest,
                        target_building=tb,
                        target_level=tl,
                        building_id=bid,
                        label=lbl,
                        status="requested",
                        pig=pig,
                        met=met,
                        kry=kry,
                        spi=spi,
                        eta_seconds=act.wait_seconds,
                        finish_ts=None,
                        reason=str(act.reason or "Waiting for building resources"),
                    )
                )

        status_order = {"in_flight": 0, "planned": 1, "requested": 2}
        items.sort(
            key=lambda it: (
                status_order.get(str(it.get("status") or ""), 9),
                float(it["eta_seconds"]) if it.get("eta_seconds") is not None else float("inf"),
                str(it.get("planet_coords") or ""),
                str(it.get("label") or ""),
            )
        )
        return items

    @staticmethod
    def _count_active_builders(
        state: Optional[GameState],
        *,
        now_ts: Optional[float] = None,
    ) -> int:
        if state is None or not state.planets:
            return 0
        now_w = _safe_api_float(now_ts, time.time()) if now_ts is not None else time.time()
        pid_by_coords: dict[str, int] = {}
        owned_pids: set[int] = set()
        for p in state.planets:
            if normalize_planet_type(p) == "raid":
                continue
            pid = _safe_api_int(p.planet_id, 0)
            if pid <= 0:
                continue
            owned_pids.add(pid)
            nc = normalize_coords(p.coords)
            if nc:
                pid_by_coords[nc] = pid
        active_pid = _safe_api_int(state.active_planet_id, 0)
        busy_pids: set[int] = set()
        for q in state.active_queues or ():
            if getattr(q, "category", None) != "building":
                continue
            fin = getattr(q, "finish_ts", None)
            rem = getattr(q, "remaining_seconds", None)
            if fin is not None:
                if _safe_api_float(fin, 0.0) <= now_w:
                    continue
            elif rem is not None and _safe_api_float(rem, 0.0) <= 0.0:
                continue
            q_orig = normalize_coords(getattr(q, "origin_coords", "") or "")
            if q_orig and q_orig in pid_by_coords:
                busy_pids.add(pid_by_coords[q_orig])
            elif not q_orig and active_pid in owned_pids:
                busy_pids.add(active_pid)
        return len(busy_pids)

    @classmethod
    def _compute_current_hourly_production(
        cls,
        state: Optional[GameState],
        *,
        now_ts: Optional[float] = None,
    ) -> dict[str, Any]:
        eff_now = _safe_api_float(now_ts, time.time()) if now_ts is not None else time.time()
        if eff_now <= 0.0:
            eff_now = time.time()
        minute_bucket = int(eff_now // 60) * 60
        if state is None or not state.planets:
            return {
                "minute_bucket": minute_bucket,
                "recorded_at": round(eff_now, 3),
                "pig_iron_per_hour": 0.0,
                "metal_per_hour": 0.0,
                "kryptonite_per_hour": 0.0,
                "spice_per_hour": 0.0,
                "total_per_hour": 0.0,
                "speq_per_hour": 0.0,
                "planet_count": 0,
                "active_builders": 0,
            }
        econ = estimate_hourly_economy(state)
        pig_hr = round(_safe_api_float(econ.get("pig_iron_per_hr"), 0.0), 1)
        met_hr = round(max(0.0, _safe_api_float(econ.get("metal_per_hr"), 0.0)), 1)
        kry_hr = round(max(0.0, _safe_api_float(econ.get("kryptonite_per_hr"), 0.0)), 1)
        spi_hr = round(max(0.0, _safe_api_float(econ.get("spice_per_hr"), 0.0)), 1)
        tot_hr = round(max(0.0, _safe_api_float(econ.get("net_resources_per_hr"), max(0.0, pig_hr) + met_hr + kry_hr + spi_hr)), 1)
        speq_hr = round(max(0.0, _safe_api_float(econ.get("spice_eq_per_hr"), 0.0)), 1)
        p_cnt = sum(1 for p in state.planets if normalize_planet_type(p) != "raid")
        b_cnt = cls._count_active_builders(state, now_ts=eff_now)
        return {
            "minute_bucket": minute_bucket,
            "recorded_at": round(eff_now, 3),
            "pig_iron_per_hour": pig_hr,
            "metal_per_hour": met_hr,
            "kryptonite_per_hour": kry_hr,
            "spice_per_hour": spi_hr,
            "total_per_hour": tot_hr,
            "speq_per_hour": speq_hr,
            "planet_count": p_cnt,
            "active_builders": b_cnt,
        }

    def _db_total_changes_token(self) -> Optional[tuple[int, int, int]]:
        db = self._db
        if db is None:
            return None
        tok_fn = getattr(db, "change_token", None)
        if callable(tok_fn):
            try:
                return tok_fn()
            except Exception:
                return None
        conn = getattr(db, "_conn", None)
        if conn is None:
            return None
        db_lock = getattr(db, "_lock", None)
        try:
            if db_lock is not None:
                with db_lock:
                    dv_row = conn.execute("PRAGMA data_version").fetchone()
                    tc = int(conn.total_changes)
            else:
                dv_row = conn.execute("PRAGMA data_version").fetchone()
                tc = int(conn.total_changes)
            dv = int(dv_row[0]) if dv_row is not None else 0
            return (id(db), tc, dv)
        except Exception:
            return None

    def _record_hourly_production_locked(
        self,
        state: Optional[GameState],
        *,
        now_ts: Optional[float] = None,
    ) -> dict[str, Any]:
        snap = self._compute_current_hourly_production(state, now_ts=now_ts)
        if self._db is None or state is None or not state.planets:
            return snap
        has_economy_signal = (
            float(snap.get("total_per_hour", 0.0)) > 0.0
            or float(snap.get("speq_per_hour", 0.0)) > 0.0
            or any(bool(p.buildings) or bool(p.economy_rates) for p in state.planets)
        )
        if not has_economy_signal:
            return snap
        m_bucket = int(snap["minute_bucket"])
        if m_bucket == self._last_recorded_prod_minute:
            return snap
        rec_fn = getattr(self._db, "record_hourly_production", None)
        if callable(rec_fn):
            try:
                rec_fn(
                    pig_iron_per_hour=snap["pig_iron_per_hour"],
                    metal_per_hour=snap["metal_per_hour"],
                    kryptonite_per_hour=snap["kryptonite_per_hour"],
                    spice_per_hour=snap["spice_per_hour"],
                    total_per_hour=snap["total_per_hour"],
                    speq_per_hour=snap["speq_per_hour"],
                    planet_count=snap["planet_count"],
                    active_builders=snap["active_builders"],
                    ts=snap["recorded_at"],
                )
                self._last_recorded_prod_minute = m_bucket
                self._hourly_prod_cache_key = None
            except Exception:
                pass
        return snap

    def _get_hourly_production_history_locked(self, limit: int = 1440) -> list[dict[str, Any]]:
        if self._db is None:
            return []
        get_fn = getattr(self._db, "get_hourly_production_history", None)
        if not callable(get_fn):
            return []
        tok = self._db_total_changes_token()
        ckey = (tok, int(limit)) if tok is not None else None
        if ckey is not None and self._hourly_prod_cache_key == ckey:
            return [dict(r) for r in self._hourly_prod_cache]
        try:
            rows = get_fn(limit=int(limit))
            clean = _sanitize_finite_json([dict(r) for r in (rows or ()) if isinstance(r, Mapping)])
        except Exception:
            clean = []
        if ckey is not None:
            self._hourly_prod_cache_key = ckey
            self._hourly_prod_cache = clean
        return [dict(r) for r in clean]

    def _get_loot_recycle_telemetry_locked(
        self,
        hours: int = 24,
        *,
        now_ts: Optional[float] = None,
    ) -> dict[str, Any]:
        eff_now = _safe_api_float(now_ts, time.time()) if now_ts is not None else time.time()
        if eff_now <= 0.0:
            eff_now = time.time()
        win_hours = max(1, min(168, _safe_api_int(hours, 24)))
        end_bucket = int(eff_now // 3600) * 3600
        if self._db is None or not callable(getattr(self._db, "get_loot_recycle_hourly", None)):
            start_bucket = end_bucket - (win_hours - 1) * 3600
            empty_hist = [
                {
                    "hour_bucket": hb,
                    "stolen_pig_iron": 0.0,
                    "stolen_metal": 0.0,
                    "stolen_kryptonite": 0.0,
                    "stolen_spice": 0.0,
                    "stolen_total": 0.0,
                    "recycled_pig_iron": 0.0,
                    "recycled_metal": 0.0,
                    "recycled_kryptonite": 0.0,
                    "recycled_spice": 0.0,
                    "recycled_total": 0.0,
                    "total": 0.0,
                    "attack_count": 0,
                    "recycle_count": 0,
                    "updated_at": 0.0,
                }
                for hb in range(start_bucket, end_bucket + 3600, 3600)
            ]
            return {
                "history": empty_hist,
                "summary": {
                    "total_stolen": 0.0,
                    "total_recycled": 0.0,
                    "grand_total": 0.0,
                    "attack_count": 0,
                    "recycle_count": 0,
                    "window_hours": win_hours,
                    "stolen_pig_iron": 0.0,
                    "stolen_metal": 0.0,
                    "stolen_kryptonite": 0.0,
                    "stolen_spice": 0.0,
                    "recycled_pig_iron": 0.0,
                    "recycled_metal": 0.0,
                    "recycled_kryptonite": 0.0,
                    "recycled_spice": 0.0,
                },
            }
        tok = self._db_total_changes_token()
        ckey = (tok, end_bucket, win_hours) if (tok is not None and now_ts is None) else None
        if ckey is not None and self._loot_recycle_cache_key == ckey and self._loot_recycle_cache is not None:
            return {
                "history": [dict(r) for r in self._loot_recycle_cache["history"]],
                "summary": dict(self._loot_recycle_cache["summary"]),
            }
        try:
            payload = self._db.get_loot_recycle_hourly(hours=win_hours, now_ts=eff_now)
            if not isinstance(payload, Mapping):
                raise ValueError("invalid loot_recycle_hourly payload")
            clean = _sanitize_finite_json(
                {
                    "history": [dict(r) for r in (payload.get("history") or ()) if isinstance(r, Mapping)],
                    "summary": dict(payload.get("summary") or {}),
                }
            )
        except Exception:
            return {
                "history": [],
                "summary": {
                    "total_stolen": 0.0,
                    "total_recycled": 0.0,
                    "grand_total": 0.0,
                    "attack_count": 0,
                    "recycle_count": 0,
                    "window_hours": win_hours,
                },
            }
        if ckey is not None:
            # Refresh token in case first-read auto-backfill incremented total_changes
            tok_after = self._db_total_changes_token()
            self._loot_recycle_cache_key = (tok_after, end_bucket, win_hours) if tok_after is not None else ckey
            self._loot_recycle_cache = clean
        return {
            "history": [dict(r) for r in clean["history"]],
            "summary": dict(clean["summary"]),
        }

    def _get_db_summary_cached_locked(self) -> Optional[dict[str, Any]]:
        if self._db is None:
            return None
        sum_fn = getattr(self._db, "summary", None)
        if not callable(sum_fn):
            return None
        tok = self._db_total_changes_token()
        ckey = (tok, sum_fn) if tok is not None else None
        if ckey is not None and self._db_summary_cache_key == ckey and self._db_summary_cache is not None:
            return dict(self._db_summary_cache)
        raw = sum_fn()
        clean = _sanitize_finite_json(raw) if isinstance(raw, Mapping) else raw
        if ckey is not None and isinstance(clean, dict):
            self._db_summary_cache_key = ckey
            self._db_summary_cache = clean
            return dict(clean)
        return clean

    def _attach_raid_stats(self, state: GameState) -> GameState:
        existing_rs = getattr(state, "raid_stats", None)
        base_rs: dict[str, Any] = dict(existing_rs) if isinstance(existing_rs, Mapping) else {}
        if self._db is not None:
            get_stats = getattr(self._db, "get_raid_stats", None)
            if callable(get_stats):
                tok = self._db_total_changes_token()
                rs_ckey = (tok, get_stats) if tok is not None else None
                now_wall = time.time()
                if (
                    rs_ckey is not None
                    and self._raid_stats_cache_key == rs_ckey
                    and self._raid_stats_cache is not None
                ):
                    stats = self._raid_stats_cache
                    raw_bt = stats.get("blocked_targets")
                    if isinstance(raw_bt, Mapping) and raw_bt:
                        needs_bt_refresh = (now_wall - self._raid_stats_cache_ts) >= 10.0 or any(
                            _safe_api_float(v.get("blocked_until_ts", 0.0)) <= now_wall
                            for v in raw_bt.values()
                            if isinstance(v, Mapping)
                        )
                        if needs_bt_refresh:
                            fresh_bt: dict[str, dict[str, Any]] = {}
                            for bk, bv in raw_bt.items():
                                if not isinstance(bv, Mapping):
                                    continue
                                b_until = _safe_api_float(bv.get("blocked_until_ts", 0.0))
                                if b_until > now_wall:
                                    nbv = dict(bv)
                                    nbv["remaining_seconds"] = round(max(0.0, b_until - now_wall), 1)
                                    fresh_bt[str(bk)] = nbv
                            stats = dict(stats)
                            stats["blocked_targets"] = fresh_bt
                            self._raid_stats_cache = stats
                            self._raid_stats_cache_ts = now_wall
                else:
                    stats = get_stats()
                    if rs_ckey is not None and isinstance(stats, Mapping):
                        self._raid_stats_cache_key = rs_ckey
                        self._raid_stats_cache = dict(stats)
                        self._raid_stats_cache_ts = now_wall
                if isinstance(stats, Mapping):
                    base_rs.update(stats)
            base_rs["transmitter_status"] = self._get_transmitter_status_locked()
        elif bool(getattr(self.strategy_config, "allow_transmitter", False)):
            base_rs["transmitter_status"] = self._get_transmitter_status_locked()
        enriched = replace(state, raid_stats=base_rs) if base_rs != existing_rs else state
        enriched = self._merge_colony_candidates_if_needed(enriched, self.strategy_config)
        return self._enrich_active_queues_locked(enriched)

    def _invalidate_dashboard_cache(self) -> None:
        self._dash_cache_state_ref = None
        self._dash_static_cache_key = None
        self._dash_static_cache_json = None
        self._dash_plan_cache_key = None
        self._dash_plan_cache_val = None
        self._dash_ag_cache_key = None
        self._dash_ag_cache_json = None
        self._co_goals_cache_key = None
        self._co_goals_cache = None
        self._active_ai_cache_key = None
        self._active_ai_cache = None

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
        pid = _safe_api_int(raw.get("planet_id"), 0)
        p_name = self._custom_planet_names.get(str(pid), "") if pid > 0 else ""
        if not p_name:
            ref_st = state if state is not None else self._last_state
            if ref_st is not None:
                nc_act = normalize_coords(str(raw.get("planet_coords") or ""))
                for p_it in ref_st.planets or ():
                    if (pid > 0 and int(p_it.planet_id) == pid) or (
                        nc_act and normalize_coords(p_it.coords) == nc_act
                    ):
                        p_name = self._custom_planet_names.get(str(p_it.planet_id)) or str(p_it.name or "")
                        break
        if p_name:
            raw["planet_name"] = p_name
        return _sanitize_finite_json(raw)

    @staticmethod
    def _new_idle_planner_job(kind: str) -> dict[str, Any]:
        return {
            "job_id": None,
            "kind": kind,
            "status": "idle",
            "phase": "idle",
            "progress": 0.0,
            "progress_pct": 0.0,
            "started_ts": None,
            "finished_ts": None,
            "elapsed_ms": 0.0,
            "trigger_source": "",
            "run_id": None,
            "error": None,
            "auto_replan_ai": False,
        }

    @staticmethod
    def _load_config_optimizer_mod() -> Any:
        try:
            from spaceinvasion.planner import config_optimizer as mod

            return mod
        except ImportError as exc:
            raise ApiRequestError(
                f"Config Optimizer engine is unavailable: {_redact_api_payload(exc)}"
            ) from exc

    @staticmethod
    def _load_ai_planner_mod() -> Any:
        try:
            from spaceinvasion.planner import ai_planner as mod

            return mod
        except ImportError as exc:
            raise ApiRequestError(
                f"AI-Planner engine is unavailable: {_redact_api_payload(exc)}"
            ) from exc

    @staticmethod
    def _load_action_graph_builders() -> tuple[Any, Any]:
        try:
            from spaceinvasion.planner.action_graph import (
                build_ai_planner_graph,
                build_fleet_planner_graph,
            )

            return build_ai_planner_graph, build_fleet_planner_graph
        except ImportError as exc:
            raise ApiRequestError(
                f"Action Graph 4-tab builders are unavailable: {_redact_api_payload(exc)}"
            ) from exc

    def _get_planner_state_locked(self) -> Optional[GameState]:
        if self._last_state is not None:
            return self._last_state
        if self._db is not None and hasattr(self._db, "load_latest_game_state"):
            try:
                loaded = self._db.load_latest_game_state()
                if loaded is not None:
                    return loaded
            except Exception:
                return None
        return None

    def _get_active_config_optimizer_locked(self) -> Optional[dict[str, Any]]:
        if self._db is not None and hasattr(self._db, "get_active_config_optimizer"):
            return self._db.get_active_config_optimizer()
        return dict(self._active_config_opt_mem) if isinstance(self._active_config_opt_mem, Mapping) else None

    def _get_config_optimizer_building_goals_locked(self) -> Optional[dict[str, Any]]:
        """Return lightweight per-planet Config Optimizer building target levels (`b1..b21`), cached by DB version."""
        tok = self._db_total_changes_token()
        ckey = (tok, self._db is not None, id(self._active_config_opt_mem))
        if tok is not None and self._co_goals_cache_key == ckey:
            return self._co_goals_cache
        active_co = self._get_active_config_optimizer_locked()
        if not isinstance(active_co, Mapping):
            if tok is not None:
                self._co_goals_cache_key = ckey
                self._co_goals_cache = None
            return None
        res_obj = active_co.get("result") if isinstance(active_co.get("result"), Mapping) else active_co
        planets_raw = res_obj.get("planets") if isinstance(res_obj, Mapping) else None
        if not isinstance(planets_raw, Sequence) or isinstance(planets_raw, (str, bytes)):
            if tok is not None:
                self._co_goals_cache_key = ckey
                self._co_goals_cache = None
            return None
        all_bkeys = tuple(f"b{bid}" for bid in sorted(game_data.BUILDING_BY_ID))
        by_planet_id: dict[str, dict[str, Any]] = {}
        by_coords: dict[str, dict[str, Any]] = {}
        for p_rec in planets_raw:
            if not isinstance(p_rec, Mapping) or bool(p_rec.get("is_placeholder", False)):
                continue
            pid_i = _safe_api_int(p_rec.get("planet_id"), 0)
            coords_s = normalize_coords(str(p_rec.get("coords") or ""))
            spec_s = str(
                p_rec.get("detected_specialization")
                or p_rec.get("locked_specialization")
                or p_rec.get("specialization")
                or ""
            ).strip()
            goals_map: dict[str, int] = {bk: 0 for bk in all_bkeys}
            raw_blds = p_rec.get("buildings")
            if isinstance(raw_blds, Sequence) and not isinstance(raw_blds, (str, bytes)):
                for brow in raw_blds:
                    if not isinstance(brow, Mapping):
                        continue
                    bkey = str(brow.get("key") or "").strip().lower()
                    if bkey in goals_map:
                        goals_map[bkey] = max(0, _safe_api_int(brow.get("target_level"), 0))
            entry = {
                "planet_id": pid_i,
                "coords": coords_s,
                "specialization": spec_s,
                "goals": goals_map,
            }
            if pid_i > 0:
                by_planet_id[str(pid_i)] = entry
            if coords_s:
                by_coords[coords_s] = entry
        out: Optional[dict[str, Any]] = (
            {
                "run_id": active_co.get("run_id"),
                "updated_ts": _safe_api_float(active_co.get("updated_ts"), 0.0),
                "by_planet_id": by_planet_id,
                "by_coords": by_coords,
            }
            if (by_planet_id or by_coords)
            else None
        )
        if tok is not None:
            self._co_goals_cache_key = ckey
            self._co_goals_cache = out
        return out

    def _list_config_optimizer_runs_locked(self, limit: int = 20) -> list[dict[str, Any]]:
        if self._db is not None and hasattr(self._db, "list_config_optimizer_runs"):
            return list(self._db.list_config_optimizer_runs(limit=limit))
        return list(reversed(self._config_opt_runs_mem[-limit:]))

    def _get_reserved_floors_locked(self) -> dict[str, dict[str, int]]:
        if self._db is not None and hasattr(self._db, "get_reserved_building_floors"):
            return self._db.get_reserved_building_floors()
        return {pk: dict(bm) for pk, bm in self._reserved_floors_mem.items()}

    def _update_reserved_floor_locked(
        self,
        planet_key: str,
        building_key: str,
        min_level: int,
        *,
        reason: str = "",
    ) -> dict[str, dict[str, int]]:
        if self._db is not None and hasattr(self._db, "update_reserved_building_floor"):
            return self._db.update_reserved_building_floor(
                planet_key,
                building_key,
                min_level,
                reason=reason,
            )
        p_map = self._reserved_floors_mem.setdefault(str(planet_key), {})
        p_map[str(building_key)] = max(max(0, int(min_level)), int(p_map.get(str(building_key), 0)))
        return {pk: dict(bm) for pk, bm in self._reserved_floors_mem.items()}

    def _get_planet_specializations_locked(self) -> dict[str, str]:
        if self._db is not None and hasattr(self._db, "get_config_optimizer_specializations"):
            return self._db.get_config_optimizer_specializations()
        return dict(self._planet_specializations_mem)

    def _update_planet_specializations_locked(
        self,
        specializations: Mapping[str, Any],
        *,
        replace: bool = False,
    ) -> dict[str, str]:
        if self._db is not None and hasattr(self._db, "update_config_optimizer_specializations"):
            updated = self._db.update_config_optimizer_specializations(
                specializations,
                replace=replace,
            )
            self._planet_specializations_mem = dict(updated)
            return dict(updated)
        co_mod = self._load_config_optimizer_mod()
        if replace:
            next_map = co_mod.normalize_planet_specializations_map(specializations)
        else:
            next_map = dict(self._planet_specializations_mem)
            for raw_pk, raw_spec in specializations.items():
                pk = str(raw_pk).strip()[:64]
                if not pk:
                    continue
                norm_spec = co_mod.normalize_planet_specialization(raw_spec)
                if norm_spec == "auto":
                    next_map.pop(pk, None)
                else:
                    next_map[pk] = norm_spec
        self._planet_specializations_mem = dict(next_map)
        if isinstance(self._active_config_opt_mem, dict):
            params_obj = self._active_config_opt_mem.get("params")
            if isinstance(params_obj, dict):
                params_obj["planet_specializations"] = dict(next_map)
        return dict(next_map)

    def _get_active_ai_planner_locked(self) -> Optional[dict[str, Any]]:
        if self._db is not None and hasattr(self._db, "get_active_ai_planner"):
            tok = self._db_total_changes_token()
            ckey = (tok, id(self._db)) if tok is not None else None
            if ckey is not None and self._active_ai_cache_key == ckey:
                return dict(self._active_ai_cache) if isinstance(self._active_ai_cache, Mapping) else None
            rec = self._db.get_active_ai_planner()
            if ckey is not None:
                self._active_ai_cache_key = ckey
                self._active_ai_cache = rec if isinstance(rec, Mapping) else None
            return dict(rec) if isinstance(rec, Mapping) else None
        return dict(self._active_ai_plan_mem) if isinstance(self._active_ai_plan_mem, Mapping) else None

    def _list_ai_planner_runs_locked(self, limit: int = 20) -> list[dict[str, Any]]:
        if self._db is not None and hasattr(self._db, "list_ai_planner_runs"):
            return list(self._db.list_ai_planner_runs(limit=limit))
        return list(reversed(self._ai_planner_runs_mem[-limit:]))

    def _list_field_conflicts_locked(self, status: Optional[str] = "pending") -> list[dict[str, Any]]:
        if self._db is not None and hasattr(self._db, "list_field_conflict_prompts"):
            raw_items = list(self._db.list_field_conflict_prompts(status=status))
        else:
            raw_items = list(self._field_conflicts_mem.values())
            if status is not None:
                raw_items = [c for c in raw_items if str(c.get("status", "pending")) == status]
        if not self._custom_planet_names:
            return raw_items
        out_items: list[dict[str, Any]] = []
        for c in raw_items:
            cd = dict(c)
            cpid = _safe_api_int(cd.get("planet_id"), 0)
            if cpid > 0 and self._custom_planet_names.get(str(cpid)):
                cd["planet_name"] = self._custom_planet_names[str(cpid)]
            out_items.append(cd)
        return out_items

    def _upsert_field_conflict_locked(self, conflict: Mapping[str, Any]) -> dict[str, Any]:
        enriched = dict(conflict)
        cands = conflict.get("candidates")
        if isinstance(cands, Sequence) and not isinstance(cands, (str, bytes)) and cands:
            first_c = cands[0]
            if isinstance(first_c, Mapping):
                enriched.setdefault(
                    "candidate_building_key",
                    str(first_c.get("building_key") or first_c.get("key") or ""),
                )
                enriched.setdefault(
                    "candidate_level",
                    max(0, _safe_api_int(first_c.get("level", first_c.get("current_level", 0)), 0)),
                )
        if self._db is not None and hasattr(self._db, "upsert_field_conflict_prompt"):
            rec = self._db.upsert_field_conflict_prompt(enriched)
        else:
            ck = str(enriched.get("conflict_key") or "")
            existing = self._field_conflicts_mem.get(ck)
            if existing is not None:
                rec = dict(existing)
            else:
                rec = {
                    "conflict_key": ck,
                    "planet_id": _safe_api_int(enriched.get("planet_id", 0), 0),
                    "planet_coords": str(enriched.get("planet_coords") or ""),
                    "blocked_building_key": str(enriched.get("blocked_building_key") or ""),
                    "blocked_target_level": _safe_api_int(enriched.get("blocked_target_level", 0), 0),
                    "candidate_building_key": str(enriched.get("candidate_building_key") or ""),
                    "candidate_level": _safe_api_int(enriched.get("candidate_level", 0), 0),
                    "status": "pending",
                    "decision": "",
                    "created_ts": round(time.time(), 3),
                    "resolved_ts": None,
                    "detail": dict(enriched),
                }
                self._field_conflicts_mem[ck] = rec
        ck_str = str(rec.get("conflict_key") or enriched.get("conflict_key") or "").strip()
        if (
            ck_str
            and str(rec.get("status", "pending")) == "pending"
            and ck_str not in self._notified_field_conflict_keys
        ):
            self._notified_field_conflict_keys.add(ck_str)
            if self._db is not None and hasattr(self._db, "create_or_bump_notification"):
                p_coords = str(rec.get("planet_coords") or enriched.get("planet_coords") or "")
                b_name = str(
                    enriched.get("blocked_building_name")
                    or rec.get("blocked_building_key")
                    or "Building"
                )
                b_lvl = _safe_api_int(rec.get("blocked_target_level", 0), 0)
                cand_k = str(rec.get("candidate_building_key") or "")
                cand_l = _safe_api_int(rec.get("candidate_level", 0), 0)
                self._db.create_or_bump_notification(
                    title=f"Field conflict on {p_coords or 'colony'}: {b_name} L{b_lvl}",
                    message=(
                        f"Planet {p_coords or rec.get('planet_id')} has 0 free building fields for "
                        f"AI-Planner target {b_name} L{b_lvl}. Bot will never auto-demolish — "
                        f"choose Demolish ({cand_k} L{cand_l}) or Keep & Reserve Fields to rerun planner."
                    ),
                    help_prompt=(
                        "Open Action Graph -> AI-Planner tab and choose Demolish Building or "
                        "Keep & Reserve Fields (Rerun Pipeline)."
                    ),
                    severity="warning",
                    category="field_conflict",
                    action_kind="BUILD",
                    planet_coords=p_coords,
                    target_name=f"{b_name} L{b_lvl}",
                    dedupe_key=f"field_conflict:{ck_str}",
                    details=rec,
                )
        return rec

    def _snapshot_universe_research_max_map_locked(
        self, state: Optional[GameState]
    ) -> dict[str, int]:
        rows = self._get_universe_research_max_locked(state)
        out: dict[str, int] = {}
        for row in rows:
            if not isinstance(row, Mapping):
                continue
            rkey = str(row.get("key") or "").strip()
            if rkey:
                out[rkey] = max(0, _safe_api_int(row.get("universe_max_level", 0), 0))
        return out

    def _snapshot_attack_history_locked(self) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        if self._db is None:
            return [], []
        raid_rows: list[dict[str, Any]] = []
        if hasattr(self._db, "get_raid_stats"):
            try:
                get_rs = self._db.get_raid_stats
                rs = (
                    get_rs(include_telemetry_summary=False)
                    if GameApiBridge._supports_kwarg(get_rs, "include_telemetry_summary")
                    else get_rs()
                )
                hist = rs.get("history") if isinstance(rs, Mapping) else None
                if isinstance(hist, Sequence) and not isinstance(hist, (str, bytes)):
                    raid_rows = [dict(h) for h in hist if isinstance(h, Mapping)]
            except Exception:
                raid_rows = []
        message_rows: list[dict[str, Any]] = []
        if hasattr(self._db, "get_battle_reports"):
            try:
                raw_battles = list(self._db.get_battle_reports(limit=500))
                for b in raw_battles:
                    if not isinstance(b, Mapping):
                        continue
                    flat_b = dict(b)
                    loot_m = b.get("loot")
                    if isinstance(loot_m, Mapping):
                        for rk in ("pig_iron", "metal", "kryptonite", "spice"):
                            flat_b.setdefault(rk, loot_m.get(rk, 0))
                    message_rows.append(flat_b)
            except Exception:
                message_rows = []
        if not message_rows and hasattr(self._db, "get_game_messages"):
            try:
                message_rows = list(self._db.get_game_messages(category="battle_report", limit=500))
            except Exception:
                message_rows = []
        return raid_rows, message_rows

    def _compute_live_attack_telemetry_locked(
        self, state: Optional[GameState]
    ) -> dict[str, Any]:
        raid_rows, message_rows = self._snapshot_attack_history_locked()
        realized = (
            self._get_realized_loot_hourly_locked()
            if bool(getattr(self.strategy_config, "ai_adaptive_execution", False))
            else None
        )
        try:
            ai_mod = self._load_ai_planner_mod()
            if realized is not None:
                return dict(
                    ai_mod.compute_attack_telemetry(
                        raid_rows,
                        message_rows,
                        state=state,
                        realized_hourly_loot=realized["hourly_loot"],
                        realized_missions_per_hour=realized["missions_per_hour"],
                    )
                )
            return dict(ai_mod.compute_attack_telemetry(raid_rows, message_rows, state=state))
        except ApiRequestError:
            cur_slots = 0
            if state is not None:
                for p in state.planets:
                    if normalize_planet_type(p) == "normal":
                        cur_slots += 1 + max(0, p.building_level(10))
            return {
                "pig_iron": 0.0,
                "metal": 0.0,
                "kryptonite": 0.0,
                "spice": 0.0,
                "spice_eq": 0.0,
                "median_round_trip_seconds": 1800.0,
                "sample_count": 0,
                "current_fleet_slots": cur_slots,
                "estimated_attacks_per_hour": 0.0,
                "hourly_loot": {
                    "pig_iron": 0.0,
                    "metal": 0.0,
                    "kryptonite": 0.0,
                    "spice": 0.0,
                    "spice_eq": 0.0,
                },
            }

    def _evaluate_ai_planner_gate_locked(
        self,
        state: Optional[GameState],
        cfg: Optional[StrategyConfig] = None,
    ) -> dict[str, Any]:
        eff_cfg = cfg or self.strategy_config
        colonies_owned = count_allocated_normal_planets(state) if state is not None else 0
        max_planets = max(1, _safe_api_int(getattr(eff_cfg, "max_planets", 11), 11))
        live_ai_unlocked = colonies_owned >= max_planets
        active_rec = self._get_active_ai_planner_locked()
        active_result = (
            active_rec.get("result")
            if isinstance(active_rec, Mapping) and isinstance(active_rec.get("result"), Mapping)
            else None
        )
        has_plan = active_result is not None
        active_mode = normalize_planner_mode(getattr(eff_cfg, "planner_mode", "priority"))
        effective_mode = active_mode
        fallback_reason = ""
        if active_mode == "ai_planner":
            if not live_ai_unlocked:
                effective_mode = "priority"
                fallback_reason = (
                    f"Colonies {colonies_owned}/{max_planets} — live AI-Planner unlocks at "
                    f"{max_planets}/{max_planets}"
                )
            elif not has_plan:
                effective_mode = "priority"
                fallback_reason = "No active AI-Planner path generated yet — falling back to Heuristics"
        return {
            "colonies_owned": colonies_owned,
            "max_planets": max_planets,
            "live_ai_unlocked": live_ai_unlocked,
            "has_plan": has_plan,
            "active_planner_mode": active_mode,
            "effective_planner_mode": effective_mode,
            "fallback_reason": fallback_reason,
            "active_rec": active_rec,
            "active_result": active_result,
        }

    def _get_planner_jobs_snapshot(
        self,
        state: Optional[GameState] = None,
        cfg: Optional[StrategyConfig] = None,
    ) -> dict[str, Any]:
        eff_state = state if state is not None else self._get_planner_state_locked()
        gate = self._evaluate_ai_planner_gate_locked(eff_state, cfg=cfg)
        pending_conflicts = self._list_field_conflicts_locked(status="pending")
        with self._planner_jobs_lock:
            co_job = dict(self._config_opt_job)
            ai_job = dict(self._ai_planner_job)
            now_t = time.time()
            for j in (co_job, ai_job):
                if j.get("status") == "running" and isinstance(j.get("started_ts"), (int, float)):
                    j["elapsed_ms"] = round(max(0.0, (now_t - float(j["started_ts"])) * 1000.0), 1)
        return {
            "config_optimizer_job": co_job,
            "ai_planner_job": ai_job,
            "colonies_owned": gate["colonies_owned"],
            "max_planets": gate["max_planets"],
            "live_ai_unlocked": gate["live_ai_unlocked"],
            "active_planner_mode": gate["active_planner_mode"],
            "effective_planner_mode": gate["effective_planner_mode"],
            "fallback_reason": gate["fallback_reason"],
            "pending_field_conflicts": pending_conflicts,
        }

    @staticmethod
    def _normalize_progress_pct(raw_val: Any) -> float:
        val = _safe_api_float(raw_val, 0.0)
        if not math.isfinite(val):
            return 0.0
        if 0.0 < val <= 1.0:
            return round(val * 100.0, 2)
        return round(max(0.0, min(100.0, val)), 2)

    def _persist_config_optimizer_result(
        self,
        result: Mapping[str, Any],
        *,
        params: Mapping[str, Any],
        reserved_floors: Mapping[str, Any],
        trigger_source: str,
        elapsed_ms: float,
    ) -> int:
        with self._lock:
            if self._planner_closed:
                return 0
            db = self._db
            if db is not None and hasattr(db, "record_config_optimizer_run"):
                run_id = int(
                    db.record_config_optimizer_run(
                        result,
                        params=params,
                        reserved_floors=reserved_floors,
                        trigger_source=trigger_source,
                        elapsed_ms=elapsed_ms,
                        set_active=True,
                    )
                )
            else:
                run_id = len(self._config_opt_runs_mem) + 1
                rec = {
                    "run_id": run_id,
                    "updated_ts": round(time.time(), 3),
                    "trigger_source": trigger_source,
                    "elapsed_ms": round(elapsed_ms, 2),
                    "params": dict(params),
                    "reserved_floors": {
                        str(k): dict(v) for k, v in reserved_floors.items() if isinstance(v, Mapping)
                    },
                    "result": dict(result),
                }
                self._config_opt_runs_mem.append(rec)
                self._active_config_opt_mem = rec
                raw_specs = params.get("planet_specializations")
                if isinstance(raw_specs, Mapping):
                    self._planet_specializations_mem = {
                        str(k): str(v) for k, v in raw_specs.items() if str(v) and str(v) != "auto"
                    }
            self._invalidate_dashboard_cache()
        return run_id

    def _persist_ai_planner_result(
        self,
        result: Mapping[str, Any],
        *,
        config_run_id: Optional[int],
        params: Mapping[str, Any],
        trigger_source: str,
        elapsed_ms: float,
    ) -> int:
        with self._lock:
            if self._planner_closed:
                return 0
            db = self._db
            if db is not None and hasattr(db, "record_ai_planner_run"):
                run_id = int(
                    db.record_ai_planner_run(
                        result,
                        config_run_id=config_run_id,
                        params=params,
                        trigger_source=trigger_source,
                        elapsed_ms=elapsed_ms,
                        set_active=True,
                    )
                )
            else:
                run_id = len(self._ai_planner_runs_mem) + 1
                rec = {
                    "run_id": run_id,
                    "config_run_id": config_run_id,
                    "updated_ts": round(time.time(), 3),
                    "trigger_source": trigger_source,
                    "elapsed_ms": round(elapsed_ms, 2),
                    "params": dict(params),
                    "summary": (
                        dict(result.get("summary") or {})
                        if isinstance(result.get("summary"), Mapping)
                        else {}
                    ),
                    "result": dict(result),
                }
                self._ai_planner_runs_mem.append(rec)
                self._active_ai_plan_mem = rec
            self._invalidate_dashboard_cache()
        return run_id

    def _start_config_optimizer_job(
        self,
        *,
        params: Optional[Mapping[str, Any]] = None,
        auto_replan_ai: bool = True,
        sync: bool = False,
        trigger_source: str = "manual",
    ) -> dict[str, Any]:
        co_mod = self._load_config_optimizer_mod()
        # Snapshot state, universe research max, and reserved floors quickly under `_lock` (zero browser calls!)
        with self._lock:
            snap_state = self._get_planner_state_locked()
            if snap_state is None:
                raise ApiRequestError(
                    "No game state snapshot available yet; open /state or /dashboard first."
                )
            uni_max = self._snapshot_universe_research_max_map_locked(snap_state)
            reserved_floors = self._get_reserved_floors_locked()
            persisted_specs = self._get_planet_specializations_locked()
            custom_names = self._get_custom_planet_names_locked()
            display_state = self._apply_custom_planet_names_to_state(snap_state, custom_names) or snap_state
            active_co = self._get_active_config_optimizer_locked()
            cfg = self.strategy_config

        previous_result = (
            dict(active_co["result"])
            if isinstance(active_co, Mapping) and isinstance(active_co.get("result"), Mapping)
            else None
        )
        base_params = dict(getattr(co_mod, "DEFAULT_CONFIG_OPTIMIZER_PARAMS", {}) or {})
        if isinstance(active_co, Mapping) and isinstance(active_co.get("params"), Mapping):
            base_params.update(dict(active_co["params"]))
        base_params["planet_specializations"] = dict(persisted_specs)
        base_params["planet_names"] = dict(custom_names)
        base_params["custom_planet_names"] = dict(custom_names)
        base_params["max_planets"] = max(1, _safe_api_int(getattr(cfg, "max_planets", 11), 11))
        if isinstance(params, Mapping):
            base_params.update(dict(params))
            if "planet_specializations" in params:
                base_params["planet_specializations"] = co_mod.normalize_planet_specializations_map(
                    params.get("planet_specializations")
                )
        # Always merge persisted reserved floors with any explicit override
        merged_floors: dict[str, dict[str, int]] = {
            str(pk): {str(bk): max(0, _safe_api_int(lv, 0)) for bk, lv in bm.items()}
            for pk, bm in reserved_floors.items()
            if isinstance(bm, Mapping)
        }
        raw_param_floors = base_params.get("reserved_floors_by_planet")
        if isinstance(raw_param_floors, Mapping):
            for pk, bm in raw_param_floors.items():
                if not isinstance(bm, Mapping):
                    continue
                slot_f = merged_floors.setdefault(str(pk), {})
                for bk, lv in bm.items():
                    slot_f[str(bk)] = max(slot_f.get(str(bk), 0), max(0, _safe_api_int(lv, 0)))
        base_params["reserved_floors_by_planet"] = merged_floors

        with self._planner_jobs_lock:
            if not sync and self._config_opt_job.get("status") == "running":
                return {
                    "ok": True,
                    "command": "config_optimizer_run",
                    "already_running": True,
                    "job": dict(self._config_opt_job),
                }
            self._planner_job_seq += 1
            seq = self._planner_job_seq
            started_ts = time.time()
            job_id = f"config_optimizer-{seq}-{int(started_ts)}"
            self._config_opt_job = {
                "job_id": job_id,
                "kind": "config_optimizer",
                "status": "running",
                "phase": "starting",
                "progress": 0.0,
                "progress_pct": 0.0,
                "started_ts": started_ts,
                "finished_ts": None,
                "elapsed_ms": 0.0,
                "trigger_source": str(trigger_source or "manual"),
                "run_id": None,
                "error": None,
                "auto_replan_ai": bool(auto_replan_ai),
            }
            initial_job = dict(self._config_opt_job)

        def _progress(phase: str, pct: float) -> None:
            norm_p = self._normalize_progress_pct(pct)
            with self._planner_jobs_lock:
                if self._config_opt_job.get("job_id") == job_id:
                    self._config_opt_job["phase"] = str(phase or "running")
                    self._config_opt_job["progress"] = norm_p
                    self._config_opt_job["progress_pct"] = norm_p
                    self._config_opt_job["elapsed_ms"] = round(
                        max(0.0, (time.time() - started_ts) * 1000.0), 1
                    )

        outcome: dict[str, Any] = {}

        def _worker() -> None:
            t0 = time.monotonic()
            try:
                res = co_mod.run_config_optimizer(
                    snap_state,
                    universe_research_max=uni_max,
                    params=base_params,
                    planet_names=custom_names,
                    previous_result=previous_result,
                    progress_cb=_progress,
                )
                elapsed_ms = round((time.monotonic() - t0) * 1000.0, 2)
                run_id = self._persist_config_optimizer_result(
                    res,
                    params=base_params,
                    reserved_floors=merged_floors,
                    trigger_source=str(trigger_source or "manual"),
                    elapsed_ms=elapsed_ms,
                )
                finished_ts = time.time()
                with self._planner_jobs_lock:
                    if self._config_opt_job.get("job_id") == job_id:
                        self._config_opt_job.update(
                            {
                                "status": "completed",
                                "phase": "completed",
                                "progress": 100.0,
                                "progress_pct": 100.0,
                                "finished_ts": finished_ts,
                                "elapsed_ms": elapsed_ms,
                                "run_id": run_id,
                                "error": None,
                            }
                        )
                    closed_now = self._planner_closed
                    final_job = dict(self._config_opt_job)
                outcome["result"] = res
                outcome["run_id"] = run_id
                outcome["job"] = final_job
                if auto_replan_ai and not closed_now:
                    try:
                        ai_resp = self._start_ai_planner_job(
                            params=None,
                            sync=sync,
                            trigger_source=f"auto_replan:{trigger_source}",
                            auto_run_config_if_missing=False,
                            override_config_run_id=run_id,
                            override_config_result=res,
                        )
                        outcome["ai_planner_job"] = ai_resp.get("job")
                        if sync and "result" in ai_resp:
                            outcome["ai_planner_result"] = ai_resp["result"]
                    except Exception as ai_exc:
                        outcome["ai_planner_error"] = str(_redact_api_payload(ai_exc))
            except Exception as exc:
                elapsed_ms = round((time.monotonic() - t0) * 1000.0, 2)
                err_txt = str(_redact_api_payload(exc))
                with self._planner_jobs_lock:
                    if self._config_opt_job.get("job_id") == job_id:
                        self._config_opt_job.update(
                            {
                                "status": "error",
                                "phase": "error",
                                "finished_ts": time.time(),
                                "elapsed_ms": elapsed_ms,
                                "error": err_txt,
                            }
                        )
                    outcome["job"] = dict(self._config_opt_job)
                outcome["exc"] = exc

        if sync:
            _worker()
            if "exc" in outcome:
                exc_obj = outcome["exc"]
                if isinstance(exc_obj, ApiRequestError):
                    raise exc_obj
                raise ApiRequestError(f"Config Optimizer failed: {_redact_api_payload(exc_obj)}") from exc_obj
            resp: dict[str, Any] = {
                "ok": True,
                "command": "config_optimizer_run",
                "job": outcome["job"],
                "run_id": outcome.get("run_id"),
                "result": outcome.get("result"),
            }
            if "ai_planner_job" in outcome:
                resp["ai_planner_job"] = outcome["ai_planner_job"]
            return resp

        th = threading.Thread(target=_worker, name=f"si-config-opt-{seq}", daemon=True)
        th.start()
        return {
            "ok": True,
            "command": "config_optimizer_run",
            "job": initial_job,
        }

    def _start_ai_planner_job(
        self,
        *,
        params: Optional[Mapping[str, Any]] = None,
        sync: bool = False,
        refresh: bool = False,
        trigger_source: str = "manual",
        auto_run_config_if_missing: bool = True,
        override_config_run_id: Optional[int] = None,
        override_config_result: Optional[Mapping[str, Any]] = None,
    ) -> dict[str, Any]:
        ai_mod = self._load_ai_planner_mod()
        # Snapshot latest live state under `_lock` (optional live browser refresh when `refresh=True`)
        with self._lock:
            if (
                refresh
                and self._is_browser_port_open()
                and self._page_limiter.rate_limit_remaining() <= 0.0
                and not self._bot_control.get("captcha_blocked")
            ):
                try:
                    with self._client_factory(self.debug_port) as client:
                        fresh_snap = self._read_snapshot(
                            client,
                            include_buildings=True,
                            include_research=True,
                            include_shipyard=True,
                            return_home=False,
                            priority="normal",
                        )
                        obs_active = bool(getattr(client, "last_snapshot_observed_active_planet", True))
                        self._record_state(
                            fresh_snap,
                            fetched_buildings=True,
                            fetched_research=True,
                            fetched_shipyard=True,
                            fetched_home=True,
                            observed_active_planet=obs_active,
                        )
                        self._last_url = str(getattr(client.driver, "current_url", "") or self._last_url)
                except RateLimitError as exc:
                    self._record_server_rate_limit(exc)
                except AntiBotChallengeError as exc:
                    self._record_antibot_pause(exc)
                except (SpaceInvasionAuthError, Exception):
                    pass

            snap_state = self._get_planner_state_locked()
            if snap_state is None:
                raise ApiRequestError(
                    "No game state snapshot available yet; open /state or /dashboard first."
                )
            snap_state = self._merge_economy_rates(snap_state)
            custom_names = self._get_custom_planet_names_locked()
            display_state = self._apply_custom_planet_names_to_state(snap_state, custom_names) or snap_state
            active_co = self._get_active_config_optimizer_locked()
            active_ai = self._get_active_ai_planner_locked()
            raid_rows, message_rows = self._snapshot_attack_history_locked()
            uni_max = self._snapshot_universe_research_max_map_locked(snap_state)
            reserved_floors = self._get_reserved_floors_locked()
            cfg = self.strategy_config
            # Adaptive execution: plan on the loot and recycling actually received, not on every
            # fleet slot raiding back to back.
            realized_loot = (
                self._get_realized_loot_hourly_locked()
                if bool(getattr(cfg, "ai_adaptive_execution", False))
                else None
            )

        target_config: Optional[Mapping[str, Any]] = override_config_result
        config_run_id: Optional[int] = override_config_run_id
        if target_config is None and isinstance(active_co, Mapping) and isinstance(active_co.get("result"), Mapping):
            target_config = active_co["result"]
            config_run_id = _safe_api_int(active_co.get("run_id"), 0) or None

        if target_config is None and not auto_run_config_if_missing:
            raise ApiRequestError(
                "No active Config Optimizer target found. Run Config Optimization first."
            )

        base_params = dict(getattr(ai_mod, "DEFAULT_AI_PLANNER_PARAMS", {}) or {})
        if isinstance(active_ai, Mapping) and isinstance(active_ai.get("params"), Mapping):
            base_params.update(
                {k: v for k, v in active_ai["params"].items() if k != "warm_start_genes"}
            )
        if isinstance(params, Mapping):
            base_params.update(dict(params))
        persisted_params = {k: v for k, v in base_params.items() if k != "warm_start_genes"}

        with self._planner_jobs_lock:
            if not sync and self._ai_planner_job.get("status") == "running":
                return {
                    "ok": True,
                    "command": "ai_planner_run",
                    "already_running": True,
                    "job": dict(self._ai_planner_job),
                }
            self._planner_job_seq += 1
            seq = self._planner_job_seq
            started_ts = time.time()
            job_id = f"ai_planner-{seq}-{int(started_ts)}"
            self._ai_planner_job = {
                "job_id": job_id,
                "kind": "ai_planner",
                "status": "running",
                "phase": "starting",
                "progress": 0.0,
                "progress_pct": 0.0,
                "started_ts": started_ts,
                "finished_ts": None,
                "elapsed_ms": 0.0,
                "trigger_source": str(trigger_source or "manual"),
                "run_id": None,
                "error": None,
                "auto_replan_ai": False,
            }
            initial_job = dict(self._ai_planner_job)

        def _progress(phase: str, pct: float) -> None:
            norm_p = self._normalize_progress_pct(pct)
            with self._planner_jobs_lock:
                if self._ai_planner_job.get("job_id") == job_id:
                    self._ai_planner_job["phase"] = str(phase or "running")
                    self._ai_planner_job["progress"] = norm_p
                    self._ai_planner_job["progress_pct"] = norm_p
                    self._ai_planner_job["elapsed_ms"] = round(
                        max(0.0, (time.time() - started_ts) * 1000.0), 1
                    )

        outcome: dict[str, Any] = {}

        def _worker() -> None:
            nonlocal target_config, config_run_id
            t0 = time.monotonic()
            try:
                if target_config is None:
                    _progress("config_optimizer_bootstrap", 5.0)
                    co_mod = self._load_config_optimizer_mod()
                    co_params = dict(getattr(co_mod, "DEFAULT_CONFIG_OPTIMIZER_PARAMS", {}) or {})
                    co_params["max_planets"] = max(1, _safe_api_int(getattr(cfg, "max_planets", 11), 11))
                    co_params["pad_generic_colonies"] = bool(base_params.get("pad_generic_colonies", False))
                    co_params["reserved_floors_by_planet"] = reserved_floors
                    co_params["planet_names"] = dict(custom_names)
                    co_params["custom_planet_names"] = dict(custom_names)
                    # Fast bootstrap GA budget when auto-seeding a missing config target
                    co_params["catalogue_grid_size"] = min(int(co_params.get("catalogue_grid_size", 25000)), 4000)
                    co_params["ga_population_size"] = min(int(co_params.get("ga_population_size", 200)), 32)
                    co_params["ga_generations"] = min(int(co_params.get("ga_generations", 150)), 24)
                    t_co = time.monotonic()
                    co_res = co_mod.run_config_optimizer(
                        display_state,
                        universe_research_max=uni_max,
                        params=co_params,
                    )
                    co_elapsed = round((time.monotonic() - t_co) * 1000.0, 2)
                    config_run_id = self._persist_config_optimizer_result(
                        co_res,
                        params=co_params,
                        reserved_floors=reserved_floors,
                        trigger_source="auto_for_ai_planner",
                        elapsed_ms=co_elapsed,
                    )
                    target_config = co_res

                _progress("attack_telemetry", 10.0)
                tel_kwargs: dict[str, Any] = {}
                if realized_loot is not None:
                    tel_kwargs = {
                        "realized_hourly_loot": realized_loot["hourly_loot"],
                        "realized_missions_per_hour": realized_loot["missions_per_hour"],
                    }
                telemetry = ai_mod.compute_attack_telemetry(
                    raid_rows,
                    message_rows,
                    state=display_state,
                    **tel_kwargs,
                )
                res = ai_mod.run_ai_planner(
                    display_state,
                    target_config,
                    attack_telemetry=telemetry,
                    params=base_params,
                    progress_cb=_progress,
                )
                elapsed_ms = round((time.monotonic() - t0) * 1000.0, 2)
                run_id = self._persist_ai_planner_result(
                    res,
                    config_run_id=config_run_id,
                    params=persisted_params,
                    trigger_source=str(trigger_source or "manual"),
                    elapsed_ms=elapsed_ms,
                )
                finished_ts = time.time()
                with self._planner_jobs_lock:
                    if self._ai_planner_job.get("job_id") == job_id:
                        self._ai_planner_job.update(
                            {
                                "status": "completed",
                                "phase": "completed",
                                "progress": 100.0,
                                "progress_pct": 100.0,
                                "finished_ts": finished_ts,
                                "elapsed_ms": elapsed_ms,
                                "run_id": run_id,
                                "error": None,
                            }
                        )
                    final_job = dict(self._ai_planner_job)
                outcome["result"] = res
                outcome["run_id"] = run_id
                outcome["job"] = final_job
            except Exception as exc:
                elapsed_ms = round((time.monotonic() - t0) * 1000.0, 2)
                err_txt = str(_redact_api_payload(exc))
                with self._planner_jobs_lock:
                    if self._ai_planner_job.get("job_id") == job_id:
                        self._ai_planner_job.update(
                            {
                                "status": "error",
                                "phase": "error",
                                "finished_ts": time.time(),
                                "elapsed_ms": elapsed_ms,
                                "error": err_txt,
                            }
                        )
                    outcome["job"] = dict(self._ai_planner_job)
                outcome["exc"] = exc

        if sync:
            _worker()
            if "exc" in outcome:
                exc_obj = outcome["exc"]
                if isinstance(exc_obj, ApiRequestError):
                    raise exc_obj
                raise ApiRequestError(f"AI-Planner failed: {_redact_api_payload(exc_obj)}") from exc_obj
            return {
                "ok": True,
                "command": "ai_planner_run",
                "job": outcome["job"],
                "run_id": outcome.get("run_id"),
                "result": outcome.get("result"),
            }

        th = threading.Thread(target=_worker, name=f"si-ai-planner-{seq}", daemon=True)
        th.start()
        return {
            "ok": True,
            "command": "ai_planner_run",
            "job": initial_job,
        }

    @staticmethod
    def _extract_ai_resource_targets(
        ai_actions: Sequence[PlannedAction],
    ) -> tuple[Resources, dict[int, Resources]]:
        """Derive `(reserved_cost, priority_resource_need_by_planet)` from live AI-Planner steps."""
        reserved = Resources()
        first_waiting = next(
            (
                a
                for a in ai_actions
                if a.kind in ("BUILD", "WAIT_RESOURCES", "RESEARCH")
                and not a.executable_now
                and not a.cost.is_zero()
            ),
            None,
        )
        if first_waiting is None:
            first_waiting = next(
                (
                    a
                    for a in ai_actions
                    if a.kind in ("BUILD", "WAIT_RESOURCES", "RESEARCH")
                    and not a.cost.is_zero()
                ),
                None,
            )
        if first_waiting is not None:
            reserved = first_waiting.cost

        needs_by_pid: dict[int, list[int]] = {}
        for act in ai_actions:
            if act.kind not in ("BUILD", "WAIT_RESOURCES", "RESEARCH"):
                continue
            if act.planet_id <= 0 or act.missing.is_zero():
                continue
            buf = needs_by_pid.setdefault(act.planet_id, [0, 0, 0, 0])
            buf[0] = max(buf[0], max(0, _safe_api_int(act.missing.pig_iron, 0)))
            buf[1] = max(buf[1], max(0, _safe_api_int(act.missing.metal, 0)))
            buf[2] = max(buf[2], max(0, _safe_api_int(act.missing.kryptonite, 0)))
            buf[3] = max(buf[3], max(0, _safe_api_int(act.missing.spice, 0)))
        prio_needs = {
            pid: Resources(vals[0], vals[1], vals[2], vals[3])
            for pid, vals in needs_by_pid.items()
            if sum(vals) > 0
        }
        return reserved, prio_needs

    @staticmethod
    def _check_demolish_energy_safety(
        state: Optional[GameState],
        planet: Optional[PlanetState],
        bid: int,
    ) -> tuple[bool, str, int]:
        """Validate whether demolishing 1 level of `bid` on `planet` is energy-safe."""
        binfo = game_data.BUILDING_BY_ID.get(bid)
        bname = (
            str(binfo.get("name") or f"Building b{bid}")
            if isinstance(binfo, Mapping)
            else f"Building b{bid}"
        )
        if planet is None:
            return True, f"Demolish {bname} (no live planet snapshot for energy check)", 1
        cur_lvl = planet.building_level(bid)
        if cur_lvl < 1:
            return False, f"{bname} is already at Level 0 on {planet.coords}", 0
        ptype_norm = normalize_planet_type(planet)
        if ptype_norm in ("asteroid", "raid"):
            return False, f"Building demolition unavailable on {ptype_norm}", cur_lvl
        raw_loss = max(0, game_data.building_energy_delta(bid, cur_lvl))
        if raw_loss > 0:
            r2 = max(
                0,
                min(
                    200,
                    _safe_api_int(
                        (
                            state.researches
                            if state is not None and isinstance(state.researches, Mapping)
                            else {}
                        ).get("r2", 0),
                        0,
                    ),
                ),
            )
            safe_loss = int(math.ceil(raw_loss * (1.0 + 0.01 * r2)))
            throttled_res = 0
            econ_map = planet.economy_rates if isinstance(planet.economy_rates, Mapping) else {}
            for mbid, rkey in ((1, "resource1"), (2, "resource2"), (3, "resource3"), (4, "resource4")):
                mlvl = planet.building_level(mbid)
                if mlvl <= 0:
                    continue
                mpct = max(0, min(100, _safe_api_int(econ_map.get(rkey, 100), 100)))
                if mpct < 100:
                    full_e = game_data.mine_energy_needed(mbid, mlvl)
                    throttled_res += max(0, int(math.ceil(full_e * (100 - mpct) / 100.0)))
            if _safe_api_int(planet.energy.net, 0) - throttled_res - safe_loss < 0:
                return (
                    False,
                    f"Insufficient spare energy to demolish {bname} safely (upgrade Fusion plant first)",
                    cur_lvl,
                )
        return True, f"Demolish {bname} L{cur_lvl} -> L{cur_lvl - 1} to reclaim 1 field", cur_lvl

    def _plan_hybrid_ai_actions_locked(
        self,
        enriched_state: GameState,
        active_plan_result: Mapping[str, Any],
        cfg: StrategyConfig,
        *,
        record_conflicts: bool = True,
    ) -> list[PlannedAction]:
        ai_mod = self._load_ai_planner_mod()
        raw_ai_actions, field_conflicts = ai_mod.reconcile_live_ai_actions(
            enriched_state,
            active_plan_result,
            cfg,
        )
        summary_m = (
            active_plan_result.get("summary")
            if isinstance(active_plan_result.get("summary"), Mapping)
            else {}
        )
        meta_m = (
            active_plan_result.get("meta_parameters")
            if isinstance(active_plan_result.get("meta_parameters"), Mapping)
            else {}
        )
        plan_allows_demolition = bool(
            active_plan_result.get("allow_demolition", False)
            or summary_m.get("allow_demolition", False)
            or meta_m.get("allow_demolition", False)
        )
        if plan_allows_demolition:
            planets_by_pid = {int(p.planet_id): p for p in (enriched_state.planets or ())}
            ai_actions = []
            for a in raw_ai_actions:
                if a.kind != "DEMOLISH_BUILDING":
                    ai_actions.append(a)
                    continue
                p_obj = planets_by_pid.get(_safe_api_int(a.planet_id, 0))
                # Strict triple gate: allow_demolition enabled + planet strictly out of fields + energy-safe
                if (
                    p_obj is not None
                    and p_obj.fields_max > 0
                    and p_obj.fields_used >= p_obj.fields_max
                    and a.target_id is not None
                    and self._check_demolish_energy_safety(enriched_state, p_obj, int(a.target_id))[0]
                ):
                    ai_actions.append(a)
        else:
            ai_actions = [a for a in raw_ai_actions if a.kind != "DEMOLISH_BUILDING"]
        if record_conflicts and field_conflicts:
            for conflict in field_conflicts:
                if isinstance(conflict, Mapping):
                    self._upsert_field_conflict_locked(conflict)

        reserved_cost, priority_resource_need = self._extract_ai_resource_targets(ai_actions)
        merged_priority_needs: dict[int, Any] = dict(priority_resource_need)
        early_needs_fn = getattr(ai_mod, "compute_early_transport_needs", None)
        early_needs_map: dict[int, dict[str, Any]] = {}
        if callable(early_needs_fn):
            raw_early = early_needs_fn(
                enriched_state,
                active_plan_result,
                ai_actions,
                config=cfg,
            )
            if isinstance(raw_early, Mapping):
                for pid_k, entry in raw_early.items():
                    if not isinstance(entry, Mapping):
                        continue
                    pid_int = _safe_api_int(pid_k, 0)
                    if pid_int <= 0:
                        continue
                    clean_entry = dict(entry)
                    early_needs_map[pid_int] = clean_entry
                    merged_priority_needs[pid_int] = clean_entry
        self._last_early_transport_needs = {int(k): dict(v) for k, v in early_needs_map.items()}
        self._last_early_transport_needs_sig = id(enriched_state)
        if reserved_cost.is_zero() and early_needs_map:
            first_early = min(
                early_needs_map.values(),
                key=lambda item: float(item.get("need_by_sec", 0.0) or 0.0),
            )
            reserved_cost = Resources(
                pig_iron=max(0, _safe_api_int(first_early.get("pig_iron"), 0)),
                metal=max(0, _safe_api_int(first_early.get("metal"), 0)),
                kryptonite=max(0, _safe_api_int(first_early.get("kryptonite"), 0)),
                spice=max(0, _safe_api_int(first_early.get("spice"), 0)),
            )

        meta_p = active_plan_result.get("meta_parameters")
        spare_pct: Any = (
            meta_p.get("resource_spare_percent")
            if isinstance(meta_p, Mapping) and meta_p.get("resource_spare_percent") is not None
            else float(getattr(cfg, "ai_resource_spare_pct", 10.0))
        )
        atk_tel = active_plan_result.get("attack_telemetry")
        if not isinstance(atk_tel, Mapping):
            atk_tel = self._compute_live_attack_telemetry_locked(enriched_state)
        adaptive_on = bool(getattr(cfg, "ai_adaptive_execution", False))
        budget_extra: dict[str, Any] = {}
        if adaptive_on:
            # Both ship allowances (spare % and 25% infra) are shares of income that really arrived:
            # the plan's stored raid income may be the modelled every-slot-raiding figure.
            atk_tel = self._overlay_realized_loot_locked(atk_tel)
            budget_extra["spare_spend_in_window"] = self._get_spare_ship_spend_in_window_locked()
            budget_extra["spare_window_hours"] = self.SPARE_SHIP_BUDGET_WINDOW_HOURS
        inbound_cargo: Optional[Mapping[int, Any]] = None
        in_prog_rem: Optional[Mapping[int, float]] = None
        in_prog_res_rem: Optional[float] = None
        transport_demand: Optional[dict[str, Any]] = None
        if adaptive_on:
            inbound_fn = getattr(ai_mod, "compute_inbound_friendly_cargo_by_planet", None)
            raw_inbound = inbound_fn(enriched_state) if callable(inbound_fn) else None
            inbound_cargo = raw_inbound if isinstance(raw_inbound, Mapping) else {}
            in_prog_fn = getattr(ai_mod, "compute_in_progress_build_rem_by_planet", None)
            raw_in_prog = in_prog_fn(enriched_state) if callable(in_prog_fn) else None
            in_prog_rem = raw_in_prog if isinstance(raw_in_prog, Mapping) else {}
            in_prog_res_fn = getattr(ai_mod, "compute_in_progress_research_rem_seconds", None)
            raw_res_rem = in_prog_res_fn(enriched_state) if callable(in_prog_res_fn) else None
            if isinstance(raw_res_rem, (int, float)) and not isinstance(raw_res_rem, bool) and math.isfinite(raw_res_rem):
                in_prog_res_rem = max(0.0, float(raw_res_rem))
            raw_td = active_plan_result.get("transport_demand")
            if isinstance(raw_td, Mapping):
                transport_demand = dict(raw_td)
                td_rt = max(0.25, _safe_api_float(transport_demand.get("round_trips_per_hour", 2.5), 2.5))
                transport_demand["realized_utilization"] = self._recompute_transport_utilization_locked(
                    rt_per_hour=td_rt,
                )
                transport_demand["infra_cargo_ordered_last_hour"] = (
                    self._get_infra_cargo_ordered_last_hour_locked()
                )
        infra_spend_1h: Optional[dict[str, float]] = None
        infra_carryover_h = 1.0
        if adaptive_on:
            infra_spend_1h, infra_carryover_h = self._get_infra_transport_spend_last_hour_locked()
        spare_budget = compute_spare_ship_budget(
            enriched_state,
            spare_pct,
            attack_telemetry=atk_tel,
            reserved_cost=reserved_cost,
            adaptive_execution=adaptive_on,
            config=cfg,
            infra_spend_last_hour=infra_spend_1h,
            infra_carryover_hours=infra_carryover_h,
            **budget_extra,
        )
        fleet_actions = plan_fleet_and_attack_actions(
            enriched_state,
            config=cfg,
            scanned_systems=self._get_scanned_systems(),
            transmitter_status=self._get_transmitter_status_locked(),
            existing_plan=ai_actions,
            priority_resource_need=merged_priority_needs,
            spare_budget=spare_budget,
            transport_demand=transport_demand,
        )
        fleet_actions = [a for a in fleet_actions if a.kind != "DEMOLISH_BUILDING"]
        cleared_overflow_pids: set[int] = set()
        throttle_actions = plan_ai_blast_furnace_throttles(
            enriched_state,
            ai_actions,
            fleet_actions,
            cfg,
            throttle_cooldowns=self._get_throttle_cooldowns(),
            storage_warn_ratio=cfg.storage_warn_ratio,
            metal_overflow_paused=tuple(self._metal_overflow_paused_planets.keys()),
            cleared_overflow_pids=cleared_overflow_pids,
            inbound_cargo_by_planet=inbound_cargo,
            in_progress_rem_by_planet=in_prog_rem,
            in_progress_research_rem_seconds=in_prog_res_rem,
            enable_donor_smelting=bool(adaptive_on and transport_demand),
        )
        runtime_dirty = False
        if record_conflicts and cleared_overflow_pids:
            for cpid in cleared_overflow_pids:
                if self._metal_overflow_paused_planets.pop(cpid, None) is not None:
                    runtime_dirty = True
        if record_conflicts:
            if self._record_adaptive_idle_bucket_locked(
                enriched_state,
                active_plan_result,
                ai_actions=ai_actions,
                cfg=cfg,
                field_conflicts=field_conflicts,
                inbound_cargo_by_planet=inbound_cargo,
                ai_mod=ai_mod,
            ):
                runtime_dirty = True
        if runtime_dirty:
            self._persist_runtime_state()

        combined: list[PlannedAction] = [*ai_actions, *fleet_actions, *throttle_actions]
        prio_rank = {"P0": 0, "P1": 1, "P2": 2, "P3": 3}

        def _hybrid_subrank(a: PlannedAction) -> tuple[int, int]:
            if a.priority == "P0":
                return (0 if a.kind == "ALERT_HOSTILE" else 1, 0)
            if a.priority == "P1":
                if a.kind == "THROTTLE_BUILDING":
                    rsn_l = str(a.reason or "").lower()
                    if (
                        "negative energy" in rsn_l
                        or "metal storage full" in rsn_l
                        or "metal overflow cleared" in rsn_l
                    ):
                        return (0, 0)
                    return (1, 0)
                if a.kind in ("BUILD", "DEMOLISH_BUILDING", "RESEARCH"):
                    return (0, 1)
                if a.kind == "TRANSPORT":
                    return (0, 2)
                if a.kind == "SPY":
                    return (0, 3)
                if a.kind == "WAIT_RESOURCES":
                    return (1, 1)
                return (2, 0)
            if a.priority == "P2":
                if a.kind in ("BUILD", "RESEARCH", "WAIT_RESOURCES"):
                    return (0, 0)
                if a.kind == "THROTTLE_BUILDING":
                    return (0, 5)
                if a.kind == "BUILD_SHIP" and AI_INFRA_CARGO_TAG in str(a.reason or ""):
                    # Plan-sized transport orders are batched and capped per hour, so they run
                    # before the fleet missions instead of waiting for a spare page view.
                    return (0, 6)
                if a.kind == "SPY":
                    return (1, 0)
                if a.kind == "TRANSPORT":
                    return (2, 0)
                if a.kind == "STATION":
                    return (3, 0)
                if a.kind in ("ATTACK", "RECYCLE"):
                    return (4, 0)
                return (5, 0)
            return (0, 0)

        combined.sort(
            key=lambda a: (
                prio_rank.get(a.priority, 9),
                -1 if a.kind == "ALERT_HOSTILE" else (0 if a.executable_now else 1),
                _hybrid_subrank(a),
            )
        )
        deduped: list[PlannedAction] = []
        seen: set[tuple[str, int, Optional[int], str]] = set()
        for act in combined:
            key = (act.kind, act.planet_id, act.target_id, act.target_name)
            if key not in seen:
                seen.add(key)
                deduped.append(act)
        return attach_action_etas(enriched_state, deduped)

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
        gate = self._evaluate_ai_planner_gate_locked(enriched_state, cfg=cfg)
        eff_mode = gate["effective_planner_mode"]
        if eff_mode == "ai_planner" and isinstance(gate["active_result"], Mapping):
            actions = self._plan_hybrid_ai_actions_locked(
                enriched_state,
                gate["active_result"],
                cfg,
                record_conflicts=bool(record_summary and config is None),
            )
            actions = self._apply_fleet_slot_cooldowns(actions, re_sort_priority=False)
            actions = self._apply_fleet_slot_holds(enriched_state, actions, cfg=cfg, re_sort_priority=False)
            return self._apply_manual_queue_to_plan(
                enriched_state,
                actions,
                cfg=cfg,
                persist_auto_satisfied=bool(record_summary and config is None),
                reconcile_metal_production=False,
            ), None
        actions = plan_actions(
            enriched_state,
            config=cfg,
            scanned_systems=self._get_scanned_systems(),
            throttle_cooldowns=self._get_throttle_cooldowns(),
        )
        actions = self._apply_fleet_slot_cooldowns(actions, re_sort_priority=True)
        actions = self._apply_fleet_slot_holds(enriched_state, actions, cfg=cfg, re_sort_priority=True)
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
        reconcile_metal_production: bool = True,
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
            reconcile_metal_production=reconcile_metal_production,
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
        gate = self._evaluate_ai_planner_gate_locked(enriched, cfg=self.strategy_config)
        in_ai_planner = gate.get("effective_planner_mode") == "ai_planner"
        merged, _ = merge_manual_queue_actions(
            enriched,
            [],
            pending_mq,
            config=self.strategy_config,
            reconcile_metal_production=not in_ai_planner,
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
        if in_ai_planner and pinned_needs_metal:
            summary_txt = "Blast furnace controlled exclusively by AI-Planner (+ 100% Metal overflow guard)"
        elif bf_throttle is not None:
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

    def _read_snapshot(
        self,
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
        snap = GameApiBridge._call_with_optional_priority(
            fn,
            include_buildings=include_buildings,
            include_research=include_research,
            include_shipyard=include_shipyard,
            return_home=return_home,
            priority=priority,
            **extra,
        )
        bonus_seen = getattr(client, "last_home_daily_bonus_present", None)
        if isinstance(bonus_seen, bool):
            self._note_daily_login_bonus_seen_locked(present=bonus_seen)
        return snap

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
        acquired = self._lock.acquire(timeout=1.5)
        if not acquired:
            port_open = self._is_browser_port_open()
            rate_rem = self._page_limiter.rate_limit_remaining()
            bot_ctrl_snap = dict(self._bot_control)
            cap_blocked = bool(bot_ctrl_snap.get("captcha_blocked"))
            in_game = port_open and not cap_blocked and (self._last_state is not None or rate_rem > 0.0)
            current_url = self._last_url
            return _sanitize_finite_json(
                _redact_api_payload(
                    {
                        "ok": port_open,
                        "busy": True,
                        "debug_host": self.debug_host,
                        "debug_port": self.debug_port,
                        "debug_port_open": port_open,
                        "novnc_url": self.novnc_url,
                        "in_game": in_game,
                        "rate_limited": rate_rem > 0.0,
                        "retry_after": round(rate_rem, 1),
                        "page_views_last_60s": self._page_limiter.views_last_60s(),
                        "page_view_limit_per_minute": self._page_limiter.max_views_per_minute,
                        "rate_limit_hits": self._page_limiter.rate_limit_hits,
                        "url": current_url if current_url else None,
                        "action_batch_interval_seconds": self.strategy_config.action_batch_interval_seconds,
                        "remaining_hourly_actions": self._live_executor.remaining_hourly_budget(),
                        "galaxy_sweep_in_progress": bool(self._sweep_in_progress),
                        "bot_control": bot_ctrl_snap,
                        "db_backend": self._build_db_backend_status(include_ping=False),
                    }
                )
            )
        try:
            port_open = self._is_browser_port_open()
            rate_rem = self._page_limiter.rate_limit_remaining()
            cap_blocked = bool(self._bot_control.get("captcha_blocked"))
            in_game = port_open and not cap_blocked and (self._last_state is not None or rate_rem > 0.0)
            current_url: Optional[str] = self._last_url
            if port_open and not cap_blocked and self._sweep_in_progress:
                in_game = True
            elif port_open and rate_rem <= 0.0 and not cap_blocked:
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
            elif port_open and rate_rem > 0.0 and not cap_blocked:
                in_game = True
            self._last_url = current_url
            rate_rem = self._page_limiter.rate_limit_remaining()
            return _sanitize_finite_json(
                _redact_api_payload(
                    {
                        "ok": port_open,
                        "debug_host": self.debug_host,
                        "debug_port": self.debug_port,
                        "debug_port_open": port_open,
                        "novnc_url": self.novnc_url,
                        "in_game": in_game,
                        "rate_limited": rate_rem > 0.0,
                        "retry_after": round(rate_rem, 1),
                        "page_views_last_60s": self._page_limiter.views_last_60s(),
                        "page_view_limit_per_minute": self._page_limiter.max_views_per_minute,
                        "rate_limit_hits": self._page_limiter.rate_limit_hits,
                        "url": current_url if current_url else None,
                        "action_batch_interval_seconds": self.strategy_config.action_batch_interval_seconds,
                        "remaining_hourly_actions": self._live_executor.remaining_hourly_budget(),
                        "galaxy_sweep_in_progress": bool(self._sweep_in_progress),
                        "bot_control": dict(self._bot_control),
                        "db_backend": self._build_db_backend_status(include_ping=False),
                    }
                )
            )
        finally:
            self._lock.release()

    def get_bot_control(self) -> dict[str, Any]:
        acquired = self._lock.acquire(timeout=1.5)
        if not acquired:
            cached = self._last_bot_control_payload
            bot_ctrl_snap = dict(self._bot_control)
            sw_active = bool(self._sweep_in_progress)
            sw_blocking = bool(
                sw_active and getattr(self.strategy_config, "galaxy_scan_block_other_actions", True)
            )
            if isinstance(cached, Mapping):
                fallback = dict(cached)
                fallback.update(bot_ctrl_snap)
                fallback["ok"] = True
                fallback["busy"] = True
                fallback["galaxy_sweep_in_progress"] = sw_active
                fallback["galaxy_scan_blocking"] = sw_blocking
                raw_fb_sw = fallback.get("galaxy_sweep")
                fb_sw = dict(raw_fb_sw) if isinstance(raw_fb_sw, Mapping) else {}
                fb_sw["in_progress"] = sw_active
                fb_sw["blocking_other_actions"] = sw_blocking
                fallback["galaxy_sweep"] = fb_sw
                return _sanitize_finite_json(_redact_api_payload(fallback))
            return _sanitize_finite_json(
                _redact_api_payload(
                    {
                        "ok": True,
                        "busy": True,
                        **bot_ctrl_snap,
                        "galaxy_sweep_in_progress": sw_active,
                        "galaxy_scan_blocking": sw_blocking,
                        "p0_escape_flight_ready": False,
                        "scanned_systems": dict(self._scanned_systems),
                        "throttle_cooldowns": {},
                        "metal_overflow_paused_planets": sorted(
                            int(pid) for pid in self._metal_overflow_paused_planets
                        ),
                        "fleet_slot_cooldowns": {},
                        "economy_sync_timestamps": dict(self._economy_sync_timestamps),
                        "metal_production_by_planet": {},
                        "manual_queue": [],
                        "transmitter_status": {},
                        "galaxy_sweep": {
                            "in_progress": sw_active,
                            "blocking_other_actions": sw_blocking,
                        },
                    }
                )
            )
        try:
            metal_prod = {
                str(pid): _safe_api_int(rates.get("resource2", 100), 100) > 0
                for pid, rates in self._economy_rates.items()
                if isinstance(rates, Mapping)
            }
            p0_esc_ready, _ = self._has_urgent_p0_escape_flight_locked()
            payload = _sanitize_finite_json(
                _redact_api_payload(
                    {
                        "ok": True,
                        **dict(self._bot_control),
                        "galaxy_sweep_in_progress": bool(self._sweep_in_progress),
                        "galaxy_scan_blocking": bool(self._is_galaxy_scan_blocking_locked()),
                        "p0_escape_flight_ready": bool(p0_esc_ready),
                        "scanned_systems": self._get_scanned_systems(),
                        "throttle_cooldowns": self._get_throttle_cooldowns(),
                        "metal_overflow_paused_planets": sorted(
                            int(pid) for pid in self._metal_overflow_paused_planets
                        ),
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
                        "novnc_url": self.novnc_url,
                        "strategy_config": asdict(self.strategy_config),
                    }
                )
            )
            if isinstance(payload, dict):
                self._last_bot_control_payload = payload
            return payload
        finally:
            self._lock.release()

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

    def _refresh_raid_targets_locked(
        self,
        *,
        limit: int = 100,
        debris_limit: int = 60,
        state_override: Optional[GameState] = None,
    ) -> tuple[dict[str, Any], ...]:
        """Refresh `self._raid_targets` by merging top spy/raid targets with proximity-ranked
        active debris fields so nearby free/player rubble is never evicted by distant Spacia
        planets."""
        if self._db is None:
            self._raid_targets = self._enrich_raid_targets(self._raid_targets)
            return self._raid_targets
        st = state_override if state_override is not None else getattr(self, "_last_state", None)
        own_coords = [str(p.coords or "") for p in (st.planets if st is not None else ()) if getattr(p, "coords", "")]
        tok = self._db_total_changes_token()
        rt_ckey = (
            (
                tok,
                int(limit),
                int(debris_limit),
                tuple(own_coords),
                tuple(sorted(self._target_blocks.items())),
            )
            if tok is not None
            else None
        )
        if rt_ckey is not None and self._raid_targets_cache_key == rt_ckey:
            return self._raid_targets
        spy_rows = self._db.get_spy_targets(limit=limit)
        merged: list[dict[str, Any]] = []
        seen_coords: set[str] = set()
        get_deb_fn = getattr(self._db, "get_active_debris_targets", None)
        if callable(get_deb_fn) and debris_limit > 0:
            for drow in get_deb_fn(origin_coords=own_coords, limit=debris_limit, include_own=False):
                if not isinstance(drow, Mapping):
                    continue
                nc = normalize_coords(drow.get("coords") or drow.get("target_coords") or "")
                if nc and nc not in seen_coords:
                    seen_coords.add(nc)
                    merged.append(dict(drow))
        for srow in spy_rows:
            if not isinstance(srow, Mapping):
                continue
            nc = normalize_coords(srow.get("coords") or srow.get("target_coords") or "")
            if nc and nc in seen_coords:
                continue
            if nc:
                seen_coords.add(nc)
            merged.append(dict(srow))
        self._raid_targets = self._enrich_raid_targets(merged)
        self._raid_targets_cache_key = rt_ckey
        return self._raid_targets

    def _enrich_raid_targets(self, rows: Sequence[dict[str, Any]]) -> tuple[dict[str, Any], ...]:
        if not rows:
            return ()
        per_target: dict[str, Any] = {}
        if self._db is not None:
            get_stats = getattr(self._db, "get_raid_stats", None)
            if callable(get_stats):
                stats = (
                    get_stats(include_telemetry_summary=False)
                    if GameApiBridge._supports_kwarg(get_stats, "include_telemetry_summary")
                    else get_stats()
                )
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
            self._refresh_raid_targets_locked(limit=100, state_override=state)
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
                    self._last_research_sync_sig = None
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
                    now_extra_wall = time.time()
                    extra_q = [
                        q
                        for q in prev.active_queues
                        if q.category not in cur_cats
                        and not (
                            q.category == "building"
                            and (
                                (q.finish_ts is not None and _safe_api_float(q.finish_ts, 0.0) <= now_extra_wall)
                                or (
                                    q.finish_ts is None
                                    and q.remaining_seconds is not None
                                    and _safe_api_float(q.remaining_seconds, 0.0) <= 0.0
                                )
                            )
                        )
                    ]
                    if extra_q:
                        upd_kwargs["active_queues"] = tuple(list(state.active_queues) + extra_q)
            if upd_kwargs:
                state = replace(state, **upd_kwargs)
        if prev is not None and not fetched_buildings and prev.active_queues:
            ap_cur = state.active_planet()
            ap_prev = prev.active_planet()
            cur_norm_c = normalize_coords(getattr(ap_cur, "coords", "") or "") if ap_cur is not None else ""
            prev_norm_c = normalize_coords(getattr(ap_prev, "coords", "") or "") if ap_prev is not None else ""
            cur_b_jobs = [
                q
                for q in state.active_queues
                if q.category == "building"
                and (not normalize_coords(q.origin_coords) or normalize_coords(q.origin_coords) == cur_norm_c)
            ]
            prev_b_jobs = [
                q
                for q in prev.active_queues
                if q.category == "building"
                and (
                    (normalize_coords(q.origin_coords) or prev_norm_c) == cur_norm_c
                    if cur_norm_c
                    else True
                )
            ]
            if len(cur_b_jobs) == 1 and len(prev_b_jobs) > 1:
                c0 = cur_b_jobs[0]
                p0 = prev_b_jobs[0]
                same_head = (
                    (c0.building_id is not None and c0.building_id == p0.building_id)
                    or (c0.target_level is not None and c0.target_level == p0.target_level and p0.name.lower() in c0.name.lower())
                )
                now_wall = time.time()
                tail_b = [
                    pq
                    for pq in prev_b_jobs[1:]
                    if pq.queue_slot >= 2 and (pq.finish_ts is None or pq.finish_ts > now_wall)
                ]
                if same_head and tail_b:
                    state = replace(state, active_queues=tuple(list(state.active_queues) + tail_b))

        # Stamp `origin_coords` on active-planet building jobs and preserve unexpired
        # building queues observed on other owned colonies across planet switches (#100).
        ap_eff = state.active_planet()
        active_norm_coords = normalize_coords(getattr(ap_eff, "coords", "") or "") if ap_eff is not None else ""
        owned_planet_coords = {
            normalize_coords(p.coords)
            for p in (state.planets or ())
            if normalize_coords(p.coords)
        }
        stamped_queues: list[QueueJob] = []
        queues_changed = False
        stamp_active_b = bool(active_norm_coords and (fetched_buildings or fetched_home or prev is None))
        existing_b_slots: set[tuple[str, int]] = set()
        for q in state.active_queues or ():
            if q.category == "building":
                q_orig = normalize_coords(q.origin_coords)
                if not q_orig and stamp_active_b:
                    q = replace(q, origin_coords=active_norm_coords)
                    q_orig = active_norm_coords
                    queues_changed = True
                elif q_orig and q_orig != q.origin_coords:
                    q = replace(q, origin_coords=q_orig)
                    queues_changed = True
                if q_orig:
                    slot_sig = (q_orig, int(q.queue_slot or 1))
                    if slot_sig in existing_b_slots:
                        queues_changed = True
                        continue
                    existing_b_slots.add(slot_sig)
            stamped_queues.append(q)

        if prev is not None and prev.active_queues and active_norm_coords:
            now_wall_b = time.time()
            ap_prev_b = prev.active_planet()
            prev_active_norm = normalize_coords(getattr(ap_prev_b, "coords", "") or "") if ap_prev_b is not None else ""
            existing_b_sigs = {
                (
                    normalize_coords(q.origin_coords),
                    str(q.name or "").strip().lower(),
                    q.target_level,
                    int(q.queue_slot or 1),
                )
                for q in stamped_queues
                if q.category == "building"
            }
            for pq in prev.active_queues:
                if pq.category != "building":
                    continue
                pq_orig = normalize_coords(pq.origin_coords) or prev_active_norm
                if not pq_orig or pq_orig == active_norm_coords:
                    continue
                if owned_planet_coords and pq_orig not in owned_planet_coords:
                    continue
                if pq.finish_ts is not None:
                    if _safe_api_float(pq.finish_ts, 0.0) <= now_wall_b:
                        continue
                elif pq.remaining_seconds is not None and _safe_api_float(pq.remaining_seconds, 0.0) <= 0.0:
                    continue
                slot_sig_b = (pq_orig, int(pq.queue_slot or 1))
                if slot_sig_b in existing_b_slots:
                    continue
                sig_b = (
                    pq_orig,
                    str(pq.name or "").strip().lower(),
                    pq.target_level,
                    int(pq.queue_slot or 1),
                )
                if sig_b in existing_b_sigs:
                    continue
                existing_b_slots.add(slot_sig_b)
                existing_b_sigs.add(sig_b)
                stamped_queues.append(replace(pq, origin_coords=pq_orig) if pq.origin_coords != pq_orig else pq)
                queues_changed = True

        if queues_changed:
            state = replace(state, active_queues=tuple(stamped_queues))

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
        self._capture_observed_outbound_transports_locked(merged)
        self._record_hourly_production_locked(merged)
        self._record_new_colonies_locked(prev, merged)
        now_wall_bucket = time.time()
        if int(now_wall_bucket // self.ADAPTIVE_IDLE_BUCKET_SECONDS) not in self._adaptive_idle_buckets:
            active_ai_rec = self._get_active_ai_planner_locked()
            if isinstance(active_ai_rec, Mapping) and isinstance(active_ai_rec.get("result"), Mapping):
                self._record_adaptive_idle_bucket_locked(
                    merged,
                    active_ai_rec["result"],
                    cfg=self.strategy_config,
                    now_ts=now_wall_bucket,
                )
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
        elif not merged.research_offers and prev is not None and prev.active_planet_id != merged.active_planet_id:
            self._last_research_sync_sig = None
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

    def _get_universe_research_max_locked(
        self,
        state: Optional[GameState] = None,
    ) -> list[dict[str, Any]]:
        eff_state = state if state is not None else self._last_state
        own_res = eff_state.researches if eff_state is not None else None
        own_nick = eff_state.nickname if eff_state is not None else ""
        if self._db is not None and hasattr(self._db, "get_universe_research_max"):
            return list(
                self._db.get_universe_research_max(
                    own_researches=own_res,
                    own_nick=own_nick,
                )
            )
        rows: list[dict[str, Any]] = []
        own_map = dict(own_res) if isinstance(own_res, Mapping) else {}
        for slug, info in sorted(
            (game_data.RESEARCHES or {}).items(),
            key=lambda kv: _safe_api_int((kv[1] or {}).get("id"), 999),
        ):
            if not isinstance(info, Mapping):
                continue
            rid = _safe_api_int(info.get("id"), 0)
            if rid <= 0:
                continue
            rkey = f"r{rid}"
            own_l = max(0, _safe_api_int(own_map.get(rkey, own_map.get(slug, 0)), 0))
            levels_d = info.get("levels") if isinstance(info.get("levels"), Mapping) else {}
            cat_max = max((int(k) for k in levels_d.keys() if str(k).isdigit()), default=0) or None
            rows.append(
                {
                    "research_id": rid,
                    "key": rkey,
                    "slug": str(slug),
                    "name": str(info.get("name") or slug),
                    "own_level": own_l,
                    "universe_max_level": own_l,
                    "spied_max_level": 0,
                    "server_record_level": None,
                    "catalog_max_level": cat_max,
                    "gap_to_max": 0,
                    "holder": (own_nick or "You") if own_l > 0 else "",
                    "holder_coords": "",
                    "updated_ts": 0.0,
                }
            )
        return rows

    def dashboard(self, *, refresh: bool = False) -> dict[str, Any]:
        """Return combined UI telemetry, plan, buildings ETAs, combat, ranking, config, and logs."""
        cached_dash = self._last_dashboard_payload
        if not refresh and isinstance(cached_dash, Mapping):
            acquired = self._lock.acquire(timeout=2.0)
            if not acquired:
                fallback = dict(cached_dash)
                fallback["bot_control"] = _sanitize_finite_json(
                    _redact_api_payload(dict(self._bot_control))
                )
                fallback["busy"] = True
                return fallback
        else:
            self._lock.acquire()
        try:
            port_open = self._is_browser_port_open()
            rate_rem = self._page_limiter.rate_limit_remaining()
            cap_blocked_dash = bool(self._bot_control.get("captcha_blocked"))
            sweep_blocking_dash = self._is_galaxy_scan_blocking_locked()
            sweep_deferred_refresh = bool(refresh and sweep_blocking_dash)
            in_game = port_open and not cap_blocked_dash and (
                self._last_state is not None or rate_rem > 0.0 or self._sweep_in_progress
            )
            current_url = self._last_url
            if (
                port_open
                and rate_rem <= 0.0
                and not cap_blocked_dash
                and not sweep_blocking_dash
                and (refresh or self._last_state is None or self._last_snapshot_at < 0.0)
            ):
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
            if refresh and dash_state is not None:
                self._record_hourly_production_locked(dash_state)
            custom_names = self._get_custom_planet_names_locked()
            custom_names_sig = tuple(sorted(custom_names.items()))
            display_state = (
                self._apply_custom_planet_names_to_state(dash_state, custom_names)
                if dash_state is not None
                else None
            )
            eff_mode = normalize_planner_mode(self.strategy_config.planner_mode)
            dash_sig = _dashboard_state_sig(self._last_state, dash_state, self._rank_samples)
            cfg_raw = asdict(self.strategy_config)
            cfg_sig = _freeze_sig_tree(cfg_raw)
            mq_pre = (
                self._db.get_manual_queue(status="pending", limit=200)
                if self._db is not None and hasattr(self._db, "get_manual_queue")
                else []
            )
            mq_pre_sig = _freeze_sig_tree(mq_pre)
            plan_cache_hit = False
            if dash_state is not None:
                db_tok_plan = self._db_total_changes_token()
                now_mono_plan = time.monotonic()
                now_wall_plan = time.time()
                with self._planner_jobs_lock:
                    ai_job_sig = (
                        self._ai_planner_job.get("run_id"),
                        self._ai_planner_job.get("updated_ts"),
                    )
                scanned_sig = (
                    (len(self._scanned_systems), max(self._scanned_systems.values(), default=0.0))
                    if db_tok_plan is not None
                    else tuple(sorted(self._scanned_systems.items()))
                )
                throttle_sig = tuple(
                    sorted((int(pid), round(float(ts), 1)) for pid, ts in self._throttle_timestamps.items())
                )
                overflow_paused_sig = tuple(
                    sorted(int(pid) for pid in self._metal_overflow_paused_planets)
                )
                fleet_cd_sig = tuple(
                    sorted(int(pid) for pid, ts in self._fleet_slot_cooldown_until.items() if ts > now_mono_plan)
                )
                target_blocks_sig = tuple(
                    sorted(
                        (str(c), round(float(u), 1), str(r))
                        for c, (u, r) in self._target_blocks.items()
                        if u > now_wall_plan
                    )
                )
                ai_mem_sig = None if db_tok_plan is not None else _freeze_sig_tree(self._active_ai_plan_mem)
                plan_key = (
                    dash_sig,
                    custom_names_sig,
                    cfg_sig,
                    eff_mode,
                    db_tok_plan,
                    mq_pre_sig,
                    ai_job_sig,
                    ai_mem_sig,
                    scanned_sig,
                    throttle_sig,
                    overflow_paused_sig,
                    fleet_cd_sig,
                    self._observed_fleet_slot_base_offset,
                    tuple(sorted(self._observed_fleet_slots_by_pid.items())),
                    target_blocks_sig,
                    plan_actions,
                    plan_fleet_and_attack_actions,
                    round(float(self._transport_realized_utilization), 4),
                )
                plan_state_in = display_state if display_state is not None else dash_state
                if (
                    not refresh
                    and self._dash_plan_cache_val is not None
                    and _safe_sig_eq(self._dash_plan_cache_key, plan_key)
                ):
                    cached_acts, opt_summary, cached_early_needs = self._dash_plan_cache_val
                    actions = list(cached_acts)
                    if cached_early_needs:
                        self._last_early_transport_needs = {
                            int(k): dict(v) for k, v in cached_early_needs.items()
                        }
                        self._last_early_transport_needs_sig = id(plan_state_in)
                    plan_cache_hit = True
                else:
                    actions, opt_summary = self._plan_with_summary(plan_state_in, _already_enriched=True)
                    early_copy = (
                        {int(k): dict(v) for k, v in self._last_early_transport_needs.items()}
                        if self._last_early_transport_needs_sig == id(plan_state_in)
                        else {}
                    )
                    post_db_tok = self._db_total_changes_token()
                    post_mq_sig = (
                        _freeze_sig_tree(self._db.get_manual_queue(status="pending", limit=200))
                        if (
                            post_db_tok != db_tok_plan
                            and self._db is not None
                            and hasattr(self._db, "get_manual_queue")
                        )
                        else mq_pre_sig
                    )
                    post_overflow_paused_sig = tuple(
                        sorted(int(pid) for pid in self._metal_overflow_paused_planets)
                    )
                    store_plan_key = (
                        dash_sig,
                        custom_names_sig,
                        cfg_sig,
                        eff_mode,
                        post_db_tok,
                        post_mq_sig,
                        ai_job_sig,
                        ai_mem_sig,
                        scanned_sig,
                        throttle_sig,
                        post_overflow_paused_sig,
                        fleet_cd_sig,
                        self._observed_fleet_slot_base_offset,
                        tuple(sorted(self._observed_fleet_slots_by_pid.items())),
                        target_blocks_sig,
                        plan_actions,
                        plan_fleet_and_attack_actions,
                        round(float(self._transport_realized_utilization), 4),
                    )
                    self._dash_plan_cache_key = store_plan_key
                    self._dash_plan_cache_val = (list(actions), opt_summary, early_copy)
            else:
                actions = []
                opt_summary = None

            static_key = (
                dash_sig,
                cfg_sig,
                custom_names_sig,
                build_ranking_evolution,
                build_buildings_eta,
                build_combat_overview,
                estimate_planet_hourly_economy,
            )
            if self._dash_static_cache_json is None or not _safe_sig_eq(
                self._dash_static_cache_key, static_key
            ):
                ranking = (
                    build_ranking_evolution(display_state, self._rank_samples)
                    if display_state is not None
                    else None
                )
                buildings_eta = (
                    build_buildings_eta(display_state)
                    if display_state is not None
                    else None
                )
                planet_economies = (
                    {
                        str(p.planet_id): estimate_planet_hourly_economy(display_state, p)
                        for p in display_state.planets
                    }
                    if display_state is not None
                    else {}
                )
                combat = (
                    build_combat_overview(
                        display_state,
                        safety_margin=self.strategy_config.combat_safety_margin,
                    )
                    if display_state is not None
                    else None
                )
                static_clean = _sanitize_finite_json(
                    {
                        "config": cfg_raw,
                        "state": (
                            self._serialize_state_with_custom_names(
                                dash_state,
                                display_state,
                                custom_names,
                            )
                            if dash_state is not None
                            else None
                        ),
                        "buildings_eta": buildings_eta,
                        "planet_economies": planet_economies,
                        "current_hourly_production_base": self._compute_current_hourly_production(dash_state),
                        "combat": combat,
                        "ranking": ranking,
                    }
                )
                self._dash_cache_state_ref = self._last_state
                self._dash_static_cache_key = static_key
                self._dash_static_cache_json = json.dumps(static_clean, allow_nan=False)
            static_val = json.loads(self._dash_static_cache_json)

            planner_status = self._get_planner_jobs_snapshot(dash_state, cfg=self.strategy_config)
            manual_queue_pending = (
                mq_pre
                if plan_cache_hit
                else (
                    self._db.get_manual_queue(status="pending", limit=200)
                    if self._db is not None and hasattr(self._db, "get_manual_queue")
                    else []
                )
            )
            ag_opt = None
            ag_opt_sig = _freeze_sig_tree(_summary_without_elapsed(ag_opt))
            mq_sig = mq_pre_sig if plan_cache_hit else _freeze_sig_tree(manual_queue_pending)
            early_needs_map = (
                self._last_early_transport_needs
                if (
                    dash_state is not None
                    and planner_status["effective_planner_mode"] == "ai_planner"
                    and self._last_early_transport_needs_sig in (id(dash_state), id(display_state))
                )
                else {}
            )
            early_needs_sig = _freeze_sig_tree(early_needs_map)
            ag_key = (
                dash_sig,
                cfg_sig,
                custom_names_sig,
                eff_mode,
                planner_status["effective_planner_mode"],
                planner_status["ai_planner_job"].get("run_id"),
                ag_opt_sig,
                mq_sig,
                early_needs_sig,
                tuple(actions),
                build_action_graph,
            )
            if self._dash_ag_cache_json is None or not _safe_sig_eq(
                self._dash_ag_cache_key, ag_key
            ):
                plan_json = [self._serialize_action(a) for a in actions]
                raw_ag = build_action_graph(
                    display_state if display_state is not None else dash_state,
                    actions,
                    effective_config=self.strategy_config,
                    active_config=self.strategy_config,
                    simulated=False,
                    optimizer_summary=ag_opt,
                    sanitize=False,
                )
                ag_json = _sanitize_finite_json(_redact_action_graph_strings(raw_ag))
                bld_res_reqs = _sanitize_finite_json(
                    self._build_building_resource_requests_locked(
                        display_state if display_state is not None else dash_state,
                        actions,
                        early_needs_map,
                    )
                )
                ag_clean = {
                    "plan": plan_json,
                    "action_graph": ag_json,
                    "building_resource_requests": bld_res_reqs,
                }
                self._dash_cache_state_ref = self._last_state
                self._dash_ag_cache_key = ag_key
                self._dash_ag_cache_json = json.dumps(ag_clean, allow_nan=False)
            ag_val = json.loads(self._dash_ag_cache_json)

            now_dash_ts = time.time()
            cur_prod_base = static_val.get("current_hourly_production_base")
            if isinstance(cur_prod_base, dict):
                current_hourly_prod = dict(cur_prod_base)
                current_hourly_prod["minute_bucket"] = int(now_dash_ts // 60) * 60
                current_hourly_prod["recorded_at"] = round(now_dash_ts, 3)
                current_hourly_prod["active_builders"] = self._count_active_builders(dash_state, now_ts=now_dash_ts)
            else:
                current_hourly_prod = self._compute_current_hourly_production(dash_state, now_ts=now_dash_ts)
            hourly_prod_hist = self._get_hourly_production_history_locked(limit=1440)
            loot_rec = self._get_loot_recycle_telemetry_locked(hours=24)
            db_summary_clean = self._get_db_summary_cached_locked()

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
                        "novnc_url": self.novnc_url,
                        "action_batch_interval_seconds": self.strategy_config.action_batch_interval_seconds,
                        "remaining_hourly_actions": self._live_executor.remaining_hourly_budget(),
                    },
                    "bot_control": dict(self._bot_control),
                    "custom_planet_names": custom_names,
                    "planner_mode": eff_mode,
                    "effective_planner_mode": planner_status["effective_planner_mode"],
                    "fallback_reason": planner_status["fallback_reason"],
                    "live_ai_unlocked": planner_status["live_ai_unlocked"],
                    "planner_status": planner_status,
                    "optimizer_summary": ag_opt,
                    "manual_queue": manual_queue_pending,
                    "notifications": self._get_notifications_summary_locked(),
                    "transmitter_status": self._get_transmitter_status_locked(),
                    "galaxy_sweep": self._get_galaxy_sweep_state_locked(),
                    "universe_research_max": self._get_universe_research_max_locked(dash_state),
                    "novnc_url": self.novnc_url,
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
            co_goals_clean = _sanitize_finite_json(
                _redact_api_payload(self._get_config_optimizer_building_goals_locked())
            )
            result = {
                "ok": envelope["ok"],
                "server_boot_id": envelope["server_boot_id"],
                **(ui_assets_clean if isinstance(ui_assets_clean, dict) else {}),
                "health": envelope["health"],
                "bot_control": envelope["bot_control"],
                "custom_planet_names": envelope["custom_planet_names"],
                "planner_mode": envelope["planner_mode"],
                "effective_planner_mode": envelope["effective_planner_mode"],
                "fallback_reason": envelope["fallback_reason"],
                "live_ai_unlocked": envelope["live_ai_unlocked"],
                "planner_status": envelope["planner_status"],
                "config": static_val["config"],
                "state": static_val["state"],
                "plan": ag_val["plan"],
                "action_graph": ag_val["action_graph"],
                "building_resource_requests": ag_val.get("building_resource_requests", []),
                "manual_queue": envelope["manual_queue"],
                "notifications": envelope["notifications"],
                "transmitter_status": envelope["transmitter_status"],
                "galaxy_sweep": envelope["galaxy_sweep"],
                "universe_research_max": envelope["universe_research_max"],
                "hourly_production_history": hourly_prod_hist,
                "current_hourly_production": current_hourly_prod,
                "loot_recycle_history": loot_rec["history"],
                "loot_recycle_summary": loot_rec["summary"],
                "optimizer_summary": envelope["optimizer_summary"],
                "config_optimizer_goals": co_goals_clean,
                "buildings_eta": static_val["buildings_eta"],
                "planet_economies": static_val["planet_economies"],
                "combat": static_val["combat"],
                "ranking": static_val["ranking"],
                "db_summary": db_summary_clean,
                "db_backend": self._build_db_backend_status(include_ping=False),
                "novnc_url": envelope.get("novnc_url"),
            }
            if (
                dash_state is not None
                and not plan_cache_hit
                and isinstance(self._dash_plan_cache_key, tuple)
                and len(self._dash_plan_cache_key) >= 5
            ):
                final_db_tok = self._db_total_changes_token()
                if self._dash_plan_cache_key[4] != final_db_tok:
                    self._dash_plan_cache_key = (
                        self._dash_plan_cache_key[:4]
                        + (final_db_tok,)
                        + self._dash_plan_cache_key[5:]
                    )
            self._last_dashboard_payload = result
            if sweep_deferred_refresh:
                result = {
                    **result,
                    "sweep_deferred_refresh": True,
                    "sweep_message": self._galaxy_scan_busy_error_message(),
                }
        finally:
            self._lock.release()
        self._maybe_trigger_ai_drift_replan()
        return result

    get_dashboard = dashboard

    def get_state(self, *, include_buildings: bool = True) -> dict[str, Any]:
        with self._lock:
            if self._bot_control.get("captcha_blocked"):
                raise AntiBotChallengeError(
                    str(
                        self._bot_control.get("captcha_message")
                        or "Paused for CAPTCHA — confirm solved in UI to resume."
                    )
                )
            self._raise_if_rate_limited()
            if self._is_galaxy_scan_blocking_locked():
                if self._last_state is not None:
                    cached = self._refresh_cached_economy(self._last_state)
                    custom_names = self._get_custom_planet_names_locked()
                    enriched_cached = self._attach_raid_stats(cached)
                    serialized = self._serialize_state_with_custom_names(
                        enriched_cached,
                        custom_names=custom_names,
                    )
                    serialized["custom_planet_names"] = custom_names
                    serialized["sweep_in_progress"] = True
                    return _sanitize_finite_json(_redact_api_payload(serialized))
                raise GalaxyScanInProgressError(self._galaxy_scan_busy_error_message())
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
                custom_names = self._get_custom_planet_names_locked()
                enriched_cached = self._attach_raid_stats(cached)
                serialized = self._serialize_state_with_custom_names(
                    enriched_cached,
                    custom_names=custom_names,
                )
                serialized["custom_planet_names"] = custom_names
                return _sanitize_finite_json(_redact_api_payload(serialized))

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
                if self.log_dir is not None and not isinstance(
                    exc, (ApiRequestError, GalaxyScanInProgressError)
                ):
                    append_crash_debug_log(
                        component="api_server.get_state",
                        event="state_error",
                        error=exc,
                    )
                raise
            custom_names = self._get_custom_planet_names_locked()
            enriched_merged = self._attach_raid_stats(merged)
            serialized = self._serialize_state_with_custom_names(
                enriched_merged,
                custom_names=custom_names,
            )
            serialized["custom_planet_names"] = custom_names
            state_resp = _sanitize_finite_json(_redact_api_payload(serialized))
        self._maybe_trigger_ai_drift_replan()
        return state_resp

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
        from spaceinvasion.planner.strategy import _is_stale_remote_resource_wait

        is_remote = planet.planet_id != state.active_planet_id
        prereqs_met = not game_data.missing_prerequisites(
            "buildings",
            bid_int,
            buildings=planet.buildings,
            researches=state.researches,
        )
        can_exec = planet_type_allowed and offer.can_build and planet.resources.can_afford(offer.cost)
        if (
            planet_type_allowed
            and not can_exec
            and prereqs_met
            and (allow_live_verify or is_remote)
            and not offer.cost.is_zero()
            and _is_stale_remote_resource_wait(offer)
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
            action_href=offer.build_href if (can_exec and offer.can_build) else None,
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

    def _persist_galaxy_slots(
        self,
        slots: list[Any] | tuple[Any, ...],
        *,
        refresh_targets: bool = True,
    ) -> int:
        if not slots:
            return 0
        for s in slots:
            norm_sc_deb = normalize_coords(getattr(s, "coords", "") or "")
            if not norm_sc_deb:
                continue
            has_deb = bool(getattr(s, "has_debris", False))
            if has_deb:
                dp = max(0, _safe_api_int(getattr(s, "debris_pig_iron", 0), 0))
                dm = max(0, _safe_api_int(getattr(s, "debris_metal", 0), 0))
                dk = max(0, _safe_api_int(getattr(s, "debris_kryptonite", 0), 0))
                ds = max(0, _safe_api_int(getattr(s, "debris_spice", 0), 0))
                if dp + dm + dk + ds > 0:
                    self._galaxy_debris_amounts[norm_sc_deb] = {
                        "pig_iron": dp,
                        "metal": dm,
                        "kryptonite": dk,
                        "spice": ds,
                    }
            else:
                self._galaxy_debris_amounts.pop(norm_sc_deb, None)
        if self._db is not None:
            saved = self._db.record_galaxy_slots(slots)
            if refresh_targets:
                self._refresh_raid_targets_locked(limit=250)
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
            norm_sc = normalize_coords(s.coords)
            if (
                getattr(s, "status_color", "free") == "free"
                and not getattr(s, "is_spacia", False)
                and not has_debris
            ):
                by_coords.pop(norm_sc, None)
                continue
            status_color = _bounded_api_str(getattr(s, "status_color", "yellow") or "yellow", default="yellow")
            is_spacia = bool(getattr(s, "is_spacia", False))
            prev = by_coords.get(norm_sc, {})
            reset_stale_spacia = status_color == "free" or (bool(prev.get("is_spacia", False)) and not is_spacia)
            by_coords[norm_sc] = {
                "coords": norm_sc,
                "owner": _bounded_api_str(getattr(s, "owner", "") or "", default=""),
                "planet_name": _bounded_api_str(getattr(s, "name", "") or "", default=""),
                "status_color": status_color,
                "is_inactive": bool(status_color == "yellow" or is_spacia),
                "is_spacia": is_spacia,
                "has_debris": has_debris,
                "points": None if reset_stale_spacia else prev.get("points"),
                "resources": (
                    {"pig_iron": 0, "metal": 0, "kryptonite": 0, "spice": 0}
                    if reset_stale_spacia
                    else (prev.get("resources") or {"pig_iron": 0, "metal": 0, "kryptonite": 0, "spice": 0})
                ),
                "fleet": {} if reset_stale_spacia else (prev.get("fleet") or {}),
                "defense": {} if reset_stale_spacia else (prev.get("defense") or {}),
                "dug_in": False if reset_stale_spacia else prev.get("dug_in", False),
                "last_scanned_ts": now_ts,
                "last_spied_ts": 0.0 if reset_stale_spacia else prev.get("last_spied_ts", 0.0),
                "has_spy_intel": False if reset_stale_spacia else bool(prev.get("has_spy_intel", False)),
                "blocked_until_ts": _safe_api_float(prev.get("blocked_until_ts", 0.0)),
                "block_reason": _bounded_api_str(prev.get("block_reason") or "", default=""),
                "bash_hits": _safe_api_int(prev.get("bash_hits", 0)),
                "last_blocked_ts": _safe_api_float(prev.get("last_blocked_ts", 0.0)),
            }
            saved += 1
        self._raid_targets = self._enrich_raid_targets(list(by_coords.values())[:250])
        return saved

    def _owned_sweep_galaxies_locked(self, *, state_override: Optional[GameState] = None) -> list[int]:
        gals: list[int] = []
        max_g = self._max_galaxies_locked()
        st = state_override if state_override is not None else self._last_state
        if st is not None:
            for p in st.planets:
                if normalize_planet_type(p) == "raid":
                    continue
                m = re.search(r"(\d{1,2}):(\d{1,3})", str(p.coords or ""), re.ASCII)
                if m:
                    g = max(1, min(max_g, int(m.group(1))))
                    if g not in gals:
                        gals.append(g)
        return gals or [1]

    def _sweep_target_galaxies_locked(
        self,
        *,
        explicit_galaxy: Optional[int] = None,
        refresh_pct_override: Optional[int] = None,
        all_galaxies_override: Optional[bool] = None,
        state_override: Optional[GameState] = None,
        scanned_systems_override: Optional[Mapping[str, float]] = None,
    ) -> list[int]:
        max_g = self._max_galaxies_locked()
        if explicit_galaxy is not None:
            return [max(1, min(max_g, int(explicit_galaxy)))]
        cfg = self.strategy_config
        scan_all_gals = (
            bool(all_galaxies_override)
            if all_galaxies_override is not None
            else bool(getattr(cfg, "galaxy_scan_all_galaxies", False))
        )
        if scan_all_gals:
            return list(range(1, max_g + 1))
        owned = self._owned_sweep_galaxies_locked(state_override=state_override)
        refresh_pct_cfg = max(
            0,
            min(
                100,
                _safe_api_int(
                    refresh_pct_override
                    if refresh_pct_override is not None
                    else getattr(cfg, "galaxy_scan_refresh_pct", 0),
                    0,
                ),
            ),
        )
        if 0 < refresh_pct_cfg < 100:
            scanned = (
                scanned_systems_override
                if scanned_systems_override is not None
                else self._get_scanned_systems()
            ) or {}
            owned_set = set(owned)
            owned_scanned: set[tuple[int, int]] = set()
            for sk, ts_v in scanned.items():
                if _safe_api_float(ts_v, 0.0) <= 0.0:
                    continue
                m_s = re.search(r"(\d{1,2})\s*:\s*(\d{1,3})", str(sk), re.ASCII)
                if m_s:
                    gi_u, si_u = int(m_s.group(1)), int(m_s.group(2))
                    if gi_u in owned_set and 1 <= si_u <= 400:
                        owned_scanned.add((gi_u, si_u))
            if len(owned_scanned) >= len(owned_set) * 400:
                return list(range(1, max_g + 1))
        return owned

    def run_galaxy_sweep(
        self,
        *,
        pages: Optional[int] = None,
        galaxy: Optional[int] = None,
        start_system: Optional[int] = None,
        delay_range: Optional[tuple[float, float]] = None,
        dry_run: bool = False,
        priority: str = "normal",
        automatic: bool = False,
    ) -> dict[str, Any]:
        """Scan `pages` (default `100`) solar systems with randomized intervals, releasing `_lock` between steps."""
        with self._lock:
            if self._bot_control.get("captcha_blocked"):
                cur_st_cap = self._get_galaxy_sweep_state_locked()
                return _sanitize_finite_json(
                    _redact_api_payload(
                        {
                            "ok": True,
                            "command": "galaxy_sweep",
                            "executed": False,
                            "dry_run": dry_run,
                            "captcha_blocked": True,
                            "message": "Paused for CAPTCHA — confirm solved in UI before sweeping galaxy.",
                            "galaxy_sweep": cur_st_cap,
                        }
                    )
                )
            if not dry_run:
                p0_urg_start, p0_rsn_start = self._has_urgent_p0_escape_flight_locked()
                if p0_urg_start:
                    cur_st_p0 = self._get_galaxy_sweep_state_locked()
                    return _sanitize_finite_json(
                        _redact_api_payload(
                            {
                                "ok": True,
                                "command": "galaxy_sweep",
                                "executed": False,
                                "dry_run": False,
                                "preempted": True,
                                "message": p0_rsn_start,
                                "galaxy_sweep": cur_st_p0,
                            }
                        )
                    )
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
        arrow_steps_used = 0
        aborted_reason: Optional[str] = None

        try:
            with self._lock:
                self._sweep_preempt_event.clear()
                self._sweep_wake_event.clear()
                self._sweep_preempt_reason = None
                if not dry_run:
                    self._sweep_in_progress = True
                    self._sweep_done_event.clear()
                cfg = self.strategy_config
                started_with_scans_allowed = bool(getattr(cfg, "allow_galaxy_scan", False))
                enforce_scan_toggle = (automatic or started_with_scans_allowed) and priority != "interactive"
                total_pages = max(
                    1,
                    min(400, int(pages if pages is not None else getattr(cfg, "galaxy_sweep_pages", 100))),
                )
                max_g_start = self._max_galaxies_locked()
                owned_gals = self._owned_sweep_galaxies_locked()
                sw_state = self._get_galaxy_sweep_state_locked(now_ts=sweep_started_ts, include_smart=False)
                cur_gal = max(1, min(max_g_start, int(galaxy) if galaxy is not None else int(sw_state.get("cursor_galaxy") or owned_gals[0])))
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
                cfg_delay_s = round(
                    max(0.2, min(60.0, _safe_api_float(getattr(cfg, "galaxy_scan_click_delay_seconds", 1.5), 1.5))),
                    3,
                )
                cfg_jitter_s = round(
                    max(0.0, min(30.0, _safe_api_float(getattr(cfg, "galaxy_scan_click_jitter_seconds", 0.6), 0.6))),
                    3,
                )
                self._save_galaxy_sweep_state_locked(
                    {
                        "last_sweep_started_ts": sweep_started_ts,
                        "click_delay_seconds": cfg_delay_s,
                        "click_jitter_seconds": cfg_jitter_s,
                    },
                    ts=sweep_started_ts,
                )

                # Smart 2-Phase target selection (exploratory -> weighted targeted refresh). An explicit
                # operator/test `start_system` keeps the legacy contiguous cursor walk for backward compatibility.
                rotate_gals = owned_gals if galaxy is None else []
                smart_plan: Optional[Mapping[str, Any]] = None
                sweep_targets: list[tuple[int, int]] = []
                contiguous_tail: Optional[tuple[int, int]] = None
                if start_system is None:
                    smart_plan = self._plan_smart_galaxy_sweep_locked(
                        batch_size=total_pages,
                        galaxies=self._sweep_target_galaxies_locked(explicit_galaxy=galaxy),
                        collect_colonies=collect_colonies,
                        now_ts=sweep_started_ts,
                    )
                    seen_tg: set[tuple[int, int]] = set()
                    for sec in (smart_plan or {}).get("selected_sectors") or []:
                        if not isinstance(sec, Mapping):
                            continue
                        tg = (_safe_api_int(sec.get("galaxy"), 0), _safe_api_int(sec.get("system"), 0))
                        if 1 <= tg[0] <= max_g_start and 1 <= tg[1] <= 400 and tg not in seen_tg:
                            seen_tg.add(tg)
                            sweep_targets.append(tg)
                    sweep_targets = sweep_targets[:total_pages]
                selection_mode = "smart" if sweep_targets else "contiguous"
                if not sweep_targets:
                    full_seq = self._contiguous_sweep_targets(
                        cur_gal,
                        cur_sys,
                        total_pages + 1,
                        rotate_galaxies=rotate_gals,
                    )
                    sweep_targets = full_seq[:total_pages]
                    contiguous_tail = full_seq[total_pages] if len(full_seq) > total_pages else None
                    if smart_plan is None:
                        # Coverage/phase telemetry only (targets stay contiguous)
                        smart_plan = self._plan_smart_galaxy_sweep_locked(
                            batch_size=1,
                            galaxies=[max(1, min(max_g_start, int(cur_gal)))] if galaxy is not None else owned_gals,
                            collect_colonies=collect_colonies,
                            now_ts=sweep_started_ts,
                        )
                    contiguous_samples = [
                        {
                            "sector": f"{tg:02d}:{ts_c:03d}",
                            "galaxy": tg,
                            "system": ts_c,
                            "phase": "contiguous",
                            "score": 0.0,
                            "distance": 0,
                            "direct_raid_count": 0,
                            "owner_affinity_raid_count": 0,
                            "reason": "explicit start_system contiguous walk",
                        }
                        for tg, ts_c in sweep_targets[:5]
                    ]
                    smart_summary = self._store_smart_sweep_summary_locked(
                        smart_plan,
                        selection_mode="contiguous",
                        now_ts=sweep_started_ts,
                        sample_targets=contiguous_samples,
                    )
                else:
                    smart_summary = self._store_smart_sweep_summary_locked(
                        smart_plan,
                        selection_mode="smart",
                        now_ts=sweep_started_ts,
                    )
                cur_gal, cur_sys = sweep_targets[0]
                planned_pages = len(sweep_targets)
                scan_phase = str(smart_summary.get("scan_phase") or "exploratory")
                known_scanned_keys: set[str] = {
                    str(k)
                    for k, ts_v in (self._get_scanned_systems() or {}).items()
                    if _safe_api_float(ts_v, 0.0) > 0.0
                }
                scan_gal_set: set[int] = {
                    _safe_api_int(g, 0)
                    for g in (smart_summary.get("scan_galaxies") or owned_gals)
                    if _safe_api_int(g, 0) > 0
                }

            if dry_run:
                if contiguous_tail is not None:
                    end_gal, end_sys = contiguous_tail
                else:
                    end_gal, end_sys = sweep_targets[-1]
                with self._lock:
                    dry_state = self._get_galaxy_sweep_state_locked(now_ts=sweep_started_ts, include_smart=False)
                return _sanitize_finite_json(
                    _redact_api_payload(
                        {
                            "ok": True,
                            "command": "galaxy_sweep",
                            "executed": False,
                            "dry_run": True,
                            "pages_planned": planned_pages,
                            "start_galaxy": cur_gal,
                            "start_system": cur_sys,
                            "end_galaxy": end_gal,
                            "end_system": end_sys,
                            "selection_mode": selection_mode,
                            "scan_phase": scan_phase,
                            "explored_systems": smart_summary.get("explored_systems", 0),
                            "unscanned_systems": smart_summary.get("unscanned_systems", 0),
                            "total_systems": smart_summary.get("total_systems", 0),
                            "coverage_pct": smart_summary.get("coverage_pct", 0.0),
                            "exploratory_count": _safe_api_int((smart_plan or {}).get("exploratory_count"), 0)
                            if selection_mode == "smart"
                            else 0,
                            "refresh_count": _safe_api_int((smart_plan or {}).get("refresh_count"), 0)
                            if selection_mode == "smart"
                            else 0,
                            "sample_targets": smart_summary.get("sample_targets", []),
                            "planned_targets": [f"{g:02d}:{s:03d}" for g, s in sweep_targets[:400]],
                            "collect_colonies": collect_colonies,
                            "allocated_normal_planets": allocated_normal,
                            "max_planets": max_planets_cap,
                            "click_delay_seconds": cfg_delay_s,
                            "click_jitter_seconds": cfg_jitter_s,
                            "galaxy_sweep": dry_state,
                            "message": (
                                f"[DRY-RUN] Would sweep {planned_pages} galaxy pages "
                                f"({selection_mode} {scan_phase}, coverage {smart_summary.get('coverage_pct', 0.0)}%) "
                                f"starting at [{cur_gal:02d}:{cur_sys:03d}] "
                                f"(colonies={'ON' if collect_colonies else 'FULL'})."
                            ),
                        }
                    )
                )

            lo_d = max(0.0, float(min(delay_range[0], delay_range[1]))) if delay_range is not None else 0.0
            hi_d = max(lo_d, float(max(delay_range[0], delay_range[1]))) if delay_range is not None else 0.0
            has_pacing = (delay_range is None) or (hi_d > 0.0)
            prev_browser_coords: Optional[tuple[int, int]] = getattr(self, "_last_galaxy_browser_coords", None)

            def _record_sweep_page_locked(
                page_html: str,
                page_gal: int,
                page_sys: int,
                *,
                collect_col_flag: bool,
                count_as_planned: bool,
            ) -> None:
                nonlocal pages_scanned, players_found, debris_found, colonies_found, prev_browser_coords
                self._sweep_xhr_timestamps.append(time.monotonic())
                self._last_galaxy_browser_coords = (page_gal, page_sys)
                prev_browser_coords = (page_gal, page_sys)
                slots = parse_galaxy_system_html(page_html, galaxy=page_gal, system=page_sys)
                players_on_page = sum(
                    1
                    for s in slots
                    if s.status_color in ("yellow", "light_red") or s.is_spacia
                )
                debris_on_page = sum(1 for s in slots if bool(getattr(s, "has_debris", False)))
                free_on_page = sum(1 for s in slots if s.status_color == "free")

                slots_for_scan = (
                    slots
                    if collect_col_flag
                    else [
                        s
                        for s in slots
                        if s.status_color != "free"
                        or bool(getattr(s, "has_debris", False))
                        or bool(getattr(s, "is_spacia", False))
                    ]
                )
                self._persist_galaxy_scan(page_gal, page_sys, slots_for_scan)
                self._persist_galaxy_slots(slots_for_scan, refresh_targets=False)

                if count_as_planned:
                    pages_scanned += 1
                players_found += players_on_page
                debris_found += debris_on_page
                if collect_col_flag:
                    colonies_found += free_on_page

                skey_now = f"{page_gal:02d}:{page_sys:03d}"
                if skey_now not in known_scanned_keys:
                    known_scanned_keys.add(skey_now)
                    sm_live = getattr(self, "_smart_sweep_summary", None)
                    if isinstance(sm_live, dict):
                        uni_tot_l = max(1, _safe_api_int(sm_live.get("universe_total_systems"), self._max_galaxies_locked() * 400))
                        uni_exp_l = min(
                            uni_tot_l,
                            max(0, _safe_api_int(sm_live.get("universe_explored_systems"), 0)) + 1,
                        )
                        sm_live["universe_explored_systems"] = uni_exp_l
                        sm_live["universe_coverage_pct"] = round(min(100.0, (uni_exp_l / uni_tot_l) * 100.0), 2)
                        if page_gal in scan_gal_set:
                            gal_tot_l = max(1, _safe_api_int(sm_live.get("total_systems"), 400))
                            gal_exp_l = min(
                                gal_tot_l,
                                max(0, _safe_api_int(sm_live.get("explored_systems"), 0)) + 1,
                            )
                            gal_un_l = max(0, gal_tot_l - gal_exp_l)
                            sm_live["explored_systems"] = gal_exp_l
                            sm_live["unscanned_systems"] = gal_un_l
                            sm_live["coverage_pct"] = round(min(100.0, (gal_exp_l / gal_tot_l) * 100.0), 2)
                            if gal_un_l == 0:
                                sm_live["scan_phase"] = "targeted_refresh"
                        cur_samples = sm_live.get("sample_targets")
                        if isinstance(cur_samples, list) and cur_samples:
                            sm_live["sample_targets"] = [
                                s_it for s_it in cur_samples if str((s_it or {}).get("sector") or "") != skey_now
                            ]
                        sm_live["smart_summary_ts"] = round(time.time(), 3)
                        self._smart_sweep_summary_ts = time.time()
                    self._invalidate_dashboard_cache()

            def _wait_sweep_pacing(duration_s: float) -> bool:
                rem = max(0.0, float(duration_s))
                if rem <= 0.0:
                    return False
                if (
                    self._sweep_stop_event.is_set()
                    or self._sweep_preempt_event.is_set()
                    or bool(self._bot_control.get("captcha_blocked"))
                    or (bool(self._bot_control.get("paused")) and priority != "interactive")
                    or (
                        enforce_scan_toggle
                        and not bool(getattr(self.strategy_config, "allow_galaxy_scan", False))
                    )
                ):
                    return True
                stop_ev = self._sweep_stop_event
                if "wait" in getattr(stop_ev, "__dict__", {}):
                    return bool(stop_ev.wait(rem))
                if self._sweep_wake_event.wait(rem):
                    return True
                return bool(stop_ev.is_set())

            for step_idx, (cur_gal, cur_sys) in enumerate(sweep_targets):
                if self._sweep_stop_event.is_set():
                    aborted_reason = "Sweep stopped on shutdown."
                    break
                if self._sweep_preempt_event.is_set():
                    aborted_reason = str(
                        self._sweep_preempt_reason or "Preempted by P0 emergency Escape Flight."
                    )
                    break

                if step_idx > 0 and has_pacing:
                    if delay_range is not None:
                        wait_jitter = random.uniform(lo_d, hi_d)
                    else:
                        cfg_step = self.strategy_config
                        base_delay = max(
                            0.2,
                            min(60.0, _safe_api_float(getattr(cfg_step, "galaxy_scan_click_delay_seconds", 1.5), 1.5)),
                        )
                        jitter = max(
                            0.0,
                            min(30.0, _safe_api_float(getattr(cfg_step, "galaxy_scan_click_jitter_seconds", 0.6), 0.6)),
                        )
                        wait_jitter = max(
                            0.2,
                            base_delay + (random.uniform(-jitter, jitter) if jitter > 0.0 else 0.0),
                        )
                    if wait_jitter > 0.0 and _wait_sweep_pacing(wait_jitter):
                        if self._sweep_stop_event.is_set():
                            aborted_reason = "Sweep stopped on shutdown."
                            break

                needed_xhr = 1
                if (
                    prev_browser_coords is not None
                    and prev_browser_coords[0] == cur_gal
                    and 1 <= prev_browser_coords[1] <= 400
                    and 1 <= cur_sys <= 400
                ):
                    delta_est = abs(cur_sys - prev_browser_coords[1])
                    circ_est = min(delta_est, 400 - delta_est)
                    if 1 <= circ_est < ARROW_NAV_MAX_DISTANCE:
                        needed_xhr = circ_est

                if has_pacing:
                    budget_deadline = time.monotonic() + 75.0
                    while not self._sweep_stop_event.is_set() and time.monotonic() < budget_deadline:
                        if self._sweep_preempt_event.is_set():
                            break
                        if bool(self._bot_control.get("captcha_blocked")) or (
                            bool(self._bot_control.get("paused")) and priority != "interactive"
                        ):
                            break
                        if enforce_scan_toggle and not bool(
                            getattr(self.strategy_config, "allow_galaxy_scan", False)
                        ):
                            break
                        rem_rl = self.rate_limit_remaining()
                        total_60s = self._page_limiter.views_last_60s() + self._sweep_views_last_60s()
                        sweep_cap = max(6, int(self._page_limiter.max_views_per_minute))
                        if rem_rl <= 0.0 and (total_60s + needed_xhr) <= sweep_cap:
                            break
                        sleep_s = max(1.0, min(5.0, rem_rl if rem_rl > 0.0 else random.uniform(1.1, 2.2)))
                        if _wait_sweep_pacing(sleep_s):
                            break

                with self._lock:
                    if self._sweep_preempt_event.is_set():
                        aborted_reason = str(
                            self._sweep_preempt_reason or "Preempted by P0 emergency Escape Flight."
                        )
                        break
                    p0_urg_step, p0_rsn_step = self._has_urgent_p0_escape_flight_locked()
                    if p0_urg_step:
                        aborted_reason = p0_rsn_step
                        break
                    if self._bot_control.get("captcha_blocked"):
                        aborted_reason = str(
                            self._bot_control.get("captcha_message") or "CAPTCHA active"
                        )
                        break
                    if self._bot_control.get("paused") and priority != "interactive":
                        aborted_reason = "Bot paused via operator UI."
                        break
                    if enforce_scan_toggle and not bool(
                        getattr(self.strategy_config, "allow_galaxy_scan", False)
                    ):
                        aborted_reason = "Galaxy scans suspended via operator UI."
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
                                step_kwargs: dict[str, Any] = {
                                    "galaxy": cur_gal,
                                    "system": cur_sys,
                                    "pace": False,
                                    "priority": priority,
                                }
                                try:
                                    step_sig = inspect.signature(client.step_galaxy_system)
                                    accepts_inter = any(
                                        p.name == "on_intermediate_page"
                                        or p.kind == inspect.Parameter.VAR_KEYWORD
                                        for p in step_sig.parameters.values()
                                    )
                                except Exception:
                                    accepts_inter = False
                                if accepts_inter:
                                    step_kwargs["on_intermediate_page"] = (
                                        lambda inter_html, ig, isys, _cc=collect_colonies: _record_sweep_page_locked(
                                            inter_html,
                                            ig,
                                            isys,
                                            collect_col_flag=_cc,
                                            count_as_planned=False,
                                        )
                                    )
                                html, actual_gal, actual_sys = client.step_galaxy_system(**step_kwargs)
                                if getattr(client, "last_galaxy_nav_method", "") == "arrow":
                                    arrow_steps_used += 1
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

                    eff_max_g = self._observe_galaxy_universe_bounds_locked(client=client, html=html)
                    _record_sweep_page_locked(
                        html,
                        actual_gal,
                        actual_sys,
                        collect_col_flag=collect_colonies,
                        count_as_planned=True,
                    )
                    if cur_gal > eff_max_g:
                        aborted_reason = (
                            f"Observed universe maxGalaxies={eff_max_g} (target galaxy {cur_gal} out of bounds); "
                            "re-anchoring sweep."
                        )
                        break

            # Next cursor: contiguous mode continues after the last scanned page; smart mode records the system
            # after the last visited sector (clamped to observed universe maxGalaxies).
            eff_max_g_end = self._max_galaxies_locked()
            if selection_mode == "contiguous":
                if pages_scanned < len(sweep_targets):
                    cur_gal, cur_sys = sweep_targets[pages_scanned]
                elif contiguous_tail is not None:
                    cur_gal, cur_sys = contiguous_tail
            elif prev_browser_coords is not None and pages_scanned > 0:
                last_g, last_s = prev_browser_coords
                cur_gal = max(1, min(eff_max_g_end, int(last_g)))
                cur_sys = 1 if int(last_s) >= 400 else max(1, int(last_s) + 1)
            elif pages_scanned > 0:
                last_g, last_s = sweep_targets[pages_scanned - 1]
                cur_gal = max(1, min(eff_max_g_end, int(last_g)))
                cur_sys = 1 if int(last_s) >= 400 else max(1, int(last_s) + 1)
            else:
                cur_gal, cur_sys = sweep_targets[0]
            cur_gal = max(1, min(eff_max_g_end, int(cur_gal)))
            cur_sys = max(1, min(400, int(cur_sys)))

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
                cur_prev = self._get_galaxy_sweep_state_locked(now_ts=now_done, include_smart=False)
                updated_sw = self._save_galaxy_sweep_state_locked(
                    {
                        "cursor_galaxy": cur_gal,
                        "cursor_system": cur_sys,
                        "last_sweep_started_ts": sweep_started_ts,
                        "last_sweep_completed_ts": now_done,
                        "next_sweep_due_ts": round(next_due, 3),
                        "last_pages_scanned": pages_scanned,
                        "last_arrow_steps": arrow_steps_used,
                        "last_players_found": players_found,
                        "last_colonies_found": colonies_found,
                        "last_debris_found": debris_found,
                        "total_sweeps": int(cur_prev.get("total_sweeps") or 0) + (1 if pages_scanned > 0 else 0),
                        "total_pages_scanned": int(cur_prev.get("total_pages_scanned") or 0) + pages_scanned,
                    },
                    ts=now_done,
                )
                if pages_scanned > 0:
                    self._refresh_raid_targets_locked(limit=250)
                    # Refresh coverage + next-batch preview (phase may flip exploratory -> targeted_refresh)
                    self._refresh_smart_sweep_summary_locked(now_ts=now_done, force=True)
                    updated_sw = self._merge_smart_sweep_summary_locked(
                        {k: v for k, v in updated_sw.items() if k not in self._SMART_SWEEP_STATE_KEYS}
                    )
                self._invalidate_dashboard_cache()
                cov_txt = f"{updated_sw.get('coverage_pct', 0.0)}%"
                msg_summary = (
                    f"Swept {pages_scanned}/{planned_pages} galaxy pages "
                    f"[{selection_mode} {scan_phase}, coverage {cov_txt}] "
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
                            reason=(
                                f"Smart 2-phase galaxy sweep ({scan_phase}, coverage {cov_txt})"
                                if selection_mode == "smart"
                                else "Periodic randomized 100-page galaxy sweep"
                            ),
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
                        "pages_planned": planned_pages,
                        "pages_scanned": pages_scanned,
                        "arrow_steps": arrow_steps_used,
                        "players_found": players_found,
                        "colonies_found": colonies_found,
                        "debris_found": debris_found,
                        "collect_colonies": collect_colonies,
                        "selection_mode": selection_mode,
                        "scan_phase": scan_phase,
                        "explored_systems": updated_sw.get("explored_systems", 0),
                        "total_systems": updated_sw.get("total_systems", 0),
                        "coverage_pct": updated_sw.get("coverage_pct", 0.0),
                        "sample_targets": smart_summary.get("sample_targets", []),
                        "visited_targets": [f"{g:02d}:{s:03d}" for g, s in sweep_targets[:pages_scanned]],
                        "click_delay_seconds": updated_sw.get("click_delay_seconds", cfg_delay_s),
                        "click_jitter_seconds": updated_sw.get("click_jitter_seconds", cfg_jitter_s),
                        "aborted_reason": aborted_reason,
                        "message": msg_summary,
                        "galaxy_sweep": updated_sw,
                    }
                )
            )
        finally:
            with self._lock:
                self._sweep_in_progress = False
                self._sweep_done_event.set()
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
                p0_urg_wk, _ = self._has_urgent_p0_escape_flight_locked()
                cmd_idle_ok = (time.monotonic() - self._last_live_command_mono) >= 3.0
                if (
                    bool(getattr(cfg, "allow_galaxy_scan", False))
                    and not paused
                    and not cap_blk
                    and not is_dry
                    and not p0_urg_wk
                    and cmd_idle_ok
                    and bool(sw.get("due", True))
                    and self.rate_limit_remaining() <= 0.0
                    and self._is_browser_port_open()
                ):
                    should_run = True
            if should_run:
                try:
                    self.run_galaxy_sweep(priority="normal", automatic=True)
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
        wake_ev = getattr(self, "_sweep_wake_event", None)
        if wake_ev is not None:
            wake_ev.set()
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
            if any(getattr(rep, "researches", None) for rep in filtered) and hasattr(
                self._db, "refresh_universe_research_max"
            ):
                self._db.refresh_universe_research_max(
                    own_researches=(self._last_state.researches if self._last_state is not None else None),
                    own_nick=(self._last_state.nickname if self._last_state is not None else ""),
                    source="spy_report",
                    ts=now_ts,
                )
            self._refresh_raid_targets_locked(limit=100)
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
        non_spy_msg_ids = {
            m.server_msg_id
            for m in messages
            if m.server_msg_id and m.category != "spy_report"
        }
        f_low_ing = (folder_type or "").strip().lower()
        page_spy_reports = (
            [
                rep
                for rep in parse_spy_messages_html(html, exclude_coords=excl)
                if not (rep.server_msg_id and rep.server_msg_id in non_spy_msg_ids)
            ]
            if f_low_ing in ("", "spio", "inbox")
            else []
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
            if (
                not any(m.spy_report is not None for m in messages)
                and page_spy_reports
                and not any(
                    m.category in ("fleet_return", "battle_report", "recycle_report", "fleet_transport", "colonize_report", "counter_spy")
                    for m in messages
                )
            ):
                reports_to_persist = page_spy_reports
        else:
            reports_to_persist = page_spy_reports

        if reports_to_persist:
            self._persist_spy_reports(reports_to_persist, exclude_coords=excl)

        debris_changed = False
        if self._db is not None:
            for msg in newly_inserted:
                mc = normalize_coords(msg.coords or "")
                if not mc:
                    continue
                if msg.category == "battle_report" and bool((msg.parsed_data or {}).get("has_debris")):
                    if mc not in excl:
                        self._db.set_target_debris(mc, True)
                        debris_changed = True
                elif msg.category == "recycle_report":
                    self._db.set_target_debris(mc, False)
                    self._galaxy_debris_amounts.pop(mc, None)
                    debris_changed = True
            if debris_changed:
                self._refresh_raid_targets_locked(limit=100)

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

    # ------------------------------------------------------------------
    # Colony trial ledger (`colony_attempts`, V011) — every call is guarded
    # so a missing table/method never breaks the API.
    # ------------------------------------------------------------------
    COLONY_FAILED_DEDUPE_S = 600.0

    def _colony_min_fields(self) -> int:
        return max(50, _safe_api_int(getattr(self.strategy_config, "min_colony_fields", 300), 300))

    def _safe_record_colony_attempt(self, **kwargs: Any) -> Optional[dict[str, Any]]:
        db = self._db
        rec_fn = getattr(db, "record_colony_attempt", None) if db is not None else None
        if not callable(rec_fn):
            return None
        try:
            out = rec_fn(**kwargs)
        except Exception:  # noqa: BLE001 - ledger must never crash the API
            pass
            return None
        return out if isinstance(out, dict) else None

    def _colony_attempts_payload(self) -> dict[str, Any]:
        """Return `colony_attempts`, `colony_attempts_summary`, `attempts_by_coords` (empty when unavailable)."""
        attempts: list[dict[str, Any]] = []
        summary: dict[str, Any] = {
            "total_events": 0,
            "unique_coords_tried": 0,
            "dispatched_count": 0,
            "deleted_undersized_count": 0,
            "kept_count": 0,
            "failed_count": 0,
            "by_coords": {},
        }
        db = self._db
        if db is not None:
            get_fn = getattr(db, "get_colony_attempts", None)
            if callable(get_fn):
                try:
                    raw_rows = get_fn(limit=100)
                    attempts = [dict(r) for r in (raw_rows or ()) if isinstance(r, Mapping)]
                except Exception:  # noqa: BLE001
                    pass
            sum_fn = getattr(db, "get_colony_attempts_summary", None)
            if callable(sum_fn):
                try:
                    raw_sum = sum_fn()
                    if isinstance(raw_sum, Mapping):
                        summary = dict(raw_sum)
                except Exception:  # noqa: BLE001
                    pass
        by_coords = summary.get("by_coords")
        if not isinstance(by_coords, Mapping):
            by_coords = {}
            summary["by_coords"] = by_coords
        return {
            "colony_attempts": attempts,
            "colony_attempts_summary": summary,
            "attempts_by_coords": dict(by_coords),
        }

    def _hydrate_colony_attempt_seen(self) -> None:
        db = self._db
        get_fn = getattr(db, "get_colony_attempts", None) if db is not None else None
        if not callable(get_fn):
            return
        try:
            rows = get_fn(limit=500) or ()
        except Exception:  # noqa: BLE001
            return
        for r in rows:
            if not isinstance(r, Mapping):
                continue
            if str(r.get("event_type") or "").lower() not in ("kept", "settled"):
                continue
            pid_h = _safe_api_int(r.get("planet_id"), 0)
            nc_h = normalize_coords(str(r.get("target_coords") or ""))
            if pid_h > 0 and nc_h:
                self._colony_attempt_seen.add((pid_h, nc_h))

    def _lookup_colony_candidate(self, norm_target: str, origin_coords: str) -> Optional[dict[str, Any]]:
        db = self._db
        get_fn = getattr(db, "get_colony_candidates", None) if db is not None else None
        if not callable(get_fn) or not norm_target:
            return None
        try:
            rows = get_fn(
                origin_coords=origin_coords,
                min_fields_target=self._colony_min_fields(),
                include_discarded=True,
                limit=1000,
            )
        except Exception:  # noqa: BLE001
            return None
        for r in rows or ():
            if isinstance(r, Mapping) and normalize_coords(str(r.get("coords") or "")) == norm_target:
                return dict(r)
        return None

    def _record_colony_attempt_from_result_locked(self, state: GameState, res: Any, cmd: str) -> None:
        """Append `dispatched`/`failed` (COLONIZE) or `deleted_undersized` (ABANDON_COLONY) ledger rows."""
        act = getattr(res, "action", None)
        kind = getattr(act, "kind", None)
        if kind not in ("COLONIZE", "ABANDON_COLONY") or self._db is None:
            return
        executed = bool(getattr(res, "executed", False))
        msg = _bounded_api_str(getattr(res, "message", None), default="")
        min_f = self._colony_min_fields()
        origin_pid = _safe_api_int(getattr(act, "planet_id", 0), 0)
        if kind == "ABANDON_COLONY":
            if not executed:
                return
            planet = next((p for p in state.planets if p.planet_id == origin_pid), None)
            coords_ab = normalize_coords(
                str(getattr(planet, "coords", "") or getattr(act, "planet_coords", "") or "")
            )
            if not coords_ab:
                return
            min_ab = _safe_api_int(getattr(act, "target_level", None), 0)
            self._safe_record_colony_attempt(
                target_coords=coords_ab,
                event_type="deleted_undersized",
                planet_id=origin_pid if origin_pid > 0 else None,
                fields_rolled=(_safe_api_int(planet.fields_max, 0) if planet is not None else None),
                min_fields_target=min_ab if min_ab >= 50 else min_f,
                temperature=(_safe_api_int(planet.temperature, 0) if planet is not None else None),
                status_message=msg,
                details={"source": cmd},
            )
            return
        # COLONIZE
        tname = game_data.safe_key(act.target_name) if getattr(act, "target_name", None) is not None else ""
        m_tc = re.search(r"(\d{1,2}:\d{1,3}:\d{1,2})", tname, re.ASCII)
        norm_target = normalize_coords(m_tc.group(1)) if m_tc else ""
        if not norm_target:
            return
        origin_coords = normalize_coords(str(getattr(act, "planet_coords", "") or ""))
        if not executed:
            if not bool(getattr(act, "executable_now", False)) or msg.lower().startswith("guarded"):
                return
            now_wall = time.time()
            prev_fail = self._colony_attempt_failed_recent.get(norm_target)
            if prev_fail is not None and prev_fail[0] == msg and (now_wall - prev_fail[1]) < self.COLONY_FAILED_DEDUPE_S:
                return
            self._colony_attempt_failed_recent[norm_target] = (msg, now_wall)
            self._safe_record_colony_attempt(
                target_coords=norm_target,
                event_type="failed",
                origin_coords=origin_coords,
                min_fields_target=min_f,
                status_message=msg or "COLONIZE dispatch failed",
                details={"source": cmd, "origin_planet_id": origin_pid},
            )
            return
        self._colony_attempt_failed_recent.pop(norm_target, None)
        cand = self._lookup_colony_candidate(norm_target, origin_coords) or {}
        exp_max_raw = (
            cand.get("colonize_max_fields")
            if cand.get("colonize_max_fields") is not None
            else cand.get("uninhabited_fields")
        )
        self._safe_record_colony_attempt(
            target_coords=norm_target,
            event_type="dispatched",
            origin_coords=origin_coords,
            min_fields_target=min_f,
            expected_min_fields=(
                _safe_api_int(cand.get("colonize_min_fields"), 0)
                if cand.get("colonize_min_fields") is not None
                else None
            ),
            expected_max_fields=_safe_api_int(exp_max_raw, 0) if exp_max_raw is not None else None,
            temperature=(
                _safe_api_int(cand.get("temperature"), 0) if cand.get("temperature") is not None else None
            ),
            grade=str(cand.get("grade") or ""),
            score=_safe_api_int(cand.get("score"), 0) if cand.get("score") is not None else None,
            status_message=msg,
            details={"source": cmd, "origin_planet_id": origin_pid},
        )

    def _record_new_colonies_locked(self, prev: Optional[GameState], new_state: GameState) -> None:
        """Append `kept`/`settled` rows when a new secondary normal planet appears (deduped per planet)."""
        if prev is None or self._db is None:
            return
        try:
            prev_ids = {p.planet_id for p in prev.planets}
            hw_id = homeworld_planet_id(new_state)
            min_f = self._colony_min_fields()
            for p in new_state.planets:
                pid_n = _safe_api_int(p.planet_id, 0)
                if pid_n <= 0 or pid_n == hw_id or normalize_planet_type(p) != "normal":
                    continue
                if pid_n in prev_ids and pid_n not in self._colony_attempt_pending_pids:
                    continue
                nc_n = normalize_coords(str(p.coords or ""))
                if not nc_n:
                    continue
                key = (pid_n, nc_n)
                if key in self._colony_attempt_seen:
                    self._colony_attempt_pending_pids.discard(pid_n)
                    continue
                f_max = _safe_api_int(p.fields_max, 0)
                if f_max <= 0:
                    # Field count not parsed yet; retry on the next snapshot.
                    self._colony_attempt_pending_pids.add(pid_n)
                    continue
                self._colony_attempt_pending_pids.discard(pid_n)
                self._colony_attempt_seen.add(key)
                self._safe_record_colony_attempt(
                    target_coords=nc_n,
                    event_type="kept" if f_max >= min_f else "settled",
                    planet_id=pid_n,
                    fields_rolled=f_max,
                    min_fields_target=min_f,
                    temperature=_safe_api_int(p.temperature, 0),
                    details={"source": "state", "planet_name": _bounded_api_str(p.name, default="")},
                )
        except Exception:  # noqa: BLE001 - never break state recording
            pass

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

        # Pre-dispatch safety verification for SPY when `spy_reserved_slots_only` is enabled:
        # Never allow SPY dispatch (step, batch, execute_action, manual spy, or fleet_execute)
        # to exceed `min(max_slots, reserved_spy_slots)` concurrent in-flight SPY flights on the planet.
        if (
            not dry_run
            and planned.kind == "SPY"
            and bool(getattr(self.strategy_config, "spy_reserved_slots_only", False))
        ):
            spy_planet = (
                next((p for p in (state.planets or ()) if p.planet_id == planned.planet_id), None)
                or state.active_planet()
            )
            if spy_planet is not None:
                max_spy_sl = self._max_fleet_slots_for_planet_locked(spy_planet)
                spy_cap = min(
                    max_spy_sl,
                    max(0, _safe_api_int(getattr(self.strategy_config, "reserved_spy_slots", 1), 1)),
                )
                spy_used = count_in_flight_spy_fleets_for_planet(state, spy_planet)
                if spy_used >= spy_cap:
                    abort_msg = (
                        f"Aborted: Spy restricted to reserved slot(s) "
                        f"({spy_used}/{spy_cap} spy slots in use on [{spy_planet.coords}])."
                    )
                    aborted_act = replace(planned, executable_now=False)
                    if self._db is not None:
                        self._db.record_action(
                            source="api",
                            command=cmd,
                            action=aborted_act,
                            executed=False,
                            dry_run=False,
                            message=abort_msg,
                        )
                    return _sanitize_finite_json(
                        _redact_api_payload(
                            {
                                "ok": True,
                                "command": cmd,
                                "executed": False,
                                "validated": True,
                                "dry_run": False,
                                "message": abort_msg,
                                "action": self._serialize_action(aborted_act, state),
                                "next_action": None,
                            }
                        )
                    )

        # Pre-dispatch safety verification for Spacia SPY / ATTACK (matching RECYCLE):
        # Temporary Spacia NPC planets despawn or get settled by real players between galaxy scans.
        # Open the target solar system in live `internalGalaxy` right before dispatching so we:
        # 1) Persist fresh slot/debris intel for all 16 positions in that system, and
        # 2) Confirm the target coordinate is STILL a live Spacia planet before touching `internalFleet`.
        if not dry_run and planned.kind in ("SPY", "ATTACK") and client is not None:
            sp_tname = game_data.safe_key(planned.target_name) if planned.target_name is not None else ""
            sp_reason = game_data.safe_key(planned.reason) if planned.reason is not None else ""
            is_free_probe_act = "free slot" in sp_tname.lower() or "free slot" in sp_reason.lower()
            m_sp_pre = re.search(r"(\d{1,2}):(\d{1,3}):(\d{1,2})", sp_tname, re.ASCII)
            if m_sp_pre and not is_free_probe_act:
                sp_gal = int(m_sp_pre.group(1))
                sp_sys = int(m_sp_pre.group(2))
                sp_pos = int(m_sp_pre.group(3))
                norm_sp_target = f"{sp_gal:02d}:{sp_sys:03d}:{sp_pos:02d}"
                is_expected_spacia = (
                    "spacia" in sp_tname.lower()
                    or "spacia" in sp_reason.lower()
                )
                if not is_expected_spacia:
                    for rt_row in (self._raid_targets or ()):
                        if normalize_coords(rt_row.get("coords") or rt_row.get("target_coords") or "") == norm_sp_target:
                            if bool(rt_row.get("is_spacia")) or str(rt_row.get("owner") or "").strip().lower() == "spacia":
                                is_expected_spacia = True
                            break
                if not is_expected_spacia and self._db is not None:
                    get_by_c = getattr(self._db, "get_spy_target_by_coords", None)
                    if callable(get_by_c):
                        st_row = get_by_c(norm_sp_target)
                        if isinstance(st_row, Mapping) and (
                            bool(st_row.get("is_spacia"))
                            or str(st_row.get("owner") or "").strip().lower() == "spacia"
                        ):
                            is_expected_spacia = True
                    if not is_expected_spacia:
                        get_gs = getattr(self._db, "get_galaxy_slots", None)
                        if callable(get_gs):
                            for gs_row in get_gs(galaxy=sp_gal, system=sp_sys, limit=20):
                                if normalize_coords(gs_row.get("coords") or "") == norm_sp_target:
                                    if bool(gs_row.get("is_spacia")) or str(gs_row.get("owner") or "").strip().lower() == "spacia":
                                        is_expected_spacia = True
                                    break
                if is_expected_spacia and self._is_real_client_method(client, "open_galaxy"):
                    sp_origin = (
                        next((p for p in (state.planets or ()) if p.planet_id == planned.planet_id), None)
                        or state.active_planet()
                    )
                    if sp_origin is not None:
                        if planned.kind == "SPY" and _safe_api_int(sp_origin.ships.get("s10", 0), 0) <= 0:
                            abort_msg = f"Aborted: no spy probes (s10) stationed on [{sp_origin.coords}]."
                            aborted_act = replace(planned, executable_now=False)
                            if self._db is not None:
                                self._db.record_action(
                                    source="api",
                                    command=cmd,
                                    action=aborted_act,
                                    executed=False,
                                    dry_run=False,
                                    message=abort_msg,
                                )
                            return _sanitize_finite_json(
                                _redact_api_payload(
                                    {
                                        "ok": True,
                                        "command": cmd,
                                        "executed": False,
                                        "validated": True,
                                        "dry_run": False,
                                        "message": abort_msg,
                                        "action": self._serialize_action(aborted_act, state),
                                        "next_action": None,
                                    }
                                )
                            )
                        sp_max_slots = self._max_fleet_slots_for_planet_locked(sp_origin)
                        sp_used_slots = count_in_flight_fleets_for_planet(state, sp_origin)
                        sp_cd_rem = float(self._fleet_slot_cooldown_until.get(int(sp_origin.planet_id), 0.0)) - time.monotonic()
                        if sp_used_slots >= sp_max_slots or sp_cd_rem > 0.0:
                            abort_msg = (
                                f"Aborted: no free fleet slot on [{sp_origin.coords}] "
                                f"({sp_used_slots}/{sp_max_slots} in flight)."
                            )
                            aborted_act = replace(planned, executable_now=False)
                            if self._db is not None:
                                self._db.record_action(
                                    source="api",
                                    command=cmd,
                                    action=aborted_act,
                                    executed=False,
                                    dry_run=False,
                                    message=abort_msg,
                                )
                            return _sanitize_finite_json(
                                _redact_api_payload(
                                    {
                                        "ok": True,
                                        "command": cmd,
                                        "executed": False,
                                        "validated": True,
                                        "dry_run": False,
                                        "message": abort_msg,
                                        "action": self._serialize_action(aborted_act, state),
                                        "next_action": None,
                                    }
                                )
                            )
                    try:
                        sp_gal_html = self._open_galaxy(
                            client,
                            galaxy=sp_gal,
                            system=sp_sys,
                            priority="interactive",
                        )
                    except (RateLimitError, AntiBotChallengeError):
                        raise
                    except Exception:
                        sp_gal_html = ""
                    sp_live_slots = (
                        parse_galaxy_system_html(sp_gal_html, galaxy=sp_gal, system=sp_sys)
                        if isinstance(sp_gal_html, str) and sp_gal_html.strip()
                        else []
                    )
                    sp_live_slot = None
                    if sp_live_slots:
                        self._persist_galaxy_scan(sp_gal, sp_sys, sp_live_slots)
                        self._persist_galaxy_slots(sp_live_slots)
                        sp_live_slot = next(
                            (s for s in sp_live_slots if normalize_coords(s.coords) == norm_sp_target),
                            None,
                        )
                    if sp_live_slot is None:
                        abort_msg = (
                            f"Aborted: live galaxy view could not confirm Spacia at [{norm_sp_target}] "
                            f"before {planned.kind.lower()} dispatch."
                        )
                        print(redact_sensitive(f"[API] {abort_msg}"), flush=True)
                        aborted_act = replace(planned, executable_now=False)
                        if self._db is not None:
                            self._db.record_action(
                                source="api",
                                command=cmd,
                                action=aborted_act,
                                executed=False,
                                dry_run=False,
                                message=abort_msg,
                            )
                        return _sanitize_finite_json(
                            _redact_api_payload(
                                {
                                    "ok": True,
                                    "command": cmd,
                                    "executed": False,
                                    "validated": True,
                                    "dry_run": False,
                                    "message": abort_msg,
                                    "action": self._serialize_action(aborted_act, state),
                                    "next_action": None,
                                }
                            )
                        )
                    if not bool(getattr(sp_live_slot, "is_spacia", False)):
                        self._dash_ag_cache_json = None
                        live_col = str(getattr(sp_live_slot, "status_color", "") or "").strip().lower()
                        live_own = str(getattr(sp_live_slot, "owner", "") or "").strip()
                        if live_col == "free" and not live_own:
                            abort_msg = (
                                f"Aborted: live galaxy check confirmed [{norm_sp_target}] is now uninhabited "
                                "(Spacia despawned); cleared stale Spacia target."
                            )
                        else:
                            abort_msg = (
                                f"Aborted: live galaxy check confirmed [{norm_sp_target}] is no longer Spacia "
                                f"(now {live_own or 'occupied'} [{live_col or 'unknown'}]); updated galaxy intel."
                            )
                        print(redact_sensitive(f"[API] {abort_msg}"), flush=True)
                        aborted_act = replace(planned, executable_now=False)
                        if self._db is not None:
                            self._db.record_action(
                                source="api",
                                command=cmd,
                                action=aborted_act,
                                executed=False,
                                dry_run=False,
                                message=abort_msg,
                            )
                        return _sanitize_finite_json(
                            _redact_api_payload(
                                {
                                    "ok": True,
                                    "command": cmd,
                                    "executed": False,
                                    "validated": True,
                                    "dry_run": False,
                                    "message": abort_msg,
                                    "action": self._serialize_action(aborted_act, state),
                                    "next_action": None,
                                }
                            )
                        )

        # Pre-dispatch safety verification for RECYCLE:
        # 1) Never duplicate an already in-flight Recycle wave (auto-planned / step / execute_action).
        # 2) Always open the target system in live `internalGalaxy` right before dispatching so we
        #    confirm the debris field is actually present right now (SpaceInvasion RETRO accepts
        #    RECYCLE on 0-rubble slots without error, wasting hours of flight time).
        # 3) Re-size auto-planned Recycler quantity to exact live `rubblefield1` volume.
        if not dry_run and planned.kind == "RECYCLE" and client is not None:
            rec_origin = (
                next((p for p in (state.planets or ()) if p.planet_id == planned.planet_id), None)
                or state.active_planet()
            )
            if rec_origin is not None:
                rec_avail = _safe_api_int(rec_origin.ships.get("s11", 0), 0)
                if rec_avail <= 0:
                    abort_msg = f"Aborted: no recyclers (s11) stationed on [{rec_origin.coords}]."
                    aborted_act = replace(planned, executable_now=False)
                    if self._db is not None:
                        self._db.record_action(
                            source="api",
                            command=cmd,
                            action=aborted_act,
                            executed=False,
                            dry_run=False,
                            message=abort_msg,
                        )
                    return _sanitize_finite_json(
                        _redact_api_payload(
                            {
                                "ok": True,
                                "command": cmd,
                                "executed": False,
                                "validated": True,
                                "dry_run": False,
                                "message": abort_msg,
                                "action": self._serialize_action(aborted_act, state),
                                "next_action": None,
                            }
                        )
                    )
                rec_max_slots = self._max_fleet_slots_for_planet_locked(rec_origin)
                rec_used_slots = count_in_flight_fleets_for_planet(state, rec_origin)
                rec_cd_rem = float(self._fleet_slot_cooldown_until.get(int(rec_origin.planet_id), 0.0)) - time.monotonic()
                if rec_used_slots >= rec_max_slots or rec_cd_rem > 0.0:
                    abort_msg = (
                        f"Aborted: no free fleet slot on [{rec_origin.coords}] "
                        f"({rec_used_slots}/{rec_max_slots} in flight)."
                    )
                    aborted_act = replace(planned, executable_now=False)
                    if self._db is not None:
                        self._db.record_action(
                            source="api",
                            command=cmd,
                            action=aborted_act,
                            executed=False,
                            dry_run=False,
                            message=abort_msg,
                        )
                    return _sanitize_finite_json(
                        _redact_api_payload(
                            {
                                "ok": True,
                                "command": cmd,
                                "executed": False,
                                "validated": True,
                                "dry_run": False,
                                "message": abort_msg,
                                "action": self._serialize_action(aborted_act, state),
                                "next_action": None,
                            }
                        )
                    )
            tname_pre = game_data.safe_key(planned.target_name) if planned.target_name is not None else ""
            m_rc_pre = re.search(r"(\d{1,2}):(\d{1,3}):(\d{1,2})", tname_pre, re.ASCII)
            if m_rc_pre:
                t_gal = int(m_rc_pre.group(1))
                t_sys = int(m_rc_pre.group(2))
                t_pos = int(m_rc_pre.group(3))
                norm_rec_target = f"{t_gal:02d}:{t_sys:03d}:{t_pos:02d}"
                if cmd != "recycle" and norm_rec_target in extract_in_flight_recycle_coords(state):
                    abort_msg = f"Aborted: Recyclers are already in transit to [{norm_rec_target}]."
                    aborted_act = replace(planned, executable_now=False)
                    if self._db is not None:
                        self._db.record_action(
                            source="api",
                            command=cmd,
                            action=aborted_act,
                            executed=False,
                            dry_run=False,
                            message=abort_msg,
                        )
                    return _sanitize_finite_json(
                        _redact_api_payload(
                            {
                                "ok": True,
                                "command": cmd,
                                "executed": False,
                                "validated": True,
                                "dry_run": False,
                                "message": abort_msg,
                                "action": self._serialize_action(aborted_act, state),
                                "next_action": None,
                            }
                        )
                    )
                if self._is_real_client_method(client, "open_galaxy"):
                    try:
                        gal_html = self._open_galaxy(
                            client,
                            galaxy=t_gal,
                            system=t_sys,
                            priority="interactive",
                        )
                    except (RateLimitError, AntiBotChallengeError):
                        raise
                    except Exception:
                        gal_html = ""
                    live_slots = (
                        parse_galaxy_system_html(gal_html, galaxy=t_gal, system=t_sys)
                        if isinstance(gal_html, str) and gal_html.strip()
                        else []
                    )
                    live_slot = None
                    if live_slots:
                        self._persist_galaxy_scan(t_gal, t_sys, live_slots)
                        self._persist_galaxy_slots(live_slots)
                        live_slot = next(
                            (s for s in live_slots if normalize_coords(s.coords) == norm_rec_target),
                            None,
                        )
                    if live_slot is None:
                        abort_msg = (
                            f"Aborted: live galaxy view could not confirm debris at [{norm_rec_target}] "
                            "before dispatch."
                        )
                        aborted_act = replace(planned, executable_now=False)
                        if self._db is not None:
                            self._db.record_action(
                                source="api",
                                command=cmd,
                                action=aborted_act,
                                executed=False,
                                dry_run=False,
                                message=abort_msg,
                            )
                        return _sanitize_finite_json(
                            _redact_api_payload(
                                {
                                    "ok": True,
                                    "command": cmd,
                                    "executed": False,
                                    "validated": True,
                                    "dry_run": False,
                                    "message": abort_msg,
                                    "action": self._serialize_action(aborted_act, state),
                                    "next_action": None,
                                }
                            )
                        )
                    if not bool(live_slot.has_debris):
                        if self._db is not None:
                            self._db.set_target_debris(norm_rec_target, False)
                        self._galaxy_debris_amounts.pop(norm_rec_target, None)
                        self._refresh_raid_targets_locked(limit=100, state_override=state)
                        self._dash_ag_cache_json = None
                        abort_msg = (
                            f"Aborted: live galaxy check confirmed [{norm_rec_target}] is clean "
                            "(no debris field); cleared stale debris flag."
                        )
                        aborted_act = replace(planned, executable_now=False)
                        if self._db is not None:
                            self._db.record_action(
                                source="api",
                                command=cmd,
                                action=aborted_act,
                                executed=False,
                                dry_run=False,
                                message=abort_msg,
                            )
                        return _sanitize_finite_json(
                            _redact_api_payload(
                                {
                                    "ok": True,
                                    "command": cmd,
                                    "executed": False,
                                    "validated": True,
                                    "dry_run": False,
                                    "message": abort_msg,
                                    "action": self._serialize_action(aborted_act, state),
                                    "next_action": None,
                                }
                            )
                        )
                    if cmd != "recycle":
                        live_tot = (
                            max(0, int(getattr(live_slot, "debris_pig_iron", 0) or 0))
                            + max(0, int(getattr(live_slot, "debris_metal", 0) or 0))
                            + max(0, int(getattr(live_slot, "debris_kryptonite", 0) or 0))
                            + max(0, int(getattr(live_slot, "debris_spice", 0) or 0))
                        )
                        if live_tot > 0:
                            miner_lvl = _safe_api_int((getattr(state, "specs", None) or {}).get("miner", 0), 0)
                            per_rec_cap = max(1, fleet_cargo_capacity({"s11": 1}, miner_spec_level=miner_lvl))
                            needed_rec = max(1, math.ceil(live_tot / per_rec_cap))
                            cur_qty = _safe_api_int(planned.target_level, 0)
                            if cur_qty > needed_rec:
                                planned = replace(planned, target_level=needed_rec)

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
                db_checked_val = (
                    bool(tm_info.get("daily_bonus_checked", True))
                    if isinstance(tm_info, Mapping)
                    else True
                )
                db_collected_val = (
                    bool(tm_info.get("daily_bonus_collected", False))
                    if isinstance(tm_info, Mapping)
                    else False
                )
                if res.executed:
                    self._record_transmitter_state_locked(
                        activated=True,
                        free_spins_remaining=free_rem_val,
                        reward=rew_val,
                        daily_bonus_checked=db_checked_val,
                        daily_bonus_collected=db_collected_val,
                        daily_bonus_available=False,
                    )
                elif (
                    isinstance(tm_info, Mapping)
                    and _safe_api_int(tm_info.get("free_spins"), 1) <= 0
                    and bool(getattr(res, "validated", True))
                ):
                    self._record_transmitter_state_locked(
                        activated=False,
                        free_spins_remaining=0,
                        daily_bonus_checked=db_checked_val,
                        daily_bonus_collected=db_collected_val,
                        daily_bonus_available=False,
                    )
            if res.executed and res.action.kind == "THROTTLE_BUILDING":
                pid_i = game_data.cap_json_int(_safe_api_int(res.action.planet_id, 0))
                now_th_wall = time.time()
                self._throttle_timestamps[pid_i] = now_th_wall
                if pid_i > 0 and _safe_api_int(res.action.target_id, 0) == 2:
                    pct_set = _safe_api_int(res.action.target_level, 0)
                    rsn_th = _bounded_api_str(res.action.reason, default="").lower()
                    p_th = next((p for p in (state.planets or ()) if p.planet_id == pid_i), None)
                    live_overflow = False
                    if p_th is not None:
                        sto_m = _safe_api_int(getattr(p_th.storage, "metal", 0), 0)
                        res_m = _safe_api_int(getattr(p_th.resources, "metal", 0), 0)
                        sto_p = _safe_api_int(getattr(p_th.storage, "pig_iron", 0), 0)
                        res_p = _safe_api_int(getattr(p_th.resources, "pig_iron", 0), 0)
                        live_overflow = (
                            sto_m > 0
                            and res_m >= sto_m
                            and (sto_p <= 0 or res_p < sto_p)
                        )
                    if pct_set == 0 and ("metal storage full" in rsn_th or live_overflow):
                        self._metal_overflow_paused_planets[pid_i] = now_th_wall
                        self._persist_runtime_state()
                    elif self._metal_overflow_paused_planets.pop(pid_i, None) is not None:
                        self._persist_runtime_state()
            if res.action.kind in ("ATTACK", "SPY", "TRANSPORT", "STATION", "RECYCLE", "ESCAPE_FLIGHT", "COLONIZE"):
                pid_f = game_data.cap_json_int(_safe_api_int(res.action.planet_id, 0))
                origin_p_f = next((p for p in (state.planets or ()) if p.planet_id == pid_f), None)
                obs_f_avail = getattr(res, "observed_fleet_slots_available", None)
                if obs_f_avail is not None:
                    self._record_observed_fleet_slots_locked(origin_p_f, obs_f_avail)
                if pid_f > 0:
                    if res.executed:
                        self._fleet_slot_cooldown_until.pop(pid_f, None)
                    else:
                        msg_l = _bounded_api_str(res.message, default="").lower()
                        if any(marker in msg_l for marker in self._FLEET_SLOT_BUSY_MARKERS):
                            cd_dur_s = self._compute_fleet_slot_busy_cooldown_s(state, origin_p_f)
                            self._fleet_slot_cooldown_until[pid_f] = (
                                time.monotonic() + cd_dur_s
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
                            self._refresh_raid_targets_locked(limit=100, state_override=state)
                        else:
                            self._raid_targets = self._enrich_raid_targets(self._raid_targets)
                if f_coords and bool(getattr(res, "clear_target_debris", False)):
                    self._galaxy_debris_amounts.pop(f_coords, None)
                    if self._db is not None:
                        self._db.set_target_debris(f_coords, False)
                        self._refresh_raid_targets_locked(limit=100, state_override=state)
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
                self._refresh_raid_targets_locked(limit=100, state_override=state)
            if res.executed and res.action.kind == "TRANSPORT":
                orig_tr = normalize_coords(str(getattr(res.action, "planet_coords", "") or ""))
                if not orig_tr:
                    orig_p = next((p for p in state.planets if p.planet_id == res.action.planet_id), None)
                    orig_tr = normalize_coords(getattr(orig_p, "coords", "") or "") if orig_p is not None else ""
                dest_tr = _extract_coords_from_text(
                    str(getattr(res.action, "target_name", "") or "")
                ) or _extract_coords_from_text(str(getattr(res, "message", "") or ""))
                if orig_tr and dest_tr and res.action.cost and not res.action.cost.is_zero():
                    self._record_transport_dispatch_locked(
                        origin_coords=orig_tr,
                        dest_coords=dest_tr,
                        cargo=res.action.cost,
                        ships=_bounded_api_str(res.message, default="")[:140],
                        source="dispatch",
                    )
            if res.executed and res.action.kind == "BUILD_SHIP":
                ship_sid = game_data.safe_optional_int(res.action.target_id, None)
                # `res.action` comes from `_find_ship_action`: `cost` is per unit, `target_level` the quantity.
                ship_qty = max(1, _safe_api_int(getattr(res.action, "target_level", 1), 1))
                adaptive_exec = bool(getattr(self.strategy_config, "ai_adaptive_execution", False))
                if AI_INFRA_CARGO_TAG in str(getattr(res.action, "reason", "") or "") or (
                    adaptive_exec and ship_sid in (1, 2)
                ):
                    self._record_infra_transport_spend_locked(res.action, quantity=ship_qty)
                    self._persist_runtime_state()
                elif adaptive_exec and ship_sid != 9:
                    # Every other hull except Solar cells (s9, an energy build) draws on the spare % budget.
                    self._record_spare_ship_spend_locked(res.action, quantity=ship_qty)
                    self._persist_runtime_state()
            if res.action.kind in ("COLONIZE", "ABANDON_COLONY"):
                self._record_colony_attempt_from_result_locked(state, res, cmd)
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
                    self._last_research_sync_sig = None
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
                self._last_research_sync_sig = None
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
            state_to_record = (
                res_updated
                if (not dry_run and isinstance(res_updated, GameState))
                else state
            )
            obs_flag = False
            if not dry_run and obs_conf_bool is True:
                obs_flag = True
                if (
                    valid_obs_pid is not None
                    and any(p.planet_id == valid_obs_pid for p in state_to_record.planets)
                    and state_to_record.active_planet_id != valid_obs_pid
                ):
                    state_to_record = replace(
                        state_to_record,
                        active_planet_id=valid_obs_pid,
                        research_offers=(),
                    )
                    self._last_research_sync_sig = None
            merged_state = self._record_state(
                state_to_record,
                fetched_buildings=(
                    self._last_buildings_fetched
                    and state_to_record.active_planet_id == state.active_planet_id
                ),
                fetched_home=False,
                observed_active_planet=obs_flag,
                update_snapshot_ts=isinstance(res_updated, GameState) and not dry_run,
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

    _KNOWN_COMMANDS: frozenset[str] = frozenset(
        {
            "notifications_list",
            "notification_get",
            "notification_ack",
            "notifications_ack_all",
            "queue_add",
            "queue_update",
            "queue_cancel",
            "queue_clear",
            "config",
            "db_summary",
            "db_verify",
            "espionage_intel",
            "scanned_sectors",
            "colony_candidates",
            "discard_colony_candidate",
            "universe_research_max",
            "attack_results",
            "recycler_results",
            "bot_control",
            "update_config",
            "action_graph",
            "galaxy_sweep",
            "simulate_combat",
            "planner_jobs_status",
            "config_optimizer_get",
            "rename_planet",
            "sync_planet_names",
            "config_optimizer_set_specializations",
            "config_optimizer_update_planet",
            "config_optimizer_yearly_projection",
            "config_optimizer_run",
            "ai_planner_get",
            "ai_planner_run",
            "resolve_field_conflict",
            "fleet_planner_get",
            "ensure_session",
            "buildings_eta",
            "combat_overview",
            "plan",
            "tick_once",
            "navigate",
            "scan_galaxy",
            "activate_transmitter",
            "sync_messages",
            "spy",
            "recycle",
            "colonize",
            "transport",
            "station",
            "abandon_colony",
            "attack",
            "build",
            "demolish_building",
            "research",
            "build_ship",
            "sync_economy",
            "throttle_building",
            "fleetsave_plan",
            "escape_flight",
            "claim_tutorial",
        }
    )

    def handle_command(self, payload: dict[str, Any]) -> dict[str, Any]:
        resp = self._handle_command_impl(payload)
        raw_cmd = payload.get("command") if isinstance(payload, Mapping) else None
        cmd_norm = raw_cmd.strip().lower() if isinstance(raw_cmd, str) else ""
        if cmd_norm in ("plan", "tick_once"):
            self._maybe_trigger_ai_drift_replan()
        return resp

    def _handle_command_impl(self, payload: dict[str, Any]) -> dict[str, Any]:
        if not isinstance(payload, Mapping) or not payload.get("command"):
            raise ApiRequestError("Request JSON must include a non-empty 'command' field.")

        raw_cmd = payload["command"]
        if not isinstance(raw_cmd, str):
            raise ApiRequestError("Request JSON 'command' field must be a non-empty string.")
        cmd = raw_cmd.strip().lower()
        if not cmd:
            raise ApiRequestError("Request JSON must include a non-empty 'command' field.")
        if cmd not in self._KNOWN_COMMANDS:
            raise ApiRequestError(f"Unknown command {cmd!r}.")
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

        if cmd == "notification_get":
            raw_nid = (
                payload.get("notification_id")
                if payload.get("notification_id") is not None
                else (
                    payload.get("notification_code")
                    if payload.get("notification_code") is not None
                    else payload.get("id")
                )
            )
            parsed_nid = GameDatabase.parse_notification_id(raw_nid)
            if parsed_nid is None:
                raise ApiRequestError(
                    "'notification_get' requires a valid 'notification_id' (e.g. 42 or 'NTF-0042')."
                )
            with self._lock:
                if self._db is None or not hasattr(self._db, "get_notification"):
                    raise ApiRequestError("Database notifications are not configured.")
                found_item = self._db.get_notification(parsed_nid)
                if found_item is None:
                    raise ApiRequestError(
                        f"Notification {GameDatabase.format_notification_code(parsed_nid)} not found."
                    )
                return _sanitize_finite_json(
                    {
                        "ok": True,
                        "command": cmd,
                        "item": _redact_api_payload(found_item),
                    }
                )

        if cmd == "notification_ack":
            raw_ack_nid = (
                payload.get("notification_id")
                if payload.get("notification_id") is not None
                else payload.get("notification_code")
            )
            nid = (
                GameDatabase.parse_notification_id(raw_ack_nid)
                if isinstance(raw_ack_nid, str)
                else _parse_command_int("notification_id", raw_ack_nid, min_val=1, max_val=2**62)
            )
            if nid is None:
                raise ApiRequestError(
                    "'notification_id' must be a positive integer or code like 'NTF-0042'."
                )
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
                "TRANSPORT",
                "STATION",
                "ATTACK",
                "RECYCLE",
                "COLONIZE",
                "ABANDON_COLONY",
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
            if norm_kind in ("SPY", "TRANSPORT", "STATION", "ATTACK", "RECYCLE", "COLONIZE") and not (tcoords.strip() or tname.strip()):
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

        if cmd == "espionage_intel":
            limit = _parse_command_int(
                "limit",
                payload.get("limit", 150),
                min_val=1,
                max_val=1000,
            ) or 150
            coords_raw = payload.get("coords")
            coords_str = str(coords_raw).strip() if isinstance(coords_raw, str) and coords_raw.strip() else None
            with self._intel_read_lock:
                db_snap = self._db
                state_snap = self._last_state
                cfg_snap = self.strategy_config
                origin_coords = ""
                if state_snap is not None:
                    spy_pid = _safe_api_int(getattr(cfg_snap, "spy_dispatch_planet_id", 0), 0)
                    orig_p = (
                        next((p for p in state_snap.planets if p.planet_id == spy_pid), None)
                        if spy_pid > 0
                        else None
                    )
                    if orig_p is None:
                        orig_p = state_snap.active_planet()
                    if orig_p is None and state_snap.planets:
                        orig_p = state_snap.planets[0]
                    if orig_p is not None and orig_p.coords:
                        origin_coords = normalize_coords(orig_p.coords)

                m_orig_esp = (
                    re.fullmatch(r"(\d+):(\d+):(\d+)", origin_coords, re.ASCII)
                    if origin_coords
                    else None
                )
                orig_gsp = (
                    (int(m_orig_esp.group(1)), int(m_orig_esp.group(2)), int(m_orig_esp.group(3)))
                    if m_orig_esp
                    else None
                )

                def _esp_distance_fields(target_raw: Any) -> dict[str, Any]:
                    norm_t = normalize_coords(target_raw) if target_raw else ""
                    m_t = re.fullmatch(r"(\d+):(\d+):(\d+)", norm_t, re.ASCII) if norm_t else None
                    if orig_gsp is None or m_t is None:
                        return {
                            "distance": None,
                            "system_distance": None,
                            "galaxy_delta": None,
                            "system_delta": None,
                            "position_delta": None,
                        }
                    tg, ts_i, tp = int(m_t.group(1)), int(m_t.group(2)), int(m_t.group(3))
                    g_diff = abs(orig_gsp[0] - tg)
                    s_diff = abs(orig_gsp[1] - ts_i)
                    p_diff = abs(orig_gsp[2] - tp)
                    return {
                        "distance": round(float(_parse_coords_distance(origin_coords, norm_t)), 1),
                        "system_distance": int(g_diff * 400 + s_diff),
                        "galaxy_delta": int(g_diff),
                        "system_delta": int(s_diff),
                        "position_delta": int(p_diff),
                    }

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
                    td.update(_esp_distance_fields(td.get("coords") or td.get("target_coords")))
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
                    rd.update(_esp_distance_fields(rd.get("coords") or rd.get("target_coords")))
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
                            "origin_coords": origin_coords,
                            "summary": summary,
                            "targets": targets,
                            "reports": reports,
                        }
                    )
                )

        if cmd == "scanned_sectors":
            gal_filter = _parse_command_int(
                "galaxy",
                payload.get("galaxy"),
                allow_none=True,
                min_val=0,
                max_val=14,
            )
            raw_inc_unscanned = payload.get("include_unscanned")
            if raw_inc_unscanned is None:
                include_unscanned = bool(gal_filter and gal_filter > 0)
            elif isinstance(raw_inc_unscanned, bool):
                include_unscanned = raw_inc_unscanned
            else:
                raise ApiRequestError("'scanned_sectors' field 'include_unscanned' must be a boolean.")
            limit = _parse_command_int(
                "limit",
                payload.get("limit", 5600),
                min_val=1,
                max_val=5600,
            ) or 5600
            req_pid = _parse_command_int(
                "planet_id",
                payload.get("planet_id"),
                allow_none=True,
                min_val=1,
                max_val=2**63 - 1,
            )
            preview_refresh_pct = _parse_command_int(
                "preview_refresh_pct",
                payload.get("preview_refresh_pct"),
                allow_none=True,
                min_val=0,
                max_val=100,
            )
            preview_sweep_pages = _parse_command_int(
                "preview_sweep_pages",
                payload.get("preview_sweep_pages"),
                allow_none=True,
                min_val=1,
                max_val=400,
            )
            raw_preview_all_gals = payload.get("preview_all_galaxies")
            if raw_preview_all_gals is None:
                preview_all_galaxies: Optional[bool] = None
            elif isinstance(raw_preview_all_gals, bool):
                preview_all_galaxies = raw_preview_all_gals
            else:
                raise ApiRequestError("'scanned_sectors' field 'preview_all_galaxies' must be a boolean.")
            is_preview = (
                preview_refresh_pct is not None
                or preview_sweep_pages is not None
                or preview_all_galaxies is not None
            )
            with self._intel_read_lock:
                now_ts = time.time()
                cfg = self.strategy_config
                state_snap = self._last_state
                db_snap = self._db
                raid_targets_snap = [r for r in (self._raid_targets or ()) if isinstance(r, Mapping)]
                scanned_systems_snap = self._get_scanned_systems()
                origin_coords = ""
                if state_snap is not None:
                    orig_p = None
                    if req_pid is not None:
                        orig_p = next(
                            (p for p in state_snap.planets if p.planet_id == req_pid),
                            None,
                        )
                    if orig_p is None:
                        orig_p = state_snap.active_planet()
                    if orig_p is None and state_snap.planets:
                        orig_p = state_snap.planets[0]
                    if orig_p is not None and orig_p.coords:
                        origin_coords = normalize_coords(orig_p.coords)
                m_orig_sec = (
                    re.fullmatch(r"(\d+):(\d+):(\d+)", origin_coords, re.ASCII)
                    if origin_coords
                    else None
                )
                orig_gs = (
                    (int(m_orig_sec.group(1)), int(m_orig_sec.group(2)))
                    if m_orig_sec
                    else None
                )

                max_g = self._max_galaxies_locked()
                intel: Mapping[str, Any] = {}
                if db_snap is not None:
                    raw_intel = self._call_get_galaxy_sector_intel_locked(
                        db_snap,
                        galaxies=None,
                        now_ts=now_ts,
                        max_galaxies=max_g,
                    )
                    if isinstance(raw_intel, Mapping):
                        intel = raw_intel

                raw_sectors = intel.get("sectors")
                sectors_map: Mapping[str, Any] = (
                    raw_sectors if isinstance(raw_sectors, Mapping) else {}
                )

                saved_sweep_pages = max(1, min(400, _safe_api_int(getattr(cfg, "galaxy_sweep_pages", 100), 100)))
                saved_refresh_pct = max(0, min(100, _safe_api_int(getattr(cfg, "galaxy_scan_refresh_pct", 0), 0)))
                saved_explore_first = bool(getattr(cfg, "galaxy_scan_explore_first", True))
                saved_scan_all_gals = bool(getattr(cfg, "galaxy_scan_all_galaxies", False))

                sweep_pages = preview_sweep_pages if preview_sweep_pages is not None else saved_sweep_pages
                refresh_pct_cfg = preview_refresh_pct if preview_refresh_pct is not None else saved_refresh_pct
                explore_first_cfg = (refresh_pct_cfg < 100) if preview_refresh_pct is not None else saved_explore_first
                scan_all_gals_cfg = (
                    preview_all_galaxies if preview_all_galaxies is not None else saved_scan_all_gals
                )

                all_galaxies = list(range(1, max_g + 1))
                owned_galaxies = self._owned_sweep_galaxies_locked(state_override=state_snap)
                owned_gal_set = set(owned_galaxies)
                queue_galaxies = self._sweep_target_galaxies_locked(
                    refresh_pct_override=refresh_pct_cfg,
                    all_galaxies_override=scan_all_gals_cfg,
                    state_override=state_snap,
                    scanned_systems_override=scanned_systems_snap,
                )
                queue_gal_set = set(queue_galaxies)

                allocated_normal = count_allocated_normal_planets(state_snap)
                max_planets_cap = max(1, _safe_api_int(getattr(cfg, "max_planets", 11), 11))
                collect_colonies = bool(allocated_normal < max_planets_cap)

                smart_plan = self._plan_smart_galaxy_sweep_locked(
                    batch_size=sweep_pages,
                    galaxies=queue_galaxies,
                    collect_colonies=collect_colonies,
                    now_ts=now_ts,
                    sector_intel_override=intel or None,
                    explore_first_override=explore_first_cfg,
                    refresh_pct_override=refresh_pct_cfg,
                    scanned_systems_override=scanned_systems_snap,
                    state_override=state_snap,
                    raid_targets_override=raid_targets_snap,
                )
                if smart_plan is not None and not is_preview:
                    self._store_smart_sweep_summary_locked(smart_plan, selection_mode="smart", now_ts=now_ts)
                sweep_summary = self._get_galaxy_sweep_state_locked(now_ts=now_ts, include_smart=False)

                def _calc_sector_dist(
                    gal_i: int, sys_i: int, fallback_dist: Any = None
                ) -> tuple[Optional[int], Optional[int], Optional[int], Optional[int]]:
                    if orig_gs is not None:
                        gd = abs(orig_gs[0] - gal_i)
                        sd = abs(orig_gs[1] - sys_i)
                        sdist = int(gd * 400 + sd)
                        cdist = int(min(sd, 400 - sd)) if gd == 0 else sdist
                        return gd, sd, sdist, cdist
                    fb = _safe_api_int(fallback_dist, -1) if fallback_dist is not None else -1
                    sdist_fb = fb if fb >= 0 else None
                    return None, None, sdist_fb, sdist_fb

                def _extract_freq_owners(raw_freq: Any) -> list[str]:
                    out_names: list[str] = []
                    if isinstance(raw_freq, Sequence) and not isinstance(raw_freq, (str, bytes)):
                        for fo in raw_freq[:3]:
                            if isinstance(fo, Mapping) and fo.get("owner"):
                                out_names.append(f"{fo['owner']} ({_safe_api_int(fo.get('raid_count'), 0)})")
                            elif isinstance(fo, str) and fo.strip():
                                out_names.append(fo.strip())
                    return out_names

                scan_queue: list[dict[str, Any]] = []
                queue_by_sector: dict[str, dict[str, Any]] = {}
                queued_count_by_gal: dict[int, int] = {g: 0 for g in all_galaxies}
                for q_idx, q_item in enumerate((smart_plan or {}).get("selected_sectors") or [], start=1):
                    if not isinstance(q_item, Mapping):
                        continue
                    qg = _safe_api_int(q_item.get("galaxy"), 0)
                    qs = _safe_api_int(q_item.get("system"), 0)
                    if not (1 <= qg <= max_g and 1 <= qs <= 400):
                        continue
                    q_key = f"{qg:02d}:{qs:03d}"
                    q_sec_raw = sectors_map.get(q_key)
                    q_sec_map = q_sec_raw if isinstance(q_sec_raw, Mapping) else {}
                    q_scanned = bool(q_item.get("scanned", False)) or _safe_api_float(
                        q_sec_map.get("last_scanned_ts"), 0.0
                    ) > 0.0
                    q_last_ts = max(
                        _safe_api_float(q_item.get("last_scanned_ts"), 0.0),
                        _safe_api_float(q_sec_map.get("last_scanned_ts"), 0.0),
                    )
                    q_age_s: Optional[float] = (
                        round(max(0.0, now_ts - q_last_ts), 1) if q_scanned and q_last_ts > 0.0 else None
                    )
                    q_gd, q_sd, q_sdist, q_cdist = _calc_sector_dist(qg, qs, q_item.get("distance"))
                    q_phase = str(q_item.get("phase") or ("exploratory" if not q_scanned else "targeted_refresh"))
                    q_row = {
                        "queue_pos": q_idx,
                        "sector": q_key,
                        "galaxy": qg,
                        "system": qs,
                        "phase": q_phase,
                        "scanned": q_scanned,
                        "last_scanned_ts": round(q_last_ts, 3),
                        "age_seconds": q_age_s,
                        "scan_count": max(
                            1 if q_scanned else 0,
                            _safe_api_int(q_sec_map.get("scan_count"), 0),
                        ),
                        "occupied_count": max(0, _safe_api_int(q_sec_map.get("occupied_count"), 0)),
                        "debris_count": max(
                            _safe_api_int(q_item.get("debris_count"), 0),
                            _safe_api_int(q_sec_map.get("debris_count"), 0),
                        ),
                        "target_count": max(
                            _safe_api_int(q_item.get("target_count"), 0),
                            _safe_api_int(q_sec_map.get("target_count"), 0),
                        ),
                        "free_count": max(
                            _safe_api_int(q_item.get("free_count"), 0),
                            _safe_api_int(q_sec_map.get("free_count"), 0),
                        ),
                        "prime_colony_free_count": max(
                            0, _safe_api_int(q_sec_map.get("prime_colony_free_count"), 0)
                        ),
                        "direct_raid_count": max(
                            _safe_api_int(q_item.get("direct_raid_count"), 0),
                            _safe_api_int(q_sec_map.get("direct_raid_count"), 0),
                        ),
                        "owner_affinity_raid_count": max(
                            _safe_api_int(q_item.get("owner_affinity_raid_count"), 0),
                            _safe_api_int(q_sec_map.get("owner_affinity_raid_count"), 0),
                        ),
                        "frequent_owners": _extract_freq_owners(
                            q_item.get("frequent_owners") or q_sec_map.get("frequent_owners")
                        ),
                        "system_distance": q_sdist,
                        "circular_distance": q_cdist,
                        "galaxy_delta": q_gd,
                        "system_delta": q_sd,
                        "score": round(_safe_api_float(q_item.get("score"), 0.0), 2),
                        "importance": round(_safe_api_float(q_item.get("importance"), 0.0), 2),
                        "reason": str(q_item.get("reason") or ""),
                    }
                    scan_queue.append(q_row)
                    queue_by_sector[q_key] = q_row
                    queued_count_by_gal[qg] = queued_count_by_gal.get(qg, 0) + 1

                gal_agg: dict[int, dict[str, int]] = {
                    g: {
                        "scanned_count": 0,
                        "occupied_count": 0,
                        "debris_count": 0,
                        "target_count": 0,
                        "prime_free_count": 0,
                    }
                    for g in all_galaxies
                }
                scanned_gals_set: set[int] = set()
                scanned_by_key: dict[str, Mapping[str, Any]] = {}
                for skey_raw, sec_v in sectors_map.items():
                    if not isinstance(sec_v, Mapping):
                        continue
                    last_ts_v = _safe_api_float(sec_v.get("last_scanned_ts"), 0.0)
                    if last_ts_v <= 0.0 and not bool(sec_v.get("scanned")):
                        continue
                    gi = _safe_api_int(sec_v.get("galaxy"), 0)
                    si = _safe_api_int(sec_v.get("system"), 0)
                    if not (1 <= gi <= max_g and 1 <= si <= 400):
                        continue
                    norm_skey = f"{gi:02d}:{si:03d}"
                    scanned_by_key[norm_skey] = sec_v
                    scanned_gals_set.add(gi)
                    agg = gal_agg[gi]
                    agg["scanned_count"] += 1
                    agg["occupied_count"] += max(0, _safe_api_int(sec_v.get("occupied_count"), 0))
                    agg["debris_count"] += max(0, _safe_api_int(sec_v.get("debris_count"), 0))
                    inact_v = max(0, _safe_api_int(sec_v.get("inactive_count"), 0))
                    spacia_v = max(0, _safe_api_int(sec_v.get("spacia_count"), 0))
                    agg["target_count"] += max(0, _safe_api_int(sec_v.get("target_count"), inact_v + spacia_v))
                    agg["prime_free_count"] += max(0, _safe_api_int(sec_v.get("prime_colony_free_count"), 0))

                galaxy_stats: list[dict[str, Any]] = []
                for g in all_galaxies:
                    agg = gal_agg[g]
                    sc_cnt = agg["scanned_count"]
                    galaxy_stats.append(
                        {
                            "galaxy": g,
                            "is_owned": g in owned_gal_set,
                            "in_sweep_scope": g in queue_gal_set,
                            "scanned_count": sc_cnt,
                            "unscanned_count": max(0, 400 - sc_cnt),
                            "total_systems": 400,
                            "coverage_pct": round((sc_cnt / 400.0) * 100.0, 2),
                            "occupied_count": agg["occupied_count"],
                            "debris_count": agg["debris_count"],
                            "target_count": agg["target_count"],
                            "prime_free_count": agg["prime_free_count"],
                            "queued_count": queued_count_by_gal.get(g, 0),
                        }
                    )

                if gal_filter and gal_filter > 0:
                    score_galaxies = [gal_filter]
                elif include_unscanned:
                    score_galaxies = all_galaxies
                else:
                    score_galaxies = sorted(scanned_gals_set | queue_gal_set)

                score_by_sector: dict[str, Mapping[str, Any]] = {}
                if score_galaxies:
                    raid_stats = (
                        getattr(state_snap, "raid_stats", None)
                        if state_snap is not None
                        else None
                    )
                    scored_rows = score_galaxy_scan_sectors(
                        own_coords=self._own_sweep_coords_locked(state_override=state_snap) or ([origin_coords] if origin_coords else ()),
                        sector_intel=intel,
                        galaxies=score_galaxies,
                        scanned_systems=scanned_systems_snap,
                        raid_targets=raid_targets_snap,
                        raid_stats=raid_stats if isinstance(raid_stats, Mapping) else None,
                        explore_first=False,
                        near_weight=max(0.0, min(20.0, _safe_api_float(getattr(cfg, "galaxy_scan_near_weight", 2.0), 2.0))),
                        attack_weight=max(0.0, min(20.0, _safe_api_float(getattr(cfg, "galaxy_scan_attack_weight", 3.0), 3.0))),
                        collect_colonies=collect_colonies,
                        now_ts=now_ts,
                    )
                    for sc in scored_rows:
                        skey_sc = str(sc.get("sector") or "")
                        if skey_sc:
                            score_by_sector[skey_sc] = sc

                def _build_sector_row(
                    gal_i: int,
                    sys_i: int,
                    sec_raw: Optional[Mapping[str, Any]],
                ) -> dict[str, Any]:
                    skey_row = f"{gal_i:02d}:{sys_i:03d}"
                    sc_info = score_by_sector.get(skey_row, {})
                    q_info = queue_by_sector.get(skey_row)
                    is_scanned = sec_raw is not None
                    last_ts = _safe_api_float(sec_raw.get("last_scanned_ts"), 0.0) if sec_raw else 0.0
                    scan_cnt = max(0, _safe_api_int(sec_raw.get("scan_count"), 0)) if sec_raw else 0
                    age_s: Optional[float] = (
                        (
                            round(max(0.0, now_ts - last_ts), 1)
                            if last_ts > 0.0
                            else _safe_api_float(sec_raw.get("age_seconds"), 0.0)
                        )
                        if is_scanned and sec_raw is not None
                        else None
                    )
                    occ_cnt = max(0, _safe_api_int(sec_raw.get("occupied_count"), 0)) if sec_raw else 0
                    free_cnt = max(0, _safe_api_int(sec_raw.get("free_count"), 0)) if sec_raw else 0
                    prime_cnt = max(0, _safe_api_int(sec_raw.get("prime_colony_free_count"), 0)) if sec_raw else 0
                    inact_cnt = max(0, _safe_api_int(sec_raw.get("inactive_count"), 0)) if sec_raw else 0
                    spacia_cnt = max(0, _safe_api_int(sec_raw.get("spacia_count"), 0)) if sec_raw else 0
                    target_cnt = (
                        max(0, _safe_api_int(sec_raw.get("target_count"), inact_cnt + spacia_cnt))
                        if sec_raw
                        else 0
                    )
                    debris_cnt = max(0, _safe_api_int(sec_raw.get("debris_count"), 0)) if sec_raw else 0
                    active_occ = max(0, occ_cnt - inact_cnt - spacia_cnt)
                    g_diff, s_diff, sys_dist, circ_dist = _calc_sector_dist(
                        gal_i, sys_i, sc_info.get("distance") if sc_info else None
                    )
                    freq_names = _extract_freq_owners(sec_raw.get("frequent_owners")) if sec_raw else []
                    reason_txt = (
                        str(q_info.get("reason") or "")
                        if q_info and not is_scanned
                        else str(sc_info.get("reason") or ("unscanned sector" if not is_scanned else ""))
                    )
                    return {
                        "sector": skey_row,
                        "galaxy": gal_i,
                        "system": sys_i,
                        "scanned": is_scanned,
                        "queue_pos": int(q_info["queue_pos"]) if q_info else None,
                        "queue_phase": str(q_info["phase"]) if q_info else None,
                        "occupied_count": occ_cnt,
                        "max_slots": 16,
                        "active_occupied_count": active_occ,
                        "free_count": free_cnt,
                        "prime_colony_free_count": prime_cnt,
                        "debris_count": debris_cnt,
                        "target_count": target_cnt,
                        "inactive_count": inact_cnt,
                        "spacia_count": spacia_cnt,
                        "scan_count": max(1, scan_cnt) if is_scanned else 0,
                        "last_scanned_ts": round(last_ts, 3),
                        "age_seconds": age_s,
                        "system_distance": sys_dist,
                        "circular_distance": circ_dist,
                        "galaxy_delta": g_diff,
                        "system_delta": s_diff,
                        "direct_raid_count": max(0, _safe_api_int(sec_raw.get("direct_raid_count"), 0)) if sec_raw else 0,
                        "direct_raid_24h_count": (
                            max(0, _safe_api_int(sec_raw.get("direct_raid_24h_count"), 0)) if sec_raw else 0
                        ),
                        "direct_loot_spice_eq": (
                            round(max(0.0, _safe_api_float(sec_raw.get("direct_loot_spice_eq"), 0.0)), 1)
                            if sec_raw
                            else 0.0
                        ),
                        "owner_affinity_raid_count": (
                            max(0, _safe_api_int(sec_raw.get("owner_affinity_raid_count"), 0)) if sec_raw else 0
                        ),
                        "frequent_owners": freq_names,
                        "score": round(_safe_api_float(sc_info.get("score"), 0.0), 2),
                        "importance": round(_safe_api_float(sc_info.get("importance"), 0.0), 2),
                        "reason": reason_txt,
                    }

                rows_out: list[dict[str, Any]] = []
                total_scanned_all = len(scanned_by_key)
                if gal_filter and gal_filter > 0:
                    for sys_i in range(1, 401):
                        skey_g = f"{gal_filter:02d}:{sys_i:03d}"
                        sec_m = scanned_by_key.get(skey_g)
                        if sec_m is not None or include_unscanned:
                            rows_out.append(_build_sector_row(gal_filter, sys_i, sec_m))
                else:
                    for skey_s, sec_m in scanned_by_key.items():
                        gi = _safe_api_int(sec_m.get("galaxy"), 0)
                        si = _safe_api_int(sec_m.get("system"), 0)
                        if 1 <= gi <= max_g and 1 <= si <= 400:
                            rows_out.append(_build_sector_row(gi, si, sec_m))
                    if include_unscanned:
                        unscanned_candidates: list[tuple[int, int, int, int]] = []
                        for g_i in all_galaxies:
                            for s_i in range(1, 401):
                                sk_u = f"{g_i:02d}:{s_i:03d}"
                                if sk_u in scanned_by_key:
                                    continue
                                q_ent = queue_by_sector.get(sk_u)
                                q_rank = int(q_ent["queue_pos"]) if q_ent else 10**6
                                _, _, sdist_u, _ = _calc_sector_dist(g_i, s_i, None)
                                unscanned_candidates.append(
                                    (q_rank, sdist_u if sdist_u is not None else 10**9, g_i, s_i)
                                )
                        unscanned_candidates.sort()
                        unscanned_cap = min(400, max(1, int(limit)))
                        for _, _, g_u, s_u in unscanned_candidates[:unscanned_cap]:
                            rows_out.append(_build_sector_row(g_u, s_u, None))

                rows_out.sort(
                    key=lambda r: (
                        0 if r["scanned"] else 1,
                        -float(r["last_scanned_ts"]) if r["scanned"] else (
                            int(r["queue_pos"]) if r["queue_pos"] is not None else 10**6
                        ),
                        r["system_distance"] if r["system_distance"] is not None else 10**9,
                        int(r["galaxy"]),
                        int(r["system"]),
                    )
                )

                eff_refresh_pct = refresh_pct_cfg if refresh_pct_cfg > 0 else (0 if explore_first_cfg else 100)
                saved_eff_refresh_pct = (
                    saved_refresh_pct if saved_refresh_pct > 0 else (0 if saved_explore_first else 100)
                )
                planned_refresh_pages = int(round(sweep_pages * (eff_refresh_pct / 100.0)))
                planned_discovery_pages = max(0, sweep_pages - planned_refresh_pages)

                scanned_rows = [r for r in rows_out if r["scanned"]]
                unscanned_in_view = len(rows_out) - len(scanned_rows)
                total_occ = sum(int(r["occupied_count"]) for r in scanned_rows)
                total_free = sum(int(r["free_count"]) for r in scanned_rows)
                total_prime = sum(int(r["prime_colony_free_count"]) for r in scanned_rows)
                total_debris = sum(int(r["debris_count"]) for r in scanned_rows)
                sectors_with_debris = sum(1 for r in scanned_rows if int(r["debris_count"]) > 0)
                total_targets = sum(int(r["target_count"]) for r in scanned_rows)
                total_spacia = sum(int(r["spacia_count"]) for r in scanned_rows)
                total_inactives = sum(int(r["inactive_count"]) for r in scanned_rows)
                stale_1h = sum(
                    1 for r in scanned_rows if r["age_seconds"] is not None and float(r["age_seconds"]) >= 3600.0
                )
                total_raids = sum(int(r["direct_raid_count"]) for r in scanned_rows)

                uni_exp = max(
                    total_scanned_all,
                    _safe_api_int(intel.get("universe_explored_systems"), sweep_summary.get("universe_explored_systems", 0)),
                )
                uni_tot = max(1, _safe_api_int(intel.get("universe_total_systems"), max_g * 400))
                uni_cov = round(min(100.0, (uni_exp / uni_tot) * 100.0), 2)
                q_disc_cnt = sum(1 for q in scan_queue if q["phase"] == "exploratory")
                q_ref_cnt = len(scan_queue) - q_disc_cnt
                scope_src: Mapping[str, Any] = (
                    smart_plan if (is_preview and isinstance(smart_plan, Mapping)) else sweep_summary
                )

                summary_out = {
                    "scanned_sectors": len(scanned_rows),
                    "unscanned_sectors_in_view": unscanned_in_view,
                    "unscanned_total_all_galaxies": max(0, uni_tot - uni_exp),
                    "total_scanned_all_galaxies": total_scanned_all,
                    "explored_systems": _safe_api_int(scope_src.get("explored_systems"), total_scanned_all),
                    "unscanned_systems": _safe_api_int(scope_src.get("unscanned_systems"), 0),
                    "total_systems": _safe_api_int(scope_src.get("total_systems"), len(queue_galaxies) * 400),
                    "coverage_pct": round(_safe_api_float(scope_src.get("coverage_pct"), 0.0), 2),
                    "universe_explored_systems": uni_exp,
                    "universe_total_systems": uni_tot,
                    "universe_coverage_pct": uni_cov,
                    "total_occupied": total_occ,
                    "avg_occupied": round(total_occ / len(scanned_rows), 1) if scanned_rows else 0.0,
                    "total_free": total_free,
                    "total_prime_free": total_prime,
                    "total_debris_fields": total_debris,
                    "sectors_with_debris": sectors_with_debris,
                    "total_targets": total_targets,
                    "total_spacia": total_spacia,
                    "total_inactives": total_inactives,
                    "stale_over_1h_count": stale_1h,
                    "total_raids": total_raids,
                    "sweep_pages": sweep_pages,
                    "sweep_interval_seconds": _safe_api_int(getattr(cfg, "galaxy_sweep_interval_seconds", 270), 270),
                    "allow_galaxy_scan": bool(getattr(cfg, "allow_galaxy_scan", False)),
                    "explore_first": explore_first_cfg,
                    "scan_all_galaxies": scan_all_gals_cfg,
                    "refresh_pct": refresh_pct_cfg,
                    "discovery_pct": 100 - refresh_pct_cfg,
                    "effective_refresh_pct": eff_refresh_pct,
                    "effective_discovery_pct": 100 - eff_refresh_pct,
                    "saved_refresh_pct": saved_eff_refresh_pct,
                    "saved_sweep_pages": saved_sweep_pages,
                    "saved_scan_all_galaxies": saved_scan_all_gals,
                    "planned_refresh_pages": planned_refresh_pages,
                    "planned_discovery_pages": planned_discovery_pages,
                    "next_batch_refresh_count": q_ref_cnt,
                    "next_batch_discovery_count": q_disc_cnt,
                    "queue_count": len(scan_queue),
                    "queue_discovery_count": q_disc_cnt,
                    "queue_refresh_count": q_ref_cnt,
                    "queue_galaxies": list(queue_galaxies),
                    "scan_phase": str(scope_src.get("scan_phase") or scope_src.get("phase") or "exploratory"),
                }
                return _sanitize_finite_json(
                    _redact_api_payload(
                        {
                            "ok": True,
                            "command": cmd,
                            "preview": is_preview,
                            "origin_coords": origin_coords,
                            "galaxy_filter": gal_filter or 0,
                            "include_unscanned": include_unscanned,
                            "galaxies_available": all_galaxies,
                            "galaxies_with_scans": sorted(scanned_gals_set),
                            "galaxy_stats": galaxy_stats,
                            "scan_queue": scan_queue,
                            "summary": summary_out,
                            "sectors": rows_out[: int(limit)],
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
            inc_discarded = bool(payload.get("include_discarded", False))
            with self._intel_read_lock:
                state_snap = self._last_state
                cfg_snap = self.strategy_config
                db_snap = self._db
                ap = state_snap.active_planet() if state_snap is not None else None
                origin_coords = ap.coords if ap is not None else ""
                min_fields = max(50, _safe_api_int(cfg_snap.min_colony_fields, 300))
                own_coords_snap = self._own_planet_coords(state_snap)
                candidates = (
                    db_snap.get_colony_candidates(
                        origin_coords=origin_coords,
                        min_fields_target=min_fields,
                        exclude_coords=own_coords_snap,
                        include_discarded=inc_discarded,
                        limit=int(limit),
                    )
                    if db_snap is not None
                    else []
                )
                discarded_count = (
                    len(db_snap.get_discarded_colony_coords())
                    if db_snap is not None and hasattr(db_snap, "get_discarded_colony_coords")
                    else 0
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
                max_planets = max(1, _safe_api_int(getattr(cfg_snap, "max_planets", 11), 11))
                if state_snap is not None:
                    planet_count = len(state_snap.planets)
                    allocated_normal_planets = count_allocated_normal_planets(state_snap)
                    for p in state_snap.planets:
                        colony_ships += max(0, _safe_api_int(p.ships.get("s8", 0)))
                        spy_probes += max(0, _safe_api_int(p.ships.get("s10", 0)))
                needed_colonies = max(0, max_planets - allocated_normal_planets)
                attempts_payload = self._colony_attempts_payload()
                return _sanitize_finite_json(
                    _redact_api_payload(
                        {
                            "ok": True,
                            "command": cmd,
                            "origin_coords": origin_coords,
                            "min_colony_fields": min_fields,
                            "allow_colonize": bool(cfg_snap.allow_colonize),
                            "allow_galaxy_scan": bool(cfg_snap.allow_galaxy_scan),
                            "colony_ships": colony_ships,
                            "spy_probes": spy_probes,
                            "planet_count": planet_count,
                            "allocated_normal_planets": allocated_normal_planets,
                            "max_planets": max_planets,
                            "needed_colonies": needed_colonies,
                            "all_planets_allocated": allocated_normal_planets >= max_planets,
                            "grade_counts": grade_counts,
                            "probed_count": probed_count,
                            "unprobed_count": unprobed_count,
                            "qualified_count": qualified_count,
                            "discarded_count": discarded_count,
                            "candidates": candidates,
                            **attempts_payload,
                        }
                    )
                )

        if cmd == "discard_colony_candidate":
            raw_tc = payload.get("target_coords") if payload.get("target_coords") is not None else payload.get("coords")
            if not isinstance(raw_tc, str) or not raw_tc.strip():
                raise ApiRequestError(f"{cmd!r} requires 'target_coords' formatted as GG:SSS:PP.")
            m_tc = re.search(r"(\d{1,2}:\d{1,3}:\d{1,2})", raw_tc.strip(), re.ASCII)
            if not m_tc:
                raise ApiRequestError(f"{cmd!r} requires 'target_coords' formatted as GG:SSS:PP.")
            norm_tc = normalize_coords(m_tc.group(1))
            with self._lock:
                if self._db is None:
                    raise ApiRequestError("Database is not configured on this API bridge.")
                changed = bool(self._db.discard_colony_candidate(norm_tc))
                self._invalidate_dashboard_cache()
                discarded_count = len(self._db.get_discarded_colony_coords())
                return _sanitize_finite_json(
                    _redact_api_payload(
                        {
                            "ok": True,
                            "command": cmd,
                            "target_coords": norm_tc,
                            "discarded": True,
                            "changed": changed,
                            "discarded_count": discarded_count,
                            "message": f"Discarded colony candidate {norm_tc}.",
                        }
                    )
                )

        if cmd == "universe_research_max":
            do_sync = bool(payload.get("sync", False))
            with self._lock:
                if do_sync and not dry_run and self._is_galaxy_scan_blocking_locked():
                    raise GalaxyScanInProgressError(self._galaxy_scan_busy_error_message())
                synced_live = False
                parsed_srv: dict[str, dict[str, Any]] = {}
                if (
                    do_sync
                    and not dry_run
                    and self._is_browser_port_open()
                    and not self._bot_control.get("captcha_blocked")
                    and self.rate_limit_remaining() <= 0.0
                ):
                    try:
                        with self._client_factory(self.debug_port) as client:
                            self._call_with_optional_priority(client.ensure_in_game, priority="interactive")
                            for act_name in ("internalStatistics", "internalToplist", "internalUniverse"):
                                if self.rate_limit_remaining() > 0.0:
                                    break
                                html_rec = ""
                                try:
                                    html_rec = self._call_with_optional_priority(
                                        client.navigate_menu,
                                        act_name,
                                        priority="interactive",
                                    )
                                except (RateLimitError, AntiBotChallengeError, SpaceInvasionAuthError):
                                    raise
                                except Exception:
                                    build_url = getattr(client, "_build_internal_url", None)
                                    res_sid = getattr(client, "_resolve_sid_query", None)
                                    mark_nav = getattr(client, "_mark_frame_navigation", None)
                                    wait_fr = getattr(client, "_wait_frame_ready", None)
                                    sw_fr = getattr(client, "_switch_frame", None)
                                    drv_obj = getattr(client, "driver", None)
                                    if (
                                        callable(build_url)
                                        and callable(res_sid)
                                        and callable(mark_nav)
                                        and callable(wait_fr)
                                        and callable(sw_fr)
                                        and drv_obj is not None
                                    ):
                                        try:
                                            sid_q = str(res_sid() or "")
                                            q_str = f"action={act_name}" + (f"&{sid_q}" if sid_q else "")
                                            tgt_url = build_url(q_str)
                                            tok = mark_nav("F_Hauptframe")
                                            sw_fr("F_Hauptframe")
                                            drv_obj.execute_script("location.href = arguments[0];", tgt_url)
                                            html_rec = wait_fr(
                                                "F_Hauptframe",
                                                expected_action=act_name,
                                                nav_token=tok,
                                            )
                                        except (RateLimitError, AntiBotChallengeError):
                                            raise
                                        except Exception:
                                            html_rec = ""
                                if isinstance(html_rec, str) and html_rec:
                                    synced_live = True
                                    found_recs = parse_universe_research_records(html_rec)
                                    if found_recs:
                                        parsed_srv.update(found_recs)
                                        break
                    except RateLimitError as rl_exc:
                        self._record_server_rate_limit(rl_exc)
                    except AntiBotChallengeError as ab_exc:
                        self._record_antibot_pause(ab_exc)
                    except Exception:
                        pass
                eff_st = self._last_state
                if self._db is not None and hasattr(self._db, "refresh_universe_research_max"):
                    if do_sync or parsed_srv:
                        rows = self._db.refresh_universe_research_max(
                            own_researches=(eff_st.researches if eff_st is not None else None),
                            own_nick=(eff_st.nickname if eff_st is not None else ""),
                            server_records=(parsed_srv or None),
                            source=("manual_sync" if do_sync else "api"),
                        )
                    else:
                        rows = self._db.get_universe_research_max(
                            own_researches=(eff_st.researches if eff_st is not None else None),
                            own_nick=(eff_st.nickname if eff_st is not None else ""),
                        )
                else:
                    rows = self._get_universe_research_max_locked(eff_st)
                if do_sync:
                    self._invalidate_dashboard_cache()
                return _sanitize_finite_json(
                    _redact_api_payload(
                        {
                            "ok": True,
                            "command": cmd,
                            "synced": synced_live or do_sync,
                            "live_pages_checked": synced_live,
                            "server_records_found": len(parsed_srv),
                            "rows": rows,
                            "universe_research_max": rows,
                            "message": (
                                f"Synced {len(rows)} research records ({len(parsed_srv)} server records, spy intel + own state merged)."
                                if do_sync
                                else f"Loaded {len(rows)} universe research records."
                            ),
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
            with self._intel_read_lock:
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
            with self._intel_read_lock:
                recycles = (
                    self._db.get_recycle_reports(limit=int(limit))
                    if self._db is not None
                    else []
                )
                own_coords_rec = [
                    str(p.coords or "")
                    for p in (self._last_state.planets if self._last_state is not None else ())
                    if getattr(p, "coords", "")
                ]
                get_deb_fn = getattr(self._db, "get_active_debris_targets", None) if self._db is not None else None
                if callable(get_deb_fn):
                    raw_debris = self._enrich_raid_targets(
                        get_deb_fn(origin_coords=own_coords_rec, limit=200, include_own=False)
                    )
                    debris_targets = [dict(t) for t in raw_debris if bool(t.get("has_debris"))]
                else:
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
                if len(recycles) < int(limit):
                    cat_counts["recycle_report"] = len(recycles)
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
                    paused_in = bool(payload["paused"])
                else:
                    paused_in = None
                if paused_in is not None:
                    self._bot_control["paused"] = paused_in
                    if paused_in and getattr(self, "_sweep_wake_event", None) is not None:
                        self._sweep_wake_event.set()
                    if not paused_in:
                        self._bot_control["captcha_blocked"] = False
                        self._bot_control["captcha_message"] = None
                        self._ack_captcha_notifications_locked()
                        self._invalidate_dashboard_cache()
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
                if isinstance(raw_cfg.get("fleet_research_targets"), Mapping):
                    merged["fleet_research_targets"] = {
                        **dict(getattr(prev_cfg, "fleet_research_targets", None) or {}),
                        **dict(raw_cfg["fleet_research_targets"]),
                    }
                new_cfg = strategy_config_from_dict(merged)
                if self.config_path is not None:
                    save_strategy_config(new_cfg, self.config_path)
                self.strategy_config = new_cfg
                if (
                    not bool(getattr(new_cfg, "allow_galaxy_scan", False))
                    and getattr(self, "_sweep_wake_event", None) is not None
                ):
                    self._sweep_wake_event.set()
                if self._db is not None:
                    save_cfg_fn = getattr(self._db, "save_strategy_config_snapshot", None)
                    if callable(save_cfg_fn):
                        save_cfg_fn(asdict(new_cfg))
                self._invalidate_dashboard_cache()
                if any(
                    getattr(new_cfg, k, None) != getattr(prev_cfg, k, None)
                    for k in (
                        "allow_galaxy_scan",
                        "galaxy_scan_block_other_actions",
                        "galaxy_sweep_pages",
                        "galaxy_scan_explore_first",
                        "galaxy_scan_refresh_pct",
                        "galaxy_scan_all_galaxies",
                        "galaxy_scan_near_weight",
                        "galaxy_scan_attack_weight",
                    )
                ):
                    self._smart_sweep_summary_ts = 0.0
                self._persist_runtime_state()
                for ex in (self._live_executor, self._dry_executor):
                    ex.allow_fleetsave = new_cfg.allow_fleetsave
                    ex.allow_raids = new_cfg.allow_raids
                    ex.allow_colonize = new_cfg.allow_colonize
                    ex.allow_transport = new_cfg.allow_transport
                    ex.allow_transmitter = new_cfg.allow_transmitter
                    ex.max_actions_per_hour = _safe_hourly_budget(new_cfg.max_actions_per_hour, 60)
                    if "max_actions_per_hour" in raw_cfg:
                        ex.enforce_hourly_cap = True
                self._sync_page_limiter_from_config(new_cfg)
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
                        "'planner_mode' override must be a non-empty string ('priority' or 'ai_planner')."
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
                    custom_names = self._get_custom_planet_names_locked()
                    ag_raw_state = (
                        self._attach_raid_stats(self._last_state)
                        if self._last_state is not None
                        else None
                    )
                    ag_state = (
                        self._apply_custom_planet_names_to_state(ag_raw_state, custom_names)
                        if ag_raw_state is not None
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
                            "custom_planet_names": custom_names,
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

        if cmd == "planner_jobs_status":
            with self._lock:
                snap = self._get_planner_jobs_snapshot()
            return _sanitize_finite_json(
                _redact_api_payload(
                    {
                        "ok": True,
                        "command": cmd,
                        **snap,
                    }
                )
            )

        if cmd == "config_optimizer_get":
            runs_limit = _parse_command_int(
                "limit",
                payload.get("limit", 20),
                min_val=1,
                max_val=200,
            ) or 20
            co_mod = self._load_config_optimizer_mod()
            default_params = dict(getattr(co_mod, "DEFAULT_CONFIG_OPTIMIZER_PARAMS", {}) or {})
            with self._lock:
                snap_state = self._get_planner_state_locked()
                gate = self._evaluate_ai_planner_gate_locked(snap_state)
                custom_names = self._get_custom_planet_names_locked()
                active_co = self._overlay_custom_names_on_config_optimizer(
                    self._get_active_config_optimizer_locked(),
                    snap_state,
                    custom_names,
                )
                runs = self._list_config_optimizer_runs_locked(limit=int(runs_limit))
                reserved_floors = self._get_reserved_floors_locked()
                planet_specializations = self._get_planet_specializations_locked()
                jobs_snap = self._get_planner_jobs_snapshot(snap_state)
            return _sanitize_finite_json(
                _redact_api_payload(
                    {
                        "ok": True,
                        "command": cmd,
                        "active": active_co,
                        "job": jobs_snap["config_optimizer_job"],
                        "runs": runs,
                        "reserved_floors": reserved_floors,
                        "planet_specializations": planet_specializations,
                        "custom_planet_names": custom_names,
                        "specialization_options": list(
                            getattr(
                                co_mod,
                                "VALID_PLANET_SPECIALIZATIONS",
                                ("auto", "metal", "kryptonite", "spice", "shipyard", "spice_shipyard"),
                            )
                        ),
                        "default_params": default_params,
                        "colonies_owned": gate["colonies_owned"],
                        "max_planets": gate["max_planets"],
                        "live_ai_unlocked": gate["live_ai_unlocked"],
                    }
                )
            )

        if cmd == "rename_planet":
            raw_batch = payload.get("planet_names")
            if raw_batch is not None:
                if not isinstance(raw_batch, Mapping) or not raw_batch:
                    raise ApiRequestError(
                        "'rename_planet' field 'planet_names' must be a non-empty dictionary."
                    )
                with self._lock:
                    snap_state = self._get_planner_state_locked()
                    if snap_state is None or not snap_state.planets:
                        raise ApiRequestError("No game state snapshot available yet; cannot validate planet_id.")
                    planets_by_id = {int(p.planet_id): p for p in snap_state.planets}
                    validated_updates: dict[int, str] = {}
                    for raw_pid, raw_nm in raw_batch.items():
                        pid_i = _parse_command_int(
                            "planet_id",
                            raw_pid,
                            min_val=1,
                            max_val=2**63 - 1,
                        )
                        if pid_i is None or pid_i not in planets_by_id:
                            raise ApiRequestError(f"Planet {raw_pid} does not belong to this empire.")
                        if raw_nm is not None and not isinstance(raw_nm, str):
                            raise ApiRequestError("'rename_planet' planet names must be strings.")
                        validated_updates[int(pid_i)] = "" if raw_nm is None else str(raw_nm)
                    updated_names = self._set_custom_planet_names_batch_locked(validated_updates)
                    updated_planets: list[dict[str, Any]] = []
                    for pid_i in validated_updates:
                        p_obj = planets_by_id[pid_i]
                        g_name = str(p_obj.name or "")
                        c_name = updated_names.get(str(pid_i)) or None
                        updated_planets.append(
                            {
                                "planet_id": pid_i,
                                "coords": str(p_obj.coords or ""),
                                "name": c_name or g_name,
                                "game_name": g_name,
                                "custom_name": c_name,
                            }
                        )
                first_p = updated_planets[0] if updated_planets else None
                return _sanitize_finite_json(
                    _redact_api_payload(
                        {
                            "ok": True,
                            "command": cmd,
                            "planet_id": first_p["planet_id"] if first_p else None,
                            "name": first_p["name"] if first_p else "",
                            "custom_name": first_p["custom_name"] if first_p else None,
                            "game_name": first_p["game_name"] if first_p else "",
                            "custom_planet_names": updated_names,
                            "planet": first_p,
                            "updated": updated_planets,
                        }
                    )
                )

            pid_val = _parse_command_int(
                "planet_id",
                payload.get("planet_id"),
                min_val=1,
                max_val=2**63 - 1,
            )
            if pid_val is None:
                raise ApiRequestError("'rename_planet' requires integer 'planet_id' or dict 'planet_names'.")
            raw_name = payload.get("name", "")
            if raw_name is not None and not isinstance(raw_name, str):
                raise ApiRequestError("'rename_planet' field 'name' must be a string.")
            with self._lock:
                snap_state = self._get_planner_state_locked()
                if snap_state is None or not snap_state.planets:
                    raise ApiRequestError("No game state snapshot available yet; cannot validate planet_id.")
                target_p = next((p for p in snap_state.planets if int(p.planet_id) == int(pid_val)), None)
                if target_p is None:
                    raise ApiRequestError(f"Planet {pid_val} does not belong to this empire.")
                updated_names = self._set_custom_planet_name_locked(
                    int(pid_val),
                    "" if raw_name is None else str(raw_name),
                )
                g_name = str(target_p.name or "")
                c_name = updated_names.get(str(pid_val)) or None
                eff_name = c_name or g_name
                planet_info = {
                    "planet_id": int(pid_val),
                    "coords": str(target_p.coords or ""),
                    "name": eff_name,
                    "game_name": g_name,
                    "custom_name": c_name,
                }
            return _sanitize_finite_json(
                _redact_api_payload(
                    {
                        "ok": True,
                        "command": cmd,
                        "planet_id": int(pid_val),
                        "name": eff_name,
                        "custom_name": c_name,
                        "game_name": g_name,
                        "custom_planet_names": updated_names,
                        "planet": planet_info,
                    }
                )
            )

        if cmd == "config_optimizer_set_specializations":
            raw_specs = payload.get("planet_specializations")
            if not isinstance(raw_specs, Mapping):
                raise ApiRequestError(
                    "'config_optimizer_set_specializations' field 'planet_specializations' must be a dictionary."
                )
            replace_raw = payload.get("replace", False)
            if not isinstance(replace_raw, bool):
                raise ApiRequestError(
                    "'config_optimizer_set_specializations' field 'replace' must be a boolean."
                )
            with self._lock:
                updated_specs = self._update_planet_specializations_locked(
                    raw_specs,
                    replace=bool(replace_raw),
                )
                self._invalidate_dashboard_cache()
            return _sanitize_finite_json(
                _redact_api_payload(
                    {
                        "ok": True,
                        "command": cmd,
                        "planet_specializations": updated_specs,
                        "locked_planets_count": len(updated_specs),
                    }
                )
            )

        if cmd == "config_optimizer_run":
            raw_params = payload.get("params")
            if raw_params is not None and not isinstance(raw_params, Mapping):
                raise ApiRequestError("'config_optimizer_run' field 'params' must be a dictionary.")
            auto_replan_raw = payload.get("auto_replan_ai", True)
            if not isinstance(auto_replan_raw, bool):
                raise ApiRequestError("'config_optimizer_run' field 'auto_replan_ai' must be a boolean.")
            sync_raw = payload.get("sync", False)
            if not isinstance(sync_raw, bool):
                raise ApiRequestError("'config_optimizer_run' field 'sync' must be a boolean.")
            trig = _bounded_api_str(payload.get("trigger_source") or "manual", default="manual")[:64]
            resp = self._start_config_optimizer_job(
                params=raw_params,
                auto_replan_ai=bool(auto_replan_raw),
                sync=bool(sync_raw),
                trigger_source=trig,
            )
            return _sanitize_finite_json(_redact_api_payload(resp))

        if cmd == "config_optimizer_yearly_projection":
            horizon_years = _parse_command_int(
                "horizon_years",
                payload.get("horizon_years", 5),
                min_val=1,
                max_val=15,
            ) or 5
            co_mod = self._load_config_optimizer_mod()
            with self._intel_read_lock:
                active_co = self._get_active_config_optimizer_locked()
                snap_state = self._get_planner_state_locked()
            if not isinstance(active_co, Mapping) or not isinstance(active_co.get("result"), Mapping):
                raise ApiRequestError(
                    "No active Config Optimizer result available yet; run Config Optimizer first."
                )
            cache_key = (
                active_co.get("run_id"),
                active_co.get("updated_ts"),
                int(horizon_years),
                round(float(getattr(snap_state, "timestamp", 0.0) or 0.0), 0) if snap_state is not None else 0.0,
            )
            cached_proj = self._co_yearly_proj_cache.get(cache_key)
            if cached_proj is None:
                cached_proj = co_mod.compute_yearly_ships_per_week_projection(
                    active_co["result"],
                    snap_state,
                    horizon_years=int(horizon_years),
                )
                if len(self._co_yearly_proj_cache) >= 16:
                    self._co_yearly_proj_cache.clear()
                self._co_yearly_proj_cache[cache_key] = cached_proj
            return _sanitize_finite_json(
                _redact_api_payload(
                    {
                        "ok": True,
                        "command": cmd,
                        "projection": cached_proj,
                    }
                )
            )

        if cmd == "config_optimizer_update_planet":
            raw_blds = payload.get("target_buildings")
            if not isinstance(raw_blds, Mapping):
                raise ApiRequestError(
                    "'config_optimizer_update_planet' requires a dictionary 'target_buildings'."
                )
            slot_val = _parse_command_int(
                "slot",
                payload.get("slot"),
                allow_none=True,
                min_val=1,
                max_val=100,
            )
            pid_val = _parse_command_int(
                "planet_id",
                payload.get("planet_id"),
                allow_none=True,
                min_val=-1000,
                max_val=2**63 - 1,
            )
            coords_val = (
                str(payload.get("coords") or "").strip()
                if payload.get("coords") is not None
                else None
            )
            fm_val = _parse_command_int(
                "fields_max_override",
                payload.get("fields_max_override"),
                allow_none=True,
                min_val=1,
                max_val=2000,
            )
            spec_raw = payload.get("locked_specialization")
            if spec_raw is not None and not isinstance(spec_raw, str):
                raise ApiRequestError("'locked_specialization' must be a string.")
            pname_raw = payload.get("planet_name")
            if pname_raw is not None and not isinstance(pname_raw, str):
                raise ApiRequestError("'planet_name' must be a string.")
            lock_for_ga = payload.get("lock_for_ga", True)
            if not isinstance(lock_for_ga, bool):
                raise ApiRequestError("'lock_for_ga' must be a boolean.")
            auto_wh = payload.get("auto_warehouses", False)
            if not isinstance(auto_wh, bool):
                raise ApiRequestError("'auto_warehouses' must be a boolean.")
            auto_replan_ai = payload.get("auto_replan_ai", True)
            if not isinstance(auto_replan_ai, bool):
                raise ApiRequestError("'auto_replan_ai' must be a boolean.")

            co_mod = self._load_config_optimizer_mod()
            with self._lock:
                snap_state = self._get_planner_state_locked()
                if snap_state is None:
                    raise ApiRequestError("No game state snapshot available yet; open /dashboard first.")
                if pname_raw is not None and pid_val is not None and int(pid_val) > 0:
                    if any(int(p.planet_id) == int(pid_val) for p in snap_state.planets):
                        self._set_custom_planet_name_locked(int(pid_val), pname_raw)
                uni_max = self._snapshot_universe_research_max_map_locked(snap_state)
                reserved_floors = self._get_reserved_floors_locked()
                persisted_specs = self._get_planet_specializations_locked()
                custom_names = self._get_custom_planet_names_locked()
                active_co = self._get_active_config_optimizer_locked()
                cfg_snap = self.strategy_config

            base_res = (
                dict(active_co["result"])
                if isinstance(active_co, Mapping) and isinstance(active_co.get("result"), Mapping)
                else None
            )
            merged_params = dict(getattr(co_mod, "DEFAULT_CONFIG_OPTIMIZER_PARAMS", {}) or {})
            if isinstance(active_co, Mapping) and isinstance(active_co.get("params"), Mapping):
                merged_params.update(dict(active_co["params"]))
            merged_params["planet_specializations"] = dict(persisted_specs)
            merged_params["planet_names"] = dict(custom_names)
            merged_params["custom_planet_names"] = dict(custom_names)
            merged_params["max_planets"] = max(1, _safe_api_int(getattr(cfg_snap, "max_planets", 11), 11))
            merged_floors = {
                str(pk): {str(bk): max(0, _safe_api_int(lv, 0)) for bk, lv in bm.items()}
                for pk, bm in reserved_floors.items()
                if isinstance(bm, Mapping)
            }
            merged_params["reserved_floors_by_planet"] = merged_floors

            try:
                updated_res = co_mod.apply_manual_planet_override(
                    snap_state,
                    target_buildings=raw_blds,
                    base_result=base_res,
                    slot=slot_val,
                    planet_id=pid_val,
                    coords=coords_val,
                    fields_max_override=fm_val,
                    locked_specialization=spec_raw,
                    planet_name=pname_raw,
                    lock_for_ga=bool(lock_for_ga),
                    auto_warehouses=bool(auto_wh),
                    universe_research_max=uni_max,
                    params=merged_params,
                )
            except ValueError as exc:
                raise ApiRequestError(str(_redact_api_payload(exc))) from exc

            out_params = (
                dict(updated_res.get("meta_parameters") or merged_params)
                if isinstance(updated_res.get("meta_parameters"), Mapping)
                else merged_params
            )
            run_id = self._persist_config_optimizer_result(
                updated_res,
                params=out_params,
                reserved_floors=merged_floors,
                trigger_source="manual_planet_edit",
                elapsed_ms=float(updated_res.get("elapsed_ms", 0.0) or 0.0),
            )
            with self._lock:
                if spec_raw is not None and isinstance(out_params.get("planet_specializations"), Mapping):
                    updated_specs = self._update_planet_specializations_locked(
                        out_params["planet_specializations"],
                        replace=True,
                    )
                else:
                    updated_specs = self._get_planet_specializations_locked()
                custom_names_after = self._get_custom_planet_names_locked()
                overlaid_active = self._overlay_custom_names_on_config_optimizer(
                    self._get_active_config_optimizer_locked(),
                    snap_state,
                    custom_names_after,
                )
                self._co_yearly_proj_cache.clear()
                self._invalidate_dashboard_cache()

            ai_job_out: Optional[dict[str, Any]] = None
            if auto_replan_ai and not self._planner_closed:
                with contextlib.suppress(Exception):
                    ai_resp = self._start_ai_planner_job(
                        params=None,
                        sync=False,
                        trigger_source="auto_replan:manual_planet_edit",
                        auto_run_config_if_missing=False,
                        override_config_run_id=run_id,
                        override_config_result=updated_res,
                    )
                    if isinstance(ai_resp, Mapping) and isinstance(ai_resp.get("job"), Mapping):
                        ai_job_out = dict(ai_resp["job"])

            return _sanitize_finite_json(
                _redact_api_payload(
                    {
                        "ok": True,
                        "command": cmd,
                        "run_id": run_id,
                        "active": overlaid_active,
                        "result": overlaid_active["result"] if isinstance(overlaid_active, Mapping) else updated_res,
                        "planet_specializations": updated_specs,
                        "custom_planet_names": custom_names_after,
                        "ai_planner_job": ai_job_out,
                    }
                )
            )

        if cmd == "ai_planner_get":
            window_size = _parse_command_int(
                "window_size",
                payload.get("window_size", 40),
                min_val=5,
                max_val=200,
            ) or 40
            week_filter = _parse_command_int(
                "week",
                payload.get("week"),
                allow_none=True,
                min_val=1,
                max_val=520,
            )
            raw_pid_filter = payload.get("planet_id")
            planet_filter: Optional[int | str] = None
            if raw_pid_filter not in (None, "", "all"):
                if isinstance(raw_pid_filter, int) and not isinstance(raw_pid_filter, bool):
                    planet_filter = int(raw_pid_filter)
                elif isinstance(raw_pid_filter, str) and raw_pid_filter.strip():
                    s_pf = raw_pid_filter.strip()
                    planet_filter = int(s_pf) if s_pf.isascii() and s_pf.isdigit() else s_pf
                else:
                    raise ApiRequestError("'ai_planner_get' field 'planet_id' must be an integer or string.")
            page = _parse_command_int("page", payload.get("page", 1), min_val=1, max_val=100_000) or 1
            page_size = _parse_command_int(
                "page_size",
                payload.get("page_size", 50),
                min_val=5,
                max_val=500,
            ) or 50
            search_raw = payload.get("search")
            search_q = str(search_raw).strip().lower() if isinstance(search_raw, str) else ""
            runs_limit = _parse_command_int("limit", payload.get("limit", 20), min_val=1, max_val=200) or 20

            ai_mod = self._load_ai_planner_mod()
            build_ai_graph, _ = self._load_action_graph_builders()
            default_params = dict(getattr(ai_mod, "DEFAULT_AI_PLANNER_PARAMS", {}) or {})
            with self._lock:
                snap_state = self._get_planner_state_locked()
                gate = self._evaluate_ai_planner_gate_locked(snap_state)
                custom_names = self._get_custom_planet_names_locked()
                display_state = (
                    self._apply_custom_planet_names_to_state(snap_state, custom_names)
                    if snap_state is not None
                    else None
                )
                jobs_snap = self._get_planner_jobs_snapshot(snap_state)
                runs = self._list_ai_planner_runs_locked(limit=int(runs_limit))
                active_result = gate["active_result"]
                live_telemetry = self._compute_live_attack_telemetry_locked(snap_state)
                cfg_snap = self.strategy_config
                window_24h_snap = self._summarize_adaptive_idle_window_24h_locked()
                raw_td_locked = (
                    active_result.get("transport_demand")
                    if isinstance(active_result, Mapping) and isinstance(active_result.get("transport_demand"), Mapping)
                    else None
                )
                transport_fleet_snap = self._build_transport_fleet_summary_locked(
                    snap_state,
                    raw_td_locked,
                )
                realized_util_snap = (
                    transport_fleet_snap["realized_utilization"]
                    if isinstance(transport_fleet_snap, Mapping)
                    and transport_fleet_snap.get("realized_utilization") is not None
                    else round(
                        min(self.TRANSPORT_UTIL_MAX, max(self.TRANSPORT_UTIL_MIN, float(self._transport_realized_utilization))),
                        4,
                    )
                )
                transport_demand_snap: Optional[dict[str, Any]] = None
                if isinstance(raw_td_locked, Mapping):
                    transport_demand_snap = dict(raw_td_locked)
                    transport_demand_snap["realized_utilization"] = realized_util_snap
                drift_eval = self._evaluate_ai_drift_replan_locked(snap_state, cfg=cfg_snap, gate=gate)
                auto_replan_snap = {
                    "cooldown_seconds": int(self.AI_DRIFT_REPLAN_COOLDOWN_SECONDS),
                    "last_auto_replan_ts": (
                        round(float(self._last_auto_ai_replan_ts), 3)
                        if self._last_auto_ai_replan_ts > 0.0
                        else None
                    ),
                    "last_trigger_reason": str(self._last_auto_ai_replan_reason or ""),
                    "drift_ready": bool(drift_eval.get("drift_ready", False)),
                }

            has_plan = isinstance(active_result, Mapping)
            active_summary = (
                dict(active_result.get("summary") or {})
                if has_plan and isinstance(active_result.get("summary"), Mapping)
                else None
            )
            meta_parameters = (
                dict(active_result.get("meta_parameters") or {})
                if has_plan and isinstance(active_result.get("meta_parameters"), Mapping)
                else dict(default_params)
            )
            weekly_breakdown = (
                list(active_result.get("weekly_breakdown") or [])
                if has_plan and isinstance(active_result.get("weekly_breakdown"), Sequence)
                else []
            )
            plan_telemetry = (
                dict(active_result.get("attack_telemetry") or {})
                if has_plan and isinstance(active_result.get("attack_telemetry"), Mapping)
                else live_telemetry
            )

            raw_all_steps = (
                list(active_result.get("steps") or [])
                if has_plan and isinstance(active_result.get("steps"), Sequence)
                else []
            )
            all_steps = self._overlay_custom_names_on_ai_items(
                [s for s in raw_all_steps if isinstance(s, Mapping)],
                snap_state,
                custom_names,
            )
            filtered_steps: list[dict[str, Any]] = []
            for st_item in all_steps:
                if not isinstance(st_item, Mapping):
                    continue
                if week_filter is not None and _safe_api_int(st_item.get("week"), 0) != int(week_filter):
                    continue
                if planet_filter is not None:
                    spid = st_item.get("planet_id")
                    scoords = str(st_item.get("planet_coords") or "")
                    if isinstance(planet_filter, int):
                        if _safe_api_int(spid, -1) != planet_filter:
                            continue
                    else:
                        if str(spid) != planet_filter and scoords != planet_filter and str(st_item.get("planet_name") or "") != planet_filter:
                            continue
                if search_q:
                    haystack = " ".join(
                        str(st_item.get(k) or "")
                        for k in ("seq", "kind", "entity_key", "entity_name", "planet_name", "planet_coords", "week")
                    ).lower()
                    if search_q not in haystack and f"w{st_item.get('week', '')}" not in search_q:
                        continue
                filtered_steps.append(dict(st_item))
            total_steps = len(filtered_steps)
            offset = (int(page) - 1) * int(page_size)
            page_items = filtered_steps[offset : offset + int(page_size)]

            in_progress_snap = (
                dict(active_result.get("in_progress") or {})
                if has_plan and isinstance(active_result.get("in_progress"), Mapping)
                else None
            )
            transport_matrix_snap = (
                dict(active_result.get("transport_matrix") or {})
                if has_plan and isinstance(active_result.get("transport_matrix"), Mapping)
                else None
            )
            smelting_snap: Optional[dict[str, Any]] = None
            if has_plan and isinstance(active_result.get("smelting"), Mapping):
                smelting_snap = dict(active_result.get("smelting") or {})
                coords_to_cname: dict[str, str] = {}
                if snap_state is not None:
                    for pl_s in snap_state.planets or ():
                        cn_s = custom_names.get(str(pl_s.planet_id))
                        nc_s = normalize_coords(pl_s.coords)
                        if cn_s and nc_s:
                            coords_to_cname[nc_s] = cn_s
                for list_key in ("planets", "initial_planets_needing_toggle"):
                    raw_rows = smelting_snap.get(list_key)
                    if isinstance(raw_rows, Sequence) and not isinstance(raw_rows, (str, bytes)):
                        patched_rows: list[dict[str, Any]] = []
                        for r in raw_rows:
                            if not isinstance(r, Mapping):
                                continue
                            rd = dict(r)
                            rpid = _safe_api_int(rd.get("planet_id"), 0)
                            if rpid > 0 and custom_names.get(str(rpid)):
                                rd["name"] = custom_names[str(rpid)]
                            patched_rows.append(rd)
                        smelting_snap[list_key] = patched_rows
                raw_evs = smelting_snap.get("events")
                if isinstance(raw_evs, Sequence) and not isinstance(raw_evs, (str, bytes)):
                    patched_evs: list[dict[str, Any]] = []
                    for ev in raw_evs:
                        if not isinstance(ev, Mapping):
                            continue
                        evd = dict(ev)
                        ev_pid = _safe_api_int(evd.get("step_planet_id"), 0)
                        ev_nc = normalize_coords(str(evd.get("step_planet_coords") or ""))
                        ev_cn = (
                            (custom_names.get(str(ev_pid)) if ev_pid > 0 else None)
                            or (coords_to_cname.get(ev_nc) if ev_nc else None)
                        )
                        if ev_cn:
                            evd["step_planet_name"] = ev_cn
                        patched_evs.append(evd)
                    smelting_snap["events"] = patched_evs
            live_blast_furnace = (
                ai_mod.summarize_live_blast_furnace_state(display_state if display_state is not None else snap_state)
                if hasattr(ai_mod, "summarize_live_blast_furnace_state")
                else None
            )
            search_snap: Optional[dict[str, Any]] = None
            if has_plan and isinstance(active_result.get("search"), Mapping):
                raw_s = active_result["search"]
                search_snap = {
                    "best_strategy": raw_s.get("best_strategy"),
                    "best_parameters": (
                        dict(raw_s["best_parameters"])
                        if isinstance(raw_s.get("best_parameters"), Mapping)
                        else None
                    ),
                    "tuned_genes": (
                        dict(raw_s["tuned_genes"])
                        if isinstance(raw_s.get("tuned_genes"), Mapping)
                        else None
                    ),
                    "best_reward": raw_s.get("best_reward"),
                    "genetic_algorithm_ran": bool(raw_s.get("genetic_algorithm_ran", False)),
                    "why": raw_s.get("why"),
                }
            adaptive_status: Optional[dict[str, Any]] = None
            diag_state = display_state if display_state is not None else snap_state
            diag_fn = getattr(ai_mod, "diagnose_ai_colony_idle_states", None)
            if diag_state is not None and callable(diag_fn):
                try:
                    raw_diag = diag_fn(
                        diag_state,
                        active_result if has_plan else {},
                        config=cfg_snap,
                        field_conflicts=jobs_snap.get("pending_field_conflicts") or (),
                    )
                except Exception:
                    raw_diag = None
                if isinstance(raw_diag, Mapping):
                    diag_cols: list[dict[str, Any]] = []
                    for col_item in raw_diag.get("colonies") or ():
                        if not isinstance(col_item, Mapping):
                            continue
                        cd = dict(col_item)
                        cpid = _safe_api_int(cd.get("planet_id"), 0)
                        if cpid > 0 and custom_names.get(str(cpid)):
                            cd["name"] = custom_names[str(cpid)]
                        diag_cols.append(cd)
                    adaptive_status = {
                        "enabled": bool(getattr(cfg_snap, "ai_adaptive_execution", False)),
                        "active_builders": max(0, _safe_api_int(raw_diag.get("active_builders", 0), 0)),
                        "total_colonies": max(0, _safe_api_int(raw_diag.get("total_colonies", len(diag_cols)), len(diag_cols))),
                        "colonies": diag_cols,
                        "counts": (
                            {str(k): max(0, _safe_api_int(v, 0)) for k, v in raw_diag["counts"].items()}
                            if isinstance(raw_diag.get("counts"), Mapping)
                            else {}
                        ),
                        "idle_with_affordable_count": max(0, _safe_api_int(raw_diag.get("idle_with_affordable_count", 0), 0)),
                        "ready_not_started_count": max(0, _safe_api_int(raw_diag.get("ready_not_started_count", 0), 0)),
                        "window_24h": window_24h_snap,
                        "transport_fleet": transport_fleet_snap,
                        "auto_replan": auto_replan_snap,
                    }
            if adaptive_status is None:
                adaptive_status = {
                    "enabled": bool(getattr(cfg_snap, "ai_adaptive_execution", False)),
                    "active_builders": 0,
                    "total_colonies": 0,
                    "colonies": [],
                    "counts": {k: 0 for k in self._ADAPTIVE_IDLE_STATE_KEYS},
                    "idle_with_affordable_count": 0,
                    "ready_not_started_count": 0,
                    "window_24h": window_24h_snap,
                    "transport_fleet": transport_fleet_snap,
                    "auto_replan": auto_replan_snap,
                }
            graph_obj = build_ai_graph(
                display_state if display_state is not None else snap_state,
                active_result,
                window_size=int(window_size),
                week_filter=week_filter,
                planet_filter=planet_filter,
            )
            return _sanitize_finite_json(
                _redact_api_payload(
                    {
                        "ok": True,
                        "command": cmd,
                        "has_plan": has_plan,
                        "active_summary": active_summary,
                        "meta_parameters": meta_parameters,
                        "weekly_breakdown": weekly_breakdown,
                        "attack_telemetry": plan_telemetry,
                        "live_attack_telemetry": live_telemetry,
                        "in_progress": in_progress_snap,
                        "smelting": smelting_snap,
                        "live_blast_furnace": live_blast_furnace,
                        "transport_matrix": transport_matrix_snap,
                        "transport_demand": transport_demand_snap,
                        "search": search_snap,
                        "adaptive_status": adaptive_status,
                        "graph": graph_obj,
                        "steps_page": {
                            "items": page_items,
                            "total": total_steps,
                            "page": int(page),
                            "page_size": int(page_size),
                        },
                        "job": jobs_snap["ai_planner_job"],
                        "runs": runs,
                        "custom_planet_names": custom_names,
                        "colonies_owned": gate["colonies_owned"],
                        "max_planets": gate["max_planets"],
                        "live_ai_unlocked": gate["live_ai_unlocked"],
                        "active_planner_mode": gate["active_planner_mode"],
                        "effective_planner_mode": gate["effective_planner_mode"],
                        "fallback_reason": gate["fallback_reason"],
                        "pending_field_conflicts": jobs_snap["pending_field_conflicts"],
                        "default_params": default_params,
                    }
                )
            )

        if cmd == "ai_planner_run":
            raw_params = payload.get("params")
            if raw_params is not None and not isinstance(raw_params, Mapping):
                raise ApiRequestError("'ai_planner_run' field 'params' must be a dictionary.")
            if isinstance(raw_params, Mapping):
                for delay_key in (
                    "transport_dispatch_delay_minutes",
                    "PATH_TRANSPORT_DISPATCH_DELAY_MINUTES",
                    "fleet_dispatch_delay_minutes",
                ):
                    if delay_key in raw_params and raw_params.get(delay_key) is not None:
                        raw_delay = raw_params.get(delay_key)
                        if isinstance(raw_delay, bool):
                            raise ApiRequestError(
                                f"'ai_planner_run' param '{delay_key}' must be a number between 0 and 1440."
                            )
                        try:
                            delay_val = float(raw_delay)
                        except (TypeError, ValueError) as exc:
                            raise ApiRequestError(
                                f"'ai_planner_run' param '{delay_key}' must be a number between 0 and 1440."
                            ) from exc
                        if not math.isfinite(delay_val) or delay_val < 0.0 or delay_val > 1440.0:
                            raise ApiRequestError(
                                f"'ai_planner_run' param '{delay_key}' must be between 0 and 1440 minutes."
                            )
            sync_raw = payload.get("sync", False)
            if not isinstance(sync_raw, bool):
                raise ApiRequestError("'ai_planner_run' field 'sync' must be a boolean.")
            refresh_raw = payload.get("refresh", False)
            if not isinstance(refresh_raw, bool):
                raise ApiRequestError("'ai_planner_run' field 'refresh' must be a boolean.")
            auto_cfg_raw = payload.get("auto_run_config_if_missing", True)
            if not isinstance(auto_cfg_raw, bool):
                raise ApiRequestError(
                    "'ai_planner_run' field 'auto_run_config_if_missing' must be a boolean."
                )
            trig = _bounded_api_str(payload.get("trigger_source") or "manual", default="manual")[:64]
            if bool(refresh_raw):
                with self._lock:
                    if self._is_galaxy_scan_blocking_locked():
                        raise GalaxyScanInProgressError(self._galaxy_scan_busy_error_message())
            resp = self._start_ai_planner_job(
                params=raw_params,
                sync=bool(sync_raw),
                refresh=bool(refresh_raw),
                trigger_source=trig,
                auto_run_config_if_missing=bool(auto_cfg_raw),
            )
            return _sanitize_finite_json(_redact_api_payload(resp))

        if cmd == "resolve_field_conflict":
            raw_ck = payload.get("conflict_key")
            if not isinstance(raw_ck, str) or not raw_ck.strip():
                raise ApiRequestError("'resolve_field_conflict' requires a non-empty 'conflict_key'.")
            conflict_key = raw_ck.strip()
            raw_dec = payload.get("decision")
            if raw_dec is None:
                raw_dec = payload.get("action")
            if not isinstance(raw_dec, str) or raw_dec.strip().lower() not in ("demolish", "keep_reserve"):
                raise ApiRequestError(
                    "'resolve_field_conflict' requires 'decision' of 'demolish' or 'keep_reserve'."
                )
            decision = raw_dec.strip().lower()
            sync_raw = payload.get("sync", False)
            if not isinstance(sync_raw, bool):
                raise ApiRequestError("'resolve_field_conflict' field 'sync' must be a boolean.")
            override_bkey = payload.get("candidate_building_key") or payload.get("building_key")

            with self._lock:
                all_conflicts = self._list_field_conflicts_locked(status=None)
                target_conflict = next(
                    (c for c in all_conflicts if str(c.get("conflict_key") or "") == conflict_key),
                    None,
                )
                if target_conflict is None:
                    raise ApiRequestError(f"Unknown field conflict {conflict_key!r}.")

                pid = _safe_api_int(target_conflict.get("planet_id", 0), 0)
                pcoords = str(target_conflict.get("planet_coords") or "")
                cand_bkey = str(
                    override_bkey
                    or target_conflict.get("candidate_building_key")
                    or ""
                ).strip()
                cand_lvl = _safe_api_int(target_conflict.get("candidate_level", 0), 0)
                detail_m = target_conflict.get("detail") if isinstance(target_conflict.get("detail"), Mapping) else {}
                cands_list = detail_m.get("candidates") if isinstance(detail_m.get("candidates"), Sequence) else ()
                if not cand_bkey and cands_list:
                    first_c = cands_list[0]
                    if isinstance(first_c, Mapping):
                        cand_bkey = str(first_c.get("building_key") or first_c.get("key") or "").strip()
                        cand_lvl = max(
                            cand_lvl,
                            _safe_api_int(first_c.get("level", first_c.get("current_level", 0)), 0),
                        )
                elif override_bkey and cands_list:
                    for c_it in cands_list:
                        if isinstance(c_it, Mapping) and str(c_it.get("building_key") or c_it.get("key") or "") == cand_bkey:
                            cand_lvl = max(
                                1,
                                _safe_api_int(c_it.get("level", c_it.get("current_level", cand_lvl)), cand_lvl),
                            )
                            break

                m_bid = re.fullmatch(r"b?(\d{1,3})", cand_bkey.lower(), re.ASCII)
                if not m_bid:
                    raise ApiRequestError(
                        f"Field conflict {conflict_key!r} has invalid candidate building key {cand_bkey!r}."
                    )
                bid = int(m_bid.group(1))
                cand_bkey = f"b{bid}"
                binfo = game_data.BUILDING_BY_ID.get(bid)
                bname = (
                    str(binfo.get("name") or f"Building b{bid}")
                    if isinstance(binfo, Mapping)
                    else f"Building b{bid}"
                )
                snap_state = self._get_planner_state_locked()
                planet_obj: Optional[PlanetState] = None
                if snap_state is not None:
                    planet_obj = next(
                        (
                            p
                            for p in snap_state.planets
                            if (pid > 0 and p.planet_id == pid) or (pcoords and p.coords == pcoords)
                        ),
                        None,
                    )
                    if planet_obj is not None:
                        pid = planet_obj.planet_id
                        pcoords = planet_obj.coords

                if decision == "demolish":
                    safe_ok, safe_reason, live_lvl = self._check_demolish_energy_safety(
                        snap_state, planet_obj, bid
                    )
                    if not safe_ok:
                        raise ApiRequestError(safe_reason)
                    if self._db is None or not hasattr(self._db, "enqueue_manual_command"):
                        raise ApiRequestError("Database is not configured for manual command queuing.")
                    queued_item = self._db.enqueue_manual_command(
                        kind="DEMOLISH_BUILDING",
                        priority="P1",
                        position="top",
                        planet_id=int(pid),
                        planet_coords=pcoords,
                        target_id=bid,
                        target_name=bname,
                        target_level=max(0, live_lvl - 1),
                        note=f"Approved demolition via field conflict {conflict_key}",
                        deduplicate_pending=True,
                    )
                    if hasattr(self._db, "resolve_field_conflict_prompt"):
                        resolved = self._db.resolve_field_conflict_prompt(
                            conflict_key,
                            "demolish",
                            detail={
                                "building_key": cand_bkey,
                                "queued_demolish_id": queued_item.get("id"),
                            },
                        )
                    else:
                        target_conflict["status"] = "resolved"
                        target_conflict["decision"] = "demolish"
                        resolved = dict(target_conflict)
                    self._invalidate_dashboard_cache()
                    return _sanitize_finite_json(
                        _redact_api_payload(
                            {
                                "ok": True,
                                "command": cmd,
                                "conflict": resolved,
                                "queued_demolish": queued_item,
                            }
                        )
                    )

                # decision == "keep_reserve": record floor & chain config_optimizer -> ai_planner rerun
                live_lvl = planet_obj.building_level(bid) if planet_obj is not None else 0
                min_lvl = max(1, cand_lvl, live_lvl)
                planet_key = str(pid) if pid > 0 else pcoords
                reserved_floors = self._update_reserved_floor_locked(
                    planet_key,
                    cand_bkey,
                    min_lvl,
                    reason=f"Field conflict {conflict_key}",
                )
                if self._db is not None and hasattr(self._db, "resolve_field_conflict_prompt"):
                    resolved = self._db.resolve_field_conflict_prompt(
                        conflict_key,
                        "keep_reserve",
                        detail={
                            "planet_key": planet_key,
                            "building_key": cand_bkey,
                            "min_level": min_lvl,
                        },
                    )
                else:
                    target_conflict["status"] = "resolved"
                    target_conflict["decision"] = "keep_reserve"
                    resolved = dict(target_conflict)
                self._invalidate_dashboard_cache()

            # Trigger chained Config Optimizer -> AI-Planner rerun outside `_lock`!
            rerun_resp = self._start_config_optimizer_job(
                params=None,
                auto_replan_ai=True,
                sync=bool(sync_raw),
                trigger_source="field_conflict_keep_reserve",
            )
            out_res: dict[str, Any] = {
                "ok": True,
                "command": cmd,
                "conflict": resolved,
                "reserved_floors": reserved_floors,
                "job": rerun_resp.get("job"),
            }
            if "ai_planner_job" in rerun_resp:
                out_res["ai_planner_job"] = rerun_resp["ai_planner_job"]
            return _sanitize_finite_json(_redact_api_payload(out_res))

        if cmd == "fleet_planner_get":
            raw_fp_cfg = payload.get("config")
            if raw_fp_cfg is not None:
                if not isinstance(raw_fp_cfg, Mapping):
                    raise ApiRequestError("'fleet_planner_get' field 'config' must be a dictionary.")
                _validate_config_patch(raw_fp_cfg, strict_bools=False)
            _, build_fleet_graph = self._load_action_graph_builders()
            with self._lock:
                effective_fp_cfg, _ = merge_strategy_config_overrides(
                    self.strategy_config,
                    raw_fp_cfg,
                )
                snap_state = self._get_planner_state_locked()
                custom_names = self._get_custom_planet_names_locked()
                raw_enriched_fp = (
                    self._merge_colony_candidates_if_needed(
                        self._attach_raid_stats(snap_state),
                        effective_fp_cfg,
                    )
                    if snap_state is not None
                    else None
                )
                enriched_fp = (
                    self._apply_custom_planet_names_to_state(raw_enriched_fp, custom_names)
                    if raw_enriched_fp is not None
                    else None
                )
                atk_tel = self._compute_live_attack_telemetry_locked(enriched_fp)
                active_ai = self._get_active_ai_planner_locked()
                active_ai_res = (
                    active_ai.get("result")
                    if isinstance(active_ai, Mapping) and isinstance(active_ai.get("result"), Mapping)
                    else None
                )
                reserved_cost = Resources()
                priority_resource_need: dict[int, Resources] = {}
                spare_pct: Any = float(getattr(effective_fp_cfg, "ai_resource_spare_pct", 10.0))
                if enriched_fp is not None and active_ai_res is not None:
                    meta_p = active_ai_res.get("meta_parameters")
                    if isinstance(meta_p, Mapping) and meta_p.get("resource_spare_percent") is not None:
                        spare_pct = meta_p["resource_spare_percent"]
                    try:
                        ai_mod = self._load_ai_planner_mod()
                        ai_acts, _ = ai_mod.reconcile_live_ai_actions(
                            enriched_fp,
                            active_ai_res,
                            effective_fp_cfg,
                        )
                        reserved_cost, priority_resource_need = self._extract_ai_resource_targets(ai_acts)
                    except ApiRequestError:
                        ai_acts = []
                else:
                    ai_acts = []

                if enriched_fp is not None:
                    fp_adaptive_on = bool(getattr(effective_fp_cfg, "ai_adaptive_execution", False))
                    raw_fp_td = (
                        active_ai_res.get("transport_demand")
                        if isinstance(active_ai_res, Mapping) and isinstance(active_ai_res.get("transport_demand"), Mapping)
                        else None
                    )
                    fp_transport_demand: Optional[dict[str, Any]] = None
                    fp_infra_spend: Optional[dict[str, float]] = None
                    fp_infra_carryover = 1.0
                    fp_budget_extra: dict[str, Any] = {}
                    if fp_adaptive_on:
                        fp_infra_spend, fp_infra_carryover = self._get_infra_transport_spend_last_hour_locked()
                        fp_budget_extra["spare_spend_in_window"] = self._get_spare_ship_spend_in_window_locked()
                        fp_budget_extra["spare_window_hours"] = self.SPARE_SHIP_BUDGET_WINDOW_HOURS
                        atk_tel = self._overlay_realized_loot_locked(atk_tel)
                        if isinstance(raw_fp_td, Mapping):
                            fp_transport_demand = dict(raw_fp_td)
                            fp_rt = max(0.25, _safe_api_float(fp_transport_demand.get("round_trips_per_hour", 2.5), 2.5))
                            fp_transport_demand["realized_utilization"] = self._recompute_transport_utilization_locked(
                                rt_per_hour=fp_rt,
                            )
                            fp_transport_demand["infra_cargo_ordered_last_hour"] = (
                                self._get_infra_cargo_ordered_last_hour_locked()
                            )
                    spare_budget = compute_spare_ship_budget(
                        enriched_fp,
                        spare_pct,
                        attack_telemetry=atk_tel,
                        reserved_cost=reserved_cost,
                        adaptive_execution=fp_adaptive_on,
                        config=effective_fp_cfg,
                        infra_spend_last_hour=fp_infra_spend,
                        infra_carryover_hours=fp_infra_carryover,
                        **fp_budget_extra,
                    )
                    fleet_actions = plan_fleet_and_attack_actions(
                        enriched_fp,
                        config=effective_fp_cfg,
                        scanned_systems=self._get_scanned_systems(),
                        transmitter_status=self._get_transmitter_status_locked(),
                        existing_plan=ai_acts,
                        priority_resource_need=priority_resource_need,
                        spare_budget=spare_budget,
                        transport_demand=fp_transport_demand,
                    )
                    fleet_actions = self._apply_fleet_slot_cooldowns(fleet_actions, re_sort_priority=True)
                    fleet_actions = self._apply_fleet_slot_holds(
                        enriched_fp,
                        fleet_actions,
                        cfg=effective_fp_cfg,
                        re_sort_priority=True,
                    )
                    graph_obj = build_fleet_graph(
                        enriched_fp,
                        fleet_actions,
                        effective_fp_cfg,
                        attack_telemetry=atk_tel,
                        spare_budget=spare_budget,
                    )
                else:
                    spare_budget = {
                        "spare_percent": {"pig_iron": 10.0, "metal": 10.0, "kryptonite": 10.0, "spice": 10.0},
                        "available_budget": {"pig_iron": 0.0, "metal": 0.0, "kryptonite": 0.0, "spice": 0.0},
                        "urgent_zero_count_ships": [],
                    }
                    graph_obj = build_fleet_graph(
                        None,
                        [],
                        effective_fp_cfg,
                        attack_telemetry=atk_tel,
                        spare_budget=spare_budget,
                    )
            return _sanitize_finite_json(
                _redact_api_payload(
                    {
                        "ok": True,
                        "command": cmd,
                        "graph": graph_obj,
                        "slot_budgets": graph_obj.get("slot_budgets", []),
                        "attack_telemetry": atk_tel,
                        "spare_budget": spare_budget,
                        "config": asdict(effective_fp_cfg),
                        "custom_planet_names": custom_names,
                    }
                )
            )

        if cmd == "escape_flight" and not dry_run:
            with self._lock:
                allow_p0_preempt = bool(
                    self._sweep_in_progress
                    and getattr(self.strategy_config, "allow_fleetsave", False)
                )
            if allow_p0_preempt:
                self._preempt_galaxy_sweep_and_wait("Preempted by P0 emergency Escape Flight.")

        with self._lock:
            if cmd == "ensure_session":
                if self._is_galaxy_scan_blocking_locked():
                    raise GalaxyScanInProgressError(self._galaxy_scan_busy_error_message())
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
                disp_st = self._apply_custom_planet_names_to_state(self._last_state) or self._last_state
                planet = self._resolve_planet(disp_st, pid) if pid is not None else disp_st.active_planet()
                eta_payload = build_buildings_eta(disp_st, planet)
                co_goals = self._get_config_optimizer_building_goals_locked()
                if isinstance(eta_payload, dict):
                    eta_payload["config_optimizer_goals"] = co_goals
                return _sanitize_finite_json(
                    {
                        "ok": True,
                        "command": cmd,
                        "buildings_eta": _redact_api_payload(eta_payload),
                        "config_optimizer_goals": _redact_api_payload(co_goals),
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
                disp_st = self._apply_custom_planet_names_to_state(self._last_state) or self._last_state
                planet = self._resolve_planet(disp_st, pid) if pid is not None else disp_st.active_planet()
                return _sanitize_finite_json(
                    {
                        "ok": True,
                        "command": cmd,
                        "combat": _redact_api_payload(
                            build_combat_overview(
                                disp_st,
                                planet,
                                safety_margin=self.strategy_config.combat_safety_margin,
                            )
                        ),
                    }
                )

            if (
                cmd == "plan"
                and "refresh" in payload
                and payload.get("refresh") is False
                and self._last_state is not None
            ):
                override_cfg_fast: Optional[StrategyConfig] = None
                if "planner_mode" in payload and payload["planner_mode"] is not None:
                    raw_mode_fast = payload["planner_mode"]
                    if not isinstance(raw_mode_fast, str) or not raw_mode_fast.strip():
                        raise ApiRequestError(
                            "'planner_mode' override must be a non-empty string ('priority' or 'ai_planner')."
                        )
                    try:
                        req_mode_fast = normalize_planner_mode(raw_mode_fast, strict=True)
                    except ValueError as exc:
                        raise ApiRequestError(_redact_api_payload(exc)) from exc
                    override_cfg_fast = replace(self.strategy_config, planner_mode=req_mode_fast)
                effective_cfg_fast = override_cfg_fast or self.strategy_config
                effective_mode_fast = normalize_planner_mode(effective_cfg_fast.planner_mode)
                state_fast_raw = self._refresh_cached_economy(self._last_state)
                state_fast = self._apply_custom_planet_names_to_state(state_fast_raw) or state_fast_raw
                actions_fast, opt_summary_fast = self._plan_with_summary(state_fast, config=override_cfg_fast)
                gate_fast = self._evaluate_ai_planner_gate_locked(state_fast, cfg=effective_cfg_fast)
                return _sanitize_finite_json(
                    _redact_api_payload(
                        {
                            "ok": True,
                            "command": cmd,
                            "planner_mode": effective_mode_fast,
                            "effective_planner_mode": gate_fast["effective_planner_mode"],
                            "fallback_reason": gate_fast["fallback_reason"],
                            "live_ai_unlocked": gate_fast["live_ai_unlocked"],
                            "colonies_owned": gate_fast["colonies_owned"],
                            "max_planets": gate_fast["max_planets"],
                            "actions": [self._serialize_action(a) for a in actions_fast],
                            "optimizer_summary": opt_summary_fast,
                        }
                    )
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
                        if self._bot_control.get("captcha_blocked"):
                            raise AntiBotChallengeError(
                                str(
                                    self._bot_control.get("captcha_message")
                                    or "Paused for CAPTCHA — confirm solved in UI to resume."
                                )
                            )
                        if self._is_galaxy_scan_blocking_locked() and not (
                            cmd == "escape_flight" and not dry_run
                        ):
                            raise GalaxyScanInProgressError(self._galaxy_scan_busy_error_message())
                        if _client_state[0] is None:
                            _client_state[0] = stack.enter_context(
                                self._client_factory(self.debug_port)
                            )
                        if not dry_run and cmd not in (
                            "plan",
                            "action_graph",
                            "buildings_eta",
                            "combat_overview",
                            "fleetsave_plan",
                        ):
                            self._last_live_command_mono = time.monotonic()
                        return _client_state[0]

                    def _exec_client(planned: PlannedAction) -> Any:
                        if dry_run or not planned.executable_now:
                            return None
                        if planned.kind == "ATTACK" and not self._live_executor.allow_raids:
                            return None
                        if planned.kind == "ESCAPE_FLIGHT" and not self._live_executor.allow_fleetsave:
                            return None
                        if planned.kind in ("COLONIZE", "ABANDON_COLONY") and not self._live_executor.allow_colonize:
                            return None
                        if planned.kind in ("TRANSPORT", "STATION") and not self._live_executor.allow_transport:
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
                        if self._bot_control.get("captcha_blocked") and self._last_state is not None:
                            return self._refresh_cached_economy(self._last_state)
                        if (
                            self._is_galaxy_scan_blocking_locked()
                            and not force_refresh
                            and self._last_state is not None
                        ):
                            return self._refresh_cached_economy(self._last_state)
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
                        if (
                            self._is_galaxy_scan_blocking_locked()
                            and not force_refresh
                            and self._last_state is not None
                        ):
                            return self._refresh_cached_economy(self._last_state)
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
                                    "'planner_mode' override must be a non-empty string ('priority' or 'ai_planner')."
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
                        disp_plan_state = self._apply_custom_planet_names_to_state(state) or state
                        actions, opt_summary = self._plan_with_summary(disp_plan_state, config=override_cfg)
                        gate = self._evaluate_ai_planner_gate_locked(disp_plan_state, cfg=effective_cfg)
                        return _sanitize_finite_json(
                            _redact_api_payload(
                                {
                                    "ok": True,
                                    "command": cmd,
                                    "planner_mode": effective_mode,
                                    "effective_planner_mode": gate["effective_planner_mode"],
                                    "fallback_reason": gate["fallback_reason"],
                                    "live_ai_unlocked": gate["live_ai_unlocked"],
                                    "colonies_owned": gate["colonies_owned"],
                                    "max_planets": gate["max_planets"],
                                    "actions": [self._serialize_action(a, disp_plan_state) for a in actions],
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
                        custom_names = self._get_custom_planet_names_locked()
                        disp_ag_state = self._apply_custom_planet_names_to_state(state, custom_names) or state
                        if ag_pid is not None:
                            self._resolve_planet(disp_ag_state, ag_pid)
                        actions, ag_opt_summary = self._plan_with_summary(
                            disp_ag_state,
                            config=effective_ag_cfg,
                            record_summary=(not ag_simulated and not has_ag_overrides),
                        )
                        graph_obj = build_action_graph(
                            disp_ag_state,
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
                                    "custom_planet_names": custom_names,
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
                        disp_eta_state = self._apply_custom_planet_names_to_state(state) or state
                        planet = self._resolve_planet(disp_eta_state, pid) if pid is not None else disp_eta_state.active_planet()
                        eta_payload = build_buildings_eta(disp_eta_state, planet)
                        co_goals = self._get_config_optimizer_building_goals_locked()
                        if isinstance(eta_payload, dict):
                            eta_payload["config_optimizer_goals"] = co_goals
                        return _sanitize_finite_json(
                            {
                                "ok": True,
                                "command": cmd,
                                "buildings_eta": _redact_api_payload(eta_payload),
                                "config_optimizer_goals": _redact_api_payload(co_goals),
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
                        disp_cmb_state = self._apply_custom_planet_names_to_state(state) or state
                        planet = self._resolve_planet(disp_cmb_state, pid) if pid is not None else disp_cmb_state.active_planet()
                        return _sanitize_finite_json(
                            {
                                "ok": True,
                                "command": cmd,
                                "combat": _redact_api_payload(
                                    build_combat_overview(
                                        disp_cmb_state,
                                        planet,
                                        safety_margin=self.strategy_config.combat_safety_margin,
                                    )
                                ),
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

                    if cmd == "sync_planet_names":
                        snap_state = self._get_planner_state_locked()
                        if snap_state is None or not snap_state.planets:
                            snap_state = _cached_or_fetch(
                                include_buildings=False,
                                include_research=False,
                                include_shipyard=False,
                                include_home=False,
                                priority="interactive",
                            )
                        planets_by_id = {int(p.planet_id): p for p in snap_state.planets if int(p.planet_id) > 0}
                        raw_batch_sync = payload.get("planet_names")
                        single_pid_sync = _parse_command_int(
                            "planet_id",
                            payload.get("planet_id"),
                            allow_none=True,
                            min_val=1,
                            max_val=2**63 - 1,
                        )
                        targets_to_sync: dict[int, str] = {}
                        if raw_batch_sync is not None:
                            if not isinstance(raw_batch_sync, Mapping):
                                raise ApiRequestError(
                                    "'sync_planet_names' field 'planet_names' must be a dictionary."
                                )
                            batch_local: dict[int, str] = {}
                            for raw_pid_s, raw_nm_s in raw_batch_sync.items():
                                pid_s = _parse_command_int("planet_id", raw_pid_s, min_val=1, max_val=2**63 - 1)
                                if pid_s is None or pid_s not in planets_by_id:
                                    raise ApiRequestError(f"Planet {raw_pid_s} does not belong to this empire.")
                                if raw_nm_s is not None and not isinstance(raw_nm_s, str):
                                    raise ApiRequestError("Planet names must be strings.")
                                clean_s = GameDatabase.sanitize_custom_planet_name(raw_nm_s)
                                batch_local[int(pid_s)] = clean_s
                                if clean_s:
                                    targets_to_sync[int(pid_s)] = clean_s
                            if batch_local:
                                self._set_custom_planet_names_batch_locked(batch_local)
                        elif single_pid_sync is not None:
                            if single_pid_sync not in planets_by_id:
                                raise ApiRequestError(f"Planet {single_pid_sync} does not belong to this empire.")
                            if "name" in payload and payload.get("name") is not None:
                                if not isinstance(payload.get("name"), str):
                                    raise ApiRequestError("Field 'name' must be a string.")
                                self._set_custom_planet_name_locked(int(single_pid_sync), str(payload["name"]))
                            cur_custom = self._get_custom_planet_names_locked().get(str(single_pid_sync), "")
                            desired_nm = cur_custom or str(planets_by_id[single_pid_sync].name or "")
                            if desired_nm:
                                targets_to_sync[int(single_pid_sync)] = desired_nm
                        else:
                            # Sync all custom planet names that differ from the live in-game name (or all if force=True)
                            force_all = bool(payload.get("force", False))
                            custom_map = self._get_custom_planet_names_locked()
                            for pid_s_str, c_name in custom_map.items():
                                pid_i = _safe_api_int(pid_s_str, 0)
                                if pid_i <= 0 or pid_i not in planets_by_id or not c_name:
                                    continue
                                g_name = str(planets_by_id[pid_i].name or "").strip()
                                if force_all or c_name != g_name:
                                    targets_to_sync[pid_i] = str(c_name)

                        if not targets_to_sync:
                            return _sanitize_finite_json(
                                _redact_api_payload(
                                    {
                                        "ok": True,
                                        "command": cmd,
                                        "synced_count": 0,
                                        "synced": [],
                                        "skipped": [],
                                        "custom_planet_names": self._get_custom_planet_names_locked(),
                                        "message": "All planet names in Config Optimizer already match in-game names.",
                                    }
                                )
                            )

                        if dry_run:
                            return _sanitize_finite_json(
                                _redact_api_payload(
                                    {
                                        "ok": True,
                                        "command": cmd,
                                        "dry_run": True,
                                        "synced_count": 0,
                                        "pending_targets": {str(k): v for k, v in targets_to_sync.items()},
                                        "custom_planet_names": self._get_custom_planet_names_locked(),
                                        "message": f"Dry run: would sync {len(targets_to_sync)} planet name(s) via internalSettings.",
                                    }
                                )
                            )

                        client = _get_client()
                        self._call_with_optional_priority(client.ensure_in_game, priority="interactive")
                        sync_out = self._live_executor.sync_planet_names_in_game(
                            client,
                            snap_state,
                            targets_to_sync,
                        )
                        updated_st = sync_out.get("updated_state")
                        if isinstance(updated_st, GameState):
                            self._record_state(
                                updated_st,
                                fetched_buildings=False,
                                fetched_research=False,
                                fetched_shipyard=False,
                                fetched_home=False,
                                observed_active_planet=False,
                            )
                        # Reconcile any game-side length truncation into custom_planet_names
                        reconcile_batch: dict[int, str] = {}
                        for s_row in sync_out.get("synced") or ():
                            if not isinstance(s_row, Mapping):
                                continue
                            s_pid = _safe_api_int(s_row.get("planet_id"), 0)
                            obs_gname = str(s_row.get("game_name") or "").strip()
                            if s_pid > 0 and obs_gname:
                                reconcile_batch[s_pid] = obs_gname
                        if reconcile_batch:
                            self._set_custom_planet_names_batch_locked(reconcile_batch)
                        self._invalidate_dashboard_cache()
                        return _sanitize_finite_json(
                            _redact_api_payload(
                                {
                                    "ok": bool(sync_out.get("ok", True)),
                                    "command": cmd,
                                    "synced_count": int(sync_out.get("synced_count", 0) or 0),
                                    "synced": list(sync_out.get("synced") or []),
                                    "skipped": list(sync_out.get("skipped") or []),
                                    "custom_planet_names": self._get_custom_planet_names_locked(),
                                    "message": str(sync_out.get("message") or "Synced planet names in-game."),
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
                            try:
                                html = self._call_with_optional_priority(
                                    client.navigate_menu,
                                    action,
                                    priority="interactive",
                                )
                            except (RateLimitError, AntiBotChallengeError, SpaceInvasionAuthError):
                                raise
                            except Exception:
                                if action not in ("internalStatistics", "internalToplist", "internalUniverse", "internalSettings"):
                                    raise
                                build_url = getattr(client, "_build_internal_url", None)
                                res_sid = getattr(client, "_resolve_sid_query", None)
                                mark_nav = getattr(client, "_mark_frame_navigation", None)
                                wait_fr = getattr(client, "_wait_frame_ready", None)
                                sw_fr = getattr(client, "_switch_frame", None)
                                drv_obj = getattr(client, "driver", None)
                                if not (
                                    callable(build_url)
                                    and callable(res_sid)
                                    and callable(mark_nav)
                                    and callable(wait_fr)
                                    and callable(sw_fr)
                                    and drv_obj is not None
                                ):
                                    raise
                                sid_q = str(res_sid() or "")
                                q_str = f"action={action}" + (f"&{sid_q}" if sid_q else "")
                                tgt_url = build_url(q_str)
                                tok = mark_nav("F_Hauptframe")
                                sw_fr("F_Hauptframe")
                                drv_obj.execute_script("location.href = arguments[0];", tgt_url)
                                html = wait_fr("F_Hauptframe", expected_action=action, nav_token=tok)

                        if (
                            isinstance(html, str)
                            and html
                            and action in ("internalStatistics", "internalToplist", "internalUniverse")
                            and self._db is not None
                            and hasattr(self._db, "refresh_universe_research_max")
                        ):
                            nav_srv_recs = parse_universe_research_records(html)
                            if nav_srv_recs:
                                eff_st_nav = self._last_state
                                self._db.refresh_universe_research_max(
                                    own_researches=(eff_st_nav.researches if eff_st_nav is not None else None),
                                    own_nick=(eff_st_nav.nickname if eff_st_nav is not None else ""),
                                    server_records=nav_srv_recs,
                                    source=f"nav:{action}",
                                )
                                self._invalidate_dashboard_cache()

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
                                    self._note_daily_login_bonus_seen_locked(
                                        present=bool(parse_daily_login_bonus(html).get("present", False)),
                                    )
                            elif action == "internalFleet" and self._last_state is not None:
                                nav_fc = ActionExecutor._extract_internal_fleet_planet_coords(html)
                                nav_fp = (
                                    next(
                                        (p for p in self._last_state.planets if normalize_coords(p.coords) == nav_fc),
                                        None,
                                    )
                                    if nav_fc
                                    else self._last_state.active_planet()
                                )
                                if nav_fp is not None and nav_fc:
                                    obs_nav_pid = nav_fp.planet_id
                                nav_slots = ActionExecutor._parse_internal_fleet_slot_status(html)
                                if nav_slots is not None:
                                    self._record_observed_fleet_slots_locked(nav_fp, nav_slots[0])
                                    self._persist_runtime_state()
                            elif action in (
                                "internalBuildings",
                                "internalEconomy",
                                "internalResearch",
                                "internalWeaponFactory",
                                "internalTransmitter",
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
                            self._observe_galaxy_universe_bounds_locked(client=client, html=html)
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
                            self._last_galaxy_browser_coords = (actual_gal, actual_sys)
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
                        hw_pid = homeworld_planet_id(state)
                        planet = self._resolve_planet(state, hw_pid if hw_pid is not None else pid)
                        planned = PlannedAction(
                            priority="P2",
                            kind="ACTIVATE_TRANSMITTER",
                            planet_id=planet.planet_id,
                            planet_coords=planet.coords,
                            target_name="Interdimensional Transmitter",
                            reason=(
                                f"Collect Command Central daily bonus & activate daily free "
                                f"Interdimensional Transmitter spin on Homeworld [{planet.coords}] (0 Urplasma)"
                            ),
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

                    if cmd == "sync_messages":
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
                            desig_pid = max(
                                0,
                                _safe_api_int(getattr(self.strategy_config, "spy_dispatch_planet_id", 0), 0),
                            )
                            desig_p = (
                                next(
                                    (
                                        p
                                        for p in state.planets
                                        if p.planet_id == desig_pid and normalize_planet_type(p) != "raid"
                                    ),
                                    None,
                                )
                                if desig_pid > 0
                                else None
                            )
                            if desig_p is not None:
                                planet = desig_p
                            else:
                                probe_p = next(
                                    (
                                        p
                                        for p in state.planets
                                        if normalize_planet_type(p) != "raid"
                                        and _safe_api_int(p.ships.get("s10", 0)) > 0
                                    ),
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
                        if not dry_run:
                            if bool(getattr(self.strategy_config, "spy_reserved_slots_only", False)):
                                max_spy_sl_live = self._max_fleet_slots_for_planet_locked(planet)
                                spy_cap_live = min(
                                    max_spy_sl_live,
                                    max(0, _safe_api_int(getattr(self.strategy_config, "reserved_spy_slots", 1), 1)),
                                )
                                spy_used_live = count_in_flight_spy_fleets_for_planet(state, planet)
                                if spy_used_live >= spy_cap_live:
                                    return _sanitize_finite_json(
                                        _redact_api_payload(
                                            {
                                                "ok": True,
                                                "command": cmd,
                                                "executed": False,
                                                "validated": True,
                                                "dry_run": dry_run,
                                                "message": (
                                                    f"Aborted: Spy restricted to reserved slot(s) "
                                                    f"({spy_used_live}/{spy_cap_live} spy slots in use on [{planet.coords}])."
                                                ),
                                                "action": self._serialize_action(
                                                    replace(planned, executable_now=False), state
                                                ),
                                                "next_action": None,
                                            }
                                        )
                                    )
                            now_spy_s = time.time()
                            spy_trow = next(
                                (
                                    r
                                    for r in (self._raid_targets or ())
                                    if normalize_coords(r.get("coords") or r.get("target_coords") or "") == norm_target
                                ),
                                None,
                            )
                            if spy_trow is None and self._db is not None:
                                get_by_coords = getattr(self._db, "get_spy_target_by_coords", None)
                                if callable(get_by_coords):
                                    spy_trow = get_by_coords(norm_target)
                            gs_slot = None
                            if self._db is not None:
                                get_gs_fn = getattr(self._db, "get_galaxy_slots", None)
                                m_gsp = re.match(r"^(\d+):(\d+):(\d+)$", norm_target)
                                if callable(get_gs_fn) and m_gsp:
                                    sys_slots = get_gs_fn(galaxy=int(m_gsp.group(1)), system=int(m_gsp.group(2)), limit=20)
                                    gs_slot = next(
                                        (s for s in sys_slots if normalize_coords(s.get("coords") or "") == norm_target),
                                        None,
                                    )
                            blocked_until_spy = _safe_api_float((spy_trow or {}).get("blocked_until_ts", 0.0))
                            if norm_target in self._target_blocks:
                                blocked_until_spy = max(blocked_until_spy, float(self._target_blocks[norm_target][0]))
                            if blocked_until_spy > now_spy_s:
                                rem_spy_cd = max(0.0, blocked_until_spy - now_spy_s)
                                return _sanitize_finite_json(
                                    _redact_api_payload(
                                        {
                                            "ok": True,
                                            "command": cmd,
                                            "executed": False,
                                            "validated": True,
                                            "dry_run": dry_run,
                                            "message": f"Aborted: target {norm_target} is on protection cooldown ({rem_spy_cd:.0f}s remaining).",
                                            "action": self._serialize_action(replace(planned, executable_now=False), state),
                                            "next_action": None,
                                        }
                                    )
                                )
                            eff_slot = gs_slot if (
                                gs_slot is not None
                                and _safe_api_float(gs_slot.get("last_scanned_ts", 0.0)) >= _safe_api_float((spy_trow or {}).get("last_scanned_ts", 0.0))
                            ) else spy_trow
                            if eff_slot is not None:
                                spy_color = game_data.safe_key(eff_slot.get("status_color") or "").strip().lower()
                                spy_spacia = bool(eff_slot.get("is_spacia", False))
                                if spy_color == "free" and not spy_spacia:
                                    planned = replace(planned, target_name=f"{norm_target} (Free slot)")
                                if spy_color in ("blue", "green", "white", "dark_red") and not spy_spacia:
                                    return _sanitize_finite_json(
                                        _redact_api_payload(
                                            {
                                                "ok": True,
                                                "command": cmd,
                                                "executed": False,
                                                "validated": True,
                                                "dry_run": dry_run,
                                                "message": f"Aborted: target {norm_target} is protected ({spy_color}).",
                                                "action": self._serialize_action(replace(planned, executable_now=False), state),
                                                "next_action": None,
                                            }
                                        )
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

                    if cmd == "transport":
                        raw_target_coords = payload.get("target_coords")
                        dest_pid = _parse_command_int(
                            "dest_planet_id",
                            payload.get("dest_planet_id"),
                            allow_none=True,
                            min_val=1,
                            max_val=2**63 - 1,
                        )
                        norm_target = ""
                        if isinstance(raw_target_coords, str) and raw_target_coords.strip():
                            m_tr = re.search(r"(\d{1,2}:\d{1,3}:\d{1,2})", raw_target_coords.strip(), re.ASCII)
                            if not m_tr:
                                raise ApiRequestError(
                                    "'transport' command 'target_coords' must be formatted as GG:SSS:PP."
                                )
                            norm_target = normalize_coords(m_tr.group(1))
                        if not norm_target and dest_pid is None:
                            raise ApiRequestError(
                                "'transport' command requires 'target_coords' or 'dest_planet_id'."
                            )
                        raw_res = payload.get("resources")
                        if not isinstance(raw_res, Mapping):
                            raise ApiRequestError(
                                "'transport' command requires a 'resources' object with pig_iron/metal/kryptonite/spice."
                            )
                        cargo_res = Resources(
                            pig_iron=_parse_command_int("resources.pig_iron", raw_res.get("pig_iron", 0), min_val=0, max_val=game_data.MAX_JSON_SAFE_INT) or 0,
                            metal=_parse_command_int("resources.metal", raw_res.get("metal", 0), min_val=0, max_val=game_data.MAX_JSON_SAFE_INT) or 0,
                            kryptonite=_parse_command_int("resources.kryptonite", raw_res.get("kryptonite", 0), min_val=0, max_val=game_data.MAX_JSON_SAFE_INT) or 0,
                            spice=_parse_command_int("resources.spice", raw_res.get("spice", 0), min_val=0, max_val=game_data.MAX_JSON_SAFE_INT) or 0,
                        )
                        if cargo_res.is_zero():
                            raise ApiRequestError(
                                "'transport' command requires at least one positive resource amount in 'resources'."
                            )
                        pid = _parse_command_int(
                            "planet_id",
                            payload.get("planet_id"),
                            allow_none=True,
                            min_val=1,
                            max_val=2**63 - 1,
                        )
                        state = _resolve_fleet_command_state(pid, is_spy=True)
                        dest_planet = (
                            next((p for p in state.planets if p.planet_id == dest_pid), None)
                            if dest_pid is not None
                            else next((p for p in state.planets if normalize_coords(p.coords) == norm_target), None)
                        )
                        if dest_planet is None:
                            planned_bad = PlannedAction(
                                priority="P1",
                                kind="TRANSPORT",
                                planet_id=pid or (state.active_planet().planet_id if state.active_planet() else 0),
                                planet_coords=(state.active_planet().coords if state.active_planet() else ""),
                                target_id=dest_pid,
                                target_name=f"Transport -> {norm_target or dest_pid}",
                                cost=cargo_res,
                                reason="Own-planet transport safety check",
                                executable_now=False,
                            )
                            return _sanitize_finite_json(
                                _redact_api_payload(
                                    {
                                        "ok": True,
                                        "command": cmd,
                                        "executed": False,
                                        "dry_run": dry_run,
                                        "message": f"Aborted: destination {norm_target or dest_pid} is not one of your own planets.",
                                        "action": self._serialize_action(planned_bad, state),
                                        "next_action": None,
                                    }
                                )
                            )
                        norm_dest = normalize_coords(dest_planet.coords)
                        if pid is None:
                            donor_p = next(
                                (
                                    p
                                    for p in state.planets
                                    if p.planet_id != dest_planet.planet_id
                                    and any(_safe_api_int(p.ships.get(k, 0)) > 0 for k in ("s1", "s2", "s3"))
                                ),
                                None,
                            )
                            planet = donor_p or self._resolve_planet(state, None)
                        else:
                            planet = self._resolve_planet(state, pid)
                        dest_label = f"Transport -> {norm_dest}" + (f" ({dest_planet.name})" if dest_planet.name else "")
                        planned = PlannedAction(
                            priority="P1",
                            kind="TRANSPORT",
                            planet_id=planet.planet_id,
                            planet_coords=planet.coords,
                            target_id=dest_planet.planet_id,
                            target_name=dest_label,
                            cost=cargo_res,
                            reason="API inter-planet transport command",
                            executable_now=True,
                        )
                        if planet.planet_id == dest_planet.planet_id:
                            planned_same = replace(
                                planned,
                                reason="Own-planet transport safety check",
                                executable_now=False,
                            )
                            return _sanitize_finite_json(
                                _redact_api_payload(
                                    {
                                        "ok": True,
                                        "command": cmd,
                                        "executed": False,
                                        "dry_run": dry_run,
                                        "message": f"Aborted: origin and destination are the same planet ({norm_dest}).",
                                        "action": self._serialize_action(planned_same, state),
                                        "next_action": None,
                                    }
                                )
                            )
                        if not dry_run and not self._live_executor.allow_transport:
                            return self._execute_and_summarize(_exec_client(planned), state, cmd, planned, dry_run)
                        return self._execute_and_summarize(_exec_client(planned), state, cmd, planned, dry_run)

                    if cmd == "station":
                        raw_target_coords = payload.get("target_coords")
                        dest_pid = _parse_command_int(
                            "dest_planet_id",
                            payload.get("dest_planet_id"),
                            allow_none=True,
                            min_val=1,
                            max_val=2**63 - 1,
                        )
                        norm_target = ""
                        if isinstance(raw_target_coords, str) and raw_target_coords.strip():
                            m_st = re.search(r"(\d{1,2}:\d{1,3}:\d{1,2})", raw_target_coords.strip(), re.ASCII)
                            if not m_st:
                                raise ApiRequestError(
                                    "'station' command 'target_coords' must be formatted as GG:SSS:PP."
                                )
                            norm_target = normalize_coords(m_st.group(1))
                        if not norm_target and dest_pid is None:
                            raise ApiRequestError(
                                "'station' command requires 'target_coords' or 'dest_planet_id'."
                            )
                        raw_ships = payload.get("ships")
                        clean_ships: dict[str, int] = {}
                        if isinstance(raw_ships, Mapping):
                            for raw_sk, raw_cnt in raw_ships.items():
                                sk_norm = game_data.safe_key(raw_sk).strip().lower()
                                if not re.fullmatch(r"s\d{1,3}", sk_norm, re.ASCII) or sk_norm == "s9":
                                    continue
                                cnt_val = _parse_command_int(
                                    f"ships.{sk_norm}",
                                    raw_cnt,
                                    min_val=0,
                                    max_val=game_data.MAX_JSON_SAFE_INT,
                                ) or 0
                                if cnt_val > 0:
                                    clean_ships[sk_norm] = cnt_val
                        elif isinstance(raw_ships, str) and raw_ships.strip():
                            clean_ships = parse_station_ships_spec(raw_ships)
                        elif isinstance(payload.get("ships_spec"), str) and payload.get("ships_spec", "").strip():
                            clean_ships = parse_station_ships_spec(str(payload.get("ships_spec")))
                        if not clean_ships:
                            raise ApiRequestError(
                                "'station' command requires a non-empty 'ships' map (e.g. {'s11': 10, 's4': 6}) or 'ships_spec'."
                            )
                        pid = _parse_command_int(
                            "planet_id",
                            payload.get("planet_id"),
                            allow_none=True,
                            min_val=1,
                            max_val=2**63 - 1,
                        )
                        state = _resolve_fleet_command_state(pid, is_spy=True)
                        dest_planet = (
                            next((p for p in state.planets if p.planet_id == dest_pid), None)
                            if dest_pid is not None
                            else next((p for p in state.planets if normalize_coords(p.coords) == norm_target), None)
                        )
                        ship_spec = format_station_ships_spec(clean_ships)
                        if dest_planet is None or normalize_planet_type(dest_planet) == "raid":
                            planned_bad_st = PlannedAction(
                                priority="P2",
                                kind="STATION",
                                planet_id=pid or (state.active_planet().planet_id if state.active_planet() else 0),
                                planet_coords=(state.active_planet().coords if state.active_planet() else ""),
                                target_id=dest_pid,
                                target_level=sum(clean_ships.values()),
                                target_name=f"Station -> {norm_target or dest_pid} [{ship_spec}]",
                                reason="Own-planet station safety check",
                                executable_now=False,
                            )
                            return _sanitize_finite_json(
                                _redact_api_payload(
                                    {
                                        "ok": True,
                                        "command": cmd,
                                        "executed": False,
                                        "dry_run": dry_run,
                                        "message": (
                                            f"Aborted: destination {norm_target or dest_pid} is not one of your own non-raid planets."
                                        ),
                                        "action": self._serialize_action(planned_bad_st, state),
                                        "next_action": None,
                                    }
                                )
                            )
                        norm_dest = normalize_coords(dest_planet.coords)
                        if pid is None:
                            donor_p = next(
                                (
                                    p
                                    for p in state.planets
                                    if p.planet_id != dest_planet.planet_id
                                    and normalize_planet_type(p) != "raid"
                                    and any(_safe_api_int(p.ships.get(k, 0)) > 0 for k in clean_ships)
                                ),
                                None,
                            )
                            planet = donor_p or self._resolve_planet(state, None)
                        else:
                            planet = self._resolve_planet(state, pid)
                        dest_label = (
                            f"Station -> {norm_dest}"
                            + (f" ({dest_planet.name})" if dest_planet.name else "")
                            + f" [{ship_spec}]"
                        )
                        planned_st = PlannedAction(
                            priority="P2",
                            kind="STATION",
                            planet_id=planet.planet_id,
                            planet_coords=planet.coords,
                            target_id=dest_planet.planet_id,
                            target_level=sum(clean_ships.values()),
                            target_name=dest_label,
                            reason="API one-way fleet stationing command",
                            executable_now=True,
                        )
                        if planet.planet_id == dest_planet.planet_id:
                            planned_same_st = replace(
                                planned_st,
                                reason="Own-planet station safety check",
                                executable_now=False,
                            )
                            return _sanitize_finite_json(
                                _redact_api_payload(
                                    {
                                        "ok": True,
                                        "command": cmd,
                                        "executed": False,
                                        "dry_run": dry_run,
                                        "message": f"Aborted: origin and destination are the same planet ({norm_dest}).",
                                        "action": self._serialize_action(planned_same_st, state),
                                        "next_action": None,
                                    }
                                )
                            )
                        return self._execute_and_summarize(_exec_client(planned_st), state, cmd, planned_st, dry_run)

                    if cmd == "abandon_colony":
                        pid = _parse_command_int(
                            "planet_id",
                            payload.get("planet_id"),
                            allow_none=True,
                            min_val=1,
                            max_val=2**63 - 1,
                        )
                        raw_tc = payload.get("target_coords")
                        norm_tc = (
                            normalize_coords(raw_tc.strip())
                            if isinstance(raw_tc, str) and re.fullmatch(r"\d{1,2}:\d{1,3}:\d{1,2}", raw_tc.strip(), re.ASCII)
                            else ""
                        )
                        if pid is None and not norm_tc:
                            raise ApiRequestError(
                                "'abandon_colony' command requires 'planet_id' or 'target_coords'."
                            )
                        min_col_f = _parse_command_int(
                            "min_colony_fields",
                            payload.get("min_colony_fields"),
                            allow_none=True,
                            min_val=50,
                            max_val=1000,
                        ) or max(50, _safe_api_int(getattr(self.strategy_config, "min_colony_fields", 300), 300))
                        state = _cached_or_fetch(
                            include_buildings=False,
                            include_research=False,
                            include_shipyard=False,
                            include_home=False,
                            priority="interactive",
                        )
                        if pid is not None:
                            planet = self._resolve_planet(state, pid)
                        else:
                            p_found = next(
                                (p for p in state.planets if normalize_coords(p.coords) == norm_tc),
                                None,
                            )
                            if p_found is None:
                                raise ApiRequestError(f"Unknown colony coordinates {norm_tc}.")
                            planet = p_found
                        safe_ab = is_undersized_fresh_colony(planet, state, min_col_f)
                        planned = PlannedAction(
                            priority="P1",
                            kind="ABANDON_COLONY",
                            planet_id=planet.planet_id,
                            planet_coords=planet.coords,
                            target_level=min_col_f,
                            target_name=f"Delete colony {planet.coords} ({planet.fields_max}/{min_col_f} fields)",
                            reason=(
                                f"Delete undersized colony {planet.coords} ({planet.fields_max} < {min_col_f} fields)"
                                if safe_ab
                                else f"Safety check blocked deleting {planet.coords} ({planet.fields_used}/{planet.fields_max} fields)"
                            ),
                            executable_now=True,
                        )
                        if not dry_run and not self._live_executor.allow_colonize:
                            return self._execute_and_summarize(_exec_client(planned), state, cmd, planned, dry_run)
                        if not safe_ab:
                            blocked_plan = replace(planned, executable_now=False)
                            return _sanitize_finite_json(
                                _redact_api_payload(
                                    {
                                        "ok": True,
                                        "command": cmd,
                                        "executed": False,
                                        "dry_run": dry_run,
                                        "message": (
                                            f"Aborted safety check: refusing to delete [{planet.coords}] "
                                            f"({planet.fields_used}/{planet.fields_max} fields) — Homeworld, developed, stationed fleet/defense, or >= {min_col_f} fields."
                                        ),
                                        "action": self._serialize_action(blocked_plan, state),
                                        "next_action": None,
                                    }
                                )
                            )
                        res_ab = self._execute_and_summarize(_exec_client(planned), state, cmd, planned, dry_run)
                        if isinstance(res_ab, Mapping) and res_ab.get("executed"):
                            self._last_buildings_fetched = False
                            self._last_shipyard_sync_at = 0.0
                            self._last_shipyard_sync_sig = None
                            self._economy_rates.pop(planet.planet_id, None)
                            self._economy_sync_timestamps.pop(planet.planet_id, None)
                            self._throttle_timestamps.pop(planet.planet_id, None)
                            if self._metal_overflow_paused_planets.pop(planet.planet_id, None) is not None:
                                self._persist_runtime_state()
                            self._fleet_slot_cooldown_until.pop(planet.planet_id, None)
                            self._invalidate_dashboard_cache()
                        return res_ab

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
                        gs_atk_slot = None
                        if self._db is not None:
                            get_gs_fn = getattr(self._db, "get_galaxy_slots", None)
                            m_gsa = re.match(r"^(\d+):(\d+):(\d+)$", norm_target)
                            if callable(get_gs_fn) and m_gsa:
                                sys_slots_a = get_gs_fn(
                                    galaxy=int(m_gsa.group(1)),
                                    system=int(m_gsa.group(2)),
                                    limit=20,
                                )
                                gs_atk_slot = next(
                                    (s for s in sys_slots_a if normalize_coords(s.get("coords") or "") == norm_target),
                                    None,
                                )
                        eff_atk_slot = (
                            gs_atk_slot
                            if (
                                gs_atk_slot is not None
                                and _safe_api_float(gs_atk_slot.get("last_scanned_ts", 0.0))
                                >= _safe_api_float((target_row or {}).get("last_scanned_ts", 0.0))
                            )
                            else target_row
                        )
                        is_inactive = bool((eff_atk_slot or target_row or {}).get("is_inactive", False))
                        is_spacia = bool((eff_atk_slot or target_row or {}).get("is_spacia", False))
                        status_color = game_data.safe_key(
                            (eff_atk_slot or target_row or {}).get("status_color") or ""
                        ).strip().lower()
                        atk_owner = game_data.safe_key((eff_atk_slot or target_row or {}).get("owner") or "").strip()
                        atk_pname = game_data.safe_key(
                            (eff_atk_slot or target_row or {}).get("planet_name")
                            or (eff_atk_slot or target_row or {}).get("name")
                            or ""
                        ).strip()
                        if status_color == "yellow":
                            is_inactive = True
                        if eff_atk_slot is not None and (
                            (status_color == "free" and not is_spacia)
                            or (not is_spacia and not atk_owner and not atk_pname)
                        ):
                            return _sanitize_finite_json(
                                _redact_api_payload(
                                    {
                                        "ok": True,
                                        "command": cmd,
                                        "executed": False,
                                        "dry_run": dry_run,
                                        "message": f"Aborted: target {norm_target} is uninhabited/ownerless and cannot be attacked.",
                                        "action": self._serialize_action(replace(planned, executable_now=False), state),
                                        "next_action": None,
                                    }
                                )
                            )
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

                        now_atk_s = time.time()
                        if self._db is not None:
                            bash = self._db.load_bash_tracker()
                            if not bash.can_attack(
                                norm_target,
                                is_inactive=is_inactive,
                                is_spacia=is_spacia,
                            ):
                                until_cd = bash.blocked_until(norm_target)
                                rem_cd = max(0.0, until_cd - now_atk_s) if until_cd > 0.0 else 0.0
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
                            mem_until, mem_reason = self._target_blocks[norm_target]
                            rem_cd = mem_until - now_atk_s
                            if rem_cd > 0.0 and "bash" in str(mem_reason).lower():
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

                        blocked_until_atk = _safe_api_float((target_row or {}).get("blocked_until_ts", 0.0))
                        if norm_target in self._target_blocks:
                            blocked_until_atk = max(blocked_until_atk, float(self._target_blocks[norm_target][0]))
                        if blocked_until_atk > now_atk_s:
                            rem_atk_cd = max(0.0, blocked_until_atk - now_atk_s)
                            return _sanitize_finite_json(
                                _redact_api_payload(
                                    {
                                        "ok": True,
                                        "command": cmd,
                                        "executed": False,
                                        "validated": True,
                                        "dry_run": dry_run,
                                        "message": f"Aborted: target {norm_target} is on protection cooldown ({rem_atk_cd:.0f}s remaining).",
                                        "action": self._serialize_action(replace(planned, executable_now=False), state),
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
                            raw_th_reason = payload.get("reason")
                            if isinstance(raw_th_reason, str) and raw_th_reason.strip():
                                t_reason = _bounded_api_str(
                                    raw_th_reason.strip(),
                                    default=f"Set {label} rate to {pct}% in internalEconomy",
                                    max_len=300,
                                )
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
                if self.log_dir is not None and not isinstance(
                    exc, (ApiRequestError, GalaxyScanInProgressError)
                ):
                    append_crash_debug_log(
                        component="api_server.handle_command",
                        event=f"command_error:{cmd}",
                        error=exc,
                        details={"command": cmd, "payload": payload},
                    )
                raise


class _RequestTooLargeError(Exception):
    """Raised when an HTTP POST request exceeds `MAX_REQUEST_BYTES`."""


_CLIENT_DISCONNECT_ERRNOS = frozenset(
    {
        errno.EPIPE,
        errno.ECONNRESET,
        errno.ECONNABORTED,
        getattr(errno, "ESHUTDOWN", errno.EPIPE),
    }
)


def _is_client_disconnect_error(exc: Optional[BaseException]) -> bool:
    """Return True for benign peer-disconnect socket errors (e.g. BrokenPipeError)."""
    if exc is None:
        return False
    if isinstance(exc, (BrokenPipeError, ConnectionResetError, ConnectionAbortedError)):
        return True
    if isinstance(exc, OSError) and exc.errno in _CLIENT_DISCONNECT_ERRNOS:
        return True
    return False


class _ReusableThreadingHTTPServer(ThreadingHTTPServer):
    allow_reuse_address = True
    daemon_threads = True

    def handle_error(self, request: Any, client_address: Any) -> None:
        exc = sys.exc_info()[1]
        if _is_client_disconnect_error(exc):
            return
        super().handle_error(request, client_address)


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
        def _send_bytes(
            self,
            status: int,
            raw: bytes,
            *,
            content_type: Optional[str] = None,
            extra_headers: Optional[dict[str, str]] = None,
        ) -> None:
            try:
                self.send_response(status)
                if content_type is not None:
                    self.send_header("Content-Type", content_type)
                self.send_header("Cache-Control", "no-store")
                if extra_headers:
                    for hk, hv in extra_headers.items():
                        self.send_header(str(hk), str(hv))
                if raw or content_type is not None:
                    self.send_header("Content-Length", str(len(raw)))
                self.end_headers()
                if raw:
                    self.wfile.write(raw)
            except OSError as exc:
                if _is_client_disconnect_error(exc):
                    self.close_connection = True
                    return
                raise

        def _send_json(
            self,
            status: int,
            body: dict[str, Any],
            *,
            extra_headers: Optional[dict[str, str]] = None,
        ) -> None:
            clean_body = _sanitize_finite_json(body)
            raw = json.dumps(clean_body, allow_nan=False).encode("utf-8")
            self._send_bytes(
                status,
                raw,
                content_type="application/json; charset=utf-8",
                extra_headers=extra_headers,
            )

        def _send_static(self, filename: str, content_type: str) -> None:
            asset_path = UI_DIR / filename
            if not asset_path.exists():
                self._send_json(HTTPStatus.NOT_FOUND, {"ok": False, "error": f"Missing UI asset {filename}"})
                return
            raw = asset_path.read_bytes()
            self._send_bytes(HTTPStatus.OK, raw, content_type=content_type)

        def log_message(self, format: str, *args: Any) -> None:
            return

        def do_GET(self) -> None:
            parsed = urlparse(self.path)
            try:
                if parsed.path in _UI_ASSETS:
                    filename, ctype = _UI_ASSETS[parsed.path]
                    self._send_static(filename, ctype)
                    return
                if parsed.path == "/assets" or parsed.path.startswith("/assets/"):
                    rel_asset = parsed.path[len("/assets/"):] if parsed.path.startswith("/assets/") else ""
                    if (
                        not rel_asset
                        or "\x00" in rel_asset
                        or "\\" in rel_asset
                        or rel_asset.startswith("/")
                        or any(seg in ("", ".", "..") for seg in rel_asset.split("/"))
                    ):
                        self._send_json(
                            HTTPStatus.BAD_REQUEST,
                            {"ok": False, "error": "Invalid asset path."},
                        )
                        return
                    assets_root = (UI_DIR / "assets").resolve()
                    cand_path = (assets_root / rel_asset).resolve()
                    try:
                        cand_path.relative_to(assets_root)
                    except ValueError:
                        self._send_json(
                            HTTPStatus.BAD_REQUEST,
                            {"ok": False, "error": "Invalid asset path."},
                        )
                        return
                    if not cand_path.is_file():
                        self._send_json(
                            HTTPStatus.NOT_FOUND,
                            {"ok": False, "error": _redact_api_payload(f"Missing UI asset {rel_asset}")},
                        )
                        return
                    ext = cand_path.suffix.lower()
                    ext_types = {
                        ".js": "application/javascript; charset=utf-8",
                        ".mjs": "application/javascript; charset=utf-8",
                        ".css": "text/css; charset=utf-8",
                        ".svg": "image/svg+xml",
                        ".png": "image/png",
                        ".jpg": "image/jpeg",
                        ".jpeg": "image/jpeg",
                        ".webp": "image/webp",
                        ".ico": "image/x-icon",
                        ".woff2": "font/woff2",
                        ".woff": "font/woff",
                        ".ttf": "font/ttf",
                        ".json": "application/json; charset=utf-8",
                        ".map": "application/json; charset=utf-8",
                    }
                    ctype_asset = ext_types.get(ext, "application/octet-stream")
                    raw_asset = cand_path.read_bytes()
                    self._send_bytes(HTTPStatus.OK, raw_asset, content_type=ctype_asset)
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
                    self._send_bytes(HTTPStatus.NO_CONTENT, b"")
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
            except GalaxyScanInProgressError as exc:
                self._send_json(
                    HTTPStatus.CONFLICT,
                    {
                        "ok": False,
                        "sweep_in_progress": True,
                        "sweep_blocked": True,
                        "error": format_clean_error(_redact_api_payload(exc), max_chars=1024),
                    },
                )
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
                self._send_json(
                    HTTPStatus.SERVICE_UNAVAILABLE,
                    {"ok": False, "error": format_clean_error(_redact_api_payload(exc), max_chars=1024)},
                )
            except OSError as exc:
                if _is_client_disconnect_error(exc):
                    self.close_connection = True
                    return
                self._send_json(
                    HTTPStatus.INTERNAL_SERVER_ERROR,
                    {"ok": False, "error": format_clean_error(_redact_api_payload(exc), max_chars=1024)},
                )
            except Exception as exc:
                self._send_json(
                    HTTPStatus.INTERNAL_SERVER_ERROR,
                    {"ok": False, "error": format_clean_error(_redact_api_payload(exc), max_chars=1024)},
                )

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
            except GalaxyScanInProgressError as exc:
                status_code = HTTPStatus.CONFLICT
                body = {
                    "ok": False,
                    "sweep_in_progress": True,
                    "sweep_blocked": True,
                    "error": format_clean_error(_redact_api_payload(exc), max_chars=1024),
                }
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
                body = {"ok": False, "error": format_clean_error(_redact_api_payload(exc), max_chars=1024)}
            except OSError as exc:
                if _is_client_disconnect_error(exc):
                    self.close_connection = True
                    return
                status_code = HTTPStatus.INTERNAL_SERVER_ERROR
                body = {"ok": False, "error": format_clean_error(_redact_api_payload(exc), max_chars=1024)}
            except Exception as exc:
                status_code = HTTPStatus.INTERNAL_SERVER_ERROR
                body = {"ok": False, "error": format_clean_error(_redact_api_payload(exc), max_chars=1024)}

            log_dir = getattr(bridge, "log_dir", None)
            if isinstance(log_dir, (str, Path)) or is_container_runtime():
                log_payload = (
                    payload
                    if isinstance(payload, Mapping)
                    else ({"raw_payload": payload} if payload is not None else None)
                )
                try:
                    append_api_log(
                        Path(log_dir) if isinstance(log_dir, (str, Path)) else None,
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
