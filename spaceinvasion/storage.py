"""SQLite persistence layer for SpaceInvasion Autoplayer telemetry, tutorial progress, actions, and intel."""

from __future__ import annotations

from dataclasses import asdict, is_dataclass
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

from spaceinvasion import game_data
from spaceinvasion.fleet import (
    BashTracker,
    GalaxySlot,
    InboxMessage,
    SpyReport,
    _classify_and_parse_inbox_block,
    grade_colony_candidate,
    normalize_coords,
)
from spaceinvasion.logging_utils import (
    _SECRET_DICT_KEYS,
    _SID_RE as _LOG_SID_RE,
    _sub_sid_kv as _sub_log_sid_kv,
    is_secret_dict_key,
    redact_sensitive,
)
from spaceinvasion.migrations import (
    ATTACK_HISTORY_SCHEMA_VERSION,
    DEFAULT_GAME_MIGRATION_RUNNER,
    GAME_DB_MIGRATIONS,
    LATEST_SCHEMA_VERSION,
    PROTECTED_META_KEYS,
    SCHEMA_LEDGER_DDL,
    SCHEMA_VERSION,
    create_sqlite_backup,
    import_legacy_attack_history_json,
    import_legacy_rank_history_json,
    import_legacy_snapshot_json,
)
from spaceinvasion.state import GameState, game_state_from_dict, normalize_planet_type

