"""Versioned, append-only database migration and multi-store versioning engine.

Manages forward-only schema evolution, SHA-256 DDL checksums, savepoint-isolated
execution, online SQLite `.backup()` snapshots, integrity verification, and
multi-database / JSON sidecar store version tracking without destructive deletes.
"""

from __future__ import annotations

import argparse
import contextlib
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import re
import sqlite3
import sys
import tempfile
import time
from typing import Any, Callable, Mapping, Optional, Sequence

from spaceinvasion.core.logging_utils import redact_sensitive

DEFAULT_GAME_DB_PATH = Path("state") / "spaceinvasion.db"
DEFAULT_ATTACK_HISTORY_JSON_PATH = Path("data") / "attack_history.json"
DEFAULT_RANK_HISTORY_JSON_PATH = Path("state") / "rank_history.json"
DEFAULT_SNAPSHOT_JSON_PATH = Path("state") / "snapshot.json"
DEFAULT_CONFIG_YAML_PATH = Path("config.yaml")

LATEST_SCHEMA_VERSION: int = 8
SCHEMA_VERSION: str = str(LATEST_SCHEMA_VERSION)
ATTACK_HISTORY_SCHEMA_VERSION: int = 2

PROTECTED_META_KEYS: frozenset[str] = frozenset(
    {
        "schema_version",
        "schema_version_tag",
        "schema_checksum",
    }
)

_SAFE_IDENT_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def _quote_ident(ident: str) -> str:
    clean = str(ident).strip()
    if not _SAFE_IDENT_RE.match(clean):
        raise ValueError(f"Unsafe SQL identifier: {ident!r}")
    return f'"{clean}"'


def _clean_json_obj(obj: Any, *, _depth: int = 0) -> Any:
    if _depth > 32:
        return "[Truncated]"
    if obj is None or isinstance(obj, bool):
        return obj
    if isinstance(obj, int):
        return max(-(1 << 53) + 1, min((1 << 53) - 1, obj))
    if isinstance(obj, float):
        return obj if math.isfinite(obj) else None
    if isinstance(obj, (bytes, bytearray)):
        return str(redact_sensitive(bytes(obj).decode("utf-8", errors="replace")))
    if isinstance(obj, (str, Path)):
        return str(redact_sensitive(str(obj)))
    if isinstance(obj, Mapping):
        return {
            str(redact_sensitive(str(k))): _clean_json_obj(v, _depth=_depth + 1)
            for k, v in obj.items()
        }
    if isinstance(obj, (list, tuple, set, frozenset)):
        return [_clean_json_obj(v, _depth=_depth + 1) for v in obj]
    return str(redact_sensitive(str(obj)))


def _safe_json_dumps(payload: Any, *, indent: Optional[int] = None) -> str:
    cleaned = _clean_json_obj(payload)
    return json.dumps(
        cleaned,
        sort_keys=True,
        indent=indent,
        separators=(",", ":") if indent is None else None,
        allow_nan=False,
    )


def split_sql_statements(sql_script: str) -> list[str]:
    """Split a multi-statement SQL string on top-level semicolons without `executescript`.

    Executing statements individually via `conn.execute(...)` preserves active
    SQLite `SAVEPOINT` transactions so failed migrations roll back atomically.
    """
    statements: list[str] = []
    buf: list[str] = []
    in_single = False
    in_double = False
    in_line_comment = False
    in_block_comment = False
    i = 0
    n = len(sql_script)

    while i < n:
        ch = sql_script[i]
        nxt = sql_script[i + 1] if i + 1 < n else ""

        if in_line_comment:
            if ch == "\n":
                in_line_comment = False
                buf.append(ch)
            i += 1
            continue

        if in_block_comment:
            if ch == "*" and nxt == "/":
                in_block_comment = False
                i += 2
            else:
                i += 1
            continue

        if not in_single and not in_double:
            if ch == "-" and nxt == "-":
                in_line_comment = True
                i += 2
                continue
            if ch == "/" and nxt == "*":
                in_block_comment = True
                i += 2
                continue

        if ch == "'" and not in_double:
            buf.append(ch)
            if in_single and nxt == "'":
                buf.append(nxt)
                i += 2
                continue
            in_single = not in_single
            i += 1
            continue

        if ch == '"' and not in_single:
            buf.append(ch)
            if in_double and nxt == '"':
                buf.append(nxt)
                i += 2
                continue
            in_double = not in_double
            i += 1
            continue

        if ch == ";" and not in_single and not in_double:
            stmt = "".join(buf).strip()
            if stmt:
                statements.append(stmt)
            buf.clear()
            i += 1
            continue

        buf.append(ch)
        i += 1

    tail = "".join(buf).strip()
    if tail:
        statements.append(tail)
    return statements


@dataclass(frozen=True)
class Migration:
    """Single forward-only, non-destructive database schema migration."""

    version: int
    name: str
    description: str
    sql: str = ""
    column_additions: tuple[tuple[str, str, str], ...] = ()
    data_hook_name: str = ""

    @property
    def version_tag(self) -> str:
        return f"V{int(self.version):03d}"

    @property
    def checksum(self) -> str:
        normalized_stmts = [
            re.sub(r"\s+", " ", s.strip())
            for s in split_sql_statements(self.sql)
        ]
        normalized_cols = [
            f"{tbl}.{col}:{re.sub(r'\\s+', ' ', ddl.strip())}"
            for tbl, col, ddl in self.column_additions
        ]
        canonical = _safe_json_dumps(
            {
                "version": int(self.version),
                "name": self.name,
                "statements": normalized_stmts,
                "columns": normalized_cols,
                "data_hook": self.data_hook_name,
            }
        )
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


SCHEMA_LEDGER_DDL = """
CREATE TABLE IF NOT EXISTS schema_migrations (
    version INTEGER PRIMARY KEY,
    version_tag TEXT NOT NULL,
    name TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    checksum TEXT NOT NULL,
    applied_ts REAL NOT NULL,
    duration_ms REAL NOT NULL DEFAULT 0.0,
    app_schema_version TEXT NOT NULL DEFAULT '',
    details_json TEXT NOT NULL DEFAULT '{}'
);

CREATE TABLE IF NOT EXISTS schema_migration_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts REAL NOT NULL,
    event_type TEXT NOT NULL,
    from_version INTEGER NOT NULL DEFAULT 0,
    to_version INTEGER NOT NULL DEFAULT 0,
    version INTEGER,
    name TEXT NOT NULL DEFAULT '',
    checksum TEXT NOT NULL DEFAULT '',
    backup_path TEXT NOT NULL DEFAULT '',
    details_json TEXT NOT NULL DEFAULT '{}'
);
CREATE INDEX IF NOT EXISTS idx_schema_migration_events_ts
    ON schema_migration_events(ts);
"""


# ---------------------------------------------------------------------------
# Canonical Ordered Migrations for `state/spaceinvasion.db` (V001 .. V005)
# ---------------------------------------------------------------------------

_V001_SQL = """
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

CREATE TABLE IF NOT EXISTS config_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts REAL NOT NULL,
    config_json TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_config_history_ts ON config_history(ts);
"""

_V002_SQL = """
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
"""

_V002_COLUMNS: tuple[tuple[str, str, str], ...] = (
    ("spy_targets", "planet_name", "TEXT NOT NULL DEFAULT ''"),
    ("spy_targets", "status_color", "TEXT NOT NULL DEFAULT 'yellow'"),
    ("spy_targets", "last_spied_ts", "REAL NOT NULL DEFAULT 0.0"),
    ("spy_targets", "has_debris", "INTEGER NOT NULL DEFAULT 0"),
    ("spy_targets", "blocked_until_ts", "REAL NOT NULL DEFAULT 0.0"),
    ("spy_targets", "block_reason", "TEXT NOT NULL DEFAULT ''"),
    ("spy_targets", "bash_hits", "INTEGER NOT NULL DEFAULT 0"),
    ("spy_targets", "last_blocked_ts", "REAL NOT NULL DEFAULT 0.0"),
    ("spy_targets", "uninhabited_fields", "INTEGER"),
    ("spy_targets", "spy_count", "INTEGER NOT NULL DEFAULT 0"),
    ("spy_targets", "is_own_planet", "INTEGER NOT NULL DEFAULT 0"),
    ("spy_targets", "merged_into", "TEXT NOT NULL DEFAULT ''"),
    ("spy_reports", "planet_name", "TEXT NOT NULL DEFAULT ''"),
    ("spy_reports", "buildings_json", "TEXT NOT NULL DEFAULT '{}'"),
    ("spy_reports", "researches_json", "TEXT NOT NULL DEFAULT '{}'"),
    ("spy_reports", "server_msg_id", "TEXT NOT NULL DEFAULT ''"),
    ("spy_reports", "msg_date_text", "TEXT NOT NULL DEFAULT ''"),
    ("spy_reports", "raw_text", "TEXT NOT NULL DEFAULT ''"),
)

_V003_SQL = """
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
"""

_V003_COLUMNS: tuple[tuple[str, str, str], ...] = (
    ("recycle_reports", "harvested_kryptonite", "INTEGER NOT NULL DEFAULT 0"),
    ("recycle_reports", "harvested_spice", "INTEGER NOT NULL DEFAULT 0"),
)

_V004_SQL = """
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
"""

