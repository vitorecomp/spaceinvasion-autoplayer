"""Tests for `notebooks/optimizer_experimentation.ipynb` (F20)."""

from __future__ import annotations

import ast
import contextlib
import gc
import hashlib
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
import types
import unittest
import warnings

from spaceinvasion import game_data
from spaceinvasion.optimizer import run_optimizer
from spaceinvasion.ranking import compute_action_eta
from spaceinvasion.state import (
    BuildingOffer,
    EnergyBalance,
    GameState,
    PlanetState,
    Resources,
)
from spaceinvasion.storage import GameDatabase
from spaceinvasion.strategy import PlannedAction, plan_actions

REPO_ROOT = Path(__file__).resolve().parents[1]
NOTEBOOK_PATH = REPO_ROOT / "notebooks" / "optimizer_experimentation.ipynb"
ATTACK_HISTORY_PATH = REPO_ROOT / "data" / "attack_history.json"
EXPECTED_ATTACK_HISTORY_SHA256 = (
    "51c3bf9611ba3511a3fe7e56f2391e3f005e20523683e453d811466fa0c8f13b"
)
SHIP_SLUG_BY_ID: dict[int, str] = {
    1: "small-transport",
    4: "small-fighter",
    6: "starcruiser",
    9: "solar-cells",
}
FORBIDDEN_IMPORT_NAMES = frozenset(
    {"api_server", "e2e_tests", "selenium", "requests"}
)
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
            unmet = game_data.missing_prerequisites(
                "buildings", int(act.target_id), buildings=bld, researches=res
            )
            test_case.assertEqual(
                unmet,
                [],
                f"Illegal executable BUILD b{act.target_id} with unmet prereqs: {unmet}",
            )
        elif act.kind == "RESEARCH" and act.target_id is not None:
            rkey = f"r{int(act.target_id)}"
            unmet = game_data.missing_prerequisites(
                "research", rkey, buildings=bld, researches=res
            )
            test_case.assertEqual(
                unmet,
                [],
                f"Illegal executable RESEARCH {rkey} with unmet prereqs: {unmet}",
            )
        elif act.kind == "BUILD_SHIP" and act.target_id is not None:
            slug = SHIP_SLUG_BY_ID.get(int(act.target_id), str(act.target_id))
            unmet = game_data.missing_prerequisites(
                "ships", slug, buildings=bld, researches=res
            )
            test_case.assertEqual(
                unmet,
                [],
                f"Illegal executable BUILD_SHIP {slug} with unmet prereqs: {unmet}",
            )


