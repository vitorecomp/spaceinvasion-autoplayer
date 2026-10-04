"""Hermetic test guards and disposable Unix-socket PostgreSQL 16 test harness.

Provides:
1. Hermetic network & filesystem guards (`install_hermetic_guards`) usable from both
   `tests/conftest.py` (pytest) and `tests/__init__.py` (direct `unittest` runs):
   - Blocks TCP connections to live host/container ports `{8787, 5433, 9222, 7900, 5173}`
     on loopback/wildcard addresses while permitting `AF_UNIX` sockets and ephemeral
     loopback ports spawned by `E2ETestHarness`.
   - Redirects default/repo-root log artifacts so `commander.jsonl` / `commander.log`
     are never written into the repository root.
   - Snapshots and verifies repository-root cleanliness on teardown.
2. Disposable Unix-socket PostgreSQL 16 container (`DisposablePostgresServer`) launched via
   Podman with `--network=none --userns=keep-id` and `listen_addresses=''`, migrating a
   `si_template` database once per process and cloning per-test databases via
   `CREATE DATABASE test_<n> TEMPLATE si_template`.
3. `PostgresTestRedirector` routing storage, bridge, and e2e tests onto disposable
   PostgreSQL through `GameDatabase` while keeping explicit SQLite / `:memory:` tests intact.
"""

from __future__ import annotations

import atexit
import contextlib
import os
from pathlib import Path
import re
import shutil
import socket
import sqlite3
import subprocess
import tempfile
import threading
import time
from typing import Any, Iterator, Optional
import uuid
import weakref

import psycopg
from psycopg.conninfo import conninfo_to_dict
import psycopg_pool

REPO_ROOT: Path = Path(__file__).resolve().parents[1]
FORBIDDEN_LIVE_PORTS: frozenset[int] = frozenset({8787, 5433, 9222, 7900, 5173})
_HERMETIC_SCRUB_ENV_KEYS: tuple[str, ...] = (
    "SPACEINVASION_DB_URL",
    "SPACEINVASION_DB_BACKEND",
    "SPACEINVASION_PG_HOST",
    "SPACEINVASION_PG_PORT",
    "SPACEINVASION_PG_DB",
    "SPACEINVASION_PG_USER",
    "SPACEINVASION_PG_PASSWORD",
    "SPACEINVASION_CONTAINER",
    "SPACEINVASION_AUTO_PODMAN",
    "DATABASE_URL",
    "PGHOST",
    "PGHOSTADDR",
    "PGPORT",
    "PGDATABASE",
    "PGUSER",
    "PGPASSWORD",
    "PGSERVICE",
    "PGPASSFILE",
)
_ALLOWED_TOOL_ROOT_ENTRIES: frozenset[str] = frozenset(
    {".pytest_cache", "__pycache__", ".coverage", ".mypy_cache", ".ruff_cache"}
)
_SQLITE_HEADER = b"SQLite format 3\x00"
_SAFE_DB_PREFIX_RE = re.compile(r"[^a-z0-9_]+")

_GUARD_LOCK = threading.Lock()
_SOCKET_GUARD_INSTALLED = False
_LIBPQ_GUARD_INSTALLED = False
_LOG_GUARD_INSTALLED = False
_PREF_GUARD_INSTALLED = False
_HERMETIC_TMP_DIR: Optional[Path] = None

_ORIG_SOCK_CONNECT = socket.socket.connect
_ORIG_SOCK_CONNECT_EX = socket.socket.connect_ex
_ORIG_CREATE_CONNECTION = socket.create_connection
_ORIG_SQLITE_CONNECT = sqlite3.connect
_ORIG_PSYCOPG_CONNECT = psycopg.connect
_ORIG_PSYCOPG_CONN_CONNECT = psycopg.Connection.connect.__func__  # type: ignore[attr-defined]
_ORIG_PSYCOPG_ASYNC_CONNECT = psycopg.AsyncConnection.connect.__func__  # type: ignore[attr-defined]
_ORIG_POOL_INIT = psycopg_pool.ConnectionPool.__init__
_ORIG_ASYNC_POOL_INIT = psycopg_pool.AsyncConnectionPool.__init__


def _ensure_hermetic_tmp_dir() -> Path:
    """Create (once per process) a temporary directory for redirected test artifacts."""
    global _HERMETIC_TMP_DIR
    if _HERMETIC_TMP_DIR is None:
        _HERMETIC_TMP_DIR = Path(tempfile.mkdtemp(prefix="si_hermetic_logs_"))
        atexit.register(_cleanup_hermetic_tmp)
    return _HERMETIC_TMP_DIR