_V005_SQL = (
    SCHEMA_LEDGER_DDL
    + """
CREATE INDEX IF NOT EXISTS idx_spy_targets_colony_filter
    ON spy_targets(is_own_planet, merged_into, is_spacia, is_inactive);
CREATE INDEX IF NOT EXISTS idx_battle_reports_ts
    ON battle_reports(ts);
CREATE INDEX IF NOT EXISTS idx_recycle_reports_ts
    ON recycle_reports(ts);
"""
)


GAME_DB_MIGRATIONS: tuple[Migration, ...] = (
    Migration(
        version=1,
        name="v001_core_empire_telemetry",
        description="Core empire telemetry tables (meta, rank_snapshots, planet_snapshots, economy_rates_cache, tutorial_progress, action_history, config_history).",
        sql=_V001_SQL,
    ),
    Migration(
        version=2,
        name="v002_espionage_galaxy_and_raid_ledger",
        description="Espionage intel, longitudinal galaxy scans/slots, 24h raid history, and bash-block tracking.",
        sql=_V002_SQL,
        column_additions=_V002_COLUMNS,
    ),
    Migration(
        version=3,
        name="v003_inbox_battle_and_recycle_reports",
        description="Append-only game_messages archive plus structured battle_reports and 4-resource recycle_reports.",
        sql=_V003_SQL,
        column_additions=_V003_COLUMNS,
    ),
    Migration(
        version=4,
        name="v004_manual_command_queue",
        description="Operator manual_queue table with priority/order controls and immutable manual_queue_events audit log.",
        sql=_V004_SQL,
    ),
    Migration(
        version=5,
        name="v005_migration_ledger_and_analytics_indexes",
        description="Versioned schema_migrations ledger, append-only schema_migration_events log, and analytical report/colony indexes.",
        sql=_V005_SQL,
    ),
    Migration(
        version=6,
        name="v006_absorb_legacy_attack_json_and_retire_mirror",
        description="Consolidate raid_history and bash_blocks solely in SQLite, absorb legacy attack_history.json rows non-destructively, and index raid_history(attacked_ts).",
        sql="""
CREATE INDEX IF NOT EXISTS idx_raid_history_ts
    ON raid_history(attacked_ts);
CREATE INDEX IF NOT EXISTS idx_bash_blocks_until_ts
    ON bash_blocks(blocked_until_ts);
""",
    ),
    Migration(
        version=7,
        name="v007_consolidate_rank_and_state_snapshots_in_sqlite",
        description="Add append-only state_snapshots table and retire legacy state/rank_history.json and state/snapshot.json into SQLite.",
        sql="""
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

CREATE INDEX IF NOT EXISTS idx_state_snapshots_ts
    ON state_snapshots(ts);
""",
    ),
    Migration(
        version=8,
        name="v008_db_strategy_config_and_ui_notifications",
        description="Consolidate StrategyConfig into strategy_config_store (retiring config.yaml) and add persistent ui_notifications for action-validation help prompts.",
        sql="""
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

CREATE INDEX IF NOT EXISTS idx_ui_notifications_status_ts
    ON ui_notifications(status, updated_ts);

CREATE INDEX IF NOT EXISTS idx_ui_notifications_dedupe
    ON ui_notifications(dedupe_key, status);
""",
    ),
)

GAME_REQUIRED_TABLES: tuple[str, ...] = (
    "meta",
    "rank_snapshots",
    "planet_snapshots",
    "state_snapshots",
    "strategy_config_store",
    "ui_notifications",
    "economy_rates_cache",
    "tutorial_progress",
    "action_history",
    "config_history",
    "spy_targets",
    "spy_reports",
    "galaxy_scans",
    "galaxy_scan_history",
    "galaxy_slots",
    "galaxy_slot_history",
    "raid_history",
    "bash_blocks",
    "game_messages",
    "battle_reports",
    "recycle_reports",
    "manual_queue",
    "manual_queue_events",
    "schema_migrations",
    "schema_migration_events",
)


def compute_catalog_head_checksum(migrations: Sequence[Migration] = GAME_DB_MIGRATIONS) -> str:
    """Compute aggregate SHA-256 checksum across an ordered migration sequence."""
    ordered = sorted(migrations, key=lambda m: m.version)
    blob = "|".join(f"{m.version}:{m.name}:{m.checksum}" for m in ordered)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def create_sqlite_backup(
    conn: sqlite3.Connection,
    db_path: Path,
    *,
    from_version: int = 0,
    to_version: int = LATEST_SCHEMA_VERSION,
    label: str = "upgrade",
    backup_dir: Optional[Path] = None,
) -> Path:
    """Create a hot, WAL-consistent online SQLite backup via `sqlite3.Connection.backup`."""
    resolved_db = Path(db_path)
    out_dir = backup_dir if backup_dir is not None else (resolved_db.parent / "backups")
    out_dir.mkdir(parents=True, exist_ok=True)
    safe_label = re.sub(r"[^A-Za-z0-9_.-]+", "_", str(label or "backup").strip())[:40] or "backup"
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    ns_tail = time.time_ns() % 1_000_000
    dest_name = (
        f"{resolved_db.stem}.{safe_label}.v{max(0, int(from_version)):03d}_to_v{max(0, int(to_version)):03d}"
        f".{stamp}_{ns_tail:06d}.db"
    )
    dest_path = out_dir / dest_name
    tmp_fd, tmp_raw = tempfile.mkstemp(prefix=f".{resolved_db.stem}.bkp.", suffix=".tmp", dir=str(out_dir))
    os.close(tmp_fd)
    tmp_path = Path(tmp_raw)
    try:
        with contextlib.closing(sqlite3.connect(str(tmp_path))) as dst_conn:
            conn.backup(dst_conn)
            dst_conn.commit()
        os.replace(tmp_path, dest_path)
        return dest_path
    except Exception:
        try:
            tmp_path.unlink(missing_ok=True)
        except OSError:
            pass
        raise


