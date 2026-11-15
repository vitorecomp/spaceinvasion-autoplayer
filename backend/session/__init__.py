"""Browser lifecycle, BitMeUp authentication, Selenium frame client, and click/form executor."""

from __future__ import annotations

import importlib
from types import ModuleType

__all__ = ["auth", "browser", "client", "executor"]


def __getattr__(name: str) -> ModuleType:
    if name in __all__:
        mod = importlib.import_module(f"backend.session.{name}")
        globals()[name] = mod
        return mod
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
