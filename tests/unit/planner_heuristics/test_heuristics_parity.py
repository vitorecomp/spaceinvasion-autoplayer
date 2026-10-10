"""Exact 1:1 parity, golden reproduction, Plan v4, fuzz, and Section 4 tests for `planner.heuristics`."""

from __future__ import annotations

import ast
from collections.abc import Sequence
from dataclasses import asdict
import json
from pathlib import Path
import random

import pytest

from spaceinvasion.core.config import StrategyConfig
from spaceinvasion.core.state import (
    BuildingOffer,
    EnergyBalance,
    FleetAlert,
    GameState,
    PlanetState,
    QueueJob,
    Resources,
    TutorialStatus,
    TutorialTask,
)
from spaceinvasion.planner import heuristics as new_heur
from spaceinvasion.planner import strategy as legacy_strategy
from tests.goldens.scenarios import CONFIGS, GOLDEN_EPOCH, STATES

_REPO_ROOT = Path(__file__).resolve().parents[3]
_HEUR_DIR = _REPO_ROOT / "spaceinvasion" / "planner" / "heuristics"
_GOLDENS_DIR = _REPO_ROOT / "tests" / "goldens" / "reference" / "planner"


def _norm_actions(actions: Sequence[object]) -> list[dict[str, object]]:
    return [asdict(a) for a in actions]  # type: ignore[call-overload]


def _canonical_json(obj: object) -> str:
    def _canon(val: object) -> object:
        if val is None or isinstance(val, (bool, int, str)):
            return val
        if isinstance(val, float):
            rounded = float(f"{val:.10g}")
            return 0.0 if rounded == 0.0 else rounded
        if isinstance(val, dict):
            return {str(k): _canon(v) for k, v in val.items()}
        if isinstance(val, (list, tuple)):
            return [_canon(v) for v in val]
        if hasattr(val, "__dataclass_fields__"):
            return _canon(asdict(val))  # type: ignore[call-overload]
        return str(val)

    return json.dumps(_canon(obj), indent=1, sort_keys=True, ensure_ascii=True, allow_nan=False) + "\n"


@pytest.mark.parametrize("state_name", sorted(STATES.keys()))
@pytest.mark.parametrize("cfg_name", sorted(CONFIGS.keys()))
def test_golden_reference_planner_files_reproduced_byte_for_byte(state_name: str, cfg_name: str) -> None:
    state = STATES[state_name]()
    cfg = CONFIGS[cfg_name]()

    new_actions = new_heur.plan_actions(
        state,
        config=cfg,
        scanned_systems={},
        throttle_cooldowns={},
        now_ts=GOLDEN_EPOCH,
    )
    legacy_actions = legacy_strategy.plan_actions(
        state,
        config=cfg,
        scanned_systems={},
        throttle_cooldowns={},
        now_ts=GOLDEN_EPOCH,
    )
    assert _norm_actions(new_actions) == _norm_actions(legacy_actions)

    ref_path = _GOLDENS_DIR / f"priority__{state_name}__{cfg_name}.json"
    expected_text = ref_path.read_text(encoding="utf-8")
    actual_text = _canonical_json({"summary": new_heur.summarize_plan(new_actions), "actions": new_actions})
    assert actual_text == expected_text


@pytest.mark.parametrize("state_name", sorted(STATES.keys()))
@pytest.mark.parametrize("cfg_name", sorted(CONFIGS.keys()))
def test_plan_fleet_and_attack_actions_parity(state_name: str, cfg_name: str) -> None:
    state = STATES[state_name]()
    cfg = CONFIGS[cfg_name]()
    new_acts = new_heur.plan_fleet_and_attack_actions(state, config=cfg, scanned_systems={}, now_ts=GOLDEN_EPOCH)
    old_acts = legacy_strategy.plan_fleet_and_attack_actions(state, config=cfg, scanned_systems={}, now_ts=GOLDEN_EPOCH)
    assert _norm_actions(new_acts) == _norm_actions(old_acts)


