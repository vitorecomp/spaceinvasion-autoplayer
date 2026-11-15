"""Module runner for `python -m backend.db.migrations`."""

from __future__ import annotations

from backend.db.migrations.cli import main

if __name__ == "__main__":
    raise SystemExit(main())
