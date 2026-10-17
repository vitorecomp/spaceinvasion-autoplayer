"""1:1 numerical, structural, override, projection, and Section 4 parity tests for ``planner.config_opt``."""

from __future__ import annotations

import ast
import copy
from pathlib import Path

import numpy as np
import pytest

from spaceinvasion.core.model import (
    EnergyBalance as ModelEnergy,
    GameState as ModelGameState,
    PlanetState as ModelPlanetState,
    Resources as ModelResources,
)
from spaceinvasion.core.state import (
    EnergyBalance as LegacyEnergy,
    GameState as LegacyGameState,
    PlanetState as LegacyPlanetState,
    Resources as LegacyResources,
)
from spaceinvasion.planner import config_opt as new_co
from spaceinvasion.planner import config_optimizer as legacy_co

REPO_ROOT = Path(__file__).resolve().parents[3]


def _strip_wall_clock(obj: object) -> object:
    if isinstance(obj, dict):
        return {
            k: _strip_wall_clock(v) for k, v in obj.items() if k not in ("generated_at", "elapsed_ms", "updated_at")
        }
    if isinstance(obj, list):
        return [_strip_wall_clock(v) for v in obj]
    return obj


def _make_legacy_planet(
    planet_id: int,
    coords: str,
    name: str,
    *,
    temperature: int = 20,
    fields_max: int = 280,
    buildings: dict[str, int] | None = None,
    ships: dict[str, int] | None = None,
) -> LegacyPlanetState:
    bmap = dict(buildings or {})
    return LegacyPlanetState(
        planet_id=planet_id,
        name=name,
        coords=coords,
        planet_type="normal",
        temperature=temperature,
        fields_used=sum(bmap.values()),
        fields_max=fields_max,
        resources=LegacyResources(50_000, 25_000, 20_000, 10_000),
        storage=LegacyResources(200_000, 200_000, 200_000, 200_000),
        energy=LegacyEnergy(produced=500, used=300),
        buildings=bmap,
        ships=dict(ships or {}),
    )


def _make_model_planet(
    planet_id: int,
    coords: str,
    name: str,
    *,
    temperature: int = 20,
    fields_max: int = 280,
    buildings: dict[str, int] | None = None,
    ships: dict[str, int] | None = None,
) -> ModelPlanetState:
    bmap = dict(buildings or {})
    return ModelPlanetState(
        planet_id=planet_id,
        name=name,
        coords=coords,
        planet_type="normal",
        temperature=temperature,
        fields_used=sum(bmap.values()),
        fields_max=fields_max,
        resources=ModelResources(50_000, 25_000, 20_000, 10_000),
        storage=ModelResources(200_000, 200_000, 200_000, 200_000),
        energy=ModelEnergy(produced=500, used=300),
        buildings=bmap,
        ships=dict(ships or {}),
    )


def _make_states() -> tuple[LegacyGameState, ModelGameState]:
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
    leg_planets = tuple(
        _make_legacy_planet(pid, coords, nm, temperature=temp, fields_max=fmax, buildings=blds)
        for pid, coords, nm, temp, fmax, blds in specs
    )
    mod_planets = tuple(
        _make_model_planet(pid, coords, nm, temperature=temp, fields_max=fmax, buildings=blds)
        for pid, coords, nm, temp, fmax, blds in specs
    )
    res_map = {"r1": 8, "r2": 8, "r3": 8, "r6": 8, "r13": 8, "r15": 1}
    leg_st = LegacyGameState(
        universe_id=31,
        user_id=7,
        nickname="Architect",
        timestamp=1_700_000_000.0,
        planets=leg_planets,
        active_planet_id=101,
        researches=dict(res_map),
        speedup_factor=10.0,
    )
    mod_st = ModelGameState(
        universe_id=31,
        user_id=7,
        nickname="Architect",
        timestamp=1_700_000_000.0,
        planets=mod_planets,
        active_planet_id=101,
        researches=dict(res_map),
        speedup_factor=10.0,
    )
    return leg_st, mod_st


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