def test_plan_v4_e1_empire_smelting_arm_parity() -> None:
    cfg = StrategyConfig(
        planner_mode="ai_planner",
        allow_multi_planet=True,
        allow_transport=True,
        auto_throttle_blast_furnace=True,
        merchant_mode=False,
        ai_adaptive_execution=True,
    )
    needy = PlanetState(
        planet_id=901,
        name="Needy",
        coords="02:380:04",
        planet_type="normal",
        temperature=20,
        fields_used=50,
        fields_max=300,
        resources=Resources(pig_iron=80_000, metal=0, kryptonite=50_000, spice=20_000),
        storage=Resources(2_000_000, 2_000_000, 2_000_000, 2_000_000),
        energy=EnergyBalance(produced=2000, used=500),
        buildings={"b1": 10, "b2": 0, "b3": 10, "b4": 5, "b5": 10},
        economy_rates={"resource1": 100, "resource2": 0, "resource3": 100, "resource4": 100},
    )
    helper = PlanetState(
        planet_id=902,
        name="Helper",
        coords="02:380:08",
        planet_type="normal",
        temperature=20,
        fields_used=50,
        fields_max=300,
        resources=Resources(pig_iron=120_000, metal=10_000, kryptonite=50_000, spice=20_000),
        storage=Resources(2_000_000, 2_000_000, 2_000_000, 2_000_000),
        energy=EnergyBalance(produced=2000, used=500),
        buildings={"b1": 10, "b2": 10, "b3": 10, "b4": 5, "b5": 10},
        economy_rates={"resource1": 100, "resource2": 0, "resource3": 100, "resource4": 100},
    )
    st = GameState(31, 1, "T", 5000.0, (needy, helper), active_planet_id=901, researches={"r1": 8, "r2": 4})
    ai_steps_new = [
        new_heur.PlannedAction(
            "P2",
            "WAIT_RESOURCES",
            901,
            "02:380:04",
            "Development center",
            "AI-Planner #1: build Development center L10",
            target_id=7,
            target_level=10,
            cost=Resources(0, 80_000, 0, 0),
            missing=Resources(0, 80_000, 0, 0),
        )
    ]
    ai_steps_old = [
        legacy_strategy.PlannedAction(
            "P2",
            "WAIT_RESOURCES",
            901,
            "02:380:04",
            "Development center",
            "AI-Planner #1: build Development center L10",
            target_id=7,
            target_level=10,
            cost=Resources(0, 80_000, 0, 0),
            missing=Resources(0, 80_000, 0, 0),
        )
    ]
    got = new_heur.plan_ai_blast_furnace_throttles(st, ai_steps_new, [], cfg, now_ts=5000.0, enable_donor_smelting=True)
    exp = legacy_strategy.plan_ai_blast_furnace_throttles(
        st, ai_steps_old, [], cfg, now_ts=5000.0, enable_donor_smelting=True
    )
    assert _norm_actions(got) == _norm_actions(exp)
    assert [(a.planet_id, a.target_level) for a in got] == [(902, 100)]