def _is_forbidden_tcp_target(family: int, address: Any) -> bool:
    """Return True if `(family, address)` targets a forbidden live TCP port on any host."""
    if family == getattr(socket, "AF_UNIX", -1):
        return False
    if family not in (socket.AF_INET, socket.AF_INET6, socket.AF_UNSPEC):
        return False
    if not isinstance(address, tuple) or len(address) < 2:
        return False
    port_raw = address[1]
    if isinstance(port_raw, bool):
        return False
    try:
        port = int(port_raw)
    except (TypeError, ValueError):
        return False
    return port in FORBIDDEN_LIVE_PORTS


def _assert_hermetic_libpq_dsn(conninfo: Any = "", extra_kwargs: Optional[dict[str, Any]] = None) -> None:
    """Reject any `psycopg`/`libpq` connection not targeting a temp-dir Unix domain socket."""
    raw_ci = "" if conninfo is None else str(conninfo)
    try:
        parsed: dict[str, Any] = dict(conninfo_to_dict(raw_ci))
    except Exception as exc:
        raise RuntimeError(f"Hermetic test guard blocked unparseable libpq conninfo: {exc}") from exc

    if extra_kwargs:
        for key in ("host", "hostaddr", "port", "service"):
            if key in extra_kwargs and extra_kwargs[key] is not None:
                parsed[key] = extra_kwargs[key]

    service = str(parsed.get("service") or os.environ.get("PGSERVICE") or "").strip()
    hostaddr = str(parsed.get("hostaddr") or os.environ.get("PGHOSTADDR") or "").strip()
    host = str(parsed.get("host") or os.environ.get("PGHOST") or "").strip()
    port = str(parsed.get("port") or os.environ.get("PGPORT") or "5432").strip()

    tmp_roots = {
        os.path.realpath(tempfile.gettempdir()).rstrip("/"),
        os.path.normpath(tempfile.gettempdir()).rstrip("/"),
        "/tmp",
    }
    host_norm = os.path.normpath(host) if host else ""
    host_real = os.path.realpath(host) if host else ""
    in_tmp = bool(
        host
        and host.startswith("/")
        and "," not in host
        and any(host_norm.startswith(r + "/") for r in tmp_roots)
        and any(host_real.startswith(r + "/") for r in tmp_roots)
    )
    if service or hostaddr or not in_tmp:
        raise RuntimeError(
            "Hermetic test guard blocked libpq PostgreSQL connection "
            f"(host={host!r}, hostaddr={hostaddr!r}, port={port!r}, service={service!r}); "
            "only Unix-socket DSNs under a temp directory are allowed in tests."
        )


def install_libpq_guard() -> None:
    """Wrap `psycopg` and `psycopg_pool` entrypoints so C-level libpq cannot reach TCP/live Postgres."""
    global _LIBPQ_GUARD_INSTALLED
    with _GUARD_LOCK:
        if _LIBPQ_GUARD_INSTALLED:
            return

        def _guarded_psycopg_connect(conninfo: Any = "", *args: Any, **kwargs: Any) -> Any:
            _assert_hermetic_libpq_dsn(conninfo, kwargs)
            return _ORIG_PSYCOPG_CONNECT(conninfo, *args, **kwargs)

        def _guarded_conn_connect(cls: Any, conninfo: Any = "", *args: Any, **kwargs: Any) -> Any:
            _assert_hermetic_libpq_dsn(conninfo, kwargs)
            return _ORIG_PSYCOPG_CONN_CONNECT(cls, conninfo, *args, **kwargs)

        async def _guarded_async_connect(cls: Any, conninfo: Any = "", *args: Any, **kwargs: Any) -> Any:
            _assert_hermetic_libpq_dsn(conninfo, kwargs)
            return await _ORIG_PSYCOPG_ASYNC_CONNECT(cls, conninfo, *args, **kwargs)

        def _guarded_pool_init(self: Any, conninfo: Any = "", *args: Any, **kwargs: Any) -> None:
            pool_kw = kwargs.get("kwargs")
            _assert_hermetic_libpq_dsn(conninfo, pool_kw if isinstance(pool_kw, dict) else None)
            _ORIG_POOL_INIT(self, conninfo, *args, **kwargs)

        def _guarded_async_pool_init(self: Any, conninfo: Any = "", *args: Any, **kwargs: Any) -> None:
            pool_kw = kwargs.get("kwargs")
            _assert_hermetic_libpq_dsn(conninfo, pool_kw if isinstance(pool_kw, dict) else None)
            _ORIG_ASYNC_POOL_INIT(self, conninfo, *args, **kwargs)

        psycopg.connect = _guarded_psycopg_connect  # type: ignore[assignment]
        psycopg.Connection.connect = classmethod(_guarded_conn_connect)  # type: ignore[assignment,method-assign]
        psycopg.AsyncConnection.connect = classmethod(_guarded_async_connect)  # type: ignore[assignment,method-assign]
        psycopg_pool.ConnectionPool.__init__ = _guarded_pool_init  # type: ignore[assignment,method-assign]
        psycopg_pool.AsyncConnectionPool.__init__ = _guarded_async_pool_init  # type: ignore[assignment,method-assign]
        _LIBPQ_GUARD_INSTALLED = True


