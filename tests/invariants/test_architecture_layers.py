"""Architectural layer DAG invariants across all production Python modules."""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]

LAYER_RANK: dict[str, int] = {
    "core": 0,
    "parsing": 1,
    "combat": 1,
    "session": 2,
    "planner": 2,
    "db": 2,
    "runtime": 3,
    "bin": 4,
    "scripts": 4,
}

NOTEBOOK_SHIM_FILES: frozenset[str] = frozenset(
    {
        "backend/core/game_data.py",
        "backend/core/state.py",
        "backend/core/logging_utils.py",
        "backend/combat/fleet.py",
        "backend/combat/ranking.py",
        "backend/db/storage.py",
    }
)

RETIRED_MONOLITHS: frozenset[str] = frozenset(
    {
        "backend.core.game_data",
        "backend.core.state",
        "backend.core.logging_utils",
        "backend.combat.fleet",
        "backend.combat.ranking",
        "backend.planner.strategy",
        "backend.planner.ai_planner",
        "backend.planner.action_graph",
        "backend.db.storage",
        "backend.db.postgres",
        "backend.db.backend",
        "backend.runtime.api_server",
    }
)

FLAT_ALIASES: frozenset[str] = frozenset(
    {
        "game_data",
        "state",
        "config",
        "logging_utils",
        "auth",
        "browser",
        "client",
        "executor",
        "fleet",
        "ranking",
        "strategy",
        "action_graph",
        "migrations",
        "storage",
        "api_server",
        "commander_client",
        "reloader",
    }
)


def _layer_of(mod: str) -> str:
    parts = mod.split(".")
    return parts[1] if parts[0] == "backend" and len(parts) > 1 else parts[0]


def _src_layer(rel: str) -> str:
    parts = Path(rel).parts
    return parts[1] if parts[0] == "backend" and len(parts) > 2 else parts[0]


@pytest.mark.invariants
def test_strict_downward_architecture_layers() -> None:
    """Assert strict downward DAG bin -> runtime -> {session, planner, db} -> {parsing, combat} -> core."""
    violations: list[str] = []
    for root in ("backend", "bin", "scripts"):
        for path in sorted((REPO_ROOT / root).rglob("*.py")):
            rel = path.relative_to(REPO_ROOT).as_posix()
            if rel.startswith("backend/ui/") or rel in NOTEBOOK_SHIM_FILES:
                continue
            src = _src_layer(rel)
            if src not in LAYER_RANK:
                continue
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=rel)
            for node in ast.walk(tree):
                mods: list[str] = []
                if isinstance(node, ast.Import):
                    mods = [
                        alias.name
                        for alias in node.names
                        if alias.name == "backend" or alias.name.startswith("backend.")
                    ]
                elif isinstance(node, ast.ImportFrom) and node.module:
                    if node.module == "backend":
                        mods = [f"backend.{alias.name}" for alias in node.names]
                    elif node.module.startswith("backend."):
                        mods = [node.module] + [f"{node.module}.{alias.name}" for alias in node.names]
                for mod in mods:
                    if mod in RETIRED_MONOLITHS:
                        violations.append(f"Retired monolith import in {rel}:{node.lineno} -> {mod}")
                    parts = mod.split(".")
                    if len(parts) >= 2 and parts[1] in FLAT_ALIASES:
                        violations.append(f"Flat alias import in {rel}:{node.lineno} -> {mod}")
                    tgt = _layer_of(mod)
                    if tgt not in LAYER_RANK:
                        continue
                    if LAYER_RANK[tgt] > LAYER_RANK[src]:
                        violations.append(f"Upward import {rel}:{node.lineno} ({src} -> {tgt}): {mod}")
                    elif LAYER_RANK[tgt] == LAYER_RANK[src] and tgt != src:
                        violations.append(f"Cross-peer import {rel}:{node.lineno} ({src} -> {tgt}): {mod}")
    assert not violations, "\n".join(violations)
