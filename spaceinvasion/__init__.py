"""SpaceInvasion Classic Autoplayer — modular strategy, combat, session, DB, and runtime package."""

from __future__ import annotations

import importlib
import importlib.abc
import importlib.machinery
import sys
from types import ModuleType
from typing import Optional, Sequence

__all__ = [
    "combat",
    "core",
    "db",
    "planner",
    "runtime",
    "session",
]

_SUBMODULE_MAP: dict[str, str] = {
    "game_data": "spaceinvasion.core.game_data",
    "state": "spaceinvasion.core.state",
    "config": "spaceinvasion.core.config",
    "logging_utils": "spaceinvasion.core.logging_utils",
    "auth": "spaceinvasion.session.auth",
    "browser": "spaceinvasion.session.browser",
    "client": "spaceinvasion.session.client",
    "executor": "spaceinvasion.session.executor",
    "fleet": "spaceinvasion.combat.fleet",
    "ranking": "spaceinvasion.combat.ranking",
    "strategy": "spaceinvasion.planner.strategy",
    "optimizer": "spaceinvasion.planner.optimizer",
    "action_graph": "spaceinvasion.planner.action_graph",
    "migrations": "spaceinvasion.db.migrations",
    "storage": "spaceinvasion.db.storage",
    "api_server": "spaceinvasion.runtime.api_server",
    "commander_client": "spaceinvasion.runtime.commander_client",
    "reloader": "spaceinvasion.runtime.reloader",
}


class _AliasLoader(importlib.abc.Loader):
    def __init__(self, target_fqn: str) -> None:
        self._target_fqn = target_fqn

    def create_module(self, spec: importlib.machinery.ModuleSpec) -> ModuleType:
        target = importlib.import_module(self._target_fqn)
        sys.modules[spec.name] = target
        pkg = sys.modules.get("spaceinvasion")
        short = spec.name.rpartition(".")[2]
        if pkg is not None and short:
            setattr(pkg, short, target)
        return target

    def exec_module(self, module: ModuleType) -> None:
        return None


class _LegacySubmoduleFinder(importlib.abc.MetaPathFinder):
    def find_spec(
        self,
        fullname: str,
        path: Optional[Sequence[str]],
        target: Optional[ModuleType] = None,
    ) -> Optional[importlib.machinery.ModuleSpec]:
        if not fullname.startswith("spaceinvasion."):
            return None
        rest = fullname[len("spaceinvasion."):]
        if "." in rest:
            return None
        canonical = _SUBMODULE_MAP.get(rest)
        if canonical is None:
            return None
        return importlib.machinery.ModuleSpec(
            name=fullname,
            loader=_AliasLoader(canonical),
        )


if not any(type(f).__name__ == "_LegacySubmoduleFinder" for f in sys.meta_path):
    sys.meta_path.insert(0, _LegacySubmoduleFinder())


def __getattr__(name: str) -> ModuleType:
    if name in __all__:
        mod = importlib.import_module(f"spaceinvasion.{name}")
        globals()[name] = mod
        return mod
    if name in _SUBMODULE_MAP:
        mod = importlib.import_module(_SUBMODULE_MAP[name])
        sys.modules[f"spaceinvasion.{name}"] = mod
        globals()[name] = mod
        return mod
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
