#!/usr/bin/env python3
"""CLI entrypoint for SpaceInvasion Autoplayer database versioning and migrations.

Usage examples:
  .venv/bin/python3 scripts/db_migrate.py status
  .venv/bin/python3 scripts/db_migrate.py migrate --all --backup
  .venv/bin/python3 scripts/db_migrate.py migrate --dry-run
  .venv/bin/python3 scripts/db_migrate.py verify --full-integrity
  .venv/bin/python3 scripts/db_migrate.py backup --label pre_experiment
  .venv/bin/python3 scripts/db_migrate.py history --limit 25
"""

from __future__ import annotations

from pathlib import Path
import sys

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from spaceinvasion.db.migrations import main


if __name__ == "__main__":
    raise SystemExit(main())