def install_socket_guard() -> None:
    """Monkeypatch `socket` connect entrypoints to reject TCP connections to live stack ports."""
    global _SOCKET_GUARD_INSTALLED
    with _GUARD_LOCK:
        if _SOCKET_GUARD_INSTALLED:
            return

        def _guarded_connect(self: socket.socket, address: Any) -> Any:
            if _is_forbidden_tcp_target(self.family, address):
                raise RuntimeError(
                    f"Hermetic test guard blocked TCP connect() to live port {address!r} "
                    f"(forbidden ports: {sorted(FORBIDDEN_LIVE_PORTS)})"
                )
            return _ORIG_SOCK_CONNECT(self, address)

        def _guarded_connect_ex(self: socket.socket, address: Any) -> int:
            if _is_forbidden_tcp_target(self.family, address):
                raise RuntimeError(
                    f"Hermetic test guard blocked TCP connect_ex() to live port {address!r} "
                    f"(forbidden ports: {sorted(FORBIDDEN_LIVE_PORTS)})"
                )
            return _ORIG_SOCK_CONNECT_EX(self, address)

        def _guarded_create_connection(address: tuple[Any, Any], *args: Any, **kwargs: Any) -> socket.socket:
            if _is_forbidden_tcp_target(socket.AF_UNSPEC, address):
                raise RuntimeError(
                    f"Hermetic test guard blocked TCP create_connection() to live port {address!r} "
                    f"(forbidden ports: {sorted(FORBIDDEN_LIVE_PORTS)})"
                )
            return _ORIG_CREATE_CONNECTION(address, *args, **kwargs)

        socket.socket.connect = _guarded_connect  # type: ignore[assignment,method-assign]
        socket.socket.connect_ex = _guarded_connect_ex  # type: ignore[assignment,method-assign]
        socket.create_connection = _guarded_create_connection
        _SOCKET_GUARD_INSTALLED = True


def _cleanup_hermetic_tmp() -> None:
    global _HERMETIC_TMP_DIR
    tmp = _HERMETIC_TMP_DIR
    _HERMETIC_TMP_DIR = None
    if tmp is not None:
        shutil.rmtree(tmp, ignore_errors=True)


def install_log_artifact_guard() -> None:
    """Prevent tests from writing `commander.jsonl`, `commander.log`, or crash logs into repo root."""
    global _LOG_GUARD_INSTALLED
    with _GUARD_LOCK:
        if _LOG_GUARD_INSTALLED:
            return
        from spaceinvasion.core import logging_utils

        hermetic_tmp = _ensure_hermetic_tmp_dir()
        repo_root_resolved = REPO_ROOT.resolve()
        repo_logs_resolved = (REPO_ROOT / "logs").resolve()
        repo_state_resolved = (REPO_ROOT / "state").resolve()
        orig_skip = logging_utils._should_skip_disk_log

        def _hermetic_should_skip_disk_log(target_dir: Optional[Path]) -> bool:
            if target_dir is None:
                return True
            try:
                raw_s = str(target_dir).strip()
            except Exception:
                return True
            if raw_s in ("", "."):
                return True
            try:
                resolved = Path(target_dir).resolve()
            except OSError:
                return True
            if resolved in (repo_root_resolved, repo_logs_resolved, repo_state_resolved):
                return True
            return orig_skip(target_dir)

        logging_utils._should_skip_disk_log = _hermetic_should_skip_disk_log
        logging_utils.DEFAULT_CRASH_LOG_DIR = (hermetic_tmp / "crash_logs").resolve()
        _LOG_GUARD_INSTALLED = True


def scrub_live_db_env() -> None:
    """Remove inherited live DB / libpq environment variables from `os.environ`."""
    for key in _HERMETIC_SCRUB_ENV_KEYS:
        os.environ.pop(key, None)


def install_backend_pref_guard() -> None:
    """Redirect repo-root `state/db_backend.json` lookups into the hermetic temp directory."""
    global _PREF_GUARD_INSTALLED
    with _GUARD_LOCK:
        if _PREF_GUARD_INSTALLED:
            return
        from spaceinvasion.db import backend as backend_mod

        hermetic_tmp = _ensure_hermetic_tmp_dir()
        repo_root_resolved = REPO_ROOT.resolve()
        orig_pref_path = backend_mod._pref_file_path

        def _hermetic_pref_file_path(root_dir: Path | str = Path(".")) -> Path:
            try:
                resolved = Path(root_dir).resolve()
            except OSError:
                return hermetic_tmp / backend_mod.DEFAULT_BACKEND_PREF_REL_PATH
            if resolved == repo_root_resolved:
                return hermetic_tmp / backend_mod.DEFAULT_BACKEND_PREF_REL_PATH
            return orig_pref_path(root_dir)

        backend_mod._pref_file_path = _hermetic_pref_file_path
        _PREF_GUARD_INSTALLED = True


