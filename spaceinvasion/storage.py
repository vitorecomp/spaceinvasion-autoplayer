"""SQLite persistence layer for SpaceInvasion Autoplayer telemetry, tutorial progress, actions, and intel."""

from __future__ import annotations

from dataclasses import asdict, is_dataclass
import json
from pathlib import Path
import sqlite3
import threading
import time
from typing import Any, Optional

from spaceinvasion.fleet import BashTracker, SpyReport
from spaceinvasion.logging_utils import redact_sensitive
from spaceinvasion.state import GameState

DEFAULT_DB_PATH = Path("state") / "spaceinvasion.db"
SCHEMA_VERSION = "1"

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
    last_scanned_ts REAL NOT NULL
);

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
        db_path: Path = DEFAULT_DB_PATH,
        *,
        legacy_rank_json: Optional[Path] = None,
    ) -> None:
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(
            str(self.db_path),
            timeout=10.0,
            check_same_thread=False,
        )
        self._conn.row_factory = sqlite3.Row
        with self._lock:
            self._conn.execute("PRAGMA journal_mode=WAL;")
            self._conn.execute("PRAGMA synchronous=NORMAL;")
            self._conn.execute("PRAGMA foreign_keys=ON;")
            self._conn.executescript(_SCHEMA_SQL)
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
        ts: Optional[float] = None,
    ) -> None:
        now_ts = float(ts or time.time())
        with self._lock:
            self._conn.execute(
                """
                INSERT INTO spy_targets(
                    coords, owner, is_inactive, is_spacia, points,
                    pig_iron, metal, kryptonite, spice,
                    fleet_json, defense_json, dug_in, last_scanned_ts
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(coords) DO UPDATE SET
                    owner = excluded.owner,
                    is_inactive = excluded.is_inactive,
                    is_spacia = excluded.is_spacia,
                    points = COALESCE(excluded.points, spy_targets.points),
                    pig_iron = excluded.pig_iron,
                    metal = excluded.metal,
                    kryptonite = excluded.kryptonite,
                    spice = excluded.spice,
                    fleet_json = excluded.fleet_json,
                    defense_json = excluded.defense_json,
                    dug_in = excluded.dug_in,
                    last_scanned_ts = excluded.last_scanned_ts
                """,
                (
                    report.target_coords,
                    str(report.owner),
                    1 if is_inactive else 0,
                    1 if report.is_spacia else 0,
                    points,
                    int(report.resources.pig_iron),
                    int(report.resources.metal),
                    int(report.resources.kryptonite),
                    int(report.resources.spice),
                    _json_dumps(dict(report.fleet)),
                    _json_dumps(dict(report.defense)),
                    1 if report.dug_in else 0,
                    round(now_ts, 3),
                ),
            )
            self._conn.commit()

    def record_raid(
        self,
        target_coords: str,
        *,
        attacked_ts: Optional[float] = None,
        is_exempt: bool = False,
        outcome: str = "",
        loot: Optional[dict[str, int]] = None,
    ) -> None:
        ts_val = float(attacked_ts or time.time())
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
                    str(outcome),
                    _json_dumps(loot or {}),
                ),
            )
            self._conn.commit()

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
        size_bytes = self.db_path.stat().st_size if self.db_path.exists() else 0
        return {
            "db_path": str(self.db_path),
            "size_bytes": size_bytes,
            "counts": counts,
            "tutorial_history": tut_rows,
            "recent_actions": recent_actions,
        }
