"""Unit & notebook mathematical parity tests for `spaceinvasion/planner/ai_planner.py`."""

from __future__ import annotations

import ast
import json
import math
from pathlib import Path
import unittest

import numpy as np

from spaceinvasion.core import catalog as game_data
from spaceinvasion.core.config import StrategyConfig
from spaceinvasion.core.model import (
    BuildingOffer,
    EnergyBalance,
    GameState,
    PlanetState,
    QueueJob,
    ResearchOffer,
    Resources,
)
from spaceinvasion.planner import ai as ap
from spaceinvasion.planner import config_optimizer as co

REPO_ROOT = Path(__file__).resolve().parents[2]
AI_PKG_DIR = REPO_ROOT / "spaceinvasion" / "planner" / "ai"
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
    energy: EnergyBalance | None = None,
    building_offers: tuple[BuildingOffer, ...] = (),
    economy_rates: dict[str, int] | None = None,
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
        energy=energy or EnergyBalance(produced=400, used=200),
        buildings=bmap,
        building_offers=building_offers,
        economy_rates=dict(economy_rates or {}),
    )


def _config_with_adaptive(adaptive: bool, **kwargs: object) -> StrategyConfig:
    cfg = StrategyConfig(**kwargs)
    object.__setattr__(cfg, "ai_adaptive_execution", bool(adaptive))
    return cfg


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
        forbidden = {"pandas", "selenium", "IPython", "jupyter", "requests"}
        found: set[str] = set()
        for path in sorted(AI_PKG_DIR.glob("*.py")):
            source = path.read_text(encoding="utf-8")
            self.assertNotIn("9222", source)
            self.assertNotIn("spaceinvasion.db", source)
            tree = ast.parse(source)
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    for alias in node.names:
                        found.add(alias.name.split(".")[0])
                elif isinstance(node, ast.ImportFrom) and node.module:
                    found.add(node.module.split(".")[0])
        self.assertEqual(found & forbidden, set())

    def test_default_params_match_contract(self) -> None:
        d = ap.DEFAULT_AI_PLANNER_PARAMS
        self.assertEqual(d["reward_weeks"], 26)
        self.assertEqual(d["weekly_discount"], 0.90)
        self.assertEqual(d["transport_dispatch_delay_minutes"], 120.0)
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
                "loot_json": json.dumps(
                    {"pig_iron": 6_000_000, "metal": 5_000_000, "kryptonite": 3_000_000, "spice": 1_000_000}
                ),
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
                    {
                        "b1": 7,
                        "b2": 4,
                        "b3": 6,
                        "b4": 4,
                        "b5": 9,
                        "b7": 3,
                        "b8": 3,
                        "b10": 2,
                        "b11": 2,
                        "b12": 2,
                        "b13": 2,
                        "b14": 2,
                    },
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
        self.assertGreaterEqual(plan_rich["summary"]["discounted_reward"], plan_poor["summary"]["discounted_reward"])
        self.assertEqual(len(plan_rich["weekly_breakdown"]), 4)

    def test_end_of_horizon_handled_cleanly(self) -> None:
        # Very high mine target (b1 -> 45) takes far longer than 1 week (168h).
        p1 = _make_planet(
            101,
            "02:380:12",
            "Homeworld",
            buildings={
                "b1": 10,
                "b2": 6,
                "b3": 8,
                "b4": 6,
                "b5": 12,
                "b7": 4,
                "b8": 4,
                "b11": 20,
                "b12": 20,
                "b13": 20,
                "b14": 20,
            },
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
                    {
                        "b1": 45,
                        "b2": 35,
                        "b3": 40,
                        "b4": 35,
                        "b5": 45,
                        "b7": 12,
                        "b8": 12,
                        "b11": 20,
                        "b12": 20,
                        "b13": 20,
                        "b14": 20,
                    },
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
            buildings={
                "b1": 6,
                "b2": 3,
                "b3": 5,
                "b4": 3,
                "b5": 8,
                "b7": 4,
                "b8": 3,
                "b10": 1,
                "b11": 4,
                "b12": 4,
                "b13": 4,
                "b14": 4,
            },
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
                    {
                        "b1": 10,
                        "b2": 5,
                        "b3": 8,
                        "b4": 6,
                        "b5": 12,
                        "b7": 4,
                        "b8": 3,
                        "b10": 4,
                        "b11": 4,
                        "b12": 4,
                        "b13": 4,
                        "b14": 4,
                    },
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
            buildings={
                "b1": 6,
                "b2": 3,
                "b3": 5,
                "b4": 3,
                "b5": 8,
                "b7": 4,
                "b8": 3,
                "b10": 1,
                "b11": 8,
                "b12": 8,
                "b13": 8,
                "b14": 8,
            },
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
                    {
                        "b1": 22,
                        "b2": 16,
                        "b3": 20,
                        "b4": 16,
                        "b5": 24,
                        "b7": 8,
                        "b8": 4,
                        "b10": 4,
                        "b11": 8,
                        "b12": 8,
                        "b13": 8,
                        "b14": 8,
                    },
                    is_central=True,
                    pays_for_research=True,
                ),
            ],
            planned_research={"r1": 6, "r2": 6},
        )
        rich_tel = {
            "sample_count": 20,
            "median_loot": {
                "pig_iron": 30_000.0,
                "metal": 15_000.0,
                "kryptonite": 15_000.0,
                "spice": 5_000.0,
                "spice_eq": 21_000.0,
            },
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
                {
                    "seq": 1,
                    "kind": "BUILD",
                    "planet_id": 101,
                    "planet_coords": "02:380:12",
                    "planet_name": "Homeworld",
                    "entity_key": "b1",
                    "entity_name": "Pig iron mine",
                    "target_level": 5,
                },
                # Placeholder colonize preview -> skipped!
                {
                    "seq": 2,
                    "kind": "COLONIZE_PREVIEW",
                    "planet_id": -1,
                    "planet_coords": "EMPTY:01",
                    "planet_name": "Empty",
                    "entity_key": "s8",
                    "entity_name": "Colony ship",
                    "target_level": 1,
                },
                # Next causally unblocked on p1 -> emitted BUILD executable_now=True!
                {
                    "seq": 3,
                    "kind": "BUILD",
                    "planet_id": 101,
                    "planet_coords": "02:380:12",
                    "planet_name": "Homeworld",
                    "entity_key": "b1",
                    "entity_name": "Pig iron mine",
                    "target_level": 6,
                },
                # Second step on p1 -> not emitted because p1 lane already has seq #3!
                {
                    "seq": 4,
                    "kind": "BUILD",
                    "planet_id": 101,
                    "planet_coords": "02:380:12",
                    "planet_name": "Homeworld",
                    "entity_key": "b3",
                    "entity_name": "Kryptonite mine",
                    "target_level": 5,
                },
                # In-flight on p2 -> skipped!
                {
                    "seq": 5,
                    "kind": "BUILD",
                    "planet_id": 102,
                    "planet_coords": "02:381:04",
                    "planet_name": "Colony 1",
                    "entity_key": "b3",
                    "entity_name": "Kryptonite mine",
                    "target_level": 5,
                },
                # Next causally unblocked on p2 (queue busy with 1 slot -> WAIT_RESOURCES, executable_now=False)
                {
                    "seq": 6,
                    "kind": "BUILD",
                    "planet_id": 102,
                    "planet_coords": "02:381:04",
                    "planet_name": "Colony 1",
                    "entity_key": "b1",
                    "entity_name": "Pig iron mine",
                    "target_level": 5,
                },
                # Full planet p3 -> triggers structured field conflict, NOT demolition!
                {
                    "seq": 7,
                    "kind": "BUILD",
                    "planet_id": 103,
                    "planet_coords": "02:382:09",
                    "planet_name": "FullPlanet",
                    "entity_key": "b1",
                    "entity_name": "Pig iron mine",
                    "target_level": 16,
                },
                # Research r1 L2 already done -> skipped!
                {
                    "seq": 8,
                    "kind": "RESEARCH",
                    "planet_id": 0,
                    "planet_coords": "account",
                    "planet_name": "Account",
                    "entity_key": "r1",
                    "entity_name": "Mining technology",
                    "target_level": 2,
                },
                # Research r1 L3 -> emitted RESEARCH executable_now=True!
                {
                    "seq": 9,
                    "kind": "RESEARCH",
                    "planet_id": 0,
                    "planet_coords": "account",
                    "planet_name": "Account",
                    "entity_key": "r1",
                    "entity_name": "Mining technology",
                    "target_level": 3,
                },
                # Research r2 L5 -> skipped because global research lane already has r1 L3!
                {
                    "seq": 10,
                    "kind": "RESEARCH",
                    "planet_id": 0,
                    "planet_coords": "account",
                    "planet_name": "Account",
                    "entity_key": "r2",
                    "entity_name": "Energy technology",
                    "target_level": 5,
                },
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
                elif (
                    isinstance(stmt, ast.AnnAssign)
                    and isinstance(stmt.target, ast.Name)
                    and stmt.target.id
                    in (
                        "PRODUCTION_FLOW_KEYS",
                        "PATH_FLOW_BUILDING_KEYS",
                        "PATH_MINE_KEYS",
                        "PATH_ACCELERATOR_KEYS",
                        "PATH_HOURS_PER_WEEK",
                        "PATH_NEVER",
                    )
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
                    {
                        "b1": 10,
                        "b2": 7,
                        "b3": 9,
                        "b4": 7,
                        "b5": 12,
                        "b7": 4,
                        "b8": 4,
                        "b10": 3,
                        "b11": 4,
                        "b12": 4,
                        "b13": 4,
                        "b14": 4,
                    },
                    is_central=True,
                    pays_for_research=True,
                    temperature=25,
                ),
                _target_planet_entry(
                    2,
                    102,
                    "02:381:05",
                    "Colony 1",
                    {
                        "b1": 9,
                        "b2": 6,
                        "b3": 8,
                        "b4": 7,
                        "b5": 11,
                        "b7": 3,
                        "b10": 2,
                        "b11": 4,
                        "b12": 4,
                        "b13": 4,
                        "b14": 4,
                    },
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
            transport_dispatch_delay_minutes=0.0,
            model_transport_travel=False,
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
            self.assertTrue(np.allclose(our_run["weekly_spice_eq"], nb_run["weekly_spice_eq"], rtol=1e-9, atol=1e-6))

    def test_interplanet_distance_and_travel_time_delay_cross_planet_steps(self) -> None:
        t_same, d_same, _ = ap.estimate_interplanet_transport_seconds(
            "02:380:01", "02:380:04", researches={"r4": 4, "r5": 4}
        )
        t_adj, d_adj, _ = ap.estimate_interplanet_transport_seconds(
            "02:380:01", "02:381:01", researches={"r4": 4, "r5": 4}
        )
        t_far, d_far, _ = ap.estimate_interplanet_transport_seconds(
            "02:380:01", "05:100:01", researches={"r4": 4, "r5": 4}
        )
        self.assertGreater(t_same, 0.0)
        self.assertLess(d_same, d_adj)
        self.assertLess(d_adj, d_far)
        self.assertLess(t_same, t_adj)
        self.assertLess(t_adj, t_far)

        # Donor has abundant stock; recipient has 0 stock and needs b1 L5 funded via cross-planet transport.
        def _run_with_dest_coords(dest_coords: str) -> dict[str, object]:
            donor = _make_planet(
                101,
                "02:380:01",
                "Donor",
                buildings={
                    "b1": 10,
                    "b2": 6,
                    "b3": 8,
                    "b4": 6,
                    "b5": 12,
                    "b7": 4,
                    "b8": 4,
                    "b11": 8,
                    "b12": 8,
                    "b13": 8,
                    "b14": 8,
                },
                resources=Resources(250_000, 150_000, 150_000, 80_000),
            )
            dest = _make_planet(
                102,
                dest_coords,
                "RemoteColony",
                buildings={
                    "b1": 4,
                    "b2": 0,
                    "b3": 0,
                    "b4": 0,
                    "b5": 8,
                    "b7": 2,
                    "b11": 6,
                    "b12": 6,
                    "b13": 6,
                    "b14": 6,
                },
                resources=Resources(0, 0, 0, 0),
            )
            state = GameState(
                universe_id=31,
                user_id=1,
                nickname="TransportDist",
                timestamp=1_700_000_000.0,
                planets=(donor, dest),
                active_planet_id=101,
                researches={"r1": 2, "r2": 4, "r4": 2, "r5": 2},
                speedup_factor=5.0,
            )
            target = _sample_target_config(
                [
                    _target_planet_entry(
                        1,
                        101,
                        "02:380:01",
                        "Donor",
                        {
                            "b1": 10,
                            "b2": 6,
                            "b3": 8,
                            "b4": 6,
                            "b5": 12,
                            "b7": 4,
                            "b8": 4,
                            "b11": 8,
                            "b12": 8,
                            "b13": 8,
                            "b14": 8,
                        },
                        is_central=True,
                        pays_for_research=True,
                    ),
                    _target_planet_entry(
                        2,
                        102,
                        dest_coords,
                        "RemoteColony",
                        {"b1": 5, "b5": 8, "b7": 2, "b11": 6, "b12": 6, "b13": 6, "b14": 6},
                    ),
                ],
                planned_research={"r1": 2, "r2": 4},
            )
            return ap.run_ai_planner(
                state,
                target,
                params={
                    "reward_weeks": 2,
                    "ga_generations": 0,
                    "transport_dispatch_delay_minutes": 0.0,
                },
            )

        near_plan = _run_with_dest_coords("02:380:04")
        far_plan = _run_with_dest_coords("06:100:08")
        near_step = near_plan["steps"][0]
        far_step = far_plan["steps"][0]
        self.assertTrue(near_step["needs_transport"])
        self.assertTrue(far_step["needs_transport"])
        self.assertGreater(near_step["transport_travel_sec"], 0.0)
        self.assertGreater(far_step["transport_travel_sec"], near_step["transport_travel_sec"])
        self.assertGreater(far_step["start_sec"], near_step["start_sec"])
        self.assertEqual(near_step["transport_request_at_sec"], 0.0)
        self.assertEqual(far_step["transport_request_at_sec"], 0.0)
        self.assertIn("transport_matrix", far_plan)
        self.assertGreater(
            far_plan["transport_matrix"]["max_one_way_seconds"],
            near_plan["transport_matrix"]["max_one_way_seconds"],
        )

    def test_transport_dispatch_delay_minutes_shifts_start_and_request_timing(self) -> None:
        donor = _make_planet(
            101,
            "02:380:01",
            "Donor",
            buildings={
                "b1": 10,
                "b2": 6,
                "b3": 8,
                "b4": 6,
                "b5": 12,
                "b7": 4,
                "b8": 4,
                "b11": 8,
                "b12": 8,
                "b13": 8,
                "b14": 8,
            },
            resources=Resources(250_000, 150_000, 150_000, 80_000),
        )
        dest = _make_planet(
            102,
            "02:381:05",
            "Recipient",
            buildings={"b1": 4, "b2": 0, "b3": 0, "b4": 0, "b5": 8, "b7": 2, "b11": 6, "b12": 6, "b13": 6, "b14": 6},
            resources=Resources(0, 0, 0, 0),
        )
        state = GameState(
            universe_id=31,
            user_id=1,
            nickname="DelayTest",
            timestamp=1_700_000_000.0,
            planets=(donor, dest),
            active_planet_id=101,
            researches={"r1": 2, "r2": 4, "r4": 2, "r5": 2},
            speedup_factor=5.0,
        )
        target = _sample_target_config(
            [
                _target_planet_entry(
                    1,
                    101,
                    "02:380:01",
                    "Donor",
                    {
                        "b1": 10,
                        "b2": 6,
                        "b3": 8,
                        "b4": 6,
                        "b5": 12,
                        "b7": 4,
                        "b8": 4,
                        "b11": 8,
                        "b12": 8,
                        "b13": 8,
                        "b14": 8,
                    },
                    is_central=True,
                    pays_for_research=True,
                ),
                _target_planet_entry(
                    2,
                    102,
                    "02:381:05",
                    "Recipient",
                    {"b1": 5, "b5": 8, "b7": 2, "b11": 6, "b12": 6, "b13": 6, "b14": 6},
                ),
            ],
            planned_research={"r1": 2, "r2": 4},
        )
        p0 = ap.run_ai_planner(
            state, target, params={"reward_weeks": 2, "ga_generations": 0, "transport_dispatch_delay_minutes": 0}
        )
        p120 = ap.run_ai_planner(
            state, target, params={"reward_weeks": 2, "ga_generations": 0, "transport_dispatch_delay_minutes": 120}
        )
        p300 = ap.run_ai_planner(
            state, target, params={"reward_weeks": 2, "ga_generations": 0, "transport_dispatch_delay_minutes": 300}
        )

        s0, s120, s300 = p0["steps"][0], p120["steps"][0], p300["steps"][0]
        self.assertEqual(p120["meta_parameters"]["transport_dispatch_delay_minutes"], 120.0)
        self.assertAlmostEqual(s120["transport_dispatch_delay_sec"], 7200.0, places=1)
        self.assertAlmostEqual(s300["transport_dispatch_delay_sec"], 18000.0, places=1)
        # Because dest produces almost nothing, extra dispatch delay shifts start_sec directly
        self.assertAlmostEqual(s120["start_sec"] - s0["start_sec"], 7200.0, delta=30.0)
        self.assertAlmostEqual(s300["start_sec"] - s120["start_sec"], 10800.0, delta=30.0)
        self.assertEqual(s120["transport_request_at_sec"], 0.0)

    def test_in_progress_builds_and_research_accounted_and_not_duplicated(self) -> None:
        # Donor has rich resources; Recipient currently has b1 L15 in progress (finishes in 10800s = 3h),
        # and r1 L3 is currently researching (finishes in 5400s = 1.5h).
        donor = _make_planet(
            101,
            "02:380:01",
            "Donor",
            buildings={
                "b1": 10,
                "b2": 6,
                "b3": 8,
                "b4": 6,
                "b5": 14,
                "b7": 6,
                "b8": 8,
                "b11": 10,
                "b12": 10,
                "b13": 10,
                "b14": 10,
            },
            resources=Resources(500_000, 300_000, 300_000, 150_000),
        )
        dest = _make_planet(
            102,
            "02:380:04",
            "Builder",
            buildings={
                "b1": 14,
                "b2": 0,
                "b3": 0,
                "b4": 0,
                "b5": 16,
                "b7": 6,
                "b11": 10,
                "b12": 10,
                "b13": 10,
                "b14": 10,
            },
            resources=Resources(0, 0, 0, 0),
        )
        state = GameState(
            universe_id=31,
            user_id=1,
            nickname="InProgress",
            timestamp=1_700_000_000.0,
            planets=(donor, dest),
            active_planet_id=101,
            researches={"r1": 2, "r2": 6, "r4": 4, "r5": 4},
            active_queues=(
                QueueJob(
                    category="building",
                    name="Pig iron mine (Level 15)",
                    building_id=1,
                    target_level=15,
                    origin_coords="02:380:04",
                    remaining_seconds=10_800,
                    remaining_text="03:00:00",
                ),
                QueueJob(
                    category="research",
                    name="Mining technology (Level 3)",
                    target_level=3,
                    remaining_seconds=5_400,
                    remaining_text="01:30:00",
                ),
            ),
            speedup_factor=5.0,
        )
        target = _sample_target_config(
            [
                _target_planet_entry(
                    1,
                    101,
                    "02:380:01",
                    "Donor",
                    {
                        "b1": 10,
                        "b2": 6,
                        "b3": 8,
                        "b4": 6,
                        "b5": 14,
                        "b7": 6,
                        "b8": 8,
                        "b11": 10,
                        "b12": 10,
                        "b13": 10,
                        "b14": 10,
                    },
                    is_central=True,
                    pays_for_research=True,
                ),
                _target_planet_entry(
                    2,
                    102,
                    "02:380:04",
                    "Builder",
                    {"b1": 16, "b5": 16, "b7": 6, "b11": 10, "b12": 10, "b13": 10, "b14": 10},
                ),
            ],
            planned_research={"r1": 4, "r2": 6},
        )
        plan = ap.run_ai_planner(
            state,
            target,
            params={"reward_weeks": 2, "ga_generations": 0, "transport_dispatch_delay_minutes": 120.0},
        )
        self.assertEqual(plan["summary"]["in_progress_buildings_count"], 1)
        self.assertEqual(plan["summary"]["in_progress_research_count"], 1)
        self.assertEqual(len(plan["in_progress"]["buildings"]), 1)
        self.assertEqual(len(plan["in_progress"]["research"]), 1)

        # Neither b1 L15 nor r1 L3 may be duplicated in new steps!
        step_pairs = {(s["kind"], s["entity_key"], s["target_level"]) for s in plan["steps"]}
        self.assertNotIn(("BUILD", "b1", 15), step_pairs)
        self.assertNotIn(("RESEARCH", "r1", 3), step_pairs)
        self.assertIn(("BUILD", "b1", 16), step_pairs)
        self.assertIn(("RESEARCH", "r1", 4), step_pairs)

        b1_16 = next(s for s in plan["steps"] if s["entity_key"] == "b1" and s["target_level"] == 16)
        r1_4 = next(s for s in plan["steps"] if s["entity_key"] == "r1" and s["target_level"] == 4)
        # Queue occupancy respected: b1 L16 cannot start before 10800s; r1 L4 cannot start before 5400s.
        self.assertGreaterEqual(b1_16["start_sec"], 10_799.0)
        self.assertGreaterEqual(r1_4["start_sec"], 5_399.0)
        # Early dispatch overlap: transport for b1 L16 is dispatched BEFORE b1 L15 finishes so b1 L16 starts right at ~10800s!
        self.assertTrue(b1_16["needs_transport"])
        self.assertLess(b1_16["transport_request_at_sec"], 10_800.0)
        self.assertAlmostEqual(b1_16["start_sec"], 10_800.0, delta=60.0)

    def test_in_flight_incoming_cargo_and_returning_fleet_credit_recipient(self) -> None:
        # Donor has 0 stock, Recipient has 0 stock, but an inbound Transport arrives in 1800s with full cost of b1 L5!
        cost_b1_5 = co.full_cost(game_data.building_cost(1, 5))
        donor = _make_planet(
            101,
            "02:380:01",
            "Donor",
            buildings={"b1": 1, "b5": 4, "b7": 2, "b8": 2, "b11": 4, "b12": 4, "b13": 4, "b14": 4},
            resources=Resources(0, 0, 0, 0),
        )
        dest = _make_planet(
            102,
            "02:380:04",
            "Dest",
            buildings={"b1": 4, "b5": 8, "b7": 2, "b11": 6, "b12": 6, "b13": 6, "b14": 6},
            resources=Resources(0, 0, 0, 0),
        )
        state = GameState(
            universe_id=31,
            user_id=1,
            nickname="CargoTest",
            timestamp=1_700_000_000.0,
            planets=(donor, dest),
            active_planet_id=101,
            researches={"r1": 1, "r2": 2},
            active_queues=(
                QueueJob(
                    category="fleet",
                    name="Transport -> 02:380:04",
                    origin_coords="02:380:01",
                    remaining_seconds=1800,
                    cargo=Resources(
                        pig_iron=int(cost_b1_5["pig_iron"]) + 500,
                        metal=int(cost_b1_5["metal"]) + 500,
                        kryptonite=int(cost_b1_5["kryptonite"]) + 500,
                        spice=int(cost_b1_5["spice"]) + 500,
                    ),
                ),
            ),
            speedup_factor=5.0,
        )
        target = _sample_target_config(
            [
                _target_planet_entry(
                    1,
                    101,
                    "02:380:01",
                    "Donor",
                    {"b1": 1, "b5": 4, "b7": 2, "b8": 2, "b11": 4, "b12": 4, "b13": 4, "b14": 4},
                    is_central=True,
                    pays_for_research=True,
                ),
                _target_planet_entry(
                    2, 102, "02:380:04", "Dest", {"b1": 5, "b5": 8, "b7": 2, "b11": 6, "b12": 6, "b13": 6, "b14": 6}
                ),
            ],
            planned_research={"r1": 1, "r2": 2},
        )
        plan = ap.run_ai_planner(
            state, target, params={"reward_weeks": 1, "ga_generations": 0, "transport_dispatch_delay_minutes": 120.0}
        )
        self.assertEqual(plan["summary"]["in_flight_arrivals_count"], 1)
        b1_5 = plan["steps"][0]
        self.assertEqual(b1_5["entity_key"], "b1")
        self.assertEqual(b1_5["target_level"], 5)
        # Starts right when the live in-flight transport lands (~1800s), without waiting for a new 120m dispatch!
        self.assertAlmostEqual(b1_5["start_sec"], 1800.0, delta=60.0)
        self.assertFalse(b1_5["needs_transport"])

        # Returning fleet carrying cargo back to origin_coords="02:380:04" also credits Dest at ~900s!
        ret_state = GameState(
            universe_id=31,
            user_id=1,
            nickname="ReturnCargo",
            timestamp=1_700_000_000.0,
            planets=(donor, dest),
            active_planet_id=101,
            researches={"r1": 1, "r2": 2},
            active_queues=(
                QueueJob(
                    category="fleet",
                    name="↩ Attack 02:381:09",
                    detail="Returning",
                    origin_coords="02:380:04",
                    remaining_seconds=900,
                    cargo=Resources(
                        pig_iron=int(cost_b1_5["pig_iron"]) + 500,
                        metal=int(cost_b1_5["metal"]) + 500,
                        kryptonite=int(cost_b1_5["kryptonite"]) + 500,
                        spice=int(cost_b1_5["spice"]) + 500,
                    ),
                ),
            ),
            speedup_factor=5.0,
        )
        ret_plan = ap.run_ai_planner(
            ret_state,
            target,
            params={"reward_weeks": 1, "ga_generations": 0, "transport_dispatch_delay_minutes": 120.0},
        )
        self.assertEqual(ret_plan["summary"]["in_flight_arrivals_count"], 1)
        self.assertAlmostEqual(ret_plan["steps"][0]["start_sec"], 900.0, delta=60.0)
        self.assertFalse(ret_plan["steps"][0]["needs_transport"])

    def test_rerunning_ai_planner_always_starts_from_updated_live_colony_state(self) -> None:
        base_target = _sample_target_config(
            [
                _target_planet_entry(
                    1,
                    101,
                    "02:380:01",
                    "Homeworld",
                    {"b1": 7, "b3": 5, "b5": 10, "b7": 3, "b8": 3, "b11": 4, "b12": 4, "b13": 4, "b14": 4},
                    is_central=True,
                    pays_for_research=True,
                ),
                _target_planet_entry(
                    2,
                    -1,
                    "EMPTY:01",
                    "Empty Slot 2",
                    {"b1": 4, "b5": 6, "b11": 2, "b12": 2, "b13": 2, "b14": 2},
                    is_placeholder=True,
                ),
            ],
            planned_research={"r1": 2, "r2": 4},
        )
        # Run 1: Homeworld at b1=4, b3=3
        s1 = GameState(
            universe_id=31,
            user_id=1,
            nickname="Rerun",
            timestamp=1_700_000_000.0,
            planets=(
                _make_planet(
                    101,
                    "02:380:01",
                    "Homeworld",
                    buildings={"b1": 4, "b3": 3, "b5": 10, "b7": 3, "b8": 3, "b11": 4, "b12": 4, "b13": 4, "b14": 4},
                    resources=Resources(50_000, 30_000, 30_000, 10_000),
                ),
            ),
            active_planet_id=101,
            researches={"r1": 1, "r2": 4},
            speedup_factor=5.0,
        )
        run1 = ap.run_ai_planner(s1, base_target, params={"reward_weeks": 2, "ga_generations": 0})
        r1_keys = {(s["planet_id"], s["entity_key"], s["target_level"]) for s in run1["steps"]}
        self.assertIn((101, "b1", 5), r1_keys)
        self.assertIn((101, "b1", 6), r1_keys)

        # Run 2: Colony progressed (b1=6 built, b1 L7 in progress) + newly founded colony 102 added!
        s2 = GameState(
            universe_id=31,
            user_id=1,
            nickname="Rerun",
            timestamp=1_700_003_600.0,
            planets=(
                _make_planet(
                    101,
                    "02:380:01",
                    "Homeworld",
                    buildings={"b1": 6, "b3": 5, "b5": 10, "b7": 3, "b8": 3, "b11": 4, "b12": 4, "b13": 4, "b14": 4},
                    resources=Resources(80_000, 40_000, 40_000, 15_000),
                ),
                _make_planet(
                    102,
                    "02:380:09",
                    "NewColony",
                    buildings={"b1": 1, "b5": 4, "b11": 2, "b12": 2, "b13": 2, "b14": 2},
                    resources=Resources(10_000, 5_000, 5_000, 2_000),
                ),
            ),
            active_planet_id=101,
            researches={"r1": 2, "r2": 4},
            active_queues=(
                QueueJob(
                    category="building",
                    name="Pig iron mine (Level 7)",
                    building_id=1,
                    target_level=7,
                    origin_coords="02:380:01",
                    remaining_seconds=1200,
                ),
            ),
            speedup_factor=5.0,
        )
        run2 = ap.run_ai_planner(s2, base_target, params={"reward_weeks": 2, "ga_generations": 0})
        r2_keys = {(s["planet_id"], s["entity_key"], s["target_level"]) for s in run2["steps"]}
        # Already built & in-progress Homeworld levels are gone; newly founded colony 102 is automatically bound and planned!
        self.assertNotIn((101, "b1", 5), r2_keys)
        self.assertNotIn((101, "b1", 6), r2_keys)
        self.assertNotIn((101, "b1", 7), r2_keys)
        self.assertIn((102, "b1", 2), r2_keys)
        self.assertFalse(any(s["kind"] == "COLONIZE_PREVIEW" for s in run2["steps"]))

    def test_compute_early_transport_needs_preships_within_lead_window_only(self) -> None:
        donor = _make_planet(
            101,
            "02:380:01",
            "Donor",
            buildings={"b1": 15, "b2": 10, "b3": 12, "b4": 10, "b5": 18, "b7": 8, "b8": 8},
            resources=Resources(500_000, 400_000, 400_000, 150_000),
        )
        # Dest currently builds b1 L15 (finishing in 1800s) and needs b1 L16 next (cost > local stock + 0.5h output).
        dest_soon = _make_planet(
            102,
            "02:380:04",
            "DestSoon",
            buildings={
                "b1": 14,
                "b2": 0,
                "b3": 0,
                "b4": 0,
                "b5": 18,
                "b7": 10,
                "b11": 10,
                "b12": 10,
                "b13": 10,
                "b14": 10,
            },
            resources=Resources(0, 0, 0, 0),
        )
        # DestFar builds b1 L15 finishing in 50_000s (~13.9h) -> well outside 120m dispatch + ~30m travel lead window!
        dest_late = _make_planet(
            103,
            "02:380:05",
            "DestLate",
            buildings={
                "b1": 14,
                "b2": 0,
                "b3": 0,
                "b4": 0,
                "b5": 18,
                "b7": 10,
                "b11": 10,
                "b12": 10,
                "b13": 10,
                "b14": 10,
            },
            resources=Resources(0, 0, 0, 0),
        )
        # DestPrereq builds b7 L12 finishing in 1200s (with r2=10) -> unlocks b17 L1 next!
        dest_prereq = _make_planet(
            104,
            "02:380:06",
            "DestPrereq",
            buildings={"b1": 6, "b5": 16, "b7": 11, "b11": 12, "b12": 12, "b13": 12, "b14": 12},
            resources=Resources(0, 0, 0, 0),
            storage=Resources(2_000_000, 2_000_000, 2_000_000, 2_000_000),
        )
        state = GameState(
            universe_id=31,
            user_id=1,
            nickname="EarlyNeed",
            timestamp=1_700_000_000.0,
            planets=(donor, dest_soon, dest_late, dest_prereq),
            active_planet_id=101,
            researches={"r1": 2, "r2": 10, "r4": 4, "r5": 4},
            active_queues=(
                QueueJob(
                    category="building",
                    name="Pig iron mine (Level 15)",
                    building_id=1,
                    target_level=15,
                    origin_coords="02:380:04",
                    remaining_seconds=1800,
                ),
                QueueJob(
                    category="building",
                    name="Pig iron mine (Level 15)",
                    building_id=1,
                    target_level=15,
                    origin_coords="02:380:05",
                    remaining_seconds=50_000,
                ),
                QueueJob(
                    category="building",
                    name="Development center (Level 12)",
                    building_id=7,
                    target_level=12,
                    origin_coords="02:380:06",
                    remaining_seconds=1200,
                ),
            ),
            speedup_factor=5.0,
        )
        plan_res = {
            "meta_parameters": {"transport_dispatch_delay_minutes": 120.0},
            "steps": [
                {
                    "seq": 1,
                    "kind": "BUILD",
                    "planet_id": 102,
                    "planet_coords": "02:380:04",
                    "entity_key": "b1",
                    "entity_name": "Pig iron mine",
                    "target_level": 16,
                },
                {
                    "seq": 2,
                    "kind": "BUILD",
                    "planet_id": 103,
                    "planet_coords": "02:380:05",
                    "entity_key": "b1",
                    "entity_name": "Pig iron mine",
                    "target_level": 16,
                },
                {
                    "seq": 3,
                    "kind": "BUILD",
                    "planet_id": 104,
                    "planet_coords": "02:380:06",
                    "entity_key": "b17",
                    "entity_name": "Microsystem accelerator",
                    "target_level": 1,
                },
            ],
        }
        needs = ap.compute_early_transport_needs(state, plan_res, ())
        self.assertIn(102, needs)
        self.assertGreater(needs[102]["pig_iron"], 0)
        self.assertIn("early pre-ship", needs[102]["label"])
        self.assertEqual(needs[102]["need_by_sec"], 1800.0)
        self.assertNotIn(103, needs)  # 50000s > dispatch + travel lead -> no premature shipment!
        self.assertIn(104, needs)  # b7 L10 finishing unlocks b17 L1 -> early pre-ship triggered!
        self.assertGreater(needs[104]["metal"], 0)

    def test_parallel_multi_planet_reconcile_emits_ready_builds_for_all_idle_colonies(self) -> None:
        now_ts = 1_700_005_000.0
        # p1 (active): empty building_offers=() from fast poll, rich stock -> must STILL emit executable BUILD!
        p1 = _make_planet(
            101,
            "02:380:01",
            "ActiveEmptyOffers",
            buildings={"b1": 5, "b3": 4, "b5": 10},
            resources=Resources(50_000, 20_000, 20_000, 10_000),
            building_offers=(),
        )
        # p2 (remote): stale cached offer can_build=False ("Not enough resources"), live stock now affordable -> BUILD!
        p2 = _make_planet(
            102,
            "02:380:02",
            "RemoteStaleOffer",
            buildings={"b1": 5, "b3": 4, "b5": 10},
            resources=Resources(50_000, 20_000, 20_000, 10_000),
            building_offers=(
                BuildingOffer(
                    building_id=1,
                    name="Pig iron mine",
                    current_level=5,
                    target_level=6,
                    cost=Resources(600, 0, 200, 0),
                    can_build=False,
                    status_text="Not enough resources",
                    build_href=None,
                ),
            ),
        )
        # p3 (remote): outdated cached offer target_level=5 while live b1 is already 5 and next step is L6 -> BUILD!
        p3 = _make_planet(
            103,
            "02:380:03",
            "RemoteOutdatedOffer",
            buildings={"b1": 5, "b3": 4, "b5": 10},
            resources=Resources(50_000, 20_000, 20_000, 10_000),
            building_offers=(
                BuildingOffer(
                    building_id=1,
                    name="Pig iron mine",
                    current_level=4,
                    target_level=5,
                    cost=Resources(300, 0, 100, 0),
                    can_build=True,
                    status_text="Build level 5",
                    build_href="href_old",
                ),
            ),
        )
        # p4 (remote): has an EXPIRED building QueueJob (finish_ts <= state.timestamp) -> idle -> BUILD!
        p4 = _make_planet(
            104,
            "02:380:04",
            "RemoteExpiredJob",
            buildings={"b1": 5, "b3": 4, "b5": 10},
            resources=Resources(50_000, 20_000, 20_000, 10_000),
        )
        # p5 (remote): actively constructing b3 L10 (future finish_ts) -> queue full -> WAIT_RESOURCES!
        p5 = _make_planet(
            105,
            "02:380:05",
            "RemoteBusy",
            buildings={"b1": 5, "b3": 9, "b5": 10},
            resources=Resources(50_000, 20_000, 20_000, 10_000),
        )
        # p6 (remote): genuinely short on resources -> WAIT_RESOURCES!
        p6 = _make_planet(
            106,
            "02:380:06",
            "RemotePoor",
            buildings={"b1": 5, "b3": 4, "b5": 10},
            resources=Resources(5, 0, 5, 0),
        )
        state = GameState(
            universe_id=31,
            user_id=1,
            nickname="Parallel",
            timestamp=now_ts,
            planets=(p1, p2, p3, p4, p5, p6),
            active_planet_id=101,
            researches={"r1": 4, "r2": 4},
            active_queues=(
                QueueJob(
                    category="building",
                    name="Pig iron mine (Level 5)",
                    building_id=1,
                    target_level=5,
                    origin_coords="02:380:04",
                    finish_ts=now_ts - 30.0,
                ),
                QueueJob(
                    category="building",
                    name="Kryptonite mine (Level 10)",
                    building_id=3,
                    target_level=10,
                    origin_coords="02:380:05",
                    finish_ts=now_ts + 600.0,
                ),
            ),
            speedup_factor=5.0,
        )
        plan_result = {
            "steps": [
                {
                    "seq": 1,
                    "kind": "BUILD",
                    "planet_id": 106,
                    "planet_coords": "02:380:06",
                    "entity_key": "b1",
                    "entity_name": "Pig iron mine",
                    "target_level": 6,
                },
                {
                    "seq": 2,
                    "kind": "BUILD",
                    "planet_id": 105,
                    "planet_coords": "02:380:05",
                    "entity_key": "b1",
                    "entity_name": "Pig iron mine",
                    "target_level": 6,
                },
                {
                    "seq": 3,
                    "kind": "BUILD",
                    "planet_id": 101,
                    "planet_coords": "02:380:01",
                    "entity_key": "b1",
                    "entity_name": "Pig iron mine",
                    "target_level": 6,
                },
                {
                    "seq": 4,
                    "kind": "BUILD",
                    "planet_id": 102,
                    "planet_coords": "02:380:02",
                    "entity_key": "b1",
                    "entity_name": "Pig iron mine",
                    "target_level": 6,
                },
                {
                    "seq": 5,
                    "kind": "BUILD",
                    "planet_id": 103,
                    "planet_coords": "02:380:03",
                    "entity_key": "b1",
                    "entity_name": "Pig iron mine",
                    "target_level": 6,
                },
                {
                    "seq": 6,
                    "kind": "BUILD",
                    "planet_id": 104,
                    "planet_coords": "02:380:04",
                    "entity_key": "b1",
                    "entity_name": "Pig iron mine",
                    "target_level": 6,
                },
            ]
        }

        actions, conflicts = ap.reconcile_live_ai_actions(state, plan_result, StrategyConfig(allow_multi_planet=True))
        self.assertEqual(conflicts, [])
        by_pid = {a.planet_id: a for a in actions}
        # All 4 idle affordable colonies (p1 active + p2/p3/p4 remote) emit BUILD executable_now=True in parallel!
        for ready_pid in (101, 102, 103, 104):
            self.assertEqual(by_pid[ready_pid].kind, "BUILD", f"planet {ready_pid} should emit BUILD")
            self.assertTrue(by_pid[ready_pid].executable_now, f"planet {ready_pid} should be executable_now")
        # Busy p5 and poor p6 emit WAIT_RESOURCES without blocking p1..p4.
        self.assertEqual(by_pid[105].kind, "WAIT_RESOURCES")
        self.assertFalse(by_pid[105].executable_now)
        self.assertIn("build queue full", by_pid[105].reason)
        self.assertEqual(by_pid[106].kind, "WAIT_RESOURCES")
        self.assertFalse(by_pid[106].executable_now)

        # allow_multi_planet=False restricts execution to active planet only.
        single_acts, _ = ap.reconcile_live_ai_actions(state, plan_result, StrategyConfig(allow_multi_planet=False))
        single_by_pid = {a.planet_id: a for a in single_acts}
        self.assertEqual(single_by_pid[101].kind, "BUILD")
        self.assertTrue(single_by_pid[101].executable_now)
        for remote_pid in (102, 103, 104):
            self.assertEqual(single_by_pid[remote_pid].kind, "WAIT_RESOURCES")
            self.assertFalse(single_by_pid[remote_pid].executable_now)
            self.assertIn("multi-planet disabled", single_by_pid[remote_pid].reason)

        # Active planet with stale "Not enough resources" offer AND a legacy untagged QueueJob from p5 (b3 L10):
        # active planet has b3=4 (4+1 != 10) and zero construction offers -> must NOT be falsely blocked!
        p1_stale = _make_planet(
            101,
            "02:380:01",
            "ActiveStale",
            buildings={"b1": 5, "b3": 4, "b5": 10},
            resources=Resources(50_000, 20_000, 20_000, 10_000),
            building_offers=(
                BuildingOffer(
                    building_id=1,
                    name="Pig iron mine",
                    current_level=5,
                    target_level=6,
                    cost=Resources(600, 0, 200, 0),
                    can_build=False,
                    status_text="Not enough resources",
                    build_href=None,
                ),
            ),
        )
        st_untagged = GameState(
            universe_id=31,
            user_id=1,
            nickname="Untagged",
            timestamp=now_ts,
            planets=(p1_stale, p5),
            active_planet_id=101,
            active_queues=(
                QueueJob(
                    category="building",
                    name="Kryptonite mine (Level 10)",
                    building_id=3,
                    target_level=10,
                    origin_coords="",
                    finish_ts=now_ts + 600.0,
                ),
            ),
            speedup_factor=5.0,
        )
        acts_untagged, _ = ap.reconcile_live_ai_actions(
            st_untagged, plan_result, StrategyConfig(allow_multi_planet=True)
        )
        ut_by_pid = {a.planet_id: a for a in acts_untagged}
        self.assertEqual(ut_by_pid[101].kind, "BUILD")
        self.assertTrue(ut_by_pid[101].executable_now)
        self.assertEqual(ut_by_pid[105].kind, "WAIT_RESOURCES")
        self.assertIn("build queue full", ut_by_pid[105].reason)

        # "In construction" status_text (emitted by live DOM parser) is detected as active construction.
        p1_building = _make_planet(
            101,
            "02:380:01",
            "ActiveConstructing",
            buildings={"b1": 5, "b3": 4, "b5": 10},
            resources=Resources(50_000, 20_000, 20_000, 10_000),
            building_offers=(
                BuildingOffer(
                    building_id=1,
                    name="Pig iron mine",
                    current_level=5,
                    target_level=6,
                    cost=Resources(600, 0, 200, 0),
                    can_build=False,
                    status_text="In construction",
                    build_href=None,
                ),
            ),
        )
        st_constr = GameState(
            universe_id=31,
            user_id=1,
            nickname="Constr",
            timestamp=now_ts,
            planets=(p1_building, p2),
            active_planet_id=101,
            speedup_factor=5.0,
        )
        plan_constr = {
            "steps": [
                {
                    "seq": 1,
                    "kind": "BUILD",
                    "planet_id": 101,
                    "planet_coords": "02:380:01",
                    "entity_key": "b1",
                    "entity_name": "Pig iron mine",
                    "target_level": 6,
                },
                {
                    "seq": 2,
                    "kind": "BUILD",
                    "planet_id": 101,
                    "planet_coords": "02:380:01",
                    "entity_key": "b3",
                    "entity_name": "Kryptonite mine",
                    "target_level": 5,
                },
                {
                    "seq": 3,
                    "kind": "BUILD",
                    "planet_id": 102,
                    "planet_coords": "02:380:02",
                    "entity_key": "b1",
                    "entity_name": "Pig iron mine",
                    "target_level": 6,
                },
            ]
        }
        acts_constr, _ = ap.reconcile_live_ai_actions(st_constr, plan_constr, StrategyConfig(allow_multi_planet=True))
        constr_by_pid = {a.planet_id: a for a in acts_constr}
        # p1 skips in-flight b1 L6, queues b3 L5 as WAIT_RESOURCES (build queue full), while p2 STILL builds!
        self.assertEqual(constr_by_pid[101].target_id, 3)
        self.assertEqual(constr_by_pid[101].kind, "WAIT_RESOURCES")
        self.assertIn("build queue full", constr_by_pid[101].reason)
        self.assertEqual(constr_by_pid[102].kind, "BUILD")
        self.assertTrue(constr_by_pid[102].executable_now)

    def test_commander_tick_executes_parallel_ai_builds_across_colonies_despite_failures_and_cap(self) -> None:
        import importlib.util

        cmd_path = REPO_ROOT / "bin" / "commander.py"
        spec = importlib.util.spec_from_file_location("commander_parallel_test_mod", cmd_path)
        assert spec is not None and spec.loader is not None
        commander_mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(commander_mod)

        planets = tuple(
            _make_planet(
                100 + i,
                f"02:380:0{i}",
                f"Colony-{i}",
                buildings={"b1": 5, "b5": 10},
                resources=Resources(50_000, 20_000, 20_000, 10_000),
            )
            for i in range(1, 5)
        )
        base_state = GameState(
            universe_id=31,
            user_id=1,
            nickname="CommanderParallel",
            timestamp=1_700_005_000.0,
            planets=planets,
            active_planet_id=101,
            speedup_factor=5.0,
        )

        class _FakeParallelApi:
            def __init__(self, *, trigger_captcha: bool = False) -> None:
                self.state = base_state
                self.built: set[int] = set()
                self.dispatched: list[tuple[str, int]] = []
                self.trigger_captcha = trigger_captcha

            def get_state(self, include_buildings: bool = True) -> GameState:
                return self.state

            def send_command(self, payload: dict[str, object]) -> dict[str, object]:
                cmd = str(payload.get("command") or "")
                if cmd == "plan":
                    # Intentionally put SPY & non-emergency THROTTLE ahead of BUILD to verify commander prioritization.
                    plan_actions_raw = [
                        {
                            "priority": "P1",
                            "kind": "SPY",
                            "planet_id": 101,
                            "planet_coords": "02:380:01",
                            "target_id": None,
                            "target_name": "02:380:09",
                            "target_level": 1,
                            "reason": "Auto-spy inactive target",
                            "executable_now": True,
                        },
                        {
                            "priority": "P1",
                            "kind": "THROTTLE_BUILDING",
                            "planet_id": 101,
                            "planet_coords": "02:380:01",
                            "target_id": 2,
                            "target_name": "Blast furnace",
                            "target_level": 0,
                            "reason": "Pause blast furnace to save pig iron",
                            "executable_now": True,
                        },
                    ]
                    for pid in (101, 102, 103, 104):
                        if pid in self.built:
                            continue
                        plan_actions_raw.append(
                            {
                                "priority": "P1",
                                "kind": "BUILD",
                                "planet_id": pid,
                                "planet_coords": f"02:380:0{pid - 100}",
                                "target_id": 1,
                                "target_name": "Pig iron mine",
                                "target_level": 6,
                                "reason": f"AI-Planner #{pid - 100}: build Pig iron mine L6",
                                "executable_now": True,
                            }
                        )
                    return {
                        "ok": True,
                        "effective_planner_mode": "ai_planner",
                        "actions": plan_actions_raw,
                    }

                pid = int(payload.get("planet_id") or 0)
                self.dispatched.append((cmd, pid))
                if self.trigger_captcha:
                    return {
                        "ok": True,
                        "executed": False,
                        "captcha_gate": True,
                        "paused": True,
                        "message": "CAPTCHA detected",
                    }
                if cmd == "build" and pid == 102:
                    # Simulate non-fatal failure WITHOUT state payload: must NOT abort remaining colonies!
                    return {
                        "ok": True,
                        "executed": False,
                        "message": "Stale offer on planet 102",
                    }
                if cmd == "build":
                    self.built.add(pid)
                return {
                    "ok": True,
                    "executed": True,
                    "message": f"Executed {cmd} on {pid}",
                }

        cfg = StrategyConfig(planner_mode="ai_planner", max_planets=4, allow_multi_planet=True)
        fake_api = _FakeParallelApi()
        _st, _act, _res, _nxt, outcomes = commander_mod.run_commander_tick(
            fake_api,
            live=True,
            max_actions=2,
            config=cfg,
        )
        # 1. All 4 planets' BUILD commands run BEFORE SPY / non-emergency THROTTLE!
        self.assertEqual(fake_api.dispatched[:4], [("build", 101), ("build", 102), ("build", 103), ("build", 104)])
        # 2. Failure on 102 did NOT abort the tick, and distinct-planet overflow built 101, 103, 104 despite max_actions=2!
        self.assertEqual(fake_api.built, {101, 103, 104})
        self.assertEqual(len(outcomes), 4)

        # 3. CAPTCHA / pause gate stops the batch immediately with zero further commands.
        captcha_api = _FakeParallelApi(trigger_captcha=True)
        commander_mod.run_commander_tick(
            captcha_api,
            live=True,
            max_actions=5,
            config=cfg,
        )
        self.assertEqual(captcha_api.dispatched, [("build", 101)])

    def test_initial_blast_furnace_state_and_smelting_transitions(self) -> None:
        # Target requires b7 L5 (which needs Metal > 0), starting with 0 Metal so AI-Planner must smelt at t=0.
        target_single = _sample_target_config(
            [
                _target_planet_entry(
                    1,
                    101,
                    "02:380:12",
                    "Homeworld",
                    {
                        "b1": 8,
                        "b2": 5,
                        "b3": 6,
                        "b4": 4,
                        "b5": 12,
                        "b7": 5,
                        "b8": 3,
                        "b11": 6,
                        "b12": 6,
                        "b13": 6,
                        "b14": 6,
                    },
                    is_central=True,
                    pays_for_research=True,
                ),
            ],
            planned_research={"r1": 2, "r2": 4},
        )
        base_b = {
            "b1": 8,
            "b2": 5,
            "b3": 6,
            "b4": 4,
            "b5": 12,
            "b7": 4,
            "b8": 3,
            "b11": 6,
            "b12": 6,
            "b13": 6,
            "b14": 6,
        }

        def _state_with_single(res: Resources, eco: dict[str, int]) -> GameState:
            return GameState(
                universe_id=31,
                user_id=1,
                nickname="SmeltTest",
                timestamp=1_700_000_000.0,
                planets=(
                    _make_planet(
                        101,
                        "02:380:12",
                        "Homeworld",
                        buildings=base_b,
                        resources=res,
                        economy_rates=eco,
                    ),
                ),
                active_planet_id=101,
                researches={"r1": 2, "r2": 4},
                speedup_factor=5.0,
            )

        # Case 1: Live b2 OFF (0%), 0 Metal stock -> AI-Planner wants ON (100%) at t=0 -> emits t=0 ENABLE toggle!
        st_off_need = _state_with_single(Resources(200_000, 0, 200_000, 100_000), {"resource2": 0})
        plan_off_need = ap.run_ai_planner(st_off_need, target_single, params={"reward_weeks": 2, "ga_generations": 0})
        sm1 = plan_off_need["smelting"]
        self.assertEqual(sm1["initial_live_mode"], "off")
        self.assertFalse(sm1["initial_live_any_enabled"])
        self.assertTrue(sm1["initial_target_enabled"])
        self.assertEqual(sm1["initial_target_pct"], 100)
        self.assertTrue(sm1["initial_toggle_needed"])
        self.assertEqual(len(sm1["initial_planets_needing_toggle"]), 1)
        self.assertEqual(sm1["initial_planets_needing_toggle"][0]["from_pct"], 0)
        self.assertEqual(sm1["initial_planets_needing_toggle"][0]["to_pct"], 100)
        self.assertGreater(sm1["metal_smelted"], 0)
        self.assertGreater(len(sm1["smelting_spans"]), 0)
        self.assertGreaterEqual(len(sm1["smelting_events"]), 2)
        ev0 = sm1["smelting_events"][0]
        self.assertTrue(ev0["is_initial"])
        self.assertEqual(ev0["time_sec"], 0.0)
        self.assertEqual(ev0["action"], "enable")
        self.assertTrue(ev0["enabled"])
        self.assertEqual(ev0["from_mode"], "off")
        # Once Metal is satisfied / step starts, AI-Planner emits a subsequent DISABLE event.
        self.assertEqual(sm1["smelting_events"][1]["action"], "disable")
        self.assertFalse(sm1["smelting_events"][1]["is_initial"])
        self.assertGreater(sm1["smelting_events"][1]["time_sec"], 0.0)
        # Summary mirrors smelting metadata
        self.assertEqual(plan_off_need["summary"]["initial_smelting_live_mode"], "off")
        self.assertFalse(plan_off_need["summary"]["initial_smelting_live_enabled"])
        self.assertTrue(plan_off_need["summary"]["initial_smelting_target_enabled"])
        self.assertTrue(plan_off_need["summary"]["initial_smelting_toggle_needed"])
        self.assertEqual(plan_off_need["summary"]["smelting_toggles_count"], len(sm1["smelting_events"]))

        # Case 2: Live b2 ALREADY ON (100%) with same 0 Metal stock -> aligned at t=0, NO initial toggle event,
        # and simulation reward/steps/metal_smelted remain 100% identical to Case 1!
        st_on_need = _state_with_single(Resources(200_000, 0, 200_000, 100_000), {"resource2": 100})
        plan_on_need = ap.run_ai_planner(st_on_need, target_single, params={"reward_weeks": 2, "ga_generations": 0})
        sm2 = plan_on_need["smelting"]
        self.assertEqual(sm2["initial_live_mode"], "on")
        self.assertTrue(sm2["initial_live_any_enabled"])
        self.assertTrue(sm2["initial_live_all_enabled"])
        self.assertTrue(sm2["initial_target_enabled"])
        self.assertFalse(sm2["initial_toggle_needed"])
        self.assertEqual(sm2["initial_planets_needing_toggle"], [])
        self.assertFalse(any(ev["is_initial"] for ev in sm2["smelting_events"]))
        self.assertEqual(sm2["metal_smelted"], sm1["metal_smelted"])
        self.assertEqual(plan_on_need["summary"]["discounted_reward"], plan_off_need["summary"]["discounted_reward"])
        self.assertEqual(len(plan_on_need["steps"]), len(plan_off_need["steps"]))

        # Case 3: Live b2 ON (100%), abundant Metal stock -> AI-Planner wants OFF (0%) at t=0 -> emits t=0 DISABLE!
        st_on_abundant = _state_with_single(Resources(0, 300_000, 200_000, 100_000), {"resource2": 100})
        plan_on_abundant = ap.run_ai_planner(
            st_on_abundant, target_single, params={"reward_weeks": 2, "ga_generations": 0}
        )
        sm3 = plan_on_abundant["smelting"]
        self.assertEqual(sm3["initial_live_mode"], "on")
        self.assertFalse(sm3["initial_target_enabled"])
        self.assertTrue(sm3["initial_toggle_needed"])
        self.assertEqual(sm3["smelting_events"][0]["is_initial"], True)
        self.assertEqual(sm3["smelting_events"][0]["action"], "disable")
        self.assertEqual(sm3["smelting_events"][0]["target_pct"], 0)
        self.assertEqual(sm3["smelting_events"][0]["from_mode"], "on")

        # Case 4: Mixed multi-colony (P1 ON 100%, P2 OFF 0%, P3 no b2) -> only mismatched b2 planet flagged!
        p1_on = _make_planet(
            101,
            "02:380:01",
            "Prime",
            buildings=base_b,
            resources=Resources(100_000, 0, 100_000, 50_000),
            economy_rates={"resource2": 100},
        )
        p2_off = _make_planet(
            102,
            "02:380:02",
            "Forge",
            buildings={"b1": 8, "b2": 4, "b3": 4, "b5": 12, "b11": 6, "b12": 6, "b13": 6, "b14": 6},
            resources=Resources(50_000, 0, 50_000, 20_000),
            economy_rates={"resource2": 0},
        )
        p3_nob2 = _make_planet(
            103,
            "02:380:03",
            "Outpost",
            buildings={"b1": 6, "b3": 4, "b5": 10, "b11": 6, "b12": 6, "b13": 6, "b14": 6},
            resources=Resources(50_000, 0, 50_000, 20_000),
            economy_rates={"resource2": 0},
        )
        st_mixed = GameState(
            universe_id=31,
            user_id=1,
            nickname="MixedSmelt",
            timestamp=1_700_000_000.0,
            planets=(p1_on, p2_off, p3_nob2),
            active_planet_id=101,
            researches={"r1": 2, "r2": 4},
            speedup_factor=5.0,
        )
        target_mixed = _sample_target_config(
            [
                _target_planet_entry(
                    1, 101, "02:380:01", "Prime", {**base_b, "b7": 5}, is_central=True, pays_for_research=True
                ),
                _target_planet_entry(
                    2,
                    102,
                    "02:380:02",
                    "Forge",
                    {"b1": 8, "b2": 4, "b3": 4, "b5": 12, "b11": 6, "b12": 6, "b13": 6, "b14": 6},
                ),
                _target_planet_entry(
                    3, 103, "02:380:03", "Outpost", {"b1": 6, "b3": 4, "b5": 10, "b11": 6, "b12": 6, "b13": 6, "b14": 6}
                ),
            ],
            planned_research={"r1": 2, "r2": 4},
        )
        plan_mixed = ap.run_ai_planner(
            st_mixed,
            target_mixed,
            params={"reward_weeks": 2, "ga_generations": 0, "transport_dispatch_delay_minutes": 0},
        )
        sm4 = plan_mixed["smelting"]
        self.assertEqual(sm4["initial_live_mode"], "mixed")
        self.assertEqual(sm4["initial_live_enabled_count"], 1)
        self.assertEqual(sm4["initial_live_disabled_count"], 1)
        self.assertEqual(sm4["total_b2_planets"], 2)
        self.assertTrue(sm4["initial_target_enabled"])
        self.assertTrue(sm4["initial_toggle_needed"])
        # Only planet 102 (0% -> 100%) needs toggling; 101 is already 100% and 103 has no b2!
        self.assertEqual([row["planet_id"] for row in sm4["initial_planets_needing_toggle"]], [102])
        self.assertEqual(sm4["smelting_events"][0]["action"], "enable")
        self.assertEqual(sm4["smelting_events"][0]["from_mode"], "mixed")

        # Case 5: No b2 built anywhere yet -> mode="none", no t=0 toggle needed!
        p_nob2 = _make_planet(
            201,
            "02:380:09",
            "Fresh",
            buildings={"b1": 4, "b3": 2, "b5": 6, "b11": 4, "b12": 4, "b13": 4, "b14": 4},
            resources=Resources(10_000, 0, 10_000, 5_000),
        )
        st_nob2 = GameState(
            universe_id=31,
            user_id=1,
            nickname="NoB2",
            timestamp=1_700_000_000.0,
            planets=(p_nob2,),
            active_planet_id=201,
            researches={"r1": 1, "r2": 1},
            speedup_factor=5.0,
        )
        target_nob2 = _sample_target_config(
            [
                _target_planet_entry(
                    1,
                    201,
                    "02:380:09",
                    "Fresh",
                    {"b1": 5, "b3": 2, "b5": 6, "b11": 4, "b12": 4, "b13": 4, "b14": 4},
                    is_central=True,
                )
            ],
            planned_research={"r1": 1, "r2": 1},
        )
        plan_nob2 = ap.run_ai_planner(st_nob2, target_nob2, params={"reward_weeks": 1, "ga_generations": 0})
        sm5 = plan_nob2["smelting"]
        self.assertEqual(sm5["initial_live_mode"], "none")
        self.assertEqual(sm5["total_b2_planets"], 0)
        self.assertFalse(sm5["initial_target_enabled"])
        self.assertFalse(sm5["initial_toggle_needed"])
        self.assertEqual(sm5["smelting_events"], [])

    def test_compute_inbound_friendly_cargo_and_in_progress_rem_helpers(self) -> None:
        now_ts = 1_700_010_000.0
        p1 = _make_planet(
            101,
            "02:380:01",
            "Prime",
            buildings={"b1": 10, "b7": 6, "b8": 8},
            building_offers=(
                BuildingOffer(
                    building_id=1,
                    name="Pig iron mine",
                    current_level=10,
                    target_level=11,
                    cost=Resources(5000, 0, 2000, 0),
                    can_build=False,
                    status_text="In construction",
                ),
            ),
        )
        p2 = _make_planet(
            102,
            "02:380:04",
            "Forge",
            buildings={"b1": 8, "b3": 6, "b7": 4},
        )
        p3 = _make_planet(
            103,
            "02:380:09",
            "OfferOnlyBuild",
            buildings={"b1": 6, "b7": 4},
            building_offers=(
                BuildingOffer(
                    building_id=1,
                    name="Pig iron mine",
                    current_level=6,
                    target_level=7,
                    cost=Resources(1000, 0, 400, 0),
                    can_build=False,
                    status_text="In progress",
                ),
            ),
        )
        raid_p = _make_planet(104, "02:380:15", "RaidBase", planet_type="raid", buildings={"b1": 5})

        class _HostileJob(QueueJob):
            hostile: bool = True

        state = GameState(
            universe_id=31,
            user_id=1,
            nickname="HelpersTest",
            timestamp=now_ts,
            planets=(p1, p2, p3, raid_p),
            active_planet_id=101,
            researches={"r1": 4},
            active_queues=(
                # Outbound Transport -> p2
                QueueJob(
                    category="fleet",
                    name="Transport → 02:380:04",
                    detail="Outbound · 5 Small transport",
                    origin_coords="02:380:01",
                    remaining_seconds=600,
                    finish_ts=now_ts + 600,
                    cargo=Resources(1_000, 5_000, 2_000, 500),
                ),
                # Outbound Station with both origin & dest coords in name -> must pick destination p2!
                QueueJob(
                    category="fleet",
                    name="Station 02:380:01 -> 02:380:04",
                    detail="Outbound · 3 Small transport",
                    origin_coords="02:380:01",
                    remaining_seconds=900,
                    finish_ts=now_ts + 900,
                    cargo=Resources(0, 3_000, 1_000, 0),
                ),
                # Returning fleet carrying loot -> credited to origin_coords (p1)
                QueueJob(
                    category="fleet",
                    name="↩ Attack 02:381:12",
                    detail="Returning · 4 Small transport",
                    origin_coords="02:380:01",
                    remaining_seconds=450,
                    finish_ts=now_ts + 450,
                    cargo=Resources(4_000, 2_500, 1_500, 750),
                ),
                # Outbound Attack carrying fuel/cargo -> NOT inbound friendly delivery
                QueueJob(
                    category="fleet",
                    name="Attack → 02:380:04",
                    detail="Outbound · 10 Fighter",
                    origin_coords="02:380:01",
                    remaining_seconds=300,
                    finish_ts=now_ts + 300,
                    cargo=Resources(9_999, 9_999, 9_999, 9_999),
                ),
                # Hostile fleet -> MUST be excluded!
                _HostileJob(
                    category="fleet",
                    name="Transport → 02:380:04",
                    detail="Hostile incoming",
                    origin_coords="02:399:01",
                    remaining_seconds=200,
                    finish_ts=now_ts + 200,
                    cargo=Resources(50_000, 50_000, 50_000, 50_000),
                ),
                # Expired friendly Transport -> already landed, MUST be excluded!
                QueueJob(
                    category="fleet",
                    name="Transport → 02:380:04",
                    detail="Outbound · 2 Small transport",
                    origin_coords="02:380:01",
                    finish_ts=now_ts - 15.0,
                    cargo=Resources(10_000, 10_000, 0, 0),
                ),
                # Outbound Transport to raid planet -> ignored (normal colonies only)
                QueueJob(
                    category="fleet",
                    name="Transport → 02:380:15",
                    detail="Outbound · 2 Small transport",
                    origin_coords="02:380:01",
                    remaining_seconds=300,
                    finish_ts=now_ts + 300,
                    cargo=Resources(1_000, 1_000, 0, 0),
                ),
                # Building job on p1 with exact 120s left (must NOT be overwritten by longer Wiki fallback on offer!)
                QueueJob(
                    category="building",
                    name="Pig iron mine (Level 11)",
                    building_id=1,
                    target_level=11,
                    origin_coords="02:380:01",
                    remaining_seconds=120,
                    finish_ts=now_ts + 120,
                ),
                # Two building jobs on p2 (one via finish_ts=900s, one via remaining_text="01:15:30"=4530s -> max=4530s)
                QueueJob(
                    category="building",
                    name="Pig iron mine (Level 9)",
                    building_id=1,
                    target_level=9,
                    origin_coords="02:380:04",
                    finish_ts=now_ts + 900.0,
                ),
                QueueJob(
                    category="building",
                    name="Kryptonite mine (Level 7)",
                    building_id=3,
                    target_level=7,
                    origin_coords="02:380:04",
                    remaining_text="01:15:30",
                ),
                # Expired building job on p3 (should not mask offer fallback)
                QueueJob(
                    category="building",
                    name="Kryptonite mine (Level 5)",
                    building_id=3,
                    target_level=5,
                    origin_coords="02:380:09",
                    finish_ts=now_ts - 5.0,
                ),
                # Active research job (2400s remaining)
                QueueJob(
                    category="research",
                    name="Mining technology (Level 5)",
                    target_level=5,
                    remaining_seconds=2400,
                    finish_ts=now_ts + 2400,
                ),
            ),
            speedup_factor=5.0,
        )

        inbound = ap.compute_inbound_friendly_cargo_by_planet(state)
        self.assertEqual(set(inbound.keys()), {101, 102})
        self.assertEqual(inbound[101], Resources(4_000, 2_500, 1_500, 750))
        self.assertEqual(inbound[102], Resources(1_000, 8_000, 3_000, 500))
        self.assertNotIn(104, inbound)

        build_rem = ap.compute_in_progress_build_rem_by_planet(state)
        self.assertEqual(build_rem[101], 120.0)
        self.assertEqual(build_rem[102], 4530.0)
        self.assertIn(103, build_rem)
        self.assertGreater(build_rem[103], 0.0)
        self.assertNotIn(104, build_rem)

        self.assertEqual(ap.compute_in_progress_research_rem_seconds(state), 2400.0)

        # Idle state returns empty dicts & 0.0
        idle_state = GameState(
            universe_id=31,
            user_id=1,
            nickname="Idle",
            timestamp=now_ts,
            planets=(p2,),
            active_planet_id=102,
            speedup_factor=5.0,
        )
        self.assertEqual(ap.compute_inbound_friendly_cargo_by_planet(idle_state), {})
        self.assertEqual(ap.compute_in_progress_build_rem_by_planet(idle_state), {})
        self.assertEqual(ap.compute_in_progress_research_rem_seconds(idle_state), 0.0)

    def test_reconcile_remote_research_start_rule_adaptive_vs_switch_off(self) -> None:
        lab = _make_planet(
            101,
            "02:380:01",
            "LabColony",
            buildings={"b1": 10, "b8": 8},
            resources=Resources(50_000, 50_000, 50_000, 20_000),
        )
        remote_other = _make_planet(
            102,
            "02:380:02",
            "NonLabActive",
            buildings={"b1": 10, "b8": 0},
            resources=Resources(100, 100, 100, 100),
        )
        stale_nonlab_offer = ResearchOffer(
            research_id=1,
            research_key="r1",
            name="Mining technology",
            current_level=2,
            target_level=3,
            cost=Resources(4_000, 0, 2_000, 0),
            can_research=False,
            status_text="Not enough resources",
            research_href=None,
            active=False,
        )
        ready_lab_offer = ResearchOffer(
            research_id=1,
            research_key="r1",
            name="Mining technology",
            current_level=2,
            target_level=3,
            cost=Resources(4_000, 0, 2_000, 0),
            can_research=True,
            status_text="Research level 3",
            research_href="indexInternal.es?action=internalBuildItem&researchID=1",
            active=False,
        )
        plan_res = {
            "steps": [
                {
                    "seq": 1,
                    "kind": "RESEARCH",
                    "planet_id": 0,
                    "planet_coords": "account",
                    "entity_key": "r1",
                    "entity_name": "Mining technology",
                    "target_level": 3,
                }
            ]
        }

        # 1. No offers cached on active lab planet
        st_empty_offers = GameState(
            universe_id=31,
            user_id=1,
            nickname="ResTest",
            timestamp=1_700_000_000.0,
            planets=(lab, remote_other),
            active_planet_id=101,
            researches={"r1": 2, "r2": 4},
            research_offers=(),
            speedup_factor=5.0,
        )
        # Switch OFF -> exact legacy behavior (executable_now=False)
        acts_off, _ = ap.reconcile_live_ai_actions(st_empty_offers, plan_res, _config_with_adaptive(False))
        self.assertEqual(len(acts_off), 1)
        self.assertFalse(acts_off[0].executable_now)
        # Switch ON -> Wiki cost fallback allows execution with action_href=None
        acts_on, _ = ap.reconcile_live_ai_actions(st_empty_offers, plan_res, _config_with_adaptive(True))
        self.assertEqual(len(acts_on), 1)
        self.assertTrue(acts_on[0].executable_now)
        self.assertIsNone(acts_on[0].action_href)
        self.assertEqual(acts_on[0].planet_id, 101)
        self.assertEqual(acts_on[0].cost, Resources(4_000, 0, 2_000, 0))

        # 2. Offers read on a NON-LAB active planet (102) with can_research=False
        st_nonlab_active = GameState(
            universe_id=31,
            user_id=1,
            nickname="ResTest",
            timestamp=1_700_000_000.0,
            planets=(lab, remote_other),
            active_planet_id=102,
            researches={"r1": 2, "r2": 4},
            research_offers=(stale_nonlab_offer,),
            speedup_factor=5.0,
        )
        # Switch OFF -> trusts stale offer -> executable_now=False
        acts_nonlab_off, _ = ap.reconcile_live_ai_actions(st_nonlab_active, plan_res, _config_with_adaptive(False))
        self.assertFalse(acts_nonlab_off[0].executable_now)
        # Switch ON + allow_multi_planet=True -> ignores non-lab offer, executes on lab 101 with action_href=None!
        acts_nonlab_on, _ = ap.reconcile_live_ai_actions(
            st_nonlab_active, plan_res, _config_with_adaptive(True, allow_multi_planet=True)
        )
        self.assertTrue(acts_nonlab_on[0].executable_now)
        self.assertIsNone(acts_nonlab_on[0].action_href)
        self.assertEqual(acts_nonlab_on[0].planet_id, 101)
        # Switch ON + allow_multi_planet=False -> remote lab blocked!
        acts_nonlab_single, _ = ap.reconcile_live_ai_actions(
            st_nonlab_active, plan_res, _config_with_adaptive(True, allow_multi_planet=False)
        )
        self.assertFalse(acts_nonlab_single[0].executable_now)
        self.assertIn("multi-planet disabled", acts_nonlab_single[0].reason)
        # Switch ON + allow_research=False -> blocked!
        acts_nonlab_nores, _ = ap.reconcile_live_ai_actions(
            st_nonlab_active, plan_res, _config_with_adaptive(True, allow_research=False)
        )
        self.assertFalse(acts_nonlab_nores[0].executable_now)
        self.assertIn("research disabled", acts_nonlab_nores[0].reason)

        # 3. Offers read on ACTIVE lab planet (101) ARE trusted
        st_active_lab_blocked = GameState(
            universe_id=31,
            user_id=1,
            nickname="ResTest",
            timestamp=1_700_000_000.0,
            planets=(lab, remote_other),
            active_planet_id=101,
            researches={"r1": 2, "r2": 4},
            research_offers=(stale_nonlab_offer,),
            speedup_factor=5.0,
        )
        acts_lab_blocked, _ = ap.reconcile_live_ai_actions(st_active_lab_blocked, plan_res, _config_with_adaptive(True))
        self.assertFalse(acts_lab_blocked[0].executable_now)

        st_active_lab_ready = GameState(
            universe_id=31,
            user_id=1,
            nickname="ResTest",
            timestamp=1_700_000_000.0,
            planets=(lab, remote_other),
            active_planet_id=101,
            researches={"r1": 2, "r2": 4},
            research_offers=(ready_lab_offer,),
            speedup_factor=5.0,
        )
        acts_lab_ready, _ = ap.reconcile_live_ai_actions(st_active_lab_ready, plan_res, _config_with_adaptive(True))
        self.assertTrue(acts_lab_ready[0].executable_now)
        self.assertEqual(acts_lab_ready[0].action_href, ready_lab_offer.research_href)

        # 4. Research slot busy -> executable_now=False even when adaptive=True
        st_busy = GameState(
            universe_id=31,
            user_id=1,
            nickname="ResTest",
            timestamp=1_700_000_000.0,
            planets=(lab, remote_other),
            active_planet_id=102,
            researches={"r1": 2, "r2": 4},
            active_queues=(QueueJob(category="research", name="Energy technology (Level 5)", target_level=5),),
            speedup_factor=5.0,
        )
        acts_busy, _ = ap.reconcile_live_ai_actions(st_busy, plan_res, _config_with_adaptive(True))
        self.assertFalse(acts_busy[0].executable_now)
        self.assertIn("research slot busy", acts_busy[0].reason)

        # 5. Lab planet unaffordable -> executable_now=False even when adaptive=True
        poor_lab = _make_planet(
            101,
            "02:380:01",
            "LabPoor",
            buildings={"b1": 10, "b8": 8},
            resources=Resources(500, 0, 500, 0),
        )
        st_poor_lab = GameState(
            universe_id=31,
            user_id=1,
            nickname="ResTest",
            timestamp=1_700_000_000.0,
            planets=(poor_lab, remote_other),
            active_planet_id=102,
            researches={"r1": 2, "r2": 4},
            research_offers=(),
            speedup_factor=5.0,
        )
        acts_poor, _ = ap.reconcile_live_ai_actions(st_poor_lab, plan_res, _config_with_adaptive(True))
        self.assertFalse(acts_poor[0].executable_now)
        self.assertFalse(acts_poor[0].missing.is_zero())

    def test_transport_demand_telemetry_and_summary_fields(self) -> None:
        donor = _make_planet(
            101,
            "02:380:01",
            "Donor",
            buildings={
                "b1": 12,
                "b2": 8,
                "b3": 10,
                "b4": 8,
                "b5": 16,
                "b7": 6,
                "b8": 4,
                "b11": 8,
                "b12": 8,
                "b13": 8,
                "b14": 8,
            },
            resources=Resources(500_000, 300_000, 300_000, 150_000),
        )
        dest = _make_planet(
            102,
            "02:380:05",
            "Recipient",
            buildings={"b1": 4, "b2": 0, "b3": 0, "b4": 0, "b5": 10, "b7": 2, "b11": 6, "b12": 6, "b13": 6, "b14": 6},
            resources=Resources(0, 0, 0, 0),
        )
        state = GameState(
            universe_id=31,
            user_id=1,
            nickname="DemandTest",
            timestamp=1_700_000_000.0,
            planets=(donor, dest),
            active_planet_id=101,
            researches={"r1": 2, "r2": 4, "r4": 3, "r5": 3},
            speedup_factor=5.0,
        )
        target = _sample_target_config(
            [
                _target_planet_entry(
                    1,
                    101,
                    "02:380:01",
                    "Donor",
                    {
                        "b1": 12,
                        "b2": 8,
                        "b3": 10,
                        "b4": 8,
                        "b5": 16,
                        "b7": 6,
                        "b8": 4,
                        "b11": 8,
                        "b12": 8,
                        "b13": 8,
                        "b14": 8,
                    },
                    is_central=True,
                    pays_for_research=True,
                ),
                _target_planet_entry(
                    2,
                    102,
                    "02:380:05",
                    "Recipient",
                    {"b1": 10, "b3": 8, "b5": 12, "b7": 5, "b11": 6, "b12": 6, "b13": 6, "b14": 6},
                ),
            ],
            planned_research={"r1": 2, "r2": 4},
        )
        plan = ap.run_ai_planner(
            state,
            target,
            params={"reward_weeks": 2, "ga_generations": 0, "transport_dispatch_delay_minutes": 0.0},
        )
        td = plan["transport_demand"]
        expected_keys = {
            "cargo_4h",
            "cargo_per_hour_4h",
            "cargo_24h",
            "cargo_per_hour_24h",
            "max_single_shipment_cargo",
            "shipment_steps_4h",
            "shipment_steps_24h",
            "avg_one_way_seconds",
            "round_trips_per_hour",
            "utilization_assumed",
            "recommended_cargo_capacity",
            "recommended_large_transports",
            "recommended_small_transports",
        }
        self.assertEqual(set(td.keys()), expected_keys)
        self.assertGreater(td["cargo_4h"], 0)
        self.assertGreaterEqual(td["cargo_24h"], td["cargo_4h"])
        self.assertGreater(td["max_single_shipment_cargo"], 0)
        self.assertLessEqual(td["max_single_shipment_cargo"], td["cargo_24h"])
        self.assertGreaterEqual(td["shipment_steps_4h"], 1)
        self.assertGreaterEqual(td["shipment_steps_24h"], td["shipment_steps_4h"])
        self.assertEqual(td["avg_one_way_seconds"], plan["transport_matrix"]["avg_one_way_seconds"])
        self.assertGreater(td["avg_one_way_seconds"], 0.0)
        self.assertAlmostEqual(
            td["round_trips_per_hour"], round(3600.0 / (2.0 * td["avg_one_way_seconds"]), 4), places=4
        )
        self.assertEqual(td["utilization_assumed"], 0.5)
        expected_cap = int(math.ceil(td["cargo_per_hour_24h"] / max(0.25, td["round_trips_per_hour"]) / 0.5))
        self.assertEqual(td["recommended_cargo_capacity"], expected_cap)
        self.assertEqual(td["recommended_large_transports"], int(math.ceil(expected_cap / 30_000.0)))
        self.assertEqual(td["recommended_small_transports"], int(math.ceil(expected_cap / 6_000.0)))

        # Summary mirrors key transport demand fields
        self.assertEqual(plan["summary"]["planned_cargo_per_hour_24h"], td["cargo_per_hour_24h"])
        self.assertEqual(plan["summary"]["recommended_cargo_capacity"], td["recommended_cargo_capacity"])
        self.assertEqual(plan["summary"]["recommended_large_transports"], td["recommended_large_transports"])

        # Single-planet empire has zero transport demand and fallback 2.5 round_trips_per_hour
        single_state = GameState(
            universe_id=31,
            user_id=1,
            nickname="Single",
            timestamp=1_700_000_000.0,
            planets=(donor,),
            active_planet_id=101,
            researches={"r1": 2, "r2": 4},
            speedup_factor=5.0,
        )
        single_target = _sample_target_config(
            [
                _target_planet_entry(
                    1,
                    101,
                    "02:380:01",
                    "Donor",
                    {
                        "b1": 13,
                        "b2": 8,
                        "b3": 10,
                        "b4": 8,
                        "b5": 16,
                        "b7": 6,
                        "b8": 4,
                        "b11": 8,
                        "b12": 8,
                        "b13": 8,
                        "b14": 8,
                    },
                    is_central=True,
                    pays_for_research=True,
                ),
            ],
            planned_research={"r1": 2, "r2": 4},
        )
        single_plan = ap.run_ai_planner(single_state, single_target, params={"reward_weeks": 1, "ga_generations": 0})
        std = single_plan["transport_demand"]
        self.assertEqual(std["cargo_4h"], 0)
        self.assertEqual(std["cargo_24h"], 0)
        self.assertEqual(std["avg_one_way_seconds"], 0.0)
        self.assertEqual(std["round_trips_per_hour"], 2.5)
        self.assertEqual(std["recommended_cargo_capacity"], 0)
        self.assertEqual(std["recommended_large_transports"], 0)
        self.assertEqual(std["recommended_small_transports"], 0)

    def test_diagnose_ai_colony_idle_states_all_buckets_step5_gate_and_b2_energy(self) -> None:
        now_ts = 1_700_020_000.0
        # b2 L10 consumes 80 energy (mine_energy_needed(2, 10) == 80)
        # 1. building (active queue + affordable later step -> idle_with_affordable MUST be False!)
        p_build = _make_planet(
            101,
            "02:380:01",
            "Col-Building",
            buildings={"b1": 5, "b2": 10, "b3": 4},
            resources=Resources(50_000, 50_000, 50_000, 20_000),
            energy=EnergyBalance(produced=400, used=250),  # net=150; b2=0% -> margin@100 = 150 - 80 = 70
            economy_rates={"resource2": 0},
        )
        # 2. ready_not_started (top step b1 L6 affordable -> idle_with_affordable MUST be False!)
        p_ready = _make_planet(
            102,
            "02:380:02",
            "Col-Ready",
            buildings={"b1": 5, "b2": 10},
            resources=Resources(50_000, 50_000, 50_000, 20_000),
            energy=EnergyBalance(produced=400, used=330),  # net=70; b2=100% -> margin@100 = 70 - 0 = 70
            economy_rates={"resource2": 100},
        )
        # 3. field_conflict (fields_used == fields_max)
        p_field = _make_planet(
            103,
            "02:380:03",
            "Col-Fields",
            fields_max=20,
            fields_used=20,
            buildings={"b1": 10, "b5": 10},
            resources=Resources(50_000, 50_000, 50_000, 20_000),
            energy=EnergyBalance(produced=400, used=200),  # b2=0 -> margin@100 = 200
        )
        # 4. storage_blocked (b17 L1 costs 500k Metal / 1M Kry, storage=400k, prereqs b7=12 & r2=10 met)
        p_storage = _make_planet(
            104,
            "02:380:04",
            "Col-Storage",
            buildings={"b7": 12},
            resources=Resources(100_000, 100_000, 100_000, 100_000),
            storage=Resources(400_000, 400_000, 400_000, 400_000),
        )
        # 5. transport_inbound (needs b7 L6 = 19200M/9600K/6400S; short 19200M, inbound covers 20000M!)
        p_inbound = _make_planet(
            105,
            "02:380:05",
            "Col-Inbound",
            buildings={"b7": 5},
            resources=Resources(10_000, 0, 20_000, 20_000),
        )
        # 6. waiting_metal + affordable secondary step b1 L6 (costs 613 Pig, 197 Kry, 0 Metal) -> Step 5 gate True!
        p_wait_m = _make_planet(
            106,
            "02:380:06",
            "Col-WaitMetal",
            buildings={"b1": 5, "b2": 10, "b7": 5},
            resources=Resources(10_000, 0, 20_000, 20_000),
            energy=EnergyBalance(produced=300, used=250),  # net=50, b2=0% -> margin@100 = -30
            economy_rates={"resource2": 0},
        )
        # 7. waiting_pig_iron (needs b1 L6 = 613 Pig, 197 Kry; has 0 Pig, 1000 Kry)
        p_wait_p = _make_planet(
            107,
            "02:380:07",
            "Col-WaitPig",
            buildings={"b1": 5},
            resources=Resources(0, 10_000, 10_000, 10_000),
        )
        # 8. waiting_kryptonite (needs b1 L6 = 613 Pig, 197 Kry; has 10000 Pig, 0 Kry)
        p_wait_k = _make_planet(
            108,
            "02:380:08",
            "Col-WaitKry",
            buildings={"b1": 5},
            resources=Resources(10_000, 10_000, 0, 10_000),
        )
        # 9. waiting_spice (needs b7 L6 = 19200M, 9600K, 6400S; has 50k M, 50k K, 0 S)
        p_wait_s = _make_planet(
            109,
            "02:380:09",
            "Col-WaitSpice",
            buildings={"b7": 5},
            resources=Resources(50_000, 50_000, 50_000, 0),
        )
        # 10. idle_complete (all planned steps already met)
        p_done = _make_planet(
            110,
            "02:380:10",
            "Col-Done",
            buildings={"b1": 10},
            resources=Resources(50_000, 50_000, 50_000, 50_000),
        )
        # Non-normal raid planet must be excluded from total_colonies
        p_raid = _make_planet(111, "02:380:15", "RaidPlanet", planet_type="raid", buildings={"b1": 1})

        state = GameState(
            universe_id=31,
            user_id=1,
            nickname="DiagTest",
            timestamp=now_ts,
            planets=(
                p_build,
                p_ready,
                p_field,
                p_storage,
                p_inbound,
                p_wait_m,
                p_wait_p,
                p_wait_k,
                p_wait_s,
                p_done,
                p_raid,
            ),
            active_planet_id=101,
            researches={"r1": 4, "r2": 10},
            active_queues=(
                QueueJob(
                    category="building",
                    name="Pig iron mine (Level 6)",
                    building_id=1,
                    target_level=6,
                    origin_coords="02:380:01",
                    remaining_seconds=600,
                    finish_ts=now_ts + 600,
                ),
                QueueJob(
                    category="fleet",
                    name="Transport → 02:380:05",
                    detail="Outbound · 4 Small transport",
                    origin_coords="02:380:01",
                    remaining_seconds=300,
                    finish_ts=now_ts + 300,
                    cargo=Resources(0, 20_000, 0, 0),
                ),
            ),
            speedup_factor=5.0,
        )
        plan_res = {
            "steps": [
                # p_build: b1 L6 in flight, b3 L5 affordable (must NOT set idle_with_affordable since building!)
                {
                    "seq": 1,
                    "kind": "BUILD",
                    "planet_id": 101,
                    "planet_coords": "02:380:01",
                    "entity_key": "b1",
                    "entity_name": "Pig iron mine",
                    "target_level": 6,
                },
                {
                    "seq": 2,
                    "kind": "BUILD",
                    "planet_id": 101,
                    "planet_coords": "02:380:01",
                    "entity_key": "b3",
                    "entity_name": "Kryptonite mine",
                    "target_level": 5,
                },
                # p_ready: b1 L6 ready
                {
                    "seq": 3,
                    "kind": "BUILD",
                    "planet_id": 102,
                    "planet_coords": "02:380:02",
                    "entity_key": "b1",
                    "entity_name": "Pig iron mine",
                    "target_level": 6,
                },
                # p_field: b1 L11 field-blocked
                {
                    "seq": 4,
                    "kind": "BUILD",
                    "planet_id": 103,
                    "planet_coords": "02:380:03",
                    "entity_key": "b1",
                    "entity_name": "Pig iron mine",
                    "target_level": 11,
                },
                # p_storage: b17 L1 exceeds 400k storage
                {
                    "seq": 5,
                    "kind": "BUILD",
                    "planet_id": 104,
                    "planet_coords": "02:380:04",
                    "entity_key": "b17",
                    "entity_name": "Microsystem accelerator",
                    "target_level": 1,
                },
                # p_inbound: b7 L6 short of Metal, covered by inbound Transport
                {
                    "seq": 6,
                    "kind": "BUILD",
                    "planet_id": 105,
                    "planet_coords": "02:380:05",
                    "entity_key": "b7",
                    "entity_name": "Development center",
                    "target_level": 6,
                },
                # p_wait_m: top step b7 L6 waits on Metal, later step b1 L6 IS locally affordable!
                {
                    "seq": 7,
                    "kind": "BUILD",
                    "planet_id": 106,
                    "planet_coords": "02:380:06",
                    "entity_key": "b7",
                    "entity_name": "Development center",
                    "target_level": 6,
                },
                {
                    "seq": 8,
                    "kind": "BUILD",
                    "planet_id": 106,
                    "planet_coords": "02:380:06",
                    "entity_key": "b1",
                    "entity_name": "Pig iron mine",
                    "target_level": 6,
                },
                # p_wait_p: b1 L6 waits on Pig iron
                {
                    "seq": 9,
                    "kind": "BUILD",
                    "planet_id": 107,
                    "planet_coords": "02:380:07",
                    "entity_key": "b1",
                    "entity_name": "Pig iron mine",
                    "target_level": 6,
                },
                # p_wait_k: b1 L6 waits on Kryptonite
                {
                    "seq": 10,
                    "kind": "BUILD",
                    "planet_id": 108,
                    "planet_coords": "02:380:08",
                    "entity_key": "b1",
                    "entity_name": "Pig iron mine",
                    "target_level": 6,
                },
                # p_wait_s: b7 L6 waits on Spice
                {
                    "seq": 11,
                    "kind": "BUILD",
                    "planet_id": 109,
                    "planet_coords": "02:380:09",
                    "entity_key": "b7",
                    "entity_name": "Development center",
                    "target_level": 6,
                },
                # p_done: b1 L10 already built
                {
                    "seq": 12,
                    "kind": "BUILD",
                    "planet_id": 110,
                    "planet_coords": "02:380:10",
                    "entity_key": "b1",
                    "entity_name": "Pig iron mine",
                    "target_level": 10,
                },
            ]
        }

        cfg = StrategyConfig(allow_multi_planet=True)
        ai_acts, conflicts = ap.reconcile_live_ai_actions(state, plan_res, cfg)
        diag = ap.diagnose_ai_colony_idle_states(
            state,
            plan_res,
            ai_acts,
            cfg,
            field_conflicts=conflicts,
        )

        self.assertEqual(diag["total_colonies"], 10)
        self.assertEqual(diag["active_builders"], 1)
        self.assertEqual(diag["ready_not_started_count"], 1)
        self.assertEqual(diag["idle_with_affordable_count"], 1)
        self.assertEqual(sum(diag["counts"].values()), 10)
        for bucket in ap.AI_COLONY_IDLE_STATES:
            self.assertEqual(diag["counts"][bucket], 1, f"expected 1 colony in bucket {bucket!r}")

        by_pid = {row["planet_id"]: row for row in diag["colonies"]}
        self.assertNotIn(111, by_pid)
        self.assertEqual(by_pid[101]["state"], "building")
        self.assertFalse(by_pid[101]["idle_with_affordable_target_step"])
        self.assertEqual(by_pid[101]["b2_level"], 10)
        self.assertEqual(by_pid[101]["b2_pct"], 0)
        self.assertEqual(by_pid[101]["energy_net"], 150)
        self.assertEqual(by_pid[101]["b2_energy_margin_at_100"], 70)

        self.assertEqual(by_pid[102]["state"], "ready_not_started")
        self.assertFalse(by_pid[102]["idle_with_affordable_target_step"])
        self.assertEqual(by_pid[102]["b2_pct"], 100)
        self.assertEqual(by_pid[102]["energy_net"], 70)
        self.assertEqual(by_pid[102]["b2_energy_margin_at_100"], 70)

        self.assertEqual(by_pid[103]["state"], "field_conflict")
        self.assertFalse(by_pid[103]["idle_with_affordable_target_step"])
        self.assertEqual(by_pid[103]["b2_level"], 0)
        self.assertEqual(by_pid[103]["b2_energy_margin_at_100"], 200)

        self.assertEqual(by_pid[104]["state"], "storage_blocked")
        self.assertEqual(by_pid[105]["state"], "transport_inbound")

        self.assertEqual(by_pid[106]["state"], "waiting_metal")
        self.assertTrue(by_pid[106]["idle_with_affordable_target_step"])
        self.assertEqual(by_pid[106]["b2_energy_margin_at_100"], -30)

        self.assertEqual(by_pid[107]["state"], "waiting_pig_iron")
        self.assertFalse(by_pid[107]["idle_with_affordable_target_step"])
        self.assertEqual(by_pid[108]["state"], "waiting_kryptonite")
        self.assertEqual(by_pid[109]["state"], "waiting_spice")
        self.assertEqual(by_pid[110]["state"], "idle_complete")
        self.assertIsNone(by_pid[110]["next_label"])

        # Auto-reconcile path (ai_actions omitted) matches explicit path 1:1
        diag_auto = ap.diagnose_ai_colony_idle_states(state, plan_res, config=cfg)
        self.assertEqual(diag_auto["counts"], diag["counts"])
        self.assertEqual(diag_auto["idle_with_affordable_count"], diag["idle_with_affordable_count"])
        self.assertEqual(diag_auto["ready_not_started_count"], diag["ready_not_started_count"])

    def test_step6_gene_persistence_and_non_regressing_warm_start(self) -> None:
        p1 = _make_planet(
            101,
            "02:380:12",
            "Homeworld",
            buildings={
                "b1": 6,
                "b2": 3,
                "b3": 5,
                "b4": 3,
                "b5": 9,
                "b7": 3,
                "b8": 3,
                "b10": 1,
                "b11": 5,
                "b12": 5,
                "b13": 5,
                "b14": 5,
            },
            resources=Resources(30_000, 15_000, 15_000, 8_000),
        )
        p2 = _make_planet(
            102,
            "02:381:05",
            "Colony 1",
            buildings={
                "b1": 4,
                "b2": 2,
                "b3": 4,
                "b4": 2,
                "b5": 7,
                "b7": 2,
                "b10": 1,
                "b11": 5,
                "b12": 5,
                "b13": 5,
                "b14": 5,
            },
            resources=Resources(15_000, 8_000, 8_000, 4_000),
        )
        state = GameState(
            universe_id=31,
            user_id=1,
            nickname="WarmStartTest",
            timestamp=1_700_000_000.0,
            planets=(p1, p2),
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
                    {
                        "b1": 12,
                        "b2": 8,
                        "b3": 10,
                        "b4": 8,
                        "b5": 14,
                        "b7": 5,
                        "b8": 5,
                        "b10": 3,
                        "b11": 5,
                        "b12": 5,
                        "b13": 5,
                        "b14": 5,
                    },
                    is_central=True,
                    pays_for_research=True,
                ),
                _target_planet_entry(
                    2,
                    102,
                    "02:381:05",
                    "Colony 1",
                    {
                        "b1": 10,
                        "b2": 7,
                        "b3": 9,
                        "b4": 7,
                        "b5": 13,
                        "b7": 4,
                        "b10": 2,
                        "b11": 5,
                        "b12": 5,
                        "b13": 5,
                        "b14": 5,
                    },
                ),
            ],
            planned_research={"r1": 5, "r2": 6},
        )

        # 1. Default run (no warm_start_genes): always records search["best_parameters"], exact 3 baseline comparisons
        base_plan = ap.run_ai_planner(
            state,
            target,
            params={"reward_weeks": 4, "ga_generations": 2, "ga_population_size": 4, "ga_random_seed": 42},
        )
        search_base = base_plan["search"]
        self.assertIn("best_parameters", search_base)
        self.assertIsInstance(search_base["best_parameters"], dict)
        self.assertIn("rule", search_base["best_parameters"])
        self.assertFalse(search_base["warm_start_used"])
        self.assertIsNone(search_base["warm_start_genes"])
        self.assertEqual(len(search_base["strategy_comparisons"]), 3)

        base_only_no_ga = ap.run_ai_planner(
            state,
            target,
            params={"reward_weeks": 4, "ga_generations": 0},
        )
        best_baseline_reward = float(base_only_no_ga["search"]["best_reward"])

        # 2. Warm-start from out-of-range 8-float sequence -> clamped to PATH_GENE_RANGES & non-regressing!
        extreme_seq = [99.0, -5.0, 5.0, -2.0, 10.0, -10.0, 0.5, -0.5]
        warm_seq_plan = ap.run_ai_planner(
            state,
            target,
            params={
                "reward_weeks": 4,
                "ga_generations": 0,
                "warm_start_genes": extreme_seq,
            },
        )
        ws_search = warm_seq_plan["search"]
        self.assertTrue(ws_search["warm_start_used"])
        self.assertEqual(ws_search["warm_start_genes"]["focus"], 3.0)
        self.assertEqual(ws_search["warm_start_genes"]["critical_share"], 0.2)
        self.assertEqual(ws_search["warm_start_genes"]["patience"], 1.0)
        self.assertEqual(ws_search["warm_start_genes"]["speed_weight"], 0.0)
        self.assertEqual(ws_search["warm_start_genes"]["value_pig_iron"], 2.0)
        self.assertEqual(ws_search["warm_start_genes"]["value_metal"], -2.0)
        self.assertEqual(ws_search["warm_start_genes"]["value_kryptonite"], 0.5)
        self.assertEqual(ws_search["warm_start_genes"]["value_spice"], -0.5)
        self.assertEqual(len(ws_search["strategy_comparisons"]), 4)
        self.assertTrue(
            any(row["strategy"] == "warm-start tuned strategy" for row in ws_search["strategy_comparisons"])
        )
        # Non-regressing guarantee: never worse than the best baseline!
        self.assertGreaterEqual(ws_search["best_reward"], best_baseline_reward)

        # 3. Warm-start from gene dict (tuned_genes) with GA enabled -> never regresses below warm-start or baselines!
        warm_dict = search_base["tuned_genes"] or {
            "focus": 1.2,
            "critical_share": 0.85,
            "patience": 0.3,
            "speed_weight": 1.6,
            "value_pig_iron": 0.1,
            "value_metal": 0.4,
            "value_kryptonite": -0.2,
            "value_spice": 0.0,
        }
        warm_ga_plan = ap.run_ai_planner(
            state,
            target,
            params={
                "reward_weeks": 4,
                "ga_generations": 2,
                "ga_population_size": 4,
                "ga_random_seed": 99,
                "warm_start_genes": warm_dict,
            },
        )
        wga_search = warm_ga_plan["search"]
        self.assertTrue(wga_search["warm_start_used"])
        self.assertIn("best_parameters", wga_search)
        warm_comp = next(
            row for row in wga_search["strategy_comparisons"] if row["strategy"] == "warm-start tuned strategy"
        )
        self.assertGreaterEqual(
            wga_search["best_reward"], max(best_baseline_reward, float(warm_comp["discounted_reward"])) - 1e-2
        )

        # 4. Warm-start from `best_parameters` dict (bias -> log2 round-trip) & graceful ignore of cheapest rule
        bp_payload = {
            "rule": "payback",
            "values": "scarcity",
            "focus": 1.5,
            "critical_share": 0.8,
            "patience": 0.4,
            "speed_weight": 1.2,
            "bias": [2.0, 0.5, 4.0, 0.25],
        }
        warm_bp_plan = ap.run_ai_planner(
            state,
            target,
            params={"reward_weeks": 4, "ga_generations": 0, "warm_start_genes": bp_payload},
        )
        self.assertTrue(warm_bp_plan["search"]["warm_start_used"])
        self.assertEqual(warm_bp_plan["search"]["warm_start_genes"]["value_pig_iron"], 1.0)
        self.assertEqual(warm_bp_plan["search"]["warm_start_genes"]["value_metal"], -1.0)
        self.assertEqual(warm_bp_plan["search"]["warm_start_genes"]["value_kryptonite"], 2.0)
        self.assertEqual(warm_bp_plan["search"]["warm_start_genes"]["value_spice"], -2.0)

        cheapest_bp_plan = ap.run_ai_planner(
            state,
            target,
            params={"reward_weeks": 4, "ga_generations": 0, "warm_start_genes": {"rule": "cheapest"}},
        )
        self.assertFalse(cheapest_bp_plan["search"]["warm_start_used"])
        self.assertEqual(len(cheapest_bp_plan["search"]["strategy_comparisons"]), 3)

    def test_extract_live_in_progress_preserves_legacy_hostile_and_first_coord_semantics(self) -> None:
        """Verify F7: _extract_live_in_progress_state keeps legacy job.hostile-only & first-coord semantics."""
        p1 = _make_planet(201, "02:380:01", "Alpha")
        p2 = _make_planet(202, "02:380:02", "Beta")
        q_multi = QueueJob(
            category="fleet",
            name="Transport 02:380:01 → 02:380:02",
            detail="Outbound · enemy_bait",
            origin_coords="02:380:01",
            remaining_seconds=300.0,
            cargo=Resources(0, 5_000, 0, 0),
        )
        st = GameState(
            universe_id=31,
            user_id=1,
            nickname="Legacy",
            timestamp=1000.0,
            planets=(p1, p2),
            active_planet_id=201,
            active_queues=(q_multi,),
        )
        prob_planets = [
            {
                "index": 0,
                "planet_id": 201,
                "coords": "02:380:01",
                "name": "Alpha",
                "is_placeholder": False,
                "levels": {},
            },
            {
                "index": 1,
                "planet_id": 202,
                "coords": "02:380:02",
                "name": "Beta",
                "is_placeholder": False,
                "levels": {},
            },
        ]
        _, _, arrivals = ap._extract_live_in_progress_state(
            st,
            prob_planets,
            speedup=1.0,
            research_now={},
            base_hours_fn=lambda *a, **k: 1.0,
            lab_planet_index=0,
        )
        # Legacy path: job.hostile=False -> accepted, first coord (02:380:01 -> planet 0) matched
        self.assertEqual(len(arrivals), 1)
        self.assertEqual(arrivals[0]["planet_id"], 201)
        # Strict Step 1 helper skips text-hostile/enemy and uses destination (last) coord on normal jobs
        self.assertEqual(ap.compute_inbound_friendly_cargo_by_planet(st), {})

    def test_allow_demolition_triggers_demolish_strictly_when_planet_is_out_of_fields(self) -> None:
        from spaceinvasion.planner import graph as ag

        # Planet starts at 38/40 fields (2 free fields):
        # b1: 5 -> 9 (+4 fields needed -> 2 free fields used first, then 2 demolitions of b3 triggered @ 40/40!)
        # b3: 5 -> 0 planned in Config Optimizer, so 3 levels of b3 remain untouched because builds finish!
        p_38 = _make_planet(
            701,
            "02:380:01",
            "TightColony",
            fields_max=40,
            fields_used=38,
            buildings={"b1": 5, "b3": 5, "b5": 15, "b7": 5, "b8": 3, "b11": 2, "b12": 1, "b13": 1, "b14": 1},
            resources=Resources(500_000, 500_000, 500_000, 500_000),
            energy=EnergyBalance(produced=900, used=300),
        )
        state_38 = GameState(
            universe_id=31,
            user_id=1,
            nickname="OutOfFields",
            timestamp=1_700_000_000.0,
            planets=(p_38,),
            active_planet_id=701,
            researches={"r1": 4, "r2": 4},
            speedup_factor=20.0,
        )
        target_cfg = {
            "schema_version": 1,
            "generated_at": "2026-11-20T01:00:00+00:00",
            "meta_parameters": {
                "allow_demolition": True,
                "min_fleet_per_planet": {},
            },
            "inputs": {"universe_speedup_factor": 20.0},
            "planned_research_levels": {f"r{i}": (4 if i in (1, 2) else 0) for i in range(1, 16)},
            "summary": {"target_ship": "Imperial starbase", "ships_per_month": 100.0, "allow_demolition": True},
            "planets": [
                {
                    "slot": 1,
                    "slot_index": 1,
                    "planet_id": 701,
                    "coords": "02:380:01",
                    "name": "TightColony",
                    "is_placeholder": False,
                    "planet_status": "colonized",
                    "is_central_planet": True,
                    "pays_for_research": True,
                    "temperature": 20,
                    "temperature_c": 20,
                    "fields_max": 40,
                    "built_floors": dict(p_38.buildings),
                    "buildings": [
                        {"key": "b1", "built_level": 5, "target_level": 9},
                        {"key": "b3", "built_level": 5, "target_level": 0},
                        {"key": "b5", "built_level": 15, "target_level": 15},
                        {"key": "b7", "built_level": 5, "target_level": 5},
                        {"key": "b8", "built_level": 3, "target_level": 3},
                        {"key": "b11", "built_level": 2, "target_level": 2},
                        {"key": "b12", "built_level": 1, "target_level": 1},
                        {"key": "b13", "built_level": 1, "target_level": 1},
                        {"key": "b14", "built_level": 1, "target_level": 1},
                    ],
                }
            ],
        }
        plan = ap.run_ai_planner(
            state_38,
            target_cfg,
            params={"reward_weeks": 4, "ga_generations": 1, "ga_population_size": 4},
        )
        self.assertEqual(plan["summary"]["status"], "reached")
        self.assertTrue(plan["allow_demolition"])
        kinds = [s["kind"] for s in plan["steps"]]
        # Exactly 4 BUILD steps and ONLY 2 DEMOLISH steps (triggered strictly when 38 -> 40/40 fields)!
        self.assertEqual(kinds.count("BUILD"), 4)
        self.assertEqual(kinds.count("DEMOLISH"), 2)
        # First 2 steps MUST be BUILD (while 38/40 and 39/40 still had free fields!)
        self.assertEqual(kinds[:2], ["BUILD", "BUILD"])
        # Last step MUST be BUILD (no trailing demolitions after builds complete!)
        self.assertEqual(kinds[-1], "BUILD")

        # 1) Live reconcile at 38/40 fields (free fields remain): emits BUILD b1 L6, ZERO DEMOLISH_BUILDING!
        acts_38, confs_38 = ap.reconcile_live_ai_actions(state_38, plan, StrategyConfig())
        self.assertEqual(confs_38, [])
        self.assertEqual(len(acts_38), 1)
        self.assertEqual(acts_38[0].kind, "BUILD")
        self.assertEqual((acts_38[0].target_id, acts_38[0].target_level), (1, 6))

        # 2) Live reconcile at 40/40 fields (out of fields!): emits DEMOLISH_BUILDING b3 L5 -> L4!
        p_40 = _make_planet(
            701,
            "02:380:01",
            "TightColony",
            fields_max=40,
            fields_used=40,
            buildings={"b1": 7, "b3": 5, "b5": 15, "b7": 5, "b8": 3, "b11": 2, "b12": 1, "b13": 1, "b14": 1},
            resources=Resources(500_000, 500_000, 500_000, 500_000),
            energy=EnergyBalance(produced=900, used=350),
        )
        state_40 = GameState(
            universe_id=31,
            user_id=1,
            nickname="OutOfFields",
            timestamp=1_700_000_000.0,
            planets=(p_40,),
            active_planet_id=701,
            researches={"r1": 4, "r2": 4},
            speedup_factor=20.0,
        )
        acts_40, confs_40 = ap.reconcile_live_ai_actions(state_40, plan, StrategyConfig())
        self.assertEqual(confs_40, [])
        self.assertEqual(len(acts_40), 1)
        self.assertEqual(acts_40[0].kind, "DEMOLISH_BUILDING")
        self.assertEqual(acts_40[0].target_id, 3)
        self.assertEqual(acts_40[0].target_level, 4)
        self.assertTrue(acts_40[0].executable_now)

        # 3) Action Graph DAG renders DEMOLISH -> BUILD field-freeing edges cleanly
        dag = ag.build_ai_planner_graph(state_38, plan)
        dag_kinds = [n["kind"] for n in dag["nodes"]]
        self.assertIn("DEMOLISH", dag_kinds)
        self.assertTrue(any("frees 1 field" in str(e.get("label") or "") for e in dag["edges"]))


if __name__ == "__main__":
    unittest.main()