def install_hermetic_guards() -> None:
    """Install socket, libpq, env-scrub, backend-pref, and log-artifact hermetic guards."""
    scrub_live_db_env()
    install_socket_guard()
    install_libpq_guard()
    install_log_artifact_guard()
    install_backend_pref_guard()


def snapshot_repo_root() -> frozenset[str]:
    """Return the set of top-level entry names currently in the repository root."""
    return frozenset(p.name for p in REPO_ROOT.iterdir())


def find_unexpected_repo_root_files(baseline: frozenset[str]) -> list[str]:
    """Return sorted top-level entries that appeared in `REPO_ROOT` after `baseline`."""
    current = snapshot_repo_root()
    added = current - baseline - _ALLOWED_TOOL_ROOT_ENTRIES
    return sorted(added)


def assert_repo_root_clean(baseline: frozenset[str], *, context: str = "test teardown") -> None:
    """Raise `AssertionError` if unexpected files appeared in the repository root."""
    unexpected = find_unexpected_repo_root_files(baseline)
    if unexpected:
        raise AssertionError(
            f"Hermetic filesystem guard failed at {context}: unexpected entries in repository root "
            f"{REPO_ROOT}: {unexpected}"
        )


def _select_postgres_image(podman_bin: str) -> str:
    """Pick the local `postgres:16-alpine` image without pulling from the network."""
    for candidate in ("docker.io/library/postgres:16-alpine", "postgres:16-alpine"):
        chk = subprocess.run(
            [podman_bin, "image", "exists", candidate],
            capture_output=True,
            timeout=5.0,
            check=False,
        )
        if chk.returncode == 0:
            return candidate
    raise RuntimeError(
        "Local Podman image `postgres:16-alpine` is required for the disposable PostgreSQL test harness."
    )