class MigrationRunner:
    """Forward-only, savepoint-isolated migration runner for any SQLite database."""

    def __init__(
        self,
        migrations: Sequence[Migration] = GAME_DB_MIGRATIONS,
        *,
        schema_family: str = "game",
        required_tables: Sequence[str] = GAME_REQUIRED_TABLES,
        data_hooks: Optional[Mapping[str, Callable[[sqlite3.Connection, float], Optional[dict[str, Any]]]]] = None,
    ) -> None:
        ordered = tuple(sorted(migrations, key=lambda m: int(m.version)))
        versions = [m.version for m in ordered]
        if len(versions) != len(set(versions)):
            raise ValueError(f"Duplicate migration versions in {schema_family!r}: {versions}")
        if any(v <= 0 for v in versions):
            raise ValueError(f"Migration versions must be >= 1 in {schema_family!r}: {versions}")
        self.schema_family = str(schema_family or "game")
        self.migrations: tuple[Migration, ...] = ordered
        self.required_tables: tuple[str, ...] = tuple(required_tables)
        self.data_hooks: dict[str, Callable[[sqlite3.Connection, float], Optional[dict[str, Any]]]] = (
            dict(data_hooks) if data_hooks else {}
        )

    @property
    def latest_version(self) -> int:
        return self.migrations[-1].version if self.migrations else 0

    @property
    def head_checksum(self) -> str:
        return compute_catalog_head_checksum(self.migrations)

    @staticmethod
    def _table_exists(conn: sqlite3.Connection, table_name: str) -> bool:
        row = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ? LIMIT 1",
            (table_name,),
        ).fetchone()
        return row is not None

    @staticmethod
    def _read_user_version(conn: sqlite3.Connection) -> int:
        row = conn.execute("PRAGMA user_version;").fetchone()
        return int(row[0] or 0) if row is not None else 0

    @classmethod
    def _read_meta_map(cls, conn: sqlite3.Connection) -> dict[str, str]:
        if not cls._table_exists(conn, "meta"):
            return {}
        try:
            rows = conn.execute(
                "SELECT key, value FROM meta WHERE key IN ('schema_version', 'schema_version_tag', 'schema_checksum')"
            ).fetchall()
        except sqlite3.DatabaseError:
            return {}
        out: dict[str, str] = {}
        for r in rows:
            k = str(r[0] if not isinstance(r, sqlite3.Row) else r["key"])
            v = str(r[1] if not isinstance(r, sqlite3.Row) else r["value"])
            out[k] = v
        return out

    @classmethod
    def _read_applied_rows(cls, conn: sqlite3.Connection) -> dict[int, dict[str, Any]]:
        if not cls._table_exists(conn, "schema_migrations"):
            return {}
        rows = conn.execute(
            """
            SELECT version, version_tag, name, description, checksum,
                   applied_ts, duration_ms, app_schema_version, details_json
            FROM schema_migrations
            ORDER BY version ASC
            """
        ).fetchall()
        applied: dict[int, dict[str, Any]] = {}
        for r in rows:
            ver = int(r[0])
            raw_details = str(r[8] or "{}")
            try:
                parsed_details = json.loads(raw_details)
                if not isinstance(parsed_details, dict):
                    parsed_details = {}
            except (TypeError, ValueError):
                parsed_details = {}
            applied[ver] = {
                "version": ver,
                "version_tag": str(r[1] or f"V{ver:03d}"),
                "name": str(r[2] or ""),
                "description": str(r[3] or ""),
                "checksum": str(r[4] or ""),
                "applied_ts": float(r[5] or 0.0),
                "duration_ms": float(r[6] or 0.0),
                "app_schema_version": str(r[7] or ""),
                "details": _clean_json_obj(parsed_details),
            }
        return applied

    @classmethod
    def _has_existing_domain_data(cls, conn: sqlite3.Connection) -> bool:
        for probe_tbl in ("rank_snapshots", "planet_snapshots", "action_history", "spy_targets", "game_messages"):
            if cls._table_exists(conn, probe_tbl):
                try:
                    cnt_row = conn.execute(f"SELECT COUNT(*) FROM {_quote_ident(probe_tbl)}").fetchone()
                    if cnt_row is not None and int(cnt_row[0] or 0) > 0:
                        return True
                except sqlite3.DatabaseError:
                    continue
        return False

    def _detect_current_version(
        self,
        conn: sqlite3.Connection,
        applied_rows: Mapping[int, Mapping[str, Any]],
        meta_map: Mapping[str, str],
    ) -> int:
        ledger_max = max(applied_rows.keys(), default=0)
        user_ver = self._read_user_version(conn)
        raw_meta = str(meta_map.get("schema_version", "")).strip()
        meta_ver = int(raw_meta) if raw_meta.isdigit() else 0
        if ledger_max > 0:
            return max(ledger_max, user_ver)
        return max(user_ver, meta_ver)

    def _compute_checksum_drift(
        self,
        applied_rows: Mapping[int, Mapping[str, Any]],
    ) -> list[dict[str, Any]]:
        drift: list[dict[str, Any]] = []
        by_ver = {m.version: m for m in self.migrations}
        for ver, rec in sorted(applied_rows.items()):
            spec = by_ver.get(ver)
            if spec is None:
                drift.append(
                    {
                        "version": ver,
                        "name": str(rec.get("name", "")),
                        "reason": "applied_version_unknown_to_code",
                        "recorded_checksum": str(rec.get("checksum", "")),
                        "expected_checksum": None,
                    }
                )
            elif str(rec.get("checksum", "")) != spec.checksum:
                drift.append(
                    {
                        "version": ver,
                        "name": spec.name,
                        "reason": "checksum_mismatch",
                        "recorded_checksum": str(rec.get("checksum", "")),
                        "expected_checksum": spec.checksum,
                    }
                )
        return drift

    def inspect(
        self,
        conn: sqlite3.Connection,
        *,
        db_path: Optional[Path | str] = None,
        include_events: bool = True,
        event_limit: int = 50,
    ) -> dict[str, Any]:
        """Read-only inspection of current schema version, applied/pending migrations, and drift."""
        applied_rows = self._read_applied_rows(conn)
        meta_map = self._read_meta_map(conn)
        user_ver = self._read_user_version(conn)
        detected_ver = self._detect_current_version(conn, applied_rows, meta_map)
        target_ver = self.latest_version
        newer_than_code = detected_ver > target_ver

        pending_specs = [
            {
                "version": m.version,
                "version_tag": m.version_tag,
                "name": m.name,
                "description": m.description,
                "checksum": m.checksum,
                "statement_count": len(split_sql_statements(m.sql)),
                "column_addition_count": len(m.column_additions),
            }
            for m in self.migrations
            if m.version not in applied_rows and not newer_than_code
        ]
        drift = self._compute_checksum_drift(applied_rows)

        events: list[dict[str, Any]] = []
        if include_events and self._table_exists(conn, "schema_migration_events"):
            lim = max(1, min(500, int(event_limit)))
            ev_rows = conn.execute(
                """
                SELECT id, ts, event_type, from_version, to_version,
                       version, name, checksum, backup_path, details_json
                FROM schema_migration_events
                ORDER BY id DESC
                LIMIT ?
                """,
                (lim,),
            ).fetchall()
            for r in ev_rows:
                try:
                    det = json.loads(str(r[9] or "{}"))
                    if not isinstance(det, dict):
                        det = {}
                except (TypeError, ValueError):
                    det = {}
                events.append(
                    {
                        "id": int(r[0]),
                        "ts": float(r[1] or 0.0),
                        "event_type": str(r[2] or ""),
                        "from_version": int(r[3] or 0),
                        "to_version": int(r[4] or 0),
                        "version": int(r[5]) if r[5] is not None else None,
                        "name": str(r[6] or ""),
                        "checksum": str(r[7] or ""),
                        "backup_path": str(redact_sensitive(str(r[8] or ""))),
                        "details": _clean_json_obj(det),
                    }
                )

        up_to_date = (
            len(pending_specs) == 0
            and not newer_than_code
            and detected_ver == target_ver
            and len(applied_rows) >= len(self.migrations)
        )
        return _clean_json_obj(
            {
                "schema_family": self.schema_family,
                "db_path": ":memory:" if db_path is None else str(db_path),
                "version": detected_ver,
                "version_tag": f"V{detected_ver:03d}" if detected_ver > 0 else "V000",
                "target_version": target_ver,
                "target_version_tag": f"V{target_ver:03d}",
                "user_version": user_ver,
                "meta_schema_version": meta_map.get("schema_version"),
                "schema_checksum": meta_map.get("schema_checksum") or (self.head_checksum if up_to_date else ""),
                "expected_head_checksum": self.head_checksum,
                "up_to_date": up_to_date,
                "newer_than_code": newer_than_code,
                "applied_count": len(applied_rows),
                "pending_count": len(pending_specs),
                "applied_migrations": list(applied_rows.values()),
                "pending_migrations": pending_specs,
                "checksum_drift": drift,
                "events": events,
            }
        )

    @staticmethod
    def _ensure_ledger_tables(conn: sqlite3.Connection) -> None:
        if not getattr(conn, "in_transaction", False):
            conn.executescript(SCHEMA_LEDGER_DDL)
        else:
            for stmt in split_sql_statements(SCHEMA_LEDGER_DDL):
                conn.execute(stmt)

    @staticmethod
    def _record_event(
        conn: sqlite3.Connection,
        *,
        ts: float,
        event_type: str,
        from_version: int = 0,
        to_version: int = 0,
        version: Optional[int] = None,
        name: str = "",
        checksum: str = "",
        backup_path: str = "",
        details: Optional[Mapping[str, Any]] = None,
    ) -> None:
        conn.execute(
            """
            INSERT INTO schema_migration_events(
                ts, event_type, from_version, to_version,
                version, name, checksum, backup_path, details_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                round(float(ts), 3),
                str(event_type)[:64],
                int(from_version),
                int(to_version),
                int(version) if version is not None else None,
                str(name)[:128],
                str(checksum)[:128],
                str(redact_sensitive(str(backup_path)))[:512],
                _safe_json_dumps(details or {}),
            ),
        )

    @staticmethod
    def _sync_meta_version_markers(
        conn: sqlite3.Connection,
        *,
        effective_version: int,
        head_checksum: str,
        now_ts: float,
    ) -> None:
        ver_str = str(int(effective_version))
        tag_str = f"V{int(effective_version):03d}"
        ts_rounded = round(float(now_ts), 3)
        for k, v in (
            ("schema_version", ver_str),
            ("schema_version_tag", tag_str),
            ("schema_checksum", str(head_checksum)),
        ):
            conn.execute(
                """
                INSERT INTO meta(key, value, updated_ts) VALUES(?, ?, ?)
                ON CONFLICT(key) DO UPDATE SET
                    value = excluded.value,
                    updated_ts = excluded.updated_ts
                """,
                (k, v, ts_rounded),
            )
        conn.execute(f"PRAGMA user_version = {max(0, int(effective_version))};")

    def migrate(
        self,
        conn: sqlite3.Connection,
        *,
        db_path: Optional[Path] = None,
        is_memory: bool = False,
        target_version: Optional[int] = None,
        dry_run: bool = False,
        backup: bool = False,
        auto_backup_default_db: bool = True,
        backup_dir: Optional[Path] = None,
        now_ts: Optional[float] = None,
    ) -> dict[str, Any]:
        """Apply all pending forward migrations up to `target_version` safely."""
        ts_now = float(now_ts) if now_ts is not None else time.time()
        eff_target = self.latest_version if target_version is None else int(target_version)
        if eff_target < 1 or eff_target > self.latest_version:
            raise ValueError(
                f"target_version={eff_target} out of supported range 1..{self.latest_version}"
            )

        applied_before = self._read_applied_rows(conn)
        meta_before = self._read_meta_map(conn)
        from_version = self._detect_current_version(conn, applied_before, meta_before)

        if from_version > self.latest_version:
            status = self.inspect(conn, db_path=db_path, include_events=False)
            status["dry_run"] = bool(dry_run)
            status["executed_migrations"] = []
            status["backup_path"] = None
            return status

        to_apply = [
            m
            for m in self.migrations
            if m.version <= eff_target and m.version not in applied_before
        ]

        if dry_run:
            preview = self.inspect(conn, db_path=db_path, include_events=False)
            preview["dry_run"] = True
            preview["from_version"] = from_version
            preview["target_version"] = eff_target
            preview["would_apply"] = [
                {
                    "version": m.version,
                    "version_tag": m.version_tag,
                    "name": m.name,
                    "description": m.description,
                    "checksum": m.checksum,
                }
                for m in to_apply
            ]
            preview["backup_path"] = None
            return preview

        backup_created_path: Optional[Path] = None
        should_auto_backup = (
            bool(auto_backup_default_db)
            and not is_memory
            and db_path is not None
            and len(to_apply) > 0
            and Path(db_path).exists()
            and Path(db_path).resolve() == DEFAULT_GAME_DB_PATH.resolve()
            and self._has_existing_domain_data(conn)
        )
        if (backup or should_auto_backup) and not is_memory and db_path is not None and Path(db_path).exists():
            backup_created_path = create_sqlite_backup(
                conn,
                Path(db_path),
                from_version=from_version,
                to_version=eff_target,
                label="pre_migrate" if should_auto_backup and not backup else "manual_migrate",
                backup_dir=backup_dir,
            )

        self._ensure_ledger_tables(conn)
        if backup_created_path is not None:
            self._record_event(
                conn,
                ts=ts_now,
                event_type="backup_created",
                from_version=from_version,
                to_version=eff_target,
                backup_path=str(backup_created_path),
                details={"reason": "pre_migration_upgrade"},
            )

        executed_records: list[dict[str, Any]] = []
        for mig in to_apply:
            sp_name = f"_si_mig_v{mig.version:03d}"
            t0 = time.perf_counter()
            added_columns: list[str] = []
            hook_result: dict[str, Any] = {}
            conn.execute(f"SAVEPOINT {sp_name};")
            try:
                for stmt in split_sql_statements(mig.sql):
                    conn.execute(stmt)

                cols_cache: dict[str, set[str]] = {}
                for tbl_name, col_name, col_ddl in mig.column_additions:
                    q_tbl = _quote_ident(tbl_name)
                    q_col = _quote_ident(col_name)
                    if tbl_name not in cols_cache:
                        cols_cache[tbl_name] = {
                            str(r[1])
                            for r in conn.execute(f"PRAGMA table_info({q_tbl});").fetchall()
                        }
                    if col_name not in cols_cache[tbl_name]:
                        conn.execute(f"ALTER TABLE {q_tbl} ADD COLUMN {q_col} {col_ddl}")
                        cols_cache[tbl_name].add(col_name)
                        added_columns.append(f"{tbl_name}.{col_name}")

                if mig.data_hook_name and mig.data_hook_name in self.data_hooks:
                    res = self.data_hooks[mig.data_hook_name](conn, ts_now)
                    if isinstance(res, Mapping):
                        hook_result = dict(res)

                dur_ms = round((time.perf_counter() - t0) * 1000.0, 3)
                mig_ts = time.time() if now_ts is None else ts_now
                details_obj = {
                    "added_columns": added_columns,
                    "statement_count": len(split_sql_statements(mig.sql)),
                    **(hook_result if hook_result else {}),
                }
                conn.execute(
                    """
                    INSERT INTO schema_migrations(
                        version, version_tag, name, description, checksum,
                        applied_ts, duration_ms, app_schema_version, details_json
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        mig.version,
                        mig.version_tag,
                        mig.name,
                        mig.description,
                        mig.checksum,
                        round(float(mig_ts), 3),
                        dur_ms,
                        str(eff_target),
                        _safe_json_dumps(details_obj),
                    ),
                )
                self._record_event(
                    conn,
                    ts=mig_ts,
                    event_type="migration_applied",
                    from_version=from_version,
                    to_version=mig.version,
                    version=mig.version,
                    name=mig.name,
                    checksum=mig.checksum,
                    backup_path=str(backup_created_path or ""),
                    details=details_obj,
                )
                conn.execute(f"RELEASE SAVEPOINT {sp_name};")
                executed_records.append(
                    {
                        "version": mig.version,
                        "version_tag": mig.version_tag,
                        "name": mig.name,
                        "checksum": mig.checksum,
                        "duration_ms": dur_ms,
                        "added_columns": added_columns,
                    }
                )
            except Exception:
                try:
                    conn.execute(f"ROLLBACK TO SAVEPOINT {sp_name};")
                    conn.execute(f"RELEASE SAVEPOINT {sp_name};")
                except sqlite3.DatabaseError:
                    pass
                raise

        applied_after = self._read_applied_rows(conn)
        effective_ver = max(applied_after.keys(), default=eff_target)
        applied_specs = [m for m in self.migrations if m.version <= effective_ver]
        eff_checksum = compute_catalog_head_checksum(applied_specs)

        # Sync `meta` markers and `PRAGMA user_version` whenever `meta` exists
        # (including on brand-new DBs and when any marker is missing/outdated).
        meta_now = self._read_meta_map(conn)
        user_ver_now = self._read_user_version(conn)
        if (
            executed_records
            or meta_now.get("schema_version") != str(effective_ver)
            or meta_now.get("schema_version_tag") != f"V{effective_ver:03d}"
            or meta_now.get("schema_checksum") != eff_checksum
            or user_ver_now != effective_ver
        ):
            self._sync_meta_version_markers(
                conn,
                effective_version=effective_ver,
                head_checksum=eff_checksum,
                now_ts=ts_now,
            )

        conn.commit()
        out = self.inspect(conn, db_path=db_path, include_events=True, event_limit=25)
        out["dry_run"] = False
        out["from_version"] = from_version
        out["executed_migrations"] = executed_records
        out["backup_path"] = str(backup_created_path) if backup_created_path is not None else None
        return _clean_json_obj(out)

    def verify(
        self,
        conn: sqlite3.Connection,
        *,
        db_path: Optional[Path | str] = None,
        full_integrity: bool = False,
    ) -> dict[str, Any]:
        """Verify SQLite B-tree integrity, required tables/columns, version sync, and SHA-256 checksums."""
        pragma_name = "integrity_check" if full_integrity else "quick_check"
        chk_rows = conn.execute(f"PRAGMA {pragma_name};").fetchall()
        chk_messages = [str(r[0]) for r in chk_rows]
        sqlite_ok = chk_messages == ["ok"]

        existing_tables = {
            str(r[0])
            for r in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'").fetchall()
        }
        missing_tables = [t for t in self.required_tables if t not in existing_tables]

        missing_columns: list[str] = []
        for mig in self.migrations:
            for tbl_name, col_name, _ddl in mig.column_additions:
                if tbl_name not in existing_tables:
                    continue
                cols = {
                    str(r[1])
                    for r in conn.execute(f"PRAGMA table_info({_quote_ident(tbl_name)});").fetchall()
                }
                if col_name not in cols:
                    missing_columns.append(f"{tbl_name}.{col_name}")

        status = self.inspect(conn, db_path=db_path, include_events=False)
        version_synced = (
            status["user_version"] == status["version"]
            and str(status["meta_schema_version"] or "") == str(status["version"])
        )
        ok = (
            sqlite_ok
            and not missing_tables
            and not missing_columns
            and len(status["checksum_drift"]) == 0
            and version_synced
            and bool(status["up_to_date"])
        )
        return _clean_json_obj(
            {
                "ok": ok,
                "sqlite_check_mode": pragma_name,
                "sqlite_integrity_ok": sqlite_ok,
                "sqlite_messages": chk_messages[:20],
                "missing_tables": missing_tables,
                "missing_columns": missing_columns,
                "version_synced": version_synced,
                "version": status["version"],
                "version_tag": status["version_tag"],
                "target_version": status["target_version"],
                "up_to_date": status["up_to_date"],
                "checksum_drift": status["checksum_drift"],
                "schema_checksum": status["schema_checksum"],
            }
        )


