"""Unit & notebook mathematical parity tests for `spaceinvasion/planner/config_optimizer.py`."""

from __future__ import annotations

import ast
import copy
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


if __name__ == "__main__":
    unittest.main()
