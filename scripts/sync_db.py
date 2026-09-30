#!/usr/bin/env python3
"""Synchronize SpaceInvasion Autoplayer databases between local SQLite (`state/spaceinvasion.db`) and Docker/Podman PostgreSQL (`./data/postgres`).

Usage examples:
  ./scripts/sync_db.py                                   # Auto-detects fresher store for hot state & syncs both DBs
  ./scripts/sync_db.py --direction sqlite-to-postgres    # Copy local SQLite -> Docker PostgreSQL
  ./scripts/sync_db.py --direction postgres-to-sqlite    # Copy Docker PostgreSQL -> local SQLite
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sys
import time
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))


def _maybe_reexec_venv() -> None:
    if os.environ.get("SPACEINVASION_NO_REEXEC") == "1":
        return
    if sys.prefix != sys.base_prefix:
        return
    venv_python = REPO_ROOT / ".venv" / "bin" / "python3"
    if venv_python.exists() and os.path.abspath(sys.executable) != os.path.abspath(venv_python):
        os.execv(str(venv_python), [str(venv_python), *sys.argv])


_maybe_reexec_venv()

from spaceinvasion.db.backend import (  # noqa: E402
    DEFAULT_SQLITE_REL_PATH,
    SYNCABLE_HISTORY_TABLES,
    build_default_postgres_url,
    ensure_postgres_container_ready,
    redact_db_url,
    sync_database_stores,
)
from spaceinvasion.db.storage import GameDatabase  # noqa: E402


def _latest_hot_ts(db: GameDatabase) -> float:
    best = 0.0
    with db._lock:
        for sql in (
            "SELECT updated_ts FROM latest_state_hot WHERE id = 1",
            "SELECT updated_ts FROM strategy_config_store WHERE id = 1",
            "SELECT MAX(ts) FROM state_snapshots",
        ):
            try:
                row = db._conn.execute(sql).fetchone()
                if row is not None and row[0] is not None:
                    best = max(best, float(row[0]))
            except Exception:
                pass
    return best


def _sync_history_only(source_db: GameDatabase, target_db: GameDatabase, *, batch_size: int = 1000) -> int:
    """Append any missing history rows from `source_db` into `target_db` without overwriting hot tables."""
    copied_total = 0
    with source_db._lock:
        src_tables = {
            str(r[0])
            for r in source_db._conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'").fetchall()
        }
    with target_db._lock:
        dst_tables = {
            str(r[0])
            for r in target_db._conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'").fetchall()
        }

    for tbl in SYNCABLE_HISTORY_TABLES:
        if tbl not in src_tables or tbl not in dst_tables:
            continue
        with source_db._lock:
            src_cols = [str(r[1]) for r in source_db._conn.execute(f"PRAGMA table_info({tbl})").fetchall()]
        with target_db._lock:
            dst_cols = {str(r[1]) for r in target_db._conn.execute(f"PRAGMA table_info({tbl})").fetchall()}
        common_cols = [c for c in src_cols if c in dst_cols]
        if not common_cols:
            continue
        col_list_sql = ", ".join(f'"{c}"' for c in common_cols)
        placeholders = ", ".join("?" for _ in common_cols)
        insert_sql = f'INSERT INTO "{tbl}" ({col_list_sql}) VALUES ({placeholders}) ON CONFLICT DO NOTHING'
        if "id" in common_cols:
            last_id = -1
            id_idx = common_cols.index("id")
            while True:
                with source_db._lock:
                    cur = source_db._conn.cursor()
                    cur.row_factory = None
                    rows = cur.execute(
                        f'SELECT {col_list_sql} FROM "{tbl}" WHERE "id" > ? ORDER BY "id" ASC LIMIT ?',
                        (last_id, batch_size),
                    ).fetchall()
                if not rows:
                    break
                chunk = [tuple(r) for r in rows]
                last_id = int(chunk[-1][id_idx])
                with target_db._lock:
                    target_db._conn.executemany(insert_sql, chunk)
                    target_db._conn.commit()
                copied_total += len(chunk)
        else:
            with source_db._lock:
                cur = source_db._conn.cursor()
                cur.row_factory = None
                rows = cur.execute(f'SELECT {col_list_sql} FROM "{tbl}"').fetchall()
            if rows:
                with target_db._lock:
                    for start in range(0, len(rows), batch_size):
                        chunk = [tuple(r) for r in rows[start : start + batch_size]]
                        target_db._conn.executemany(insert_sql, chunk)
                        copied_total += len(chunk)
                    target_db._conn.commit()

    pg_backend = getattr(target_db, "_pg_backend", None)
    if pg_backend is not None:
        pg_backend.sync_identity_sequences()
    return copied_total


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Sync SpaceInvasion Autoplayer data between local SQLite (./starter.sh) and Docker PostgreSQL (./data/postgres)."
    )
    parser.add_argument(
        "--direction",
        "-d",
        choices=("auto", "sqlite-to-postgres", "s2p", "postgres-to-sqlite", "p2s"),
        default="auto",
        help="Sync direction: 'auto' (default, syncs newer hot state + merges history both ways), 'sqlite-to-postgres' ('s2p'), or 'postgres-to-sqlite' ('p2s').",
    )
    parser.add_argument(
        "--sqlite-path",
        type=Path,
        default=REPO_ROOT / DEFAULT_SQLITE_REL_PATH,
        help=f"Path to local SQLite database (default: {DEFAULT_SQLITE_REL_PATH}).",
    )
    parser.add_argument(
        "--db-url",
        default=None,
        help="Explicit PostgreSQL DSN (defaults to SPACEINVASION_DB_URL or 127.0.0.1:5433).",
    )
    parser.add_argument(
        "--batch-size",
        type=int,
        default=2000,
        help="Batch size per SQL chunk (default: 2000).",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Print machine-readable JSON report.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    t0 = time.perf_counter()

    pg_url = str(args.db_url).strip() if args.db_url else build_default_postgres_url()
    sqlite_path: Path = Path(args.sqlite_path)

    ready = ensure_postgres_container_ready(pg_url, root_dir=REPO_ROOT, auto_start=True)
    if not ready.get("ok"):
        err = f"PostgreSQL is not reachable at {redact_db_url(pg_url)}: {ready.get('error', 'connection failed')}"
        if args.json:
            print(json.dumps({"ok": False, "error": err}, indent=2))
        else:
            print(f"[SYNC] ERROR: {err}", file=sys.stderr)
        return 1

    sqlite_path.parent.mkdir(parents=True, exist_ok=True)
    sq_db = GameDatabase(sqlite_path, attack_history_path="")
    pg_db = GameDatabase(pg_url, backend="postgres")
    try:
        sq_ts = _latest_hot_ts(sq_db)
        pg_ts = _latest_hot_ts(pg_db)
        direction = args.direction
        if direction == "s2p":
            direction = "sqlite-to-postgres"
        elif direction == "p2s":
            direction = "postgres-to-sqlite"

        reverse_history_rows = 0
        if direction == "sqlite-to-postgres":
            primary_res = sync_database_stores(sq_db, pg_db, batch_size=args.batch_size)
            effective_dir = "sqlite -> postgres"
        elif direction == "postgres-to-sqlite":
            primary_res = sync_database_stores(pg_db, sq_db, batch_size=args.batch_size)
            effective_dir = "postgres -> sqlite"
        else:
            if pg_ts > sq_ts:
                primary_res = sync_database_stores(pg_db, sq_db, batch_size=args.batch_size)
                reverse_history_rows = _sync_history_only(sq_db, pg_db, batch_size=args.batch_size)
                effective_dir = "auto (postgres -> sqlite primary + bidirectional history)"
            else:
                primary_res = sync_database_stores(sq_db, pg_db, batch_size=args.batch_size)
                reverse_history_rows = _sync_history_only(pg_db, sq_db, batch_size=args.batch_size)
                effective_dir = "auto (sqlite -> postgres primary + bidirectional history)"

        sq_sum = sq_db.summary()
        pg_sum = pg_db.summary()
        sq_cfg = sq_db.load_strategy_config_snapshot() or {}
        pg_cfg = pg_db.load_strategy_config_snapshot() or {}
        elapsed_ms = round((time.perf_counter() - t0) * 1000.0, 2)

        report: dict[str, Any] = {
            "ok": True,
            "direction": effective_dir,
            "sqlite_path": str(sqlite_path),
            "postgres_target": redact_db_url(pg_url),
            "tables_synced": primary_res.get("tables_synced", 0),
            "rows_processed": primary_res.get("rows_processed", 0),
            "reverse_history_rows_checked": reverse_history_rows,
            "sqlite_total_rows": sum(int(v) for v in (sq_sum.get("counts") or {}).values()),
            "postgres_total_rows": sum(int(v) for v in (pg_sum.get("counts") or {}).values()),
            "strategy_planner_mode": pg_cfg.get("planner_mode") or sq_cfg.get("planner_mode") or "priority",
            "elapsed_ms": elapsed_ms,
        }
        if args.json:
            print(json.dumps(report, indent=2))
        else:
            print(f"[SYNC] Completed ({report['direction']}) in {elapsed_ms:.1f}ms")
            print(f"  SQLite   ({report['sqlite_path']}): {report['sqlite_total_rows']:,} rows")
            print(f"  Postgres ({report['postgres_target']}): {report['postgres_total_rows']:,} rows")
            print(f"  StrategyConfig planner_mode: {report['strategy_planner_mode']}")
        return 0
    finally:
        sq_db.close()
        pg_db.close()


if __name__ == "__main__":
    raise SystemExit(main())
