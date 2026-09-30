"""Unit & notebook mathematical parity tests for `spaceinvasion/planner/config_optimizer.py`."""

from __future__ import annotations

import ast
import copy
import dataclasses
import json
import math
from pathlib import Path
import unittest

import numpy as np

from spaceinvasion.core import game_data
from spaceinvasion.core.state import EnergyBalance, GameState, PlanetState, Resources
from spaceinvasion.planner import config_optimizer as co

REPO_ROOT = Path(__file__).resolve().parents[2]
MODULE_PATH = REPO_ROOT / "spaceinvasion" / "planner" / "config_optimizer.py"
NOTEBOOK_PATH = REPO_ROOT / "notebooks" / "config_optimizer.ipynb"


def _make_planet(
    planet_id: int,
    coords: str,
    name: str,
    *,
    planet_type: str = "normal",
    temperature: int = 20,
    fields_max: int = 250,
    buildings: dict[str, int] | None = None,
    ships: dict[str, int] | None = None,
    resources: Resources | None = None,
) -> PlanetState:
    bmap = dict(buildings or {})
    return PlanetState(
        planet_id=planet_id,
        name=name,
        coords=coords,
        planet_type=planet_type,
        temperature=temperature,
        fields_used=sum(bmap.values()),
        fields_max=fields_max,
        resources=resources or Resources(50_000, 25_000, 20_000, 10_000),
        storage=Resources(200_000, 200_000, 200_000, 200_000),
        energy=EnergyBalance(produced=500, used=300),
        buildings=bmap,
        ships=dict(ships or {}),
    )


def _fast_params(**overrides: object) -> dict[str, object]:
    """Small deterministic search budget for fast unit tests."""
    base: dict[str, object] = {
        "catalogue_grid_size": 512,
        "catalogue_priority_steps": 2,
        "hill_climb_step_sizes": [1, 2],
        "ga_population_size": 24,
        "ga_generations": 15,
        "ga_random_seed": 20261117,
        "central_planet_min_warehouse_level": 8,
        "optimized_building_max_levels": {
            "b1": 35,
            "b2": 35,
            "b3": 35,
            "b4": 35,
            "b5": 35,
            "b9": 20,
            "b17": 10,
        },
    }
    base.update(overrides)
    return base


