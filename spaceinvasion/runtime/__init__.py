"""Thread-locked HTTP JSON API server, pure-HTTP Commander client, and subprocess hot-reloader."""

from __future__ import annotations

import importlib
from types import ModuleType

__all__ = ['api_server', 'commander_client', 'reloader']


def __getattr__(name: str) -> ModuleType:
    if name in __all__:
        mod = importlib.import_module(f"spaceinvasion.runtime.{name}")
        globals()[name] = mod
        return mod
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
