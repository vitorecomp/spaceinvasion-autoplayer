"""Parity, golden reproduction, Plan v4, and Section 4 standards tests for core/model and planner/ai."""

from __future__ import annotations

import ast
import dataclasses
import importlib.util
from pathlib import Path
import sys
from types import ModuleType

import pytest

from spaceinvasion.core import model as new_model
from spaceinvasion.core import state as legacy_state
from spaceinvasion.core.config import StrategyConfig
from spaceinvasion.planner import ai as new_ai
from spaceinvasion.planner import ai_planner as legacy_ai
from spaceinvasion.planner import strategy as legacy_strategy
from spaceinvasion.planner.action_graph import build_ai_planner_graph
from tests.goldens.scenarios import AI_PLANNER_PARAMS, GOLDEN_EPOCH, STATES, ai_target_config

REPO_ROOT = Path(__file__).resolve().parents[3]
MODEL_FILES = (
    "spaceinvasion/core/_model_helpers.py",
    "spaceinvasion/core/_model_types.py",
    "spaceinvasion/core/_model_codec.py",
    "spaceinvasion/core/model.py",
)
FORBIDDEN_AI_PREFIXES = ("spaceinvasion.db", "spaceinvasion.session", "spaceinvasion.runtime", "bin")


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
    """Verify 1:1 field, method, predicate, and dict round-trip parity with core.state and strategy.PlannedAction."""
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
    )
    for name in cls_names:
        old_fields = [f.name for f in dataclasses.fields(getattr(legacy_state, name))]
        new_fields = [f.name for f in dataclasses.fields(getattr(new_model, name))]
        assert new_fields == old_fields, f"{name} field mismatch"

    assert [f.name for f in dataclasses.fields(new_model.PlannedAction)] == [
        f.name for f in dataclasses.fields(legacy_strategy.PlannedAction)
    ]
    assert new_model.ShipOffer is new_model.UnitOffer and new_model.DefenseOffer is new_model.UnitOffer

    for scenario_name, make_state in STATES.items():
        old_gs = make_state()
        raw = dataclasses.asdict(old_gs)
        new_gs = new_model.game_state_from_dict(raw)
        assert new_model.game_state_from_dict(new_model.game_state_to_dict(new_gs)) == new_gs, scenario_name
        assert new_model.game_state_to_dict(new_gs) == dataclasses.asdict(legacy_state.game_state_from_dict(raw))
        assert new_model.homeworld_planet_id(new_gs) == legacy_state.homeworld_planet_id(old_gs)
        assert new_model.count_allocated_normal_planets(new_gs) == legacy_state.count_allocated_normal_planets(old_gs)
        assert new_model.extract_in_flight_colonize_coords(new_gs) == legacy_state.extract_in_flight_colonize_coords(
            old_gs
        )
        assert new_model.extract_in_flight_recycle_coords(new_gs) == legacy_state.extract_in_flight_recycle_coords(
            old_gs
        )
        assert new_model.extract_in_flight_station_dest_coords(
            new_gs
        ) == legacy_state.extract_in_flight_station_dest_coords(old_gs)
        for new_p, old_p in zip(new_gs.planets, old_gs.planets, strict=True):
            assert new_p.fields_remaining == old_p.fields_remaining
            assert new_p.storage_fill_ratios() == old_p.storage_fill_ratios()
            assert new_model.is_undersized_fresh_colony(new_p, new_gs) == legacy_state.is_undersized_fresh_colony(
                old_p, old_gs
            )
            assert new_model.count_in_flight_fleets_for_planet(
                new_gs, new_p
            ) == legacy_state.count_in_flight_fleets_for_planet(old_gs, old_p)
            assert new_model.count_in_flight_spy_fleets_for_planet(
                new_gs, new_p
            ) == legacy_state.count_in_flight_spy_fleets_for_planet(old_gs, old_p)

    for ptype in ("normal", "asteroid", "raid", "moon"):
        for bid in (*range(0, 23), True, "7", "bad"):
            assert new_model.is_building_allowed_on_planet_type(
                ptype, bid
            ) == legacy_state.is_building_allowed_on_planet_type(ptype, bid)

    r_big = new_model.Resources(10**5000, 100, 50, 0)
    assert r_big.format_short() == legacy_state.Resources(10**5000, 100, 50, 0).format_short()
    assert r_big.can_afford(new_model.Resources(10, 100, 50, 0))
    assert not r_big.can_afford(new_model.Resources(10, 101, 50, 0))
    assert r_big.missing(new_model.Resources(10, 120, 50, 5)) == new_model.Resources(0, 20, 0, 5)
    assert new_model.Resources.from_list([1, 2, 3, 4]) == new_model.Resources(1, 2, 3, 4)


