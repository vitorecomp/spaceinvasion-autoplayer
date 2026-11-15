"""Parity, golden reproduction, Plan v4, and Section 4 standards tests for core/model and planner/ai."""

from __future__ import annotations

import ast
import dataclasses
import importlib.util
from pathlib import Path
import sys
from types import ModuleType

import pytest

from backend.core import model as new_model
from backend.core.config import StrategyConfig
from backend.planner import ai as new_ai
from backend.planner.graph import build_ai_planner_graph
from tests.goldens.scenarios import AI_PLANNER_PARAMS, GOLDEN_EPOCH, STATES, ai_target_config

REPO_ROOT = Path(__file__).resolve().parents[3]
MODEL_FILES = (
    "backend/core/_model_helpers.py",
    "backend/core/_model_types.py",
    "backend/core/_model_codec.py",
    "backend/core/model.py",
)
FORBIDDEN_AI_PREFIXES = ("backend.db", "backend.session", "backend.runtime", "bin")


def _load_capture_goldens() -> ModuleType:
    cached = sys.modules.get("capture_goldens")
    if cached is not None:
        return cached
    spec = importlib.util.spec_from_file_location("capture_goldens", REPO_ROOT / "scripts" / "capture_goldens.py")
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    sys.modules["capture_goldens"] = mod
    spec.loader.exec_module(mod)
    return mod


def test_core_model_dataclasses_and_codec_parity() -> None:
    """Verify field, method, predicate, and dict round-trip invariants for core.model."""
    cls_names = (
        "BuildingOffer",
        "EnergyBalance",
        "FleetAlert",
        "GameState",
        "PlanetState",
        "QueueJob",
        "ResearchOffer",
        "Resources",
        "TutorialStatus",
        "TutorialTask",
        "UnitOffer",
        "PlannedAction",
    )
    for name in cls_names:
        assert dataclasses.is_dataclass(getattr(new_model, name)), name
    assert new_model.ShipOffer is new_model.UnitOffer and new_model.DefenseOffer is new_model.UnitOffer

    for scenario_name, make_state in STATES.items():
        orig_gs = make_state()
        raw = dataclasses.asdict(orig_gs)
        new_gs = new_model.game_state_from_dict(raw)
        assert new_model.game_state_from_dict(new_model.game_state_to_dict(new_gs)) == new_gs, scenario_name
        assert isinstance(new_model.homeworld_planet_id(new_gs), (int, type(None)))
        assert new_model.count_allocated_normal_planets(new_gs) >= 0
        assert isinstance(new_model.extract_in_flight_colonize_coords(new_gs), set)
        assert isinstance(new_model.extract_in_flight_recycle_coords(new_gs), set)
        assert isinstance(new_model.extract_in_flight_station_dest_coords(new_gs), set)
        for new_p in new_gs.planets:
            assert new_p.fields_remaining >= 0
            assert len(new_p.storage_fill_ratios()) == 4
            assert isinstance(new_model.is_undersized_fresh_colony(new_p, new_gs), bool)
            assert new_model.count_in_flight_fleets_for_planet(new_gs, new_p) >= 0
            assert new_model.count_in_flight_spy_fleets_for_planet(new_gs, new_p) >= 0

    for ptype in ("normal", "asteroid", "raid", "moon"):
        for bid in (*range(0, 23), True, "7", "bad"):
            assert isinstance(new_model.is_building_allowed_on_planet_type(ptype, bid), bool)

    r_big = new_model.Resources(10**5000, 100, 50, 0)
    assert isinstance(r_big.format_short(), str) and r_big.format_short()
    assert r_big.can_afford(new_model.Resources(10, 100, 50, 0))
    assert not r_big.can_afford(new_model.Resources(10, 101, 50, 0))
    assert r_big.missing(new_model.Resources(10, 120, 50, 5)) == new_model.Resources(0, 20, 0, 5)
    assert new_model.Resources.from_list([1, 2, 3, 4]) == new_model.Resources(1, 2, 3, 4)