class TestExperimentationNotebook(unittest.TestCase):
    def _load_notebook(self) -> tuple[str, dict]:
        self.assertTrue(
            NOTEBOOK_PATH.is_file(),
            f"Missing experimentation notebook at {NOTEBOOK_PATH}",
        )
        raw = NOTEBOOK_PATH.read_text(encoding="utf-8")
        nb = json.loads(raw)
        self.assertIsInstance(nb, dict)
        return raw, nb

    def _run_notebook_headless(
        self,
        cwd: Path,
        *,
        strip_repo_sys_path: bool = False,
        max_cells: int | None = None,
    ) -> tuple[dict, str]:
        _, nb = self._load_notebook()
        code_cells = [
            c for c in nb.get("cells", []) if c.get("cell_type") == "code"
        ]
        if max_cells is not None:
            code_cells = code_cells[:max_cells]
        self.assertGreater(len(code_cells), 0)

        before_sha = _sha256_bytes(ATTACK_HISTORY_PATH)
        orig_cwd = Path.cwd()
        orig_mpl = os.environ.get("MPLBACKEND")
        orig_sys_path = list(sys.path)
        stdout_buf = io.StringIO()
        ns: dict = {"__name__": "__main__"}
        self._last_exec_sys_path: list[str] = []

        try:
            if strip_repo_sys_path:
                repo_str = str(REPO_ROOT.resolve())
                sys.path[:] = [
                    entry
                    for entry in sys.path
                    if entry not in ("", str(REPO_ROOT), repo_str)
                ]
                self.assertNotIn(repo_str, sys.path)
            os.environ["MPLBACKEND"] = "Agg"
            os.chdir(cwd)

            with warnings.catch_warnings(record=True) as caught:
                warnings.simplefilter("always", ResourceWarning)
                with contextlib.redirect_stdout(stdout_buf):
                    for idx, cell in enumerate(code_cells):
                        src = _cell_source(cell)
                        for ln in src.splitlines():
                            self.assertFalse(
                                ln.lstrip().startswith(("%", "!")),
                                f"Code cell {idx} contains IPython magic: {ln!r}",
                            )
                        tree = ast.parse(src, filename=f"{NOTEBOOK_PATH}#cell_{idx}")
                        bare_file_refs = [
                            node
                            for node in ast.walk(tree)
                            if isinstance(node, ast.Name) and node.id == "__file__"
                        ]
                        self.assertEqual(
                            bare_file_refs,
                            [],
                            f"Code cell {idx} must not reference bare __file__",
                        )
                        if src.strip():
                            code = compile(
                                tree,
                                f"{NOTEBOOK_PATH}#cell_{idx}",
                                "exec",
                            )
                            exec(code, ns, ns)
                gc.collect()
            self._last_exec_sys_path = list(sys.path)
        finally:
            os.chdir(orig_cwd)
            sys.path[:] = orig_sys_path
            if orig_mpl is None:
                os.environ.pop("MPLBACKEND", None)
            else:
                os.environ["MPLBACKEND"] = orig_mpl

        res_warnings = [w for w in caught if issubclass(w.category, ResourceWarning)]
        self.assertEqual(
            res_warnings,
            [],
            f"Unexpected ResourceWarnings during notebook execution: {[str(w.message) for w in res_warnings]}",
        )
        if cwd.resolve() not in (REPO_ROOT.resolve(), (REPO_ROOT / "notebooks").resolve()):
            self.assertEqual(
                _sha256_bytes(ATTACK_HISTORY_PATH),
                before_sha,
                "Headless notebook execution mutated tracked data/attack_history.json",
            )
        else:
            self.assertTrue(ATTACK_HISTORY_PATH.is_file())
        return ns, stdout_buf.getvalue()

    def _assert_expected_namespace(
        self,
        ns: dict,
        stdout_text: str,
        *,
        expected_data_root: Path,
        expect_disk_zero_raids: bool = False,
    ) -> None:
        self.assertNotIn("__file__", ns)
        self.assertEqual(ns["REPO_ROOT"], REPO_ROOT.resolve())
        self.assertEqual(ns["ACTIVE_DATA_ROOT"], expected_data_root.resolve())
        self.assertEqual(len(ns["FEATURE_NAMES"]), 46)
        self.assertEqual(len(ns["vec_cold"]), 46)
        self.assertEqual(len(ns["vec_full"]), 46)
        self.assertTrue(all(math.isfinite(x) for x in ns["vec_cold"]))
        self.assertTrue(all(math.isfinite(x) for x in ns["vec_full"]))

        # Complete attack & telemetry history indices [36..45] must differ materially from cold start.
        for idx in range(36, 45):
            self.assertGreater(
                ns["vec_full"][idx],
                ns["vec_cold"][idx],
                f"Expected vec_full[{idx}] ({ns['FEATURE_NAMES'][idx]}) > vec_cold[{idx}]",
            )
        self.assertNotAlmostEqual(ns["vec_full"][45], ns["vec_cold"][45], places=3)

        # ascii_bar NaN / Inf / non-positive guards.
        empty_bar = "[" + ("." * 20) + "]"
        full_bar = "[" + ("#" * 20) + "]"
        self.assertEqual(ns["ascii_bar"](float("nan"), 10.0), empty_bar)
        self.assertEqual(ns["ascii_bar"](5.0, float("nan")), empty_bar)
        self.assertEqual(ns["ascii_bar"](5.0, float("inf")), empty_bar)
        self.assertEqual(ns["ascii_bar"](float("-inf"), 10.0), empty_bar)
        self.assertEqual(ns["ascii_bar"](-1.0, 10.0), empty_bar)
        self.assertEqual(ns["ascii_bar"](5.0, 0.0), empty_bar)
        self.assertEqual(ns["ascii_bar"](10**400, 10.0), full_bar)
        self.assertEqual(ns["ascii_bar"](10.0, 10**400), empty_bar)
        self.assertEqual(ns["ascii_bar"](10**5000, 10**5000), full_bar)
        self.assertEqual(ns["ascii_bar"](5 * 10**4999, 10**5000), "[" + ("#" * 10) + ("." * 10) + "]")
        self.assertEqual(ns["ascii_bar"](-(10**400), 10.0), empty_bar)
        self.assertEqual(ns["ascii_bar"](10**400, float("inf")), empty_bar)
        self.assertEqual(ns["ascii_bar"](float("inf"), 10.0), empty_bar)
        self.assertEqual(ns["ascii_bar"](True, 10.0), empty_bar)
        self.assertEqual(ns["_safe_float"](10**400), 0.0)
        self.assertEqual(ns["_safe_float"](10**5000, -1.0), -1.0)
        self.assertEqual(ns["_safe_float"]("9" * 5000, 3.0), 3.0)
        self.assertEqual(ns["_safe_int"](str(10**18 + 123456789)), 10**18 + 123456789)
        self.assertEqual(ns["_safe_int"]("9" * 5000, -7), -7)
        self.assertIn("~1e5000", ns["render_table"](["Col"], [[10**5000]]))

        # Prerequisite integrity on default exp_state offers.
        exp_state = ns["exp_state"]
        exp_p0 = exp_state.planets[0]
        exp_r_by_key = {o.research_key: o for o in exp_state.research_offers}
        for rk in ("r1", "r4", "r8"):
            self.assertFalse(exp_r_by_key[rk].can_research)
            self.assertEqual(exp_r_by_key[rk].status_text, "Locked")
        self.assertTrue(exp_r_by_key["r2"].can_research)
        self.assertEqual(exp_r_by_key["r2"].status_text, "Ready")
        exp_s_by_id = {o.unit_id: o for o in exp_p0.ship_offers}
        for sid in (1, 6):
            self.assertFalse(exp_s_by_id[sid].can_build)
            self.assertEqual(exp_s_by_id[sid].status_text, "Locked")
        _assert_action_prereqs_legal(self, exp_state, ns["prio_actions"])
        _assert_action_prereqs_legal(self, exp_state, ns["ga_actions"])

        train_stats = ns["train_stats"]
        self.assertLess(train_stats.final_loss, train_stats.initial_loss)
        self.assertGreater(train_stats.weight_delta_norm, 0.0)

        summary = ns["summary"]
        self.assertGreaterEqual(len(summary.generation_history), 5)
        self.assertGreaterEqual(summary.best_fitness, summary.baseline_fitness)

        # Non-tautological ETA assertions on probe_eta, priority, ga_nn, and disk_history_ga_nn.
        probe_eta = ns["probe_eta"]
        self.assertEqual(probe_eta["wait_eta"], "32.3h")
        self.assertEqual(probe_eta["eta_bottleneck"], "pig_iron")
        self.assertEqual(probe_eta["duration_text"], "11d 1h")
        self.assertEqual(probe_eta["total_eta"], "12.4d")

        eta_plans = ns["eta_plans"]
        self.assertIn("priority", eta_plans)
        self.assertIn("ga_nn", eta_plans)
        self.assertIn("disk_history_ga_nn", eta_plans)

        prio_builds = [a for a in eta_plans["priority"] if a.kind == "BUILD"]
        self.assertTrue(
            any(a.duration_seconds > 0.0 and a.duration_text != "Instant" for a in prio_builds),
            f"Expected at least one priority BUILD action with non-Instant duration; got {prio_builds}",
        )
        self.assertTrue(
            any(
                (a.wait_seconds or 0.0) > 0.0
                and a.wait_eta != "Ready"
                and bool(a.eta_bottleneck)
                for a in eta_plans["priority"]
            ),
            f"Expected at least one waiting action with bottleneck in priority plan; got {eta_plans['priority']}",
        )

        ga_attacks = [a for a in eta_plans["ga_nn"] if a.kind == "ATTACK"]
        self.assertGreater(len(ga_attacks), 0)
        self.assertEqual([a.target_name for a in ga_attacks], ["02:380:04 (TargetAlpha)"])
        self.assertEqual(ga_attacks[0].wait_eta, "Ready")
        self.assertEqual(ga_attacks[0].duration_text, "Instant")
        self.assertEqual(ga_attacks[0].total_eta, "Ready")

        for mode, ref_state in (
            ("priority", exp_state),
            ("ga_nn", exp_state),
            ("disk_history_ga_nn", ns["base_state_no_hist"]),
        ):
            self.assertGreater(len(eta_plans[mode]), 0, f"Empty eta_plans[{mode!r}]")
            for act in eta_plans[mode]:
                expected_eta = compute_action_eta(ref_state, act)
                self.assertEqual(act.wait_eta, expected_eta["wait_eta"], f"{mode}: {act}")
                self.assertEqual(act.duration_text, expected_eta["duration_text"], f"{mode}: {act}")
                self.assertEqual(act.total_eta, expected_eta["total_eta"], f"{mode}: {act}")
                self.assertEqual(act.eta_bottleneck, expected_eta["eta_bottleneck"], f"{mode}: {act}")
                self.assertAlmostEqual(
                    act.duration_seconds,
                    expected_eta["duration_seconds"],
                    places=3,
                    msg=f"{mode}: {act}",
                )
                if expected_eta["wait_seconds"] is None:
                    self.assertIsNone(act.wait_seconds, f"{mode}: {act}")
                    self.assertIsNone(act.total_seconds, f"{mode}: {act}")
                else:
                    self.assertIsNotNone(act.wait_seconds, f"{mode}: {act}")
                    self.assertIsNotNone(act.total_seconds, f"{mode}: {act}")
                    self.assertAlmostEqual(
                        float(act.wait_seconds),
                        float(expected_eta["wait_seconds"]),
                        places=2,
                        msg=f"{mode}: {act}",
                    )
                    self.assertAlmostEqual(
                        float(act.total_seconds),
                        float(expected_eta["total_seconds"]),
                        places=2,
                        msg=f"{mode}: {act}",
                    )

        disk_attacks = [
            a.target_name for a in ns["disk_only_actions"] if a.kind == "ATTACK"
        ]
        if expect_disk_zero_raids:
            self.assertTrue(
                any("02:380:04" in t for t in disk_attacks)
                and any("02:380:05" in t for t in disk_attacks),
                f"Expected disk_only_actions with 0 disk raids to avoid demo_stats leak and raid both 04 & 05; got {disk_attacks}",
            )
        else:
            self.assertGreaterEqual(len(disk_attacks), 1)

        # Hyperparameter sweep diversity & positive GA improvement checks.
        sweep_results = ns["sweep_results"]
        self.assertGreaterEqual(len(sweep_results), 6)
        by_label = {r["label"]: r for r in sweep_results}
        self.assertNotEqual(
            by_label["miner_default"]["top_action"],
            by_label["fleeter_nn_035"]["top_action"],
        )
        self.assertNotAlmostEqual(
            by_label["miner_default"]["best_fitness"],
            by_label["fleeter_nn_035"]["best_fitness"],
            places=2,
        )
        fleeter_rows = [r for r in sweep_results if r["playstyle"] == "fleeter"]
        self.assertGreaterEqual(len(fleeter_rows), 4)
        self.assertTrue(
            any(r["improvement_pct"] > 0.0 for r in fleeter_rows),
            f"Expected at least one fleeter config with improvement_pct > 0; got {fleeter_rows}",
        )
        self.assertGreaterEqual(
            len({round(r["best_fitness"], 3) for r in fleeter_rows}),
            3,
            f"Expected diverse best_fitness across fleeter sweep rows; got {fleeter_rows}",
        )

        self.assertTrue(stdout_text.strip())
        self.assertEqual(
            re.findall(r"sid=([A-Za-z0-9_.-]+)", stdout_text, flags=re.IGNORECASE),
            [],
            f"Captured notebook stdout leaked a sid= token:\n{stdout_text}",
        )

    def _assert_cell_ast_safe(self, src: str, label: str) -> None:
        tree = ast.parse(src, filename=f"{NOTEBOOK_PATH}#{label}")
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
                        dst_names = [
                            n.id for n in ast.walk(dst_node) if isinstance(n, ast.Name)
                        ]
                        self.assertTrue(
                            any(nm.startswith("tmp_") for nm in dst_names),
                            f"shutil.copy2 must copy only into tmp_* dir in {label}",
                        )
                is_dyn_import = (
                    (
                        isinstance(node.func, ast.Name)
                        and node.func.id in ("__import__", "import_module")
                    )
                    or (
                        isinstance(node.func, ast.Attribute)
                        and node.func.attr == "import_module"
                    )
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

    def test_notebook_nbformat_v4_metadata_cells_and_ast(self) -> None:
        _, nb = self._load_notebook()
        self.assertEqual(nb.get("nbformat"), 4)
        self.assertIsInstance(nb.get("nbformat_minor"), int)
        self.assertGreaterEqual(nb["nbformat_minor"], 5)

        meta = nb.get("metadata")
        self.assertIsInstance(meta, dict)
        self.assertIsInstance(meta.get("kernelspec"), dict)
        self.assertIn("name", meta["kernelspec"])
        self.assertIn("display_name", meta["kernelspec"])
        self.assertIsInstance(meta.get("language_info"), dict)
        self.assertEqual(meta["language_info"].get("name"), "python")

        cells = nb.get("cells")
        self.assertIsInstance(cells, list)
        self.assertGreaterEqual(len(cells), 8)

        md_cells = [c for c in cells if c.get("cell_type") == "markdown"]
        code_cells = [c for c in cells if c.get("cell_type") == "code"]
        self.assertGreaterEqual(len(md_cells), 4)
        self.assertGreaterEqual(len(code_cells), 4)

        seen_ids: set[str] = set()
        for idx, cell in enumerate(cells):
            cid = cell.get("id")
            self.assertIsInstance(cid, str, f"Cell {idx} missing string id")
            self.assertTrue(cid.strip())
            self.assertNotIn(cid, seen_ids)
            seen_ids.add(cid)
            self.assertIsInstance(cell.get("metadata"), dict)
            src = _cell_source(cell)
            self.assertTrue(src.strip(), f"Cell {idx} has empty source")
            if cell.get("cell_type") == "code":
                self.assertIn("execution_count", cell)
                self.assertIsNone(cell["execution_count"])
                self.assertEqual(cell.get("outputs"), [])
                for ln in src.splitlines():
                    self.assertFalse(
                        ln.lstrip().startswith(("%", "!")),
                        f"Code cell {idx} uses non-Python magic: {ln!r}",
                    )
                self._assert_cell_ast_safe(src, f"code cell {idx}")

        for bad_import_stmt in (
            "from spaceinvasion import api_server",
            "from spaceinvasion import game_data, api_server as srv",
            "from e2e_tests import harness",
            "from . import selenium",
            "importlib.import_module('spaceinvasion.api_server')",
            "__import__('e2e_tests.harness')",
            "from importlib import import_module\nimport_module('spaceinvasion.api_server')",
            "import importlib\nimportlib.import_module(name='spaceinvasion.api_server')",
            "import importlib\nimportlib.import_module('.harness', 'e2e_tests')",
        ):
            with self.assertRaises(AssertionError):
                self._assert_cell_ast_safe(bad_import_stmt, "ast_guard_self_check")

        code_sources = "\n".join(_cell_source(c) for c in code_cells).lower()
        for required_tok in (
            "load_attack_history_stats",
            "get_raid_stats",
            "get_full_history_summary",
            "encode_empire_state",
            "neuralvaluenetwork",
            "run_optimizer",
            "optimize_plan_actions",
            "attach_action_etas",
            "generation_history",
        ):
            self.assertIn(
                required_tok,
                code_sources,
                f"Notebook code cells missing expected domain workflow token {required_tok!r}",
            )

    def test_hygiene_secrets_and_tracked_data_integrity(self) -> None:
        raw, nb = self._load_notebook()
        self.assertTrue(ATTACK_HISTORY_PATH.is_file())
        hist_raw = ATTACK_HISTORY_PATH.read_text(encoding="utf-8")
        hist_obj = json.loads(hist_raw)
        self.assertIsInstance(hist_obj, dict)
        for req_key in ("version", "overall", "per_target", "history", "telemetry_summary"):
            self.assertIn(req_key, hist_obj)
        self.assertEqual(
            re.findall(r"sid=([A-Za-z0-9_.-]+)", hist_raw, flags=re.IGNORECASE),
            [],
        )

        for rel in ("data/attack_history.json", "notebooks/optimizer_experimentation.ipynb"):
            chk = subprocess.run(
                ["git", "check-ignore", "-q", rel],
                cwd=str(REPO_ROOT),
                check=False,
            )
            self.assertNotEqual(
                chk.returncode,
                0,
                f"Deliverable {rel!r} must not be ignored by .gitignore",
            )

        self.assertEqual(
            re.findall(r"sid=([A-Za-z0-9_.-]+)", raw, flags=re.IGNORECASE),
            [],
            "Notebook JSON must contain zero sid= strings",
        )
        lowered_raw = raw.lower()
        for forbidden in (
            ".browser_profile",
            ".env",
            ".sessions",
            "cookies.json",
            "password",
            "secret=",
        ):
            self.assertNotIn(
                forbidden,
                lowered_raw,
                f"Forbidden hygiene token {forbidden!r} found in notebook JSON",
            )

        for cell in nb.get("cells", []):
            out_json = json.dumps(cell.get("outputs") or [])
            self.assertEqual(out_json, "[]")

    def test_headless_exec_from_repo_root_and_notebooks_cwd(self) -> None:
        ns_root, out_root = self._run_notebook_headless(REPO_ROOT)
        root_zero_raids = int((ns_root.get("disk_overall") or {}).get("attempts", 0) or 0) == 0
        self._assert_expected_namespace(
            ns_root,
            out_root,
            expected_data_root=REPO_ROOT,
            expect_disk_zero_raids=root_zero_raids,
        )

        nb_dir = REPO_ROOT / "notebooks"
        ns_nb, out_nb = self._run_notebook_headless(nb_dir, strip_repo_sys_path=True)
        nb_zero_raids = int((ns_nb.get("disk_overall") or {}).get("attempts", 0) or 0) == 0
        self._assert_expected_namespace(
            ns_nb,
            out_nb,
            expected_data_root=REPO_ROOT,
            expect_disk_zero_raids=nb_zero_raids,
        )
        self.assertIn(str(REPO_ROOT.resolve()), self._last_exec_sys_path)

        # True cold subprocess verification from notebooks/ cwd without PYTHONPATH.
        _, nb = self._load_notebook()
        first_code = next(
            _cell_source(c) for c in nb["cells"] if c.get("cell_type") == "code"
        )
        clean_env = {
            k: v
            for k, v in os.environ.items()
            if k not in ("PYTHONPATH", "PYTHONHOME")
        }
        clean_env["PYTHONDONTWRITEBYTECODE"] = "1"
        proc = subprocess.run(
            [sys.executable, "-W", "error::ResourceWarning", "-c", first_code],
            cwd=str(nb_dir),
            env=clean_env,
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(
            proc.returncode,
            0,
            f"Cold notebooks/ bootstrap failed:\nSTDOUT:\n{proc.stdout}\nSTDERR:\n{proc.stderr}",
        )
        self.assertIn(str(REPO_ROOT.resolve()), proc.stdout)

    def test_build_sample_game_state_prerequisites_and_ga_seed_determinism(self) -> None:
        ns, _ = self._run_notebook_headless(REPO_ROOT, max_cells=4)

        high_res = ns["build_sample_game_state"](
            resources=Resources(500_000, 500_000, 500_000, 500_000)
        )
        r_offers = {o.research_key: o for o in high_res.research_offers}
        for rk in ("r1", "r4", "r8"):
            self.assertFalse(r_offers[rk].can_research)
            self.assertEqual(r_offers[rk].status_text, "Locked")
        self.assertTrue(r_offers["r2"].can_research)
        self.assertEqual(r_offers["r2"].status_text, "Ready")

        s_offers = {o.unit_id: o for o in high_res.planets[0].ship_offers}
        for sid in (1, 6):
            self.assertFalse(s_offers[sid].can_build)
            self.assertEqual(s_offers[sid].status_text, "Locked")
        for sid in (4, 9):
            self.assertTrue(s_offers[sid].can_build)
            self.assertEqual(s_offers[sid].status_text, "Buildable")

        prio_high = plan_actions(high_res, config=ns["prio_cfg"])
        ga_high = plan_actions(high_res, config=ns["ga_cfg"])
        _assert_action_prereqs_legal(self, high_res, prio_high)
        _assert_action_prereqs_legal(self, high_res, ga_high)

        # Determinism check across repeated run_optimizer calls with same ga_seed.
        s1 = run_optimizer(
            ns["exp_state"], config=ns["ga_cfg"], raid_stats=ns["demo_stats"]
        )
        s2 = run_optimizer(
            ns["exp_state"], config=ns["ga_cfg"], raid_stats=ns["demo_stats"]
        )
        self.assertEqual(s1.best_fitness, s2.best_fitness)
        self.assertEqual(s1.nn_final_loss, s2.nn_final_loss)
        self.assertEqual(s1.generation_history, s2.generation_history)
        self.assertEqual(
            [(a.kind, a.planet_id, a.target_id, a.target_name, a.target_level) for a in s1.actions],
            [(a.kind, a.planet_id, a.target_id, a.target_name, a.target_level) for a in s2.actions],
        )

    def test_isolated_temp_workspace_wal_preservation_and_cold_start(self) -> None:
        # Scenario A: populated closed DB + WAL active-writer DB + sanitized JSON
        with tempfile.TemporaryDirectory(prefix="si_nb_populated_") as tmp:
            tmp_path = Path(tmp)
            (tmp_path / "data").mkdir()
            (tmp_path / "state").mkdir()
            db_path = tmp_path / "state" / "spaceinvasion.db"
            secret_sid = "TEST_LEAK_TOKEN_987654"
            hidden_owner = "PrivateOwnerDoNotPrint"

            db = GameDatabase(db_path, legacy_rank_json=None)
            try:
                db.record_state_snapshot(_sample_snapshot_state())
                db.record_action(
                    source="test",
                    command="attack",
                    action=PlannedAction(
                        priority="P2",
                        kind="ATTACK",
                        planet_id=12476,
                        planet_coords="02:380:12",
                        target_name="02:380:04",
                        reason=f"url?sid={secret_sid}",
                        executable_now=True,
                    ),
                    executed=True,
                    dry_run=False,
                    message=f"Dispatched sid={secret_sid}",
                )
                db._conn.execute(
                    """
                    INSERT INTO action_history(
                        ts, source, command, priority, kind, planet_id, planet_coords,
                        target_id, target_name, target_level, executed, dry_run, reason, message
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        1_700_000_050.0,
                        "test",
                        "attack",
                        "P2",
                        "ATTACK",
                        12476,
                        "02:380:12",
                        None,
                        b"\xff\xfeTargetBlob sid=" + secret_sid.encode("ascii"),
                        None,
                        1,
                        0,
                        "blob test",
                        "ok",
                    ),
                )
                db._conn.execute(
                    """
                    INSERT INTO action_history(
                        ts, source, command, priority, kind, planet_id, planet_coords,
                        target_id, target_name, target_level, executed, dry_run, reason, message
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        1_700_000_060.0,
                        "test",
                        "attack",
                        b"\xff\xfeP2 sid=" + secret_sid.encode("ascii"),
                        b"\xff\xfeATTACK sid=" + secret_sid.encode("ascii"),
                        12476,
                        b"\xff\xfe02:380:12 sid=" + secret_sid.encode("ascii"),
                        None,
                        None,
                        None,
                        1,
                        0,
                        "blob non-target cols and null target_name",
                        "ok",
                    ),
                )
                db.record_raid(
                    "02:380:04",
                    attacked_ts=1_700_000_100.0,
                    is_exempt=True,
                    outcome=f"Victory sid={secret_sid}",
                    loot={"pig_iron": 9000, "metal": 4500, "kryptonite": 2000, "spice": 500},
                )
                # Store sensitive owner via SQL spy_targets table to verify notebook omits PII.
                db._conn.execute(
                    """
                    INSERT OR REPLACE INTO spy_targets(
                        coords, owner, planet_name, status_color, is_inactive, is_spacia,
                        pig_iron, metal, kryptonite, spice, fleet_json, defense_json,
                        dug_in, last_scanned_ts, last_spied_ts
                    ) VALUES (?, ?, ?, ?, 1, 0, 1000, 1000, 500, 200, '{}', '{}', 0, 1700000100.0, 1700000100.0)
                    """,
                    ("02:380:04", hidden_owner, "FarmA", "yellow"),
                )
                db._conn.commit()
                hist_payload = db.get_raid_stats()
            finally:
                db.close()

            json_path = tmp_path / "data" / "attack_history.json"
            json_path.write_text(json.dumps(hist_payload), encoding="utf-8")
            before_db_mtime = db_path.stat().st_mtime_ns
            before_db_bytes = db_path.read_bytes()
            before_json_bytes = json_path.read_bytes()

            ns_pop, out_pop = self._run_notebook_headless(tmp_path)
            self._assert_expected_namespace(ns_pop, out_pop, expected_data_root=tmp_path)
            self.assertEqual(db_path.stat().st_mtime_ns, before_db_mtime)
            self.assertEqual(db_path.read_bytes(), before_db_bytes)
            self.assertEqual(json_path.read_bytes(), before_json_bytes)
            self.assertFalse((tmp_path / "state" / "spaceinvasion.db-wal").exists())
            self.assertFalse((tmp_path / "state" / "spaceinvasion.db-shm").exists())
            self.assertEqual(
                ns_pop["live_db_inspection"]["counts"]["raid_history"],
                1,
            )
            recent_actions = ns_pop["live_db_inspection"]["recent_actions_sanitized"]
            self.assertGreaterEqual(len(recent_actions), 2)
            self.assertTrue(
                all(
                    act["target_name"] is None or isinstance(act["target_name"], str)
                    for act in recent_actions
                )
            )
            self.assertTrue(any(act["target_name"] is None for act in recent_actions))
            recent_json = json.dumps(recent_actions)
            self.assertNotIn(secret_sid, recent_json)
            self.assertIn("sid=REDACTED", recent_json)
            self.assertNotIn(secret_sid, out_pop)
            self.assertNotIn(hidden_owner, out_pop)
            self.assertNotIn("TempTestPlayer", out_pop)

            # Also verify active uncheckpointed WAL data is visible read-only without mutating live DB.
            live_writer = GameDatabase(db_path, legacy_rank_json=None)
            try:
                live_writer.record_raid(
                    "02:380:09",
                    attacked_ts=1_700_000_200.0,
                    is_exempt=True,
                    outcome="Victory",
                    loot={"pig_iron": 4000, "metal": 2000, "kryptonite": 1000, "spice": 500},
                )
                ns_wal, out_wal = self._run_notebook_headless(tmp_path)
                self._assert_expected_namespace(ns_wal, out_wal, expected_data_root=tmp_path)
                self.assertEqual(
                    ns_wal["live_db_inspection"]["counts"]["raid_history"],
                    2,
                )
            finally:
                live_writer.close()

        # Scenario B: cold-start completely empty DB + minimal JSON (no telemetry_summary/last_attack)
        with tempfile.TemporaryDirectory(prefix="si_nb_cold_") as tmp:
            tmp_path = Path(tmp)
            (tmp_path / "data").mkdir()
            (tmp_path / "state").mkdir()
            empty_db_path = tmp_path / "state" / "spaceinvasion.db"
            empty_db = GameDatabase(empty_db_path, legacy_rank_json=None)
            empty_db.close()

            minimal_json = tmp_path / "data" / "attack_history.json"
            minimal_json.write_text(
                json.dumps(
                    {
                        "overall": {
                            "attempts": 0,
                            "wins": 0,
                            "losses": 0,
                            "success_rate": 0.0,
                        },
                        "per_target": {},
                        "history": [],
                    }
                ),
                encoding="utf-8",
            )
            before_empty_mtime = empty_db_path.stat().st_mtime_ns
            before_empty_bytes = empty_db_path.read_bytes()

            ns_cold, out_cold = self._run_notebook_headless(tmp_path)
            self._assert_expected_namespace(
                ns_cold,
                out_cold,
                expected_data_root=tmp_path,
                expect_disk_zero_raids=True,
            )
            self.assertEqual(empty_db_path.stat().st_mtime_ns, before_empty_mtime)
            self.assertEqual(empty_db_path.read_bytes(), before_empty_bytes)
            self.assertFalse((tmp_path / "state" / "spaceinvasion.db-wal").exists())
            self.assertFalse((tmp_path / "state" / "spaceinvasion.db-shm").exists())
            self.assertEqual(
                ns_cold["live_db_inspection"]["counts"]["raid_history"],
                0,
            )

    def test_edge_cases_readonly_0444_corrupt_db_and_malformed_json_zero_resource_warnings(
        self,
    ) -> None:
        # 1) 0444 read-only valid SQLite DB
        with tempfile.TemporaryDirectory(prefix="si_nb_ro444_") as tmp:
            tmp_path = Path(tmp)
            (tmp_path / "data").mkdir()
            (tmp_path / "state").mkdir()
            db_path = tmp_path / "state" / "spaceinvasion.db"
            db = GameDatabase(db_path, legacy_rank_json=None)
            try:
                db.record_raid(
                    "02:380:04",
                    attacked_ts=1_700_000_100.0,
                    is_exempt=True,
                    outcome="Victory",
                    loot={"pig_iron": 5000, "metal": 2000, "kryptonite": 1000, "spice": 500},
                )
            finally:
                db.close()
            (tmp_path / "data" / "attack_history.json").write_text("{}", encoding="utf-8")
            db_path.chmod(0o444)
            try:
                before_bytes = db_path.read_bytes()
                ns_ro, _ = self._run_notebook_headless(tmp_path, max_cells=2)
                insp = ns_ro["live_db_inspection"]
                self.assertIsNone(insp["error"])
                self.assertEqual(insp["counts"]["raid_history"], 1)
                self.assertEqual(db_path.stat().st_mode & 0o777, 0o444)
                self.assertEqual(db_path.read_bytes(), before_bytes)
            finally:
                db_path.chmod(0o600)

        # 1b) 0444 read-only valid SQLite DB + 0444 -wal + 0444 -shm uncheckpointed sidecars
        with tempfile.TemporaryDirectory(prefix="si_nb_ro444_wal_") as tmp:
            tmp_path = Path(tmp)
            src_dir = tmp_path / "src_wal"
            src_dir.mkdir()
            (tmp_path / "data").mkdir()
            (tmp_path / "state").mkdir()
            (tmp_path / "data" / "attack_history.json").write_text("{}", encoding="utf-8")

            src_db = src_dir / "spaceinvasion.db"
            live_writer = GameDatabase(src_db, legacy_rank_json=None)
            try:
                live_writer.record_raid(
                    "02:380:04",
                    attacked_ts=1_700_000_100.0,
                    is_exempt=True,
                    outcome="Victory",
                    loot={"pig_iron": 5000, "metal": 2000, "kryptonite": 1000, "spice": 500},
                )
                live_writer.record_raid(
                    "02:380:09",
                    attacked_ts=1_700_000_200.0,
                    is_exempt=True,
                    outcome="Victory",
                    loot={"pig_iron": 4000, "metal": 1500, "kryptonite": 500, "spice": 250},
                )
                self.assertGreater(Path(f"{src_db}-wal").stat().st_size, 0)
                self.assertTrue(Path(f"{src_db}-shm").is_file())
                for suffix in ("", "-wal", "-shm"):
                    shutil.copy2(
                        Path(f"{src_db}{suffix}"),
                        tmp_path / "state" / f"spaceinvasion.db{suffix}",
                    )
            finally:
                live_writer.close()

            ro_files = [
                tmp_path / "state" / f"spaceinvasion.db{suffix}"
                for suffix in ("", "-wal", "-shm")
            ]
            for ro_p in ro_files:
                ro_p.chmod(0o444)
            try:
                before_snapshot = [
                    (p.stat().st_mode & 0o777, p.stat().st_mtime_ns, p.read_bytes())
                    for p in ro_files
                ]
                ns_ro_wal, out_ro_wal = self._run_notebook_headless(tmp_path, max_cells=2)
                insp_wal = ns_ro_wal["live_db_inspection"]
                self.assertIsNone(insp_wal["error"])
                self.assertEqual(insp_wal["counts"]["raid_history"], 2)
                self.assertEqual(insp_wal["raid_stats"]["overall"]["attempts"], 2)
                self.assertTrue(out_ro_wal.strip())
                after_snapshot = [
                    (p.stat().st_mode & 0o777, p.stat().st_mtime_ns, p.read_bytes())
                    for p in ro_files
                ]
                self.assertEqual(after_snapshot, before_snapshot)
            finally:
                for ro_p in ro_files:
                    if ro_p.exists():
                        ro_p.chmod(0o600)

        # 2) Non-SQLite corrupt DB + valid-JSON type mismatches
        with tempfile.TemporaryDirectory(prefix="si_nb_corrupt_") as tmp:
            tmp_path = Path(tmp)
            (tmp_path / "data").mkdir()
            (tmp_path / "state").mkdir()
            corrupt_db = tmp_path / "state" / "spaceinvasion.db"
            corrupt_db.write_bytes(b"NOT_SQLITE_CORRUPT_HEADER_BYTES" * 8)
            json_path = tmp_path / "data" / "attack_history.json"

            type_mismatch_payloads = [
                {"overall": [1, 2], "telemetry_summary": {"planet_snapshots": 123}},
                {
                    "overall": {
                        "attempts": "bad",
                        "wins": "bad",
                        "losses": None,
                        "success_rate": "bad",
                        "total_loot_spice_eq": "nan",
                        "last_attack": ["unexpected"],
                    },
                    "per_target": {"02:380:04": "not-a-dict"},
                    "history": [123, "bad"],
                    "telemetry_summary": {
                        "planet_snapshots": 123,
                        "rank_snapshots": "bad",
                        "action_history": [1, 2],
                        "spy_targets": None,
                        "raid_history": 999,
                    },
                },
            ]
            for payload in type_mismatch_payloads:
                json_path.write_text(json.dumps(payload), encoding="utf-8")
                ns_bad, out_bad = self._run_notebook_headless(tmp_path)
                self.assertTrue(ns_bad["live_db_inspection"]["error"])
                self.assertTrue(out_bad.strip())

        # 2b) Multi-page (>=250 raids) page-2+, truncated, -1B slack, 1B DBs + corrupt -wal sidecar
        with tempfile.TemporaryDirectory(prefix="si_nb_page2bad_") as tmp:
            tmp_path = Path(tmp)
            (tmp_path / "data").mkdir()
            (tmp_path / "state").mkdir()
            (tmp_path / "data" / "attack_history.json").write_text("{}", encoding="utf-8")
            db_path = tmp_path / "state" / "spaceinvasion.db"
            db = GameDatabase(db_path, legacy_rank_json=None)
            try:
                db._conn.executemany(
                    "INSERT INTO raid_history(target_coords, attacked_ts, is_exempt, outcome, loot_json) "
                    "VALUES (?, ?, 1, ?, ?)",
                    [
                        (
                            "02:380:04",
                            1_700_000_000.0 + i,
                            "Victory",
                            '{"pig_iron":5000,"metal":2000,"kryptonite":1000,"spice":500}',
                        )
                        for i in range(250)
                    ],
                )
                db._conn.commit()
            finally:
                db.close()
            orig_raw = db_path.read_bytes()
            self.assertGreaterEqual(len(orig_raw), 12288)

            page2_damaged = bytearray(orig_raw)
            page2_damaged[4096:8192] = b"\xde\xad\xbe\xef" * 1024
            page3_damaged = bytearray(orig_raw)
            page3_damaged[8192:12288] = b"\xca\xfe\xba\xbe" * 1024
            for raw_variant in (
                bytes(page2_damaged),
                bytes(page3_damaged),
                orig_raw[:6000],
                orig_raw[:-1],
                orig_raw[:1],
            ):
                db_path.write_bytes(raw_variant)
                ns_p2, out_p2 = self._run_notebook_headless(tmp_path, max_cells=2)
                insp_p2 = ns_p2["live_db_inspection"]
                self.assertTrue(insp_p2["exists"])
                self.assertIsInstance(insp_p2["error"], str)
                self.assertTrue(insp_p2["error"].strip())
                self.assertEqual(insp_p2["counts"], {})
                self.assertEqual(insp_p2["raid_stats"]["overall"]["attempts"], 0)
                self.assertTrue(out_p2.strip())

            # Corrupt -wal sidecar alongside valid DB: SQLite ignores garbage WAL frames cleanly.
            db_path.write_bytes(orig_raw)
            Path(f"{db_path}-wal").write_bytes(b"corrupt_wal_garbage_bytes" * 16)
            ns_wal_bad, out_wal_bad = self._run_notebook_headless(tmp_path, max_cells=2)
            insp_wal_bad = ns_wal_bad["live_db_inspection"]
            self.assertTrue(insp_wal_bad["exists"])
            self.assertIsNone(insp_wal_bad["error"])
            self.assertEqual(insp_wal_bad["counts"]["raid_history"], 250)
            self.assertEqual(insp_wal_bad["raid_stats"]["overall"]["attempts"], 250)
            self.assertTrue(out_wal_bad.strip())

        # 2b-idx) Index key-parity corruption (PRAGMA quick_check passes, PRAGMA integrity_check fails)
        with tempfile.TemporaryDirectory(prefix="si_nb_idxkey_") as idx_tmp:
            idx_dir = Path(idx_tmp)
            (idx_dir / "data").mkdir()
            (idx_dir / "state").mkdir()
            (idx_dir / "data" / "attack_history.json").write_text("{}", encoding="utf-8")
            idx_db_path = idx_dir / "state" / "spaceinvasion.db"
            idx_db = GameDatabase(idx_db_path, legacy_rank_json=None)
            try:
                idx_db.record_raid(
                    "02:380:77",
                    attacked_ts=1_700_000_000.0,
                    is_exempt=True,
                    outcome="Victory",
                    loot={"pig_iron": 5000, "metal": 2000, "kryptonite": 1000, "spice": 500},
                )
            finally:
                idx_db.close()

            orig_bytes = idx_db_path.read_bytes()
            self.assertEqual(orig_bytes.count(b"02:380:77"), 2)
            idx_pos = orig_bytes.rfind(b"02:380:77")
            self.assertGreater(idx_pos, 0)
            corrupted_bytes = (
                orig_bytes[:idx_pos]
                + b"02:380:99"
                + orig_bytes[idx_pos + len(b"02:380:77") :]
            )
            idx_db_path.write_bytes(corrupted_bytes)

            with contextlib.closing(
                sqlite3.connect(f"file:{idx_db_path}?mode=ro", uri=True)
            ) as chk_conn:
                self.assertEqual(chk_conn.execute("PRAGMA quick_check;").fetchall(), [("ok",)])
                ic_chk = chk_conn.execute("PRAGMA integrity_check;").fetchall()
                self.assertEqual(len(ic_chk), 1)
                self.assertIn("missing from index idx_raid_history_coords_ts", ic_chk[0][0])

            ns_idx, out_idx = self._run_notebook_headless(idx_dir, max_cells=2)
            insp_idx = ns_idx["live_db_inspection"]
            self.assertTrue(insp_idx["exists"])
            self.assertIsInstance(insp_idx["error"], str)
            self.assertTrue(insp_idx["error"].strip())
            self.assertIn("idx_raid_history_coords_ts", insp_idx["error"])
            self.assertEqual(insp_idx["counts"], {})
            self.assertEqual(insp_idx["raid_stats"]["overall"]["attempts"], 0)
            self.assertTrue(out_idx.strip())

        # 2c) Incompatible-schema SQLite DBs + valid legacy spy_targets
        legacy_spy_ddl = """
            CREATE TABLE spy_targets(
                coords TEXT PRIMARY KEY,
                owner TEXT NOT NULL DEFAULT '',
                is_inactive INTEGER NOT NULL DEFAULT 0,
                is_spacia INTEGER NOT NULL DEFAULT 0,
                points INTEGER,
                pig_iron INTEGER NOT NULL DEFAULT 0,
                metal INTEGER NOT NULL DEFAULT 0,
                kryptonite INTEGER NOT NULL DEFAULT 0,
                spice INTEGER NOT NULL DEFAULT 0,
                fleet_json TEXT NOT NULL DEFAULT '{}',
                defense_json TEXT NOT NULL DEFAULT '{}',
                dug_in INTEGER NOT NULL DEFAULT 0,
                last_scanned_ts REAL NOT NULL
            );
            INSERT INTO spy_targets(coords, owner, is_inactive, is_spacia, pig_iron, metal, kryptonite, spice, last_scanned_ts)
            VALUES ('02:380:04', 'LegacyOwner', 1, 0, 5000, 3000, 1000, 500, 1700000000.0);
        """
        bad_ddls = (
            "CREATE VIEW spy_targets AS SELECT 1 AS coords;",
            "CREATE VIEW Spy_Targets AS SELECT 1 AS coords;",
            (
                "CREATE VIEW rank_snapshots AS "
                "SELECT 1 AS id, 0.0 AS ts, NULL AS universe_id, '' AS nickname, "
                "NULL AS server_points, NULL AS server_rank, 0.0 AS estimated_points, "
                "0.0 AS pts_per_hr, 0.0 AS spice_value, 1 AS planets;"
            ),
            "CREATE TABLE raid_history(wrong INT);",
            "CREATE TABLE spy_targets(wrong INT);",
            "CREATE TABLE meta(wrong INT);",
            "CREATE TABLE idx_rank_snapshots_ts(a INT);",
            "CREATE VIRTUAL TABLE meta USING fts5(key, value, updated_ts);",
            "CREATE VIRTUAL TABLE raid_history USING fts5(id, target_coords, attacked_ts, is_exempt, outcome, loot_json);",
            "CREATE VIRTUAL TABLE rank_snapshots USING fts5(id, ts, universe_id, nickname, server_points, server_rank, estimated_points, pts_per_hr, spice_value, planets);",
            (
                "CREATE TABLE meta(key TEXT PRIMARY KEY, value TEXT NOT NULL, updated_ts REAL NOT NULL);"
                "CREATE TRIGGER meta_abort BEFORE INSERT ON meta "
                "BEGIN SELECT RAISE(ABORT, 'blocked'); END;"
            ),
            (
                "CREATE TABLE parent(id TEXT PRIMARY KEY); "
                "INSERT INTO parent VALUES ('__si_probe__'); "
                "CREATE TABLE meta(key TEXT PRIMARY KEY REFERENCES parent(id), "
                "value TEXT NOT NULL, updated_ts REAL NOT NULL);"
            ),
            (
                "CREATE TABLE meta(key TEXT PRIMARY KEY, value TEXT NOT NULL, updated_ts REAL NOT NULL); "
                "CREATE TRIGGER t1 BEFORE INSERT ON meta WHEN NEW.key = 'schema_version' "
                "BEGIN SELECT RAISE(ABORT, 'blocked sv'); END;"
            ),
            (
                "CREATE TABLE meta(key TEXT PRIMARY KEY, value TEXT NOT NULL, updated_ts REAL NOT NULL); "
                "INSERT INTO meta VALUES ('__si_probe__', '1', 0.0); "
                "CREATE TRIGGER t2 AFTER INSERT ON meta "
                "BEGIN SELECT RAISE(ABORT, 'after blocked'); END;"
            ),
            (
                "CREATE TABLE meta(key TEXT PRIMARY KEY, value TEXT NOT NULL, updated_ts REAL NOT NULL); "
                "CREATE TRIGGER t3 AFTER INSERT ON meta WHEN NEW.key = 'schema_version' "
                "BEGIN SELECT RAISE(ABORT, 'after sv blocked'); END;"
            ),
            (
                "CREATE TABLE spy_targets("
                "coords TEXT PRIMARY KEY, owner TEXT NOT NULL DEFAULT '', "
                "PLANET_NAME TEXT NOT NULL DEFAULT '', "
                "is_inactive INTEGER NOT NULL DEFAULT 0, is_spacia INTEGER NOT NULL DEFAULT 0, "
                "points INTEGER, pig_iron INTEGER NOT NULL DEFAULT 0, "
                "metal INTEGER NOT NULL DEFAULT 0, kryptonite INTEGER NOT NULL DEFAULT 0, "
                "spice INTEGER NOT NULL DEFAULT 0, fleet_json TEXT NOT NULL DEFAULT '{}', "
                "defense_json TEXT NOT NULL DEFAULT '{}', dug_in INTEGER NOT NULL DEFAULT 0, "
                "last_scanned_ts REAL NOT NULL);"
            ),
        )
        for bad_ddl in bad_ddls:
            with tempfile.TemporaryDirectory(prefix="si_nb_badschema_") as tmp:
                tmp_path = Path(tmp)
                (tmp_path / "data").mkdir()
                (tmp_path / "state").mkdir()
                (tmp_path / "data" / "attack_history.json").write_text("{}", encoding="utf-8")
                bad_db_path = tmp_path / "state" / "spaceinvasion.db"
                with contextlib.closing(sqlite3.connect(bad_db_path)) as conn:
                    conn.executescript(bad_ddl)
                    conn.commit()
                ns_sch, out_sch = self._run_notebook_headless(tmp_path, max_cells=2)
                insp_sch = ns_sch["live_db_inspection"]
                self.assertTrue(insp_sch["exists"])
                self.assertIsInstance(insp_sch["error"], str)
                self.assertTrue(insp_sch["error"].strip())
                self.assertEqual(insp_sch["counts"], {})
                self.assertEqual(insp_sch["raid_stats"]["overall"]["attempts"], 0)
                self.assertTrue(out_sch.strip())

        with tempfile.TemporaryDirectory(prefix="si_nb_legacyspy_") as tmp:
            tmp_path = Path(tmp)
            (tmp_path / "data").mkdir()
            (tmp_path / "state").mkdir()
            (tmp_path / "data" / "attack_history.json").write_text("{}", encoding="utf-8")
            leg_db_path = tmp_path / "state" / "spaceinvasion.db"
            with contextlib.closing(sqlite3.connect(leg_db_path)) as conn:
                conn.executescript(legacy_spy_ddl)
                conn.commit()
                ns_leg, out_leg = self._run_notebook_headless(tmp_path, max_cells=2)
            insp_leg = ns_leg["live_db_inspection"]
            self.assertTrue(insp_leg["exists"])
            self.assertIsNone(insp_leg["error"])
            self.assertEqual(insp_leg["counts"]["spy_targets"], 1)
            self.assertTrue(out_leg.strip())

        # 2c-trigger) Benign AFTER UPDATE trigger on `meta` must pass preflight cleanly (M21)
        with tempfile.TemporaryDirectory(prefix="si_nb_benigntrig_") as tmp:
            tmp_path = Path(tmp)
            (tmp_path / "data").mkdir()
            (tmp_path / "state").mkdir()
            (tmp_path / "data" / "attack_history.json").write_text("{}", encoding="utf-8")
            trig_db_path = tmp_path / "state" / "spaceinvasion.db"
            trig_db = GameDatabase(trig_db_path, legacy_rank_json=None)
            try:
                trig_db._conn.execute(
                    "CREATE TRIGGER benign_meta_audit AFTER UPDATE ON meta BEGIN SELECT 1; END;"
                )
                trig_db._conn.commit()
            finally:
                trig_db.close()
            ns_trig, out_trig = self._run_notebook_headless(tmp_path, max_cells=2)
            insp_trig = ns_trig["live_db_inspection"]
            self.assertTrue(insp_trig["exists"])
            self.assertIsNone(insp_trig["error"])
            self.assertTrue(out_trig.strip())

        # 2d) Schema-valid DB with weird column values (atomic out staging + 0 ResourceWarnings)
        weird_sql_cases = (
            (
                "INSERT INTO tutorial_progress(step, total_steps, tasks_json, reward_text, can_complete, first_seen_ts, last_seen_ts, completed_ts) "
                "VALUES (1, 11, '{bad', '5000 Pig iron', 0, 1700000000.0, 1700000000.0, NULL);"
                "INSERT INTO raid_history(target_coords, attacked_ts, is_exempt, outcome, loot_json) "
                "VALUES ('02:380:04', 1700000100.0, 1, 'Victory', '{\"pig_iron\":5000}');"
            ),
            (
                "INSERT INTO spy_targets(coords, owner, planet_name, status_color, is_inactive, is_spacia, pig_iron, metal, kryptonite, spice, fleet_json, defense_json, dug_in, last_scanned_ts, last_spied_ts) "
                "VALUES ('02:380:04', 'TargetA', 'FarmA', 'yellow', 1, 0, 1000, 1000, 500, 200, 'not-json', '{}', 0, 1700000100.0, 1700000100.0);"
                "INSERT INTO raid_history(target_coords, attacked_ts, is_exempt, outcome, loot_json) "
                "VALUES ('02:380:04', 1700000100.0, 1, 'Victory', '{\"pig_iron\":5000}');"
            ),
            (
                "INSERT INTO raid_history(target_coords, attacked_ts, is_exempt, outcome, loot_json) "
                "VALUES ('02:380:04', 1700000100.0, 1, 'Victory', '{\"pig_iron\": 1e400}');"
            ),
        )
        for weird_sql in weird_sql_cases:
            with tempfile.TemporaryDirectory(prefix="si_nb_weirdcol_") as tmp:
                tmp_path = Path(tmp)
                (tmp_path / "data").mkdir()
                (tmp_path / "state").mkdir()
                (tmp_path / "data" / "attack_history.json").write_text("{}", encoding="utf-8")
                weird_db_path = tmp_path / "state" / "spaceinvasion.db"
                db = GameDatabase(weird_db_path, legacy_rank_json=None)
                try:
                    db._conn.executescript(weird_sql)
                    db._conn.commit()
                finally:
                    db.close()
                ns_w, out_w = self._run_notebook_headless(tmp_path, max_cells=2)
                insp_w = ns_w["live_db_inspection"]
                self.assertTrue(insp_w["exists"])
                self.assertIsNone(insp_w["error"])
                self.assertEqual(insp_w["counts"].get("raid_history"), 1)
                self.assertTrue(out_w.strip())

        with tempfile.TemporaryDirectory(prefix="si_nb_nulltask_") as tmp:
            tmp_path = Path(tmp)
            (tmp_path / "data").mkdir()
            (tmp_path / "state").mkdir()
            (tmp_path / "data" / "attack_history.json").write_text("{}", encoding="utf-8")
            null_db_path = tmp_path / "state" / "spaceinvasion.db"
            with contextlib.closing(sqlite3.connect(null_db_path)) as conn:
                conn.executescript(
                    "CREATE TABLE tutorial_progress("
                    "step INTEGER PRIMARY KEY, total_steps INTEGER NOT NULL, tasks_json TEXT, "
                    "reward_text TEXT NOT NULL, can_complete INTEGER NOT NULL, "
                    "first_seen_ts REAL NOT NULL, last_seen_ts REAL NOT NULL, completed_ts REAL);"
                    "CREATE TABLE raid_history("
                    "id INTEGER PRIMARY KEY AUTOINCREMENT, target_coords TEXT NOT NULL, "
                    "attacked_ts REAL NOT NULL, is_exempt INTEGER NOT NULL DEFAULT 0, "
                    "outcome TEXT NOT NULL DEFAULT '', loot_json TEXT NOT NULL DEFAULT '{}');"
                    "INSERT INTO tutorial_progress(step, total_steps, tasks_json, reward_text, can_complete, first_seen_ts, last_seen_ts, completed_ts) "
                    "VALUES (1, 11, NULL, '5000 Pig iron', 0, 1700000000.0, 1700000000.0, NULL);"
                    "INSERT INTO raid_history(target_coords, attacked_ts, is_exempt, outcome, loot_json) "
                    "VALUES ('02:380:04', 1700000100.0, 1, 'Victory', '{\"pig_iron\":5000}');"
                )
                conn.commit()
            ns_nt, out_nt = self._run_notebook_headless(tmp_path, max_cells=2)
            insp_nt = ns_nt["live_db_inspection"]
            self.assertTrue(insp_nt["exists"])
            self.assertIsNone(insp_nt["error"])
            self.assertEqual(insp_nt["counts"].get("raid_history"), 1)
            self.assertEqual(insp_nt["raid_stats"]["overall"]["attempts"], 1)
            self.assertTrue(out_nt.strip())

        # 3) 0-byte DB + malformed JSON ("{bad")
        with tempfile.TemporaryDirectory(prefix="si_nb_zerobad_") as tmp:
            tmp_path = Path(tmp)
            (tmp_path / "data").mkdir()
            (tmp_path / "state").mkdir()
            (tmp_path / "state" / "spaceinvasion.db").write_bytes(b"")
            (tmp_path / "data" / "attack_history.json").write_text("{bad", encoding="utf-8")
            ns_zb, _ = self._run_notebook_headless(tmp_path)
            self.assertTrue(ns_zb["live_db_inspection"]["exists"])
            self.assertIsNone(ns_zb["live_db_inspection"]["error"])
            self.assertEqual(ns_zb["disk_overall"], {})

        # 4) Missing attack_history.json with valid populated DB
        with tempfile.TemporaryDirectory(prefix="si_nb_nojson_") as tmp:
            tmp_path = Path(tmp)
            (tmp_path / "state").mkdir()
            db_path = tmp_path / "state" / "spaceinvasion.db"
            db = GameDatabase(db_path, legacy_rank_json=None)
            try:
                db.record_state_snapshot(_sample_snapshot_state())
            finally:
                db.close()
            ns_nj, _ = self._run_notebook_headless(tmp_path)
            self.assertIsNone(ns_nj["attack_history_sha256"])
            self.assertIsNone(ns_nj["live_db_inspection"]["error"])
            self.assertEqual(ns_nj["live_db_inspection"]["counts"]["planet_snapshots"], 1)

        # 5) 0000 unreadable attack_history.json and 0000 unreadable spaceinvasion.db (non-root)
        if os.geteuid() != 0:
            with tempfile.TemporaryDirectory(prefix="si_nb_perm0000_") as tmp:
                tmp_path = Path(tmp)
                (tmp_path / "data").mkdir()
                (tmp_path / "state").mkdir()
                unreadable_json = tmp_path / "data" / "attack_history.json"
                unreadable_json.write_text('{"overall":{"attempts":3}}', encoding="utf-8")
                unreadable_db = tmp_path / "state" / "spaceinvasion.db"
                db = GameDatabase(unreadable_db, legacy_rank_json=None)
                try:
                    db.record_state_snapshot(_sample_snapshot_state())
                finally:
                    db.close()
                unreadable_json.chmod(0o000)
                unreadable_db.chmod(0o000)
                try:
                    ns_perm, out_perm = self._run_notebook_headless(tmp_path, max_cells=2)
                    self.assertIsNone(ns_perm["attack_history_sha256"])
                    self.assertEqual(ns_perm["disk_overall"], {})
                    insp_perm = ns_perm["live_db_inspection"]
                    self.assertTrue(insp_perm["exists"])
                    self.assertIsInstance(insp_perm["error"], str)
                    self.assertTrue(insp_perm["error"].strip())
                    self.assertEqual(insp_perm["counts"], {})
                    self.assertTrue(out_perm.strip())
                finally:
                    unreadable_json.chmod(0o600)
                    unreadable_db.chmod(0o600)

    def test_optional_matplotlib_mocked_backend_guard_and_close(self) -> None:
        _, nb = self._load_notebook()
        code_cells = [
            _cell_source(c) for c in nb.get("cells", []) if c.get("cell_type") == "code"
        ]

        class _FakeAxis:
            def __init__(self) -> None:
                self.plotted: list[tuple] = []

            def plot(self, *args, **kwargs) -> None:
                self.plotted.append((args, kwargs))

            def set_xlabel(self, label: str) -> None:
                pass

            def set_ylabel(self, label: str) -> None:
                pass

            def set_title(self, title: str) -> None:
                pass

            def legend(self) -> None:
                pass

        def _make_fake_mpl(backend: str = "Agg") -> tuple[types.ModuleType, types.ModuleType, dict]:
            state_log: dict = {"use_calls": [], "figs": [], "closed": [], "show_calls": 0}
            fake_mpl = types.ModuleType("matplotlib")
            fake_plt = types.ModuleType("matplotlib.pyplot")

            def _use(name: str, **_kw) -> None:
                state_log["use_calls"].append(name)

            def _get_backend() -> str:
                return backend

            def _subplots(**_kw):
                fig = object()
                state_log["figs"].append(fig)
                return fig, _FakeAxis()

            def _show() -> None:
                state_log["show_calls"] += 1

            def _close(fig=None) -> None:
                state_log["closed"].append(fig)

            fake_mpl.use = _use  # type: ignore[attr-defined]
            fake_mpl.get_backend = _get_backend  # type: ignore[attr-defined]
            fake_mpl.pyplot = fake_plt  # type: ignore[attr-defined]
            fake_plt.subplots = _subplots  # type: ignore[attr-defined]
            fake_plt.show = _show  # type: ignore[attr-defined]
            fake_plt.close = _close  # type: ignore[attr-defined]
            return fake_mpl, fake_plt, state_log

        # Part A: Headless mocked matplotlib sets HAS_MATPLOTLIB=True and closes fig.
        orig_mods = {
            k: sys.modules.get(k)
            for k in ("matplotlib", "matplotlib.pyplot", "ipykernel")
        }
        orig_mpl_env = os.environ.pop("MPLBACKEND", None)
        orig_cwd = Path.cwd()
        try:
            fake_mpl, fake_plt, log_a = _make_fake_mpl("Agg")
            sys.modules["matplotlib"] = fake_mpl
            sys.modules["matplotlib.pyplot"] = fake_plt
            sys.modules.pop("ipykernel", None)
            os.chdir(REPO_ROOT)
            ns_a: dict = {"__name__": "__main__"}
            with contextlib.redirect_stdout(io.StringIO()):
                for idx in range(4):
                    exec(compile(code_cells[idx], f"mpl_a_{idx}", "exec"), ns_a, ns_a)
            self.assertTrue(ns_a["HAS_MATPLOTLIB"])
            self.assertEqual(log_a["use_calls"], ["Agg"])
            self.assertEqual(len(log_a["figs"]), 1)
            self.assertEqual(log_a["closed"], log_a["figs"])
            self.assertEqual(log_a["show_calls"], 0)

            # Part B: Interactive ipykernel inline backend is NOT overwritten and calls plt.show().
            os.environ.pop("MPLBACKEND", None)
            fake_mpl_b, fake_plt_b, log_b = _make_fake_mpl("module://matplotlib_inline.backend_inline")
            sys.modules["matplotlib"] = fake_mpl_b
            sys.modules["matplotlib.pyplot"] = fake_plt_b
            sys.modules["ipykernel"] = types.ModuleType("ipykernel")
            ns_b: dict = {"__name__": "__main__"}
            with contextlib.redirect_stdout(io.StringIO()):
                for idx in range(4):
                    exec(compile(code_cells[idx], f"mpl_b_{idx}", "exec"), ns_b, ns_b)
            self.assertTrue(ns_b["HAS_MATPLOTLIB"])
            self.assertEqual(log_b["use_calls"], [])
            self.assertNotIn("MPLBACKEND", os.environ)
            self.assertEqual(log_b["show_calls"], 1)
            self.assertEqual(log_b["closed"], log_b["figs"])
        finally:
            os.chdir(orig_cwd)
            for mod_name, orig_val in orig_mods.items():
                if orig_val is None:
                    sys.modules.pop(mod_name, None)
                else:
                    sys.modules[mod_name] = orig_val
            if orig_mpl_env is None:
                os.environ.pop("MPLBACKEND", None)
            else:
                os.environ["MPLBACKEND"] = orig_mpl_env

    def test_r10_cell3_helpers_reject_arabic_indic_decode_bytes_and_allow_nan_false(self) -> None:
        _, nb = self._load_notebook()
        self.assertEqual((nb.get("nbformat"), nb.get("nbformat_minor")), (4, 5))
        for cell in nb.get("cells", []):
            if cell.get("cell_type") == "code":
                self.assertEqual(cell.get("outputs"), [])
                self.assertIsNone(cell.get("execution_count"))

        cell3_src = _cell_source(nb["cells"][3])
        self.assertIn("bkey[1:].isascii() and bkey[1:].isdigit()", cell3_src)
        self.assertIn("allow_nan=False", cell3_src)

        tree = ast.parse(cell3_src)
        helpers = [
            n
            for n in tree.body
            if isinstance(n, ast.FunctionDef) and n.name in ("_safe_float", "_safe_int")
        ]
        self.assertEqual(len(helpers), 2)
        mod = ast.Module(body=helpers, type_ignores=[])
        ns: dict = {"Any": object, "math": math}
        exec(compile(mod, "cell3_helpers", "exec"), ns, ns)
        sf = ns["_safe_float"]
        si = ns["_safe_int"]

        self.assertEqual(sf(b"2.5"), 2.5)
        self.assertEqual(sf("\u0662.\u0665"), 0.0)
        self.assertEqual(sf(frozenset({1})), 0.0)
        self.assertEqual(si(b"42"), 42)
        self.assertEqual(si("\u0664\u0662"), 0)
        self.assertEqual(si("inf"), 0)
        self.assertEqual(si(float("nan")), 0)
        self.assertEqual(si(frozenset({1})), 0)
        self.assertEqual(si(10**5000), 10**5000)


if __name__ == "__main__":
    unittest.main()
