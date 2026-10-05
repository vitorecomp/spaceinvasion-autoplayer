"""PostgreSQL database backend configuration, Podman lifecycle helper, and backup utilities."""

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
from typing import TYPE_CHECKING, Any, Mapping, Optional
from urllib.parse import parse_qs, quote, urlparse, urlunparse

from spaceinvasion.core.logging_utils import redact_sensitive

if TYPE_CHECKING:
    from spaceinvasion.db.storage import GameDatabase

VALID_DB_BACKENDS: frozenset[str] = frozenset({"postgres"})
DEFAULT_DB_BACKEND: str = "postgres"
DEFAULT_COMPOSE_FILENAME = "docker-compose.yml"
DEFAULT_PODMAN_CONTAINER_NAME = "spaceinvasion_postgres"
DEFAULT_PG_HOST = "127.0.0.1"
DEFAULT_PG_PORT = 5433
DEFAULT_PG_DB = "spaceinvasion"
DEFAULT_PG_USER = "spaceinvasion"
DEFAULT_PG_PASSWORD = "spaceinvasion"

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
        hosts = parse_qs(urlparse(str(dsn).strip()).query).get("host", ())
    except ValueError:
        return None
    return next((s for h in hosts if (s := str(h).strip()).startswith("/")), None)


def is_unix_socket_dsn(dsn: Any) -> bool:
    return get_unix_socket_host(dsn) is not None


def normalize_backend_name(raw: Any, default: str = DEFAULT_DB_BACKEND) -> str:
    if raw is None:
        return default
    txt = str(raw).strip().lower()
    if txt in ("", "auto"):
        return default
    if txt in ("pg", "postgres", "postgresql"):
        return "postgres"
    if txt in ("sqlite", "sqlite3", "file"):
        raise ValueError("SQLite backend has been removed; PostgreSQL is the only supported backend.")
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
    auto_start_podman: bool = True
    selection_source: str = "default"

    @property
    def active_target(self) -> str:
        return self.postgres_url

    @property
    def display_target(self) -> str:
        return redact_db_url(self.postgres_url)


def is_container_runtime(env: Optional[Mapping[str, str]] = None) -> bool:
    """Return True when running inside the SpaceInvasion Docker/Podman container stack."""
    src = env if env is not None else os.environ
    flag = str(src.get("SPACEINVASION_CONTAINER") or "").strip().lower()
    return flag in ("1", "true", "yes", "on")


def resolve_database_config(
    *,
    explicit_backend: Optional[str] = None,
    explicit_db_url: Optional[str] = None,
    root_dir: Path | str = Path("."),
    env: Optional[Mapping[str, str]] = None,
) -> ResolvedDatabaseConfig:
    """Resolve PostgreSQL database configuration for container or host execution."""
    _ = root_dir
    src_env = env if env is not None else os.environ
    in_container = is_container_runtime(src_env)

    env_url = str(src_env.get("SPACEINVASION_DB_URL") or "").strip()
    if explicit_db_url and is_postgres_dsn(explicit_db_url):
        pg_url = str(explicit_db_url).strip()
    elif is_postgres_dsn(env_url):
        pg_url = env_url
    else:
        pg_url = build_default_postgres_url(src_env)

    auto_podman = not in_container
    env_auto = str(src_env.get("SPACEINVASION_AUTO_PODMAN") or "").strip().lower()
    if env_auto in ("0", "false", "no", "off"):
        auto_podman = False
    elif env_auto in ("1", "true", "yes", "on"):
        auto_podman = True

    if explicit_backend is not None and str(explicit_backend).strip().lower() not in ("", "auto"):
        norm_b = normalize_backend_name(explicit_backend, DEFAULT_DB_BACKEND)
        return ResolvedDatabaseConfig(
            backend=norm_b,
            postgres_url=pg_url,
            auto_start_podman=auto_podman,
            selection_source="container_postgres_only" if in_container else "cli_flag",
        )

    if in_container:
        return ResolvedDatabaseConfig(
            backend="postgres",
            postgres_url=pg_url,
            auto_start_podman=False,
            selection_source="container_postgres_only",
        )

    if explicit_db_url and is_postgres_dsn(explicit_db_url):
        return ResolvedDatabaseConfig(
            backend="postgres",
            postgres_url=pg_url,
            auto_start_podman=auto_podman,
            selection_source="cli_db_url",
        )

    env_backend = str(src_env.get("SPACEINVASION_DB_BACKEND") or "").strip().lower()
    if env_backend and env_backend != "auto":
        return ResolvedDatabaseConfig(
            backend=normalize_backend_name(env_backend, DEFAULT_DB_BACKEND),
            postgres_url=pg_url,
            auto_start_podman=auto_podman,
            selection_source="env_var",
        )
    if is_postgres_dsn(env_url):
        return ResolvedDatabaseConfig(
            backend="postgres",
            postgres_url=pg_url,
            auto_start_podman=auto_podman,
            selection_source="env_db_url",
        )

    return ResolvedDatabaseConfig(
        backend="postgres",
        postgres_url=pg_url,
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


def create_postgres_backup(
    db: "GameDatabase",
    *,
    label: str = "manual",
    backup_dir: Optional[Path] = None,
) -> Path:
    """Create a non-destructive PostgreSQL snapshot in `data/backups/` (`pg_dump` or gzipped JSONL)."""
    dsn = getattr(db, "postgres_url", "") or ""
    sock_host = get_unix_socket_host(dsn)
    default_dir = (Path(sock_host).parent / "backups") if sock_host is not None else (Path("data") / "backups")
    out_dir = backup_dir if backup_dir is not None else default_dir
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
                    [
                        podman_bin,
                        "exec",
                        DEFAULT_PODMAN_CONTAINER_NAME,
                        "pg_dump",
                        "-U",
                        pg_user,
                        "-d",
                        pg_db,
                        "-Fc",
                    ],
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