@pytest.mark.parametrize("state_name", ("midgame_three_colonies", "lategame_five_colonies"))
def test_ai_planner_reproduces_all_golden_reference_outputs_byte_for_byte(state_name: str) -> None:
    """Prove `backend.planner.ai` reproduces all 8 golden files in `tests/goldens/reference/ai_planner/`."""
    capture = _load_capture_goldens()
    cfg = StrategyConfig(planner_mode="ai_planner")
    with capture.frozen_time(GOLDEN_EPOCH):
        state = STATES[state_name]()
        plan = new_ai.run_ai_planner(state, ai_target_config(state), params=AI_PLANNER_PARAMS)
        actions, conflicts = new_ai.reconcile_live_ai_actions(state, plan, cfg)
        fills = new_ai.plan_live_fill_actions(state, plan, cfg, actions)
        graph_full = build_ai_planner_graph(state, plan)
        graph_w1 = build_ai_planner_graph(
            state, plan, window_size=12, week_filter=1, planet_filter=state.planets[1].planet_id
        )

    expected_cases = {
        f"path__{state_name}": plan,
        f"actions__{state_name}": {
            "summary": capture.summarize_actions(actions),
            "actions": actions,
            "field_conflicts": conflicts,
            "fill_actions": fills,
        },
        f"graph__{state_name}": graph_full,
        f"graph_week1_colony__{state_name}": graph_w1,
    }
    for case_name, payload in expected_cases.items():
        ref_path = capture.reference_path("ai_planner", case_name)
        assert ref_path.is_file(), f"Missing golden reference {ref_path}"
        expected = ref_path.read_text(encoding="utf-8")
        actual = capture.render(payload)
        assert actual == expected, f"Golden mismatch on ai_planner/{case_name}:\n" + capture.diff_text(
            expected, actual, f"ai_planner/{case_name}.json"
        )

    # Also verify diagnostics, early transport needs, TXT export, and core.model.GameState roundtrip
    diag_new = new_ai.diagnose_ai_colony_idle_states(state, plan, actions, cfg)
    assert isinstance(diag_new, dict)
    needs_new = new_ai.compute_early_transport_needs(state, plan, actions, config=cfg)
    assert isinstance(needs_new, dict)
    assert "AI-Planner" in new_ai.export_ai_planner_txt(plan) or len(new_ai.export_ai_planner_txt(plan)) > 0

    model_gs = new_model.game_state_from_dict(dataclasses.asdict(state))
    model_actions, model_conflicts = new_ai.reconcile_live_ai_actions(model_gs, plan, cfg)
    assert [dataclasses.asdict(a) for a in model_actions] == [dataclasses.asdict(a) for a in actions]
    assert model_conflicts == conflicts


def test_ai_planner_golden_directory_full_coverage() -> None:
    """Confirm all recorded files under `tests/goldens/reference/ai_planner/` are verified."""
    ref_dir = REPO_ROOT / "tests" / "goldens" / "reference" / "ai_planner"
    recorded = sorted(p.stem for p in ref_dir.glob("*.json"))
    expected = sorted(
        f"{prefix}__{state_name}"
        for state_name in ("midgame_three_colonies", "lategame_five_colonies")
        for prefix in ("actions", "graph", "graph_week1_colony", "path")
    )
    assert recorded == expected