def test_plan_v4_e3_loans_and_e4_infra_transport_targets_parity() -> None:
    cfg = StrategyConfig(
        planner_mode="ai_planner",
        allow_multi_planet=True,
        allow_transport=True,
        allow_shipyard=True,
        ai_adaptive_execution=True,
    )
    p1 = PlanetState(
        planet_id=101,
        name="C1",
        coords="02:380:04",
        planet_type="normal",
        temperature=20,
        fields_used=50,
        fields_max=300,
        resources=Resources(pig_iron=10_000, metal=10_000, kryptonite=10_000, spice=5_000),
        storage=Resources(2_000_000, 2_000_000, 2_000_000, 2_000_000),
        energy=EnergyBalance(produced=2000, used=1000),
        buildings={"b1": 10, "b2": 10, "b3": 10, "b4": 6, "b5": 10, "b9": 6, "b10": 5},
        ships={"s2": 10},
    )
    p2 = PlanetState(
        planet_id=102,
        name="C2",
        coords="02:380:08",
        planet_type="normal",
        temperature=20,
        fields_used=50,
        fields_max=300,
        resources=Resources(pig_iron=100_000, metal=120_000, kryptonite=100_000, spice=50_000),
        storage=Resources(2_000_000, 2_000_000, 2_000_000, 2_000_000),
        energy=EnergyBalance(produced=2000, used=1000),
        buildings={"b1": 10, "b2": 10, "b3": 10, "b4": 6, "b5": 10, "b9": 6, "b10": 5},
        ships={"s2": 15},
    )
    raid_job = QueueJob(
        category="fleet",
        name="Attack 02:381:05",
        remaining_seconds=600.0,
        remaining_text="10m",
        detail="Outbound · 10 Large transport",
        origin_coords="02:380:08",
    )
    st = GameState(
        31,
        1,
        "T",
        1_700_000_000.0,
        (p1, p2),
        active_planet_id=101,
        researches={"r1": 8, "r2": 4, "r3": 6, "r4": 4, "r6": 6},
        active_queues=(raid_job,),
    )
    demand = {"cargo_per_hour_24h": 500_000.0, "round_trips_per_hour": 2.0, "utilization_assumed": 0.5}
    assert new_heur.compute_infra_transport_targets(st, cfg, demand) == legacy_strategy.compute_infra_transport_targets(
        st, cfg, demand
    )

    pending_new = [
        new_heur.PlannedAction(
            "P1",
            "WAIT_RESOURCES",
            101,
            "02:380:04",
            "Development center",
            "AI-Planner #1: build Development center L10",
            target_id=7,
            target_level=10,
            cost=Resources(0, 90_000, 0, 0),
            missing=Resources(0, 80_000, 0, 0),
        ),
        new_heur.PlannedAction(
            "P1",
            "WAIT_RESOURCES",
            102,
            "02:380:08",
            "Development center",
            "AI-Planner #5: build Development center L11",
            target_id=7,
            target_level=11,
            cost=Resources(0, 300_000, 0, 0),
            missing=Resources(0, 180_000, 0, 0),
        ),
    ]
    pending_old = [
        legacy_strategy.PlannedAction(
            "P1",
            "WAIT_RESOURCES",
            101,
            "02:380:04",
            "Development center",
            "AI-Planner #1: build Development center L10",
            target_id=7,
            target_level=10,
            cost=Resources(0, 90_000, 0, 0),
            missing=Resources(0, 80_000, 0, 0),
        ),
        legacy_strategy.PlannedAction(
            "P1",
            "WAIT_RESOURCES",
            102,
            "02:380:08",
            "Development center",
            "AI-Planner #5: build Development center L11",
            target_id=7,
            target_level=11,
            cost=Resources(0, 300_000, 0, 0),
            missing=Resources(0, 180_000, 0, 0),
        ),
    ]
    needs: dict[int, dict[str, object]] = {
        101: {
            "pig_iron": 0,
            "metal": 80_000,
            "kryptonite": 0,
            "spice": 0,
            "label": "Development center L10",
            "need_by_sec": 0.0,
            "lead_sec": 600.0,
            "storage_cap_ratio": 0.85,
        },
        102: {
            "pig_iron": 0,
            "metal": 180_000,
            "kryptonite": 0,
            "spice": 0,
            "label": "Development center L11",
            "need_by_sec": 0.0,
            "lead_sec": 600.0,
            "storage_cap_ratio": 0.85,
        },
    }
    got_tr = new_heur.plan_interplanet_transports(st, cfg, pending_new, priority_resource_need=needs)
    exp_tr = legacy_strategy.plan_interplanet_transports(st, cfg, pending_old, priority_resource_need=needs)
    assert _norm_actions(got_tr) == _norm_actions(exp_tr)
    assert any(new_heur.AI_PLAN_ORDER_LOAN_TAG in a.reason for a in got_tr)


