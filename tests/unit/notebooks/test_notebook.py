"""Tests for `notebooks/full_planner.ipynb` and `notebooks/config_optimizer.ipynb`."""

from __future__ import annotations

import ast
import contextlib
import dataclasses
import datetime
import gc
import hashlib
import importlib
import io
import json
import math
import os
from pathlib import Path
import re
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import tomllib
import types
import unittest
import warnings

import pytest

import numpy as np
import pandas as pd

from backend.core import catalog as game_data
from backend.planner.eta import compute_action_eta
from backend.core.model import (
    BuildingOffer,
    EnergyBalance,
    GameState,
    PlanetState,
    PlannedAction,
    Resources,
)
from backend.planner.heuristics import plan_actions

REPO_ROOT = Path(__file__).resolve().parents[3]
ATTACK_HISTORY_PATH = REPO_ROOT / "state" / "spaceinvasion.db"
LEGACY_ATTACK_JSON_PATH = REPO_ROOT / "data" / "attack_history.json"
SHIP_SLUG_BY_ID: dict[int, str] = {
    1: "small-transport",
    4: "small-fighter",
    6: "starcruiser",
    9: "solar-cells",
}
FORBIDDEN_IMPORT_NAMES = frozenset({"api_server", "e2e_tests", "selenium", "requests"})
FORBIDDEN_WRITE_ATTRS = frozenset(
    {
        "write_text",
        "write_bytes",
        "unlink",
        "rmdir",
        "rmtree",
        "mkdir",
        "makedirs",
        "rename",
        "touch",
        "symlink_to",
        "hardlink_to",
    }
)


def _cell_source(cell: dict) -> str:
    src = cell.get("source") or []
    return "".join(src) if isinstance(src, list) else str(src)


def _sha256_bytes(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _sample_snapshot_state(ts: float = 1_700_000_000.0) -> GameState:
    bld = {"b1": 4, "b2": 3, "b3": 4, "b4": 2, "b5": 7, "b7": 1}
    p = PlanetState(
        planet_id=12476,
        name="Homeworld",
        coords="02:380:12",
        planet_type="normal",
        temperature=31,
        fields_used=sum(bld.values()),
        fields_max=230,
        resources=Resources(9_000, 6_000, 4_000, 2_000),
        storage=Resources(100_000, 100_000, 100_000, 100_000),
        energy=EnergyBalance(produced=150, used=110),
        buildings=bld,
        building_offers=(
            BuildingOffer(
                building_id=1,
                name="Pig iron mine",
                current_level=4,
                target_level=5,
                cost=Resources(600, 0, 200, 0),
                can_build=True,
                status_text="Build level 5",
                build_href="indexInternal.es?action=internalBuildItem&buildingID=1",
            ),
        ),
    )
    return GameState(
        universe_id=31,
        user_id=1001,
        nickname="TempTestPlayer",
        timestamp=ts,
        planets=(p,),
        active_planet_id=12476,
        researches={"r2": 1},
        speedup_factor=5.0,
    )


def _assert_action_prereqs_legal(
    test_case: unittest.TestCase,
    state: GameState,
    actions: list[PlannedAction],
) -> None:
    p_map = {p.planet_id: p for p in state.planets}
    for act in actions:
        if not act.executable_now:
            continue
        planet = p_map.get(act.planet_id)
        bld = dict(planet.buildings) if planet is not None else {}
        res = dict(state.researches or {})
        if act.kind == "BUILD" and act.target_id is not None:
            unmet = game_data.missing_prerequisites("buildings", int(act.target_id), buildings=bld, researches=res)
            test_case.assertEqual(
                unmet,
                [],
                f"Illegal executable BUILD b{act.target_id} with unmet prereqs: {unmet}",
            )
        elif act.kind == "RESEARCH" and act.target_id is not None:
            rkey = f"r{int(act.target_id)}"
            unmet = game_data.missing_prerequisites("research", rkey, buildings=bld, researches=res)
            test_case.assertEqual(
                unmet,
                [],
                f"Illegal executable RESEARCH {rkey} with unmet prereqs: {unmet}",
            )
        elif act.kind == "BUILD_SHIP" and act.target_id is not None:
            slug = SHIP_SLUG_BY_ID.get(int(act.target_id), str(act.target_id))
            unmet = game_data.missing_prerequisites("ships", slug, buildings=bld, researches=res)
            test_case.assertEqual(
                unmet,
                [],
                f"Illegal executable BUILD_SHIP {slug} with unmet prereqs: {unmet}",
            )


def _assert_notebook_cell_ast_safe(self: unittest.TestCase, src: str, label: str) -> None:
    tree = ast.parse(src, filename=f"notebook#{label}")
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                parts = {p for p in alias.name.split(".") if p}
                self.assertTrue(
                    FORBIDDEN_IMPORT_NAMES.isdisjoint(parts),
                    f"Forbidden import {alias.name!r} in {label}",
                )
        elif isinstance(node, ast.ImportFrom):
            mod = node.module or ""
            parts = {p for p in mod.split(".") if p}
            self.assertTrue(
                FORBIDDEN_IMPORT_NAMES.isdisjoint(parts),
                f"Forbidden import from {mod!r} in {label}",
            )
            for alias in node.names:
                alias_parts = {p for p in alias.name.split(".") if p}
                self.assertTrue(
                    FORBIDDEN_IMPORT_NAMES.isdisjoint(alias_parts),
                    f"Forbidden import {alias.name!r} from {mod!r} in {label}",
                )
        elif isinstance(node, ast.Call):
            if isinstance(node.func, ast.Name):
                self.assertNotEqual(
                    node.func.id,
                    "open",
                    f"Direct open() disallowed in {label}",
                )
            elif isinstance(node.func, ast.Attribute):
                self.assertNotIn(
                    node.func.attr,
                    FORBIDDEN_WRITE_ATTRS,
                    f"Filesystem write method {node.func.attr!r} disallowed in {label}",
                )
                if node.func.attr == "open":
                    self.fail(f"File .open() disallowed in {label}")
                if node.func.attr == "chmod":
                    self.assertIsInstance(
                        node.func.value,
                        ast.Name,
                        f"chmod() target must be temp var in {label}",
                    )
                    self.assertTrue(
                        node.func.value.id.startswith("tmp_"),
                        f"chmod() must target tmp_* path only, got {node.func.value.id!r}",
                    )
                if (
                    node.func.attr == "copy2"
                    and isinstance(node.func.value, ast.Name)
                    and node.func.value.id == "shutil"
                ):
                    dst_node = node.args[1] if len(node.args) > 1 else None
                    self.assertIsNotNone(dst_node)
                    dst_names = [n.id for n in ast.walk(dst_node) if isinstance(n, ast.Name)]
                    self.assertTrue(
                        any(nm.startswith("tmp_") for nm in dst_names),
                        f"shutil.copy2 must copy only into tmp_* dir in {label}",
                    )
            is_dyn_import = (isinstance(node.func, ast.Name) and node.func.id in ("__import__", "import_module")) or (
                isinstance(node.func, ast.Attribute) and node.func.attr == "import_module"
            )
            if is_dyn_import:
                dyn_targets: list[str] = []
                for arg_idx in (0, 1):
                    if (
                        len(node.args) > arg_idx
                        and isinstance(node.args[arg_idx], ast.Constant)
                        and isinstance(node.args[arg_idx].value, str)
                    ):
                        dyn_targets.append(node.args[arg_idx].value)
                for kw in node.keywords:
                    if (
                        kw.arg in ("name", "package")
                        and isinstance(kw.value, ast.Constant)
                        and isinstance(kw.value.value, str)
                    ):
                        dyn_targets.append(kw.value.value)
                for dyn_target in dyn_targets:
                    dyn_parts = {p for p in dyn_target.split(".") if p}
                    self.assertTrue(
                        FORBIDDEN_IMPORT_NAMES.isdisjoint(dyn_parts),
                        f"Forbidden dynamic import {dyn_target!r} in {label}",
                    )


# ---------------------------------------------------------------------------
# notebooks/full_planner.ipynb
# ---------------------------------------------------------------------------

FULL_PLANNER_PATH = REPO_ROOT / "notebooks" / "full_planner.ipynb"
FULL_PLANNER_METADATA_DIR = REPO_ROOT / "notebooks" / "metadata"
FULL_PLANNER_METADATA_FILES = (
    "buildings.json",
    "technologies.json",
    "ships.json",
    "defenses.json",
    "resources.json",
    "planner_settings.json",
)
# Sections whose code cell generates data, in notebook order. Section 10 prints each one in its own cell.
FULL_PLANNER_DATA_SECTIONS = [
    "1.1",
    "1.6",
    "2.3",
    "3",
    "4",
    "5",
    "6",
    "6.1",
    "6.2",
    "6.3",
    "6.4",
    "7.2",
    "7.3",
    "8.1",
    "8.2",
    "9",
]
# Section 11, the path to the saved configuration, follows the print cells. Its cells print their own data,
# except 11.1, which only sets the meta parameters that 11.2 prints.
FULL_PLANNER_PATH_SECTIONS = ["11.1", "11.2", "11.3", "11.4", "11.5", "11.6", "11.7", "11.8"]
# The only file that full_planner.ipynb writes: the actions of the best path as text, in its METADATA_DIR.
FULL_PLANNER_PATH_FILE = "full_planner_path.txt"
# What Section 11 has in common with the optimizer cells of config_optimizer.ipynb, character for character.
FULL_PLANNER_SHARED_WITH_OPTIMIZER = {
    "WAREHOUSE_KEYS",
    "PRODUCTION_FLOW_KEYS",
    "building_level_flows",
    "warehouse_level_for",
}
# pyproject.toml dependencies whose import name differs from the distribution name.
REQUIREMENT_IMPORT_NAMES = {"python-dotenv": "dotenv", "beautifulsoup4": "bs4"}

# Runs a notebook in a fresh interpreter with some modules made unimportable, then reports what was imported.
# argv: notebook path, working directory, JSON list of blocked top-level module names and, optionally, a folder
# that replaces the notebook's METADATA_DIR as soon as a cell has defined it.
FULL_PLANNER_SUBPROCESS_DRIVER = """
import contextlib, importlib.abc, io, json, os, pathlib, sys

blocked = set(json.loads(sys.argv[3]))
metadata_dir = pathlib.Path(sys.argv[4]) if len(sys.argv) > 4 else None


class Blocker(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname.split(".")[0] in blocked:
            raise ImportError("blocked for this test: " + fullname)
        return None


sys.meta_path.insert(0, Blocker())
with open(sys.argv[1], encoding="utf-8") as handle:
    cells = json.load(handle)["cells"]
os.chdir(sys.argv[2])
namespace = {"__name__": "__main__"}
with contextlib.redirect_stdout(io.StringIO()):
    for cell in cells:
        source = "".join(cell["source"])
        if cell["cell_type"] == "code" and not source.lstrip().startswith("!"):
            had_metadata_dir = "METADATA_DIR" in namespace
            exec(compile(source, "notebook", "exec"), namespace, namespace)
            if metadata_dir is not None and not had_metadata_dir and "METADATA_DIR" in namespace:
                namespace["METADATA_DIR"] = metadata_dir
print(json.dumps(sorted({name.split(".")[0] for name in sys.modules})))
"""


def _is_generated_data(name: str, value: object) -> bool:
    """True for a public notebook global that holds data: not a callable, a module, or a static source constant."""
    return not (
        name.startswith("_")
        or callable(value)
        or isinstance(value, types.ModuleType)
        or name.startswith("SQL_")
        or name.endswith("_COLUMNS")
    )


def _last_heading_number(markdown: str) -> str | None:
    """Section number of the last `##` or `###` heading of a markdown cell (`"6.1"` for `### 6.1 Buildings`)."""
    numbers = re.findall(r"(?m)^#{2,3} (\d+(?:\.\d+)*)\.? ", markdown)
    return numbers[-1] if numbers else None


def _requirement_name(spec: str) -> str:
    """Distribution name of a requirement such as `pandas>=2.2,<4`."""
    return re.split(r"[<>=!~ ]", spec.strip(), maxsplit=1)[0].lower()


def _undominated_rows(points: np.ndarray) -> np.ndarray:
    """The distinct rows of `points` that no other row matches or beats in every column."""
    unique = np.unique(points, axis=0)
    beaten = [bool(((unique >= row).all(axis=1) & (unique > row).any(axis=1)).any()) for row in unique]
    return unique[~np.array(beaten)]


def _databases_opened_by(call: object) -> list[Path]:
    """Every database path that `sqlite3.connect` is asked to open while `call()` runs."""
    opened: list[Path] = []
    real_connect = sqlite3.connect

    def recording_connect(database: object, *args: object, **kwargs: object) -> sqlite3.Connection:
        opened.append(Path(str(database)).resolve())
        return real_connect(database, *args, **kwargs)

    sqlite3.connect = recording_connect
    try:
        call()
    finally:
        sqlite3.connect = real_connect
    return opened


def _three_planet_state() -> GameState:
    """The sample account with a Research lab and Weapons factory at home, a second, colder colony and an asteroid."""
    state = _sample_snapshot_state()
    home = dataclasses.replace(state.planets[0], buildings={**state.planets[0].buildings, "b8": 1, "b9": 2})
    cold_colony = dataclasses.replace(
        home,
        planet_id=20001,
        name="Cold Colony",
        coords="02:381:15",
        temperature=-40,
        fields_used=15,
        buildings={"b4": 5, "b7": 3, "b8": 2, "b9": 4, "b17": 1},
        ships={"s4": 12, "s9": 3},
        defense={"d1": 7},
        building_offers=(),
    )
    asteroid = dataclasses.replace(
        home,
        planet_id=30007,
        name="Rock",
        coords="02:380:12A",
        planet_type="asteroid",
        temperature=12,
        fields_used=4,
        fields_max=40,
        buildings={"b7": 2, "b9": 2},
        building_offers=(),
    )
    return dataclasses.replace(state, planets=(home, cold_colony, asteroid))


_NOTEBOOK_SQLITE_DDL = """
CREATE TABLE IF NOT EXISTS meta (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL,
    updated_ts REAL NOT NULL DEFAULT 0.0
);
CREATE TABLE IF NOT EXISTS planet_snapshots (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts REAL NOT NULL DEFAULT 0.0,
    planet_id INTEGER NOT NULL,
    coords TEXT NOT NULL DEFAULT '',
    name TEXT NOT NULL DEFAULT '',
    planet_type TEXT NOT NULL DEFAULT 'normal',
    temperature INTEGER NOT NULL DEFAULT 0,
    fields_used INTEGER NOT NULL DEFAULT 0,
    fields_max INTEGER NOT NULL DEFAULT 0,
    pig_iron INTEGER NOT NULL DEFAULT 0,
    metal INTEGER NOT NULL DEFAULT 0,
    kryptonite INTEGER NOT NULL DEFAULT 0,
    spice INTEGER NOT NULL DEFAULT 0,
    energy_produced INTEGER NOT NULL DEFAULT 0,
    energy_used INTEGER NOT NULL DEFAULT 0,
    buildings_json TEXT NOT NULL DEFAULT '{}',
    ships_json TEXT NOT NULL DEFAULT '{}',
    defense_json TEXT NOT NULL DEFAULT '{}',
    researches_json TEXT NOT NULL DEFAULT '{}',
    economy_rates_json TEXT NOT NULL DEFAULT '{}'
);
CREATE TABLE IF NOT EXISTS raid_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    target_coords TEXT NOT NULL,
    attacked_ts REAL NOT NULL,
    is_exempt INTEGER NOT NULL DEFAULT 0,
    outcome TEXT NOT NULL DEFAULT '',
    loot_json TEXT NOT NULL DEFAULT '{}'
);
CREATE TABLE IF NOT EXISTS action_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts REAL NOT NULL DEFAULT 0.0,
    kind TEXT NOT NULL DEFAULT '',
    executed INTEGER NOT NULL DEFAULT 0,
    dry_run INTEGER NOT NULL DEFAULT 0,
    planet_coords TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_action_history_ts ON action_history(ts);
CREATE TABLE IF NOT EXISTS battle_reports (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts REAL NOT NULL DEFAULT 0.0,
    msg_uid TEXT NOT NULL DEFAULT '',
    server_msg_id TEXT NOT NULL DEFAULT '',
    battle_id TEXT NOT NULL DEFAULT '',
    msg_date_text TEXT NOT NULL DEFAULT '',
    target_coords TEXT NOT NULL DEFAULT '',
    attacker TEXT NOT NULL DEFAULT '',
    defender TEXT NOT NULL DEFAULT '',
    outcome TEXT NOT NULL DEFAULT '',
    rounds INTEGER NOT NULL DEFAULT 0,
    pig_iron INTEGER NOT NULL DEFAULT 0,
    metal INTEGER NOT NULL DEFAULT 0,
    kryptonite INTEGER NOT NULL DEFAULT 0,
    spice INTEGER NOT NULL DEFAULT 0,
    loot_spice_eq REAL NOT NULL DEFAULT 0.0,
    attacker_lost_spice REAL NOT NULL DEFAULT 0.0,
    defender_lost_spice REAL NOT NULL DEFAULT 0.0,
    debris_spice_eq REAL NOT NULL DEFAULT 0.0
);
CREATE TABLE IF NOT EXISTS game_messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    msg_uid TEXT NOT NULL DEFAULT '',
    ingested_ts REAL NOT NULL DEFAULT 0.0,
    deleted_in_game INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS spy_targets (
    coords TEXT PRIMARY KEY,
    status_color TEXT NOT NULL DEFAULT 'unknown',
    is_inactive INTEGER NOT NULL DEFAULT 0,
    is_spacia INTEGER NOT NULL DEFAULT 0,
    bash_hits INTEGER NOT NULL DEFAULT 0,
    is_own_planet INTEGER NOT NULL DEFAULT 0,
    merged_into TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS recycle_reports (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts REAL NOT NULL DEFAULT 0.0,
    coords TEXT NOT NULL DEFAULT '',
    recycler_count INTEGER NOT NULL DEFAULT 0,
    harvested_pig_iron INTEGER NOT NULL DEFAULT 0,
    harvested_metal INTEGER NOT NULL DEFAULT 0,
    harvested_kryptonite INTEGER NOT NULL DEFAULT 0,
    harvested_spice INTEGER NOT NULL DEFAULT 0,
    harvested_spice_eq REAL NOT NULL DEFAULT 0.0
);
CREATE TABLE IF NOT EXISTS bash_blocks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    target_coords TEXT NOT NULL DEFAULT '',
    blocked_until_ts REAL NOT NULL DEFAULT 0.0
);
CREATE TABLE IF NOT EXISTS universe_research_max (
    research_key TEXT PRIMARY KEY,
    research_id INTEGER NOT NULL DEFAULT 0,
    slug TEXT NOT NULL DEFAULT '',
    name TEXT NOT NULL DEFAULT '',
    own_level INTEGER NOT NULL DEFAULT 0,
    spied_max_level INTEGER NOT NULL DEFAULT 0,
    server_record_level INTEGER,
    universe_max_level INTEGER NOT NULL DEFAULT 0,
    catalog_max_level INTEGER,
    holder TEXT NOT NULL DEFAULT '',
    holder_coords TEXT NOT NULL DEFAULT '',
    updated_ts REAL NOT NULL DEFAULT 0.0
);
CREATE TABLE IF NOT EXISTS spy_reports (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts REAL NOT NULL DEFAULT 0.0,
    target_coords TEXT NOT NULL DEFAULT '',
    owner TEXT NOT NULL DEFAULT '',
    researches_json TEXT NOT NULL DEFAULT '{}'
);
"""


def _add_universe_research(conn: sqlite3.Connection) -> None:
    """Fill the bot's `universe_research_max` ledger and archive six spy reports, four of them with researches."""
    conn.executemany(
        "INSERT INTO universe_research_max(research_key, research_id, slug, name, own_level, spied_max_level, "
        "server_record_level, universe_max_level, catalog_max_level, holder, holder_coords, updated_ts) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        [
            ("r1", 1, "mining-technology", "Mining technology", 0, 14, 27, 27, 40, "TopMiner", "01:010:02", 1.7e9),
            # The maximum of this row lags behind its own server record: it still says 20.
            ("r2", 2, "energy-technology", "Energy technology", 1, 12, 21, 20, 40, "TopMiner", "01:010:02", 1.7e9),
            ("r3", 3, "engine-research", "Engine research", 0, 9, None, 13, 35, "OldRecord", "03:200:11", 1.7e9),
            ("r7", 7, "spy-technology", "Spy technology", 4, 0, None, 4, 35, "Me", "", 1.7e9),
            (
                "urplasma-extraction",
                16,
                "urplasma-extraction",
                "Urplasma-extraction",
                0,
                3,
                None,
                3,
                None,
                "Raider",
                "02:383:02",
                1.7e9,
            ),
        ],
    )
    conn.executemany(
        "INSERT INTO spy_reports(ts, target_coords, owner, researches_json) VALUES (?, ?, ?, ?)",
        [
            (1_700_100_000.0, "02:383:02", "Raider", '{"r8": 7, "r13": 11, "r9": 3}'),
            (1_700_110_000.0, "02:390:04", "Miner", '{"Weapons technology": 9, "shield-technology": 5, "r99": 30}'),
            (1_700_120_000.0, "02:391:07", "Late", '{"r8": 9, "r1": 15, "r10": 0, "r9": "x"}'),
            (1_700_130_000.0, "02:392:01", "NoResearch", "{}"),
            (1_700_140_000.0, "02:393:01", "Broken", "not json"),
            (1_700_150_000.0, "02:394:01", "", '{"r4": 12, "r13": 4}'),
        ],
    )
    conn.commit()


def _seed_sqlite_state_snapshot(conn: sqlite3.Connection, state: GameState) -> None:
    payload = json.dumps(dataclasses.asdict(state), sort_keys=True)
    conn.execute(
        "INSERT OR REPLACE INTO meta(key, value, updated_ts) VALUES ('latest_game_state', ?, ?)",
        (payload, float(state.timestamp)),
    )
    res_json = json.dumps(dict(state.researches or {}), sort_keys=True)
    for p in state.planets:
        conn.execute(
            "INSERT INTO planet_snapshots("
            "ts, planet_id, coords, name, planet_type, temperature, fields_used, fields_max, "
            "pig_iron, metal, kryptonite, spice, energy_produced, energy_used, "
            "buildings_json, ships_json, defense_json, researches_json, economy_rates_json"
            ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                float(state.timestamp),
                int(p.planet_id),
                str(p.coords),
                str(p.name),
                str(p.planet_type),
                int(p.temperature),
                int(p.fields_used),
                int(p.fields_max),
                int(p.resources.pig_iron),
                int(p.resources.metal),
                int(p.resources.kryptonite),
                int(p.resources.spice),
                int(p.energy.produced),
                int(p.energy.used),
                json.dumps(dict(p.buildings or {}), sort_keys=True),
                json.dumps(dict(p.ships or {}), sort_keys=True),
                json.dumps(dict(p.defense or {}), sort_keys=True),
                res_json,
                json.dumps(dict(p.economy_rates or {}), sort_keys=True),
            ),
        )


def _build_full_planner_workspace(
    root: Path,
    *,
    with_attacks: bool,
    state: GameState | None = None,
    universe_research: bool = False,
) -> Path:
    """Create `root/state/spaceinvasion.db`: empty, or with one planet, three raid dispatches and three battles.

    `state` replaces the one-planet sample account that is snapshotted when `with_attacks` is set, and
    `universe_research` adds what `_add_universe_research` records about other players' research.
    """
    (root / "state").mkdir(parents=True)
    db_file = root / "state" / "spaceinvasion.db"
    with contextlib.closing(sqlite3.connect(db_file)) as conn:
        conn.executescript(_NOTEBOOK_SQLITE_DDL)
        if with_attacks:
            _seed_sqlite_state_snapshot(conn, state if state is not None else _sample_snapshot_state())
            conn.executemany(
                "INSERT INTO raid_history(target_coords, attacked_ts, is_exempt, outcome, loot_json) "
                "VALUES (?, ?, 1, 'Dispatched attack', '{}')",
                [
                    ("02:383:02", 1_700_000_000.0),
                    ("02:383:02", 1_700_001_000.0),
                    ("02:378:15", 1_700_003_000.0),
                ],
            )
            conn.executemany(
                "INSERT INTO battle_reports("
                "ts, msg_uid, server_msg_id, battle_id, msg_date_text, target_coords, "
                "attacker, defender, outcome, rounds, pig_iron, metal, kryptonite, spice, loot_spice_eq"
                ") VALUES (?, ?, ?, ?, ?, ?, 'Me', 'Target', ?, 1, ?, ?, ?, ?, ?)",
                [
                    (
                        1_700_000_300.0,
                        "m1",
                        "1",
                        "b1",
                        "2023-11-14 22:18:20",
                        "02:383:02",
                        "win",
                        6000,
                        2500,
                        3000,
                        1000,
                        4000.0,
                    ),
                    (
                        1_700_001_360.0,
                        "m2",
                        "2",
                        "b2",
                        "2023-11-14 22:36:00",
                        "02:383:02",
                        "win",
                        3000,
                        1250,
                        1500,
                        500,
                        2000.0,
                    ),
                    (1_700_003_200.0, "m3", "3", "b3", "2023-11-14 23:06:40", "02:378:15", "loss", 0, 0, 0, 0, 0.0),
                ],
            )
            conn.commit()
        if universe_research:
            _add_universe_research(conn)
    return root


def _run_full_planner(
    code_cells: list[dict],
    workspace: Path,
    *,
    metadata_dir: Path | None = None,
    last_cell: int | None = None,
    overrides: dict[str, object] | None = None,
    label: str = "full_planner",
) -> tuple[dict, dict[int, str], dict[int, set[str]]]:
    """Execute a notebook's code cells with `workspace` as working directory (the pip cell is skipped).

    Returns the namespace and, per code-cell index, the captured stdout and the global names the cell added.
    `metadata_dir` replaces the notebook's `METADATA_DIR` as soon as a cell has defined it, and `overrides`
    does the same for any other global. `last_cell` stops the run after that code cell, and `label` names the
    notebook in tracebacks. The pandas display options the notebook sets are restored.
    """
    replaced = overrides or {}
    ns: dict = {"__name__": "__main__"}
    stdout: dict[int, str] = {}
    new_names: dict[int, set[str]] = {}
    display_options = ("display.max_columns", "display.width", "display.max_colwidth")
    saved_options = {option: pd.get_option(option) for option in display_options}
    orig_cwd = Path.cwd()
    os.chdir(workspace)
    try:
        for idx, cell in enumerate(code_cells[1:], start=1):
            if last_cell is not None and idx > last_cell:
                break
            names_before = set(ns)
            cell_buf = io.StringIO()
            with contextlib.redirect_stdout(cell_buf):
                exec(compile(_cell_source(cell), f"{label}_cell_{idx}", "exec"), ns, ns)
            stdout[idx] = cell_buf.getvalue()
            new_names[idx] = set(ns) - names_before
            if metadata_dir is not None and "METADATA_DIR" in new_names[idx]:
                ns["METADATA_DIR"] = metadata_dir
            for name in new_names[idx] & set(replaced):
                ns[name] = replaced[name]
    finally:
        os.chdir(orig_cwd)
        for option, value in saved_options.items():
            pd.set_option(option, value)
    return ns, stdout, new_names


def _path_section_test_parameters() -> dict[str, object]:
    """Section 11 settings of the test runs: a genetic algorithm small enough to keep the runs short."""
    return {"PATH_GA_POPULATION_SIZE": 4, "PATH_GA_GENERATIONS": 1}


def _save_configuration_for(workspace: Path, metadata_dir: Path, overrides: dict[str, object] | None = None) -> Path:
    """Run config_optimizer.ipynb on `workspace`, so that `metadata_dir` holds the configuration that Section 11
    of full_planner.ipynb reads for that workspace. Returns the saved file."""
    cells = [
        cell
        for cell in json.loads(CONFIG_OPTIMIZER_PATH.read_text(encoding="utf-8"))["cells"]
        if cell.get("cell_type") == "code"
    ]
    _run_config_optimizer(
        cells, workspace, metadata_dir, overrides={"GA_POPULATION_SIZE": 60, "GA_GENERATIONS": 40, **(overrides or {})}
    )
    return metadata_dir / CONFIG_OPTIMIZER_RESULT_FILE


def _catalog_planet_income(
    levels: dict[str, int], temperature: int, mining: int, energy_tech: int, solar_cells: int, speed: float
) -> tuple[float, float, float, float, float]:
    """What a planet makes per hour, worked out from the catalog rows and the rules of the game alone.

    Returns `(pig iron mined, pig iron left while the furnace runs, metal while the furnace runs, kryptonite,
    spice)`. Nothing of the notebook is used: this is the yardstick for its production model.
    """
    pig = kryptonite = spice = metal = pig_used = spice_used = produced = used = 0.0
    for key, level in levels.items():
        building_id = int(key[1:])
        if building_id > 6 or level <= 0:
            continue
        row = game_data.BUILDING_BY_ID[building_id]["levels"][level]
        energy = float(row.get("energy") or 0.0)
        if energy > 0.0:
            produced += energy * (1.0 + 0.01 * energy_tech)
        else:
            used -= energy
        if building_id <= 4:
            if row.get("prod_by_temp"):
                made = float(game_data.interpolate_temp_value(row["prod_by_temp"], temperature))
            else:
                made = float(row.get("prod", 0.0))
            made *= speed * (1.0 if building_id == 2 else 1.0 + 0.02 * mining)
            if building_id == 1:
                pig += made
            elif building_id == 2:
                metal += made
                pig_used += speed * float(row.get("pig_iron_used") or 0.0)
            elif building_id == 3:
                kryptonite += made
            else:
                spice += made
        spice_used += float(row.get("spice_used") or 0.0)
    if solar_cells:
        table = game_data.SHIPS["solar-cells"]["solar_cell_energy_by_temp"]
        per_cell = game_data.interpolate_temp_value(table, temperature)
        produced += solar_cells * float(per_cell) * (1.0 + 0.01 * energy_tech)
    power = min(1.0, produced / used) if used > 0.0 else 1.0
    mined = pig * power
    wanted = pig_used * power
    smelting = min(1.0, mined / wanted) if wanted > 0.0 else 1.0
    return mined, mined - wanted * smelting, metal * power * smelting, kryptonite * power, spice * power - spice_used


def _path_rule_violations(
    problem: dict,
    attack_income: dict[str, float],
    run: dict,
    *,
    slot_attack_income: dict[str, float] | None = None,
    fleet_command_center_level: int | None = None,
    spare_percent: dict[str, float] | None = None,
    reward_weeks: int = 26,
    weekly_discount: float = 0.90,
    limit: int = 12,
) -> list[str]:
    """Replay a simulated path (either `horizon` or `reached`) and list every rule or reward mismatch."""
    resources = ("pig_iron", "metal", "kryptonite", "spice")
    warehouses = ("b11", "b12", "b13", "b14")
    steps, planets = problem["steps"], problem["planets"]
    speed = float(problem["speedup_factor"])
    attack = [float(attack_income[resource]) for resource in resources]
    slot_attack = [float((slot_attack_income or {}).get(resource, 0.0)) for resource in resources]
    keep = [1.0 - float((spare_percent or {}).get(resource, 0.0)) / 100.0 for resource in resources]
    fixed_cc = None if fleet_command_center_level is None else int(fleet_command_center_level)
    start, end = run["start"], run["end"]
    stop_hour = float(run["stopped_at_hours"])
    horizon_hours = 168.0 * int(reward_weeks)
    found: list[str] = []

    def label(step: int) -> str:
        details = steps[step]
        where = planets[details["planet"]]["coords"] if details["planet"] >= 0 else "account"
        return f"#{step} {details['kind']} {details['name']} {details['level_from']}->{details['level_to']} ({where})"

    if run["status"] not in ("reached", "horizon"):
        return [f"the path is {run['status']}"]
    if run["status"] == "reached":
        missing = [label(step) for step in range(len(steps)) if start[step] is None or end[step] is None]
        if missing:
            return [f"{len(missing)} steps never ran, for example {missing[0]}"]
        if abs(max(end, default=0.0) - run["hours"]) > 1e-9 * max(1.0, run["hours"]):
            found.append(f"hours is {run['hours']}, the last step ends at {max(end)}")
    else:
        late = [label(step) for step in run["order"] if start[step] is None or float(start[step]) >= stop_hour]
        if late:
            found.append(f"steps started at or after horizon {stop_hour}: {late[0]}")

    levels = [dict(planet["levels"]) for planet in planets]
    research = dict(problem["research_levels"])
    solar = [int(planet["solar_cells"]) for planet in planets]
    founded = [bool(planet["founded"]) for planet in planets]
    fields = [sum(planet_levels.values()) for planet_levels in levels]
    stock = [float(amount) for amount in problem["start_stock"]]
    busy: dict[int, int] = {}
    started = [False] * len(steps)
    ended = [False] * len(steps)
    before: dict[int, int] = {}
    chains: dict[tuple, list[int]] = {}
    for details in steps:
        chains.setdefault(tuple(details["chain"]), []).append(details["id"])
    for members in chains.values():
        before.update(zip(members[1:], members))

    def active_slots() -> int:
        return sum(
            1 + (levels[idx].get("b10", 0) if fixed_cc is None else fixed_cc)
            for idx in range(len(planets))
            if founded[idx]
        )

    def income(furnace_on: bool) -> list[float]:
        slots = active_slots()
        gross = [attack[pos] + slots * slot_attack[pos] for pos in range(4)]
        for index, planet in enumerate(planets):
            if founded[index]:
                mined, left, metal, kryptonite, spice = _catalog_planet_income(
                    levels[index], int(planet["temperature_c"]), research["r1"], research["r2"], solar[index], speed
                )
                gross[0] += left if furnace_on else mined
                gross[1] += metal if furnace_on else 0.0
                gross[2] += kryptonite
                gross[3] += spice
        return [keep[pos] * gross[pos] for pos in range(4)]

    def steady_spice_eq_per_hour() -> float:
        rates = (1.0 / 6.0, 1.0 / 2.5, 1.0 / 3.0, 1.0)
        total = 0.0
        for index, planet in enumerate(planets):
            if founded[index]:
                _, left, metal, kryptonite, spice = _catalog_planet_income(
                    levels[index], int(planet["temperature_c"]), research["r1"], research["r2"], solar[index], speed
                )
                total += (
                    keep[0] * left * rates[0]
                    + keep[1] * metal * rates[1]
                    + keep[2] * kryptonite * rates[2]
                    + keep[3] * spice * rates[3]
                )
        return total

    weekly_spice = [0.0] * int(reward_weeks)

    def accrue_spice(t0: float, dt: float) -> None:
        if dt <= 0.0 or t0 >= horizon_hours:
            return
        t1 = min(horizon_hours, t0 + dt)
        rate = steady_spice_eq_per_hour()
        w = int(t0 // 168.0)
        cur = t0
        while cur < t1 and w < int(reward_weeks):
            nxt = min(t1, (w + 1) * 168.0)
            if nxt > cur:
                weekly_spice[w] += rate * (nxt - cur)
            cur = nxt
            w += 1

    def duration(step: int) -> float:
        details = steps[step]
        if details["kind"] == "colonize":
            return details["base_hours"]
        if details["kind"] == "research":
            return details["base_hours"] / max(1.0, (levels[problem["lab_planet"]].get("b8", 0) + 1) * speed)
        here = levels[details["planet"]]
        accelerator = 2 ** min(here.get("b17", 0), 40)
        if details["kind"] == "ships":
            return details["base_hours"] / max(1.0, max(1, here.get("b9", 0)) * accelerator * speed)
        return details["base_hours"] / max(1.0, (here.get("b7", 0) + 1) * accelerator * speed)

    def obstacle(step: int) -> str | None:
        details = steps[step]
        planet = details["planet"]
        if step in before and not ended[before[step]]:
            return "the level before it is not finished"
        if planet >= 0 and details["kind"] != "colonize" and not founded[planet]:
            return "its planet is not founded"
        if details["queue"] >= 0 and details["queue"] in busy:
            return f"its queue is busy with {label(busy[details['queue']])}"
        for owner, key, level in details["requires"]:
            have = research.get(key, 0) if owner < 0 else levels[owner].get(key, 0)
            if have < level:
                return f"it needs {key} {level} and there is {have}"
        paying = levels[details["pay_planet"]]
        for position, amount in enumerate(details["cost"]):
            capacity = 100_000 * 2 ** paying.get(warehouses[position], 0)
            if amount > capacity:
                return f"its {resources[position]} cost {amount:.6g} exceeds the warehouse capacity {capacity:.6g}"
        if details["kind"] == "build" and fields[planet] >= int(planets[planet]["fields_max"]):
            return "its planet has no free field"
        return None

    def short_of(step: int, clock: float) -> dict[str, float]:
        unseen = 16.0 * math.ulp(max(clock, 1.0))
        on, off = income(True), income(False)
        cost = steps[step]["cost"]
        return {
            resources[position]: cost[position] - stock[position]
            for position in range(4)
            if cost[position] - stock[position]
            > 1e-9 * cost[position] + 1e-6 + unseen * max(on[position], off[position])
        }

    spans = sorted(run["smelting_spans"])
    span = 0
    starts_at: dict[float, list[int]] = {}
    ends_at: dict[float, list[int]] = {}
    for step in run["order"]:
        starts_at.setdefault(float(start[step]), []).append(step)
        if end[step] is not None and float(end[step]) <= stop_hour:
            ends_at.setdefault(float(end[step]), []).append(step)
    moments = sorted(
        {
            0.0,
            stop_hour,
            *starts_at,
            *ends_at,
            *(hour for pair in spans for hour in pair if hour <= stop_hour),
        }
    )
    clock = 0.0
    for moment in moments:
        if moment > clock:
            accrue_spice(clock, moment - clock)
            while span < len(spans) and spans[span][1] <= clock:
                span += 1
            rates = income(span < len(spans) and spans[span][0] <= clock < spans[span][1])
            for position in range(4):
                stock[position] += rates[position] * (moment - clock)
            clock = moment
        to_start = list(starts_at.get(moment, []))
        to_end = list(ends_at.get(moment, []))
        moved = True
        while moved and (to_start or to_end):
            moved = False
            for step in list(to_end):
                if not started[step]:
                    continue
                to_end.remove(step)
                moved = True
                ended[step] = True
                details = steps[step]
                if details["queue"] >= 0:
                    busy.pop(details["queue"], None)
                planet = details["planet"]
                if details["kind"] == "research":
                    research[details["key"]] = details["level_to"]
                elif details["kind"] == "colonize":
                    founded[planet] = True
                elif details["kind"] == "ships":
                    if details["key"] == "s9":
                        solar[planet] += details["level_to"]
                else:
                    if details["level_to"] > 0:
                        levels[planet][details["key"]] = details["level_to"]
                    else:
                        levels[planet].pop(details["key"], None)
                    if details["kind"] == "demolish":
                        fields[planet] -= 1
            for step in list(to_start):
                if obstacle(step) is not None or short_of(step, clock):
                    continue
                to_start.remove(step)
                moved = True
                started[step] = True
                details = steps[step]
                expected = duration(step)
                took = end[step] - start[step]
                if abs(took - expected) > 1e-9 * max(1.0, expected, start[step]):
                    found.append(f"{label(step)} took {took:.9g} h, the game gives {expected:.9g} h")
                for position in range(4):
                    stock[position] = max(0.0, stock[position] - details["cost"][position])
                if details["queue"] >= 0:
                    busy[details["queue"]] = step
                if details["kind"] == "build":
                    fields[details["planet"]] += 1
        for step in to_start:
            reason = obstacle(step) or f"the stock is short of {short_of(step, clock)}"
            found.append(f"{label(step)} started at {moment:.9g} h although {reason}")
            started[step] = True
        for step in to_end:
            found.append(f"{label(step)} ended at {moment:.9g} h before it started")
        if len(found) >= limit:
            return found[:limit]

    if clock < horizon_hours:
        accrue_spice(clock, horizon_hours - clock)
    expected_disc = sum((weekly_discount**w) * weekly_spice[w] for w in range(int(reward_weeks)))
    if abs(expected_disc - float(run["discounted_reward"])) > 1e-6 * max(1.0, expected_disc):
        found.append(f"discounted_reward is {run['discounted_reward']}, replay gives {expected_disc}")
    if levels != run["final_levels"]:
        found.append("final_levels at stop_hour does not match replay")
    if research != run["final_research"]:
        found.append("final_research at stop_hour does not match replay")
    if founded != run["final_founded"]:
        found.append("final_founded at stop_hour does not match replay")
    if active_slots() != int(run["final_fleet_slots"]):
        found.append(f"final_fleet_slots is {run['final_fleet_slots']}, replay gives {active_slots()}")
    if run["status"] == "reached":
        for index, planet in enumerate(planets):
            if levels[index] != {key: level for key, level in planet["target_levels"].items() if level > 0}:
                found.append(f"{planet['coords']} does not end with the levels of the configuration")
            if not founded[index]:
                found.append(f"{planet['coords']} was never founded")
        if research != problem["research_target"]:
            found.append("the research levels at the end are not the planned ones")
    return found[:limit]


@pytest.mark.notebooks
class TestFullPlannerNotebook(unittest.TestCase):
    """`notebooks/full_planner.ipynb`: layout rules, static metadata, generated data, printouts and the path section."""

    _assert_cell_ast_safe = _assert_notebook_cell_ast_safe

    @classmethod
    def setUpClass(cls) -> None:
        cls.nb = json.loads(FULL_PLANNER_PATH.read_text(encoding="utf-8"))
        cls.cells = cls.nb["cells"]
        cls.code_positions = [pos for pos, cell in enumerate(cls.cells) if cell.get("cell_type") == "code"]
        cls.code_cells = [cls.cells[pos] for pos in cls.code_positions]
        # Section 10 holds the print cells: the last cells of the notebook.
        cls.print_idxs = [idx for idx, cell in enumerate(cls.code_cells) if cls._is_print_cell(cell)]
        cls.first_print_idx = cls.print_idxs[0]
        # Section 11 holds the path search: the code cells after the last print cell.
        cls.path_idxs = list(range(cls.print_idxs[-1] + 1, len(cls.code_cells)))
        cls.universe_research_cell_idx = next(
            idx
            for idx, pos in enumerate(cls.code_positions)
            if re.search(r"(?m)^## 9\. ", _cell_source(cls.cells[pos - 1]))
        )
        cls.metadata_cell_idx = next(
            idx for idx, cell in enumerate(cls.code_cells) if re.search(r"(?m)^def load_metadata\(", _cell_source(cell))
        )
        tmp = tempfile.TemporaryDirectory(prefix="si_fp_test_")
        cls.addClassCleanup(tmp.cleanup)
        cls.attack_workspace = _build_full_planner_workspace(Path(tmp.name) / "attacks", with_attacks=True)
        cls.cold_workspace = _build_full_planner_workspace(Path(tmp.name) / "cold", with_attacks=False)
        cls.three_planet_workspace = _build_full_planner_workspace(
            Path(tmp.name) / "three_planets", with_attacks=True, state=_three_planet_state()
        )
        cls.universe_workspace = _build_full_planner_workspace(
            Path(tmp.name) / "universe", with_attacks=True, universe_research=True
        )
        cls._attack_run_cache = None
        cls.attack_workspace_before_after = None
        cls.attack_metadata_before_after = None
        cls._other_runs: dict[Path, tuple[dict, dict[int, str], dict[int, set[str]]]] = {}
        cls._metadata_root = Path(tmp.name) / "saved_configurations"
        cls._metadata_dirs: dict[Path, Path] = {}
        cls.repo_metadata_before = cls._workspace_fingerprint(FULL_PLANNER_METADATA_DIR)

    @classmethod
    def tearDownClass(cls) -> None:
        # Section 11 reads a saved configuration; every test run takes it from its own copy of the folder.
        if cls._workspace_fingerprint(FULL_PLANNER_METADATA_DIR) != cls.repo_metadata_before:
            raise AssertionError("A test run changed notebooks/metadata/")

    @classmethod
    def _metadata_for(cls, workspace: Path) -> Path:
        """A copy of notebooks/metadata/ that holds the configuration config_optimizer.ipynb saves for `workspace`.

        The copy leaves out the two files that the notebooks write: the configuration is saved again for
        `workspace`, and the path text has to come from the run of the test itself.
        """
        if workspace not in cls._metadata_dirs:
            meta_dir = cls._metadata_root / workspace.name / "metadata"
            shutil.copytree(
                FULL_PLANNER_METADATA_DIR,
                meta_dir,
                ignore=shutil.ignore_patterns(CONFIG_OPTIMIZER_RESULT_FILE, FULL_PLANNER_PATH_FILE),
            )
            _save_configuration_for(workspace, meta_dir)
            cls._metadata_dirs[workspace] = meta_dir
        return cls._metadata_dirs[workspace]

    @staticmethod
    def _is_print_cell(cell: dict) -> bool:
        return cell.get("cell_type") == "code" and _cell_source(cell).lstrip().startswith("print_section_data(")

    @staticmethod
    def _workspace_fingerprint(workspace: Path) -> dict[str, str]:
        return {
            str(path.relative_to(workspace)): _sha256_bytes(path)
            for path in sorted(workspace.rglob("*"))
            if path.is_file()
        }

    @classmethod
    def _attack_run(cls) -> tuple[dict, dict[int, str], dict[int, set[str]]]:
        """The notebook executed once on the workspace with three attacks (cached for the whole class)."""
        if cls._attack_run_cache is None:
            meta_dir = cls._metadata_for(cls.attack_workspace)
            before = cls._workspace_fingerprint(cls.attack_workspace)
            meta_before = cls._workspace_fingerprint(meta_dir)
            run = _run_full_planner(
                cls.code_cells, cls.attack_workspace, metadata_dir=meta_dir, overrides=_path_section_test_parameters()
            )
            cls.attack_workspace_before_after = (before, cls._workspace_fingerprint(cls.attack_workspace))
            cls.attack_metadata_before_after = (meta_before, cls._workspace_fingerprint(meta_dir))
            cls._attack_run_cache = run
        return cls._attack_run_cache

    @classmethod
    def _cached_run(cls, workspace: Path) -> tuple[dict, dict[int, str], dict[int, set[str]]]:
        """The notebook executed once on another workspace of the class (cached like `_attack_run`)."""
        if workspace not in cls._other_runs:
            cls._other_runs[workspace] = _run_full_planner(
                cls.code_cells,
                workspace,
                metadata_dir=cls._metadata_for(workspace),
                overrides=_path_section_test_parameters(),
            )
        return cls._other_runs[workspace]

    def _section_cells(self) -> list[tuple[str, int, int]]:
        """`(section number, data code-cell index, print code-cell index)` of every data-producing cell, in order."""
        ns, _, new_names = self._attack_run()
        data_idxs = [
            idx
            for idx in range(2, self.first_print_idx)  # code cells 0 and 1 install packages and import modules
            if any(_is_generated_data(name, ns[name]) for name in new_names[idx])
        ]
        self.assertEqual(
            len(self.print_idxs),
            len(data_idxs),
            "Every data-producing code cell needs exactly one Section 10 print cell, in the same order",
        )
        sections: list[tuple[str, int, int]] = []
        for data_idx, print_idx in zip(data_idxs, self.print_idxs):
            section = _last_heading_number(_cell_source(self.cells[self.code_positions[data_idx] - 1]))
            self.assertIsNotNone(section, f"Code cell {data_idx} has no numbered heading above it")
            sections.append((section, data_idx, print_idx))
        return sections

    def _edited_metadata_dir(self, parent: Path, edits: dict) -> Path:
        """Copy `notebooks/metadata/` into `parent` and apply `{file name: function(document)}` edits."""
        meta_dir = parent / "metadata"
        shutil.copytree(FULL_PLANNER_METADATA_DIR, meta_dir)
        for file_name, change in edits.items():
            document = json.loads((meta_dir / file_name).read_text(encoding="utf-8"))
            change(document)
            (meta_dir / file_name).write_text(json.dumps(document), encoding="utf-8")
        return meta_dir

    # ------------------------------------------------------------------ layout and hygiene

    def test_layout_one_described_code_cell_per_component(self) -> None:
        self.assertEqual((self.nb.get("nbformat"), self.nb.get("nbformat_minor")), (4, 5))
        cell_ids = [cell.get("id") for cell in self.cells]
        self.assertTrue(all(isinstance(cell_id, str) and cell_id.strip() for cell_id in cell_ids))
        self.assertEqual(len(set(cell_ids)), len(cell_ids), "Cell ids must be unique")
        for pos, cell in enumerate(self.cells):
            self.assertIn(cell.get("cell_type"), ("markdown", "code"))
            self.assertTrue(_cell_source(cell).strip(), f"Cell {pos} is empty")
        # The notebook opens with its title and what it reads.
        self.assertEqual(self.cells[0].get("cell_type"), "markdown")
        self.assertTrue(_cell_source(self.cells[0]).startswith("# Full Planner"))

        for idx, pos in enumerate(self.code_positions):
            cell = self.cells[pos]
            self.assertGreater(pos, 0)
            self.assertEqual(
                self.cells[pos - 1].get("cell_type"),
                "markdown",
                f"Code cell {idx} needs its markdown description directly above it",
            )
            self.assertIsNotNone(
                _last_heading_number(_cell_source(self.cells[pos - 1])),
                f"The description above code cell {idx} needs a numbered heading",
            )
            src = _cell_source(cell)
            if idx == 0:
                continue
            for ln in src.splitlines():
                self.assertFalse(
                    ln.lstrip().startswith(("%", "!")),
                    f"Only the first code cell may hold shell or magic lines: {ln!r}",
                )
            if idx != len(self.code_cells) - 1:  # the last cell saves the path as text (checked below)
                self._assert_cell_ast_safe(src, f"full_planner code cell {idx}")

        # Section 0: the first code cell only installs packages.
        pip_lines = [
            ln.strip()
            for ln in _cell_source(self.code_cells[0]).splitlines()
            if ln.strip() and not ln.strip().startswith("#")
        ]
        self.assertGreater(len(pip_lines), 0, "The first code cell must hold the !pip install lines")
        for ln in pip_lines:
            self.assertTrue(ln.startswith("!pip install "), f"The first code cell must only install packages: {ln!r}")

        # Section 0.1: every import is in the second code cell, uses the canonical package path, and is used.
        cells_with_imports = [
            idx
            for idx, cell in enumerate(self.code_cells[1:], start=1)
            if any(isinstance(node, (ast.Import, ast.ImportFrom)) for node in ast.walk(ast.parse(_cell_source(cell))))
        ]
        self.assertEqual(cells_with_imports, [1], "Every import belongs in the Imports cell")
        imported_names: set[str] = set()
        for node in ast.walk(ast.parse(_cell_source(self.code_cells[1]))):
            if isinstance(node, ast.Import):
                imported_names.update((alias.asname or alias.name).split(".")[0] for alias in node.names)
            elif isinstance(node, ast.ImportFrom):
                imported_names.update(alias.asname or alias.name for alias in node.names)
                if (node.module or "").split(".")[0] == "backend":
                    self.assertEqual(
                        importlib.import_module(node.module).__name__,
                        node.module,
                        f"Import {node.module} through its real package path, not a legacy alias",
                    )
        used_names = {
            node.id
            for cell in self.code_cells[1:]
            for node in ast.walk(ast.parse(_cell_source(cell)))
            if isinstance(node, ast.Name)
        }
        self.assertEqual(sorted(imported_names - used_names), [], "The Imports cell imports names nothing uses")

        # Section 1: helpers start in the Utils section, with resolve_repo_root in its first cell.
        self.assertIn("## 1. Utils", _cell_source(self.cells[self.code_positions[2] - 1]))
        utils_functions = {
            node.name for node in ast.parse(_cell_source(self.code_cells[2])).body if isinstance(node, ast.FunctionDef)
        }
        self.assertIn("resolve_repo_root", utils_functions)

        # Section 10: one block of alternating heading / print cells, one print call per cell.
        print_positions = [pos for pos, cell in enumerate(self.cells) if self._is_print_cell(cell)]
        self.assertEqual(
            print_positions,
            list(range(print_positions[0], print_positions[-1] + 1, 2)),
            "Section 10 print cells must form one block, alternating markdown heading / print cell",
        )
        self.assertIn("## 10. ", _cell_source(self.cells[print_positions[0] - 1]))
        for number, pos in enumerate(print_positions, start=1):
            self.assertRegex(
                _cell_source(self.cells[pos - 1]),
                rf"(?m)^### 10\.{number} Section [\d.]+ Data\b",
                "Section 10 headings must be numbered 10.1, 10.2, ... in order",
            )
            print_body = ast.parse(_cell_source(self.cells[pos])).body
            self.assertEqual(len(print_body), 1, "A Section 10 cell must be one print_section_data(...) call")
            self.assertIsInstance(print_body[0], ast.Expr)
            self.assertIsInstance(print_body[0].value, ast.Call)
            self.assertEqual(print_body[0].value.func.id, "print_section_data")

        # The search for the best empire configuration has its own notebook, so no cell here sets its meta
        # parameters.
        for cell in self.code_cells[1:]:
            self.assertNotRegex(_cell_source(cell), r"(?m)^(TARGET_SHIP_KEY|GA_RANDOM_SEED)\b")

        # Section 11 is the last section: the path to the saved configuration, one described cell per step.
        self.assertEqual(
            [_last_heading_number(_cell_source(self.cells[self.code_positions[idx] - 1])) for idx in self.path_idxs],
            FULL_PLANNER_PATH_SECTIONS,
        )
        self.assertEqual(self.code_positions[self.path_idxs[-1]], len(self.cells) - 1)
        self.assertRegex(
            _cell_source(self.cells[self.code_positions[self.path_idxs[0]] - 1]),
            r"(?m)^## 11\. Best Discounted Weekly Path Toward the Saved Configuration$",
        )
        # Its first cell only sets the meta parameters: plain values, one per statement, and nothing else.
        parameters = ast.parse(_cell_source(self.code_cells[self.path_idxs[0]])).body
        self.assertGreater(len(parameters), 15)
        for statement in parameters:
            self.assertIsInstance(statement, ast.AnnAssign)
            self.assertTrue(statement.target.id.startswith("PATH_"))
            ast.literal_eval(statement.value)
        # Every other path cell ends with its printout, titled with its own section.
        for idx, section in zip(self.path_idxs[1:], FULL_PLANNER_PATH_SECTIONS[1:]):
            last = ast.parse(_cell_source(self.code_cells[idx])).body[-1]
            self.assertIsInstance(last, ast.Expr)
            self.assertEqual(last.value.func.id, "print_section_data")
            self.assertIn(f"Section {section} data: ", ast.unparse(last.value.args[0]))
        # Section 11.8, the last cell, is the only one that writes: one write_text, into the notebook's METADATA_DIR.
        save_tree = ast.parse(_cell_source(self.code_cells[-1]))
        calls = [node for node in ast.walk(save_tree) if isinstance(node, ast.Call)]
        self.assertNotIn("open", [node.func.id for node in calls if isinstance(node.func, ast.Name)])
        writes = [
            ast.unparse(node.func)
            for node in calls
            if isinstance(node.func, ast.Attribute) and node.func.attr in FORBIDDEN_WRITE_ATTRS | {"open"}
        ]
        self.assertEqual(writes, ["path.write_text"])
        saved_to = next(
            node for node in save_tree.body if isinstance(node, ast.AnnAssign) and node.target.id == "PATH_ACTIONS_PATH"
        )
        self.assertEqual(ast.unparse(saved_to.value), "METADATA_DIR / PATH_ACTIONS_FILE_NAME")
        self.assertRegex(
            _cell_source(self.code_cells[self.path_idxs[0]]),
            rf'(?m)^PATH_ACTIONS_FILE_NAME: str = "{re.escape(FULL_PLANNER_PATH_FILE)}"$',
        )

    def test_pip_cell_installs_exactly_what_the_notebook_imports(self) -> None:
        deps = tomllib.loads((REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]["dependencies"]
        requirements = {_requirement_name(line): line.strip() for line in deps}
        installed = {
            _requirement_name(spec): spec for spec in re.findall(r'"([^"]+)"', _cell_source(self.code_cells[0]))
        }
        self.assertGreater(len(installed), 0)
        for name, spec in installed.items():
            self.assertEqual(requirements.get(name), spec, f"The pip cell must use the pyproject.toml range of {name}")

        # A fresh interpreter in which every requirement the pip cell leaves out cannot be imported.
        left_out = sorted(REQUIREMENT_IMPORT_NAMES.get(name, name) for name in set(requirements) - set(installed))
        result = subprocess.run(
            [
                sys.executable,
                "-c",
                FULL_PLANNER_SUBPROCESS_DRIVER,
                str(FULL_PLANNER_PATH),
                str(self.cold_workspace),
                json.dumps(left_out),
                str(self._metadata_for(self.cold_workspace)),
            ],
            capture_output=True,
            text=True,
            timeout=300,
            env={**os.environ, "PYTHONPATH": str(REPO_ROOT), "PYTHONDONTWRITEBYTECODE": "1"},
        )
        self.assertEqual(
            result.returncode,
            0,
            f"The notebook needs a package that its pip cell does not install ({left_out} were blocked):\n"
            f"{result.stderr[-2000:]}",
        )
        loaded = set(json.loads(result.stdout.strip().splitlines()[-1]))
        for name in installed:
            self.assertIn(
                REQUIREMENT_IMPORT_NAMES.get(name, name),
                loaded,
                f"The pip cell installs {name}, which the notebook never imports",
            )

    def test_run_is_read_only_and_leaves_no_scratch_globals(self) -> None:
        ns, _, _ = self._attack_run()
        before, after = self.attack_workspace_before_after
        self.assertEqual(after, before, "Running the notebook must not create or change a file in its workspace")
        self.assertFalse(LEGACY_ATTACK_JSON_PATH.exists())
        self.assertEqual(ns["LIVE_DB_PATH"], self.attack_workspace.resolve() / "state" / "spaceinvasion.db")
        # The run read its static metadata and the saved configuration from a copy. It wrote one file there,
        # the path as text, and changed nothing else.
        self.assertEqual(ns["METADATA_DIR"], self._metadata_for(self.attack_workspace))
        meta_before, meta_after = self.attack_metadata_before_after
        self.assertEqual(sorted(set(meta_after) - set(meta_before)), [FULL_PLANNER_PATH_FILE])
        self.assertEqual({name: meta_after[name] for name in meta_before}, meta_before)
        self.assertEqual(self._workspace_fingerprint(FULL_PLANNER_METADATA_DIR), self.repo_metadata_before)

        scratch_globals = sorted(
            name
            for name, value in ns.items()
            if name.startswith("_")
            and not name.startswith("__")
            and not callable(value)
            and not isinstance(value, types.ModuleType)
        )
        self.assertEqual(
            [name for name in scratch_globals if name not in ("_", "_cand")],
            [],
            "Top-level temporaries belong inside functions, not in the notebook namespace",
        )
        self.assertNotIn("statistics", ns)

        # The only database access is a query-only connection to a temporary copy.
        with ns["open_readonly_db_copy"](ns["LIVE_DB_PATH"]) as conn:
            self.assertNotEqual(
                Path(conn.execute("PRAGMA database_list").fetchone()[2]).resolve(),
                ns["LIVE_DB_PATH"].resolve(),
            )
            with self.assertRaises(sqlite3.OperationalError):
                conn.execute("CREATE TABLE full_planner_probe (x)")
            plan = " ".join(
                str(row[-1])
                for row in conn.execute("EXPLAIN QUERY PLAN " + ns["SQL_RAID_DISPATCH_ORIGIN_JOIN"], ("x",))
            )
        self.assertIn("idx_action_history_ts", plan, "The raid/action join must use the timestamp index")
        # No loader opens the live file: every connection goes to a temporary copy.
        opened = _databases_opened_by(
            lambda: [
                ns[loader](ns["LIVE_DB_PATH"])
                for loader in ("load_attack_telemetry", "load_live_planet_states", "load_universe_research_sources")
            ]
        )
        self.assertGreaterEqual(len(opened), 3)
        self.assertNotIn(ns["LIVE_DB_PATH"].resolve(), opened, "The live database must only be copied, never opened")
        self.assertEqual({path.name for path in opened}, {"spaceinvasion.db"})
        self.assertEqual(self._workspace_fingerprint(self.attack_workspace), before)

        # resolve_repo_root's last resort: package importable, repository neither in cwd nor on sys.path.
        saved_sys_path = list(sys.path)
        orig_cwd = Path.cwd()
        os.chdir(self.attack_workspace)
        try:
            sys.path[:] = [
                entry
                for entry in sys.path
                if entry and not (Path(entry).resolve() / "backend" / "__init__.py").is_file()
            ]
            self.assertEqual(ns["resolve_repo_root"](), REPO_ROOT.resolve())
        finally:
            os.chdir(orig_cwd)
            sys.path[:] = saved_sys_path

    # ------------------------------------------------------------------ static metadata

    def test_metadata_files_agree_with_the_game_catalog(self) -> None:
        from backend.core.catalog import (
            DEFENSE_SLUG_BY_KEY,
            SHIP_SLUG_BY_KEY,
        )
        from backend.core.config import StrategyConfig

        docs = {
            name: json.loads((FULL_PLANNER_METADATA_DIR / name).read_text(encoding="utf-8"))
            for name in FULL_PLANNER_METADATA_FILES
        }
        for name, doc in docs.items():
            self.assertIsInstance(doc.get("description"), str, f"{name} needs a top-level description")
            self.assertTrue(doc["description"].strip())

        def measured_cost_scale(levels: dict) -> float:
            """Geometric mean of the Spice-equivalent cost ratio between consecutive levels (first 20 levels)."""
            last = min(20, len(levels))
            first_cost = game_data.spice_equivalent(levels[1]["cost"])
            last_cost = game_data.spice_equivalent(levels[last]["cost"])
            return (last_cost / first_cost) ** (1.0 / (last - 1))

        buildings = docs["buildings.json"]["buildings"]
        self.assertEqual(
            {key: entry["name"] for key, entry in buildings.items()},
            {f"b{building_id}": info["name"] for building_id, info in game_data.BUILDING_BY_ID.items()},
        )
        for key, entry in buildings.items():
            measured = measured_cost_scale(game_data.BUILDING_BY_ID[int(key[1:])]["levels"])
            self.assertAlmostEqual(
                entry["cost_scale_factor"] / measured,
                1.0,
                delta=0.005,
                msg=f"{key}: cost_scale_factor {entry['cost_scale_factor']} vs catalog {measured:.4f}",
            )

        technologies = docs["technologies.json"]["technologies"]
        self.assertEqual(
            {key: entry["name"] for key, entry in technologies.items()},
            {key: info["name"] for key, info in game_data.RESEARCH_BY_ID.items()},
        )
        for key, info in game_data.RESEARCH_BY_ID.items():
            self.assertAlmostEqual(
                docs["technologies.json"]["cost_scale_factor"] / measured_cost_scale(info["levels"]),
                1.0,
                delta=0.005,
                msg=f"{key}: research costs do not scale by the documented factor",
            )
        self.assertEqual(
            technologies["r1"]["per_level_bonus_pct"],
            game_data.MINING_TECH_BONUS_PER_LEVEL * 100.0,
        )
        for key in ("r4", "r5", "r6"):  # each engine research lists exactly the ships that fly with that engine
            ships_with_engine = {
                ship_key
                for ship_key, slug in SHIP_SLUG_BY_KEY.items()
                if (game_data.SHIPS[slug].get("stats") or {}).get("engine") == technologies[key]["name"]
            }
            self.assertEqual(set(re.findall(r"\bs\d+\b", technologies[key]["applies_to"])), ships_with_engine)

        self.assertEqual(
            {key: entry["name"] for key, entry in docs["ships.json"]["ships"].items()},
            {key: game_data.SHIPS[slug]["name"] for key, slug in SHIP_SLUG_BY_KEY.items()},
        )
        defenses = docs["defenses.json"]["defenses"]
        self.assertEqual(
            {key: entry["name"] for key, entry in defenses.items()},
            {key: game_data.DEFENSES[slug]["name"] for key, slug in DEFENSE_SLUG_BY_KEY.items()},
        )
        for key, entry in defenses.items():  # REQ-CMB-004: every defense except the shield domes can dig in
            self.assertEqual(entry["can_dig_in"], "dome" not in DEFENSE_SLUG_BY_KEY[key], key)

        resources = docs["resources.json"]["resources"]
        self.assertEqual(list(resources), ["pig_iron", "metal", "kryptonite", "spice"])
        for resource, entry in resources.items():
            mine_id = int(entry["mine_building_key"][1:])
            base_output = game_data.mine_production_per_hour(mine_id, 5, mining_tech_level=0)
            boosted_output = game_data.mine_production_per_hour(mine_id, 5, mining_tech_level=10)
            self.assertGreater(base_output, 0.0, f"{entry['mine_building_key']} does not produce {resource}")
            self.assertEqual(entry["boosted_by_mining_tech"], boosted_output > base_output, resource)
            warehouse = game_data.BUILDING_BY_ID[int(entry["warehouse_building_key"][1:])]
            self.assertEqual(warehouse["name"], f"{resource.replace('_', ' ').capitalize()} warehouse")
            for level, row in warehouse["levels"].items():
                self.assertEqual(row["storage_capacity"], 100_000 * 2**level)

        settings = docs["planner_settings.json"]
        expected_settings = {
            "cold_start_colony": {"origin_coords": str, "speedup_factor": float},
            "table_level_caps": {"buildings": int, "researches": int},
            "empire": {
                "max_planets": int,
                "empty_colony_fields": int,
                "empty_colony_temperature_c": int,
                "fallback_planet_fields": int,
            },
            "flight_time_fallback": {"min_ship_speed": float, "fleet_speed_factor": float},
        }
        self.assertEqual(set(settings) - {"description"}, set(expected_settings))
        for group, fields in expected_settings.items():
            self.assertEqual(set(settings[group]) - {"description"}, set(fields), group)
            for field, kind in fields.items():
                self.assertIs(type(settings[group][field]), kind, f"{group}.{field}")
        self.assertRegex(settings["cold_start_colony"]["origin_coords"], r"^\d{2}:\d{3}:\d{2}$")
        for group, field in (
            ("cold_start_colony", "speedup_factor"),
            ("table_level_caps", "buildings"),
            ("table_level_caps", "researches"),
            ("empire", "max_planets"),
            ("empire", "empty_colony_fields"),
            ("empire", "fallback_planet_fields"),
            ("flight_time_fallback", "min_ship_speed"),
            ("flight_time_fallback", "fleet_speed_factor"),
        ):
            self.assertGreater(settings[group][field], 0, f"{group}.{field}")
        self.assertEqual(
            settings["empire"]["max_planets"],
            StrategyConfig().max_planets,
            "planner_settings.json documents max_planets as the default of the bot's colony cap "
            "(StrategyConfig.max_planets); update one of the two",
        )

    def test_metadata_values_reach_the_generated_tables(self) -> None:
        def edit_planner_settings(doc: dict) -> None:
            doc["table_level_caps"].update(buildings=12, researches=9)
            doc["empire"].update(
                max_planets=7, empty_colony_fields=250, empty_colony_temperature_c=-5, fallback_planet_fields=180
            )
            doc["cold_start_colony"].update(origin_coords="09:123:04", speedup_factor=2.0)
            doc["flight_time_fallback"].update(min_ship_speed=48000.0)

        def edit_technologies(doc: dict) -> None:
            doc["cost_scale_factor"] = 7.5
            doc["technologies"]["r2"].update(per_level_bonus_pct=3.0, domain="probe_domain")

        edits = {
            "buildings.json": lambda doc: doc["buildings"]["b1"].update(
                category="probe_category", cost_scale_factor=9.5, primary_effect="probe effect"
            ),
            "technologies.json": edit_technologies,
            "ships.json": lambda doc: doc["ships"]["s7"].update(role="probe_role"),
            "defenses.json": lambda doc: doc["defenses"]["d1"].update(role="probe_role", can_dig_in=False),
            "resources.json": lambda doc: doc["resources"]["spice"].update(notes="probe note"),
            "planner_settings.json": edit_planner_settings,
        }
        default_ns, _, _ = self._attack_run()
        with tempfile.TemporaryDirectory(prefix="si_fp_meta_") as tmp:
            meta_dir = self._edited_metadata_dir(Path(tmp), edits)
            # The edits change the roster, so the saved configuration no longer applies: stop before Section 11.
            last = self.path_idxs[0] - 1
            ns, _, _ = _run_full_planner(self.code_cells, self.attack_workspace, metadata_dir=meta_dir, last_cell=last)
            cold_ns, _, _ = _run_full_planner(
                self.code_cells, self.cold_workspace, metadata_dir=meta_dir, last_cell=last
            )

        buildings = ns["buildings_catalog_df"].set_index("key")
        self.assertEqual(buildings.loc["b1", "category"], "probe_category")
        self.assertEqual(buildings.loc["b1", "cost_scale_factor"], 9.5)
        self.assertEqual(buildings.loc["b1", "primary_effect"], "probe effect")
        levels = ns["buildings_levels_df"]
        self.assertEqual(set(levels.loc[levels["key"] == "b1", "category"]), {"probe_category"})
        self.assertEqual(int(levels["level"].max()), 12)
        self.assertEqual(int(ns["technologies_levels_df"]["level"].max()), 9)

        technologies = ns["technologies_catalog_df"].set_index("key")
        self.assertEqual(set(technologies["cost_scale_factor"]), {7.5})
        self.assertEqual(technologies.loc["r2", "per_level_bonus_pct"], 3.0)
        self.assertEqual(technologies.loc["r2", "domain"], "probe_domain")
        tech_levels = ns["technologies_levels_df"]
        r2_l4 = tech_levels[(tech_levels["key"] == "r2") & (tech_levels["level"] == 4)].iloc[0]
        self.assertEqual((r2_l4["bonus_pct_total"], r2_l4["multiplier_at_level"]), (12.0, 1.12))
        # The account has Energy technology 1: with +3 % per level, energy producers yield 1.03x their base.
        self.assertEqual(ns["GAME_PARAM_CONTEXT"]["r2_energy_tech"], 1)
        fusion = levels[levels["key"] == "b5"]
        for base, effective in zip(fusion["energy_at_level_base"], fusion["energy_at_level_effective"]):
            self.assertEqual(effective, round(base * 1.03, 2))
        ships = ns["ships_df"].set_index("key")
        self.assertEqual(ships.loc["s7", "role"], "probe_role")
        self.assertEqual(
            ships.loc["s9", "energy_output_effective"], round(ships.loc["s9", "energy_output_base"] * 1.03, 2)
        )
        defenses = ns["defenses_df"].set_index("key")
        self.assertEqual(defenses.loc["d1", "role"], "probe_role")
        self.assertFalse(defenses.loc["d1", "can_dig_in"])
        merchant = ns["merchant_rates_df"].set_index("resource")
        self.assertEqual(merchant.loc["spice", "notes"], "probe note")

        self.assertEqual(
            (ns["MAX_EMPIRE_PLANETS"], ns["EMPTY_COLONY_SIZE_FIELDS"], ns["EMPTY_COLONY_DEFAULT_TEMP_C"]),
            (7, 250, -5),
        )
        empire = ns["empire_planets_df"]
        self.assertEqual(len(empire), 7)
        empty_rows = empire[empire["is_empty_slot"]]
        self.assertEqual(len(empty_rows), 6)
        self.assertEqual(set(empty_rows["fields_max"]), {250})
        self.assertEqual(set(empty_rows["temperature_c"]), {-5})
        # Section 6 works on the same roster: one colony and six generic planets at the generic temperature.
        contexts = ns["planet_contexts_df"]
        self.assertEqual(list(contexts["planet_status"]), ["colonized"] + ["empty_colony_slot"] * 6)
        self.assertEqual(list(contexts["temperature_c"]), [31] + [-5] * 6)
        self.assertEqual(ns["GENERIC_PLANET_CONTEXT"]["planet_temperature_c"], -5)
        self.assertEqual(ns["planet_buildings_df"]["slot_index"].nunique(), 7)
        self.assertEqual(ships.loc["s9", "energy_output_base"], ns["solar_cell_energy_per_unit"](-5))
        self.assertEqual(ns["planet_state_from_fields"]({}).fields_max, 180)
        self.assertEqual(default_ns["planet_state_from_fields"]({}).fields_max, 230)

        # Without a planet snapshot the attack origin and the universe speed come from `cold_start_colony`,
        # and every planet of the roster is generic.
        self.assertEqual(cold_ns["origin_planet_coords"], "09:123:04")
        self.assertEqual(cold_ns["GAME_PARAM_CONTEXT"]["speedup_factor"], 2.0)
        self.assertEqual(list(cold_ns["planet_contexts_df"]["planet_status"]), ["empty_colony_slot"] * 7)
        self.assertEqual(set(cold_ns["planet_contexts_df"]["building_time_divisor"]), {2.0})
        # Four times the assumed ship speed halves the travel part of the fallback flight time.
        slow = default_ns["estimate_physics_one_way_seconds"]("02:380:12", "02:383:02")
        fast = ns["estimate_physics_one_way_seconds"]("02:380:12", "02:383:02")
        self.assertAlmostEqual((slow - 10.0) / (fast - 10.0), 2.0, delta=0.05)

    def test_metadata_loading_fails_fast_on_inconsistent_files(self) -> None:
        cases = [
            ("buildings.json", lambda doc: doc["buildings"].pop("b21"), r"buildings\.json: missing \['b21'\]"),
            (
                "buildings.json",
                lambda doc: doc["buildings"].update(b99=dict(doc["buildings"]["b1"])),
                r"unknown \['b99'\]",
            ),
            (
                "buildings.json",
                lambda doc: doc["buildings"]["b1"].update(name="Iron mine"),
                r"b1 is named 'Iron mine', but game_data calls it 'Pig iron mine'",
            ),
            (
                "technologies.json",
                lambda doc: doc["technologies"]["r3"].pop("applies_to"),
                r"r3 lacks \['applies_to'\]",
            ),
            ("ships.json", lambda doc: doc["ships"]["s1"].pop("role"), r"ships\.json: s1 lacks \['role'\]"),
            ("defenses.json", lambda doc: doc["defenses"].pop("d11"), r"defenses\.json: missing \['d11'\]"),
            ("resources.json", lambda doc: doc["resources"].pop("metal"), r"resources\.json: missing \['metal'\]"),
            (
                "resources.json",
                lambda doc: doc["resources"]["spice"].pop("warehouse_building_key"),
                r"spice lacks \['warehouse_building_key'\]",
            ),
        ]
        for file_name, change, message in cases:
            with self.subTest(file=file_name, error=message), tempfile.TemporaryDirectory(prefix="si_fp_meta_") as tmp:
                meta_dir = self._edited_metadata_dir(Path(tmp), {file_name: change})
                with self.assertRaisesRegex(ValueError, message):
                    _run_full_planner(
                        self.code_cells, self.cold_workspace, metadata_dir=meta_dir, last_cell=self.metadata_cell_idx
                    )

    # ------------------------------------------------------------------ generated data

    def test_attack_statistics_of_three_recorded_attacks(self) -> None:
        ns, _, _ = self._attack_run()
        self.assertAlmostEqual(ns["attack_resource_mean"]["spice_eq"], 2000.0, places=2)
        self.assertAlmostEqual(ns["attack_resource_std"]["spice_eq"], 2000.0, places=2)
        self.assertAlmostEqual(ns["winning_attack_resource_mean"]["spice_eq"], 3000.0, places=2)
        self.assertGreater(ns["attack_time_mean_sec"], 0.0)
        self.assertGreater(ns["attack_time_std_sec"], 0.0)
        self.assertAlmostEqual(ns["inter_attack_time_mean_sec"], 1500.0, places=2)
        self.assertAlmostEqual(ns["attack_success_rate"], 2.0 / 3.0, places=4)
        self.assertEqual(ns["ATTACK_SIM_PARAMS"]["attempts"], 3)

        # Every battle was paired with its dispatch: 300 s, 360 s and 200 s of flight, doubled for the round trip.
        attacks = ns["attacks_df"]
        self.assertEqual(list(attacks["time_source"]), ["observed_dispatch_pair"] * 3)
        self.assertEqual(list(attacks["one_way_sec"]), [300.0, 360.0, 200.0])
        self.assertEqual(list(attacks["round_trip_sec"]), [600.0, 720.0, 400.0])
        self.assertEqual(list(attacks["won"]), [True, True, False])
        self.assertEqual(ns["fleet_slot_capacity"], 1)
        self.assertEqual(ns["origin_planet_coords"], "02:380:12")

        for name in (
            "one_way_times_arr",
            "round_trip_times_arr",
            "inter_attack_intervals_arr",
            "attack_dispatch_ts_arr",
        ):
            self.assertIsInstance(ns[name], np.ndarray)
            self.assertEqual(ns[name].dtype, np.float64)
        attack_tables = ns["ATTACK_STATS_TABLES"]
        self.assertEqual(
            list(attack_tables),
            [
                "attacks",
                "recycles",
                "attack_resource_stats",
                "attack_time_stats",
                "attack_summary_stats",
                "attack_class_stats",
                "attack_target_stats",
            ],
        )
        for table_name, table in attack_tables.items():
            self.assertIs(table, ns[f"{table_name}_df"], f"{table_name} must be the {table_name}_df table itself")
            self.assertFalse(table.isna().any().any(), f"{table_name} must not contain NaNs")
        res_df = ns["attack_resource_stats_df"].set_index("metric")
        self.assertAlmostEqual(float(res_df.loc["spice_eq", "mean_all"]), 2000.0, places=2)
        self.assertAlmostEqual(float(res_df.loc["spice_eq", "sample_std_all"]), 2000.0, places=2)
        self.assertAlmostEqual(float(res_df.loc["spice_eq", "mean_wins"]), 3000.0, places=2)
        self.assertEqual(len(ns["attack_target_stats_df"]), 2)
        self.assertEqual(ns["per_target_success_rate"]["02:383:02"]["success_rate"], 1.0)
        self.assertEqual(ns["per_target_success_rate"]["02:378:15"]["losses"], 1)

        # The row lists kept in telemetry_bundle are the rows of the tables and the values of the array.
        bundle = ns["telemetry_bundle"]
        self.assertEqual([record["target_coords"] for record in bundle["records"]], list(attacks["target_coords"]))
        self.assertEqual(len(bundle["recycle_rows"]), len(ns["recycles_df"]))
        self.assertEqual(bundle["dispatch_timestamps"], ns["attack_dispatch_ts_arr"].tolist())

    def test_telemetry_record_builders(self) -> None:
        ns, _, _ = self._attack_run()
        dispatch_ts = 1_700_000_000.0  # 2023-11-14 22:13:20 UTC
        raids = [
            {
                "target_coords": "02:383:02",
                "attacked_ts": dispatch_ts,
                "origin_coords": "02:381:15",
                "is_exempt": 1,
                "outcome": "Dispatched attack",
                "loot_json": '{"metal": 500, "spice": 100}',
            },
            {
                "target_coords": "02:378:15",
                "attacked_ts": dispatch_ts + 50.0,
                "origin_coords": "",
                "is_exempt": 0,
                "outcome": "Defeat",
                "loot_json": "",
            },
        ]

        def battle(target: str, clock_text: str, outcome: str, **flags: object) -> dict:
            return {
                "target_coords": target,
                "msg_date_text": clock_text,
                "ts": 0.0,
                "outcome": outcome,
                "pig_iron": 600.0,
                "metal": 250.0,
                "kryptonite": 300.0,
                "spice": 100.0,
                "loot_spice_eq": 400.0,
                "attacker_lost_spice": 150.0,
                "debris_spice_eq": 20.0,
                "target_status_color": "unknown",
                "target_is_inactive": 0,
                "target_is_spacia": 0,
                **flags,
            }

        # The server clock is one hour ahead of UTC: its 23:18:20 is 22:18:20 UTC, 300 s after the dispatch.
        battles = [
            battle("02:383:02", "2023-11-14 23:18:20", "win", target_is_spacia=1),
            battle("02:390:07", "2023-11-14 23:30:00", "loss", target_status_color="yellow"),
            battle("02:391:07", "not a date", "victory"),
        ]
        clock = [ns["parse_server_clock"](row["msg_date_text"]) for row in battles]
        self.assertEqual(clock[0], dispatch_ts + 3600.0 + 300.0)
        self.assertIsNone(clock[2])
        offset = ns["infer_server_tz_offset_sec"](battles, clock, raids)
        self.assertEqual(offset, -3600.0)
        self.assertEqual(ns["infer_server_tz_offset_sec"](battles, clock, []), 0.0)

        records = ns["attack_records_from_battles"](battles, clock, raids, "02:380:12", offset)
        self.assertEqual(
            [record["time_source"] for record in records],
            ["observed_dispatch_pair", "calibrated_distance", "calibrated_distance"],
        )
        self.assertEqual([record["target_class"] for record in records], ["spacia", "yellow_inactive", "active_colony"])
        self.assertEqual([record["won"] for record in records], [True, False, True])
        self.assertEqual([record["origin_coords"] for record in records], ["02:381:15", "02:380:12", "02:380:12"])
        self.assertEqual(
            (records[0]["event_ts"], records[0]["one_way_sec"], records[0]["round_trip_sec"]),
            (dispatch_ts + 300.0, 300.0, 600.0),
        )
        self.assertEqual(records[0]["net_spice_eq"], 400.0 - 150.0)
        self.assertEqual(records[2]["event_ts"], 0.0, "An unreadable server clock falls back to the row's own ts")
        # A battle without a dispatch gets the observed seconds per sqrt(distance), applied to its own distance.
        distance = ns["_parse_coords_distance"]
        calibration = (300.0 - 10.0) / math.sqrt(distance("02:381:15", "02:383:02"))
        self.assertAlmostEqual(
            records[1]["one_way_sec"],
            10.0 + calibration * math.sqrt(distance("02:380:12", "02:390:07")),
            places=9,
        )
        # Without any observed pair, the rough estimate is the only source left.
        unpaired = ns["attack_records_from_battles"](battles[1:], clock[1:], [], "02:380:12", 0.0)
        self.assertEqual({record["time_source"] for record in unpaired}, {"physics_formula"})
        self.assertEqual(unpaired[0]["one_way_sec"], ns["estimate_physics_one_way_seconds"]("02:380:12", "02:390:07"))

        # Dispatches alone (no battle report yet): one record each, loot from loot_json.
        raid_records = ns["attack_records_from_raids"](raids, "02:380:12")
        self.assertEqual([record["target_class"] for record in raid_records], ["exempt_farm", "active_colony"])
        self.assertEqual([record["won"] for record in raid_records], [True, False])
        self.assertEqual([record["origin_coords"] for record in raid_records], ["02:381:15", "02:380:12"])
        self.assertEqual(raid_records[0]["spice_eq"], 500.0 / 2.5 + 100.0)
        self.assertEqual({record["time_source"] for record in raid_records}, {"physics_formula"})
        self.assertEqual(set(records[0]), set(ns["ATTACK_DF_COLUMNS"]))
        self.assertEqual(set(raid_records[0]), set(ns["ATTACK_DF_COLUMNS"]))

    def test_game_parameter_tables(self) -> None:
        ns, _, _ = self._attack_run()
        tables = ns["GAME_PARAMS_TABLES"]
        self.assertEqual(
            list(tables),
            [
                "merchant_rates",
                "planet_contexts",
                "buildings_catalog",
                "buildings_levels",
                "planet_buildings",
                "technologies_catalog",
                "technologies_levels",
                "planet_technologies",
                "ships",
                "planet_ships",
                "defenses",
                "planet_defenses",
            ],
        )
        for table_name, table in tables.items():
            self.assertIs(table, ns[f"{table_name}_df"], f"{table_name} must be the {table_name}_df table itself")
            self.assertGreater(len(table), 0, f"{table_name} must not be empty")
        self.assertEqual(list(ns["game_params_summary_df"]["table_name"]), list(tables))

        self.assertEqual(len(ns["merchant_rates_df"]), 4)
        self.assertEqual(len(ns["buildings_catalog_df"]), 21)
        self.assertEqual(len(ns["buildings_levels_df"]), 975)
        self.assertEqual(len(ns["technologies_catalog_df"]), 15)
        self.assertEqual(len(ns["technologies_levels_df"]), 445)
        self.assertEqual(len(ns["ships_df"]), 15)
        self.assertEqual(len(ns["defenses_df"]), 11)
        # One colony and ten generic planets: 11 planets times 21 buildings, 15 researches, 15 ships, 11 defenses.
        self.assertEqual(len(ns["planet_contexts_df"]), 11)
        self.assertEqual(len(ns["planet_buildings_df"]), 11 * 21)
        self.assertEqual(len(ns["planet_technologies_df"]), 11 * 15)
        self.assertEqual(len(ns["planet_ships_df"]), 11 * 15)
        self.assertEqual(len(ns["planet_defenses_df"]), 11 * 11)

        # The catalog and level tables are computed for a generic planet: universe speed 5, no accelerators.
        generic = ns["GENERIC_PLANET_CONTEXT"]
        self.assertEqual(
            [generic[key] for key in ("b7_dev_center", "b8_research_lab", "b9_weapons_factory", "b17_microsystem")],
            [0, 0, 0, 0],
        )
        self.assertEqual(generic["planet_temperature_c"], ns["EMPTY_COLONY_DEFAULT_TEMP_C"])
        self.assertNotIn("b7_dev_center", ns["GAME_PARAM_CONTEXT"], "The account-wide context has no planet values")
        generic_levels = ns["buildings_levels_df"]
        for base_time, effective_time in zip(generic_levels["base_time_s"], generic_levels["effective_build_time_s"]):
            self.assertTrue(
                math.isclose(effective_time, base_time / 5.0, rel_tol=1e-9, abs_tol=0.006),
                f"{effective_time} is not {base_time} / 5",
            )

        # Blast furnace (b2) 2:1 Pig iron feedstock invariant & monotonic build times
        bld_lvls = ns["buildings_levels_df"]
        b2_rows = bld_lvls[bld_lvls["key"] == "b2"]
        for consumed, produced in zip(b2_rows["pig_iron_consumed_hr_effective"], b2_rows["hourly_prod_effective"]):
            self.assertAlmostEqual(float(consumed), 2.0 * float(produced), places=2)
        for bkey, grp in bld_lvls.groupby("key"):
            times = list(grp.sort_values("level")["base_time_s"])
            self.assertEqual(times, sorted(times), f"Non-monotonic base_time_s in {bkey}")

        # Accelerator multipliers are stored exactly (0.5^L for b17 must never round down to 0)
        self.assertTrue((bld_lvls["time_reduction_multiplier"] > 0.0).all())
        b17_l30 = bld_lvls[(bld_lvls["key"] == "b17") & (bld_lvls["level"] == 30)]
        self.assertEqual(float(b17_l30["time_reduction_multiplier"].iloc[0]), 0.5**30)

        # Energy technology (level 1 in this colony, +1 % per level) boosts producers and leaves consumers alone.
        self.assertEqual(ns["GAME_PARAM_CONTEXT"]["r2_energy_tech"], 1)
        for key, factor in (("b5", 1.01), ("b1", 1.0)):
            rows = bld_lvls[bld_lvls["key"] == key]
            self.assertTrue(((rows["energy_at_level_base"] > 0) == (key == "b5")).all())
            for base, effective in zip(rows["energy_at_level_base"], rows["energy_at_level_effective"]):
                self.assertEqual(effective, round(base * factor, 2), key)

        # Warehouse capacities follow 100,000 * 2^level, the formula the planet loader falls back to.
        warehouses = bld_lvls[bld_lvls["key"].isin(["b11", "b12", "b13", "b14"])]
        for level, capacity in zip(warehouses["level"], warehouses["storage_capacity"]):
            self.assertEqual(int(capacity), ns["warehouse_capacity"](int(level)))
        self.assertEqual(ns["warehouse_capacity"](0), 100_000)

        # Technology quantitative effects checks (r1 +2%, r2 +1%, r4..r6 & r8..r10 +10%)
        tech_cat = ns["technologies_catalog_df"].set_index("key")
        self.assertEqual(float(tech_cat.loc["r1", "per_level_bonus_pct"]), 2.0)
        self.assertEqual(float(tech_cat.loc["r2", "per_level_bonus_pct"]), 1.0)
        for rk in ("r4", "r5", "r6", "r8", "r9", "r10"):
            self.assertEqual(float(tech_cat.loc[rk, "per_level_bonus_pct"]), 10.0)
        self.assertEqual(ns["technology_multiplier"]("r8", 5), 1.5)
        self.assertEqual(ns["technology_multiplier"]("none", 9), 1.0)

        # Spaceships & orbital solar cell (s9) checks
        ships_by_key = ns["ships_df"].set_index("key")
        self.assertGreater(float(ships_by_key.loc["s9", "energy_output_effective"]), 0.0)
        self.assertEqual(int(ships_by_key.loc["s9", "fields_used"]), 0)
        self.assertEqual(int(ships_by_key.loc["s3", "cargo"]), 250_000)
        self.assertEqual(ships_by_key.loc["s7", "engine_tech_key"], "r5")
        self.assertEqual(ships_by_key.loc["s9", "engine_tech_key"], "none")

        # A table builder takes the colony context as an argument, so it can be rebuilt for another state.
        boosted_ships = ns["build_ship_table"]({**ns["GENERIC_PLANET_CONTEXT"], "r8_weapons_tech": 5}).set_index("key")
        self.assertEqual(
            float(boosted_ships.loc["s7", "attack_effective"]),
            round(1.5 * float(ships_by_key.loc["s7", "attack_base"]), 1),
        )
        self.assertEqual(
            float(ships_by_key.loc["s7", "attack_effective"]), float(ships_by_key.loc["s7", "attack_base"])
        )

    def test_per_planet_tables_use_each_colony_and_generic_planets(self) -> None:
        ns, _, _ = self._cached_run(self.three_planet_workspace)
        single_ns, _, _ = self._attack_run()

        # Roster: the three owned planets, then generic planets until there are 11 normal planets.
        # The asteroid is listed but does not count, so nine generic planets join the two colonies.
        contexts = ns["planet_contexts_df"]
        self.assertEqual(list(contexts["slot_index"]), list(range(1, 13)))
        self.assertEqual(list(contexts["coords"][:4]), ["02:380:12", "02:381:15", "02:380:12A", "EMPTY:01"])
        self.assertEqual(list(contexts["planet_status"]), ["colonized"] * 3 + ["empty_colony_slot"] * 9)
        self.assertEqual(list(contexts["planet_type"][:3]), ["normal", "normal", "asteroid"])
        self.assertEqual(list(contexts["temperature_c"]), [31, -40, 12] + [0] * 9)
        # Time divisors at universe speed 5: (b7 + 1) * 2^b17 * 5, (b8 + 1) * 5 and max(1, b9) * 2^b17 * 5.
        self.assertEqual(
            contexts[["building_time_divisor", "research_time_divisor", "shipyard_time_divisor"]].values.tolist()[:4],
            [[10.0, 10.0, 10.0], [40.0, 15.0, 40.0], [15.0, 5.0, 10.0], [5.0, 5.0, 5.0]],
        )
        self.assertEqual([context["coords"] for context in ns["PLANET_PARAM_CONTEXTS"]], list(contexts["coords"]))
        self.assertEqual(ns["EMPIRE_PLANET_STATE_SUMMARY"]["colonized_planets"], 3)
        self.assertEqual(len(ns["empire_planets_df"]), 12)

        def spice_output(level: int, temperature: int) -> float:
            return 5.0 * game_data.mine_production_per_hour(4, level, temperature=temperature)

        # Buildings. The cold colony has Spice mine 5, so its row describes level 6 at -40 degrees C,
        # built with Development center 3 and Microsystem accelerator 1.
        planet_buildings = ns["planet_buildings_df"]
        buildings = planet_buildings.set_index(["coords", "key"])
        spice_mine_levels = game_data.BUILDING_BY_ID[4]["levels"]
        cold_mine = buildings.loc[("02:381:15", "b4")]
        self.assertEqual((cold_mine["current_level"], cold_mine["next_level"]), (5, 6))
        self.assertEqual(cold_mine["spice"], game_data.building_cost(4, 6)["spice"])
        self.assertEqual(cold_mine["effective_build_time_s"], round(spice_mine_levels[6]["base_time_s"] / 40.0, 2))
        self.assertEqual(cold_mine["hourly_prod_effective"], round(spice_output(6, -40), 2))
        self.assertEqual(cold_mine["delta_net_spice_eq_hr"], round(spice_output(6, -40) - spice_output(5, -40), 2))
        self.assertGreater(spice_output(6, -40), spice_output(6, 31), "A colder planet yields more Spice")
        home_mine = buildings.loc[("02:380:12", "b4")]
        self.assertEqual((home_mine["current_level"], home_mine["next_level"]), (2, 3))
        self.assertEqual(home_mine["effective_build_time_s"], round(spice_mine_levels[3]["base_time_s"] / 10.0, 2))
        self.assertEqual(home_mine["hourly_prod_effective"], round(spice_output(3, 31), 2))
        # The full level table of one planet, rebuilt on demand, holds the same row.
        _, cold_levels = ns["build_building_tables"](ns["PLANET_PARAM_CONTEXTS"][1], ns["MAX_BUILDING_TABLE_LEVEL"])
        cold_level_6 = cold_levels[(cold_levels["key"] == "b4") & (cold_levels["level"] == 6)].iloc[0]
        for column in cold_levels.columns:
            if column not in ("key", "level"):
                self.assertEqual(cold_mine[column], cold_level_6[column], column)

        # A building at the last level of the catalog has no next level, so it has no row.
        maxed_out = dataclasses.replace(
            ns["live_planet_states"][0], buildings={"b21": game_data.BUILDING_BY_ID[21]["max_table_level"]}
        )
        maxed_out_rows = ns["build_planet_building_table"]([maxed_out], ns["PLANET_PARAM_CONTEXTS"][:1])
        self.assertEqual(len(maxed_out_rows), 20)
        self.assertNotIn("b21", set(maxed_out_rows["key"]))

        # An asteroid can only hold the buildings the catalog allows there; every other planet takes them all.
        on_asteroid = planet_buildings[planet_buildings["coords"] == "02:380:12A"]
        self.assertEqual(
            list(on_asteroid.loc[on_asteroid["buildable_on_planet"], "key"]),
            [
                f"b{building_id}"
                for building_id, info in sorted(game_data.BUILDING_BY_ID.items())
                if info["asteroid_ok"]
            ],
        )
        self.assertTrue(planet_buildings.loc[planet_buildings["coords"] != "02:380:12A", "buildable_on_planet"].all())

        # Generic planets are identical: nothing built, and their next level is level 1 of the level table.
        generic_tables = [
            planet_buildings[planet_buildings["slot_index"] == slot]
            .drop(columns=["slot_index", "planet_id", "coords"])
            .reset_index(drop=True)
            for slot in range(4, 13)
        ]
        for other_generic in generic_tables[1:]:
            pd.testing.assert_frame_equal(other_generic, generic_tables[0])
        self.assertEqual((set(generic_tables[0]["current_level"]), set(generic_tables[0]["next_level"])), ({0}, {1}))
        levels = ns["buildings_levels_df"]
        level_1 = levels[levels["level"] == 1].reset_index(drop=True)
        shared_columns = [column for column in level_1.columns if column != "level"]
        pd.testing.assert_frame_equal(generic_tables[0][shared_columns], level_1[shared_columns], check_dtype=False)

        # Technologies: research levels belong to the account; only the time depends on the planet's Research lab.
        planet_technologies = ns["planet_technologies_df"]
        technologies = planet_technologies.set_index(["coords", "key"])
        energy_level_2_time = game_data.RESEARCH_BY_ID["r2"]["levels"][2]["base_time_s"]
        cold_energy = technologies.loc[("02:381:15", "r2")]
        self.assertEqual(
            (cold_energy["research_lab_level"], cold_energy["current_level"], cold_energy["next_level"]), (2, 1, 2)
        )
        self.assertEqual(cold_energy["effective_research_time_s"], round(energy_level_2_time / 15.0, 2))
        self.assertEqual(
            technologies.loc[("EMPTY:01", "r2"), "effective_research_time_s"], round(energy_level_2_time / 5.0, 2)
        )
        self.assertEqual(set(planet_technologies.groupby("key")["current_level"].nunique()), {1})
        self.assertEqual(len(planet_technologies), 12 * 15)

        # Ships: stationed units and build time per planet; Solar cells follow the planet's temperature.
        planet_ships = ns["planet_ships_df"]
        ships = planet_ships.set_index(["coords", "key"])
        fighter_time = game_data.SHIPS[ns["SHIP_SLUG_BY_KEY"]["s4"]]["base_build_time_s"]
        self.assertEqual(ships.loc[("02:381:15", "s4"), "stationed"], 12)
        self.assertEqual(int(planet_ships["stationed"].sum()), 15)
        for coords, divisor in (("02:380:12", 10.0), ("02:381:15", 40.0), ("02:380:12A", 10.0), ("EMPTY:01", 5.0)):
            self.assertEqual(ships.loc[(coords, "s4"), "effective_build_time_s"], round(fighter_time / divisor, 2))
        self.assertEqual(
            ships.loc[("02:381:15", "s9"), ["stationed", "energy_output_base", "energy_output_effective"]].tolist(),
            [3, 20.0, 20.2],  # 20 energy at -40 degrees C, +1 % for Energy technology 1
        )
        self.assertEqual(ships.loc[("EMPTY:01", "s9"), "energy_output_base"], 30.0)
        self.assertEqual(len(planet_ships), 12 * 15)

        # Defenses: stationed units and build time per planet.
        planet_defenses = ns["planet_defenses_df"]
        defenses = planet_defenses.set_index(["coords", "key"])
        mortar_time = game_data.DEFENSES[ns["DEFENSE_SLUG_BY_KEY"]["d1"]]["base_build_time_s"]
        self.assertEqual(defenses.loc[("02:381:15", "d1"), "stationed"], 7)
        self.assertEqual(int(planet_defenses["stationed"].sum()), 7)
        self.assertEqual(defenses.loc[("02:381:15", "d1"), "effective_build_time_s"], round(mortar_time / 40.0, 2))
        self.assertEqual(len(planet_defenses), 12 * 11)

        # The catalog and level tables describe a generic planet, so they do not change with the colonies.
        for name in (
            "buildings_catalog_df",
            "buildings_levels_df",
            "technologies_catalog_df",
            "technologies_levels_df",
            "ships_df",
            "defenses_df",
        ):
            pd.testing.assert_frame_equal(ns[name], single_ns[name])

    def test_technology_requirement_graph_and_unlock_plans(self) -> None:
        ns, _, _ = self._attack_run()
        tech_graph = ns["TECH_GRAPH"]
        self.assertEqual(len(tech_graph.nodes), 62)
        self.assertEqual(len(tech_graph.topological_order), 62)
        self.assertEqual(repr(tech_graph), "TechnologyRequirementGraph(nodes=62, edges=101)")
        self.assertEqual(len(ns["tech_graph_nodes_df"]), 62)
        self.assertEqual(len(ns["tech_graph_edges_df"]), 101)
        self.assertEqual(len(ns["unlock_targets_summary_df"]), 62)
        edges = ns["tech_graph_edges_df"]
        for src_k, tgt_k in zip(edges["source_key"], edges["target_key"]):
            self.assertLess(tech_graph.topological_layer[src_k], tech_graph.topological_layer[tgt_k])

        graph_tables = ns["TECH_GRAPH_TABLES"]
        self.assertEqual(
            list(graph_tables),
            [
                "tech_graph_nodes",
                "tech_graph_edges",
                "unlock_targets_summary",
                "phoenix_unlock_plan",
                "cold_phoenix_unlock_plan",
            ],
        )
        for table_name, table in graph_tables.items():
            self.assertIs(table, ns[f"{table_name}_df"], f"{table_name} must be the {table_name}_df table itself")

        # Direct requirements: the Weapons factory needs Energy technology 1 and Development center 3.
        self.assertEqual(
            [
                (m["key"], m["required_level"], m["current_level"], m["missing_levels"])
                for m in tech_graph.direct_missing("b9", {"b7": 2}, {})
            ],
            [("r2", 1, 0, 1), ("b7", 3, 2, 1)],
        )
        self.assertEqual(tech_graph.direct_missing("b9", {"b7": 3}, {"r2": 1}), [])
        self.assertTrue(tech_graph.is_unlocked("b9", {"b7": 3}, {"r2": 1}))
        self.assertFalse(tech_graph.is_unlocked("b9", {"b7": 2}, {"r2": 1}))

        # Diamond prerequisite resolution on s7 Phoenix
        s7_reqs = tech_graph.transitive_requirements("s7")
        self.assertEqual(
            s7_reqs,
            {"b7": 3, "b8": 5, "r2": 1, "r3": 6, "b9": 7, "r5": 8, "r10": 5},
        )
        b9_reqs = tech_graph.transitive_requirements("b9", include_target_level=7)
        self.assertEqual(b9_reqs, {"b7": 3, "b8": 1, "r2": 1, "b9": 7})
        self.assertEqual(list(b9_reqs)[-1], "b9", "Requirements are listed in an order that can be built")
        with self.assertRaises(KeyError):
            tech_graph.transitive_requirements("b99")

        # The universe speed has no default: a plan must say which speed its times are for.
        with self.assertRaises(TypeError):
            tech_graph.plan_unlock_sequence("s7", {}, {})
        # Times follow the simulated state: the Research lab is built with the Development center already at 2.
        b8_plan = tech_graph.plan_unlock_sequence("b8", {}, {}, speedup_factor=5.0, include_target_level=1)
        self.assertEqual(list(b8_plan["key"]), ["b7", "b8"])
        self.assertEqual(list(b8_plan["to_level"]), [2, 1])
        dev_center_times = game_data.BUILDING_BY_ID[7]["levels"]
        research_lab_times = game_data.BUILDING_BY_ID[8]["levels"]
        self.assertEqual(
            list(b8_plan["step_effective_time_s"]),
            [
                round(dev_center_times[1]["base_time_s"] / 5.0 + dev_center_times[2]["base_time_s"] / 10.0, 2),
                round(research_lab_times[1]["base_time_s"] / 15.0, 2),
            ],
        )

        r2_plan = tech_graph.plan_unlock_sequence("r2", {}, {}, speedup_factor=5.0, include_target_level=1)
        self.assertEqual(list(r2_plan["key"]), ["b7", "b8", "r2"])
        energy_technology_times = game_data.RESEARCH_BY_ID["r2"]["levels"]
        self.assertEqual(
            float(r2_plan["step_effective_time_s"].iloc[2]),
            round(energy_technology_times[1]["base_time_s"] / 10.0, 2),  # Research lab 1: (1 + 1) * speed 5
        )
        self.assertAlmostEqual(
            float(r2_plan["cum_effective_time_s"].iloc[2]), float(r2_plan["step_effective_time_s"].sum()), delta=0.02
        )

        # Applying plan_unlock_sequence from cold start unlocks every single entity legally
        for target_key, node_meta in tech_graph.nodes.items():
            plan_df = tech_graph.plan_unlock_sequence(target_key, {}, {}, speedup_factor=5.0)
            sim_b: dict[str, int] = {}
            sim_r: dict[str, int] = {}
            for sk, category, to_level in zip(plan_df["key"], plan_df["category"], plan_df["to_level"]):
                # Every prerequisite of sk must ALREADY be satisfied before sk upgrades!
                self.assertTrue(
                    tech_graph.is_unlocked(sk, sim_b, sim_r),
                    f"Step {sk} executed before its own prerequisites in {target_key} plan",
                )
                if category == "buildings":
                    sim_b[sk] = int(to_level)
                else:
                    sim_r[sk] = int(to_level)
            self.assertTrue(tech_graph.is_unlocked(target_key, sim_b, sim_r))
            unmet = game_data.missing_prerequisites(
                node_meta["category"],
                node_meta["slug"],
                buildings=sim_b,
                researches=sim_r,
            )
            self.assertEqual(unmet, [], f"Unmet prereqs after unlock plan for {target_key}: {unmet}")

    def test_planet_roster_pads_empty_colony_slots(self) -> None:
        ns, _, _ = self._attack_run()
        self.assertEqual(ns["MAX_EMPIRE_PLANETS"], 11)
        self.assertEqual(ns["EMPTY_COLONY_SIZE_FIELDS"], 300)
        self.assertEqual(len(ns["live_planet_states"]), 1)
        self.assertEqual(len(ns["empire_planet_states"]), 11)
        ep_df = ns["empire_planets_df"]
        eb_df = ns["empire_planet_buildings_df"]
        self.assertEqual(len(ep_df), 11)
        self.assertEqual(len(eb_df), 11)
        self.assertEqual(int((~ep_df["is_empty_slot"]).sum()), 1)
        self.assertEqual(int(ep_df["is_empty_slot"].sum()), 10)
        self.assertEqual(int(ep_df.loc[~ep_df["is_empty_slot"], "fields_max"].iloc[0]), 230)
        empty_rows = ep_df[ep_df["is_empty_slot"]]
        self.assertTrue((empty_rows["fields_max"] == 300).all())
        self.assertTrue((empty_rows["fields_used"] == 0).all())
        self.assertTrue((empty_rows["fields_remaining"] == 300).all())
        self.assertEqual(list(empty_rows["planet_id"]), list(range(-1, -11, -1)))
        self.assertEqual(list(empty_rows["coords"]), [f"EMPTY:{slot:02d}" for slot in range(1, 11)])
        building_columns = [f"b{i}" for i in range(1, 22)]
        self.assertTrue((eb_df.loc[eb_df["status"] == "empty_colony_slot", building_columns] == 0).all().all())
        self.assertEqual(ns["EMPIRE_PLANET_STATE_SUMMARY"]["total_fields_capacity"], 230 + 10 * 300)
        self.assertEqual(list(ns["planet_sizes_arr"]), [230] + [300] * 10)

        planet_tables = ns["EMPIRE_PLANET_TABLES"]
        self.assertEqual(list(planet_tables), ["live_planets", "empire_planets", "empire_planet_buildings"])
        for table_name, table in planet_tables.items():
            self.assertIs(table, ns[f"{table_name}_df"], f"{table_name} must be the {table_name}_df table itself")
        self.assertEqual(list(ns["live_planets_df"].columns), list(ep_df.columns))

        # When already at the planet limit, zero empty slots are added
        home_planet = ns["live_planet_states"][0]
        roster_settings = {"max_planets": 11, "empty_planet_size": 300, "empty_temp_c": 0}
        full_states = ns["build_padded_empire_planets"]([home_planet] * 11, **roster_settings)
        self.assertEqual([state.planet_id for state in full_states], [home_planet.planet_id] * 11)
        full_df, _ = ns["build_planet_tables"](full_states, 11)
        self.assertEqual(int(full_df["is_empty_slot"].sum()), 0)
        # The roster comes from the settings: none of the three values has a built-in default.
        for missing in roster_settings:
            with self.assertRaises(TypeError, msg=f"{missing} must be required"):
                ns["build_padded_empire_planets"](
                    [home_planet], **{name: value for name, value in roster_settings.items() if name != missing}
                )

        # Asteroids are listed but do not count toward the planet limit.
        asteroid = dataclasses.replace(home_planet, planet_id=99, coords="02:380:12A", planet_type="asteroid")
        mixed_states = ns["build_padded_empire_planets"](
            [home_planet, asteroid], max_planets=3, empty_planet_size=120, empty_temp_c=-7
        )
        self.assertEqual([state.planet_type for state in mixed_states], ["normal", "asteroid", "normal", "normal"])
        self.assertEqual([(state.fields_max, state.temperature) for state in mixed_states[2:]], [(120, -7)] * 2)
        mixed_df, mixed_buildings_df = ns["build_planet_tables"](mixed_states, 2)
        self.assertEqual(list(mixed_df["is_empty_slot"]), [False, False, True, True])
        self.assertEqual(list(mixed_buildings_df["status"]), ["colonized"] * 2 + ["empty_colony_slot"] * 2)

        # Without a saved game state, the same planet is rebuilt from its newest planet_snapshots row.
        with ns["open_readonly_db_copy"](ns["LIVE_DB_PATH"]) as conn:
            snapshot_rows = list(conn.execute(ns["SQL_LATEST_PLANETS_PER_ID"]))
        self.assertEqual(len(snapshot_rows), 1)
        from_snapshot = ns["planet_state_from_fields"](ns["snapshot_row_to_planet_fields"](snapshot_rows[0]))
        for field in (
            "planet_id",
            "name",
            "coords",
            "planet_type",
            "temperature",
            "fields_used",
            "fields_max",
            "resources",
            "storage",
            "energy",
            "buildings",
        ):
            self.assertEqual(getattr(from_snapshot, field), getattr(home_planet, field), field)
        # A warehouse level stands in for a capacity that was not recorded.
        upgraded = ns["planet_state_from_fields"]({"buildings": {"b12": 3}})
        self.assertEqual((upgraded.storage.metal, upgraded.storage.spice), (800_000, 100_000))

    def test_cold_start_on_an_empty_database(self) -> None:
        with warnings.catch_warnings():
            warnings.simplefilter("error", RuntimeWarning)
            cold_ns, cold_stdout, _ = _run_full_planner(
                self.code_cells,
                self.cold_workspace,
                metadata_dir=self._metadata_for(self.cold_workspace),
                overrides=_path_section_test_parameters(),
            )
        self.assertEqual(cold_ns["telemetry_source"], "cold_start")
        self.assertEqual(cold_ns["ATTACK_SIM_PARAMS"]["attempts"], 0)
        self.assertEqual(cold_ns["attack_success_rate"], 0.0)
        self.assertAlmostEqual(cold_ns["attack_success_rate_smoothed"], 0.5, places=4)
        self.assertEqual(len(cold_ns["live_planet_states"]), 0)
        self.assertEqual(len(cold_ns["empire_planet_states"]), 11)
        self.assertTrue((cold_ns["empire_planets_df"]["fields_max"] == 300).all())
        self.assertEqual(cold_ns["EMPIRE_PLANET_STATE_SUMMARY"]["total_fields_capacity"], 3300)
        for table_name, table in cold_ns["ATTACK_STATS_TABLES"].items():
            self.assertFalse(table.isna().any().any(), f"Cold-start {table_name} contained NaNs")

        # Every planet of the roster is generic: nothing built, so every next level is level 1.
        self.assertEqual(list(cold_ns["planet_contexts_df"]["planet_status"]), ["empty_colony_slot"] * 11)
        cold_buildings = cold_ns["planet_buildings_df"]
        self.assertEqual(len(cold_buildings), 11 * 21)
        self.assertEqual((set(cold_buildings["current_level"]), set(cold_buildings["next_level"])), ({0}, {1}))
        self.assertEqual(set(cold_ns["planet_ships_df"]["stationed"]), {0})

        # An empty table has the columns of a filled one, in the same order.
        ns, _, _ = self._attack_run()
        for table_name, table in cold_ns["ATTACK_STATS_TABLES"].items():
            self.assertEqual(
                list(table.columns),
                list(ns["ATTACK_STATS_TABLES"][table_name].columns),
                f"Cold-start {table_name} has different columns",
            )

        # Section 10 on an empty account: empty tables / arrays / lists print cleanly, slots print in full.
        cold_section9 = "".join(cold_stdout[i] for i in self.print_idxs)
        self.assertNotIn("...", cold_section9)
        self.assertIn(
            "[attacks_df] pandas.DataFrame, shape=(0, 17)\n(no rows) columns: ['event_ts', 'origin_coords', ",
            cold_section9,
        )
        self.assertIn(
            "[attack_dispatch_ts_arr] numpy.ndarray, shape=(0,), dtype=float64\n[]",
            cold_section9,
        )
        self.assertIn("[live_planet_states] list, len=0\n[]", cold_section9)
        self.assertIn("[per_target_success_rate] dict, len=0\n{}", cold_section9)
        self.assertEqual(cold_section9.count('"fields_max": 300'), 11)
        for print_idx in self.print_idxs:
            self.assertRegex(cold_stdout[print_idx], r"(?m)^Printed: \d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2} [+-]\d{4} \| ")

        # Nothing is known about anybody's research on an empty account.
        self.assertEqual(int(cold_ns["universe_research_df"]["universe_max_level"].sum()), 0)
        self.assertEqual(set(cold_ns["universe_research_df"]["max_source"]), {"none"})

        # Section 11 on an empty account: no planet, no stock, no loot. Nothing can be paid, and the section
        # says so instead of failing.
        self.assertEqual(cold_ns["PATH_TARGET_SUMMARY"]["planets_to_found"], 11)
        self.assertEqual(set(cold_ns["ATTACK_INCOME_SUMMARY"]["income_per_hour"].values()), {0.0})
        self.assertEqual(set(cold_ns["path_strategies_df"]["status"]), {"stuck"})
        self.assertEqual(cold_ns["PATH_SEARCH_SUMMARY"]["why"], "no strategy can start any step")
        self.assertFalse(cold_ns["PATH_SEARCH_SUMMARY"]["genetic_algorithm_ran"])
        cold_summary = cold_ns["BEST_PATH_SUMMARY"]
        self.assertEqual(
            (cold_summary["status"], cold_summary["time"], cold_summary["discounted_reward"]), ("stuck", "never", 0.0)
        )
        self.assertEqual((cold_summary["steps_started"], cold_summary["waiting_for"]), (0, "Colony ship 1"))
        self.assertEqual(cold_summary["colonies_founded"], 0)
        self.assertFalse(cold_summary["ends_with_the_output_of_the_configuration"])
        self.assertEqual(cold_ns["best_path_steps_df"].shape, (0, 17))
        self.assertEqual(list(cold_ns["best_path_steps_df"].columns), list(ns["best_path_steps_df"].columns))
        self.assertTrue((cold_ns["best_path_output_df"]["reached_after"] == "never").all())
        self.assertIn(
            f"the configuration is not reached, the path stops after 0 of {len(cold_ns['PATH_PROBLEM']['steps'])} steps",
            cold_stdout[self.path_idxs[-2]],
        )
        self.assertGreater(cold_ns["PATH_LOWER_BOUND_HOURS"], 0.0)
        cold_text = (self._metadata_for(self.cold_workspace) / FULL_PLANNER_PATH_FILE).read_text(encoding="utf-8")
        cold_lines = cold_text.split("\n")
        self.assertTrue(cold_lines[5].startswith("Attacks & spare: 0 -> 0 fleet slots"))
        self.assertEqual(
            cold_lines[7:9] + cold_lines[11:],
            [
                f"Result: NOT reached. The path stops after 0 of {len(cold_ns['PATH_PROBLEM']['steps'])} actions: "
                "the empire can never pay for Colony ship 1",
                "Colonies reached: 0/11 founded",
                f"Actions: 0 within {cold_ns['PATH_REWARD_WEEKS']} weeks, in the order in which they start. "
                "Times count from the moment this file was written.",
                "Planets are named by their coordinates. "
                'EMPTY:nn is an empty colony slot: "found the colony" comes first there.',
                "",
                "",
            ],
        )
        self.assertTrue(cold_lines[9].startswith("Max building levels reached: "))
        self.assertTrue(cold_lines[10].startswith("Research levels reached: "))
        self.assertEqual(cold_ns["PATH_ACTIONS_SUMMARY"]["first_action"], None)

        # A missing database file is a cold start too.
        missing_db = self.cold_workspace / "state" / "missing.db"
        self.assertFalse(cold_ns["has_database"](missing_db))
        self.assertEqual(cold_ns["load_live_planet_states"](missing_db), [])
        self.assertEqual(cold_ns["load_attack_telemetry"](missing_db)["source_label"], "cold_start")

    def test_utility_helpers(self) -> None:
        ns, _, _ = self._attack_run()
        for seconds, text in ((45, "45s"), (187, "3m 07s"), (20_100, "5h 35m"), (1_051_200, "12d 04h")):
            self.assertEqual(ns["fmt_duration"](seconds), text)
        for not_a_duration in (0.0, -3.0, float("nan"), float("inf")):
            self.assertEqual(ns["fmt_duration"](not_a_duration), "0s")

        mean, sample_std, pop_std = ns["compute_mean_and_std"]([1.0, 2.0, 3.0, float("nan")])
        self.assertEqual((mean, sample_std), (2.0, 1.0))
        self.assertAlmostEqual(pop_std, math.sqrt(2.0 / 3.0), places=12)
        self.assertEqual(ns["compute_mean_and_std"]([]), (0.0, 0.0, 0.0))
        self.assertEqual(ns["compute_mean_and_std"]([7.0]), (7.0, 0.0, 0.0))

        self.assertEqual(ns["parse_json_object"]('{"a": 1}'), {"a": 1})
        for not_an_object in ("not json", "[1, 2]", "", None, 7):
            self.assertEqual(ns["parse_json_object"](not_an_object), {})
        self.assertEqual(
            ns["parse_level_map"]('{"b1": 3, "b2": "x", "b3": -2, "b4": 2.9}'),
            {"b1": 3, "b3": 0, "b4": 2},
        )

        self.assertEqual(
            ns["format_prerequisites"](game_data.BUILDING_BY_ID[9]["requirements"]),
            "r2 L1 (Energy technology), b7 L3 (Development center)",
        )
        self.assertEqual(ns["format_prerequisites"]([]), "None")
        self.assertEqual(ns["full_cost"]({"metal": 5}), {"metal": 5, "pig_iron": 0, "kryptonite": 0, "spice": 0})
        self.assertEqual(ns["cost_points"]({"pig_iron": 600, "metal": 0, "kryptonite": 400, "spice": 0}), 1.0)
        # Time divisors of Section 1.3: (b7 + 1) * 2^b17 * speed, (b8 + 1) * speed, max(1, b9) * 2^b17 * speed.
        self.assertEqual(ns["building_time_divisor"](3, 2, 5.0), 80.0)
        self.assertEqual(ns["research_time_divisor"](3, 5.0), 20.0)
        self.assertEqual(ns["shipyard_time_divisor"](0, 2, 5.0), 20.0)
        self.assertEqual(ns["shipyard_time_divisor"](4, 0, 5.0), 20.0)
        # Solar cells: the catalog table gives 30 energy at 0 degrees C and 10 at -80 degrees C or colder.
        self.assertEqual(
            [ns["solar_cell_energy_per_unit"](temperature) for temperature in (-200, -80, 0, 31, 200, 400)],
            [10.0, 10.0, 30.0, 37.75, 80.0, 80.0],
        )

    # ------------------------------------------------------------------ Section 9: universe research

    def test_universe_research_maximum_levels(self) -> None:
        ns, stdout, _ = self._cached_run(self.universe_workspace)
        table = ns["universe_research_df"]
        rows = table.set_index("key")
        # The 15 researches of the catalog, then the one research that only the bot's ledger tracks.
        self.assertEqual(list(table["key"]), [f"r{number}" for number in range(1, 16)] + ["urplasma-extraction"])
        self.assertEqual(list(table["research_id"]), list(range(1, 17)))

        def levels(key: str) -> list:
            columns = ["own_level", "spied_max_level", "server_record_level", "universe_max_level", "max_source"]
            return rows.loc[key, columns].tolist()

        # Mining technology: the server record (27) beats the ledger's spied level (14) and a newer report (15).
        self.assertEqual(levels("r1"), [0, 15, 27, 27, "server_record"])
        self.assertEqual(rows.loc["r1", ["holder", "holder_coords"]].tolist(), ["TopMiner", "01:010:02"])
        self.assertEqual(
            rows.loc["r1", ["gap_to_max", "own_percent_of_max", "catalog_max_level"]].tolist(), [27, 0.0, 40]
        )
        self.assertEqual(rows.loc["r1", "ledger_updated_utc"], "2023-11-14 22:13:20")
        # Energy technology: the account has level 1. The server record (21) counts although the ledger's own
        # maximum still says 20, and the holder of that 20 is not the holder of the record.
        self.assertEqual(levels("r2"), [1, 12, 21, 21, "server_record"])
        self.assertEqual(rows.loc["r2", ["gap_to_max", "own_percent_of_max"]].tolist(), [20, 4.8])
        self.assertEqual(rows.loc["r2", ["holder", "holder_coords"]].tolist(), ["", ""])
        # Engine research: the ledger remembers 13, more than any source shows today.
        self.assertEqual(levels("r3"), [0, 9, 0, 13, "ledger"])
        self.assertEqual(rows.loc["r3", "holder"], "OldRecord")
        # Rocket engine: only a spy report, from a planet whose owner is unknown.
        self.assertEqual(levels("r4"), [0, 12, 0, 12, "spy_report"])
        self.assertEqual(rows.loc["r4", ["holder", "holder_coords"]].tolist(), ["", "02:394:01"])
        # Spy technology: nobody is known above the account's own level, which the ledger recorded as 4.
        self.assertEqual(levels("r7"), [4, 0, 0, 4, "own_level"])
        self.assertEqual(rows.loc["r7", ["holder", "gap_to_max", "own_percent_of_max"]].tolist(), ["", 0, 100.0])
        # Weapons technology: three reports (7, then 9 under its name, then 9 again); the newest 9 names the holder.
        self.assertEqual(levels("r8"), [0, 9, 0, 9, "spy_report"])
        self.assertEqual(rows.loc["r8", ["holder", "holder_coords"]].tolist(), ["Late", "02:391:07"])
        # Shield technology: 5 under its slug beats 3; the non-numeric level of the last report is ignored.
        self.assertEqual(levels("r9"), [0, 5, 0, 5, "spy_report"])
        self.assertEqual(rows.loc["r9", "holder"], "Miner")
        # A level of 0 in a report and an unknown key (`r99`) count for nothing.
        self.assertEqual(levels("r10"), [0, 0, 0, 0, "none"])
        self.assertEqual(rows.loc["r10", ["holder", "ledger_updated_utc"]].tolist(), ["", ""])
        # Graviton technology: the 11 of the oldest report stays, although the newest report shows only 4.
        self.assertEqual(levels("r13"), [0, 11, 0, 11, "spy_report"])
        self.assertEqual(rows.loc["r13", ["holder", "holder_coords"]].tolist(), ["Raider", "02:383:02"])
        # The ledger's own row: no level table in the catalog, holder from the ledger.
        self.assertEqual(levels("urplasma-extraction"), [0, 3, 0, 3, "spy_report"])
        self.assertEqual(
            rows.loc["urplasma-extraction", ["name", "holder", "catalog_max_level"]].tolist(),
            ["Urplasma-extraction", "Raider", 0],
        )

        self.assertEqual(ns["UNIVERSE_RESEARCH_MAX_LEVELS"]["r1"], 27)
        self.assertEqual(ns["UNIVERSE_RESEARCH_MAX_LEVELS"], dict(zip(table["key"], table["universe_max_level"])))
        self.assertEqual(
            ns["UNIVERSE_RESEARCH_SUMMARY"],
            {
                "researches": 16,
                "ledger_rows": 5,
                "spy_reports_with_research": 4,
                "researches_with_server_record": 2,
                "researches_seen_in_spy_reports": 8,
                "researches_above_own_level": 8,
                "highest_level": 27,
            },
        )
        sources = ns["universe_research_sources"]
        self.assertEqual([row["research_key"] for row in sources["ledger_rows"]][-1], "urplasma-extraction")
        self.assertEqual(sources["spied_levels"]["r8"], {"level": 9, "holder": "Late", "holder_coords": "02:391:07"})
        self.assertEqual(sorted(sources["spied_levels"]), ["r1", "r13", "r4", "r8", "r9"])

        # Ties: the server record names the source before a spy report does, a spy report before the own level.
        research_row = ns["universe_research_row"]
        catalog_entry = dict(ns["research_catalog"]())["r6"]
        ledger_tie = {"server_record_level": 9, "spied_max_level": 9, "universe_max_level": 9, "holder": "Both"}
        tie = research_row("r6", catalog_entry, ledger_tie, {}, 9)
        self.assertEqual((tie["max_source"], tie["holder"], tie["gap_to_max"]), ("server_record", "Both", 0))
        tie = research_row("r6", catalog_entry, {}, {"level": 9, "holder": "Seen", "holder_coords": "01:002:03"}, 9)
        self.assertEqual((tie["max_source"], tie["holder"], tie["holder_coords"]), ("spy_report", "Seen", "01:002:03"))
        # A holder is named for the maximum only: here the ledger's spied level beats the newest report.
        ledger_ahead = {"spied_max_level": 9, "universe_max_level": 9, "holder": "Ledger", "holder_coords": "01:001:01"}
        ahead = research_row(
            "r6", catalog_entry, ledger_ahead, {"level": 6, "holder": "Report", "holder_coords": "02:002:02"}, 0
        )
        self.assertEqual(
            (ahead["max_source"], ahead["spied_max_level"], ahead["holder"], ahead["holder_coords"]),
            ("spy_report", 9, "Ledger", "01:001:01"),
        )
        # The cell says so when nothing is known about other players, and only then.
        own_levels_note = "every universe maximum is your own level"
        self.assertNotIn(own_levels_note, stdout[self.universe_research_cell_idx])
        self.assertIn("5 ledger row(s), 4 spy report(s) with research levels", stdout[self.universe_research_cell_idx])

        # Without a ledger row or a spy report, every maximum is the account's own level.
        plain_ns, plain_stdout, _ = self._attack_run()
        self.assertIn(own_levels_note, plain_stdout[self.universe_research_cell_idx])
        self.assertEqual(
            plain_ns["universe_research_sources"],
            {"ledger_rows": [], "spied_levels": {}, "spy_reports_with_research": 0},
        )
        plain = plain_ns["universe_research_df"].set_index("key")
        self.assertEqual(len(plain), 15)
        self.assertEqual(
            plain.loc["r2", ["own_level", "universe_max_level", "max_source"]].tolist(), [1, 1, "own_level"]
        )
        self.assertEqual(set(plain.drop(index="r2")["max_source"]), {"none"})
        self.assertEqual(plain_ns["UNIVERSE_RESEARCH_SUMMARY"]["researches_above_own_level"], 0)

        # A source can be missing altogether: an own level beyond everything else on record wins.
        own_ahead = ns["build_universe_research_table"](
            {"ledger_rows": [], "spied_levels": {"r5": {"level": 3, "holder": "X", "holder_coords": "01:001:01"}}},
            {"r5": 7},
        ).set_index("key")
        self.assertEqual(
            own_ahead.loc[
                "r5", ["own_level", "spied_max_level", "universe_max_level", "max_source", "holder"]
            ].tolist(),
            [7, 3, 7, "own_level", ""],
        )
        self.assertEqual(
            ns["load_universe_research_sources"](self.cold_workspace / "state" / "missing.db"),
            {"ledger_rows": [], "spied_levels": {}, "spy_reports_with_research": 0},
        )
        self.assertEqual(ns["research_key_aliases"]()["weapons technology"], "r8")

    def _parse_printout(self, out: str) -> tuple[str, dict[str, list[str]]]:
        """Check the frame of one `print_section_data` printout; return its section number and its item blocks."""
        rule_eq = "=" * 100
        item_rule = "\n" + "-" * 100 + "\n"
        out_lines = out.rstrip("\n").split("\n")
        self.assertEqual(out_lines[0], rule_eq)
        self.assertEqual(out_lines[3], rule_eq)
        title = re.fullmatch(r"Section ([\d.]+) data: \S.*", out_lines[1])
        self.assertIsNotNone(title, f"Malformed printout title: {out_lines[1]!r}")
        stamp = re.fullmatch(
            r"Printed: (\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2} [+-]\d{4}) \| (\d+) item\(s\)",
            out_lines[2],
        )
        self.assertIsNotNone(stamp, f"Missing print date/time header: {out_lines[2]!r}")
        printed_at = datetime.datetime.strptime(stamp.group(1), "%Y-%m-%d %H:%M:%S %z")
        now_utc = datetime.datetime.now(datetime.timezone.utc)
        self.assertLess(abs((now_utc - printed_at).total_seconds()), 600.0)
        blocks = "\n".join(out_lines[4:]).split(item_rule)
        self.assertEqual(len(blocks), int(stamp.group(2)))
        named: dict[str, list[str]] = {}
        for block in blocks:
            block_lines = block.split("\n")
            head = re.fullmatch(r"\[(?P<name>[^\]]+)\] (?P<desc>.+)", block_lines[0])
            self.assertIsNotNone(head, f"Malformed item header: {block_lines[0]!r}")
            self.assertNotIn(head.group("name"), named, "Each object is printed exactly once")
            named[head.group("name")] = block_lines
        return title.group(1), named

    def test_every_generated_object_is_printed_in_full(self) -> None:
        ns, stdout, new_names = self._attack_run()
        sections = self._section_cells()
        self.assertEqual([section for section, _, _ in sections], FULL_PLANNER_DATA_SECTIONS)
        # Section 11 prints its own data: every cell after its meta parameters, which the next cell prints.
        path_cells = dict(zip(FULL_PLANNER_PATH_SECTIONS, self.path_idxs))
        self.assertEqual(stdout[path_cells["11.1"]], "", "The meta parameter cell must print nothing")
        all_sections = sections + [
            (section, path_cells["11.1"] if section == "11.2" else idx, idx)
            for section, idx in path_cells.items()
            if section != "11.1"
        ]

        printed_text = "".join(stdout[print_idx] for _, _, print_idx in all_sections)
        self.assertNotIn("...", printed_text, "Printouts must never be truncated")
        self.assertNotRegex(printed_text, r"\de\+\d", "Large numbers must not print in scientific notation")
        self.assertEqual(re.findall(r"sid=(?!REDACTED)", printed_text, flags=re.IGNORECASE), [])

        printed_blocks: dict[str, list[str]] = {}
        section_out: dict[str, str] = {}
        for section, data_idx, print_idx in all_sections:
            out = stdout[print_idx]
            title_section, blocks = self._parse_printout(out)
            self.assertEqual(
                title_section,
                section,
                f"Print cell {print_idx} is titled Section {title_section} but prints the data of Section {section}",
            )
            if print_idx not in self.path_idxs:
                self.assertRegex(
                    _cell_source(self.cells[self.code_positions[print_idx] - 1]),
                    rf"(?m)^### 10\.\d+ Section {re.escape(section)} Data\b",
                    f"The heading above print cell {print_idx} must name Section {section}",
                )
            section_out[section] = out
            for name, block_lines in blocks.items():
                self.assertNotIn(name, printed_blocks, "Each object is printed exactly once")
                printed_blocks[name] = block_lines

            # Every data name the matching cell defined must appear in its printout. A path cell prints what
            # it generated itself, and Section 11.2 also the meta parameters of Section 11.1.
            defining = {data_idx, print_idx} if print_idx in self.path_idxs else {data_idx}
            section_names = sorted(
                name for idx in defining for name in new_names[idx] if _is_generated_data(name, ns[name])
            )
            self.assertGreater(len(section_names), 0)
            for name in section_names:
                self.assertRegex(
                    out,
                    rf"(?<![A-Za-z0-9_]){re.escape(name)}(?![A-Za-z0-9_])",
                    f"{name!r} (generated by code cell {data_idx}) is missing from the printout of cell {print_idx}",
                )

        # Every DataFrame / ndarray the notebook generated is printed row for row / element for element.
        for name, value in sorted(ns.items()):
            if name.startswith("_") or not isinstance(value, (pd.DataFrame, np.ndarray)):
                continue
            self.assertIn(name, printed_blocks, f"{name} is generated but never printed")
            block_lines = printed_blocks[name]
            if isinstance(value, pd.DataFrame):
                self.assertEqual(
                    block_lines[0],
                    f"[{name}] pandas.DataFrame, shape=({value.shape[0]}, {value.shape[1]})",
                )
                if value.empty:
                    self.assertEqual(block_lines[1], f"(no rows) columns: {list(value.columns)}")
                else:
                    self.assertEqual(len(block_lines), 2 + len(value), f"{name} was not printed row for row")
                    self.assertEqual(block_lines[1].split(), [str(col) for col in value.columns])
            else:
                self.assertEqual(
                    block_lines[0],
                    f"[{name}] numpy.ndarray, shape={value.shape}, dtype={value.dtype}",
                )
                tokens = " ".join(block_lines[1:]).replace("[", " ").replace("]", " ").split()
                printed_vals = [float(tok) for tok in tokens]
                self.assertEqual(len(printed_vals), value.size)
                np.testing.assert_allclose(printed_vals, value.astype(np.float64).ravel(), rtol=0.0, atol=1e-6)

        # Spot checks: paths, metadata, fixed-point timestamps, full-digit big integers, graph closure, planets.
        self.assertIn(str(self.attack_workspace.resolve() / "state" / "spaceinvasion.db"), section_out["1.1"])
        self.assertIn('  "b1": {"name": "Pig iron mine", ', section_out["1.6"])
        self.assertIn('"max_planets": 11', section_out["1.6"])
        self.assertIn(
            "[attack_dispatch_ts_arr] numpy.ndarray, shape=(3,), dtype=float64\n[1700000000 1700001000 1700003000]",
            section_out["2.3"],
        )
        top_b17_metal = game_data.building_cost(17, 50)["metal"]
        self.assertGreater(top_b17_metal, 10**20)
        self.assertIn(f" {top_b17_metal} ", section_out["6.1"])
        s7_closure_line = next(ln for ln in section_out["7.2"].split("\n") if ln.startswith('  "s7": '))
        s7_closure = json.loads(s7_closure_line.split(": ", 1)[1].rstrip(","))
        self.assertEqual(set(s7_closure["all_prerequisites"]), set(ns["TECH_GRAPH"].transitive_requirements("s7")))
        self.assertEqual(s7_closure["all_unlocks"], [])
        self.assertIn('"b5": 7', section_out["6"])
        self.assertEqual(section_out["6"].count('"fields_max": 300'), 10)
        self.assertIn('"coords": "EMPTY:10"', section_out["6"])
        # Section 9: the research table.
        self.assertIn("[universe_research_df] pandas.DataFrame, shape=(15, 15)", section_out["9"])

    def test_print_helpers_show_full_data_and_redact_session_tokens(self) -> None:
        ns, _, _ = self._attack_run()
        # Float formatting used by the printouts: no scientific notation for large values, no float noise.
        for raw_val, expected_text in (
            (1794880358.409, "1794880358.409"),
            (123456789012.34, "123456789012.34"),
            (8892.061995397919, "8892.061995"),
            (8.600298e20, "860029799999999967232"),
            (100.0, "100"),
            (1.0 / 3.0, "0.333333"),
            (0.5**21, "4.76837e-07"),
            (-0.0, "0"),
            (float("nan"), "nan"),
        ):
            self.assertEqual(ns["format_full_float"](raw_val), expected_text)

        # print_section_data on data far beyond the pandas / numpy default display limits:
        # nothing is elided, a non-default index is kept, and session tokens are redacted.
        probe_df = pd.DataFrame(
            {
                "n": np.arange(2500),
                "x": np.arange(2500) * 1e9 + 0.25,
                "txt": ["z" * 150] * 2500,
            }
        )
        probe_df.loc[7, "txt"] = "indexInternal.es?action=internalHome&sid=FAKE0123456789abcdef&x=1"
        probe_arr = np.arange(3000, dtype=np.float64) + 1_794_880_000.25
        probe_buf = io.StringIO()
        with contextlib.redirect_stdout(probe_buf):
            ns["print_section_data"](
                "Probe",
                [
                    ("probe_df", probe_df),
                    ("probe_arr", probe_arr),
                    ("probe_dict", {"href": "indexGame.es?sid=FAKEfedcba9876543210", 7: (1, 2)}),
                    ("probe_indexed", probe_df.tail(2).set_index("x")[["n"]]),
                    ("probe_registry", {"probe": probe_df}),
                ],
            )
        probe_out = probe_buf.getvalue()
        self.assertNotIn("...", probe_out)
        self.assertNotRegex(probe_out, r"\de\+\d")
        item_rule = "\n" + "-" * 100 + "\n"
        probe_blocks = probe_out.rstrip("\n").split("\n", 4)[4].split(item_rule)
        self.assertEqual(len(probe_blocks), 5)
        self.assertEqual(len(probe_blocks[0].split("\n")), 2 + 2500)
        self.assertRegex(probe_blocks[0], r"(?m)^\s*2499\s+2499000000000\.25\s+z{150}$")
        self.assertEqual(len(probe_blocks[1].replace("[", " ").replace("]", " ").split("\n", 1)[1].split()), 3000)
        self.assertIn("1794882999.25]", probe_blocks[1])
        self.assertNotIn("FAKE0123456789abcdef", probe_out)
        self.assertNotIn("FAKEfedcba9876543210", probe_out)
        self.assertEqual(probe_out.count("sid=REDACTED"), 2)
        self.assertIn('  "7": [1, 2]', probe_blocks[2])
        self.assertRegex(probe_blocks[3], r"(?m)^2499000000000\.25\s+2499$")
        self.assertEqual(
            probe_blocks[4].split("\n"),
            [
                "[probe_registry] dict, len=1 (registry of DataFrames; each table is printed in full by its own section)",
                "  probe: shape=(2500, 3)",
            ],
        )

    # ---------------------------------------------------------------------------
    # notebooks/config_optimizer.ipynb
    # ---------------------------------------------------------------------------
    # ------------------------------------------------------------------ Section 11: the W-week discounted path

    def _tiny_path(
        self,
        ns: dict,
        levels: dict[str, int],
        target_levels: dict[str, int],
        *,
        stock: tuple[float, ...] = (0.0, 0.0, 0.0, 0.0),
        fields_max: int = 300,
        strategy: object | None = None,
        demolition_time_factor: float = 1.0,
        slot_attack_income: dict[str, float] | None = None,
        fleet_command_center_level: int | None = None,
        spare_percent: dict[str, float] | None = None,
        reward_weeks: int = 26,
        weekly_discount: float = 0.90,
        horizon_hours: float | None = None,
    ) -> tuple[dict, dict, object]:
        """Simulate a path problem of one planet, built by hand: `(problem, run, simulator)`."""
        planet = dataclasses.replace(
            ns["empty_colony_slot"](1, fields_max, 0),
            planet_id=4242,
            coords="09:099:09",
            name="Tiny",
            buildings=dict(levels),
            resources=Resources(*(int(amount) for amount in stock)),
        )
        context = ns["planet_param_context"](ns["GAME_PARAM_CONTEXT"], 1, planet, is_empty_slot=False)
        target = {
            "schema_version": 1,
            "meta_parameters": {"MIN_FLEET_PER_PLANET": {}},
            "planned_research_levels": {},
            "planets": [
                {
                    "slot_index": 1,
                    "coords": planet.coords,
                    "planet_status": "colonized",
                    "temperature_c": 0,
                    "fields_max": fields_max,
                    "is_central_planet": True,
                    "pays_for_research": True,
                    "buildings": [{"key": key, "target_level": level} for key, level in target_levels.items()],
                    "production": {},
                }
            ],
        }
        problem = ns["build_path_problem"](
            target,
            [planet],
            [context],
            [planet],
            {},
            colony_founding_hours=0.0,
            demolition_time_factor=demolition_time_factor,
        )
        simulator = ns["EmpirePathSimulator"](
            problem,
            dict.fromkeys(ns["RESOURCE_KEYS"], 0.0),
            slot_attack_income=slot_attack_income,
            fleet_command_center_level=fleet_command_center_level,
            spare_percent=spare_percent,
            reward_weeks=reward_weeks,
            weekly_discount=weekly_discount,
        )
        run = simulator.run(strategy or ns["PathStrategy"]("test", rule="cheapest"), horizon_hours=horizon_hours)
        return problem, run, simulator

    def test_path_section_reaches_the_saved_configuration_by_the_rules(self) -> None:
        ns, stdout, _ = self._attack_run()
        problem = ns["PATH_PROBLEM"]
        steps = problem["steps"]
        saved = json.loads((self._metadata_for(self.attack_workspace) / CONFIG_OPTIMIZER_RESULT_FILE).read_text())
        weeks = int(ns["PATH_REWARD_WEEKS"])
        discount = float(ns["PATH_WEEKLY_DISCOUNT"])
        horizon_hours = 168.0 * weeks

        # The problem: every level between the roster of today and the saved configuration, once.
        self.assertEqual(
            ns["PATH_TARGET_PATH"], self._metadata_for(self.attack_workspace) / CONFIG_OPTIMIZER_RESULT_FILE
        )
        self.assertTrue(ns["path_target_checks_df"]["same_planet"].all())
        self.assertEqual(len(problem["planets"]), 11)
        self.assertEqual([planet["founded"] for planet in problem["planets"]], [True] + [False] * 10)
        expected_builds = expected_teardowns = 0
        for entry, planet, state in zip(saved["planets"], problem["planets"], ns["empire_planet_states"]):
            targets = {b["key"]: b["target_level"] for b in entry["buildings"] if b["target_level"] > 0}
            self.assertEqual(planet["target_levels"], targets)
            self.assertEqual(planet["levels"], {k: v for k, v in state.buildings.items() if v > 0})
            for key in {*targets, *planet["levels"]}:
                change = targets.get(key, 0) - planet["levels"].get(key, 0)
                expected_builds += max(0, change)
                expected_teardowns += max(0, -change)
        kinds = ns["PATH_TARGET_SUMMARY"]["steps_by_kind"]
        self.assertEqual((kinds["build"], kinds["demolish"]), (expected_builds, expected_teardowns))
        self.assertGreater(expected_teardowns, 0, "The fixture must make the path tear levels down")
        self.assertEqual(kinds["colonize"], 10)
        self.assertEqual(sum(kinds.values()), len(steps))
        for step in steps[:: max(1, len(steps) // 60)]:
            if step["kind"] == "build":
                cost = game_data.building_cost(int(step["key"][1:]), step["level_to"])
                self.assertEqual(step["cost"], tuple(float(cost.get(r, 0)) for r in ns["RESOURCE_KEYS"]))
                self.assertEqual(step["queue"], step["planet"])
            elif step["kind"] == "demolish":
                self.assertEqual(step["cost"], (0.0, 0.0, 0.0, 0.0))

        # The production model ends where the configuration model says.
        self.assertTrue(ns["path_final_output_df"]["matches_saved"].all())
        self.assertEqual(len(ns["path_final_output_df"]), 11)

        # Every simulated W-week path obeys the rules and weekly reward accounting.
        fixed_income = ns["ATTACK_INCOME_SUMMARY"]["fixed_income_per_hour"]
        slot_income = ns["ATTACK_INCOME_SUMMARY"]["income_per_slot_per_hour"]
        cc_level = ns["PATH_FLEET_COMMAND_CENTER_LEVEL"]
        spare_pct = ns["PATH_RESOURCE_SPARE_PERCENT"]
        ceiling = ns["PATH_REWARD_UPPER_BOUND"]
        self.assertGreater(ceiling, 0.0)
        self.assertEqual(list(ns["PATH_RUNS"]), [strategy.name for strategy in ns["PATH_STRATEGIES"]])
        self.assertEqual(len(ns["PATH_RUNS"]), 3)
        for name, run in [*ns["PATH_RUNS"].items(), ("best", ns["BEST_PATH_RUN"])]:
            self.assertEqual(
                _path_rule_violations(
                    problem,
                    fixed_income,
                    run,
                    slot_attack_income=slot_income,
                    fleet_command_center_level=cc_level,
                    spare_percent=spare_pct,
                    reward_weeks=weeks,
                    weekly_discount=discount,
                ),
                [],
                f"The path of {name!r} breaks a rule",
            )
            self.assertIn(run["status"], ("horizon", "reached"))
            self.assertLessEqual(run["discounted_reward"], ceiling * (1.0 + 1e-12))
            self.assertGreater(run["discounted_reward"], 0.0)
            self.assertEqual(len(run["weekly_spice_eq"]), weeks)
            for step in run["order"]:
                self.assertLess(float(run["start"][step]), horizon_hours)

        # Winning path beats or equals every named strategy on discounted weekly production.
        best = ns["BEST_PATH_RUN"]
        self.assertGreaterEqual(
            best["discounted_reward"],
            max(run["discounted_reward"] for run in ns["PATH_RUNS"].values()) * (1.0 - 1e-12),
        )
        # Discounted payback beats greedy cheapest-first on the multi-planet W-week problem.
        self.assertGreater(
            best["discounted_reward"],
            ns["PATH_RUNS"]["cheapest step first"]["discounted_reward"],
        )

        # Weekly production table and factor levels table match the run.
        summary = ns["BEST_PATH_SUMMARY"]
        weekly_df = ns["best_path_weekly_df"]
        self.assertEqual(len(weekly_df), weeks)
        self.assertEqual(list(weekly_df["week"]), list(range(1, weeks + 1)))
        np.testing.assert_allclose(weekly_df["discount_weight"], [discount**w for w in range(weeks)], rtol=1e-12)
        np.testing.assert_allclose(
            weekly_df["discounted_spice_eq"],
            weekly_df["discount_weight"] * weekly_df["spice_eq"],
            rtol=1e-12,
        )
        self.assertAlmostEqual(float(weekly_df["discounted_spice_eq"].sum()), best["discounted_reward"], places=5)
        self.assertAlmostEqual(
            float(weekly_df["cumulative_discounted_spice_eq"].iloc[-1]), best["discounted_reward"], places=5
        )

        levels_df = ns["best_path_levels_df"]
        self.assertEqual(levels_df.iloc[0]["factor_kind"], "colonies")
        self.assertEqual(int(levels_df.iloc[0]["reached_max_level"]), summary["colonies_founded"])
        for row in levels_df[levels_df["factor_kind"] == "building"].itertuples(index=False):
            expected_max = max((lv.get(row.key, 0) for lv in best["final_levels"]), default=0)
            self.assertEqual(int(row.reached_max_level), expected_max)
            self.assertEqual(
                summary["max_building_levels_reached"][row.name], f"{expected_max}/{int(row.target_max_level)}"
            )
        for row in levels_df[levels_df["factor_kind"] == "research"].itertuples(index=False):
            expected_r = int(best["final_research"].get(row.key, 0))
            self.assertEqual(int(row.reached_max_level), expected_r)
            self.assertEqual(summary["research_levels_reached"][row.name], f"{expected_r}/{int(row.target_max_level)}")

        # Steps table: only actions started within the W weeks, in causal start order.
        path = ns["best_path_steps_df"]
        self.assertEqual(len(path), len(best["order"]))
        self.assertEqual(list(path["order"]), list(range(1, len(best["order"]) + 1)))
        self.assertTrue(path["start_hours"].is_monotonic_increasing)
        self.assertTrue((path["start_hours"] < horizon_hours).all())
        np.testing.assert_allclose(path["end_hours"] - path["start_hours"], path["duration_hours"], rtol=0, atol=1e-9)
        position = {step: place for place, step in enumerate(best["order"])}
        self.assertEqual([steps[step]["name"] for step in best["order"]], list(path["name"]))
        founding = {
            step["planet"]: position[step["id"]]
            for step in steps
            if step["kind"] == "colonize" and step["id"] in position
        }
        chain_place: dict[tuple, int] = {}
        for step_id in best["order"]:
            step = steps[step_id]
            if step["planet"] in founding and step["kind"] != "colonize":
                self.assertGreater(position[step_id], founding[step["planet"]])
            chain = tuple(step["chain"])
            self.assertGreater(position[step_id], chain_place.get(chain, -1))
            chain_place[chain] = position[step_id]
        same_hour = sum(1 for a, b in zip(path["start_hours"], path["start_hours"][1:]) if a == b)
        self.assertGreater(same_hour, 0, "The fixture must start several steps in the same hour")

        # Initial timeline output matches catalog.
        hours, output = ns["path_output_timeline"](ns["PATH_SIMULATOR"], best)
        home, research_now = problem["planets"][0], problem["research_levels"]
        raw_home = _catalog_planet_income(
            home["levels"], home["temperature_c"], research_now["r1"], research_now["r2"], home["solar_cells"], 5.0
        )[1:]
        np.testing.assert_allclose(
            output[0],
            [k * val for k, val in zip(ns["PATH_SIMULATOR"].keep, raw_home)],
            rtol=1e-12,
        )

        # Section 11.8 saved the W-week path as text with the 14-line header including slots, spare% & max factor levels.
        meta_dir = self._metadata_for(self.attack_workspace)
        self.assertEqual(ns["PATH_ACTIONS_PATH"], meta_dir / FULL_PLANNER_PATH_FILE)
        text_lines = (meta_dir / FULL_PLANNER_PATH_FILE).read_text(encoding="utf-8").split("\n")
        self.assertEqual(text_lines[-1], "", "The file ends with a line break")
        header, actions = text_lines[:14], text_lines[14:-1]
        self.assertRegex(header[1], r"^Written: \d{4}-\d\d-\d\d \d\d:\d\d:\d\d [+-]\d{4}$")
        expected_buildings = ns["format_factor_levels"](summary["max_building_levels_reached"])
        expected_research = ns["format_factor_levels"](summary["research_levels_reached"])
        self.assertIn("Pig iron mine ", expected_buildings)
        self.assertEqual(summary["status"], "horizon")
        self.assertIn(f"Result: {weeks}-week horizon ended with ", header[7])
        self.assertNotIn("None", header[7])
        s0, s1 = summary["start_fleet_slots"], summary["final_fleet_slots"]
        self.assertGreater(s1, s0, "Founding colonies and upgrading Fleet base (b10) grows fleet slots")
        per_slot = summary["attacks_per_slot_per_hour"]
        spare_txt = ", ".join(f"{res} {pct:.1f}%" for res, pct in summary["spare_percent"].items())
        self.assertEqual(
            [header[0], *header[2:7], *header[8:]],
            [
                f"Best {weeks}-week path toward the saved configuration (notebooks/full_planner.ipynb, Section 11)",
                f"Configuration: {CONFIG_OPTIMIZER_RESULT_FILE}, saved {saved['generated_at']}",
                f"Strategy: {summary['strategy']}",
                f"Horizon: {weeks} weeks ({summary['horizon']}), weekly discount {discount:.2f}",
                f"Attacks & spare: {s0} -> {s1} fleet slots ({s0 * per_slot:.2f} -> {s1 * per_slot:.2f} attacks/h); "
                f"spare {spare_txt}",
                f"Reward: {summary['discounted_reward']:,.1f} SpiceEq net discounted "
                f"({summary['total_spice_eq_produced']:,.1f} net SpiceEq produced in {weeks} weeks; "
                f"week 1: {summary['week_1_spice_eq']:,.1f}, week {weeks}: {summary['last_week_spice_eq']:,.1f})",
                f"Colonies reached: {summary['colonies_founded']}/{summary['colonies_target']} founded",
                f"Max building levels reached: {expected_buildings}",
                f"Research levels reached: {expected_research}",
                f"Actions: {len(actions)} within {weeks} weeks, in the order in which they start. "
                "Times count from the moment this file was written.",
                "Planets are named by their coordinates. "
                'EMPTY:nn is an empty colony slot: "found the colony" comes first there.',
                "",
            ],
        )
        phrases = {
            "build": "build {name} level {to}",
            "demolish": "tear down {name} to level {to}",
            "research": "research {name} level {to}",
            "ships": "build {to} x {name}",
            "colonize": "found the colony",
        }
        rows = path.to_dict("records")
        self.assertEqual(len(actions), len(rows))
        width = len(str(len(rows)))
        for line, row in zip(actions, rows):
            where = "" if row["planet"] == "account" else f" on {row['planet']}"
            action = phrases[row["kind"]].format(name=row["name"], to=row["to_level"])
            took = ns["fmt_path_time"](row["duration_hours"])
            self.assertEqual(line, f"{row['order']:>{width}}. at {row['start']}{where}: {action} (takes {took})")
        self.assertEqual(
            ns["PATH_ACTIONS_SUMMARY"],
            {
                "file": FULL_PLANNER_PATH_FILE,
                "folder": str(meta_dir),
                "status": summary["status"],
                "reward_weeks": weeks,
                "discounted_reward": round(float(summary["discounted_reward"]), 1),
                "actions": len(rows),
                "lines": len(rows) + 14,
                "first_action": actions[0],
                "last_action": actions[-1],
            },
        )
        self.assertIn(
            f"{len(rows)} actions ({weeks} weeks) saved in {FULL_PLANNER_PATH_FILE}", stdout[self.path_idxs[-1]]
        )

        # Whatever a strategy proposes, the simulator refuses a step on a planet that is not founded yet.
        simulator = ns["PATH_SIMULATOR"]
        simulator._load_start()
        on_colony = next(step["id"] for step in steps if step["kind"] == "build" and step["planet"] == 1)
        self.assertIn(("colony", 1), simulator.blockers(on_colony))
        self.assertEqual(simulator.blockers(simulator.colonize_step[1]), [])
        self.assertFalse(simulator._start_proposals([((1.0, 0.0, 0), on_colony)]))
        self.assertEqual((simulator.reserved, simulator.started[on_colony]), (-1, False))

    def test_path_simulator_on_problems_worked_out_by_hand(self) -> None:
        ns, _, _ = self._attack_run()
        speed = float(ns["GAME_PARAM_CONTEXT"]["speedup_factor"])
        self.assertEqual(speed, 5.0)

        def base_hours(building_id: int, level: int) -> float:
            return float(game_data.BUILDING_BY_ID[building_id]["levels"][level]["base_time_s"]) / 3600.0

        def cost_of(building_id: int, level: int) -> tuple[float, ...]:
            cost = game_data.building_cost(building_id, level)
            return tuple(float(cost.get(resource, 0)) for resource in ns["RESOURCE_KEYS"])

        # Invalid discount / week parameters are rejected.
        with self.assertRaisesRegex(ValueError, "reward_weeks"):
            self._tiny_path(ns, {"b1": 1}, {"b1": 2}, reward_weeks=0)
        with self.assertRaisesRegex(ValueError, "weekly_discount"):
            self._tiny_path(ns, {"b1": 1}, {"b1": 2}, weekly_discount=0.0)
        with self.assertRaisesRegex(ValueError, "weekly_discount"):
            self._tiny_path(ns, {"b1": 1}, {"b1": 2}, weekly_discount=1.1)

        # 1. A level that the stock pays for starts at once, finishes in week 1, and earns exact weekly discount.
        levels = {"b1": 1, "b3": 1, "b5": 2, "b7": 2}
        problem, run, _ = self._tiny_path(
            ns, levels, {**levels, "b1": 2}, stock=cost_of(1, 2), reward_weeks=3, weekly_discount=0.80
        )
        dur = base_hours(1, 2) / (3 * speed)
        self.assertEqual((run["status"], run["start"], run["smelting_spans"]), ("reached", [0.0], []))
        self.assertAlmostEqual(run["hours"], dur, places=12)
        before_out = _catalog_planet_income(levels, 0, 0, 0, 0, speed)
        after_out = _catalog_planet_income({**levels, "b1": 2}, 0, 0, 0, 0, speed)
        rate_before = before_out[1] / 6.0 + before_out[3] / 3.0
        rate_after = after_out[1] / 6.0 + after_out[3] / 3.0
        expected_w1 = rate_before * dur + rate_after * (168.0 - dur)
        expected_w2 = rate_after * 168.0
        expected_disc = expected_w1 + 0.80 * expected_w2 + (0.80**2) * expected_w2
        self.assertAlmostEqual(run["weekly_spice_eq"][0], expected_w1, places=9)
        self.assertAlmostEqual(run["weekly_spice_eq"][1], expected_w2, places=9)
        self.assertAlmostEqual(run["discounted_reward"], expected_disc, places=9)
        self.assertEqual(
            _path_rule_violations(
                problem, dict.fromkeys(ns["RESOURCE_KEYS"], 0.0), run, reward_weeks=3, weekly_discount=0.80
            ),
            [],
        )

        # 2. Without stock the empire first earns the cost: the resource that takes longest sets the start.
        problem, run, _ = self._tiny_path(ns, levels, {**levels, "b1": 2})
        mined, _, _, kryptonite, _ = _catalog_planet_income(levels, 0, 0, 0, 0, speed)
        wait = max(cost_of(1, 2)[0] / mined, cost_of(1, 2)[2] / kryptonite)
        self.assertAlmostEqual(run["start"][0], wait, places=9)
        self.assertAlmostEqual(run["hours"], wait + dur, places=9)
        simulator = ns["EmpirePathSimulator"](
            problem, {"pig_iron": mined, "metal": 0.0, "kryptonite": kryptonite, "spice": 0.0}
        )
        self.assertAlmostEqual(
            simulator.run(ns["PathStrategy"]("test", rule="cheapest"))["start"][0], wait / 2.0, places=9
        )

        # 3. The furnace runs only until the Metal of the next step is there, then the Pig iron is kept.
        levels = {"b1": 5, "b2": 5, "b5": 5}
        problem, run, _ = self._tiny_path(ns, levels, {**levels, "b12": 1})
        mined, left, metal, _, _ = _catalog_planet_income(levels, 0, 0, 0, 0, speed)
        pig_iron_cost, metal_cost = cost_of(12, 1)[:2]
        smelting = metal_cost / metal
        wait = smelting + max(0.0, pig_iron_cost - left * smelting) / mined
        self.assertAlmostEqual(run["start"][0], wait, places=9)
        self.assertEqual(len(run["smelting_spans"]), 1)
        np.testing.assert_allclose(run["smelting_spans"][0], (0.0, smelting), rtol=1e-12)
        self.assertAlmostEqual(run["metal_smelted"], metal_cost, places=6)
        self.assertEqual(_path_rule_violations(problem, dict.fromkeys(ns["RESOURCE_KEYS"], 0.0), run), [])

        # 4. A cost above the warehouse capacity waits for the warehouse level.
        levels = {"b1": 18, "b5": 20}
        rich = (10**7, 10**7, 10**7, 10**7)
        problem, run, simulator = self._tiny_path(ns, levels, {**levels, "b1": 19, "b11": 1}, stock=rich)
        mine, warehouse = (next(s["id"] for s in problem["steps"] if s["key"] == key) for key in ("b1", "b11"))
        simulator._load_start()
        self.assertEqual(simulator.blockers(mine), [("storage", 0, 0, 1)])
        self.assertEqual(run["start"][mine], run["end"][warehouse])
        with self.assertRaisesRegex(ValueError, "costs more pig_iron than the Pig iron warehouse"):
            self._tiny_path(ns, levels, {**levels, "b1": 19}, stock=rich)

        # 5. A full planet tears a level down before it builds; tearing down takes the factor of the build time.
        levels = {"b1": 2, "b3": 1, "b5": 2}
        for strategy in (
            ns["PathStrategy"]("test", rule="cheapest"),
            ns["PathStrategy"]("test", rule="payback", values="scarcity"),
        ):
            problem, run, _ = self._tiny_path(
                ns, levels, {"b1": 3, "b5": 2}, stock=rich, fields_max=5, strategy=strategy, demolition_time_factor=0.5
            )
            build, teardown = (
                next(s["id"] for s in problem["steps"] if s["kind"] == kind) for kind in ("build", "demolish")
            )
            self.assertEqual(run["start"][teardown], 0.0)
            self.assertAlmostEqual(run["end"][teardown], 0.5 * base_hours(3, 1) / speed, places=12)
            self.assertEqual(run["start"][build], run["end"][teardown])
            self.assertEqual(run["final_levels"], [{"b1": 3, "b5": 2}])
            self.assertEqual(_path_rule_violations(problem, dict.fromkeys(ns["RESOURCE_KEYS"], 0.0), run), [])

        # 6. Stuck path and missing prerequisite check.
        problem, run, _ = self._tiny_path(ns, {}, {"b1": 1})
        self.assertEqual(
            (run["status"], run["hours"], run["steps_started"], run["discounted_reward"]),
            ("stuck", float("inf"), 0, 0.0),
        )
        with self.assertRaisesRegex(ValueError, "Weapons factory 1 on 09:099:09 needs Energy technology 1, but"):
            self._tiny_path(ns, {}, {"b9": 1})

        # 7. Lower bounds and horizon truncation (`status == "horizon"` when the step starts after the window).
        levels = {"b1": 1, "b3": 1, "b5": 2}
        problem, run, simulator = self._tiny_path(ns, levels, {**levels, "b7": 2, "b1": 3})
        bounds = dict(simulator.lower_bounds())
        queue = (
            base_hours(7, 1) / speed
            + base_hours(7, 2) / (2 * speed)
            + (base_hours(1, 2) + base_hours(1, 3)) / (3 * speed)
        )
        self.assertAlmostEqual(bounds["building queue of 09:099:09"], queue, places=12)
        need = [sum(step["cost"][position] for step in problem["steps"]) for position in range(4)]
        top = _catalog_planet_income({**levels, "b1": 3}, 0, 0, 0, 0, speed)
        self.assertAlmostEqual(bounds["supply of Kryptonite"], need[2] / top[3], places=9)
        self.assertEqual(bounds["supply of Metal"], float("inf"))

        _, clipped, _ = self._tiny_path(
            ns, {"b1": 1, "b3": 1, "b5": 2}, {"b1": 2, "b3": 1, "b5": 2}, horizon_hours=0.01
        )
        self.assertEqual(
            (clipped["status"], clipped["steps_started"], clipped["stopped_at_hours"]), ("horizon", 0, 0.01)
        )

        # 8. Spare percentage deducts from both mine income and raid loot, and scales net weekly reward.
        for bad_spare in (
            {"pig_iron": 10.0},
            {"pig_iron": -1.0, "metal": 0.0, "kryptonite": 0.0, "spice": 0.0},
            {"pig_iron": 100.0, "metal": 0.0, "kryptonite": 0.0, "spice": 0.0},
        ):
            with self.assertRaisesRegex(ValueError, "spare_percent"):
                self._tiny_path(ns, {"b1": 1}, {"b1": 2}, spare_percent=bad_spare)
        with self.assertRaisesRegex(ValueError, "fleet_command_center_level"):
            self._tiny_path(ns, {"b1": 1}, {"b1": 2}, fleet_command_center_level=-1)

        levels = {"b1": 1, "b3": 1, "b5": 2}
        spare_50 = {"pig_iron": 50.0, "metal": 50.0, "kryptonite": 50.0, "spice": 50.0}
        _, run_0, _ = self._tiny_path(ns, levels, {**levels, "b1": 2})
        prob_50, run_50, _ = self._tiny_path(ns, levels, {**levels, "b1": 2}, spare_percent=spare_50)
        # Keeping 50% of hourly income doubles the wait to afford b1 level 2.
        self.assertAlmostEqual(run_50["start"][0], 2.0 * run_0["start"][0], places=9)
        self.assertGreater(sum(run_50["spare_diverted"]), 0.0)
        self.assertEqual(
            _path_rule_violations(prob_50, dict.fromkeys(ns["RESOURCE_KEYS"], 0.0), run_50, spare_percent=spare_50),
            [],
        )

        # 9. Dynamic Fleet base (`b10`) slots: completing b10 level 1 raises slots from 1 to 2 and doubles raid income.
        levels_b10 = {"b7": 3, "b9": 2}
        slot_loot = {"pig_iron": 10_000.0, "metal": 10_000.0, "kryptonite": 10_000.0, "spice": 10_000.0}
        prob_b10, run_b10, _ = self._tiny_path(
            ns, levels_b10, {**levels_b10, "b10": 1}, stock=cost_of(10, 1), slot_attack_income=slot_loot
        )
        self.assertEqual((run_b10["start_fleet_slots"], run_b10["final_fleet_slots"]), (1, 2))
        self.assertEqual(
            _path_rule_violations(
                prob_b10, dict.fromkeys(ns["RESOURCE_KEYS"], 0.0), run_b10, slot_attack_income=slot_loot
            ),
            [],
        )
        # Fixed fleet_command_center_level=3 gives 1 + 3 = 4 slots.
        _, run_cc, _ = self._tiny_path(
            ns,
            levels_b10,
            {**levels_b10, "b10": 1},
            stock=cost_of(10, 1),
            slot_attack_income=slot_loot,
            fleet_command_center_level=3,
        )
        self.assertEqual((run_cc["start_fleet_slots"], run_cc["final_fleet_slots"]), (4, 4))

    def test_median_attack_income(self) -> None:
        ns, _, _ = self._attack_run()
        table, summary = ns["median_attack_df"], ns["ATTACK_INCOME_SUMMARY"]
        self.assertEqual(list(table["metric"]), [*ns["RESOURCE_KEYS"], "spice_eq", "net_spice_eq"])
        self.assertEqual(list(table["median_per_attack"][:4]), [3000.0, 1250.0, 1500.0, 500.0])
        # One-way flights 300s, 360s, 200s -> round-trip 600s, 720s, 400s -> median round-trip 600s (6 attacks/slot/h).
        self.assertEqual(summary["median_round_trip_sec"], 600.0)
        self.assertAlmostEqual(summary["attacks_per_slot_per_hour"], 6.0)
        home_b10 = ns["live_planet_states"][0].buildings.get("b10", 0)
        expected_slots = 1 + home_b10
        self.assertEqual(summary["fleet_slots_now"], expected_slots)
        self.assertEqual(summary["pace_set_by"], "Fleet base (b10) slots x median round-trip")
        self.assertEqual(
            {k: round(v, 9) for k, v in summary["income_per_slot_per_hour"].items()},
            {"pig_iron": 18000.0, "metal": 7500.0, "kryptonite": 9000.0, "spice": 3000.0},
        )
        self.assertEqual(
            {k: round(v, 9) for k, v in summary["income_per_hour"].items()},
            {
                "pig_iron": 18000.0 * expected_slots,
                "metal": 7500.0 * expected_slots,
                "kryptonite": 9000.0 * expected_slots,
                "spice": 3000.0 * expected_slots,
            },
        )
        self.assertEqual(ns["PATH_SIMULATOR"].slot_attack, tuple(summary["income_per_slot_per_hour"].values()))

        # Explicit fleet_command_center_level=4 -> 5 slots; median still ignores a single extreme outlier.
        attacks = ns["attacks_df"].copy()
        attacks.loc[0, "pig_iron"] = 6_000_000.0
        table_cc, summary_cc = ns["median_attack_income"](
            attacks, ns["round_trip_times_arr"], ns["live_planet_states"], fleet_command_center_level=4
        )
        self.assertEqual(table_cc["median_per_attack"].iloc[0], 3000.0)
        self.assertEqual(summary_cc["fleet_slots_now"], 5)
        self.assertEqual(summary_cc["income_per_hour"]["pig_iron"], 5 * 18000.0)

        # Explicit attacks_per_day=24.0 overrides slots; 0.0 or empty history gives zero income.
        _, summary_fixed = ns["median_attack_income"](
            attacks, ns["round_trip_times_arr"], ns["live_planet_states"], attacks_per_day=24.0
        )
        self.assertEqual(
            (summary_fixed["attacks_per_day"], summary_fixed["pace_set_by"]), (24.0, "PATH_ATTACKS_PER_DAY")
        )
        self.assertEqual(summary_fixed["income_per_hour"]["pig_iron"], 3000.0)
        for frame, rt, planets_arg, pace in (
            (ns["attacks_df"].iloc[:0], np.array([]), ns["live_planet_states"], None),
            (ns["attacks_df"], ns["round_trip_times_arr"], ns["live_planet_states"], 0.0),
            (ns["attacks_df"], ns["round_trip_times_arr"], [], None),
        ):
            _, empty_sum = ns["median_attack_income"](frame, rt, planets_arg, attacks_per_day=pace)
            self.assertEqual(set(empty_sum["income_per_hour"].values()), {0.0})

    def test_path_target_file_is_checked(self) -> None:
        ns, _, _ = self._attack_run()
        saved_path = self._metadata_for(self.attack_workspace) / CONFIG_OPTIMIZER_RESULT_FILE
        saved = json.loads(saved_path.read_text(encoding="utf-8"))
        planets, contexts = ns["empire_planet_states"], ns["PLANET_PARAM_CONTEXTS"]
        with tempfile.TemporaryDirectory(prefix="si_fp_path_") as tmp:
            with self.assertRaisesRegex(FileNotFoundError, r"run notebooks/config_optimizer\.ipynb first"):
                ns["load_path_target"](Path(tmp) / "missing.json")
            other = Path(tmp) / "other.json"
            other.write_text(json.dumps({**saved, "schema_version": 2}), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "schema_version 2"):
                ns["load_path_target"](other)
        self.assertEqual(ns["load_path_target"](saved_path), saved)
        with self.assertRaisesRegex(ValueError, r"plans the roster slots \[1, 2, 3, 4, 5, 6, 7, 8, 9, 10\]"):
            ns["check_path_target"]({**saved, "planets": saved["planets"][:-1]}, planets, contexts)
        too_big = json.loads(json.dumps(saved))
        too_big["planets"][0]["buildings"][0]["target_level"] += 400
        with self.assertRaisesRegex(ValueError, r"levels on 02:380:12 \(slot 1\), which has \d+ fields"):
            ns["check_path_target"](too_big, planets, contexts)
        moved = json.loads(json.dumps(saved))
        moved["planets"][3]["temperature_c"] = 55
        checks = ns["check_path_target"](moved, planets, contexts)
        self.assertEqual(list(checks["same_planet"]), [True, True, True, False] + [True] * 7)

    def test_path_search_with_the_genetic_algorithm(self) -> None:
        # Small reachable configuration that finishes well inside the 26-week horizon (exercises all 5 action kinds).
        with tempfile.TemporaryDirectory(prefix="si_fp_path_") as tmp:
            meta_dir = Path(tmp) / "metadata"
            shutil.copytree(
                FULL_PLANNER_METADATA_DIR,
                meta_dir,
                ignore=shutil.ignore_patterns(CONFIG_OPTIMIZER_RESULT_FILE, FULL_PLANNER_PATH_FILE),
            )
            _save_configuration_for(
                self.attack_workspace,
                meta_dir,
                {
                    "OPTIMIZED_BUILDING_MAX_LEVELS": dict.fromkeys(("b1", "b2", "b3", "b4", "b5", "b9", "b17"), 14),
                    "RESEARCH_PERCENT_OF_UNIVERSE_MAX": 0.0,
                },
            )
            ns, _, _ = _run_full_planner(
                self.code_cells,
                self.attack_workspace,
                metadata_dir=meta_dir,
                overrides={"PATH_GA_POPULATION_SIZE": 4, "PATH_GA_GENERATIONS": 2, "PATH_SEARCH_TOLERANCE": -1.0},
            )
            txt_lines = (meta_dir / FULL_PLANNER_PATH_FILE).read_text(encoding="utf-8").split("\n")
        summary, history = ns["PATH_SEARCH_SUMMARY"], ns["path_ga_history_df"]
        self.assertTrue(summary["genetic_algorithm_ran"])
        self.assertEqual(list(history["generation"]), [0, 1, 2])
        self.assertTrue(history["best_so_far_reward"].is_monotonic_increasing)
        self.assertTrue((history["best_reward"] >= history["mean_reward"]).all())
        self.assertEqual(int(history["simulations_so_far"].iloc[-1]), summary["simulations_of_the_genetic_algorithm"])
        self.assertLessEqual(summary["simulations_of_the_genetic_algorithm"], 4 * 3)
        self.assertEqual(summary["tuned_reward"], float(history["best_so_far_reward"].iloc[-1]))
        self.assertEqual(list(summary["tuned_genes"]), list(ns["PATH_GENE_RANGES"]))
        for name, gene in summary["tuned_genes"].items():
            low, high = ns["PATH_GENE_RANGES"][name]
            self.assertTrue(low <= gene <= high, f"gene {name} left its range")
        scarcity = ns["PATH_RUNS"]["discounted payback at scarcity values"]["discounted_reward"]
        self.assertGreaterEqual(summary["tuned_reward"], scarcity * (1.0 - 1e-12))
        named = max(run["discounted_reward"] for run in ns["PATH_RUNS"].values())
        self.assertGreaterEqual(
            ns["BEST_PATH_RUN"]["discounted_reward"], max(named, summary["tuned_reward"]) * (1.0 - 1e-12)
        )
        # On this small configuration every step finishes within the 26 weeks (`status == "reached"`), covering all 5 kinds.
        self.assertEqual(ns["BEST_PATH_RUN"]["status"], "reached")
        self.assertTrue(ns["BEST_PATH_SUMMARY"]["ends_with_the_output_of_the_configuration"])
        self.assertEqual(set(ns["best_path_steps_df"]["kind"]), {"build", "demolish", "research", "ships", "colonize"})
        self.assertIn("Result: configuration reached after ", txt_lines[7])
        self.assertTrue(any(": tear down " in line for line in txt_lines[14:]))
        self.assertTrue(any(": research " in line and " on " not in line for line in txt_lines[14:]))
        self.assertTrue(any(" x " in line for line in txt_lines[14:]))
        self.assertEqual(
            _path_rule_violations(
                ns["PATH_PROBLEM"],
                ns["ATTACK_INCOME_SUMMARY"]["fixed_income_per_hour"],
                ns["BEST_PATH_RUN"],
                slot_attack_income=ns["ATTACK_INCOME_SUMMARY"]["income_per_slot_per_hour"],
                fleet_command_center_level=ns["PATH_FLEET_COMMAND_CENTER_LEVEL"],
                spare_percent=ns["PATH_RESOURCE_SPARE_PERCENT"],
            ),
            [],
        )

        # Same seed gives the exact same result; all 8 genes map onto strategy parameters.
        settings = dict(
            population_size=4,
            generations=2,
            tournament_size=3,
            crossover_rate=0.9,
            mutation_rate=0.3,
            elite_count=2,
            random_seed=ns["PATH_GA_RANDOM_SEED"],
            revalue_every_steps=ns["PATH_REVALUE_EVERY_STEPS"],
        )
        start = (
            ns["PATH_SCARCITY_FOCUS"],
            ns["PATH_CRITICAL_QUEUE_SHARE"],
            ns["PATH_PATIENCE"],
            ns["PATH_SPEED_WEIGHT"],
            0.0,
            0.0,
            0.0,
            0.0,
        )
        genes, run, again = ns["tune_path_strategy"](ns["PATH_SIMULATOR"], start, **settings)
        self.assertEqual(run["discounted_reward"], summary["tuned_reward"])
        self.assertEqual(dict(zip(ns["PATH_GENE_RANGES"], genes)), summary["tuned_genes"])
        pd.testing.assert_frame_equal(again, history)
        strategy = ns["strategy_from_genes"]("x", (2.0, 0.5, 0.75, 1.25, 1.0, 0.0, -1.0, 2.0), 7)
        self.assertEqual(
            strategy.parameters(),
            {
                "rule": "payback",
                "values": "scarcity",
                "focus": 2.0,
                "critical_share": 0.5,
                "patience": 0.75,
                "speed_weight": 1.25,
                "bias": (2.0, 1.0, 0.5, 4.0),
            },
        )
        self.assertEqual(strategy.revalue_every, 7)
        for off in ({"generations": 0}, {"population_size": 1}):
            _, best_run, empty, why = ns["search_fastest_path"](
                ns["PATH_SIMULATOR"],
                ns["PATH_STRATEGIES"],
                ns["PATH_RUNS"],
                ns["PATH_REWARD_UPPER_BOUND"],
                tolerance=-1.0,
                start_genes=start,
                **{**settings, **off},
            )
            self.assertEqual(
                (best_run["discounted_reward"], len(empty), why["genetic_algorithm_ran"]), (named, 0, False)
            )
            self.assertEqual(why["why"], "the genetic algorithm is switched off")


CONFIG_OPTIMIZER_PATH = REPO_ROOT / "notebooks" / "config_optimizer.ipynb"
# The one file that the notebook writes, into its METADATA_DIR.
CONFIG_OPTIMIZER_RESULT_FILE = "config_optimizer_result.json"
# Section number of the heading above every code cell, in notebook order.
CONFIG_OPTIMIZER_SECTIONS = [
    "0",
    "0.1",
    "1",
    "2.1",
    "2.2",
    "2.3",
    "2.4",
    "2.5",
    "2.6",
    "2.7",
    "2.8",
    "2.9",
    "3.1",
    "3.2",
    "4.1",
    "4.2",
    "4.3",
    "4.4",
    "4.5",
    "5",
]
# Sections whose code cell generates data and ends by printing it. Section 1, the meta parameters, only sets
# values; Section 4.1 prints them.
CONFIG_OPTIMIZER_PRINTING_SECTIONS = ["2.2", "2.5", "2.8", "3.1", "3.2", "4.1", "4.2", "4.3", "4.4", "4.5", "5"]
# Names that both notebooks assign at top level, but not with the same statement, and why.
CONFIG_OPTIMIZER_OWN_STATEMENTS = {
    "colony_researches": "read by load_account_snapshot; full_planner takes it from its attack telemetry",
    "GAME_PARAM_CONTEXT": "holds only the account-wide inputs that the optimizer uses",
}
# Helpers of Section 2 that full_planner does not have.
CONFIG_OPTIMIZER_OWN_HELPERS = {"load_account_snapshot"}


def _config_optimizer_meta_parameters() -> dict[str, object]:
    """The meta parameters of Section 1 that the tests plan with.

    The values in the notebook's own cell are the user's to tune. Every test run therefore replaces them
    with these, the values that the expectations of the tests were worked out for.
    """
    return {
        "TARGET_SHIP_KEY": "s16",
        "PRODUCTION_MONTH_DAYS": 30.0,
        "RESEARCH_PERCENT_OF_UNIVERSE_MAX": 100.0,
        "MIN_FLEET_PER_PLANET": {"s2": 10, "s10": 10, "s11": 10},
        "MISC_BUILDING_LEVELS": {
            "b6": 0,
            "b7": 12,
            "b8": 0,
            "b10": 5,
            "b11": 0,
            "b12": 0,
            "b13": 0,
            "b14": 0,
            "b15": 0,
            "b16": 0,
            "b18": 0,
            "b19": 0,
            "b20": 0,
            "b21": 0,
        },
        "MISC_BUILDING_LEVELS_BY_SLOT": {1: {"b8": 12}},
        "WAREHOUSE_HOURS_OF_PRODUCTION": 24.0,
        "CENTRAL_PLANET_SLOT": 1,
        "CENTRAL_PLANET_MIN_WAREHOUSE_LEVEL": 15,
        "OPTIMIZED_BUILDING_MAX_LEVELS": {"b1": 50, "b2": 50, "b3": 50, "b4": 50, "b5": 50, "b9": 50, "b17": 50},
        "CATALOGUE_GRID_SIZE": 100_000,
        "CATALOGUE_PRIORITY_STEPS": 4,
        "HILL_CLIMB_STEP_SIZES": (1, 2, 4, 8),
        "GA_POPULATION_SIZE": 400,
        "GA_GENERATIONS": 300,
        "GA_TOURNAMENT_SIZE": 3,
        "GA_CROSSOVER_RATE": 0.9,
        "GA_MUTATION_RATE": 0.1,
        "GA_ELITE_COUNT": 4,
        "GA_RANDOM_SEED": 20261117,
    }


def _run_config_optimizer(
    code_cells: list[dict],
    workspace: Path,
    metadata_dir: Path,
    *,
    last_cell: int | None = None,
    overrides: dict[str, object] | None = None,
) -> tuple[dict, dict[int, str], dict[int, set[str]]]:
    """Execute the code cells of config_optimizer.ipynb like `_run_full_planner` executes those of full_planner.

    `metadata_dir` is not optional: the last cell of the notebook writes its result into METADATA_DIR, and a
    test run must never write into the repository. The meta parameters of Section 1 always get the values of
    `_config_optimizer_meta_parameters`, unless `overrides` names others.
    """
    if metadata_dir.resolve() == FULL_PLANNER_METADATA_DIR.resolve():
        raise ValueError("A test run of config_optimizer.ipynb needs its own copy of notebooks/metadata/")
    return _run_full_planner(
        code_cells,
        workspace,
        metadata_dir=metadata_dir,
        last_cell=last_cell,
        overrides={**_config_optimizer_meta_parameters(), **(overrides or {})},
        label="config_optimizer",
    )


def _top_level_definitions(code_cells: list[dict]) -> dict[str, tuple[int, str]]:
    """`{name: (code-cell index, source text)}` of every function, class and assignment at the top level of a notebook.

    The text of a function or class includes its decorators. A name that several statements assign keeps the
    last one, the one that is in force when the notebook has run.
    """
    found: dict[str, tuple[int, str]] = {}
    for idx, cell in enumerate(code_cells):
        source = _cell_source(cell)
        if source.lstrip().startswith("!"):
            continue
        lines = source.split("\n")
        for node in ast.parse(source).body:
            if isinstance(node, (ast.FunctionDef, ast.ClassDef)):
                names = [node.name]
            elif isinstance(node, (ast.Assign, ast.AnnAssign)):
                targets = node.targets if isinstance(node, ast.Assign) else [node.target]
                names = [sub.id for target in targets for sub in ast.walk(target) if isinstance(sub, ast.Name)]
            else:
                continue
            first_line = min([node.lineno, *(decorator.lineno for decorator in getattr(node, "decorator_list", []))])
            text = "\n".join(lines[first_line - 1 : node.end_lineno])
            for name in names:
                found[name] = (idx, text)
    return found


def _json_document(value: object) -> object:
    """`value` as it comes back from a JSON file: tuples as lists, every key as text."""
    return json.loads(json.dumps(value))


@pytest.mark.notebooks
class TestConfigOptimizerNotebook(unittest.TestCase):
    """`notebooks/config_optimizer.ipynb`: layout rules, the code it shares with full_planner, the optimizer of the
    empire configuration, and the JSON file that it saves."""

    _assert_cell_ast_safe = _assert_notebook_cell_ast_safe
    _parse_printout = TestFullPlannerNotebook._parse_printout

    @classmethod
    def setUpClass(cls) -> None:
        cls.nb = json.loads(CONFIG_OPTIMIZER_PATH.read_text(encoding="utf-8"))
        cls.cells = cls.nb["cells"]
        cls.code_positions = [pos for pos, cell in enumerate(cls.cells) if cell.get("cell_type") == "code"]
        cls.code_cells = [cls.cells[pos] for pos in cls.code_positions]
        # The section number of the heading above each code cell ("" when a cell has none: the layout test says so).
        cls.sections = [
            (_last_heading_number(_cell_source(cls.cells[pos - 1])) or "") if pos > 0 else ""
            for pos in cls.code_positions
        ]
        cls.parameters_idx = cls.sections.index("1")
        cls.metadata_cell_idx = cls.sections.index("2.5")
        cls.universe_research_cell_idx = cls.sections.index("3.2")
        cls.optimizer_idxs = [idx for idx, section in enumerate(cls.sections) if section.startswith("4.")]
        cls.save_idx = cls.sections.index("5")
        tmp = tempfile.TemporaryDirectory(prefix="si_co_test_")
        cls.addClassCleanup(tmp.cleanup)
        cls.tmp_root = Path(tmp.name)
        cls.attack_workspace = _build_full_planner_workspace(cls.tmp_root / "attacks", with_attacks=True)
        cls.cold_workspace = _build_full_planner_workspace(cls.tmp_root / "cold", with_attacks=False)
        cls.three_planet_workspace = _build_full_planner_workspace(
            cls.tmp_root / "three_planets", with_attacks=True, state=_three_planet_state()
        )
        cls.universe_workspace = _build_full_planner_workspace(
            cls.tmp_root / "universe", with_attacks=True, universe_research=True
        )
        cls.repository_metadata_before = cls._folder_fingerprint(FULL_PLANNER_METADATA_DIR)
        cls._runs: dict[Path, tuple[dict, dict[int, str], dict[int, set[str]]]] = {}
        cls._workspace_before_after: dict[Path, tuple[dict[str, str], dict[str, str]]] = {}
        cls._metadata_copies = 0

    @classmethod
    def tearDownClass(cls) -> None:
        # Every run of this class works on its own copy of the metadata folder: the repository's stays as it was.
        if cls._folder_fingerprint(FULL_PLANNER_METADATA_DIR) != cls.repository_metadata_before:
            raise AssertionError("A test run of config_optimizer.ipynb changed notebooks/metadata/ in the repository")

    @staticmethod
    def _folder_fingerprint(folder: Path) -> dict[str, str]:
        return {
            str(path.relative_to(folder)): _sha256_bytes(path) for path in sorted(folder.rglob("*")) if path.is_file()
        }

    @classmethod
    def _metadata_copy(cls) -> Path:
        """A fresh copy of `notebooks/metadata/` for one run: the folder that the run saves its result into."""
        cls._metadata_copies += 1
        target = cls.tmp_root / f"metadata_{cls._metadata_copies}" / "metadata"
        shutil.copytree(FULL_PLANNER_METADATA_DIR, target)
        return target

    @classmethod
    def _cached_run(cls, workspace: Path) -> tuple[dict, dict[int, str], dict[int, set[str]]]:
        """The notebook executed once on a workspace of the class, with its own metadata copy (cached).

        The fingerprints of the workspace before and after the run are kept in `_workspace_before_after`.
        """
        if workspace not in cls._runs:
            before = cls._folder_fingerprint(workspace)
            cls._runs[workspace] = _run_config_optimizer(cls.code_cells, workspace, cls._metadata_copy())
            cls._workspace_before_after[workspace] = (before, cls._folder_fingerprint(workspace))
        return cls._runs[workspace]

    @classmethod
    def _attack_run(cls) -> tuple[dict, dict[int, str], dict[int, set[str]]]:
        """The notebook executed once on the workspace with three attacks (cached for the whole class)."""
        return cls._cached_run(cls.attack_workspace)

    def _rerun_planner(self, ns: dict, changes: dict[str, object]) -> dict:
        """Section 4 executed again on a copy of a finished run, with other meta parameters.

        Its cells only read what Sections 1 to 3 left behind, so the copy can share those objects. Section 5 is
        not executed: a rerun saves nothing.
        """
        rerun = {**ns, **changes}
        with contextlib.redirect_stdout(io.StringIO()):
            for idx in self.optimizer_idxs:
                exec(compile(_cell_source(self.code_cells[idx]), f"config_optimizer_cell_{idx}", "exec"), rerun, rerun)
        return rerun

    def _notebook_meta_parameters(self) -> dict[str, object]:
        """The meta parameters as the notebook's own Section 1 cell sets them."""
        statements = ast.parse(_cell_source(self.code_cells[self.parameters_idx])).body
        return {statement.target.id: ast.literal_eval(statement.value) for statement in statements}

    # ------------------------------------------------------------------ layout and hygiene

    def test_layout_one_described_code_cell_per_component(self) -> None:
        self.assertEqual((self.nb.get("nbformat"), self.nb.get("nbformat_minor")), (4, 5))
        cell_ids = [cell.get("id") for cell in self.cells]
        self.assertTrue(all(isinstance(cell_id, str) and cell_id.strip() for cell_id in cell_ids))
        self.assertEqual(len(set(cell_ids)), len(cell_ids), "Cell ids must be unique")
        for pos, cell in enumerate(self.cells):
            self.assertIn(cell.get("cell_type"), ("markdown", "code"))
            self.assertTrue(_cell_source(cell).strip(), f"Cell {pos} is empty")

        # A title, then one description and one code cell per component, up to the cell that saves the result.
        self.assertTrue(_cell_source(self.cells[0]).startswith("# Config Optimizer"))
        self.assertEqual(self.code_positions, list(range(2, len(self.cells), 2)))
        self.assertEqual([self.cells[pos - 1].get("cell_type") for pos in self.code_positions], ["markdown"] * 20)
        self.assertEqual(self.sections, CONFIG_OPTIMIZER_SECTIONS)
        for idx, cell in enumerate(self.code_cells[1:], start=1):
            src = _cell_source(cell)
            for ln in src.splitlines():
                self.assertFalse(
                    ln.lstrip().startswith(("%", "!")),
                    f"Only the first code cell may hold shell or magic lines: {ln!r}",
                )
            if idx != self.save_idx:
                self._assert_cell_ast_safe(src, f"config_optimizer code cell {idx}")

        # Section 0: the first code cell only installs packages.
        pip_lines = [
            ln.strip()
            for ln in _cell_source(self.code_cells[0]).splitlines()
            if ln.strip() and not ln.strip().startswith("#")
        ]
        self.assertGreater(len(pip_lines), 0, "The first code cell must hold the !pip install lines")
        for ln in pip_lines:
            self.assertTrue(ln.startswith("!pip install "), f"The first code cell must only install packages: {ln!r}")

        # Section 0.1: every import is in the second code cell, uses the canonical package path, and is used.
        cells_with_imports = [
            idx
            for idx, cell in enumerate(self.code_cells[1:], start=1)
            if any(isinstance(node, (ast.Import, ast.ImportFrom)) for node in ast.walk(ast.parse(_cell_source(cell))))
        ]
        self.assertEqual(cells_with_imports, [1], "Every import belongs in the Imports cell")
        imported_names: set[str] = set()
        for node in ast.walk(ast.parse(_cell_source(self.code_cells[1]))):
            if isinstance(node, ast.Import):
                imported_names.update((alias.asname or alias.name).split(".")[0] for alias in node.names)
            elif isinstance(node, ast.ImportFrom):
                imported_names.update(alias.asname or alias.name for alias in node.names)
                if (node.module or "").split(".")[0] == "backend":
                    self.assertEqual(
                        importlib.import_module(node.module).__name__,
                        node.module,
                        f"Import {node.module} through its real package path, not a legacy alias",
                    )
        used_names = {
            node.id
            for cell in self.code_cells[1:]
            for node in ast.walk(ast.parse(_cell_source(cell)))
            if isinstance(node, ast.Name)
        }
        self.assertEqual(sorted(imported_names - used_names), [], "The Imports cell imports names nothing uses")

        # Section 1: the meta parameters come right after the imports. The cell only assigns plain values.
        self.assertEqual(self.parameters_idx, 2)
        self.assertIn("## 1. Meta Parameters", _cell_source(self.cells[self.code_positions[2] - 1]))
        meta_parameters = ast.parse(_cell_source(self.code_cells[2])).body
        self.assertGreater(len(meta_parameters), 10)
        for statement in meta_parameters:
            self.assertIsInstance(statement, ast.AnnAssign, "The meta-parameter cell only assigns annotated constants")
            self.assertRegex(statement.target.id, r"^[A-Z][A-Z0-9_]+$")
            try:
                ast.literal_eval(statement.value)
            except ValueError:
                self.fail(f"{statement.target.id} must be a plain value: the cell holds parameters, not results")
        self.assertEqual(
            [statement.target.id for statement in meta_parameters],
            list(_config_optimizer_meta_parameters()),
            "The tests replace every meta parameter; keep _config_optimizer_meta_parameters in step with the cell",
        )

        # Section 2: the helpers, with print_section_data first, because every later cell prints with it.
        self.assertIn("## 2. Utils", _cell_source(self.cells[self.code_positions[3] - 1]))
        first_helpers = {
            node.name for node in ast.parse(_cell_source(self.code_cells[3])).body if isinstance(node, ast.FunctionDef)
        }
        self.assertIn("print_section_data", first_helpers)

        # A cell that generates data ends by printing it, with one print_section_data call and nothing else.
        # Every other cell only defines things: no statement of it prints or calls anything.
        for idx, section in enumerate(self.sections[2:], start=2):
            body = ast.parse(_cell_source(self.code_cells[idx])).body
            top_level_calls = [node for node in body if isinstance(node, ast.Expr) and isinstance(node.value, ast.Call)]
            if section in CONFIG_OPTIMIZER_PRINTING_SECTIONS:
                printers = [
                    node.func.id
                    for node in ast.walk(ast.Module(body=body, type_ignores=[]))
                    if isinstance(node, ast.Call)
                    and isinstance(node.func, ast.Name)
                    and node.func.id in ("print", "print_section_data")
                ]
                self.assertEqual(printers, ["print_section_data"], f"Section {section} must print once")
                self.assertIs(body[-1], top_level_calls[-1], f"Section {section} must end with its printout")
                self.assertEqual(body[-1].value.func.id, "print_section_data")
                title = body[-1].value.args[0]
                title_start = title.value if isinstance(title, ast.Constant) else title.values[0].value
                self.assertTrue(
                    title_start.startswith(f"Section {section} data: "),
                    f"The printout of Section {section} is titled {title_start!r}",
                )
            else:
                self.assertEqual(top_level_calls, [], f"Section {section} must only define values and helpers")

        # Section 5, the last cell, is the only one that writes: one write_text, into the notebook's METADATA_DIR.
        self.assertEqual(self.save_idx, len(self.code_cells) - 1)
        self.assertEqual(self.code_positions[-1], len(self.cells) - 1)
        save_tree = ast.parse(_cell_source(self.code_cells[self.save_idx]))
        calls = [node for node in ast.walk(save_tree) if isinstance(node, ast.Call)]
        self.assertNotIn("open", [node.func.id for node in calls if isinstance(node.func, ast.Name)])
        writes = [
            ast.unparse(node.func)
            for node in calls
            if isinstance(node.func, ast.Attribute) and node.func.attr in FORBIDDEN_WRITE_ATTRS | {"open"}
        ]
        self.assertEqual(writes, ["CONFIG_RESULT_PATH.write_text"])
        result_path = next(
            node
            for node in save_tree.body
            if isinstance(node, ast.AnnAssign) and node.target.id == "CONFIG_RESULT_PATH"
        )
        self.assertEqual(ast.unparse(result_path.value), "METADATA_DIR / CONFIG_RESULT_FILE_NAME")
        file_name = next(
            node
            for node in ast.parse(_cell_source(self.code_cells[self.sections.index("2.2")])).body
            if isinstance(node, ast.AnnAssign) and node.target.id == "CONFIG_RESULT_FILE_NAME"
        )
        self.assertEqual(ast.literal_eval(file_name.value), CONFIG_OPTIMIZER_RESULT_FILE)

    def test_notebook_is_saved_without_outputs(self) -> None:
        # The outputs of a run print game data: planets, coordinates, the research of other players. The notebook
        # is therefore committed without them. This test fails after every run in the editor, until the outputs
        # are cleared ("Clear All Outputs") and the notebook is saved.
        ran = [
            idx
            for idx, cell in enumerate(self.code_cells)
            if cell.get("outputs") != [] or cell.get("execution_count") is not None
        ]
        self.assertFalse(
            ran,
            f"{len(ran)} code cells of notebooks/config_optimizer.ipynb hold the outputs or execution counts of a "
            "run; clear all outputs and save the notebook before committing it",
        )

    def test_pip_cell_installs_exactly_what_the_notebook_imports(self) -> None:
        deps = tomllib.loads((REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]["dependencies"]
        requirements = {_requirement_name(line): line.strip() for line in deps}
        installed = {
            _requirement_name(spec): spec for spec in re.findall(r'"([^"]+)"', _cell_source(self.code_cells[0]))
        }
        self.assertGreater(len(installed), 0)
        for name, spec in installed.items():
            self.assertEqual(requirements.get(name), spec, f"The pip cell must use the pyproject.toml range of {name}")

        # A fresh interpreter in which every requirement the pip cell leaves out cannot be imported. The run
        # saves its result into a copy of the metadata folder.
        left_out = sorted(REQUIREMENT_IMPORT_NAMES.get(name, name) for name in set(requirements) - set(installed))
        metadata_dir = self._metadata_copy()
        result = subprocess.run(
            [
                sys.executable,
                "-c",
                FULL_PLANNER_SUBPROCESS_DRIVER,
                str(CONFIG_OPTIMIZER_PATH),
                str(self.cold_workspace),
                json.dumps(left_out),
                str(metadata_dir),
            ],
            capture_output=True,
            text=True,
            timeout=300,
            env={**os.environ, "PYTHONPATH": str(REPO_ROOT), "PYTHONDONTWRITEBYTECODE": "1"},
        )
        self.assertEqual(
            result.returncode,
            0,
            f"The notebook needs a package that its pip cell does not install ({left_out} were blocked):\n"
            f"{result.stderr[-2000:]}",
        )
        loaded = set(json.loads(result.stdout.strip().splitlines()[-1]))
        for name in installed:
            self.assertIn(
                REQUIREMENT_IMPORT_NAMES.get(name, name),
                loaded,
                f"The pip cell installs {name}, which the notebook never imports",
            )
        # That run, with the notebook's own meta parameters, saved a result too, and only there.
        self.assertTrue((metadata_dir / CONFIG_OPTIMIZER_RESULT_FILE).is_file())
        self.assertEqual(self._folder_fingerprint(FULL_PLANNER_METADATA_DIR), self.repository_metadata_before)

    # ------------------------------------------------------------------ code shared with full_planner.ipynb

    def test_shared_code_is_identical_to_full_planner(self) -> None:
        full_planner_cells = [
            cell
            for cell in json.loads(FULL_PLANNER_PATH.read_text(encoding="utf-8"))["cells"]
            if cell.get("cell_type") == "code"
        ]
        originals = _top_level_definitions(full_planner_cells)
        copies = _top_level_definitions(self.code_cells)

        # Every function, class and assignment that both notebooks have under one name is the same statement,
        # character for character, unless the name is listed with the reason for its own version.
        shared = sorted(set(copies) & set(originals))
        for name in shared:
            if name in CONFIG_OPTIMIZER_OWN_STATEMENTS:
                continue
            self.assertEqual(
                copies[name][1],
                originals[name][1],
                f"{name} differs between config_optimizer.ipynb and full_planner.ipynb: change it in both notebooks",
            )
        for name in CONFIG_OPTIMIZER_OWN_STATEMENTS:
            self.assertIn(name, shared, f"{name} is listed as an own version but is no longer shared")
            self.assertNotEqual(
                copies[name][1], originals[name][1], f"{name} is listed as an own version but is a copy"
            )
        self.assertGreater(len(shared), 60)

        # Every helper of Section 2 is such a copy, except the ones listed as the notebook's own.
        helper_cells = [idx for idx, section in enumerate(self.sections) if section.startswith("2.")]
        helpers = {
            node.name
            for idx in helper_cells
            for node in ast.parse(_cell_source(self.code_cells[idx])).body
            if isinstance(node, (ast.FunctionDef, ast.ClassDef))
        }
        self.assertEqual(sorted(helpers - set(originals)), sorted(CONFIG_OPTIMIZER_OWN_HELPERS))
        for name in (
            "print_section_data",
            "open_readonly_db_copy",
            "load_live_planet_states",
            "build_padded_empire_planets",
            "planet_param_context",
            "building_level_economy",
            "TechnologyRequirementGraph",
            "build_universe_research_table",
            "build_monotonic_base_times",
            "technology_multiplier",
        ):
            self.assertIn(name, helpers)
        # The statements that load the inputs are copies too.
        for name in (
            "live_planet_states",
            "empire_planet_states",
            "PLANET_PARAM_CONTEXTS",
            "TECH_GRAPH",
            "universe_research_sources",
            "universe_research_df",
            "UNIVERSE_RESEARCH_MAX_LEVELS",
            "TECHNOLOGY_METADATA",
            "RESOURCE_METADATA",
            "PLANNER_SETTINGS",
            "METADATA_DIR",
            "LIVE_DB_PATH",
        ):
            self.assertIn(name, shared)
        # What moved here is no longer in full_planner: the optimizer has one home. The path section of
        # full_planner needs four of its statements; they are copies like the helpers (checked above).
        moved_cells = (self.parameters_idx, *self.optimizer_idxs, self.save_idx)
        optimizer_names = {name for name, (idx, _) in copies.items() if idx in moved_cells}
        self.assertGreater(len(optimizer_names), 80)
        self.assertEqual(sorted(optimizer_names & set(originals)), sorted(FULL_PLANNER_SHARED_WITH_OPTIMIZER))

    def test_inputs_are_the_ones_that_full_planner_reads(self) -> None:
        full_planner_cells = [
            cell
            for cell in json.loads(FULL_PLANNER_PATH.read_text(encoding="utf-8"))["cells"]
            if cell.get("cell_type") == "code"
        ]
        # full_planner up to its Section 9, the last one whose data this notebook also loads.
        last_input_cell = next(
            idx
            for idx, cell in enumerate(full_planner_cells)
            if "UNIVERSE_RESEARCH_MAX_LEVELS: dict" in _cell_source(cell)
        )
        for label, workspace in (
            ("attacks", self.attack_workspace),
            ("universe research", self.universe_workspace),
            ("three planets", self.three_planet_workspace),
            ("empty database", self.cold_workspace),
        ):
            with self.subTest(workspace=label):
                theirs, _, _ = _run_full_planner(full_planner_cells, workspace, last_cell=last_input_cell)
                ours, _, _ = self._cached_run(workspace)
                self.assertEqual(ours["colony_researches"], theirs["colony_researches"])
                self.assertEqual(
                    ours["ACCOUNT_SNAPSHOT"],
                    {
                        "colony_researches": theirs["colony_researches"],
                        "universe_speedup_factor": theirs["telemetry_bundle"]["universe_speedup_factor"],
                    },
                )
                # The account context holds a part of full_planner's, with the same values.
                self.assertEqual(
                    sorted(ours["GAME_PARAM_CONTEXT"]), ["r1_mining_tech", "r2_energy_tech", "speedup_factor"]
                )
                for key, value in ours["GAME_PARAM_CONTEXT"].items():
                    self.assertEqual(value, theirs["GAME_PARAM_CONTEXT"][key], key)
                self.assertEqual(ours["live_planet_states"], theirs["live_planet_states"])
                self.assertEqual(ours["empire_planet_states"], theirs["empire_planet_states"])
                self.assertEqual(len(ours["PLANET_PARAM_CONTEXTS"]), len(theirs["PLANET_PARAM_CONTEXTS"]))
                for own_context, their_context in zip(ours["PLANET_PARAM_CONTEXTS"], theirs["PLANET_PARAM_CONTEXTS"]):
                    self.assertEqual(own_context, {key: their_context[key] for key in own_context})
                    self.assertIn("planet_temperature_c", own_context)
                self.assertEqual(ours["universe_research_sources"], theirs["universe_research_sources"])
                pd.testing.assert_frame_equal(ours["universe_research_df"], theirs["universe_research_df"])
                self.assertEqual(ours["UNIVERSE_RESEARCH_MAX_LEVELS"], theirs["UNIVERSE_RESEARCH_MAX_LEVELS"])
                self.assertEqual(
                    {key: getattr(ours["TECH_GRAPH"], key) for key in ("nodes", "prereqs_of", "topological_order")},
                    {key: getattr(theirs["TECH_GRAPH"], key) for key in ("nodes", "prereqs_of", "topological_order")},
                )
        # The fixtures are not all alike: the comparison saw research levels, several planets and an empty account.
        universe, _, _ = self._cached_run(self.universe_workspace)
        self.assertGreater(universe["UNIVERSE_RESEARCH_MAX_LEVELS"]["r1"], 20)
        three, _, _ = self._cached_run(self.three_planet_workspace)
        self.assertEqual(len(three["live_planet_states"]), 3)
        cold, _, _ = self._cached_run(self.cold_workspace)
        self.assertEqual((cold["live_planet_states"], cold["colony_researches"]), ([], {}))

    def test_account_snapshot_loader(self) -> None:
        ns, _, _ = self._attack_run()
        load = ns["load_account_snapshot"]
        cold_start_speed = float(ns["PLANNER_SETTINGS"]["cold_start_colony"]["speedup_factor"])
        attack_db = self.attack_workspace / "state" / "spaceinvasion.db"
        # The research levels of the newest planet snapshot, and the speed of the bot's latest game state.
        snapshot = load(attack_db)
        self.assertEqual(snapshot, {"colony_researches": {"r2": 1}, "universe_speedup_factor": 5.0})
        self.assertEqual(list(snapshot), ["colony_researches", "universe_speedup_factor"])
        self.assertEqual(ns["ACCOUNT_SNAPSHOT"], snapshot)
        self.assertEqual(ns["colony_researches"], {"r2": 1})
        self.assertEqual(ns["GAME_PARAM_CONTEXT"], {"speedup_factor": 5.0, "r1_mining_tech": 0, "r2_energy_tech": 1})

        # Without a database, or with an empty one: no research, and the speed of the cold-start colony.
        nothing = {"colony_researches": {}, "universe_speedup_factor": cold_start_speed}
        self.assertEqual(load(self.cold_workspace / "state" / "missing.db"), nothing)
        self.assertEqual(load(self.cold_workspace / "state" / "spaceinvasion.db"), nothing)

        # The speed of the fixture happens to be the cold-start speed, so the saved value is changed in a copy of
        # the database: a positive number is used as it is, anything else falls back to the cold-start speed.
        self.assertEqual(cold_start_speed, 5.0)
        with tempfile.TemporaryDirectory(prefix="si_co_snapshot_") as tmp:
            edited_db = Path(tmp) / "spaceinvasion.db"
            for suffix in ("", "-wal", "-shm"):
                if Path(f"{attack_db}{suffix}").is_file():
                    shutil.copy2(f"{attack_db}{suffix}", f"{edited_db}{suffix}")
            for saved_speed, expected in (
                (7.5, 7.5),
                (3, 3.0),
                (0, cold_start_speed),
                (2.5, 2.5),
                (-2.0, cold_start_speed),
                ("fast", cold_start_speed),
                (None, cold_start_speed),
                (float("inf"), cold_start_speed),
            ):
                with contextlib.closing(sqlite3.connect(edited_db)) as conn:
                    state = json.loads(
                        conn.execute("SELECT value FROM meta WHERE key = 'latest_game_state'").fetchone()[0]
                    )
                    state["speedup_factor"] = saved_speed
                    conn.execute("UPDATE meta SET value = ? WHERE key = 'latest_game_state'", (json.dumps(state),))
                    conn.commit()
                self.assertEqual(load(edited_db), {"colony_researches": {"r2": 1}, "universe_speedup_factor": expected})
            # The research levels are the ones of the newest planet snapshot; a level that is no number is dropped.
            with contextlib.closing(sqlite3.connect(edited_db)) as conn:
                columns = [row[1] for row in conn.execute("PRAGMA table_info(planet_snapshots)") if row[1] != "id"]
                copied = ", ".join("?" if column in ("ts", "researches_json") else column for column in columns)
                for ts, researches in (
                    (1_600_000_000.0, '{"r1": 3}'),
                    (1_800_000_000.0, '{"r1": 9, "r2": 4, "r5": "x"}'),
                ):
                    conn.execute(
                        f"INSERT INTO planet_snapshots ({', '.join(columns)}) SELECT {copied} FROM planet_snapshots "
                        "WHERE id = (SELECT MIN(id) FROM planet_snapshots)",
                        (ts, researches),
                    )
                conn.commit()
            self.assertEqual(load(edited_db)["colony_researches"], {"r1": 9, "r2": 4})
        # The live file is only copied. No loader of this notebook opens it: every connection goes to a temporary
        # copy, and the workspace stays as it was.
        opened = _databases_opened_by(
            lambda: [
                ns[loader](attack_db)
                for loader in ("load_account_snapshot", "load_live_planet_states", "load_universe_research_sources")
            ]
        )
        self.assertGreaterEqual(len(opened), 3)
        self.assertNotIn(attack_db.resolve(), opened, "The live database must only be copied, never opened")
        self.assertEqual({path.name for path in opened}, {"spaceinvasion.db"})
        before, _ = self._workspace_before_after[self.attack_workspace]
        self.assertEqual(self._folder_fingerprint(self.attack_workspace), before)

    # ------------------------------------------------------------------ the saved result

    def test_run_writes_only_the_result_file(self) -> None:
        ns, _, _ = self._attack_run()
        before, after = self._workspace_before_after[self.attack_workspace]
        self.assertEqual(after, before, "Running the notebook must not create or change a file in its workspace")
        self.assertFalse(LEGACY_ATTACK_JSON_PATH.exists())
        self.assertEqual(ns["LIVE_DB_PATH"], self.attack_workspace.resolve() / "state" / "spaceinvasion.db")

        # The metadata folder of the run gained the result file, and nothing else in it changed.
        metadata_dir = ns["METADATA_DIR"]
        self.assertNotEqual(metadata_dir.resolve(), FULL_PLANNER_METADATA_DIR.resolve())
        self.assertEqual(ns["CONFIG_RESULT_PATH"], metadata_dir / CONFIG_OPTIMIZER_RESULT_FILE)
        originals = {
            name: digest
            for name, digest in self.repository_metadata_before.items()
            if name != CONFIG_OPTIMIZER_RESULT_FILE
        }
        after_run = self._folder_fingerprint(metadata_dir)
        self.assertEqual(sorted(set(after_run) - set(originals)), [CONFIG_OPTIMIZER_RESULT_FILE])
        self.assertEqual({name: after_run[name] for name in originals}, originals)
        self.assertGreaterEqual(set(originals), set(FULL_PLANNER_METADATA_FILES))
        # The repository's own metadata folder was not touched.
        self.assertEqual(self._folder_fingerprint(FULL_PLANNER_METADATA_DIR), self.repository_metadata_before)
        # A run that is pointed at the repository's folder is refused by the test runner itself.
        with self.assertRaisesRegex(ValueError, "needs its own copy"):
            _run_config_optimizer(self.code_cells, self.cold_workspace, FULL_PLANNER_METADATA_DIR)

        scratch_globals = sorted(
            name
            for name, value in ns.items()
            if name.startswith("_")
            and not name.startswith("__")
            and not callable(value)
            and not isinstance(value, types.ModuleType)
        )
        self.assertEqual(
            [name for name in scratch_globals if name not in ("_", "_cand")],
            [],
            "Top-level temporaries belong inside functions, not in the notebook namespace",
        )

        # The only database access is a query-only connection to a temporary copy.
        with ns["open_readonly_db_copy"](ns["LIVE_DB_PATH"]) as conn:
            self.assertNotEqual(
                Path(conn.execute("PRAGMA database_list").fetchone()[2]).resolve(),
                ns["LIVE_DB_PATH"].resolve(),
            )
            with self.assertRaises(sqlite3.OperationalError):
                conn.execute("CREATE TABLE config_optimizer_probe (x)")
        self.assertEqual(self._folder_fingerprint(self.attack_workspace), before)

    def test_saved_result_document(self) -> None:
        ns, _, _ = self._attack_run()
        result = ns["CONFIG_RESULT"]
        text = ns["CONFIG_RESULT_PATH"].read_text(encoding="utf-8")
        # The file is CONFIG_RESULT as indented JSON, and reading it back gives the same document.
        self.assertEqual(text, json.dumps(result, indent=2, ensure_ascii=False, allow_nan=False) + "\n")
        self.assertEqual(json.loads(text), result)
        self.assertEqual(_json_document(result), result, "CONFIG_RESULT must already be plain JSON data")
        self.assertNotRegex(text, r"\b(NaN|Infinity)\b")
        self.assertEqual(
            list(result),
            [
                "description",
                "schema_version",
                "generated_at",
                "meta_parameters",
                "inputs",
                "planned_research_levels",
                "summary",
                "search",
                "limits",
                "planets",
            ],
        )
        self.assertIn("notebooks/config_optimizer.ipynb", result["description"])
        self.assertEqual(result["schema_version"], 1)
        generated_at = datetime.datetime.fromisoformat(result["generated_at"])
        self.assertIsNotNone(generated_at.tzinfo, "generated_at must say which time zone it is in")
        self.assertRegex(result["generated_at"], r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}[+-]\d{2}:\d{2}$")
        self.assertLess(abs((datetime.datetime.now(datetime.timezone.utc) - generated_at).total_seconds()), 900.0)

        # The meta parameters of the run: every name of the Section 1 cell, with the values the run used.
        pinned = _config_optimizer_meta_parameters()
        self.assertEqual(list(result["meta_parameters"]), list(pinned))
        self.assertEqual(result["meta_parameters"], _json_document(pinned))
        self.assertEqual(result["meta_parameters"]["MISC_BUILDING_LEVELS_BY_SLOT"], {"1": {"b8": 12}})
        self.assertEqual(result["meta_parameters"]["HILL_CLIMB_STEP_SIZES"], [1, 2, 4, 8])

        self.assertEqual(
            result["inputs"],
            {
                "universe_speedup_factor": 5.0,
                "owned_planets": 1,
                "roster_planets": 11,
                "own_research_levels": ns["colony_researches"],
                "universe_research_max_levels": ns["UNIVERSE_RESEARCH_MAX_LEVELS"],
            },
        )
        self.assertEqual(result["planned_research_levels"], ns["PLANNED_RESEARCH_LEVELS"])
        self.assertEqual(result["summary"], _json_document(ns["BEST_CONFIG_SUMMARY"]))
        self.assertEqual(result["summary"]["ships_per_month"], ns["BEST_CONFIG_SUMMARY"]["ships_per_month"])
        self.assertGreater(result["summary"]["ships_per_month"], 0.0)
        self.assertEqual(result["search"], _json_document(ns["GA_SUMMARY"]))
        self.assertEqual(result["limits"], _json_document(ns["best_config_limits_df"].to_dict(orient="records")))
        self.assertEqual([row["limit"] for row in result["limits"]], ["metal", "kryptonite", "spice", "shipyards"])

        # One record per planet of the roster, in its order.
        plans, buildings = ns["PLANET_PLANS"], ns["best_config_buildings_df"]
        production, warehouses = ns["best_config_planets_df"], ns["best_config_warehouses_df"]
        self.assertEqual(len(result["planets"]), 11)
        identity = ["slot_index", "planet_id", "coords", "planet_status"]
        for index, (record, table) in enumerate(zip(result["planets"], ns["PLANET_CONFIGURATIONS"].values())):
            plan = plans[index]
            self.assertEqual(
                list(record),
                [
                    *identity,
                    "is_central_planet",
                    "pays_for_research",
                    "temperature_c",
                    "fields_max",
                    "fields_used",
                    "fields_free",
                    "production",
                    "warehouses",
                    "buildings",
                ],
            )
            self.assertEqual([record[key] for key in identity], [plan[key] for key in identity])
            self.assertEqual(
                (record["is_central_planet"], record["pays_for_research"], record["temperature_c"]),
                (plan["is_central_planet"], plan["pays_for_research"], plan["temperature_c"]),
            )
            self.assertIs(type(record["is_central_planet"]), bool)
            self.assertEqual(
                [record[key] for key in ("fields_max", "fields_used", "fields_free")],
                [int(buildings.loc[index, key]) for key in ("fields_max", "fields_used", "fields_free")],
            )
            self.assertEqual(
                record["production"], _json_document(production.drop(columns=identity).iloc[index].to_dict())
            )
            own_warehouses = warehouses[warehouses["slot_index"] == plan["slot_index"]]
            self.assertEqual(
                record["warehouses"],
                _json_document(own_warehouses.drop(columns=["slot_index", "coords"]).to_dict(orient="records")),
            )
            self.assertEqual([row["resource"] for row in record["warehouses"]], list(ns["RESOURCE_KEYS"]))
            self.assertEqual(record["buildings"], _json_document(table.to_dict(orient="records")))
            self.assertEqual(
                {row["key"]: row["target_level"] for row in record["buildings"]},
                {key: int(buildings.loc[index, key]) for key in table["key"]},
            )
        home = result["planets"][0]
        self.assertEqual(
            (home["coords"], home["planet_status"], home["is_central_planet"]), ("02:380:12", "colonized", True)
        )
        self.assertEqual(result["planets"][10]["coords"], "EMPTY:10")
        fusion = next(row for row in home["buildings"] if row["key"] == "b5")
        self.assertEqual((fusion["building"], fusion["role"], fusion["built_level"]), ("Fusion plant", "optimized", 7))
        self.assertEqual(
            sorted(fusion),
            sorted(
                [
                    "key",
                    "building",
                    "role",
                    "built_level",
                    "target_level",
                    "levels_to_build",
                    "upgrade_cost_spice_eq",
                    "last_level_build_time_s",
                    "last_level_build_time_text",
                ]
            ),
        )
        # The printout of the cell names the file and what it holds, without repeating the tables.
        _, stdout, _ = self._attack_run()
        self.assertIn(f"Section 5 data: result saved as {CONFIG_OPTIMIZER_RESULT_FILE}", stdout[self.save_idx])
        self.assertIn(str(ns["CONFIG_RESULT_PATH"]), stdout[self.save_idx])
        self.assertIn('"planets": "list, len=11"', stdout[self.save_idx])
        self.assertLess(len(stdout[self.save_idx]), 3000)

        # json_ready: what JSON cannot write as it is.
        json_ready = ns["json_ready"]
        self.assertEqual(
            json_ready(
                {
                    7: (1, 2.5, "x"),
                    "array": np.array([[1, 2], [3, 4]]),
                    "numbers": [np.int64(5), np.float64(0.5), np.bool_(True)],
                    "not finite": [float("nan"), float("inf"), -float("inf"), np.float64("nan")],
                    "plain": [None, True, False, 0, -1.25, "text"],
                }
            ),
            {
                "7": [1, 2.5, "x"],
                "array": [[1, 2], [3, 4]],
                "numbers": [5, 0.5, True],
                "not finite": [None, None, None, None],
                "plain": [None, True, False, 0, -1.25, "text"],
            },
        )
        converted = json_ready({"numbers": [np.int64(5), np.float64(0.5), np.bool_(True)]})["numbers"]
        self.assertEqual([type(value) for value in converted], [int, float, bool])
        for unsupported in ({1, 2}, Path("x"), object()):
            with self.assertRaisesRegex(TypeError, "Cannot save a value of type"):
                json_ready({"value": unsupported})
        self.assertEqual(
            ns["table_records"](pd.DataFrame({"n": [1, 2], "x": [0.5, float("nan")], "t": ["a", "b"]})),
            [{"n": 1, "x": 0.5, "t": "a"}, {"n": 2, "x": None, "t": "b"}],
        )

        # In this run the central planet also holds the Research lab, so the two flags agree on every planet.
        self.assertEqual([planet["is_central_planet"] for planet in result["planets"]], [True] + [False] * 10)
        self.assertEqual([planet["pays_for_research"] for planet in result["planets"]], [True] + [False] * 10)

        # Another run replaces the file. Here: Section 4 again with a month of 15 days and the Research lab on
        # slot 2, saved into the same folder.
        other = self._rerun_planner(
            ns, {"PRODUCTION_MONTH_DAYS": 15.0, "MISC_BUILDING_LEVELS_BY_SLOT": {2: {"b8": 12}}}
        )
        with contextlib.redirect_stdout(io.StringIO()):
            exec(compile(_cell_source(self.code_cells[self.save_idx]), "config_optimizer_save", "exec"), other, other)
        try:
            replaced = json.loads(ns["CONFIG_RESULT_PATH"].read_text(encoding="utf-8"))
            self.assertEqual(replaced, other["CONFIG_RESULT"])
            self.assertEqual(replaced["meta_parameters"]["PRODUCTION_MONTH_DAYS"], 15.0)
            self.assertEqual(replaced["meta_parameters"]["MISC_BUILDING_LEVELS_BY_SLOT"], {"2": {"b8": 12}})
            self.assertEqual(replaced["summary"]["hours_per_month"], 360.0)
            self.assertNotEqual(replaced["summary"]["ships_per_month"], result["summary"]["ships_per_month"])
            # The central planet is still slot 1, but slot 2 pays for the research now.
            self.assertEqual([planet["is_central_planet"] for planet in replaced["planets"]], [True] + [False] * 10)
            self.assertEqual(
                [planet["pays_for_research"] for planet in replaced["planets"]], [False, True] + [False] * 9
            )
            self.assertEqual(
                sorted(path.name for path in ns["METADATA_DIR"].iterdir()),
                sorted({*self.repository_metadata_before, CONFIG_OPTIMIZER_RESULT_FILE}),
            )
        finally:  # leave the file of the cached run as that run wrote it
            ns["CONFIG_RESULT_PATH"].write_text(text, encoding="utf-8")

    def test_cold_start_on_an_empty_database(self) -> None:
        with warnings.catch_warnings():
            warnings.simplefilter("error", RuntimeWarning)
            metadata_dir = self._metadata_copy()
            cold_ns, cold_stdout, _ = _run_config_optimizer(self.code_cells, self.cold_workspace, metadata_dir)
        # An empty account: no planet, no research, the speed of the cold-start colony, eleven generic planets.
        self.assertEqual(len(cold_ns["live_planet_states"]), 0)
        self.assertEqual(len(cold_ns["empire_planet_states"]), 11)
        self.assertEqual(cold_ns["colony_researches"], {})
        self.assertEqual(
            cold_ns["GAME_PARAM_CONTEXT"],
            {
                "speedup_factor": float(cold_ns["PLANNER_SETTINGS"]["cold_start_colony"]["speedup_factor"]),
                "r1_mining_tech": 0,
                "r2_energy_tech": 0,
            },
        )
        self.assertEqual(
            {context["planet_status"] for context in cold_ns["PLANET_PARAM_CONTEXTS"]}, {"empty_colony_slot"}
        )
        for idx, out in cold_stdout.items():
            if self.sections[idx] in CONFIG_OPTIMIZER_PRINTING_SECTIONS:
                self.assertRegex(out, r"(?m)^Printed: \d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2} [+-]\d{4} \| ")
                self.assertNotIn("...", out)
        self.assertIn("[live_planet_states] list, len=0\n[]", cold_stdout[self.sections.index("3.1")])

        # The planner works on an empty account too: nothing is known about anybody's research, slot 1 gets the
        # Research lab, the other ten generic planets are twins, and the search still finds starbases to build.
        self.assertEqual(int(cold_ns["universe_research_df"]["universe_max_level"].sum()), 0)
        self.assertEqual(set(cold_ns["universe_research_df"]["max_source"]), {"none"})
        self.assertEqual(cold_ns["plan_planets_df"]["free_fields"].tolist(), [271] + [283] * 10)
        self.assertEqual(cold_ns["PRODUCTION_MODEL"].twin_planets, [0] + [1] * 10)
        self.assertEqual(set(cold_ns["plan_building_levels_df"]["built_level"]), {0})
        self.assertGreater(cold_ns["BEST_CONFIG_SUMMARY"]["ships_per_month"], 0.0)
        self.assertEqual(
            cold_ns["BEST_CONFIG_SUMMARY"]["levels_to_build"],
            int(cold_ns["best_config_buildings_df"]["fields_used"].sum()),
        )
        # Its result is saved like any other.
        saved = json.loads((metadata_dir / CONFIG_OPTIMIZER_RESULT_FILE).read_text(encoding="utf-8"))
        self.assertEqual(saved, cold_ns["CONFIG_RESULT"])
        self.assertEqual((saved["inputs"]["owned_planets"], saved["inputs"]["own_research_levels"]), (0, {}))
        self.assertEqual({planet["planet_status"] for planet in saved["planets"]}, {"empty_colony_slot"})
        self.assertEqual({row["built_level"] for planet in saved["planets"] for row in planet["buildings"]}, {0})

        # A missing database file is a cold start too.
        missing_db = self.cold_workspace / "state" / "missing.db"
        self.assertFalse(cold_ns["has_database"](missing_db))
        self.assertEqual(cold_ns["load_live_planet_states"](missing_db), [])
        self.assertEqual(cold_ns["load_universe_research_sources"](missing_db)["ledger_rows"], [])

    # ------------------------------------------------------------------ Section 4: best configuration

    def _toy_model(
        self,
        ns: dict,
        target: str,
        max_levels: dict[str, int],
        planets: int,
        fields: list[int],
        warehouse_hours: float = 0.0,
    ):
        """A small empire of the notebook's roster: its first planets, resized, with tiny level ranges.

        Its buildings cost so little that no warehouse is needed, unless `warehouse_hours` asks for one.
        """
        states = [
            dataclasses.replace(planet, fields_max=size)
            for planet, size in zip(ns["empire_planet_states"][:planets], fields)
        ]
        misc = {f"b{number}": 0 for number in game_data.BUILDING_BY_ID if f"b{number}" not in max_levels}
        misc["b7"] = 3  # the Weapons factory needs Development center 3
        ns["check_optimizer_settings"](max_levels, misc, {}, {}, target, warehouse_hours, 0)
        plans = ns["build_planet_plans"](
            states,
            ns["PLANET_PARAM_CONTEXTS"][:planets],
            optimized_max_levels=max_levels,
            misc_levels=misc,
            misc_levels_by_slot={},
            min_fleet={},
            central_planet_slot=1,
            central_min_warehouse_level=0,
            research_costs=[],
        )
        contexts = [
            ns["planet_param_context"](
                ns["PLAN_ACCOUNT_CONTEXT"], plan["slot_index"], states[index], is_empty_slot=index > 0
            )
            for index, plan in enumerate(plans)
        ]
        return ns["EmpireProductionModel"](
            plans,
            contexts,
            optimized_keys=tuple(max_levels),
            target_ship_key=target,
            month_days=30.0,
            warehouse_hours=warehouse_hours,
        )

    def test_planned_research_and_fixed_planet_parts(self) -> None:
        ns, _, _ = self._attack_run()

        # Research: a share of the universe maximum (half a level rounds up), never below the current or the
        # required level, never beyond the catalog.
        research = ns["plan_research_levels"](
            {"r1": 27, "r2": 5, "r3": 13, "r4": 100}, {"r2": 4, "r3": 1}, {"r3": 8, "r6": 10}, 50.0
        ).set_index("key")
        columns = ["own_level", "universe_max_level", "percent_level", "required_level", "planned_level", "set_by"]
        self.assertEqual(research.loc["r1", columns].tolist(), [0, 27, 14, 0, 14, "universe maximum"])
        self.assertEqual(research.loc["r2", columns].tolist(), [4, 5, 3, 0, 4, "current level"])
        self.assertEqual(research.loc["r3", columns].tolist(), [1, 13, 7, 8, 8, "prerequisite"])
        self.assertEqual(research.loc["r4", columns].tolist(), [0, 100, 40, 0, 40, "universe maximum"])
        self.assertEqual(research.loc["r6", columns].tolist(), [0, 0, 0, 10, 10, "prerequisite"])
        self.assertEqual(research.loc["r5", columns].tolist(), [0, 0, 0, 0, 0, "not needed"])
        full = ns["plan_research_levels"]({"r1": 27, "r2": 5}, {"r2": 5}, {}, 100.0).set_index("key")
        self.assertEqual(full.loc["r1", "planned_level"], 27)
        # A level that you already have is called your current level, even when it is the universe maximum too.
        self.assertEqual(full.loc["r2", columns].tolist(), [5, 5, 5, 0, 5, "current level"])

        # What a planet itself must have built, and what the account must have researched.
        building_needs = ns["same_planet_building_requirements"]
        self.assertEqual(building_needs("s16"), {"b9": 13, "b7": 3})
        self.assertEqual(building_needs("s1"), {"b9": 2, "b7": 3})
        self.assertEqual(building_needs("b17", 2), {"b17": 2, "b7": 12})
        self.assertEqual(building_needs("b17"), {"b7": 12})
        self.assertEqual(building_needs("b18", 1), {"b18": 1, "b17": 2, "b7": 12})
        # A building needed through two paths keeps the higher level, whichever path is walked last, and a
        # building that only a research needs does not have to stand on the same planet.
        real_graph = ns["TECH_GRAPH"]
        ns["TECH_GRAPH"] = types.SimpleNamespace(
            nodes={
                "x": {"category": "buildings"},
                "a": {"category": "buildings"},
                "b": {"category": "buildings"},
                "c": {"category": "buildings"},
                "r": {"category": "research"},
            },
            prereqs_of={"x": (("b", 1), ("a", 5), ("r", 3)), "b": (("a", 2),), "a": (), "r": (("c", 9),), "c": ()},
        )
        try:
            self.assertEqual(building_needs("x"), {"b": 1, "a": 5})
            self.assertEqual(building_needs("x", 4), {"x": 4, "b": 1, "a": 5})
        finally:
            ns["TECH_GRAPH"] = real_graph
        research_needs = ns["research_requirements"]
        self.assertEqual(research_needs(["s16"]), {"r2": 2, "r3": 8, "r13": 10, "r6": 10, "r15": 2})
        self.assertEqual(research_needs(["s1", "b17"]), {"r2": 10, "r3": 2, "r4": 4})
        self.assertEqual(research_needs([]), {})
        self.assertEqual(
            ns["context_with_research"](
                {"r1_mining_tech": 0, "speedup_factor": 5.0, "r2_energy_tech": 1}, {"r1": 9, "r2": 3}
            ),
            {"r1_mining_tech": 9, "speedup_factor": 5.0, "r2_energy_tech": 3},
        )

        # One planet: Development center 2 asked, 1 built, 3 needed by the Weapons factory that both the
        # Large transports (level 6) and the Tunnel system (level 5) require.
        home = dataclasses.replace(ns["live_planet_states"][0], ships={"s2": 1, "s9": 10})
        misc = {f"b{number}": 0 for number in game_data.BUILDING_BY_ID if number not in (1, 2, 3, 4, 5, 9, 17)}
        misc.update(b7=2, b10=1, b20=1)
        max_levels = {"b1": 30, "b2": 30, "b3": 30, "b4": 30, "b5": 30, "b9": 20, "b17": 20}
        plan = ns["build_planet_plan"](
            ns["PLANET_PARAM_CONTEXTS"][0],
            home,
            optimized_max_levels=max_levels,
            misc_levels=misc,
            min_fleet={"s2": 3, "s9": 4, "s10": 0},
        )
        self.assertEqual((plan["slot_index"], plan["coords"], plan["planet_status"]), (1, "02:380:12", "colonized"))
        self.assertEqual(
            {key: level for key, level in plan["fixed_levels"].items() if level}, {"b7": 3, "b10": 1, "b20": 1}
        )
        self.assertEqual((plan["fixed_fields"], plan["free_fields"], plan["fields_max"]), (5, 225, 230))
        self.assertEqual(plan["min_levels"], {"b1": 0, "b2": 0, "b3": 0, "b4": 0, "b5": 0, "b9": 6, "b17": 0})
        # The Microsystem accelerator needs Development center 12, which this planet does not get.
        self.assertEqual(plan["max_levels"], {"b1": 30, "b2": 30, "b3": 30, "b4": 30, "b5": 30, "b9": 20, "b17": 0})
        self.assertEqual(plan["locked_by"], {"b17": "b7 L12"})
        self.assertEqual(plan["built_levels"]["b5"], 7)
        # Fleet: 1 of 3 Large transports is there; the 10 Solar cells stationed count, not the 4 asked for.
        self.assertEqual(plan["fleet_missing"], {"s2": 2, "s9": 0, "s10": 0})
        self.assertEqual(plan["fleet_cost_spice_eq"], 2 * (8000 / 2.5 + 6000 / 3.0))
        self.assertEqual(plan["solar_cells"], 10)
        # A ship with a count of 0 asks for nothing: no Weapons factory without the Tunnel system either.
        no_fleet = ns["build_planet_plan"](
            ns["PLANET_PARAM_CONTEXTS"][0],
            home,
            optimized_max_levels=max_levels,
            misc_levels={**misc, "b20": 0},
            min_fleet={"s13": 0},
        )
        self.assertEqual((no_fleet["min_levels"]["b9"], no_fleet["fleet_missing"]), (0, {"s13": 0}))
        # A level already built beyond the meta parameter is kept; a maximum beyond the catalog is cut.
        built_up = dataclasses.replace(home, buildings={**home.buildings, "b7": 14, "b10": 3})
        kept = ns["build_planet_plan"](
            ns["PLANET_PARAM_CONTEXTS"][0],
            built_up,
            optimized_max_levels={**max_levels, "b1": 500},
            misc_levels=misc,
            min_fleet={},
        )
        self.assertEqual((kept["fixed_levels"]["b7"], kept["fixed_levels"]["b10"]), (14, 3))
        self.assertEqual((kept["max_levels"]["b17"], kept["locked_by"]), (20, {}))
        self.assertEqual(kept["max_levels"]["b1"], game_data.BUILDING_BY_ID[1]["max_table_level"])
        self.assertEqual(kept["min_levels"]["b9"], 5)

        # Meta parameters that contradict the catalog or each other stop the run.
        check = ns["check_optimizer_settings"]
        check(max_levels, misc, {1: {"b8": 3}}, {"s2": 3}, "s16", 24.0, 15)
        without_b21 = {key: level for key, level in misc.items() if key != "b21"}
        for arguments, message in (
            ((max_levels, without_b21, {}, {}, "s16"), r"missing \['b21'\]"),
            ((max_levels, {**misc, "b9": 4}, {}, {}, "s16"), r"in both \['b9'\]"),
            ((max_levels, {**misc, "b99": 1}, {}, {}, "s16"), r"unknown \['b99'\]"),
            ((max_levels, misc, {2: {"b1": 4}}, {}, "s16"), r"MISC_BUILDING_LEVELS_BY_SLOT\[2\] sets \['b1'\]"),
            ((max_levels, misc, {}, {"s99": 1}, "s16"), r"need ship keys"),
            ((max_levels, misc, {}, {}, "b9"), r"need ship keys"),
            ((max_levels, {**misc, "b10": -1}, {}, {}, "s16"), r"whole numbers of 0 or more"),
            ((max_levels, misc, {}, {"s2": 1.5}, "s16"), r"whole numbers of 0 or more"),
            (
                ({**max_levels, "b6": 9}, {k: v for k, v in misc.items() if k != "b6"}, {}, {}, "s16"),
                r"b6 needs \['b4'\]",
            ),
        ):
            with self.subTest(error=message), self.assertRaisesRegex(ValueError, message):
                check(*arguments, 24.0, 15)
        # A planet too small for its fixed buildings, its minimum levels and their warehouses is an error, not a
        # silent overflow: 5 fixed fields, Weapons factory 6 for the Large transport, and the 8 warehouse levels
        # that hold the cost of the Tunnel system need 19.
        tunnel_cost = game_data.building_cost(20, 1)
        self.assertEqual(sum(self._warehouse_level_for(amount) for amount in tunnel_cost.values()), 8)
        for fields, fits in ((18, False), (19, True)):
            tiny = dataclasses.replace(home, fields_max=fields)
            tiny_plans = ns["build_planet_plans"](
                [tiny],
                ns["PLANET_PARAM_CONTEXTS"][:1],
                optimized_max_levels=max_levels,
                misc_levels=misc,
                misc_levels_by_slot={},
                min_fleet={"s2": 1},
                central_planet_slot=1,
                central_min_warehouse_level=0,
                research_costs=[],
            )
            tiny_model = {
                "optimized_keys": tuple(max_levels),
                "target_ship_key": "s16",
                "month_days": 30.0,
                "warehouse_hours": 0.0,
            }
            tiny_contexts = [ns["planet_param_context"](ns["PLAN_ACCOUNT_CONTEXT"], 1, tiny, is_empty_slot=False)]
            if fits:
                self.assertEqual(
                    ns["EmpireProductionModel"](tiny_plans, tiny_contexts, **tiny_model).free_fields.tolist(), [14]
                )
            else:
                with self.assertRaisesRegex(ValueError, r"Planet 02:380:12 \(slot 1\) has 18 fields, .* need 19"):
                    ns["EmpireProductionModel"](tiny_plans, tiny_contexts, **tiny_model)

        # The notebook checks the meta parameters before it plans, and the ones it ships with pass.
        with self.assertRaisesRegex(ValueError, "need ship keys"):
            self._rerun_planner(ns, {"TARGET_SHIP_KEY": "b9"})
        own = self._notebook_meta_parameters()
        check(
            own["OPTIMIZED_BUILDING_MAX_LEVELS"],
            own["MISC_BUILDING_LEVELS"],
            own["MISC_BUILDING_LEVELS_BY_SLOT"],
            own["MIN_FLEET_PER_PLANET"],
            own["TARGET_SHIP_KEY"],
            own["WAREHOUSE_HOURS_OF_PRODUCTION"],
            own["CENTRAL_PLANET_MIN_WAREHOUSE_LEVEL"],
        )

        # The notebook's own plan. The account has Energy technology 1 and nothing is known about other players,
        # so every other planned level comes from a prerequisite of the plan.
        self.assertEqual(
            ns["PLAN_REQUIRED_RESEARCH"],
            {"r2": 10, "r3": 8, "r13": 10, "r6": 10, "r15": 2, "r4": 6, "r7": 2, "r5": 4},
        )
        planned = ns["planned_research_df"].set_index("key")
        self.assertEqual(planned.loc["r2", columns].tolist(), [1, 1, 1, 10, 10, "prerequisite"])
        self.assertEqual(planned.loc["r1", columns].tolist(), [0, 0, 0, 0, 0, "not needed"])
        self.assertEqual(ns["PLANNED_RESEARCH_LEVELS"], dict(zip(planned.index, planned["planned_level"])))
        self.assertEqual(ns["PLAN_ACCOUNT_CONTEXT"]["r2_energy_tech"], 10)
        self.assertEqual(ns["GAME_PARAM_CONTEXT"]["r2_energy_tech"], 1)
        self.assertEqual(ns["PLAN_ACCOUNT_CONTEXT"]["speedup_factor"], 5.0)

        plans = ns["PLANET_PLANS"]
        self.assertEqual([plan["slot_index"] for plan in plans], list(range(1, 12)))
        planets = ns["plan_planets_df"]
        # Slot 1 gets the Research lab: 12 + 12 + 5 fixed fields of its 230; a generic planet 17 of 300.
        # The warehouses are not among them: their fields come out of the free ones.
        self.assertEqual(planets[["fixed_fields", "free_fields"]].values.tolist(), [[29, 201]] + [[17, 283]] * 10)
        self.assertEqual(set(planets["min_optimized_fields"]), {6})
        self.assertEqual(set(planets["solar_cells"]), {0})
        # 10 Large transports, 10 Spy probes and 10 Recyclers are missing everywhere.
        self.assertEqual(set(planets["fleet_units_missing"]), {30})
        self.assertEqual(set(planets["fleet_cost_spice_eq"]), {10 * 5200.0 + 10 * 1200.0 + 10 * 6000.0})
        levels_table = ns["plan_building_levels_df"]
        self.assertEqual(len(levels_table), 11 * 21)
        by_planet = levels_table.set_index(["coords", "key"])
        self.assertEqual(
            by_planet.loc[("02:380:12", "b8"), ["role", "built_level", "min_level", "max_level"]].tolist(),
            ["fixed", 0, 12, 12],
        )
        self.assertEqual(by_planet.loc[("EMPTY:01", "b8"), ["min_level", "max_level"]].tolist(), [0, 0])
        self.assertEqual(
            by_planet.loc[("02:380:12", "b9"), ["role", "built_level", "min_level", "max_level", "locked_by"]].tolist(),
            ["optimized", 0, 6, 50, ""],
        )
        self.assertEqual(by_planet.loc[("02:380:12", "b5"), "built_level"], 7)
        self.assertEqual(
            int((levels_table["role"] == "optimized").sum()), 11 * len(ns["OPTIMIZED_BUILDING_MAX_LEVELS"])
        )

        # Asteroids take no part: the three-planet account plans its two colonies and nine generic planets.
        three_ns, _, _ = self._cached_run(self.three_planet_workspace)
        self.assertEqual([plan["slot_index"] for plan in three_ns["PLANET_PLANS"]], [1, 2, *range(4, 13)])
        self.assertNotIn("02:380:12A", set(three_ns["best_config_buildings_df"]["coords"]))
        cold_colony = three_ns["PLANET_PLANS"][1]
        self.assertEqual((cold_colony["coords"], cold_colony["temperature_c"]), ("02:381:15", -40))
        # Its 3 Solar cells are counted, and its Microsystem accelerator level is the start of the range.
        self.assertEqual((cold_colony["solar_cells"], cold_colony["built_levels"]["b17"]), (3, 1))
        self.assertEqual(three_ns["PRODUCTION_MODEL"].twin_planets, [0, 1, *([2] * 9)])

    def test_production_model_matches_the_game_formulas(self) -> None:
        ns, _, _ = self._attack_run()
        model = ns["PRODUCTION_MODEL"]
        self.assertEqual(model.optimized_keys, ("b1", "b2", "b3", "b4", "b5", "b9", "b17"))
        self.assertEqual(model.limit_names, ("metal", "kryptonite", "spice", "shipyards"))
        self.assertEqual(model.ship_cost.tolist(), [3_000_000.0, 2_000_000.0, 500_000.0])
        self.assertEqual(model.target_requirements, {"b9": 13, "b7": 3})
        self.assertEqual(model.hours_per_month, 720.0)
        self.assertEqual(model.free_fields.tolist(), [201] + [283] * 10)
        self.assertEqual(model.twin_planets, [0] + [1] * 10)
        self.assertEqual(model.target_ship_key, "s16")

        speed, hours = 5.0, 720.0
        energy_boost = 1.1  # planned Energy technology 10

        def row(building_id: int, level: int) -> dict:
            return game_data.BUILDING_BY_ID[building_id]["levels"][level]

        def expected(levels: dict[str, int], temperature: int) -> dict[str, float]:
            """The model's steps, written out with the catalog numbers."""
            produced = row(5, levels["b5"])["energy"] * energy_boost
            used = -sum(row(number, levels[f"b{number}"])["energy"] for number in (1, 2, 3, 4))
            power = min(1.0, produced / used)
            pig_iron = speed * game_data.mine_production_per_hour(1, levels["b1"]) * power
            wanted = speed * row(2, levels["b2"])["pig_iron_used"] * power
            smelting = min(1.0, pig_iron / wanted)
            metal = speed * row(2, levels["b2"])["prod"] * power * smelting
            kryptonite = speed * game_data.mine_production_per_hour(3, levels["b3"]) * power
            spice = speed * game_data.mine_production_per_hour(4, levels["b4"], temperature=temperature) * power
            shipyard = 0.0
            if levels["b9"] >= 13:
                build_time = 1_633_333.333 / (levels["b9"] * 2 ** levels["b17"] * speed)
                shipyard = hours * 3600.0 / build_time
            return {
                "power": power,
                "smelting": smelting,
                "net": [pig_iron - wanted * smelting, metal, kryptonite, spice],
                "limits": [metal * hours / 3e6, kryptonite * hours / 2e6, spice * hours / 5e5, shipyard],
                "energy": [produced, used],
            }

        cases = {
            "balanced": {"b1": 12, "b2": 8, "b3": 6, "b4": 5, "b5": 12, "b9": 13, "b17": 1},
            "short of energy": {"b1": 12, "b2": 8, "b3": 6, "b4": 5, "b5": 3, "b9": 14, "b17": 0},
            "short of pig iron": {"b1": 3, "b2": 9, "b3": 6, "b4": 5, "b5": 12, "b9": 20, "b17": 2},
            "no shipyard for the starbase": {"b1": 12, "b2": 8, "b3": 6, "b4": 5, "b5": 12, "b9": 12, "b17": 3},
        }
        for planet, temperature in ((0, 31), (1, 0)):
            for label, levels in cases.items():
                with self.subTest(case=label, planet=planet):
                    want = expected(levels, temperature)
                    design = np.array([[[levels[key] for key in model.optimized_keys]]])
                    got = {name: values[0, 0] for name, values in model.simulate(design, [planet]).items()}
                    np.testing.assert_allclose(got["planet_limits"], want["limits"], rtol=1e-9)
                    np.testing.assert_allclose(got["planet_net_per_hour"], want["net"], rtol=1e-9, atol=1e-9)
                    self.assertAlmostEqual(got["planet_power_ratio"], want["power"], places=12)
                    self.assertAlmostEqual(got["planet_smelting_ratio"], want["smelting"], places=12)
                    np.testing.assert_allclose(
                        [got["planet_energy_produced"], got["planet_energy_used"]], want["energy"], rtol=1e-12
                    )
        # The cases cover what they are named for.
        self.assertEqual(expected(cases["balanced"], 0)["power"], 1.0)
        self.assertEqual(expected(cases["balanced"], 0)["smelting"], 1.0)
        self.assertLess(expected(cases["short of energy"], 0)["power"], 0.5)
        self.assertLess(expected(cases["short of pig iron"], 0)["smelting"], 0.5)
        self.assertAlmostEqual(expected(cases["short of pig iron"], 0)["net"][0], 0.0, places=9)
        self.assertEqual(expected(cases["no shipyard for the starbase"], 0)["limits"][3], 0.0)
        self.assertGreater(expected(cases["balanced"], 0)["limits"][3], 0.0)
        # A hotter planet mines less Spice from the same mine.
        self.assertLess(expected(cases["balanced"], 31)["limits"][2], expected(cases["balanced"], 0)["limits"][2])

        # A whole configuration: every limit is added up over the planets, and the smallest sum counts.
        configuration = np.array([model.min_levels.copy()])
        configuration[0, 2] = [cases["balanced"][key] for key in model.optimized_keys]
        configuration[0, 5] = [cases["short of pig iron"][key] for key in model.optimized_keys]
        totals = np.add(expected(cases["balanced"], 0)["limits"], expected(cases["short of pig iron"], 0)["limits"])
        np.testing.assert_allclose(model.simulate(configuration)["planet_limits"][0].sum(axis=0), totals, rtol=1e-9)
        self.assertAlmostEqual(model.ships_per_month(configuration)[0], totals.min(), delta=1e-9 * totals.min())
        self.assertEqual(model.simulate(configuration)["planet_limits"].shape, (1, 11, 4))

        # Feasible means: inside the level ranges (Weapons factory at least 6 for the fleet) and inside the fields,
        # warehouses included. The first planet has 201 free fields: 46 + 46 + 42 + 6 levels and warehouses
        # 17, 15, 15 and 15 fill them exactly.
        feasible = model.is_feasible
        self.assertTrue(feasible(configuration)[0])
        self.assertTrue(feasible(np.array([model.min_levels]))[0])
        too_low, too_high, too_many = (configuration.copy() for _ in range(3))
        too_low[0, 3, model.building_index["b9"]] = 5
        too_high[0, 3, model.building_index["b1"]] = 51
        too_many[0, 0] = [45, 46, 0, 0, 42, 6, 0]
        self.assertEqual(model.simulate(too_many)["planet_warehouse_levels"][0, 0].tolist(), [17, 15, 15, 15])
        self.assertEqual(int(model.simulate(too_many)["planet_fields_needed"][0, 0]), 201)
        self.assertEqual(feasible(np.concatenate([too_low, too_high, too_many])).tolist(), [False, False, True])
        too_many[0, 0, 0] = 46
        self.assertFalse(feasible(too_many)[0])
        self.assertEqual(feasible(too_many[:, :1], [0]).tolist(), [False])
        self.assertEqual(feasible(too_many[:, 1:2], [1]).tolist(), [True])

        # Fixed buildings and the fleet's Solar cells feed the planet too: 40 Solar cells give 30 energy each
        # at 0 degrees C and 37.75 at 31 degrees C, times 1.1 for Energy technology 10.
        plans = ns["build_planet_plans"](
            ns["empire_planet_states"][:2],
            ns["PLANET_PARAM_CONTEXTS"][:2],
            optimized_max_levels=ns["OPTIMIZED_BUILDING_MAX_LEVELS"],
            misc_levels={**ns["MISC_BUILDING_LEVELS"], "b6": 4},
            misc_levels_by_slot={},
            min_fleet={"s9": 40},
            central_planet_slot=1,
            central_min_warehouse_level=0,
            research_costs=[],
        )
        contexts = [
            ns["planet_param_context"](
                ns["PLAN_ACCOUNT_CONTEXT"],
                plan["slot_index"],
                ns["empire_planet_states"][index],
                is_empty_slot=index > 0,
            )
            for index, plan in enumerate(plans)
        ]
        solar = ns["EmpireProductionModel"](
            plans,
            contexts,
            optimized_keys=model.optimized_keys,
            target_ship_key="s16",
            month_days=30.0,
            warehouse_hours=24.0,
        )
        # The Thermosolar plant 4 needs Spice mine 6, and the Solar cells a Weapons factory.
        self.assertEqual(solar.min_levels.tolist(), [[0, 0, 0, 6, 0, 1, 0]] * 2)
        idle = solar.simulate(np.array([solar.min_levels]))
        thermosolar = row(6, 4)
        np.testing.assert_allclose(
            idle["planet_energy_produced"][0],
            [(40 * 37.75 + thermosolar["energy"]) * 1.1, (40 * 30.0 + thermosolar["energy"]) * 1.1],
            rtol=1e-12,
        )
        self.assertEqual(idle["planet_power_ratio"][0].tolist(), [1.0, 1.0])
        # The Thermosolar plant burns Spice; the Spice mine 6 that it requires makes more than that.
        np.testing.assert_allclose(
            idle["planet_net_per_hour"][0, :, 3],
            [
                speed * game_data.mine_production_per_hour(4, 6, temperature=temperature) - thermosolar["spice_used"]
                for temperature in (31, 0)
            ],
            rtol=1e-12,
        )
        self.assertGreater(thermosolar["spice_used"], 0)
        self.assertEqual(solar.twin_planets, [0, 1])
        # Twins share a design catalogue, so only planets that are equal in every input may be twins.
        generic_plan, generic_context = ns["PLANET_PLANS"][1], contexts[1]
        other_fixed_levels = {**generic_plan["fixed_levels"], "b10": 0, "b15": generic_plan["fixed_levels"]["b10"]}
        self.assertEqual(sum(other_fixed_levels.values()), generic_plan["fixed_fields"])
        variants = [
            (generic_plan, generic_context),
            (dict(generic_plan), dict(generic_context)),  # an equal planet
            (generic_plan, {**generic_context, "planet_temperature_c": 40}),  # another Spice mine output
            ({**generic_plan, "solar_cells": 5}, generic_context),  # more energy
            ({**generic_plan, "min_levels": {**generic_plan["min_levels"], "b9": 7}}, generic_context),
            ({**generic_plan, "max_levels": {**generic_plan["max_levels"], "b1": 40}}, generic_context),
            ({**generic_plan, "free_fields": generic_plan["free_fields"] - 1}, generic_context),
            ({**generic_plan, "fixed_levels": other_fixed_levels}, generic_context),  # the same fields, other buildings
            # ... and in every input of the warehouses: the lowest levels, what the plan asks the planet to store,
            # the levels already built (they decide what is left to pay for), and being the central planet.
            (
                {**generic_plan, "warehouse_min_levels": {**generic_plan["warehouse_min_levels"], "b13": 1}},
                generic_context,
            ),
            (
                {**generic_plan, "capacity_needed": {**generic_plan["capacity_needed"], "spice": 5e6}},
                generic_context,
            ),
            ({**generic_plan, "built_levels": {**generic_plan["built_levels"], "b3": 9}}, generic_context),
            ({**generic_plan, "built_levels": {**generic_plan["built_levels"], "b14": 9}}, generic_context),
            ({**generic_plan, "is_central_planet": True}, generic_context),
        ]
        lookalikes = ns["EmpireProductionModel"](
            [plan for plan, _ in variants],
            [context for _, context in variants],
            optimized_keys=model.optimized_keys,
            target_ship_key="s16",
            month_days=30.0,
            warehouse_hours=24.0,
        )
        self.assertEqual(lookalikes.twin_planets, [0, 0, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12])
        self.assertEqual(lookalikes.central_planet, 12)
        # A month of 15 days halves every limit.
        half = ns["EmpireProductionModel"](
            ns["PLANET_PLANS"][:2],
            contexts,
            optimized_keys=model.optimized_keys,
            target_ship_key="s16",
            month_days=15.0,
            warehouse_hours=24.0,
        )
        design = np.array([[[cases["balanced"][key] for key in model.optimized_keys]]])
        np.testing.assert_allclose(
            half.simulate(design, [1])["planet_limits"], model.simulate(design, [1])["planet_limits"] / 2.0, rtol=1e-12
        )
        # Another target ship brings its own cost and prerequisites: the Small transport needs no Spice.
        transport = ns["EmpireProductionModel"](
            ns["PLANET_PLANS"][:2],
            contexts,
            optimized_keys=model.optimized_keys,
            target_ship_key="s1",
            month_days=30.0,
            warehouse_hours=24.0,
        )
        self.assertEqual(transport.limit_names, ("metal", "kryptonite", "shipyards"))
        self.assertEqual(
            (transport.ship_cost.tolist(), transport.target_requirements), ([3000.0, 3000.0], {"b9": 2, "b7": 3})
        )

    # ------------------------------------------------------------------ Section 4: warehouses

    @staticmethod
    def _warehouse_level_for(amount: float) -> int:
        """Lowest warehouse level that holds `amount`: a warehouse of level L holds 100,000 * 2^L."""
        level = 0
        while 100_000 * 2**level < amount:
            level += 1
        return level

    @staticmethod
    def _dearest_level(key: str, built_level: int, top_level: int) -> list[float]:
        """Per resource, the dearest single level of building `key` above `built_level`, up to `top_level`."""
        costs = [game_data.building_cost(int(key[1:]), level) for level in range(built_level + 1, top_level + 1)]
        return [
            max((float(cost.get(resource, 0)) for cost in costs), default=0.0)
            for resource in ("pig_iron", "metal", "kryptonite", "spice")
        ]

    def _expected_warehouses(
        self,
        plan: dict,
        levels: dict[str, int],
        net_per_hour: list[float],
        *,
        hours: float,
        ship_cost: dict | None = None,
        reserve: tuple[float, ...] = (0.0, 0.0, 0.0, 0.0),
    ) -> list[int]:
        """The warehouse rules of Section 4, written out for one planet and one design.

        `levels` are the design's levels of the optimized buildings, `ship_cost` the cost of the target ship
        when the design's shipyard builds it, and `reserve` what the central planet keeps for the empire.
        """
        resources = ("pig_iron", "metal", "kryptonite", "spice")
        warehouse_keys = ("b11", "b12", "b13", "b14")
        need = [
            max(float(plan["capacity_needed"][resource]), hours * max(0.0, float(net)), float(kept))
            for resource, net, kept in zip(resources, net_per_hour, reserve)
        ]
        for key, level in levels.items():
            need = [max(pair) for pair in zip(need, self._dearest_level(key, plan["built_levels"][key], level))]
        if ship_cost is not None:
            need = [max(amount, float(ship_cost.get(resource, 0))) for amount, resource in zip(need, resources)]
        warehouses = [
            max(plan["warehouse_min_levels"][key], self._warehouse_level_for(amount))
            for key, amount in zip(warehouse_keys, need)
        ]
        while True:  # a warehouse level has a cost of its own, which the warehouses must hold too
            own = [0.0] * 4
            for key, level in zip(warehouse_keys, warehouses):
                own = [max(pair) for pair in zip(own, self._dearest_level(key, plan["built_levels"][key], level))]
            raised = [max(level, self._warehouse_level_for(amount)) for level, amount in zip(warehouses, own)]
            if raised == warehouses:
                return warehouses
            warehouses = raised

    def test_warehouse_helpers_and_plan_inputs(self) -> None:
        ns, _, _ = self._attack_run()
        self.assertEqual(ns["WAREHOUSE_KEYS"], ("b11", "b12", "b13", "b14"))

        # A warehouse of level L holds 100,000 * 2^L, as the catalog says. An amount needs the first level that
        # holds it.
        level_for = ns["warehouse_level_for"]
        self.assertEqual(
            [level_for(amount) for amount in (0, 1, 100_000, 100_001, 200_000, 200_001)], [0, 0, 0, 1, 1, 2]
        )
        for level in (1, 7, 19, 33, 60):
            capacity = game_data.BUILDING_BY_ID[12]["levels"][level]["storage_capacity"]
            self.assertEqual(capacity, 100_000 * 2**level)
            self.assertEqual(
                (level_for(capacity), level_for(capacity + 1), level_for(capacity - 1)), (level, level + 1, level)
            )
            self.assertEqual(level_for(float(capacity)), self._warehouse_level_for(capacity))
        self.assertEqual([ns["catalog_top_level"](key) for key in ("b1", "b9", "b11", "b17")], [100, 60, 60, 55])

        # The largest cost per resource, with the label of the first cost that asks for it.
        amounts, labels = ns["largest_costs"](
            [("a", {"metal": 5, "spice": 1}), ("b", {"metal": 7}), ("c", {"metal": 7, "pig_iron": 2}), ("d", {})]
        )
        self.assertEqual(amounts, {"pig_iron": 2.0, "metal": 7.0, "kryptonite": 0.0, "spice": 1.0})
        self.assertEqual(labels, {"pig_iron": "c", "metal": "b", "kryptonite": "", "spice": "a"})
        self.assertEqual(ns["largest_costs"]([]), ({r: 0.0 for r in amounts}, {r: "" for r in amounts}))

        # The levels still to build, each with its cost.
        upgrades = ns["building_upgrade_costs"]("b2", 48, 50)
        self.assertEqual([label for label, _ in upgrades], ["b2 L49", "b2 L50"])
        self.assertEqual(upgrades[1][1], game_data.building_cost(2, 50))
        self.assertEqual(upgrades[1][1]["pig_iron"], 44_150_000_000)
        self.assertEqual(
            (ns["building_upgrade_costs"]("b2", 50, 50), ns["building_upgrade_costs"]("b2", 9, 4)), ([], [])
        )
        research = ns["research_upgrade_costs"]({"r2": 4, "r9": 3}, {"r2": 6, "r1": 1, "r3": 0, "r9": 3})
        self.assertEqual([label for label, _ in research], ["r2 L5", "r2 L6", "r1 L1"])
        self.assertEqual(research[1][1], game_data.RESEARCH_BY_ID["r2"]["levels"][6]["cost"])

        # upgrade_cost_table: nothing to pay up to the built level, then the dearest level so far.
        table = ns["upgrade_cost_table"]("b2", 3, 6)
        self.assertEqual(table.shape, (7, 4))
        self.assertEqual(table[:4].tolist(), [[0.0] * 4] * 4)
        for level in (4, 5, 6):
            self.assertEqual(table[level].tolist(), self._dearest_level("b2", 3, level))
        self.assertGreater(table[6, 0], table[5, 0])
        self.assertEqual(ns["upgrade_cost_table"]("b2", 6, 6).tolist(), [[0.0] * 4] * 7)
        # Beyond the catalog a level costs nothing more: the table keeps the dearest level it knows.
        beyond = ns["upgrade_cost_table"]("b17", 0, 57)
        self.assertEqual(beyond[57].tolist(), beyond[55].tolist())
        self.assertEqual(beyond[55].tolist(), self._dearest_level("b17", 0, 55))
        plans = ns["PLANET_PLANS"]
        tables = ns["upgrade_cost_tables"](plans[:2], ("b5", "b9"), 8)
        self.assertEqual(tables.shape, (2, 2, 9, 4))
        # The colony has Fusion plant 7: only level 8 is left to pay for there; a generic planet pays for all eight.
        self.assertEqual(tables[0, 0, 7].tolist(), [0.0] * 4)
        self.assertEqual(tables[0, 0, 8].tolist(), self._dearest_level("b5", 7, 8))
        self.assertEqual(tables[1, 0, 7].tolist(), self._dearest_level("b5", 0, 7))
        self.assertEqual(tables[1, 1, 8].tolist(), self._dearest_level("b9", 0, 8))

        # One planet. Warehouses: Pig iron 4 built, Metal 3 asked by the meta parameter, Kryptonite 2 built and
        # 1 asked, Spice nothing; as the central planet with a minimum of 2 it gets Spice 2, and the built
        # Kryptonite 2 is named before the equal minimum.
        home = ns["live_planet_states"][0]
        stocked = dataclasses.replace(home, buildings={**home.buildings, "b11": 4, "b13": 2}, ships={"s2": 1})
        misc = {f"b{number}": 0 for number in game_data.BUILDING_BY_ID if number not in (1, 2, 3, 4, 5, 9, 17)}
        misc.update(b7=2, b10=1, b20=1, b12=3, b13=1)
        max_levels = {"b1": 30, "b2": 30, "b3": 30, "b4": 30, "b5": 30, "b9": 20, "b17": 20}
        arguments = {"optimized_max_levels": max_levels, "misc_levels": misc, "min_fleet": {"s2": 3, "s10": 0}}
        context = ns["PLANET_PARAM_CONTEXTS"][0]
        plain = ns["build_planet_plan"](context, stocked, **arguments)
        central = ns["build_planet_plan"](context, stocked, **arguments, central_min_warehouse_level=2)
        self.assertEqual(plain["warehouse_min_levels"], {"b11": 4, "b12": 3, "b13": 2, "b14": 0})
        self.assertEqual(
            plain["warehouse_min_set_by"],
            {
                "b11": "level already built",
                "b12": "MISC_BUILDING_LEVELS",
                "b13": "level already built",
                "b14": "no minimum",
            },
        )
        self.assertEqual(central["warehouse_min_levels"], {"b11": 4, "b12": 3, "b13": 2, "b14": 2})
        self.assertEqual(central["warehouse_min_set_by"]["b14"], "central planet minimum")
        self.assertEqual(central["warehouse_min_set_by"]["b13"], "level already built")
        self.assertEqual((plain["is_central_planet"], central["is_central_planet"]), (False, True))
        self.assertEqual((plain["pays_for_research"], central["pays_for_research"]), (False, False))
        # A central planet with a minimum of 0 is still the central planet.
        self.assertTrue(
            ns["build_planet_plan"](context, stocked, **arguments, central_min_warehouse_level=0)["is_central_planet"]
        )
        # The warehouses are no fixed buildings: they take none of the fixed fields.
        self.assertEqual(
            {key: level for key, level in plain["fixed_levels"].items() if level}, {"b7": 3, "b10": 1, "b20": 1}
        )
        self.assertEqual(set(plain["fixed_levels"]) & {"b11", "b12", "b13", "b14"}, set())
        self.assertEqual((plain["fixed_fields"], plain["free_fields"]), (5, 225))
        # What the planet must be able to store whatever its design: the dearest level of its fixed buildings
        # (Development center 2 and 3, Fleet base 1, Tunnel system 1) and one Large transport, of which 2 are missing.
        costs = [
            game_data.building_cost(7, 2),
            game_data.building_cost(7, 3),
            game_data.building_cost(10, 1),
            game_data.building_cost(20, 1),
            {"metal": 8000, "kryptonite": 6000},
        ]
        labels = ["b7 L2", "b7 L3", "b10 L1", "b20 L1", "s2"]
        for resource in ("pig_iron", "metal", "kryptonite", "spice"):
            dearest = max(float(cost.get(resource, 0)) for cost in costs)
            self.assertEqual(plain["capacity_needed"][resource], dearest, resource)
            first = next((label for label, cost in zip(labels, costs) if cost.get(resource, 0) == dearest > 0), "")
            self.assertEqual(plain["capacity_needed_for"][resource], first, resource)
        # The Tunnel system costs more than a Large transport, which therefore asks for nothing here. A missing
        # Imperial starbase costs 3 million Metal; one that is already there costs nothing more.
        self.assertEqual(set(plain["capacity_needed_for"].values()) - {""}, {"b20 L1"})
        # A fixed building that already stands costs nothing more either: with the Tunnel system built, the
        # dearest Metal the planet has to store is the Large transport's.
        tunnelled = dataclasses.replace(stocked, buildings={**stocked.buildings, "b20": 1})
        with_tunnel = ns["build_planet_plan"](context, tunnelled, **arguments)
        self.assertEqual(
            (with_tunnel["capacity_needed"]["metal"], with_tunnel["capacity_needed_for"]["metal"]), (8000.0, "s2")
        )
        self.assertNotIn("b20 L1", with_tunnel["capacity_needed_for"].values())
        starbase_fleet = {**arguments, "min_fleet": {"s16": 1}}
        missing = ns["build_planet_plan"](context, stocked, **starbase_fleet)
        present = ns["build_planet_plan"](context, dataclasses.replace(stocked, ships={"s16": 1}), **starbase_fleet)
        self.assertEqual(
            (missing["capacity_needed"]["metal"], missing["capacity_needed_for"]["metal"]), (3_000_000.0, "s16")
        )
        self.assertLess(present["capacity_needed"]["metal"], 3_000_000.0)

        # warehouse_requirement_rows: what each rule asks of the stocked planet with its minimum design, when it
        # makes 30,000 Metal an hour and stores 10 hours of it.
        design = plain["min_levels"]
        net = np.array([0.0, 30_000.0, 0.0, 0.0])
        levels = self._expected_warehouses(plain, design, net, hours=10.0)
        self.assertEqual(levels, [4, 4, 2, 3])
        planned_levels = {**plain["fixed_levels"], **design, **dict(zip(("b11", "b12", "b13", "b14"), levels))}
        reasons = pd.DataFrame(
            ns["warehouse_requirement_rows"](
                plain, planned_levels, net, warehouse_hours=10.0, ship_built=None, central_reserve=np.zeros(4)
            )
        )
        self.assertEqual(reasons["resource"].tolist(), ["pig_iron", "metal", "kryptonite", "spice"])
        self.assertEqual(reasons["key"].tolist(), ["b11", "b12", "b13", "b14"])
        self.assertEqual(set(reasons["slot_index"]) | set(reasons["coords"]), {1, "02:380:12"})
        self.assertEqual(reasons["built_level"].tolist(), [4, 0, 2, 0])
        self.assertEqual(reasons["level"].tolist(), levels)
        self.assertEqual(reasons["capacity"].tolist(), [1_600_000.0, 1_600_000.0, 400_000.0, 800_000.0])
        # Pig iron: the dearest level is the Metal warehouse 4 itself, far less than a warehouse of level 0 holds.
        # Metal, Kryptonite and Spice: the Tunnel system costs 1,000,000, 200,000 and 500,000.
        self.assertEqual(reasons["largest_cost_of"].tolist(), ["b12 L4", "b20 L1", "b20 L1", "b20 L1"])
        self.assertEqual(reasons["largest_cost"].tolist(), [16_000.0, 1_000_000.0, 200_000.0, 500_000.0])
        self.assertEqual(reasons["level_for_largest_cost"].tolist(), [0, 4, 1, 3])
        self.assertEqual(reasons["level_for_own_production"].tolist(), [0, 2, 0, 0])
        self.assertEqual(reasons["level_for_empire_production"].tolist(), [0, 0, 0, 0])
        self.assertEqual(reasons["minimum_level"].tolist(), [4, 3, 2, 0])
        self.assertEqual(
            reasons["set_by"].tolist(),
            ["level already built", "cost of b20 L1", "level already built", "cost of b20 L1"],
        )
        # With a starbase to pay for, and as a central planet that keeps a billion Spice for the empire.
        with_ship = pd.DataFrame(
            ns["warehouse_requirement_rows"](
                plain,
                {**planned_levels, "b12": 5, "b13": 5},
                net,
                warehouse_hours=10.0,
                ship_built="s16",
                central_reserve=np.array([0.0, 0.0, 0.0, 1e9]),
            )
        )
        # The starbase costs as much Spice as the Tunnel system: the cost that comes first keeps the name.
        self.assertEqual(with_ship["largest_cost_of"].tolist(), ["b12 L5", "s16", "s16", "b20 L1"])
        self.assertEqual(with_ship["level_for_largest_cost"].tolist(), [0, 5, 5, 3])
        self.assertEqual(with_ship["level_for_empire_production"].tolist(), [0, 0, 0, 14])
        self.assertEqual(
            with_ship["set_by"].tolist(), ["level already built", "cost of s16", "cost of s16", "empire production"]
        )
        # Nothing to store: no cost, no production, no minimum.
        bare = {
            **plain,
            "capacity_needed": dict.fromkeys(plain["capacity_needed"], 0.0),
            "warehouse_min_levels": dict.fromkeys(plain["warehouse_min_levels"], 0),
        }
        nothing = ns["warehouse_requirement_rows"](
            bare,
            {**dict.fromkeys(planned_levels, 0), "b11": 4, "b13": 2},
            np.zeros(4),
            warehouse_hours=10.0,
            ship_built=None,
            central_reserve=np.zeros(4),
        )
        self.assertEqual([row["set_by"] for row in nothing], ["nothing to store"] * 4)

        # All planets: the central planet is the one in the given slot, and the research is paid for where the
        # highest Research lab stands (the first one between equals).
        states, contexts = ns["empire_planet_states"][:3], ns["PLANET_PARAM_CONTEXTS"][:3]
        research_costs = [("r1 L9", {"pig_iron": 7e9, "kryptonite": 5}), ("r2 L3", {"metal": 6e9, "spice": 1})]

        def plans_with(labs: dict[int, dict[str, int]], slot: int = 2) -> list[dict]:
            return ns["build_planet_plans"](
                states,
                contexts,
                optimized_max_levels=max_levels,
                misc_levels={**misc, "b12": 0, "b13": 0},
                misc_levels_by_slot=labs,
                min_fleet={},
                central_planet_slot=slot,
                central_min_warehouse_level=7,
                research_costs=research_costs,
            )

        three = plans_with({2: {"b8": 4}, 3: {"b8": 4}})
        self.assertEqual([plan["is_central_planet"] for plan in three], [False, True, False])
        self.assertEqual([plan["warehouse_min_levels"]["b14"] for plan in three], [0, 7, 0])
        self.assertEqual([plan["pays_for_research"] for plan in three], [False, True, False])
        self.assertEqual(three[1]["capacity_needed"]["pig_iron"], 7e9)
        self.assertEqual(three[1]["capacity_needed"]["metal"], 6e9)
        self.assertEqual(three[1]["capacity_needed_for"]["pig_iron"], "r1 L9")
        # Kryptonite: the Research lab 4 of the planet itself costs more than the 5 of the research.
        self.assertGreater(three[1]["capacity_needed"]["kryptonite"], 5)
        self.assertEqual(three[1]["capacity_needed_for"]["kryptonite"][:1], "b")
        # The third planet has the same lab but comes second: it stores for its own buildings only.
        self.assertLess(three[2]["capacity_needed"]["pig_iron"], 7e9)
        self.assertLess(three[2]["capacity_needed"]["metal"], 6e9)
        self.assertNotIn("r1 L9", three[2]["capacity_needed_for"].values())
        # The higher lab pays, wherever it stands; without a lab nobody pays.
        self.assertEqual(
            [plan["pays_for_research"] for plan in plans_with({1: {"b8": 2}, 3: {"b8": 5}})], [False, False, True]
        )
        self.assertEqual([plan["pays_for_research"] for plan in plans_with({})], [False, False, False])
        self.assertEqual([plan["is_central_planet"] for plan in plans_with({}, slot=3)], [False, False, True])
        with self.assertRaisesRegex(
            ValueError, r"CENTRAL_PLANET_SLOT 9 is not a normal planet of the roster: .*\[1, 2, 3\]"
        ):
            plans_with({}, slot=9)

        # The settings check knows the warehouse parameters.
        check = ns["check_optimizer_settings"]
        check(max_levels, misc, {}, {}, "s16", 0.0, 0)
        for arguments, message in (
            ((max_levels, misc, {}, {}, "s16", -1.0, 0), "WAREHOUSE_HOURS_OF_PRODUCTION"),
            ((max_levels, misc, {}, {}, "s16", math.inf, 0), "WAREHOUSE_HOURS_OF_PRODUCTION"),
            ((max_levels, misc, {}, {}, "s16", math.nan, 0), "WAREHOUSE_HOURS_OF_PRODUCTION"),
            ((max_levels, misc, {}, {}, "s16", 24.0, -1), "whole numbers of 0 or more"),
            ((max_levels, misc, {}, {}, "s16", 24.0, 2.5), "whole numbers of 0 or more"),
            (
                ({**max_levels, "b12": 9}, {k: v for k, v in misc.items() if k != "b12"}, {}, {}, "s16", 24.0, 0),
                r"The warehouses \['b12'\] belong in MISC_BUILDING_LEVELS",
            ),
        ):
            with self.subTest(error=message), self.assertRaisesRegex(ValueError, message):
                check(*arguments)

        # The notebook's own plan: slot 1 is the central planet with the minimum 15 and pays for the research.
        self.assertEqual([plan["is_central_planet"] for plan in plans], [True] + [False] * 10)
        self.assertEqual([plan["pays_for_research"] for plan in plans], [True] + [False] * 10)
        self.assertEqual(plans[0]["warehouse_min_levels"], {"b11": 15, "b12": 15, "b13": 15, "b14": 15})
        self.assertEqual(set(plans[0]["warehouse_min_set_by"].values()), {"central planet minimum"})
        self.assertEqual(plans[1]["warehouse_min_levels"], {"b11": 0, "b12": 0, "b13": 0, "b14": 0})
        self.assertEqual(set(plans[1]["warehouse_min_set_by"].values()), {"no minimum"})
        # A generic planet stores the cost of Development center 12 and of a Recycler; the colony also the
        # research of the plan: Graviton technology 10, Black hole research 2, ...
        self.assertEqual(
            plans[1]["capacity_needed_for"],
            {"pig_iron": "s11", "metal": "b7 L12", "kryptonite": "b7 L12", "spice": "b7 L12"},
        )
        self.assertEqual(plans[1]["capacity_needed"]["metal"], float(game_data.building_cost(7, 12)["metal"]))
        own, planned = ns["colony_researches"], ns["PLANNED_RESEARCH_LEVELS"]
        research_levels = [
            (f"{key} L{level}", game_data.RESEARCH_BY_ID[key]["levels"][level]["cost"])
            for key, top in planned.items()
            for level in range(own.get(key, 0) + 1, top + 1)
        ]
        self.assertGreater(len(research_levels), 40)
        for resource in ("metal", "kryptonite"):
            dearest_label, dearest_cost = max(research_levels, key=lambda entry: entry[1].get(resource, 0))
            self.assertEqual(plans[0]["capacity_needed"][resource], float(dearest_cost[resource]))
            self.assertEqual(plans[0]["capacity_needed_for"][resource], dearest_label)
            self.assertGreater(plans[0]["capacity_needed"][resource], plans[1]["capacity_needed"][resource])
        self.assertEqual(plans[0]["capacity_needed_for"]["spice"], "b8 L12")
        planets = ns["plan_planets_df"]
        self.assertEqual(planets["is_central_planet"].tolist(), [True] + [False] * 10)
        self.assertEqual(planets["pays_for_research"].tolist(), [True] + [False] * 10)
        # The lowest warehouses: four times level 15 on the central planet; on a generic planet the levels that
        # hold Development center 12 (Metal 4, Kryptonite 3, Spice 3).
        self.assertEqual(planets["min_warehouse_fields"].tolist(), [60] + [10] * 10)
        levels_table = ns["plan_building_levels_df"].set_index(["coords", "key"])
        self.assertEqual(
            levels_table.loc[("02:380:12", "b12"), ["role", "built_level", "min_level", "max_level"]].tolist(),
            ["warehouse", 0, 15, 60],
        )
        self.assertEqual(
            levels_table.loc[("EMPTY:01", "b14"), ["role", "min_level", "max_level"]].tolist(), ["warehouse", 0, 60]
        )
        self.assertEqual(int((ns["plan_building_levels_df"]["role"] == "warehouse").sum()), 11 * 4)

    def test_warehouses_of_a_design(self) -> None:
        ns, _, _ = self._attack_run()
        model, plans = ns["PRODUCTION_MODEL"], ns["PLANET_PLANS"]
        ship_cost = game_data.SHIPS[ns["SHIP_SLUG_BY_KEY"]["s16"]]["base_cost"]
        self.assertEqual(
            {resource: amount for resource, amount in ship_cost.items() if amount},
            {"metal": 3_000_000, "kryptonite": 2_000_000, "spice": 500_000},
        )
        self.assertEqual(model.warehouse_hours, 24.0)
        self.assertEqual((model.central_planet, model.central_reserve.tolist()), (0, [0.0] * 4))
        self.assertEqual(model.top_warehouse_level, 60)
        self.assertEqual(model.warehouse_capacities.tolist(), [float(100_000 * 2**level) for level in range(61)])
        self.assertEqual(model.planet_names[:2], ["02:380:12 (slot 1)", "EMPTY:01 (slot 2)"])

        def simulated(a_model, planet: int, levels: dict[str, int]) -> dict:
            design = np.array([[[levels[key] for key in a_model.optimized_keys]]])
            return {name: values[0, 0] for name, values in a_model.simulate(design, [planet]).items()}

        # A Metal planet on a generic planet. Blast furnace 50 costs 44.15 billion Pig iron and 14.72 billion
        # Kryptonite: Pig iron warehouse 19 and Kryptonite warehouse 18. Pig iron warehouse 19 costs Metal that
        # only Metal warehouse 12 holds, more than the 24 hours of Metal output ask for. Development center 12
        # costs Spice that needs Spice warehouse 3.
        metal_planet = {"b1": 47, "b2": 50, "b3": 0, "b4": 0, "b5": 44, "b9": 6, "b17": 0}
        outcome = simulated(model, 1, metal_planet)
        self.assertEqual(outcome["planet_warehouse_levels"].tolist(), [19, 12, 18, 3])
        self.assertEqual(outcome["planet_fields_needed"], 47 + 50 + 44 + 6 + 19 + 12 + 18 + 3)
        self.assertTrue(outcome["planet_fits"])
        self.assertEqual(
            game_data.building_cost(2, 50),
            {"pig_iron": 44_150_000_000, "metal": 0, "kryptonite": 14_720_000_000, "spice": 0},
        )
        self.assertEqual(
            (self._warehouse_level_for(44_150_000_000), self._warehouse_level_for(14_720_000_000)), (19, 18)
        )
        metal_of_the_warehouse = game_data.building_cost(11, 19)["metal"]
        self.assertEqual(self._warehouse_level_for(metal_of_the_warehouse), 12)
        self.assertEqual(self._warehouse_level_for(24.0 * outcome["planet_net_per_hour"][1]), 9)
        # On the colony the central minimum of 15 lifts the Metal and Spice warehouses: 214 fields, 13 too many.
        on_colony = simulated(model, 0, metal_planet)
        self.assertEqual(on_colony["planet_warehouse_levels"].tolist(), [19, 15, 18, 15])
        self.assertEqual((int(on_colony["planet_fields_needed"]), bool(on_colony["planet_fits"])), (214, False))
        self.assertEqual(int(model.free_fields[0]), 201)

        # Every rule, for many designs on the colony and on a generic planet, against the rules written out.
        rng = np.random.default_rng(11)
        cases = [
            metal_planet,
            {"b1": 12, "b2": 8, "b3": 6, "b4": 5, "b5": 12, "b9": 13, "b17": 1},
            {"b1": 3, "b2": 9, "b3": 6, "b4": 5, "b5": 12, "b9": 20, "b17": 2},
            {"b1": 50, "b2": 50, "b3": 50, "b4": 50, "b5": 50, "b9": 6, "b17": 0},
            dict(zip(model.optimized_keys, model.min_levels[1].tolist())),
        ]
        for planet in (0, 1):
            low, high = model.min_levels[planet], model.max_levels[planet]
            random_cases = [
                dict(zip(model.optimized_keys, row.tolist())) for row in rng.integers(low, high + 1, size=(60, 7))
            ]
            for levels in [*cases, *random_cases]:
                outcome = simulated(model, planet, levels)
                expected = self._expected_warehouses(
                    plans[planet],
                    levels,
                    outcome["planet_net_per_hour"],
                    hours=24.0,
                    ship_cost=ship_cost if levels["b9"] >= 13 else None,
                )
                self.assertEqual(outcome["planet_warehouse_levels"].tolist(), expected, (planet, levels))
                needed = sum(levels.values()) + sum(expected)
                self.assertEqual(int(outcome["planet_fields_needed"]), needed)
                self.assertEqual(bool(outcome["planet_fits"]), needed <= int(model.free_fields[planet]))
        # The random designs covered designs that fit and designs that do not, shipyards and none.
        spread = rng.integers(model.min_levels[1], model.max_levels[1] + 1, size=(400, 1, 7))
        fits = model.simulate(spread, [1])["planet_fits"][:, 0]
        self.assertTrue(0 < int(fits.sum()) < 400)
        self.assertTrue(0 < int((spread[:, 0, 5] >= 13).sum()) < 400)

        # The hours of production: none, a day, a hundred days. A hundred days of Metal need Metal warehouse 16.
        generic_context = ns["planet_param_context"](
            ns["PLAN_ACCOUNT_CONTEXT"], 2, ns["empire_planet_states"][1], is_empty_slot=True
        )
        by_hours = {
            hours: ns["EmpireProductionModel"](
                plans[1:2],
                [generic_context],
                optimized_keys=model.optimized_keys,
                target_ship_key="s16",
                month_days=30.0,
                warehouse_hours=hours,
            )
            for hours in (0.0, 24.0, 2400.0)
        }
        self.assertEqual(by_hours[2400.0].central_planet, -1)
        self.assertEqual(simulated(by_hours[0.0], 0, metal_planet)["planet_warehouse_levels"].tolist(), [19, 12, 18, 3])
        self.assertEqual(
            simulated(by_hours[24.0], 0, metal_planet)["planet_warehouse_levels"].tolist(), [19, 12, 18, 3]
        )
        long_storage = simulated(by_hours[2400.0], 0, metal_planet)
        self.assertEqual(long_storage["planet_warehouse_levels"].tolist(), [19, 16, 18, 3])
        self.assertEqual(self._warehouse_level_for(2400.0 * long_storage["planet_net_per_hour"][1]), 16)
        # Only what is left over is stored: a design short of Pig iron keeps none, whatever the hours.
        starved = {"b1": 3, "b2": 9, "b3": 6, "b4": 5, "b5": 12, "b9": 6, "b17": 0}
        self.assertEqual(simulated(by_hours[2400.0], 0, starved)["planet_net_per_hour"][0], 0.0)
        self.assertEqual(
            simulated(by_hours[2400.0], 0, starved)["planet_warehouse_levels"][0],
            simulated(by_hours[0.0], 0, starved)["planet_warehouse_levels"][0],
        )
        self.assertGreater(
            simulated(by_hours[2400.0], 0, starved)["planet_warehouse_levels"][2],
            simulated(by_hours[0.0], 0, starved)["planet_warehouse_levels"][2],
        )

        # A shipyard that builds the starbase must hold its cost: 3 million Metal, 2 million Kryptonite.
        yard = dict(zip(model.optimized_keys, [0, 0, 0, 0, 0, 13, 0]))
        no_yard = {**yard, "b9": 12}
        self.assertEqual(simulated(by_hours[0.0], 0, yard)["planet_warehouse_levels"].tolist(), [4, 5, 5, 4])
        self.assertEqual(simulated(by_hours[0.0], 0, no_yard)["planet_warehouse_levels"].tolist(), [3, 4, 3, 3])
        self.assertEqual((self._warehouse_level_for(3_000_000), self._warehouse_level_for(2_000_000)), (5, 5))

        # The central planet's reserve for the empire: a copy of the model, with more room on that planet only.
        reserve = np.array([0.0, 5e9, 0.0, 3e7])
        reserved = model.with_central_reserve(reserve)
        self.assertIsNot(reserved, model)
        self.assertEqual(
            (model.central_reserve.tolist(), reserved.central_reserve.tolist()), ([0.0] * 4, reserve.tolist())
        )
        self.assertIs(reserved.flow_tables, model.flow_tables)
        minimum = dict(zip(model.optimized_keys, model.min_levels[0].tolist()))
        self.assertEqual(simulated(model, 0, minimum)["planet_warehouse_levels"].tolist(), [15, 15, 15, 15])
        self.assertEqual(simulated(reserved, 0, minimum)["planet_warehouse_levels"].tolist(), [15, 16, 15, 15])
        self.assertEqual(
            simulated(reserved, 0, minimum)["planet_warehouse_levels"].tolist(),
            self._expected_warehouses(plans[0], minimum, [0.0] * 4, hours=24.0, reserve=tuple(reserve)),
        )
        self.assertEqual(
            simulated(reserved, 1, metal_planet)["planet_warehouse_levels"].tolist(),
            simulated(model, 1, metal_planet)["planet_warehouse_levels"].tolist(),
        )
        # A reserve that leaves the planet no room is an error: 29 fixed fields, Weapons factory 6 and four
        # warehouses of level 49 need 231 of the 230 fields. One level less on one warehouse fits.
        level_49 = float(100_000 * 2**49)
        with self.assertRaisesRegex(ValueError, r"Planet 02:380:12 \(slot 1\) has 230 fields, .* need 231: lower"):
            model.with_central_reserve(np.full(4, level_49))
        roomy = model.with_central_reserve(np.array([level_49, level_49, level_49, level_49 / 2.0]))
        self.assertEqual(simulated(roomy, 0, minimum)["planet_warehouse_levels"].tolist(), [49, 49, 49, 48])
        # So is a reserve that no warehouse of the catalog holds: level 60 is the last one.
        with self.assertRaisesRegex(ValueError, r"Planet 02:380:12 \(slot 1\) would need a warehouse beyond level 60"):
            model.with_central_reserve(np.array([0.0, 1e30, 0.0, 0.0]))

        # A planet too small for its fixed buildings, its minimum levels and their warehouses is an error:
        # 17 fixed fields, Weapons factory 6 for the fleet, and warehouses 0, 4, 3 and 3 make 33.
        tiny = {**plans[1], "fields_max": 32, "free_fields": 32 - 17}
        with self.assertRaisesRegex(
            ValueError, r"Planet EMPTY:01 \(slot 2\) has 32 fields, .* need 33: lower MISC_BUILDING_LEVELS"
        ):
            ns["EmpireProductionModel"](
                [tiny],
                [generic_context],
                optimized_keys=model.optimized_keys,
                target_ship_key="s16",
                month_days=30.0,
                warehouse_hours=24.0,
            )
        just_enough = ns["EmpireProductionModel"](
            [{**tiny, "fields_max": 33, "free_fields": 16}],
            [generic_context],
            optimized_keys=model.optimized_keys,
            target_ship_key="s16",
            month_days=30.0,
            warehouse_hours=24.0,
        )
        self.assertTrue(just_enough.is_feasible(just_enough.min_levels[None])[0])
        self.assertEqual(
            simulated(just_enough, 0, dict(zip(model.optimized_keys, just_enough.min_levels[0].tolist())))[
                "planet_warehouse_levels"
            ].tolist(),
            [0, 4, 3, 3],
        )

        # in_range looks at the levels only; is_feasible also at the fields, warehouses included.
        full = np.array([[[50, 50, 50, 22, 50, 6, 0]]])
        overfull = np.array([[[50, 50, 50, 23, 50, 6, 0]]])
        self.assertEqual(int(model.simulate(full, [1])["planet_fields_needed"][0, 0]), 283)
        self.assertEqual(model.simulate(full, [1])["planet_warehouse_levels"][0, 0].tolist(), [20, 13, 19, 3])
        self.assertEqual(
            (model.is_feasible(full, [1]).tolist(), model.is_feasible(overfull, [1]).tolist()), ([True], [False])
        )
        self.assertEqual((model.in_range(full, [1]).tolist(), model.in_range(overfull, [1]).tolist()), ([True], [True]))
        too_high = np.array([[[51, 0, 0, 0, 0, 6, 0]]])
        too_low = np.array([[[0, 0, 0, 0, 0, 5, 0]]])
        self.assertEqual(
            (model.in_range(too_high, [1]).tolist(), model.is_feasible(too_high, [1]).tolist()), ([False], [False])
        )
        self.assertEqual(
            (model.in_range(too_low, [1]).tolist(), model.is_feasible(too_low, [1]).tolist()), ([False], [False])
        )
        self.assertEqual(model.planet_index(None).tolist(), list(range(11)))
        self.assertEqual(model.planet_index([4, 2]).tolist(), [4, 2])

    def test_genetic_algorithm_and_hill_climb(self) -> None:
        ns, _, _ = self._attack_run()
        run_ga = ns["run_genetic_algorithm"]
        settings = {
            "population_size": 100,
            "generations": 60,
            "tournament_size": 2,
            "crossover_rate": 0.9,
            "mutation_rate": 0.3,
            "elite_count": 2,
        }

        def every_choice_of(counts: np.ndarray) -> np.ndarray:
            """Every way to pick one design per planet, one row each."""
            grids = np.meshgrid(*[np.arange(count) for count in counts], indexing="ij")
            return np.stack(grids, axis=-1).reshape(-1, len(counts))

        # A choice problem small enough to enumerate: 5 planets with 5 to 8 designs each, and 3 limits.
        rng = np.random.default_rng(12345)
        design_counts = np.array([8, 6, 7, 8, 5])
        design_limits = rng.uniform(0.0, 100.0, size=(5, 8, 3))
        # A design beyond a planet's catalogue would be worth a fortune: the algorithm must never pick one.
        for planet, count in enumerate(design_counts):
            design_limits[planet, count:] = 1_000.0
        every_choice = every_choice_of(design_counts)
        self.assertEqual(len(every_choice), 13_440)
        fitness = design_limits[np.arange(5), every_choice].sum(axis=1).min(axis=1)
        chosen, history = run_ga(design_limits, design_counts, seed=7, **settings)
        self.assertEqual(chosen.tolist(), every_choice[int(fitness.argmax())].tolist())
        # The history has one row per generation, starts below the optimum and never loses its best chromosome.
        self.assertEqual(list(history["generation"]), list(range(61)))
        best = history["best_ships_per_month"].to_numpy()
        self.assertEqual(best[-1], fitness.max())
        self.assertTrue((np.diff(best) >= 0.0).all())
        self.assertLess(best[0], best[-1])
        self.assertTrue((history["mean_ships_per_month"] <= history["best_ships_per_month"]).all())
        self.assertTrue((history["worst_ships_per_month"] <= history["mean_ships_per_month"]).all())
        # The same seed repeats the run; another seed takes another path.
        again, again_history = run_ga(design_limits, design_counts, seed=7, **settings)
        self.assertEqual(again.tolist(), chosen.tolist())
        pd.testing.assert_frame_equal(again_history, history)
        other_history = run_ga(design_limits, design_counts, seed=8, **settings)[1]
        self.assertFalse(other_history["mean_ships_per_month"].equals(history["mean_ships_per_month"]))

        def first_and_last_best(**changes: float) -> tuple[float, float]:
            """Best fitness of the first and of the last generation of a run with changed settings."""
            best_per_generation = run_ga(design_limits, design_counts, seed=7, **{**settings, **changes})[1][
                "best_ships_per_month"
            ]
            return float(best_per_generation.iloc[0]), float(best_per_generation.iloc[-1])

        # Selection alone can only spread what the first generation holds. Crossover alone finds something
        # better, and so does mutation alone.
        first, last = first_and_last_best(crossover_rate=0.0, mutation_rate=0.0)
        self.assertEqual(last, first)
        first, last = first_and_last_best(mutation_rate=0.0)
        self.assertGreater(last, first)
        first, last = first_and_last_best(crossover_rate=0.0)
        self.assertGreater(last, first)
        # The tournament prefers the fitter chromosome: with larger tournaments the population closes in faster.
        early_mean = [
            float(
                run_ga(design_limits, design_counts, seed=7, **{**settings, "tournament_size": size})[1][
                    "mean_ships_per_month"
                ].iloc[3]
            )
            for size in (1, 2, 6)
        ]
        self.assertLess(early_mean[0], early_mean[1])
        self.assertLess(early_mean[1], early_mean[2])

        # The history reports the mean. One design in eight is worth 100 ships here and the others nothing:
        # most of a random first generation scores 0, a few score 100.
        lottery = np.zeros((1, 8, 3))
        lottery[0, 0] = 100.0
        lottery_choice, lottery_history = run_ga(lottery, np.array([8]), seed=7, **{**settings, "generations": 0})
        self.assertEqual(lottery_choice.tolist(), [0])
        self.assertEqual(len(lottery_history), 1)
        first_generation = lottery_history.iloc[0]
        self.assertEqual(
            (first_generation["best_ships_per_month"], first_generation["worst_ships_per_month"]), (100.0, 0.0)
        )
        self.assertTrue(0.0 < first_generation["mean_ships_per_month"] < 50.0)

        # Scores: the balanced score lies between the smallest limit and a little above it, and rewards the rest.
        limits = np.array(
            [[10.0, 10.0, 10.0, 10.0], [10.0, 20.0, 30.0, 40.0], [10.0, 20.0, 30.0, 400.0], [0.0, 5.0, 5.0, 5.0]]
        )
        smallest = ns["smallest_limit"](limits)
        balanced = ns["balanced_score"](limits)
        self.assertEqual(smallest.tolist(), [10.0, 10.0, 10.0, 0.0])
        self.assertAlmostEqual(balanced[0], 10.0, places=12)
        self.assertTrue(10.0 < balanced[1] < balanced[2] < 10.0 * 4 ** (1 / 8))
        self.assertTrue(0.0 < balanced[3] < 1e-8)

        # Moves of the hill-climb: 6 single, 12 double and 3 field-neutral triple moves for 3 buildings.
        moves = ns["planet_moves"](3, (1,))
        self.assertEqual(len(moves), 21)
        self.assertEqual(len({tuple(move) for move in moves.tolist()}), 21)
        self.assertEqual(sorted(np.abs(moves).sum(axis=1).tolist()), [1] * 6 + [2] * 12 + [4] * 3)
        self.assertEqual(sorted(moves[np.abs(moves).sum(axis=1) == 4].sum(axis=1).tolist()), [0, 0, 0])
        self.assertEqual(len(ns["planet_moves"](7, (1, 2, 4, 8))), 4 * (14 + 84 + 105))
        self.assertEqual(int(np.abs(ns["planet_moves"](3, (4,))).max()), 8)

        # Toy empires that can be enumerated: Small transports from one to three planets, with mines of at
        # most level 4 to 6. Each line: highest mine level, fields of each planet, and the share of the best
        # of all configurations that the search reaches. The search is not exact: where the fields are so few
        # that every level is a big step, the best design of a planet is none that its catalogue holds.
        step_sizes = (1, 2, 4, 8)
        all_moves = ns["planet_moves"](5, step_sizes)
        toys = [
            (5, [20, 18], 1.0),
            (5, [20], 1.0),
            (4, [16, 14, 12], 1.0),
            (6, [20, 18, 16], 0.96),
            (5, [14, 12], 0.91),
        ]
        for top_level, fields, share in toys:
            with self.subTest(top_level=top_level, fields=fields):
                max_levels = {"b1": top_level, "b2": top_level, "b3": top_level, "b5": top_level + 1, "b9": 3}
                toy = self._toy_model(ns, "s1", max_levels, len(fields), fields)
                # Every planet gets Development center 3, and the colony keeps the Spice mine 2 that it has.
                self.assertEqual(toy.free_fields.tolist(), [fields[0] - 5] + [size - 3 for size in fields[1:]])
                self.assertEqual(toy.limit_names, ("metal", "kryptonite", "shipyards"))

                # Brute force. A design that another design of its planet matches or beats in every limit
                # is never needed, so the best configuration combines designs that are left without those.
                configurations = 1
                every_limit = []
                totals = np.zeros((1, 3))
                for planet in range(len(fields)):
                    axes = [
                        np.arange(low, high + 1) for low, high in zip(toy.min_levels[planet], toy.max_levels[planet])
                    ]
                    designs = np.stack(np.meshgrid(*axes, indexing="ij"), axis=-1).reshape(-1, 5)
                    designs = designs[toy.is_feasible(designs[:, None, :], [planet])]
                    configurations *= len(designs)
                    every_limit.append(toy.simulate(designs[:, None, :], [planet])["planet_limits"][:, 0])
                    front = _undominated_rows(every_limit[planet])
                    totals = _undominated_rows((totals[:, None, :] + front[None, :, :]).reshape(-1, 3))
                best_ships = float(totals.min(axis=1).max())
                self.assertGreater(best_ships, 0.0)
                self.assertGreater(configurations, 1_000 ** len(fields))

                # Catalogues: feasible designs without repeats. With every level on the coarse grid, each
                # planet's catalogue holds the design that is best for each single resource.
                catalogues = ns["build_design_catalogues"](
                    toy, grid_size=100_000, priority_steps=4, step_sizes=step_sizes
                )
                self.assertEqual(len(catalogues), len(fields))
                toy_limits, toy_counts = ns["catalogue_limits"](toy, catalogues)
                self.assertEqual(toy_counts.tolist(), [len(catalogue) for catalogue in catalogues])
                self.assertEqual(toy_limits.shape, (len(fields), int(toy_counts.max()), 3))
                for planet, catalogue in enumerate(catalogues):
                    self.assertTrue(toy.is_feasible(catalogue[:, None, :], [planet]).all())
                    self.assertEqual(len(np.unique(catalogue, axis=0)), len(catalogue))
                    own_limits = toy_limits[planet, : toy_counts[planet]]
                    np.testing.assert_allclose(
                        own_limits, toy.simulate(catalogue[:, None, :], [planet])["planet_limits"][:, 0]
                    )
                    self.assertTrue((toy_limits[planet, toy_counts[planet] :] == 0.0).all())
                    for resource in range(2):
                        self.assertAlmostEqual(
                            own_limits[:, resource].max(), every_limit[planet][:, resource].max(), places=9
                        )

                # The genetic algorithm finds the best of all the choices that the catalogues allow.
                chosen, history = run_ga(toy_limits, toy_counts, seed=1, **settings)
                every_choice = every_choice_of(toy_counts)
                best_choice_ships = toy_limits[np.arange(len(fields)), every_choice].sum(axis=1).min(axis=1).max()
                self.assertEqual(history["best_ships_per_month"].iloc[-1], best_choice_ships)
                ga_levels = np.array([catalogues[planet][design] for planet, design in enumerate(chosen)])
                ga_ships = float(toy.ships_per_month(ga_levels[None])[0])
                self.assertAlmostEqual(ga_ships, best_choice_ships, places=9)

                # The polish keeps or raises that, and ends at the promised share of the true optimum.
                polished = ns["polish_both_ways"](toy, ga_levels, step_sizes)
                polished_ships = float(toy.ships_per_month(polished[None])[0])
                self.assertTrue(toy.is_feasible(polished[None])[0])
                self.assertGreaterEqual(polished_ships, ga_ships)
                self.assertLessEqual(polished_ships, best_ships * (1.0 + 1e-12))
                self.assertGreaterEqual(polished_ships, share * best_ships * (1.0 - 1e-12))
                # It is a local optimum without idle levels: no move of one planet builds more ships, and
                # none builds as many with fewer levels.
                for planet in range(len(fields)):
                    neighbours = polished[planet] + all_moves
                    neighbours = neighbours[toy.is_feasible(neighbours[:, None, :], [planet])]
                    changed = np.repeat(polished[None], len(neighbours), axis=0)
                    changed[:, planet] = neighbours
                    ships = toy.ships_per_month(changed)
                    self.assertFalse((ships > polished_ships).any())
                    self.assertFalse(
                        ((ships == polished_ships) & (neighbours.sum(axis=1) < polished[planet].sum())).any()
                    )
                # Counting ships directly, polish_configuration alone never loses ships either.
                directly = ns["polish_configuration"](toy, ga_levels, ns["smallest_limit"], step_sizes)
                self.assertGreaterEqual(float(toy.ships_per_month(directly[None])[0]), ga_ships)
                self.assertLessEqual(float(toy.ships_per_month(directly[None])[0]), polished_ships + 1e-9)

        # improve_design never makes a design worse, and leaves a local optimum untouched.
        toy = self._toy_model(ns, "s1", {"b1": 5, "b2": 5, "b3": 5, "b5": 6, "b9": 3}, 2, [20, 18])
        own_ships = ns["smallest_limit"]
        # The polish moves by the given step sizes only. One level less on a polished planet costs ships;
        # steps of 1 level win them back, steps of 8 levels fit no building of this toy.
        catalogues = ns["build_design_catalogues"](toy, grid_size=100_000, priority_steps=4, step_sizes=step_sizes)
        chosen = run_ga(*ns["catalogue_limits"](toy, catalogues), seed=1, **settings)[0]
        polished = ns["polish_both_ways"](
            toy, np.array([catalogues[planet][design] for planet, design in enumerate(chosen)]), step_sizes
        )
        lowered = polished.copy()
        lowered[1, int(lowered[1].argmax())] -= 1
        ships_of_levels = toy.ships_per_month
        self.assertLess(ships_of_levels(lowered[None])[0], ships_of_levels(polished[None])[0])
        self.assertEqual(ns["polish_configuration"](toy, lowered, own_ships, (8,)).tolist(), lowered.tolist())
        self.assertGreater(
            ships_of_levels(ns["polish_configuration"](toy, lowered, own_ships, (1,))[None])[0],
            ships_of_levels(lowered[None])[0],
        )
        # The polish works on a copy: the configuration it is given stays as it was.
        self.assertEqual(lowered[1].sum(), polished[1].sum() - 1)

        # Following the balanced score first can end higher than counting ships from the start, which is why
        # polish_both_ways tries both and keeps the better result. Two generic planets of the notebook's empire
        # show it.
        trio_plans = ns["PLANET_PLANS"][1:3]
        trio = ns["EmpireProductionModel"](
            trio_plans,
            [
                ns["planet_param_context"](
                    ns["PLAN_ACCOUNT_CONTEXT"],
                    plan["slot_index"],
                    ns["empire_planet_states"][plan["slot_index"] - 1],
                    is_empty_slot=plan["planet_status"] == "empty_colony_slot",
                )
                for plan in trio_plans
            ],
            optimized_keys=ns["PRODUCTION_MODEL"].optimized_keys,
            target_ship_key="s16",
            month_days=30.0,
            warehouse_hours=0.0,
        )
        trio_catalogues = ns["build_design_catalogues"](
            trio, grid_size=100_000, priority_steps=4, step_sizes=step_sizes
        )
        trio_choice = run_ga(*ns["catalogue_limits"](trio, trio_catalogues), seed=2, **settings)[0]
        trio_levels = np.array([trio_catalogues[planet][design] for planet, design in enumerate(trio_choice)])
        counted_directly = ns["polish_configuration"](trio, trio_levels, own_ships, step_sizes)
        both_ways = ns["polish_both_ways"](trio, trio_levels, step_sizes)
        self.assertGreater(
            trio.ships_per_month(both_ways[None])[0], 1.005 * trio.ships_per_month(counted_directly[None])[0]
        )
        self.assertTrue(trio.is_feasible(both_ways[None])[0])
        # Counting ships from the start can just as well be the better way, and then that result is kept. From
        # the last design of each catalogue, which has no shipyard, the detour over the balanced score loses.
        polish = ns["polish_configuration"]
        last_designs = np.array([catalogue[-1] for catalogue in trio_catalogues])
        self.assertEqual(trio.ships_per_month(last_designs[None])[0], 0.0)
        directly = polish(trio, last_designs, own_ships, step_sizes)
        detour = polish(trio, polish(trio, last_designs, ns["balanced_score"], step_sizes), own_ships, step_sizes)
        self.assertGreater(trio.ships_per_month(directly[None])[0], 1.2 * trio.ships_per_month(detour[None])[0])
        self.assertEqual(ns["polish_both_ways"](trio, last_designs, step_sizes).tolist(), directly.tolist())

        # The polish goes round the planets until a whole round changes nothing. Here the first planet starts
        # as a bare shipyard and the second as the all-rounder of its catalogue, which builds every ship alone.
        # The first round can only tune the all-rounder. It takes more rounds to make a producer of the first
        # planet too, and then the two build more than twice what the all-rounder built alone.
        trio_limits = ns["catalogue_limits"](trio, trio_catalogues)[0]
        lopsided = np.array([trio_catalogues[0][0], trio_catalogues[1][int(own_ships(trio_limits[1]).argmax())]])
        self.assertEqual(lopsided[0, :5].tolist(), [0] * 5)
        alone = float(trio.ships_per_month(lopsided[None])[0])
        self.assertGreater(alone, 0.0)
        settled = polish(trio, lopsided, own_ships, step_sizes)
        self.assertGreater(float(trio.ships_per_month(settled[None])[0]), 2.0 * alone)
        # What the polish returns, it leaves as it is.
        self.assertEqual(polish(trio, settled, own_ships, step_sizes).tolist(), settled.tolist())

        def ships_of(design: np.ndarray) -> float:
            return float(own_ships(toy.simulate(design[None, None, :], [1])["planet_limits"][:, 0])[0])

        def ignore_the_design(limits: np.ndarray) -> np.ndarray:
            return np.zeros(len(limits))

        start = toy.max_levels[1] // 2
        improved = ns["improve_design"](toy, 1, start, own_ships, all_moves)
        self.assertGreater(ships_of(improved), ships_of(start))
        self.assertEqual(ns["improve_design"](toy, 1, improved, own_ships, all_moves).tolist(), improved.tolist())
        # Idle levels go: with a score that ignores the design, the hill-climb strips it to the minimum levels.
        self.assertEqual(
            ns["improve_design"](toy, 0, toy.max_levels[0] // 2, ignore_the_design, all_moves).tolist(), [0] * 5
        )
        # A planet without a single field to move stays as it is.
        cramped = self._toy_model(ns, "s1", {"b1": 0, "b2": 0, "b3": 0, "b5": 0, "b9": 0}, 1, [5])
        self.assertEqual(cramped.free_fields.tolist(), [0])
        self.assertEqual(
            ns["improve_design"](cramped, 0, np.zeros(5, dtype=np.int64), own_ships, all_moves).tolist(), [0] * 5
        )

    def test_best_configuration_of_the_notebook(self) -> None:
        ns, _, _ = self._attack_run()
        model, plans = ns["BEST_CONFIG_MODEL"], ns["PLANET_PLANS"]
        best = ns["best_levels_arr"]
        summary = ns["BEST_CONFIG_SUMMARY"]
        ga = ns["GA_SUMMARY"]
        self.assertEqual(best.shape, (11, 7))
        self.assertTrue(model.is_feasible(best[None])[0])
        ships = float(model.ships_per_month(best[None])[0])
        self.assertGreater(ships, 1000.0)
        self.assertEqual(summary["ships_per_month"], round(ships, 2))
        self.assertEqual(summary["whole_ships_per_month"], int(ships))
        self.assertEqual(summary["target_ship"], "Imperial starbase")
        self.assertEqual((summary["planets"], summary["hours_per_month"]), (11, 720.0))

        # Catalogues: one per planet, the ten generic planets share theirs, and every design is feasible.
        catalogues = ns["DESIGN_CATALOGUES"]
        self.assertEqual(len(catalogues), 11)
        for planet, catalogue in enumerate(catalogues):
            self.assertTrue(model.is_feasible(catalogue[:, None, :], [planet]).all())
            self.assertIs(catalogue, catalogues[model.twin_planets[planet]])
        self.assertIsNot(catalogues[0], catalogues[1])
        self.assertEqual(ga["designs_per_planet"], [len(catalogue) for catalogue in catalogues])
        catalogue_table = ns["design_catalogue_df"]
        self.assertEqual(len(catalogue_table), sum(ga["designs_per_planet"]))
        first = catalogue_table.iloc[0]
        self.assertEqual((first["slot_index"], first["coords"], first["design"]), (1, "02:380:12", 0))
        self.assertEqual(first[list(model.optimized_keys)].tolist(), catalogues[0][0].tolist())
        # A design takes the fields of its levels and of the warehouses that the model gives it.
        self.assertEqual(
            catalogue_table["fields"].tolist(),
            (catalogue_table[list(model.optimized_keys)].sum(axis=1) + catalogue_table["warehouse_levels"]).tolist(),
        )
        for planet in (0, 1):
            rows = catalogue_table[catalogue_table["slot_index"] == planet + 1]
            outcome = model.simulate(catalogues[planet][:, None, :], [planet])
            self.assertEqual(
                rows["warehouse_levels"].tolist(), outcome["planet_warehouse_levels"][:, 0].sum(axis=1).tolist()
            )
            self.assertEqual(rows["fields"].tolist(), outcome["planet_fields_needed"][:, 0].tolist())
            self.assertTrue((rows["fields"] <= model.free_fields[planet]).all())
            self.assertGreater(rows["warehouse_levels"].nunique(), 3)
        self.assertGreater(catalogue_table["fields"].nunique(), 5)
        np.testing.assert_allclose(
            first[[f"{limit}_ships_per_month" for limit in model.limit_names]].to_numpy(dtype=float),
            model.simulate(catalogues[0][:1, None, :], [0])["planet_limits"][0, 0],
            atol=0.006,
        )
        # The catalogue holds specialists: for each resource, a design that makes that one and neither of the others.
        generic = catalogue_table[catalogue_table["slot_index"] == 2]
        resource_columns = ["metal_ships_per_month", "kryptonite_ships_per_month", "spice_ships_per_month"]
        for column in resource_columns:
            others = [other for other in resource_columns if other != column]
            self.assertTrue(((generic[column] > 0) & (generic[others] == 0).all(axis=1)).any(), column)
        # ... and no design wastes its fields on a shipyard that the empire could never feed. The most that
        # the empire can pay for is what its scarcest resource gives when every planet makes nothing else; a
        # shipyard doubles with every Microsystem accelerator level, so the smallest one that finishes those
        # ships finishes less than twice as many.
        colony = catalogue_table[catalogue_table["slot_index"] == 1]
        ceiling = float((colony[resource_columns].max() + 10.0 * generic[resource_columns].max()).min())
        self.assertGreater(ceiling, ships)
        for rows in (colony, generic):
            self.assertLess(float(rows["shipyards_ships_per_month"].max()), 2.0 * ceiling)
            # Some designs pair such a shipyard with a mine: the shipyard does not need a planet of its own.
            self.assertTrue(
                ((rows["shipyards_ships_per_month"] >= ceiling) & (rows[resource_columns].max(axis=1) > 0)).any()
            )
        peaks = np.array(
            [
                ns["resource_peaks"](
                    model, planet, *ns["coarse_designs"](model, planet, 100_000), ns["planet_moves"](7, (1, 2, 4, 8))
                )
                for planet in (0, 1)
            ]
        )
        np.testing.assert_allclose(peaks[0], colony[resource_columns].max().to_numpy(), atol=0.006)
        np.testing.assert_allclose(peaks[1], generic[resource_columns].max().to_numpy(), atol=0.006)
        grid, grid_limits = ns["coarse_designs"](model, 1, 100_000)
        self.assertTrue(model.is_feasible(grid[:, None, :], [1]).all())
        self.assertEqual(len(np.unique(grid, axis=0)), len(grid))
        np.testing.assert_array_equal(grid_limits, model.simulate(grid[:, None, :], [1])["planet_limits"][:, 0])
        # The grid holds five levels of every building; most of their combinations do not fit the planet.
        self.assertEqual([len(np.unique(grid[:, column])) for column in range(7)], [5, 5, 5, 5, 5, 5, 5])
        self.assertTrue(1000 < len(grid) < 5**7)

        # The search settings reach the hill-climb. With a grid of only the lowest and the highest level of
        # every building, and steps of 50 levels, a design can hold no other level; the notebook's own do.
        corners = ns["build_design_catalogues"](model, grid_size=1, priority_steps=4, step_sizes=(50,))
        for planet in (0, 1):
            lowest, highest = model.min_levels[planet], model.max_levels[planet]
            self.assertTrue(((corners[planet] == lowest) | (corners[planet] == highest)).all())
            self.assertFalse(((catalogues[planet] == lowest) | (catalogues[planet] == highest)).all())

        # The genetic algorithm chose one design per planet; the polish kept or raised its result.
        chosen = ns["ga_designs_arr"]
        self.assertEqual(chosen.shape, (11,))
        self.assertEqual(
            ns["ga_levels_arr"].tolist(), [catalogues[planet][design].tolist() for planet, design in enumerate(chosen)]
        )
        history = ns["ga_history_df"]
        self.assertEqual(len(history), ns["GA_GENERATIONS"] + 1)
        rounds = ns["central_reserve_rounds_df"]
        self.assertEqual(ga["search_rounds"], len(rounds))
        self.assertEqual(
            ga["chromosomes_evaluated"], ns["GA_POPULATION_SIZE"] * (ns["GA_GENERATIONS"] + 1) * len(rounds)
        )
        self.assertEqual(ga["ga_ships_per_month"], round(float(history["best_ships_per_month"].iloc[-1]), 2))
        self.assertEqual(ga["polished_ships_per_month"], round(ships, 2))
        self.assertGreaterEqual(ga["polished_ships_per_month"], ga["ga_ships_per_month"])
        self.assertEqual(ga["levels_changed_by_polish"], int(np.abs(best - ns["ga_levels_arr"]).sum()))
        settled = ga["first_generation_with_the_final_best"]
        self.assertEqual(history["best_ships_per_month"].iloc[settled], history["best_ships_per_month"].iloc[-1])
        self.assertTrue(
            settled == 0 or history["best_ships_per_month"].iloc[settled - 1] < history["best_ships_per_month"].iloc[-1]
        )
        self.assertLess(settled, ns["GA_GENERATIONS"], "The run should settle before its last generation")
        self.assertAlmostEqual(
            ga["possible_choices_log10"], sum(math.log10(count) for count in ga["designs_per_planet"]), delta=0.006
        )
        # The result is a fixed point of the polish: no planet can add ships, or drop a level, on its own.
        again = ns["polish_configuration"](model, best, ns["smallest_limit"], ns["HILL_CLIMB_STEP_SIZES"])
        self.assertEqual(again.tolist(), best.tolist())
        # The algorithm's choice is at least as good as the best choice that gives all generic planets one design.
        design_limits, design_counts = ns["catalogue_limits"](model, catalogues)
        uniform = (
            design_limits[0, : design_counts[0], None, :] + 10.0 * design_limits[1, None, : design_counts[1], :]
        ).min(axis=2)
        self.assertGreaterEqual(ga["ga_ships_per_month"], round(float(uniform.max()), 2))
        # Its run is the run of the meta parameters: the same settings and seed give the same history.
        rerun = ns["run_genetic_algorithm"](
            design_limits,
            design_counts,
            population_size=ns["GA_POPULATION_SIZE"],
            generations=ns["GA_GENERATIONS"],
            tournament_size=ns["GA_TOURNAMENT_SIZE"],
            crossover_rate=ns["GA_CROSSOVER_RATE"],
            mutation_rate=ns["GA_MUTATION_RATE"],
            elite_count=ns["GA_ELITE_COUNT"],
            seed=ns["GA_RANDOM_SEED"],
        )
        self.assertEqual(rerun[0].tolist(), chosen.tolist())
        pd.testing.assert_frame_equal(rerun[1], history)
        # The polish ran on the model of the last round, with the reserve that this round started from.
        last_reserve = rounds.iloc[-1][[f"{resource}_reserved" for resource in ns["RESOURCE_KEYS"]]].to_numpy(
            dtype=float
        )
        round_model = ns["PRODUCTION_MODEL"].with_central_reserve(last_reserve)
        self.assertEqual(
            best.tolist(),
            ns["polish_both_ways"](round_model, ns["ga_levels_arr"], ns["HILL_CLIMB_STEP_SIZES"]).tolist(),
        )

        # Limits: the bottleneck is used in full, and the result is its number of ships.
        limits = ns["best_config_limits_df"].set_index("limit")
        self.assertEqual(list(limits.index), ["metal", "kryptonite", "spice", "shipyards"])
        self.assertEqual(limits["needed_per_ship"].tolist(), [3e6, 2e6, 5e5, 1.0])
        totals = model.simulate(best[None])["planet_limits"][0].sum(axis=0)
        np.testing.assert_allclose(limits["ships_per_month"], totals, atol=0.006)
        np.testing.assert_allclose(limits["available_per_month"], totals * [3e6, 2e6, 5e5, 1.0], atol=0.06)
        self.assertEqual(limits["is_bottleneck"].tolist(), (totals == totals.min()).tolist())
        self.assertEqual(int(limits["is_bottleneck"].sum()), 1)
        self.assertEqual(summary["bottleneck"], limits.index[limits["is_bottleneck"]][0])
        self.assertEqual(limits.loc[summary["bottleneck"], "used_percent"], 100.0)
        self.assertTrue((limits["used_percent"] <= 100.0).all())
        np.testing.assert_allclose(limits["used_percent"], 100.0 * ships / totals, atol=0.006)

        # Buildings: optimized levels from the search, fixed levels from the plan, and never more than the fields.
        buildings = ns["best_config_buildings_df"]
        self.assertEqual(buildings[list(model.optimized_keys)].values.tolist(), best.tolist())
        building_keys = [f"b{number}" for number in range(1, 22)]
        self.assertEqual(list(buildings.columns[-21:]), building_keys)
        self.assertEqual(buildings["fields_used"].tolist(), buildings[building_keys].sum(axis=1).tolist())
        self.assertTrue((buildings["fields_used"] <= buildings["fields_max"]).all())
        self.assertEqual(
            (buildings["fields_max"] - buildings["fields_used"]).tolist(), buildings["fields_free"].tolist()
        )
        self.assertEqual(buildings.loc[0, ["b7", "b8", "b10"]].tolist(), [12, 12, 5])
        self.assertEqual(buildings.loc[1, ["b7", "b8", "b10"]].tolist(), [12, 0, 5])
        # The warehouses are the ones that the model gives the designs.
        warehouse_levels = model.simulate(best[None])["planet_warehouse_levels"][0]
        self.assertEqual(buildings[["b11", "b12", "b13", "b14"]].values.tolist(), warehouse_levels.tolist())
        self.assertTrue((warehouse_levels[0] >= 15).all(), "The central planet keeps its minimum level")
        self.assertTrue((warehouse_levels[1:, 0] >= 17).all(), "Mines of level 45 and more need large warehouses")
        self.assertEqual(list(buildings["coords"]), [plan["coords"] for plan in plans])
        self.assertEqual(
            (summary["fields_used"], summary["fields_free"]),
            (int(buildings["fields_used"].sum()), int(buildings["fields_free"].sum())),
        )
        # Every planet can build its fleet, and at least one can build the starbase.
        self.assertTrue((buildings["b9"] >= 6).all())
        self.assertTrue((buildings["b9"] >= 13).any())

        # Planets: the model's numbers, the share of the ships each shipyard builds, and the cost of each planet.
        planets = ns["best_config_planets_df"]
        outcome = {name: values[0] for name, values in model.simulate(best[None]).items()}
        np.testing.assert_allclose(planets["power_ratio"], outcome["planet_power_ratio"], atol=6e-5)
        np.testing.assert_allclose(planets["smelting_ratio"], outcome["planet_smelting_ratio"], atol=6e-5)
        np.testing.assert_allclose(planets["energy_produced"], outcome["planet_energy_produced"], atol=0.06)
        np.testing.assert_allclose(planets["energy_used"], outcome["planet_energy_used"], atol=0.06)
        for column, resource in enumerate(("pig_iron", "metal", "kryptonite", "spice")):
            np.testing.assert_allclose(
                planets[f"{resource}_per_hour"], outcome["planet_net_per_hour"][:, column], atol=0.06
            )
        np.testing.assert_allclose(planets["shipyard_ships_per_month"], outcome["planet_limits"][:, 3], atol=0.006)
        self.assertAlmostEqual(float(planets["ships_built_per_month"].sum()), ships, delta=0.06)
        self.assertTrue((planets["ships_built_per_month"] <= planets["shipyard_ships_per_month"] + 0.006).all())
        self.assertEqual(
            (planets["ships_built_per_month"] > 0).tolist(), (planets["shipyard_ships_per_month"] > 0).tolist()
        )
        self.assertEqual(planets["warehouse_fields"].tolist(), warehouse_levels.sum(axis=1).tolist())
        self.assertEqual(summary["warehouse_fields"], int(warehouse_levels.sum()))
        self.assertEqual(summary["central_planet"], "02:380:12 (slot 1)")
        # A generic planet starts from nothing: every level of its plan must be built and paid for.
        generic_levels = buildings.loc[1, building_keys].to_dict()
        self.assertEqual(planets.loc[1, "levels_to_build"], sum(generic_levels.values()))
        expected_cost = sum(
            game_data.spice_equivalent(game_data.building_cost(int(key[1:]), level))
            for key, top in generic_levels.items()
            for level in range(1, top + 1)
        )
        self.assertAlmostEqual(planets.loc[1, "upgrade_cost_spice_eq"], expected_cost, delta=0.006)
        # The colony already has Fusion plant 7 and Development center 1: only the levels above count.
        home_levels = buildings.loc[0, building_keys].to_dict()
        built = plans[0]["built_levels"]
        self.assertEqual((built["b5"], built["b7"]), (7, 1))
        self.assertEqual(
            planets.loc[0, "levels_to_build"], sum(max(0, level - built[key]) for key, level in home_levels.items())
        )
        self.assertLess(planets.loc[0, "levels_to_build"], sum(home_levels.values()))
        home_cost = sum(
            game_data.spice_equivalent(game_data.building_cost(int(key[1:]), level))
            for key, top in home_levels.items()
            for level in range(built[key] + 1, top + 1)
        )
        self.assertAlmostEqual(planets.loc[0, "upgrade_cost_spice_eq"], home_cost, delta=0.006)
        # The levels that the colony has already built are worth more than that tolerance.
        self.assertGreater(
            sum(
                game_data.spice_equivalent(game_data.building_cost(int(key[1:]), level))
                for key, top in home_levels.items()
                for level in range(1, min(built[key], top) + 1)
            ),
            1000.0,
        )
        self.assertEqual(planets["fleet_cost_spice_eq"].tolist(), [124000.0] * 11)

        cost = ns["building_upgrade_cost_spice_eq"]
        self.assertEqual(
            cost("b1", 2, 4),
            sum(game_data.spice_equivalent(game_data.building_cost(1, level)) for level in (3, 4)),
        )
        self.assertEqual((cost("b1", 4, 4), cost("b1", 9, 4)), (0.0, 0.0))
        research_cost = ns["research_upgrade_cost_spice_eq"]
        energy_levels = game_data.RESEARCH_BY_ID["r2"]["levels"]
        self.assertEqual(
            research_cost("r2", 1, 3),
            sum(game_data.spice_equivalent(energy_levels[level]["cost"]) for level in (2, 3)),
        )
        self.assertEqual(research_cost("r2", 3, 3), 0.0)

        # Summary: the investment is the three costs, set against a month of the finished configuration's output.
        self.assertAlmostEqual(
            summary["buildings_cost_spice_eq"], float(planets["upgrade_cost_spice_eq"].sum()), delta=0.006
        )
        self.assertEqual(summary["fleet_cost_spice_eq"], 11 * 124000.0)
        planned, own = ns["PLANNED_RESEARCH_LEVELS"], ns["colony_researches"]
        self.assertAlmostEqual(
            summary["research_cost_spice_eq"],
            sum(research_cost(key, own.get(key, 0), level) for key, level in planned.items()),
            delta=0.006,
        )
        self.assertGreater(summary["research_cost_spice_eq"], research_cost("r2", 1, 10))
        self.assertAlmostEqual(
            summary["investment_spice_eq"],
            summary["buildings_cost_spice_eq"] + summary["fleet_cost_spice_eq"] + summary["research_cost_spice_eq"],
            delta=0.02,
        )
        self.assertAlmostEqual(summary["ship_cost_spice_eq"], 3e6 / 2.5 + 2e6 / 3.0 + 5e5, delta=0.006)
        self.assertAlmostEqual(summary["monthly_output_spice_eq"], ships * (3e6 / 2.5 + 2e6 / 3.0 + 5e5), delta=1.0)
        self.assertAlmostEqual(
            summary["investment_in_months_of_output"],
            summary["investment_spice_eq"] / summary["monthly_output_spice_eq"],
            delta=0.006,
        )
        self.assertEqual(summary["levels_to_build"], int(planets["levels_to_build"].sum()))
        self.assertAlmostEqual(
            summary["pig_iron_left_over_per_month"],
            720.0 * float(outcome["planet_net_per_hour"][:, 0].sum()),
            delta=40.0,
        )
        tables = ns["BEST_CONFIG_TABLES"]
        self.assertEqual(
            list(tables),
            ["best_config_limits", "best_config_buildings", "best_config_planets", "best_config_warehouses"],
        )
        for table_name, table in tables.items():
            self.assertIs(table, ns[f"{table_name}_df"], f"{table_name} must be the {table_name}_df table itself")

        # A configuration that builds nothing still describes itself: no ships, no shares, no payback.
        idle_tables = ns["build_best_configuration_tables"](model, plans, model.min_levels)
        self.assertEqual(len(idle_tables), 4)
        # Only a planet whose shipyard builds the starbase stores the cost of one: Weapons factory 13 does, 12 does not.
        with_yards = model.min_levels.copy()
        with_yards[1, model.building_index["b9"]], with_yards[2, model.building_index["b9"]] = 13, 12
        yard_rows = ns["build_best_configuration_tables"](model, plans, with_yards)[3]
        yard_rows = yard_rows.set_index(["slot_index", "resource"])
        self.assertEqual(
            yard_rows.loc[(2, "metal"), ["largest_cost_of", "level", "set_by"]].tolist(), ["s16", 5, "cost of s16"]
        )
        self.assertEqual(yard_rows.loc[(3, "metal"), ["largest_cost_of", "level"]].tolist(), ["b7 L12", 4])
        self.assertNotIn("s16", set(yard_rows.drop(index=2, level="slot_index")["largest_cost_of"]))
        self.assertEqual(float(idle_tables[1]["ships_built_per_month"].sum()), 0.0)
        self.assertEqual(idle_tables[2]["used_percent"].tolist(), [0.0] * 4)
        idle_summary = ns["summarize_best_configuration"](model, model.min_levels, *idle_tables[:3], own, planned)
        self.assertEqual((idle_summary["ships_per_month"], idle_summary["investment_in_months_of_output"]), (0.0, None))
        self.assertEqual(idle_summary["bottleneck"], "metal, kryptonite, spice, shipyards")

        # Warehouses: one row per planet and resource, with the level that every rule asks for.
        warehouses = ns["best_config_warehouses_df"]
        self.assertEqual(len(warehouses), 11 * 4)
        self.assertEqual(warehouses["slot_index"].tolist(), [slot for slot in range(1, 12) for _ in range(4)])
        self.assertEqual(warehouses["resource"].tolist(), ["pig_iron", "metal", "kryptonite", "spice"] * 11)
        self.assertEqual(warehouses["key"].tolist(), ["b11", "b12", "b13", "b14"] * 11)
        self.assertEqual(warehouses["level"].tolist(), warehouse_levels.ravel().tolist())
        self.assertEqual(
            warehouses["capacity"].tolist(), [float(100_000 * 2**level) for level in warehouse_levels.ravel()]
        )
        self.assertEqual(set(warehouses["built_level"]), {0})
        rule_columns = [
            "level_for_largest_cost",
            "level_for_own_production",
            "level_for_empire_production",
            "minimum_level",
        ]
        # The level is what the most demanding rule asks for: no warehouse is larger or smaller than it has to be.
        self.assertEqual(warehouses[rule_columns].max(axis=1).tolist(), warehouses["level"].tolist())
        net = model.simulate(best[None])["planet_net_per_hour"][0]
        for (_, row), planet_net in zip(warehouses.iterrows(), net.ravel()):
            self.assertEqual(row["level_for_own_production"], self._warehouse_level_for(24.0 * max(0.0, planet_net)))
            self.assertEqual(row["level_for_largest_cost"], self._warehouse_level_for(row["largest_cost"]))
            if row["set_by"].startswith("cost of "):
                self.assertEqual(row["set_by"], f"cost of {row['largest_cost_of']}")
                self.assertEqual(row["level_for_largest_cost"], row["level"])
            self.assertEqual(row["level"] > 0, row["set_by"] != "nothing to store")
        # The largest cost is the cost of the level or ship that the table names.
        for _, row in warehouses.iterrows():
            key, _, level = row["largest_cost_of"].partition(" L")
            if key.startswith("b"):
                self.assertEqual(
                    row["largest_cost"], float(game_data.building_cost(int(key[1:]), int(level))[row["resource"]])
                )
        # The whole table is the rules written out, planet by planet.
        for planet, plan in enumerate(plans):
            design = dict(zip(model.optimized_keys, best[planet].tolist()))
            expected = self._expected_warehouses(
                plan,
                design,
                net[planet],
                hours=24.0,
                ship_cost=game_data.SHIPS[ns["SHIP_SLUG_BY_KEY"]["s16"]]["base_cost"] if design["b9"] >= 13 else None,
                reserve=tuple(model.central_reserve) if planet == 0 else (0.0,) * 4,
            )
            self.assertEqual(warehouse_levels[planet].tolist(), expected, plan["coords"])
        # The central planet: its reserve is what the result produces in 24 hours, and its minimum level is 15.
        np.testing.assert_allclose(model.central_reserve, 24.0 * np.maximum(net.sum(axis=0), 0.0), rtol=1e-12)
        np.testing.assert_allclose(ns["central_reserve_needed"](model, best), model.central_reserve, rtol=1e-12)
        central = warehouses[warehouses["slot_index"] == 1].set_index("resource")
        self.assertEqual(
            central["level_for_empire_production"].tolist(),
            [self._warehouse_level_for(amount) for amount in model.central_reserve],
        )
        self.assertEqual(central["minimum_level"].tolist(), [15] * 4)
        self.assertEqual(central.loc["spice", ["level", "set_by"]].tolist(), [15, "central planet minimum"])
        self.assertTrue(central["level_for_empire_production"].max() > 0)
        others = warehouses[warehouses["slot_index"] > 1]
        self.assertEqual(set(others["level_for_empire_production"]) | set(others["minimum_level"]), {0})
        # On the shipyard planet the starbase is among the costs that the warehouses hold.
        self.assertTrue(
            set(warehouses["set_by"])
            <= {
                "central planet minimum",
                "own production",
                "empire production",
                "nothing to store",
                *(f"cost of {label}" for label in set(warehouses["largest_cost_of"])),
            }
        )
        self.assertIn("own production", set(warehouses["set_by"]))
        # One round was enough here: the minimum level of the central planet already holds the production.
        self.assertEqual(len(rounds), 1)
        self.assertEqual(rounds.loc[0, "holds_the_production"], True)
        for column, resource in enumerate(ns["RESOURCE_KEYS"]):
            self.assertEqual(rounds.loc[0, f"{resource}_reserved"], 0.0)
            self.assertAlmostEqual(rounds.loc[0, f"{resource}_needed"], model.central_reserve[column], delta=0.06)
            self.assertEqual(rounds.loc[0, f"{resource}_held"], float(100_000 * 2 ** warehouse_levels[0, column]))
            self.assertLessEqual(rounds.loc[0, f"{resource}_needed"], rounds.loc[0, f"{resource}_held"])
        self.assertEqual(rounds.loc[0, "ships_per_month"], summary["ships_per_month"])
        self.assertEqual(list(rounds["round"]), [1])

        # Section 4.5: one table per planet, with every building that the planet has or gets.
        configurations = ns["PLANET_CONFIGURATIONS"]
        labels = list(configurations)
        self.assertEqual(len(labels), 11)
        self.assertRegex(labels[0], r"^slot 1, 02:380:12 \(colonized, \d+ of 230 fields\)$")
        self.assertRegex(labels[10], r"^slot 11, EMPTY:10 \(empty_colony_slot, \d+ of 300 fields\)$")
        self.assertIn(f"{int(buildings.loc[0, 'fields_used'])} of 230 fields", labels[0])
        for index, table in enumerate(configurations.values()):
            targets = dict(zip(table["key"], table["target_level"]))
            row, built_here = buildings.loc[index], plans[index]["built_levels"]
            # Every building with a level today or in the plan is listed, with the level of the plan.
            self.assertEqual(
                targets, {key: int(row[key]) for key in building_keys if row[key] > 0 or built_here[key] > 0}
            )
            self.assertEqual(table["built_level"].tolist(), [built_here[key] for key in table["key"]])
            self.assertEqual(
                table["levels_to_build"].tolist(), [max(0, level - built_here[key]) for key, level in targets.items()]
            )
            self.assertEqual(int(table["levels_to_build"].sum()), planets.loc[index, "levels_to_build"])
            self.assertAlmostEqual(
                float(table["upgrade_cost_spice_eq"].sum()), planets.loc[index, "upgrade_cost_spice_eq"], delta=0.5
            )
            self.assertEqual(set(table["role"]), {"optimized", "fixed", "warehouse"})
            self.assertEqual(
                table.loc[table["role"] == "warehouse", "key"].tolist(),
                [key for key in ("b11", "b12", "b13", "b14") if key in targets],
            )
        home_table = configurations[labels[0]].set_index("key")
        self.assertEqual(home_table.loc["b12", ["role", "built_level"]].tolist(), ["warehouse", 0])
        self.assertEqual(home_table.loc["b12", "target_level"], warehouse_levels[0, 1])
        self.assertEqual(
            home_table.loc["b5", ["building", "role", "built_level"]].tolist(), ["Fusion plant", "optimized", 7]
        )
        self.assertEqual(home_table.loc["b7", ["role", "built_level", "target_level"]].tolist(), ["fixed", 1, 12])
        self.assertGreater(home_table.loc["b5", "upgrade_cost_spice_eq"], 0.0)
        # A building that is neither built nor planned is left out.
        self.assertNotIn("b6", home_table.index)

        # Build times. The last missing level of a building takes the base time that the catalog gives its
        # target level, divided by (Development center + 1) * 2^Microsystem accelerator * universe speed, with
        # the levels that the planet has in the configuration. A building with no level left to build takes none.
        speed = float(ns["PLAN_ACCOUNT_CONTEXT"]["speedup_factor"])
        self.assertGreater(speed, 1.0)

        def catalog_time(key: str, level: int) -> float:
            return float(game_data.BUILDING_BY_ID[int(key[1:])]["levels"][level]["base_time_s"])

        timed, untimed = 0, 0
        for table in configurations.values():
            self.assertEqual(
                list(table.columns),
                [
                    "key",
                    "building",
                    "role",
                    "built_level",
                    "target_level",
                    "levels_to_build",
                    "upgrade_cost_spice_eq",
                    "last_level_build_time_s",
                    "last_level_build_time_text",
                ],
            )
            targets = {key: int(level) for key, level in zip(table["key"], table["target_level"])}
            for _, entry in table.iterrows():
                key = entry["key"]
                if entry["levels_to_build"] == 0:
                    expected = 0.0
                    untimed += 1
                else:
                    # The Development center and the Microsystem accelerator are built at their level before.
                    dev_center = targets["b7"] - (1 if key == "b7" else 0)
                    accelerator = targets.get("b17", 0) - (1 if key == "b17" else 0)
                    expected = catalog_time(key, targets[key]) / ((dev_center + 1) * 2**accelerator * speed)
                    timed += 1
                self.assertEqual(entry["last_level_build_time_s"], round(expected, 2), (key, targets[key]))
                self.assertEqual(entry["last_level_build_time_text"], ns["fmt_duration"](expected))
        self.assertGreater(timed, 100)
        self.assertGreater(untimed, 0)
        # One planet has a Microsystem accelerator: there the same Development center 12 is built that much faster.
        yards = [index for index in range(1, 11) if buildings.loc[index, "b17"] > 0]
        plain = [index for index in range(1, 11) if buildings.loc[index, "b17"] == 0]
        self.assertTrue(yards and plain)
        tables = list(configurations.values())
        yard_table, plain_table = tables[yards[0]].set_index("key"), tables[plain[0]].set_index("key")
        self.assertEqual(
            plain_table.loc["b7", "last_level_build_time_s"],
            round(catalog_time("b7", 12) / (12 * speed), 2),
        )
        self.assertAlmostEqual(
            yard_table.loc["b7", "last_level_build_time_s"],
            catalog_time("b7", 12) / (12 * speed) / 2 ** int(buildings.loc[yards[0], "b17"]),
            delta=0.006,
        )
        self.assertEqual(
            plain_table.loc["b7", "last_level_build_time_text"],
            ns["fmt_duration"](catalog_time("b7", 12) / (12 * speed)),
        )

        # last_level_build_time on explicit levels: Development center 4, Microsystem accelerator 2, speed 5.
        last_level = ns["last_level_build_time"]
        levels = {"b1": 3, "b7": 4, "b17": 2}
        self.assertEqual(last_level("b1", levels, 5.0), catalog_time("b1", 3) / (5 * 4 * 5.0))
        self.assertEqual(last_level("b7", levels, 5.0), catalog_time("b7", 4) / (4 * 4 * 5.0))
        self.assertEqual(last_level("b17", levels, 5.0), catalog_time("b17", 2) / (5 * 2 * 5.0))
        self.assertEqual(last_level("b1", {**levels, "b1": 0}, 5.0), 0.0)
        # Beyond 1e14 seconds the catalog kept the digits of a base time and lost its exponent: Kryptonite mine
        # 68 reads less than 10 seconds. Such a time is rebuilt from the level before and the growth of the
        # cost, and it agrees with the digits that survived.
        mine_levels = game_data.BUILDING_BY_ID[3]["levels"]
        self.assertGreater(catalog_time("b3", 67), 8e13)
        self.assertLess(catalog_time("b3", 68), 10.0)
        rebuilt = last_level("b3", {"b3": 68, "b7": 0, "b17": 0}, 1.0)
        costs = [float(game_data.spice_equivalent(mine_levels[level]["cost"])) for level in (67, 68)]
        self.assertAlmostEqual(rebuilt / (catalog_time("b3", 67) * costs[1] / costs[0]), 1.0, places=12)
        self.assertAlmostEqual(rebuilt / (catalog_time("b3", 68) * 1e14), 1.0, delta=0.001)

        # planet_configuration_table on explicit levels. The home planet has Fusion plant 7: a plan that keeps
        # it there has nothing to build and takes no time; one more level takes the time of level 8.
        table_of = ns["planet_configuration_table"]
        built = plans[0]["built_levels"]
        self.assertEqual(built["b5"], 7)
        kept = table_of(plans[0], dict(built), 2.0).set_index("key")
        self.assertEqual(kept["levels_to_build"].tolist(), [0] * len(kept))
        self.assertEqual(kept["last_level_build_time_s"].tolist(), [0.0] * len(kept))
        self.assertEqual(set(kept["last_level_build_time_text"]), {"0s"})
        raised = table_of(plans[0], {**built, "b5": 8}, 2.0).set_index("key")
        fusion_time = catalog_time("b5", 8) / ((built["b7"] + 1) * 2 ** built["b17"] * 2.0)
        self.assertEqual(raised.loc["b5", "last_level_build_time_s"], round(fusion_time, 2))
        self.assertEqual(raised.loc["b5", "last_level_build_time_text"], ns["fmt_duration"](fusion_time))
        self.assertEqual(raised["last_level_build_time_s"].drop(index="b5").tolist(), [0.0] * (len(raised) - 1))
        # A plan below the level that stands builds nothing either.
        lowered = table_of(plans[0], {**built, "b5": 6}, 2.0).set_index("key")
        self.assertEqual(lowered.loc["b5", ["levels_to_build", "last_level_build_time_s"]].tolist(), [0, 0.0])

    def test_meta_parameters_steer_the_plan(self) -> None:
        default_ns, _, _ = self._attack_run()
        default_summary = default_ns["BEST_CONFIG_SUMMARY"]

        # Run A: lower maximum levels, a fleet with Battleships and Solar cells, more miscellaneous buildings,
        # and a crude search: a grid of only the lowest and the highest level of every building (300 designs
        # allow no more), and a hill-climb that moves by 3 levels at a time.
        caps = {**default_ns["OPTIMIZED_BUILDING_MAX_LEVELS"], "b1": 22, "b2": 20, "b3": 21, "b4": 19, "b5": 25}
        search = {
            "CATALOGUE_GRID_SIZE": 300,
            "CATALOGUE_PRIORITY_STEPS": 2,
            "HILL_CLIMB_STEP_SIZES": (3,),
            "GA_POPULATION_SIZE": 80,
            "GA_GENERATIONS": 60,
            "GA_TOURNAMENT_SIZE": 2,
            "GA_CROSSOVER_RATE": 0.7,
            "GA_MUTATION_RATE": 0.2,
            "GA_ELITE_COUNT": 3,
            "GA_RANDOM_SEED": 5,
        }
        changed = self._rerun_planner(
            default_ns,
            {
                "OPTIMIZED_BUILDING_MAX_LEVELS": caps,
                "MIN_FLEET_PER_PLANET": {"s13": 2, "s9": 50},
                "MISC_BUILDING_LEVELS": {**default_ns["MISC_BUILDING_LEVELS"], "b10": 9, "b15": 1},
                "MISC_BUILDING_LEVELS_BY_SLOT": {2: {"b8": 7}, 3: {"b18": 1}},
                **search,
            },
        )
        buildings = changed["best_config_buildings_df"]
        # No optimized building exceeds its maximum, the Blast furnaces reach theirs, and far fewer ships are built.
        for key, cap in caps.items():
            self.assertLessEqual(int(buildings[key].max()), cap, key)
        self.assertEqual(int(buildings["b2"].max()), 20)
        self.assertGreater(changed["BEST_CONFIG_SUMMARY"]["ships_per_month"], 0.0)
        self.assertLess(changed["BEST_CONFIG_SUMMARY"]["ships_per_month"], 0.1 * default_summary["ships_per_month"])
        self.assertLess(
            changed["BEST_CONFIG_SUMMARY"]["investment_spice_eq"], 0.01 * default_summary["investment_spice_eq"]
        )
        # With maximum levels this low every planet keeps free fields, and its label in Section 4.5 says how
        # many of its fields it uses.
        self.assertTrue((buildings["fields_used"] < buildings["fields_max"]).all())
        self.assertEqual(
            [label.rsplit(", ", 1)[1] for label in changed["PLANET_CONFIGURATIONS"]],
            [f"{used} of {size} fields)" for used, size in zip(buildings["fields_used"], buildings["fields_max"])],
        )
        # Battleships need Weapons factory 8 on every planet; the Solar cells give every planet energy.
        self.assertEqual(set(changed["plan_planets_df"]["min_optimized_fields"]) - {10}, {8})
        self.assertTrue((buildings["b9"] >= 8).all())
        self.assertEqual(set(changed["plan_planets_df"]["solar_cells"]), {50})
        energy_row = changed["PRODUCTION_FLOW_KEYS"].index("energy_produced")
        np.testing.assert_allclose(
            changed["PRODUCTION_MODEL"].fixed_flows[energy_row], [50 * 37.75 * 1.1] + [50 * 30.0 * 1.1] * 10
        )
        # Each planet lacks 2 Battleships (35,000 Metal, 25,000 Kryptonite) and 50 Solar cells.
        self.assertAlmostEqual(
            changed["BEST_CONFIG_SUMMARY"]["fleet_cost_spice_eq"],
            11 * (2 * (35000 / 2.5 + 25000 / 3.0) + 50 * (2000 / 2.5 + 200 / 3.0 + 400.0)),
            delta=0.02,
        )
        # Fleet base 9 and Particle cannon 1 everywhere, a Research lab 7 on slot 2 only, a Teleporter on
        # slot 3 only, which needs Microsystem accelerator 2 there.
        self.assertEqual(changed["plan_planets_df"]["fixed_fields"].tolist(), [22, 29, 23] + [22] * 8)
        self.assertEqual(buildings["b10"].tolist(), [9] * 11)
        self.assertEqual(buildings["b15"].tolist(), [1] * 11)
        self.assertEqual(buildings["b8"].tolist(), [0, 7] + [0] * 9)
        self.assertEqual(buildings["b18"].tolist(), [0, 0, 1] + [0] * 8)
        self.assertGreaterEqual(int(buildings.loc[2, "b17"]), 2)
        self.assertEqual(changed["plan_planets_df"]["min_optimized_fields"].tolist(), [8, 8, 10] + [8] * 8)
        self.assertEqual(changed["PRODUCTION_MODEL"].twin_planets, [0, 1, 2] + [3] * 8)
        # The research of the plan covers these buildings too: the Particle cannon of every planet needs
        # Shield technology 5 and Ion technology 5, the Teleporter of slot 3 Hyperspace technology 6.
        required = changed["PLAN_REQUIRED_RESEARCH"]
        self.assertEqual({key: required[key] for key in ("r9", "r12", "r14")}, {"r9": 5, "r12": 5, "r14": 6})
        self.assertEqual({key for key in ("r9", "r12", "r14") if key in default_ns["PLAN_REQUIRED_RESEARCH"]}, set())
        self.assertEqual(changed["PLANNED_RESEARCH_LEVELS"]["r14"], 6)
        # The only Research lab stands on slot 2 now: that planet pays for the research, and its warehouses
        # hold the dearest research level, Hyperspace technology 6.
        self.assertEqual(changed["plan_planets_df"]["pays_for_research"].tolist(), [False, True] + [False] * 9)
        self.assertEqual(changed["plan_planets_df"]["is_central_planet"].tolist(), [True] + [False] * 10)
        lab_warehouses = changed["best_config_warehouses_df"]
        lab_warehouses = lab_warehouses[lab_warehouses["slot_index"] == 2].set_index("resource")
        self.assertEqual(
            lab_warehouses.loc["metal", ["largest_cost_of", "set_by"]].tolist(), ["r14 L6", "cost of r14 L6"]
        )
        self.assertEqual(
            lab_warehouses.loc["metal", "largest_cost"],
            float(game_data.RESEARCH_BY_ID["r14"]["levels"][6]["cost"]["metal"]),
        )
        self.assertGreater(lab_warehouses.loc["metal", "level"], 4)
        # The search ran with the changed settings: 10 priority mixes and the all-rounder at most, and the
        # history of 61 generations of 80 chromosomes is the one these settings and this seed produce.
        self.assertLessEqual(max(changed["GA_SUMMARY"]["designs_per_planet"]), 11)
        self.assertLess(
            max(changed["GA_SUMMARY"]["designs_per_planet"]), max(default_ns["GA_SUMMARY"]["designs_per_planet"])
        )
        self.assertEqual(changed["GA_SUMMARY"]["search_rounds"], 1)
        self.assertEqual(changed["GA_SUMMARY"]["chromosomes_evaluated"], 80 * 61)
        model = changed["PRODUCTION_MODEL"]
        catalogues = changed["build_design_catalogues"](model, grid_size=300, priority_steps=2, step_sizes=(3,))
        self.assertEqual([c.tolist() for c in catalogues], [c.tolist() for c in changed["DESIGN_CATALOGUES"]])
        rerun = changed["run_genetic_algorithm"](
            *changed["catalogue_limits"](model, catalogues),
            population_size=80,
            generations=60,
            tournament_size=2,
            crossover_rate=0.7,
            mutation_rate=0.2,
            elite_count=3,
            seed=5,
        )
        pd.testing.assert_frame_equal(rerun[1], changed["ga_history_df"])
        self.assertEqual(rerun[0].tolist(), changed["ga_designs_arr"].tolist())
        self.assertEqual(
            changed["best_levels_arr"].tolist(),
            changed["polish_both_ways"](model, changed["ga_levels_arr"], (3,)).tolist(),
        )

        def steps_of_three(levels: np.ndarray, lowest: np.ndarray, highest: np.ndarray) -> np.ndarray:
            """True where a level is a multiple of 3 away from the lowest or the highest level of its building."""
            return ((levels - lowest) % 3 == 0) | ((highest - levels) % 3 == 0)

        # From a grid of lowest and highest levels, steps of 3 lead nowhere else: neither in the catalogues
        # nor in the polished result. The default search, with its finer grid and steps, does get elsewhere.
        for planet, catalogue in enumerate(changed["DESIGN_CATALOGUES"]):
            self.assertTrue(steps_of_three(catalogue, model.min_levels[planet], model.max_levels[planet]).all())
        self.assertTrue(steps_of_three(changed["best_levels_arr"], model.min_levels, model.max_levels).all())
        default_model = default_ns["PRODUCTION_MODEL"]
        self.assertFalse(
            steps_of_three(
                default_ns["DESIGN_CATALOGUES"][1], default_model.min_levels[1], default_model.max_levels[1]
            ).all()
        )
        self.assertFalse(
            steps_of_three(default_ns["best_levels_arr"], default_model.min_levels, default_model.max_levels).all()
        )

        # Run B: no fleet, Development center 5 only, and Battleships over a month of 15 days.
        other = self._rerun_planner(
            default_ns,
            {
                "MIN_FLEET_PER_PLANET": {},
                "MISC_BUILDING_LEVELS": {**default_ns["MISC_BUILDING_LEVELS"], "b7": 5},
                "TARGET_SHIP_KEY": "s13",
                "PRODUCTION_MONTH_DAYS": 15.0,
            },
        )
        # No Weapons factory is forced on any planet, and nothing is missing from a fleet.
        self.assertEqual(set(other["plan_planets_df"]["min_optimized_fields"]), {0})
        self.assertEqual(other["BEST_CONFIG_SUMMARY"]["fleet_cost_spice_eq"], 0.0)
        self.assertNotIn("r5", other["PLAN_REQUIRED_RESEARCH"])
        # Without Development center 12 the Microsystem accelerator is locked everywhere.
        self.assertEqual(set(other["best_config_buildings_df"]["b17"]), {0})
        level_rows = other["plan_building_levels_df"]
        self.assertEqual(set(level_rows.loc[level_rows["key"] == "b17", "locked_by"]), {"b7 L12"})
        self.assertEqual(set(level_rows.loc[level_rows["key"] == "b17", "max_level"]), {0})
        # The Battleship costs Metal and Kryptonite, needs Weapons factory 8, and is counted over 360 hours.
        self.assertEqual(other["BEST_CONFIG_SUMMARY"]["target_ship"], "Battleship")
        self.assertEqual(other["BEST_CONFIG_SUMMARY"]["hours_per_month"], 360.0)
        self.assertEqual(list(other["best_config_limits_df"]["limit"]), ["metal", "kryptonite", "shipyards"])
        self.assertEqual(other["best_config_limits_df"]["needed_per_ship"].tolist(), [35000.0, 25000.0, 1.0])
        # A Weapons factory stands only where it builds Battleships: at level 8 or more, or not at all.
        factories = other["best_config_buildings_df"]["b9"]
        self.assertTrue((factories >= 8).any())
        self.assertTrue(((factories == 0) | (factories >= 8)).all())
        self.assertEqual(other["PLAN_REQUIRED_RESEARCH"]["r6"], 4)
        self.assertNotIn("r15", other["PLAN_REQUIRED_RESEARCH"])
        self.assertGreater(other["BEST_CONFIG_SUMMARY"]["ships_per_month"], default_summary["ships_per_month"])
        self.assertAlmostEqual(
            other["BEST_CONFIG_SUMMARY"]["ship_cost_spice_eq"], 35000 / 2.5 + 25000 / 3.0, delta=0.006
        )

        # Run C: half the universe maximum. Mining technology 27 and Energy technology 21 are known there.
        universe_ns, _, _ = self._cached_run(self.universe_workspace)
        half = self._rerun_planner(universe_ns, {"RESEARCH_PERCENT_OF_UNIVERSE_MAX": 50.0})
        self.assertEqual((half["PLANNED_RESEARCH_LEVELS"]["r1"], half["PLANNED_RESEARCH_LEVELS"]["r2"]), (14, 11))
        full = half["plan_research_levels"](
            half["UNIVERSE_RESEARCH_MAX_LEVELS"], half["colony_researches"], half["PLAN_REQUIRED_RESEARCH"], 100.0
        ).set_index("key")
        self.assertEqual((full.loc["r1", "planned_level"], full.loc["r2", "planned_level"]), (27, 21))
        self.assertEqual(half["planned_research_df"].set_index("key").loc["r1", "set_by"], "universe maximum")
        self.assertEqual(
            (half["PLAN_ACCOUNT_CONTEXT"]["r1_mining_tech"], half["PLAN_ACCOUNT_CONTEXT"]["r2_energy_tech"]), (14, 11)
        )
        # Mining technology 14 adds 28 % to the Kryptonite of a mine, Energy technology 11 adds 11 % to the energy.
        design = np.array([[[0, 0, 20, 0, 30, 6, 0]]])
        outcome = {name: values[0, 0] for name, values in half["PRODUCTION_MODEL"].simulate(design, [1]).items()}
        self.assertEqual(outcome["planet_power_ratio"], 1.0)
        self.assertAlmostEqual(
            outcome["planet_net_per_hour"][2],
            5.0 * 1.28 * game_data.BUILDING_BY_ID[3]["levels"][20]["prod"],
            delta=1e-6,
        )
        self.assertAlmostEqual(
            outcome["planet_energy_produced"], 1.11 * game_data.BUILDING_BY_ID[5]["levels"][30]["energy"], delta=1e-6
        )

        # Run D: the Microsystem accelerator as a miscellaneous building, at level 2 on slot 1 and nowhere else.
        fixed_accelerator = self._rerun_planner(
            default_ns,
            {
                "OPTIMIZED_BUILDING_MAX_LEVELS": {key: 50 for key in ("b1", "b2", "b3", "b4", "b5", "b9")},
                "MISC_BUILDING_LEVELS": {**default_ns["MISC_BUILDING_LEVELS"], "b17": 0},
                "MISC_BUILDING_LEVELS_BY_SLOT": {1: {"b8": 12, "b17": 2}},
                **search,
            },
        )
        model = fixed_accelerator["PRODUCTION_MODEL"]
        self.assertEqual(model.optimized_keys, ("b1", "b2", "b3", "b4", "b5", "b9"))
        self.assertEqual(fixed_accelerator["best_levels_arr"].shape, (11, 6))
        self.assertEqual(fixed_accelerator["best_config_buildings_df"]["b17"].tolist(), [2] + [0] * 10)
        self.assertGreater(fixed_accelerator["BEST_CONFIG_SUMMARY"]["ships_per_month"], 0.0)
        # With Weapons factory 13 on every planet, the shipyard of slot 1 is four times as fast as the others.
        factories = np.zeros((1, 11, 6), dtype=np.int64)
        factories[0, :, model.building_index["b9"]] = 13
        shipyards = model.simulate(factories)["planet_limits"][0, :, -1]
        self.assertGreater(shipyards[1], 0.0)
        np.testing.assert_allclose(shipyards[1:], shipyards[0] / 4.0, rtol=1e-12)

    def test_warehouse_parameters_steer_the_plan(self) -> None:
        default_ns, _, _ = self._attack_run()
        default_summary = default_ns["BEST_CONFIG_SUMMARY"]
        rule_columns = [
            "level_for_largest_cost",
            "level_for_own_production",
            "level_for_empire_production",
            "minimum_level",
        ]

        def consistent(ns: dict, hours: float) -> pd.DataFrame:
            """The warehouses table of a run, checked against the run's own production and reserve."""
            model, best, table = ns["BEST_CONFIG_MODEL"], ns["best_levels_arr"], ns["best_config_warehouses_df"]
            self.assertEqual(model.warehouse_hours, hours)
            outcome = model.simulate(best[None])
            self.assertTrue(model.is_feasible(best[None])[0])
            self.assertEqual(table["level"].tolist(), outcome["planet_warehouse_levels"][0].ravel().tolist())
            self.assertEqual(table[rule_columns].max(axis=1).tolist(), table["level"].tolist())
            net = outcome["planet_net_per_hour"][0]
            self.assertEqual(
                table["level_for_own_production"].tolist(),
                [self._warehouse_level_for(hours * max(0.0, amount)) for amount in net.ravel()],
            )
            np.testing.assert_allclose(model.central_reserve, hours * np.maximum(net.sum(axis=0), 0.0), rtol=1e-12)
            central = table[table["slot_index"] == ns["CENTRAL_PLANET_SLOT"]]
            self.assertEqual(
                central["level_for_empire_production"].tolist(),
                [self._warehouse_level_for(amount) for amount in model.central_reserve],
            )
            self.assertTrue((central["level"] >= central["level_for_empire_production"]).all())
            # The reason named is the first rule, in this order, that asks for the level.
            for _, row in table.iterrows():
                if row["level"] == 0:
                    self.assertEqual(row["set_by"], "nothing to store")
                elif row["level_for_largest_cost"] == row["level"]:
                    self.assertEqual(row["set_by"], f"cost of {row['largest_cost_of']}")
                elif row["level_for_own_production"] == row["level"]:
                    self.assertEqual(row["set_by"], "own production")
                elif row["level_for_empire_production"] == row["level"]:
                    self.assertEqual(row["set_by"], "empire production")
                else:
                    self.assertIn(
                        row["set_by"], ("central planet minimum", "level already built", "MISC_BUILDING_LEVELS")
                    )
            self.assertEqual(
                set(table.loc[table["slot_index"] != ns["CENTRAL_PLANET_SLOT"], "level_for_empire_production"]), {0}
            )
            # The rounds: every round but the last fell short, and each reserved what the one before needed.
            rounds = ns["central_reserve_rounds_df"]
            self.assertEqual(rounds["holds_the_production"].tolist(), [False] * (len(rounds) - 1) + [True])
            self.assertEqual(list(rounds["round"]), list(range(1, len(rounds) + 1)))
            self.assertEqual(ns["GA_SUMMARY"]["search_rounds"], len(rounds))
            last = rounds.iloc[-1]
            for resource, level in zip(ns["RESOURCE_KEYS"], central["level"]):
                # What the last round held is what the central planet's warehouses of the result hold. (A round
                # that reserved a whole warehouse level more than its result needs would hold more. None does here.)
                self.assertEqual(last[f"{resource}_held"], float(100_000 * 2**level))
                self.assertLessEqual(last[f"{resource}_needed"], last[f"{resource}_held"])
            self.assertEqual(last["ships_per_month"], ns["BEST_CONFIG_SUMMARY"]["ships_per_month"])
            for resource in ns["RESOURCE_KEYS"]:
                self.assertEqual(rounds.loc[0, f"{resource}_reserved"], 0.0)
                for index in range(1, len(rounds)):
                    self.assertAlmostEqual(
                        rounds.loc[index, f"{resource}_reserved"],
                        max(rounds.loc[index - 1, f"{resource}_reserved"], rounds.loc[index - 1, f"{resource}_needed"]),
                        delta=0.06,
                    )
            return table

        # No hours: nothing is stored but the cost of what is built. More hours: larger warehouses, fewer ships.
        none = self._rerun_planner(default_ns, {"WAREHOUSE_HOURS_OF_PRODUCTION": 0.0})
        table = consistent(none, 0.0)
        self.assertEqual(set(table["level_for_own_production"]) | set(table["level_for_empire_production"]), {0})
        self.assertEqual(none["BEST_CONFIG_MODEL"].central_reserve.tolist(), [0.0] * 4)
        self.assertEqual(len(none["central_reserve_rounds_df"]), 1)
        ten_days = self._rerun_planner(default_ns, {"WAREHOUSE_HOURS_OF_PRODUCTION": 240.0})
        table = consistent(ten_days, 240.0)
        self.assertIn("own production", set(table["set_by"]))
        self.assertGreater(int(table["level_for_own_production"].max()), 12)
        self.assertLess(none["BEST_CONFIG_SUMMARY"]["warehouse_fields"], default_summary["warehouse_fields"])
        self.assertLess(default_summary["warehouse_fields"], ten_days["BEST_CONFIG_SUMMARY"]["warehouse_fields"])
        self.assertGreaterEqual(none["BEST_CONFIG_SUMMARY"]["ships_per_month"], default_summary["ships_per_month"])
        self.assertLess(ten_days["BEST_CONFIG_SUMMARY"]["ships_per_month"], default_summary["ships_per_month"])
        # Ten days of the empire's production no longer fit the central planet's minimum level of 15: the
        # search needed a second round, which reserved the room.
        self.assertEqual(len(ten_days["central_reserve_rounds_df"]), 2)
        central = table[table["slot_index"] == 1].set_index("resource")
        self.assertGreater(central.loc["metal", "level_for_empire_production"], 15)
        self.assertEqual(central.loc["metal", "set_by"], "empire production")
        self.assertEqual(ten_days["GA_SUMMARY"]["chromosomes_evaluated"], 2 * 400 * 301)

        # No minimum level on the central planet: its warehouses are what its own buildings and the empire's
        # production ask for. That takes two rounds, and leaves the planet more fields for mines.
        no_minimum = self._rerun_planner(default_ns, {"CENTRAL_PLANET_MIN_WAREHOUSE_LEVEL": 0})
        table = consistent(no_minimum, 24.0)
        self.assertEqual(len(no_minimum["central_reserve_rounds_df"]), 2)
        central = table[table["slot_index"] == 1].set_index("resource")
        self.assertEqual(central["minimum_level"].tolist(), [0] * 4)
        self.assertEqual(central.loc["spice", "set_by"], "empire production")
        self.assertLess(int(central.loc["spice", "level"]), 15)
        self.assertEqual(no_minimum["plan_planets_df"]["min_warehouse_fields"].tolist(), [13] + [10] * 10)
        self.assertGreater(no_minimum["BEST_CONFIG_SUMMARY"]["ships_per_month"], default_summary["ships_per_month"])
        # A high minimum: every warehouse of the central planet has it, and fewer ships are built.
        high_minimum = self._rerun_planner(default_ns, {"CENTRAL_PLANET_MIN_WAREHOUSE_LEVEL": 22})
        table = consistent(high_minimum, 24.0)
        central = table[table["slot_index"] == 1]
        self.assertEqual(central["level"].tolist(), [22] * 4)
        self.assertEqual(set(central["set_by"]), {"central planet minimum"})
        self.assertEqual(high_minimum["plan_planets_df"]["min_warehouse_fields"].tolist(), [88] + [10] * 10)
        self.assertLess(high_minimum["BEST_CONFIG_SUMMARY"]["ships_per_month"], default_summary["ships_per_month"])

        # Another central planet: slot 4, a generic planet. It has no twin any more; the colony still pays
        # for the research, because the Research lab stands there.
        moved = self._rerun_planner(default_ns, {"CENTRAL_PLANET_SLOT": 4})
        table = consistent(moved, 24.0)
        self.assertEqual(moved["plan_planets_df"]["is_central_planet"].tolist(), [False] * 3 + [True] + [False] * 7)
        self.assertEqual(moved["plan_planets_df"]["pays_for_research"].tolist(), [True] + [False] * 10)
        self.assertEqual(moved["plan_planets_df"]["min_warehouse_fields"].tolist(), [13, 10, 10, 60] + [10] * 7)
        self.assertEqual(moved["PRODUCTION_MODEL"].twin_planets, [0, 1, 1, 3] + [1] * 7)
        self.assertEqual(moved["PRODUCTION_MODEL"].central_planet, 3)
        self.assertEqual(moved["BEST_CONFIG_SUMMARY"]["central_planet"], "EMPTY:03 (slot 4)")
        self.assertTrue((table.loc[table["slot_index"] == 4, "level"] >= 15).all())
        self.assertEqual(set(table.loc[table["slot_index"] == 4, "minimum_level"]), {15})
        self.assertEqual(set(table.loc[table["slot_index"] == 1, "minimum_level"]), {0})
        with self.assertRaisesRegex(ValueError, "CENTRAL_PLANET_SLOT 12 is not a normal planet of the roster"):
            self._rerun_planner(default_ns, {"CENTRAL_PLANET_SLOT": 12})
        with self.assertRaisesRegex(ValueError, "WAREHOUSE_HOURS_OF_PRODUCTION must be a number of hours of 0 or more"):
            self._rerun_planner(default_ns, {"WAREHOUSE_HOURS_OF_PRODUCTION": -24.0})
        with self.assertRaisesRegex(ValueError, "whole numbers of 0 or more"):
            self._rerun_planner(default_ns, {"CENTRAL_PLANET_MIN_WAREHOUSE_LEVEL": 1.5})

        # A level for the warehouses in MISC_BUILDING_LEVELS is a minimum on every planet, and no more than that.
        floors = {**default_ns["MISC_BUILDING_LEVELS"], "b11": 5, "b12": 5, "b13": 5, "b14": 5}
        with_floors = self._rerun_planner(default_ns, {"MISC_BUILDING_LEVELS": floors})
        table = consistent(with_floors, 24.0)
        self.assertTrue((table["level"] >= 5).all())
        self.assertEqual(set(table.loc[table["slot_index"] > 1, "minimum_level"]), {5})
        self.assertIn("MISC_BUILDING_LEVELS", set(table["set_by"]))
        self.assertGreater(int(table["level"].max()), 15)
        self.assertEqual(with_floors["plan_planets_df"]["min_warehouse_fields"].tolist(), [60] + [20] * 10)
        default_table = default_ns["best_config_warehouses_df"]
        self.assertLess(int(default_table["level"].min()), 5)
        self.assertNotIn("MISC_BUILDING_LEVELS", set(default_table["set_by"]))

    # ------------------------------------------------------------------ printouts

    # ------------------------------------------------------------------ printouts

    def test_every_generated_object_is_printed_in_full(self) -> None:
        ns, stdout, new_names = self._attack_run()
        # The cells that generate data, in notebook order. Code cells 0 and 1 install packages and import modules.
        data_idxs = [
            idx
            for idx in range(2, len(self.code_cells))
            if any(_is_generated_data(name, ns[name]) for name in new_names[idx])
        ]
        self.assertEqual([self.sections[idx] for idx in data_idxs], ["1", *CONFIG_OPTIMIZER_PRINTING_SECTIONS])
        # A cell prints its own data. The meta parameters of Section 1 are the exception: the cell only sets
        # values, and Section 4.1 prints them with the plan.
        print_idx_of = {idx: self.optimizer_idxs[0] if idx == self.parameters_idx else idx for idx in data_idxs}
        for idx in range(1, len(self.code_cells)):
            if self.sections[idx] not in CONFIG_OPTIMIZER_PRINTING_SECTIONS:
                self.assertEqual(stdout[idx], "", f"The cell of Section {self.sections[idx]} must print nothing")

        printed_text = "".join(stdout[idx] for idx in data_idxs)
        self.assertNotIn("...", printed_text, "Printouts must never be truncated")
        self.assertNotRegex(printed_text, r"\de\+\d", "Large numbers must not print in scientific notation")
        self.assertEqual(re.findall(r"sid=(?!REDACTED)", printed_text, flags=re.IGNORECASE), [])

        printed_blocks: dict[str, list[str]] = {}
        section_out: dict[str, str] = {}
        for idx in data_idxs[1:]:
            section = self.sections[idx]
            title_section, blocks = self._parse_printout(stdout[idx])
            self.assertEqual(
                title_section, section, f"The cell of Section {section} titles its printout Section {title_section}"
            )
            section_out[section] = stdout[idx]
            for name, block_lines in blocks.items():
                self.assertNotIn(name, printed_blocks, "Each object is printed exactly once")
                printed_blocks[name] = block_lines
        for idx in data_idxs:
            # Every data name that the cell defined must appear in its printout.
            out = stdout[print_idx_of[idx]]
            section_names = sorted(name for name in new_names[idx] if _is_generated_data(name, ns[name]))
            self.assertGreater(len(section_names), 0)
            for name in section_names:
                self.assertRegex(
                    out,
                    rf"(?<![A-Za-z0-9_]){re.escape(name)}(?![A-Za-z0-9_])",
                    f"{name!r} (generated by code cell {idx}) is missing from the printout of cell {print_idx_of[idx]}",
                )

        # Every DataFrame / ndarray the notebook generated is printed row for row / element for element.
        tables_and_arrays = 0
        for name, value in sorted(ns.items()):
            if name.startswith("_") or not isinstance(value, (pd.DataFrame, np.ndarray)):
                continue
            tables_and_arrays += 1
            self.assertIn(name, printed_blocks, f"{name} is generated but never printed")
            block_lines = printed_blocks[name]
            if isinstance(value, pd.DataFrame):
                self.assertEqual(
                    block_lines[0],
                    f"[{name}] pandas.DataFrame, shape=({value.shape[0]}, {value.shape[1]})",
                )
                if value.empty:
                    self.assertEqual(block_lines[1], f"(no rows) columns: {list(value.columns)}")
                else:
                    self.assertEqual(len(block_lines), 2 + len(value), f"{name} was not printed row for row")
                    self.assertEqual(block_lines[1].split(), [str(col) for col in value.columns])
            else:
                self.assertEqual(
                    block_lines[0],
                    f"[{name}] numpy.ndarray, shape={value.shape}, dtype={value.dtype}",
                )
                tokens = " ".join(block_lines[1:]).replace("[", " ").replace("]", " ").split()
                printed_vals = [float(tok) for tok in tokens]
                self.assertEqual(len(printed_vals), value.size)
                np.testing.assert_allclose(printed_vals, value.astype(np.float64).ravel(), rtol=0.0, atol=1e-6)
        self.assertGreaterEqual(tables_and_arrays, 14)

        # Spot checks: paths, metadata, the graph, the roster, the research table.
        self.assertIn(str(self.attack_workspace.resolve() / "state" / "spaceinvasion.db"), section_out["2.2"])
        self.assertIn(f"[CONFIG_RESULT_FILE_NAME] str = {CONFIG_OPTIMIZER_RESULT_FILE!r}", section_out["2.2"])
        self.assertIn('"max_planets": 11', section_out["2.5"])
        self.assertIn('  "r1": {"name": "Mining technology", ', section_out["2.5"])
        graph = ns["TECH_GRAPH"]
        self.assertIn("[TECH_GRAPH (TechnologyRequirementGraph summary)] dict, len=3", section_out["2.8"])
        self.assertIn(f'"nodes": {len(graph.nodes)}', section_out["2.8"])
        self.assertIn(
            f'"edges": {sum(len(requirements) for requirements in graph.prereqs_of.values())}', section_out["2.8"]
        )
        self.assertIn('"b5": 7', section_out["3.1"])
        self.assertEqual(section_out["3.1"].count('"fields_max": 300'), 10)
        self.assertIn('"coords": "EMPTY:10"', section_out["3.1"])
        self.assertIn("[ACCOUNT_SNAPSHOT] dict, len=2", section_out["3.1"])
        self.assertIn('"universe_speedup_factor": 5.0', section_out["3.1"])
        self.assertIn("[universe_research_df] pandas.DataFrame, shape=(15, 15)", section_out["3.2"])
        # Section 4: the meta parameters of the run, then the plan, the model, and the result in the titles.
        used = _config_optimizer_meta_parameters()
        self.assertTrue(
            section_out["4.1"].split("\n")[1].startswith("Section 4.1 data: meta parameters, planned research")
        )
        self.assertIn(f"[TARGET_SHIP_KEY] str = {used['TARGET_SHIP_KEY']!r}", section_out["4.1"])
        self.assertIn(
            f"[WAREHOUSE_HOURS_OF_PRODUCTION] float = {used['WAREHOUSE_HOURS_OF_PRODUCTION']!r}", section_out["4.1"]
        )
        self.assertIn(f'"GA_RANDOM_SEED": {used["GA_RANDOM_SEED"]}', section_out["4.1"])
        for building, level in used["OPTIMIZED_BUILDING_MAX_LEVELS"].items():
            self.assertIn(f'"{building}": {level}', section_out["4.1"])
        # The parameters are printed before the plan that follows from them.
        self.assertLess(section_out["4.1"].index("[TARGET_SHIP_KEY]"), section_out["4.1"].index("[WAREHOUSE_KEYS]"))
        for name in used:
            self.assertRegex(section_out["4.1"], rf"(?<![A-Za-z0-9_]){name}(?![A-Za-z0-9_])")
        self.assertIn("[PRODUCTION_MODEL (EmpireProductionModel summary)] dict, len=10", section_out["4.2"])
        self.assertRegex(
            section_out["4.4"].split("\n")[1],
            r"^Section 4\.4 data: best configuration, [\d,]+\.\d\d Imperial starbases per month \(bottleneck: \w+\)$",
        )
        self.assertIn(f"{ns['BEST_CONFIG_SUMMARY']['ships_per_month']:,.2f} Imperial starbases", section_out["4.4"])
        # Section 4.5 prints one table per planet, under the label of that planet.
        self.assertIn(f"{ns['BEST_CONFIG_SUMMARY']['ships_per_month']:,.2f} Imperial starbases", section_out["4.5"])
        for label in ns["PLANET_CONFIGURATIONS"]:
            self.assertIn(f"[PLANET_CONFIGURATIONS: {label}] pandas.DataFrame, shape=(", section_out["4.5"])


if __name__ == "__main__":
    unittest.main()
