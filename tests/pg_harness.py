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
3. `PostgresTestRedirector` routing `GameDatabase` instances in tests onto disposable
   PostgreSQL (`DisposablePostgresServer`) databases.
"""

from __future__ import annotations

import atexit
from collections.abc import Sequence
import contextlib
import os
from pathlib import Path
import re
import shutil
import socket
import subprocess
import tempfile
import threading
import time
from typing import Any, Optional
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
_SAFE_DB_PREFIX_RE = re.compile(r"[^a-z0-9_]+")

_GUARD_LOCK = threading.Lock()
_SOCKET_GUARD_INSTALLED = False
_LIBPQ_GUARD_INSTALLED = False
_LOG_GUARD_INSTALLED = False
_HERMETIC_TMP_DIR: Optional[Path] = None

_ORIG_SOCK_CONNECT = socket.socket.connect
_ORIG_SOCK_CONNECT_EX = socket.socket.connect_ex
_ORIG_CREATE_CONNECTION = socket.create_connection
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
        from spaceinvasion.runtime import logs as logs_mod

        hermetic_tmp = _ensure_hermetic_tmp_dir()
        repo_root_resolved = REPO_ROOT.resolve()
        repo_logs_resolved = (REPO_ROOT / "logs").resolve()
        repo_state_resolved = (REPO_ROOT / "state").resolve()
        orig_skip = logs_mod._should_skip_disk_log

        def _hermetic_should_skip_disk_log(
            target_dir: Optional[Path],
            *,
            allow_default_disk_logs: bool = False,
        ) -> bool:
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
            return orig_skip(target_dir, allow_default_disk_logs=allow_default_disk_logs)

        logs_mod._should_skip_disk_log = _hermetic_should_skip_disk_log
        logs_mod.DEFAULT_CRASH_LOG_DIR = (hermetic_tmp / "crash_logs").resolve()
        _LOG_GUARD_INSTALLED = True


def scrub_live_db_env() -> None:
    """Remove inherited live DB / libpq environment variables from `os.environ`."""
    for key in _HERMETIC_SCRUB_ENV_KEYS:
        os.environ.pop(key, None)


def install_hermetic_guards() -> None:
    """Install socket, libpq, env-scrub, and log-artifact hermetic guards."""
    scrub_live_db_env()
    install_socket_guard()
    install_libpq_guard()
    install_log_artifact_guard()


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


_HARNESS_NAME_PID_RE = re.compile(r"^si_test_pg_(\d+)_[0-9a-f]+$")


def _is_pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False


def _reap_stale_harness_containers(podman_bin: str) -> list[str]:
    """Remove orphaned `spaceinvasion.test_harness=1` containers whose owner PID has exited."""
    reaped: list[str] = []
    ps_proc = subprocess.run(
        [podman_bin, "ps", "-a", "--filter", "label=spaceinvasion.test_harness=1", "--format", "{{.ID}}\t{{.Names}}"],
        capture_output=True,
        text=True,
        timeout=10.0,
        check=False,
    )
    if ps_proc.returncode != 0:
        return reaped
    tmp_root = Path(tempfile.gettempdir()).resolve()
    for raw_line in (ps_proc.stdout or "").splitlines():
        parts = raw_line.strip().split("\t", 1)
        if len(parts) != 2:
            continue
        cid, cname = parts[0].strip(), parts[1].strip()
        m_pid = _HARNESS_NAME_PID_RE.match(cname)
        if not cid or m_pid is None:
            continue
        owner_pid = int(m_pid.group(1))
        if _is_pid_alive(owner_pid):
            continue
        mount_dirs: list[Path] = []
        insp = subprocess.run(
            [podman_bin, "inspect", "-f", "{{range .Mounts}}{{.Source}}\n{{end}}", cid],
            capture_output=True,
            text=True,
            timeout=5.0,
            check=False,
        )
        if insp.returncode == 0:
            for msrc in (insp.stdout or "").splitlines():
                src = msrc.strip()
                if not src:
                    continue
                cand = Path(src).resolve().parent
                if cand.name.startswith("si_pg_harness_") and cand.parent == tmp_root:
                    mount_dirs.append(cand)
        subprocess.run([podman_bin, "rm", "-f", cid], capture_output=True, text=True, timeout=10.0, check=False)
        for mdir in mount_dirs:
            shutil.rmtree(mdir, ignore_errors=True)
        reaped.append(cname)
    return reaped


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
            _reap_stale_harness_containers(podman_bin)
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
        from spaceinvasion.db.migrations import NO_BACKUP
        from spaceinvasion.db.store import DatabaseStore

        with psycopg.connect(self.admin_dsn, autocommit=True) as conn:
            conn.execute(f'DROP DATABASE IF EXISTS "{self.TEMPLATE_DB}"')
            conn.execute(f'CREATE DATABASE "{self.TEMPLATE_DB}"')

        store = DatabaseStore.from_dsn(
            self.template_dsn,
            pool_size=1,
            max_overflow=1,
        )
        try:
            store.ensure_ready(backup_fn=NO_BACKUP, verify_schema=True)
        finally:
            store.close()

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
    """Create a marker file for temp test paths so `h.db_path.exists()` stays True."""
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
    """Redirects `GameDatabase` instances in tests onto isolated disposable PostgreSQL databases."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._installed = False
        self._enabled = False
        self._path_to_dsn: dict[str, str] = {}
        self._open_dbs: list[weakref.ref[Any]] = []
        self.redirected_count: int = 0
        self._orig_db_init: Any = None
        self._orig_load_cfg: Any = None
        self._orig_save_cfg: Any = None
        self._orig_load_rank_history: Any = None

    @property
    def enabled(self) -> bool:
        return self._enabled

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
            from spaceinvasion.combat import points as points_mod
            from spaceinvasion.core import config as config_mod
            from spaceinvasion.db import config_store as config_store_mod
            from spaceinvasion.db import dsn as dsn_mod
            from spaceinvasion.runtime.bridge import _context_init as ctx_init_mod
            from spaceinvasion.runtime.bridge._db_gateway import RuntimeDatabaseGateway

            self._orig_open_gateway = ctx_init_mod.open_runtime_gateway
            self._orig_load_cfg = config_store_mod.load_strategy_config
            self._orig_save_cfg = config_store_mod.save_strategy_config
            self._orig_load_cfg_json = config_mod.load_strategy_config
            self._orig_save_cfg_json = config_mod.save_strategy_config
            self._orig_load_rank_history = points_mod.load_rank_history
            redirector = self

            def _wrapped_open_gateway(
                target: Any,
                *,
                db_backend: Optional[str] = None,
                pool_size: int = 2,
                max_overflow: int = 4,
            ) -> RuntimeDatabaseGateway:
                explicit_invalid_backend = db_backend is not None and str(db_backend).strip().lower() in (
                    "sqlite",
                    "sqlite3",
                    "file",
                )
                if explicit_invalid_backend:
                    dsn_mod.normalize_backend_name(db_backend)
                raw_path_s = str(target or "").strip()
                use_pg_redirect = redirector._enabled and not dsn_mod.is_postgres_dsn(raw_path_s)
                if use_pg_redirect:
                    key = _resolve_db_key(target)
                    harness = get_shared_pg_harness()
                    if key is not None:
                        with redirector._lock:
                            mapped_dsn = redirector._path_to_dsn.get(key)
                        if mapped_dsn is None:
                            new_dsn = harness.create_database(prefix="test", from_template=True)
                            with redirector._lock:
                                mapped_dsn = redirector._path_to_dsn.setdefault(key, new_dsn)
                        _touch_pg_marker_if_outside_repo(Path(key), mapped_dsn)
                    else:
                        mapped_dsn = harness.create_database(prefix="mem", from_template=True)
                    gw = RuntimeDatabaseGateway.from_dsn(
                        mapped_dsn,
                        pool_size=1,
                        max_overflow=4,
                        ensure_ready=False,
                    )
                    with redirector._lock:
                        redirector.redirected_count += 1
                        redirector._open_dbs.append(weakref.ref(gw))
                    return gw

                gw = redirector._orig_open_gateway(
                    target,
                    db_backend=db_backend,
                    pool_size=min(max(1, int(pool_size)), 2),
                    max_overflow=min(max(1, int(max_overflow)), 4),
                )
                with redirector._lock:
                    redirector._open_dbs.append(weakref.ref(gw))
                return gw

            def _wrapped_load_cfg(path: Any = None, *, env: Any = None) -> Any:
                if redirector._enabled and path is None:
                    env_url = str(os.environ.get("SPACEINVASION_DB_URL") or "").strip().lower()
                    if not env_url.startswith(("postgresql://", "postgres://")):
                        return config_mod.StrategyConfig()
                if redirector._enabled and path is not None:
                    mapped = redirector.lookup_mapped_dsn(path)
                    if mapped is not None:
                        return redirector._orig_load_cfg(mapped, env=env)
                return redirector._orig_load_cfg(path, env=env)

            def _wrapped_save_cfg(cfg: Any, path: Any = None, *, env: Any = None) -> Any:
                if redirector._enabled and path is not None:
                    mapped = redirector.lookup_mapped_dsn(path)
                    if mapped is not None:
                        return redirector._orig_save_cfg(cfg, mapped, env=env)
                return redirector._orig_save_cfg(cfg, path, env=env)

            def _wrapped_load_rank_history(history_path: Any = None) -> list[dict[str, Any]]:
                if redirector._enabled and history_path is not None:
                    mapped = redirector.lookup_mapped_dsn(history_path)
                    if mapped is not None:
                        gw = RuntimeDatabaseGateway.from_dsn(
                            mapped,
                            pool_size=1,
                            max_overflow=1,
                            ensure_ready=False,
                        )
                        try:
                            return gw.get_rank_samples()
                        finally:
                            gw.close()
                return redirector._orig_load_rank_history(history_path)

            def _wrapped_load_cfg_json(path: Any = None) -> Any:
                if redirector._enabled and path is not None:
                    mapped = redirector.lookup_mapped_dsn(path)
                    if mapped is not None:
                        return redirector._orig_load_cfg(mapped)
                return redirector._orig_load_cfg_json(path)

            def _wrapped_save_cfg_json(cfg: Any, path: Any = None) -> Any:
                if redirector._enabled and path is not None:
                    mapped = redirector.lookup_mapped_dsn(path)
                    if mapped is not None:
                        return redirector._orig_save_cfg(cfg, mapped)
                return redirector._orig_save_cfg_json(cfg, path)

            ctx_init_mod.open_runtime_gateway = _wrapped_open_gateway  # type: ignore[assignment]
            config_store_mod.load_strategy_config = _wrapped_load_cfg  # type: ignore[assignment]
            config_store_mod.save_strategy_config = _wrapped_save_cfg  # type: ignore[assignment]
            config_mod.load_strategy_config = _wrapped_load_cfg_json  # type: ignore[assignment]
            config_mod.save_strategy_config = _wrapped_save_cfg_json  # type: ignore[assignment]
            points_mod.load_rank_history = _wrapped_load_rank_history  # type: ignore[assignment]
            _ = dsn_mod.is_unix_socket_dsn
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