@pytest.mark.parametrize("state_name", ("midgame_three_colonies", "lategame_five_colonies"))
def test_ai_planner_reproduces_all_golden_reference_outputs_byte_for_byte(state_name: str) -> None:
    """Prove `spaceinvasion.planner.ai` reproduces all 8 golden files in `tests/goldens/reference/ai_planner/`."""
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

    # Also verify diagnostics, early transport needs, TXT export, and core.model.GameState parity
    legacy_actions, _ = legacy_ai.reconcile_live_ai_actions(state, plan, cfg)
    diag_old = legacy_ai.diagnose_ai_colony_idle_states(state, plan, legacy_actions, cfg)
    diag_new = new_ai.diagnose_ai_colony_idle_states(state, plan, actions, cfg)
    assert diag_new == diag_old

    needs_old = legacy_ai.compute_early_transport_needs(state, plan, legacy_actions, config=cfg)
    needs_new = new_ai.compute_early_transport_needs(state, plan, actions, config=cfg)
    assert needs_new == needs_old

    assert new_ai.export_ai_planner_txt(plan) == legacy_ai.export_ai_planner_txt(plan)

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
    """Run every `TestLiveFillRule` Plan v4 test case against `spaceinvasion.planner.ai`."""
    from tests.unit import test_adaptive_v4 as v4

    assert new_ai.AI_FILL_REASON_PREFIX == legacy_ai.AI_FILL_REASON_PREFIX
    assert new_ai.AI_FILL_MIN_PATIENCE == legacy_ai.AI_FILL_MIN_PATIENCE
    assert new_ai.AI_FILL_MAX_PATIENCE == legacy_ai.AI_FILL_MAX_PATIENCE
    assert new_ai.AI_FILL_POOL_LEAD_HOURS == legacy_ai.AI_FILL_POOL_LEAD_HOURS
    assert new_ai._AI_STEP_SEQ_RE.pattern == legacy_ai._AI_STEP_SEQ_RE.pattern
    assert new_ai.AI_COLONY_IDLE_STATES == legacy_ai.AI_COLONY_IDLE_STATES

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
                        "spaceinvasion.core.coercion",
                        "spaceinvasion.core.coords",
                        "spaceinvasion.core.redaction",
                        "spaceinvasion.core._model_helpers",
                        "spaceinvasion.core._model_types",
                        "spaceinvasion.core._model_codec",
                    )
                    assert ok, f"{rel}: core/model forbidden import {mod!r}"
                else:
                    assert not mod.startswith(FORBIDDEN_AI_PREFIXES), f"{rel}: planner/ai forbidden import {mod!r}"
                    assert mod != "spaceinvasion.planner.strategy", (
                        f"{rel}: must use core.model.PlannedAction, not strategy"
                    )


def test_section4_engineering_standards_and_layering_rules() -> None:
    """Enforce Section 4 LOC, class, function, smell, and layering rules across all new files."""
    stdlib = set(sys.stdlib_module_names) | {"__future__"}
    for rel in MODEL_FILES:
        _check_ast_standards(REPO_ROOT / rel, rel, stdlib, is_model=True)
    ai_files = sorted((REPO_ROOT / "spaceinvasion" / "planner" / "ai").rglob("*.py"))
    assert len(ai_files) >= 20
    for path in ai_files:
        _check_ast_standards(path, path.relative_to(REPO_ROOT).as_posix(), stdlib, is_model=False)
    for path in sorted((REPO_ROOT / "tests" / "unit" / "planner_ai").rglob("*.py")):
        _check_ast_standards(path, path.relative_to(REPO_ROOT).as_posix(), stdlib, is_model=False)