DEFAULT_GAME_MIGRATION_RUNNER = MigrationRunner()


# ---------------------------------------------------------------------------
# Multi-Database & JSON Sidecar Store Versioning Registry
# ---------------------------------------------------------------------------

def inspect_json_store(path: Path, *, store_kind: str) -> dict[str, Any]:
    """Inspect schema version of a JSON sidecar store (`attack_history`, `rank_history`, `snapshot`)."""
    p = Path(path)
    if not p.exists() or p.is_dir():
        return _clean_json_obj(
            {
                "store_kind": store_kind,
                "path": str(p),
                "exists": False,
                "format": "json",
                "schema_version": None,
                "target_schema_version": ATTACK_HISTORY_SCHEMA_VERSION if store_kind == "attack_history" else 1,
                "up_to_date": True,
                "size_bytes": 0,
            }
        )
    size_bytes = p.stat().st_size
    try:
        parsed = json.loads(p.read_text(encoding="utf-8"))
    except Exception as exc:
        return _clean_json_obj(
            {
                "store_kind": store_kind,
                "path": str(p),
                "exists": True,
                "format": "json",
                "schema_version": None,
                "target_schema_version": ATTACK_HISTORY_SCHEMA_VERSION if store_kind == "attack_history" else 1,
                "up_to_date": False,
                "size_bytes": size_bytes,
                "error": str(exc),
            }
        )

    if store_kind == "attack_history" and isinstance(parsed, dict):
        sv = int(parsed.get("schema_version") or parsed.get("version") or 1)
        return _clean_json_obj(
            {
                "store_kind": store_kind,
                "path": str(p),
                "exists": True,
                "format": "json",
                "schema_version": sv,
                "target_schema_version": ATTACK_HISTORY_SCHEMA_VERSION,
                "db_schema_version": parsed.get("db_schema_version"),
                "up_to_date": sv >= ATTACK_HISTORY_SCHEMA_VERSION,
                "size_bytes": size_bytes,
            }
        )

    inferred_ver = int(parsed.get("schema_version", 1)) if isinstance(parsed, dict) else 1
    return _clean_json_obj(
        {
            "store_kind": store_kind,
            "path": str(p),
            "exists": True,
            "format": "json",
            "schema_version": inferred_ver,
            "target_schema_version": 1,
            "up_to_date": True,
            "size_bytes": size_bytes,
        }
    )


