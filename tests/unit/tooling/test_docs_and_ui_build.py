"""Tests for Phase 7 documentation accuracy, generated references, and Phase 5.6 UI bundle build."""

from __future__ import annotations

import ast
from pathlib import Path
import subprocess
import sys

REPO_ROOT: Path = Path(__file__).resolve().parents[3]
GEN_DOCS_SCRIPT: Path = REPO_ROOT / "scripts" / "generate_docs.py"

_HOT_TABLES: tuple[str, ...] = (
    "meta",
    "latest_state_hot",
    "empire_planets_hot",
    "galaxy_sector_hot",
    "colony_candidates_hot",
    "strategy_config_store",
    "economy_rates_cache",
    "tutorial_progress",
    "spy_targets",
    "galaxy_scans",
    "galaxy_slots",
    "ui_notifications",
    "universe_research_max",
    "manual_queue",
    "config_optimizer_active",
    "ai_planner_active",
    "field_conflict_prompts",
    "loot_recycle_hourly",
    "schema_migrations",
)
_HISTORY_TABLES: tuple[str, ...] = (
    "rank_snapshots",
    "planet_snapshots",
    "state_snapshots",
    "action_history",
    "config_history",
    "spy_reports",
    "galaxy_scan_history",
    "galaxy_slot_history",
    "raid_history",
    "bash_blocks",
    "game_messages",
    "battle_reports",
    "recycle_reports",
    "manual_queue_events",
    "universe_research_max_history",
    "colony_attempts",
    "config_optimizer_runs",
    "ai_planner_runs",
    "field_conflict_events",
    "hourly_production_history",
    "schema_migration_events",
)
_ALL_40_TABLES: tuple[str, ...] = (*_HOT_TABLES, *_HISTORY_TABLES)

_STALE_DOC_TOKENS: tuple[str, ...] = (
    "ga_nn",
    "run_bot.py",
    "advisor.py",
    "optimizer_experimentation.ipynb",
    "V001..V007",
    "V001..V008",
    "V001..V009",
    "V001..V010",
    "25-table SQLite",
    "27 SQLite tables",
)


def test_generate_docs_check_passes_with_zero_drift() -> None:
    """`scripts/generate_docs.py --check` exits 0 and both generated docs cover all settings & commands."""
    res = subprocess.run(
        [sys.executable, str(GEN_DOCS_SCRIPT), "--check"],
        capture_output=True,
        text=True,
        check=False,
        timeout=30.0,
    )
    assert res.returncode == 0, f"generate_docs.py --check failed: {res.stdout}\n{res.stderr}"

    settings_md = (REPO_ROOT / "docs" / "settings-reference.md").read_text(encoding="utf-8")
    api_md = (REPO_ROOT / "docs" / "api-commands.md").read_text(encoding="utf-8")
    assert "`planner_mode`" in settings_md
    assert "`min_colony_fields`" in settings_md
    cmd_headings = [line for line in api_md.splitlines() if line.startswith("## `")]
    assert len(cmd_headings) == 60


def test_core_docs_have_zero_stale_tokens_and_cover_all_40_tables() -> None:
    """Rewritten core docs omit removed legacy artifacts, remove stale docs, and list all 40 PG tables."""
    assert not (REPO_ROOT / "docs" / "project.md").exists()
    assert not (REPO_ROOT / "docs" / "testing").exists()

    doc_paths = (
        REPO_ROOT / "AGENTS.md",
        REPO_ROOT / "README.md",
        REPO_ROOT / "docs" / "README.md",
        REPO_ROOT / "docs" / "architecture.md",
        REPO_ROOT / "docs" / "database-mer.md",
        REPO_ROOT / "docs" / "manual.md",
    )
    for path in doc_paths:
        text = path.read_text(encoding="utf-8")
        for token in _STALE_DOC_TOKENS:
            assert token not in text, f"Stale token {token!r} found in {path.relative_to(REPO_ROOT)}"

    mer_text = (REPO_ROOT / "docs" / "database-mer.md").read_text(encoding="utf-8")
    assert len(_ALL_40_TABLES) == 40
    for table_name in _ALL_40_TABLES:
        assert f"`{table_name}`" in mer_text, f"Table {table_name!r} missing from docs/database-mer.md"


def test_docker_backend_builds_ui_into_opt_and_compose_preserves_pg_stats() -> None:
    """Docker backend builds frontend + static icons into `/opt/spaceinvasion-ui` and compose keeps PG stats intact."""
    containerfile = (REPO_ROOT / "docker" / "Containerfile.backend").read_text(encoding="utf-8")
    assert "AS ui-builder" in containerfile
    assert "npm run build" in containerfile
    assert "/app/backend/ui" in containerfile
    assert "COPY backend /app/backend" in containerfile
    assert "/opt/spaceinvasion-ui" in containerfile
    assert "SPACEINVASION_UI_DIR=/opt/spaceinvasion-ui" in containerfile
    assert "spaceinvasion/ui" not in containerfile
    assert "COPY spaceinvasion" not in containerfile
    for rel in ("frontend/vite.config.ts", "frontend/scripts/prerender.mjs"):
        build_src = (REPO_ROOT / rel).read_text(encoding="utf-8")
        assert "../backend/ui" in build_src
        assert "spaceinvasion/ui" not in build_src
    for icon in ("bell.svg", "favicon.svg", "favicon.ico"):
        assert f"backend/ui/{icon}" in containerfile
        assert (REPO_ROOT / "backend" / "ui" / icon).is_file()

    compose = (REPO_ROOT / "podman-compose.yml").read_text(encoding="utf-8")
    assert "./data/postgres:/var/lib/postgresql/data" in compose
    assert "shared_preload_libraries=pg_stat_statements" in compose
    assert "pg_stat_statements.track=all" in compose
    assert 'SPACEINVASION_UI_DIR: "/opt/spaceinvasion-ui"' in compose


def test_generate_docs_script_obeys_section4_standards() -> None:
    """`scripts/generate_docs.py` satisfies Section 4 LOC, typing, and smell limits."""
    src = GEN_DOCS_SCRIPT.read_text(encoding="utf-8")
    lines = src.splitlines()
    assert len(lines) <= 400, f"generate_docs.py has {len(lines)} lines (>400)"
    tree = ast.parse(src, filename=str(GEN_DOCS_SCRIPT))
    assert ast.get_docstring(tree)
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            span = (node.end_lineno or node.lineno) - node.lineno + 1
            assert span <= 100, f"{node.name} exceeds 100 LOC ({span})"
        elif isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
            assert node.func.id not in ("print", "getattr", "hasattr", "setattr")
        elif isinstance(node, ast.Name):
            assert node.id != "Any"
        elif isinstance(node, ast.ExceptHandler):
            exc_name = node.type.id if isinstance(node.type, ast.Name) else ""
            assert node.type is not None and exc_name not in ("Exception", "BaseException")