def test_classic_tutorial_fusion_bucket4_invariant() -> None:
    planet = PlanetState(
        planet_id=501,
        name="Home",
        coords="01:010:05",
        planet_type="normal",
        temperature=20,
        fields_used=10,
        fields_max=300,
        resources=Resources(50_000, 50_000, 50_000, 10_000),
        storage=Resources(500_000, 500_000, 500_000, 500_000),
        energy=EnergyBalance(produced=400, used=100),
        buildings={"b1": 2, "b5": 2},
        building_offers=(
            BuildingOffer(1, "Pig iron mine", 2, 3, Resources(100, 50, 0, 0), True, "Build", "href_b1"),
            BuildingOffer(5, "Fusion plant", 2, 3, Resources(100, 50, 0, 0), True, "Build", "href_b5"),
        ),
    )
    tut = TutorialStatus(
        step=2,
        total_steps=11,
        can_complete=False,
        reward_text="Bonus",
        tasks=(
            TutorialTask(
                description="Fusion plant level 3",
                building_name="Fusion plant",
                building_id=5,
                current=2,
                target=3,
                category="building",
            ),
            TutorialTask(
                description="Pig iron mine level 3",
                building_name="Pig iron mine",
                building_id=1,
                current=2,
                target=3,
                category="building",
            ),
        ),
    )
    st = GameState(31, 1, "T", GOLDEN_EPOCH, (planet,), active_planet_id=501, tutorial=tut)
    cfg = StrategyConfig()
    got = new_heur.plan_actions(st, config=cfg, scanned_systems={}, throttle_cooldowns={}, now_ts=GOLDEN_EPOCH)
    exp = legacy_strategy.plan_actions(st, config=cfg, scanned_systems={}, throttle_cooldowns={}, now_ts=GOLDEN_EPOCH)
    assert _norm_actions(got) == _norm_actions(exp)
    build_ids = [a.target_id for a in got if a.kind == "BUILD"]
    assert build_ids.index(1) < build_ids.index(5)