def migrate_attack_history_json(
    path: Path = DEFAULT_ATTACK_HISTORY_JSON_PATH,
    *,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Non-destructively upgrade `attack_history.json` envelope to `ATTACK_HISTORY_SCHEMA_VERSION`."""
    p = Path(path)
    info = inspect_json_store(p, store_kind="attack_history")
    if not info["exists"] or info.get("error") or info["up_to_date"]:
        info["migrated"] = False
        info["dry_run"] = bool(dry_run)
        return info

    raw_obj = json.loads(p.read_text(encoding="utf-8"))
    if not isinstance(raw_obj, dict):
        info["migrated"] = False
        info["dry_run"] = bool(dry_run)
        return info

    prev_ver = int(raw_obj.get("schema_version") or raw_obj.get("version") or 1)
    if dry_run:
        info["migrated"] = False
        info["dry_run"] = True
        info["would_upgrade_from"] = prev_ver
        return info

    upgraded = dict(raw_obj)
    upgraded.setdefault("version", 1)
    upgraded["schema_version"] = ATTACK_HISTORY_SCHEMA_VERSION
    upgraded.setdefault("db_schema_version", LATEST_SCHEMA_VERSION)
    upgraded.setdefault("overall", {})
    upgraded.setdefault("per_target", {})
    upgraded.setdefault("history", [])
    upgraded.setdefault("bash_blocks", [])
    upgraded.setdefault("blocked_targets", {})
    upgraded.setdefault("telemetry_summary", {})
    upgraded["migrated_at"] = round(time.time(), 3)

    tmp_fd, tmp_raw = tempfile.mkstemp(prefix=f".{p.stem}.", suffix=".tmp", dir=str(p.parent))
    os.close(tmp_fd)
    tmp_path = Path(tmp_raw)
    try:
        with tmp_path.open("w", encoding="utf-8") as fh:
            json.dump(_clean_json_obj(upgraded), fh, indent=2, sort_keys=True, allow_nan=False)
            fh.write("\n")
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp_path, p)
    except Exception:
        try:
            tmp_path.unlink(missing_ok=True)
        except OSError:
            pass
        raise

    after = inspect_json_store(p, store_kind="attack_history")
    after["migrated"] = True
    after["from_schema_version"] = prev_ver
    after["dry_run"] = False
    return after


def import_legacy_attack_history_json(
    conn: sqlite3.Connection,
    json_path: Path,
    *,
    now_ts: Optional[float] = None,
    remove_after_import: bool = False,
) -> dict[str, Any]:
    """Non-destructively absorb a legacy `attack_history.json` file into SQLite tables (`raid_history`, `bash_blocks`)."""
    p = Path(json_path)
    ts_ref = float(now_ts) if now_ts is not None else time.time()
    if not p.exists() or not p.is_file():
        return _clean_json_obj(
            {
                "exists": False,
                "imported_raids": 0,
                "imported_bash_blocks": 0,
                "removed_json": False,
                "path": str(p),
            }
        )

    try:
        raw_obj = json.loads(p.read_text(encoding="utf-8"))
    except Exception as exc:
        return _clean_json_obj(
            {
                "exists": True,
                "imported_raids": 0,
                "imported_bash_blocks": 0,
                "removed_json": False,
                "path": str(p),
                "error": str(exc),
            }
        )

    hist_rows = raw_obj if isinstance(raw_obj, list) else (raw_obj.get("history") if isinstance(raw_obj, Mapping) else [])
    bash_rows = raw_obj.get("bash_blocks") if isinstance(raw_obj, Mapping) else []

    imported_raids = 0
    if isinstance(hist_rows, list) and MigrationRunner._table_exists(conn, "raid_history"):
        for r in hist_rows:
            if not isinstance(r, Mapping):
                continue
            coords = str(r.get("target_coords") or r.get("coords") or "").strip()
            if not coords:
                continue
            try:
                atk_ts = round(float(r.get("attacked_ts") or r.get("ts") or 0.0), 3)
            except (TypeError, ValueError):
                atk_ts = 0.0
            outcome = str(redact_sensitive(str(r.get("outcome") or "")))[:256]
            is_ex = 1 if bool(r.get("is_exempt", False)) else 0
            loot_obj = r.get("loot") if isinstance(r.get("loot"), Mapping) else {}
            clean_loot = {
                rk: max(0, int(loot_obj.get(rk, 0) or 0))
                for rk in ("pig_iron", "metal", "kryptonite", "spice")
            }
            dup = conn.execute(
                """
                SELECT 1 FROM raid_history
                WHERE target_coords = ? AND ABS(attacked_ts - ?) < 0.002 AND outcome = ?
                LIMIT 1
                """,
                (coords, atk_ts, outcome),
            ).fetchone()
            if dup is None:
                conn.execute(
                    """
                    INSERT INTO raid_history(target_coords, attacked_ts, is_exempt, outcome, loot_json)
                    VALUES (?, ?, ?, ?, ?)
                    """,
                    (coords, atk_ts, is_ex, outcome, _safe_json_dumps(clean_loot)),
                )
                imported_raids += 1

    imported_blocks = 0
    if isinstance(bash_rows, list) and MigrationRunner._table_exists(conn, "bash_blocks"):
        for b in bash_rows:
            if not isinstance(b, Mapping):
                continue
            coords = str(b.get("target_coords") or b.get("coords") or "").strip()
            if not coords:
                continue
            try:
                blk_ts = round(float(b.get("blocked_ts") or b.get("ts") or 0.0), 3)
                until_ts = round(float(b.get("blocked_until_ts") or 0.0), 3)
                cd_sec = round(float(b.get("cooldown_seconds") or max(0.0, until_ts - blk_ts)), 3)
            except (TypeError, ValueError):
                continue
            is_bash = 1 if bool(b.get("is_bash", True)) else 0
            reason = str(redact_sensitive(str(b.get("reason") or "")))[:256]
            dup_b = conn.execute(
                """
                SELECT 1 FROM bash_blocks
                WHERE target_coords = ? AND ABS(blocked_ts - ?) < 0.002
                LIMIT 1
                """,
                (coords, blk_ts),
            ).fetchone()
            if dup_b is None:
                conn.execute(
                    """
                    INSERT INTO bash_blocks(
                        target_coords, blocked_ts, blocked_until_ts, cooldown_seconds, is_bash, reason
                    ) VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (coords, blk_ts, until_ts, cd_sec, is_bash, reason),
                )
                imported_blocks += 1

    if MigrationRunner._table_exists(conn, "meta"):
        meta_val = _safe_json_dumps(
            {
                "source_path": str(p),
                "imported_raids": imported_raids,
                "imported_bash_blocks": imported_blocks,
                "imported_ts": round(ts_ref, 3),
            }
        )
        conn.execute(
            """
            INSERT INTO meta(key, value, updated_ts) VALUES(?, ?, ?)
            ON CONFLICT(key) DO UPDATE SET
                value = excluded.value,
                updated_ts = excluded.updated_ts
            """,
            ("legacy_attack_history_import", meta_val, round(ts_ref, 3)),
        )

    removed_json = False
    if remove_after_import:
        conn.commit()
        try:
            p.unlink(missing_ok=True)
            removed_json = not p.exists()
            if removed_json and p.parent.exists() and p.parent.name == "data" and not any(p.parent.iterdir()):
                p.parent.rmdir()
        except OSError:
            removed_json = False

    return _clean_json_obj(
        {
            "exists": True,
            "imported_raids": imported_raids,
            "imported_bash_blocks": imported_blocks,
            "removed_json": removed_json,
            "path": str(p),
        }
    )


