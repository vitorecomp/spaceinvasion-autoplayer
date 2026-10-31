"""Tests for Phase 7 documentation accuracy, generated references, and Phase 5.6 UI bundle build."""

from __future__ import annotations

import ast
import os
from pathlib import Path
import stat
import subprocess
import sys
import time

REPO_ROOT: Path = Path(__file__).resolve().parents[3]
STARTER_SH: Path = REPO_ROOT / "starter.sh"
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
    assert "/opt/spaceinvasion-ui" in containerfile
    assert "SPACEINVASION_UI_DIR=/opt/spaceinvasion-ui" in containerfile
    for icon in ("bell.svg", "favicon.svg", "favicon.ico"):
        assert f"spaceinvasion/ui/{icon}" in containerfile
        assert (REPO_ROOT / "spaceinvasion" / "ui" / icon).is_file()

    compose = (REPO_ROOT / "docker-compose.yml").read_text(encoding="utf-8")
    assert "./data/postgres:/var/lib/postgresql/data" in compose
    assert "shared_preload_libraries=pg_stat_statements" in compose
    assert "pg_stat_statements.track=all" in compose
    assert 'SPACEINVASION_UI_DIR: "/opt/spaceinvasion-ui"' in compose


def _run_starter_ui_check(
    tmp_path: Path,
    frontend_dir: Path,
    ui_dir: Path,
    npm_stub: Path,
) -> subprocess.CompletedProcess[str]:
    env = dict(os.environ)
    env.update(
        {
            "PYTHON_BIN": sys.executable,
            "WATCHDOG_LOG": str(tmp_path / "watchdog.log"),
            "WATCHDOG_PID_FILE": str(tmp_path / "watchdog.pid"),
            "SPACEINVASION_FRONTEND_DIR": str(frontend_dir),
            "SPACEINVASION_UI_DIR": str(ui_dir),
            "NPM_BIN": str(npm_stub),
        }
    )
    return subprocess.run(
        ["bash", str(STARTER_SH), "--check-ui-build"],
        env=env,
        capture_output=True,
        text=True,
        check=False,
        timeout=15.0,
    )


def test_starter_sh_ui_staleness_rebuilds_when_missing_or_stale_and_skips_when_fresh(
    tmp_path: Path,
) -> None:
    """`starter.sh` rebuilds UI when `app.js` is missing or older than frontend sources, and skips when fresh."""
    frontend_dir = tmp_path / "frontend"
    (frontend_dir / "src").mkdir(parents=True)
    (frontend_dir / "scripts").mkdir(parents=True)
    ui_dir = tmp_path / "ui"
    ui_dir.mkdir(parents=True)

    pkg_json = frontend_dir / "package.json"
    index_html = frontend_dir / "index.html"
    tsconfig_json = frontend_dir / "tsconfig.json"
    vite_cfg = frontend_dir / "vite.config.ts"
    src_file = frontend_dir / "src" / "App.tsx"
    prerender_script = frontend_dir / "scripts" / "prerender.mjs"
    all_inputs = (pkg_json, index_html, tsconfig_json, vite_cfg, src_file, prerender_script)
    for fpath, body in (
        (pkg_json, '{"name":"test"}\n'),
        (index_html, "<html></html>\n"),
        (tsconfig_json, "{}\n"),
        (vite_cfg, "export default {};\n"),
        (src_file, "export const App = () => null;\n"),
        (prerender_script, "// prerender\n"),
    ):
        fpath.write_text(body, encoding="utf-8")

    build_log = tmp_path / "npm_builds.log"
    npm_stub = tmp_path / "fake_npm.sh"
    npm_stub.write_text(
        "#!/usr/bin/env bash\n"
        f'echo "$*" >> "{build_log}"\n'
        f'mkdir -p "{ui_dir}"\n'
        f'echo "// built" > "{ui_dir / "app.js"}"\n',
        encoding="utf-8",
    )
    npm_stub.chmod(npm_stub.stat().st_mode | stat.S_IXUSR)

    # 1. Missing app.js -> MUST build.
    res1 = _run_starter_ui_check(tmp_path, frontend_dir, ui_dir, npm_stub)
    assert res1.returncode == 0, res1.stderr
    assert (ui_dir / "app.js").is_file()
    assert len(build_log.read_text(encoding="utf-8").splitlines()) == 1

    # 2. app.js newer than all frontend sources -> MUST skip.
    now = time.time()
    for fpath in all_inputs:
        os.utime(fpath, (now - 20.0, now - 20.0))
    os.utime(ui_dir / "app.js", (now - 5.0, now - 5.0))
    res2 = _run_starter_ui_check(tmp_path, frontend_dir, ui_dir, npm_stub)
    assert res2.returncode == 0, res2.stderr
    assert len(build_log.read_text(encoding="utf-8").splitlines()) == 1

    # 3. frontend/src/App.tsx newer than app.js -> MUST rebuild.
    os.utime(src_file, (now + 5.0, now + 5.0))
    res3 = _run_starter_ui_check(tmp_path, frontend_dir, ui_dir, npm_stub)
    assert res3.returncode == 0, res3.stderr
    assert len(build_log.read_text(encoding="utf-8").splitlines()) == 2

    # 4. frontend/scripts/prerender.mjs newer than app.js -> MUST rebuild.
    os.utime(ui_dir / "app.js", (now + 10.0, now + 10.0))
    os.utime(prerender_script, (now + 15.0, now + 15.0))
    res4 = _run_starter_ui_check(tmp_path, frontend_dir, ui_dir, npm_stub)
    assert res4.returncode == 0, res4.stderr
    assert len(build_log.read_text(encoding="utf-8").splitlines()) == 3

    # 5. frontend/tsconfig.json newer than app.js -> MUST rebuild.
    os.utime(ui_dir / "app.js", (now + 20.0, now + 20.0))
    os.utime(tsconfig_json, (now + 25.0, now + 25.0))
    res5 = _run_starter_ui_check(tmp_path, frontend_dir, ui_dir, npm_stub)
    assert res5.returncode == 0, res5.stderr
    assert len(build_log.read_text(encoding="utf-8").splitlines()) == 4


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
