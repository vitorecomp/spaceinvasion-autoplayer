#!/usr/bin/env python3
"""Switch on and read PostgreSQL statement statistics (refactor plan phase 0.8, decision D15).

`docker-compose.yml` starts PostgreSQL with `shared_preload_libraries=pg_stat_statements`,
`pg_stat_statements.track=all`, `pg_stat_statements.max=10000` and
`log_min_duration_statement=50`. Those settings need the `postgres` container to be
recreated once (`podman-compose up -d postgres`; the data in `./data/postgres` stays).
The init script `docker/postgres/01-pg-stat-statements.sql` only runs for an empty data
directory, so a database that already exists needs this script once after the restart:

    .venv/bin/python3 scripts/enable_pg_stats.py            # create the extension (idempotent)
    .venv/bin/python3 scripts/enable_pg_stats.py --check    # report the settings, change nothing
    .venv/bin/python3 scripts/enable_pg_stats.py --report   # statements ranked by total time
    .venv/bin/python3 scripts/enable_pg_stats.py --report --order-by mean --limit 40 --json

The connection comes from `--dsn`, else from `SPACEINVASION_DB_URL` or the
`SPACEINVASION_PG_*` variables (default `127.0.0.1:5433`, the compose port).
`--check` and `--report` use a read-only session. `--reset` clears the counters and
is the only destructive option; it never touches application tables.

Results go to stdout, progress and problems to the log on stderr.

Exit codes: 0 ready, 1 connection or SQL error, 2 the library is not preloaded
(the container still runs with the old command), 3 extension missing (`--check`, `--report`).
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
import json
import logging
from pathlib import Path
import sys
from typing import Any, Optional, Sequence

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

LOG = logging.getLogger("enable_pg_stats")

EXTENSION = "pg_stat_statements"
CREATE_EXTENSION_SQL = f"CREATE EXTENSION IF NOT EXISTS {EXTENSION};"

# Settings docker-compose.yml is expected to apply: name -> expected value (None: any value).
EXPECTED_SETTINGS: dict[str, Optional[str]] = {
    "shared_preload_libraries": None,
    "pg_stat_statements.track": "all",
    "pg_stat_statements.max": "10000",
    "log_min_duration_statement": "50ms",
}

ORDER_COLUMNS = {
    "total": "total_exec_time",
    "mean": "mean_exec_time",
    "max": "max_exec_time",
    "calls": "calls",
    "rows": "rows",
    "io": "shared_blks_read",
}

EXIT_OK = 0
EXIT_ERROR = 1
EXIT_NOT_PRELOADED = 2
EXIT_NO_EXTENSION = 3

_CURRENT_DB_FILTER = "dbid = (SELECT oid FROM pg_database WHERE datname = current_database())"


@dataclass(frozen=True)
class ServerStatus:
    """What the server says about statement statistics."""

    settings: dict[str, Optional[str]]
    extension_version: Optional[str]

    @property
    def preloaded(self) -> bool:
        libs = [part.strip() for part in (self.settings.get("shared_preload_libraries") or "").split(",")]
        return EXTENSION in libs

    def problems(self) -> list[str]:
        found: list[str] = []
        if not self.preloaded:
            found.append(
                f"shared_preload_libraries is {self.settings.get('shared_preload_libraries')!r}: "
                f"{EXTENSION} is not loaded"
            )
        for name, expected in EXPECTED_SETTINGS.items():
            if expected is not None and self.settings.get(name) != expected:
                found.append(f"{name} is {self.settings.get(name)!r}, expected {expected!r}")
        return found


def _emit(line: str) -> None:
    """Write one line of the result to stdout."""
    sys.stdout.write(f"{line}\n")


def default_dsn() -> str:
    from spaceinvasion.db.backend import build_default_postgres_url

    return build_default_postgres_url()


def redact(dsn: str) -> str:
    from spaceinvasion.db.backend import redact_db_url

    return redact_db_url(dsn)


def read_status(conn: Any) -> ServerStatus:
    settings: dict[str, Optional[str]] = {}
    for name in EXPECTED_SETTINGS:
        row = conn.execute("SELECT current_setting(%s, true)", (name,)).fetchone()
        settings[name] = None if row is None or row[0] is None else str(row[0])
    row = conn.execute("SELECT extversion FROM pg_extension WHERE extname = %s", (EXTENSION,)).fetchone()
    return ServerStatus(settings=settings, extension_version=None if row is None else str(row[0]))


def ensure_extension(conn: Any) -> ServerStatus:
    """Create the extension if it is missing and return the status afterwards."""
    before = read_status(conn).extension_version
    conn.execute(CREATE_EXTENSION_SQL)
    status = read_status(conn)
    LOG.info("%s %s: %s", EXTENSION, status.extension_version, "already present" if before else "created")
    return status


def _statement_row(rank: int, row: Sequence[Any], total_ms: float) -> dict[str, Any]:
    return {
        "rank": rank,
        "queryid": str(row[0]),
        "toplevel": bool(row[1]),
        "calls": int(row[2]),
        "total_ms": round(float(row[3]), 3),
        "mean_ms": round(float(row[4]), 3),
        "max_ms": round(float(row[5]), 3),
        "stddev_ms": round(float(row[6]), 3),
        "pct_of_total": round(100.0 * float(row[3]) / total_ms, 2) if total_ms > 0 else 0.0,
        "rows": int(row[7]),
        "shared_blks_hit": int(row[8]),
        "shared_blks_read": int(row[9]),
        "temp_blks_written": int(row[10]),
        "query": " ".join(str(row[11]).split()),
    }


def fetch_report(conn: Any, *, order_by: str = "total", limit: int = 25, min_calls: int = 1) -> dict[str, Any]:
    """Statements of the current database ranked by `order_by`, plus totals and the collection window."""
    column = ORDER_COLUMNS[order_by]  # a fixed column name from the table above, never user text
    info = conn.execute("SELECT dealloc, stats_reset FROM pg_stat_statements_info").fetchone()
    totals = conn.execute(
        "SELECT count(*), coalesce(sum(calls), 0), coalesce(sum(total_exec_time), 0) "
        f"FROM pg_stat_statements WHERE {_CURRENT_DB_FILTER}"
    ).fetchone()
    total_ms = float(totals[2] or 0.0)
    rows = conn.execute(
        "SELECT queryid, toplevel, calls, total_exec_time, mean_exec_time, max_exec_time, stddev_exec_time, "
        "rows, shared_blks_hit, shared_blks_read, temp_blks_written, query "
        f"FROM pg_stat_statements WHERE {_CURRENT_DB_FILTER} AND calls >= %s "
        "AND query NOT ILIKE '%%pg_stat_statements%%' "
        f"ORDER BY {column} DESC, queryid LIMIT %s",
        (max(1, int(min_calls)), max(1, int(limit))),
    ).fetchall()
    return {
        "database": conn.execute("SELECT current_database()").fetchone()[0],
        "order_by": order_by,
        "stats_reset": None if info is None or info[1] is None else info[1].isoformat(),
        "deallocated_entries": 0 if info is None else int(info[0]),
        "distinct_statements": int(totals[0]),
        "total_calls": int(totals[1]),
        "total_exec_ms": round(total_ms, 3),
        "statements": [_statement_row(rank, row, total_ms) for rank, row in enumerate(rows, start=1)],
    }


def format_report(report: dict[str, Any], *, query_width: int = 110) -> str:
    lines = [
        f"pg_stat_statements report for database {report['database']!r}, ranked by {report['order_by']}",
        (
            f"collecting since {report['stats_reset'] or 'unknown'}; "
            f"{report['distinct_statements']} distinct statements, {report['total_calls']} calls, "
            f"{report['total_exec_ms']:.1f} ms total; {report['deallocated_entries']} entries evicted"
        ),
        "",
        f"{'#':>3} {'calls':>9} {'total ms':>12} {'%':>6} {'mean ms':>10} {'max ms':>10} {'rows':>10} "
        f"{'blk read':>9}  query",
    ]
    for st in report["statements"]:
        query = st["query"]
        if len(query) > query_width:
            query = query[: query_width - 1] + "…"
        lines.append(
            f"{st['rank']:>3} {st['calls']:>9} {st['total_ms']:>12.1f} {st['pct_of_total']:>6.2f} "
            f"{st['mean_ms']:>10.3f} {st['max_ms']:>10.3f} {st['rows']:>10} {st['shared_blks_read']:>9}  {query}"
        )
    if not report["statements"]:
        lines.append("(no statements recorded yet)")
    return "\n".join(lines)


def _emit_status(status: ServerStatus) -> None:
    for name, value in status.settings.items():
        _emit(f"  {name} = {value}")
    version = status.extension_version
    _emit(f"  extension {EXTENSION}: {'version ' + version if version else 'not created in this database'}")
    for problem in status.problems():
        _emit(f"PROBLEM: {problem}")


def _readiness_exit_code(status: ServerStatus) -> int:
    if not status.preloaded:
        LOG.error(
            "%s is not in shared_preload_libraries (%r). Recreate the postgres container so that the command "
            "in docker-compose.yml applies: podman-compose up -d postgres",
            EXTENSION,
            status.settings.get("shared_preload_libraries"),
        )
        return EXIT_NOT_PRELOADED
    if status.extension_version is None:
        LOG.error("%s is not created in this database; run this script without --check or --report.", EXTENSION)
        return EXIT_NO_EXTENSION
    return EXIT_OK


def _run_on_connection(conn: Any, args: argparse.Namespace) -> int:
    read_only = (args.check or args.report) and not args.reset
    if read_only:
        conn.execute("SET default_transaction_read_only = on")
    status = read_status(conn) if read_only else ensure_extension(conn)

    if args.check:
        _emit_status(status)
        code = _readiness_exit_code(status)
        if code == EXIT_OK:
            _emit("Statement statistics are on." + (" (with differences, see above)" if status.problems() else ""))
        return code

    code = _readiness_exit_code(status)
    if code != EXIT_OK:
        return code
    if args.reset:
        conn.execute("SELECT pg_stat_statements_reset()")
        LOG.info("Statement counters reset.")
    if args.report:
        report = fetch_report(conn, order_by=args.order_by, limit=args.limit, min_calls=args.min_calls)
        _emit(json.dumps(report, indent=2) if args.json else format_report(report))
    else:
        _emit_status(status)
    return EXIT_OK


def run(args: argparse.Namespace) -> int:
    try:
        import psycopg
    except ImportError:
        LOG.error("psycopg is not installed for this interpreter; run the script with .venv/bin/python3")
        return EXIT_ERROR

    dsn = args.dsn or default_dsn()
    LOG.info("PostgreSQL: %s", redact(dsn))
    try:
        with psycopg.connect(dsn, connect_timeout=args.connect_timeout, autocommit=True) as conn:
            return _run_on_connection(conn, args)
    except psycopg.Error as exc:
        LOG.error("PostgreSQL error: %s: %s", type(exc).__name__, " ".join(str(exc).split()))
        return EXIT_ERROR


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Create the pg_stat_statements extension and print a ranked statement report.",
        epilog="Without options: verify the settings and run CREATE EXTENSION IF NOT EXISTS pg_stat_statements.",
    )
    parser.add_argument(
        "--dsn", default=None, help="PostgreSQL DSN (default: SPACEINVASION_DB_URL / SPACEINVASION_PG_*)"
    )
    parser.add_argument("--check", action="store_true", help="report settings and extension state, change nothing")
    parser.add_argument("--report", action="store_true", help="print the ranked statement report")
    parser.add_argument(
        "--order-by", choices=sorted(ORDER_COLUMNS), default="total", help="ranking column (default: total)"
    )
    parser.add_argument("--limit", type=int, default=25, help="statements to show (default: 25)")
    parser.add_argument("--min-calls", type=int, default=1, help="skip statements called fewer times (default: 1)")
    parser.add_argument("--json", action="store_true", help="print the report as JSON")
    parser.add_argument("--reset", action="store_true", help="reset the counters (starts a new baseline)")
    parser.add_argument("--connect-timeout", type=int, default=5, help="connection timeout in seconds (default: 5)")
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s", stream=sys.stderr)
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.check and args.reset:
        parser.error("--check changes nothing and cannot be combined with --reset")
    return run(args)


if __name__ == "__main__":
    raise SystemExit(main())