def import_legacy_rank_history_json(
    conn: sqlite3.Connection,
    json_path: Path,
    *,
    now_ts: Optional[float] = None,
    remove_after_import: bool = False,
) -> dict[str, Any]:
    """Non-destructively absorb a legacy `rank_history.json` file into `rank_snapshots`."""
    p = Path(json_path)
    ts_ref = float(now_ts) if now_ts is not None else time.time()
    if not p.exists() or not p.is_file():
        return _clean_json_obj(
            {
                "exists": False,
                "imported_samples": 0,
                "removed_json": False,
                "path": str(p),
            }
        )

    try:
        raw_obj = json.loads(p.read_text(encoding="utf-8"))
    except Exception as exc:
        return _clean_json_obj(
            {
                "exists": True,
                "imported_samples": 0,
                "removed_json": False,
                "path": str(p),
                "error": str(exc),
            }
        )

    samples = (
        raw_obj
        if isinstance(raw_obj, list)
        else (
            raw_obj.get("samples") or raw_obj.get("history") or []
            if isinstance(raw_obj, Mapping)
            else []
        )
    )
    imported_samples = 0
    if isinstance(samples, list) and MigrationRunner._table_exists(conn, "rank_snapshots"):
        for item in samples:
            if not isinstance(item, Mapping):
                continue
            try:
                sample_ts = round(float(item.get("ts") or 0.0), 3)
            except (TypeError, ValueError):
                sample_ts = 0.0
            if sample_ts <= 0.0:
                continue
            dup = conn.execute(
                """
                SELECT 1 FROM rank_snapshots
                WHERE ABS(ts - ?) < 0.002
                LIMIT 1
                """,
                (sample_ts,),
            ).fetchone()
            if dup is not None:
                continue
            try:
                uni_id = int(item.get("universe_id") or 0)
            except (TypeError, ValueError):
                uni_id = 0
            nick = str(redact_sensitive(str(item.get("nickname") or "")))[:120]
            srv_pts_raw = item.get("server_points")
            try:
                srv_pts = int(srv_pts_raw) if srv_pts_raw is not None else None
            except (TypeError, ValueError):
                srv_pts = None
            srv_rk_raw = item.get("server_rank")
            try:
                srv_rk = int(srv_rk_raw) if srv_rk_raw is not None else None
            except (TypeError, ValueError):
                srv_rk = None
            try:
                est_pts = float(item.get("estimated_points") or 0.0)
                if not math.isfinite(est_pts):
                    est_pts = 0.0
            except (TypeError, ValueError):
                est_pts = 0.0
            try:
                pph = float(item.get("pts_per_hr") or 0.0)
                if not math.isfinite(pph):
                    pph = 0.0
            except (TypeError, ValueError):
                pph = 0.0
            try:
                sp_val = float(item.get("spice_value") or 0.0)
                if not math.isfinite(sp_val):
                    sp_val = 0.0
            except (TypeError, ValueError):
                sp_val = 0.0
            try:
                p_cnt = max(1, int(item.get("planets") or 1))
            except (TypeError, ValueError):
                p_cnt = 1
            conn.execute(
                """
                INSERT INTO rank_snapshots(
                    ts, universe_id, nickname, server_points, server_rank,
                    estimated_points, pts_per_hr, spice_value, planets
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (sample_ts, uni_id, nick, srv_pts, srv_rk, est_pts, pph, sp_val, p_cnt),
            )
            imported_samples += 1

    if MigrationRunner._table_exists(conn, "meta"):
        meta_val = _safe_json_dumps(
            {
                "source_path": str(p),
                "imported_samples": imported_samples,
                "imported_ts": round(ts_ref, 3),
            }
        )
        conn.execute(
            """
            INSERT INTO meta(key, value, updated_ts) VALUES(?, ?, ?)
            ON CONFLICT(key) DO UPDATE SET
                value = excluded.value,
                updated_ts = excluded.updated_ts
            """,
            ("legacy_rank_history_import", meta_val, round(ts_ref, 3)),
        )

    removed_json = False
    if remove_after_import:
        conn.commit()
        try:
            p.unlink(missing_ok=True)
            removed_json = not p.exists()
        except OSError:
            removed_json = False

    return _clean_json_obj(
        {
            "exists": True,
            "imported_samples": imported_samples,
            "removed_json": removed_json,
            "path": str(p),
        }
    )


def import_legacy_snapshot_json(
    conn: sqlite3.Connection,
    json_path: Path,
    *,
    now_ts: Optional[float] = None,
    remove_after_import: bool = False,
) -> dict[str, Any]:
    """Non-destructively absorb a legacy `snapshot.json` / `state.json` file into `state_snapshots` and `planet_snapshots`."""
    p = Path(json_path)
    ts_ref = float(now_ts) if now_ts is not None else time.time()
    if not p.exists() or not p.is_file():
        return _clean_json_obj(
            {
                "exists": False,
                "imported_snapshots": 0,
                "imported_planets": 0,
                "removed_json": False,
                "path": str(p),
            }
        )

    try:
        raw_obj = json.loads(p.read_text(encoding="utf-8"))
    except Exception as exc:
        return _clean_json_obj(
            {
                "exists": True,
                "imported_snapshots": 0,
                "imported_planets": 0,
                "removed_json": False,
                "path": str(p),
                "error": str(exc),
            }
        )

    if not isinstance(raw_obj, Mapping):
        return _clean_json_obj(
            {
                "exists": True,
                "imported_snapshots": 0,
                "imported_planets": 0,
                "removed_json": False,
                "path": str(p),
                "error": "Expected JSON object in snapshot file",
            }
        )

    state_raw = raw_obj.get("state") if isinstance(raw_obj.get("state"), Mapping) else raw_obj
    plan_raw = raw_obj.get("plan") if isinstance(raw_obj.get("plan"), list) else []
    clean_state = _clean_json_obj(dict(state_raw))
    clean_plan = _clean_json_obj(list(plan_raw))

    try:
        snap_ts = round(float(clean_state.get("timestamp") or 0.0), 3)
    except (TypeError, ValueError):
        snap_ts = 0.0
    if snap_ts <= 0.0:
        try:
            snap_ts = round(float(p.stat().st_mtime), 3)
        except OSError:
            snap_ts = round(ts_ref, 3)

    planets_list = clean_state.get("planets") if isinstance(clean_state.get("planets"), list) else []
    imported_snapshots = 0
    if MigrationRunner._table_exists(conn, "state_snapshots"):
        dup_s = conn.execute(
            """
            SELECT 1 FROM state_snapshots
            WHERE ABS(ts - ?) < 0.002 AND source = ?
            LIMIT 1
            """,
            (snap_ts, "legacy_snapshot_json"),
        ).fetchone()
        if dup_s is None:
            try:
                uni_id = int(clean_state.get("universe_id") or 0)
            except (TypeError, ValueError):
                uni_id = 0
            try:
                usr_id = int(clean_state.get("user_id") or 0)
            except (TypeError, ValueError):
                usr_id = 0
            nick = str(redact_sensitive(str(clean_state.get("nickname") or "")))[:120]
            try:
                act_pid = int(clean_state.get("active_planet_id") or 0)
            except (TypeError, ValueError):
                act_pid = 0
            conn.execute(
                """
                INSERT INTO state_snapshots(
                    ts, source, universe_id, user_id, nickname, active_planet_id,
                    planet_count, planned_action_count, state_json, plan_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    snap_ts,
                    "legacy_snapshot_json",
                    uni_id,
                    usr_id,
                    nick,
                    act_pid,
                    len(planets_list),
                    len(clean_plan),
                    _safe_json_dumps(clean_state),
                    _safe_json_dumps(clean_plan),
                ),
            )
            imported_snapshots += 1

    imported_planets = 0
    if MigrationRunner._table_exists(conn, "planet_snapshots") and isinstance(planets_list, list):
        researches_obj = clean_state.get("researches") if isinstance(clean_state.get("researches"), Mapping) else {}
        res_json = _safe_json_dumps(researches_obj)
        try:
            uni_id_p = int(clean_state.get("universe_id") or 0)
        except (TypeError, ValueError):
            uni_id_p = 0
        for pl in planets_list:
            if not isinstance(pl, Mapping):
                continue
            try:
                pid_val = int(pl.get("planet_id") or 0)
            except (TypeError, ValueError):
                pid_val = 0
            if pid_val <= 0:
                continue
            dup_p = conn.execute(
                """
                SELECT 1 FROM planet_snapshots
                WHERE planet_id = ? AND ABS(ts - ?) < 0.002
                LIMIT 1
                """,
                (pid_val, snap_ts),
            ).fetchone()
            if dup_p is not None:
                continue
            res_map = pl.get("resources") if isinstance(pl.get("resources"), Mapping) else {}
            eng_map = pl.get("energy") if isinstance(pl.get("energy"), Mapping) else {}
            conn.execute(
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
                    snap_ts,
                    uni_id_p,
                    pid_val,
                    str(redact_sensitive(str(pl.get("coords") or "")))[:32],
                    str(redact_sensitive(str(pl.get("name") or "")))[:80],
                    str(pl.get("planet_type") or "normal")[:24],
                    int(pl.get("temperature") or 0),
                    int(pl.get("fields_used") or 0),
                    int(pl.get("fields_max") or 0),
                    int(res_map.get("pig_iron") or 0),
                    int(res_map.get("metal") or 0),
                    int(res_map.get("kryptonite") or 0),
                    int(res_map.get("spice") or 0),
                    int(eng_map.get("produced") or 0),
                    int(eng_map.get("used") or 0),
                    _safe_json_dumps(pl.get("buildings") if isinstance(pl.get("buildings"), Mapping) else {}),
                    _safe_json_dumps(pl.get("ships") if isinstance(pl.get("ships"), Mapping) else {}),
                    _safe_json_dumps(pl.get("defense") if isinstance(pl.get("defense"), Mapping) else {}),
                    _safe_json_dumps(pl.get("economy_rates") if isinstance(pl.get("economy_rates"), Mapping) else {}),
                    res_json,
                ),
            )
            imported_planets += 1

    if MigrationRunner._table_exists(conn, "meta"):
        meta_val = _safe_json_dumps(
            {
                "source_path": str(p),
                "imported_snapshots": imported_snapshots,
                "imported_planets": imported_planets,
                "imported_ts": round(ts_ref, 3),
            }
        )
        conn.execute(
            """
            INSERT INTO meta(key, value, updated_ts) VALUES(?, ?, ?)
            ON CONFLICT(key) DO UPDATE SET
                value = excluded.value,
                updated_ts = excluded.updated_ts
            """,
            ("legacy_snapshot_import", meta_val, round(ts_ref, 3)),
        )

    removed_json = False
    if remove_after_import:
        conn.commit()
        try:
            p.unlink(missing_ok=True)
            removed_json = not p.exists()
        except OSError:
            removed_json = False

    return _clean_json_obj(
        {
            "exists": True,
            "imported_snapshots": imported_snapshots,
            "imported_planets": imported_planets,
            "removed_json": removed_json,
            "path": str(p),
        }
    )