class DisposablePostgresServer:
    """Throwaway Unix-socket-only PostgreSQL 16 server running in an isolated Podman container.

    Guarantees:
    - `--network=none` and `listen_addresses=''`: cannot bind TCP or contact live Postgres on `5433`.
    - Migrates `si_template` once via `GameDatabase`, then clones per-test databases via
      `CREATE DATABASE test_<n> TEMPLATE si_template`.
    - Cleans up its container (`podman rm -f`) and temp directory on `stop()` and at process exit.
    """

    TEMPLATE_DB = "si_template"

    def __init__(self, *, start_timeout: float = 35.0) -> None:
        self._start_timeout = max(10.0, float(start_timeout))
        self._lock = threading.Lock()
        self._podman_bin: Optional[str] = None
        self._container_id: Optional[str] = None
        self._container_name: Optional[str] = None
        self._tmp_dir: Optional[Path] = None
        self._pgdata_dir: Optional[Path] = None
        self._socket_dir: Optional[Path] = None
        self._started = False
        self._stopped = False
        self._counter = 0
        self._created_dbs: list[str] = []
        self._admin_conn: Optional[psycopg.Connection[Any]] = None

    @property
    def container_id(self) -> Optional[str]:
        return self._container_id

    @property
    def container_name(self) -> Optional[str]:
        return self._container_name

    @property
    def socket_dir(self) -> Path:
        if self._socket_dir is None:
            raise RuntimeError("DisposablePostgresServer has not been started.")
        return self._socket_dir

    @property
    def is_running(self) -> bool:
        return self._started and not self._stopped and self._container_id is not None

    def dsn_for(self, db_name: str) -> str:
        """Build a Unix-socket PostgreSQL DSN (`postgresql:///<db>?host=<sock>&user=postgres`)."""
        return f"postgresql:///{db_name}?host={self.socket_dir}&user=postgres"

    @property
    def admin_dsn(self) -> str:
        return self.dsn_for("postgres")

    @property
    def template_dsn(self) -> str:
        return self.dsn_for(self.TEMPLATE_DB)

    def start(self) -> "DisposablePostgresServer":
        with self._lock:
            if self._started and not self._stopped:
                return self
            podman_bin = shutil.which("podman")
            if not podman_bin:
                raise RuntimeError("`podman` binary not found; cannot start disposable PostgreSQL server.")
            image = _select_postgres_image(podman_bin)

            tmp_dir = Path(tempfile.mkdtemp(prefix="si_pg_harness_"))
            pgdata_dir = tmp_dir / "pgdata"
            socket_dir = tmp_dir / "pgsock"
            pgdata_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
            socket_dir.mkdir(mode=0o755, parents=True, exist_ok=True)

            cname = f"si_test_pg_{os.getpid()}_{uuid.uuid4().hex[:8]}"
            cmd = [
                podman_bin,
                "run",
                "-d",
                "--rm",
                "--name",
                cname,
                "--label",
                "spaceinvasion.test_harness=1",
                "--network=none",
                "--userns=keep-id",
                "-e",
                "POSTGRES_HOST_AUTH_METHOD=trust",
                "-e",
                "POSTGRES_USER=postgres",
                "-e",
                "PGDATA=/var/lib/postgresql/data/pg",
                "-v",
                f"{pgdata_dir}:/var/lib/postgresql/data",
                "-v",
                f"{socket_dir}:/var/run/postgresql",
                image,
                "postgres",
                "-c",
                "listen_addresses=",
                "-c",
                "unix_socket_directories=/var/run/postgresql",
                "-c",
                "fsync=off",
                "-c",
                "synchronous_commit=off",
                "-c",
                "full_page_writes=off",
                "-c",
                "max_connections=300",
                "-c",
                "shared_buffers=64MB",
                "-c",
                "checkpoint_timeout=30min",
            ]
            self._podman_bin = podman_bin
            self._tmp_dir = tmp_dir
            self._pgdata_dir = pgdata_dir
            self._socket_dir = socket_dir
            self._container_name = cname
            self._stopped = False

            try:
                proc = subprocess.run(
                    cmd,
                    capture_output=True,
                    text=True,
                    timeout=30.0,
                    check=False,
                )
                if proc.returncode != 0:
                    raise RuntimeError(
                        f"Failed to start disposable Postgres container: {(proc.stderr or proc.stdout).strip()}"
                    )
                self._container_id = proc.stdout.strip()
                atexit.register(self.stop)
                self._wait_until_ready()
                self._build_template_database()
                self._started = True
            except Exception:
                self._stop_unlocked()
                raise
            return self

    def _wait_until_ready(self) -> None:
        assert self._podman_bin is not None and self._container_id is not None and self._socket_dir is not None
        deadline = time.monotonic() + self._start_timeout
        sock_file = self._socket_dir / ".s.PGSQL.5432"
        init_complete = False
        last_err = ""

        while time.monotonic() < deadline:
            if not init_complete:
                logs_proc = subprocess.run(
                    [self._podman_bin, "logs", self._container_id],
                    capture_output=True,
                    text=True,
                    timeout=5.0,
                    check=False,
                )
                combined_logs = f"{logs_proc.stdout}\n{logs_proc.stderr}"
                if "PostgreSQL init process complete" in combined_logs:
                    init_complete = True
                else:
                    # Verify container is still running
                    st_proc = subprocess.run(
                        [self._podman_bin, "inspect", "-f", "{{.State.Running}}", self._container_id],
                        capture_output=True,
                        text=True,
                        timeout=5.0,
                        check=False,
                    )
                    if st_proc.returncode != 0 or st_proc.stdout.strip().lower() != "true":
                        raise RuntimeError(
                            f"Disposable Postgres container exited prematurely:\n{combined_logs[-2000:]}"
                        )
                    time.sleep(0.1)
                    continue

            if not sock_file.exists():
                time.sleep(0.05)
                continue

            try:
                with psycopg.connect(self.admin_dsn, autocommit=True, connect_timeout=2) as conn:
                    row = conn.execute("SHOW listen_addresses").fetchone()
                    listen_addr = str(row[0]) if row and row[0] is not None else ""
                    if listen_addr != "":
                        raise RuntimeError(
                            f"Disposable Postgres must not listen on TCP, got listen_addresses={listen_addr!r}"
                        )
                    return
            except RuntimeError:
                raise
            except Exception as exc:
                last_err = str(exc)
                time.sleep(0.1)

        logs_tail = ""
        with contextlib.suppress(Exception):
            lp = subprocess.run(
                [self._podman_bin, "logs", self._container_id],
                capture_output=True,
                text=True,
                timeout=5.0,
                check=False,
            )
            logs_tail = (lp.stdout + "\n" + lp.stderr)[-2000:]
        raise RuntimeError(
            f"Disposable Postgres server did not become ready within {self._start_timeout:.1f}s "
            f"(last_err={last_err!r}).\n{logs_tail}"
        )

    def _build_template_database(self) -> None:
        from spaceinvasion.db.storage import GameDatabase

        with psycopg.connect(self.admin_dsn, autocommit=True) as conn:
            conn.execute(f'DROP DATABASE IF EXISTS "{self.TEMPLATE_DB}"')
            conn.execute(f'CREATE DATABASE "{self.TEMPLATE_DB}"')

        tpl_db = GameDatabase(
            self.template_dsn,
            backend="postgres",
            pool_min_size=1,
            pool_max_size=2,
            attack_history_path="",
        )
        try:
            ver = tpl_db.verify_schema(full_integrity=False)
            if not ver.get("ok"):
                raise RuntimeError(f"si_template schema verification failed: {ver}")
        finally:
            tpl_db.close()

        # Ensure zero active connections linger on si_template before cloning
        with psycopg.connect(self.admin_dsn, autocommit=True) as conn:
            conn.execute(
                """
                SELECT pg_terminate_backend(pid)
                FROM pg_stat_activity
                WHERE datname = %s AND pid <> pg_backend_pid()
                """,
                (self.TEMPLATE_DB,),
            )

    def _get_admin_conn_unlocked(self) -> psycopg.Connection[Any]:
        if self._admin_conn is not None and not self._admin_conn.closed:
            return self._admin_conn
        self._admin_conn = psycopg.connect(self.admin_dsn, autocommit=True, connect_timeout=5)
        return self._admin_conn

    def create_database(self, prefix: str = "test", *, from_template: bool = True) -> str:
        """Create a fresh isolated database (fast template clone by default) and return its Unix-socket DSN."""
        if not self.is_running:
            self.start()
        safe_prefix = _SAFE_DB_PREFIX_RE.sub("_", str(prefix or "test").strip().lower()).strip("_") or "test"
        with self._lock:
            self._counter += 1
            db_name = f"{safe_prefix[:24]}_{self._counter}"
            conn = self._get_admin_conn_unlocked()
            if from_template:
                conn.execute(f'CREATE DATABASE "{db_name}" TEMPLATE "{self.TEMPLATE_DB}"')
            else:
                conn.execute(f'CREATE DATABASE "{db_name}"')
            self._created_dbs.append(db_name)
            return self.dsn_for(db_name)

    def drop_database(self, db_name: str) -> None:
        """Terminate connections and drop a test database on the disposable server."""
        if not self.is_running:
            return
        safe_name = str(db_name).strip().strip('"')
        if not safe_name or safe_name in ("postgres", "template0", "template1", self.TEMPLATE_DB):
            return
        with self._lock:
            with contextlib.suppress(Exception):
                conn = self._get_admin_conn_unlocked()
                conn.execute(
                    """
                    SELECT pg_terminate_backend(pid)
                    FROM pg_stat_activity
                    WHERE datname = %s AND pid <> pg_backend_pid()
                    """,
                    (safe_name,),
                )
                conn.execute(f'DROP DATABASE IF EXISTS "{safe_name}"')

    def _stop_unlocked(self) -> None:
        if self._stopped and self._container_id is None and self._tmp_dir is None:
            return
        self._stopped = True
        self._started = False
        admin_conn = self._admin_conn
        self._admin_conn = None
        if admin_conn is not None:
            with contextlib.suppress(Exception):
                admin_conn.close()
        cid = self._container_id
        podman_bin = self._podman_bin
        tmp_dir = self._tmp_dir
        self._container_id = None
        self._tmp_dir = None
        self._pgdata_dir = None
        self._socket_dir = None
        if podman_bin and cid:
            with contextlib.suppress(Exception):
                subprocess.run(
                    [podman_bin, "rm", "-f", cid],
                    capture_output=True,
                    timeout=15.0,
                    check=False,
                )
        if tmp_dir is not None:
            shutil.rmtree(tmp_dir, ignore_errors=True)

    def stop(self) -> None:
        with self._lock:
            self._stop_unlocked()