class ConfigOptimizerUnitTests(unittest.TestCase):
    def test_zero_forbidden_dependencies(self) -> None:
        source = MODULE_PATH.read_text(encoding="utf-8")
        tree = ast.parse(source)
        forbidden = {"pandas", "selenium", "IPython", "jupyter", "requests"}
        found: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    found.add(alias.name.split(".")[0])
            elif isinstance(node, ast.ImportFrom) and node.module:
                found.add(node.module.split(".")[0])
        self.assertEqual(found & forbidden, set())
        self.assertNotIn("9222", source)
        self.assertNotIn("spaceinvasion.db", source)

    def test_default_params_match_contract(self) -> None:
        d = co.DEFAULT_CONFIG_OPTIMIZER_PARAMS
        self.assertEqual(d["target_ship_key"], "s16")
        self.assertEqual(d["production_month_days"], 30.0)
        self.assertEqual(d["research_percent_of_universe_max"], 100.0)
        self.assertEqual(d["min_fleet_per_planet"], {"s2": 10, "s10": 10, "s11": 10})
        self.assertEqual(d["misc_building_levels_by_slot"], {"1": {"b8": 12}})
        self.assertEqual(d["warehouse_hours_of_production"], 24.0)
        self.assertEqual(d["central_planet_slot"], 1)
        self.assertEqual(d["central_planet_min_warehouse_level"], 15)
        self.assertEqual(d["catalogue_grid_size"], 25000)
        self.assertEqual(d["catalogue_priority_steps"], 4)
        self.assertEqual(d["hill_climb_step_sizes"], [1, 2, 4, 8])
        self.assertEqual(d["ga_population_size"], 200)
        self.assertEqual(d["ga_generations"], 150)
        self.assertEqual(d["ga_tournament_size"], 3)
        self.assertEqual(d["ga_crossover_rate"], 0.9)
        self.assertEqual(d["ga_mutation_rate"], 0.1)
        self.assertEqual(d["ga_elite_count"], 4)
        self.assertEqual(d["ga_random_seed"], 20261117)
        self.assertFalse(d["pad_generic_colonies"])
        self.assertFalse(d["allow_demolition"])
        self.assertEqual(d["max_planets"], 11)
        self.assertEqual(d["empty_colony_fields"], 300)
        self.assertEqual(d["reserved_floors_by_planet"], {})
        self.assertEqual(d["planet_specializations"], {})
        self.assertEqual(d["planet_names"], {})
        self.assertTrue(d["preserve_previous_specializations"])
        self.assertEqual(d["specialization_switch_threshold_pct"], 0.5)
        all_bkeys = set(d["misc_building_levels"]) | set(d["optimized_building_max_levels"])
        self.assertEqual(all_bkeys, {f"b{bid}" for bid in game_data.BUILDING_BY_ID})
        self.assertEqual(set(d["misc_building_levels"]) & set(d["optimized_building_max_levels"]), set())

    def test_monotonic_base_times_repairs_wiki_truncation(self) -> None:
        bf_levels = game_data.BUILDING_BY_ID[2]["levels"]
        repaired = co.build_monotonic_base_times(bf_levels, 100)
        self.assertEqual(len(repaired), 100)
        for lvl in range(2, 101):
            self.assertGreaterEqual(repaired[lvl], repaired[lvl - 1])
        # Level 69 in raw wiki data drops to ~1.3366s; repaired must stay huge.
        self.assertGreater(repaired[69], repaired[68])

    def test_warehouse_and_build_time_helpers(self) -> None:
        self.assertEqual(co.warehouse_level_for(0), 0)
        self.assertEqual(co.warehouse_level_for(100_000), 0)
        self.assertEqual(co.warehouse_level_for(100_001), 1)
        self.assertEqual(co.warehouse_level_for(200_000), 1)
        self.assertEqual(co.warehouse_level_for(200_001), 2)

        planned = {"b1": 10, "b7": 12, "b17": 3}
        # Self-upgrade of b7 uses b7-1 = 11; self-upgrade of b17 uses b17-1 = 2.
        t_b7 = co.last_level_build_time("b7", planned, 5.0)
        t_b17 = co.last_level_build_time("b17", planned, 5.0)
        b7_base = co.build_monotonic_base_times(game_data.BUILDING_BY_ID[7]["levels"], 12)[12]
        b17_base = co.build_monotonic_base_times(game_data.BUILDING_BY_ID[17]["levels"], 3)[3]
        self.assertAlmostEqual(t_b7, b7_base / ((11 + 1) * (2**3) * 5.0), places=6)
        self.assertAlmostEqual(t_b17, b17_base / ((12 + 1) * (2**2) * 5.0), places=6)

        # SpaceInvasion Classic invariant: Blast furnace (b2) is NOT boosted by Mining tech (r1).
        ctx0 = {"speedup_factor": 5.0, "planet_temperature_c": 20, "r1_mining_tech": 0, "r2_energy_tech": 0}
        ctx10 = {"speedup_factor": 5.0, "planet_temperature_c": 20, "r1_mining_tech": 10, "r2_energy_tech": 0}
        self.assertAlmostEqual(
            co.building_level_economy(2, 10, ctx0)["production"],
            co.building_level_economy(2, 10, ctx10)["production"],
            places=6,
        )
        self.assertAlmostEqual(
            co.building_level_economy(1, 10, ctx10)["production"],
            co.building_level_economy(1, 10, ctx0)["production"] * 1.20,
            places=6,
        )

    def test_hard_built_floors_and_reserved_floors_enforced(self) -> None:
        p1 = _make_planet(
            101,
            "02:380:12",
            "Homeworld",
            temperature=30,
            fields_max=280,
            buildings={"b1": 22, "b2": 14, "b3": 16, "b4": 11, "b5": 18, "b6": 4, "b7": 12, "b8": 12, "b15": 2},
        )
        p2 = _make_planet(
            102,
            "02:381:04",
            "IceColony",
            temperature=-40,
            fields_max=300,
            buildings={"b1": 15, "b3": 19, "b4": 14, "b5": 15, "b7": 12, "b10": 6},
        )
        state = GameState(
            universe_id=31,
            user_id=1,
            nickname="Tester",
            timestamp=1_700_000_000.0,
            planets=(p1, p2),
            active_planet_id=101,
            researches={"r1": 8, "r2": 10},
            speedup_factor=5.0,
        )
        reserved = {
            "101": {"b6": 6, "b3": 20, "b11": 12},
            "02:381:04": {"b10": 9, "b1": 18, "b14": 11},
        }
        result = co.run_config_optimizer(
            state,
            universe_research_max={"r1": 15, "r2": 15, "r6": 12, "r15": 2},
            params=_fast_params(
                reserved_floors_by_planet=reserved,
                # Even with a low max_level cap below built_level, built_level must never drop!
                optimized_building_max_levels={
                    "b1": 15,
                    "b2": 30,
                    "b3": 30,
                    "b4": 30,
                    "b5": 35,
                    "b9": 20,
                    "b17": 8,
                },
            ),
        )
        self.assertEqual(result["schema_version"], 1)
        self.assertEqual(len(result["planets"]), 2)
        self.assertGreater(result["summary"]["ships_per_month"], 0.0)

        p1_rec, p2_rec = result["planets"]
        p1_targets = {row["key"]: row["target_level"] for row in p1_rec["buildings"]}
        p2_targets = {row["key"]: row["target_level"] for row in p2_rec["buildings"]}

        # Every built level and reserved floor on p1 is preserved as a hard floor.
        self.assertGreaterEqual(p1_targets["b1"], 22)
        self.assertGreaterEqual(p1_targets["b2"], 14)
        self.assertGreaterEqual(p1_targets["b3"], 20)
        self.assertGreaterEqual(p1_targets["b4"], 11)
        self.assertGreaterEqual(p1_targets["b5"], 18)
        self.assertGreaterEqual(p1_targets["b6"], 6)
        self.assertGreaterEqual(p1_targets["b11"], 12)
        self.assertGreaterEqual(p1_targets["b15"], 2)
        self.assertEqual(p1_rec["built_floors"]["b1"], 22)
        self.assertEqual(p1_rec["reserved_floors"]["b6"], 6)

        # Every built level and reserved floor on p2 (keyed by coords) is preserved.
        self.assertGreaterEqual(p2_targets["b1"], 18)
        self.assertGreaterEqual(p2_targets["b3"], 19)
        self.assertGreaterEqual(p2_targets["b4"], 14)
        self.assertGreaterEqual(p2_targets["b10"], 9)
        self.assertGreaterEqual(p2_targets["b14"], 11)
        self.assertEqual(p2_rec["reserved_floors"]["b10"], 9)

        # Export produces valid strict JSON without NaN/Infinity.
        raw_json = co.export_config_optimizer_json(result)
        parsed = json.loads(raw_json)
        self.assertEqual(parsed["schema_version"], 1)
        self.assertEqual(parsed["planets"][0]["planet_id"], 101)

    def test_pad_generic_colonies_toggle_and_non_normal_exclusion(self) -> None:
        p1 = _make_planet(101, "02:380:12", "Homeworld", fields_max=260, buildings={"b1": 10, "b5": 10})
        ast_p = _make_planet(201, "02:380:12", "Homeworld Asteroid", planet_type="asteroid", fields_max=30)
        raid_p = _make_planet(301, "02:390:05", "Raid Outpost", planet_type="raid", fields_max=50)
        state = GameState(
            universe_id=31,
            user_id=1,
            nickname="Tester",
            timestamp=1_700_000_000.0,
            planets=(p1, ast_p, raid_p),
            active_planet_id=101,
            researches={"r1": 5, "r2": 5},
            speedup_factor=5.0,
        )

        res_owned = co.run_config_optimizer(
            state,
            params=_fast_params(pad_generic_colonies=False, max_planets=4, empty_colony_fields=300),
        )
        self.assertEqual(len(res_owned["planets"]), 1)
        self.assertFalse(res_owned["planets"][0]["is_placeholder"])
        self.assertEqual(res_owned["planets"][0]["slot"], 1)

        res_padded = co.run_config_optimizer(
            state,
            params=_fast_params(pad_generic_colonies=True, max_planets=4, empty_colony_fields=300),
        )
        self.assertEqual(len(res_padded["planets"]), 4)
        self.assertFalse(res_padded["planets"][0]["is_placeholder"])
        self.assertTrue(all(p["is_placeholder"] for p in res_padded["planets"][1:]))
        self.assertTrue(all(p["fields_max"] == 300 for p in res_padded["planets"][1:]))
        self.assertGreater(res_padded["summary"]["ships_per_month"], res_owned["summary"]["ships_per_month"])

        # Cold-start state (0 owned planets) with pad_generic_colonies=True works; False raises ValueError.
        empty_state = GameState(
            universe_id=31,
            user_id=1,
            nickname="Empty",
            timestamp=1_700_000_000.0,
            planets=(),
            speedup_factor=5.0,
        )
        with self.assertRaises(ValueError):
            co.run_config_optimizer(empty_state, params=_fast_params(pad_generic_colonies=False))
        cold_padded = co.run_config_optimizer(
            empty_state,
            params=_fast_params(pad_generic_colonies=True, max_planets=3, empty_colony_fields=300),
        )
        self.assertEqual(len(cold_padded["planets"]), 3)
        self.assertTrue(all(p["is_placeholder"] for p in cold_padded["planets"]))

    def test_improve_design_is_nan_safe(self) -> None:
        p1 = _make_planet(101, "02:380:12", "Homeworld", fields_max=260)
        plan = co.build_planet_plan(
            slot_index=1,
            planet=p1,
            is_placeholder=False,
            optimized_max_levels={"b1": 20, "b2": 20, "b3": 20, "b4": 20, "b5": 20, "b9": 15, "b17": 5},
            misc_levels=co.DEFAULT_CONFIG_OPTIMIZER_PARAMS["misc_building_levels"],
            min_fleet={"s2": 10},
            reserved_floors={},
            central_min_warehouse_level=5,
        )
        ctx = {"speedup_factor": 5.0, "planet_temperature_c": 20, "r1_mining_tech": 5, "r2_energy_tech": 5}
        model = co.EmpireProductionModel(
            [plan],
            [ctx],
            optimized_keys=("b1", "b2", "b3", "b4", "b5", "b9", "b17"),
            target_ship_key="s16",
            month_days=30.0,
            warehouse_hours=24.0,
        )
        moves = co.planet_moves(7, (1,))
        start = model.min_levels[0].copy()

        # All-NaN score must terminate immediately without infinite loop.
        out_all_nan = co.improve_design(model, 0, start, lambda lim: np.full(len(lim), np.nan), moves)
        self.assertTrue(np.array_equal(out_all_nan, start))

        # Partial-NaN score ignores NaN neighbours and climbs finite ones.
        def mixed_score(lim: np.ndarray) -> np.ndarray:
            vals = lim[:, 0].copy()
            if len(vals) > 2:
                vals[0] = np.nan
            return vals

        out_mixed = co.improve_design(model, 0, start, mixed_score, moves)
        self.assertTrue(model.is_feasible(out_mixed[None, None, :], [0])[0])

    def test_determinism_progress_callback_and_validation(self) -> None:
        p1 = _make_planet(101, "02:380:12", "Homeworld", fields_max=260, buildings={"b1": 5, "b5": 5})
        state = GameState(
            universe_id=31,
            user_id=1,
            nickname="Tester",
            timestamp=1_700_000_000.0,
            planets=(p1,),
            active_planet_id=101,
            researches={"r1": 4, "r2": 4},
            speedup_factor=5.0,
        )
        events: list[tuple[str, float]] = []
        r1 = co.run_config_optimizer(
            state,
            params=_fast_params(),
            progress_cb=lambda phase, frac: events.append((phase, frac)),
        )
        r2 = co.run_config_optimizer(state, params=_fast_params())

        self.assertEqual(r1["summary"]["ships_per_month"], r2["summary"]["ships_per_month"])
        self.assertEqual(r1["planets"][0]["buildings"], r2["planets"][0]["buildings"])
        self.assertGreater(len(events), 3)
        self.assertEqual(events[-1], ("completed", 1.0))

        # Invalid settings must raise ValueError.
        with self.assertRaises(ValueError):
            co.run_config_optimizer(
                state,
                params=_fast_params(target_ship_key="s999"),
            )
        tiny_planet = _make_planet(101, "02:380:12", "Tiny", fields_max=10)
        tiny_state = GameState(
            universe_id=31,
            user_id=1,
            nickname="Tester",
            timestamp=1_700_000_000.0,
            planets=(tiny_planet,),
            active_planet_id=101,
            speedup_factor=5.0,
        )
        with self.assertRaises(ValueError):
            co.run_config_optimizer(tiny_state, params=_fast_params())

    def test_mathematical_parity_with_config_optimizer_notebook(self) -> None:
        """Cross-check `EmpireProductionModel` & `search_in_rounds` against `notebooks/config_optimizer.ipynb`."""
        nb = json.loads(NOTEBOOK_PATH.read_text(encoding="utf-8"))
        # Load pure helper & model functions from the notebook into an isolated namespace (no DB/file I/O).
        ns: dict[str, object] = {}
        # Cell 4: imports; Cell 12: catalog helpers; Cell 20: building economy; Cell 22: TECH_GRAPH;
        # Cell 30: build_planet_plan helpers; Cell 32: EmpireProductionModel; Cell 34: search functions.
        code_cells = [4, 12, 20, 22]
        for idx in code_cells:
            src = "".join(nb["cells"][idx]["source"])
            # Strip print_section_data tail calls and notebook-only globals
            lines = []
            for line in src.splitlines():
                if line.startswith("print_section_data("):
                    break
                lines.append(line)
            if idx == 4:
                ns["RESOURCE_KEYS"] = ("pig_iron", "metal", "kryptonite", "spice")
                ns["RESOURCE_METADATA"] = {
                    "pig_iron": {"mine_building_key": "b1", "warehouse_building_key": "b11"},
                    "metal": {"mine_building_key": "b2", "warehouse_building_key": "b12"},
                    "kryptonite": {"mine_building_key": "b3", "warehouse_building_key": "b13"},
                    "spice": {"mine_building_key": "b4", "warehouse_building_key": "b14"},
                }
                ns["technology_multiplier"] = co.technology_multiplier
                ns["planet_identity"] = lambda context: {
                    column: context[column] for column in ("slot_index", "planet_id", "coords", "planet_status")
                }
            exec("\n".join(lines), ns)

        # Extract pure defs from cells 30, 32, 34 without executing module-level notebook state
        for idx in (30, 32, 34):
            src = "".join(nb["cells"][idx]["source"])
            tree = ast.parse(src)
            kept: list[ast.stmt] = []
            for stmt in tree.body:
                if isinstance(stmt, (ast.FunctionDef, ast.ClassDef)):
                    kept.append(stmt)
                elif isinstance(stmt, ast.AnnAssign) and isinstance(stmt.target, ast.Name) and stmt.target.id in (
                    "WAREHOUSE_KEYS",
                    "PRODUCTION_FLOW_KEYS",
                ):
                    kept.append(stmt)
            mod = ast.Module(body=kept, type_ignores=[])
            exec(compile(mod, f"nb_cell_{idx}", "exec"), ns)

        # Compare EmpireProductionModel & search_in_rounds on identical 2 empty planets (no built floors).
        p1 = _make_planet(101, "02:380:12", "Homeworld", temperature=25, fields_max=250)
        p2 = _make_planet(102, "02:381:05", "Colony 1", temperature=-30, fields_max=280)
        state = GameState(
            universe_id=31,
            user_id=1,
            nickname="Parity",
            timestamp=1_700_000_000.0,
            planets=(p1, p2),
            active_planet_id=101,
            researches={"r1": 10, "r2": 10, "r6": 10, "r15": 2},
            speedup_factor=5.0,
        )
        params = _fast_params(
            central_planet_min_warehouse_level=10,
            ga_population_size=30,
            ga_generations=20,
            ga_random_seed=20261117,
        )
        ours = co.run_config_optimizer(
            state,
            universe_research_max={"r1": 10, "r2": 10, "r6": 10, "r15": 2},
            params=params,
        )

        # Build the exact same model via notebook functions.
        nb_contexts = [
            {
                "slot_index": 1,
                "planet_id": 101,
                "coords": "02:380:12",
                "planet_status": "colonized",
                "speedup_factor": 5.0,
                "r1_mining_tech": 10,
                "r2_energy_tech": 10,
                "planet_temperature_c": 25,
            },
            {
                "slot_index": 2,
                "planet_id": 102,
                "coords": "02:381:05",
                "planet_status": "colonized",
                "speedup_factor": 5.0,
                "r1_mining_tech": 10,
                "r2_energy_tech": 10,
                "planet_temperature_c": -30,
            },
        ]
        nb_plans = ns["build_planet_plans"](
            [p1, p2],
            nb_contexts,
            optimized_max_levels=params["optimized_building_max_levels"],
            misc_levels=co.DEFAULT_CONFIG_OPTIMIZER_PARAMS["misc_building_levels"],
            misc_levels_by_slot={1: {"b8": 12}},
            min_fleet=co.DEFAULT_CONFIG_OPTIMIZER_PARAMS["min_fleet_per_planet"],
            central_planet_slot=1,
            central_min_warehouse_level=10,
            research_costs=co.research_upgrade_costs(state.researches, ours["planned_research_levels"]),
        )
        nb_model = ns["EmpireProductionModel"](
            nb_plans,
            nb_contexts,
            optimized_keys=tuple(params["optimized_building_max_levels"]),
            target_ship_key="s16",
            month_days=30.0,
            warehouse_hours=24.0,
        )
        nb_best_model, _rounds, _cats, _gd, _gh, _gl, nb_best_levels = ns["search_in_rounds"](
            nb_model,
            grid_size=params["catalogue_grid_size"],
            priority_steps=params["catalogue_priority_steps"],
            step_sizes=tuple(params["hill_climb_step_sizes"]),
            population_size=params["ga_population_size"],
            generations=params["ga_generations"],
            tournament_size=3,
            crossover_rate=0.9,
            mutation_rate=0.1,
            elite_count=4,
            seed=20261117,
        )
        nb_ships = round(float(nb_best_model.ships_per_month(nb_best_levels[None])[0]), 2)
        self.assertAlmostEqual(ours["summary"]["ships_per_month"], nb_ships, places=2)

    def test_explicit_planet_specialization_locks_enforced(self) -> None:
        p1 = _make_planet(
            101,
            "02:380:01",
            "KryptWorld",
            temperature=10,
            fields_max=280,
            buildings={"b1": 10, "b2": 8, "b3": 14, "b4": 6, "b5": 12, "b7": 12, "b8": 12, "b9": 5, "b10": 5},
        )
        p2 = _make_planet(
            102,
            "02:380:02",
            "MetalWorld",
            temperature=10,
            fields_max=280,
            buildings={"b1": 14, "b2": 12, "b3": 8, "b4": 6, "b5": 12, "b7": 12, "b9": 7, "b10": 5},
        )
        p3 = _make_planet(
            103,
            "02:380:03",
            "ColdYard",
            temperature=-45,
            fields_max=280,
            buildings={"b1": 8, "b2": 6, "b3": 6, "b4": 12, "b5": 12, "b7": 12, "b9": 8, "b10": 5},
        )
        state = GameState(
            universe_id=31,
            user_id=1,
            nickname="Locks",
            timestamp=1_700_000_000.0,
            planets=(p1, p2, p3),
            active_planet_id=101,
            researches={"r1": 10, "r2": 10, "r6": 10, "r15": 2},
            speedup_factor=5.0,
        )
        res = co.run_config_optimizer(
            state,
            universe_research_max={"r1": 10, "r2": 10, "r6": 10, "r15": 2},
            params=_fast_params(
                central_planet_min_warehouse_level=10,
                planet_specializations={
                    "101": "kryptonite",
                    "02:380:02": "metal",
                    "103": "spice_shipyard",
                },
            ),
        )
        self.assertEqual(res["summary"]["locked_planets_count"], 3)
        by_pid = {p["planet_id"]: p for p in res["planets"]}

        # Planet 101 locked to kryptonite: only b3 & b5 may grow above built/prerequisite min floors
        self.assertEqual(by_pid[101]["locked_specialization"], "kryptonite")
        self.assertEqual(by_pid[101]["detected_specialization"], "kryptonite")
        p1_b = {row["key"]: row for row in by_pid[101]["buildings"]}
        self.assertGreater(p1_b["b3"]["target_level"], p1_b["b3"]["built_level"])
        for forbidden_key in ("b1", "b2", "b4", "b17"):
            if forbidden_key in p1_b:
                self.assertEqual(p1_b[forbidden_key]["target_level"], p1_b[forbidden_key]["built_level"])
        # b9 started at L5; min_fleet_per_planet requires b9 L6 prerequisite and never grows above L6
        self.assertEqual(p1_b["b9"]["target_level"], 6)

        # Planet 102 locked to metal: b1/b2 grow while b3/b4/b9/b17 stay strictly at built floors (b9=7 >= L6)
        self.assertEqual(by_pid[102]["locked_specialization"], "metal")
        self.assertEqual(by_pid[102]["detected_specialization"], "metal")
        p2_b = {row["key"]: row for row in by_pid[102]["buildings"]}
        self.assertGreater(p2_b["b2"]["target_level"], p2_b["b2"]["built_level"])
        for forbidden_key in ("b3", "b4", "b9", "b17"):
            if forbidden_key in p2_b:
                self.assertEqual(p2_b[forbidden_key]["target_level"], p2_b[forbidden_key]["built_level"])

        # Planet 103 locked to spice_shipyard: b1/b2/b3 stay at built floors
        self.assertEqual(by_pid[103]["locked_specialization"], "spice_shipyard")
        p3_b = {row["key"]: row for row in by_pid[103]["buildings"]}
        for forbidden_key in ("b1", "b2", "b3"):
            if forbidden_key in p3_b:
                self.assertEqual(p3_b[forbidden_key]["target_level"], p3_b[forbidden_key]["built_level"])

    def test_sticky_previous_plan_prevents_role_flips_and_prefers_sunk_cost(self) -> None:
        # 4 symmetric colonies: without stickiness, changing seed/warehouse hours can permute roles.
        planets = tuple(
            _make_planet(
                200 + i,
                f"02:380:{i:02d}",
                f"Twin-{i}",
                temperature=0,
                fields_max=280,
                buildings={"b1": 5, "b2": 5, "b3": 5, "b4": 5, "b5": 8, "b7": 12, "b10": 5},
            )
            for i in range(1, 5)
        )
        state = GameState(
            universe_id=31,
            user_id=1,
            nickname="Sticky",
            timestamp=1_700_000_000.0,
            planets=planets,
            active_planet_id=201,
            researches={"r1": 10, "r2": 10, "r6": 10, "r15": 2},
            speedup_factor=5.0,
        )
        res1 = co.run_config_optimizer(
            state,
            universe_research_max={"r1": 10, "r2": 10, "r6": 10, "r15": 2},
            params=_fast_params(
                central_planet_min_warehouse_level=10,
                warehouse_hours_of_production=24.0,
                ga_random_seed=20261117,
            ),
        )
        roles1 = {p["planet_id"]: p["detected_specialization"] for p in res1["planets"]}

        # Re-run with tweaked warehouse hours + different GA seed + previous_result: roles must stay pinned!
        res2 = co.run_config_optimizer(
            state,
            universe_research_max={"r1": 10, "r2": 10, "r6": 10, "r15": 2},
            params=_fast_params(
                central_planet_min_warehouse_level=10,
                warehouse_hours_of_production=26.0,
                ga_random_seed=999999,
                preserve_previous_specializations=True,
                specialization_switch_threshold_pct=1.0,
            ),
            previous_result=res1,
        )
        roles2 = {p["planet_id"]: p["detected_specialization"] for p in res2["planets"]}
        self.assertEqual(roles2, roles1)
        self.assertEqual(res2["summary"]["roles_changed_count"], 0)
        self.assertEqual(res2["summary"]["roles_preserved_count"], 4)
        self.assertTrue(all(not p["specialization_changed"] for p in res2["planets"]))

        # Regression guard: built mines must not bias detect_planet_specialization toward a smaller secondary mine.
        self.assertEqual(
            co.detect_planet_specialization(
                {"b1": 34, "b2": 35, "b3": 23, "b4": 17, "b9": 6, "b17": 0},
                built_levels={"b1": 22, "b2": 18, "b3": 0, "b4": 0, "b9": 0, "b17": 0},
                shipyard_ships=0.0,
            ),
            "metal",
        )

        # Sunk-cost test: on realistic field-constrained colonies with existing Kryptonite / Metal / Spice investments,
        # pairwise alignment preserves sunk cost without leaking unrelated built floors.
        sc_p1 = _make_planet(
            301,
            "02:381:01",
            "AlreadyKrypt",
            temperature=0,
            fields_max=220,
            buildings={"b3": 20, "b13": 13, "b5": 15, "b7": 12, "b8": 12, "b10": 5},
        )
        sc_p2 = _make_planet(
            302,
            "02:381:02",
            "AlreadyMetal",
            temperature=0,
            fields_max=220,
            buildings={"b1": 22, "b2": 18, "b12": 13, "b5": 15, "b7": 12, "b10": 5},
        )
        sc_p3 = _make_planet(
            303,
            "02:381:03",
            "ColdSpiceYard",
            temperature=-55,
            fields_max=220,
            buildings={"b4": 18, "b9": 12, "b17": 3, "b5": 15, "b7": 12, "b10": 5},
        )
        sc_state = GameState(
            universe_id=31,
            user_id=1,
            nickname="SunkCost",
            timestamp=1_700_000_000.0,
            planets=(sc_p1, sc_p2, sc_p3),
            active_planet_id=301,
            researches={"r1": 10, "r2": 10, "r6": 10, "r15": 2},
            speedup_factor=5.0,
        )
        sc_res = co.run_config_optimizer(
            sc_state,
            universe_research_max={"r1": 10, "r2": 10, "r6": 10, "r15": 2},
            params=_fast_params(
                central_planet_min_warehouse_level=10,
                optimized_building_max_levels={
                    "b1": 80,
                    "b2": 80,
                    "b3": 80,
                    "b4": 80,
                    "b5": 80,
                    "b9": 60,
                    "b17": 20,
                },
            ),
        )
        sc_roles = {p["planet_id"]: p["detected_specialization"] for p in sc_res["planets"]}
        self.assertEqual(sc_roles[301], "kryptonite")
        self.assertEqual(sc_roles[302], "metal")
        self.assertIn(sc_roles[303], {"spice", "spice_shipyard"})

        # Threshold override test: if previous plan had ALL planets as metal (0 kryptonite/spice/ships),
        # fresh plan beats sticky by >> threshold and unlocked planets cleanly adapt.
        bad_prev = copy.deepcopy(res1)
        for p in bad_prev["planets"]:
            p["detected_specialization"] = "metal"
        res_adapted = co.run_config_optimizer(
            state,
            universe_research_max={"r1": 10, "r2": 10, "r6": 10, "r15": 2},
            params=_fast_params(
                central_planet_min_warehouse_level=10,
                preserve_previous_specializations=True,
                specialization_switch_threshold_pct=0.5,
            ),
            previous_result=bad_prev,
        )
        self.assertFalse(res_adapted["summary"]["sticky_plan_applied"])
        self.assertGreater(res_adapted["summary"]["roles_changed_count"], 0)
        self.assertGreater(res_adapted["summary"]["ships_per_month"], 1.0)

    def test_custom_planet_names_preserved_from_state_params_and_exports(self) -> None:
        # Helper sanitization: whitespace, control chars, length cap (48), non-string/bool guards.
        self.assertEqual(co.normalize_planet_name("  Iron\t\nForge\x00\x07  "), "Iron Forge")
        self.assertEqual(co.normalize_planet_name(True), "")
        self.assertEqual(co.normalize_planet_name("   "), "")
        long_name = "A" * 80
        self.assertEqual(len(co.normalize_planet_name(long_name)), co.MAX_PLANET_NAME_LENGTH)

        p1 = _make_planet(101, "02:380:12", "Homeworld", temperature=25, fields_max=260, buildings={"b1": 8, "b5": 10})
        p2 = _make_planet(102, "02:381:05", "Colony 2", temperature=-10, fields_max=280, buildings={"b3": 8, "b5": 10})
        p3 = _make_planet(103, "02:382:09", "Colony 3", temperature=-45, fields_max=280, buildings={"b4": 8, "b5": 10})

        # Precedence check: planet_id > exact coords > normalized coords > slot:<n> (regardless of dict order).
        disp_norm, game_norm = co._resolve_planet_display_name(
            p2,
            2,
            {"slot:2": "SlotFallback", "2:381:5": "NormalizedCoordWins"},
        )
        self.assertEqual(disp_norm, "NormalizedCoordWins")
        self.assertEqual(game_norm, "Colony 2")

        disp_pid, _ = co._resolve_planet_display_name(
            p3,
            3,
            {"slot:3": "SlotName", "02:382:09": "ExactCoordName", "103": "PidWins"},
        )
        self.assertEqual(disp_pid, "PidWins")

        state = GameState(
            universe_id=31,
            user_id=1,
            nickname="Renamer",
            timestamp=1_700_000_000.0,
            planets=(p1, p2, p3),
            active_planet_id=101,
            researches={"r1": 6, "r2": 6, "r6": 6, "r15": 1},
            speedup_factor=5.0,
        )
        res = co.run_config_optimizer(
            state,
            universe_research_max={"r1": 8, "r2": 8, "r6": 8, "r15": 1},
            params=_fast_params(
                custom_planet_names={
                    "slot:2": "Fallback Slot 2",
                    "2:381:5": "Krypt\nForge",
                    "02:382:09": "Coord Overridden",
                },
                planet_names={
                    "103": "  Ice Shipyard  ",
                },
            ),
            planet_names={"101": "Titan Capital"},
        )

        by_pid = {p["planet_id"]: p for p in res["planets"]}
        self.assertEqual(by_pid[101]["name"], "Titan Capital")
        self.assertEqual(by_pid[101]["game_name"], "Homeworld")
        self.assertEqual(by_pid[102]["name"], "Krypt Forge")
        self.assertEqual(by_pid[102]["game_name"], "Colony 2")
        self.assertEqual(by_pid[103]["name"], "Ice Shipyard")
        self.assertEqual(by_pid[103]["game_name"], "Colony 3")

        self.assertEqual(
            res["planet_names"],
            {"101": "Titan Capital", "102": "Krypt Forge", "103": "Ice Shipyard"},
        )
        self.assertEqual(res["summary"]["central_planet_name"], "Titan Capital")
        self.assertEqual(res["summary"]["central_planet_coords"], "02:380:12")
        self.assertIn("Titan Capital", res["summary"]["central_planet"])

        # Strict JSON export preserves custom planet names cleanly.
        exported = json.loads(co.export_config_optimizer_json(res))
        self.assertEqual(exported["planet_names"]["101"], "Titan Capital")
        self.assertEqual(exported["planets"][1]["name"], "Krypt Forge")
        self.assertEqual(exported["planets"][1]["game_name"], "Colony 2")
        self.assertEqual(exported["summary"]["central_planet_name"], "Titan Capital")

        # Downstream AI-Planner inherits custom names from target_config cleanly.
        from spaceinvasion.planner import ai_planner as ap

        ai_plan = ap.run_ai_planner(
            state,
            res,
            params={"reward_weeks": 2, "ga_generations": 0},
        )
        step_names_by_pid = {
            int(s["planet_id"]): s["planet_name"]
            for s in ai_plan["steps"]
            if s.get("kind") == "BUILD" and int(s.get("planet_id") or 0) > 0
        }
        self.assertTrue(step_names_by_pid)
        for pid_k, pname in step_names_by_pid.items():
            self.assertEqual(pname, res["planet_names"][str(pid_k)])

    def test_min_warehouse_level_enforced_across_all_resources_and_colonies(self) -> None:
        p1 = _make_planet(401, "02:380:01", "Hub", temperature=20, fields_max=280, buildings={"b1": 10, "b5": 10})
        p2 = _make_planet(402, "02:380:02", "MetalCol", temperature=10, fields_max=280, buildings={"b1": 12, "b2": 10, "b5": 12})
        p3 = _make_planet(403, "02:380:03", "SpiceCol", temperature=-40, fields_max=280, buildings={"b4": 12, "b5": 12})
        state = GameState(
            universe_id=31,
            user_id=1,
            nickname="WhMin",
            timestamp=1_700_000_000.0,
            planets=(p1, p2, p3),
            active_planet_id=401,
            researches={"r1": 8, "r2": 8, "r6": 8, "r15": 1},
            speedup_factor=5.0,
        )
        res = co.run_config_optimizer(
            state,
            universe_research_max={"r1": 8, "r2": 8, "r6": 8, "r15": 1},
            params=_fast_params(
                min_warehouse_level=12,
                central_planet_min_warehouse_level=15,
                optimized_building_max_levels={
                    "b1": 25,
                    "b2": 25,
                    "b3": 25,
                    "b4": 25,
                    "b5": 25,
                    "b9": 20,
                    "b17": 5,
                },
            ),
        )
        self.assertEqual(res["meta_parameters"]["min_warehouse_level"], 12)
        for pl in res["planets"]:
            b_by_key = {row["key"]: row["target_level"] for row in pl["buildings"]}
            min_expected = 15 if pl["is_central_planet"] else 12
            for wh_key in ("b11", "b12", "b13", "b14"):
                self.assertGreaterEqual(
                    b_by_key[wh_key],
                    min_expected,
                    f"{pl['coords']} {wh_key}={b_by_key[wh_key]} < {min_expected}",
                )
        non_central_wh_drivers = {
            w["set_by"]
            for pl in res["planets"]
            if not pl["is_central_planet"]
            for w in pl["warehouses"]
        }
        self.assertIn("minimum warehouse level", non_central_wh_drivers)

        with self.assertRaises(ValueError):
            co.run_config_optimizer(
                state,
                params=_fast_params(min_warehouse_level=-1),
            )
        with self.assertRaises(ValueError):
            co.run_config_optimizer(
                state,
                params=_fast_params(min_warehouse_level=61),
            )

    def test_allow_demolition_reclaims_off_role_mines_while_honoring_reserved_floors(self) -> None:
        # Tight 230-field shipyard planet loaded with off-role mines + b6 Thermosolar,
        # plus 5 Metal colonies + Kryptonite + Spice colonies so shipyard & central reserve bind.
        p1 = _make_planet(
            501,
            "02:380:12",
            "PP Planet",
            temperature=10,
            fields_max=230,
            buildings={
                "b1": 22,
                "b2": 22,
                "b3": 21,
                "b4": 17,
                "b5": 21,
                "b6": 3,
                "b7": 9,
                "b8": 8,
                "b9": 8,
                "b10": 7,
                "b11": 7,
                "b12": 4,
                "b13": 6,
                "b14": 6,
            },
            ships={"s2": 100, "s10": 10, "s11": 100},
        )
        metal_colonies = [
            _make_planet(
                502 + idx,
                f"02:379:0{idx + 1}",
                f"Metal Colony {idx + 1}",
                temperature=20,
                fields_max=330,
                buildings={"b1": 23, "b2": 17, "b3": 15, "b4": 10, "b5": 21, "b7": 12, "b9": 6, "b10": 5},
                ships={"s2": 100, "s10": 10, "s11": 100},
            )
            for idx in range(5)
        ]
        p_krypt = _make_planet(
            510,
            "02:379:06",
            "Krypt Colony",
            temperature=0,
            fields_max=320,
            buildings={"b1": 15, "b2": 16, "b3": 21, "b4": 15, "b5": 20, "b7": 12, "b10": 5},
            ships={"s2": 100, "s10": 10, "s11": 100},
        )
        p_spice = _make_planet(
            511,
            "02:381:05",
            "Spice Colony",
            temperature=-40,
            fields_max=304,
            buildings={"b1": 9, "b2": 8, "b3": 7, "b4": 18, "b5": 18, "b7": 12, "b10": 5},
            ships={"s2": 100, "s10": 10, "s11": 100},
        )
        state = GameState(
            universe_id=31,
            user_id=1,
            nickname="DemolishTester",
            timestamp=1_700_000_000.0,
            planets=(p1, *metal_colonies, p_krypt, p_spice),
            active_planet_id=501,
            researches={"r1": 12, "r2": 9, "r6": 5, "r15": 1},
            speedup_factor=20.0,
        )
        specs = {
            "501": "spice_shipyard",
            **{str(502 + idx): "metal" for idx in range(5)},
            "510": "kryptonite",
            "511": "spice",
        }
        uni_max = {"r1": 24, "r2": 22, "r6": 20, "r15": 2}
        res_no_dem = co.run_config_optimizer(
            state,
            universe_research_max=uni_max,
            params=_fast_params(
                allow_demolition=False,
                hill_climb_step_sizes=[1, 2, 4, 8],
                min_warehouse_level=15,
                central_planet_min_warehouse_level=15,
                warehouse_hours_of_production=72.0,
                planet_specializations=specs,
                optimized_building_max_levels={
                    "b1": 80,
                    "b2": 80,
                    "b3": 80,
                    "b4": 80,
                    "b5": 80,
                    "b9": 40,
                    "b17": 15,
                },
            ),
        )
        self.assertFalse(res_no_dem["summary"]["allow_demolition"])
        self.assertEqual(res_no_dem["summary"]["levels_to_demolish"], 0)
        # Central-reserve overshoot convergence ensures Slot 1 uses its fields instead of stranding at b9=13, b17=6 (26,406)!
        p1_no_dem = res_no_dem["planets"][0]
        p1_nd_targets = {r["key"]: r["target_level"] for r in p1_no_dem["buildings"]}
        self.assertGreater(res_no_dem["summary"]["ships_per_month"], 50_000.0)
        self.assertGreater(p1_nd_targets["b9"] + p1_nd_targets["b17"], 19)
        self.assertTrue(all(r["holds_the_production"] for r in res_no_dem["search"]["rounds"][-1:]))

        res_dem = co.run_config_optimizer(
            state,
            universe_research_max=uni_max,
            params=_fast_params(
                allow_demolition=True,
                hill_climb_step_sizes=[1, 2, 4, 8],
                min_warehouse_level=15,
                central_planet_min_warehouse_level=15,
                warehouse_hours_of_production=72.0,
                planet_specializations=specs,
                reserved_floors_by_planet={"501": {"b3": 5}},
                optimized_building_max_levels={
                    "b1": 80,
                    "b2": 80,
                    "b3": 80,
                    "b4": 80,
                    "b5": 80,
                    "b9": 40,
                    "b17": 15,
                },
            ),
        )
        self.assertTrue(res_dem["summary"]["allow_demolition"])
        self.assertGreater(res_dem["summary"]["levels_to_demolish"], 0)
        self.assertGreater(
            res_dem["summary"]["ships_per_month"],
            res_no_dem["summary"]["ships_per_month"],
        )
        p1_dem = res_dem["planets"][0]
        p1_rows = {r["key"]: r for r in p1_dem["buildings"]}
        # Off-role b1/b2 and b6 Thermosolar cleared to 0; reserved floor b3 >= 5 honored!
        self.assertEqual(p1_rows["b1"]["target_level"], 0)
        self.assertEqual(p1_rows["b1"]["levels_to_demolish"], 22)
        self.assertEqual(p1_rows["b2"]["target_level"], 0)
        self.assertEqual(p1_rows["b6"]["target_level"], 0)
        self.assertEqual(p1_rows["b3"]["target_level"], 5)
        self.assertEqual(p1_rows["b3"]["levels_to_demolish"], 16)

    def test_manual_planet_override_yearly_projection_and_ingame_planet_name_sync(self) -> None:
        from unittest.mock import MagicMock
        from spaceinvasion.runtime.api_server import GameApiBridge
        from spaceinvasion.session.executor import ActionExecutor

        p1 = _make_planet(
            601,
            "02:380:12",
            "Colony",
            temperature=20,
            fields_max=260,
            buildings={"b1": 18, "b2": 16, "b3": 16, "b4": 14, "b5": 20, "b7": 10, "b8": 10, "b9": 10},
        )
        p2 = _make_planet(
            602,
            "02:380:08",
            "Colony",
            temperature=-20,
            fields_max=300,
            buildings={"b1": 14, "b3": 16, "b4": 16, "b5": 18, "b7": 8},
        )
        state = GameState(
            universe_id=31,
            user_id=7,
            nickname="Architect",
            timestamp=1_700_000_000.0,
            planets=(p1, p2),
            active_planet_id=601,
            researches={"r1": 8, "r2": 8, "r3": 8, "r6": 8, "r13": 8, "r15": 1},
            speedup_factor=10.0,
        )

        # 1. Manual planet override updates target buildings, auto-raises b7 prereq for b17, and pins for future GA runs
        manual_res = co.apply_manual_planet_override(
            state,
            slot=2,
            target_buildings={"b3": 24, "b4": 26, "b5": 26, "b9": 14, "b17": 2, "b13": 10, "b14": 10},
            planet_name="Starforge II",
            locked_specialization="spice_shipyard",
            lock_for_ga=True,
            universe_research_max={"r1": 12, "r2": 12, "r3": 10, "r6": 12, "r13": 12, "r15": 3},
            params=_fast_params(max_planets=2, central_planet_min_warehouse_level=10),
        )
        p2_rec = manual_res["planets"][1]
        p2_blds = {r["key"]: r["target_level"] for r in p2_rec["buildings"]}
        self.assertEqual(p2_rec["name"], "Starforge II")
        self.assertEqual(p2_rec["game_name"], "Colony")
        self.assertTrue(p2_rec["manual_pinned"])
        self.assertEqual(p2_blds["b4"], 26)
        self.assertEqual(p2_blds["b9"], 14)
        self.assertEqual(p2_blds["b17"], 2)
        self.assertGreaterEqual(p2_blds["b7"], 12)  # Auto-raised b7 prereq for b17!

        # Over-field manual override raises ValueError cleanly
        with self.assertRaises(ValueError):
            co.apply_manual_planet_override(
                state,
                base_result=manual_res,
                slot=2,
                target_buildings={"b1": 95, "b2": 95, "b3": 95, "b5": 95},
            )

        # Pinned manual planet survives subsequent GA run while Slot 1 optimizes around it
        ga_after_pin = co.run_config_optimizer(
            state,
            universe_research_max={"r1": 12, "r2": 12, "r3": 10, "r6": 12, "r13": 12, "r15": 3},
            params=manual_res["meta_parameters"],
            previous_result=manual_res,
        )
        ga_p2_blds = {r["key"]: r["target_level"] for r in ga_after_pin["planets"][1]["buildings"]}
        self.assertEqual(ga_p2_blds["b4"], 26)
        self.assertEqual(ga_p2_blds["b9"], 14)
        self.assertEqual(ga_p2_blds["b17"], 2)

        # 2. Year-by-year Ships/Week projection produces 5 years (261 weekly points) climbing toward target
        proj = co.compute_yearly_ships_per_week_projection(ga_after_pin, state, horizon_years=5)
        self.assertEqual(proj["horizon_years"], 5)
        self.assertEqual(len(proj["years"]), 5)
        self.assertEqual(len(proj["weeks"]), 5 * 52 + 1)
        self.assertGreater(proj["target_ships_per_week"], 0.0)
        self.assertGreaterEqual(
            proj["years"][-1]["end_ships_per_week"],
            proj["years"][0]["start_ships_per_week"],
        )
        self.assertGreater(proj["years"][-1]["cumulative_ships_produced"], 0.0)

        # 3. ActionExecutor.sync_planet_names_in_game submits internalSettings form & NEVER clicks delete triggers
        settings_before = """
        <html><body>
          <form method="POST">
            <table>
              <tr>
                <td><b>Pos. 1</b></td>
                <td><input name="planet_601" type="text" value="Colony" maxlength="24"/></td>
                <td>02:380:12</td>
                <td><a onclick="confirmPlanetDeletion('02:380:12', 601)">Delete</a></td>
              </tr>
              <tr>
                <td><b>Pos. 2</b></td>
                <td><input name="planet_602" type="text" value="Colony" maxlength="24"/></td>
                <td>02:380:08</td>
                <td><a onclick="confirmPlanetDeletion('02:380:08', 602)">Delete</a></td>
              </tr>
            </table>
            <input type="submit" value="Save"/>
          </form>
        </body></html>
        """
        settings_after = settings_before.replace('name="planet_602" type="text" value="Colony"', 'name="planet_602" type="text" value="Starforge II"')
        executed_scripts: list[tuple[str, Any]] = []

        fake_client = MagicMock()
        fake_client.navigate_menu.return_value = settings_before
        fake_client._wait_frame_ready.return_value = settings_after

        def _fake_exec_script(script: str, *args: Any) -> Any:
            executed_scripts.append((script, args))
            if "const updates = arguments[0]" in script:
                return {"ok": True, "method": "submit_button", "applied": args[0]}
            return 1

        fake_client.driver.execute_script.side_effect = _fake_exec_script
        updated_p2 = dataclasses.replace(p2, name="Starforge II")
        fake_client.read_planets_overview.return_value = dataclasses.replace(state, planets=(p1, updated_p2))

        executor = ActionExecutor(action_delay_range=(0.0, 0.0))
        sync_res = executor.sync_planet_names_in_game(fake_client, state, {602: "Starforge II"})
        self.assertTrue(sync_res["ok"])
        self.assertEqual(sync_res["synced_count"], 1)
        self.assertEqual(sync_res["synced"][0]["game_name"], "Starforge II")
        self.assertTrue(sync_res["synced"][0]["verified"])
        for scr, _a in executed_scripts:
            self.assertNotIn("executeDeletePlanet", scr)
            self.assertNotIn("confirmPlanetDeletion", scr)

        # 4. GameApiBridge commands: rename_planet preserves true game_name, sync_planet_names updates game_name,
        #    and config_optimizer_update_planet + config_optimizer_yearly_projection work end-to-end.
        fake_client_cm = MagicMock()
        fake_client_cm.__enter__.return_value = fake_client
        fake_client_cm.__exit__.return_value = False
        bridge = GameApiBridge(
            debug_port=9222,
            client_factory=lambda _port: fake_client_cm,
            ensure_session_fn=lambda: None,
            db_path=None,
        )
        bridge._last_state = state
        bridge._live_executor = executor

        ren_out = bridge.handle_command({"command": "rename_planet", "planet_id": 602, "name": "Starforge II"})
        self.assertTrue(ren_out["ok"])
        self.assertEqual(ren_out["name"], "Starforge II")
        self.assertEqual(ren_out["game_name"], "Colony")

        upd_out = bridge.handle_command(
            {
                "command": "config_optimizer_update_planet",
                "slot": 2,
                "planet_id": 602,
                "planet_name": "Starforge II",
                "locked_specialization": "spice_shipyard",
                "target_buildings": {"b4": 25, "b5": 24, "b9": 13, "b17": 1},
                "auto_replan_ai": False,
            }
        )
        self.assertTrue(upd_out["ok"])
        # Before in-game sync, game_name remains "Colony" while display name is "Starforge II"
        self.assertEqual(upd_out["result"]["planets"][1]["name"], "Starforge II")
        self.assertEqual(upd_out["result"]["planets"][1]["game_name"], "Colony")

        proj_out = bridge.handle_command({"command": "config_optimizer_yearly_projection", "horizon_years": 3})
        self.assertTrue(proj_out["ok"])
        self.assertEqual(len(proj_out["projection"]["years"]), 3)

        sync_api_out = bridge.handle_command({"command": "sync_planet_names"})
        self.assertTrue(sync_api_out["ok"])
        self.assertEqual(sync_api_out["synced_count"], 1)
        co_get_after = bridge.handle_command({"command": "config_optimizer_get"})
        self.assertEqual(co_get_after["active"]["result"]["planets"][1]["name"], "Starforge II")
        self.assertEqual(co_get_after["active"]["result"]["planets"][1]["game_name"], "Starforge II")


if __name__ == "__main__":
    unittest.main()
