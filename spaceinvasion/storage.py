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
from typing import Any, Mapping, Optional, Sequence

from spaceinvasion import game_data
from spaceinvasion.fleet import BashTracker, GalaxySlot, SpyReport, normalize_coords
from spaceinvasion.logging_utils import (
    _SECRET_DICT_KEYS,
    _SID_RE as _LOG_SID_RE,
    _sub_sid_kv as _sub_log_sid_kv,
    is_secret_dict_key,
    redact_sensitive,
)
from spaceinvasion.state import GameState, normalize_planet_type

DEFAULT_DB_PATH = Path("state") / "spaceinvasion.db"
DEFAULT_ATTACK_HISTORY_PATH = Path("data") / "attack_history.json"
SCHEMA_VERSION = "1"

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
    points INTEGER,
    pig_iron INTEGER NOT NULL DEFAULT 0,
    metal INTEGER NOT NULL DEFAULT 0,
    kryptonite INTEGER NOT NULL DEFAULT 0,
    spice INTEGER NOT NULL DEFAULT 0,
    fleet_json TEXT NOT NULL DEFAULT '{}',
    defense_json TEXT NOT NULL DEFAULT '{}',
    dug_in INTEGER NOT NULL DEFAULT 0,
    last_scanned_ts REAL NOT NULL,
    last_spied_ts REAL NOT NULL DEFAULT 0.0
);

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