_SHARED_HARNESS_LOCK = threading.Lock()
_SHARED_HARNESS: Optional[DisposablePostgresServer] = None


def get_shared_pg_harness() -> DisposablePostgresServer:
    """Return the process-wide shared disposable PostgreSQL server, starting it lazily."""
    global _SHARED_HARNESS
    with _SHARED_HARNESS_LOCK:
        if _SHARED_HARNESS is None or not _SHARED_HARNESS.is_running:
            _SHARED_HARNESS = DisposablePostgresServer().start()
        return _SHARED_HARNESS


def shutdown_shared_pg_harness() -> None:
    """Stop and clean up the process-wide shared disposable PostgreSQL server if running."""
    global _SHARED_HARNESS
    with _SHARED_HARNESS_LOCK:
        harness = _SHARED_HARNESS
        _SHARED_HARNESS = None
    if harness is not None:
        harness.stop()


class _SqliteCompatPostgresConn:
    """Context-manager wrapper over a `PostgresPoolBackend` for tests calling `sqlite3.connect(h.db_path)`."""

    def __init__(self, dsn: str) -> None:
        from spaceinvasion.db.postgres import PostgresPoolBackend

        self._backend = PostgresPoolBackend(dsn, min_size=1, max_size=2)
        self._proxy = self._backend.conn_proxy

    @property
    def row_factory(self) -> Any:
        return self._proxy.row_factory

    @row_factory.setter
    def row_factory(self, val: Any) -> None:
        self._proxy.row_factory = val

    def cursor(self) -> Any:
        return self._proxy.cursor()

    def execute(self, sql: str, params: Any = None) -> Any:
        return self._proxy.execute(sql, params)

    def executemany(self, sql: str, seq_of_params: Any) -> Any:
        return self._proxy.executemany(sql, seq_of_params)

    def executescript(self, script: str) -> Any:
        return self._proxy.executescript(script)

    def commit(self) -> None:
        self._proxy.commit()

    def rollback(self) -> None:
        self._proxy.rollback()

    def close(self) -> None:
        self._backend.close()

    def __enter__(self) -> "_SqliteCompatPostgresConn":
        return self

    def __exit__(self, exc_type: Any, _exc_val: Any, _exc_tb: Any) -> None:
        if exc_type is not None:
            self.rollback()
        else:
            self.commit()
        self.close()


