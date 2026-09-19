"""Unit & notebook mathematical parity tests for `spaceinvasion/planner/ai_planner.py`."""

from __future__ import annotations

import ast
import json
from pathlib import Path
import unittest

import numpy as np

from spaceinvasion.core import game_data
from spaceinvasion.core.config import StrategyConfig
from spaceinvasion.core.state import (
    BuildingOffer,
    EnergyBalance,
    GameState,
    PlanetState,
    QueueJob,
    ResearchOffer,
    Resources,
)
from spaceinvasion.planner import ai_planner as ap
from spaceinvasion.planner import config_optimizer as co

REPO_ROOT = Path(__file__).resolve().parents[2]
MODULE_PATH = REPO_ROOT / "spaceinvasion" / "planner" / "ai_planner.py"
NOTEBOOK_PATH = REPO_ROOT / "notebooks" / "full_planner.ipynb"


def _make_planet(
    planet_id: int,
    coords: str,
    name: str,
    *,
    planet_type: str = "normal",
    temperature: int = 20,
    fields_max: int = 250,
    fields_used: int | None = None,
    buildings: dict[str, int] | None = None,
    resources: Resources | None = None,
    storage: Resources | None = None,
    building_offers: tuple[BuildingOffer, ...] = (),
) -> PlanetState:
    bmap = dict(buildings or {})
    return PlanetState(
        planet_id=planet_id,
        name=name,
        coords=coords,
        planet_type=planet_type,
        temperature=temperature,
        fields_used=sum(bmap.values()) if fields_used is None else fields_used,
        fields_max=fields_max,
        resources=resources or Resources(20_000, 10_000, 10_000, 5_000),
        storage=storage or Resources(400_000, 400_000, 400_000, 400_000),
        energy=EnergyBalance(produced=400, used=200),
        buildings=bmap,
        building_offers=building_offers,
    )


def _sample_target_config(
    planets_spec: list[dict[str, object]],
    *,
    planned_research: dict[str, int] | None = None,
    speedup: float = 5.0,
) -> dict[str, object]:
    res = {f"r{i}": 0 for i in range(1, 16)}
    if planned_research:
        res.update(planned_research)
    return {
        "schema_version": 1,
        "generated_at": "2026-11-18T01:00:00+00:00",
        "meta_parameters": {
            "min_fleet_per_planet": {"s2": 10, "s10": 10, "s11": 10},
        },
        "inputs": {"universe_speedup_factor": speedup},
        "planned_research_levels": res,
        "summary": {"target_ship": "Imperial starbase", "ships_per_month": 10.0},
        "planets": planets_spec,
    }


def _target_planet_entry(
    slot: int,
    planet_id: int,
    coords: str,
    name: str,
    targets: dict[str, int],
    *,
    is_placeholder: bool = False,
    is_central: bool = False,
    pays_for_research: bool = False,
    temperature: int = 20,
    fields_max: int = 250,
) -> dict[str, object]:
    return {
        "slot": slot,
        "slot_index": slot,
        "planet_id": planet_id,
        "coords": coords,
        "name": name,
        "is_placeholder": is_placeholder,
        "planet_status": "empty_colony_slot" if is_placeholder else "colonized",
        "is_central_planet": is_central,
        "pays_for_research": pays_for_research,
        "temperature": temperature,
        "temperature_c": temperature,
        "fields_max": fields_max,
        "buildings": [{"key": k, "target_level": v} for k, v in targets.items()],
    }


