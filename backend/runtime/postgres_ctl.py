"""Runtime PostgreSQL container lifecycle, readiness probes, and `pg_dump` backups."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from datetime import datetime, timezone
import os
from pathlib import Path
import re
import shutil
import socket
import subprocess
import tempfile
import threading
import time
from urllib.parse import urlparse

from backend.core.env import get_process_env as get_process_env
from backend.core.redaction import redact_sensitive
from backend.db.dsn import (
    DEFAULT_PG_DB,
    DEFAULT_PG_USER,
    get_unix_socket_host,
    is_container_env,
    is_unix_socket_dsn,
    redact_db_url,
)
from backend.db.session import ping_postgres

DEFAULT_COMPOSE_FILENAME: str = "podman-compose.yml"
DEFAULT_PODMAN_CONTAINER_NAME: str = "spaceinvasion_postgres"

_PODMAN_STATUS_LOCK = threading.Lock()
_PODMAN_STATUS_CACHE: tuple[float, dict[str, object]] = (0.0, {})


def is_container_runtime(env: Mapping[str, str] | None = None) -> bool:
    """Return True when running inside the SpaceInvasion Docker/Podman container stack."""
    src = env if env is not None else get_process_env()
    return is_container_env(src)


def check_postgres_tcp_open(host: str, port: int, *, timeout: float = 0.5) -> bool:
    """Return True if a TCP connection to `(host, port)` succeeds within `timeout` seconds."""
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.settimeout(max(0.1, float(timeout)))
            return sock.connect_ex((host, int(port))) == 0
    except OSError:
        return False


def get_postgres_container_status(*, force: bool = False) -> dict[str, object]:
    """Return cached Podman/Docker container status for `spaceinvasion_postgres`."""
    global _PODMAN_STATUS_CACHE
    now_m = time.monotonic()
    with _PODMAN_STATUS_LOCK:
        if not force and _PODMAN_STATUS_CACHE[1] and (now_m - _PODMAN_STATUS_CACHE[0]) < 3.0:
            return dict(_PODMAN_STATUS_CACHE[1])

    podman_bin = shutil.which("podman")
    podman_compose_bin = shutil.which("podman-compose")
    docker_bin = shutil.which("docker")
    info: dict[str, object] = {
        "runtime": "podman" if podman_bin else ("docker" if docker_bin else "none"),
        "podman_available": bool(podman_bin),
        "podman_compose_available": bool(podman_compose_bin),
        "container_name": DEFAULT_PODMAN_CONTAINER_NAME,
        "container_running": False,
        "container_status": "not_found",
    }
    runner_bin = podman_bin or docker_bin
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
            lines = (proc.stdout or "").strip().splitlines()
            if lines:
                parts = lines[0].split("|", 1)
                state_s = parts[0].strip().lower()
                status_s = parts[1].strip() if len(parts) > 1 else state_s
                info["container_running"] = state_s == "running" or status_s.lower().startswith("up")
                info["container_status"] = status_s or state_s
        except (OSError, subprocess.SubprocessError) as exc:
            info["container_status"] = f"probe_error: {redact_sensitive(str(exc))}"

    with _PODMAN_STATUS_LOCK:
        _PODMAN_STATUS_CACHE = (now_m, dict(info))
    return info


def get_podman_postgres_status(*, force: bool = False) -> dict[str, object]:
    """Backwards-compatible alias for `get_postgres_container_status`."""
    return get_postgres_container_status(force=force)


def _select_container_start_cmd(repo_root: Path) -> list[str] | None:
    compose_file = repo_root / DEFAULT_COMPOSE_FILENAME
    podman_compose_bin = shutil.which("podman-compose")
    podman_bin = shutil.which("podman")
    docker_bin = shutil.which("docker")
    if podman_compose_bin and compose_file.is_file():
        return [podman_compose_bin, "-f", str(compose_file), "up", "-d", "postgres"]
    if podman_bin:
        try:
            exists_proc = subprocess.run(
                [podman_bin, "container", "exists", DEFAULT_PODMAN_CONTAINER_NAME],
                capture_output=True,
                timeout=4.0,
                check=False,
            )
            if exists_proc.returncode == 0:
                return [podman_bin, "start", DEFAULT_PODMAN_CONTAINER_NAME]
        except (OSError, subprocess.SubprocessError):
            pass
        if compose_file.is_file():
            return [podman_bin, "compose", "-f", str(compose_file), "up", "-d", "postgres"]
    if docker_bin and compose_file.is_file():
        return [docker_bin, "compose", "-f", str(compose_file), "up", "-d", "postgres"]
    return None


def ensure_postgres_container_ready(
    dsn: str,
    *,
    root_dir: Path | str = Path("."),
    auto_start: bool = True,
    timeout: float = 25.0,
    env: Mapping[str, str] | None = None,
) -> dict[str, object]:
    """Ensure PostgreSQL at `dsn` is reachable, starting `spaceinvasion_postgres` if local and stopped."""
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
    cmd = _select_container_start_cmd(repo_root)
    start_err = ""
    if cmd is not None:
        env_copy = dict(env) if env is not None else get_process_env()
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
        except (OSError, subprocess.SubprocessError) as exc:
            start_err = str(exc)

    deadline = time.monotonic() + max(2.0, float(timeout))
    last_err = start_err or msg
    port = int(parsed.port or 5432)
    while time.monotonic() < deadline:
        if check_postgres_tcp_open("127.0.0.1", port, timeout=0.5):
            ok2, ms2, msg2 = ping_postgres(dsn, timeout=2.0)
            if ok2:
                get_postgres_container_status(force=True)
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


def _run_pg_dump_to_file(cmd: list[str], tmp_path: Path, *, timeout_seconds: float) -> tuple[bool, str]:
    try:
        with tmp_path.open("wb") as fh:
            proc = subprocess.run(
                cmd,
                stdout=fh,
                stderr=subprocess.PIPE,
                timeout=timeout_seconds,
                check=False,
            )
    except (OSError, subprocess.SubprocessError) as exc:
        return (False, str(redact_sensitive(str(exc))))
    if proc.returncode == 0 and tmp_path.exists() and tmp_path.stat().st_size > 0:
        return (True, "")
    err = proc.stderr.decode("utf-8", errors="replace").strip() if proc.stderr else f"exit {proc.returncode}"
    return (False, str(redact_sensitive(err)))


def create_postgres_backup(
    dsn: str,
    *,
    label: str = "manual",
    backup_dir: Path | None = None,
    timeout_seconds: float = 120.0,
) -> Path:
    """Create an atomic custom-format PostgreSQL `pg_dump` snapshot (host binary or Podman exec)."""
    cleaned_dsn = str(dsn or "").strip()
    if not cleaned_dsn:
        raise ValueError("PostgreSQL DSN is required to create a backup")

    sock_host = get_unix_socket_host(cleaned_dsn)
    default_dir = (Path(sock_host).parent / "backups") if sock_host is not None else (Path("data") / "backups")
    out_dir = backup_dir if backup_dir is not None else default_dir
    out_dir.mkdir(parents=True, exist_ok=True)
    safe_label = re.sub(r"[^A-Za-z0-9_.-]+", "_", str(label or "backup").strip())[:40] or "backup"
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    ns_tail = time.time_ns() % 1_000_000
    dest = out_dir / f"spaceinvasion_pg.{safe_label}.{stamp}_{ns_tail:06d}.dump"

    parsed = urlparse(cleaned_dsn)
    is_local_tcp = (sock_host is None) and (
        (parsed.hostname or "127.0.0.1").lower() in ("127.0.0.1", "localhost", "::1")
    )

    candidates: list[list[str]] = []
    pg_dump_bin = shutil.which("pg_dump")
    if pg_dump_bin:
        candidates.append([pg_dump_bin, "-Fc", cleaned_dsn])
    podman_bin = shutil.which("podman")
    if podman_bin and is_local_tcp:
        pg_user = parsed.username or DEFAULT_PG_USER
        pg_db = (parsed.path or "").strip("/") or DEFAULT_PG_DB
        candidates.append(
            [podman_bin, "exec", DEFAULT_PODMAN_CONTAINER_NAME, "pg_dump", "-U", pg_user, "-d", pg_db, "-Fc"]
        )
    if not candidates:
        raise RuntimeError("Neither host `pg_dump` nor local Podman `pg_dump` is available to create a backup.")

    last_err = "pg_dump failed"
    for cmd in candidates:
        tmp_fd, tmp_raw = tempfile.mkstemp(prefix=".spaceinvasion_pg.bkp.", suffix=".tmp", dir=str(out_dir))
        os.close(tmp_fd)
        tmp_path = Path(tmp_raw)
        ok, err = _run_pg_dump_to_file(cmd, tmp_path, timeout_seconds=max(5.0, float(timeout_seconds)))
        if ok:
            os.replace(tmp_path, dest)
            return dest
        tmp_path.unlink(missing_ok=True)
        last_err = err or last_err

    raise RuntimeError(f"PostgreSQL backup failed for {redact_db_url(cleaned_dsn)}: {last_err}")


def make_pg_dump_backup_fn(
    dsn: str,
    *,
    label: str = "pre_migrate",
    backup_dir: Path | None = None,
    timeout_seconds: float = 120.0,
) -> Callable[[], Path]:
    """Return a zero-argument backup callback compatible with Alembic `upgrade_database`."""

    def _run_backup() -> Path:
        return create_postgres_backup(
            dsn,
            label=label,
            backup_dir=backup_dir,
            timeout_seconds=timeout_seconds,
        )

    return _run_backup


__all__ = [
    "DEFAULT_COMPOSE_FILENAME",
    "DEFAULT_PODMAN_CONTAINER_NAME",
    "check_postgres_tcp_open",
    "create_postgres_backup",
    "ensure_postgres_container_ready",
    "get_podman_postgres_status",
    "get_postgres_container_status",
    "get_process_env",
    "is_container_runtime",
    "make_pg_dump_backup_fn",
    "ping_postgres",
]
