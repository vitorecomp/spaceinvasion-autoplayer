"""Pre-upgrade PostgreSQL `pg_dump` backup callback factory for Alembic upgrades."""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path
import shutil
import subprocess

from sqlalchemy.engine import make_url

from spaceinvasion.db.session import normalize_sqlalchemy_dsn

BackupCallback = Callable[[], str | Path | None]


class _NoBackupSentinel:
    """Explicit opt-out sentinel for tests upgrading disposable databases without pg_dump."""

    def __repr__(self) -> str:
        return "NO_BACKUP"


NO_BACKUP: _NoBackupSentinel = _NoBackupSentinel()
BackupOption = BackupCallback | _NoBackupSentinel


def _query_str(value: str | tuple[str, ...] | None) -> str | None:
    if isinstance(value, str) and value:
        return value
    if isinstance(value, tuple) and value and isinstance(value[0], str) and value[0]:
        return value[0]
    return None


def make_pg_dump_backup_fn(
    dsn: str,
    backup_dir: Path,
    *,
    label: str = "pre_alembic_upgrade",
) -> BackupCallback:
    """Create a pre-upgrade `pg_dump -Fc` backup callback for TCP or Unix-socket DSNs."""
    normalized = normalize_sqlalchemy_dsn(dsn)
    parsed = make_url(normalized)
    db_name = parsed.database or ""
    if not db_name:
        raise ValueError("PostgreSQL DSN must specify a database name for pg_dump")
    libpq_dsn = normalized.replace("postgresql+psycopg://", "postgresql://", 1)

    def _run_pg_dump() -> Path:
        pg_dump_bin = shutil.which("pg_dump")
        if pg_dump_bin is None:
            raise FileNotFoundError("pg_dump executable not found on PATH")
        backup_dir.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        out_path = backup_dir / f"{label}_{stamp}.dump"

        if parsed.password:
            cmd = [pg_dump_bin, "-Fc", "-f", str(out_path), f"--dbname={libpq_dsn}"]
        else:
            cmd = [pg_dump_bin, "-Fc", "-f", str(out_path)]
            host = parsed.host or _query_str(parsed.query.get("host"))
            if host:
                cmd.extend(["-h", host])
            if parsed.port is not None:
                cmd.extend(["-p", str(parsed.port)])
            user = parsed.username or _query_str(parsed.query.get("user"))
            if user:
                cmd.extend(["-U", user])
            cmd.append(db_name)

        proc = subprocess.run(cmd, capture_output=True, text=True, check=False)
        if proc.returncode != 0:
            raise RuntimeError(f"pg_dump failed ({proc.returncode}): {proc.stderr.strip()}")
        if not out_path.is_file() or out_path.stat().st_size == 0:
            raise RuntimeError(f"pg_dump produced an empty backup file: {out_path}")
        return out_path

    return _run_pg_dump


__all__ = ["NO_BACKUP", "BackupCallback", "BackupOption", "make_pg_dump_backup_fn"]
