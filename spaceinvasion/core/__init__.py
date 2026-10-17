"""Core domain models, SpaceInvasion Classic Wiki catalog, configuration, and redaction utilities."""

from __future__ import annotations

import importlib
from types import ModuleType

__all__ = ["catalog", "coercion", "config", "coords", "logging_utils", "model", "redaction", "settings"]


def __getattr__(name: str) -> ModuleType:
    if name in __all__:
        mod = importlib.import_module(f"spaceinvasion.core.{name}")
        globals()[name] = mod
        return mod
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
