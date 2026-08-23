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
from spaceinvasion.fleet import BashTracker, GalaxySlot, SpyReport
from spaceinvasion.logging_utils import redact_sensitive
from spaceinvasion.state import GameState

DEFAULT_DB_PATH = Path("state") / "spaceinvasion.db"
DEFAULT_ATTACK_HISTORY_PATH = Path("data") / "attack_history.json"
SCHEMA_VERSION = "1"

_RESOURCE_KEYS: tuple[str, ...] = ("pig_iron", "metal", "kryptonite", "spice")
_EXTRA_SID_RE = re.compile(r"(sid=)([A-Za-z0-9_.-]+)", re.IGNORECASE)
_SECRET_KV_RE = re.compile(
    r"\b(password|passwd|cookie|api_key|secret)\s*[:=]\s*([^\s&;,]+)",
    re.IGNORECASE,
)
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
)


def _sanitize_text(text: Any) -> str:
    s = str(redact_sensitive(str(text or "")))
    s = _EXTRA_SID_RE.sub(r"\1REDACTED", s)
    s = _SECRET_KV_RE.sub(r"\1=REDACTED", s)
    return s


def _sanitize_obj(obj: Any) -> Any:
    if isinstance(obj, str):
        return _sanitize_text(obj)
    if isinstance(obj, dict):
        return {_sanitize_obj(k): _sanitize_obj(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_sanitize_obj(v) for v in obj]
    return obj


def _empty_loot_int() -> dict[str, int]:
    return {rk: 0 for rk in _RESOURCE_KEYS}


def _empty_loot_float() -> dict[str, float]:
    return {rk: 0.0 for rk in _RESOURCE_KEYS}


def _normalize_loot(raw: Any) -> dict[str, int]:
    if raw is None:
        return _empty_loot_int()
    src: Any = raw
    if isinstance(src, str):
        txt = src.strip()
        if not txt:
            return _empty_loot_int()
        try:
            src = json.loads(txt)
        except ValueError:
            return _empty_loot_int()
    if isinstance(src, Mapping):
        out: dict[str, int] = {}
        for rk in _RESOURCE_KEYS:
            try:
                val = int(float(src.get(rk, 0) or 0))
            except (TypeError, ValueError):
                val = 0
            out[rk] = max(0, val)
        return out
    out = {}
    for rk in _RESOURCE_KEYS:
        if hasattr(src, rk):
            try:
                val = int(float(getattr(src, rk) or 0))
            except (TypeError, ValueError):
                val = 0
            out[rk] = max(0, val)
        else:
            out[rk] = 0
    return out


def _is_win_outcome(outcome: str, loot_spice_eq: float) -> bool:
    s = str(outcome or "").strip().lower()
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


def _json_dumps(obj: Any) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"))


class GameDatabase:
    """Thread-safe SQLite store for empire telemetry, tutorial progress, actions, and intel."""

    def __init__(
        self,
        db_path: Path | str = DEFAULT_DB_PATH,
        *,
        legacy_rank_json: Optional[Path] = None,
        attack_history_path: Optional[Path | str] = None,
    ) -> None:
        raw_path = str(db_path)
        self._is_memory = raw_path.strip() == ":memory:" or "mode=memory" in raw_path
        if self._is_memory:
            self.db_path = Path(":memory:")
            self.attack_history_path: Optional[Path] = (
                Path(attack_history_path) if attack_history_path is not None else None
            )
            conn_target = raw_path
            use_uri = "mode=memory" in raw_path and raw_path.startswith("file:")
        else:
            self.db_path = Path(db_path)
            self.db_path.parent.mkdir(parents=True, exist_ok=True)
            if attack_history_path is not None:
                self.attack_history_path = Path(attack_history_path)
            elif self.db_path.resolve() == DEFAULT_DB_PATH.resolve():
                self.attack_history_path = DEFAULT_ATTACK_HISTORY_PATH
            else:
                self.attack_history_path = self.db_path.with_name("attack_history.json")
            conn_target = str(self.db_path)
            use_uri = False

        self._lock = threading.Lock()
        self._conn = sqlite3.connect(
            conn_target,
            uri=use_uri,
            timeout=10.0,
            check_same_thread=False,
        )
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
            if not isinstance(item, dict):
                continue
            rows.append(
                (
                    float(item.get("ts") or 0.0),
                    int(item.get("universe_id") or 0),
                    str(item.get("nickname") or ""),
                    item.get("server_points"),
                    item.get("server_rank"),
                    float(item.get("estimated_points") or 0.0),
                    float(item.get("pts_per_hr") or 0.0),
                    float(item.get("spice_value") or 0.0),
                    int(item.get("planets") or 1),
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
        now_ts = float(state.timestamp or time.time())
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
                elapsed = abs(now_ts - float(last_rank["ts"]))
                changed_server = (
                    (state.account_points is not None and state.account_points != last_rank["server_points"])
                    or (state.account_rank is not None and state.account_rank != last_rank["server_rank"])
                )
                changed_est = int(round(est["estimated_points"])) != int(
                    round(float(last_rank["estimated_points"]))
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
                        int(state.universe_id),
                        str(state.nickname),
                        state.account_points,
                        state.account_rank,
                        float(est["estimated_points"]),
                        float(econ["pts_per_hr_steady"]),
                        float(est["spice_value"]),
                        len(state.planets),
                    ),
                )

            # 2. Colony snapshots + economy rate cache
            res_json = _json_dumps(state.researches or {})
            for planet in state.planets:
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
                        (planet.planet_id, planet.coords, rates_json, now_ts),
                    )

                last_p = self._conn.execute(
                    """
                    SELECT ts, buildings_json, ships_json, economy_rates_json
                    FROM planet_snapshots WHERE planet_id = ? ORDER BY id DESC LIMIT 1
                    """,
                    (planet.planet_id,),
                ).fetchone()
                should_snap = False
                if last_p is None:
                    should_snap = True
                else:
                    elapsed_p = abs(now_ts - float(last_p["ts"]))
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
                            int(state.universe_id),
                            int(planet.planet_id),
                            str(planet.coords),
                            str(planet.name),
                            str(planet.planet_type),
                            int(planet.temperature),
                            int(planet.fields_used),
                            int(planet.fields_max),
                            int(planet.resources.pig_iron),
                            int(planet.resources.metal),
                            int(planet.resources.kryptonite),
                            int(planet.resources.spice),
                            int(planet.energy.produced),
                            int(planet.energy.used),
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
                tasks_payload = [
                    {
                        "description": t.description,
                        "category": t.category,
                        "current": t.current,
                        "target": t.target,
                        "completed": t.completed,
                    }
                    for t in tut.tasks
                ]
                self._conn.execute(
                    """
                    UPDATE tutorial_progress
                    SET completed_ts = COALESCE(completed_ts, ?)
                    WHERE step < ? AND completed_ts IS NULL
                    """,
                    (now_ts, int(tut.step)),
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
                        int(tut.step),
                        int(tut.total_steps),
                        _json_dumps(tasks_payload),
                        str(tut.reward_text),
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
                (max(1, int(limit)),),
            ).fetchall()
        return [
            {
                "ts": float(r["ts"]),
                "server_points": r["server_points"],
                "server_rank": r["server_rank"],
                "estimated_points": float(r["estimated_points"]),
                "pts_per_hr": float(r["pts_per_hr"]),
                "spice_value": float(r["spice_value"]),
                "planets": int(r["planets"]),
            }
            for r in rows
        ]

    def save_economy_rates(
        self,
        planet_id: int,
        coords: str,
        rates: dict[str, int],
        ts: Optional[float] = None,
    ) -> None:
        if not rates:
            return
        now_ts = float(ts or time.time())
        clean = {str(k): int(v) for k, v in rates.items()}
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
                (int(planet_id), str(coords), _json_dumps(clean), now_ts),
            )
            self._conn.commit()

    def get_all_economy_rates(self) -> dict[int, dict[str, int]]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT planet_id, rates_json FROM economy_rates_cache"
            ).fetchall()
        out: dict[int, dict[str, int]] = {}
        for row in rows:
            try:
                parsed = json.loads(row["rates_json"])
                if isinstance(parsed, dict):
                    out[int(row["planet_id"])] = {str(k): int(v) for k, v in parsed.items()}
            except ValueError:
                continue
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
        now_ts = float(ts or time.time())
        if is_dataclass(action):
            act_map = redact_sensitive(asdict(action))
        elif isinstance(action, dict):
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
                    str(source),
                    str(command),
                    act_map.get("priority"),
                    str(act_map.get("kind") or command.upper()),
                    act_map.get("planet_id"),
                    act_map.get("planet_coords"),
                    act_map.get("target_id"),
                    act_map.get("target_name"),
                    act_map.get("target_level"),
                    1 if executed else 0,
                    1 if dry_run else 0,
                    str(redact_sensitive(act_map.get("reason") or "")),
                    str(redact_sensitive(message or "")),
                ),
            )
            self._conn.commit()

    def record_spy_report(
        self,
        report: SpyReport,
        *,
        points: Optional[int] = None,
        is_inactive: bool = False,
        status_color: Optional[str] = None,
        ts: Optional[float] = None,
    ) -> None:
        now_ts = round(float(ts if ts is not None else time.time()), 3)
        explicit_status = status_color is not None or bool(is_inactive) or bool(report.is_spacia)
        insert_inactive = bool(is_inactive or report.is_spacia or status_color == "yellow")
        insert_color = status_color or ("yellow" if insert_inactive else "light_red")
        with self._lock:
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
                    report.target_coords,
                    str(report.owner),
                    insert_color,
                    1 if insert_inactive else 0,
                    1 if report.is_spacia else 0,
                    points,
                    int(report.resources.pig_iron),
                    int(report.resources.metal),
                    int(report.resources.kryptonite),
                    int(report.resources.spice),
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
        now_ts = round(float(ts if ts is not None else time.time()), 3)
        saved = 0
        with self._lock:
            for slot in slots:
                if slot.status_color == "free":
                    continue
                is_inactive = 1 if (slot.status_color == "yellow" or slot.is_spacia) else 0
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
                        str(slot.coords),
                        str(slot.owner),
                        str(slot.name),
                        str(slot.status_color),
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
        now_ts = round(float(ts if ts is not None else time.time()), 3)
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
                    int(galaxy),
                    int(system),
                    now_ts,
                    int(occupied_count),
                    int(target_count),
                    int(free_count),
                ),
            )
            self._conn.commit()

    def get_galaxy_scans(
        self,
        *,
        max_age_seconds: Optional[float] = None,
        now: Optional[float] = None,
    ) -> dict[str, float]:
        with self._lock:
            if max_age_seconds is not None:
                ref = float(now if now is not None else time.time())
                cutoff = ref - float(max_age_seconds)
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
        return {
            f"{int(r['galaxy']):02d}:{int(r['system']):03d}": float(r["last_scanned_ts"])
            for r in rows
        }

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
                (int(building_id),),
            ).fetchall()
        return {int(r["planet_id"]): float(r["last_ts"]) for r in rows}

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
                (max(1, int(limit)),),
            ).fetchall()
        out: list[dict[str, Any]] = []
        for r in rows:
            spied_ts = float(r["last_spied_ts"] or 0.0)
            out.append(
                {
                    "coords": r["coords"],
                    "owner": r["owner"],
                    "planet_name": r["planet_name"],
                    "status_color": r["status_color"],
                    "is_inactive": bool(r["is_inactive"]),
                    "is_spacia": bool(r["is_spacia"]),
                    "points": r["points"],
                    "resources": {
                        "pig_iron": int(r["pig_iron"]),
                        "metal": int(r["metal"]),
                        "kryptonite": int(r["kryptonite"]),
                        "spice": int(r["spice"]),
                    },
                    "fleet": json.loads(r["fleet_json"] or "{}"),
                    "defense": json.loads(r["defense_json"] or "{}"),
                    "dug_in": bool(r["dug_in"]),
                    "last_scanned_ts": float(r["last_scanned_ts"]),
                    "last_spied_ts": spied_ts,
                    "has_spy_intel": spied_ts > 0.0,
                }
            )
        return out

    def record_raid(
        self,
        target_coords: str,
        *,
        attacked_ts: Optional[float] = None,
        is_exempt: bool = False,
        outcome: str = "",
        loot: Optional[dict[str, int]] = None,
    ) -> None:
        ts_val = float(attacked_ts if attacked_ts is not None else time.time())
        clean_outcome = _sanitize_text(str(outcome or ""))
        norm_loot = _normalize_loot(loot) if loot else {}
        with self._lock:
            self._conn.execute(
                """
                INSERT INTO raid_history(target_coords, attacked_ts, is_exempt, outcome, loot_json)
                VALUES (?, ?, ?, ?, ?)
                """,
                (
                    str(target_coords),
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
                tot_loot[rk] += int(r["loot"][rk])
            sp = float(r["loot_spice_eq"])
            sp_values.append(sp)
            tot_sp += sp
            order_w = 0.65 ** ((n - 1) - idx)
            ts_i = float(r["attacked_ts"])
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
        weighted_sum = sum(w * sp for w, sp in zip(weights, sp_values))
        avg_loot = {rk: round(tot_loot[rk] / n, 3) for rk in _RESOURCE_KEYS}
        return {
            "attempts": n,
            "wins": wins,
            "losses": losses,
            "success_rate": round(wins / n, 6),
            "total_loot": tot_loot,
            "avg_loot": avg_loot,
            "total_loot_spice_eq": round(tot_sp, 3),
            "avg_loot_spice_eq": round(tot_sp / n, 3),
            "recency_weighted_loot_spice_eq": round(weighted_sum / max(1e-12, weight_tot), 3),
        }

    def _query_norm_raid_rows_locked(
        self,
        *,
        window_seconds: Optional[float] = None,
        now: Optional[float] = None,
    ) -> list[dict[str, Any]]:
        if window_seconds is not None:
            ref_cutoff = float(now if now is not None else time.time())
            cutoff = ref_cutoff - max(0.0, float(window_seconds))
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
            loot_spice_eq = round(float(game_data.spice_equivalent(loot)), 3)
            clean_outcome = _sanitize_text(r["outcome"])
            won = _is_win_outcome(clean_outcome, loot_spice_eq)
            norm_rows.append(
                {
                    "id": int(r["id"]),
                    "target_coords": str(r["target_coords"] or ""),
                    "attacked_ts": float(r["attacked_ts"] or 0.0),
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
        spied_count = sum(1 for r in spy_rows if float(r["last_spied_ts"] or 0.0) > 0.0)
        total_known_res_sp = round(
            sum(
                float(
                    game_data.spice_equivalent(
                        {
                            "pig_iron": int(r["pig_iron"] or 0),
                            "metal": int(r["metal"] or 0),
                            "kryptonite": int(r["kryptonite"] or 0),
                            "spice": int(r["spice"] or 0),
                        }
                    )
                )
                for r in spy_rows
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
            pid = int(r["planet_id"])
            try:
                bld_map = json.loads(r["buildings_json"] or "{}")
                if not isinstance(bld_map, dict):
                    bld_map = {}
            except ValueError:
                bld_map = {}
            try:
                shp_map = json.loads(r["ships_json"] or "{}")
                if not isinstance(shp_map, dict):
                    shp_map = {}
            except ValueError:
                shp_map = {}
            try:
                econ_map = json.loads(r["economy_rates_json"] or "{}")
                if not isinstance(econ_map, dict):
                    econ_map = {}
            except ValueError:
                econ_map = {}
            res_dict = {
                "pig_iron": int(r["pig_iron"] or 0),
                "metal": int(r["metal"] or 0),
                "kryptonite": int(r["kryptonite"] or 0),
                "spice": int(r["spice"] or 0),
            }
            snap_info = {
                "planet_id": pid,
                "coords": str(r["coords"] or ""),
                "planet_type": str(r["planet_type"] or "normal"),
                "fields_used": int(r["fields_used"] or 0),
                "fields_max": int(r["fields_max"] or 0),
                "resources": res_dict,
                "resources_spice_eq": round(float(game_data.spice_equivalent(res_dict)), 3),
                "energy_produced": int(r["energy_produced"] or 0),
                "energy_used": int(r["energy_used"] or 0),
                "buildings_total_levels": sum(int(v or 0) for v in bld_map.values()),
                "ships_total": sum(int(v or 0) for v in shp_map.values()),
                "economy_rates": {str(k): int(v or 0) for k, v in econ_map.items()},
            }
            if pid not in first_by_pid:
                first_by_pid[pid] = snap_info
            latest_by_pid[pid] = snap_info

        first_bld_lvls = sum(v["buildings_total_levels"] for v in first_by_pid.values())
        latest_bld_lvls = sum(v["buildings_total_levels"] for v in latest_by_pid.values())
        first_fields = sum(v["fields_used"] for v in first_by_pid.values())
        latest_fields = sum(v["fields_used"] for v in latest_by_pid.values())
        latest_e_net = sum(v["energy_produced"] - v["energy_used"] for v in latest_by_pid.values())
        first_res_sp = round(sum(v["resources_spice_eq"] for v in first_by_pid.values()), 3)
        latest_res_sp = round(sum(v["resources_spice_eq"] for v in latest_by_pid.values()), 3)
        latest_by_planet_clean = {
            str(pid): {k: v for k, v in info.items() if k != "resources_spice_eq"}
            for pid, info in latest_by_pid.items()
        }
        planet_summary = {
            "count": p_count,
            "distinct_planets": len(latest_by_pid),
            "first_ts": float(p_rows[0]["ts"]) if p_rows else None,
            "latest_ts": float(p_rows[-1]["ts"]) if p_rows else None,
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
                "resources_spice_eq_delta": round(latest_res_sp - first_res_sp, 3),
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
        est_vals = [float(r["estimated_points"] or 0.0) for r in rk_rows]
        sp_vals = [int(r["server_points"]) for r in rk_rows if r["server_points"] is not None]
        sr_vals = [int(r["server_rank"]) for r in rk_rows if r["server_rank"] is not None]
        pph_vals = [float(r["pts_per_hr"] or 0.0) for r in rk_rows]
        spice_vals = [float(r["spice_value"] or 0.0) for r in rk_rows]

        est_delta = round((est_vals[-1] - est_vals[0]), 3) if rk_count >= 1 else 0.0
        srv_pts_delta = (sp_vals[-1] - sp_vals[0]) if len(sp_vals) >= 1 else 0
        rank_climbed = (sr_vals[0] - sr_vals[-1]) if len(sr_vals) >= 1 else 0
        pph_delta = round((pph_vals[-1] - pph_vals[0]), 3) if rk_count >= 1 else 0.0
        latest_pph = round(pph_vals[-1], 3) if rk_count >= 1 else 0.0
        rank_trajectory = [
            {
                "ts": float(r["ts"]),
                "estimated_points": float(r["estimated_points"] or 0.0),
                "server_points": r["server_points"],
                "server_rank": r["server_rank"],
                "pts_per_hr": float(r["pts_per_hr"] or 0.0),
                "spice_value": float(r["spice_value"] or 0.0),
                "planets": int(r["planets"] or 1),
            }
            for r in rk_rows[-240:]
        ]
        rank_summary = {
            "count": rk_count,
            "first_ts": float(rk_rows[0]["ts"]) if rk_rows else None,
            "latest_ts": float(rk_rows[-1]["ts"]) if rk_rows else None,
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
            "avg_pts_per_hr": round(sum(pph_vals) / len(pph_vals), 3) if pph_vals else 0.0,
            "pts_per_hr_delta": pph_delta,
            "latest_spice_value": round(spice_vals[-1], 3) if spice_vals else 0.0,
            "spice_value_delta": round((spice_vals[-1] - spice_vals[0]), 3) if spice_vals else 0.0,
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
            kind = str(r["kind"] or "UNKNOWN")
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

        points_delta_val = float(srv_pts_delta) if srv_pts_delta != 0 else est_delta
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
        ref_ts = float(now) if (now is not None and now > 0.0) else max_row_ts

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
        target = Path(self.attack_history_path)
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
                json.dump(payload, tf, indent=2, sort_keys=True)
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
        ref = float(now if now is not None else time.time())
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
                tracker.record_attack(
                    r["target_coords"],
                    timestamp=float(r["attacked_ts"]),
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
                (max(1, int(limit)),),
            ).fetchall()
        return [dict(r) for r in rows]

    def get_planet_history(self, planet_id: int, *, limit: int = 50) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._conn.execute(
                """
                SELECT ts, planet_id, coords, name, pig_iron, metal, kryptonite, spice,
                       energy_produced, energy_used, buildings_json, economy_rates_json
                FROM planet_snapshots WHERE planet_id = ? ORDER BY id DESC LIMIT ?
                """,
                (int(planet_id), max(1, int(limit))),
            ).fetchall()
        out = []
        for r in rows:
            item = dict(r)
            item["buildings"] = json.loads(item.pop("buildings_json", "{}"))
            item["economy_rates"] = json.loads(item.pop("economy_rates_json", "{}"))
            out.append(item)
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
            item["tasks"] = json.loads(item.pop("tasks_json", "[]"))
            item["can_complete"] = bool(item["can_complete"])
            out.append(item)
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
        return {
            "db_path": ":memory:" if self._is_memory else str(self.db_path),
            "size_bytes": size_bytes,
            "counts": counts,
            "tutorial_history": tut_rows,
            "recent_actions": recent_actions,
            "spy_targets": spy_targets,
        }
