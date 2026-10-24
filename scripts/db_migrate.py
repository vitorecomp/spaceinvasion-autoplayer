#!/usr/bin/env python3
"""CLI entrypoint for SpaceInvasion Autoplayer database versioning and Alembic migrations.

Usage examples:
  .venv/bin/python3 scripts/db_migrate.py status
  .venv/bin/python3 scripts/db_migrate.py migrate --all --backup
  .venv/bin/python3 scripts/db_migrate.py migrate --dry-run
  .venv/bin/python3 scripts/db_migrate.py verify --full-integrity
  .venv/bin/python3 scripts/db_migrate.py backup --label pre_experiment
  .venv/bin/python3 scripts/db_migrate.py history --limit 25
"""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path
import sys

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))


def main(argv: Sequence[str] | None = None) -> int:
    """Run the Alembic database migration CLI with host environment and Podman lifecycle hooks."""
    from spaceinvasion.db.migrations.cli import main as run_migrations_cli
    from spaceinvasion.runtime.postgres_ctl import (
        create_postgres_backup,
        ensure_postgres_container_ready,
        get_process_env,
    )

    env = get_process_env()

    def _prepare(dsn: str, root_dir: Path, auto_start: bool) -> dict[str, object]:
        return ensure_postgres_container_ready(dsn, root_dir=root_dir, auto_start=auto_start, env=env)

    def _backup(dsn: str, label: str, backup_dir: Path | None) -> Path:
        return create_postgres_backup(dsn, label=label, backup_dir=backup_dir)

    return run_migrations_cli(argv, env=env, prepare_fn=_prepare, backup_hook=_backup)


if __name__ == "__main__":
    raise SystemExit(main())