class AiPlannerUnitTests(unittest.TestCase):
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
        d = ap.DEFAULT_AI_PLANNER_PARAMS
        self.assertEqual(d["reward_weeks"], 26)
        self.assertEqual(d["weekly_discount"], 0.90)
        self.assertEqual(
            d["resource_spare_percent"],
            {"pig_iron": 10.0, "metal": 10.0, "kryptonite": 10.0, "spice": 10.0},
        )
        self.assertIsNone(d["fleet_command_center_level"])
        self.assertIsNone(d["attacks_per_day"])
        self.assertEqual(d["scarcity_focus"], 1.0)
        self.assertEqual(d["critical_queue_share"], 0.9)
        self.assertEqual(d["patience"], 0.25)
        self.assertEqual(d["speed_weight"], 1.5)
        self.assertEqual(d["revalue_every_steps"], 25)
        self.assertEqual(d["search_tolerance"], 0.01)
        self.assertEqual(d["ga_population_size"], 10)
        self.assertEqual(d["ga_generations"], 6)
        self.assertEqual(d["ga_tournament_size"], 3)
        self.assertEqual(d["ga_crossover_rate"], 0.9)
        self.assertEqual(d["ga_mutation_rate"], 0.3)
        self.assertEqual(d["ga_elite_count"], 2)
        self.assertEqual(d["ga_random_seed"], 20261117)
        self.assertFalse(d["pad_generic_colonies"])

    def test_compute_attack_telemetry_defaults_medians_and_battle_pairing(self) -> None:
        p1 = _make_planet(101, "02:380:12", "Homeworld", buildings={"b10": 4})
        p2 = _make_planet(102, "02:381:04", "Colony 1", buildings={"b10": 2})
        raid_p = _make_planet(103, "02:382:01", "Raid", planet_type="raid", buildings={"b10": 5})
        state = GameState(
            universe_id=31,
            user_id=1,
            nickname="Tester",
            timestamp=1_700_000_000.0,
            planets=(p1, p2, raid_p),
            active_planet_id=101,
            speedup_factor=5.0,
        )

        # Empty history -> 1800.0s default RT, normal fleet slots = (1+4) + (1+2) = 8.
        empty = ap.compute_attack_telemetry([], None, state=state)
        self.assertEqual(empty["sample_count"], 0)
        self.assertEqual(empty["median_round_trip_seconds"], 1800.0)
        self.assertEqual(empty["current_fleet_slots"], 8)
        self.assertEqual(empty["estimated_attacks_per_hour"], 0.0)
        self.assertEqual(empty["hourly_loot"]["spice_eq"], 0.0)

        # Outlier resistance: median must pick middle loot (12_000 pig_iron), not mean.
        raids = [
            {
                "target_coords": "02:380:15",
                "attacked_ts": 1_700_000_000.0,
                "outcome": "win",
                "loot_json": json.dumps({"pig_iron": 6_000, "metal": 2_500, "kryptonite": 3_000, "spice": 1_000}),
                "round_trip_sec": 1200.0,
            },
            {
                "target_coords": "02:380:15",
                "attacked_ts": 1_700_001_000.0,
                "outcome": "win",
                "loot_json": json.dumps({"pig_iron": 12_000, "metal": 5_000, "kryptonite": 6_000, "spice": 2_000}),
                "round_trip_sec": 1800.0,
            },
            {
                "target_coords": "02:380:15",
                "attacked_ts": 1_700_002_000.0,
                "outcome": "win",
                "loot_json": json.dumps({"pig_iron": 6_000_000, "metal": 5_000_000, "kryptonite": 3_000_000, "spice": 1_000_000}),
                "round_trip_sec": 2400.0,
            },
        ]
        tel = ap.compute_attack_telemetry(raids, None, state=state)
        self.assertEqual(tel["sample_count"], 3)
        self.assertEqual(tel["median_loot"]["pig_iron"], 12_000.0)
        self.assertEqual(tel["median_loot"]["spice"], 2_000.0)
        self.assertEqual(tel["median_round_trip_seconds"], 1800.0)
        self.assertAlmostEqual(tel["attacks_per_slot_per_hour"], 2.0, places=4)
        self.assertAlmostEqual(tel["estimated_attacks_per_hour"], 16.0, places=4)
        self.assertAlmostEqual(tel["hourly_loot"]["pig_iron"], 192_000.0, places=2)

        # Battle report dispatch-impact pairing (gap = 600s -> RT = 1200s).
        battle_msgs = [
            {
                "ts": 1_700_000_600.0,
                "target_coords": "02:380:15",
                "outcome": "win",
                "pig_iron": 18_000,
                "metal": 5_000,
                "kryptonite": 6_000,
                "spice": 2_000,
                "loot_spice_eq": 9_000.0,
            }
        ]
        paired_tel = ap.compute_attack_telemetry(raids[:1], battle_msgs, state=state)
        self.assertEqual(paired_tel["source"], "battle_reports")
        self.assertAlmostEqual(paired_tel["median_round_trip_seconds"], 1200.0, places=2)
        self.assertEqual(paired_tel["median_loot"]["pig_iron"], 18_000.0)

    def test_run_ai_planner_uses_live_stock_and_never_demolishes(self) -> None:
        # Live planet has b6=5 (NOT in target_config) and rich live stock!
        p1 = _make_planet(
            101,
            "02:380:12",
            "Homeworld",
            buildings={"b1": 4, "b2": 2, "b3": 3, "b4": 2, "b5": 6, "b6": 5, "b7": 2, "b8": 3, "b10": 1},
            resources=Resources(50_000, 30_000, 30_000, 15_000),
        )
        p1_poor = _make_planet(
            101,
            "02:380:12",
            "Homeworld",
            buildings={"b1": 4, "b2": 2, "b3": 3, "b4": 2, "b5": 6, "b6": 5, "b7": 2, "b8": 3, "b10": 1},
            resources=Resources(0, 0, 0, 0),
        )
        state_rich = GameState(
            universe_id=31,
            user_id=1,
            nickname="Tester",
            timestamp=1_700_000_000.0,
            planets=(p1,),
            active_planet_id=101,
            researches={"r1": 1, "r2": 4},
            speedup_factor=5.0,
        )
        state_poor = GameState(
            universe_id=31,
            user_id=1,
            nickname="Tester",
            timestamp=1_700_000_000.0,
            planets=(p1_poor,),
            active_planet_id=101,
            researches={"r1": 1, "r2": 4},
            speedup_factor=5.0,
        )
        target = _sample_target_config(
            [
                _target_planet_entry(
                    1,
                    101,
                    "02:380:12",
                    "Homeworld",
                    # Note: b6 omitted (target 0 < built 5) — AI-Planner must NEVER demolish b6!
                    {"b1": 7, "b2": 4, "b3": 6, "b4": 4, "b5": 9, "b7": 3, "b8": 3, "b10": 2, "b11": 2, "b12": 2, "b13": 2, "b14": 2},
                    is_central=True,
                    pays_for_research=True,
                )
            ],
            planned_research={"r1": 3, "r2": 5},
        )
        plan_rich = ap.run_ai_planner(
            state_rich,
            target,
            params={"reward_weeks": 4, "ga_generations": 2, "ga_population_size": 4},
        )
        plan_poor = ap.run_ai_planner(
            state_poor,
            target,
            params={"reward_weeks": 4, "ga_generations": 2, "ga_population_size": 4},
        )

        self.assertEqual(plan_rich["summary"]["status"], "reached")
        self.assertGreater(len(plan_rich["steps"]), 0)
        # ZERO demolitions, ONLY valid step kinds.
        for step in plan_rich["steps"]:
            self.assertIn(step["kind"], ("BUILD", "RESEARCH", "COLONIZE_PREVIEW"))
            self.assertNotEqual(step["entity_key"], "b6")
            self.assertIn("cost", step)
            self.assertGreaterEqual(step["week"], 1)
            self.assertLessEqual(step["week"], 4)
        # Rich starting stock starts first upgrade immediately at t=0 and finishes faster / yields >= reward.
        self.assertEqual(plan_rich["steps"][0]["start_sec"], 0.0)
        self.assertGreaterEqual(
            plan_rich["summary"]["discounted_reward"], plan_poor["summary"]["discounted_reward"]
        )
        self.assertEqual(len(plan_rich["weekly_breakdown"]), 4)

    def test_end_of_horizon_handled_cleanly(self) -> None:
        # Very high mine target (b1 -> 45) takes far longer than 1 week (168h).
        p1 = _make_planet(
            101,
            "02:380:12",
            "Homeworld",
            buildings={"b1": 10, "b2": 6, "b3": 8, "b4": 6, "b5": 12, "b7": 4, "b8": 4, "b11": 20, "b12": 20, "b13": 20, "b14": 20},
            resources=Resources(100_000, 100_000, 100_000, 50_000),
        )
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
        target = _sample_target_config(
            [
                _target_planet_entry(
                    1,
                    101,
                    "02:380:12",
                    "Homeworld",
                    {"b1": 45, "b2": 35, "b3": 40, "b4": 35, "b5": 45, "b7": 12, "b8": 12, "b11": 20, "b12": 20, "b13": 20, "b14": 20},
                    is_central=True,
                    pays_for_research=True,
                )
            ],
            planned_research={"r1": 12, "r2": 12},
        )
        plan = ap.run_ai_planner(
            state,
            target,
            params={"reward_weeks": 1, "ga_generations": 2, "ga_population_size": 4},
        )
        self.assertEqual(plan["summary"]["status"], "horizon")
        self.assertEqual(len(plan["weekly_breakdown"]), 1)
        self.assertGreater(plan["summary"]["discounted_reward"], 0.0)
        self.assertIsNone(plan["summary"]["hours"])
        self.assertTrue(all(s["week"] == 1 for s in plan["steps"]))

    def test_pad_generic_colonies_and_attack_b10_npv(self) -> None:
        p1 = _make_planet(
            101,
            "02:380:12",
            "Homeworld",
            buildings={"b1": 6, "b2": 3, "b3": 5, "b4": 3, "b5": 8, "b7": 4, "b8": 3, "b10": 1, "b11": 4, "b12": 4, "b13": 4, "b14": 4},
            resources=Resources(40_000, 20_000, 20_000, 10_000),
        )
        state = GameState(
            universe_id=31,
            user_id=1,
            nickname="Tester",
            timestamp=1_700_000_000.0,
            planets=(p1,),
            active_planet_id=101,
            researches={"r1": 2, "r2": 4},
            speedup_factor=5.0,
        )
        target = _sample_target_config(
            [
                _target_planet_entry(
                    1,
                    101,
                    "02:380:12",
                    "Homeworld",
                    {"b1": 10, "b2": 5, "b3": 8, "b4": 6, "b5": 12, "b7": 4, "b8": 3, "b10": 4, "b11": 4, "b12": 4, "b13": 4, "b14": 4},
                    is_central=True,
                    pays_for_research=True,
                ),
                _target_planet_entry(
                    2,
                    -1,
                    "EMPTY:01",
                    "Empty Colony Slot 1",
                    {"b1": 6, "b3": 4, "b5": 6, "b11": 2, "b12": 2, "b13": 2, "b14": 2},
                    is_placeholder=True,
                ),
            ],
            planned_research={"r1": 3, "r2": 5},
        )
        no_pad = ap.run_ai_planner(
            state,
            target,
            params={"reward_weeks": 4, "ga_generations": 0, "pad_generic_colonies": False},
        )
        with_pad = ap.run_ai_planner(
            state,
            target,
            params={"reward_weeks": 4, "ga_generations": 0, "pad_generic_colonies": True},
        )
        self.assertFalse(any(s["kind"] == "COLONIZE_PREVIEW" for s in no_pad["steps"]))
        self.assertTrue(any(s["kind"] == "COLONIZE_PREVIEW" for s in with_pad["steps"]))

        # Rich attack telemetry gives b10 slot-loot NPV and accelerates a resource-constrained empire.
        constrained_p1 = _make_planet(
            101,
            "02:380:12",
            "Homeworld",
            buildings={"b1": 6, "b2": 3, "b3": 5, "b4": 3, "b5": 8, "b7": 4, "b8": 3, "b10": 1, "b11": 8, "b12": 8, "b13": 8, "b14": 8},
            resources=Resources(0, 0, 0, 0),
        )
        constrained_state = GameState(
            universe_id=31,
            user_id=1,
            nickname="Tester",
            timestamp=1_700_000_000.0,
            planets=(constrained_p1,),
            active_planet_id=101,
            researches={"r1": 2, "r2": 4},
            speedup_factor=5.0,
        )
        constrained_target = _sample_target_config(
            [
                _target_planet_entry(
                    1,
                    101,
                    "02:380:12",
                    "Homeworld",
                    {"b1": 22, "b2": 16, "b3": 20, "b4": 16, "b5": 24, "b7": 8, "b8": 4, "b10": 4, "b11": 8, "b12": 8, "b13": 8, "b14": 8},
                    is_central=True,
                    pays_for_research=True,
                ),
            ],
            planned_research={"r1": 6, "r2": 6},
        )
        rich_tel = {
            "sample_count": 20,
            "median_loot": {"pig_iron": 30_000.0, "metal": 15_000.0, "kryptonite": 15_000.0, "spice": 5_000.0, "spice_eq": 21_000.0},
            "median_round_trip_seconds": 1800.0,
        }
        plan_with_raids = ap.run_ai_planner(
            constrained_state,
            constrained_target,
            attack_telemetry=rich_tel,
            params={"reward_weeks": 3, "ga_generations": 0, "pad_generic_colonies": False},
        )
        plan_no_raids = ap.run_ai_planner(
            constrained_state,
            constrained_target,
            attack_telemetry=rich_tel,
            params={"reward_weeks": 3, "ga_generations": 0, "attacks_per_day": 0.0, "pad_generic_colonies": False},
        )
        self.assertGreater(
            plan_with_raids["summary"]["discounted_reward"],
            plan_no_raids["summary"]["discounted_reward"],
        )
        self.assertGreater(
            plan_with_raids["summary"]["final_fleet_slots"],
            plan_with_raids["summary"]["start_fleet_slots"],
        )
        self.assertEqual(plan_no_raids["summary"]["loot_counted"]["pig_iron"], 0)

        # Higher resource_spare_percent reduces net weekly reward cleanly.
        plan_spare_0 = ap.run_ai_planner(
            constrained_state,
            constrained_target,
            params={
                "reward_weeks": 2,
                "ga_generations": 0,
                "resource_spare_percent": {"pig_iron": 0.0, "metal": 0.0, "kryptonite": 0.0, "spice": 0.0},
            },
        )
        plan_spare_25 = ap.run_ai_planner(
            constrained_state,
            constrained_target,
            params={
                "reward_weeks": 2,
                "ga_generations": 0,
                "resource_spare_percent": {"pig_iron": 25.0, "metal": 25.0, "kryptonite": 25.0, "spice": 25.0},
            },
        )
        self.assertGreater(
            plan_spare_0["summary"]["discounted_reward"],
            plan_spare_25["summary"]["discounted_reward"],
        )
        self.assertGreater(plan_spare_25["summary"]["spare_diverted"]["pig_iron"], 0)

    def test_reconcile_live_ai_actions_queues_prereqs_and_field_conflicts(self) -> None:
        b1_offer = BuildingOffer(
            building_id=1,
            name="Pig iron mine",
            current_level=5,
            target_level=6,
            cost=Resources(600, 0, 200, 0),
            can_build=True,
            status_text="Build level 6",
            build_href="indexInternal.es?action=internalBuildItem&buildingID=1",
        )
        r1_offer = ResearchOffer(
            research_id=1,
            research_key="r1",
            name="Mining technology",
            current_level=2,
            target_level=3,
            cost=Resources(400, 400, 800, 0),
            can_research=True,
            status_text="Research level 3",
            research_href="indexInternal.es?action=internalResearch&rid=1",
            active=False,
        )
        p1 = _make_planet(
            101,
            "02:380:12",
            "Homeworld",
            fields_max=200,
            buildings={"b1": 5, "b3": 4, "b5": 8, "b7": 2, "b8": 3},
            resources=Resources(50_000, 50_000, 50_000, 20_000),
            building_offers=(b1_offer,),
        )
        # p2 is remote and has b3 L5 currently IN FLIGHT via QueueJob, so reconcile should skip b3 L5 and pick b1 L5!
        p2 = _make_planet(
            102,
            "02:381:04",
            "Colony 1",
            fields_max=250,
            buildings={"b1": 4, "b3": 4, "b5": 6, "b7": 2},
            resources=Resources(50_000, 50_000, 50_000, 20_000),
        )
        # p3 is FULL (fields_used == fields_max == 40) -> must emit field conflict, NEVER DEMOLISH_BUILDING!
        p3 = _make_planet(
            103,
            "02:382:09",
            "FullPlanet",
            fields_max=40,
            fields_used=40,
            buildings={"b1": 15, "b3": 10, "b5": 12, "b6": 3},
            resources=Resources(50_000, 50_000, 50_000, 20_000),
        )
        state = GameState(
            universe_id=31,
            user_id=1,
            nickname="Tester",
            timestamp=1_700_000_000.0,
            planets=(p1, p2, p3),
            active_planet_id=101,
            researches={"r1": 2, "r2": 4},
            research_offers=(r1_offer,),
            active_queues=(
                QueueJob(
                    category="building",
                    name="Kryptonite mine (Level 5)",
                    building_id=3,
                    target_level=5,
                    origin_coords="02:381:04",
                ),
            ),
            speedup_factor=5.0,
        )
        plan_result = {
            "steps": [
                # Already built on p1 (b1 is 5) -> skipped!
                {"seq": 1, "kind": "BUILD", "planet_id": 101, "planet_coords": "02:380:12", "planet_name": "Homeworld", "entity_key": "b1", "entity_name": "Pig iron mine", "target_level": 5},
                # Placeholder colonize preview -> skipped!
                {"seq": 2, "kind": "COLONIZE_PREVIEW", "planet_id": -1, "planet_coords": "EMPTY:01", "planet_name": "Empty", "entity_key": "s8", "entity_name": "Colony ship", "target_level": 1},
                # Next causally unblocked on p1 -> emitted BUILD executable_now=True!
                {"seq": 3, "kind": "BUILD", "planet_id": 101, "planet_coords": "02:380:12", "planet_name": "Homeworld", "entity_key": "b1", "entity_name": "Pig iron mine", "target_level": 6},
                # Second step on p1 -> not emitted because p1 lane already has seq #3!
                {"seq": 4, "kind": "BUILD", "planet_id": 101, "planet_coords": "02:380:12", "planet_name": "Homeworld", "entity_key": "b3", "entity_name": "Kryptonite mine", "target_level": 5},
                # In-flight on p2 -> skipped!
                {"seq": 5, "kind": "BUILD", "planet_id": 102, "planet_coords": "02:381:04", "planet_name": "Colony 1", "entity_key": "b3", "entity_name": "Kryptonite mine", "target_level": 5},
                # Next causally unblocked on p2 (queue busy with 1 slot -> WAIT_RESOURCES, executable_now=False)
                {"seq": 6, "kind": "BUILD", "planet_id": 102, "planet_coords": "02:381:04", "planet_name": "Colony 1", "entity_key": "b1", "entity_name": "Pig iron mine", "target_level": 5},
                # Full planet p3 -> triggers structured field conflict, NOT demolition!
                {"seq": 7, "kind": "BUILD", "planet_id": 103, "planet_coords": "02:382:09", "planet_name": "FullPlanet", "entity_key": "b1", "entity_name": "Pig iron mine", "target_level": 16},
                # Research r1 L2 already done -> skipped!
                {"seq": 8, "kind": "RESEARCH", "planet_id": 0, "planet_coords": "account", "planet_name": "Account", "entity_key": "r1", "entity_name": "Mining technology", "target_level": 2},
                # Research r1 L3 -> emitted RESEARCH executable_now=True!
                {"seq": 9, "kind": "RESEARCH", "planet_id": 0, "planet_coords": "account", "planet_name": "Account", "entity_key": "r1", "entity_name": "Mining technology", "target_level": 3},
                # Research r2 L5 -> skipped because global research lane already has r1 L3!
                {"seq": 10, "kind": "RESEARCH", "planet_id": 0, "planet_coords": "account", "planet_name": "Account", "entity_key": "r2", "entity_name": "Energy technology", "target_level": 5},
            ]
        }
        actions, conflicts = ap.reconcile_live_ai_actions(state, plan_result, StrategyConfig())

        self.assertFalse(any(a.kind == "DEMOLISH_BUILDING" for a in actions))
        self.assertEqual(len(actions), 3)
        by_planet_kind = {(a.planet_id, a.kind, a.target_id, a.target_level): a for a in actions}
        self.assertIn((101, "BUILD", 1, 6), by_planet_kind)
        self.assertTrue(by_planet_kind[(101, "BUILD", 1, 6)].executable_now)
        self.assertEqual(by_planet_kind[(101, "BUILD", 1, 6)].action_href, b1_offer.build_href)

        self.assertIn((102, "WAIT_RESOURCES", 1, 5), by_planet_kind)
        self.assertFalse(by_planet_kind[(102, "WAIT_RESOURCES", 1, 5)].executable_now)

        self.assertIn((101, "RESEARCH", 1, 3), by_planet_kind)
        self.assertTrue(by_planet_kind[(101, "RESEARCH", 1, 3)].executable_now)
        self.assertEqual(by_planet_kind[(101, "RESEARCH", 1, 3)].action_href, r1_offer.research_href)

        # Field conflict for full planet p3
        self.assertEqual(len(conflicts), 1)
        c = conflicts[0]
        self.assertEqual(c["conflict_key"], "p103:b1:L16")
        self.assertEqual(c["planet_id"], 103)
        self.assertEqual(c["planet_coords"], "02:382:09")
        self.assertEqual(c["planet_name"], "FullPlanet")
        self.assertEqual(c["blocked_building_key"], "b1")
        self.assertEqual(c["blocked_target_level"], 16)
        cand_keys = [item["building_key"] for item in c["candidates"]]
        self.assertEqual(set(cand_keys), {"b1", "b3", "b5", "b6"})
        # Thermosolar plant (b6) is ranked first; Fusion plant (b5) is protected last.
        self.assertEqual(cand_keys[0], "b6")
        self.assertEqual(cand_keys[-1], "b5")

    def test_reconcile_skips_in_flight_research_and_supports_remote_build_and_full_pipeline(self) -> None:
        p1 = _make_planet(
            101,
            "02:380:12",
            "Homeworld",
            fields_max=280,
            buildings={"b1": 10, "b2": 6, "b3": 8, "b4": 6, "b5": 12, "b7": 12, "b8": 12, "b10": 5},
            resources=Resources(100_000, 60_000, 60_000, 20_000),
        )
        p2 = _make_planet(
            102,
            "02:381:05",
            "Colony 1",
            fields_max=290,
            buildings={"b1": 8, "b2": 4, "b3": 6, "b4": 5, "b5": 10, "b7": 12, "b10": 5},
            resources=Resources(100_000, 60_000, 60_000, 20_000),
        )
        # r1 L5 is actively researching (in-flight), so reconcile must skip r1 L5 and pick r2 L6 with executable_now=False (busy).
        r1_active = ResearchOffer(
            research_id=1,
            research_key="r1",
            name="Mining technology",
            current_level=4,
            target_level=5,
            cost=Resources(1000, 1000, 2000, 0),
            can_research=False,
            status_text="In progress",
            active=True,
        )
        state = GameState(
            universe_id=31,
            user_id=1,
            nickname="Pipeline",
            timestamp=1_700_000_000.0,
            planets=(p1, p2),
            active_planet_id=101,
            researches={"r1": 4, "r2": 5},
            research_offers=(r1_active,),
            speedup_factor=5.0,
        )

        # Full end-to-end pure-Python pipeline: Config Optimizer -> AI-Planner -> Reconcile!
        cfg_res = co.run_config_optimizer(
            state,
            universe_research_max={"r1": 8, "r2": 8, "r6": 10, "r15": 2},
            params={
                "catalogue_grid_size": 256,
                "catalogue_priority_steps": 2,
                "hill_climb_step_sizes": [1, 2],
                "ga_population_size": 16,
                "ga_generations": 10,
                "central_planet_min_warehouse_level": 8,
                "optimized_building_max_levels": {
                    "b1": 25,
                    "b2": 22,
                    "b3": 22,
                    "b4": 22,
                    "b5": 28,
                    "b9": 15,
                    "b17": 6,
                },
            },
        )
        plan_res = ap.run_ai_planner(
            state,
            cfg_res,
            params={"reward_weeks": 4, "ga_generations": 2, "ga_population_size": 4},
        )
        self.assertGreater(len(plan_res["steps"]), 0)
        actions, conflicts = ap.reconcile_live_ai_actions(state, plan_res, StrategyConfig())
        self.assertEqual(conflicts, [])
        self.assertFalse(any(a.kind == "DEMOLISH_BUILDING" for a in actions))
        # Remote planet p2 (102) has sufficient stock and no queue -> BUILD with executable_now=True, action_href=None!
        p2_actions = [a for a in actions if a.planet_id == 102 and a.kind in ("BUILD", "WAIT_RESOURCES")]
        self.assertEqual(len(p2_actions), 1)
        self.assertEqual(p2_actions[0].kind, "BUILD")
        self.assertTrue(p2_actions[0].executable_now)
        self.assertIsNone(p2_actions[0].action_href)
        # In-flight research r1 L5 must be skipped.
        res_actions = [a for a in actions if a.kind == "RESEARCH"]
        for ra in res_actions:
            self.assertFalse(ra.target_id == 1 and ra.target_level == 5)
            self.assertFalse(ra.executable_now)  # Research slot is busy!

    def test_export_ai_planner_txt_14_line_header_and_causal_lines(self) -> None:
        p1 = _make_planet(
            101,
            "02:380:12",
            "Homeworld",
            buildings={"b1": 4, "b2": 2, "b3": 3, "b4": 2, "b5": 6, "b7": 2, "b8": 3},
            resources=Resources(40_000, 20_000, 20_000, 10_000),
        )
        state = GameState(
            universe_id=31,
            user_id=1,
            nickname="Tester",
            timestamp=1_700_000_000.0,
            planets=(p1,),
            active_planet_id=101,
            researches={"r1": 1, "r2": 4},
            speedup_factor=5.0,
        )
        target = _sample_target_config(
            [
                _target_planet_entry(
                    1,
                    101,
                    "02:380:12",
                    "Homeworld",
                    {"b1": 6, "b2": 3, "b3": 5, "b4": 3, "b5": 8, "b7": 2, "b8": 3, "b11": 2, "b12": 2, "b13": 2, "b14": 2},
                    is_central=True,
                    pays_for_research=True,
                ),
            ],
            planned_research={"r1": 2, "r2": 5},
        )
        plan = ap.run_ai_planner(state, target, params={"reward_weeks": 4, "ga_generations": 0})
        txt = ap.export_ai_planner_txt(plan)
        lines = txt.splitlines()
        # Exact 14-line header: lines 0..12 non-empty, line 13 blank separator, line 14+ numbered steps.
        self.assertGreater(len(lines), 14)
        self.assertTrue(all(lines[i].strip() for i in range(13)))
        self.assertEqual(lines[13], "")
        self.assertTrue(lines[0].startswith("Best 4-week path"))
        self.assertTrue(lines[5].startswith("Attacks & spare:"))
        self.assertTrue(lines[6].startswith("Reward:"))
        self.assertTrue(lines[8].startswith("Colonies reached:"))
        self.assertTrue(lines[9].startswith("Max building levels reached:"))
        self.assertTrue(lines[10].startswith("Research levels reached:"))
        action_lines = lines[14:]
        self.assertEqual(len(action_lines), len(plan["steps"]))
        self.assertIn("1. at ", action_lines[0])
        self.assertTrue(any("build " in ln for ln in action_lines))
        self.assertTrue(any("research " in ln for ln in action_lines))

    def test_mathematical_parity_with_full_planner_notebook(self) -> None:
        """Cross-check `EmpirePathSimulator` & `PathStrategy` against `notebooks/full_planner.ipynb` §11."""
        nb = json.loads(NOTEBOOK_PATH.read_text(encoding="utf-8"))
        ns: dict[str, object] = {
            "RESOURCE_KEYS": co.RESOURCE_KEYS,
            "WAREHOUSE_KEYS": co.WAREHOUSE_KEYS,
            "TECH_GRAPH": co.TECH_GRAPH,
            "building_level_economy": co.building_level_economy,
            "produced_resource": co.produced_resource,
            "building_level_flows": co.building_level_flows,
            "building_time_divisor": co.building_time_divisor,
            "research_time_divisor": co.research_time_divisor,
            "shipyard_time_divisor": co.shipyard_time_divisor,
            "solar_cell_energy_per_unit": co.solar_cell_energy_per_unit,
            "technology_multiplier": co.technology_multiplier,
            "full_cost": co.full_cost,
            "game_data": game_data,
        }
        import heapq as _heapq
        import math as _math
        from typing import Any as _Any
        import pandas as _pd

        ns["heapq"] = _heapq
        ns["math"] = _math
        ns["Any"] = _Any
        ns["np"] = np
        ns["pd"] = _pd

        for idx in (92, 94):
            src = "".join(nb["cells"][idx]["source"])
            tree = ast.parse(src)
            kept: list[ast.stmt] = []
            for stmt in tree.body:
                if isinstance(stmt, (ast.FunctionDef, ast.ClassDef)):
                    kept.append(stmt)
                elif isinstance(stmt, ast.AnnAssign) and isinstance(stmt.target, ast.Name) and stmt.target.id in (
                    "PRODUCTION_FLOW_KEYS",
                    "PATH_FLOW_BUILDING_KEYS",
                    "PATH_MINE_KEYS",
                    "PATH_ACCELERATOR_KEYS",
                    "PATH_HOURS_PER_WEEK",
                    "PATH_NEVER",
                ):
                    kept.append(stmt)
            exec(compile(ast.Module(body=kept, type_ignores=[]), f"fp_cell_{idx}", "exec"), ns)

        p1 = _make_planet(
            101,
            "02:380:12",
            "Homeworld",
            temperature=25,
            buildings={"b1": 5, "b2": 3, "b3": 4, "b4": 3, "b5": 7, "b7": 2, "b8": 2, "b10": 1},
            resources=Resources(25_000, 10_000, 12_000, 5_000),
        )
        p2 = _make_planet(
            102,
            "02:381:05",
            "Colony 1",
            temperature=-20,
            buildings={"b1": 3, "b2": 1, "b3": 3, "b4": 2, "b5": 5, "b7": 1, "b10": 1},
            resources=Resources(10_000, 5_000, 5_000, 2_000),
        )
        state = GameState(
            universe_id=31,
            user_id=1,
            nickname="Parity",
            timestamp=1_700_000_000.0,
            planets=(p1, p2),
            active_planet_id=101,
            researches={"r1": 2, "r2": 2},
            speedup_factor=5.0,
        )
        target = _sample_target_config(
            [
                _target_planet_entry(
                    1,
                    101,
                    "02:380:12",
                    "Homeworld",
                    {"b1": 10, "b2": 7, "b3": 9, "b4": 7, "b5": 12, "b7": 4, "b8": 4, "b10": 3, "b11": 4, "b12": 4, "b13": 4, "b14": 4},
                    is_central=True,
                    pays_for_research=True,
                    temperature=25,
                ),
                _target_planet_entry(
                    2,
                    102,
                    "02:381:05",
                    "Colony 1",
                    {"b1": 9, "b2": 6, "b3": 8, "b4": 7, "b5": 11, "b7": 3, "b10": 2, "b11": 4, "b12": 4, "b13": 4, "b14": 4},
                    temperature=-20,
                ),
            ],
            planned_research={"r1": 5, "r2": 5},
        )
        problem = ap.build_path_problem(state, target)
        fixed_atk = {r: 0.0 for r in co.RESOURCE_KEYS}
        slot_atk = {"pig_iron": 5000.0, "metal": 2000.0, "kryptonite": 2500.0, "spice": 800.0}
        spare = {"pig_iron": 10.0, "metal": 10.0, "kryptonite": 10.0, "spice": 10.0}

        our_sim = ap.EmpirePathSimulator(
            problem,
            fixed_atk,
            slot_attack_income=slot_atk,
            spare_percent=spare,
            reward_weeks=6,
            weekly_discount=0.90,
        )
        nb_sim = ns["EmpirePathSimulator"](
            problem,
            fixed_atk,
            slot_attack_income=slot_atk,
            spare_percent=spare,
            reward_weeks=6,
            weekly_discount=0.90,
        )

        for strat_kwargs in (
            {"rule": "cheapest", "patience": 0.25},
            {"rule": "payback", "values": "merchant", "patience": 0.25},
            {"rule": "payback", "values": "scarcity", "focus": 1.0, "patience": 0.25},
        ):
            our_run = our_sim.run(ap.PathStrategy("s", **strat_kwargs))
            nb_run = nb_sim.run(ns["PathStrategy"]("s", **strat_kwargs))
            self.assertEqual(our_run["status"], nb_run["status"])
            self.assertEqual(our_run["steps_started"], nb_run["steps_started"])
            self.assertEqual(our_run["order"], nb_run["order"])
            self.assertAlmostEqual(our_run["discounted_reward"], nb_run["discounted_reward"], places=6)
            self.assertTrue(
                np.allclose(our_run["weekly_spice_eq"], nb_run["weekly_spice_eq"], rtol=1e-9, atol=1e-6)
            )


if __name__ == "__main__":
    unittest.main()
