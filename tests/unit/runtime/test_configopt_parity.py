"""Determinism, override pinning, projection, and Section 4 tests for ``planner.config_optimizer``."""

from __future__ import annotations

import ast
import copy
from pathlib import Path

import numpy as np
import pytest

from backend.core.model import (
    EnergyBalance,
    GameState,
    PlanetState,
    Resources,
)
from backend.planner import config_optimizer as co

REPO_ROOT = Path(__file__).resolve().parents[3]


def _strip_wall_clock(obj: object) -> object:
    if isinstance(obj, dict):
        return {
            k: _strip_wall_clock(v) for k, v in obj.items() if k not in ("generated_at", "elapsed_ms", "updated_at")
        }
    if isinstance(obj, list):
        return [_strip_wall_clock(v) for v in obj]
    return obj


def _make_planet(
    planet_id: int,
    coords: str,
    name: str,
    *,
    temperature: int = 20,
    fields_max: int = 280,
    buildings: dict[str, int] | None = None,
    ships: dict[str, int] | None = None,
) -> PlanetState:
    bmap = dict(buildings or {})
    return PlanetState(
        planet_id=planet_id,
        name=name,
        coords=coords,
        planet_type="normal",
        temperature=temperature,
        fields_used=sum(bmap.values()),
        fields_max=fields_max,
        resources=Resources(50_000, 25_000, 20_000, 10_000),
        storage=Resources(200_000, 200_000, 200_000, 200_000),
        energy=EnergyBalance(produced=500, used=300),
        buildings=bmap,
        ships=dict(ships or {}),
    )


def _make_state() -> GameState:
    specs = [
        (
            101,
            "02:380:12",
            "Homeworld",
            25,
            260,
            {"b1": 18, "b2": 16, "b3": 16, "b4": 14, "b5": 20, "b7": 10, "b8": 10, "b9": 10},
        ),
        (102, "02:380:08", "MetalForge", 10, 300, {"b1": 20, "b2": 18, "b3": 12, "b4": 8, "b5": 20, "b7": 10}),
        (103, "02:381:05", "IceSpice", -40, 310, {"b1": 10, "b3": 15, "b4": 18, "b5": 18, "b7": 10}),
    ]
    planets = tuple(
        _make_planet(pid, coords, nm, temperature=temp, fields_max=fmax, buildings=blds)
        for pid, coords, nm, temp, fmax, blds in specs
    )
    return GameState(
        universe_id=31,
        user_id=7,
        nickname="Architect",
        timestamp=1_700_000_000.0,
        planets=planets,
        active_planet_id=101,
        researches={"r1": 8, "r2": 8, "r3": 8, "r6": 8, "r13": 8, "r15": 1},
        speedup_factor=10.0,
    )


def _fast_params(**overrides: object) -> dict[str, object]:
    base: dict[str, object] = {
        "catalogue_grid_size": 512,
        "catalogue_priority_steps": 2,
        "hill_climb_step_sizes": [1, 2],
        "ga_population_size": 24,
        "ga_generations": 15,
        "ga_random_seed": 20261117,
        "central_planet_min_warehouse_level": 10,
        "optimized_building_max_levels": {
            "b1": 35,
            "b2": 35,
            "b3": 35,
            "b4": 35,
            "b5": 35,
            "b9": 20,
            "b17": 8,
        },
    }
    base.update(overrides)
    return base


def test_config_optimizer_exports_and_core_math_invariants() -> None:
    expected = (
        "RESOURCE_KEYS",
        "WAREHOUSE_KEYS",
        "PRODUCTION_FLOW_KEYS",
        "VALID_PLANET_SPECIALIZATIONS",
        "SPECIALIZATION_ALLOWED_BUILDINGS",
        "DEFAULT_CONFIG_OPTIMIZER_PARAMS",
        "MAX_PLANET_NAME_LENGTH",
        "TECH_GRAPH",
        "TechnologyRequirementGraph",
        "EmpireProductionModel",
        "run_config_optimizer",
        "apply_manual_planet_override",
        "compute_yearly_ships_per_week_projection",
        "detect_planet_specialization",
        "normalize_planet_name",
        "normalize_planet_names_map",
        "normalize_planet_specialization",
        "normalize_planet_specializations_map",
        "normalize_planet_target_buildings",
        "normalize_manual_planet_configs_map",
        "building_level_economy",
        "building_level_flows",
        "build_monotonic_base_times",
        "warehouse_capacity",
        "warehouse_level_for",
        "search_in_rounds",
    )
    for sym in expected:
        assert sym in co.__all__, f"Missing export {sym} from backend.planner.config_optimizer"

    ctx0 = {"speedup_factor": 5.0, "planet_temperature_c": 20, "r1_mining_tech": 0, "r2_energy_tech": 0}
    ctx10 = {"speedup_factor": 5.0, "planet_temperature_c": 20, "r1_mining_tech": 10, "r2_energy_tech": 0}
    assert co.building_level_economy(2, 10, ctx0)["production"] == pytest.approx(
        co.building_level_economy(2, 10, ctx10)["production"]
    )
    assert co.building_level_economy(1, 10, ctx10)["production"] == pytest.approx(
        co.building_level_economy(1, 10, ctx0)["production"] * 1.20
    )