def test_config_opt_exports_and_core_math_invariants() -> None:
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
        assert hasattr(new_co, sym), f"Missing export {sym} from spaceinvasion.planner.config_opt"

    # SpaceInvasion Classic invariant: Blast furnace (b2) is NOT boosted by Mining tech (r1).
    ctx0 = {"speedup_factor": 5.0, "planet_temperature_c": 20, "r1_mining_tech": 0, "r2_energy_tech": 0}
    ctx10 = {"speedup_factor": 5.0, "planet_temperature_c": 20, "r1_mining_tech": 10, "r2_energy_tech": 0}
    assert new_co.building_level_economy(2, 10, ctx0) == legacy_co.building_level_economy(2, 10, ctx0)
    assert new_co.building_level_economy(2, 10, ctx0)["production"] == pytest.approx(
        new_co.building_level_economy(2, 10, ctx10)["production"]
    )
    assert new_co.building_level_economy(1, 10, ctx10)["production"] == pytest.approx(
        new_co.building_level_economy(1, 10, ctx0)["production"] * 1.20
    )


@pytest.mark.parametrize("seed", [20261117, 42])
@pytest.mark.parametrize("allow_demolition", [False, True])
def test_fixed_seed_ga_runs_match_legacy_1_to_1_on_both_state_models(
    seed: int,
    allow_demolition: bool,
) -> None:
    leg_st, mod_st = _make_states()
    uni_max = {"r1": 12, "r2": 12, "r3": 10, "r6": 12, "r13": 12, "r15": 3}
    params = _fast_params(
        ga_random_seed=seed,
        allow_demolition=allow_demolition,
        min_warehouse_level=8,
        reserved_floors_by_planet={"101": {"b3": 16}},
        planet_specializations={"102": "metal"},
        planet_names={"101": "Titan Capital", "103": "Cryo Spire"},
    )
    leg_res = legacy_co.run_config_optimizer(leg_st, universe_research_max=uni_max, params=params)
    new_leg_res = new_co.run_config_optimizer(leg_st, universe_research_max=uni_max, params=params)
    new_mod_res = new_co.run_config_optimizer(mod_st, universe_research_max=uni_max, params=params)

    assert _strip_wall_clock(new_leg_res) == _strip_wall_clock(leg_res)
    assert _strip_wall_clock(new_mod_res) == _strip_wall_clock(leg_res)

    # Stability alignment with previous_result (both sticky hold and bad-prev adaptation)
    leg_sticky = legacy_co.run_config_optimizer(
        leg_st, universe_research_max=uni_max, params=params, previous_result=leg_res
    )
    new_sticky = new_co.run_config_optimizer(
        mod_st, universe_research_max=uni_max, params=params, previous_result=new_mod_res
    )
    assert _strip_wall_clock(new_sticky) == _strip_wall_clock(leg_sticky)

    bad_prev = copy.deepcopy(leg_res)
    for pl in bad_prev["planets"]:
        pl["detected_specialization"] = "metal"
    leg_adapt = legacy_co.run_config_optimizer(
        leg_st, universe_research_max=uni_max, params=params, previous_result=bad_prev
    )
    new_adapt = new_co.run_config_optimizer(
        mod_st, universe_research_max=uni_max, params=params, previous_result=bad_prev
    )
    assert _strip_wall_clock(new_adapt) == _strip_wall_clock(leg_adapt)