def import_legacy_config_yaml(
    conn: sqlite3.Connection,
    yaml_path: Path,
    *,
    now_ts: Optional[float] = None,
    remove_after_import: bool = False,
) -> dict[str, Any]:
    """Non-destructively absorb a legacy `config.yaml` file into `strategy_config_store`, `meta`, and `config_history`."""
    from dataclasses import asdict as _dc_asdict
    from spaceinvasion.core.config import load_strategy_config as _load_cfg

    p = Path(yaml_path)
    ts_ref = round(float(now_ts) if now_ts is not None else time.time(), 3)
    if not p.exists() or not p.is_file():
        return _clean_json_obj(
            {
                "exists": False,
                "imported_config": False,
                "removed_yaml": False,
                "path": str(p),
            }
        )

    try:
        cfg_obj = _load_cfg(p)
        cfg_dict = _clean_json_obj(_dc_asdict(cfg_obj))
        cfg_json = _safe_json_dumps(cfg_dict)
    except Exception as exc:
        return _clean_json_obj(
            {
                "exists": True,
                "imported_config": False,
                "removed_yaml": False,
                "path": str(p),
                "error": str(exc),
            }
        )

    imported_cfg = False
    if MigrationRunner._table_exists(conn, "strategy_config_store"):
        conn.execute(
            """
            INSERT INTO strategy_config_store(id, config_json, updated_ts, source)
            VALUES (1, ?, ?, 'legacy_config_yaml')
            ON CONFLICT(id) DO UPDATE SET
                config_json = excluded.config_json,
                updated_ts = excluded.updated_ts,
                source = excluded.source
            """,
            (cfg_json, ts_ref),
        )
        imported_cfg = True

    if MigrationRunner._table_exists(conn, "meta"):
        conn.execute(
            """
            INSERT INTO meta(key, value, updated_ts) VALUES ('strategy_config', ?, ?)
            ON CONFLICT(key) DO UPDATE SET
                value = excluded.value,
                updated_ts = excluded.updated_ts
            """,
            (cfg_json, ts_ref),
        )
        meta_audit = _safe_json_dumps(
            {
                "source_path": str(p),
                "imported_config": imported_cfg,
                "imported_ts": ts_ref,
            }
        )
        conn.execute(
            """
            INSERT INTO meta(key, value, updated_ts) VALUES ('legacy_config_yaml_import', ?, ?)
            ON CONFLICT(key) DO UPDATE SET
                value = excluded.value,
                updated_ts = excluded.updated_ts
            """,
            (meta_audit, ts_ref),
        )
        imported_cfg = True

    if MigrationRunner._table_exists(conn, "config_history"):
        last_row = conn.execute(
            "SELECT config_json FROM config_history ORDER BY id DESC LIMIT 1"
        ).fetchone()
        if last_row is None or last_row[0] != cfg_json:
            conn.execute(
                "INSERT INTO config_history(ts, config_json) VALUES (?, ?)",
                (ts_ref, cfg_json),
            )

    removed_yaml = False
    if remove_after_import:
        conn.commit()
        try:
            p.unlink(missing_ok=True)
            removed_yaml = not p.exists()
        except OSError:
            removed_yaml = False

    return _clean_json_obj(
        {
            "exists": True,
            "imported_config": imported_cfg,
            "removed_yaml": removed_yaml,
            "path": str(p),
        }
    )


class DatabaseMigrationRegistry:
    """Multi-database registry managing schema versioning across SQLite databases."""

    def __init__(self, root_dir: Path | str = Path(".")) -> None:
        self.root_dir = Path(root_dir)
        self._runners: dict[str, MigrationRunner] = {
            "game": DEFAULT_GAME_MIGRATION_RUNNER,
        }
        self._db_targets: dict[str, tuple[Path, str]] = {
            "primary": (self.root_dir / DEFAULT_GAME_DB_PATH, "game"),
        }

    def register_schema_family(self, family: str, runner: MigrationRunner) -> None:
        self._runners[str(family)] = runner

    def register_database(self, alias: str, db_path: Path | str, *, schema_family: str = "game") -> None:
        if schema_family not in self._runners:
            raise KeyError(f"Unknown schema_family {schema_family!r}")
        self._db_targets[str(alias)] = (Path(db_path), str(schema_family))

    def discover_sqlite_databases(self) -> dict[str, tuple[Path, str]]:
        targets = dict(self._db_targets)
        state_dir = self.root_dir / "state"
        if state_dir.exists() and state_dir.is_dir():
            known_resolved = {p.resolve() for p, _ in targets.values()}
            for cand in sorted(state_dir.glob("*.db")):
                if cand.is_file() and cand.resolve() not in known_resolved:
                    targets[cand.stem] = (cand, "game")
                    known_resolved.add(cand.resolve())
        return targets

    def status_all(self, *, include_events: bool = False) -> dict[str, Any]:
        databases: dict[str, Any] = {}
        for alias, (db_p, family) in self.discover_sqlite_databases().items():
            runner = self._runners[family]
            if not db_p.exists():
                databases[alias] = {
                    "alias": alias,
                    "schema_family": family,
                    "db_path": str(db_p),
                    "exists": False,
                    "version": 0,
                    "version_tag": "V000",
                    "target_version": runner.latest_version,
                    "target_version_tag": f"V{runner.latest_version:03d}",
                    "up_to_date": False,
                    "pending_count": len(runner.migrations),
                }
                continue
            with contextlib.closing(sqlite3.connect(f"file:{db_p}?mode=ro", uri=True, timeout=5.0)) as conn:
                st = runner.inspect(conn, db_path=db_p, include_events=include_events)
                st["alias"] = alias
                st["exists"] = True
                databases[alias] = st

        legacy_atk_path = self.root_dir / DEFAULT_ATTACK_HISTORY_JSON_PATH
        legacy_rank_path = self.root_dir / DEFAULT_RANK_HISTORY_JSON_PATH
        legacy_snap_path = self.root_dir / DEFAULT_SNAPSHOT_JSON_PATH
        legacy_cfg_path = self.root_dir / DEFAULT_CONFIG_YAML_PATH
        legacy_atk_retired = not legacy_atk_path.exists()
        legacy_rank_retired = not legacy_rank_path.exists()
        legacy_snap_retired = not legacy_snap_path.exists()
        legacy_cfg_retired = not legacy_cfg_path.exists()
        json_stores = {
            "attack_history_retired": {
                "store_kind": "attack_history",
                "path": str(legacy_atk_path),
                "exists": legacy_atk_path.exists(),
                "retired_to_sqlite": legacy_atk_retired,
                "up_to_date": legacy_atk_retired,
            },
            "rank_history_retired": {
                "store_kind": "rank_history",
                "path": str(legacy_rank_path),
                "exists": legacy_rank_path.exists(),
                "retired_to_sqlite": legacy_rank_retired,
                "up_to_date": legacy_rank_retired,
            },
            "snapshot_retired": {
                "store_kind": "snapshot",
                "path": str(legacy_snap_path),
                "exists": legacy_snap_path.exists(),
                "retired_to_sqlite": legacy_snap_retired,
                "up_to_date": legacy_snap_retired,
            },
            "config_yaml_retired": {
                "store_kind": "config_yaml",
                "path": str(legacy_cfg_path),
                "exists": legacy_cfg_path.exists(),
                "retired_to_sqlite": legacy_cfg_retired,
                "up_to_date": legacy_cfg_retired,
            },
        }
        all_ok = (
            all((not d["exists"]) or bool(d.get("up_to_date")) for d in databases.values())
            and legacy_atk_retired
            and legacy_rank_retired
            and legacy_snap_retired
            and legacy_cfg_retired
        )
        return _clean_json_obj(
            {
                "ok": all_ok,
                "latest_game_schema_version": LATEST_SCHEMA_VERSION,
                "databases": databases,
                "json_stores": json_stores,
            }
        )

    def migrate_all(
        self,
        *,
        dry_run: bool = False,
        backup: bool = False,
        target_version: Optional[int] = None,
        create_missing_primary: bool = False,
        remove_legacy_attack_json: bool = True,
        remove_legacy_rank_json: bool = True,
        remove_legacy_snapshot_json: bool = True,
        remove_legacy_config_yaml: bool = True,
    ) -> dict[str, Any]:
        db_results: dict[str, Any] = {}
        legacy_atk_path = self.root_dir / DEFAULT_ATTACK_HISTORY_JSON_PATH
        legacy_rank_path = self.root_dir / DEFAULT_RANK_HISTORY_JSON_PATH
        legacy_snap_path = self.root_dir / DEFAULT_SNAPSHOT_JSON_PATH
        legacy_cfg_path = self.root_dir / DEFAULT_CONFIG_YAML_PATH
        atk_import_res: dict[str, Any] = {
            "exists": legacy_atk_path.exists(),
            "imported_raids": 0,
            "imported_bash_blocks": 0,
            "removed_json": False,
            "dry_run": bool(dry_run),
        }
        rank_import_res: dict[str, Any] = {
            "exists": legacy_rank_path.exists(),
            "imported_samples": 0,
            "removed_json": False,
            "dry_run": bool(dry_run),
        }
        snap_import_res: dict[str, Any] = {
            "exists": legacy_snap_path.exists(),
            "imported_snapshots": 0,
            "imported_planets": 0,
            "removed_json": False,
            "dry_run": bool(dry_run),
        }
        cfg_import_res: dict[str, Any] = {
            "exists": legacy_cfg_path.exists(),
            "imported_config": False,
            "removed_yaml": False,
            "dry_run": bool(dry_run),
        }
        has_any_legacy_json = (
            legacy_atk_path.exists()
            or legacy_rank_path.exists()
            or legacy_snap_path.exists()
            or legacy_cfg_path.exists()
        )
        for alias, (db_p, family) in self.discover_sqlite_databases().items():
            if not db_p.exists() and not (alias == "primary" and (create_missing_primary or has_any_legacy_json)):
                continue
            db_p.parent.mkdir(parents=True, exist_ok=True)
            runner = self._runners[family]
            with contextlib.closing(sqlite3.connect(str(db_p), timeout=10.0)) as conn:
                conn.execute("PRAGMA journal_mode=WAL;")
                conn.execute("PRAGMA synchronous=NORMAL;")
                conn.execute("PRAGMA foreign_keys=ON;")
                db_results[alias] = runner.migrate(
                    conn,
                    db_path=db_p,
                    target_version=target_version,
                    dry_run=dry_run,
                    backup=backup,
                )
                if alias == "primary" and not dry_run:
                    if legacy_atk_path.exists():
                        atk_import_res = import_legacy_attack_history_json(
                            conn,
                            legacy_atk_path,
                            remove_after_import=remove_legacy_attack_json,
                        )
                    if legacy_rank_path.exists():
                        rank_import_res = import_legacy_rank_history_json(
                            conn,
                            legacy_rank_path,
                            remove_after_import=remove_legacy_rank_json,
                        )
                    if legacy_snap_path.exists():
                        snap_import_res = import_legacy_snapshot_json(
                            conn,
                            legacy_snap_path,
                            remove_after_import=remove_legacy_snapshot_json,
                        )
                    if legacy_cfg_path.exists():
                        cfg_import_res = import_legacy_config_yaml(
                            conn,
                            legacy_cfg_path,
                            remove_after_import=remove_legacy_config_yaml,
                        )
                    conn.commit()

        return _clean_json_obj(
            {
                "ok": True,
                "dry_run": bool(dry_run),
                "databases": db_results,
                "legacy_attack_json": atk_import_res,
                "legacy_rank_json": rank_import_res,
                "legacy_snapshot_json": snap_import_res,
                "legacy_config_yaml": cfg_import_res,
            }
        )