def test_plan_v4_adaptive_live_fill_rule_parity(monkeypatch: pytest.MonkeyPatch) -> None:
    """Run every `TestLiveFillRule` Plan v4 test case against `backend.planner.ai`."""
    from tests.unit.planner import test_adaptive_v4 as v4

    assert new_ai.AI_FILL_REASON_PREFIX == "AI-Planner fill"
    assert new_ai.AI_FILL_MIN_PATIENCE > 0
    assert new_ai.AI_FILL_MAX_PATIENCE >= new_ai.AI_FILL_MIN_PATIENCE
    assert new_ai.AI_FILL_POOL_LEAD_HOURS > 0
    assert new_ai.AI_STEP_SEQ_RE.pattern
    assert isinstance(new_ai.AI_COLONY_IDLE_STATES, (set, frozenset, tuple))

    monkeypatch.setattr(v4, "ap", new_ai)
    suite = v4.TestLiveFillRule()
    method_names = sorted(m for m in dir(suite) if m.startswith("test_"))
    assert len(method_names) >= 10
    for mname in method_names:
        if hasattr(suite, "setUp"):
            suite.setUp()
        getattr(suite, mname)()


def _check_ast_standards(path: Path, rel: str, stdlib_names: set[str], *, is_model: bool) -> None:
    src = path.read_text(encoding="utf-8")
    tree = ast.parse(src, filename=rel)
    loc = len(src.splitlines())
    assert loc <= 400, f"{rel} exceeds 400 LOC ({loc})"
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef):
            cloc = (node.end_lineno or node.lineno) - node.lineno + 1
            assert cloc <= 400, f"{rel}:{node.name} class exceeds 400 LOC ({cloc})"
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            floc = (node.end_lineno or node.lineno) - node.lineno + 1
            assert floc <= 100, f"{rel}:{node.name} fn exceeds 100 LOC ({floc})"
        elif isinstance(node, ast.ExceptHandler):
            assert node.type is not None, f"{rel}: bare except forbidden"
            if isinstance(node.type, ast.Name):
                assert node.type.id not in ("Exception", "BaseException"), f"{rel}: broad except {node.type.id}"
        elif isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
            assert node.func.id != "print", f"{rel}: print() forbidden"
            if not rel.startswith("tests/"):
                assert node.func.id not in ("getattr", "hasattr"), f"{rel}: {node.func.id}() forbidden"
        elif isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name):
            assert not (node.value.id == "os" and node.attr in ("environ", "getenv")), (
                f"{rel}: os.{node.attr} forbidden"
            )
        elif isinstance(node, (ast.Import, ast.ImportFrom)) and not rel.startswith("tests/"):
            mods = (
                [a.name for a in node.names] if isinstance(node, ast.Import) else ([node.module] if node.module else [])
            )
            for mod in mods:
                top = mod.split(".")[0]
                if is_model:
                    ok = top in stdlib_names or mod in (
                        "backend.core.coercion",
                        "backend.core.coords",
                        "backend.core.redaction",
                        "backend.core._inflight",
                        "backend.core._model_helpers",
                        "backend.core._model_types",
                        "backend.core._model_codec",
                        "backend.core.catalog",
                    )
                    assert ok, f"{rel}: core/model forbidden import {mod!r}"
                else:
                    assert not mod.startswith(FORBIDDEN_AI_PREFIXES), f"{rel}: planner/ai forbidden import {mod!r}"
                    assert mod != "backend.planner.strategy", f"{rel}: must use core.model.PlannedAction, not strategy"


def test_section4_engineering_standards_and_layering_rules() -> None:
    """Enforce Section 4 LOC, class, function, smell, and layering rules across all new files."""
    stdlib = set(sys.stdlib_module_names) | {"__future__"}
    for rel in MODEL_FILES:
        _check_ast_standards(REPO_ROOT / rel, rel, stdlib, is_model=True)
    ai_files = sorted((REPO_ROOT / "backend" / "planner" / "ai").rglob("*.py"))
    assert len(ai_files) >= 20
    for path in ai_files:
        _check_ast_standards(path, path.relative_to(REPO_ROOT).as_posix(), stdlib, is_model=False)
    for path in (REPO_ROOT / "tests" / "unit" / "planner" / "test_model_and_ai_parity.py",):
        _check_ast_standards(path, path.relative_to(REPO_ROOT).as_posix(), stdlib, is_model=False)