def test_manual_planet_override_pinning_and_yearly_projections_3y_5y_10y_15y() -> None:
    leg_st, mod_st = _make_states()
    uni_max = {"r1": 12, "r2": 12, "r3": 10, "r6": 12, "r13": 12, "r15": 3}
    params = _fast_params(max_planets=3, central_planet_min_warehouse_level=10)
    target_blds = {"b3": 24, "b4": 26, "b5": 26, "b9": 14, "b17": 2, "b13": 10, "b14": 10}

    leg_ovr = legacy_co.apply_manual_planet_override(
        leg_st,
        slot=3,
        target_buildings=target_blds,
        planet_name="Starforge III",
        locked_specialization="spice_shipyard",
        lock_for_ga=True,
        universe_research_max=uni_max,
        params=params,
    )
    new_ovr = new_co.apply_manual_planet_override(
        mod_st,
        slot=3,
        target_buildings=target_blds,
        planet_name="Starforge III",
        locked_specialization="spice_shipyard",
        lock_for_ga=True,
        universe_research_max=uni_max,
        params=params,
    )
    assert _strip_wall_clock(new_ovr) == _strip_wall_clock(leg_ovr)

    # Auto-warehouses + fields_max_override variant
    leg_ovr_auto = legacy_co.apply_manual_planet_override(
        leg_st,
        base_result=leg_ovr,
        slot=2,
        target_buildings={"b1": 24, "b2": 22, "b5": 24},
        fields_max_override=320,
        auto_warehouses=True,
        lock_for_ga=True,
    )
    new_ovr_auto = new_co.apply_manual_planet_override(
        mod_st,
        base_result=new_ovr,
        slot=2,
        target_buildings={"b1": 24, "b2": 22, "b5": 24},
        fields_max_override=320,
        auto_warehouses=True,
        lock_for_ga=True,
    )
    assert _strip_wall_clock(new_ovr_auto) == _strip_wall_clock(leg_ovr_auto)

    # Over-field override raises ValueError identically
    with pytest.raises(ValueError):
        new_co.apply_manual_planet_override(
            mod_st,
            base_result=new_ovr,
            slot=3,
            target_buildings={"b1": 95, "b2": 95, "b3": 95, "b5": 95},
        )

    # Subsequent GA run preserves pinned manual_planet_configs identically
    leg_ga_pin = legacy_co.run_config_optimizer(
        leg_st,
        universe_research_max=uni_max,
        params=leg_ovr_auto["meta_parameters"],
        previous_result=leg_ovr_auto,
    )
    new_ga_pin = new_co.run_config_optimizer(
        mod_st,
        universe_research_max=uni_max,
        params=new_ovr_auto["meta_parameters"],
        previous_result=new_ovr_auto,
    )
    assert _strip_wall_clock(new_ga_pin) == _strip_wall_clock(leg_ga_pin)
    p3_blds = {r["key"]: r["target_level"] for r in new_ga_pin["planets"][2]["buildings"]}
    assert p3_blds["b4"] == 26 and p3_blds["b9"] == 14 and p3_blds["b17"] == 2

    # 3Y / 5Y / 10Y / 15Y yearly ships/week projections match legacy 1:1!
    for horizon in (3, 5, 10, 15):
        leg_proj = legacy_co.compute_yearly_ships_per_week_projection(leg_ga_pin, leg_st, horizon_years=horizon)
        new_proj = new_co.compute_yearly_ships_per_week_projection(new_ga_pin, mod_st, horizon_years=horizon)
        assert _strip_wall_clock(new_proj) == _strip_wall_clock(leg_proj), f"Mismatch at {horizon}Y horizon"
        assert new_proj["horizon_years"] == horizon
        assert len(new_proj["years"]) == horizon
        assert len(new_proj["weeks"]) == horizon * 52 + 1
        assert np.isfinite(float(new_proj["target_ships_per_week"]))


def test_section4_standards_and_layering_for_config_opt() -> None:
    co_files = sorted((REPO_ROOT / "spaceinvasion/planner/config_opt").glob("*.py"))
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
                            "spaceinvasion.db",
                            "spaceinvasion.session",
                            "spaceinvasion.runtime",
                            "spaceinvasion.planner.strategy",
                            "spaceinvasion.planner.config_optimizer",
                            "bin",
                        )
                    ), f"{path}: forbidden import {mod}"
            if isinstance(node, ast.Name) and node.id == "Any":
                raise AssertionError(f"{path}: forbidden Any")