def open_test_db(
    db_path: Any = None,
    *,
    backend: Optional[str] = None,
    postgres_url: Optional[str] = None,
    pool_size: int = 1,
    max_overflow: int = 4,
    ensure_ready: bool = True,
) -> Any:
    """Open an isolated PostgreSQL `RuntimeDatabaseGateway` for unit and E2E tests."""
    from spaceinvasion.db.dsn import is_postgres_dsn, normalize_backend_name
    from spaceinvasion.runtime.bridge import _context_init as ctx_init_mod
    from spaceinvasion.runtime.bridge._db_gateway import RuntimeDatabaseGateway

    if backend is not None and str(backend).strip().lower() not in ("", "auto"):
        normalize_backend_name(backend)
    target = postgres_url if postgres_url is not None else db_path
    if target is not None and is_postgres_dsn(str(target)):
        gw = RuntimeDatabaseGateway.from_dsn(
            str(target).strip(),
            pool_size=pool_size,
            max_overflow=max_overflow,
            ensure_ready=ensure_ready,
        )
        with _REDIRECTOR._lock:
            _REDIRECTOR._open_dbs.append(weakref.ref(gw))
        return gw
    return ctx_init_mod.open_runtime_gateway(
        target if target is not None else ":memory:",
        db_backend=backend,
        pool_size=pool_size,
        max_overflow=max_overflow,
    )


def run_pg_query(db: Any, sql: str, params: Sequence[Any] = ()) -> list[dict[str, Any]]:
    """Run a read-only PostgreSQL query against a test gateway and return dict rows."""
    from psycopg.rows import dict_row

    dsn = str(getattr(db, "postgres_url", "") or "").strip()
    if not dsn:
        raise RuntimeError("run_pg_query requires a PostgreSQL-backed gateway with postgres_url")
    with psycopg.connect(dsn, row_factory=dict_row) as conn:
        if params:
            pg_sql = sql.replace("?", "%s")
            return list(conn.execute(pg_sql, tuple(params)).fetchall())
        return list(conn.execute(sql).fetchall())


def run_pg_exec(db: Any, sql: str, params: Sequence[Any] = ()) -> None:
    """Execute a raw PostgreSQL statement against a test gateway and commit."""
    dsn = str(getattr(db, "postgres_url", "") or "").strip()
    if not dsn:
        raise RuntimeError("run_pg_exec requires a PostgreSQL-backed gateway with postgres_url")
    with psycopg.connect(dsn) as conn:
        if params:
            pg_sql = sql.replace("?", "%s")
            conn.execute(pg_sql, tuple(params))
        else:
            conn.execute(sql)
        conn.commit()