def _is_existing_sqlite_file(path: Path) -> bool:
    try:
        if not path.is_file() or path.stat().st_size < len(_SQLITE_HEADER):
            return False
        with path.open("rb") as fh:
            return fh.read(len(_SQLITE_HEADER)) == _SQLITE_HEADER
    except OSError:
        return False


def _resolve_db_key(raw: Any) -> Optional[str]:
    try:
        if isinstance(raw, Path):
            txt = str(raw)
        elif isinstance(raw, str):
            txt = raw
        elif isinstance(raw, os.PathLike):
            fsp = os.fspath(raw)
            txt = os.fsdecode(fsp) if isinstance(fsp, (str, bytes)) else str(fsp)
        else:
            return None
        s = txt.strip()
        if not s or ":memory:" in s or "mode=memory" in s or s.startswith("file:"):
            return None
        if s.lower().startswith(("postgresql://", "postgres://")):
            return None
        return str(Path(s).resolve())
    except Exception:
        return None


def _touch_pg_marker_if_outside_repo(path: Path, dsn: str) -> None:
    """Create a non-SQLite marker file for temp test paths so `h.db_path.exists()` stays True."""
    try:
        resolved = path.resolve()
        repo_resolved = REPO_ROOT.resolve()
        if resolved == repo_resolved or repo_resolved in resolved.parents:
            return
        if not resolved.exists():
            resolved.parent.mkdir(parents=True, exist_ok=True)
            resolved.write_text(f"# pg-harness:{dsn}\n", encoding="utf-8")
    except OSError:
        pass


