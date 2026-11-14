"""Module runner for `python -m spaceinvasion.db.migrations`."""

from __future__ import annotations

from spaceinvasion.db.migrations.cli import main

if __name__ == "__main__":
    raise SystemExit(main())