def test_seeded_differential_fuzz_plan_actions_parity() -> None:
    rng = random.Random(20261120)
    for _ in range(40):
        n_planets = rng.randint(1, 4)
        planets: list[PlanetState] = []
        for idx in range(n_planets):
            pid = 300 + idx
            blds = {f"b{i}": rng.randint(0, 16) for i in rng.sample(range(1, 16), rng.randint(3, 10))}
            offers = tuple(
                BuildingOffer(
                    int(k[1:]),
                    f"B{k[1:]}",
                    lvl,
                    lvl + 1,
                    Resources(
                        rng.randint(100, 5000),
                        rng.randint(50, 4000),
                        rng.randint(0, 2000),
                        rng.randint(0, 500),
                    ),
                    rng.random() < 0.65,
                    "Build",
                    f"href_{k}" if rng.random() < 0.7 else None,
                )
                for k, lvl in blds.items()
            )
            planets.append(
                PlanetState(
                    planet_id=pid,
                    name=f"Col{idx}",
                    coords=f"{rng.randint(1, 4):02d}:{rng.randint(1, 200):03d}:{rng.randint(1, 15):02d}",
                    planet_type=rng.choice(["normal", "normal", "asteroid"]),
                    temperature=rng.randint(-120, 140),
                    fields_used=rng.randint(10, 150),
                    fields_max=rng.randint(160, 380),
                    resources=Resources(
                        rng.randint(0, 250_000),
                        rng.randint(0, 250_000),
                        rng.randint(0, 150_000),
                        rng.randint(0, 40_000),
                    ),
                    storage=Resources(
                        rng.randint(100_000, 2_000_000),
                        rng.randint(100_000, 2_000_000),
                        rng.randint(100_000, 2_000_000),
                        rng.randint(50_000, 1_000_000),
                    ),
                    energy=EnergyBalance(produced=rng.randint(0, 3000), used=rng.randint(0, 3000)),
                    buildings=blds,
                    ships={f"s{i}": rng.randint(0, 40) for i in rng.sample(range(1, 16), rng.randint(0, 5))},
                    building_offers=offers,
                    economy_rates={f"resource{i}": rng.choice([0, 50, 100]) for i in range(1, 5)},
                )
            )
        alerts = (FleetAlert(mission="Attack", summary="Hostile", hostile=True),) if rng.random() < 0.2 else ()
        st = GameState(
            31,
            1,
            "Fuzz",
            GOLDEN_EPOCH,
            tuple(planets),
            active_planet_id=planets[0].planet_id,
            researches={f"r{i}": rng.randint(0, 12) for i in range(1, 16)},
            fleet_alerts=alerts,
        )
        cfg = StrategyConfig(
            planner_mode="priority",
            allow_multi_planet=rng.random() < 0.7,
            allow_fleetsave=rng.random() < 0.5,
            allow_shipyard=rng.random() < 0.5,
            allow_transport=rng.random() < 0.5,
            allow_galaxy_scan=rng.random() < 0.4,
            allow_raids=rng.random() < 0.4,
            allow_colonize=rng.random() < 0.3,
            auto_throttle_blast_furnace=rng.random() < 0.6,
            merchant_mode=rng.random() < 0.3,
        )
        got = new_heur.plan_actions(st, config=cfg, scanned_systems={}, throttle_cooldowns={}, now_ts=GOLDEN_EPOCH)
        exp = legacy_strategy.plan_actions(
            st, config=cfg, scanned_systems={}, throttle_cooldowns={}, now_ts=GOLDEN_EPOCH
        )
        assert _norm_actions(got) == _norm_actions(exp)


def test_section4_ast_loc_and_layering_constraints() -> None:
    py_files = sorted(_HEUR_DIR.glob("*.py"))
    assert len(py_files) >= 12
    forbidden_prefixes = (
        "spaceinvasion.db",
        "spaceinvasion.session",
        "spaceinvasion.runtime",
        "spaceinvasion.ui",
        "spaceinvasion.planner.strategy",
        "spaceinvasion.planner.optimizer",
    )
    for path in py_files:
        src = path.read_text(encoding="utf-8")
        lines = src.splitlines()
        assert len(lines) <= 400, f"{path.name} has {len(lines)} lines (>400)"
        tree = ast.parse(src, filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                fn_len = (node.end_lineno or node.lineno) - node.lineno + 1
                assert fn_len <= 100, f"{path.name}:{node.name} has {fn_len} lines (>100)"
            elif isinstance(node, ast.ClassDef):
                cls_len = (node.end_lineno or node.lineno) - node.lineno + 1
                assert cls_len <= 400, f"{path.name}:{node.name} has {cls_len} lines (>400)"
            elif isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
                assert node.func.id != "print", f"Forbidden print() in {path.name}"
            elif isinstance(node, ast.ExceptHandler):
                if node.type is None:
                    pytest.fail(f"Bare except in {path.name}")
                if isinstance(node.type, ast.Name) and node.type.id == "Exception":
                    pytest.fail(f"Broad except Exception in {path.name}")
            elif isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name):
                assert not (node.value.id == "os" and node.attr == "environ"), f"os.environ in {path.name}"
            elif isinstance(node, ast.Import):
                for alias in node.names:
                    assert not alias.name.startswith(forbidden_prefixes), (
                        f"Forbidden import {alias.name} in {path.name}"
                    )
            elif isinstance(node, ast.ImportFrom) and node.module:
                assert not node.module.startswith(forbidden_prefixes), f"Forbidden import {node.module} in {path.name}"
