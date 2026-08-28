"""Heuristic P0-P3 planner, evolutionary GA+NN optimizer, and topological Action Graph (DAG)."""

from __future__ import annotations

import importlib
from types import ModuleType

__all__ = ['action_graph', 'optimizer', 'strategy']


def __getattr__(name: str) -> ModuleType:
    if name in __all__:
        mod = importlib.import_module(f"spaceinvasion.planner.{name}")
        globals()[name] = mod
        return mod
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