class PostgresTestRedirector:
    """Redirects file-backed `GameDatabase` instances in tests onto disposable PostgreSQL.

    Preserves explicit SQLite / `:memory:` / pre-existing SQLite file tests while routing
    storage, bridge, config, and e2e tests onto isolated per-test cloned PostgreSQL databases.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._tls = threading.local()
        self._installed = False
        self._enabled = False
        self._path_to_dsn: dict[str, str] = {}
        self._open_dbs: list[weakref.ref[Any]] = []
        self.redirected_count: int = 0
        self._orig_db_init: Any = None
        self._orig_default_db_path: Optional[Path] = None
        self._orig_load_cfg: Any = None
        self._orig_save_cfg: Any = None
        self._orig_load_rank_history: Any = None

    @property
    def enabled(self) -> bool:
        return self._enabled

    def _sqlite_allowed_on_thread(self) -> bool:
        return int(getattr(self._tls, "allow_sqlite_depth", 0)) > 0

    @contextlib.contextmanager
    def allow_sqlite(self) -> Iterator[None]:
        depth = int(getattr(self._tls, "allow_sqlite_depth", 0))
        self._tls.allow_sqlite_depth = depth + 1
        try:
            yield
        finally:
            self._tls.allow_sqlite_depth = depth

    def lookup_mapped_dsn(self, path: Any) -> Optional[str]:
        key = _resolve_db_key(path)
        if key is None:
            return None
        with self._lock:
            return self._path_to_dsn.get(key)

    def install(self) -> None:
        with self._lock:
            if self._installed:
                return
            from spaceinvasion.combat import ranking as ranking_mod
            from spaceinvasion.core import config as config_mod
            from spaceinvasion.db import backend as backend_mod
            from spaceinvasion.db import storage as storage_mod

            self._orig_db_init = storage_mod.GameDatabase.__init__
            self._orig_default_db_path = storage_mod.DEFAULT_DB_PATH
            self._orig_load_cfg = config_mod.load_strategy_config
            self._orig_save_cfg = config_mod.save_strategy_config
            self._orig_load_rank_history = ranking_mod.load_rank_history
            redirector = self

            def _wrapped_sqlite_connect(database: Any, *args: Any, **kwargs: Any) -> Any:
                if redirector._enabled and not redirector._sqlite_allowed_on_thread():
                    mapped = redirector.lookup_mapped_dsn(database)
                    if mapped is not None:
                        return _SqliteCompatPostgresConn(mapped)
                return _ORIG_SQLITE_CONNECT(database, *args, **kwargs)

            def _wrapped_db_init(
                db_self: Any,
                db_path: Any = storage_mod.DEFAULT_DB_PATH,
                *,
                backend: Optional[str] = None,
                postgres_url: Optional[str] = None,
                pool_min_size: int = 2,
                pool_max_size: int = 16,
                legacy_rank_json: Optional[Path] = None,
                attack_history_path: Optional[Path | str] = None,
                target_schema_version: Optional[int] = None,
                backup_on_upgrade: Optional[bool] = None,
            ) -> None:
                use_pg_redirect = (
                    redirector._enabled
                    and not redirector._sqlite_allowed_on_thread()
                    and backend is None
                    and postgres_url is None
                    and storage_mod.sqlite3.connect in (_ORIG_SQLITE_CONNECT, _wrapped_sqlite_connect)
                    and storage_mod.DEFAULT_DB_PATH == redirector._orig_default_db_path
                )
                key = _resolve_db_key(db_path) if use_pg_redirect else None
                if use_pg_redirect and key is not None:
                    with redirector._lock:
                        mapped_dsn = redirector._path_to_dsn.get(key)
                    if mapped_dsn is None and _is_existing_sqlite_file(Path(key)):
                        use_pg_redirect = False
                    else:
                        if mapped_dsn is None:
                            harness = get_shared_pg_harness()
                            from_tpl = target_schema_version is None
                            new_dsn = harness.create_database(
                                prefix="staged" if not from_tpl else "test",
                                from_template=from_tpl,
                            )
                            with redirector._lock:
                                mapped_dsn = redirector._path_to_dsn.setdefault(key, new_dsn)
                        _touch_pg_marker_if_outside_repo(Path(key), mapped_dsn)
                        redirector._orig_db_init(
                            db_self,
                            db_path,
                            backend="postgres",
                            postgres_url=mapped_dsn,
                            pool_min_size=min(max(1, int(pool_min_size)), 1),
                            pool_max_size=min(max(2, int(pool_max_size)), 6),
                            legacy_rank_json=legacy_rank_json,
                            attack_history_path=attack_history_path,
                            target_schema_version=target_schema_version,
                            backup_on_upgrade=backup_on_upgrade,
                        )
                        with redirector._lock:
                            redirector.redirected_count += 1
                            redirector._open_dbs.append(weakref.ref(db_self))
                        return

                redirector._orig_db_init(
                    db_self,
                    db_path,
                    backend=backend,
                    postgres_url=postgres_url,
                    pool_min_size=pool_min_size,
                    pool_max_size=pool_max_size,
                    legacy_rank_json=legacy_rank_json,
                    attack_history_path=attack_history_path,
                    target_schema_version=target_schema_version,
                    backup_on_upgrade=backup_on_upgrade,
                )
                if getattr(db_self, "_is_postgres", False):
                    with redirector._lock:
                        redirector._open_dbs.append(weakref.ref(db_self))

            def _wrapped_load_cfg(path: Any = None) -> Any:
                if redirector._enabled and path is not None:
                    mapped = redirector.lookup_mapped_dsn(path)
                    if mapped is not None:
                        return redirector._orig_load_cfg(mapped)
                return redirector._orig_load_cfg(path)

            def _wrapped_save_cfg(cfg: Any, path: Any = None) -> Any:
                if redirector._enabled and path is not None:
                    mapped = redirector.lookup_mapped_dsn(path)
                    if mapped is not None:
                        return redirector._orig_save_cfg(cfg, mapped)
                return redirector._orig_save_cfg(cfg, path)

            def _wrapped_load_rank_history(history_path: Any = None) -> list[dict[str, Any]]:
                if redirector._enabled and history_path is not None:
                    mapped = redirector.lookup_mapped_dsn(history_path)
                    if mapped is not None:
                        db = storage_mod.GameDatabase(
                            mapped,
                            backend="postgres",
                            pool_min_size=1,
                            pool_max_size=2,
                            attack_history_path="",
                        )
                        try:
                            return db.get_rank_samples()
                        finally:
                            db.close()
                return redirector._orig_load_rank_history(history_path)

            storage_mod.GameDatabase.__init__ = _wrapped_db_init  # type: ignore[assignment,method-assign]
            sqlite3.connect = _wrapped_sqlite_connect  # type: ignore[assignment]
            config_mod.load_strategy_config = _wrapped_load_cfg
            config_mod.save_strategy_config = _wrapped_save_cfg
            ranking_mod.load_rank_history = _wrapped_load_rank_history
            # Keep backend_mod referenced to ensure it's loaded before tests patch anything
            _ = backend_mod.is_unix_socket_dsn
            self._installed = True

    def enable(self) -> None:
        self.install()
        self._enabled = True

    def disable(self) -> None:
        self._enabled = False

    def reset_test_scope(self) -> None:
        """Close any lingering redirected `GameDatabase` pools and clear per-test path mappings."""
        with self._lock:
            refs = list(self._open_dbs)
            self._open_dbs.clear()
            self._path_to_dsn.clear()
        for ref in refs:
            db = ref()
            if db is not None:
                with contextlib.suppress(Exception):
                    db.close()


_REDIRECTOR = PostgresTestRedirector()


def get_pg_redirector() -> PostgresTestRedirector:
    return _REDIRECTOR


@contextlib.contextmanager
def allow_sqlite_for_test() -> Iterator[None]:
    """Context manager opting a specific block out of automatic PostgreSQL redirection."""
    with _REDIRECTOR.allow_sqlite():
        yield
