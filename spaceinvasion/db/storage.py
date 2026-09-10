"""SQLite persistence layer for SpaceInvasion Autoplayer telemetry, tutorial progress, actions, and intel."""

from __future__ import annotations

from dataclasses import asdict, is_dataclass
from datetime import datetime, timezone
import heapq
import json
import math
import os
from pathlib import Path
import re
import sqlite3
import tempfile
import threading
import time
from typing import Any, Iterable, Mapping, Optional, Sequence
from zoneinfo import ZoneInfo

from spaceinvasion.core import game_data
from spaceinvasion.combat.fleet import (
    BashTracker,
    GalaxySlot,
    InboxMessage,
    SpyReport,
    _classify_and_parse_inbox_block,
    grade_colony_candidate,
    is_non_spy_notice_text,
    normalize_coords,
)
from spaceinvasion.core.logging_utils import (
    _SECRET_DICT_KEYS,
    _SID_RE as _LOG_SID_RE,
    _sub_sid_kv as _sub_log_sid_kv,
    is_secret_dict_key,
    redact_sensitive,
)
from spaceinvasion.db.migrations import (
    ATTACK_HISTORY_SCHEMA_VERSION,
    DEFAULT_GAME_MIGRATION_RUNNER,
    GAME_DB_MIGRATIONS,
    HISTORY_TABLES,
    HOT_TABLES,
    LATEST_SCHEMA_VERSION,
    PROTECTED_META_KEYS,
    SCHEMA_LEDGER_DDL,
    SCHEMA_VERSION,
    TABLE_TIER_BY_NAME,
    backfill_v012_hot_tables,
    create_sqlite_backup,
    import_legacy_attack_history_json,
    import_legacy_config_yaml,
    import_legacy_rank_history_json,
    import_legacy_snapshot_json,
)
from spaceinvasion.core.state import GameState, game_state_from_dict, normalize_planet_type

DEFAULT_DB_PATH = Path("state") / "spaceinvasion.db"
DEFAULT_ATTACK_HISTORY_PATH: Optional[Path] = None


def server_day_key(ts: Optional[float] = None) -> str:
    """Return the SpaceInvasion server calendar date (`YYYY-MM-DD`, Europe/Berlin with UTC fallback)."""
    eff_ts = _safe_float(ts, time.time()) if ts is not None else time.time()
    if eff_ts <= 0.0:
        eff_ts = time.time()
    try:
        tz = ZoneInfo("Europe/Berlin")
    except Exception:
        tz = timezone.utc
    try:
        return datetime.fromtimestamp(eff_ts, tz=tz).strftime("%Y-%m-%d")
    except (OSError, OverflowError, ValueError):
        return datetime.now(tz=timezone.utc).strftime("%Y-%m-%d")

_RESOURCE_KEYS: tuple[str, ...] = ("pig_iron", "metal", "kryptonite", "spice")
_EXTRA_SID_RE = re.compile(_LOG_SID_RE.pattern, _LOG_SID_RE.flags)


def _sub_extra_sid(match: re.Match[str]) -> str:
    return _sub_log_sid_kv(match)
_LOSS_OUTCOME_TOKENS: tuple[str, ...] = (
    "loss",
    "lost",
    "defeat",
    "fail",
    "abort",
    "draw",
    "destroy",
    "repel",
    "blocked",
    "error",
    "timeout",
    "intercept",
)
_WIN_OUTCOME_TOKENS: tuple[str, ...] = (
    "win",
    "won",
    "vict",
    "succ",
    "loot",
    "plunder",
    "captur",
    "ok",
    "dispatch",
)


_SQLITE_INT64_MIN = -(1 << 63)
_SQLITE_INT64_MAX = (1 << 63) - 1


_MAX_STORAGE_KEY_NODES = 256
_MAX_STORAGE_KEY_CHARS = 4096
_MAX_STORAGE_TREE_NODES = 32_768
_MAX_STORAGE_TREE_DEPTH = 64


def _clip_storage_str(text: str, limit: int = _MAX_STORAGE_KEY_CHARS) -> str:
    if len(text) <= limit:
        return text
    return text[:limit] + "...[truncated]"


def _raw_text(
    value: Any,
    default: str = "",
    *,
    _depth: int = 0,
    _seen: Optional[set[int]] = None,
    _budget: Optional[list[int]] = None,
) -> str:
    if value is None:
        return default
    seen = _seen if _seen is not None else set()
    budget = _budget if _budget is not None else [_MAX_STORAGE_KEY_NODES]
    if isinstance(value, os.PathLike):
        vid = id(value)
        if vid in seen or _depth >= 16 or budget[0] <= 0:
            return f"<{type(value).__name__}>"
        budget[0] -= 1
        seen.add(vid)
        try:
            try:
                value = os.fspath(value)
            except Exception:
                return f"<{type(value).__name__}>"
            if value is None:
                return default
            if isinstance(value, os.PathLike):
                return f"<{type(value).__name__}>"
            return _raw_text(value, default, _depth=_depth + 1, _seen=seen, _budget=budget)
        finally:
            seen.discard(vid)
    if isinstance(value, str):
        return value
    if isinstance(value, (bytes, bytearray)):
        return bytes(value).decode("utf-8", errors="replace")
    if isinstance(value, int) and not isinstance(value, bool):
        return game_data.fmt_compact_int(value) if abs(value).bit_length() >= 12_000 else str(value)
    if isinstance(value, BaseException):
        str_fn = getattr(type(value), "__str__", BaseException.__str__)
        if str_fn not in (BaseException.__str__, KeyError.__str__):
            try:
                return _clip_storage_str(str(value), 16_384)
            except Exception:
                pass
        args = getattr(value, "args", ())
        if not isinstance(args, (tuple, list)) or not args:
            return "" if str_fn is BaseException.__str__ else type(value).__name__
        vid = id(value)
        if vid in seen or _depth >= 16 or budget[0] <= 0:
            return f"<{type(value).__name__}>"
        budget[0] -= 1
        seen.add(vid)
        try:
            if len(args) == 1:
                a0 = args[0]
                if isinstance(a0, str):
                    res = repr(a0) if isinstance(value, KeyError) else a0
                    return _clip_storage_str(res, 16_384)
                return _clip_storage_str(
                    _raw_text(
                        a0,
                        f"<{type(a0).__name__}>",
                        _depth=_depth + 1,
                        _seen=seen,
                        _budget=budget,
                    ),
                    _MAX_STORAGE_KEY_CHARS,
                )
            parts: list[str] = []
            for a in args[:8]:
                if budget[0] <= 0:
                    parts.append("...")
                    break
                if isinstance(a, str):
                    parts.append(repr(a))
                else:
                    parts.append(
                        _raw_text(
                            a,
                            f"<{type(a).__name__}>",
                            _depth=_depth + 1,
                            _seen=seen,
                            _budget=budget,
                        )
                    )
            if len(args) > 8:
                parts.append(f"...+{len(args) - 8}")
            return _clip_storage_str("(" + ", ".join(parts) + ")", _MAX_STORAGE_KEY_CHARS)
        finally:
            seen.discard(vid)
    if isinstance(value, (tuple, frozenset, list, set, Mapping)):
        try:
            return game_data.safe_key(value)
        except Exception:
            return f"<{type(value).__name__}>"
    try:
        return str(value)
    except Exception:
        return f"<{type(value).__name__}>"


_SAFE_STATUS_COLORS: frozenset[str] = frozenset(
    {
        "",
        "free",
        "yellow",
        "white",
        "green",
        "blue",
        "red",
        "purple",
        "orange",
        "gray",
        "grey",
        "inactive",
        "vacation",
        "banned",
        "spacia",
    }
)
_NORM_GSP_RE = re.compile(r"(\d{2}):(\d{3}):(\d{2})", re.ASCII)
_ANY_GSP_RE = re.compile(r"(\d+):(\d+):(\d+)", re.ASCII)


def _sanitize_text(text: Any) -> str:
    raw = _raw_text(text, "")
    if not raw or len(raw) < 5:
        return raw
    s = str(redact_sensitive(raw))
    if "sid" not in s.lower() and "\u0130" not in s and "\u0131" not in s and "\u017f" not in s:
        return s
    return _EXTRA_SID_RE.sub(_sub_extra_sid, s)


def _safe_storage_key(k: Any, *, nonfinite_float: Any = None) -> Any:
    if isinstance(k, (bytes, bytearray, str, os.PathLike, BaseException)):
        return _sanitize_text(k)
    if isinstance(k, int) and not isinstance(k, bool):
        return game_data.cap_json_int(k)
    if k is None or isinstance(k, bool):
        return k
    if isinstance(k, float):
        return k if math.isfinite(k) else nonfinite_float
    try:
        safe_k = game_data.safe_key(k)
    except Exception:
        safe_k = f"<{type(k).__name__}>"
    return _sanitize_text(safe_k)


def _sanitize_obj(
    obj: Any,
    *,
    nonfinite_float: Any = 0.0,
    _depth: int = 0,
    _seen: Optional[set[int]] = None,
    _budget: Optional[list[int]] = None,
) -> Any:
    seen = _seen if _seen is not None else set()
    budget = _budget if _budget is not None else [_MAX_STORAGE_TREE_NODES]

    def _walk(val: Any, d: int) -> Any:
        tv = type(val)
        if val is None or tv is bool:
            return val
        if tv is int:
            return val if -game_data.MAX_JSON_SAFE_INT <= val <= game_data.MAX_JSON_SAFE_INT else game_data.cap_json_int(val)
        if tv is str:
            return _sanitize_text(val)
        if tv is float:
            return val if math.isfinite(val) else nonfinite_float
        is_dict = tv is dict
        is_seq = tv is list or tv is tuple
        if not (is_dict or is_seq):
            if isinstance(val, bool):
                return bool(val)
            if isinstance(val, int):
                return game_data.cap_json_int(int(val))
            if isinstance(val, (bytes, bytearray, str, os.PathLike, BaseException)):
                return _sanitize_text(val)
            if isinstance(val, float):
                fv = float(val)
                return fv if math.isfinite(fv) else nonfinite_float
        if is_dict or is_seq or isinstance(val, (Mapping, list, tuple, set, frozenset)):
            vid = id(val)
            if vid in seen:
                return "[Cycle]"
            if d >= _MAX_STORAGE_TREE_DEPTH or budget[0] <= 0:
                return "[Truncated]"
            budget[0] -= 1
            seen.add(vid)
            try:
                if is_dict or isinstance(val, Mapping):
                    out: dict[Any, Any] = {}
                    for k, v in val.items():
                        if budget[0] <= 0:
                            out["[Truncated]"] = "[Truncated]"
                            break
                        clean_k = (
                            _sanitize_text(k)
                            if type(k) is str
                            else _safe_storage_key(k, nonfinite_float=nonfinite_float)
                        )
                        if isinstance(clean_k, str) and is_secret_dict_key(clean_k):
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
        return _sanitize_text(val)

    return _walk(obj, _depth)


def _safe_int(value: Any, default: int = 0) -> int:
    return game_data.safe_int(value, default=default)


def _safe_optional_int(value: Any) -> Optional[int]:
    return game_data.safe_optional_int(value, default=None)


def _sql_int(value: Any, default: int = 0) -> int:
    val = game_data.safe_int(value, default=default)
    if val < _SQLITE_INT64_MIN:
        return _SQLITE_INT64_MIN
    if val > _SQLITE_INT64_MAX:
        return _SQLITE_INT64_MAX
    return val


def _sql_optional_int(value: Any) -> Optional[int]:
    val = game_data.safe_optional_int(value, default=None)
    if val is None:
        return None
    if val < _SQLITE_INT64_MIN:
        return _SQLITE_INT64_MIN
    if val > _SQLITE_INT64_MAX:
        return _SQLITE_INT64_MAX
    return val


def _safe_float(value: Any, default: float = 0.0) -> float:
    if type(value) is float:
        return value if math.isfinite(value) else default
    if value is None or isinstance(value, bool):
        return default
    if isinstance(value, (bytes, bytearray)):
        value = bytes(value).decode("utf-8", errors="replace")
    if isinstance(value, str):
        txt = value.strip()
        if not txt or not txt.isascii():
            return default
        value = txt
    elif isinstance(value, (dict, list, tuple, set, frozenset)) or isinstance(value, Mapping):
        return default
    try:
        f = float(value)
    except Exception:
        return default
    return f if math.isfinite(f) else default


def _safe_optional_float(value: Any) -> Optional[float]:
    if type(value) is float:
        return value if math.isfinite(value) else None
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (bytes, bytearray)):
        value = bytes(value).decode("utf-8", errors="replace")
    if isinstance(value, str):
        txt = value.strip()
        if not txt or not txt.isascii():
            return None
        value = txt
    elif isinstance(value, (dict, list, tuple, set, frozenset)) or isinstance(value, Mapping):
        return None
    try:
        f = float(value)
    except Exception:
        return None
    return f if math.isfinite(f) else None


def _coord_query_candidates(coords: str) -> tuple[str, list[str], Optional[str]]:
    raw = game_data.safe_key(coords).strip() if coords is not None else ""
    norm = normalize_coords(raw)
    if not norm:
        return "", [], None
    m = re.fullmatch(r"(\d+):(\d+):(\d+)", norm, re.ASCII)
    if not m:
        cands = [norm]
        if raw and raw != norm:
            cands.append(raw)
        return norm, cands, None
    g, s, p = int(m.group(1)[:9]), int(m.group(2)[:9]), int(m.group(3)[:9])
    variants: list[str] = []
    for candidate in (
        norm,
        raw,
        f"{g}:{s}:{p}",
        f"{g:02d}:{s}:{p}",
        f"{g}:{s:03d}:{p}",
        f"{g}:{s}:{p:02d}",
        f"{g:02d}:{s:03d}:{p}",
        f"{g:02d}:{s}:{p:02d}",
        f"{g}:{s:03d}:{p:02d}",
    ):
        if candidate and candidate not in variants:
            variants.append(candidate)
    return norm, variants, f"%{g}%:%{s}%:%{p}%"


def _safe_json_dict(raw: Any) -> dict[str, Any]:
    if raw is None:
        return {}
    if isinstance(raw, Mapping):
        clean = game_data.sanitize_json_tree(
            _sanitize_obj(dict(raw), nonfinite_float=None),
            max_int=game_data.MAX_JSON_SAFE_INT,
            nonfinite_float=None,
        )
        return clean if isinstance(clean, dict) else {}
    if isinstance(raw, (bytes, bytearray)):
        raw = bytes(raw).decode("utf-8", errors="replace")
    if not isinstance(raw, str):
        return {}
    txt = raw.strip()
    if not txt:
        return {}
    try:
        parsed = json.loads(txt)
    except (ValueError, TypeError):
        return {}
    if not isinstance(parsed, dict):
        return {}
    clean = _sanitize_obj(
        game_data.sanitize_json_tree(
            parsed,
            max_int=game_data.MAX_JSON_SAFE_INT,
            nonfinite_float=None,
        )
    )
    return clean if isinstance(clean, dict) else {}


def _safe_json_list(raw: Any) -> list[Any]:
    if raw is None:
        return []
    if isinstance(raw, list):
        clean = game_data.sanitize_json_tree(
            _sanitize_obj(list(raw), nonfinite_float=None),
            max_int=game_data.MAX_JSON_SAFE_INT,
            nonfinite_float=None,
        )
        return clean if isinstance(clean, list) else []
    if isinstance(raw, (bytes, bytearray)):
        raw = bytes(raw).decode("utf-8", errors="replace")
    if not isinstance(raw, str):
        return []
    txt = raw.strip()
    if not txt:
        return []
    try:
        parsed = json.loads(txt)
    except (ValueError, TypeError):
        return []
    if not isinstance(parsed, list):
        return []
    clean = _sanitize_obj(
        game_data.sanitize_json_tree(
            parsed,
            max_int=game_data.MAX_JSON_SAFE_INT,
            nonfinite_float=None,
        )
    )
    return clean if isinstance(clean, list) else []


def _empty_loot_int() -> dict[str, int]:
    return {rk: 0 for rk in _RESOURCE_KEYS}


def _empty_loot_float() -> dict[str, float]:
    return {rk: 0.0 for rk in _RESOURCE_KEYS}


def _normalize_loot(raw: Any) -> dict[str, int]:
    if raw is None:
        return _empty_loot_int()
    src: Any = raw
    if isinstance(src, (bytes, bytearray)):
        src = bytes(src).decode("utf-8", errors="replace")
    if isinstance(src, str):
        txt = src.strip()
        if not txt:
            return _empty_loot_int()
        try:
            src = json.loads(txt)
        except (ValueError, TypeError):
            return _empty_loot_int()
    if isinstance(src, Mapping):
        out: dict[str, int] = {}
        for rk in _RESOURCE_KEYS:
            out[rk] = game_data.cap_json_int(max(0, _safe_int(src.get(rk, 0), 0)))
        return out
    out = {}
    for rk in _RESOURCE_KEYS:
        if hasattr(src, rk):
            out[rk] = game_data.cap_json_int(max(0, _safe_int(getattr(src, rk), 0)))
        else:
            out[rk] = 0
    return out


def _is_win_outcome(outcome: str, loot_spice_eq: float) -> bool:
    s = _raw_text(outcome, "").strip().lower()
    if s and any(tok in s for tok in _LOSS_OUTCOME_TOKENS):
        return False
    if float(loot_spice_eq) > 0.0:
        return True
    return bool(s and any(tok in s for tok in _WIN_OUTCOME_TOKENS))

_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS meta (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL,
    updated_ts REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS rank_snapshots (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts REAL NOT NULL,
    universe_id INTEGER NOT NULL DEFAULT 0,
    nickname TEXT NOT NULL DEFAULT '',
    server_points INTEGER,
    server_rank INTEGER,
    estimated_points REAL NOT NULL,
    pts_per_hr REAL NOT NULL,
    spice_value REAL NOT NULL,
    planets INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_rank_snapshots_ts ON rank_snapshots(ts);

CREATE TABLE IF NOT EXISTS planet_snapshots (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts REAL NOT NULL,
    universe_id INTEGER NOT NULL DEFAULT 0,
    planet_id INTEGER NOT NULL,
    coords TEXT NOT NULL,
    name TEXT NOT NULL,
    planet_type TEXT NOT NULL,
    temperature INTEGER NOT NULL,
    fields_used INTEGER NOT NULL,
    fields_max INTEGER NOT NULL,
    pig_iron INTEGER NOT NULL,
    metal INTEGER NOT NULL,
    kryptonite INTEGER NOT NULL,
    spice INTEGER NOT NULL,
    energy_produced INTEGER NOT NULL,
    energy_used INTEGER NOT NULL,
    buildings_json TEXT NOT NULL,
    ships_json TEXT NOT NULL,
    defense_json TEXT NOT NULL,
    economy_rates_json TEXT NOT NULL,
    researches_json TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_planet_snapshots_pid_ts ON planet_snapshots(planet_id, ts);

CREATE TABLE IF NOT EXISTS state_snapshots (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts REAL NOT NULL,
    source TEXT NOT NULL DEFAULT 'advisor',
    universe_id INTEGER NOT NULL DEFAULT 0,
    user_id INTEGER NOT NULL DEFAULT 0,
    nickname TEXT NOT NULL DEFAULT '',
    active_planet_id INTEGER NOT NULL DEFAULT 0,
    planet_count INTEGER NOT NULL DEFAULT 0,
    planned_action_count INTEGER NOT NULL DEFAULT 0,
    state_json TEXT NOT NULL DEFAULT '{}',
    plan_json TEXT NOT NULL DEFAULT '[]'
);
CREATE INDEX IF NOT EXISTS idx_state_snapshots_ts ON state_snapshots(ts);

CREATE TABLE IF NOT EXISTS strategy_config_store (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    config_json TEXT NOT NULL,
    updated_ts REAL NOT NULL,
    source TEXT NOT NULL DEFAULT 'sqlite'
);

CREATE TABLE IF NOT EXISTS ui_notifications (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    created_ts REAL NOT NULL,
    updated_ts REAL NOT NULL,
    severity TEXT NOT NULL DEFAULT 'warning',
    category TEXT NOT NULL DEFAULT 'action_validation',
    title TEXT NOT NULL,
    message TEXT NOT NULL,
    help_prompt TEXT NOT NULL DEFAULT '',
    action_kind TEXT NOT NULL DEFAULT '',
    planet_coords TEXT NOT NULL DEFAULT '',
    target_name TEXT NOT NULL DEFAULT '',
    dedupe_key TEXT NOT NULL DEFAULT '',
    occurrence_count INTEGER NOT NULL DEFAULT 1,
    status TEXT NOT NULL DEFAULT 'unread',
    acknowledged_ts REAL,
    details_json TEXT NOT NULL DEFAULT '{}'
);
CREATE INDEX IF NOT EXISTS idx_ui_notifications_status_ts ON ui_notifications(status, updated_ts);
CREATE INDEX IF NOT EXISTS idx_ui_notifications_dedupe ON ui_notifications(dedupe_key, status);

CREATE TABLE IF NOT EXISTS economy_rates_cache (
    planet_id INTEGER PRIMARY KEY,
    coords TEXT NOT NULL,
    rates_json TEXT NOT NULL,
    updated_ts REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS tutorial_progress (
    step INTEGER PRIMARY KEY,
    total_steps INTEGER NOT NULL,
    tasks_json TEXT NOT NULL,
    reward_text TEXT NOT NULL,
    can_complete INTEGER NOT NULL,
    first_seen_ts REAL NOT NULL,
    last_seen_ts REAL NOT NULL,
    completed_ts REAL
);

CREATE TABLE IF NOT EXISTS action_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts REAL NOT NULL,
    source TEXT NOT NULL,
    command TEXT NOT NULL,
    priority TEXT,
    kind TEXT NOT NULL,
    planet_id INTEGER,
    planet_coords TEXT,
    target_id INTEGER,
    target_name TEXT,
    target_level INTEGER,
    executed INTEGER NOT NULL,
    dry_run INTEGER NOT NULL,
    reason TEXT,
    message TEXT
);
CREATE INDEX IF NOT EXISTS idx_action_history_ts ON action_history(ts);

CREATE TABLE IF NOT EXISTS spy_targets (
    coords TEXT PRIMARY KEY,
    owner TEXT NOT NULL DEFAULT '',
    planet_name TEXT NOT NULL DEFAULT '',
    status_color TEXT NOT NULL DEFAULT 'yellow',
    is_inactive INTEGER NOT NULL DEFAULT 0,
    is_spacia INTEGER NOT NULL DEFAULT 0,
    has_debris INTEGER NOT NULL DEFAULT 0,
    points INTEGER,
    pig_iron INTEGER NOT NULL DEFAULT 0,
    metal INTEGER NOT NULL DEFAULT 0,
    kryptonite INTEGER NOT NULL DEFAULT 0,
    spice INTEGER NOT NULL DEFAULT 0,
    fleet_json TEXT NOT NULL DEFAULT '{}',
    defense_json TEXT NOT NULL DEFAULT '{}',
    dug_in INTEGER NOT NULL DEFAULT 0,
    last_scanned_ts REAL NOT NULL,
    last_spied_ts REAL NOT NULL DEFAULT 0.0,
    blocked_until_ts REAL NOT NULL DEFAULT 0.0,
    block_reason TEXT NOT NULL DEFAULT '',
    bash_hits INTEGER NOT NULL DEFAULT 0,
    last_blocked_ts REAL NOT NULL DEFAULT 0.0,
    uninhabited_fields INTEGER,
    colonize_min_fields INTEGER,
    colonize_max_fields INTEGER,
    temperature INTEGER,
    spy_count INTEGER NOT NULL DEFAULT 0,
    is_own_planet INTEGER NOT NULL DEFAULT 0,
    merged_into TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS spy_reports (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts REAL NOT NULL,
    target_coords TEXT NOT NULL,
    owner TEXT NOT NULL DEFAULT '',
    planet_name TEXT NOT NULL DEFAULT '',
    is_spacia INTEGER NOT NULL DEFAULT 0,
    pig_iron INTEGER NOT NULL DEFAULT 0,
    metal INTEGER NOT NULL DEFAULT 0,
    kryptonite INTEGER NOT NULL DEFAULT 0,
    spice INTEGER NOT NULL DEFAULT 0,
    loot_spice_eq REAL NOT NULL DEFAULT 0.0,
    buildings_json TEXT NOT NULL DEFAULT '{}',
    fleet_json TEXT NOT NULL DEFAULT '{}',
    defense_json TEXT NOT NULL DEFAULT '{}',
    researches_json TEXT NOT NULL DEFAULT '{}',
    dug_in INTEGER,
    uninhabited_fields INTEGER,
    server_msg_id TEXT NOT NULL DEFAULT '',
    msg_date_text TEXT NOT NULL DEFAULT '',
    raw_text TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_spy_reports_coords_ts ON spy_reports(target_coords, ts);

CREATE TABLE IF NOT EXISTS game_messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ingested_ts REAL NOT NULL,
    msg_uid TEXT NOT NULL UNIQUE,
    server_msg_id TEXT NOT NULL DEFAULT '',
    folder_type TEXT NOT NULL DEFAULT '',
    category TEXT NOT NULL DEFAULT 'other',
    msg_date_text TEXT NOT NULL DEFAULT '',
    sender TEXT NOT NULL DEFAULT '',
    subject TEXT NOT NULL DEFAULT '',
    coords TEXT NOT NULL DEFAULT '',
    body_text TEXT NOT NULL DEFAULT '',
    raw_html TEXT NOT NULL DEFAULT '',
    parsed_json TEXT NOT NULL DEFAULT '{}',
    deleted_in_game INTEGER NOT NULL DEFAULT 0,
    deleted_ts REAL
);
CREATE INDEX IF NOT EXISTS idx_game_messages_ts ON game_messages(ingested_ts);
CREATE INDEX IF NOT EXISTS idx_game_messages_coords ON game_messages(coords, ingested_ts);
CREATE INDEX IF NOT EXISTS idx_game_messages_cat ON game_messages(category, ingested_ts);

CREATE TABLE IF NOT EXISTS galaxy_scans (
    galaxy INTEGER NOT NULL,
    system INTEGER NOT NULL,
    last_scanned_ts REAL NOT NULL,
    occupied_count INTEGER NOT NULL DEFAULT 0,
    target_count INTEGER NOT NULL DEFAULT 0,
    free_count INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (galaxy, system)
);
CREATE INDEX IF NOT EXISTS idx_galaxy_scans_ts ON galaxy_scans(last_scanned_ts);

CREATE TABLE IF NOT EXISTS galaxy_scan_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts REAL NOT NULL,
    galaxy INTEGER NOT NULL,
    system INTEGER NOT NULL,
    occupied_count INTEGER NOT NULL DEFAULT 0,
    target_count INTEGER NOT NULL DEFAULT 0,
    free_count INTEGER NOT NULL DEFAULT 0,
    debris_count INTEGER NOT NULL DEFAULT 0,
    asteroid_count INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_galaxy_scan_history_sys_ts ON galaxy_scan_history(galaxy, system, ts);

CREATE TABLE IF NOT EXISTS galaxy_slots (
    coords TEXT PRIMARY KEY,
    galaxy INTEGER NOT NULL,
    system INTEGER NOT NULL,
    position INTEGER NOT NULL,
    planet_name TEXT NOT NULL DEFAULT '',
    owner TEXT NOT NULL DEFAULT '',
    status_color TEXT NOT NULL DEFAULT 'free',
    is_inactive INTEGER NOT NULL DEFAULT 0,
    is_spacia INTEGER NOT NULL DEFAULT 0,
    has_debris INTEGER NOT NULL DEFAULT 0,
    last_scanned_ts REAL NOT NULL,
    colonize_min_fields INTEGER,
    colonize_max_fields INTEGER,
    temperature INTEGER
);
CREATE INDEX IF NOT EXISTS idx_galaxy_slots_sys ON galaxy_slots(galaxy, system, position);

CREATE TABLE IF NOT EXISTS galaxy_slot_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts REAL NOT NULL,
    coords TEXT NOT NULL,
    galaxy INTEGER NOT NULL,
    system INTEGER NOT NULL,
    position INTEGER NOT NULL,
    planet_name TEXT NOT NULL DEFAULT '',
    owner TEXT NOT NULL DEFAULT '',
    status_color TEXT NOT NULL DEFAULT 'free',
    is_inactive INTEGER NOT NULL DEFAULT 0,
    is_spacia INTEGER NOT NULL DEFAULT 0,
    has_debris INTEGER NOT NULL DEFAULT 0,
    has_asteroid INTEGER NOT NULL DEFAULT 0,
    colonize_min_fields INTEGER,
    colonize_max_fields INTEGER,
    temperature INTEGER
);
CREATE INDEX IF NOT EXISTS idx_galaxy_slot_history_coords_ts ON galaxy_slot_history(coords, ts);

CREATE TABLE IF NOT EXISTS config_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts REAL NOT NULL,
    config_json TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_config_history_ts ON config_history(ts);

CREATE TABLE IF NOT EXISTS raid_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    target_coords TEXT NOT NULL,
    attacked_ts REAL NOT NULL,
    is_exempt INTEGER NOT NULL DEFAULT 0,
    outcome TEXT NOT NULL DEFAULT '',
    loot_json TEXT NOT NULL DEFAULT '{}'
);
CREATE INDEX IF NOT EXISTS idx_raid_history_coords_ts ON raid_history(target_coords, attacked_ts);

CREATE TABLE IF NOT EXISTS bash_blocks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    target_coords TEXT NOT NULL,
    blocked_ts REAL NOT NULL,
    blocked_until_ts REAL NOT NULL,
    cooldown_seconds REAL NOT NULL DEFAULT 0.0,
    is_bash INTEGER NOT NULL DEFAULT 1,
    reason TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_bash_blocks_coords_ts ON bash_blocks(target_coords, blocked_ts);

CREATE TABLE IF NOT EXISTS battle_reports (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts REAL NOT NULL,
    msg_uid TEXT NOT NULL UNIQUE,
    server_msg_id TEXT NOT NULL DEFAULT '',
    battle_id TEXT NOT NULL DEFAULT '',
    msg_date_text TEXT NOT NULL DEFAULT '',
    target_coords TEXT NOT NULL DEFAULT '',
    attacker TEXT NOT NULL DEFAULT '',
    defender TEXT NOT NULL DEFAULT '',
    outcome TEXT NOT NULL DEFAULT 'win',
    rounds INTEGER NOT NULL DEFAULT 0,
    pig_iron INTEGER NOT NULL DEFAULT 0,
    metal INTEGER NOT NULL DEFAULT 0,
    kryptonite INTEGER NOT NULL DEFAULT 0,
    spice INTEGER NOT NULL DEFAULT 0,
    loot_spice_eq REAL NOT NULL DEFAULT 0.0,
    attacker_lost_spice INTEGER NOT NULL DEFAULT 0,
    defender_lost_spice INTEGER NOT NULL DEFAULT 0,
    debris_pig_iron INTEGER NOT NULL DEFAULT 0,
    debris_metal INTEGER NOT NULL DEFAULT 0,
    debris_spice_eq REAL NOT NULL DEFAULT 0.0,
    raw_text TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_battle_reports_coords_ts ON battle_reports(target_coords, ts);

CREATE TABLE IF NOT EXISTS recycle_reports (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts REAL NOT NULL,
    msg_uid TEXT NOT NULL UNIQUE,
    server_msg_id TEXT NOT NULL DEFAULT '',
    msg_date_text TEXT NOT NULL DEFAULT '',
    coords TEXT NOT NULL DEFAULT '',
    recycler_count INTEGER NOT NULL DEFAULT 0,
    cargo_capacity INTEGER NOT NULL DEFAULT 0,
    debris_avail_pig INTEGER NOT NULL DEFAULT 0,
    debris_avail_metal INTEGER NOT NULL DEFAULT 0,
    harvested_pig_iron INTEGER NOT NULL DEFAULT 0,
    harvested_metal INTEGER NOT NULL DEFAULT 0,
    harvested_kryptonite INTEGER NOT NULL DEFAULT 0,
    harvested_spice INTEGER NOT NULL DEFAULT 0,
    harvested_spice_eq REAL NOT NULL DEFAULT 0.0,
    raw_text TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_recycle_reports_coords_ts ON recycle_reports(coords, ts);

CREATE TABLE IF NOT EXISTS manual_queue (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    created_ts REAL NOT NULL,
    updated_ts REAL NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending',
    priority TEXT NOT NULL DEFAULT 'P1',
    sort_rank INTEGER NOT NULL DEFAULT 1000,
    kind TEXT NOT NULL,
    planet_id INTEGER NOT NULL DEFAULT 0,
    planet_coords TEXT NOT NULL DEFAULT '',
    target_id INTEGER,
    target_name TEXT NOT NULL DEFAULT '',
    target_coords TEXT NOT NULL DEFAULT '',
    target_level INTEGER,
    note TEXT NOT NULL DEFAULT '',
    completed_ts REAL,
    canceled_ts REAL,
    result_message TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_manual_queue_status_prio_rank
    ON manual_queue(status, priority, sort_rank, id);

CREATE TABLE IF NOT EXISTS manual_queue_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts REAL NOT NULL,
    queue_id INTEGER NOT NULL,
    event_type TEXT NOT NULL,
    details_json TEXT NOT NULL DEFAULT '{}'
);
CREATE INDEX IF NOT EXISTS idx_manual_queue_events_qid_ts
    ON manual_queue_events(queue_id, ts);

CREATE TABLE IF NOT EXISTS colony_attempts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts REAL NOT NULL,
    event_type TEXT NOT NULL,
    target_coords TEXT NOT NULL,
    origin_coords TEXT NOT NULL DEFAULT '',
    planet_id INTEGER,
    fields_rolled INTEGER,
    min_fields_target INTEGER NOT NULL DEFAULT 300,
    expected_min_fields INTEGER,
    expected_max_fields INTEGER,
    temperature INTEGER,
    grade TEXT NOT NULL DEFAULT '',
    score INTEGER,
    status_message TEXT NOT NULL DEFAULT '',
    details_json TEXT NOT NULL DEFAULT '{}'
);
CREATE INDEX IF NOT EXISTS idx_colony_attempts_coords_ts
    ON colony_attempts(target_coords, ts);
CREATE INDEX IF NOT EXISTS idx_colony_attempts_ts
    ON colony_attempts(ts);
""" + SCHEMA_LEDGER_DDL


def _stringify_keys(
    obj: Any,
    *,
    _depth: int = 0,
    _seen: Optional[set[int]] = None,
    _budget: Optional[list[int]] = None,
) -> Any:
    if isinstance(obj, (dict, list)):
        seen = _seen if _seen is not None else set()
        budget = _budget if _budget is not None else [_MAX_STORAGE_TREE_NODES]
        vid = id(obj)
        if vid in seen:
            return "[Cycle]"
        if _depth >= _MAX_STORAGE_TREE_DEPTH or budget[0] <= 0:
            return "[Truncated]"
        budget[0] -= 1
        seen.add(vid)
        try:
            if isinstance(obj, dict):
                out: dict[str, Any] = {}
                for k, v in obj.items():
                    if budget[0] <= 0:
                        out["[Truncated]"] = "[Truncated]"
                        break
                    sk = k if isinstance(k, str) else _raw_text(k, "<key>")
                    out[sk] = _stringify_keys(
                        v, _depth=_depth + 1, _seen=seen, _budget=budget
                    )
                return out
            out_l: list[Any] = []
            for v in obj:
                if budget[0] <= 0:
                    out_l.append("[Truncated]")
                    break
                out_l.append(
                    _stringify_keys(v, _depth=_depth + 1, _seen=seen, _budget=budget)
                )
            return out_l
        finally:
            seen.discard(vid)
    return obj


def _json_dumps(obj: Any) -> str:
    clean = redact_sensitive(_sanitize_obj(redact_sensitive(obj), nonfinite_float=None))
    try:
        return json.dumps(clean, sort_keys=True, separators=(",", ":"), allow_nan=False)
    except TypeError:
        return json.dumps(
            _stringify_keys(clean),
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )


def _resolve_attack_history_path(
    attack_history_path: Optional[Path | str],
    *,
    is_memory: bool,
    db_path: Path,
) -> Optional[Path]:
    if attack_history_path is not None:
        raw_txt = _raw_text(attack_history_path, "").strip()
        if not raw_txt or raw_txt == ".":
            return None
        cand = Path(raw_txt) if isinstance(attack_history_path, (str, bytes, bytearray)) else attack_history_path
        if _raw_text(cand, "").strip() in ("", "."):
            return None
        return cand if isinstance(cand, Path) else Path(raw_txt)
    if is_memory:
        return None
    if db_path.resolve() == DEFAULT_DB_PATH.resolve():
        return DEFAULT_ATTACK_HISTORY_PATH
    return None


class GameDatabase:
    """Thread-safe SQLite store for empire telemetry, tutorial progress, actions, and intel."""

    def __init__(
        self,
        db_path: Path | str = DEFAULT_DB_PATH,
        *,
        legacy_rank_json: Optional[Path] = None,
        attack_history_path: Optional[Path | str] = None,
        target_schema_version: Optional[int] = None,
        backup_on_upgrade: Optional[bool] = None,
    ) -> None:
        raw_path = _raw_text(db_path, "")
        self._is_memory = ":memory:" in raw_path or "mode=memory" in raw_path
        if self._is_memory:
            self.db_path = Path(":memory:")
            self.attack_history_path: Optional[Path] = _resolve_attack_history_path(
                attack_history_path,
                is_memory=True,
                db_path=self.db_path,
            )
            conn_target = raw_path
            use_uri = raw_path.startswith("file:")
        else:
            self.db_path = db_path if isinstance(db_path, Path) else Path(raw_path)
            self.db_path.parent.mkdir(parents=True, exist_ok=True)
            self.attack_history_path = _resolve_attack_history_path(
                attack_history_path,
                is_memory=False,
                db_path=self.db_path,
            )
            conn_target = str(self.db_path)
            use_uri = False

        self._lock = threading.Lock()
        self._conn = sqlite3.connect(
            conn_target,
            uri=use_uri,
            timeout=10.0,
            check_same_thread=False,
        )
        try:
            self._conn.row_factory = sqlite3.Row
            with self._lock:
                if not self._is_memory:
                    self._conn.execute("PRAGMA journal_mode=WAL;")
                    self._conn.execute("PRAGMA synchronous=NORMAL;")
                self._conn.execute("PRAGMA foreign_keys=ON;")
                self._last_slot_history_sig: Optional[tuple[float, tuple[str, ...]]] = None
                self._summary_cache_ver: Optional[tuple[int, int, int]] = None
                self._summary_cache_json: Optional[str] = None
                self._galaxy_intel_base_ver: Optional[tuple[int, int]] = None
                self._galaxy_intel_base_cache: dict[tuple[int, ...], dict[str, Any]] = {}
                self._colony_cand_cache_ver: Optional[tuple[int, int]] = None
                self._colony_cand_cache: dict[tuple[Any, ...], list[dict[str, Any]]] = {}
                now_ts = time.time()
                self._migration_result = DEFAULT_GAME_MIGRATION_RUNNER.migrate(
                    self._conn,
                    db_path=None if self._is_memory else self.db_path,
                    is_memory=self._is_memory,
                    target_version=target_schema_version,
                    backup=bool(backup_on_upgrade) if backup_on_upgrade is not None else False,
                    auto_backup_default_db=(True if backup_on_upgrade is None else bool(backup_on_upgrade)),
                    now_ts=now_ts,
                )
                self._repair_spy_targets_locked()
                if legacy_rank_json is not None and legacy_rank_json.exists():
                    self._migrate_legacy_rank_json(legacy_rank_json)
                if not self._is_memory and self.db_path.resolve() == DEFAULT_DB_PATH.resolve():
                    if DEFAULT_GAME_MIGRATION_RUNNER._table_exists(self._conn, "raid_history"):
                        legacy_atk = self.db_path.parent.parent / "data" / "attack_history.json"
                        if legacy_atk.is_file():
                            import_legacy_attack_history_json(
                                self._conn, legacy_atk, now_ts=now_ts, remove_after_import=True
                            )
                    if DEFAULT_GAME_MIGRATION_RUNNER._table_exists(self._conn, "rank_snapshots"):
                        legacy_rk = self.db_path.parent / "rank_history.json"
                        if legacy_rk.is_file():
                            import_legacy_rank_history_json(
                                self._conn, legacy_rk, now_ts=now_ts, remove_after_import=True
                            )
                    if DEFAULT_GAME_MIGRATION_RUNNER._table_exists(self._conn, "state_snapshots"):
                        legacy_snap = self.db_path.parent / "snapshot.json"
                        if legacy_snap.is_file():
                            import_legacy_snapshot_json(
                                self._conn, legacy_snap, now_ts=now_ts, remove_after_import=True
                            )
                    if DEFAULT_GAME_MIGRATION_RUNNER._table_exists(self._conn, "strategy_config_store"):
                        legacy_cfg = self.db_path.parent.parent / "config.yaml"
                        if legacy_cfg.is_file():
                            import_legacy_config_yaml(
                                self._conn, legacy_cfg, now_ts=now_ts, remove_after_import=True
                            )
                if DEFAULT_GAME_MIGRATION_RUNNER._table_exists(self._conn, "game_messages"):
                    self._backfill_reports_from_game_messages_locked(now_ts)
                self._conn.commit()
        except Exception:
            self._conn.close()
            raise

    def _backfill_reports_from_game_messages_locked(self, now_ts: float) -> None:
        """Non-destructively re-parse existing `game_messages` rows to populate `battle_reports`, `recycle_reports`, and `spy_reports`."""
        max_row = self._conn.execute("SELECT COALESCE(MAX(id), 0) AS max_id FROM game_messages").fetchone()
        max_id = int(max_row["max_id"] or 0) if max_row is not None else 0
        if max_id <= 0:
            return
        meta_row = self._conn.execute(
            "SELECT value FROM meta WHERE key = 'reports_backfill_v2'"
        ).fetchone()
        if meta_row is not None and str(meta_row["value"]) == str(max_id):
            return

        own_coords = {
            normalize_coords(_raw_text(r["coords"], ""))
            for r in self._conn.execute("SELECT DISTINCT coords FROM planet_snapshots").fetchall()
            if normalize_coords(_raw_text(r["coords"], ""))
        }
        own_coords.update(
            normalize_coords(_raw_text(r["coords"], ""))
            for r in self._conn.execute("SELECT coords FROM spy_targets WHERE is_own_planet = 1").fetchall()
            if normalize_coords(_raw_text(r["coords"], ""))
        )

        msg_rows = self._conn.execute(
            """
            SELECT id, ingested_ts, msg_uid, server_msg_id, folder_type, category,
                   msg_date_text, sender, subject, coords, body_text, raw_html
            FROM game_messages
            WHERE msg_uid NOT LIKE 'id:msg-%'
            ORDER BY id ASC
            """
        ).fetchall()

        for r in msg_rows:
            raw_html = _raw_text(r["raw_html"], "").strip()
            btxt_orig = _raw_text(r["body_text"], "").strip()
            if re.fullmatch(r"\d[\d.]*\s*/\s*\d[\d.]*", btxt_orig):
                continue
            ftype = _raw_text(r["folder_type"], "")
            subj_orig = _raw_text(r["subject"], "")
            sender_orig = _raw_text(r["sender"], "")
            dt_orig = _raw_text(r["msg_date_text"], "")
            fallback_html = (
                f"<table><tr><td>{dt_orig}</td><td>{sender_orig}</td><td>{subj_orig}</td></tr>"
                f"<tr><td colspan='3'>{btxt_orig}{raw_html}</td></tr></table>"
            )
            reparsed = (
                _classify_and_parse_inbox_block(
                    raw_html,
                    folder_type=ftype,
                    exclude_coords=own_coords,
                )
                if raw_html
                else None
            )
            if reparsed is None:
                reparsed = _classify_and_parse_inbox_block(
                    fallback_html,
                    folder_type=ftype,
                    exclude_coords=own_coords,
                )
            if reparsed is None:
                continue

            row_ts = _safe_float(r["ingested_ts"], now_ts) or now_ts
            uid = _sanitize_text(r["msg_uid"])
            srv_id_s = _sanitize_text(r["server_msg_id"] or reparsed.server_msg_id)[:64]
            dt_s = _sanitize_text(dt_orig or reparsed.msg_date_text)[:64]
            cat_s = _sanitize_text(reparsed.category or r["category"] or "other")[:64]
            norm_c = normalize_coords(reparsed.coords) if reparsed.coords else normalize_coords(_raw_text(r["coords"], ""))
            clean_btxt = _sanitize_text(reparsed.body_text or btxt_orig)[:8000]
            pdata = reparsed.parsed_data if isinstance(reparsed.parsed_data, Mapping) else {}

            self._conn.execute(
                """
                UPDATE game_messages
                SET category = ?,
                    coords = CASE WHEN ? != '' THEN ? ELSE coords END,
                    body_text = CASE WHEN ? != '' THEN ? ELSE body_text END,
                    parsed_json = CASE WHEN ? != '{}' THEN ? ELSE parsed_json END
                WHERE id = ?
                """,
                (
                    cat_s,
                    norm_c,
                    norm_c,
                    clean_btxt,
                    clean_btxt,
                    _json_dumps(pdata),
                    _json_dumps(pdata),
                    int(r["id"]),
                ),
            )

            if cat_s == "battle_report":
                loot_obj = pdata.get("loot") if isinstance(pdata.get("loot"), Mapping) else {}
                deb_obj = pdata.get("debris") if isinstance(pdata.get("debris"), Mapping) else {}
                b_pig = max(0, _sql_int(loot_obj.get("pig_iron"), 0))
                b_met = max(0, _sql_int(loot_obj.get("metal"), 0))
                b_kry = max(0, _sql_int(loot_obj.get("kryptonite"), 0))
                b_spi = max(0, _sql_int(loot_obj.get("spice"), 0))
                b_seq = _safe_float(
                    pdata.get("loot_spice_eq"),
                    round(b_pig / 6.0 + b_met / 2.5 + b_kry / 3.0 + float(b_spi), 2),
                )
                d_pig = max(0, _sql_int(deb_obj.get("pig_iron"), 0))
                d_met = max(0, _sql_int(deb_obj.get("metal"), 0))
                d_seq = _safe_float(pdata.get("debris_spice_eq"), round(d_pig / 6.0 + d_met / 2.5, 2))
                self._conn.execute(
                    """
                    INSERT INTO battle_reports(
                        ts, msg_uid, server_msg_id, battle_id, msg_date_text,
                        target_coords, attacker, defender, outcome, rounds,
                        pig_iron, metal, kryptonite, spice, loot_spice_eq,
                        attacker_lost_spice, defender_lost_spice,
                        debris_pig_iron, debris_metal, debris_spice_eq, raw_text
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(msg_uid) DO UPDATE SET
                        battle_id = CASE WHEN excluded.battle_id != '' THEN excluded.battle_id ELSE battle_reports.battle_id END,
                        attacker = CASE WHEN excluded.attacker != '' THEN excluded.attacker ELSE battle_reports.attacker END,
                        defender = CASE WHEN excluded.defender != '' THEN excluded.defender ELSE battle_reports.defender END,
                        outcome = excluded.outcome,
                        rounds = MAX(battle_reports.rounds, excluded.rounds),
                        pig_iron = MAX(battle_reports.pig_iron, excluded.pig_iron),
                        metal = MAX(battle_reports.metal, excluded.metal),
                        kryptonite = MAX(battle_reports.kryptonite, excluded.kryptonite),
                        spice = MAX(battle_reports.spice, excluded.spice),
                        loot_spice_eq = MAX(battle_reports.loot_spice_eq, excluded.loot_spice_eq),
                        attacker_lost_spice = MAX(battle_reports.attacker_lost_spice, excluded.attacker_lost_spice),
                        defender_lost_spice = MAX(battle_reports.defender_lost_spice, excluded.defender_lost_spice),
                        debris_pig_iron = MAX(battle_reports.debris_pig_iron, excluded.debris_pig_iron),
                        debris_metal = MAX(battle_reports.debris_metal, excluded.debris_metal),
                        debris_spice_eq = MAX(battle_reports.debris_spice_eq, excluded.debris_spice_eq),
                        raw_text = CASE WHEN excluded.raw_text != '' THEN excluded.raw_text ELSE battle_reports.raw_text END
                    """,
                    (
                        row_ts,
                        uid,
                        srv_id_s,
                        _sanitize_text(pdata.get("battle_id") or "")[:64],
                        dt_s,
                        norm_c,
                        _sanitize_text(pdata.get("attacker") or "")[:120],
                        _sanitize_text(pdata.get("defender") or "")[:120],
                        _sanitize_text(pdata.get("outcome") or "win")[:32],
                        max(0, _sql_int(pdata.get("rounds"), 0)),
                        b_pig,
                        b_met,
                        b_kry,
                        b_spi,
                        b_seq,
                        max(0, _sql_int(pdata.get("attacker_lost_spice"), 0)),
                        max(0, _sql_int(pdata.get("defender_lost_spice"), 0)),
                        d_pig,
                        d_met,
                        d_seq,
                        clean_btxt[:8000],
                    ),
                )
            elif cat_s == "recycle_report":
                harv_obj = pdata.get("harvested") if isinstance(pdata.get("harvested"), Mapping) else {}
                avail_obj = pdata.get("debris_available") if isinstance(pdata.get("debris_available"), Mapping) else {}
                h_pig = max(0, _sql_int(harv_obj.get("pig_iron"), 0))
                h_met = max(0, _sql_int(harv_obj.get("metal"), 0))
                h_kry = max(0, _sql_int(harv_obj.get("kryptonite"), 0))
                h_spi = max(0, _sql_int(harv_obj.get("spice"), 0))
                h_seq = _safe_float(
                    pdata.get("harvested_spice_eq"),
                    round(h_pig / 6.0 + h_met / 2.5 + h_kry / 3.0 + float(h_spi), 2),
                )
                self._conn.execute(
                    """
                    INSERT INTO recycle_reports(
                        ts, msg_uid, server_msg_id, msg_date_text, coords,
                        recycler_count, cargo_capacity, debris_avail_pig, debris_avail_metal,
                        harvested_pig_iron, harvested_metal, harvested_kryptonite, harvested_spice,
                        harvested_spice_eq, raw_text
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(msg_uid) DO UPDATE SET
                        coords = CASE WHEN excluded.coords != '' THEN excluded.coords ELSE recycle_reports.coords END,
                        recycler_count = MAX(recycle_reports.recycler_count, excluded.recycler_count),
                        cargo_capacity = MAX(recycle_reports.cargo_capacity, excluded.cargo_capacity),
                        debris_avail_pig = excluded.debris_avail_pig,
                        debris_avail_metal = excluded.debris_avail_metal,
                        harvested_pig_iron = excluded.harvested_pig_iron,
                        harvested_metal = excluded.harvested_metal,
                        harvested_kryptonite = excluded.harvested_kryptonite,
                        harvested_spice = excluded.harvested_spice,
                        harvested_spice_eq = excluded.harvested_spice_eq,
                        raw_text = CASE WHEN excluded.raw_text != '' THEN excluded.raw_text ELSE recycle_reports.raw_text END
                    """,
                    (
                        row_ts,
                        uid,
                        srv_id_s,
                        dt_s,
                        norm_c,
                        max(0, _sql_int(pdata.get("recycler_count"), 0)),
                        max(0, _sql_int(pdata.get("cargo_capacity"), 0)),
                        max(0, _sql_int(avail_obj.get("pig_iron"), 0)),
                        max(0, _sql_int(avail_obj.get("metal"), 0)),
                        h_pig,
                        h_met,
                        h_kry,
                        h_spi,
                        h_seq,
                        clean_btxt[:8000],
                    ),
                )
            elif cat_s == "spy_report" and reparsed.spy_report is not None:
                srep = reparsed.spy_report
                srep_res_sum = (
                    _sql_int(srep.resources.pig_iron, 0)
                    + _sql_int(srep.resources.metal, 0)
                    + _sql_int(srep.resources.kryptonite, 0)
                    + _sql_int(srep.resources.spice, 0)
                )
                if srv_id_s and (srep_res_sum > 0 or srep.buildings or srep.uninhabited_fields is not None):
                    self._conn.execute(
                        """
                        UPDATE spy_reports
                        SET planet_name = CASE WHEN ? != '' THEN ? ELSE planet_name END,
                            pig_iron = CASE WHEN ? > 0 THEN ? ELSE pig_iron END,
                            metal = CASE WHEN ? > 0 THEN ? ELSE metal END,
                            kryptonite = CASE WHEN ? > 0 THEN ? ELSE kryptonite END,
                            spice = CASE WHEN ? > 0 THEN ? ELSE spice END,
                            buildings_json = CASE WHEN ? != '{}' THEN ? ELSE buildings_json END,
                            fleet_json = CASE WHEN ? != '{}' THEN ? ELSE fleet_json END,
                            defense_json = CASE WHEN ? != '{}' THEN ? ELSE defense_json END,
                            researches_json = CASE WHEN ? != '{}' THEN ? ELSE researches_json END,
                            uninhabited_fields = COALESCE(?, uninhabited_fields)
                        WHERE server_msg_id = ?
                        """,
                        (
                            _sanitize_text(srep.planet_name)[:80],
                            _sanitize_text(srep.planet_name)[:80],
                            _sql_int(srep.resources.pig_iron, 0),
                            _sql_int(srep.resources.pig_iron, 0),
                            _sql_int(srep.resources.metal, 0),
                            _sql_int(srep.resources.metal, 0),
                            _sql_int(srep.resources.kryptonite, 0),
                            _sql_int(srep.resources.kryptonite, 0),
                            _sql_int(srep.resources.spice, 0),
                            _sql_int(srep.resources.spice, 0),
                            _json_dumps(srep.buildings),
                            _json_dumps(srep.buildings),
                            _json_dumps(srep.fleet),
                            _json_dumps(srep.fleet),
                            _json_dumps(srep.defense),
                            _json_dumps(srep.defense),
                            _json_dumps(srep.researches),
                            _json_dumps(srep.researches),
                            _sql_optional_int(srep.uninhabited_fields),
                            srv_id_s,
                        ),
                    )
                    if norm_c and srep_res_sum > 0:
                        self._conn.execute(
                            """
                            UPDATE spy_targets
                            SET planet_name = CASE WHEN planet_name = '' AND ? != '' THEN ? ELSE planet_name END,
                                pig_iron = CASE WHEN pig_iron = 0 THEN ? ELSE pig_iron END,
                                metal = CASE WHEN metal = 0 THEN ? ELSE metal END,
                                kryptonite = CASE WHEN kryptonite = 0 THEN ? ELSE kryptonite END,
                                spice = CASE WHEN spice = 0 THEN ? ELSE spice END
                            WHERE coords = ?
                            """,
                            (
                                _sanitize_text(srep.planet_name)[:80],
                                _sanitize_text(srep.planet_name)[:80],
                                _sql_int(srep.resources.pig_iron, 0),
                                _sql_int(srep.resources.metal, 0),
                                _sql_int(srep.resources.kryptonite, 0),
                                _sql_int(srep.resources.spice, 0),
                                norm_c,
                            ),
                        )

        self._conn.execute(
            """
            INSERT INTO meta(key, value, updated_ts) VALUES('reports_backfill_v2', ?, ?)
            ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_ts = excluded.updated_ts
            """,
            (str(max_id), now_ts),
        )

    def _migrate_legacy_rank_json(self, legacy_path: Path) -> None:
        if not DEFAULT_GAME_MIGRATION_RUNNER._table_exists(self._conn, "rank_snapshots"):
            return
        import_legacy_rank_history_json(
            self._conn,
            legacy_path,
            now_ts=time.time(),
            remove_after_import=False,
        )

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    def _db_version_locked(self) -> tuple[int, int]:
        dv_row = self._conn.execute("PRAGMA data_version").fetchone()
        dv = int(dv_row[0]) if dv_row is not None else 0
        return (int(self._conn.total_changes), dv)

    def _invalidate_hot_caches_locked(self) -> None:
        self._galaxy_intel_base_ver = None
        self._galaxy_intel_base_cache = None
        self._colony_cand_cache_ver = None
        self._colony_cand_cache = {}
        self._summary_cache_ver = None
        self._summary_cache_json = None

    @staticmethod
    def _parse_norm_gsp_fast(raw_c: Any) -> Optional[tuple[str, int, int, int]]:
        if type(raw_c) is str:
            nc = raw_c if (len(raw_c) == 9 and raw_c[2] == ":" and raw_c[6] == ":" and raw_c[0:2].isdigit() and raw_c[3:6].isdigit() and raw_c[7:9].isdigit()) else normalize_coords(raw_c.strip())
        else:
            nc = normalize_coords(_raw_text(raw_c, "").strip())
        if not nc:
            return None
        if len(nc) == 9 and nc[2] == ":" and nc[6] == ":" and nc[0:2].isdigit() and nc[3:6].isdigit() and nc[7:9].isdigit():
            return (nc, int(nc[0:2]), int(nc[3:6]), int(nc[7:9]))
        m = _NORM_GSP_RE.match(nc)
        if not m:
            return None
        return (nc, int(m.group(1)), int(m.group(2)), int(m.group(3)))

    def rebuild_hot_tables(self, *, ts: Optional[float] = None) -> dict[str, int]:
        """Rebuild V012 hot operational projection tables idempotently from source/history tables."""
        now_ts = round(_safe_float(ts, time.time()) if ts is not None else time.time(), 3)
        with self._lock:
            counts = backfill_v012_hot_tables(self._conn, now_ts)
            self._conn.commit()
            self._invalidate_hot_caches_locked()
        return {str(k): int(v) for k, v in counts.items()}

    def _upsert_latest_state_hot_locked(
        self,
        *,
        source_snapshot_id: int,
        ts: float,
        source: str,
        universe_id: int,
        user_id: int,
        nickname: str,
        active_planet_id: int,
        planet_count: int,
        planned_action_count: int,
        state_json: str,
        plan_json: str,
        now_ts: float,
    ) -> None:
        if not DEFAULT_GAME_MIGRATION_RUNNER._table_exists(self._conn, "latest_state_hot"):
            return
        self._conn.execute(
            """
            INSERT INTO latest_state_hot(
                id, source_snapshot_id, ts, source, universe_id, user_id,
                nickname, active_planet_id, planet_count, planned_action_count,
                state_json, plan_json, updated_ts
            ) VALUES (1, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                source_snapshot_id = excluded.source_snapshot_id,
                ts = excluded.ts,
                source = excluded.source,
                universe_id = excluded.universe_id,
                user_id = excluded.user_id,
                nickname = excluded.nickname,
                active_planet_id = excluded.active_planet_id,
                planet_count = excluded.planet_count,
                planned_action_count = excluded.planned_action_count,
                state_json = excluded.state_json,
                plan_json = excluded.plan_json,
                updated_ts = excluded.updated_ts
            """,
            (
                int(source_snapshot_id),
                round(float(ts), 3),
                source,
                int(universe_id),
                int(user_id),
                nickname,
                int(active_planet_id),
                int(planet_count),
                int(planned_action_count),
                state_json,
                plan_json,
                round(float(now_ts), 3),
            ),
        )

    def _upsert_empire_planets_hot_locked(self, state: GameState, now_ts: float) -> set[tuple[int, int]]:
        touched_sectors: set[tuple[int, int]] = set()
        if not DEFAULT_GAME_MIGRATION_RUNNER._table_exists(self._conn, "empire_planets_hot"):
            return touched_sectors
        ts_r = round(float(now_ts), 3)
        prev_coords_rows = self._conn.execute(
            "SELECT planet_id, coords FROM empire_planets_hot WHERE is_active = 1"
        ).fetchall()
        prev_by_pid = {int(r[0]): str(r[1] or "") for r in prev_coords_rows}
        active_pids: list[int] = []
        for planet in state.planets:
            pid_sql = _sql_int(planet.planet_id, 0)
            if pid_sql <= 0:
                continue
            active_pids.append(pid_sql)
            norm_c = normalize_coords(_raw_text(planet.coords, "").strip())
            if prev_by_pid.get(pid_sql) != norm_c:
                for cand_c in (prev_by_pid.get(pid_sql, ""), norm_c):
                    parsed_c = self._parse_norm_gsp_fast(cand_c)
                    if parsed_c is not None and 1 <= parsed_c[1] <= 14 and 1 <= parsed_c[2] <= 400:
                        touched_sectors.add((parsed_c[1], parsed_c[2]))
            self._conn.execute(
                """
                INSERT INTO empire_planets_hot(
                    planet_id, universe_id, coords, name, planet_type,
                    temperature, fields_used, fields_max,
                    pig_iron, metal, kryptonite, spice,
                    energy_produced, energy_used, is_active, updated_ts
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?)
                ON CONFLICT(planet_id) DO UPDATE SET
                    universe_id = excluded.universe_id,
                    coords = excluded.coords,
                    name = excluded.name,
                    planet_type = excluded.planet_type,
                    temperature = excluded.temperature,
                    fields_used = excluded.fields_used,
                    fields_max = excluded.fields_max,
                    pig_iron = excluded.pig_iron,
                    metal = excluded.metal,
                    kryptonite = excluded.kryptonite,
                    spice = excluded.spice,
                    energy_produced = excluded.energy_produced,
                    energy_used = excluded.energy_used,
                    is_active = 1,
                    updated_ts = excluded.updated_ts
                """,
                (
                    pid_sql,
                    _sql_int(state.universe_id, 0),
                    norm_c,
                    _sanitize_text(planet.name),
                    normalize_planet_type(planet),
                    _sql_int(planet.temperature, 0),
                    _sql_int(planet.fields_used, 0),
                    _sql_int(planet.fields_max, 0),
                    _sql_int(planet.resources.pig_iron, 0),
                    _sql_int(planet.resources.metal, 0),
                    _sql_int(planet.resources.kryptonite, 0),
                    _sql_int(planet.resources.spice, 0),
                    _sql_int(planet.energy.produced, 0),
                    _sql_int(planet.energy.used, 0),
                    ts_r,
                ),
            )
        if active_pids:
            ph = ", ".join("?" for _ in active_pids)
            self._conn.execute(
                f"UPDATE empire_planets_hot SET is_active = 0, updated_ts = ? WHERE is_active = 1 AND planet_id NOT IN ({ph})",
                (ts_r, *active_pids),
            )
        return touched_sectors

    def _load_discarded_colony_coords_locked(self) -> set[str]:
        row = self._conn.execute(
            "SELECT value FROM meta WHERE key = 'discarded_colony_coords' LIMIT 1"
        ).fetchone()
        if row is None or not row[0]:
            return set()
        try:
            parsed = json.loads(str(row[0]))
        except (TypeError, ValueError):
            return set()
        if not isinstance(parsed, Mapping):
            return set()
        out: set[str] = set()
        for k in parsed.keys():
            nk = normalize_coords(_raw_text(k, "").strip())
            if nk:
                out.add(nk)
        return out

    def _sync_discarded_colony_hot_locked(self, discarded_norm: set[str], now_ts: float) -> None:
        if not DEFAULT_GAME_MIGRATION_RUNNER._table_exists(self._conn, "colony_candidates_hot"):
            return
        ts_r = round(float(now_ts), 3)
        self._conn.execute(
            "UPDATE colony_candidates_hot SET is_discarded = 0, updated_ts = ? WHERE is_discarded = 1",
            (ts_r,),
        )
        if discarded_norm:
            coords_list = sorted(discarded_norm)
            for idx in range(0, len(coords_list), 200):
                chunk = coords_list[idx : idx + 200]
                ph = ", ".join("?" for _ in chunk)
                self._conn.execute(
                    f"UPDATE colony_candidates_hot SET is_discarded = 1, updated_ts = ? WHERE coords IN ({ph})",
                    (ts_r, *chunk),
                )

    def _refresh_colony_candidates_hot_locked(self, coords_iter: Iterable[str], now_ts: float) -> None:
        if not DEFAULT_GAME_MIGRATION_RUNNER._table_exists(self._conn, "colony_candidates_hot"):
            return
        norm_coords = sorted({normalize_coords(_raw_text(c, "").strip()) for c in coords_iter if normalize_coords(_raw_text(c, "").strip())})
        if not norm_coords:
            return
        ts_r = round(float(now_ts), 3)
        discarded_norm = self._load_discarded_colony_coords_locked()
        cur = self._conn.cursor()
        cur.row_factory = None
        has_slots = DEFAULT_GAME_MIGRATION_RUNNER._table_exists(self._conn, "galaxy_slots")
        has_spy = DEFAULT_GAME_MIGRATION_RUNNER._table_exists(self._conn, "spy_targets")
        has_reports = DEFAULT_GAME_MIGRATION_RUNNER._table_exists(self._conn, "spy_reports")
        has_slot_cmax = has_slots and self._has_column_locked("galaxy_slots", "colonize_max_fields")
        has_spy_cmax = has_spy and self._has_column_locked("spy_targets", "colonize_max_fields")

        merged: dict[str, list[Any]] = {}
        for idx in range(0, len(norm_coords), 200):
            chunk = norm_coords[idx : idx + 200]
            ph = ", ".join("?" for _ in chunk)
            if has_slots:
                s_cols = (
                    "coords, colonize_min_fields, colonize_max_fields, temperature, last_scanned_ts"
                    if has_slot_cmax
                    else "coords, NULL, NULL, NULL, last_scanned_ts"
                )
                for r in cur.execute(
                    f"SELECT {s_cols} FROM galaxy_slots WHERE status_color = 'free' AND coords IN ({ph})",
                    chunk,
                ).fetchall():
                    nc = normalize_coords(_raw_text(r[0], ""))
                    if not nc:
                        continue
                    merged[nc] = [
                        None,
                        _safe_optional_int(r[1]),
                        _safe_optional_int(r[2]),
                        _safe_optional_int(r[3]),
                        0.0,
                        _safe_float(r[4], 0.0),
                        0,
                        0.0,
                        "",
                    ]
            if has_spy:
                st_cmin = "colonize_min_fields" if has_spy_cmax else "NULL"
                st_cmax = "colonize_max_fields" if has_spy_cmax else "NULL"
                st_temp = "temperature" if has_spy_cmax else "NULL"
                st_cmax_or = "OR colonize_max_fields IS NOT NULL" if has_spy_cmax else ""
                for sr in cur.execute(
                    f"""
                    SELECT coords, uninhabited_fields, {st_cmin}, {st_cmax}, {st_temp},
                           last_spied_ts, last_scanned_ts, spy_count, blocked_until_ts, block_reason
                    FROM spy_targets
                    WHERE coords IN ({ph})
                      AND merged_into = '' AND is_own_planet = 0
                      AND (
                          status_color = 'free'
                          OR ((uninhabited_fields IS NOT NULL {st_cmax_or}) AND owner = '' AND is_spacia = 0)
                      )
                    """,
                    chunk,
                ).fetchall():
                    nc = normalize_coords(_raw_text(sr[0], ""))
                    if not nc:
                        continue
                    entry = merged.get(nc)
                    if entry is None:
                        entry = [None, None, None, None, 0.0, 0.0, 0, 0.0, ""]
                        merged[nc] = entry
                    uf = _safe_optional_int(sr[1])
                    if uf is not None:
                        entry[0] = uf
                    cmin = _safe_optional_int(sr[2])
                    if cmin is not None and entry[1] is None:
                        entry[1] = cmin
                    cmax = _safe_optional_int(sr[3])
                    if cmax is not None and entry[2] is None:
                        entry[2] = cmax
                    temp = _safe_optional_int(sr[4])
                    if temp is not None and entry[3] is None:
                        entry[3] = temp
                    lsp = _safe_float(sr[5], 0.0)
                    if lsp > entry[4]:
                        entry[4] = lsp
                    lsc = _safe_float(sr[6], 0.0)
                    if lsc > entry[5]:
                        entry[5] = lsc
                    scnt = _safe_int(sr[7], 0)
                    if scnt > entry[6]:
                        entry[6] = scnt
                    b_until = _safe_float(sr[8], 0.0)
                    b_rsn = _sanitize_text(sr[9] or "")
                    b_low = b_rsn.lower()
                    if "can't be attacked" not in b_low and "cannot be attacked" not in b_low and b_until > entry[7]:
                        entry[7] = b_until
                        entry[8] = b_rsn
            if has_reports:
                for rr in cur.execute(
                    f"""
                    SELECT target_coords, MAX(uninhabited_fields), MAX(ts), COUNT(*)
                    FROM spy_reports
                    WHERE target_coords IN ({ph})
                      AND uninhabited_fields IS NOT NULL AND owner = '' AND is_spacia = 0
                    GROUP BY target_coords
                    """,
                    chunk,
                ).fetchall():
                    nc = normalize_coords(_raw_text(rr[0], ""))
                    if not nc:
                        continue
                    entry = merged.get(nc)
                    if entry is None:
                        entry = [None, None, None, None, 0.0, 0.0, 0, 0.0, ""]
                        merged[nc] = entry
                    uf = _safe_optional_int(rr[1])
                    if uf is not None:
                        entry[0] = uf
                    rts = _safe_float(rr[2], 0.0)
                    if rts > entry[4]:
                        entry[4] = rts
                    rcnt = _safe_int(rr[3], 0)
                    if rcnt > entry[6]:
                        entry[6] = rcnt

        upsert_params: list[tuple[Any, ...]] = []
        inactive_coords: list[str] = []
        for nc in norm_coords:
            entry = merged.get(nc)
            if entry is None:
                inactive_coords.append(nc)
                continue
            parsed_c = self._parse_norm_gsp_fast(nc)
            gal_i = parsed_c[1] if parsed_c is not None else 0
            sys_i = parsed_c[2] if parsed_c is not None else 0
            pos_i = parsed_c[3] if parsed_c is not None else 0
            uf, cmin, cmax, temp, lsp, lsc, scnt, b_until, b_rsn = entry
            uf_pos = uf if (uf is not None and uf > 0) else None
            cmin_pos = cmin if (cmin is not None and cmin > 0) else None
            cmax_pos = cmax if (cmax is not None and cmax > 0) else None
            eff_cmax = cmin_pos if (cmin_pos is not None and cmax_pos is not None and cmin_pos > cmax_pos) else cmax_pos
            max_est = uf_pos if uf_pos is not None else (eff_cmax if eff_cmax is not None else 0)
            is_probed = 1 if (uf_pos is not None or eff_cmax is not None) else 0
            is_disc = 1 if nc in discarded_norm else 0
            upsert_params.append(
                (
                    nc,
                    gal_i,
                    sys_i,
                    pos_i,
                    uf,
                    cmin,
                    cmax,
                    max_est,
                    temp,
                    is_probed,
                    lsp,
                    lsc,
                    scnt,
                    b_until,
                    b_rsn,
                    is_disc,
                    1,
                    ts_r,
                )
            )
        if upsert_params:
            cur.executemany(
                """
                INSERT INTO colony_candidates_hot(
                    coords, galaxy, system, position,
                    uninhabited_fields, colonize_min_fields, colonize_max_fields, max_fields_est,
                    temperature, is_probed, last_spied_ts, last_scanned_ts, spy_count,
                    blocked_until_ts, block_reason, is_discarded, is_active, updated_ts
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(coords) DO UPDATE SET
                    galaxy = excluded.galaxy,
                    system = excluded.system,
                    position = excluded.position,
                    uninhabited_fields = excluded.uninhabited_fields,
                    colonize_min_fields = excluded.colonize_min_fields,
                    colonize_max_fields = excluded.colonize_max_fields,
                    max_fields_est = excluded.max_fields_est,
                    temperature = excluded.temperature,
                    is_probed = excluded.is_probed,
                    last_spied_ts = excluded.last_spied_ts,
                    last_scanned_ts = excluded.last_scanned_ts,
                    spy_count = excluded.spy_count,
                    blocked_until_ts = excluded.blocked_until_ts,
                    block_reason = excluded.block_reason,
                    is_discarded = excluded.is_discarded,
                    is_active = 1,
                    updated_ts = excluded.updated_ts
                """,
                upsert_params,
            )
        if inactive_coords:
            for idx in range(0, len(inactive_coords), 200):
                chunk = inactive_coords[idx : idx + 200]
                ph = ", ".join("?" for _ in chunk)
                cur.execute(
                    f"UPDATE colony_candidates_hot SET is_active = 0, updated_ts = ? WHERE coords IN ({ph}) AND is_active = 1",
                    (ts_r, *chunk),
                )

    def _load_own_coords_locked(self) -> set[str]:
        own_coords: set[str] = set()
        cur = self._conn.cursor()
        cur.row_factory = None
        if DEFAULT_GAME_MIGRATION_RUNNER._table_exists(self._conn, "empire_planets_hot"):
            ep_rows = cur.execute("SELECT DISTINCT coords FROM empire_planets_hot WHERE coords != ''").fetchall()
            for r in ep_rows:
                parsed = self._parse_norm_gsp_fast(r[0])
                if parsed is not None:
                    own_coords.add(parsed[0])
            if not ep_rows and DEFAULT_GAME_MIGRATION_RUNNER._table_exists(self._conn, "planet_snapshots"):
                for r in cur.execute("SELECT DISTINCT coords FROM planet_snapshots WHERE coords != ''").fetchall():
                    parsed = self._parse_norm_gsp_fast(r[0])
                    if parsed is not None:
                        own_coords.add(parsed[0])
        elif DEFAULT_GAME_MIGRATION_RUNNER._table_exists(self._conn, "planet_snapshots"):
            for r in cur.execute("SELECT DISTINCT coords FROM planet_snapshots WHERE coords != ''").fetchall():
                parsed = self._parse_norm_gsp_fast(r[0])
                if parsed is not None:
                    own_coords.add(parsed[0])
        if DEFAULT_GAME_MIGRATION_RUNNER._table_exists(self._conn, "spy_targets") and self._has_column_locked("spy_targets", "is_own_planet"):
            for r in cur.execute("SELECT coords FROM spy_targets WHERE is_own_planet = 1").fetchall():
                parsed = self._parse_norm_gsp_fast(r[0])
                if parsed is not None:
                    own_coords.add(parsed[0])
        return own_coords

    def _refresh_galaxy_sectors_hot_locked(self, sectors: Iterable[tuple[int, int]], now_ts: float) -> None:
        if not DEFAULT_GAME_MIGRATION_RUNNER._table_exists(self._conn, "galaxy_sector_hot"):
            return
        valid_sectors = sorted({(int(g), int(s)) for g, s in sectors if 1 <= int(g) <= 14 and 1 <= int(s) <= 400})
        if not valid_sectors:
            return
        ts_r = round(float(now_ts), 3)
        own_coords = self._load_own_coords_locked()
        cur = self._conn.cursor()
        cur.row_factory = None
        has_scans = DEFAULT_GAME_MIGRATION_RUNNER._table_exists(self._conn, "galaxy_scans")
        has_scan_hist = DEFAULT_GAME_MIGRATION_RUNNER._table_exists(self._conn, "galaxy_scan_history")
        has_slots = DEFAULT_GAME_MIGRATION_RUNNER._table_exists(self._conn, "galaxy_slots")
        has_spy = DEFAULT_GAME_MIGRATION_RUNNER._table_exists(self._conn, "spy_targets")
        has_slot_cmax = has_slots and self._has_column_locked("galaxy_slots", "colonize_max_fields")
        has_spy_cmax = has_spy and self._has_column_locked("spy_targets", "colonize_max_fields")

        sec_params: list[tuple[Any, ...]] = []
        for gal_i, sys_i in valid_sectors:
            last_ts = 0.0
            scan_cnt = 0
            sm_occ = 0
            sm_tgt = 0
            sm_free = 0
            hist_deb = 0
            if has_scans:
                sr = cur.execute(
                    "SELECT last_scanned_ts, occupied_count, target_count, free_count FROM galaxy_scans WHERE galaxy = ? AND system = ?",
                    (gal_i, sys_i),
                ).fetchone()
                if sr is not None:
                    last_ts = _safe_float(sr[0], 0.0)
                    scan_cnt = 1 if last_ts > 0.0 else 0
                    sm_occ = max(0, _safe_int(sr[1], 0))
                    sm_tgt = max(0, _safe_int(sr[2], 0))
                    sm_free = max(0, _safe_int(sr[3], 0))
            if has_scan_hist:
                hr = cur.execute(
                    "SELECT COUNT(*), MAX(ts), MAX(debris_count) FROM galaxy_scan_history WHERE galaxy = ? AND system = ?",
                    (gal_i, sys_i),
                ).fetchone()
                if hr is not None and _safe_int(hr[0], 0) > 0:
                    scan_cnt = max(scan_cnt, max(0, _safe_int(hr[0], 0)))
                    last_ts = max(last_ts, _safe_float(hr[1], 0.0))
                    hist_deb = max(hist_deb, max(0, _safe_int(hr[2], 0)))

            occ_set: set[str] = set()
            debris_set: set[str] = set()
            inact_set: set[str] = set()
            spacia_set: set[str] = set()
            free_set: set[str] = set()
            prime_set: set[str] = set()
            owner_display: dict[str, str] = {}
            owner_coords: dict[str, set[str]] = {}
            coord_owner: dict[str, str] = {}

            if has_slots:
                s_cmax = "colonize_max_fields" if has_slot_cmax else "NULL"
                for r in cur.execute(
                    f"""
                    SELECT coords, owner, status_color, is_inactive, is_spacia, has_debris, {s_cmax}
                    FROM galaxy_slots
                    WHERE galaxy = ? AND system = ?
                    """,
                    (gal_i, sys_i),
                ).fetchall():
                    parsed = self._parse_norm_gsp_fast(r[0])
                    if parsed is None:
                        continue
                    nc, _, _, pos_i = parsed
                    scolor = _sanitize_text(r[2] or "free").strip().lower() or "free"
                    is_sp = bool(r[4])
                    owner_s = _sanitize_text(r[1] or "").strip()
                    if scolor != "free":
                        occ_set.add(nc)
                    if bool(r[5]):
                        debris_set.add(nc)
                    if nc in own_coords:
                        continue
                    if is_sp or owner_s.lower() == "spacia":
                        spacia_set.add(nc)
                    elif scolor == "yellow" or (bool(r[3]) and scolor != "free"):
                        inact_set.add(nc)
                    if owner_s and scolor != "free" and not is_sp and owner_s.lower() != "spacia":
                        okey = owner_s.lower()
                        owner_display.setdefault(okey, owner_s)
                        owner_coords.setdefault(okey, set()).add(nc)
                        coord_owner[nc] = owner_s
                    if scolor == "free":
                        free_set.add(nc)
                        cmax = _safe_int(r[6], 0)
                        if cmax >= 300 or (cmax == 0 and 7 <= pos_i <= 12):
                            prime_set.add(nc)

            if has_spy:
                lo_c = f"{gal_i:02d}:{sys_i:03d}:00"
                hi_c = f"{gal_i:02d}:{sys_i:03d}:99"
                st_cmax = "colonize_max_fields" if has_spy_cmax else "NULL"
                for r in cur.execute(
                    f"""
                    SELECT coords, owner, status_color, is_inactive, is_spacia,
                           has_debris, uninhabited_fields, {st_cmax}, is_own_planet, merged_into
                    FROM spy_targets
                    WHERE coords >= ? AND coords <= ? AND merged_into = ''
                    """,
                    (lo_c, hi_c),
                ).fetchall():
                    if bool(r[8]) or str(r[9] or "").strip():
                        continue
                    parsed = self._parse_norm_gsp_fast(r[0])
                    if parsed is None:
                        continue
                    nc, g_r, s_r, pos_i = parsed
                    if g_r != gal_i or s_r != sys_i or nc in own_coords:
                        continue
                    scolor = _sanitize_text(r[2] or "yellow").strip().lower() or "yellow"
                    is_sp = bool(r[4])
                    owner_s = _sanitize_text(r[1] or "").strip()
                    if scolor != "free":
                        occ_set.add(nc)
                    if bool(r[5]):
                        debris_set.add(nc)
                    if is_sp or owner_s.lower() == "spacia":
                        spacia_set.add(nc)
                    elif scolor == "yellow" or (bool(r[3]) and scolor != "free"):
                        inact_set.add(nc)
                    if owner_s and scolor != "free" and not is_sp and owner_s.lower() != "spacia":
                        okey = owner_s.lower()
                        owner_display[okey] = owner_s
                        owner_coords.setdefault(okey, set()).add(nc)
                        coord_owner[nc] = owner_s
                    if scolor == "free":
                        free_set.add(nc)
                        uf = _safe_int(r[6], 0)
                        cmax = _safe_int(r[7], 0)
                        if uf >= 300 or cmax >= 300 or (uf == 0 and cmax == 0 and 7 <= pos_i <= 12):
                            prime_set.add(nc)

            occ_cnt = max(sm_occ, len(occ_set))
            inact_cnt = len(inact_set)
            spacia_cnt = len(spacia_set)
            tgt_cnt = max(sm_tgt, inact_cnt + spacia_cnt)
            deb_cnt = len(debris_set)
            free_cnt = max(sm_free, len(free_set))
            prime_cnt = len(prime_set)

            owners_list: list[str] = []
            owner_counts: dict[str, int] = {}
            for okey in sorted(owner_coords.keys()):
                pcnt = len(owner_coords[okey])
                if pcnt <= 0:
                    continue
                disp = owner_display.get(okey, okey)
                owners_list.append(disp)
                owner_counts[disp] = pcnt
            co_norm = {
                nc: owner_display.get(ov.lower(), ov)
                for nc, ov in sorted(coord_owner.items())
                if nc not in spacia_set
            }
            sec_params.append(
                (
                    gal_i,
                    sys_i,
                    round(last_ts, 3),
                    scan_cnt,
                    occ_cnt,
                    tgt_cnt,
                    inact_cnt,
                    spacia_cnt,
                    deb_cnt,
                    free_cnt,
                    prime_cnt,
                    hist_deb,
                    json.dumps(owners_list, separators=(",", ":"), ensure_ascii=False),
                    json.dumps(owner_counts, separators=(",", ":"), ensure_ascii=False),
                    json.dumps(co_norm, separators=(",", ":"), ensure_ascii=False),
                    json.dumps(sorted(spacia_set), separators=(",", ":"), ensure_ascii=False),
                    ts_r,
                )
            )
        if sec_params:
            cur.executemany(
                """
                INSERT INTO galaxy_sector_hot(
                    galaxy, system, last_scanned_ts, scan_count,
                    occupied_count, target_count, inactive_count, spacia_count,
                    debris_count, free_count, prime_colony_free_count, hist_debris_max,
                    owners_json, owner_planet_counts_json, coord_owners_json, spacia_coords_json,
                    updated_ts
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(galaxy, system) DO UPDATE SET
                    last_scanned_ts = excluded.last_scanned_ts,
                    scan_count = excluded.scan_count,
                    occupied_count = excluded.occupied_count,
                    target_count = excluded.target_count,
                    inactive_count = excluded.inactive_count,
                    spacia_count = excluded.spacia_count,
                    debris_count = excluded.debris_count,
                    free_count = excluded.free_count,
                    prime_colony_free_count = excluded.prime_colony_free_count,
                    hist_debris_max = excluded.hist_debris_max,
                    owners_json = excluded.owners_json,
                    owner_planet_counts_json = excluded.owner_planet_counts_json,
                    coord_owners_json = excluded.coord_owners_json,
                    spacia_coords_json = excluded.spacia_coords_json,
                    updated_ts = excluded.updated_ts
                """,
                sec_params,
            )

    def _ensure_hot_tables_synced_locked(self, *, check_sectors: bool = False, check_colonies: bool = False) -> bool:
        if check_sectors:
            if not DEFAULT_GAME_MIGRATION_RUNNER._table_exists(self._conn, "galaxy_sector_hot"):
                return False
            hot_cnt = int(self._conn.execute("SELECT COUNT(*) FROM galaxy_sector_hot").fetchone()[0])
            scan_cnt = (
                int(self._conn.execute("SELECT COUNT(*) FROM galaxy_scans").fetchone()[0])
                if DEFAULT_GAME_MIGRATION_RUNNER._table_exists(self._conn, "galaxy_scans")
                else 0
            )
            need_rebuild = hot_cnt < scan_cnt
            if not need_rebuild and hot_cnt == 0:
                if (
                    (DEFAULT_GAME_MIGRATION_RUNNER._table_exists(self._conn, "galaxy_slots")
                     and self._conn.execute("SELECT 1 FROM galaxy_slots LIMIT 1").fetchone() is not None)
                    or (DEFAULT_GAME_MIGRATION_RUNNER._table_exists(self._conn, "spy_targets")
                        and self._conn.execute("SELECT 1 FROM spy_targets LIMIT 1").fetchone() is not None)
                ):
                    need_rebuild = True
            if need_rebuild:
                backfill_v012_hot_tables(self._conn, time.time())
                self._conn.commit()
            return True
        if check_colonies:
            if not DEFAULT_GAME_MIGRATION_RUNNER._table_exists(self._conn, "colony_candidates_hot"):
                return False
            has_any_hot = self._conn.execute("SELECT 1 FROM colony_candidates_hot LIMIT 1").fetchone() is not None
            if not has_any_hot:
                need_rebuild = False
                if (
                    (DEFAULT_GAME_MIGRATION_RUNNER._table_exists(self._conn, "galaxy_slots")
                     and self._conn.execute("SELECT 1 FROM galaxy_slots WHERE status_color = 'free' LIMIT 1").fetchone() is not None)
                    or (DEFAULT_GAME_MIGRATION_RUNNER._table_exists(self._conn, "spy_targets")
                        and self._conn.execute("SELECT 1 FROM spy_targets WHERE status_color = 'free' OR uninhabited_fields IS NOT NULL LIMIT 1").fetchone() is not None)
                    or (DEFAULT_GAME_MIGRATION_RUNNER._table_exists(self._conn, "spy_reports")
                        and self._conn.execute("SELECT 1 FROM spy_reports WHERE uninhabited_fields IS NOT NULL AND owner = '' AND is_spacia = 0 LIMIT 1").fetchone() is not None)
                ):
                    need_rebuild = True
                if need_rebuild:
                    backfill_v012_hot_tables(self._conn, time.time())
                    self._conn.commit()
            return True
        return False

    def record_state_snapshot(
        self,
        state: GameState,
        *,
        planet_min_interval_seconds: float = 120.0,
        rank_min_interval_seconds: float = 300.0,
    ) -> list[dict[str, Any]]:
        """Persist rank, colony, economy-rate, and tutorial snapshots atomically; return rank history."""
        from spaceinvasion.combat.ranking import estimate_account_points, estimate_hourly_economy

        est = estimate_account_points(state)
        econ = estimate_hourly_economy(state)
        now_ts = _safe_float(state.timestamp, 0.0)
        if now_ts <= 0:
            now_ts = time.time()

        with self._lock:
            # 1. Deduplicated rank snapshot
            last_rank = self._conn.execute(
                """
                SELECT ts, server_points, server_rank, estimated_points
                FROM rank_snapshots ORDER BY id DESC LIMIT 1
                """
            ).fetchone()
            append_rank = False
            if last_rank is None:
                append_rank = True
            else:
                last_rank_ts = _safe_optional_float(last_rank["ts"])
                last_est_pts = _safe_optional_float(last_rank["estimated_points"])
                if last_rank_ts is None or last_est_pts is None:
                    append_rank = True
                else:
                    elapsed = abs(now_ts - last_rank_ts)
                    changed_server = (
                        (state.account_points is not None and _sql_optional_int(state.account_points) != last_rank["server_points"])
                        or (state.account_rank is not None and _sql_optional_int(state.account_rank) != last_rank["server_rank"])
                    )
                    est_pts_now = _safe_float(est.get("estimated_points"), 0.0)
                    changed_est = int(round(est_pts_now)) != int(
                        round(last_est_pts)
                    )
                    if changed_server or changed_est or elapsed >= rank_min_interval_seconds:
                        append_rank = True

            if append_rank:
                self._conn.execute(
                    """
                    INSERT INTO rank_snapshots(
                        ts, universe_id, nickname, server_points, server_rank,
                        estimated_points, pts_per_hr, spice_value, planets
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        round(now_ts, 3),
                        _sql_int(state.universe_id, 0),
                        _sanitize_text(state.nickname),
                        _sql_optional_int(state.account_points),
                        _sql_optional_int(state.account_rank),
                        _safe_float(est.get("estimated_points"), 0.0),
                        _safe_float(econ.get("pts_per_hr_steady"), 0.0),
                        _safe_float(est.get("spice_value"), 0.0),
                        _sql_int(len(state.planets), 0),
                    ),
                )

            # 2. Colony snapshots + economy rate cache
            res_json = _json_dumps(state.researches or {})
            for planet in state.planets:
                pid_sql = _sql_int(planet.planet_id, 0)
                bld_json = _json_dumps(planet.buildings or {})
                shp_json = _json_dumps(planet.ships or {})
                def_json = _json_dumps(planet.defense or {})
                rates_json = _json_dumps(getattr(planet, "economy_rates", None) or {})

                if getattr(planet, "economy_rates", None):
                    self._conn.execute(
                        """
                        INSERT INTO economy_rates_cache(planet_id, coords, rates_json, updated_ts)
                        VALUES(?, ?, ?, ?)
                        ON CONFLICT(planet_id) DO UPDATE SET
                            coords = excluded.coords,
                            rates_json = excluded.rates_json,
                            updated_ts = excluded.updated_ts
                        """,
                        (pid_sql, _sanitize_text(planet.coords), rates_json, now_ts),
                    )

                last_p = self._conn.execute(
                    """
                    SELECT ts, buildings_json, ships_json, economy_rates_json
                    FROM planet_snapshots WHERE planet_id = ? ORDER BY id DESC LIMIT 1
                    """,
                    (pid_sql,),
                ).fetchone()
                should_snap = False
                if last_p is None:
                    should_snap = True
                else:
                    last_p_ts = _safe_optional_float(last_p["ts"])
                    if last_p_ts is None:
                        should_snap = True
                    else:
                        elapsed_p = abs(now_ts - last_p_ts)
                        if (
                            elapsed_p >= planet_min_interval_seconds
                            or last_p["buildings_json"] != bld_json
                            or last_p["ships_json"] != shp_json
                            or last_p["economy_rates_json"] != rates_json
                        ):
                            should_snap = True

                if should_snap:
                    self._conn.execute(
                        """
                        INSERT INTO planet_snapshots(
                            ts, universe_id, planet_id, coords, name, planet_type,
                            temperature, fields_used, fields_max,
                            pig_iron, metal, kryptonite, spice,
                            energy_produced, energy_used,
                            buildings_json, ships_json, defense_json,
                            economy_rates_json, researches_json
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            round(now_ts, 3),
                            _sql_int(state.universe_id, 0),
                            pid_sql,
                            _sanitize_text(planet.coords),
                            _sanitize_text(planet.name),
                            normalize_planet_type(planet),
                            _sql_int(planet.temperature, 0),
                            _sql_int(planet.fields_used, 0),
                            _sql_int(planet.fields_max, 0),
                            _sql_int(planet.resources.pig_iron, 0),
                            _sql_int(planet.resources.metal, 0),
                            _sql_int(planet.resources.kryptonite, 0),
                            _sql_int(planet.resources.spice, 0),
                            _sql_int(planet.energy.produced, 0),
                            _sql_int(planet.energy.used, 0),
                            bld_json,
                            shp_json,
                            def_json,
                            rates_json,
                            res_json,
                        ),
                    )

            # 3. Tutorial step progress tracking
            if state.tutorial is not None:
                tut = state.tutorial
                tut_step_sql = _sql_int(tut.step, 1)
                tut_total_sql = _sql_int(tut.total_steps, 1)
                tasks_payload = [
                    {
                        "description": t.description,
                        "category": t.category,
                        "current": game_data.cap_json_int(_safe_int(t.current, 0)),
                        "target": game_data.cap_json_int(_safe_int(t.target, 0)),
                        "completed": bool(t.completed),
                    }
                    for t in tut.tasks
                ]
                self._conn.execute(
                    """
                    UPDATE tutorial_progress
                    SET completed_ts = COALESCE(completed_ts, ?)
                    WHERE step < ? AND completed_ts IS NULL
                    """,
                    (now_ts, tut_step_sql),
                )
                self._conn.execute(
                    """
                    INSERT INTO tutorial_progress(
                        step, total_steps, tasks_json, reward_text,
                        can_complete, first_seen_ts, last_seen_ts, completed_ts
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, NULL)
                    ON CONFLICT(step) DO UPDATE SET
                        total_steps = excluded.total_steps,
                        tasks_json = excluded.tasks_json,
                        reward_text = excluded.reward_text,
                        can_complete = excluded.can_complete,
                        last_seen_ts = excluded.last_seen_ts
                    """,
                    (
                        tut_step_sql,
                        tut_total_sql,
                        _json_dumps(tasks_payload),
                        _sanitize_text(tut.reward_text),
                        1 if tut.can_complete else 0,
                        now_ts,
                        now_ts,
                    ),
                )
            else:
                self._conn.execute(
                    """
                    UPDATE tutorial_progress
                    SET completed_ts = COALESCE(completed_ts, ?)
                    WHERE completed_ts IS NULL
                    """,
                    (now_ts,),
                )

            # 4. Mark player's own planet coordinates in spy_targets & empire_planets_hot (non-destructive; never deletes rows)
            own_norm_coords = [
                normalize_coords(_raw_text(p.coords, "").strip())
                for p in state.planets
                if normalize_coords(_raw_text(p.coords, "").strip())
            ]
            if own_norm_coords:
                ph_own = ", ".join("?" for _ in own_norm_coords)
                self._conn.execute(
                    f"UPDATE spy_targets SET is_own_planet = 1 WHERE coords IN ({ph_own})",
                    own_norm_coords,
                )
            touched_own_sectors = self._upsert_empire_planets_hot_locked(state, now_ts)
            if own_norm_coords:
                self._refresh_colony_candidates_hot_locked(own_norm_coords, now_ts)
            if touched_own_sectors:
                self._refresh_galaxy_sectors_hot_locked(touched_own_sectors, now_ts)

            # 5. Full latest GameState snapshot (queues, offers, alerts, unread badges)
            self._save_latest_game_state_locked(state, now_ts)

            self._conn.commit()
        return self.get_rank_samples()

    def _save_meta_json_locked(self, key: str, payload: Any, now_ts: float) -> None:
        clean_key = _sanitize_text(key).strip()
        if not clean_key or clean_key in PROTECTED_META_KEYS:
            return
        self._conn.execute(
            """
            INSERT INTO meta(key, value, updated_ts) VALUES(?, ?, ?)
            ON CONFLICT(key) DO UPDATE SET
                value = excluded.value,
                updated_ts = excluded.updated_ts
            """,
            (clean_key, _json_dumps(payload), round(_safe_float(now_ts, time.time()), 3)),
        )
        if clean_key == "discarded_colony_coords":
            disc_set: set[str] = set()
            if isinstance(payload, Mapping):
                for k in payload.keys():
                    nk = normalize_coords(_raw_text(k, "").strip())
                    if nk:
                        disc_set.add(nk)
            self._sync_discarded_colony_hot_locked(disc_set, now_ts)

    def save_meta_json(self, key: str, payload: Any, *, ts: Optional[float] = None) -> None:
        now_ts = _safe_float(ts, time.time()) if ts is not None else time.time()
        with self._lock:
            self._save_meta_json_locked(key, payload, now_ts)
            self._conn.commit()

    def load_meta_json(self, key: str) -> Any:
        clean_key = _sanitize_text(key).strip()
        if not clean_key or clean_key in PROTECTED_META_KEYS:
            return None
        with self._lock:
            row = self._conn.execute(
                "SELECT value FROM meta WHERE key = ? LIMIT 1",
                (clean_key,),
            ).fetchone()
        if row is None or not row["value"]:
            return None
        try:
            parsed = json.loads(row["value"])
        except (TypeError, ValueError):
            return None
        return _sanitize_obj(parsed)

    def _save_latest_game_state_locked(
        self,
        state: GameState | Mapping[str, Any],
        now_ts: float,
    ) -> None:
        if is_dataclass(state) and not isinstance(state, type):
            raw = asdict(state)
        elif isinstance(state, Mapping):
            raw = dict(state)
        else:
            return
        raw.pop("raid_stats", None)
        raw.pop("raid_targets", None)
        self._save_meta_json_locked("latest_game_state", raw, now_ts)

    def save_latest_game_state(
        self,
        state: GameState | Mapping[str, Any],
        *,
        ts: Optional[float] = None,
    ) -> None:
        now_ts = _safe_float(ts, time.time()) if ts is not None else time.time()
        with self._lock:
            self._save_latest_game_state_locked(state, now_ts)
            self._conn.commit()

    def load_latest_game_state(self) -> Optional[GameState]:
        raw = self.load_meta_json("latest_game_state")
        if not isinstance(raw, Mapping):
            return None
        try:
            return game_state_from_dict(dict(raw))
        except Exception:
            return None

    def save_runtime_state(
        self,
        runtime_state: Mapping[str, Any],
        *,
        ts: Optional[float] = None,
    ) -> None:
        if not isinstance(runtime_state, Mapping):
            return
        self.save_meta_json("bridge_runtime_state", dict(runtime_state), ts=ts)

    def load_runtime_state(self) -> dict[str, Any]:
        raw = self.load_meta_json("bridge_runtime_state")
        return dict(raw) if isinstance(raw, Mapping) else {}

    def save_strategy_config_snapshot(
        self,
        config_payload: Mapping[str, Any],
        *,
        ts: Optional[float] = None,
        source: str = "sqlite",
    ) -> None:
        if not isinstance(config_payload, Mapping):
            return
        now_ts = round(_safe_float(ts, time.time()) if ts is not None else time.time(), 3)
        cfg_dict = dict(config_payload)
        cfg_json = _json_dumps(cfg_dict)
        with self._lock:
            if DEFAULT_GAME_MIGRATION_RUNNER._table_exists(self._conn, "strategy_config_store"):
                self._conn.execute(
                    """
                    INSERT INTO strategy_config_store(id, config_json, updated_ts, source)
                    VALUES (1, ?, ?, ?)
                    ON CONFLICT(id) DO UPDATE SET
                        config_json = excluded.config_json,
                        updated_ts = excluded.updated_ts,
                        source = excluded.source
                    """,
                    (cfg_json, now_ts, _sanitize_text(source or "sqlite")[:32]),
                )
            self._save_meta_json_locked("strategy_config", cfg_dict, now_ts)
            last_cfg = self._conn.execute(
                "SELECT config_json FROM config_history ORDER BY id DESC LIMIT 1"
            ).fetchone()
            if last_cfg is None or last_cfg["config_json"] != cfg_json:
                self._conn.execute(
                    "INSERT INTO config_history(ts, config_json) VALUES (?, ?)",
                    (now_ts, cfg_json),
                )
            self._conn.commit()

    def load_strategy_config_snapshot(self) -> Optional[dict[str, Any]]:
        with self._lock:
            if DEFAULT_GAME_MIGRATION_RUNNER._table_exists(self._conn, "strategy_config_store"):
                row = self._conn.execute(
                    "SELECT config_json FROM strategy_config_store WHERE id = 1"
                ).fetchone()
                if row is not None:
                    raw_txt = _raw_text(row["config_json"], "").strip()
                    if raw_txt:
                        try:
                            parsed = json.loads(raw_txt)
                            if isinstance(parsed, Mapping):
                                return _sanitize_obj(dict(parsed))
                        except ValueError:
                            pass
        raw = self.load_meta_json("strategy_config")
        return dict(raw) if isinstance(raw, Mapping) else None

    def create_or_bump_notification(
        self,
        *,
        title: str,
        message: str,
        help_prompt: str = "",
        severity: str = "warning",
        category: str = "action_validation",
        action_kind: str = "",
        planet_coords: str = "",
        target_name: str = "",
        dedupe_key: str = "",
        details: Optional[Mapping[str, Any]] = None,
        ts: Optional[float] = None,
    ) -> Optional[dict[str, Any]]:
        """Create an unread persistent UI notification or bump occurrence_count if an identical unread key exists."""
        now_ts = round(_safe_float(ts, time.time()) if ts is not None else time.time(), 3)
        sev_s = _sanitize_text(severity or "warning").lower()
        if sev_s not in ("error", "warning", "info"):
            sev_s = "warning"
        cat_s = _sanitize_text(category or "action_validation")[:48]
        title_s = _sanitize_text(title or "Action Needs Review")[:180]
        msg_s = _sanitize_text(message or "")[:1000]
        help_s = _sanitize_text(help_prompt or "")[:1000]
        kind_s = _sanitize_text(action_kind or "").upper()[:32]
        pcoords_s = _sanitize_text(planet_coords or "")[:32]
        tname_s = _sanitize_text(target_name or "")[:120]
        dkey_s = _sanitize_text(
            dedupe_key or f"{cat_s}:{kind_s}:{pcoords_s}:{tname_s}:{title_s}"
        )[:180]
        det_json = _json_dumps(_sanitize_obj(dict(details)) if isinstance(details, Mapping) else {})

        with self._lock:
            if not DEFAULT_GAME_MIGRATION_RUNNER._table_exists(self._conn, "ui_notifications"):
                return None
            existing = self._conn.execute(
                """
                SELECT id, occurrence_count FROM ui_notifications
                WHERE dedupe_key = ? AND status = 'unread'
                ORDER BY id DESC LIMIT 1
                """,
                (dkey_s,),
            ).fetchone()
            if existing is not None:
                nid = int(existing["id"])
                new_cnt = max(1, _sql_int(existing["occurrence_count"], 1)) + 1
                self._conn.execute(
                    """
                    UPDATE ui_notifications
                    SET updated_ts = ?,
                        severity = ?,
                        title = ?,
                        message = ?,
                        help_prompt = ?,
                        occurrence_count = ?,
                        details_json = ?
                    WHERE id = ?
                    """,
                    (now_ts, sev_s, title_s, msg_s, help_s, new_cnt, det_json, nid),
                )
            else:
                cur = self._conn.execute(
                    """
                    INSERT INTO ui_notifications(
                        created_ts, updated_ts, severity, category, title, message, help_prompt,
                        action_kind, planet_coords, target_name, dedupe_key, occurrence_count,
                        status, acknowledged_ts, details_json
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, 'unread', NULL, ?)
                    """,
                    (
                        now_ts,
                        now_ts,
                        sev_s,
                        cat_s,
                        title_s,
                        msg_s,
                        help_s,
                        kind_s,
                        pcoords_s,
                        tname_s,
                        dkey_s,
                        det_json,
                    ),
                )
                nid = int(cur.lastrowid or 0)
            self._conn.commit()
            row = self._conn.execute("SELECT * FROM ui_notifications WHERE id = ?", (nid,)).fetchone()
        return self._row_to_notification(row) if row is not None else None

    @staticmethod
    def format_notification_code(notification_id: Any) -> str:
        nid = max(0, _sql_int(notification_id, 0))
        return f"NTF-{nid:04d}" if nid > 0 else "NTF-0000"

    @staticmethod
    def parse_notification_id(raw: Any) -> Optional[int]:
        if isinstance(raw, bool) or raw is None:
            return None
        if isinstance(raw, int):
            return raw if 0 < raw <= 2**62 else None
        txt = _raw_text(raw, "").strip()
        if not txt:
            return None
        m = re.fullmatch(
            r"#?(?:(?:ntf|notif|notification)[-_:\s#]*)?0*(\d{1,18})",
            txt,
            re.IGNORECASE | re.ASCII,
        )
        if not m:
            return None
        val = int(m.group(1))
        return val if 0 < val <= 2**62 else None

    @staticmethod
    def _row_to_notification(r: sqlite3.Row) -> dict[str, Any]:
        try:
            det_obj = json.loads(_raw_text(r["details_json"], "{}"))
        except ValueError:
            det_obj = {}
        ack_val = _safe_optional_float(r["acknowledged_ts"])
        nid_val = _sql_int(r["id"], 0)
        return _sanitize_obj(
            {
                "id": nid_val,
                "notification_code": GameDatabase.format_notification_code(nid_val),
                "created_ts": _safe_float(r["created_ts"], 0.0),
                "updated_ts": _safe_float(r["updated_ts"], 0.0),
                "severity": _sanitize_text(_raw_text(r["severity"], "warning")),
                "category": _sanitize_text(_raw_text(r["category"], "action_validation")),
                "title": _sanitize_text(_raw_text(r["title"], "")),
                "message": _sanitize_text(_raw_text(r["message"], "")),
                "help_prompt": _sanitize_text(_raw_text(r["help_prompt"], "")),
                "action_kind": _sanitize_text(_raw_text(r["action_kind"], "")),
                "planet_coords": _sanitize_text(_raw_text(r["planet_coords"], "")),
                "target_name": _sanitize_text(_raw_text(r["target_name"], "")),
                "dedupe_key": _sanitize_text(_raw_text(r["dedupe_key"], "")),
                "occurrence_count": max(1, _sql_int(r["occurrence_count"], 1)),
                "status": _sanitize_text(_raw_text(r["status"], "unread")),
                "acknowledged_ts": ack_val,
                "details": det_obj if isinstance(det_obj, Mapping) else {},
            }
        )

    def get_notification(self, notification_id: Any) -> Optional[dict[str, Any]]:
        nid = self.parse_notification_id(notification_id)
        if nid is None or nid <= 0:
            return None
        with self._lock:
            if not DEFAULT_GAME_MIGRATION_RUNNER._table_exists(self._conn, "ui_notifications"):
                return None
            row = self._conn.execute(
                "SELECT * FROM ui_notifications WHERE id = ?",
                (nid,),
            ).fetchone()
        return self._row_to_notification(row) if row is not None else None

    def get_notifications(
        self,
        *,
        status: Optional[str] = None,
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        lim = max(1, min(200, _sql_int(limit, 50)))
        st_norm = _sanitize_text(status or "").strip().lower()
        with self._lock:
            if not DEFAULT_GAME_MIGRATION_RUNNER._table_exists(self._conn, "ui_notifications"):
                return []
            if st_norm in ("unread", "read", "dismissed"):
                rows = self._conn.execute(
                    "SELECT * FROM ui_notifications WHERE status = ? ORDER BY updated_ts DESC, id DESC LIMIT ?",
                    (st_norm, lim),
                ).fetchall()
            elif st_norm == "active":
                rows = self._conn.execute(
                    "SELECT * FROM ui_notifications WHERE status != 'dismissed' ORDER BY updated_ts DESC, id DESC LIMIT ?",
                    (lim,),
                ).fetchall()
            else:
                rows = self._conn.execute(
                    "SELECT * FROM ui_notifications ORDER BY updated_ts DESC, id DESC LIMIT ?",
                    (lim,),
                ).fetchall()
        return [self._row_to_notification(r) for r in rows]

    def unread_notification_count(self) -> int:
        with self._lock:
            if not DEFAULT_GAME_MIGRATION_RUNNER._table_exists(self._conn, "ui_notifications"):
                return 0
            row = self._conn.execute(
                "SELECT COUNT(*) FROM ui_notifications WHERE status = 'unread'"
            ).fetchone()
            return int(row[0]) if row is not None else 0

    def acknowledge_notification(
        self,
        notification_id: Any,
        *,
        status: str = "read",
        ts: Optional[float] = None,
    ) -> Optional[dict[str, Any]]:
        nid_opt = self.parse_notification_id(notification_id)
        nid = int(nid_opt) if nid_opt is not None else _sql_int(notification_id, 0)
        if nid <= 0:
            return None
        st_norm = _sanitize_text(status or "read").strip().lower()
        if st_norm not in ("read", "dismissed", "unread"):
            st_norm = "read"
        now_ts = round(_safe_float(ts, time.time()) if ts is not None else time.time(), 3)
        ack_ts = None if st_norm == "unread" else now_ts
        with self._lock:
            if not DEFAULT_GAME_MIGRATION_RUNNER._table_exists(self._conn, "ui_notifications"):
                return None
            self._conn.execute(
                """
                UPDATE ui_notifications
                SET status = ?, acknowledged_ts = ?
                WHERE id = ?
                """,
                (st_norm, ack_ts, nid),
            )
            self._conn.commit()
            row = self._conn.execute("SELECT * FROM ui_notifications WHERE id = ?", (nid,)).fetchone()
        return self._row_to_notification(row) if row is not None else None

    def acknowledge_all_notifications(
        self,
        *,
        status: str = "read",
        ts: Optional[float] = None,
    ) -> int:
        st_norm = _sanitize_text(status or "read").strip().lower()
        if st_norm not in ("read", "dismissed"):
            st_norm = "read"
        now_ts = round(_safe_float(ts, time.time()) if ts is not None else time.time(), 3)
        with self._lock:
            if not DEFAULT_GAME_MIGRATION_RUNNER._table_exists(self._conn, "ui_notifications"):
                return 0
            cur = self._conn.execute(
                """
                UPDATE ui_notifications
                SET status = ?, acknowledged_ts = ?
                WHERE status = 'unread'
                """,
                (st_norm, now_ts),
            )
            changed = int(cur.rowcount or 0)
            self._conn.commit()
        return changed

    def get_rank_samples(self, *, limit: int = 240) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._conn.execute(
                """
                SELECT ts, server_points, server_rank, estimated_points,
                       pts_per_hr, spice_value, planets
                FROM (
                    SELECT * FROM rank_snapshots ORDER BY id DESC LIMIT ?
                ) sub
                ORDER BY id ASC
                """,
                (max(1, _sql_int(limit, 240)),),
            ).fetchall()
        out_samples: list[dict[str, Any]] = []
        for r in rows:
            sp = _safe_optional_int(r["server_points"])
            sr = _safe_optional_int(r["server_rank"])
            out_samples.append(
                _sanitize_obj(
                    {
                        "ts": _safe_float(r["ts"], 0.0),
                        "server_points": game_data.cap_json_int(sp) if sp is not None else None,
                        "server_rank": game_data.cap_json_int(sr) if sr is not None else None,
                        "estimated_points": _safe_float(r["estimated_points"], 0.0),
                        "pts_per_hr": _safe_float(r["pts_per_hr"], 0.0),
                        "spice_value": _safe_float(r["spice_value"], 0.0),
                        "planets": game_data.cap_json_int(max(0, _safe_int(r["planets"], 0))),
                    }
                )
            )
        return out_samples

    def record_full_state_snapshot(
        self,
        state: GameState,
        plan: Sequence[Any] = (),
        *,
        source: str = "advisor",
        min_interval_seconds: float = 0.0,
    ) -> Optional[int]:
        """Persist a full `GameState` + action `plan` snapshot into append-only `state_snapshots`."""
        now_ts = _safe_float(state.timestamp, 0.0)
        if now_ts <= 0:
            now_ts = time.time()
        state_payload = _sanitize_obj(asdict(state) if is_dataclass(state) else dict(state))
        plan_payload = _sanitize_obj(
            [asdict(a) if is_dataclass(a) else (dict(a) if isinstance(a, Mapping) else str(a)) for a in plan]
        )
        src_clean = _sanitize_text(source or "advisor")[:32]
        with self._lock:
            if not DEFAULT_GAME_MIGRATION_RUNNER._table_exists(self._conn, "state_snapshots"):
                return None
            if min_interval_seconds > 0.0:
                last_row = self._conn.execute(
                    "SELECT id, ts FROM state_snapshots WHERE source = ? ORDER BY id DESC LIMIT 1",
                    (src_clean,),
                ).fetchone()
                if last_row is not None:
                    last_ts = _safe_optional_float(last_row["ts"])
                    if last_ts is not None and abs(now_ts - last_ts) < min_interval_seconds:
                        return _sql_int(last_row["id"], 0)
            state_json_str = _json_dumps(state_payload)
            plan_json_str = _json_dumps(plan_payload)
            cur = self._conn.execute(
                """
                INSERT INTO state_snapshots(
                    ts, source, universe_id, user_id, nickname, active_planet_id,
                    planet_count, planned_action_count, state_json, plan_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    round(now_ts, 3),
                    src_clean,
                    _sql_int(state.universe_id, 0),
                    _sql_int(state.user_id, 0),
                    _sanitize_text(state.nickname)[:120],
                    _sql_int(state.active_planet_id or 0, 0),
                    _sql_int(len(state.planets), 0),
                    _sql_int(len(plan_payload), 0),
                    state_json_str,
                    plan_json_str,
                ),
            )
            row_id = int(cur.lastrowid or 0)
            self._upsert_latest_state_hot_locked(
                source_snapshot_id=row_id,
                ts=now_ts,
                source=src_clean,
                universe_id=_sql_int(state.universe_id, 0),
                user_id=_sql_int(state.user_id, 0),
                nickname=_sanitize_text(state.nickname)[:120],
                active_planet_id=_sql_int(state.active_planet_id or 0, 0),
                planet_count=_sql_int(len(state.planets), 0),
                planned_action_count=_sql_int(len(plan_payload), 0),
                state_json=state_json_str,
                plan_json=plan_json_str,
                now_ts=now_ts,
            )
            self._conn.commit()
            return row_id

    def get_latest_state_hot(self, *, include_payload: bool = True) -> Optional[dict[str, Any]]:
        """Read the latest operational state snapshot from `latest_state_hot` (fallback: `state_snapshots`)."""
        with self._lock:
            row = None
            if DEFAULT_GAME_MIGRATION_RUNNER._table_exists(self._conn, "latest_state_hot"):
                cols = (
                    "source_snapshot_id AS id, ts, source, universe_id, user_id, nickname, "
                    "active_planet_id, planet_count, planned_action_count"
                    + (", state_json, plan_json" if include_payload else "")
                )
                row = self._conn.execute(f"SELECT {cols} FROM latest_state_hot WHERE id = 1 LIMIT 1").fetchone()
            if row is None and DEFAULT_GAME_MIGRATION_RUNNER._table_exists(self._conn, "state_snapshots"):
                cols = (
                    "id, ts, source, universe_id, user_id, nickname, active_planet_id, "
                    "planet_count, planned_action_count"
                    + (", state_json, plan_json" if include_payload else "")
                )
                row = self._conn.execute(f"SELECT {cols} FROM state_snapshots ORDER BY id DESC LIMIT 1").fetchone()
        if row is None:
            return None
        item: dict[str, Any] = {
            "id": _sql_int(row["id"], 0),
            "ts": _safe_float(row["ts"], 0.0),
            "source": _sanitize_text(_raw_text(row["source"], "advisor")),
            "universe_id": _sql_int(row["universe_id"], 0),
            "user_id": _sql_int(row["user_id"], 0),
            "nickname": _sanitize_text(_raw_text(row["nickname"], "")),
            "active_planet_id": _sql_int(row["active_planet_id"], 0),
            "planet_count": _sql_int(row["planet_count"], 0),
            "planned_action_count": _sql_int(row["planned_action_count"], 0),
        }
        if include_payload:
            try:
                item["state"] = json.loads(_raw_text(row["state_json"], "{}"))
            except ValueError:
                item["state"] = {}
            try:
                item["plan"] = json.loads(_raw_text(row["plan_json"], "[]"))
            except ValueError:
                item["plan"] = []
        return _sanitize_obj(item)

    def get_state_snapshots(self, *, limit: int = 20, include_payload: bool = False) -> list[dict[str, Any]]:
        """Return recent full state/plan snapshots from `state_snapshots`."""
        with self._lock:
            if not DEFAULT_GAME_MIGRATION_RUNNER._table_exists(self._conn, "state_snapshots"):
                return []
            cols = (
                "id, ts, source, universe_id, user_id, nickname, active_planet_id, "
                "planet_count, planned_action_count"
                + (", state_json, plan_json" if include_payload else "")
            )
            rows = self._conn.execute(
                f"SELECT {cols} FROM state_snapshots ORDER BY id DESC LIMIT ?",
                (max(1, min(200, _sql_int(limit, 20))),),
            ).fetchall()
        out: list[dict[str, Any]] = []
        for r in rows:
            item: dict[str, Any] = {
                "id": _sql_int(r["id"], 0),
                "ts": _safe_float(r["ts"], 0.0),
                "source": _sanitize_text(_raw_text(r["source"], "advisor")),
                "universe_id": _sql_int(r["universe_id"], 0),
                "user_id": _sql_int(r["user_id"], 0),
                "nickname": _sanitize_text(_raw_text(r["nickname"], "")),
                "active_planet_id": _sql_int(r["active_planet_id"], 0),
                "planet_count": _sql_int(r["planet_count"], 0),
                "planned_action_count": _sql_int(r["planned_action_count"], 0),
            }
            if include_payload:
                try:
                    item["state"] = json.loads(_raw_text(r["state_json"], "{}"))
                except ValueError:
                    item["state"] = {}
                try:
                    item["plan"] = json.loads(_raw_text(r["plan_json"], "[]"))
                except ValueError:
                    item["plan"] = []
            out.append(_sanitize_obj(item))
        return out

    def save_economy_rates(
        self,
        planet_id: int,
        coords: str,
        rates: dict[str, int],
        ts: Optional[float] = None,
    ) -> None:
        if not rates:
            return
        now_ts = _safe_float(ts, time.time()) if ts is not None else time.time()
        clean = {
            game_data.safe_key(k): v_i
            for k, v in rates.items()
            if (v_i := _safe_optional_int(v)) is not None
        }
        with self._lock:
            self._conn.execute(
                """
                INSERT INTO economy_rates_cache(planet_id, coords, rates_json, updated_ts)
                VALUES(?, ?, ?, ?)
                ON CONFLICT(planet_id) DO UPDATE SET
                    coords = excluded.coords,
                    rates_json = excluded.rates_json,
                    updated_ts = excluded.updated_ts
                """,
                (_sql_int(planet_id, 0), _raw_text(coords, ""), _json_dumps(clean), now_ts),
            )
            self._conn.commit()

    def get_all_economy_rates(self) -> dict[int, dict[str, int]]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT planet_id, rates_json FROM economy_rates_cache"
            ).fetchall()
        out: dict[int, dict[str, int]] = {}
        for row in rows:
            pid_raw = _safe_optional_int(row["planet_id"])
            if pid_raw is None:
                continue
            pid = game_data.cap_json_int(pid_raw)
            try:
                parsed = json.loads(_raw_text(row["rates_json"], ""))
            except (ValueError, TypeError):
                continue
            if isinstance(parsed, dict):
                clean_rates: dict[str, int] = {}
                for k, v in parsed.items():
                    v_i = _safe_optional_int(v)
                    if v_i is not None:
                        clean_rates[_sanitize_text(game_data.safe_key(k))] = game_data.cap_json_int(v_i)
                out[pid] = clean_rates
        return out

    def record_action(
        self,
        *,
        source: str,
        command: str,
        action: Any,
        executed: bool,
        dry_run: bool,
        message: str,
        ts: Optional[float] = None,
    ) -> None:
        now_ts = _safe_float(ts, time.time()) if ts is not None else time.time()
        if is_dataclass(action) and not isinstance(action, type):
            act_map = redact_sensitive(asdict(action))
        elif isinstance(action, Mapping):
            act_map = redact_sensitive(action)
        else:
            act_map = {}

        with self._lock:
            self._conn.execute(
                """
                INSERT INTO action_history(
                    ts, source, command, priority, kind,
                    planet_id, planet_coords, target_id, target_name, target_level,
                    executed, dry_run, reason, message
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    round(now_ts, 3),
                    _sanitize_text(source),
                    _sanitize_text(command),
                    _sanitize_text(act_map["priority"]) if act_map.get("priority") is not None else None,
                    _sanitize_text(act_map.get("kind")) or _sanitize_text(command).upper(),
                    _sql_optional_int(act_map.get("planet_id")),
                    _sanitize_text(act_map["planet_coords"]) if act_map.get("planet_coords") is not None else None,
                    _sql_optional_int(act_map.get("target_id")),
                    _sanitize_text(act_map["target_name"]) if act_map.get("target_name") is not None else None,
                    _sql_optional_int(act_map.get("target_level")),
                    1 if executed else 0,
                    1 if dry_run else 0,
                    _sanitize_text(act_map.get("reason")),
                    _sanitize_text(message),
                ),
            )
            self._conn.commit()

    def _coalesce_legacy_spy_target_locked(self, norm_coords: str, raw_coords: str = "") -> None:
        if not norm_coords:
            return
        _, cands, like_pat = _coord_query_candidates(norm_coords)
        if raw_coords and raw_coords not in cands:
            cands.append(raw_coords)
        if not cands:
            return
        placeholders = ", ".join("?" for _ in cands)
        sql = (
            "SELECT coords, owner, planet_name, status_color, is_inactive, is_spacia, has_debris, "
            "points, pig_iron, metal, kryptonite, spice, fleet_json, defense_json, "
            "dug_in, last_scanned_ts, last_spied_ts, blocked_until_ts, block_reason, bash_hits, "
            "last_blocked_ts, uninhabited_fields, spy_count "
            f"FROM spy_targets WHERE coords IN ({placeholders})"
        )
        params: list[Any] = list(cands)
        if like_pat is not None:
            sql += " OR coords LIKE ?"
            params.append(like_pat)
        rows = [
            r
            for r in self._conn.execute(sql, params).fetchall()
            if normalize_coords(_raw_text(r["coords"], "")) == norm_coords
        ]
        legacy_rows = [r for r in rows if _raw_text(r["coords"], "") != norm_coords]
        if not legacy_rows:
            return
        norm_row = next((r for r in rows if _raw_text(r["coords"], "") == norm_coords), None)
        if norm_row is None and len(legacy_rows) == 1:
            self._conn.execute(
                "UPDATE spy_targets SET coords = ? WHERE coords = ?",
                (norm_coords, legacy_rows[0]["coords"]),
            )
            return

        all_rows = ([norm_row] if norm_row is not None else []) + legacy_rows
        ranked = sorted(
            all_rows,
            key=lambda r: (
                1 if _safe_float(r["last_spied_ts"], 0.0) > 0.0 else 0,
                _safe_float(r["last_spied_ts"], 0.0),
                _safe_float(r["last_scanned_ts"], 0.0),
                1 if _raw_text(r["coords"], "") == norm_coords else 0,
            ),
            reverse=True,
        )
        best = ranked[0]
        best_points = next((r["points"] for r in ranked if r["points"] is not None), None)
        max_scanned = max(_safe_float(r["last_scanned_ts"], 0.0) for r in all_rows)
        best_spacia = 1 if any(bool(r["is_spacia"]) for r in all_rows) else 0
        best_debris = 1 if any(bool(r["has_debris"]) for r in all_rows) else 0
        max_blocked_until = max(_safe_float(r["blocked_until_ts"], 0.0) for r in all_rows)
        best_block_reason = next(
            (
                _raw_text(r["block_reason"], "")
                for r in sorted(all_rows, key=lambda x: _safe_float(x["blocked_until_ts"], 0.0), reverse=True)
                if _raw_text(r["block_reason"], "").strip()
            ),
            "",
        )
        sum_bash_hits = sum(max(0, _safe_int(r["bash_hits"], 0)) for r in all_rows)
        max_last_blocked = max(_safe_float(r["last_blocked_ts"], 0.0) for r in all_rows)
        best_uninhabited_fields = next(
            (r["uninhabited_fields"] for r in ranked if r["uninhabited_fields"] is not None),
            None,
        )
        sum_spy_count = sum(max(0, _safe_int(r["spy_count"], 0)) for r in all_rows)
        best_owner = next((_raw_text(r["owner"], "") for r in ranked if _raw_text(r["owner"], "").strip()), "")
        best_pname = next((_raw_text(r["planet_name"], "") for r in ranked if _raw_text(r["planet_name"], "").strip()), "")

        if norm_row is not None:
            self._conn.execute(
                """
                UPDATE spy_targets
                SET owner = CASE WHEN owner != '' THEN owner ELSE ? END,
                    planet_name = CASE WHEN planet_name != '' THEN planet_name ELSE ? END,
                    is_spacia = ?,
                    has_debris = ?,
                    points = COALESCE(?, points),
                    pig_iron = ?,
                    metal = ?,
                    kryptonite = ?,
                    spice = ?,
                    fleet_json = ?,
                    defense_json = ?,
                    dug_in = ?,
                    last_scanned_ts = ?,
                    last_spied_ts = ?,
                    blocked_until_ts = ?,
                    block_reason = ?,
                    bash_hits = ?,
                    last_blocked_ts = ?,
                    uninhabited_fields = COALESCE(?, uninhabited_fields),
                    spy_count = ?
                WHERE coords = ?
                """,
                (
                    best_owner,
                    best_pname,
                    best_spacia,
                    best_debris,
                    best_points,
                    best["pig_iron"],
                    best["metal"],
                    best["kryptonite"],
                    best["spice"],
                    best["fleet_json"],
                    best["defense_json"],
                    best["dug_in"],
                    max_scanned,
                    best["last_spied_ts"],
                    max_blocked_until,
                    _sanitize_text(best_block_reason),
                    _sql_int(sum_bash_hits, 0),
                    max_last_blocked,
                    _sql_optional_int(best_uninhabited_fields),
                    _sql_int(sum_spy_count, 0),
                    norm_coords,
                ),
            )
            for lr in legacy_rows:
                self._conn.execute(
                    "UPDATE spy_targets SET merged_into = ? WHERE coords = ?",
                    (norm_coords, lr["coords"]),
                )
        else:
            keep_coords = best["coords"]
            for lr in legacy_rows:
                if lr["coords"] != keep_coords:
                    self._conn.execute(
                        "UPDATE spy_targets SET merged_into = ? WHERE coords = ?",
                        (norm_coords, lr["coords"]),
                    )
            self._conn.execute(
                """
                UPDATE spy_targets
                SET coords = ?,
                    merged_into = '',
                    owner = CASE WHEN owner != '' THEN owner ELSE ? END,
                    planet_name = CASE WHEN planet_name != '' THEN planet_name ELSE ? END,
                    is_spacia = ?,
                    has_debris = ?,
                    points = COALESCE(points, ?),
                    last_scanned_ts = ?,
                    blocked_until_ts = ?,
                    block_reason = ?,
                    bash_hits = ?,
                    last_blocked_ts = ?,
                    uninhabited_fields = COALESCE(uninhabited_fields, ?),
                    spy_count = ?
                WHERE coords = ?
                """,
                (
                    norm_coords,
                    best_owner,
                    best_pname,
                    best_spacia,
                    best_debris,
                    best_points,
                    max_scanned,
                    max_blocked_until,
                    _sanitize_text(best_block_reason),
                    _sql_int(sum_bash_hits, 0),
                    max_last_blocked,
                    _sql_optional_int(best_uninhabited_fields),
                    _sql_int(sum_spy_count, 0),
                    keep_coords,
                ),
            )

    def record_spy_report(
        self,
        report: SpyReport,
        *,
        points: Optional[int] = None,
        is_inactive: bool = False,
        status_color: Optional[str] = None,
        ts: Optional[float] = None,
    ) -> None:
        now_ts = round(_safe_float(ts, time.time()) if ts is not None else time.time(), 3)
        raw_coords = _raw_text(report.target_coords, "").strip()
        norm_coords = normalize_coords(raw_coords)
        raw_txt = _sanitize_text(getattr(report, "raw_text", "") or "")[:4000]
        if not norm_coords or (raw_txt and is_non_spy_notice_text(raw_txt)):
            return
        explicit_status = status_color is not None or bool(is_inactive) or bool(report.is_spacia)
        insert_inactive = bool(is_inactive or report.is_spacia or status_color == "yellow")
        insert_color = status_color or ("yellow" if insert_inactive else "light_red")
        pig_i = _sql_int(report.resources.pig_iron, 0)
        met_i = _sql_int(report.resources.metal, 0)
        kry_i = _sql_int(report.resources.kryptonite, 0)
        spi_i = _sql_int(report.resources.spice, 0)
        loot_sp_eq = round(
            _safe_float(
                game_data.spice_equivalent(
                    {
                        "pig_iron": max(0, pig_i),
                        "metal": max(0, met_i),
                        "kryptonite": max(0, kry_i),
                        "spice": max(0, spi_i),
                    }
                ),
                0.0,
            ),
            3,
        )
        buildings_j = _json_dumps(dict(getattr(report, "buildings", None) or {}))
        fleet_j = _json_dumps(dict(report.fleet))
        defense_j = _json_dumps(dict(report.defense))
        researches_j = _json_dumps(dict(getattr(report, "researches", None) or {}))
        pname = _sanitize_text(getattr(report, "planet_name", "") or "")
        s_msg_id = _sanitize_text(getattr(report, "server_msg_id", "") or "")
        m_date = _sanitize_text(getattr(report, "msg_date_text", "") or "")
        uninhabited_f = _sql_optional_int(getattr(report, "uninhabited_fields", None))
        with self._lock:
            self._coalesce_legacy_spy_target_locked(norm_coords, raw_coords)
            self._conn.execute(
                """
                INSERT INTO spy_targets(
                    coords, owner, planet_name, status_color, is_inactive, is_spacia, points,
                    pig_iron, metal, kryptonite, spice,
                    fleet_json, defense_json, dug_in, last_scanned_ts, last_spied_ts,
                    uninhabited_fields, spy_count, merged_into
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, '')
                ON CONFLICT(coords) DO UPDATE SET
                    merged_into = '',
                    owner = CASE WHEN excluded.owner != '' THEN excluded.owner ELSE spy_targets.owner END,
                    planet_name = CASE WHEN excluded.planet_name != '' THEN excluded.planet_name ELSE spy_targets.planet_name END,
                    status_color = CASE WHEN ? = 1 THEN excluded.status_color ELSE spy_targets.status_color END,
                    is_inactive = CASE WHEN ? = 1 THEN excluded.is_inactive ELSE spy_targets.is_inactive END,
                    is_spacia = CASE WHEN excluded.is_spacia = 1 THEN 1 ELSE spy_targets.is_spacia END,
                    points = COALESCE(excluded.points, spy_targets.points),
                    pig_iron = excluded.pig_iron,
                    metal = excluded.metal,
                    kryptonite = excluded.kryptonite,
                    spice = excluded.spice,
                    fleet_json = excluded.fleet_json,
                    defense_json = excluded.defense_json,
                    dug_in = excluded.dug_in,
                    last_scanned_ts = MAX(spy_targets.last_scanned_ts, excluded.last_scanned_ts),
                    last_spied_ts = excluded.last_spied_ts,
                    uninhabited_fields = COALESCE(excluded.uninhabited_fields, spy_targets.uninhabited_fields),
                    spy_count = spy_targets.spy_count + 1
                """,
                (
                    norm_coords,
                    _sanitize_text(report.owner),
                    pname,
                    insert_color,
                    1 if insert_inactive else 0,
                    1 if report.is_spacia else 0,
                    _sql_optional_int(points),
                    pig_i,
                    met_i,
                    kry_i,
                    spi_i,
                    fleet_j,
                    defense_j,
                    1 if report.dug_in else 0,
                    now_ts,
                    now_ts,
                    uninhabited_f,
                    1 if explicit_status else 0,
                    1 if explicit_status else 0,
                ),
            )
            self._conn.execute(
                """
                INSERT INTO spy_reports(
                    ts, target_coords, owner, planet_name, is_spacia,
                    pig_iron, metal, kryptonite, spice, loot_spice_eq,
                    buildings_json, fleet_json, defense_json, researches_json,
                    dug_in, uninhabited_fields, server_msg_id, msg_date_text, raw_text
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    now_ts,
                    norm_coords,
                    _sanitize_text(report.owner),
                    pname,
                    1 if report.is_spacia else 0,
                    pig_i,
                    met_i,
                    kry_i,
                    spi_i,
                    loot_sp_eq,
                    buildings_j,
                    fleet_j,
                    defense_j,
                    researches_j,
                    (1 if report.dug_in else 0) if report.dug_in is not None else None,
                    uninhabited_f,
                    s_msg_id,
                    m_date,
                    raw_txt,
                ),
            )
            self._refresh_colony_candidates_hot_locked((norm_coords,), now_ts)
            parsed_sr = self._parse_norm_gsp_fast(norm_coords)
            if parsed_sr is not None and 1 <= parsed_sr[1] <= 14 and 1 <= parsed_sr[2] <= 400:
                self._refresh_galaxy_sectors_hot_locked(((parsed_sr[1], parsed_sr[2]),), now_ts)
            self._conn.commit()

    def _has_column_locked(self, table: str, column: str) -> bool:
        try:
            cols = {r["name"] for r in self._conn.execute(f"PRAGMA table_info({table})").fetchall()}
            return column in cols
        except sqlite3.Error:
            return False

    def _upsert_galaxy_slot_locked(
        self,
        slot: GalaxySlot,
        now_ts: float,
        *,
        append_history: bool = True,
    ) -> None:
        raw_slot_coords = _raw_text(getattr(slot, "coords", ""), "").strip()
        norm_slot_coords = normalize_coords(raw_slot_coords)
        if not norm_slot_coords:
            return
        parts = [p.strip() for p in norm_slot_coords.split(":")]
        gal_i = _sql_int(parts[0], 0) if len(parts) >= 1 and parts[0].isdigit() else 0
        sys_i = _sql_int(parts[1], 0) if len(parts) >= 2 and parts[1].isdigit() else 0
        pos_fallback = _sql_int(getattr(slot, "position", 0), 0)
        pos_i = (
            _sql_int(parts[2], pos_fallback)
            if len(parts) >= 3 and parts[2].isdigit()
            else pos_fallback
        )
        scolor = _sanitize_text(getattr(slot, "status_color", "free") or "free")
        is_spacia = 1 if bool(getattr(slot, "is_spacia", False)) else 0
        is_inactive = 1 if (scolor == "yellow" or is_spacia) else 0
        has_debris = 1 if bool(getattr(slot, "has_debris", False)) else 0
        has_asteroid = 1 if bool(getattr(slot, "has_asteroid", False)) else 0
        pname_s = _sanitize_text(getattr(slot, "name", ""))
        owner_s = _sanitize_text(getattr(slot, "owner", ""))
        c_min_opt = _sql_optional_int(getattr(slot, "colonize_min_fields", None))
        c_max_opt = _sql_optional_int(getattr(slot, "colonize_max_fields", None))
        temp_opt = _sql_optional_int(getattr(slot, "temperature", None))
        if self._has_column_locked("galaxy_slots", "colonize_max_fields"):
            self._conn.execute(
                """
                INSERT INTO galaxy_slots(
                    coords, galaxy, system, position, planet_name, owner,
                    status_color, is_inactive, is_spacia, has_debris, last_scanned_ts,
                    colonize_min_fields, colonize_max_fields, temperature
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(coords) DO UPDATE SET
                    galaxy = excluded.galaxy,
                    system = excluded.system,
                    position = excluded.position,
                    planet_name = excluded.planet_name,
                    owner = excluded.owner,
                    status_color = excluded.status_color,
                    is_inactive = excluded.is_inactive,
                    is_spacia = excluded.is_spacia,
                    has_debris = excluded.has_debris,
                    last_scanned_ts = excluded.last_scanned_ts,
                    colonize_min_fields = CASE
                        WHEN excluded.status_color != 'free' THEN NULL
                        ELSE COALESCE(excluded.colonize_min_fields, galaxy_slots.colonize_min_fields)
                    END,
                    colonize_max_fields = CASE
                        WHEN excluded.status_color != 'free' THEN NULL
                        ELSE COALESCE(excluded.colonize_max_fields, galaxy_slots.colonize_max_fields)
                    END,
                    temperature = COALESCE(excluded.temperature, galaxy_slots.temperature)
                """,
                (
                    norm_slot_coords,
                    gal_i,
                    sys_i,
                    pos_i,
                    pname_s,
                    owner_s,
                    scolor,
                    is_inactive,
                    is_spacia,
                    has_debris,
                    now_ts,
                    c_min_opt if scolor == "free" else None,
                    c_max_opt if scolor == "free" else None,
                    temp_opt,
                ),
            )
        else:
            self._conn.execute(
                """
                INSERT INTO galaxy_slots(
                    coords, galaxy, system, position, planet_name, owner,
                    status_color, is_inactive, is_spacia, has_debris, last_scanned_ts
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(coords) DO UPDATE SET
                    galaxy = excluded.galaxy,
                    system = excluded.system,
                    position = excluded.position,
                    planet_name = excluded.planet_name,
                    owner = excluded.owner,
                    status_color = excluded.status_color,
                    is_inactive = excluded.is_inactive,
                    is_spacia = excluded.is_spacia,
                    has_debris = excluded.has_debris,
                    last_scanned_ts = excluded.last_scanned_ts
                """,
                (
                    norm_slot_coords,
                    gal_i,
                    sys_i,
                    pos_i,
                    pname_s,
                    owner_s,
                    scolor,
                    is_inactive,
                    is_spacia,
                    has_debris,
                    now_ts,
                ),
            )
        if append_history:
            if self._has_column_locked("galaxy_slot_history", "colonize_max_fields"):
                self._conn.execute(
                    """
                    INSERT INTO galaxy_slot_history(
                        ts, coords, galaxy, system, position, planet_name, owner,
                        status_color, is_inactive, is_spacia, has_debris, has_asteroid,
                        colonize_min_fields, colonize_max_fields, temperature
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        now_ts,
                        norm_slot_coords,
                        gal_i,
                        sys_i,
                        pos_i,
                        pname_s,
                        owner_s,
                        scolor,
                        is_inactive,
                        is_spacia,
                        has_debris,
                        has_asteroid,
                        c_min_opt if scolor == "free" else None,
                        c_max_opt if scolor == "free" else None,
                        temp_opt,
                    ),
                )
            else:
                self._conn.execute(
                    """
                    INSERT INTO galaxy_slot_history(
                        ts, coords, galaxy, system, position, planet_name, owner,
                        status_color, is_inactive, is_spacia, has_debris, has_asteroid
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        now_ts,
                        norm_slot_coords,
                        gal_i,
                        sys_i,
                        pos_i,
                        pname_s,
                        owner_s,
                        scolor,
                        is_inactive,
                        is_spacia,
                        has_debris,
                        has_asteroid,
                    ),
                )

    def record_galaxy_slots(
        self,
        slots: Sequence[GalaxySlot],
        *,
        ts: Optional[float] = None,
    ) -> int:
        now_ts = round(_safe_float(ts, time.time()) if ts is not None else time.time(), 3)
        saved = 0
        sig_coords = tuple(
            normalize_coords(_raw_text(getattr(s, "coords", ""), "").strip())
            for s in slots
        )
        with self._lock:
            append_hist = (
                self._last_slot_history_sig is None
                or abs(now_ts - self._last_slot_history_sig[0]) > 1.0
                or self._last_slot_history_sig[1] != sig_coords
            )
            if slots and append_hist:
                self._last_slot_history_sig = (now_ts, sig_coords)
            touched_coords: set[str] = set()
            touched_sectors: set[tuple[int, int]] = set()
            for slot in slots:
                self._upsert_galaxy_slot_locked(slot, now_ts, append_history=append_hist)
                has_debris = 1 if bool(getattr(slot, "has_debris", False)) else 0
                raw_slot_coords = _raw_text(slot.coords, "").strip()
                norm_slot_coords = normalize_coords(raw_slot_coords)
                if norm_slot_coords:
                    touched_coords.add(norm_slot_coords)
                    parsed_sc = self._parse_norm_gsp_fast(norm_slot_coords)
                    if parsed_sc is not None and 1 <= parsed_sc[1] <= 14 and 1 <= parsed_sc[2] <= 400:
                        touched_sectors.add((parsed_sc[1], parsed_sc[2]))
                if slot.status_color == "free" and not has_debris:
                    if norm_slot_coords:
                        slot_uf = _sql_optional_int(getattr(slot, "uninhabited_fields", None))
                        slot_cmin = _sql_optional_int(getattr(slot, "colonize_min_fields", None))
                        slot_cmax = _sql_optional_int(getattr(slot, "colonize_max_fields", None))
                        slot_temp = _sql_optional_int(getattr(slot, "temperature", None))
                        if slot_uf is not None or (slot_cmax is not None and self._has_column_locked("spy_targets", "colonize_max_fields")):
                            if self._has_column_locked("spy_targets", "colonize_max_fields"):
                                self._conn.execute(
                                    """
                                    INSERT INTO spy_targets(
                                        coords, owner, planet_name, status_color, is_inactive, is_spacia,
                                        has_debris, last_scanned_ts, uninhabited_fields,
                                        colonize_min_fields, colonize_max_fields, temperature, merged_into
                                    ) VALUES (?, '', '', 'free', 0, 0, 0, ?, ?, ?, ?, ?, '')
                                    ON CONFLICT(coords) DO UPDATE SET
                                        owner = '',
                                        planet_name = '',
                                        status_color = 'free',
                                        is_inactive = 0,
                                        is_spacia = 0,
                                        has_debris = 0,
                                        last_scanned_ts = excluded.last_scanned_ts,
                                        uninhabited_fields = COALESCE(excluded.uninhabited_fields, spy_targets.uninhabited_fields),
                                        colonize_min_fields = COALESCE(excluded.colonize_min_fields, spy_targets.colonize_min_fields),
                                        colonize_max_fields = COALESCE(excluded.colonize_max_fields, spy_targets.colonize_max_fields),
                                        temperature = COALESCE(excluded.temperature, spy_targets.temperature),
                                        blocked_until_ts = CASE
                                            WHEN LOWER(spy_targets.block_reason) LIKE '%can''t be attacked%'
                                              OR LOWER(spy_targets.block_reason) LIKE '%cannot be attacked%'
                                            THEN 0.0
                                            ELSE spy_targets.blocked_until_ts
                                        END,
                                        block_reason = CASE
                                            WHEN LOWER(spy_targets.block_reason) LIKE '%can''t be attacked%'
                                              OR LOWER(spy_targets.block_reason) LIKE '%cannot be attacked%'
                                            THEN ''
                                            ELSE spy_targets.block_reason
                                        END
                                    WHERE spy_targets.is_own_planet = 0
                                    """,
                                    (norm_slot_coords, now_ts, slot_uf, slot_cmin, slot_cmax, slot_temp),
                                )
                            else:
                                self._conn.execute(
                                    """
                                    INSERT INTO spy_targets(
                                        coords, owner, planet_name, status_color, is_inactive, is_spacia,
                                        has_debris, last_scanned_ts, uninhabited_fields, merged_into
                                    ) VALUES (?, '', '', 'free', 0, 0, 0, ?, ?, '')
                                    ON CONFLICT(coords) DO UPDATE SET
                                        owner = '',
                                        planet_name = '',
                                        status_color = 'free',
                                        is_inactive = 0,
                                        is_spacia = 0,
                                        has_debris = 0,
                                        last_scanned_ts = excluded.last_scanned_ts,
                                        uninhabited_fields = COALESCE(excluded.uninhabited_fields, spy_targets.uninhabited_fields)
                                    WHERE spy_targets.is_own_planet = 0
                                    """,
                                    (norm_slot_coords, now_ts, slot_uf),
                                )
                        else:
                            self._conn.execute(
                                """
                                UPDATE spy_targets
                                SET owner = '',
                                    planet_name = '',
                                    status_color = 'free',
                                    is_inactive = 0,
                                    is_spacia = 0,
                                    has_debris = 0,
                                    last_scanned_ts = ?
                                WHERE coords = ? AND is_own_planet = 0
                                """,
                                (now_ts, norm_slot_coords),
                            )
                    continue
                is_inactive = 1 if (slot.status_color == "yellow" or slot.is_spacia) else 0
                if not norm_slot_coords:
                    continue
                self._coalesce_legacy_spy_target_locked(norm_slot_coords, raw_slot_coords)
                self._conn.execute(
                    """
                    INSERT INTO spy_targets(
                        coords, owner, planet_name, status_color, is_inactive, is_spacia, has_debris, last_scanned_ts, merged_into
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, '')
                    ON CONFLICT(coords) DO UPDATE SET
                        merged_into = '',
                        owner = excluded.owner,
                        planet_name = excluded.planet_name,
                        status_color = excluded.status_color,
                        is_inactive = excluded.is_inactive,
                        is_spacia = excluded.is_spacia,
                        has_debris = excluded.has_debris,
                        last_scanned_ts = excluded.last_scanned_ts
                    """,
                    (
                        norm_slot_coords,
                        _sanitize_text(slot.owner),
                        _sanitize_text(slot.name),
                        _sanitize_text(slot.status_color),
                        is_inactive,
                        1 if slot.is_spacia else 0,
                        has_debris,
                        now_ts,
                    ),
                )
                saved += 1
            if touched_coords:
                self._refresh_colony_candidates_hot_locked(touched_coords, now_ts)
            if touched_sectors:
                self._refresh_galaxy_sectors_hot_locked(touched_sectors, now_ts)
            self._conn.commit()
        return saved

    def record_galaxy_scan(
        self,
        galaxy: int,
        system: int,
        slots: Sequence[GalaxySlot] = (),
        *,
        ts: Optional[float] = None,
    ) -> None:
        now_ts = round(_safe_float(ts, time.time()) if ts is not None else time.time(), 3)
        occupied_count = sum(1 for s in slots if getattr(s, "status_color", "free") != "free")
        target_count = sum(
            1
            for s in slots
            if getattr(s, "status_color", "") == "yellow" or bool(getattr(s, "is_spacia", False))
        )
        free_count = sum(1 for s in slots if getattr(s, "status_color", "free") == "free")
        debris_count = sum(1 for s in slots if bool(getattr(s, "has_debris", False)))
        asteroid_count = sum(1 for s in slots if bool(getattr(s, "has_asteroid", False)))
        sig_coords = tuple(
            normalize_coords(_raw_text(getattr(s, "coords", ""), "").strip())
            for s in slots
        )
        with self._lock:
            append_hist = (
                self._last_slot_history_sig is None
                or abs(now_ts - self._last_slot_history_sig[0]) > 1.0
                or self._last_slot_history_sig[1] != sig_coords
            )
            if slots and append_hist:
                self._last_slot_history_sig = (now_ts, sig_coords)
            touched_coords: set[str] = set()
            for slot in slots:
                self._upsert_galaxy_slot_locked(slot, now_ts, append_history=append_hist)
                nc_s = normalize_coords(_raw_text(getattr(slot, "coords", ""), "").strip())
                if nc_s:
                    touched_coords.add(nc_s)
            gal_sql = _sql_int(galaxy, 0)
            sys_sql = _sql_int(system, 0)
            self._conn.execute(
                """
                INSERT INTO galaxy_scans(
                    galaxy, system, last_scanned_ts, occupied_count, target_count, free_count
                ) VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(galaxy, system) DO UPDATE SET
                    last_scanned_ts = excluded.last_scanned_ts,
                    occupied_count = excluded.occupied_count,
                    target_count = excluded.target_count,
                    free_count = excluded.free_count
                """,
                (
                    gal_sql,
                    sys_sql,
                    now_ts,
                    _sql_int(occupied_count, 0),
                    _sql_int(target_count, 0),
                    _sql_int(free_count, 0),
                ),
            )
            self._conn.execute(
                """
                INSERT INTO galaxy_scan_history(
                    ts, galaxy, system, occupied_count, target_count, free_count, debris_count, asteroid_count
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    now_ts,
                    gal_sql,
                    sys_sql,
                    _sql_int(occupied_count, 0),
                    _sql_int(target_count, 0),
                    _sql_int(free_count, 0),
                    _sql_int(debris_count, 0),
                    _sql_int(asteroid_count, 0),
                ),
            )
            if touched_coords:
                self._refresh_colony_candidates_hot_locked(touched_coords, now_ts)
            if 1 <= gal_sql <= 14 and 1 <= sys_sql <= 400:
                self._refresh_galaxy_sectors_hot_locked(((gal_sql, sys_sql),), now_ts)
            self._conn.commit()

    def get_galaxy_slots(
        self,
        *,
        galaxy: Optional[int] = None,
        system: Optional[int] = None,
        status_color: Optional[str] = None,
        limit: int = 500,
    ) -> list[dict[str, Any]]:
        clauses: list[str] = []
        params: list[Any] = []
        if galaxy is not None:
            clauses.append("galaxy = ?")
            params.append(_sql_int(galaxy, 0))
        if system is not None:
            clauses.append("system = ?")
            params.append(_sql_int(system, 0))
        if status_color is not None:
            clauses.append("status_color = ?")
            params.append(_sanitize_text(status_color).strip())
        where_sql = ("WHERE " + " AND ".join(clauses)) if clauses else ""
        params.append(max(1, _sql_int(limit, 500)))
        with self._lock:
            rows = self._conn.execute(
                f"""
                SELECT *
                FROM galaxy_slots
                {where_sql}
                ORDER BY galaxy ASC, system ASC, position ASC
                LIMIT ?
                """,
                params,
            ).fetchall()
        out_gs: list[dict[str, Any]] = []
        for r in rows:
            rk = set(r.keys())
            out_gs.append(
                _sanitize_obj(
                    {
                        "coords": normalize_coords(_raw_text(r["coords"], "")),
                        "galaxy": game_data.cap_json_int(_safe_int(r["galaxy"], 0)),
                        "system": game_data.cap_json_int(_safe_int(r["system"], 0)),
                        "position": game_data.cap_json_int(_safe_int(r["position"], 0)),
                        "planet_name": _sanitize_text(r["planet_name"]) if r["planet_name"] is not None else "",
                        "owner": _sanitize_text(r["owner"]) if r["owner"] is not None else "",
                        "status_color": _sanitize_text(r["status_color"]) if r["status_color"] is not None else "free",
                        "is_inactive": bool(r["is_inactive"]),
                        "is_spacia": bool(r["is_spacia"]),
                        "has_debris": bool(r["has_debris"]),
                        "last_scanned_ts": _safe_float(r["last_scanned_ts"], 0.0),
                        "colonize_min_fields": _safe_optional_int(r["colonize_min_fields"]) if "colonize_min_fields" in rk else None,
                        "colonize_max_fields": _safe_optional_int(r["colonize_max_fields"]) if "colonize_max_fields" in rk else None,
                        "temperature": _safe_optional_int(r["temperature"]) if "temperature" in rk else None,
                    }
                )
            )
        return out_gs

    def get_galaxy_scans(
        self,
        *,
        max_age_seconds: Optional[float] = None,
        now: Optional[float] = None,
    ) -> dict[str, float]:
        safe_max_age = _safe_optional_float(max_age_seconds)
        with self._lock:
            if safe_max_age is not None:
                safe_now = _safe_optional_float(now)
                ref = safe_now if safe_now is not None else time.time()
                cutoff = ref - safe_max_age
                rows = self._conn.execute(
                    """
                    SELECT galaxy, system, last_scanned_ts
                    FROM galaxy_scans
                    WHERE last_scanned_ts >= ?
                    ORDER BY last_scanned_ts DESC
                    """,
                    (cutoff,),
                ).fetchall()
            else:
                rows = self._conn.execute(
                    """
                    SELECT galaxy, system, last_scanned_ts
                    FROM galaxy_scans
                    ORDER BY last_scanned_ts DESC
                    """
                ).fetchall()
        scans: dict[str, float] = {}
        for r in rows:
            gal = game_data.cap_json_int(_safe_int(r["galaxy"], 0))
            sys_num = game_data.cap_json_int(_safe_int(r["system"], 0))
            if gal <= 0 or sys_num <= 0:
                continue
            key = f"{gal:02d}:{sys_num:03d}"
            ts_val = _safe_float(r["last_scanned_ts"], 0.0)
            scans[key] = max(scans.get(key, 0.0), ts_val) if key in scans else ts_val
        return scans

    def get_galaxy_sector_intel(
        self,
        *,
        galaxies: Optional[Sequence[int]] = None,
        now_ts: Optional[float] = None,
    ) -> dict[str, Any]:
        """Aggregate longitudinal scan coverage, per-sector targets, direct raids, and player attack affinity."""
        eff_now = round(_safe_float(now_ts, time.time()) if now_ts is not None else time.time(), 3)
        req_galaxies: list[int] = []
        if galaxies is not None and not isinstance(galaxies, (str, bytes, bytearray)):
            for g_raw in galaxies:
                gi = _safe_int(g_raw, 0)
                if 1 <= gi <= 14 and gi not in req_galaxies:
                    req_galaxies.append(gi)

        hot_base: Optional[dict[str, Any]] = None
        with self._lock:
            use_hot = self._ensure_hot_tables_synced_locked(check_sectors=True)
            if use_hot:
                db_ver = self._db_version_locked()
                base_key = (db_ver, tuple(req_galaxies))
                if self._galaxy_intel_base_ver == base_key and self._galaxy_intel_base_cache is not None:
                    hot_base = self._galaxy_intel_base_cache
                else:
                    cur = self._conn.cursor()
                    cur.row_factory = None
                    hot_rows = cur.execute(
                        """
                        SELECT galaxy, system, last_scanned_ts, scan_count,
                               occupied_count, target_count, inactive_count, spacia_count,
                               debris_count, free_count, prime_colony_free_count,
                               owners_json, owner_planet_counts_json, coord_owners_json, spacia_coords_json
                        FROM galaxy_sector_hot
                        """
                    ).fetchall()
                    own_coords_hot = self._load_own_coords_locked()
                    has_raids_h = DEFAULT_GAME_MIGRATION_RUNNER._table_exists(self._conn, "raid_history")
                    has_battles_h = DEFAULT_GAME_MIGRATION_RUNNER._table_exists(self._conn, "battle_reports")
                    norm_raids_h = self._query_norm_raid_rows_locked() if has_raids_h else []
                    battle_rows_h = (
                        cur.execute(
                            """
                            SELECT target_coords, defender, MAX(loot_spice_eq), SUM(loot_spice_eq)
                            FROM battle_reports
                            WHERE target_coords != ''
                            GROUP BY target_coords, defender
                            """
                        ).fetchall()
                        if has_battles_h
                        else []
                    )

                    discovered_gals_h: set[int] = set()
                    universe_scanned_cnt = 0
                    for hr in hot_rows:
                        g_i = int(hr[0])
                        s_i = int(hr[1])
                        if 1 <= g_i <= 14:
                            discovered_gals_h.add(g_i)
                            if 1 <= s_i <= 400 and float(hr[2] or 0.0) > 0.0:
                                universe_scanned_cnt += 1

                    active_galaxies_h = req_galaxies if req_galaxies else (sorted(discovered_gals_h) or [1])
                    active_gal_set_h = set(active_galaxies_h)

                    spacia_coords_h: set[str] = set()
                    coord_owner_h: dict[str, str] = {}
                    owner_display_h: dict[str, str] = {}
                    owner_sectors_all_h: dict[str, set[str]] = {}
                    active_sectors_h: dict[tuple[int, int], tuple[Any, ...]] = {}
                    explored_active_cnt = 0

                    for hr in hot_rows:
                        g_i = int(hr[0])
                        s_i = int(hr[1])
                        if not (1 <= g_i <= 14 and 1 <= s_i <= 400):
                            continue
                        skey_all = f"{g_i:02d}:{s_i:03d}"
                        last_ts_r = float(hr[2] or 0.0)
                        sp_raw = hr[14]
                        if sp_raw and sp_raw != "[]":
                            try:
                                for sc in json.loads(sp_raw):
                                    if sc:
                                        spacia_coords_h.add(sc)
                            except ValueError:
                                pass
                        co_raw = hr[13]
                        if co_raw and co_raw != "{}":
                            try:
                                co_dict = json.loads(co_raw)
                                if isinstance(co_dict, dict):
                                    for nc_k, ov in co_dict.items():
                                        if nc_k and ov and nc_k not in own_coords_hot and nc_k not in spacia_coords_h:
                                            coord_owner_h[nc_k] = ov
                                            ok = ov.lower()
                                            owner_display_h.setdefault(ok, ov)
                                            owner_sectors_all_h.setdefault(ok, set()).add(skey_all)
                            except ValueError:
                                pass
                        if g_i not in active_gal_set_h:
                            continue
                        if last_ts_r > 0.0:
                            explored_active_cnt += 1
                        owners_raw = hr[11]
                        opc_raw = hr[12]
                        owners_list_r: tuple[str, ...] = ()
                        opc_items: tuple[tuple[str, str, int], ...] = ()
                        if owners_raw and owners_raw != "[]":
                            try:
                                o_parsed = json.loads(owners_raw)
                                if isinstance(o_parsed, list):
                                    owners_list_r = tuple(str(x) for x in o_parsed)
                            except ValueError:
                                pass
                        if opc_raw and opc_raw != "{}":
                            try:
                                opc_parsed = json.loads(opc_raw)
                                if isinstance(opc_parsed, dict):
                                    opc_items = tuple(
                                        (str(k).lower(), str(k), int(v))
                                        for k, v in opc_parsed.items()
                                        if int(v) > 0
                                    )
                            except ValueError:
                                pass
                        active_sectors_h[(g_i, s_i)] = (
                            skey_all,
                            round(last_ts_r, 3),
                            game_data.cap_json_int(max(0, int(hr[3] or 0))),
                            game_data.cap_json_int(max(0, int(hr[4] or 0))),
                            game_data.cap_json_int(max(0, int(hr[5] or 0))),
                            game_data.cap_json_int(max(0, int(hr[6] or 0))),
                            game_data.cap_json_int(max(0, int(hr[7] or 0))),
                            game_data.cap_json_int(max(0, int(hr[8] or 0))),
                            game_data.cap_json_int(max(0, int(hr[9] or 0))),
                            game_data.cap_json_int(max(0, int(hr[10] or 0))),
                            owners_list_r,
                            opc_items,
                        )

                    battle_loot_by_coord_h: dict[str, float] = {}
                    battle_extra_owners_h: dict[tuple[int, int], dict[str, set[str]]] = {}
                    for br in battle_rows_h:
                        parsed_b = self._parse_norm_gsp_fast(br[0])
                        if parsed_b is None:
                            continue
                        nc_b, gb_i, sb_i, _ = parsed_b
                        def_name = _sanitize_text(br[1] or "").strip()
                        sum_sp = max(0.0, _safe_float(br[3], 0.0))
                        battle_loot_by_coord_h[nc_b] = battle_loot_by_coord_h.get(nc_b, 0.0) + sum_sp
                        if (
                            def_name
                            and def_name.lower() != "spacia"
                            and nc_b not in own_coords_hot
                            and nc_b not in spacia_coords_h
                            and nc_b not in coord_owner_h
                        ):
                            coord_owner_h[nc_b] = def_name
                            ok_b = def_name.lower()
                            owner_display_h.setdefault(ok_b, def_name)
                            if 1 <= gb_i <= 14 and 1 <= sb_i <= 400:
                                owner_sectors_all_h.setdefault(ok_b, set()).add(f"{gb_i:02d}:{sb_i:03d}")
                                if gb_i in active_gal_set_h:
                                    battle_extra_owners_h.setdefault((gb_i, sb_i), {}).setdefault(ok_b, set()).add(nc_b)

                    hot_base = {
                        "active_galaxies": [game_data.cap_json_int(g) for g in active_galaxies_h],
                        "active_gal_set": active_gal_set_h,
                        "explored_systems": game_data.cap_json_int(explored_active_cnt),
                        "universe_explored_systems": game_data.cap_json_int(universe_scanned_cnt),
                        "own_coords": own_coords_hot,
                        "spacia_coords": spacia_coords_h,
                        "coord_owner": coord_owner_h,
                        "owner_display_by_key": owner_display_h,
                        "owner_sectors_all": {k: sorted(v) for k, v in owner_sectors_all_h.items()},
                        "active_sectors": active_sectors_h,
                        "battle_extra_owners": battle_extra_owners_h,
                        "battle_loot_by_coord": battle_loot_by_coord_h,
                        "norm_raids": norm_raids_h,
                    }
                    self._galaxy_intel_base_ver = base_key
                    self._galaxy_intel_base_cache = hot_base

        if hot_base is not None:
            active_galaxies_h = hot_base["active_galaxies"]
            active_gal_set_h = hot_base["active_gal_set"]
            own_coords_h = hot_base["own_coords"]
            spacia_coords_h = hot_base["spacia_coords"]
            coord_owner_h = hot_base["coord_owner"]
            owner_display_h = hot_base["owner_display_by_key"]
            owner_sectors_all_h = hot_base["owner_sectors_all"]
            active_sectors_h = hot_base["active_sectors"]
            battle_extra_owners_h = hot_base["battle_extra_owners"]
            battle_loot_by_coord_h = hot_base["battle_loot_by_coord"]
            norm_raids_h = hot_base["norm_raids"]

            owner_agg_h: dict[str, dict[str, Any]] = {}
            sector_raid_agg_h: dict[tuple[int, int], dict[str, Any]] = {}
            raid_loot_by_coord_h: dict[str, float] = {}

            for rr in norm_raids_h:
                nc_r = rr["target_coords"]
                if not nc_r or nc_r in own_coords_h:
                    continue
                parsed_r = self._parse_norm_gsp_fast(nc_r)
                if parsed_r is None:
                    continue
                _, gr_i, sr_i, _ = parsed_r
                rts = _safe_float(rr["attacked_ts"], 0.0)
                age_r = max(0.0, eff_now - rts) if rts > 0.0 else 0.0
                decay = 0.25 + 0.75 * math.exp(-age_r / (7.0 * 86400.0))
                sp_eq = max(0.0, _safe_float(rr["loot_spice_eq"], 0.0))
                won = bool(rr["won"])
                raid_loot_by_coord_h[nc_r] = raid_loot_by_coord_h.get(nc_r, 0.0) + sp_eq

                owner_s = coord_owner_h.get(nc_r, "")
                if owner_s and nc_r not in spacia_coords_h and owner_s.lower() != "spacia":
                    okey = owner_s.lower()
                    disp = owner_display_h.get(okey, owner_s)
                    oa = owner_agg_h.get(okey)
                    if oa is None:
                        oa = {
                            "owner": disp,
                            "raid_count": 0,
                            "raid_wins": 0,
                            "raid_24h_count": 0,
                            "raid_7d_count": 0,
                            "recency_raid_score": 0.0,
                            "loot_spice_eq": 0.0,
                            "last_attacked_ts": 0.0,
                        }
                        owner_agg_h[okey] = oa
                    oa["raid_count"] += 1
                    if won:
                        oa["raid_wins"] += 1
                    if rts > 0.0 and age_r <= 86400.0:
                        oa["raid_24h_count"] += 1
                    if rts > 0.0 and age_r <= 7.0 * 86400.0:
                        oa["raid_7d_count"] += 1
                    oa["recency_raid_score"] += decay
                    oa["loot_spice_eq"] += sp_eq
                    if rts > oa["last_attacked_ts"]:
                        oa["last_attacked_ts"] = rts

                if gr_i in active_gal_set_h and 1 <= sr_i <= 400:
                    gsk_r = (gr_i, sr_i)
                    sa = sector_raid_agg_h.get(gsk_r)
                    if sa is None:
                        sa = {
                            "direct_raid_count": 0,
                            "direct_raid_wins": 0,
                            "direct_raid_24h_count": 0,
                            "direct_raid_7d_count": 0,
                            "direct_recency_raid_score": 0.0,
                            "direct_loot_spice_eq": 0.0,
                            "last_attacked_ts": 0.0,
                        }
                        sector_raid_agg_h[gsk_r] = sa
                    sa["direct_raid_count"] += 1
                    if won:
                        sa["direct_raid_wins"] += 1
                    if rts > 0.0 and age_r <= 86400.0:
                        sa["direct_raid_24h_count"] += 1
                    if rts > 0.0 and age_r <= 7.0 * 86400.0:
                        sa["direct_raid_7d_count"] += 1
                    sa["direct_recency_raid_score"] += decay
                    sa["direct_loot_spice_eq"] += sp_eq
                    if rts > sa["last_attacked_ts"]:
                        sa["last_attacked_ts"] = rts

            for nc_b, b_sp in battle_loot_by_coord_h.items():
                r_sp = raid_loot_by_coord_h.get(nc_b, 0.0)
                if b_sp <= r_sp:
                    continue
                extra_sp = b_sp - r_sp
                parsed_b = self._parse_norm_gsp_fast(nc_b)
                if parsed_b is not None:
                    gsk_b = (parsed_b[1], parsed_b[2])
                    if gsk_b in sector_raid_agg_h:
                        sector_raid_agg_h[gsk_b]["direct_loot_spice_eq"] += extra_sp
                owner_s = coord_owner_h.get(nc_b, "")
                if owner_s:
                    ok_b = owner_s.lower()
                    if ok_b in owner_agg_h:
                        owner_agg_h[ok_b]["loot_spice_eq"] += extra_sp

            all_gsks_h: set[tuple[int, int]] = set(active_sectors_h.keys())
            if sector_raid_agg_h:
                all_gsks_h.update(sector_raid_agg_h.keys())
            if battle_extra_owners_h:
                all_gsks_h.update(battle_extra_owners_h.keys())

            sectors_out_h: dict[str, dict[str, Any]] = {}
            has_owner_agg = bool(owner_agg_h)
            for gsk in sorted(all_gsks_h):
                gal_i, sys_i = gsk
                hs = active_sectors_h.get(gsk)
                if hs is not None:
                    (
                        skey,
                        last_ts,
                        scan_cnt,
                        occ_cnt,
                        target_cnt,
                        inact_cnt,
                        spacia_cnt,
                        debris_cnt,
                        free_cnt,
                        prime_cnt,
                        owners_tup,
                        opc_items,
                    ) = hs
                    scanned = last_ts > 0.0
                    age_s = round(max(0.0, eff_now - last_ts), 3) if scanned else None
                else:
                    skey = f"{gal_i:02d}:{sys_i:03d}"
                    last_ts = 0.0
                    scanned = False
                    age_s = None
                    scan_cnt = occ_cnt = target_cnt = inact_cnt = spacia_cnt = debris_cnt = free_cnt = prime_cnt = 0
                    owners_tup = ()
                    opc_items = ()

                ra = sector_raid_agg_h.get(gsk)
                if ra is not None:
                    d_raids = game_data.cap_json_int(int(ra["direct_raid_count"]))
                    d_wins = game_data.cap_json_int(int(ra["direct_raid_wins"]))
                    d_24h = game_data.cap_json_int(int(ra["direct_raid_24h_count"]))
                    d_7d = game_data.cap_json_int(int(ra["direct_raid_7d_count"]))
                    d_recency = round(float(ra["direct_recency_raid_score"]), 4)
                    d_loot = round(float(ra["direct_loot_spice_eq"]), 2)
                    last_atk_ts = round(float(ra["last_attacked_ts"]), 3)
                else:
                    d_raids = d_wins = d_24h = d_7d = 0
                    d_recency = d_loot = last_atk_ts = 0.0

                extra_bo = battle_extra_owners_h.get(gsk)
                if extra_bo:
                    merged_ok: dict[str, int] = {ok: cnt for ok, _disp, cnt in opc_items}
                    for ok_b, cset in extra_bo.items():
                        merged_ok[ok_b] = merged_ok.get(ok_b, 0) + len(cset)
                    owners_list_o: list[str] = []
                    owner_planet_counts_o: dict[str, int] = {}
                    freq_owners_out_o: list[dict[str, Any]] = []
                    affinity_raid_cnt_o = 0
                    for ok in sorted(merged_ok.keys()):
                        pcnt = merged_ok[ok]
                        if pcnt <= 0:
                            continue
                        disp = owner_display_h.get(ok, ok)
                        owners_list_o.append(disp)
                        owner_planet_counts_o[disp] = pcnt
                        if has_owner_agg:
                            oa = owner_agg_h.get(ok)
                            if oa is not None and int(oa["raid_count"]) > 0:
                                rcnt = int(oa["raid_count"])
                                affinity_raid_cnt_o += rcnt
                                freq_owners_out_o.append(
                                    {
                                        "owner": disp,
                                        "raid_count": game_data.cap_json_int(rcnt),
                                        "planets_in_sector": game_data.cap_json_int(pcnt),
                                        "loot_spice_eq": round(float(oa["loot_spice_eq"]), 2),
                                    }
                                )
                    if len(freq_owners_out_o) > 1:
                        freq_owners_out_o.sort(key=lambda item: (-int(item["raid_count"]), str(item["owner"])))
                else:
                    owners_list_o = list(owners_tup)
                    owner_planet_counts_o = {disp: cnt for _ok, disp, cnt in opc_items}
                    freq_owners_out_o = []
                    affinity_raid_cnt_o = 0
                    if has_owner_agg and opc_items:
                        for ok, disp, pcnt in opc_items:
                            oa = owner_agg_h.get(ok)
                            if oa is not None and int(oa["raid_count"]) > 0:
                                rcnt = int(oa["raid_count"])
                                affinity_raid_cnt_o += rcnt
                                freq_owners_out_o.append(
                                    {
                                        "owner": disp,
                                        "raid_count": game_data.cap_json_int(rcnt),
                                        "planets_in_sector": game_data.cap_json_int(pcnt),
                                        "loot_spice_eq": round(float(oa["loot_spice_eq"]), 2),
                                    }
                                )
                        if len(freq_owners_out_o) > 1:
                            freq_owners_out_o.sort(key=lambda item: (-int(item["raid_count"]), str(item["owner"])))

                sectors_out_h[skey] = {
                    "sector": skey,
                    "galaxy": game_data.cap_json_int(gal_i),
                    "system": game_data.cap_json_int(sys_i),
                    "scanned": scanned,
                    "last_scanned_ts": last_ts,
                    "age_seconds": age_s,
                    "scan_count": scan_cnt,
                    "occupied_count": occ_cnt,
                    "target_count": target_cnt,
                    "inactive_count": inact_cnt,
                    "spacia_count": spacia_cnt,
                    "debris_count": debris_cnt,
                    "free_count": free_cnt,
                    "prime_colony_free_count": prime_cnt,
                    "direct_raid_count": d_raids,
                    "direct_raid_wins": d_wins,
                    "direct_raid_24h_count": d_24h,
                    "direct_raid_7d_count": d_7d,
                    "direct_recency_raid_score": d_recency,
                    "direct_loot_spice_eq": d_loot,
                    "last_attacked_ts": last_atk_ts,
                    "owners": owners_list_o,
                    "owner_planet_counts": owner_planet_counts_o,
                    "frequent_owners": freq_owners_out_o,
                    "owner_affinity_raid_count": game_data.cap_json_int(affinity_raid_cnt_o),
                }

            sorted_owners_h = sorted(
                owner_agg_h.items(),
                key=lambda kv: (-int(kv[1]["raid_count"]), -float(kv[1]["loot_spice_eq"]), str(kv[1]["owner"])),
            )
            owner_attack_counts_h: dict[str, int] = {}
            owner_raid_stats_h: dict[str, dict[str, Any]] = {}
            for okey, oa in sorted_owners_h:
                disp = str(oa["owner"])
                rcnt = int(oa["raid_count"])
                owner_attack_counts_h[disp] = game_data.cap_json_int(rcnt)
                owner_raid_stats_h[disp] = {
                    "owner": disp,
                    "raid_count": game_data.cap_json_int(rcnt),
                    "raid_wins": game_data.cap_json_int(int(oa["raid_wins"])),
                    "raid_24h_count": game_data.cap_json_int(int(oa["raid_24h_count"])),
                    "raid_7d_count": game_data.cap_json_int(int(oa["raid_7d_count"])),
                    "recency_raid_score": round(float(oa["recency_raid_score"]), 4),
                    "loot_spice_eq": round(float(oa["loot_spice_eq"]), 2),
                    "last_attacked_ts": round(float(oa["last_attacked_ts"]), 3),
                    "known_sectors": list(owner_sectors_all_h.get(okey, ())),
                }

            explored_h = int(hot_base["explored_systems"])
            total_sys_h = len(active_galaxies_h) * 400
            unscanned_h = max(0, total_sys_h - explored_h)
            cov_pct_h = round((explored_h / total_sys_h) * 100.0, 2) if total_sys_h > 0 else 0.0
            univ_total_h = 14 * 400
            univ_exp_h = int(hot_base["universe_explored_systems"])
            univ_cov_h = round((univ_exp_h / univ_total_h) * 100.0, 2)
            return {
                "now_ts": eff_now,
                "galaxies": list(active_galaxies_h),
                "explored_systems": game_data.cap_json_int(explored_h),
                "unscanned_systems": game_data.cap_json_int(unscanned_h),
                "total_systems": game_data.cap_json_int(total_sys_h),
                "coverage_pct": cov_pct_h,
                "universe_explored_systems": game_data.cap_json_int(univ_exp_h),
                "universe_total_systems": game_data.cap_json_int(univ_total_h),
                "universe_coverage_pct": univ_cov_h,
                "owner_attack_counts": owner_attack_counts_h,
                "owner_raid_stats": owner_raid_stats_h,
                "sectors": sectors_out_h,
            }

        with self._lock:
            has_scans = DEFAULT_GAME_MIGRATION_RUNNER._table_exists(self._conn, "galaxy_scans")
            has_scan_hist = DEFAULT_GAME_MIGRATION_RUNNER._table_exists(self._conn, "galaxy_scan_history")
            has_slots = DEFAULT_GAME_MIGRATION_RUNNER._table_exists(self._conn, "galaxy_slots")
            has_spy = DEFAULT_GAME_MIGRATION_RUNNER._table_exists(self._conn, "spy_targets")
            has_raids = DEFAULT_GAME_MIGRATION_RUNNER._table_exists(self._conn, "raid_history")
            has_battles = DEFAULT_GAME_MIGRATION_RUNNER._table_exists(self._conn, "battle_reports")
            has_psnaps = DEFAULT_GAME_MIGRATION_RUNNER._table_exists(self._conn, "planet_snapshots")

            own_psnap_rows = (
                self._conn.execute("SELECT DISTINCT coords FROM planet_snapshots WHERE coords != ''").fetchall()
                if has_psnaps
                else []
            )

            scan_rows = (
                self._conn.execute(
                    """
                    SELECT galaxy, system, last_scanned_ts, occupied_count, target_count, free_count
                    FROM galaxy_scans
                    """
                ).fetchall()
                if has_scans
                else []
            )
            hist_rows = (
                self._conn.execute(
                    """
                    SELECT galaxy, system, COUNT(*) AS scan_count, MAX(ts) AS max_ts, MAX(debris_count) AS max_debris
                    FROM galaxy_scan_history
                    GROUP BY galaxy, system
                    """
                ).fetchall()
                if has_scan_hist
                else []
            )
            has_slot_cmax = has_slots and self._has_column_locked("galaxy_slots", "colonize_max_fields")
            slot_cols = (
                "coords, galaxy, system, position, owner, status_color, is_inactive, is_spacia, has_debris, colonize_max_fields"
                if has_slot_cmax
                else "coords, galaxy, system, position, owner, status_color, is_inactive, is_spacia, has_debris, NULL AS colonize_max_fields"
            )
            slot_rows = (
                self._conn.execute(f"SELECT {slot_cols} FROM galaxy_slots").fetchall()
                if has_slots
                else []
            )
            has_spy_cmax = has_spy and self._has_column_locked("spy_targets", "colonize_max_fields")
            spy_cols = (
                "coords, owner, status_color, is_inactive, is_spacia, has_debris, uninhabited_fields, colonize_max_fields, is_own_planet, merged_into"
                if has_spy_cmax
                else "coords, owner, status_color, is_inactive, is_spacia, has_debris, uninhabited_fields, NULL AS colonize_max_fields, is_own_planet, merged_into"
            )
            spy_rows = (
                self._conn.execute(f"SELECT {spy_cols} FROM spy_targets").fetchall()
                if has_spy
                else []
            )
            norm_raids = self._query_norm_raid_rows_locked() if has_raids else []
            battle_rows = (
                self._conn.execute(
                    """
                    SELECT target_coords, defender, MAX(loot_spice_eq) AS max_loot_sp, SUM(loot_spice_eq) AS sum_loot_sp
                    FROM battle_reports
                    WHERE target_coords != ''
                    GROUP BY target_coords, defender
                    """
                ).fetchall()
                if has_battles
                else []
            )

        discovered_gals: set[int] = set()
        for r in scan_rows:
            gi = _safe_int(r["galaxy"], 0)
            if 1 <= gi <= 14:
                discovered_gals.add(gi)
        for r in hist_rows:
            gi = _safe_int(r["galaxy"], 0)
            if 1 <= gi <= 14:
                discovered_gals.add(gi)
        for r in slot_rows:
            gi = _safe_int(r["galaxy"], 0)
            if 1 <= gi <= 14:
                discovered_gals.add(gi)

        active_galaxies = req_galaxies if req_galaxies else (sorted(discovered_gals) or [1])
        active_gal_set = set(active_galaxies)

        own_coords: set[str] = set()
        spacia_coords: set[str] = set()
        coord_owner: dict[str, str] = {}
        owner_display_by_key: dict[str, str] = {}
        owner_sectors_all: dict[str, set[str]] = {}
        sector_owner_coords: dict[tuple[int, int], dict[str, set[str]]] = {}
        sector_debris_coords: dict[tuple[int, int], set[str]] = {}
        sector_inact_coords: dict[tuple[int, int], set[str]] = {}
        sector_spacia_coords: dict[tuple[int, int], set[str]] = {}
        sector_free_coords: dict[tuple[int, int], set[str]] = {}
        sector_prime_coords: dict[tuple[int, int], set[str]] = {}
        sector_occ_coords: dict[tuple[int, int], set[str]] = {}

        def _parse_norm_gsp(raw_c: Any) -> Optional[tuple[str, int, int, int]]:
            nc = normalize_coords(_raw_text(raw_c, "").strip())
            if not nc:
                return None
            m_c = re.fullmatch(r"(\d{2}):(\d{3}):(\d{2})", nc, re.ASCII)
            if not m_c:
                return None
            return (nc, int(m_c.group(1)), int(m_c.group(2)), int(m_c.group(3)))

        for r in own_psnap_rows:
            parsed = _parse_norm_gsp(r["coords"])
            if parsed is not None:
                own_coords.add(parsed[0])

        for r in spy_rows:
            parsed = _parse_norm_gsp(r["coords"])
            if parsed is None:
                continue
            nc, _, _, _ = parsed
            if bool(r["is_own_planet"]):
                own_coords.add(nc)

        for r in slot_rows:
            parsed = _parse_norm_gsp(r["coords"])
            if parsed is None:
                continue
            nc, gal_i, sys_i, pos_i = parsed
            if not (1 <= gal_i <= 14 and 1 <= sys_i <= 400):
                continue
            scolor = _sanitize_text(r["status_color"] or "free").strip().lower()
            is_sp = bool(r["is_spacia"])
            owner_s = _sanitize_text(r["owner"] or "").strip()
            skey_all = f"{gal_i:02d}:{sys_i:03d}"
            if nc not in own_coords:
                if is_sp or owner_s.lower() == "spacia":
                    spacia_coords.add(nc)
                elif owner_s and scolor != "free":
                    coord_owner[nc] = owner_s
                    okey = owner_s.lower()
                    owner_display_by_key.setdefault(okey, owner_s)
                    owner_sectors_all.setdefault(okey, set()).add(skey_all)

            if gal_i not in active_gal_set:
                continue
            gsk = (gal_i, sys_i)
            if scolor != "free":
                sector_occ_coords.setdefault(gsk, set()).add(nc)
            if bool(r["has_debris"]):
                sector_debris_coords.setdefault(gsk, set()).add(nc)
            if nc in own_coords:
                continue
            if is_sp or owner_s.lower() == "spacia":
                sector_spacia_coords.setdefault(gsk, set()).add(nc)
            elif scolor == "yellow" or (bool(r["is_inactive"]) and scolor != "free"):
                sector_inact_coords.setdefault(gsk, set()).add(nc)
            if owner_s and scolor != "free" and not is_sp and owner_s.lower() != "spacia":
                okey = owner_s.lower()
                sector_owner_coords.setdefault(gsk, {}).setdefault(okey, set()).add(nc)
            if scolor == "free":
                sector_free_coords.setdefault(gsk, set()).add(nc)
                cmax = _safe_int(r["colonize_max_fields"], 0)
                if cmax >= 300 or (cmax == 0 and 7 <= pos_i <= 12):
                    sector_prime_coords.setdefault(gsk, set()).add(nc)

        for r in spy_rows:
            if _raw_text(r["merged_into"], "").strip():
                continue
            parsed = _parse_norm_gsp(r["coords"])
            if parsed is None:
                continue
            nc, gal_i, sys_i, pos_i = parsed
            if nc in own_coords or not (1 <= gal_i <= 14 and 1 <= sys_i <= 400):
                continue
            scolor = _sanitize_text(r["status_color"] or "yellow").strip().lower()
            is_sp = bool(r["is_spacia"])
            owner_s = _sanitize_text(r["owner"] or "").strip()
            skey_all = f"{gal_i:02d}:{sys_i:03d}"
            if is_sp or owner_s.lower() == "spacia":
                spacia_coords.add(nc)
            elif owner_s and scolor != "free":
                coord_owner[nc] = owner_s
                okey = owner_s.lower()
                owner_display_by_key[okey] = owner_s
                owner_sectors_all.setdefault(okey, set()).add(skey_all)

            if gal_i not in active_gal_set:
                continue
            gsk = (gal_i, sys_i)
            if scolor != "free":
                sector_occ_coords.setdefault(gsk, set()).add(nc)
            if bool(r["has_debris"]):
                sector_debris_coords.setdefault(gsk, set()).add(nc)
            if is_sp or owner_s.lower() == "spacia":
                sector_spacia_coords.setdefault(gsk, set()).add(nc)
            elif scolor == "yellow" or (bool(r["is_inactive"]) and scolor != "free"):
                sector_inact_coords.setdefault(gsk, set()).add(nc)
            if owner_s and scolor != "free" and not is_sp and owner_s.lower() != "spacia":
                okey = owner_s.lower()
                sector_owner_coords.setdefault(gsk, {}).setdefault(okey, set()).add(nc)
            if scolor == "free":
                sector_free_coords.setdefault(gsk, set()).add(nc)
                uf = _safe_int(r["uninhabited_fields"], 0)
                cmax = _safe_int(r["colonize_max_fields"], 0)
                if uf >= 300 or cmax >= 300 or (uf == 0 and cmax == 0 and 7 <= pos_i <= 12):
                    sector_prime_coords.setdefault(gsk, set()).add(nc)

        battle_loot_by_coord: dict[str, float] = {}
        for br in battle_rows:
            parsed = _parse_norm_gsp(br["target_coords"])
            if parsed is None:
                continue
            nc, gal_i, sys_i, _ = parsed
            def_name = _sanitize_text(br["defender"] or "").strip()
            sum_sp = max(0.0, _safe_float(br["sum_loot_sp"], 0.0))
            battle_loot_by_coord[nc] = battle_loot_by_coord.get(nc, 0.0) + sum_sp
            if (
                def_name
                and def_name.lower() != "spacia"
                and nc not in own_coords
                and nc not in spacia_coords
                and nc not in coord_owner
            ):
                coord_owner[nc] = def_name
                okey = def_name.lower()
                owner_display_by_key.setdefault(okey, def_name)
                if 1 <= gal_i <= 14 and 1 <= sys_i <= 400:
                    owner_sectors_all.setdefault(okey, set()).add(f"{gal_i:02d}:{sys_i:03d}")
                    if gal_i in active_gal_set:
                        sector_owner_coords.setdefault((gal_i, sys_i), {}).setdefault(okey, set()).add(nc)

        owner_agg: dict[str, dict[str, Any]] = {}
        sector_raid_agg: dict[tuple[int, int], dict[str, Any]] = {}
        raid_loot_by_coord: dict[str, float] = {}

        for rr in norm_raids:
            nc = rr["target_coords"]
            if not nc or nc in own_coords:
                continue
            parsed = _parse_norm_gsp(nc)
            if parsed is None:
                continue
            _, gal_i, sys_i, _ = parsed
            rts = _safe_float(rr["attacked_ts"], 0.0)
            age_r = max(0.0, eff_now - rts) if rts > 0.0 else 0.0
            decay = 0.25 + 0.75 * math.exp(-age_r / (7.0 * 86400.0))
            sp_eq = max(0.0, _safe_float(rr["loot_spice_eq"], 0.0))
            won = bool(rr["won"])
            raid_loot_by_coord[nc] = raid_loot_by_coord.get(nc, 0.0) + sp_eq

            owner_s = coord_owner.get(nc, "")
            if owner_s and nc not in spacia_coords and owner_s.lower() != "spacia":
                okey = owner_s.lower()
                disp = owner_display_by_key.get(okey, owner_s)
                oa = owner_agg.get(okey)
                if oa is None:
                    oa = {
                        "owner": disp,
                        "raid_count": 0,
                        "raid_wins": 0,
                        "raid_24h_count": 0,
                        "raid_7d_count": 0,
                        "recency_raid_score": 0.0,
                        "loot_spice_eq": 0.0,
                        "last_attacked_ts": 0.0,
                    }
                    owner_agg[okey] = oa
                oa["raid_count"] += 1
                if won:
                    oa["raid_wins"] += 1
                if rts > 0.0 and age_r <= 86400.0:
                    oa["raid_24h_count"] += 1
                if rts > 0.0 and age_r <= 7.0 * 86400.0:
                    oa["raid_7d_count"] += 1
                oa["recency_raid_score"] += decay
                oa["loot_spice_eq"] += sp_eq
                oa["last_attacked_ts"] = max(oa["last_attacked_ts"], rts)

            if gal_i in active_gal_set and 1 <= sys_i <= 400:
                gsk = (gal_i, sys_i)
                sa = sector_raid_agg.get(gsk)
                if sa is None:
                    sa = {
                        "direct_raid_count": 0,
                        "direct_raid_wins": 0,
                        "direct_raid_24h_count": 0,
                        "direct_raid_7d_count": 0,
                        "direct_recency_raid_score": 0.0,
                        "direct_loot_spice_eq": 0.0,
                        "last_attacked_ts": 0.0,
                    }
                    sector_raid_agg[gsk] = sa
                sa["direct_raid_count"] += 1
                if won:
                    sa["direct_raid_wins"] += 1
                if rts > 0.0 and age_r <= 86400.0:
                    sa["direct_raid_24h_count"] += 1
                if rts > 0.0 and age_r <= 7.0 * 86400.0:
                    sa["direct_raid_7d_count"] += 1
                sa["direct_recency_raid_score"] += decay
                sa["direct_loot_spice_eq"] += sp_eq
                sa["last_attacked_ts"] = max(sa["last_attacked_ts"], rts)

        for nc, b_sp in battle_loot_by_coord.items():
            if b_sp <= raid_loot_by_coord.get(nc, 0.0):
                continue
            extra_sp = b_sp - raid_loot_by_coord.get(nc, 0.0)
            parsed = _parse_norm_gsp(nc)
            if parsed is not None:
                _, gal_i, sys_i, _ = parsed
                if (gal_i, sys_i) in sector_raid_agg:
                    sector_raid_agg[(gal_i, sys_i)]["direct_loot_spice_eq"] += extra_sp
            owner_s = coord_owner.get(nc, "")
            if owner_s and owner_s.lower() in owner_agg:
                owner_agg[owner_s.lower()]["loot_spice_eq"] += extra_sp

        scan_meta: dict[tuple[int, int], dict[str, Any]] = {}
        universe_scanned_sectors: set[tuple[int, int]] = set()
        for r in scan_rows:
            gal_i = _safe_int(r["galaxy"], 0)
            sys_i = _safe_int(r["system"], 0)
            last_sc_r = _safe_float(r["last_scanned_ts"], 0.0)
            if 1 <= gal_i <= 14 and 1 <= sys_i <= 400 and last_sc_r > 0.0:
                universe_scanned_sectors.add((gal_i, sys_i))
            if gal_i not in active_gal_set or not (1 <= sys_i <= 400):
                continue
            scan_meta[(gal_i, sys_i)] = {
                "last_scanned_ts": last_sc_r,
                "scan_count": 1 if last_sc_r > 0.0 else 0,
                "occupied_count": max(0, _safe_int(r["occupied_count"], 0)),
                "target_count": max(0, _safe_int(r["target_count"], 0)),
                "free_count": max(0, _safe_int(r["free_count"], 0)),
                "hist_debris_max": 0,
            }
        for r in hist_rows:
            gal_i = _safe_int(r["galaxy"], 0)
            sys_i = _safe_int(r["system"], 0)
            max_ts = _safe_float(r["max_ts"], 0.0)
            if 1 <= gal_i <= 14 and 1 <= sys_i <= 400 and max_ts > 0.0:
                universe_scanned_sectors.add((gal_i, sys_i))
            if gal_i not in active_gal_set or not (1 <= sys_i <= 400):
                continue
            gsk = (gal_i, sys_i)
            sc_cnt = max(0, _safe_int(r["scan_count"], 0))
            max_deb = max(0, _safe_int(r["max_debris"], 0))
            cur_sm = scan_meta.get(gsk)
            if cur_sm is None:
                scan_meta[gsk] = {
                    "last_scanned_ts": max_ts,
                    "scan_count": sc_cnt,
                    "occupied_count": 0,
                    "target_count": 0,
                    "free_count": 0,
                    "hist_debris_max": max_deb,
                }
            else:
                cur_sm["last_scanned_ts"] = max(cur_sm["last_scanned_ts"], max_ts)
                cur_sm["scan_count"] = max(cur_sm["scan_count"], sc_cnt)
                cur_sm["hist_debris_max"] = max(cur_sm["hist_debris_max"], max_deb)

        explored_systems = sum(
            1 for sm in scan_meta.values() if _safe_float(sm.get("last_scanned_ts"), 0.0) > 0.0
        )
        total_systems = len(active_galaxies) * 400
        unscanned_systems = max(0, total_systems - explored_systems)
        coverage_pct = (
            round((explored_systems / total_systems) * 100.0, 2) if total_systems > 0 else 0.0
        )
        universe_total_systems = 14 * 400
        universe_explored_systems = len(universe_scanned_sectors)
        universe_coverage_pct = round(
            (universe_explored_systems / universe_total_systems) * 100.0,
            2,
        )

        all_sector_keys: set[tuple[int, int]] = set()
        all_sector_keys.update(scan_meta.keys())
        all_sector_keys.update(sector_raid_agg.keys())
        all_sector_keys.update(sector_owner_coords.keys())
        all_sector_keys.update(sector_debris_coords.keys())
        all_sector_keys.update(sector_inact_coords.keys())
        all_sector_keys.update(sector_spacia_coords.keys())
        all_sector_keys.update(sector_free_coords.keys())

        sectors_out: dict[str, dict[str, Any]] = {}
        for gal_i, sys_i in sorted(all_sector_keys):
            skey = f"{gal_i:02d}:{sys_i:03d}"
            sm = scan_meta.get((gal_i, sys_i))
            last_ts = _safe_float(sm["last_scanned_ts"], 0.0) if sm else 0.0
            scanned = last_ts > 0.0
            age_s = round(max(0.0, eff_now - last_ts), 3) if scanned else None
            scan_cnt = int(sm["scan_count"]) if sm else 0

            occ_cnt = max(
                int(sm["occupied_count"]) if sm else 0,
                len(sector_occ_coords.get((gal_i, sys_i), ())),
            )
            inact_cnt = len(sector_inact_coords.get((gal_i, sys_i), ()))
            spacia_cnt = len(sector_spacia_coords.get((gal_i, sys_i), ()))
            target_cnt = max(
                int(sm["target_count"]) if sm else 0,
                inact_cnt + spacia_cnt,
            )
            debris_cnt = len(sector_debris_coords.get((gal_i, sys_i), ()))
            free_cnt = max(
                int(sm["free_count"]) if sm else 0,
                len(sector_free_coords.get((gal_i, sys_i), ())),
            )
            prime_cnt = len(sector_prime_coords.get((gal_i, sys_i), ()))

            ra = sector_raid_agg.get((gal_i, sys_i))
            d_raids = int(ra["direct_raid_count"]) if ra else 0
            d_wins = int(ra["direct_raid_wins"]) if ra else 0
            d_24h = int(ra["direct_raid_24h_count"]) if ra else 0
            d_7d = int(ra["direct_raid_7d_count"]) if ra else 0
            d_recency = round(float(ra["direct_recency_raid_score"]), 4) if ra else 0.0
            d_loot = round(float(ra["direct_loot_spice_eq"]), 2) if ra else 0.0
            last_atk_ts = round(float(ra["last_attacked_ts"]), 3) if ra else 0.0

            omap = sector_owner_coords.get((gal_i, sys_i), {})
            owner_planet_counts: dict[str, int] = {}
            owners_list: list[str] = []
            freq_owners_out: list[dict[str, Any]] = []
            affinity_raid_cnt = 0
            for okey in sorted(omap.keys()):
                disp = owner_display_by_key.get(okey, okey)
                pcnt = len(omap[okey])
                if pcnt <= 0:
                    continue
                owner_planet_counts[disp] = pcnt
                owners_list.append(disp)
                oa = owner_agg.get(okey)
                if oa is not None and int(oa["raid_count"]) > 0:
                    rcnt = int(oa["raid_count"])
                    affinity_raid_cnt += rcnt
                    freq_owners_out.append(
                        {
                            "owner": disp,
                            "raid_count": game_data.cap_json_int(rcnt),
                            "planets_in_sector": game_data.cap_json_int(pcnt),
                            "loot_spice_eq": round(float(oa["loot_spice_eq"]), 2),
                        }
                    )
            freq_owners_out.sort(key=lambda item: (-int(item["raid_count"]), str(item["owner"])))

            sectors_out[skey] = {
                "sector": skey,
                "galaxy": game_data.cap_json_int(gal_i),
                "system": game_data.cap_json_int(sys_i),
                "scanned": scanned,
                "last_scanned_ts": round(last_ts, 3),
                "age_seconds": age_s,
                "scan_count": game_data.cap_json_int(scan_cnt),
                "occupied_count": game_data.cap_json_int(occ_cnt),
                "target_count": game_data.cap_json_int(target_cnt),
                "inactive_count": game_data.cap_json_int(inact_cnt),
                "spacia_count": game_data.cap_json_int(spacia_cnt),
                "debris_count": game_data.cap_json_int(debris_cnt),
                "free_count": game_data.cap_json_int(free_cnt),
                "prime_colony_free_count": game_data.cap_json_int(prime_cnt),
                "direct_raid_count": game_data.cap_json_int(d_raids),
                "direct_raid_wins": game_data.cap_json_int(d_wins),
                "direct_raid_24h_count": game_data.cap_json_int(d_24h),
                "direct_raid_7d_count": game_data.cap_json_int(d_7d),
                "direct_recency_raid_score": d_recency,
                "direct_loot_spice_eq": d_loot,
                "last_attacked_ts": last_atk_ts,
                "owners": owners_list,
                "owner_planet_counts": owner_planet_counts,
                "frequent_owners": freq_owners_out,
                "owner_affinity_raid_count": game_data.cap_json_int(affinity_raid_cnt),
            }

        sorted_owners = sorted(
            owner_agg.items(),
            key=lambda kv: (-int(kv[1]["raid_count"]), -float(kv[1]["loot_spice_eq"]), str(kv[1]["owner"])),
        )
        owner_attack_counts: dict[str, int] = {}
        owner_raid_stats_out: dict[str, dict[str, Any]] = {}
        for okey, oa in sorted_owners:
            disp = str(oa["owner"])
            rcnt = int(oa["raid_count"])
            owner_attack_counts[disp] = game_data.cap_json_int(rcnt)
            owner_raid_stats_out[disp] = {
                "owner": disp,
                "raid_count": game_data.cap_json_int(rcnt),
                "raid_wins": game_data.cap_json_int(int(oa["raid_wins"])),
                "raid_24h_count": game_data.cap_json_int(int(oa["raid_24h_count"])),
                "raid_7d_count": game_data.cap_json_int(int(oa["raid_7d_count"])),
                "recency_raid_score": round(float(oa["recency_raid_score"]), 4),
                "loot_spice_eq": round(float(oa["loot_spice_eq"]), 2),
                "last_attacked_ts": round(float(oa["last_attacked_ts"]), 3),
                "known_sectors": sorted(owner_sectors_all.get(okey, ())),
            }

        return _sanitize_obj(
            {
                "now_ts": eff_now,
                "galaxies": [game_data.cap_json_int(g) for g in active_galaxies],
                "explored_systems": game_data.cap_json_int(explored_systems),
                "unscanned_systems": game_data.cap_json_int(unscanned_systems),
                "total_systems": game_data.cap_json_int(total_systems),
                "coverage_pct": coverage_pct,
                "universe_explored_systems": game_data.cap_json_int(universe_explored_systems),
                "universe_total_systems": game_data.cap_json_int(universe_total_systems),
                "universe_coverage_pct": universe_coverage_pct,
                "owner_attack_counts": owner_attack_counts,
                "owner_raid_stats": owner_raid_stats_out,
                "sectors": sectors_out,
            }
        )

    def get_last_throttle_timestamps(self, *, building_id: int = 2) -> dict[int, float]:
        with self._lock:
            rows = self._conn.execute(
                """
                SELECT planet_id, MAX(ts) AS last_ts
                FROM action_history
                WHERE kind = 'THROTTLE_BUILDING'
                  AND executed = 1
                  AND dry_run = 0
                  AND planet_id IS NOT NULL
                  AND (target_id = ? OR target_id IS NULL)
                GROUP BY planet_id
                """,
                (_sql_int(building_id, 2),),
            ).fetchall()
        out: dict[int, float] = {}
        for r in rows:
            pid = game_data.cap_json_int(_safe_int(r["planet_id"], 0))
            if pid <= 0:
                continue
            out[pid] = _safe_float(r["last_ts"], 0.0)
        return out

    @staticmethod
    def _row_to_spy_target_dict(r: sqlite3.Row) -> dict[str, Any]:
        spied_ts = _safe_float(r["last_spied_ts"], 0.0)
        raw_c = _raw_text(r["coords"], "").strip()
        norm_c = normalize_coords(raw_c) if raw_c else ""
        pts = _safe_optional_int(r["points"])
        row_keys = set(r.keys())
        has_debris = bool(r["has_debris"]) if "has_debris" in row_keys else False
        blocked_until = _safe_float(r["blocked_until_ts"], 0.0) if "blocked_until_ts" in row_keys else 0.0
        block_reason = _sanitize_text(r["block_reason"]) if ("block_reason" in row_keys and r["block_reason"] is not None) else ""
        bash_hits = game_data.cap_json_int(max(0, _safe_int(r["bash_hits"], 0))) if "bash_hits" in row_keys else 0
        last_blocked_ts = _safe_float(r["last_blocked_ts"], 0.0) if "last_blocked_ts" in row_keys else 0.0
        uninhabited_fields = (
            game_data.cap_json_int(_safe_int(r["uninhabited_fields"], 0))
            if ("uninhabited_fields" in row_keys and r["uninhabited_fields"] is not None)
            else None
        )
        colonize_min_fields = (
            game_data.cap_json_int(_safe_int(r["colonize_min_fields"], 0))
            if ("colonize_min_fields" in row_keys and r["colonize_min_fields"] is not None)
            else None
        )
        colonize_max_fields = (
            game_data.cap_json_int(_safe_int(r["colonize_max_fields"], 0))
            if ("colonize_max_fields" in row_keys and r["colonize_max_fields"] is not None)
            else None
        )
        temperature = (
            game_data.cap_json_int(_safe_int(r["temperature"], 0))
            if ("temperature" in row_keys and r["temperature"] is not None)
            else None
        )
        spy_count = game_data.cap_json_int(max(0, _safe_int(r["spy_count"], 0))) if "spy_count" in row_keys else 0
        return _sanitize_obj(
            {
                "coords": _sanitize_text(norm_c) if norm_c else "",
                "owner": _sanitize_text(r["owner"]) if r["owner"] is not None else None,
                "planet_name": _sanitize_text(r["planet_name"]) if r["planet_name"] is not None else None,
                "status_color": _sanitize_text(r["status_color"]) if r["status_color"] is not None else None,
                "is_inactive": bool(r["is_inactive"]),
                "is_spacia": bool(r["is_spacia"]),
                "has_debris": has_debris,
                "points": game_data.cap_json_int(pts) if pts is not None else None,
                "resources": {
                    "pig_iron": game_data.cap_json_int(max(0, _safe_int(r["pig_iron"], 0))),
                    "metal": game_data.cap_json_int(max(0, _safe_int(r["metal"], 0))),
                    "kryptonite": game_data.cap_json_int(max(0, _safe_int(r["kryptonite"], 0))),
                    "spice": game_data.cap_json_int(max(0, _safe_int(r["spice"], 0))),
                },
                "fleet": _safe_json_dict(r["fleet_json"]),
                "defense": _safe_json_dict(r["defense_json"]),
                "dug_in": bool(r["dug_in"]),
                "last_scanned_ts": _safe_float(r["last_scanned_ts"], 0.0),
                "last_spied_ts": spied_ts,
                "has_spy_intel": spied_ts > 0.0,
                "blocked_until_ts": blocked_until,
                "block_reason": block_reason if blocked_until > 0.0 else "",
                "bash_hits": bash_hits,
                "last_blocked_ts": last_blocked_ts,
                "uninhabited_fields": uninhabited_fields,
                "colonize_min_fields": colonize_min_fields,
                "colonize_max_fields": colonize_max_fields,
                "temperature": temperature,
                "spy_count": spy_count,
            }
        )

    def get_spy_target_by_coords(self, coords: str) -> Optional[dict[str, Any]]:
        norm_c, cands, _ = _coord_query_candidates(coords)
        if not norm_c:
            return None
        with self._lock:
            placeholders = ", ".join("?" for _ in cands)
            row = self._conn.execute(
                f"""
                SELECT *
                FROM spy_targets
                WHERE coords IN ({placeholders}) AND merged_into = '' AND is_own_planet = 0
                ORDER BY (last_spied_ts > 0) DESC, last_scanned_ts DESC
                LIMIT 1
                """,
                cands,
            ).fetchone()
            if row is None:
                all_rows = self._conn.execute(
                    """
                    SELECT *
                    FROM spy_targets
                    WHERE merged_into = '' AND is_own_planet = 0
                    ORDER BY (last_spied_ts > 0) DESC, last_scanned_ts DESC
                    """
                ).fetchall()
                row = next(
                    (
                        r
                        for r in all_rows
                        if normalize_coords(_raw_text(r["coords"], "")) == norm_c
                    ),
                    None,
                )
        return self._row_to_spy_target_dict(row) if row is not None else None

    def _repair_spy_targets_locked(self) -> None:
        if not DEFAULT_GAME_MIGRATION_RUNNER._table_exists(self._conn, "spy_targets"):
            return
        changes_before = int(self._conn.total_changes)
        self._conn.execute(
            """
            UPDATE spy_targets
            SET status_color = 'free',
                is_inactive = 0
            WHERE owner = ''
              AND planet_name = ''
              AND is_spacia = 0
              AND status_color = 'yellow'
            """
        )
        self._conn.execute(
            """
            UPDATE spy_targets
            SET blocked_until_ts = 0.0,
                block_reason = ''
            WHERE status_color = 'free'
              AND owner = ''
              AND planet_name = ''
              AND is_spacia = 0
              AND is_own_planet = 0
              AND (
                  LOWER(block_reason) LIKE '%can''t be attacked%'
                  OR LOWER(block_reason) LIKE '%cannot be attacked%'
                  OR LOWER(block_reason) LIKE '%can not be attacked%'
              )
            """
        )
        if DEFAULT_GAME_MIGRATION_RUNNER._table_exists(self._conn, "spy_reports"):
            has_galaxy_slots = DEFAULT_GAME_MIGRATION_RUNNER._table_exists(self._conn, "galaxy_slots")
            poisoned_coords_rows = self._conn.execute(
                """
                SELECT DISTINCT st.coords
                FROM spy_targets st
                JOIN spy_reports sr ON sr.target_coords = st.coords
                WHERE st.is_own_planet = 0
                  AND (
                      LOWER(sr.raw_text) LIKE '%fleet return%'
                      OR LOWER(sr.raw_text) LIKE '%flottenrückkehr%'
                      OR LOWER(sr.raw_text) LIKE '%flottenrueckkehr%'
                      OR LOWER(sr.raw_text) LIKE '%returning from duty%'
                      OR LOWER(sr.raw_text) LIKE '%were unloaded%'
                      OR LOWER(sr.raw_text) LIKE '%the assignment was to:%'
                  )
                """
            ).fetchall()
            for p_row in poisoned_coords_rows:
                p_coords = _raw_text(p_row["coords"], "").strip()
                if not p_coords:
                    continue
                rep_rows = self._conn.execute(
                    """
                    SELECT ts, owner, planet_name, is_spacia,
                           pig_iron, metal, kryptonite, spice,
                           fleet_json, defense_json, dug_in, raw_text
                    FROM spy_reports
                    WHERE target_coords = ?
                    ORDER BY ts DESC, id DESC
                    """,
                    (p_coords,),
                ).fetchall()
                if not rep_rows or not is_non_spy_notice_text(_raw_text(rep_rows[0]["raw_text"], "")):
                    continue
                valid_rep = next(
                    (rr for rr in rep_rows if not is_non_spy_notice_text(_raw_text(rr["raw_text"], ""))),
                    None,
                )
                gs_row = (
                    self._conn.execute(
                        """
                        SELECT owner, planet_name, status_color, is_inactive, is_spacia, has_debris, last_scanned_ts
                        FROM galaxy_slots
                        WHERE coords = ?
                        """,
                        (p_coords,),
                    ).fetchone()
                    if has_galaxy_slots
                    else None
                )
                if valid_rep is not None:
                    v_owner = _sanitize_text(gs_row["owner"]) if gs_row is not None else _sanitize_text(valid_rep["owner"])
                    v_pname = _sanitize_text(gs_row["planet_name"]) if gs_row is not None else _sanitize_text(valid_rep["planet_name"])
                    v_is_spacia = int(bool(gs_row["is_spacia"])) if gs_row is not None else int(bool(valid_rep["is_spacia"]))
                    v_status = _sanitize_text(gs_row["status_color"]) if gs_row is not None else ("yellow" if v_is_spacia else "light_red")
                    v_inactive = int(bool(gs_row["is_inactive"])) if gs_row is not None else (1 if v_is_spacia or v_status == "yellow" else 0)
                    v_debris = int(bool(gs_row["has_debris"])) if gs_row is not None else 0
                    v_scanned = max(
                        _safe_float(gs_row["last_scanned_ts"], 0.0) if gs_row is not None else 0.0,
                        _safe_float(valid_rep["ts"], 0.0),
                    )
                    self._conn.execute(
                        """
                        UPDATE spy_targets
                        SET owner = ?,
                            planet_name = ?,
                            status_color = ?,
                            is_inactive = ?,
                            is_spacia = ?,
                            has_debris = ?,
                            pig_iron = ?,
                            metal = ?,
                            kryptonite = ?,
                            spice = ?,
                            fleet_json = ?,
                            defense_json = ?,
                            dug_in = ?,
                            last_scanned_ts = ?,
                            last_spied_ts = ?
                        WHERE coords = ?
                        """,
                        (
                            v_owner,
                            v_pname,
                            v_status,
                            v_inactive,
                            v_is_spacia,
                            v_debris,
                            _sql_int(valid_rep["pig_iron"], 0),
                            _sql_int(valid_rep["metal"], 0),
                            _sql_int(valid_rep["kryptonite"], 0),
                            _sql_int(valid_rep["spice"], 0),
                            _raw_text(valid_rep["fleet_json"], "{}") or "{}",
                            _raw_text(valid_rep["defense_json"], "{}") or "{}",
                            int(bool(valid_rep["dug_in"])) if valid_rep["dug_in"] is not None else 0,
                            v_scanned,
                            _safe_float(valid_rep["ts"], 0.0),
                            p_coords,
                        ),
                    )
                else:
                    r_owner = _sanitize_text(gs_row["owner"]) if gs_row is not None else ""
                    r_pname = _sanitize_text(gs_row["planet_name"]) if gs_row is not None else ""
                    r_status = _sanitize_text(gs_row["status_color"]) if gs_row is not None else "free"
                    r_inactive = int(bool(gs_row["is_inactive"])) if gs_row is not None else 0
                    r_spacia = int(bool(gs_row["is_spacia"])) if gs_row is not None else 0
                    r_debris = int(bool(gs_row["has_debris"])) if gs_row is not None else 0
                    r_scanned = _safe_float(gs_row["last_scanned_ts"], 0.0) if gs_row is not None else 0.0
                    clear_blk = r_status == "free"
                    self._conn.execute(
                        """
                        UPDATE spy_targets
                        SET owner = ?,
                            planet_name = ?,
                            status_color = ?,
                            is_inactive = ?,
                            is_spacia = ?,
                            has_debris = ?,
                            pig_iron = 0,
                            metal = 0,
                            kryptonite = 0,
                            spice = 0,
                            fleet_json = '{}',
                            defense_json = '{}',
                            dug_in = 0,
                            last_scanned_ts = CASE WHEN ? > 0 THEN ? ELSE last_scanned_ts END,
                            last_spied_ts = 0.0,
                            blocked_until_ts = CASE WHEN ? = 1 THEN 0.0 ELSE blocked_until_ts END,
                            block_reason = CASE WHEN ? = 1 THEN '' ELSE block_reason END
                        WHERE coords = ?
                        """,
                        (
                            r_owner,
                            r_pname,
                            r_status,
                            r_inactive,
                            r_spacia,
                            r_debris,
                            r_scanned,
                            r_scanned,
                            1 if clear_blk else 0,
                            1 if clear_blk else 0,
                            p_coords,
                        ),
                    )
        if DEFAULT_GAME_MIGRATION_RUNNER._table_exists(self._conn, "galaxy_slots"):
            self._conn.execute(
                """
                UPDATE spy_targets
                SET owner = COALESCE((SELECT gs.owner FROM galaxy_slots gs WHERE gs.coords = spy_targets.coords), spy_targets.owner),
                    planet_name = COALESCE((SELECT gs.planet_name FROM galaxy_slots gs WHERE gs.coords = spy_targets.coords), spy_targets.planet_name),
                    status_color = (SELECT gs.status_color FROM galaxy_slots gs WHERE gs.coords = spy_targets.coords),
                    is_inactive = COALESCE((SELECT gs.is_inactive FROM galaxy_slots gs WHERE gs.coords = spy_targets.coords), 0),
                    is_spacia = COALESCE((SELECT gs.is_spacia FROM galaxy_slots gs WHERE gs.coords = spy_targets.coords), 0),
                    has_debris = COALESCE((SELECT gs.has_debris FROM galaxy_slots gs WHERE gs.coords = spy_targets.coords), 0)
                WHERE is_own_planet = 0
                  AND EXISTS (
                      SELECT 1
                      FROM galaxy_slots gs
                      WHERE gs.coords = spy_targets.coords
                        AND gs.last_scanned_ts >= spy_targets.last_scanned_ts
                        AND gs.status_color IN ('free', 'blue', 'green', 'white', 'dark_red')
                  )
                """
            )
        if int(self._conn.total_changes) > changes_before and (
            DEFAULT_GAME_MIGRATION_RUNNER._table_exists(self._conn, "galaxy_sector_hot")
            or DEFAULT_GAME_MIGRATION_RUNNER._table_exists(self._conn, "colony_candidates_hot")
        ):
            backfill_v012_hot_tables(self._conn, time.time())
        self._conn.commit()

    def get_spy_targets(
        self,
        *,
        limit: int = 50,
        include_own: bool = False,
    ) -> list[dict[str, Any]]:
        own_clause = "" if include_own else "AND is_own_planet = 0"
        with self._lock:
            cmax_clause = "AND colonize_max_fields IS NULL" if self._has_column_locked("spy_targets", "colonize_max_fields") else ""
            own_filter = own_clause if self._has_column_locked("spy_targets", "is_own_planet") else ""
            merged_filter = "merged_into = ''" if self._has_column_locked("spy_targets", "merged_into") else "1=1"
            uf_clause = "AND uninhabited_fields IS NULL" if self._has_column_locked("spy_targets", "uninhabited_fields") else ""
            rows = self._conn.execute(
                f"""
                SELECT *
                FROM spy_targets
                WHERE {merged_filter} {own_filter}
                  AND NOT (
                      status_color = 'free'
                      AND owner = ''
                      AND is_spacia = 0
                      AND has_debris = 0
                      AND last_spied_ts <= 0
                      {uf_clause}
                      {cmax_clause}
                  )
                ORDER BY (is_inactive + is_spacia + has_debris) DESC, (last_spied_ts > 0) DESC, last_scanned_ts DESC
                LIMIT ?
                """,
                (max(1, _sql_int(limit, 50)),),
            ).fetchall()
        out = [self._row_to_spy_target_dict(r) for r in rows]
        debris_coords = [t["coords"] for t in out if t.get("has_debris") and t.get("coords")]
        if debris_coords:
            debris_lookup: dict[str, tuple[int, int]] = {}
            with self._lock:
                placeholders = ",".join("?" for _ in debris_coords)
                try:
                    b_rows = self._conn.execute(
                        f"""
                        SELECT target_coords, debris_pig_iron, debris_metal, ts
                        FROM battle_reports
                        WHERE target_coords IN ({placeholders})
                          AND (debris_pig_iron > 0 OR debris_metal > 0)
                        ORDER BY ts DESC, id DESC
                        """,
                        tuple(debris_coords),
                    ).fetchall()
                    for br in b_rows:
                        nc = normalize_coords(_raw_text(br["target_coords"], ""))
                        if nc and nc not in debris_lookup:
                            dp = game_data.cap_json_int(max(0, _safe_int(br["debris_pig_iron"], 0)))
                            dm = game_data.cap_json_int(max(0, _safe_int(br["debris_metal"], 0)))
                            if dp + dm > 0:
                                debris_lookup[nc] = (dp, dm)
                except sqlite3.Error:
                    pass
                missing_deb = [c for c in debris_coords if c not in debris_lookup]
                if missing_deb:
                    ph2 = ",".join("?" for _ in missing_deb)
                    try:
                        r_rows = self._conn.execute(
                            f"""
                            SELECT target_coords, debris_avail_pig, debris_avail_metal,
                                   harvested_pig_iron, harvested_metal, ts
                            FROM recycle_reports
                            WHERE target_coords IN ({ph2})
                            ORDER BY ts DESC, id DESC
                            """,
                            tuple(missing_deb),
                        ).fetchall()
                        for rr in r_rows:
                            nc = normalize_coords(_raw_text(rr["target_coords"], ""))
                            if nc and nc not in debris_lookup:
                                dp = max(0, _safe_int(rr["debris_avail_pig"], 0) - _safe_int(rr["harvested_pig_iron"], 0))
                                dm = max(0, _safe_int(rr["debris_avail_metal"], 0) - _safe_int(rr["harvested_metal"], 0))
                                if dp + dm > 0:
                                    debris_lookup[nc] = (game_data.cap_json_int(dp), game_data.cap_json_int(dm))
                    except sqlite3.Error:
                        pass
            for t in out:
                if t.get("has_debris") and t.get("coords") in debris_lookup:
                    dp, dm = debris_lookup[t["coords"]]
                    t["debris_pig_iron"] = dp
                    t["debris_metal"] = dm
                    t["debris_total"] = game_data.cap_json_int(dp + dm)
        return out

    def get_spy_reports(
        self,
        *,
        target_coords: Optional[str] = None,
        coords: Optional[str] = None,
        limit: int = 200,
    ) -> list[dict[str, Any]]:
        eff_coords = target_coords if target_coords is not None else coords
        lim = max(1, _sql_int(limit, 200))
        with self._lock:
            if eff_coords is not None:
                norm_c = normalize_coords(_raw_text(eff_coords, "").strip())
                rows = self._conn.execute(
                    """
                    SELECT id, ts, target_coords, owner, planet_name, is_spacia,
                           pig_iron, metal, kryptonite, spice, loot_spice_eq,
                           buildings_json, fleet_json, defense_json, researches_json,
                           dug_in, uninhabited_fields, server_msg_id, msg_date_text, raw_text
                    FROM spy_reports
                    WHERE target_coords = ?
                    ORDER BY ts DESC, id DESC
                    LIMIT ?
                    """,
                    (norm_c, lim),
                ).fetchall()
            else:
                rows = self._conn.execute(
                    """
                    SELECT id, ts, target_coords, owner, planet_name, is_spacia,
                           pig_iron, metal, kryptonite, spice, loot_spice_eq,
                           buildings_json, fleet_json, defense_json, researches_json,
                           dug_in, uninhabited_fields, server_msg_id, msg_date_text, raw_text
                    FROM spy_reports
                    ORDER BY ts DESC, id DESC
                    LIMIT ?
                    """,
                    (lim,),
                ).fetchall()
        out: list[dict[str, Any]] = []
        for r in rows:
            raw_r = _sanitize_text(r["raw_text"]) if r["raw_text"] is not None else ""
            if raw_r and is_non_spy_notice_text(raw_r):
                continue
            uf = _safe_optional_int(r["uninhabited_fields"])
            dug = _safe_optional_int(r["dug_in"])
            norm_tc = normalize_coords(_raw_text(r["target_coords"], ""))
            out.append(
                _sanitize_obj(
                    {
                        "id": game_data.cap_json_int(_safe_int(r["id"], 0)),
                        "ts": _safe_float(r["ts"], 0.0),
                        "coords": norm_tc,
                        "target_coords": norm_tc,
                        "owner": _sanitize_text(r["owner"]) if r["owner"] is not None else "",
                        "planet_name": _sanitize_text(r["planet_name"]) if r["planet_name"] is not None else "",
                        "is_spacia": bool(r["is_spacia"]),
                        "resources": {
                            "pig_iron": game_data.cap_json_int(max(0, _safe_int(r["pig_iron"], 0))),
                            "metal": game_data.cap_json_int(max(0, _safe_int(r["metal"], 0))),
                            "kryptonite": game_data.cap_json_int(max(0, _safe_int(r["kryptonite"], 0))),
                            "spice": game_data.cap_json_int(max(0, _safe_int(r["spice"], 0))),
                        },
                        "loot_spice_eq": _safe_float(r["loot_spice_eq"], 0.0),
                        "buildings": _safe_json_dict(r["buildings_json"]),
                        "fleet": _safe_json_dict(r["fleet_json"]),
                        "defense": _safe_json_dict(r["defense_json"]),
                        "researches": _safe_json_dict(r["researches_json"]),
                        "dug_in": bool(dug) if dug is not None else None,
                        "uninhabited_fields": game_data.cap_json_int(uf) if uf is not None else None,
                        "server_msg_id": _sanitize_text(r["server_msg_id"]) if r["server_msg_id"] is not None else "",
                        "msg_date_text": _sanitize_text(r["msg_date_text"]) if r["msg_date_text"] is not None else "",
                        "raw_text": _sanitize_text(r["raw_text"]) if r["raw_text"] is not None else "",
                    }
                )
            )
        return out

    def record_game_messages(
        self,
        messages: Sequence[InboxMessage],
        *,
        ts: Optional[float] = None,
    ) -> tuple[list[str], list[InboxMessage]]:
        """Persist every inbox message into append-only `game_messages`.

        Returns `(persisted_uids, newly_inserted_messages)` so callers only process
        spy/battle/recycle side effects once per unique message while safely confirming
        that every `persisted_uid` is durably stored in SQLite before deleting in-game.
        """
        if not messages:
            return ([], [])
        now_ts = round(_safe_float(ts, time.time()) if ts is not None else time.time(), 3)
        persisted_uids: list[str] = []
        newly_inserted: list[InboxMessage] = []
        with self._lock:
            for msg in messages:
                uid = _sanitize_text(getattr(msg, "msg_uid", "") or "").strip()[:120]
                if not uid or uid.lower().startswith("id:msg-"):
                    continue
                body_s = _sanitize_text(getattr(msg, "body_text", "") or "")[:16000]
                subj_s = _sanitize_text(getattr(msg, "subject", "") or "")[:300]
                if not body_s and not subj_s:
                    continue
                if re.fullmatch(r"\d[\d.]*\s*/\s*\d[\d.]*", body_s.strip()):
                    continue
                exists = self._conn.execute(
                    "SELECT id, LENGTH(body_text) AS body_len FROM game_messages WHERE msg_uid = ? LIMIT 1",
                    (uid,),
                ).fetchone()
                prev_body_len = _safe_int(exists["body_len"], 0) if exists is not None else 0
                raw_coords = _raw_text(getattr(msg, "coords", "") or "", "").strip()
                norm_c = normalize_coords(raw_coords) if raw_coords else ""
                cat_s = _sanitize_text(getattr(msg, "category", "other") or "other")[:64]
                srv_id_s = _sanitize_text(getattr(msg, "server_msg_id", "") or "")[:64]
                dt_s = _sanitize_text(getattr(msg, "msg_date_text", "") or "")[:64]
                pdata = dict(getattr(msg, "parsed_data", None) or {})
                self._conn.execute(
                    """
                    INSERT INTO game_messages(
                        ingested_ts, msg_uid, server_msg_id, folder_type, category,
                        msg_date_text, sender, subject, coords, body_text, raw_html, parsed_json
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(msg_uid) DO UPDATE SET
                        server_msg_id = CASE WHEN game_messages.server_msg_id != '' THEN game_messages.server_msg_id ELSE excluded.server_msg_id END,
                        category = CASE WHEN game_messages.category = 'other' AND excluded.category != 'other' THEN excluded.category ELSE game_messages.category END,
                        coords = CASE WHEN game_messages.coords != '' THEN game_messages.coords ELSE excluded.coords END,
                        body_text = CASE WHEN LENGTH(excluded.body_text) > LENGTH(game_messages.body_text) THEN excluded.body_text ELSE game_messages.body_text END,
                        raw_html = CASE WHEN LENGTH(excluded.raw_html) > LENGTH(game_messages.raw_html) THEN excluded.raw_html ELSE game_messages.raw_html END,
                        parsed_json = CASE WHEN LENGTH(excluded.body_text) >= LENGTH(game_messages.body_text) AND excluded.parsed_json != '{}' THEN excluded.parsed_json ELSE game_messages.parsed_json END
                    """,
                    (
                        now_ts,
                        uid,
                        srv_id_s,
                        _sanitize_text(getattr(msg, "folder_type", "") or "")[:64],
                        cat_s,
                        dt_s,
                        _sanitize_text(getattr(msg, "sender", "") or "")[:160],
                        subj_s,
                        norm_c,
                        body_s,
                        _sanitize_text(getattr(msg, "raw_html", "") or "")[:32000],
                        _json_dumps(pdata),
                    ),
                )
                if cat_s == "battle_report":
                    loot_obj = pdata.get("loot") if isinstance(pdata.get("loot"), Mapping) else (
                        pdata.get("resources_mentioned") if isinstance(pdata.get("resources_mentioned"), Mapping) else {}
                    )
                    deb_obj = pdata.get("debris") if isinstance(pdata.get("debris"), Mapping) else {}
                    b_pig = max(0, _sql_int(loot_obj.get("pig_iron"), 0))
                    b_met = max(0, _sql_int(loot_obj.get("metal"), 0))
                    b_kry = max(0, _sql_int(loot_obj.get("kryptonite"), 0))
                    b_spi = max(0, _sql_int(loot_obj.get("spice"), 0))
                    b_seq = _safe_float(
                        pdata.get("loot_spice_eq"),
                        round(b_pig / 6.0 + b_met / 2.5 + b_kry / 3.0 + float(b_spi), 2),
                    )
                    d_pig = max(0, _sql_int(deb_obj.get("pig_iron"), 0))
                    d_met = max(0, _sql_int(deb_obj.get("metal"), 0))
                    d_seq = _safe_float(pdata.get("debris_spice_eq"), round(d_pig / 6.0 + d_met / 2.5, 2))
                    self._conn.execute(
                        """
                        INSERT INTO battle_reports(
                            ts, msg_uid, server_msg_id, battle_id, msg_date_text,
                            target_coords, attacker, defender, outcome, rounds,
                            pig_iron, metal, kryptonite, spice, loot_spice_eq,
                            attacker_lost_spice, defender_lost_spice,
                            debris_pig_iron, debris_metal, debris_spice_eq, raw_text
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        ON CONFLICT(msg_uid) DO UPDATE SET
                            battle_id = CASE WHEN excluded.battle_id != '' THEN excluded.battle_id ELSE battle_reports.battle_id END,
                            attacker = CASE WHEN excluded.attacker != '' THEN excluded.attacker ELSE battle_reports.attacker END,
                            defender = CASE WHEN excluded.defender != '' THEN excluded.defender ELSE battle_reports.defender END,
                            outcome = excluded.outcome,
                            rounds = MAX(battle_reports.rounds, excluded.rounds),
                            pig_iron = MAX(battle_reports.pig_iron, excluded.pig_iron),
                            metal = MAX(battle_reports.metal, excluded.metal),
                            kryptonite = MAX(battle_reports.kryptonite, excluded.kryptonite),
                            spice = MAX(battle_reports.spice, excluded.spice),
                            loot_spice_eq = MAX(battle_reports.loot_spice_eq, excluded.loot_spice_eq),
                            attacker_lost_spice = MAX(battle_reports.attacker_lost_spice, excluded.attacker_lost_spice),
                            defender_lost_spice = MAX(battle_reports.defender_lost_spice, excluded.defender_lost_spice),
                            debris_pig_iron = MAX(battle_reports.debris_pig_iron, excluded.debris_pig_iron),
                            debris_metal = MAX(battle_reports.debris_metal, excluded.debris_metal),
                            debris_spice_eq = MAX(battle_reports.debris_spice_eq, excluded.debris_spice_eq),
                            raw_text = CASE WHEN LENGTH(excluded.raw_text) > LENGTH(battle_reports.raw_text) THEN excluded.raw_text ELSE battle_reports.raw_text END
                        """,
                        (
                            now_ts,
                            uid,
                            srv_id_s,
                            _sanitize_text(pdata.get("battle_id") or "")[:64],
                            dt_s,
                            norm_c,
                            _sanitize_text(pdata.get("attacker") or "")[:120],
                            _sanitize_text(pdata.get("defender") or "")[:120],
                            _sanitize_text(pdata.get("outcome") or "win")[:32],
                            max(0, _sql_int(pdata.get("rounds"), 0)),
                            b_pig,
                            b_met,
                            b_kry,
                            b_spi,
                            b_seq,
                            max(0, _sql_int(pdata.get("attacker_lost_spice"), 0)),
                            max(0, _sql_int(pdata.get("defender_lost_spice"), 0)),
                            d_pig,
                            d_met,
                            d_seq,
                            body_s[:8000],
                        ),
                    )
                elif cat_s == "recycle_report":
                    harv_obj = pdata.get("harvested") if isinstance(pdata.get("harvested"), Mapping) else {}
                    avail_obj = pdata.get("debris_available") if isinstance(pdata.get("debris_available"), Mapping) else {}
                    h_pig = max(0, _sql_int(harv_obj.get("pig_iron"), 0))
                    h_met = max(0, _sql_int(harv_obj.get("metal"), 0))
                    h_kry = max(0, _sql_int(harv_obj.get("kryptonite"), 0))
                    h_spi = max(0, _sql_int(harv_obj.get("spice"), 0))
                    h_seq = _safe_float(
                        pdata.get("harvested_spice_eq"),
                        round(h_pig / 6.0 + h_met / 2.5 + h_kry / 3.0 + float(h_spi), 2),
                    )
                    self._conn.execute(
                        """
                        INSERT INTO recycle_reports(
                            ts, msg_uid, server_msg_id, msg_date_text, coords,
                            recycler_count, cargo_capacity, debris_avail_pig, debris_avail_metal,
                            harvested_pig_iron, harvested_metal, harvested_kryptonite, harvested_spice,
                            harvested_spice_eq, raw_text
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        ON CONFLICT(msg_uid) DO UPDATE SET
                            coords = CASE WHEN excluded.coords != '' THEN excluded.coords ELSE recycle_reports.coords END,
                            recycler_count = MAX(recycle_reports.recycler_count, excluded.recycler_count),
                            cargo_capacity = MAX(recycle_reports.cargo_capacity, excluded.cargo_capacity),
                            debris_avail_pig = MAX(recycle_reports.debris_avail_pig, excluded.debris_avail_pig),
                            debris_avail_metal = MAX(recycle_reports.debris_avail_metal, excluded.debris_avail_metal),
                            harvested_pig_iron = MAX(recycle_reports.harvested_pig_iron, excluded.harvested_pig_iron),
                            harvested_metal = MAX(recycle_reports.harvested_metal, excluded.harvested_metal),
                            harvested_kryptonite = MAX(recycle_reports.harvested_kryptonite, excluded.harvested_kryptonite),
                            harvested_spice = MAX(recycle_reports.harvested_spice, excluded.harvested_spice),
                            harvested_spice_eq = MAX(recycle_reports.harvested_spice_eq, excluded.harvested_spice_eq),
                            raw_text = CASE WHEN LENGTH(excluded.raw_text) > LENGTH(recycle_reports.raw_text) THEN excluded.raw_text ELSE recycle_reports.raw_text END
                        """,
                        (
                            now_ts,
                            uid,
                            srv_id_s,
                            dt_s,
                            norm_c,
                            max(0, _sql_int(pdata.get("recycler_count"), 0)),
                            max(0, _sql_int(pdata.get("cargo_capacity"), 0)),
                            max(0, _sql_int(avail_obj.get("pig_iron"), 0)),
                            max(0, _sql_int(avail_obj.get("metal"), 0)),
                            h_pig,
                            h_met,
                            h_kry,
                            h_spi,
                            h_seq,
                            body_s[:8000],
                        ),
                    )
                persisted_uids.append(uid)
                if exists is None or len(body_s) > prev_body_len:
                    newly_inserted.append(msg)
            self._conn.commit()
        return (persisted_uids, newly_inserted)

    def mark_game_messages_deleted(
        self,
        msg_uids: Sequence[str],
        *,
        ts: Optional[float] = None,
    ) -> int:
        """Record in SQLite that `msg_uids` were deleted from the in-game server inbox (never deletes DB rows)."""
        clean_uids = [
            _sanitize_text(u).strip()[:120]
            for u in (msg_uids or ())
            if _sanitize_text(u).strip()
        ]
        if not clean_uids:
            return 0
        now_ts = round(_safe_float(ts, time.time()) if ts is not None else time.time(), 3)
        placeholders = ", ".join("?" for _ in clean_uids)
        with self._lock:
            cur = self._conn.execute(
                f"""
                UPDATE game_messages
                SET deleted_in_game = 1,
                    deleted_ts = COALESCE(deleted_ts, ?)
                WHERE msg_uid IN ({placeholders})
                """,
                [now_ts, *clean_uids],
            )
            self._conn.commit()
            return int(cur.rowcount or 0)

    def get_game_messages(
        self,
        *,
        category: Optional[str] = None,
        exclude_categories: Sequence[str] = (),
        coords: Optional[str] = None,
        folder_type: Optional[str] = None,
        limit: int = 200,
    ) -> list[dict[str, Any]]:
        clauses: list[str] = ["msg_uid NOT LIKE 'id:msg-%'"]
        params: list[Any] = []
        if category is not None:
            cat_clean = _sanitize_text(category).strip()
            if cat_clean == "general":
                clauses.append("category IN ('general', 'player', 'system', 'other')")
            else:
                clauses.append("category = ?")
                params.append(cat_clean)
        if exclude_categories:
            excl_clean = [_sanitize_text(c).strip() for c in exclude_categories if _sanitize_text(c).strip()]
            if excl_clean:
                placeholders = ", ".join("?" for _ in excl_clean)
                clauses.append(f"category NOT IN ({placeholders})")
                params.extend(excl_clean)
        if coords is not None:
            clauses.append("coords = ?")
            params.append(normalize_coords(_raw_text(coords, "").strip()))
        if folder_type is not None:
            clauses.append("folder_type = ?")
            params.append(_sanitize_text(folder_type).strip())
        where_sql = ("WHERE " + " AND ".join(clauses)) if clauses else ""
        params.append(max(1, _sql_int(limit, 200)))
        with self._lock:
            rows = self._conn.execute(
                f"""
                SELECT id, ingested_ts, msg_uid, server_msg_id, folder_type, category,
                       msg_date_text, sender, subject, coords, body_text, raw_html,
                       parsed_json, deleted_in_game, deleted_ts
                FROM game_messages
                {where_sql}
                ORDER BY ingested_ts DESC, id DESC
                LIMIT ?
                """,
                params,
            ).fetchall()
        out: list[dict[str, Any]] = []
        for r in rows:
            btxt = _sanitize_text(r["body_text"])
            if re.fullmatch(r"\d[\d.]*\s*/\s*\d[\d.]*", btxt.strip()):
                continue
            out.append(
                _sanitize_obj(
                    {
                        "id": game_data.cap_json_int(_safe_int(r["id"], 0)),
                        "ingested_ts": _safe_float(r["ingested_ts"], 0.0),
                        "msg_uid": _sanitize_text(r["msg_uid"]),
                        "server_msg_id": _sanitize_text(r["server_msg_id"]),
                        "folder_type": _sanitize_text(r["folder_type"]),
                        "category": _sanitize_text(r["category"]),
                        "msg_date_text": _sanitize_text(r["msg_date_text"]),
                        "sender": _sanitize_text(r["sender"]),
                        "subject": _sanitize_text(r["subject"]),
                        "coords": _sanitize_text(r["coords"]),
                        "body_text": btxt,
                        "raw_html": _sanitize_text(r["raw_html"]),
                        "parsed": _safe_json_dict(r["parsed_json"]),
                        "deleted_in_game": bool(r["deleted_in_game"]),
                        "deleted_ts": _safe_optional_float(r["deleted_ts"]),
                    }
                )
            )
        return out

    def get_battle_reports(
        self,
        *,
        target_coords: Optional[str] = None,
        coords: Optional[str] = None,
        limit: int = 200,
    ) -> list[dict[str, Any]]:
        eff_coords = target_coords if target_coords is not None else coords
        lim = max(1, _sql_int(limit, 200))
        with self._lock:
            if eff_coords is not None:
                norm_c = normalize_coords(_raw_text(eff_coords, "").strip())
                rows = self._conn.execute(
                    """
                    SELECT id, ts, msg_uid, server_msg_id, battle_id, msg_date_text,
                           target_coords, attacker, defender, outcome, rounds,
                           pig_iron, metal, kryptonite, spice, loot_spice_eq,
                           attacker_lost_spice, defender_lost_spice,
                           debris_pig_iron, debris_metal, debris_spice_eq, raw_text
                    FROM battle_reports
                    WHERE target_coords = ?
                    ORDER BY ts DESC, id DESC
                    LIMIT ?
                    """,
                    (norm_c, lim),
                ).fetchall()
            else:
                rows = self._conn.execute(
                    """
                    SELECT id, ts, msg_uid, server_msg_id, battle_id, msg_date_text,
                           target_coords, attacker, defender, outcome, rounds,
                           pig_iron, metal, kryptonite, spice, loot_spice_eq,
                           attacker_lost_spice, defender_lost_spice,
                           debris_pig_iron, debris_metal, debris_spice_eq, raw_text
                    FROM battle_reports
                    ORDER BY ts DESC, id DESC
                    LIMIT ?
                    """,
                    (lim,),
                ).fetchall()
        out_b: list[dict[str, Any]] = []
        for r in rows:
            norm_tc = normalize_coords(_raw_text(r["target_coords"], ""))
            att_s = _sanitize_text(r["attacker"])
            def_s = _sanitize_text(r["defender"])
            loot_sp = _safe_float(r["loot_spice_eq"], 0.0)
            att_lost = game_data.cap_json_int(max(0, _safe_int(r["attacker_lost_spice"], 0)))
            def_lost = game_data.cap_json_int(max(0, _safe_int(r["defender_lost_spice"], 0)))
            out_b.append(
                _sanitize_obj(
                    {
                        "id": game_data.cap_json_int(_safe_int(r["id"], 0)),
                        "ts": _safe_float(r["ts"], 0.0),
                        "msg_uid": _sanitize_text(r["msg_uid"]),
                        "server_msg_id": _sanitize_text(r["server_msg_id"]),
                        "battle_id": _sanitize_text(r["battle_id"]),
                        "msg_date_text": _sanitize_text(r["msg_date_text"]),
                        "target_coords": norm_tc,
                        "coords": norm_tc,
                        "attacker": att_s,
                        "attacker_name": att_s,
                        "defender": def_s,
                        "defender_name": def_s,
                        "outcome": _sanitize_text(r["outcome"]),
                        "rounds": game_data.cap_json_int(_safe_int(r["rounds"], 0)),
                        "loot": {
                            "pig_iron": game_data.cap_json_int(max(0, _safe_int(r["pig_iron"], 0))),
                            "metal": game_data.cap_json_int(max(0, _safe_int(r["metal"], 0))),
                            "kryptonite": game_data.cap_json_int(max(0, _safe_int(r["kryptonite"], 0))),
                            "spice": game_data.cap_json_int(max(0, _safe_int(r["spice"], 0))),
                            "spice_eq": loot_sp,
                        },
                        "loot_spice_eq": loot_sp,
                        "attacker_lost_spice": att_lost,
                        "defender_lost_spice": def_lost,
                        "attacker_losses": {"spice_eq": att_lost},
                        "defender_losses": {"spice_eq": def_lost},
                        "debris": {
                            "pig_iron": game_data.cap_json_int(max(0, _safe_int(r["debris_pig_iron"], 0))),
                            "metal": game_data.cap_json_int(max(0, _safe_int(r["debris_metal"], 0))),
                        },
                        "debris_spice_eq": _safe_float(r["debris_spice_eq"], 0.0),
                        "raw_text": _sanitize_text(r["raw_text"]),
                    }
                )
            )
        return out_b

    def get_recycle_reports(
        self,
        *,
        coords: Optional[str] = None,
        limit: int = 200,
    ) -> list[dict[str, Any]]:
        lim = max(1, _sql_int(limit, 200))
        with self._lock:
            if coords is not None:
                norm_c = normalize_coords(_raw_text(coords, "").strip())
                rows = self._conn.execute(
                    """
                    SELECT id, ts, msg_uid, server_msg_id, msg_date_text, coords,
                           recycler_count, cargo_capacity, debris_avail_pig, debris_avail_metal,
                           harvested_pig_iron, harvested_metal, harvested_kryptonite, harvested_spice,
                           harvested_spice_eq, raw_text
                    FROM recycle_reports
                    WHERE coords = ?
                    ORDER BY ts DESC, id DESC
                    LIMIT ?
                    """,
                    (norm_c, lim),
                ).fetchall()
            else:
                rows = self._conn.execute(
                    """
                    SELECT id, ts, msg_uid, server_msg_id, msg_date_text, coords,
                           recycler_count, cargo_capacity, debris_avail_pig, debris_avail_metal,
                           harvested_pig_iron, harvested_metal, harvested_kryptonite, harvested_spice,
                           harvested_spice_eq, raw_text
                    FROM recycle_reports
                    ORDER BY ts DESC, id DESC
                    LIMIT ?
                    """,
                    (lim,),
                ).fetchall()
        out_r: list[dict[str, Any]] = []
        for r in rows:
            hpig = game_data.cap_json_int(max(0, _safe_int(r["harvested_pig_iron"], 0)))
            hmet = game_data.cap_json_int(max(0, _safe_int(r["harvested_metal"], 0)))
            hkry = game_data.cap_json_int(max(0, _safe_int(r["harvested_kryptonite"], 0)))
            hspi = game_data.cap_json_int(max(0, _safe_int(r["harvested_spice"], 0)))
            fpig = game_data.cap_json_int(max(0, _safe_int(r["debris_avail_pig"], 0)))
            fmet = game_data.cap_json_int(max(0, _safe_int(r["debris_avail_metal"], 0)))
            hseq = _safe_float(r["harvested_spice_eq"], round(hpig / 6.0 + hmet / 2.5 + hkry / 3.0 + float(hspi), 2))
            out_r.append(
                _sanitize_obj(
                    {
                        "id": game_data.cap_json_int(_safe_int(r["id"], 0)),
                        "ts": _safe_float(r["ts"], 0.0),
                        "msg_uid": _sanitize_text(r["msg_uid"]),
                        "server_msg_id": _sanitize_text(r["server_msg_id"]),
                        "msg_date_text": _sanitize_text(r["msg_date_text"]),
                        "coords": normalize_coords(_raw_text(r["coords"], "")),
                        "recycler_count": game_data.cap_json_int(max(0, _safe_int(r["recycler_count"], 0))),
                        "cargo_capacity": game_data.cap_json_int(max(0, _safe_int(r["cargo_capacity"], 0))),
                        "field_pig_iron": fpig,
                        "field_metal": fmet,
                        "debris_available": {
                            "pig_iron": fpig,
                            "metal": fmet,
                        },
                        "harvested_pig_iron": hpig,
                        "harvested_metal": hmet,
                        "harvested_kryptonite": hkry,
                        "harvested_spice": hspi,
                        "harvested": {
                            "pig_iron": hpig,
                            "metal": hmet,
                            "kryptonite": hkry,
                            "spice": hspi,
                        },
                        "harvested_spice_eq": hseq,
                        "spice_eq": hseq,
                        "raw_text": _sanitize_text(r["raw_text"]),
                    }
                )
            )
        return out_r

    def get_discarded_colony_coords(self) -> set[str]:
        """Return normalized coordinates of operator-discarded free colony candidates."""
        raw = self.load_meta_json("discarded_colony_coords")
        if not isinstance(raw, Mapping):
            return set()
        out: set[str] = set()
        for k in raw.keys():
            nc = normalize_coords(_raw_text(k, "").strip())
            if nc:
                out.add(nc)
        return out

    def discard_colony_candidate(
        self,
        coords: str,
        *,
        reason: str = "operator_discard",
        ts: Optional[float] = None,
    ) -> dict[str, Any]:
        """Mark a free colony coordinate as discarded non-destructively in `meta['discarded_colony_coords']`."""
        norm_c = normalize_coords(_raw_text(coords, "").strip())
        if not norm_c:
            raise ValueError(f"Invalid colony coordinates: {coords!r}")
        eff_now = round(_safe_float(ts, time.time()) if ts is not None else time.time(), 3)
        raw = self.load_meta_json("discarded_colony_coords")
        cur_map: dict[str, Any] = {}
        if isinstance(raw, Mapping):
            for k, v in raw.items():
                nk = normalize_coords(_raw_text(k, "").strip())
                if nk:
                    cur_map[nk] = dict(v) if isinstance(v, Mapping) else {"discarded_ts": eff_now, "reason": "operator_discard"}
        clean_reason = _sanitize_text(reason or "operator_discard")[:160] or "operator_discard"
        entry = {"discarded_ts": eff_now, "reason": clean_reason}
        cur_map[norm_c] = entry
        self.save_meta_json("discarded_colony_coords", cur_map, ts=eff_now)
        return _sanitize_obj(
            {
                "coords": norm_c,
                "discarded": True,
                "discarded_ts": eff_now,
                "reason": clean_reason,
                "discarded_count": len(cur_map),
            }
        )

    def restore_colony_candidate(
        self,
        coords: str,
        *,
        ts: Optional[float] = None,
    ) -> bool:
        """Restore a previously discarded colony candidate coordinate."""
        norm_c = normalize_coords(_raw_text(coords, "").strip())
        if not norm_c:
            return False
        eff_now = round(_safe_float(ts, time.time()) if ts is not None else time.time(), 3)
        raw = self.load_meta_json("discarded_colony_coords")
        if not isinstance(raw, Mapping):
            return False
        cur_map: dict[str, Any] = {}
        removed = False
        for k, v in raw.items():
            nk = normalize_coords(_raw_text(k, "").strip())
            if not nk:
                continue
            if nk == norm_c:
                removed = True
                continue
            cur_map[nk] = dict(v) if isinstance(v, Mapping) else v
        if removed:
            self.save_meta_json("discarded_colony_coords", cur_map, ts=eff_now)
        return removed

    def get_colony_candidates(
        self,
        *,
        origin_coords: str = "",
        min_colony_fields: int = 300,
        min_fields_target: Optional[int] = None,
        own_coords: Iterable[str] = (),
        exclude_coords: Optional[Iterable[str]] = None,
        include_discarded: bool = False,
        limit: int = 200,
    ) -> list[dict[str, Any]]:
        eff_own = exclude_coords if exclude_coords is not None else own_coords
        eff_min = min_fields_target if min_fields_target is not None else min_colony_fields
        own_norm = {normalize_coords(c) for c in (eff_own or ()) if normalize_coords(c)}
        norm_origin = normalize_coords(origin_coords) if origin_coords else ""
        min_tgt = max(50, min(600, _safe_int(eff_min, 300)))
        eff_limit = max(1, _safe_int(limit, 200))
        cache_key = (norm_origin, min_tgt, tuple(sorted(own_norm)), bool(include_discarded), eff_limit)

        hot_rows: Optional[list[tuple[Any, ...]]] = None
        discarded_norm: set[str] = set()
        db_ver: tuple[int, int] = (0, 0)

        with self._lock:
            db_ver = self._db_version_locked()
            if self._colony_cand_cache_ver == db_ver and cache_key in self._colony_cand_cache:
                return [dict(item) for item in self._colony_cand_cache[cache_key]]
            use_hot = self._ensure_hot_tables_synced_locked(check_colonies=True)
            discarded_norm = self._load_discarded_colony_coords_locked()
            if use_hot:
                cur = self._conn.cursor()
                cur.row_factory = None
                where_sql = "is_active = 1" if include_discarded else "is_active = 1 AND is_discarded = 0"
                hot_rows = cur.execute(
                    f"""
                    SELECT coords, galaxy, system, position,
                           uninhabited_fields, colonize_min_fields, colonize_max_fields,
                           temperature, last_spied_ts, last_scanned_ts, spy_count,
                           blocked_until_ts, block_reason
                    FROM colony_candidates_hot
                    WHERE {where_sql}
                    """
                ).fetchall()
            else:
                has_st_cmax = self._has_column_locked("spy_targets", "colonize_max_fields")
                free_rows = self._conn.execute(
                    """
                    SELECT *
                    FROM galaxy_slots
                    WHERE status_color = 'free'
                    ORDER BY galaxy ASC, system ASC, position ASC
                    """
                ).fetchall()
                st_cmax_or = "OR colonize_max_fields IS NOT NULL" if has_st_cmax else ""
                spy_free_rows = self._conn.execute(
                    f"""
                    SELECT *
                    FROM spy_targets
                    WHERE merged_into = '' AND is_own_planet = 0
                      AND (
                          status_color = 'free'
                          OR ((uninhabited_fields IS NOT NULL {st_cmax_or}) AND owner = '' AND is_spacia = 0)
                      )
                    """
                ).fetchall()
                rep_free_rows = self._conn.execute(
                    """
                    SELECT target_coords, MAX(uninhabited_fields) AS max_fields, MAX(ts) AS last_ts, COUNT(*) AS rep_cnt
                    FROM spy_reports
                    WHERE uninhabited_fields IS NOT NULL AND owner = '' AND is_spacia = 0
                    GROUP BY target_coords
                    """
                ).fetchall()

        def _sort_key(item: dict[str, Any]) -> tuple[int, float, float, str]:
            eff_max = float(
                item["uninhabited_fields"]
                if item.get("uninhabited_fields") is not None
                else (item.get("colonize_max_fields") or 0)
            )
            if item["meets_target"]:
                tier = 0
                primary = -eff_max
            elif not item["spied"] and 7 <= int(item["position"]) <= 12:
                tier = 1
                primary = float(item["distance"])
            elif not item["spied"]:
                tier = 2
                primary = -float(item["score"])
            else:
                tier = 3
                primary = -eff_max
            return (tier, primary, float(item["distance"]), str(item["coords"]))

        if hot_rows is not None:
            orig_nums = (
                [min(999_999, int(x[:9])) for x in re.findall(r"\d+", norm_origin, re.ASCII)[:3]]
                if norm_origin
                else []
            )
            orig_valid = len(orig_nums) >= 3
            orig_g = orig_nums[0] if orig_valid else 0
            orig_s = orig_nums[1] if orig_valid else 0
            orig_p = orig_nums[2] if orig_valid else 0
            orig_bad = bool(norm_origin) and not orig_valid
            has_own_filter = bool(own_norm)
            check_disc_py = (not include_discarded) and bool(discarded_norm)

            ranked_entries: list[tuple[tuple[int, float, float, str], tuple[Any, ...]]] = []
            for r in hot_rows:
                nc = r[0]
                if not nc:
                    continue
                if has_own_filter and nc in own_norm:
                    continue
                if check_disc_py and nc in discarded_norm:
                    continue
                gal = r[1]
                sys_num = r[2]
                pos = r[3]
                if orig_bad or (gal == 0 and sys_num == 0 and pos == 0):
                    dist = 100.0
                elif orig_valid:
                    if orig_g != gal:
                        dist = 20000.0 * abs(orig_g - gal)
                    elif orig_s != sys_num:
                        dist = 2700.0 + 95.0 * abs(orig_s - sys_num)
                    else:
                        dist = 1000.0 + 5.0 * abs(orig_p - pos)
                else:
                    dist = 1000.0
                dist_r = round(dist, 1)

                uf = r[4]
                cmin = r[5]
                cmax = r[6]
                fields_val = uf if (uf is not None and uf > 0) else None
                cmin_val = cmin if (cmin is not None and cmin > 0) else None
                cmax_val = cmax if (cmax is not None and cmax > 0) else None
                if cmin_val is not None and cmax_val is not None and cmin_val > cmax_val:
                    cmax_val = cmin_val

                if fields_val is not None:
                    if fields_val >= min_tgt:
                        tier = 0
                    else:
                        tier = 3
                    primary = -float(fields_val)
                elif cmax_val is not None:
                    if cmax_val >= min_tgt:
                        tier = 0
                    else:
                        tier = 3
                    primary = -float(cmax_val)
                elif 7 <= pos <= 12:
                    tier = 1
                    primary = dist_r
                else:
                    tier = 2
                    if 4 <= pos <= 6:
                        pot = 56 - abs(pos - 5) * 3
                    elif 13 <= pos <= 16:
                        pot = 48 - abs(pos - 14) * 2
                    else:
                        pot = 30
                    d_pen = min(5, int(max(0.0, dist - 1000.0) / 800.0))
                    sc = max(5, min(85, pot - d_pen))
                    primary = -float(sc)

                ranked_entries.append(((tier, primary, dist_r, nc), r))

            if len(ranked_entries) > eff_limit:
                top_entries = heapq.nsmallest(eff_limit, ranked_entries, key=lambda x: x[0])
            else:
                ranked_entries.sort(key=lambda x: x[0])
                top_entries = ranked_entries

            graded_hot: list[dict[str, Any]] = []
            for _rk, r in top_entries:
                nc = str(r[0])
                uf = _safe_optional_int(r[4])
                cmin = _safe_optional_int(r[5])
                cmax = _safe_optional_int(r[6])
                temp = _safe_optional_int(r[7])
                lsp = _safe_float(r[8], 0.0)
                lsc = _safe_float(r[9], 0.0)
                has_intel_c = uf is not None or cmax is not None
                g = grade_colony_candidate(
                    norm_origin or nc,
                    nc,
                    uninhabited_fields=uf,
                    colonize_min_fields=cmin,
                    colonize_max_fields=cmax,
                    temperature=temp,
                    spied=has_intel_c,
                    min_fields_target=min_tgt,
                    last_spied_ts=lsp,
                    last_scanned_ts=lsc,
                )
                g["spy_count"] = game_data.cap_json_int(max(0, _safe_int(r[10], 0)))
                g["blocked_until_ts"] = _safe_float(r[11], 0.0)
                g["block_reason"] = _sanitize_text(r[12]) if r[12] else ""
                g["discarded"] = nc in discarded_norm
                graded_hot.append(g)

            graded_hot.sort(key=_sort_key)
            result_hot = graded_hot[:eff_limit]
            with self._lock:
                if self._db_version_locked() == db_ver:
                    if self._colony_cand_cache_ver != db_ver:
                        self._colony_cand_cache_ver = db_ver
                        self._colony_cand_cache = {}
                    if len(self._colony_cand_cache) >= 16:
                        self._colony_cand_cache.clear()
                    self._colony_cand_cache[cache_key] = [dict(item) for item in result_hot]
            return result_hot

        merged: dict[str, dict[str, Any]] = {}
        for r in free_rows:
            rk_f = set(r.keys())
            nc = normalize_coords(_raw_text(r["coords"], ""))
            if not nc or nc in own_norm or (not include_discarded and nc in discarded_norm):
                continue
            merged[nc] = {
                "coords": nc,
                "uninhabited_fields": None,
                "colonize_min_fields": _safe_optional_int(r["colonize_min_fields"]) if "colonize_min_fields" in rk_f else None,
                "colonize_max_fields": _safe_optional_int(r["colonize_max_fields"]) if "colonize_max_fields" in rk_f else None,
                "temperature": _safe_optional_int(r["temperature"]) if "temperature" in rk_f else None,
                "last_spied_ts": 0.0,
                "last_scanned_ts": _safe_float(r["last_scanned_ts"], 0.0),
                "spy_count": 0,
                "blocked_until_ts": 0.0,
                "block_reason": "",
            }
        for sr in spy_free_rows:
            rk_s = set(sr.keys())
            nc = normalize_coords(_raw_text(sr["coords"], ""))
            if not nc or nc in own_norm or (not include_discarded and nc in discarded_norm):
                continue
            entry = merged.setdefault(
                nc,
                {
                    "coords": nc,
                    "uninhabited_fields": None,
                    "colonize_min_fields": None,
                    "colonize_max_fields": None,
                    "temperature": None,
                    "last_spied_ts": 0.0,
                    "last_scanned_ts": 0.0,
                    "spy_count": 0,
                    "blocked_until_ts": 0.0,
                    "block_reason": "",
                },
            )
            uf = _safe_optional_int(sr["uninhabited_fields"]) if "uninhabited_fields" in rk_s else None
            if uf is not None:
                entry["uninhabited_fields"] = uf
            c_min_s = _safe_optional_int(sr["colonize_min_fields"]) if "colonize_min_fields" in rk_s else None
            if c_min_s is not None and entry["colonize_min_fields"] is None:
                entry["colonize_min_fields"] = c_min_s
            c_max_s = _safe_optional_int(sr["colonize_max_fields"]) if "colonize_max_fields" in rk_s else None
            if c_max_s is not None and entry["colonize_max_fields"] is None:
                entry["colonize_max_fields"] = c_max_s
            temp_s = _safe_optional_int(sr["temperature"]) if "temperature" in rk_s else None
            if temp_s is not None and entry["temperature"] is None:
                entry["temperature"] = temp_s
            entry["last_spied_ts"] = max(entry["last_spied_ts"], _safe_float(sr["last_spied_ts"], 0.0))
            entry["last_scanned_ts"] = max(entry["last_scanned_ts"], _safe_float(sr["last_scanned_ts"], 0.0))
            entry["spy_count"] = max(entry["spy_count"], _safe_int(sr["spy_count"], 0))
            b_until = _safe_float(sr["blocked_until_ts"], 0.0)
            b_rsn = _sanitize_text(sr["block_reason"] or "")
            if (
                "can't be attacked" not in b_rsn.lower()
                and "cannot be attacked" not in b_rsn.lower()
                and b_until > entry["blocked_until_ts"]
            ):
                entry["blocked_until_ts"] = b_until
                entry["block_reason"] = b_rsn
        for rr in rep_free_rows:
            nc = normalize_coords(_raw_text(rr["target_coords"], ""))
            if not nc or nc in own_norm or (not include_discarded and nc in discarded_norm):
                continue
            entry = merged.setdefault(
                nc,
                {
                    "coords": nc,
                    "uninhabited_fields": None,
                    "colonize_min_fields": None,
                    "colonize_max_fields": None,
                    "temperature": None,
                    "last_spied_ts": 0.0,
                    "last_scanned_ts": 0.0,
                    "spy_count": 0,
                    "blocked_until_ts": 0.0,
                    "block_reason": "",
                },
            )
            uf = _safe_optional_int(rr["max_fields"])
            if uf is not None:
                entry["uninhabited_fields"] = uf
            entry["last_spied_ts"] = max(entry["last_spied_ts"], _safe_float(rr["last_ts"], 0.0))
            entry["spy_count"] = max(entry["spy_count"], _safe_int(rr["rep_cnt"], 0))

        graded: list[dict[str, Any]] = []
        for nc, info in merged.items():
            has_intel_c = info["uninhabited_fields"] is not None or info["colonize_max_fields"] is not None
            g = grade_colony_candidate(
                norm_origin or nc,
                nc,
                uninhabited_fields=info["uninhabited_fields"],
                colonize_min_fields=info["colonize_min_fields"],
                colonize_max_fields=info["colonize_max_fields"],
                temperature=info["temperature"],
                spied=has_intel_c,
                min_fields_target=min_tgt,
                last_spied_ts=info["last_spied_ts"],
                last_scanned_ts=info["last_scanned_ts"],
            )
            g["spy_count"] = int(info["spy_count"])
            g["blocked_until_ts"] = float(info["blocked_until_ts"])
            g["block_reason"] = str(info["block_reason"])
            g["discarded"] = nc in discarded_norm
            graded.append(_sanitize_obj(g))

        graded.sort(key=_sort_key)
        return graded[:eff_limit]

    def get_galaxy_slot_history(
        self,
        *,
        coords: Optional[str] = None,
        galaxy: Optional[int] = None,
        system: Optional[int] = None,
        limit: int = 500,
    ) -> list[dict[str, Any]]:
        clauses: list[str] = []
        params: list[Any] = []
        if coords is not None:
            clauses.append("coords = ?")
            params.append(normalize_coords(_raw_text(coords, "").strip()))
        if galaxy is not None:
            clauses.append("galaxy = ?")
            params.append(_sql_int(galaxy, 0))
        if system is not None:
            clauses.append("system = ?")
            params.append(_sql_int(system, 0))
        where_sql = ("WHERE " + " AND ".join(clauses)) if clauses else ""
        params.append(max(1, _sql_int(limit, 500)))
        with self._lock:
            rows = self._conn.execute(
                f"""
                SELECT id, ts, coords, galaxy, system, position, planet_name, owner,
                       status_color, is_inactive, is_spacia, has_debris, has_asteroid
                FROM galaxy_slot_history
                {where_sql}
                ORDER BY ts DESC, id DESC
                LIMIT ?
                """,
                params,
            ).fetchall()
        return [
            _sanitize_obj(
                {
                    "id": game_data.cap_json_int(_safe_int(r["id"], 0)),
                    "ts": _safe_float(r["ts"], 0.0),
                    "coords": normalize_coords(_raw_text(r["coords"], "")),
                    "galaxy": game_data.cap_json_int(_safe_int(r["galaxy"], 0)),
                    "system": game_data.cap_json_int(_safe_int(r["system"], 0)),
                    "position": game_data.cap_json_int(_safe_int(r["position"], 0)),
                    "planet_name": _sanitize_text(r["planet_name"]),
                    "owner": _sanitize_text(r["owner"]),
                    "status_color": _sanitize_text(r["status_color"]),
                    "is_inactive": bool(r["is_inactive"]),
                    "is_spacia": bool(r["is_spacia"]),
                    "has_debris": bool(r["has_debris"]),
                    "has_asteroid": bool(r["has_asteroid"]),
                }
            )
            for r in rows
        ]

    def get_galaxy_scan_history(
        self,
        *,
        galaxy: Optional[int] = None,
        system: Optional[int] = None,
        limit: int = 200,
    ) -> list[dict[str, Any]]:
        clauses: list[str] = []
        params: list[Any] = []
        if galaxy is not None:
            clauses.append("galaxy = ?")
            params.append(_sql_int(galaxy, 0))
        if system is not None:
            clauses.append("system = ?")
            params.append(_sql_int(system, 0))
        where_sql = ("WHERE " + " AND ".join(clauses)) if clauses else ""
        params.append(max(1, _sql_int(limit, 200)))
        with self._lock:
            rows = self._conn.execute(
                f"""
                SELECT id, ts, galaxy, system, occupied_count, target_count,
                       free_count, debris_count, asteroid_count
                FROM galaxy_scan_history
                {where_sql}
                ORDER BY ts DESC, id DESC
                LIMIT ?
                """,
                params,
            ).fetchall()
        return [_sanitize_obj(dict(r)) for r in rows]

    def set_target_debris(self, target_coords: str, has_debris: bool) -> None:
        raw_c = _raw_text(target_coords, "").strip()
        norm_c = normalize_coords(raw_c)
        if not norm_c:
            return
        flag = 1 if has_debris else 0
        with self._lock:
            self._coalesce_legacy_spy_target_locked(norm_c, raw_c)
            self._conn.execute(
                "UPDATE spy_targets SET has_debris = ? WHERE coords = ?",
                (flag, norm_c),
            )
            self._conn.execute(
                "UPDATE galaxy_slots SET has_debris = ? WHERE coords = ?",
                (flag, norm_c),
            )
            parsed_deb = self._parse_norm_gsp_fast(norm_c)
            if parsed_deb is not None and 1 <= parsed_deb[1] <= 14 and 1 <= parsed_deb[2] <= 400:
                self._refresh_galaxy_sectors_hot_locked(((parsed_deb[1], parsed_deb[2]),), time.time())
            self._conn.commit()

    def record_target_block(
        self,
        target_coords: str,
        *,
        blocked_until_ts: float,
        reason: str = "",
        is_bash: bool = False,
        blocked_ts: Optional[float] = None,
        ts: Optional[float] = None,
    ) -> None:
        raw_c = _raw_text(target_coords, "").strip()
        norm_c = normalize_coords(raw_c)
        if not norm_c:
            return
        eff_ts = blocked_ts if blocked_ts is not None else ts
        now_val = _safe_float(eff_ts, time.time()) if eff_ts is not None else time.time()
        until_val = _safe_float(blocked_until_ts, now_val + 3600.0)
        if until_val <= now_val:
            until_val = now_val + 60.0
        cooldown_s = round(max(0.0, until_val - now_val), 3)
        clean_reason = _sanitize_text(reason)[:400]
        is_spacia_hint = 1 if "spacia" in clean_reason.lower() else 0
        with self._lock:
            self._coalesce_legacy_spy_target_locked(norm_c, raw_c)
            gs_row = None
            if DEFAULT_GAME_MIGRATION_RUNNER._table_exists(self._conn, "galaxy_slots"):
                gs_row = self._conn.execute(
                    """
                    SELECT owner, planet_name, status_color, is_inactive, is_spacia, has_debris
                    FROM galaxy_slots
                    WHERE coords = ?
                    LIMIT 1
                    """,
                    (norm_c,),
                ).fetchone()
            if gs_row is not None:
                ins_owner = _sanitize_text(gs_row["owner"] or "")
                ins_pname = _sanitize_text(gs_row["planet_name"] or "")
                ins_color = _sanitize_text(gs_row["status_color"] or "free") or "free"
                ins_inactive = 1 if bool(gs_row["is_inactive"]) else 0
                ins_spacia = 1 if (bool(gs_row["is_spacia"]) or is_spacia_hint) else 0
                ins_debris = 1 if bool(gs_row["has_debris"]) else 0
            elif is_spacia_hint:
                ins_owner = "Spacia"
                ins_pname = "Spacia"
                ins_color = "purple"
                ins_inactive = 1
                ins_spacia = 1
                ins_debris = 0
            else:
                ins_owner = ""
                ins_pname = ""
                ins_color = "free"
                ins_inactive = 0
                ins_spacia = 0
                ins_debris = 0
            self._conn.execute(
                """
                INSERT INTO spy_targets(
                    coords, owner, planet_name, status_color, is_inactive, is_spacia, has_debris,
                    last_scanned_ts, blocked_until_ts, block_reason, bash_hits, last_blocked_ts
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(coords) DO UPDATE SET
                    blocked_until_ts = MAX(spy_targets.blocked_until_ts, excluded.blocked_until_ts),
                    block_reason = CASE WHEN excluded.block_reason != '' THEN excluded.block_reason ELSE spy_targets.block_reason END,
                    bash_hits = spy_targets.bash_hits + excluded.bash_hits,
                    last_blocked_ts = MAX(spy_targets.last_blocked_ts, excluded.last_blocked_ts)
                """,
                (
                    norm_c,
                    ins_owner,
                    ins_pname,
                    ins_color,
                    ins_inactive,
                    ins_spacia,
                    ins_debris,
                    round(now_val, 3),
                    round(until_val, 3),
                    clean_reason,
                    1 if is_bash else 0,
                    round(now_val, 3),
                ),
            )
            self._conn.execute(
                """
                INSERT INTO bash_blocks(
                    target_coords, blocked_ts, blocked_until_ts, cooldown_seconds, is_bash, reason
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    norm_c,
                    round(now_val, 3),
                    round(until_val, 3),
                    cooldown_s,
                    1 if is_bash else 0,
                    clean_reason,
                ),
            )
            self._refresh_colony_candidates_hot_locked((norm_c,), now_val)
            parsed_blk = self._parse_norm_gsp_fast(norm_c)
            if parsed_blk is not None and 1 <= parsed_blk[1] <= 14 and 1 <= parsed_blk[2] <= 400:
                self._refresh_galaxy_sectors_hot_locked(((parsed_blk[1], parsed_blk[2]),), now_val)
            self._conn.commit()
            self._sync_attack_history_file_locked()

    def record_raid(
        self,
        target_coords: str,
        *,
        attacked_ts: Optional[float] = None,
        is_exempt: bool = False,
        outcome: str = "",
        loot: Optional[dict[str, int]] = None,
    ) -> None:
        ts_val = _safe_float(attacked_ts, time.time()) if attacked_ts is not None else time.time()
        norm_coords = normalize_coords(target_coords)
        clean_outcome = _sanitize_text(outcome)
        norm_loot = _normalize_loot(loot) if loot else {}
        with self._lock:
            self._conn.execute(
                """
                INSERT INTO raid_history(target_coords, attacked_ts, is_exempt, outcome, loot_json)
                VALUES (?, ?, ?, ?, ?)
                """,
                (
                    norm_coords,
                    round(ts_val, 3),
                    1 if is_exempt else 0,
                    clean_outcome,
                    _json_dumps(norm_loot),
                ),
            )
            cur_upd = self._conn.execute(
                """
                UPDATE spy_targets
                SET blocked_until_ts = 0.0, block_reason = ''
                WHERE coords = ? AND blocked_until_ts > 0.0 AND blocked_until_ts <= ?
                """,
                (norm_coords, round(ts_val, 3)),
            )
            if int(cur_upd.rowcount or 0) > 0:
                self._refresh_colony_candidates_hot_locked((norm_coords,), ts_val)
            self._conn.commit()
            self._sync_attack_history_file_locked()

    @staticmethod
    def _aggregate_raid_rows(
        rows: Sequence[dict[str, Any]],
        *,
        ref_ts: float,
    ) -> dict[str, Any]:
        n = len(rows)
        if n == 0:
            return {
                "attempts": 0,
                "wins": 0,
                "losses": 0,
                "success_rate": 0.0,
                "total_loot": _empty_loot_int(),
                "avg_loot": _empty_loot_float(),
                "total_loot_spice_eq": 0.0,
                "avg_loot_spice_eq": 0.0,
                "recency_weighted_loot_spice_eq": 0.0,
            }
        wins = sum(1 for r in rows if r["won"])
        losses = n - wins
        tot_loot = _empty_loot_int()
        tot_sp = 0.0
        order_weights: list[float] = []
        combined_weights: list[float] = []
        sp_values: list[float] = []
        for idx, r in enumerate(rows):
            for rk in _RESOURCE_KEYS:
                tot_loot[rk] += max(0, _safe_int(r["loot"][rk], 0))
            sp = _safe_float(r["loot_spice_eq"], 0.0)
            sp_values.append(sp)
            tot_sp += sp
            order_w = 0.65 ** ((n - 1) - idx)
            ts_i = _safe_float(r["attacked_ts"], 0.0)
            time_w = (
                math.exp(-max(0.0, ref_ts - ts_i) / (3.0 * 86400.0))
                if (ts_i > 0.0 and ref_ts > 0.0)
                else 0.75
            )
            order_weights.append(order_w)
            combined_weights.append(order_w * time_w)
        weight_tot = sum(combined_weights)
        if weight_tot <= 1e-12:
            weights = order_weights
            weight_tot = sum(weights)
        else:
            weights = combined_weights
        weighted_sum = _safe_float(sum(w * sp for w, sp in zip(weights, sp_values)), 0.0)
        avg_loot = {
            rk: round(game_data.safe_big_ratio(tot_loot[rk], n, cap=1e200), 3)
            for rk in _RESOURCE_KEYS
        }
        tot_sp_safe = _safe_float(tot_sp, 0.0)
        avg_sp_safe = _safe_float(tot_sp_safe / n, 0.0)
        wt_sp_safe = _safe_float(weighted_sum / max(1e-12, weight_tot), 0.0)
        return {
            "attempts": n,
            "wins": wins,
            "losses": losses,
            "success_rate": round(wins / n, 6),
            "total_loot": tot_loot,
            "avg_loot": avg_loot,
            "total_loot_spice_eq": round(tot_sp_safe, 3),
            "avg_loot_spice_eq": round(avg_sp_safe, 3),
            "recency_weighted_loot_spice_eq": round(wt_sp_safe, 3),
        }

    def _query_norm_raid_rows_locked(
        self,
        *,
        window_seconds: Optional[float] = None,
        now: Optional[float] = None,
    ) -> list[dict[str, Any]]:
        safe_window = _safe_optional_float(window_seconds)
        if safe_window is not None:
            safe_now = _safe_optional_float(now)
            ref_cutoff = safe_now if safe_now is not None else time.time()
            cutoff = ref_cutoff - max(0.0, safe_window)
            rows = self._conn.execute(
                """
                SELECT id, target_coords, attacked_ts, is_exempt, outcome, loot_json
                FROM raid_history
                WHERE attacked_ts >= ?
                ORDER BY attacked_ts ASC, id ASC
                """,
                (cutoff,),
            ).fetchall()
        else:
            rows = self._conn.execute(
                """
                SELECT id, target_coords, attacked_ts, is_exempt, outcome, loot_json
                FROM raid_history
                ORDER BY attacked_ts ASC, id ASC
                """
            ).fetchall()
        norm_rows: list[dict[str, Any]] = []
        for r in rows:
            loot = _normalize_loot(r["loot_json"])
            loot_spice_eq = round(_safe_float(game_data.spice_equivalent(loot), 0.0), 3)
            clean_outcome = _sanitize_text(r["outcome"])
            won = _is_win_outcome(clean_outcome, loot_spice_eq)
            raw_coords = _raw_text(r["target_coords"], "")
            norm_coords = normalize_coords(raw_coords) if raw_coords else ""
            norm_rows.append(
                {
                    "id": game_data.cap_json_int(_safe_int(r["id"], 0)),
                    "target_coords": norm_coords,
                    "attacked_ts": _safe_float(r["attacked_ts"], 0.0),
                    "is_exempt": bool(r["is_exempt"]),
                    "outcome": clean_outcome,
                    "loot": loot,
                    "loot_spice_eq": loot_spice_eq,
                    "won": won,
                }
            )
        return norm_rows

    def _get_full_history_summary_locked(self) -> dict[str, Any]:
        # 1. Raid history summary
        norm_raids = self._query_norm_raid_rows_locked()
        max_raid_ts = max((r["attacked_ts"] for r in norm_raids), default=0.0)
        raid_agg = self._aggregate_raid_rows(norm_raids, ref_ts=max_raid_ts)
        raid_targets_set = {r["target_coords"] for r in norm_raids if r["target_coords"]}
        last_attack = (
            {
                "target_coords": norm_raids[-1]["target_coords"],
                "attacked_ts": norm_raids[-1]["attacked_ts"],
                "outcome": norm_raids[-1]["outcome"],
                "loot": dict(norm_raids[-1]["loot"]),
                "loot_spice_eq": norm_raids[-1]["loot_spice_eq"],
            }
            if norm_raids
            else None
        )
        raid_summary = {
            "count": len(norm_raids),
            **raid_agg,
            "targets_count": len(raid_targets_set),
            "last_attack": last_attack,
        }

        # 2. Spy targets summary
        spy_rows = self._conn.execute(
            """
            SELECT is_inactive, is_spacia, last_spied_ts, pig_iron, metal, kryptonite, spice
            FROM spy_targets
            """
        ).fetchall()
        spy_count = len(spy_rows)
        inactive_count = sum(1 for r in spy_rows if bool(r["is_inactive"]))
        spacia_count = sum(1 for r in spy_rows if bool(r["is_spacia"]))
        spied_count = sum(1 for r in spy_rows if _safe_float(r["last_spied_ts"], 0.0) > 0.0)
        total_known_res_sp = round(
            _safe_float(
                sum(
                    _safe_float(
                        game_data.spice_equivalent(
                            {
                                "pig_iron": game_data.cap_json_int(_safe_int(r["pig_iron"], 0)),
                                "metal": game_data.cap_json_int(_safe_int(r["metal"], 0)),
                                "kryptonite": game_data.cap_json_int(_safe_int(r["kryptonite"], 0)),
                                "spice": game_data.cap_json_int(_safe_int(r["spice"], 0)),
                            }
                        ),
                        0.0,
                    )
                    for r in spy_rows
                ),
                0.0,
            ),
            3,
        )
        spy_summary = {
            "count": spy_count,
            "inactive_count": inactive_count,
            "spacia_count": spacia_count,
            "spied_count": spied_count,
            "total_known_resources_spice_eq": total_known_res_sp,
        }

        # 3. Galaxy scans & historical spy reports counts
        gal_scans_count = int(
            self._conn.execute("SELECT COUNT(*) FROM galaxy_scans").fetchone()[0]
        )
        spy_reports_count = int(
            self._conn.execute("SELECT COUNT(*) FROM spy_reports").fetchone()[0]
        )

        # 4. Planet snapshots summary
        p_rows = self._conn.execute(
            """
            SELECT ts, planet_id, coords, planet_type, fields_used, fields_max,
                   pig_iron, metal, kryptonite, spice, energy_produced, energy_used,
                   buildings_json, ships_json, economy_rates_json
            FROM planet_snapshots
            ORDER BY ts ASC, id ASC
            """
        ).fetchall()
        p_count = len(p_rows)
        first_by_pid: dict[int, dict[str, Any]] = {}
        latest_by_pid: dict[int, dict[str, Any]] = {}
        for r in p_rows:
            pid = game_data.cap_json_int(_safe_int(r["planet_id"], 0))
            bld_map = _safe_json_dict(r["buildings_json"])
            shp_map = _safe_json_dict(r["ships_json"])
            econ_map = _safe_json_dict(r["economy_rates_json"])
            res_dict = {
                "pig_iron": game_data.cap_json_int(_safe_int(r["pig_iron"], 0)),
                "metal": game_data.cap_json_int(_safe_int(r["metal"], 0)),
                "kryptonite": game_data.cap_json_int(_safe_int(r["kryptonite"], 0)),
                "spice": game_data.cap_json_int(_safe_int(r["spice"], 0)),
            }
            snap_info = {
                "planet_id": pid,
                "coords": _sanitize_text(_raw_text(r["coords"], "")),
                "planet_type": _sanitize_text(
                    normalize_planet_type(_raw_text(r["planet_type"], "normal") or "normal")
                ),
                "fields_used": game_data.cap_json_int(_safe_int(r["fields_used"], 0)),
                "fields_max": game_data.cap_json_int(_safe_int(r["fields_max"], 0)),
                "resources": res_dict,
                "resources_spice_eq": round(_safe_float(game_data.spice_equivalent(res_dict), 0.0), 3),
                "energy_produced": game_data.cap_json_int(_safe_int(r["energy_produced"], 0)),
                "energy_used": game_data.cap_json_int(_safe_int(r["energy_used"], 0)),
                "buildings_total_levels": game_data.cap_json_int(sum(_safe_int(v, 0) for v in bld_map.values())),
                "ships_total": game_data.cap_json_int(sum(_safe_int(v, 0) for v in shp_map.values())),
                "economy_rates": {
                    _sanitize_text(game_data.safe_key(k)): game_data.cap_json_int(_safe_int(v, 0))
                    for k, v in econ_map.items()
                },
            }
            if pid not in first_by_pid:
                first_by_pid[pid] = snap_info
            latest_by_pid[pid] = snap_info

        first_bld_lvls = sum(v["buildings_total_levels"] for v in first_by_pid.values())
        latest_bld_lvls = sum(v["buildings_total_levels"] for v in latest_by_pid.values())
        first_fields = sum(v["fields_used"] for v in first_by_pid.values())
        latest_fields = sum(v["fields_used"] for v in latest_by_pid.values())
        latest_e_net = sum(v["energy_produced"] - v["energy_used"] for v in latest_by_pid.values())
        first_res_sp = round(_safe_float(sum(v["resources_spice_eq"] for v in first_by_pid.values()), 0.0), 3)
        latest_res_sp = round(_safe_float(sum(v["resources_spice_eq"] for v in latest_by_pid.values()), 0.0), 3)
        latest_by_planet_clean = {
            str(pid): {k: v for k, v in info.items() if k != "resources_spice_eq"}
            for pid, info in latest_by_pid.items()
        }
        planet_summary = {
            "count": p_count,
            "distinct_planets": len(latest_by_pid),
            "first_ts": _safe_float(p_rows[0]["ts"], 0.0) if p_rows else None,
            "latest_ts": _safe_float(p_rows[-1]["ts"], 0.0) if p_rows else None,
            "latest_by_planet": latest_by_planet_clean,
            "trend": {
                "first_total_buildings_levels": first_bld_lvls,
                "latest_total_buildings_levels": latest_bld_lvls,
                "building_levels_delta": latest_bld_lvls - first_bld_lvls,
                "first_fields_used": first_fields,
                "latest_fields_used": latest_fields,
                "fields_used_delta": latest_fields - first_fields,
                "latest_energy_net": latest_e_net,
                "first_resources_spice_eq": first_res_sp,
                "latest_resources_spice_eq": latest_res_sp,
                "resources_spice_eq_delta": round(_safe_float(latest_res_sp - first_res_sp, 0.0), 3),
            },
        }

        # 5. Rank snapshots summary (strictly without nickname/user_id PII)
        rk_rows = self._conn.execute(
            """
            SELECT ts, server_points, server_rank, estimated_points,
                   pts_per_hr, spice_value, planets
            FROM rank_snapshots
            ORDER BY ts ASC, id ASC
            """
        ).fetchall()
        rk_count = len(rk_rows)
        est_vals = [_safe_float(r["estimated_points"], 0.0) for r in rk_rows]
        sp_vals = [
            game_data.cap_json_int(v)
            for r in rk_rows
            if (v := _safe_optional_int(r["server_points"])) is not None
        ]
        sr_vals = [
            game_data.cap_json_int(v)
            for r in rk_rows
            if (v := _safe_optional_int(r["server_rank"])) is not None
        ]
        pph_vals = [_safe_float(r["pts_per_hr"], 0.0) for r in rk_rows]
        spice_vals = [_safe_float(r["spice_value"], 0.0) for r in rk_rows]

        est_delta = round(_safe_float(est_vals[-1] - est_vals[0], 0.0), 3) if rk_count >= 1 else 0.0
        srv_pts_delta = (sp_vals[-1] - sp_vals[0]) if len(sp_vals) >= 1 else 0
        rank_climbed = (sr_vals[0] - sr_vals[-1]) if len(sr_vals) >= 1 else 0
        pph_delta = round(_safe_float(pph_vals[-1] - pph_vals[0], 0.0), 3) if rk_count >= 1 else 0.0
        latest_pph = round(_safe_float(pph_vals[-1], 0.0), 3) if rk_count >= 1 else 0.0
        rank_trajectory = [
            {
                "ts": _safe_float(r["ts"], 0.0),
                "estimated_points": _safe_float(r["estimated_points"], 0.0),
                "server_points": (
                    game_data.cap_json_int(sp_v)
                    if (sp_v := _safe_optional_int(r["server_points"])) is not None
                    else None
                ),
                "server_rank": (
                    game_data.cap_json_int(sr_v)
                    if (sr_v := _safe_optional_int(r["server_rank"])) is not None
                    else None
                ),
                "pts_per_hr": _safe_float(r["pts_per_hr"], 0.0),
                "spice_value": _safe_float(r["spice_value"], 0.0),
                "planets": game_data.cap_json_int(max(1, _safe_int(r["planets"], 1))),
            }
            for r in rk_rows[-240:]
        ]
        rank_summary = {
            "count": rk_count,
            "first_ts": _safe_float(rk_rows[0]["ts"], 0.0) if rk_rows else None,
            "latest_ts": _safe_float(rk_rows[-1]["ts"], 0.0) if rk_rows else None,
            "first_estimated_points": round(est_vals[0], 3) if est_vals else 0.0,
            "latest_estimated_points": round(est_vals[-1], 3) if est_vals else 0.0,
            "min_estimated_points": round(min(est_vals), 3) if est_vals else 0.0,
            "max_estimated_points": round(max(est_vals), 3) if est_vals else 0.0,
            "estimated_points_delta": est_delta,
            "first_server_points": sp_vals[0] if sp_vals else None,
            "latest_server_points": sp_vals[-1] if sp_vals else None,
            "min_server_points": min(sp_vals) if sp_vals else None,
            "max_server_points": max(sp_vals) if sp_vals else None,
            "server_points_delta": srv_pts_delta,
            "first_server_rank": sr_vals[0] if sr_vals else None,
            "latest_server_rank": sr_vals[-1] if sr_vals else None,
            "best_server_rank": min(sr_vals) if sr_vals else None,
            "worst_server_rank": max(sr_vals) if sr_vals else None,
            "rank_climbed": rank_climbed,
            "first_pts_per_hr": round(pph_vals[0], 3) if pph_vals else 0.0,
            "latest_pts_per_hr": latest_pph,
            "min_pts_per_hr": round(min(pph_vals), 3) if pph_vals else 0.0,
            "max_pts_per_hr": round(max(pph_vals), 3) if pph_vals else 0.0,
            "avg_pts_per_hr": round(_safe_float(sum(pph_vals) / len(pph_vals), 0.0), 3) if pph_vals else 0.0,
            "pts_per_hr_delta": pph_delta,
            "latest_spice_value": round(spice_vals[-1], 3) if spice_vals else 0.0,
            "spice_value_delta": round(_safe_float(spice_vals[-1] - spice_vals[0], 0.0), 3) if spice_vals else 0.0,
            "trajectory": rank_trajectory,
        }

        # 6. Action history summary
        act_rows = self._conn.execute(
            """
            SELECT kind, executed, dry_run
            FROM action_history
            """
        ).fetchall()
        act_total = len(act_rows)
        act_executed = 0
        act_failed = 0
        act_dry = 0
        by_kind: dict[str, dict[str, int]] = {}
        for r in act_rows:
            kind = _sanitize_text(_raw_text(r["kind"], "UNKNOWN") or "UNKNOWN")
            ex = bool(r["executed"])
            dry = bool(r["dry_run"])
            bucket = by_kind.setdefault(kind, {"total": 0, "executed": 0, "failed": 0, "dry_run": 0})
            bucket["total"] += 1
            if ex:
                act_executed += 1
                bucket["executed"] += 1
            elif dry:
                act_dry += 1
                bucket["dry_run"] += 1
            else:
                act_failed += 1
                bucket["failed"] += 1
        live_attempts = act_total - act_dry
        act_sr = round(act_executed / max(1, live_attempts), 4) if live_attempts > 0 else 0.0
        action_summary = {
            "total": act_total,
            "executed": act_executed,
            "failed": act_failed,
            "dry_run": act_dry,
            "success_rate": act_sr,
            "by_kind": by_kind,
        }

        points_delta_val = _safe_float(srv_pts_delta, 0.0) if srv_pts_delta != 0 else est_delta
        return _sanitize_obj(
            {
                "raid_history": raid_summary,
                "spy_targets": spy_summary,
                "planet_snapshots": planet_summary,
                "rank_snapshots": rank_summary,
                "action_history": action_summary,
                "rank_snapshots_count": rk_count,
                "planet_snapshots_count": p_count,
                "action_history_count": act_total,
                "spy_targets_count": spy_count,
                "spy_reports_count": spy_reports_count,
                "galaxy_scans_count": gal_scans_count,
                "points_delta": round(points_delta_val, 3),
                "points_trend": round(points_delta_val, 3),
                "pts_per_hr": latest_pph,
                "economy_trend": pph_delta,
                "action_count": act_total,
                "action_successes": act_executed,
                "action_success_rate": act_sr,
            }
        )

    def get_full_history_summary(self) -> dict[str, Any]:
        with self._lock:
            return self._get_full_history_summary_locked()

    def _get_raid_stats_locked(
        self,
        *,
        window_seconds: Optional[float] = None,
        now: Optional[float] = None,
    ) -> dict[str, Any]:
        norm_rows = self._query_norm_raid_rows_locked(window_seconds=window_seconds, now=now)
        max_row_ts = max((r["attacked_ts"] for r in norm_rows), default=0.0)
        ref_ts = _safe_float(now, max_row_ts) if (now is not None and _safe_float(now, 0.0) > 0.0) else max_row_ts

        overall = self._aggregate_raid_rows(norm_rows, ref_ts=ref_ts)
        if norm_rows:
            last_r = norm_rows[-1]
            overall["last_attack"] = {
                "target_coords": last_r["target_coords"],
                "attacked_ts": last_r["attacked_ts"],
                "outcome": last_r["outcome"],
                "loot": dict(last_r["loot"]),
                "loot_spice_eq": last_r["loot_spice_eq"],
            }
        else:
            overall["last_attack"] = None

        grouped: dict[str, list[dict[str, Any]]] = {}
        for r in norm_rows:
            c = r["target_coords"]
            if c:
                grouped.setdefault(c, []).append(r)

        per_target: dict[str, dict[str, Any]] = {}
        for coords, t_rows in grouped.items():
            t_agg = self._aggregate_raid_rows(t_rows, ref_ts=ref_ts)
            t_last = t_rows[-1]
            t_agg["last_outcome"] = t_last["outcome"]
            t_agg["last_attacked_ts"] = t_last["attacked_ts"]
            t_agg["last_loot"] = dict(t_last["loot"])
            t_agg["last_loot_spice_eq"] = t_last["loot_spice_eq"]
            per_target[coords] = t_agg

        bash_block_rows = self._conn.execute(
            """
            SELECT id, target_coords, blocked_ts, blocked_until_ts, cooldown_seconds, is_bash, reason
            FROM bash_blocks
            ORDER BY blocked_ts ASC, id ASC
            """
        ).fetchall()
        bash_blocks_list = [
            {
                "id": game_data.cap_json_int(_safe_int(br["id"], 0)),
                "target_coords": normalize_coords(_raw_text(br["target_coords"], "")),
                "blocked_ts": _safe_float(br["blocked_ts"], 0.0),
                "blocked_until_ts": _safe_float(br["blocked_until_ts"], 0.0),
                "cooldown_seconds": _safe_float(br["cooldown_seconds"], 0.0),
                "is_bash": bool(br["is_bash"]),
                "reason": _sanitize_text(_raw_text(br["reason"], "")),
            }
            for br in bash_block_rows
        ]
        active_now = _safe_float(now, time.time()) if now is not None else time.time()
        blocked_spy_rows = self._conn.execute(
            """
            SELECT coords, blocked_until_ts, block_reason, bash_hits, last_blocked_ts
            FROM spy_targets
            WHERE blocked_until_ts > ?
            ORDER BY blocked_until_ts DESC
            """,
            (active_now,),
        ).fetchall()
        blocked_targets: dict[str, dict[str, Any]] = {}
        for sr in blocked_spy_rows:
            bc = normalize_coords(_raw_text(sr["coords"], ""))
            if not bc:
                continue
            b_until = _safe_float(sr["blocked_until_ts"], 0.0)
            blocked_targets[bc] = {
                "blocked_until_ts": b_until,
                "remaining_seconds": round(max(0.0, b_until - active_now), 1),
                "reason": _sanitize_text(_raw_text(sr["block_reason"], "")),
                "bash_hits": game_data.cap_json_int(max(0, _safe_int(sr["bash_hits"], 0))),
                "last_blocked_ts": _safe_float(sr["last_blocked_ts"], 0.0),
            }

        telemetry_summary = self._get_full_history_summary_locked()
        return _sanitize_obj(
            {
                "overall": overall,
                "per_target": per_target,
                "history": norm_rows,
                "bash_blocks": bash_blocks_list,
                "blocked_targets": blocked_targets,
                "telemetry_summary": telemetry_summary,
            }
        )

    def get_raid_stats(
        self,
        *,
        window_seconds: Optional[float] = None,
        now: Optional[float] = None,
    ) -> dict[str, Any]:
        with self._lock:
            return self._get_raid_stats_locked(window_seconds=window_seconds, now=now)

    def _sync_attack_history_file_locked(self) -> Optional[Path]:
        if self.attack_history_path is None:
            return None
        raw_str = _raw_text(self.attack_history_path, "").strip()
        if not raw_str or raw_str == ".":
            return None
        target = Path(raw_str)
        if target.exists() and target.is_dir():
            return None
        target.parent.mkdir(parents=True, exist_ok=True)
        stats = self._get_raid_stats_locked()
        payload = _sanitize_obj(
            {
                "version": 1,
                "schema_version": ATTACK_HISTORY_SCHEMA_VERSION,
                "db_schema_version": LATEST_SCHEMA_VERSION,
                "updated_at": round(time.time(), 3),
                "overall": stats["overall"],
                "per_target": stats["per_target"],
                "history": stats["history"],
                "bash_blocks": stats["bash_blocks"],
                "blocked_targets": stats["blocked_targets"],
                "telemetry_summary": stats["telemetry_summary"],
            }
        )
        tmp_path: Optional[Path] = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="w",
                encoding="utf-8",
                dir=str(target.parent),
                prefix=f".{target.stem}.",
                suffix=".tmp",
                delete=False,
            ) as tf:
                tmp_path = Path(tf.name)
                json.dump(payload, tf, indent=2, sort_keys=True, allow_nan=False)
                tf.write("\n")
                tf.flush()
                os.fsync(tf.fileno())
            os.replace(tmp_path, target)
            return target
        except Exception:
            if tmp_path is not None:
                try:
                    tmp_path.unlink(missing_ok=True)
                except OSError:
                    pass
            raise

    def sync_attack_history_file(self) -> Optional[Path]:
        with self._lock:
            return self._sync_attack_history_file_locked()

    def load_bash_tracker(self, *, now: Optional[float] = None) -> BashTracker:
        ref = _safe_float(now, time.time()) if now is not None else time.time()
        cutoff = ref - 24 * 3600
        skew_max = ref + 60.0
        with self._lock:
            rows = self._conn.execute(
                """
                SELECT target_coords, attacked_ts, is_exempt
                FROM raid_history WHERE attacked_ts >= ? ORDER BY attacked_ts ASC
                """,
                (cutoff,),
            ).fetchall()
            blocked_rows = self._conn.execute(
                """
                SELECT coords, blocked_until_ts, block_reason, last_blocked_ts
                FROM spy_targets WHERE blocked_until_ts > ?
                """,
                (ref,),
            ).fetchall()
        tracker = BashTracker()
        for r in rows:
            if not bool(r["is_exempt"]):
                ts_val = _safe_float(r["attacked_ts"], 0.0)
                if cutoff <= ts_val <= skew_max:
                    tracker.record_attack(
                        normalize_coords(_raw_text(r["target_coords"], "")),
                        timestamp=ts_val,
                    )
        for br in blocked_rows:
            last_b_ts = _safe_float(br["last_blocked_ts"], 0.0)
            if last_b_ts > 0.0 and last_b_ts > skew_max:
                continue
            until_val = _safe_float(br["blocked_until_ts"], 0.0)
            if until_val > ref:
                tracker.record_block(
                    normalize_coords(_raw_text(br["coords"], "")),
                    until_val,
                    reason=_sanitize_text(_raw_text(br["block_reason"], "")),
                )
        return tracker

    def get_recent_actions(self, *, limit: int = 25) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._conn.execute(
                """
                SELECT id, ts, source, command, priority, kind, planet_coords,
                       target_name, target_level, executed, dry_run, message
                FROM action_history ORDER BY id DESC LIMIT ?
                """,
                (max(1, _sql_int(limit, 25)),),
            ).fetchall()
        return [_sanitize_obj(dict(r)) for r in rows]

    def get_planet_history(self, planet_id: int, *, limit: int = 50) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._conn.execute(
                """
                SELECT ts, planet_id, coords, name, pig_iron, metal, kryptonite, spice,
                       energy_produced, energy_used, buildings_json, economy_rates_json
                FROM planet_snapshots WHERE planet_id = ? ORDER BY id DESC LIMIT ?
                """,
                (_sql_int(planet_id, 0), max(1, _sql_int(limit, 50))),
            ).fetchall()
        out = []
        for r in rows:
            item = {
                "ts": _safe_float(r["ts"], 0.0),
                "planet_id": game_data.cap_json_int(_safe_int(r["planet_id"], 0)),
                "coords": _sanitize_text(_raw_text(r["coords"], "")),
                "name": _sanitize_text(_raw_text(r["name"], "")),
                "pig_iron": game_data.cap_json_int(_safe_int(r["pig_iron"], 0)),
                "metal": game_data.cap_json_int(_safe_int(r["metal"], 0)),
                "kryptonite": game_data.cap_json_int(_safe_int(r["kryptonite"], 0)),
                "spice": game_data.cap_json_int(_safe_int(r["spice"], 0)),
                "energy_produced": game_data.cap_json_int(_safe_int(r["energy_produced"], 0)),
                "energy_used": game_data.cap_json_int(_safe_int(r["energy_used"], 0)),
                "buildings": _safe_json_dict(r["buildings_json"]),
                "economy_rates": _safe_json_dict(r["economy_rates_json"]),
            }
            out.append(_sanitize_obj(item))
        return out

    def get_tutorial_history(self) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._conn.execute(
                """
                SELECT step, total_steps, tasks_json, reward_text, can_complete,
                       first_seen_ts, last_seen_ts, completed_ts
                FROM tutorial_progress ORDER BY step ASC
                """
            ).fetchall()
        out = []
        for r in rows:
            item = dict(r)
            item["tasks"] = _safe_json_list(item.pop("tasks_json", "[]"))
            item["can_complete"] = bool(item["can_complete"])
            out.append(_sanitize_obj(item))
        return out

    # ------------------------------------------------------------------
    # Manual Command Queue (append-only / status-versioned, zero DELETEs)
    # ------------------------------------------------------------------

    @staticmethod
    def _normalize_queue_priority(raw: Any, default: str = "P1") -> str:
        s = _sanitize_text(raw if raw is not None else default).strip().upper()
        return s if s in ("P0", "P1", "P2", "P3") else default

    @staticmethod
    def _row_to_manual_queue_dict(r: sqlite3.Row) -> dict[str, Any]:
        tid = _safe_optional_int(r["target_id"])
        tlvl = _safe_optional_int(r["target_level"])
        comp_ts = _safe_optional_float(r["completed_ts"])
        canc_ts = _safe_optional_float(r["canceled_ts"])
        raw_tc = _raw_text(r["target_coords"], "").strip()
        norm_tc = normalize_coords(raw_tc) if raw_tc else ""
        if not norm_tc and raw_tc:
            norm_tc = _sanitize_text(raw_tc)[:32]
        raw_pc = _raw_text(r["planet_coords"], "").strip()
        norm_pc = normalize_coords(raw_pc) if raw_pc else ""
        return _sanitize_obj(
            {
                "id": game_data.cap_json_int(_safe_int(r["id"], 0)),
                "created_ts": _safe_float(r["created_ts"], 0.0),
                "updated_ts": _safe_float(r["updated_ts"], 0.0),
                "status": _sanitize_text(r["status"] or "pending"),
                "priority": GameDatabase._normalize_queue_priority(r["priority"], "P1"),
                "sort_rank": game_data.cap_json_int(_safe_int(r["sort_rank"], 1000)),
                "kind": _sanitize_text(r["kind"] or "").upper(),
                "planet_id": game_data.cap_json_int(max(0, _safe_int(r["planet_id"], 0))),
                "planet_coords": norm_pc,
                "target_id": game_data.cap_json_int(tid) if tid is not None else None,
                "target_name": _sanitize_text(r["target_name"] or ""),
                "target_coords": norm_tc,
                "target_level": game_data.cap_json_int(tlvl) if tlvl is not None else None,
                "note": _sanitize_text(r["note"] or ""),
                "completed_ts": comp_ts,
                "canceled_ts": canc_ts,
                "result_message": _sanitize_text(r["result_message"] or ""),
            }
        )

    def _record_manual_queue_event_locked(
        self,
        queue_id: int,
        event_type: str,
        details: Mapping[str, Any],
        now_ts: float,
    ) -> None:
        self._conn.execute(
            """
            INSERT INTO manual_queue_events(ts, queue_id, event_type, details_json)
            VALUES (?, ?, ?, ?)
            """,
            (
                now_ts,
                _sql_int(queue_id, 0),
                _sanitize_text(event_type)[:40],
                _json_dumps(dict(details)),
            ),
        )

    def _ordered_pending_queue_rows_locked(self) -> list[sqlite3.Row]:
        return self._conn.execute(
            """
            SELECT *
            FROM manual_queue
            WHERE status = 'pending'
            ORDER BY
                CASE priority WHEN 'P0' THEN 0 WHEN 'P1' THEN 1 WHEN 'P2' THEN 2 ELSE 3 END ASC,
                sort_rank ASC,
                id ASC
            """
        ).fetchall()

    def get_manual_queue(
        self,
        *,
        status: Optional[str] = "pending",
        limit: int = 200,
    ) -> list[dict[str, Any]]:
        lim = max(1, min(1000, _sql_int(limit, 200)))
        norm_status = _sanitize_text(status).strip().lower() if isinstance(status, str) and status.strip() else None
        with self._lock:
            if norm_status == "pending":
                rows = self._conn.execute(
                    """
                    SELECT *
                    FROM manual_queue
                    WHERE status = 'pending'
                    ORDER BY
                        CASE priority WHEN 'P0' THEN 0 WHEN 'P1' THEN 1 WHEN 'P2' THEN 2 ELSE 3 END ASC,
                        sort_rank ASC,
                        id ASC
                    LIMIT ?
                    """,
                    (lim,),
                ).fetchall()
            elif norm_status and norm_status != "all":
                rows = self._conn.execute(
                    """
                    SELECT *
                    FROM manual_queue
                    WHERE status = ?
                    ORDER BY updated_ts DESC, id DESC
                    LIMIT ?
                    """,
                    (norm_status, lim),
                ).fetchall()
            else:
                rows = self._conn.execute(
                    """
                    SELECT *
                    FROM manual_queue
                    ORDER BY
                        CASE WHEN status = 'pending' THEN 0 ELSE 1 END ASC,
                        CASE priority WHEN 'P0' THEN 0 WHEN 'P1' THEN 1 WHEN 'P2' THEN 2 ELSE 3 END ASC,
                        sort_rank ASC,
                        updated_ts DESC,
                        id DESC
                    LIMIT ?
                    """,
                    (lim,),
                ).fetchall()
        return [self._row_to_manual_queue_dict(r) for r in rows]

    def enqueue_manual_command(
        self,
        *,
        kind: str,
        priority: str = "P1",
        position: str = "top",
        planet_id: int = 0,
        planet_coords: str = "",
        target_id: Optional[int] = None,
        target_name: str = "",
        target_coords: str = "",
        target_level: Optional[int] = None,
        note: str = "",
        deduplicate_pending: bool = False,
        ts: Optional[float] = None,
    ) -> dict[str, Any]:
        now_ts = round(_safe_float(ts, time.time()) if ts is not None else time.time(), 3)
        norm_kind = _sanitize_text(kind).strip().upper()[:40]
        norm_prio = self._normalize_queue_priority(priority, "P1")
        norm_pos = _sanitize_text(position).strip().lower()
        pid_i = max(0, _sql_int(planet_id, 0))
        norm_pc = normalize_coords(_raw_text(planet_coords, "").strip()) if planet_coords else ""
        tid_i = _sql_optional_int(target_id)
        tlvl_i = _sql_optional_int(target_level)
        raw_tc = _raw_text(target_coords, "").strip()
        norm_tc = normalize_coords(raw_tc) if raw_tc else ""
        if not norm_tc and raw_tc:
            norm_tc = _sanitize_text(raw_tc)[:32]
        tname_s = _sanitize_text(target_name).strip()[:160]
        note_s = _sanitize_text(note).strip()[:300]

        with self._lock:
            if deduplicate_pending:
                dup = self._conn.execute(
                    """
                    SELECT *
                    FROM manual_queue
                    WHERE status = 'pending'
                      AND kind = ?
                      AND planet_id = ?
                      AND COALESCE(target_id, -1) = COALESCE(?, -1)
                      AND target_coords = ?
                      AND COALESCE(target_level, -1) = COALESCE(?, -1)
                    ORDER BY id ASC
                    LIMIT 1
                    """,
                    (norm_kind, pid_i, tid_i, norm_tc, tlvl_i),
                ).fetchone()
                if dup is not None:
                    dup_id = int(dup["id"])
                    band_ranks = self._conn.execute(
                        "SELECT MIN(sort_rank) AS min_r, MAX(sort_rank) AS max_r FROM manual_queue WHERE status = 'pending' AND priority = ? AND id != ?",
                        (norm_prio, dup_id),
                    ).fetchone()
                    if norm_pos == "bottom":
                        new_rank = (_safe_int(band_ranks["max_r"], 1000) + 10) if band_ranks and band_ranks["max_r"] is not None else 1000
                    else:
                        new_rank = (_safe_int(band_ranks["min_r"], 1000) - 10) if band_ranks and band_ranks["min_r"] is not None else 1000
                    self._conn.execute(
                        """
                        UPDATE manual_queue
                        SET updated_ts = ?, priority = ?, sort_rank = ?,
                            note = CASE WHEN ? != '' THEN ? ELSE note END
                        WHERE id = ?
                        """,
                        (now_ts, norm_prio, new_rank, note_s, note_s, dup_id),
                    )
                    self._record_manual_queue_event_locked(
                        dup_id,
                        "reprioritized",
                        {"priority": norm_prio, "sort_rank": new_rank, "position": norm_pos or "top"},
                        now_ts,
                    )
                    self._conn.commit()
                    updated_row = self._conn.execute("SELECT * FROM manual_queue WHERE id = ?", (dup_id,)).fetchone()
                    return self._row_to_manual_queue_dict(updated_row)

            band_ranks = self._conn.execute(
                "SELECT MIN(sort_rank) AS min_r, MAX(sort_rank) AS max_r FROM manual_queue WHERE status = 'pending' AND priority = ?",
                (norm_prio,),
            ).fetchone()
            if norm_pos == "bottom":
                sort_rank = (_safe_int(band_ranks["max_r"], 1000) + 10) if band_ranks and band_ranks["max_r"] is not None else 1000
            else:
                sort_rank = (_safe_int(band_ranks["min_r"], 1000) - 10) if band_ranks and band_ranks["min_r"] is not None else 1000

            cur = self._conn.execute(
                """
                INSERT INTO manual_queue(
                    created_ts, updated_ts, status, priority, sort_rank,
                    kind, planet_id, planet_coords, target_id, target_name,
                    target_coords, target_level, note
                ) VALUES (?, ?, 'pending', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    now_ts,
                    now_ts,
                    norm_prio,
                    sort_rank,
                    norm_kind,
                    pid_i,
                    norm_pc,
                    tid_i,
                    tname_s,
                    norm_tc,
                    tlvl_i,
                    note_s,
                ),
            )
            qid = int(cur.lastrowid or 0)
            self._record_manual_queue_event_locked(
                qid,
                "enqueued",
                {
                    "kind": norm_kind,
                    "priority": norm_prio,
                    "sort_rank": sort_rank,
                    "planet_id": pid_i,
                    "target_id": tid_i,
                    "target_name": tname_s,
                    "target_coords": norm_tc,
                    "target_level": tlvl_i,
                },
                now_ts,
            )
            self._conn.commit()
            row = self._conn.execute("SELECT * FROM manual_queue WHERE id = ?", (qid,)).fetchone()
            return self._row_to_manual_queue_dict(row)

    def update_manual_queue_item(
        self,
        queue_id: int,
        *,
        priority: Optional[str] = None,
        move: Optional[str] = None,
        sort_rank: Optional[int] = None,
        target_level: Any = ...,
        note: Optional[str] = None,
        ts: Optional[float] = None,
    ) -> Optional[dict[str, Any]]:
        qid = _sql_int(queue_id, 0)
        if qid <= 0:
            return None
        now_ts = round(_safe_float(ts, time.time()) if ts is not None else time.time(), 3)
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM manual_queue WHERE id = ? LIMIT 1",
                (qid,),
            ).fetchone()
            if row is None:
                return None

            new_prio = (
                self._normalize_queue_priority(priority, _raw_text(row["priority"], "P1"))
                if priority is not None
                else self._normalize_queue_priority(row["priority"], "P1")
            )
            new_tlvl = (
                _sql_optional_int(row["target_level"])
                if target_level is ...
                else _sql_optional_int(target_level)
            )
            new_note = _sanitize_text(note)[:300] if note is not None else _sanitize_text(row["note"] or "")

            if priority is not None or target_level is not ... or note is not None or sort_rank is not None:
                explicit_rank = (
                    _sql_int(sort_rank, _safe_int(row["sort_rank"], 1000))
                    if sort_rank is not None
                    else _safe_int(row["sort_rank"], 1000)
                )
                self._conn.execute(
                    """
                    UPDATE manual_queue
                    SET updated_ts = ?, priority = ?, sort_rank = ?, target_level = ?, note = ?
                    WHERE id = ?
                    """,
                    (now_ts, new_prio, explicit_rank, new_tlvl, new_note, qid),
                )

            move_s = _sanitize_text(move).strip().lower() if isinstance(move, str) and move.strip() else ""
            if move_s in ("top", "bottom", "up", "down") and _raw_text(row["status"], "") == "pending":
                pending_rows = self._ordered_pending_queue_rows_locked()
                ordered_ids = [int(r["id"]) for r in pending_rows]
                prio_by_id = {int(r["id"]): self._normalize_queue_priority(r["priority"], "P1") for r in pending_rows}
                if qid in ordered_ids:
                    idx = ordered_ids.index(qid)
                    cur_p = prio_by_id[qid]
                    if move_s == "top":
                        band_indices = [i for i, mid in enumerate(ordered_ids) if prio_by_id[mid] == cur_p]
                        first_idx = band_indices[0] if band_indices else 0
                        ordered_ids.pop(idx)
                        ordered_ids.insert(first_idx, qid)
                    elif move_s == "bottom":
                        band_indices = [i for i, mid in enumerate(ordered_ids) if prio_by_id[mid] == cur_p]
                        last_idx = band_indices[-1] if band_indices else len(ordered_ids) - 1
                        ordered_ids.pop(idx)
                        ordered_ids.insert(last_idx, qid)
                    elif move_s == "up" and idx > 0:
                        neighbor_id = ordered_ids[idx - 1]
                        prio_by_id[qid] = prio_by_id[neighbor_id]
                        ordered_ids[idx - 1], ordered_ids[idx] = ordered_ids[idx], ordered_ids[idx - 1]
                    elif move_s == "down" and idx < len(ordered_ids) - 1:
                        neighbor_id = ordered_ids[idx + 1]
                        prio_by_id[qid] = prio_by_id[neighbor_id]
                        ordered_ids[idx + 1], ordered_ids[idx] = ordered_ids[idx], ordered_ids[idx + 1]

                    for pos_i, mid in enumerate(ordered_ids):
                        self._conn.execute(
                            "UPDATE manual_queue SET updated_ts = ?, priority = ?, sort_rank = ? WHERE id = ?",
                            (now_ts, prio_by_id[mid], (pos_i + 1) * 10, mid),
                        )

            updated_row = self._conn.execute(
                "SELECT * FROM manual_queue WHERE id = ?",
                (qid,),
            ).fetchone()
            self._record_manual_queue_event_locked(
                qid,
                "reordered" if move_s else "updated",
                {
                    "priority": updated_row["priority"],
                    "sort_rank": updated_row["sort_rank"],
                    "move": move_s or None,
                    "target_level": updated_row["target_level"],
                },
                now_ts,
            )
            self._conn.commit()
            return self._row_to_manual_queue_dict(updated_row)

    def cancel_manual_queue_item(
        self,
        queue_id: int,
        *,
        reason: str = "Canceled by operator",
        ts: Optional[float] = None,
    ) -> bool:
        qid = _sql_int(queue_id, 0)
        if qid <= 0:
            return False
        now_ts = round(_safe_float(ts, time.time()) if ts is not None else time.time(), 3)
        rsn_s = _sanitize_text(reason)[:240]
        with self._lock:
            cur = self._conn.execute(
                """
                UPDATE manual_queue
                SET status = 'canceled', updated_ts = ?, canceled_ts = ?, result_message = ?
                WHERE id = ? AND status = 'pending'
                """,
                (now_ts, now_ts, rsn_s, qid),
            )
            if (cur.rowcount or 0) <= 0:
                return False
            self._record_manual_queue_event_locked(qid, "canceled", {"reason": rsn_s}, now_ts)
            self._conn.commit()
            return True

    def clear_manual_queue(
        self,
        *,
        reason: str = "Cleared by operator",
        ts: Optional[float] = None,
    ) -> int:
        now_ts = round(_safe_float(ts, time.time()) if ts is not None else time.time(), 3)
        rsn_s = _sanitize_text(reason)[:240]
        with self._lock:
            pending_rows = self._conn.execute(
                "SELECT id FROM manual_queue WHERE status = 'pending'"
            ).fetchall()
            if not pending_rows:
                return 0
            self._conn.execute(
                """
                UPDATE manual_queue
                SET status = 'canceled', updated_ts = ?, canceled_ts = ?, result_message = ?
                WHERE status = 'pending'
                """,
                (now_ts, now_ts, rsn_s),
            )
            for r in pending_rows:
                self._record_manual_queue_event_locked(int(r["id"]), "canceled", {"reason": rsn_s}, now_ts)
            self._conn.commit()
            return len(pending_rows)

    def complete_manual_queue_item(
        self,
        queue_id: int,
        *,
        message: str = "",
        ts: Optional[float] = None,
    ) -> bool:
        qid = _sql_int(queue_id, 0)
        if qid <= 0:
            return False
        now_ts = round(_safe_float(ts, time.time()) if ts is not None else time.time(), 3)
        msg_s = _sanitize_text(message)[:300]
        with self._lock:
            cur = self._conn.execute(
                """
                UPDATE manual_queue
                SET status = 'completed', updated_ts = ?, completed_ts = ?, result_message = ?
                WHERE id = ? AND status = 'pending'
                """,
                (now_ts, now_ts, msg_s, qid),
            )
            if (cur.rowcount or 0) <= 0:
                return False
            self._record_manual_queue_event_locked(qid, "completed", {"message": msg_s}, now_ts)
            self._conn.commit()
            return True

    def complete_matching_manual_command(
        self,
        *,
        kind: str,
        planet_id: Optional[int] = None,
        target_id: Optional[int] = None,
        target_coords: str = "",
        executed_level: Optional[int] = None,
        executed_qty: Optional[int] = None,
        message: str = "",
        ts: Optional[float] = None,
    ) -> Optional[int]:
        norm_kind = _sanitize_text(kind).strip().upper()
        if not norm_kind:
            return None
        pid_i = max(0, _sql_int(planet_id, 0)) if planet_id is not None else 0
        tid_i = _sql_optional_int(target_id)
        raw_tc = _raw_text(target_coords, "").strip()
        norm_tc = normalize_coords(raw_tc) if raw_tc else ""
        if not norm_tc and raw_tc:
            norm_tc = _sanitize_text(raw_tc)[:32]
        now_ts = round(_safe_float(ts, time.time()) if ts is not None else time.time(), 3)
        msg_s = _sanitize_text(message)[:300]

        with self._lock:
            pending_rows = self._ordered_pending_queue_rows_locked()
            matched: Optional[sqlite3.Row] = None
            for r in pending_rows:
                if _raw_text(r["kind"], "").upper() != norm_kind:
                    continue
                r_pid = _safe_int(r["planet_id"], 0)
                if norm_kind != "RESEARCH" and r_pid > 0 and pid_i > 0 and r_pid != pid_i:
                    continue
                r_tid = _safe_optional_int(r["target_id"])
                r_tc = _raw_text(r["target_coords"], "").strip()
                norm_r_tc = normalize_coords(r_tc) if r_tc else r_tc
                if norm_kind in ("BUILD", "DEMOLISH_BUILDING", "RESEARCH", "BUILD_SHIP", "THROTTLE_BUILDING"):
                    if tid_i is not None and r_tid != tid_i:
                        continue
                elif norm_kind in ("SPY", "ATTACK", "RECYCLE", "COLONIZE"):
                    if norm_tc and norm_r_tc != norm_tc:
                        continue
                elif norm_kind == "SCAN_GALAXY":
                    if norm_tc and norm_r_tc and not norm_r_tc.startswith(norm_tc[:6]) and not norm_tc.startswith(norm_r_tc[:6]):
                        continue
                matched = r
                break

            if matched is None:
                return None

            qid = int(matched["id"])
            goal_lvl = _safe_optional_int(matched["target_level"])
            if norm_kind in ("BUILD", "RESEARCH") and goal_lvl is not None and executed_level is not None:
                if int(executed_level) < int(goal_lvl):
                    self._conn.execute(
                        "UPDATE manual_queue SET updated_ts = ?, result_message = ? WHERE id = ?",
                        (now_ts, f"Progressed L{int(executed_level)}/{int(goal_lvl)}: {msg_s}"[:300], qid),
                    )
                    self._record_manual_queue_event_locked(
                        qid,
                        "progressed",
                        {"executed_level": int(executed_level), "target_level": int(goal_lvl)},
                        now_ts,
                    )
                    self._conn.commit()
                    return qid
            elif norm_kind == "DEMOLISH_BUILDING" and goal_lvl is not None and executed_level is not None:
                if int(executed_level) > int(goal_lvl):
                    self._conn.execute(
                        "UPDATE manual_queue SET updated_ts = ?, result_message = ? WHERE id = ?",
                        (now_ts, f"Demolished to L{int(executed_level)} (goal L{int(goal_lvl)}): {msg_s}"[:300], qid),
                    )
                    self._record_manual_queue_event_locked(
                        qid,
                        "progressed",
                        {"executed_level": int(executed_level), "target_level": int(goal_lvl)},
                        now_ts,
                    )
                    self._conn.commit()
                    return qid
            elif norm_kind == "BUILD_SHIP" and goal_lvl is not None and executed_qty is not None and int(executed_qty) > 0:
                rem = int(goal_lvl) - int(executed_qty)
                if rem > 0:
                    self._conn.execute(
                        "UPDATE manual_queue SET updated_ts = ?, target_level = ?, result_message = ? WHERE id = ?",
                        (now_ts, rem, f"Ordered {int(executed_qty)}x ({rem}x remaining): {msg_s}"[:300], qid),
                    )
                    self._record_manual_queue_event_locked(
                        qid,
                        "progressed",
                        {"ordered": int(executed_qty), "remaining": rem},
                        now_ts,
                    )
                    self._conn.commit()
                    return qid

            self._conn.execute(
                """
                UPDATE manual_queue
                SET status = 'completed', updated_ts = ?, completed_ts = ?, result_message = ?
                WHERE id = ?
                """,
                (now_ts, now_ts, msg_s, qid),
            )
            self._record_manual_queue_event_locked(qid, "completed", {"message": msg_s}, now_ts)
            self._conn.commit()
            return qid

    # ------------------------------------------------------------------
    # Schema Versioning, Migrations, Online Backups & Verification
    # ------------------------------------------------------------------

    def get_schema_version(self) -> dict[str, Any]:
        """Return lightweight schema version, tag, checksum, and sync status."""
        with self._lock:
            st = DEFAULT_GAME_MIGRATION_RUNNER.inspect(
                self._conn,
                db_path=None if self._is_memory else self.db_path,
                include_events=False,
            )
        return _sanitize_obj(
            {
                "version": st["version"],
                "version_tag": st["version_tag"],
                "target_version": st["target_version"],
                "target_version_tag": st["target_version_tag"],
                "user_version": st["user_version"],
                "meta_schema_version": st["meta_schema_version"],
                "schema_checksum": st["schema_checksum"],
                "up_to_date": st["up_to_date"],
                "newer_than_code": st["newer_than_code"],
                "applied_count": st["applied_count"],
                "pending_count": st["pending_count"],
                "checksum_drift": st["checksum_drift"],
            }
        )

    def get_migration_status(
        self,
        *,
        include_events: bool = True,
        event_limit: int = 50,
    ) -> dict[str, Any]:
        """Return full migration ledger, pending steps, checksum audit, and events."""
        with self._lock:
            st = DEFAULT_GAME_MIGRATION_RUNNER.inspect(
                self._conn,
                db_path=None if self._is_memory else self.db_path,
                include_events=include_events,
                event_limit=event_limit,
            )
        return _sanitize_obj(st)

    def run_migrations(
        self,
        *,
        target_version: Optional[int] = None,
        dry_run: bool = False,
        backup: bool = False,
        backup_dir: Optional[Path] = None,
    ) -> dict[str, Any]:
        """Run pending forward migrations up to `target_version` (or preview with `dry_run=True`)."""
        with self._lock:
            res = DEFAULT_GAME_MIGRATION_RUNNER.migrate(
                self._conn,
                db_path=None if self._is_memory else self.db_path,
                is_memory=self._is_memory,
                target_version=target_version,
                dry_run=dry_run,
                backup=backup,
                auto_backup_default_db=False,
                backup_dir=backup_dir,
            )
            if not dry_run and DEFAULT_GAME_MIGRATION_RUNNER._table_exists(self._conn, "game_messages"):
                self._backfill_reports_from_game_messages_locked(time.time())
                self._conn.commit()
        return _sanitize_obj(res)

    def create_backup(
        self,
        *,
        label: str = "manual",
        backup_dir: Optional[Path] = None,
    ) -> dict[str, Any]:
        """Create an online WAL-consistent backup snapshot via `sqlite3.Connection.backup`."""
        if self._is_memory:
            raise ValueError("Cannot create file backup of an in-memory SQLite database.")
        with self._lock:
            st = DEFAULT_GAME_MIGRATION_RUNNER.inspect(
                self._conn,
                db_path=self.db_path,
                include_events=False,
            )
            ver = int(st["version"])
            bkp_path = create_sqlite_backup(
                self._conn,
                self.db_path,
                from_version=ver,
                to_version=ver,
                label=label,
                backup_dir=backup_dir,
            )
            DEFAULT_GAME_MIGRATION_RUNNER._ensure_ledger_tables(self._conn)
            DEFAULT_GAME_MIGRATION_RUNNER._record_event(
                self._conn,
                ts=time.time(),
                event_type="backup_created",
                from_version=ver,
                to_version=ver,
                backup_path=str(bkp_path),
                details={"label": _sanitize_text(label)[:40]},
            )
            self._conn.commit()
        return _sanitize_obj(
            {
                "ok": True,
                "db_path": _sanitize_text(self.db_path),
                "backup_path": _sanitize_text(bkp_path),
                "size_bytes": bkp_path.stat().st_size if bkp_path.exists() else 0,
                "version": ver,
                "version_tag": f"V{ver:03d}",
            }
        )

    def verify_schema(self, *, full_integrity: bool = False) -> dict[str, Any]:
        """Verify SQLite B-tree integrity, required tables/columns, version sync, and checksums."""
        with self._lock:
            res = DEFAULT_GAME_MIGRATION_RUNNER.verify(
                self._conn,
                db_path=None if self._is_memory else self.db_path,
                full_integrity=full_integrity,
            )
        return _sanitize_obj(res)

    def summary(self) -> dict[str, Any]:
        size_bytes = (
            self.db_path.stat().st_size
            if (not self._is_memory and self.db_path.exists())
            else 0
        )
        with self._lock:
            data_ver_row = self._conn.execute("PRAGMA data_version").fetchone()
            data_ver = int(data_ver_row[0]) if data_ver_row is not None else 0
            cur_ver = (int(self._conn.total_changes), data_ver, int(size_bytes))
            if self._summary_cache_json is not None and self._summary_cache_ver == cur_ver:
                return json.loads(self._summary_cache_json)
        tables = (
            "latest_state_hot",
            "empire_planets_hot",
            "galaxy_sector_hot",
            "colony_candidates_hot",
            "rank_snapshots",
            "planet_snapshots",
            "state_snapshots",
            "strategy_config_store",
            "ui_notifications",
            "universe_research_max",
            "universe_research_max_history",
            "economy_rates_cache",
            "tutorial_progress",
            "action_history",
            "spy_targets",
            "spy_reports",
            "game_messages",
            "galaxy_scans",
            "galaxy_scan_history",
            "galaxy_slots",
            "galaxy_slot_history",
            "config_history",
            "raid_history",
            "bash_blocks",
            "battle_reports",
            "recycle_reports",
            "manual_queue",
            "manual_queue_events",
            "colony_attempts",
            "schema_migrations",
            "schema_migration_events",
        )
        counts: dict[str, int] = {}
        with self._lock:
            existing_tables = {
                str(r[0])
                for r in self._conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'").fetchall()
            }
            for tbl in tables:
                counts[tbl] = (
                    int(self._conn.execute(f"SELECT COUNT(*) FROM {tbl}").fetchone()[0])
                    if tbl in existing_tables
                    else 0
                )
            meta_row_cnt = (
                int(self._conn.execute("SELECT COUNT(*) FROM meta").fetchone()[0])
                if "meta" in existing_tables
                else 0
            )
            hot_counts = {
                tbl: (meta_row_cnt if tbl == "meta" else counts.get(tbl, 0))
                for tbl in HOT_TABLES
            }
            history_counts = {
                tbl: counts.get(tbl, 0)
                for tbl in HISTORY_TABLES
            }
            meta_keys = (
                {
                    _sanitize_text(_raw_text(r["key"], ""))
                    for r in self._conn.execute("SELECT key FROM meta").fetchall()
                }
                if "meta" in existing_tables
                else set()
            )
            msg_cats = (
                {
                    _sanitize_text(_raw_text(r["category"], "general")): int(r["cnt"])
                    for r in self._conn.execute(
                        "SELECT category, COUNT(*) AS cnt FROM game_messages WHERE msg_uid NOT LIKE 'id:msg-%' AND TRIM(body_text) NOT GLOB '[0-9]* / [0-9]*' GROUP BY category"
                    ).fetchall()
                }
                if "game_messages" in existing_tables
                else {}
            )
            mig_info = DEFAULT_GAME_MIGRATION_RUNNER.inspect(
                self._conn,
                db_path=None if self._is_memory else self.db_path,
                include_events=False,
            )
        tut_rows = self.get_tutorial_history() if "tutorial_progress" in existing_tables else []
        recent_actions = self.get_recent_actions(limit=12) if "action_history" in existing_tables else []
        spy_targets = self.get_spy_targets(limit=25) if "spy_targets" in existing_tables else []
        out_summary = _sanitize_obj(
            {
                "db_path": ":memory:" if self._is_memory else _sanitize_text(self.db_path),
                "size_bytes": size_bytes,
                "schema": {
                    "version": mig_info["version"],
                    "version_tag": mig_info["version_tag"],
                    "target_version": mig_info["target_version"],
                    "target_version_tag": mig_info["target_version_tag"],
                    "user_version": mig_info["user_version"],
                    "meta_schema_version": mig_info["meta_schema_version"],
                    "up_to_date": mig_info["up_to_date"],
                    "applied_count": mig_info["applied_count"],
                    "pending_count": mig_info["pending_count"],
                    "schema_checksum": mig_info["schema_checksum"],
                    "checksum_drift": mig_info["checksum_drift"],
                    "applied_migrations": [
                        {
                            "version": m["version"],
                            "version_tag": m["version_tag"],
                            "name": m["name"],
                            "applied_ts": m["applied_ts"],
                        }
                        for m in mig_info["applied_migrations"]
                    ],
                },
                "counts": counts,
                "table_tiers": {
                    "hot": list(HOT_TABLES),
                    "history": list(HISTORY_TABLES),
                    "hot_counts": hot_counts,
                    "history_counts": history_counts,
                    "hot_total_rows": sum(hot_counts.values()),
                    "history_total_rows": sum(history_counts.values()),
                },
                "message_categories": msg_cats,
                "persisted_meta_keys": sorted(k for k in meta_keys if k),
                "tutorial_history": tut_rows,
                "recent_actions": recent_actions,
                "spy_targets": spy_targets,
            }
        )
        with self._lock:
            self._summary_cache_ver = cur_ver
            self._summary_cache_json = json.dumps(out_summary, allow_nan=False)
        return out_summary

    def get_transmitter_status(self, *, now_ts: Optional[float] = None) -> dict[str, Any]:
        """Return persisted Interdimensional Transmitter daily free-spin status."""
        eff_now = _safe_float(now_ts, time.time()) if now_ts is not None else time.time()
        today = server_day_key(eff_now)
        raw = self.load_meta_json("transmitter_state")
        data = raw if isinstance(raw, Mapping) else {}
        last_activated_day = _sanitize_text(data.get("last_activated_day") or "").strip()
        last_checked_day = _sanitize_text(data.get("last_checked_day") or "").strip()
        last_activated_ts = _safe_float(data.get("last_activated_ts"), 0.0)
        last_checked_ts = _safe_float(data.get("last_checked_ts"), 0.0)
        free_spins_rem = data.get("free_spins_remaining")
        free_spins_opt = (
            game_data.cap_json_int(max(0, _safe_int(free_spins_rem, 0)))
            if free_spins_rem is not None
            else None
        )
        last_reward = _sanitize_text(data.get("last_reward") or "").strip()[:160]
        total_activations = game_data.cap_json_int(max(0, _safe_int(data.get("total_activations"), 0)))
        already_done_today = (last_activated_day == today) or (
            last_checked_day == today and free_spins_opt == 0
        )
        return _sanitize_obj(
            {
                "today_day_key": today,
                "due": not already_done_today,
                "last_activated_day": last_activated_day,
                "last_checked_day": last_checked_day,
                "last_activated_ts": last_activated_ts,
                "last_checked_ts": last_checked_ts,
                "free_spins_remaining": free_spins_opt,
                "last_reward": last_reward,
                "total_activations": total_activations,
            }
        )

    def record_transmitter_state(
        self,
        *,
        activated: bool,
        free_spins_remaining: Optional[int] = None,
        reward: str = "",
        ts: Optional[float] = None,
    ) -> dict[str, Any]:
        """Persist Interdimensional Transmitter daily check or activation outcome into `meta`."""
        eff_now = round(_safe_float(ts, time.time()) if ts is not None else time.time(), 3)
        today = server_day_key(eff_now)
        cur = self.get_transmitter_status(now_ts=eff_now)
        payload: dict[str, Any] = {
            "last_activated_day": today if activated else cur["last_activated_day"],
            "last_checked_day": today,
            "last_activated_ts": eff_now if activated else cur["last_activated_ts"],
            "last_checked_ts": eff_now,
            "free_spins_remaining": (
                game_data.cap_json_int(max(0, _safe_int(free_spins_remaining, 0)))
                if free_spins_remaining is not None
                else cur["free_spins_remaining"]
            ),
            "last_reward": (
                _sanitize_text(reward).strip()[:160]
                if reward
                else cur["last_reward"]
            ),
            "total_activations": (
                game_data.cap_json_int(int(cur["total_activations"]) + 1)
                if activated
                else cur["total_activations"]
            ),
        }
        self.save_meta_json("transmitter_state", payload, ts=eff_now)
        return self.get_transmitter_status(now_ts=eff_now)

    def get_galaxy_sweep_state(self, *, now_ts: Optional[float] = None) -> dict[str, Any]:
        """Return persisted periodic 100-page galaxy sweep cursor and telemetry."""
        eff_now = _safe_float(now_ts, time.time()) if now_ts is not None else time.time()
        raw = self.load_meta_json("galaxy_sweep_state")
        data = raw if isinstance(raw, Mapping) else {}
        next_due_ts = _safe_float(data.get("next_sweep_due_ts"), 0.0)
        return _sanitize_obj(
            {
                "cursor_galaxy": game_data.cap_json_int(max(1, min(14, _safe_int(data.get("cursor_galaxy"), 1)))),
                "cursor_system": game_data.cap_json_int(max(1, min(400, _safe_int(data.get("cursor_system"), 1)))),
                "last_sweep_started_ts": _safe_float(data.get("last_sweep_started_ts"), 0.0),
                "last_sweep_completed_ts": _safe_float(data.get("last_sweep_completed_ts"), 0.0),
                "next_sweep_due_ts": next_due_ts,
                "due": eff_now >= next_due_ts,
                "last_pages_scanned": game_data.cap_json_int(max(0, _safe_int(data.get("last_pages_scanned"), 0))),
                "last_players_found": game_data.cap_json_int(max(0, _safe_int(data.get("last_players_found"), 0))),
                "last_colonies_found": game_data.cap_json_int(max(0, _safe_int(data.get("last_colonies_found"), 0))),
                "last_debris_found": game_data.cap_json_int(max(0, _safe_int(data.get("last_debris_found"), 0))),
                "total_sweeps": game_data.cap_json_int(max(0, _safe_int(data.get("total_sweeps"), 0))),
                "total_pages_scanned": game_data.cap_json_int(
                    max(0, _safe_int(data.get("total_pages_scanned"), 0))
                ),
                "click_delay_seconds": round(
                    max(0.2, min(60.0, _safe_float(data.get("click_delay_seconds"), 1.5))),
                    3,
                ),
                "click_jitter_seconds": round(
                    max(0.0, min(30.0, _safe_float(data.get("click_jitter_seconds"), 0.6))),
                    3,
                ),
            }
        )

    def save_galaxy_sweep_state(
        self,
        updates: Mapping[str, Any],
        *,
        ts: Optional[float] = None,
    ) -> dict[str, Any]:
        """Update and persist galaxy sweep cursor/telemetry in SQLite `meta`."""
        eff_now = round(_safe_float(ts, time.time()) if ts is not None else time.time(), 3)
        cur = self.get_galaxy_sweep_state(now_ts=eff_now)
        merged = dict(cur)
        merged.pop("due", None)
        for k, v in updates.items():
            if k == "due":
                continue
            merged[str(k)] = v
        self.save_meta_json("galaxy_sweep_state", merged, ts=eff_now)
        return self.get_galaxy_sweep_state(now_ts=eff_now)

    @staticmethod
    def _catalog_research_definitions() -> list[tuple[int, str, str, str, Optional[int]]]:
        """Return ordered `(research_id, research_key, slug, name, catalog_max_level)` across `game_data.RESEARCHES`."""
        defs: list[tuple[int, str, str, str, Optional[int]]] = []
        for slug, info in game_data.RESEARCHES.items():
            if not isinstance(info, Mapping):
                continue
            raw_id = str(info.get("id") or "").strip()
            if raw_id.startswith("r") and raw_id[1:].isascii() and raw_id[1:].isdigit():
                rid = int(raw_id[1:])
                rkey = raw_id
            else:
                rid = 16
                rkey = str(slug).strip()
            name = str(info.get("name") or slug).strip()
            lvls = info.get("levels")
            cat_max: Optional[int] = None
            if isinstance(lvls, Mapping) and lvls:
                int_lvls = [int(k) for k in lvls.keys() if isinstance(k, int) or (isinstance(k, str) and k.isdigit())]
                if int_lvls:
                    cat_max = max(int_lvls)
            defs.append((rid, rkey, str(slug), name, cat_max))
        defs.sort(key=lambda item: (item[0], item[2]))
        return defs

    def refresh_universe_research_max(
        self,
        *,
        own_researches: Optional[Mapping[str, Any]] = None,
        own_nick: str = "",
        server_records: Optional[Mapping[str, Any]] = None,
        source: str = "refresh",
        ts: Optional[float] = None,
    ) -> list[dict[str, Any]]:
        """Recompute and persist `universe_research_max` across server records, spy reports, own researches, and catalog."""
        eff_now = round(_safe_float(ts, time.time()) if ts is not None else time.time(), 3)
        defs = self._catalog_research_definitions()
        slug_to_key = {slug: rkey for _, rkey, slug, _, _ in defs}
        name_to_key = {name.lower(): rkey for _, rkey, _, name, _ in defs}

        raw_meta_srv = self.load_meta_json("universe_research_records")
        merged_srv: dict[str, dict[str, Any]] = {}
        if isinstance(raw_meta_srv, Mapping):
            for rk, rv in raw_meta_srv.items():
                if isinstance(rv, Mapping):
                    merged_srv[str(rk)] = {
                        "level": max(0, _safe_int(rv.get("level"), 0)),
                        "holder": _sanitize_text(rv.get("holder") or ""),
                        "coords": normalize_coords(_raw_text(rv.get("coords"), "")) if rv.get("coords") else "",
                    }
        srv_changed = False
        if isinstance(server_records, Mapping):
            for raw_k, raw_v in server_records.items():
                ks = _raw_text(raw_k, "").strip()
                canon_k = slug_to_key.get(ks.lower(), name_to_key.get(ks.lower(), ks))
                if isinstance(raw_v, Mapping):
                    lvl_v = max(0, _safe_int(raw_v.get("level"), 0))
                    h_v = _sanitize_text(raw_v.get("holder") or "")
                    c_raw = _raw_text(raw_v.get("coords"), "").strip()
                    c_v = normalize_coords(c_raw) if c_raw else ""
                else:
                    lvl_v = max(0, _safe_int(raw_v, 0))
                    h_v = ""
                    c_v = ""
                if lvl_v <= 0:
                    continue
                prev_s = merged_srv.get(canon_k)
                if prev_s is None or lvl_v > int(prev_s.get("level", 0)) or (lvl_v == int(prev_s.get("level", 0)) and h_v and not prev_s.get("holder")):
                    merged_srv[canon_k] = {"level": lvl_v, "holder": h_v, "coords": c_v}
                    srv_changed = True
        if srv_changed:
            self.save_meta_json("universe_research_records", merged_srv, ts=eff_now)

        eff_own_map: dict[str, int] = {}
        eff_nick = _sanitize_text(own_nick or "")
        with self._lock:
            if own_researches is None:
                snap_row = self._conn.execute(
                    "SELECT researches_json FROM planet_snapshots ORDER BY id DESC LIMIT 1"
                ).fetchone()
                if snap_row is not None and snap_row[0]:
                    try:
                        parsed_own = json.loads(snap_row[0])
                        if isinstance(parsed_own, Mapping):
                            for ok, ov in parsed_own.items():
                                eff_own_map[str(ok)] = max(0, _sql_int(ov, 0))
                    except Exception:
                        pass
                if not eff_nick:
                    rnk_row = self._conn.execute(
                        "SELECT nickname FROM rank_snapshots WHERE nickname != '' ORDER BY id DESC LIMIT 1"
                    ).fetchone()
                    if rnk_row is not None and rnk_row[0]:
                        eff_nick = _sanitize_text(rnk_row[0])
            else:
                for ok, ov in own_researches.items():
                    eff_own_map[_raw_text(ok, "").strip()] = max(0, _sql_int(ov, 0))

            prev_rows_by_key: dict[str, sqlite3.Row] = {}
            if DEFAULT_GAME_MIGRATION_RUNNER._table_exists(self._conn, "universe_research_max"):
                for pr in self._conn.execute("SELECT * FROM universe_research_max").fetchall():
                    prev_rows_by_key[str(pr["research_key"])] = pr

            spied_best: dict[str, tuple[int, str, str]] = {}
            rep_rows = self._conn.execute(
                """
                SELECT target_coords, owner, researches_json
                FROM spy_reports
                WHERE researches_json != '' AND researches_json != '{}'
                ORDER BY ts ASC, id ASC
                """
            ).fetchall()
            for rr in rep_rows:
                try:
                    rmap = json.loads(rr["researches_json"] or "{}")
                except Exception:
                    continue
                if not isinstance(rmap, Mapping):
                    continue
                r_owner = _sanitize_text(rr["owner"] or "")
                r_coords = normalize_coords(_raw_text(rr["target_coords"], ""))
                for rk_raw, rv_raw in rmap.items():
                    rk_s = _raw_text(rk_raw, "").strip()
                    canon_rk = slug_to_key.get(rk_s.lower(), name_to_key.get(rk_s.lower(), rk_s))
                    rlvl = max(0, _sql_int(rv_raw, 0))
                    if rlvl <= 0:
                        continue
                    cur_b = spied_best.get(canon_rk)
                    if cur_b is None or rlvl >= cur_b[0]:
                        spied_best[canon_rk] = (
                            rlvl,
                            r_owner or (cur_b[1] if (cur_b and cur_b[0] == rlvl) else ""),
                            r_coords or (cur_b[2] if (cur_b and cur_b[0] == rlvl) else ""),
                        )

            out_rows: list[dict[str, Any]] = []
            for rid, rkey, slug, name, cat_max in defs:
                prev_r = prev_rows_by_key.get(rkey)
                own_lvl = max(
                    0,
                    eff_own_map.get(rkey, eff_own_map.get(slug, 0)),
                )
                prev_spied = max(0, _safe_int(prev_r["spied_max_level"], 0)) if prev_r is not None else 0
                obs_spied, obs_holder, obs_coords = spied_best.get(rkey, (0, "", ""))
                spied_lvl = max(prev_spied, obs_spied)

                prev_srv = (
                    _safe_optional_int(prev_r["server_record_level"])
                    if (prev_r is not None and prev_r["server_record_level"] is not None)
                    else None
                )
                srv_entry = merged_srv.get(rkey)
                srv_lvl_cand = max(0, _sql_int(srv_entry.get("level"), 0)) if isinstance(srv_entry, Mapping) else 0
                if prev_srv is not None and srv_lvl_cand > 0:
                    srv_lvl: Optional[int] = max(prev_srv, srv_lvl_cand)
                elif srv_lvl_cand > 0:
                    srv_lvl = srv_lvl_cand
                else:
                    srv_lvl = prev_srv

                prev_univ = max(0, _sql_int(prev_r["universe_max_level"], 0)) if prev_r is not None else 0
                univ_max = _sql_int(max(prev_univ, own_lvl, spied_lvl, srv_lvl or 0), 0)

                holder = ""
                holder_coords = ""
                if univ_max > 0:
                    if srv_lvl is not None and srv_lvl == univ_max and isinstance(srv_entry, Mapping) and srv_entry.get("holder"):
                        holder = str(srv_entry.get("holder") or "")
                        holder_coords = str(srv_entry.get("coords") or "")
                    elif obs_spied == univ_max and obs_spied > 0:
                        holder = obs_holder or obs_coords
                        holder_coords = obs_coords
                    elif prev_r is not None and prev_univ == univ_max and str(prev_r["holder"] or "").strip():
                        holder = _sanitize_text(prev_r["holder"] or "")
                        holder_coords = _sanitize_text(prev_r["holder_coords"] or "")
                    elif own_lvl == univ_max and own_lvl > 0:
                        holder = eff_nick or "You"
                        holder_coords = ""

                if DEFAULT_GAME_MIGRATION_RUNNER._table_exists(self._conn, "universe_research_max"):
                    self._conn.execute(
                        """
                        INSERT INTO universe_research_max(
                            research_key, research_id, slug, name,
                            own_level, spied_max_level, server_record_level,
                            universe_max_level, catalog_max_level,
                            holder, holder_coords, updated_ts
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        ON CONFLICT(research_key) DO UPDATE SET
                            research_id = excluded.research_id,
                            slug = excluded.slug,
                            name = excluded.name,
                            own_level = excluded.own_level,
                            spied_max_level = MAX(universe_research_max.spied_max_level, excluded.spied_max_level),
                            server_record_level = CASE
                                WHEN excluded.server_record_level IS NULL THEN universe_research_max.server_record_level
                                WHEN universe_research_max.server_record_level IS NULL THEN excluded.server_record_level
                                ELSE MAX(universe_research_max.server_record_level, excluded.server_record_level)
                            END,
                            universe_max_level = MAX(universe_research_max.universe_max_level, excluded.universe_max_level),
                            catalog_max_level = excluded.catalog_max_level,
                            holder = CASE WHEN excluded.holder != '' THEN excluded.holder ELSE universe_research_max.holder END,
                            holder_coords = CASE WHEN excluded.holder != '' THEN excluded.holder_coords ELSE universe_research_max.holder_coords END,
                            updated_ts = excluded.updated_ts
                        """,
                        (
                            rkey,
                            _sql_int(rid, 0),
                            slug,
                            name,
                            _sql_int(own_lvl, 0),
                            _sql_int(spied_lvl, 0),
                            _sql_optional_int(srv_lvl),
                            _sql_int(univ_max, 0),
                            _sql_optional_int(cat_max),
                            holder[:80],
                            holder_coords[:24],
                            eff_now,
                        ),
                    )
                out_rows.append(
                    _sanitize_obj(
                        {
                            "research_id": rid,
                            "key": rkey,
                            "slug": slug,
                            "name": name,
                            "own_level": game_data.cap_json_int(own_lvl),
                            "universe_max_level": game_data.cap_json_int(univ_max),
                            "spied_max_level": game_data.cap_json_int(spied_lvl),
                            "server_record_level": game_data.cap_json_int(srv_lvl) if srv_lvl is not None else None,
                            "catalog_max_level": game_data.cap_json_int(cat_max) if cat_max is not None else None,
                            "gap_to_max": game_data.cap_json_int(max(0, univ_max - own_lvl)),
                            "holder": holder[:80],
                            "holder_coords": holder_coords[:24],
                            "updated_ts": eff_now,
                        }
                    )
                )

            if (
                source != "auto_seed"
                and DEFAULT_GAME_MIGRATION_RUNNER._table_exists(self._conn, "universe_research_max_history")
            ):
                clean_src = _sanitize_text(source or "refresh")[:32] or "refresh"
                rec_json = _json_dumps(out_rows)
                self._conn.execute(
                    "INSERT INTO universe_research_max_history(ts, source, records_json) VALUES (?, ?, ?)",
                    (eff_now, clean_src, rec_json),
                )
            self._conn.commit()
        return out_rows

    def get_universe_research_max(
        self,
        own_researches: Optional[Mapping[str, Any]] = None,
        own_nick: str = "",
    ) -> list[dict[str, Any]]:
        """Read persisted `universe_research_max` rows (auto-seeding if empty, and syncing `own_level` if higher)."""
        defs = self._catalog_research_definitions()
        with self._lock:
            if not DEFAULT_GAME_MIGRATION_RUNNER._table_exists(self._conn, "universe_research_max"):
                rows_db = []
            else:
                rows_db = self._conn.execute(
                    "SELECT * FROM universe_research_max ORDER BY research_id ASC, slug ASC"
                ).fetchall()
        if len(rows_db) < len(defs):
            return self.refresh_universe_research_max(
                own_researches=own_researches,
                own_nick=own_nick,
                source="auto_seed",
            )
        if isinstance(own_researches, Mapping) and own_researches:
            own_lookup = {_raw_text(k, "").strip(): max(0, _sql_int(v, 0)) for k, v in own_researches.items()}
            needs_refresh = any(
                own_lookup.get(str(r["research_key"]), own_lookup.get(str(r["slug"]), int(r["own_level"])))
                != int(r["own_level"])
                for r in rows_db
            )
            if needs_refresh:
                return self.refresh_universe_research_max(
                    own_researches=own_researches,
                    own_nick=own_nick,
                    source="auto_seed",
                )
        out: list[dict[str, Any]] = []
        for r in rows_db:
            own_l = max(0, _safe_int(r["own_level"], 0))
            univ_l = max(own_l, _safe_int(r["universe_max_level"], 0))
            spied_l = max(0, _safe_int(r["spied_max_level"], 0))
            srv_l = _safe_optional_int(r["server_record_level"]) if r["server_record_level"] is not None else None
            cat_l = _safe_optional_int(r["catalog_max_level"]) if r["catalog_max_level"] is not None else None
            out.append(
                _sanitize_obj(
                    {
                        "research_id": _safe_int(r["research_id"], 0),
                        "key": _sanitize_text(r["research_key"] or ""),
                        "slug": _sanitize_text(r["slug"] or ""),
                        "name": _sanitize_text(r["name"] or ""),
                        "own_level": game_data.cap_json_int(own_l),
                        "universe_max_level": game_data.cap_json_int(univ_l),
                        "spied_max_level": game_data.cap_json_int(spied_l),
                        "server_record_level": game_data.cap_json_int(srv_l) if srv_l is not None else None,
                        "catalog_max_level": game_data.cap_json_int(cat_l) if cat_l is not None else None,
                        "gap_to_max": game_data.cap_json_int(max(0, univ_l - own_l)),
                        "holder": _sanitize_text(r["holder"] or ""),
                        "holder_coords": _sanitize_text(r["holder_coords"] or ""),
                        "updated_ts": _safe_float(r["updated_ts"], 0.0),
                    }
                )
            )
        return out

    @staticmethod
    def _row_to_colony_attempt_dict(r: sqlite3.Row) -> dict[str, Any]:
        raw_tc = _raw_text(r["target_coords"], "").strip()
        norm_tc = normalize_coords(raw_tc) if raw_tc else ""
        raw_oc = _raw_text(r["origin_coords"], "").strip()
        norm_oc = normalize_coords(raw_oc) if raw_oc else ""
        pid_opt = _safe_optional_int(r["planet_id"]) if r["planet_id"] is not None else None
        f_rolled_opt = _safe_optional_int(r["fields_rolled"]) if r["fields_rolled"] is not None else None
        min_f_tgt = max(1, _safe_int(r["min_fields_target"], 300))
        exp_min_opt = _safe_optional_int(r["expected_min_fields"]) if r["expected_min_fields"] is not None else None
        exp_max_opt = _safe_optional_int(r["expected_max_fields"]) if r["expected_max_fields"] is not None else None
        temp_opt = _safe_optional_int(r["temperature"]) if r["temperature"] is not None else None
        score_opt = _safe_optional_int(r["score"]) if r["score"] is not None else None
        return _sanitize_obj(
            {
                "id": game_data.cap_json_int(_safe_int(r["id"], 0)),
                "ts": _safe_float(r["ts"], 0.0),
                "event_type": _sanitize_text(r["event_type"] or "").strip().lower(),
                "target_coords": norm_tc,
                "origin_coords": norm_oc,
                "planet_id": game_data.cap_json_int(pid_opt) if pid_opt is not None else None,
                "fields_rolled": game_data.cap_json_int(f_rolled_opt) if f_rolled_opt is not None else None,
                "min_fields_target": game_data.cap_json_int(min_f_tgt),
                "expected_min_fields": game_data.cap_json_int(exp_min_opt) if exp_min_opt is not None else None,
                "expected_max_fields": game_data.cap_json_int(exp_max_opt) if exp_max_opt is not None else None,
                "temperature": game_data.cap_json_int(temp_opt) if temp_opt is not None else None,
                "grade": _sanitize_text(r["grade"] or "").strip(),
                "score": game_data.cap_json_int(score_opt) if score_opt is not None else None,
                "status_message": _sanitize_text(r["status_message"] or ""),
                "details": _safe_json_dict(r["details_json"]),
            }
        )

    def record_colony_attempt(
        self,
        *,
        target_coords: str,
        event_type: str,
        origin_coords: str = "",
        planet_id: Optional[int] = None,
        fields_rolled: Optional[int] = None,
        min_fields_target: int = 300,
        expected_min_fields: Optional[int] = None,
        expected_max_fields: Optional[int] = None,
        temperature: Optional[int] = None,
        grade: str = "",
        score: Optional[int] = None,
        status_message: str = "",
        details: Optional[Mapping[str, Any]] = None,
        ts: Optional[float] = None,
    ) -> Optional[dict[str, Any]]:
        """Append a colonization trial event (`dispatched`, `settled`, `kept`, `deleted_undersized`, `failed`)."""
        norm_tc = normalize_coords(_raw_text(target_coords, "").strip())
        if not norm_tc:
            return None
        raw_oc = _raw_text(origin_coords, "").strip()
        norm_oc = normalize_coords(raw_oc) if raw_oc else ""
        ev_type = _sanitize_text(event_type).strip().lower()[:40] or "dispatched"
        eff_ts = round(_safe_float(ts, time.time()) if ts is not None else time.time(), 3)
        pid_i = _sql_optional_int(planet_id)
        f_rolled_i = _sql_optional_int(fields_rolled)
        min_tgt_i = max(1, _sql_int(min_fields_target, 300))
        exp_min_i = _sql_optional_int(expected_min_fields)
        exp_max_i = _sql_optional_int(expected_max_fields)
        temp_i = _sql_optional_int(temperature)
        grade_s = _sanitize_text(grade).strip()[:16]
        score_i = _sql_optional_int(score)
        msg_s = _sanitize_text(status_message).strip()[:400]
        details_json = _json_dumps(dict(details) if isinstance(details, Mapping) else {})

        with self._lock:
            if not DEFAULT_GAME_MIGRATION_RUNNER._table_exists(self._conn, "colony_attempts"):
                return None
            cur = self._conn.execute(
                """
                INSERT INTO colony_attempts(
                    ts, event_type, target_coords, origin_coords, planet_id,
                    fields_rolled, min_fields_target, expected_min_fields, expected_max_fields,
                    temperature, grade, score, status_message, details_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    eff_ts,
                    ev_type,
                    norm_tc,
                    norm_oc,
                    pid_i,
                    f_rolled_i,
                    min_tgt_i,
                    exp_min_i,
                    exp_max_i,
                    temp_i,
                    grade_s,
                    score_i,
                    msg_s,
                    details_json,
                ),
            )
            row_id = int(cur.lastrowid or 0)
            self._conn.commit()
            row = self._conn.execute(
                "SELECT * FROM colony_attempts WHERE id = ? LIMIT 1",
                (row_id,),
            ).fetchone()
        return self._row_to_colony_attempt_dict(row) if row is not None else None

    def get_colony_attempts(
        self,
        *,
        limit: int = 100,
        target_coords: Optional[str] = None,
    ) -> list[dict[str, Any]]:
        """Read colonization trial history ordered newest first (`ts DESC, id DESC`)."""
        lim = max(1, min(2000, _sql_int(limit, 100)))
        with self._lock:
            if not DEFAULT_GAME_MIGRATION_RUNNER._table_exists(self._conn, "colony_attempts"):
                return []
            if target_coords is not None and _raw_text(target_coords, "").strip():
                _, variants, _ = _coord_query_candidates(target_coords)
                if not variants:
                    return []
                ph = ", ".join("?" for _ in variants)
                rows = self._conn.execute(
                    f"""
                    SELECT * FROM colony_attempts
                    WHERE target_coords IN ({ph})
                    ORDER BY ts DESC, id DESC
                    LIMIT ?
                    """,
                    (*variants, lim),
                ).fetchall()
            else:
                rows = self._conn.execute(
                    """
                    SELECT * FROM colony_attempts
                    ORDER BY ts DESC, id DESC
                    LIMIT ?
                    """,
                    (lim,),
                ).fetchall()
        return [self._row_to_colony_attempt_dict(r) for r in rows]

    def get_colony_attempts_summary(self) -> dict[str, Any]:
        """Aggregate append-only `colony_attempts` into KPI counters and per-coordinate trial summaries."""
        empty_summary: dict[str, Any] = {
            "total_events": 0,
            "unique_coords_tried": 0,
            "dispatched_count": 0,
            "deleted_undersized_count": 0,
            "kept_count": 0,
            "failed_count": 0,
            "by_coords": {},
        }
        with self._lock:
            if not DEFAULT_GAME_MIGRATION_RUNNER._table_exists(self._conn, "colony_attempts"):
                return _sanitize_obj(empty_summary)
            rows = self._conn.execute(
                """
                SELECT * FROM colony_attempts
                ORDER BY ts ASC, id ASC
                """
            ).fetchall()

        total_events = 0
        dispatched_count = 0
        deleted_undersized_count = 0
        kept_count = 0
        failed_count = 0
        by_coords: dict[str, dict[str, Any]] = {}
        pending_dispatch_by_coords: dict[str, bool] = {}

        for r in rows:
            raw_tc = _raw_text(r["target_coords"], "").strip()
            norm_tc = normalize_coords(raw_tc) if raw_tc else ""
            if not norm_tc:
                continue
            total_events += 1
            ev = _sanitize_text(r["event_type"] or "").strip().lower()
            ts_val = _safe_float(r["ts"], 0.0)
            f_rolled = _safe_optional_int(r["fields_rolled"]) if r["fields_rolled"] is not None else None
            temp_val = _safe_optional_int(r["temperature"]) if r["temperature"] is not None else None
            orig_c = normalize_coords(_raw_text(r["origin_coords"], "").strip()) if r["origin_coords"] else ""
            msg_s = _sanitize_text(r["status_message"] or "")

            c_entry = by_coords.get(norm_tc)
            if c_entry is None:
                c_entry = {
                    "coords": norm_tc,
                    "attempts_count": 0,
                    "events_count": 0,
                    "dispatched_count": 0,
                    "deleted_count": 0,
                    "kept_count": 0,
                    "failed_count": 0,
                    "last_event_type": "",
                    "last_fields_rolled": None,
                    "last_temperature": None,
                    "last_origin_coords": "",
                    "last_status_message": "",
                    "last_ts": 0.0,
                }
                by_coords[norm_tc] = c_entry

            c_entry["events_count"] += 1
            if ev == "dispatched":
                dispatched_count += 1
                c_entry["dispatched_count"] += 1
                c_entry["attempts_count"] += 1
                pending_dispatch_by_coords[norm_tc] = True
            elif ev in ("deleted_undersized", "deleted", "abandoned", "undersized"):
                deleted_undersized_count += 1
                c_entry["deleted_count"] += 1
                if pending_dispatch_by_coords.get(norm_tc, False):
                    pending_dispatch_by_coords[norm_tc] = False
                else:
                    c_entry["attempts_count"] += 1
            elif ev in ("kept", "settled_kept"):
                kept_count += 1
                c_entry["kept_count"] += 1
                if pending_dispatch_by_coords.get(norm_tc, False):
                    pending_dispatch_by_coords[norm_tc] = False
                else:
                    c_entry["attempts_count"] += 1
            elif ev == "settled":
                min_t_row = _safe_optional_int(r["min_fields_target"]) if r["min_fields_target"] is not None else None
                eff_min_t = max(50, int(min_t_row)) if (min_t_row is not None and min_t_row >= 50) else 300
                if f_rolled is not None and f_rolled >= eff_min_t:
                    kept_count += 1
                    c_entry["kept_count"] += 1
                if pending_dispatch_by_coords.get(norm_tc, False):
                    pending_dispatch_by_coords[norm_tc] = False
                else:
                    c_entry["attempts_count"] += 1
            elif ev in ("failed", "aborted", "blocked", "error"):
                failed_count += 1
                c_entry["failed_count"] += 1
                if pending_dispatch_by_coords.get(norm_tc, False):
                    pending_dispatch_by_coords[norm_tc] = False
                else:
                    c_entry["attempts_count"] += 1
            else:
                if not pending_dispatch_by_coords.get(norm_tc, False):
                    c_entry["attempts_count"] += 1

            c_entry["last_event_type"] = ev
            if f_rolled is not None:
                c_entry["last_fields_rolled"] = game_data.cap_json_int(f_rolled)
            if temp_val is not None:
                c_entry["last_temperature"] = game_data.cap_json_int(temp_val)
            if orig_c:
                c_entry["last_origin_coords"] = orig_c
            if msg_s:
                c_entry["last_status_message"] = msg_s
            c_entry["last_ts"] = max(float(c_entry["last_ts"]), ts_val)

        return _sanitize_obj(
            {
                "total_events": game_data.cap_json_int(total_events),
                "unique_coords_tried": game_data.cap_json_int(len(by_coords)),
                "dispatched_count": game_data.cap_json_int(dispatched_count),
                "deleted_undersized_count": game_data.cap_json_int(deleted_undersized_count),
                "kept_count": game_data.cap_json_int(kept_count),
                "failed_count": game_data.cap_json_int(failed_count),
                "by_coords": by_coords,
            }
        )
