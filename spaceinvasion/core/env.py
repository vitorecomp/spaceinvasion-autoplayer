"""Centralized process environment boundary for `spaceinvasion`, `bin`, and `scripts`."""

from __future__ import annotations

import os


def get_process_env() -> dict[str, str]:
    """Return a shallow snapshot copy of the current process environment."""
    return dict(os.environ)


def get_env(name: str, default: str = "") -> str:
    """Read an environment variable through the centralized process-env boundary."""
    return os.environ.get(name, default)
