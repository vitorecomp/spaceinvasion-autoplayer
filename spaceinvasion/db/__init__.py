"""Versioned forward-only SQLite migrations (V001..V007) and append-only WAL storage."""

from __future__ import annotations

import importlib
from types import ModuleType

__all__ = ['migrations', 'storage']


def __getattr__(name: str) -> ModuleType:
    if name in __all__:
        mod = importlib.import_module(f"spaceinvasion.db.{name}")
        globals()[name] = mod
        return mod
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