DEFAULT_DB_PATH = Path("state") / "spaceinvasion.db"
DEFAULT_ATTACK_HISTORY_PATH: Optional[Path] = None

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
    last_scanned_ts REAL NOT NULL
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
    has_asteroid INTEGER NOT NULL DEFAULT 0
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

    def record_state_snapshot(
        self,
        state: GameState,
        *,
        planet_min_interval_seconds: float = 120.0,
        rank_min_interval_seconds: float = 300.0,
    ) -> list[dict[str, Any]]:
        """Persist rank, colony, economy-rate, and tutorial snapshots atomically; return rank history."""
        from spaceinvasion.ranking import estimate_account_points, estimate_hourly_economy

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

            # 4. Mark player's own planet coordinates in spy_targets (non-destructive; never deletes rows)
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
    ) -> None:
        if not isinstance(config_payload, Mapping):
            return
        now_ts = round(_safe_float(ts, time.time()) if ts is not None else time.time(), 3)
        cfg_dict = dict(config_payload)
        cfg_json = _json_dumps(cfg_dict)
        with self._lock:
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
        raw = self.load_meta_json("strategy_config")
        return dict(raw) if isinstance(raw, Mapping) else None

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
                    _json_dumps(state_payload),
                    _json_dumps(plan_payload),
                ),
            )
            row_id = int(cur.lastrowid or 0)
            self._conn.commit()
            return row_id

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
        raw_txt = _sanitize_text(getattr(report, "raw_text", "") or "")[:4000]
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
            self._conn.commit()

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
            for slot in slots:
                self._upsert_galaxy_slot_locked(slot, now_ts, append_history=append_hist)
                has_debris = 1 if bool(getattr(slot, "has_debris", False)) else 0
                if slot.status_color == "free" and not has_debris:
                    continue
                is_inactive = 1 if (slot.status_color == "yellow" or slot.is_spacia) else 0
                raw_slot_coords = _raw_text(slot.coords, "").strip()
                norm_slot_coords = normalize_coords(raw_slot_coords)
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
            for slot in slots:
                self._upsert_galaxy_slot_locked(slot, now_ts, append_history=append_hist)
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
                    _sql_int(galaxy, 0),
                    _sql_int(system, 0),
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
                    _sql_int(galaxy, 0),
                    _sql_int(system, 0),
                    _sql_int(occupied_count, 0),
                    _sql_int(target_count, 0),
                    _sql_int(free_count, 0),
                    _sql_int(debris_count, 0),
                    _sql_int(asteroid_count, 0),
                ),
            )
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
                SELECT coords, galaxy, system, position, planet_name, owner,
                       status_color, is_inactive, is_spacia, has_debris, last_scanned_ts
                FROM galaxy_slots
                {where_sql}
                ORDER BY galaxy ASC, system ASC, position ASC
                LIMIT ?
                """,
                params,
            ).fetchall()
        return [
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
                }
            )
            for r in rows
        ]

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
                SELECT coords, owner, planet_name, status_color, is_inactive, is_spacia, has_debris,
                       points, pig_iron, metal, kryptonite, spice,
                       fleet_json, defense_json, dug_in, last_scanned_ts, last_spied_ts,
                       blocked_until_ts, block_reason, bash_hits, last_blocked_ts,
                       uninhabited_fields, spy_count
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
                    SELECT coords, owner, planet_name, status_color, is_inactive, is_spacia, has_debris,
                           points, pig_iron, metal, kryptonite, spice,
                           fleet_json, defense_json, dug_in, last_scanned_ts, last_spied_ts,
                           blocked_until_ts, block_reason, bash_hits, last_blocked_ts,
                           uninhabited_fields, spy_count
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

    def get_spy_targets(
        self,
        *,
        limit: int = 50,
        include_own: bool = False,
    ) -> list[dict[str, Any]]:
        own_clause = "" if include_own else "AND is_own_planet = 0"
        with self._lock:
            rows = self._conn.execute(
                f"""
                SELECT coords, owner, planet_name, status_color, is_inactive, is_spacia, has_debris,
                       points, pig_iron, metal, kryptonite, spice,
                       fleet_json, defense_json, dug_in, last_scanned_ts, last_spied_ts,
                       blocked_until_ts, block_reason, bash_hits, last_blocked_ts,
                       uninhabited_fields, spy_count
                FROM spy_targets
                WHERE merged_into = '' {own_clause}
                ORDER BY (is_inactive + is_spacia + has_debris) DESC, (last_spied_ts > 0) DESC, last_scanned_ts DESC
                LIMIT ?
                """,
                (max(1, _sql_int(limit, 50)),),
            ).fetchall()
        return [self._row_to_spy_target_dict(r) for r in rows]

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

    def get_colony_candidates(
        self,
        *,
        origin_coords: str = "",
        min_colony_fields: int = 300,
        min_fields_target: Optional[int] = None,
        own_coords: Iterable[str] = (),
        exclude_coords: Optional[Iterable[str]] = None,
        limit: int = 200,
    ) -> list[dict[str, Any]]:
        eff_own = exclude_coords if exclude_coords is not None else own_coords
        eff_min = min_fields_target if min_fields_target is not None else min_colony_fields
        own_norm = {normalize_coords(c) for c in (eff_own or ()) if normalize_coords(c)}
        norm_origin = normalize_coords(origin_coords) if origin_coords else ""
        min_tgt = max(50, min(600, _safe_int(eff_min, 300)))
        with self._lock:
            free_rows = self._conn.execute(
                """
                SELECT coords, last_scanned_ts
                FROM galaxy_slots
                WHERE status_color = 'free'
                ORDER BY galaxy ASC, system ASC, position ASC
                """
            ).fetchall()
            spy_free_rows = self._conn.execute(
                """
                SELECT coords, uninhabited_fields, last_spied_ts, last_scanned_ts, spy_count
                FROM spy_targets
                WHERE merged_into = '' AND is_own_planet = 0
                  AND (status_color = 'free' OR (uninhabited_fields IS NOT NULL AND owner = '' AND is_spacia = 0))
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

        merged: dict[str, dict[str, Any]] = {}
        for r in free_rows:
            nc = normalize_coords(_raw_text(r["coords"], ""))
            if not nc or nc in own_norm:
                continue
            merged[nc] = {
                "coords": nc,
                "uninhabited_fields": None,
                "last_spied_ts": 0.0,
                "last_scanned_ts": _safe_float(r["last_scanned_ts"], 0.0),
                "spy_count": 0,
            }
        for sr in spy_free_rows:
            nc = normalize_coords(_raw_text(sr["coords"], ""))
            if not nc or nc in own_norm:
                continue
            entry = merged.setdefault(
                nc,
                {
                    "coords": nc,
                    "uninhabited_fields": None,
                    "last_spied_ts": 0.0,
                    "last_scanned_ts": 0.0,
                    "spy_count": 0,
                },
            )
            uf = _safe_optional_int(sr["uninhabited_fields"])
            if uf is not None:
                entry["uninhabited_fields"] = uf
            entry["last_spied_ts"] = max(entry["last_spied_ts"], _safe_float(sr["last_spied_ts"], 0.0))
            entry["last_scanned_ts"] = max(entry["last_scanned_ts"], _safe_float(sr["last_scanned_ts"], 0.0))
            entry["spy_count"] = max(entry["spy_count"], _safe_int(sr["spy_count"], 0))
        for rr in rep_free_rows:
            nc = normalize_coords(_raw_text(rr["target_coords"], ""))
            if not nc or nc in own_norm:
                continue
            entry = merged.setdefault(
                nc,
                {
                    "coords": nc,
                    "uninhabited_fields": None,
                    "last_spied_ts": 0.0,
                    "last_scanned_ts": 0.0,
                    "spy_count": 0,
                },
            )
            uf = _safe_optional_int(rr["max_fields"])
            if uf is not None:
                entry["uninhabited_fields"] = uf
            entry["last_spied_ts"] = max(entry["last_spied_ts"], _safe_float(rr["last_ts"], 0.0))
            entry["spy_count"] = max(entry["spy_count"], _safe_int(rr["rep_cnt"], 0))

        graded: list[dict[str, Any]] = []
        for nc, info in merged.items():
            g = grade_colony_candidate(
                norm_origin or nc,
                nc,
                uninhabited_fields=info["uninhabited_fields"],
                spied=info["uninhabited_fields"] is not None,
                min_fields_target=min_tgt,
                last_spied_ts=info["last_spied_ts"],
                last_scanned_ts=info["last_scanned_ts"],
            )
            g["spy_count"] = int(info["spy_count"])
            graded.append(_sanitize_obj(g))

        def _sort_key(item: dict[str, Any]) -> tuple[int, float, float, str]:
            if item["meets_target"]:
                tier = 0
                primary = -float(item["uninhabited_fields"] or 0)
            elif not item["spied"] and 7 <= int(item["position"]) <= 12:
                tier = 1
                primary = float(item["distance"])
            elif not item["spied"]:
                tier = 2
                primary = -float(item["score"])
            else:
                tier = 3
                primary = -float(item["uninhabited_fields"] or 0)
            return (tier, primary, float(item["distance"]), str(item["coords"]))

        graded.sort(key=_sort_key)
        return graded[: max(1, _safe_int(limit, 200))]

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
            self._conn.execute(
                """
                INSERT INTO spy_targets(
                    coords, owner, planet_name, status_color, is_inactive, is_spacia,
                    last_scanned_ts, blocked_until_ts, block_reason, bash_hits, last_blocked_ts
                ) VALUES (?, '', '', 'yellow', ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(coords) DO UPDATE SET
                    blocked_until_ts = MAX(spy_targets.blocked_until_ts, excluded.blocked_until_ts),
                    block_reason = CASE WHEN excluded.block_reason != '' THEN excluded.block_reason ELSE spy_targets.block_reason END,
                    bash_hits = spy_targets.bash_hits + excluded.bash_hits,
                    last_blocked_ts = MAX(spy_targets.last_blocked_ts, excluded.last_blocked_ts)
                """,
                (
                    norm_c,
                    is_spacia_hint,
                    is_spacia_hint,
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
            self._conn.execute(
                """
                UPDATE spy_targets
                SET blocked_until_ts = 0.0, block_reason = ''
                WHERE coords = ? AND blocked_until_ts > 0.0 AND blocked_until_ts <= ?
                """,
                (norm_coords, round(ts_val, 3)),
            )
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
            "rank_snapshots",
            "planet_snapshots",
            "state_snapshots",
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