CREATE TABLE IF NOT EXISTS raid_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    target_coords TEXT NOT NULL,
    attacked_ts REAL NOT NULL,
    is_exempt INTEGER NOT NULL DEFAULT 0,
    outcome TEXT NOT NULL DEFAULT '',
    loot_json TEXT NOT NULL DEFAULT '{}'
);
CREATE INDEX IF NOT EXISTS idx_raid_history_coords_ts ON raid_history(target_coords, attacked_ts);
"""


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
    return db_path.with_name("attack_history.json")


class GameDatabase:
    """Thread-safe SQLite store for empire telemetry, tutorial progress, actions, and intel."""

    def __init__(
        self,
        db_path: Path | str = DEFAULT_DB_PATH,
        *,
        legacy_rank_json: Optional[Path] = None,
        attack_history_path: Optional[Path | str] = None,
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
                self._conn.executescript(_SCHEMA_SQL)
                existing_cols = {
                    r["name"]
                    for r in self._conn.execute("PRAGMA table_info(spy_targets)").fetchall()
                }
                if "planet_name" not in existing_cols:
                    self._conn.execute(
                        "ALTER TABLE spy_targets ADD COLUMN planet_name TEXT NOT NULL DEFAULT ''"
                    )
                if "status_color" not in existing_cols:
                    self._conn.execute(
                        "ALTER TABLE spy_targets ADD COLUMN status_color TEXT NOT NULL DEFAULT 'yellow'"
                    )
                if "last_spied_ts" not in existing_cols:
                    self._conn.execute(
                        "ALTER TABLE spy_targets ADD COLUMN last_spied_ts REAL NOT NULL DEFAULT 0.0"
                    )
                now_ts = time.time()
                self._conn.execute(
                    "INSERT OR IGNORE INTO meta(key, value, updated_ts) VALUES(?, ?, ?)",
                    ("schema_version", SCHEMA_VERSION, now_ts),
                )
                if legacy_rank_json is not None and legacy_rank_json.exists():
                    self._migrate_legacy_rank_json(legacy_rank_json)
                self._conn.commit()
        except Exception:
            self._conn.close()
            raise

    def _migrate_legacy_rank_json(self, legacy_path: Path) -> None:
        count = self._conn.execute("SELECT COUNT(*) FROM rank_snapshots").fetchone()[0]
        if count > 0:
            return
        try:
            raw = json.loads(legacy_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return
        if not isinstance(raw, list):
            return
        rows = []
        for item in raw:
            if not isinstance(item, Mapping):
                continue
            rows.append(
                (
                    _safe_float(item.get("ts"), 0.0),
                    _sql_int(item.get("universe_id"), 0),
                    _sanitize_text(item.get("nickname")),
                    _sql_optional_int(item.get("server_points")),
                    _sql_optional_int(item.get("server_rank")),
                    _safe_float(item.get("estimated_points"), 0.0),
                    _safe_float(item.get("pts_per_hr"), 0.0),
                    _safe_float(item.get("spice_value"), 0.0),
                    max(1, _sql_int(item.get("planets"), 1)),
                )
            )
        if rows:
            self._conn.executemany(
                """
                INSERT INTO rank_snapshots(
                    ts, universe_id, nickname, server_points, server_rank,
                    estimated_points, pts_per_hr, spice_value, planets
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                rows,
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

            self._conn.commit()
        return self.get_rank_samples()

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
            "SELECT coords, owner, planet_name, status_color, is_inactive, is_spacia, "
            "points, pig_iron, metal, kryptonite, spice, fleet_json, defense_json, "
            "dug_in, last_scanned_ts, last_spied_ts "
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
        best_owner = next((_raw_text(r["owner"], "") for r in ranked if _raw_text(r["owner"], "").strip()), "")
        best_pname = next((_raw_text(r["planet_name"], "") for r in ranked if _raw_text(r["planet_name"], "").strip()), "")

        if norm_row is not None:
            self._conn.execute(
                """
                UPDATE spy_targets
                SET owner = CASE WHEN owner != '' THEN owner ELSE ? END,
                    planet_name = CASE WHEN planet_name != '' THEN planet_name ELSE ? END,
                    is_spacia = ?,
                    points = COALESCE(?, points),
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
                    best_owner,
                    best_pname,
                    best_spacia,
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
                    norm_coords,
                ),
            )
            for lr in legacy_rows:
                self._conn.execute("DELETE FROM spy_targets WHERE coords = ?", (lr["coords"],))
        else:
            keep_coords = best["coords"]
            for lr in legacy_rows:
                if lr["coords"] != keep_coords:
                    self._conn.execute("DELETE FROM spy_targets WHERE coords = ?", (lr["coords"],))
            self._conn.execute(
                """
                UPDATE spy_targets
                SET coords = ?,
                    owner = CASE WHEN owner != '' THEN owner ELSE ? END,
                    planet_name = CASE WHEN planet_name != '' THEN planet_name ELSE ? END,
                    is_spacia = ?,
                    points = COALESCE(points, ?),
                    last_scanned_ts = ?
                WHERE coords = ?
                """,
                (
                    norm_coords,
                    best_owner,
                    best_pname,
                    best_spacia,
                    best_points,
                    max_scanned,
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
        with self._lock:
            self._coalesce_legacy_spy_target_locked(norm_coords, raw_coords)
            self._conn.execute(
                """
                INSERT INTO spy_targets(
                    coords, owner, planet_name, status_color, is_inactive, is_spacia, points,
                    pig_iron, metal, kryptonite, spice,
                    fleet_json, defense_json, dug_in, last_scanned_ts, last_spied_ts
                ) VALUES (?, ?, '', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(coords) DO UPDATE SET
                    owner = CASE WHEN excluded.owner != '' THEN excluded.owner ELSE spy_targets.owner END,
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
                    last_spied_ts = excluded.last_spied_ts
                """,
                (
                    norm_coords,
                    _sanitize_text(report.owner),
                    insert_color,
                    1 if insert_inactive else 0,
                    1 if report.is_spacia else 0,
                    _sql_optional_int(points),
                    _sql_int(report.resources.pig_iron, 0),
                    _sql_int(report.resources.metal, 0),
                    _sql_int(report.resources.kryptonite, 0),
                    _sql_int(report.resources.spice, 0),
                    _json_dumps(dict(report.fleet)),
                    _json_dumps(dict(report.defense)),
                    1 if report.dug_in else 0,
                    now_ts,
                    now_ts,
                    1 if explicit_status else 0,
                    1 if explicit_status else 0,
                ),
            )
            self._conn.commit()

    def record_galaxy_slots(
        self,
        slots: Sequence[GalaxySlot],
        *,
        ts: Optional[float] = None,
    ) -> int:
        now_ts = round(_safe_float(ts, time.time()) if ts is not None else time.time(), 3)
        saved = 0
        with self._lock:
            for slot in slots:
                if slot.status_color == "free":
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
                        coords, owner, planet_name, status_color, is_inactive, is_spacia, last_scanned_ts
                    ) VALUES (?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(coords) DO UPDATE SET
                        owner = excluded.owner,
                        planet_name = excluded.planet_name,
                        status_color = excluded.status_color,
                        is_inactive = excluded.is_inactive,
                        is_spacia = excluded.is_spacia,
                        last_scanned_ts = excluded.last_scanned_ts
                    """,
                    (
                        norm_slot_coords,
                        _sanitize_text(slot.owner),
                        _sanitize_text(slot.name),
                        _sanitize_text(slot.status_color),
                        is_inactive,
                        1 if slot.is_spacia else 0,
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
        with self._lock:
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
            self._conn.commit()

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
        return _sanitize_obj(
            {
                "coords": _sanitize_text(norm_c) if norm_c else "",
                "owner": _sanitize_text(r["owner"]) if r["owner"] is not None else None,
                "planet_name": _sanitize_text(r["planet_name"]) if r["planet_name"] is not None else None,
                "status_color": _sanitize_text(r["status_color"]) if r["status_color"] is not None else None,
                "is_inactive": bool(r["is_inactive"]),
                "is_spacia": bool(r["is_spacia"]),
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
                SELECT coords, owner, planet_name, status_color, is_inactive, is_spacia,
                       points, pig_iron, metal, kryptonite, spice,
                       fleet_json, defense_json, dug_in, last_scanned_ts, last_spied_ts
                FROM spy_targets
                WHERE coords IN ({placeholders})
                ORDER BY (last_spied_ts > 0) DESC, last_scanned_ts DESC
                LIMIT 1
                """,
                cands,
            ).fetchone()
            if row is None:
                all_rows = self._conn.execute(
                    """
                    SELECT coords, owner, planet_name, status_color, is_inactive, is_spacia,
                           points, pig_iron, metal, kryptonite, spice,
                           fleet_json, defense_json, dug_in, last_scanned_ts, last_spied_ts
                    FROM spy_targets
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

    def get_spy_targets(self, *, limit: int = 50) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._conn.execute(
                """
                SELECT coords, owner, planet_name, status_color, is_inactive, is_spacia,
                       points, pig_iron, metal, kryptonite, spice,
                       fleet_json, defense_json, dug_in, last_scanned_ts, last_spied_ts
                FROM spy_targets
                ORDER BY (is_inactive + is_spacia) DESC, (last_spied_ts > 0) DESC, last_scanned_ts DESC
                LIMIT ?
                """,
                (max(1, _sql_int(limit, 50)),),
            ).fetchall()
        return [self._row_to_spy_target_dict(r) for r in rows]

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

        # 3. Galaxy scans count
        gal_scans_count = int(
            self._conn.execute("SELECT COUNT(*) FROM galaxy_scans").fetchone()[0]
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

        telemetry_summary = self._get_full_history_summary_locked()
        return _sanitize_obj(
            {
                "overall": overall,
                "per_target": per_target,
                "history": norm_rows,
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
                "updated_at": round(time.time(), 3),
                "overall": stats["overall"],
                "per_target": stats["per_target"],
                "history": stats["history"],
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
        with self._lock:
            rows = self._conn.execute(
                """
                SELECT target_coords, attacked_ts, is_exempt
                FROM raid_history WHERE attacked_ts >= ? ORDER BY attacked_ts ASC
                """,
                (cutoff,),
            ).fetchall()
        tracker = BashTracker()
        for r in rows:
            if not bool(r["is_exempt"]):
                ts_val = _safe_float(r["attacked_ts"], 0.0)
                if ts_val >= cutoff:
                    tracker.record_attack(
                        normalize_coords(_raw_text(r["target_coords"], "")),
                        timestamp=ts_val,
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

    def summary(self) -> dict[str, Any]:
        tables = (
            "rank_snapshots",
            "planet_snapshots",
            "economy_rates_cache",
            "tutorial_progress",
            "action_history",
            "spy_targets",
            "galaxy_scans",
            "raid_history",
        )
        counts: dict[str, int] = {}
        with self._lock:
            for tbl in tables:
                counts[tbl] = int(
                    self._conn.execute(f"SELECT COUNT(*) FROM {tbl}").fetchone()[0]
                )
        tut_rows = self.get_tutorial_history()
        recent_actions = self.get_recent_actions(limit=12)
        spy_targets = self.get_spy_targets(limit=25)
        size_bytes = (
            self.db_path.stat().st_size
            if (not self._is_memory and self.db_path.exists())
            else 0
        )
        return _sanitize_obj(
            {
                "db_path": ":memory:" if self._is_memory else _sanitize_text(self.db_path),
                "size_bytes": size_bytes,
                "counts": counts,
                "tutorial_history": tut_rows,
                "recent_actions": recent_actions,
                "spy_targets": spy_targets,
            }
        )