# ---------------------------------------------------------------------------
# CLI Entrypoint (`python -m spaceinvasion.migrations` / `scripts/db_migrate.py`)
# ---------------------------------------------------------------------------

def build_cli_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="SpaceInvasion Autoplayer — Database Versioning & Migration Manager",
    )
    parser.add_argument(
        "--root",
        type=Path,
        default=Path("."),
        help="Workspace root directory (default: current directory).",
    )
    parser.add_argument(
        "--db",
        type=Path,
        default=None,
        help="Explicit SQLite database file path (defaults to state/spaceinvasion.db or --all).",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Emit machine-readable JSON output.",
    )
    sub = parser.add_subparsers(dest="subcommand")

    p_status = sub.add_parser("status", help="Show version and pending migrations across databases.")
    p_status.add_argument("--events", action="store_true", help="Include recent migration events.")

    p_mig = sub.add_parser("migrate", help="Apply pending forward migrations.")
    p_mig.add_argument("--all", action="store_true", help="Migrate all discovered SQLite & JSON stores.")
    p_mig.add_argument("--target", type=int, default=None, help="Optional target schema version.")
    p_mig.add_argument("--dry-run", action="store_true", help="Preview pending migrations without writing.")
    p_mig.add_argument("--backup", action="store_true", help="Create online SQLite backup before migrating.")
    p_mig.add_argument("--create", action="store_true", help="Create primary DB if it does not exist yet.")

    p_ver = sub.add_parser("verify", help="Verify SQLite integrity, schema columns, and SHA-256 checksums.")
    p_ver.add_argument("--full-integrity", action="store_true", help="Run full PRAGMA integrity_check.")

    p_bkp = sub.add_parser("backup", help="Create an online WAL-safe snapshot in state/backups/.")
    p_bkp.add_argument("--label", default="manual", help="Label embedded in the backup filename.")

    p_hist = sub.add_parser("history", help="Show applied migrations and audit events.")
    p_hist.add_argument("--limit", type=int, default=50, help="Max audit events to display.")
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = build_cli_parser()
    args = parser.parse_args(argv)
    cmd = args.subcommand or "status"
    reg = DatabaseMigrationRegistry(args.root)
    explicit_db: Optional[Path] = args.db

    if cmd == "status":
        if explicit_db is not None:
            if not explicit_db.exists():
                payload: dict[str, Any] = {
                    "exists": False,
                    "db_path": str(explicit_db),
                    "version": 0,
                    "target_version": LATEST_SCHEMA_VERSION,
                    "up_to_date": False,
                }
            else:
                with contextlib.closing(sqlite3.connect(f"file:{explicit_db}?mode=ro", uri=True, timeout=5.0)) as conn:
                    payload = DEFAULT_GAME_MIGRATION_RUNNER.inspect(
                        conn,
                        db_path=explicit_db,
                        include_events=bool(getattr(args, "events", False)),
                    )
                    payload["exists"] = True
        else:
            payload = reg.status_all(include_events=bool(getattr(args, "events", False)))

        if args.json:
            print(_safe_json_dumps(payload, indent=2))
        else:
            if "databases" in payload:
                print(f"Latest Game DB Schema: V{LATEST_SCHEMA_VERSION:03d} (version {LATEST_SCHEMA_VERSION})")
                for alias, d in payload["databases"].items():
                    state_lbl = "UP-TO-DATE" if d.get("up_to_date") else ("MISSING" if not d.get("exists") else f"PENDING ({d.get('pending_count', 0)})")
                    print(f"  [SQLite:{alias}] {d['db_path']} -> {d.get('version_tag')} / {d.get('target_version_tag')} [{state_lbl}]")
                for jname, j in payload.get("json_stores", {}).items():
                    jlbl = (
                        "RETIRED-TO-SQLITE"
                        if j.get("retired_to_sqlite")
                        else ("UP-TO-DATE" if j.get("up_to_date") else "PENDING-IMPORT")
                    )
                    print(f"  [JSON:{jname}]   {j['path']} (exists={j.get('exists')}) [{jlbl}]")
            else:
                print(
                    f"{payload.get('db_path')}: {payload.get('version_tag', 'V000')} / "
                    f"V{LATEST_SCHEMA_VERSION:03d} (up_to_date={payload.get('up_to_date')})"
                )
        return 0

    if cmd == "migrate":
        if explicit_db is not None and not getattr(args, "all", False):
            explicit_db.parent.mkdir(parents=True, exist_ok=True)
            with contextlib.closing(sqlite3.connect(str(explicit_db), timeout=10.0)) as conn:
                conn.execute("PRAGMA journal_mode=WAL;")
                conn.execute("PRAGMA synchronous=NORMAL;")
                conn.execute("PRAGMA foreign_keys=ON;")
                res = DEFAULT_GAME_MIGRATION_RUNNER.migrate(
                    conn,
                    db_path=explicit_db,
                    target_version=args.target,
                    dry_run=bool(args.dry_run),
                    backup=bool(args.backup),
                )
        else:
            res = reg.migrate_all(
                dry_run=bool(args.dry_run),
                backup=bool(args.backup),
                target_version=args.target,
                create_missing_primary=bool(args.create),
            )
        if args.json:
            print(_safe_json_dumps(res, indent=2))
        else:
            print(_safe_json_dumps(res, indent=2))
        return 0

    target_db = explicit_db if explicit_db is not None else (Path(args.root) / DEFAULT_GAME_DB_PATH)
    if not target_db.exists():
        err_obj = {"ok": False, "error": f"Database file not found: {target_db}"}
        print(_safe_json_dumps(err_obj, indent=2), file=sys.stderr if not args.json else sys.stdout)
        return 1

    if cmd == "verify":
        with contextlib.closing(sqlite3.connect(f"file:{target_db}?mode=ro", uri=True, timeout=5.0)) as conn:
            ver_res = DEFAULT_GAME_MIGRATION_RUNNER.verify(
                conn,
                db_path=target_db,
                full_integrity=bool(args.full_integrity),
            )
        print(_safe_json_dumps(ver_res, indent=2))
        return 0 if ver_res.get("ok") else 2

    if cmd == "backup":
        with contextlib.closing(sqlite3.connect(str(target_db), timeout=10.0)) as conn:
            st = DEFAULT_GAME_MIGRATION_RUNNER.inspect(conn, db_path=target_db, include_events=False)
            bkp = create_sqlite_backup(
                conn,
                target_db,
                from_version=int(st["version"]),
                to_version=int(st["version"]),
                label=str(args.label),
            )
            DEFAULT_GAME_MIGRATION_RUNNER._ensure_ledger_tables(conn)
            DEFAULT_GAME_MIGRATION_RUNNER._record_event(
                conn,
                ts=time.time(),
                event_type="backup_created",
                from_version=int(st["version"]),
                to_version=int(st["version"]),
                backup_path=str(bkp),
                details={"label": str(args.label)},
            )
            conn.commit()
        bkp_out = {"ok": True, "db_path": str(target_db), "backup_path": str(bkp), "version": st["version"]}
        print(_safe_json_dumps(bkp_out, indent=2))
        return 0

    if cmd == "history":
        with contextlib.closing(sqlite3.connect(f"file:{target_db}?mode=ro", uri=True, timeout=5.0)) as conn:
            hist = DEFAULT_GAME_MIGRATION_RUNNER.inspect(
                conn,
                db_path=target_db,
                include_events=True,
                event_limit=int(args.limit),
            )
        print(_safe_json_dumps(hist, indent=2))
        return 0

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
