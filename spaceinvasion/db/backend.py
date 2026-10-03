"""Database backend selector (`postgres` vs `sqlite`), Podman lifecycle helper, and non-destructive store sync.

Allows operators to control which database engine SpaceInvasion Autoplayer uses via:
1. CLI flags (`--db-backend postgres|sqlite`, `--db-url ...`) on `starter.sh`, `bin/run_stack.py`, `bin/game_api.py`,
   `bin/advisor.py`, `bin/run_bot.py`, and `scripts/db_migrate.py`.
2. Environment variables (`SPACEINVASION_DB_BACKEND`, `SPACEINVASION_DB_URL`, `SPACEINVASION_PG_*`).
3. Runtime persistence (`state/db_backend.json`), changeable live from the Web Operator Console (**Config** tab)
   or via `.venv/bin/python3 scripts/db_migrate.py use postgres|sqlite`.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import gzip
import json
import os
from pathlib import Path
import re
import shutil
import socket
import subprocess
import tempfile
import threading
import time
from typing import TYPE_CHECKING, Any, Mapping, Optional, Sequence
from urllib.parse import parse_qs, quote, urlparse, urlunparse

from spaceinvasion.core.logging_utils import redact_sensitive

if TYPE_CHECKING:
    from spaceinvasion.db.storage import GameDatabase

VALID_DB_BACKENDS: frozenset[str] = frozenset({"postgres", "sqlite"})
DEFAULT_DB_BACKEND: str = "sqlite"
DEFAULT_SQLITE_REL_PATH = Path("state") / "spaceinvasion.db"
DEFAULT_BACKEND_PREF_REL_PATH = Path("state") / "db_backend.json"
DEFAULT_COMPOSE_FILENAME = "docker-compose.yml"
DEFAULT_PODMAN_CONTAINER_NAME = "spaceinvasion_postgres"
DEFAULT_PG_HOST = "127.0.0.1"
DEFAULT_PG_PORT = 5433
DEFAULT_PG_DB = "spaceinvasion"
DEFAULT_PG_USER = "spaceinvasion"
DEFAULT_PG_PASSWORD = "spaceinvasion"

# Primary keys for all 40 V014 tables (excluding `schema_migrations` / `schema_migration_events`
# which are managed independently per target store by `MigrationRunner`).
SYNCABLE_HOT_TABLE_PKS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("meta", ("key",)),
    ("strategy_config_store", ("id",)),
    ("latest_state_hot", ("id",)),
    ("empire_planets_hot", ("planet_id",)),
    ("economy_rates_cache", ("planet_id",)),
    ("tutorial_progress", ("step",)),
    ("spy_targets", ("coords",)),
    ("galaxy_scans", ("galaxy", "system")),
    ("galaxy_slots", ("coords",)),
    ("galaxy_sector_hot", ("galaxy", "system")),
    ("colony_candidates_hot", ("coords",)),
    ("ui_notifications", ("id",)),
    ("universe_research_max", ("research_key",)),
    ("manual_queue", ("id",)),
    ("config_optimizer_active", ("singleton_id",)),
    ("ai_planner_active", ("singleton_id",)),
    ("field_conflict_prompts", ("conflict_key",)),
    ("loot_recycle_hourly", ("hour_bucket",)),
)

SYNCABLE_HISTORY_TABLES: tuple[str, ...] = (
    "rank_snapshots",
    "planet_snapshots",
    "state_snapshots",
    "action_history",
    "config_history",
    "spy_reports",
    "galaxy_scan_history",
    "galaxy_slot_history",
    "raid_history",
    "bash_blocks",
    "game_messages",
    "battle_reports",
    "recycle_reports",
    "manual_queue_events",
    "universe_research_max_history",
    "colony_attempts",
    "config_optimizer_runs",
    "ai_planner_runs",
    "field_conflict_events",
    "hourly_production_history",
)

_PODMAN_STATUS_LOCK = threading.Lock()
_PODMAN_STATUS_CACHE: tuple[float, dict[str, Any]] = (0.0, {})


def is_postgres_dsn(value: Any) -> bool:
    if not isinstance(value, str):
        return False
    low = value.strip().lower()
    return low.startswith("postgresql://") or low.startswith("postgres://")


def get_unix_socket_host(dsn: Any) -> Optional[str]:
    """Return the Unix-socket directory from a PostgreSQL DSN (`?host=/path/...`), or None."""
    if not is_postgres_dsn(dsn):
        return None
    try:
        for h in parse_qs(urlparse(str(dsn).strip()).query).get("host", ()):
            if str(h).strip().startswith("/"):
                return str(h).strip()
    except ValueError:
        return None
    return None


def is_unix_socket_dsn(dsn: Any) -> bool:
    return get_unix_socket_host(dsn) is not None


def normalize_backend_name(raw: Any, default: str = DEFAULT_DB_BACKEND) -> str:
    if raw is None:
        return default
    txt = str(raw).strip().lower()
    if txt in ("pg", "postgres", "postgresql"):
        return "postgres"
    if txt in ("sqlite", "sqlite3", "file"):
        return "sqlite"
    return default


def build_default_postgres_url(env: Optional[Mapping[str, str]] = None) -> str:
    src = env if env is not None else os.environ
    explicit = str(src.get("SPACEINVASION_DB_URL") or "").strip()
    if is_postgres_dsn(explicit):
        return explicit
    host = str(src.get("SPACEINVASION_PG_HOST") or DEFAULT_PG_HOST).strip() or DEFAULT_PG_HOST
    raw_port = str(src.get("SPACEINVASION_PG_PORT") or DEFAULT_PG_PORT).strip()
    try:
        port = max(1, min(65535, int(raw_port)))
    except ValueError:
        port = DEFAULT_PG_PORT
    dbname = str(src.get("SPACEINVASION_PG_DB") or DEFAULT_PG_DB).strip() or DEFAULT_PG_DB
    user = str(src.get("SPACEINVASION_PG_USER") or DEFAULT_PG_USER).strip() or DEFAULT_PG_USER
    pw = str(src.get("SPACEINVASION_PG_PASSWORD") or DEFAULT_PG_PASSWORD)
    u_enc = quote(user, safe="")
    p_enc = quote(pw, safe="")
    d_enc = quote(dbname, safe="")
    return f"postgresql://{u_enc}:{p_enc}@{host}:{port}/{d_enc}"


def redact_db_url(url_or_path: Any) -> str:
    txt = str(url_or_path or "").strip()
    if not is_postgres_dsn(txt):
        return str(redact_sensitive(txt))
    try:
        parsed = urlparse(txt)
        if parsed.password:
            user_part = f"{parsed.username}:***" if parsed.username else "***"
            host_part = parsed.hostname if parsed.hostname is not None else ("" if is_unix_socket_dsn(txt) else "127.0.0.1")
            if parsed.port:
                host_part = f"{host_part}:{parsed.port}"
            netloc = f"{user_part}@{host_part}"
            return urlunparse((parsed.scheme, netloc, parsed.path, parsed.params, parsed.query, parsed.fragment))
        return txt
    except Exception:
        return re.sub(r"(postgres(?:ql)?://[^:/@\s]+:)[^@\s]+(@)", r"\1***\2", txt)


@dataclass(frozen=True)
class ResolvedDatabaseConfig:
    backend: str
    postgres_url: str
    sqlite_path: Path
    auto_start_podman: bool = True
    selection_source: str = "default"

    @property
    def active_target(self) -> Path | str:
        return self.postgres_url if self.backend == "postgres" else self.sqlite_path

    @property
    def display_target(self) -> str:
        if self.backend == "postgres":
            return redact_db_url(self.postgres_url)
        return str(self.sqlite_path)


def _pref_file_path(root_dir: Path | str = Path(".")) -> Path:
    return Path(root_dir) / DEFAULT_BACKEND_PREF_REL_PATH


def load_persisted_db_preference(root_dir: Path | str = Path(".")) -> Optional[dict[str, Any]]:
    pref_path = _pref_file_path(root_dir)
    if not pref_path.is_file():
        return None
    try:
        raw = json.loads(pref_path.read_text(encoding="utf-8"))
        return dict(raw) if isinstance(raw, Mapping) else None
    except Exception:
        return None


def save_persisted_db_preference(
    backend: str,
    *,
    postgres_url: Optional[str] = None,
    sqlite_path: Optional[Path | str] = None,
    auto_start_podman: bool = True,
    root_dir: Path | str = Path("."),
) -> Path:
    norm_backend = normalize_backend_name(backend, DEFAULT_DB_BACKEND)
    pref_path = _pref_file_path(root_dir)
    pref_path.parent.mkdir(parents=True, exist_ok=True)
    existing = load_persisted_db_preference(root_dir) or {}
    eff_pg_url = (
        str(postgres_url).strip()
        if (postgres_url and is_postgres_dsn(postgres_url))
        else str(existing.get("postgres_url") or "").strip()
    )
    eff_sqlite = (
        str(sqlite_path).strip()
        if sqlite_path is not None
        else str(existing.get("sqlite_path") or DEFAULT_SQLITE_REL_PATH)
    )
    payload: dict[str, Any] = {
        "backend": norm_backend,
        "sqlite_path": eff_sqlite,
        "auto_start_podman": bool(auto_start_podman),
        "updated_ts": round(time.time(), 3),
    }
    if eff_pg_url:
        payload["postgres_url"] = eff_pg_url
    tmp_fd, tmp_raw = tempfile.mkstemp(prefix=".db_backend.", suffix=".tmp", dir=str(pref_path.parent))
    os.close(tmp_fd)
    tmp_path = Path(tmp_raw)
    try:
        tmp_path.write_text(json.dumps(payload, indent=2, sort_keys=True, allow_nan=False) + "\n", encoding="utf-8")
        os.replace(tmp_path, pref_path)
    except Exception:
        tmp_path.unlink(missing_ok=True)
        raise
    return pref_path


def is_container_runtime(env: Optional[Mapping[str, str]] = None) -> bool:
    """Return True when running inside the SpaceInvasion Docker/Podman container stack."""
    src = env if env is not None else os.environ
    flag = str(src.get("SPACEINVASION_CONTAINER") or "").strip().lower()
    return flag in ("1", "true", "yes", "on")


def resolve_database_config(
    *,
    explicit_backend: Optional[str] = None,
    explicit_db_url: Optional[str] = None,
    explicit_sqlite_path: Optional[Path | str] = None,
    root_dir: Path | str = Path("."),
    env: Optional[Mapping[str, str]] = None,
) -> ResolvedDatabaseConfig:
    """Resolve active database backend:
    - Docker/Podman (`SPACEINVASION_CONTAINER=1`): strictly `postgres` (`postgres` is the only option in Docker).
    - Local host (`./starter.sh` without Docker): defaults to `sqlite` (`state/spaceinvasion.db`).
    """
    src_env = env if env is not None else os.environ
    root_p = Path(root_dir)
    in_container = is_container_runtime(src_env)
    persisted = (
        load_persisted_db_preference(root_p) or {}
        if (not in_container and Path(root_dir) != Path("."))
        else {}
    )

    default_sqlite = root_p / DEFAULT_SQLITE_REL_PATH
    if explicit_sqlite_path is not None and not is_postgres_dsn(str(explicit_sqlite_path)):
        sqlite_p = Path(explicit_sqlite_path)
    elif persisted.get("sqlite_path"):
        cand_sp = Path(str(persisted["sqlite_path"]))
        sqlite_p = cand_sp if cand_sp.is_absolute() else (root_p / cand_sp)
    else:
        sqlite_p = default_sqlite

    env_url = str(src_env.get("SPACEINVASION_DB_URL") or "").strip()
    persisted_url = str(persisted.get("postgres_url") or "").strip()
    if explicit_db_url and is_postgres_dsn(explicit_db_url):
        pg_url = str(explicit_db_url).strip()
    elif is_postgres_dsn(env_url):
        pg_url = env_url
    elif is_postgres_dsn(persisted_url):
        pg_url = persisted_url
    else:
        pg_url = build_default_postgres_url(src_env)

    auto_podman = False if in_container else bool(persisted.get("auto_start_podman", True))
    env_auto = str(src_env.get("SPACEINVASION_AUTO_PODMAN") or "").strip().lower()
    if env_auto in ("0", "false", "no", "off"):
        auto_podman = False
    elif env_auto in ("1", "true", "yes", "on"):
        auto_podman = True

    # Inside Docker/Podman, PostgreSQL is the ONLY supported runtime database.
    if in_container:
        if explicit_backend and normalize_backend_name(explicit_backend, "postgres") == "sqlite":
            raise ValueError(
                "SQLite is disabled inside Docker/Podman containers; PostgreSQL is the only container database."
            )
        return ResolvedDatabaseConfig(
            backend="postgres",
            postgres_url=pg_url,
            sqlite_path=sqlite_p,
            auto_start_podman=False,
            selection_source="container_postgres_only",
        )

    if explicit_db_url and is_postgres_dsn(explicit_db_url) and not explicit_backend:
        return ResolvedDatabaseConfig(
            backend="postgres",
            postgres_url=pg_url,
            sqlite_path=sqlite_p,
            auto_start_podman=auto_podman,
            selection_source="cli_db_url",
        )

    if explicit_backend and str(explicit_backend).strip().lower() not in ("", "auto"):
        return ResolvedDatabaseConfig(
            backend=normalize_backend_name(explicit_backend, DEFAULT_DB_BACKEND),
            postgres_url=pg_url,
            sqlite_path=sqlite_p,
            auto_start_podman=auto_podman,
            selection_source="cli_flag",
        )

    if explicit_sqlite_path is not None:
        if is_postgres_dsn(str(explicit_sqlite_path)):
            return ResolvedDatabaseConfig(
                backend="postgres",
                postgres_url=str(explicit_sqlite_path).strip(),
                sqlite_path=default_sqlite,
                auto_start_podman=auto_podman,
                selection_source="cli_db_path_dsn",
            )
        return ResolvedDatabaseConfig(
            backend="sqlite",
            postgres_url=pg_url,
            sqlite_path=sqlite_p,
            auto_start_podman=auto_podman,
            selection_source="explicit_sqlite_path",
        )

    env_backend = str(src_env.get("SPACEINVASION_DB_BACKEND") or "").strip().lower()
    if env_backend and env_backend != "auto":
        return ResolvedDatabaseConfig(
            backend=normalize_backend_name(env_backend, DEFAULT_DB_BACKEND),
            postgres_url=pg_url,
            sqlite_path=sqlite_p,
            auto_start_podman=auto_podman,
            selection_source="env_var",
        )
    if is_postgres_dsn(env_url):
        return ResolvedDatabaseConfig(
            backend="postgres",
            postgres_url=pg_url,
            sqlite_path=sqlite_p,
            auto_start_podman=auto_podman,
            selection_source="env_db_url",
        )

    if persisted.get("backend") in VALID_DB_BACKENDS:
        return ResolvedDatabaseConfig(
            backend=str(persisted["backend"]),
            postgres_url=pg_url,
            sqlite_path=sqlite_p,
            auto_start_podman=auto_podman,
            selection_source="persisted_preference",
        )

    return ResolvedDatabaseConfig(
        backend=DEFAULT_DB_BACKEND,
        postgres_url=pg_url,
        sqlite_path=sqlite_p,
        auto_start_podman=auto_podman,
        selection_source="default",
    )


def ping_postgres(dsn: str, *, timeout: float = 2.0) -> tuple[bool, float, str]:
    """Ping a PostgreSQL DSN (`SELECT 1`) and return `(ok, latency_ms, error_or_version)`."""
    t0 = time.perf_counter()
    try:
        import psycopg

        with psycopg.connect(dsn, connect_timeout=max(1, int(round(timeout)))) as conn:
            row = conn.execute("SELECT current_setting('server_version')").fetchone()
            ver = str(row[0]) if row and row[0] else "PostgreSQL"
        ms = round((time.perf_counter() - t0) * 1000.0, 2)
        return (True, ms, f"PostgreSQL {ver}")
    except Exception as exc:
        ms = round((time.perf_counter() - t0) * 1000.0, 2)
        return (False, ms, str(redact_sensitive(str(exc))))


def get_podman_postgres_status(*, force: bool = False) -> dict[str, Any]:
    """Return cached Podman container status for `spaceinvasion_postgres`."""
    global _PODMAN_STATUS_CACHE
    now_m = time.monotonic()
    with _PODMAN_STATUS_LOCK:
        if not force and _PODMAN_STATUS_CACHE[1] and (now_m - _PODMAN_STATUS_CACHE[0]) < 3.0:
            return dict(_PODMAN_STATUS_CACHE[1])

    podman_bin = shutil.which("podman")
    podman_compose_bin = shutil.which("podman-compose")
    info: dict[str, Any] = {
        "runtime": "podman" if podman_bin else ("docker" if shutil.which("docker") else "none"),
        "podman_available": bool(podman_bin),
        "podman_compose_available": bool(podman_compose_bin),
        "container_name": DEFAULT_PODMAN_CONTAINER_NAME,
        "container_running": False,
        "container_status": "not_found",
    }
    runner_bin = podman_bin or shutil.which("docker")
    if runner_bin:
        try:
            proc = subprocess.run(
                [
                    runner_bin,
                    "ps",
                    "-a",
                    "--filter",
                    f"name=^/?{DEFAULT_PODMAN_CONTAINER_NAME}$",
                    "--format",
                    "{{.State}}|{{.Status}}",
                ],
                capture_output=True,
                text=True,
                timeout=3.0,
                check=False,
            )
            line = (proc.stdout or "").strip().splitlines()
            if line:
                parts = line[0].split("|", 1)
                state_s = parts[0].strip().lower()
                status_s = parts[1].strip() if len(parts) > 1 else state_s
                info["container_running"] = state_s == "running" or status_s.lower().startswith("up")
                info["container_status"] = status_s or state_s
        except Exception as exc:
            info["container_status"] = f"probe_error: {redact_sensitive(str(exc))}"

    with _PODMAN_STATUS_LOCK:
        _PODMAN_STATUS_CACHE = (now_m, dict(info))
    return info


def ensure_postgres_container_ready(
    dsn: str,
    *,
    root_dir: Path | str = Path("."),
    auto_start: bool = True,
    timeout: float = 25.0,
) -> dict[str, Any]:
    """Ensure PostgreSQL at `dsn` is reachable, starting `spaceinvasion_postgres` via Podman if local and stopped."""
    ok, ms, msg = ping_postgres(dsn, timeout=2.0)
    if ok:
        return {
            "ok": True,
            "started_container": False,
            "latency_ms": ms,
            "server": msg,
            "target": redact_db_url(dsn),
        }

    parsed = urlparse(dsn)
    host = (parsed.hostname or "127.0.0.1").lower()
    is_local = (not is_unix_socket_dsn(dsn)) and host in ("127.0.0.1", "localhost", "::1")
    if not auto_start or not is_local:
        return {
            "ok": False,
            "started_container": False,
            "latency_ms": ms,
            "error": msg,
            "target": redact_db_url(dsn),
        }

    repo_root = Path(root_dir).resolve()
    compose_file = repo_root / DEFAULT_COMPOSE_FILENAME
    podman_compose_bin = shutil.which("podman-compose")
    podman_bin = shutil.which("podman")
    docker_bin = shutil.which("docker")

    cmd: Optional[list[str]] = None
    if podman_compose_bin and compose_file.is_file():
        cmd = [podman_compose_bin, "-f", str(compose_file), "up", "-d", "postgres"]
    elif podman_bin:
        # Check if container already exists in Podman
        inspect_p = subprocess.run(
            [podman_bin, "container", "exists", DEFAULT_PODMAN_CONTAINER_NAME],
            capture_output=True,
            timeout=4.0,
            check=False,
        )
        if inspect_p.returncode == 0:
            cmd = [podman_bin, "start", DEFAULT_PODMAN_CONTAINER_NAME]
        elif compose_file.is_file():
            cmd = [podman_bin, "compose", "-f", str(compose_file), "up", "-d", "postgres"]
    elif docker_bin and compose_file.is_file():
        cmd = [docker_bin, "compose", "-f", str(compose_file), "up", "-d", "postgres"]

    start_err = ""
    if cmd is not None:
        env_copy = os.environ.copy()
        if parsed.port:
            env_copy.setdefault("SPACEINVASION_PG_PORT", str(parsed.port))
        if parsed.username:
            env_copy.setdefault("SPACEINVASION_PG_USER", str(parsed.username))
        if parsed.password:
            env_copy.setdefault("SPACEINVASION_PG_PASSWORD", str(parsed.password))
        if parsed.path and parsed.path.strip("/"):
            env_copy.setdefault("SPACEINVASION_PG_DB", parsed.path.strip("/"))
        try:
            proc = subprocess.run(
                cmd,
                cwd=str(repo_root),
                env=env_copy,
                capture_output=True,
                text=True,
                timeout=30.0,
                check=False,
            )
            if proc.returncode != 0:
                start_err = (proc.stderr or proc.stdout or f"exit {proc.returncode}").strip()
        except Exception as exc:
            start_err = str(exc)

    deadline = time.monotonic() + max(2.0, float(timeout))
    last_err = start_err or msg
    while time.monotonic() < deadline:
        # Quick TCP check before full psycopg handshake
        port = int(parsed.port or 5432)
        sock_ok = False
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.settimeout(0.5)
            sock_ok = sock.connect_ex(("127.0.0.1", port)) == 0
        if sock_ok:
            ok2, ms2, msg2 = ping_postgres(dsn, timeout=2.0)
            if ok2:
                get_podman_postgres_status(force=True)
                return {
                    "ok": True,
                    "started_container": True,
                    "latency_ms": ms2,
                    "server": msg2,
                    "target": redact_db_url(dsn),
                }
            last_err = msg2
        time.sleep(0.4)

    return {
        "ok": False,
        "started_container": cmd is not None,
        "error": str(redact_sensitive(last_err)),
        "target": redact_db_url(dsn),
    }


def _list_table_columns(db: "GameDatabase", table: str) -> list[str]:
    with db._lock:
        rows = db._conn.execute(f"PRAGMA table_info({table})").fetchall()
        return [str(r[1]) for r in rows]


def _existing_tables(db: "GameDatabase") -> set[str]:
    with db._lock:
        rows = db._conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'").fetchall()
        return {str(r[0]) for r in rows}


def sync_database_stores(
    source_db: "GameDatabase",
    target_db: "GameDatabase",
    *,
    batch_size: int = 1000,
) -> dict[str, Any]:
    """Non-destructively copy/upsert all hot & history tables from `source_db` into `target_db`.

    Guarantees:
    - Strictly append/upsert-only: NEVER executes `DELETE` or `DROP TABLE` on either database.
    - Preserves protected schema ledger markers (`schema_version`, `schema_version_tag`, `schema_checksum`)
      on `target_db`.
    - Advances PostgreSQL `IDENTITY` sequences on `target_db` after copying `id` columns.
    """
    from spaceinvasion.db.migrations import PROTECTED_META_KEYS

    t0 = time.perf_counter()
    eff_batch = max(50, min(5000, int(batch_size)))
    src_tables = _existing_tables(source_db)
    dst_tables = _existing_tables(target_db)

    per_table_copied: dict[str, int] = {}
    total_rows = 0

    # 1. Sync hot tables via primary-key upsert
    for tbl, pk_cols in SYNCABLE_HOT_TABLE_PKS:
        if tbl not in src_tables or tbl not in dst_tables:
            continue
        src_cols = _list_table_columns(source_db, tbl)
        dst_cols_set = set(_list_table_columns(target_db, tbl))
        common_cols = [c for c in src_cols if c in dst_cols_set]
        if not common_cols or not all(pk in common_cols for pk in pk_cols):
            continue

        col_list_sql = ", ".join(f'"{c}"' for c in common_cols)
        placeholders = ", ".join("?" for _ in common_cols)
        pk_sql = ", ".join(f'"{pk}"' for pk in pk_cols)
        non_pk_cols = [c for c in common_cols if c not in pk_cols]
        if non_pk_cols:
            update_set = ", ".join(f'"{c}" = excluded."{c}"' for c in non_pk_cols)
            upsert_sql = (
                f'INSERT INTO "{tbl}" ({col_list_sql}) VALUES ({placeholders}) '
                f"ON CONFLICT ({pk_sql}) DO UPDATE SET {update_set}"
            )
        else:
            upsert_sql = (
                f'INSERT INTO "{tbl}" ({col_list_sql}) VALUES ({placeholders}) '
                f"ON CONFLICT ({pk_sql}) DO NOTHING"
            )

        order_sql = ", ".join(f'"{pk}" ASC' for pk in pk_cols)
        with source_db._lock:
            cur_r = source_db._conn.cursor()
            cur_r.row_factory = None
            all_rows = cur_r.execute(f'SELECT {col_list_sql} FROM "{tbl}" ORDER BY {order_sql}').fetchall()

        if tbl == "meta":
            key_idx = common_cols.index("key")
            all_rows = [r for r in all_rows if str(r[key_idx]) not in PROTECTED_META_KEYS]

        copied = 0
        if all_rows:
            with target_db._lock:
                for start in range(0, len(all_rows), eff_batch):
                    chunk = [tuple(r) for r in all_rows[start : start + eff_batch]]
                    target_db._conn.executemany(upsert_sql, chunk)
                    copied += len(chunk)
                target_db._conn.commit()
        per_table_copied[tbl] = copied
        total_rows += copied

    # 2. Sync append-only history tables via ON CONFLICT DO NOTHING
    for tbl in SYNCABLE_HISTORY_TABLES:
        if tbl not in src_tables or tbl not in dst_tables:
            continue
        src_cols = _list_table_columns(source_db, tbl)
        dst_cols_set = set(_list_table_columns(target_db, tbl))
        common_cols = [c for c in src_cols if c in dst_cols_set]
        if not common_cols:
            continue

        col_list_sql = ", ".join(f'"{c}"' for c in common_cols)
        placeholders = ", ".join("?" for _ in common_cols)
        insert_sql = (
            f'INSERT INTO "{tbl}" ({col_list_sql}) VALUES ({placeholders}) '
            f"ON CONFLICT DO NOTHING"
        )
        order_col = '"id" ASC' if "id" in common_cols else f'"{common_cols[0]}" ASC'

        copied = 0
        # Stream in chunks if `id` column exists so large history tables stay memory-light
        if "id" in common_cols:
            last_id = -1
            id_idx = common_cols.index("id")
            while True:
                with source_db._lock:
                    cur_r = source_db._conn.cursor()
                    cur_r.row_factory = None
                    batch_rows = cur_r.execute(
                        f'SELECT {col_list_sql} FROM "{tbl}" WHERE "id" > ? ORDER BY "id" ASC LIMIT ?',
                        (last_id, eff_batch),
                    ).fetchall()
                if not batch_rows:
                    break
                chunk = [tuple(r) for r in batch_rows]
                last_id = int(chunk[-1][id_idx])
                with target_db._lock:
                    target_db._conn.executemany(insert_sql, chunk)
                    target_db._conn.commit()
                copied += len(chunk)
        else:
            with source_db._lock:
                cur_r = source_db._conn.cursor()
                cur_r.row_factory = None
                all_rows = cur_r.execute(f'SELECT {col_list_sql} FROM "{tbl}" ORDER BY {order_col}').fetchall()
            if all_rows:
                with target_db._lock:
                    for start in range(0, len(all_rows), eff_batch):
                        chunk = [tuple(r) for r in all_rows[start : start + eff_batch]]
                        target_db._conn.executemany(insert_sql, chunk)
                        copied += len(chunk)
                    target_db._conn.commit()

        per_table_copied[tbl] = copied
        total_rows += copied

    # 3. Advance PostgreSQL sequences & refresh target caches
    seq_synced: dict[str, int] = {}
    pg_backend = getattr(target_db, "_pg_backend", None)
    if pg_backend is not None:
        seq_synced = pg_backend.sync_identity_sequences()

    inv_fn = getattr(target_db, "_invalidate_hot_caches_locked", None)
    if callable(inv_fn):
        with target_db._lock:
            inv_fn()

    elapsed_ms = round((time.perf_counter() - t0) * 1000.0, 2)
    return {
        "ok": True,
        "source_backend": getattr(source_db, "backend_name", "sqlite"),
        "target_backend": getattr(target_db, "backend_name", "sqlite"),
        "tables_synced": len(per_table_copied),
        "rows_processed": total_rows,
        "per_table": per_table_copied,
        "sequences_synced": len(seq_synced),
        "elapsed_ms": elapsed_ms,
    }


def create_postgres_backup(
    db: "GameDatabase",
    *,
    label: str = "manual",
    backup_dir: Optional[Path] = None,
) -> Path:
    """Create a non-destructive PostgreSQL snapshot in `data/backups/` (`pg_dump` or gzipped JSONL)."""
    dsn = getattr(db, "postgres_url", "") or ""
    sock_host = get_unix_socket_host(dsn)
    db_p = getattr(db, "db_path", None)
    sock_bkp = (
        db_p.parent / "backups"
        if isinstance(db_p, Path) and db_p.resolve() != DEFAULT_SQLITE_REL_PATH.resolve()
        else (Path(sock_host).parent / "backups" if sock_host is not None else Path("data") / "backups")
    )
    out_dir = backup_dir if backup_dir is not None else sock_bkp
    out_dir.mkdir(parents=True, exist_ok=True)
    safe_label = re.sub(r"[^A-Za-z0-9_.-]+", "_", str(label or "backup").strip())[:40] or "backup"
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    ns_tail = time.time_ns() % 1_000_000

    parsed = urlparse(dsn)
    podman_bin = shutil.which("podman")
    is_local = (sock_host is None) and ((parsed.hostname or "127.0.0.1").lower() in ("127.0.0.1", "localhost", "::1"))

    if podman_bin and is_local:
        dump_dest = out_dir / f"spaceinvasion_pg.{safe_label}.{stamp}_{ns_tail:06d}.dump"
        tmp_fd, tmp_raw = tempfile.mkstemp(prefix=".spaceinvasion_pg.bkp.", suffix=".tmp", dir=str(out_dir))
        os.close(tmp_fd)
        tmp_path = Path(tmp_raw)
        try:
            pg_user = parsed.username or DEFAULT_PG_USER
            pg_db = (parsed.path or "").strip("/") or DEFAULT_PG_DB
            with tmp_path.open("wb") as fh:
                proc = subprocess.run(
                    [podman_bin, "exec", DEFAULT_PODMAN_CONTAINER_NAME, "pg_dump", "-U", pg_user, "-d", pg_db, "-Fc"],
                    stdout=fh,
                    stderr=subprocess.PIPE,
                    timeout=60.0,
                    check=False,
                )
            if proc.returncode == 0 and tmp_path.stat().st_size > 0:
                os.replace(tmp_path, dump_dest)
                return dump_dest
            tmp_path.unlink(missing_ok=True)
        except Exception:
            tmp_path.unlink(missing_ok=True)

    # Pure-Python portable gzipped JSONL fallback
    jsonl_dest = out_dir / f"spaceinvasion_pg.{safe_label}.{stamp}_{ns_tail:06d}.jsonl.gz"
    tmp_fd, tmp_raw = tempfile.mkstemp(prefix=".spaceinvasion_pg.bkp.", suffix=".tmp.gz", dir=str(out_dir))
    os.close(tmp_fd)
    tmp_path = Path(tmp_raw)
    try:
        tables = sorted(_existing_tables(db))
        with gzip.open(tmp_path, "wt", encoding="utf-8") as gz:
            for tbl in tables:
                cols = _list_table_columns(db, tbl)
                if not cols:
                    continue
                col_sql = ", ".join(f'"{c}"' for c in cols)
                with db._lock:
                    cur = db._conn.cursor()
                    cur.row_factory = None
                    rows = cur.execute(f'SELECT {col_sql} FROM "{tbl}"').fetchall()
                for r in rows:
                    rec = {"table": tbl, "row": dict(zip(cols, r))}
                    gz.write(json.dumps(rec, sort_keys=True, allow_nan=False) + "\n")
        os.replace(tmp_path, jsonl_dest)
        return jsonl_dest
    except Exception:
        tmp_path.unlink(missing_ok=True)
        raise
