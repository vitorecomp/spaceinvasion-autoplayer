"""1:1 combat simulation, fleet dispatch helpers, galaxy/espionage/inbox parsers, and ranking ETAs."""

from __future__ import annotations

import importlib
from types import ModuleType

__all__ = ["cargo", "colonies", "flight", "points", "scanner", "simulator"]


def __getattr__(name: str) -> ModuleType:
    if name in __all__:
        mod = importlib.import_module(f"spaceinvasion.combat.{name}")
        globals()[name] = mod
        return mod
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