@pytest.mark.parametrize("seed", [20261117, 42])
@pytest.mark.parametrize("allow_demolition", [False, True])
def test_fixed_seed_ga_runs_are_deterministic_and_stable(seed: int, allow_demolition: bool) -> None:
    mod_st = _make_state()
    uni_max = {"r1": 12, "r2": 12, "r3": 10, "r6": 12, "r13": 12, "r15": 3}
    params = _fast_params(
        ga_random_seed=seed,
        allow_demolition=allow_demolition,
        min_warehouse_level=8,
        reserved_floors_by_planet={"101": {"b3": 16}},
        planet_specializations={"102": "metal"},
        planet_names={"101": "Titan Capital", "103": "Cryo Spire"},
    )
    run_a = co.run_config_optimizer(mod_st, universe_research_max=uni_max, params=params)
    run_b = co.run_config_optimizer(mod_st, universe_research_max=uni_max, params=params)
    assert _strip_wall_clock(run_a) == _strip_wall_clock(run_b)

    sticky = co.run_config_optimizer(mod_st, universe_research_max=uni_max, params=params, previous_result=run_a)
    assert sticky["planets"]

    bad_prev = copy.deepcopy(run_a)
    for pl in bad_prev["planets"]:
        pl["detected_specialization"] = "metal"
    adapted = co.run_config_optimizer(mod_st, universe_research_max=uni_max, params=params, previous_result=bad_prev)
    assert adapted["planets"]


def test_manual_planet_override_pinning_and_yearly_projections_3y_5y_10y_15y() -> None:
    mod_st = _make_state()
    uni_max = {"r1": 12, "r2": 12, "r3": 10, "r6": 12, "r13": 12, "r15": 3}
    params = _fast_params(max_planets=3, central_planet_min_warehouse_level=10)
    target_blds = {"b3": 24, "b4": 26, "b5": 26, "b9": 14, "b17": 2, "b13": 10, "b14": 10}

    ovr = co.apply_manual_planet_override(
        mod_st,
        slot=3,
        target_buildings=target_blds,
        planet_name="Starforge III",
        locked_specialization="spice_shipyard",
        lock_for_ga=True,
        universe_research_max=uni_max,
        params=params,
    )
    ovr_auto = co.apply_manual_planet_override(
        mod_st,
        base_result=ovr,
        slot=2,
        target_buildings={"b1": 24, "b2": 22, "b5": 24},
        fields_max_override=320,
        auto_warehouses=True,
        lock_for_ga=True,
    )
    with pytest.raises(ValueError):
        co.apply_manual_planet_override(
            mod_st,
            base_result=ovr,
            slot=3,
            target_buildings={"b1": 95, "b2": 95, "b3": 95, "b5": 95},
        )

    ga_pin = co.run_config_optimizer(
        mod_st,
        universe_research_max=uni_max,
        params=ovr_auto["meta_parameters"],
        previous_result=ovr_auto,
    )
    p3_blds = {r["key"]: r["target_level"] for r in ga_pin["planets"][2]["buildings"]}
    assert p3_blds["b4"] == 26 and p3_blds["b9"] == 14 and p3_blds["b17"] == 2

    for horizon in (3, 5, 10, 15):
        proj = co.compute_yearly_ships_per_week_projection(ga_pin, mod_st, horizon_years=horizon)
        assert proj["horizon_years"] == horizon
        assert len(proj["years"]) == horizon
        assert len(proj["weeks"]) == horizon * 52 + 1
        assert np.isfinite(float(proj["target_ships_per_week"]))


def test_section4_standards_and_layering_for_config_optimizer() -> None:
    co_files = sorted((REPO_ROOT / "backend/planner/config_optimizer").glob("*.py"))
    assert len(co_files) >= 10
    for path in co_files:
        src = path.read_text(encoding="utf-8")
        lines = src.splitlines()
        assert len(lines) <= 400, f"{path}: {len(lines)} > 400 LOC"
        tree = ast.parse(src, filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                floc = (node.end_lineno or node.lineno) - node.lineno + 1
                assert floc <= 100, f"{path}:{node.name}: {floc} > 100 LOC"
            if isinstance(node, ast.ClassDef):
                cloc = (node.end_lineno or node.lineno) - node.lineno + 1
                assert cloc <= 400, f"{path}:{node.name}: {cloc} > 400 LOC"
            if isinstance(node, ast.ExceptHandler):
                t = node.type
                assert t is not None, f"{path}: bare except"
                if isinstance(t, ast.Name):
                    assert t.id not in ("Exception", "BaseException"), f"{path}: broad except {t.id}"
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
                assert node.func.id not in ("print", "getattr", "hasattr"), f"{path}: forbidden call {node.func.id}"
            if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name) and node.value.id == "os":
                assert node.attr not in ("environ", "getenv"), f"{path}: forbidden os.{node.attr}"
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                mods = (
                    [alias.name for alias in node.names]
                    if isinstance(node, ast.Import)
                    else ([node.module] if node.module else [])
                )
                for mod in mods:
                    assert not mod.startswith(
                        (
                            "selenium",
                            "pandas",
                            "requests",
                            "backend.db",
                            "backend.session",
                            "backend.runtime",
                            "backend.planner.strategy",
                            "bin",
                        )
                    ), f"{path}: forbidden import {mod}"
            if isinstance(node, ast.Name) and node.id == "Any":
                raise AssertionError(f"{path}: forbidden Any")
