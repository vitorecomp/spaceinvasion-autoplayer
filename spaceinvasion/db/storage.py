"""Notebook-only compatibility shim preserving `GameDatabase(...).close()`."""

from __future__ import annotations


class GameDatabase:
    """Minimal no-op shim kept solely for legacy `notebooks/*.ipynb` SQLite copy calls."""

    def __init__(self, *args: object, **kwargs: object) -> None:
        del args, kwargs

    def close(self) -> None:
        """Close the compatibility shim (no-op)."""


__all__ = ["GameDatabase"]
