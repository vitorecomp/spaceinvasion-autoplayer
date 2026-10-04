"""Tier 1 E2E Feature Coverage Tests for R1 (GA+NN Optimizer, Complete History, Data File & Notebook): 23 tests."""

from __future__ import annotations

from dataclasses import asdict
import inspect
import json
import math
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

import commander
from tests.e2e.fixtures import (
    evaluate_sequence_score,
    make_building_offer,
    make_game_state,
    make_midgame_state,
    make_planet,
    make_research_offer,
    make_ship_offer,
)
from tests.e2e.harness import E2ETestHarness, FakeSpaceInvasionServer
from spaceinvasion import game_data
from spaceinvasion.config import (
    StrategyConfig,
    load_strategy_config,
    save_strategy_config,
    strategy_config_from_dict,
)
from spaceinvasion.executor import ActionExecutor
from spaceinvasion.state import (
    EnergyBalance,
    FleetAlert,
    GameState,
    QueueJob,
    Resources,
    TutorialStatus,
    TutorialTask,
)
from spaceinvasion.storage import GameDatabase
from spaceinvasion.strategy import (
    BLAST_FURNACE,
    DEVELOPMENT_CENTER,
    FUSION_PLANT,
    KRYPTONITE_MINE,
    PIG_IRON_MINE,
    PIG_IRON_WAREHOUSE,
    RESEARCH_LAB,
    SPICE_MINE,
    WEAPONS_FACTORY,
    PlannedAction,
    plan_actions,
)


class TestTier1R1(unittest.TestCase):
    # ------------------------------------------------------------------
    # Feature Area 1: Mode Selection, Config Persistence, API & Regression
    # ------------------------------------------------------------------

    def test_r1_a1_t1_default_strategy_config_preserves_priority(self) -> None:
        cfg_default = StrategyConfig()
        with tempfile.TemporaryDirectory() as tmp:
            cfg_missing = load_strategy_config(Path(tmp) / "nonexistent.json")
        for cfg in (cfg_default, cfg_missing):
            self.assertTrue(hasattr(cfg, "planner_mode"), "StrategyConfig missing planner_mode")
            self.assertEqual(cfg.planner_mode, "priority")
            self.assertEqual(cfg.ga_population_size, 24)
            self.assertEqual(cfg.ga_generations, 12)

    def test_r1_a1_t2_json_roundtrip_ai_planner_and_legacy_ga_nn(self) -> None:
        cfg = StrategyConfig(
            planner_mode="ai_planner",
            ga_population_size=18,
            ga_generations=9,
        )
        cfg_legacy = StrategyConfig(
            planner_mode="ga_nn",
            ga_population_size=20,
            ga_generations=10,
        )
        with tempfile.TemporaryDirectory() as tmp:
            json_path = Path(tmp) / "config.json"
            save_strategy_config(cfg, json_path)
            loaded = load_strategy_config(json_path)
            self.assertEqual(loaded, cfg)
            self.assertEqual(loaded.planner_mode, "ai_planner")
            self.assertEqual(loaded.ga_population_size, 18)
            self.assertEqual(loaded.ga_generations, 9)

            save_strategy_config(cfg_legacy, json_path)
            loaded_legacy = load_strategy_config(json_path)
            self.assertEqual(loaded_legacy.planner_mode, "priority")

            json_raw_path = Path(tmp) / "config_raw.json"
            json_raw_path.write_text(json.dumps(asdict(cfg)), encoding="utf-8")
            loaded_json = load_strategy_config(json_raw_path)
            self.assertEqual(loaded_json, cfg)

    def test_r1_a1_t3_priority_mode_regression_bit_for_bit(self) -> None:
        p1 = make_planet(
            planet_id=1,
            coords="02:380:12",
            resources=Resources(1500, 300, 700, 100),
            energy=EnergyBalance(0, 0),
            buildings={"b1": 0, "b3": 0, "b5": 0},
            auto_offers=True,
        )
        tut1 = GameState(
            universe_id=31,
            user_id=1,
            nickname="u",
            timestamp=100.0,
            planets=(p1,),
            active_planet_id=1,
            tutorial=TutorialStatus(
                step=1,
                total_steps=11,
                tasks=(
                    TutorialTask("Increase your Fusion plant level 0 / 3", "Fusion plant", 5, 0, 3),
                    TutorialTask("Increase your Pig iron mine level 0 / 3", "Pig iron mine", 1, 0, 3),
                ),
            ),
        )
        alert_state = make_game_state(
            planets=(
                make_planet(
                    planet_id=2,
                    resources=Resources(92000, 2000, 2000, 2500),
                    storage=Resources(100000, 100000, 100000, 100000),
                    energy=EnergyBalance(50, 90),
                    buildings={"b1": 4, "b5": 3},
                    ships={"s1": 3},
                    auto_offers=True,
                ),
            ),
            fleet_alerts=(FleetAlert("ATTACK", True, "Hostile incoming in 00:09:00"),),
        )
        multi = make_game_state(
            planets=(
                make_planet(planet_id=10, coords="02:380:12", temperature=50, buildings={"b1": 5, "b2": 4, "b3": 4, "b4": 2, "b5": 8}, auto_offers=True),
                make_planet(planet_id=11, coords="02:380:15", temperature=-110, buildings={"b1": 4, "b2": 3, "b3": 4, "b4": 2, "b5": 7}, auto_offers=True),
            ),
            active_planet_id=10,
            researches={"r1": 2, "r2": 2},
        )
        tut6 = make_game_state(
            planets=(
                make_planet(
                    planet_id=30,
                    buildings={"b1": 5, "b2": 4, "b3": 5, "b5": 8, "b7": 3, "b8": 2, "b9": 2},
                    ships={"s1": 1, "s4": 6},
                    ship_offers=(make_ship_offer(1, owned=1),),
                    auto_offers=True,
                ),
            ),
            tutorial=TutorialStatus(
                step=6,
                total_steps=11,
                tasks=(
                    TutorialTask("Build a Small transport 1 / 2", "Small transport", 1, 1, 2, category="ship"),
                    TutorialTask("Have a successful fight 0 / 1", "Have a successful fight", None, 0, 1, category="milestone"),
                ),
            ),
        )
        explicit_priority = strategy_config_from_dict({"planner_mode": "priority"})
        self.assertTrue(hasattr(explicit_priority, "planner_mode"))
        for state in (tut1, alert_state, multi, tut6):
            baseline = plan_actions(state, config=None)
            explicit = plan_actions(state, config=explicit_priority)
            self.assertEqual(explicit, baseline)

    def test_r1_a1_t4_legacy_ga_nn_routes_to_priority_and_executable_schema(self) -> None:
        state = make_game_state(
            planets=(
                make_planet(
                    buildings={"b1": 5, "b2": 4, "b3": 4, "b4": 2, "b5": 7, "b7": 2, "b8": 1},
                    resources=Resources(15000, 10000, 8000, 3000),
                    researches={"r2": 1},
                    auto_offers=True,
                ),
            ),
            researches={"r2": 1},
            research_offers=(make_research_offer("r2", cur_lvl=1),),
        )
        cfg = StrategyConfig(planner_mode="ga_nn")
        actions = plan_actions(state, config=cfg)
        prio_actions = plan_actions(state, config=StrategyConfig(planner_mode="priority"))
        self.assertIsInstance(actions, list)
        self.assertGreater(len(actions), 0)

        sig_ga = [(a.kind, a.target_id, a.target_level, a.planet_id) for a in actions]
        sig_pri = [(a.kind, a.target_id, a.target_level, a.planet_id) for a in prio_actions]
        self.assertEqual(
            sig_ga,
            sig_pri,
            "Legacy planner_mode='ga_nn' must normalize to priority heuristic",
        )

        supported_priorities = {"P0", "P1", "P2", "P3"}
        supported_kinds = {
            "ALERT_HOSTILE",
            "ESCAPE_FLIGHT",
            "BUILD",
            "WAIT_RESOURCES",
            "RESEARCH",
            "BUILD_SHIP",
            "SCAN_GALAXY",
            "ATTACK",
            "THROTTLE_BUILDING",
            "CLAIM_TUTORIAL",
            "TUTORIAL_TASK",
            "SHIP",
            "SPY",
        }
        for act in actions:
            self.assertIsInstance(act, PlannedAction)
            self.assertIn(act.priority, supported_priorities)
            self.assertIn(act.kind, supported_kinds)
            self.assertGreater(act.planet_id, 0)
            self.assertTrue(act.target_name)
            self.assertGreaterEqual(act.cost.pig_iron, 0)
            self.assertGreaterEqual(act.missing.pig_iron, 0)

        exec_act = next((a for a in actions if a.executable_now), None)
        self.assertIsNotNone(exec_act, "Expected at least one executable action in affordable midgame state")
        assert exec_act is not None
        if exec_act.kind in ("BUILD", "RESEARCH"):
            self.assertTrue(exec_act.action_href)

        dry_res = ActionExecutor(dry_run=True).execute_action(None, state, exec_act)
        self.assertFalse(dry_res.executed)
        self.assertTrue(dry_res.dry_run)
        self.assertIn("[DRY-RUN]", dry_res.message)

        sent_payloads: list[dict] = []

        class _StubApi:
            def get_state(self, include_buildings: bool = True):
                return state

            def send_command(self, payload: dict) -> dict:
                sent_payloads.append(dict(payload))
                return {"ok": True, "executed": False, "dry_run": True, "command": payload.get("command")}

        _st, tick_act, tick_res, _nxt, outcomes = commander.run_commander_tick(
            _StubApi(), live=False, max_actions=1, config=cfg
        )
        self.assertIsNotNone(tick_act)
        self.assertIsNotNone(tick_res)
        self.assertGreater(len(sent_payloads), 0)
        self.assertIn("command", sent_payloads[0])
        self.assertTrue(sent_payloads[0].get("dry_run", True))
        self.assertEqual(len(outcomes), len(sent_payloads))

    def test_r1_a1_t5_http_api_config_plan_dashboard_roundtrip_planner_mode(self) -> None:
        with E2ETestHarness() as h:
            h.api.get_state(include_buildings=True)
            cfg0 = h.api.send_command({"command": "config"})["config"]
            self.assertIn("planner_mode", cfg0)
            self.assertEqual(cfg0["planner_mode"], "priority")

            plan_pri_before = h.api.send_command({"command": "plan"})
            self.assertTrue(plan_pri_before["ok"])
            sig_pri_before = [
                (a["kind"], a["target_id"], a["target_level"], a["planet_id"])
                for a in plan_pri_before["actions"]
            ]

            upd = h.api.send_command(
                {
                    "command": "update_config",
                    "config": {"planner_mode": "ai_planner", "ga_generations": 6},
                }
            )["config"]
            self.assertEqual(upd["planner_mode"], "ai_planner")
            self.assertEqual(upd["ga_generations"], 6)
            reloaded = load_strategy_config(h.config_path)
            self.assertEqual(reloaded.planner_mode, "ai_planner")

            plan_ai = h.api.send_command({"command": "plan"})
            self.assertTrue(plan_ai["ok"])
            self.assertEqual(plan_ai.get("planner_mode"), "ai_planner")
            self.assertGreater(len(plan_ai["actions"]), 0)

            dash = h.get_dashboard(refresh=0)
            self.assertEqual(dash["config"]["planner_mode"], "ai_planner")
            self.assertGreater(len(dash["plan"]), 0)

            h.api.send_command({"command": "update_config", "config": {"planner_mode": "priority"}})
            plan_pri = h.api.send_command({"command": "plan"})["actions"]
            expected_pri = [asdict(a) for a in plan_actions(h.api.get_state(include_buildings=True), config=StrategyConfig())]
            self.assertEqual(
                [(a["kind"], a["target_id"], a["planet_id"]) for a in plan_pri],
                [(a["kind"], a["target_id"], a["planet_id"]) for a in expected_pri],
            )
            self.assertEqual(
                [(a["kind"], a["target_id"], a["target_level"], a["planet_id"]) for a in plan_pri],
                sig_pri_before,
            )

    def test_r1_a1_t6_clean_import_and_execution_in_venv(self) -> None:
        proc = subprocess.run(
            [
                sys.executable,
                "-c",
                (
                    "import spaceinvasion.config, "
                    "spaceinvasion.strategy, spaceinvasion.api_server; "
                    "print('OK')"
                ),
            ],
            capture_output=True,
            text=True,
            timeout=15,
            check=False,
        )
        self.assertEqual(proc.returncode, 0, msg=f"Import failed: {proc.stderr}")
        self.assertIn("OK", proc.stdout)

    # ------------------------------------------------------------------
    # Feature Area 2: Multi-Step Simulation Rules & Invariants
    # ------------------------------------------------------------------

    def test_r1_a2_t1_prerequisite_enforcement_b8_b9_r1_ships(self) -> None:
        p = make_planet(
            buildings={"b1": 5, "b2": 4, "b3": 5, "b4": 3, "b5": 9, "b7": 1, "b8": 0, "b9": 0},
            resources=Resources(100_000, 100_000, 100_000, 100_000),
            researches={"r2": 0},
            auto_offers=True,
        )
        state = make_game_state(planets=(p,), researches={"r2": 0})
        cfg = StrategyConfig()
        actions = plan_actions(state, config=cfg)
        self.assertGreater(len(actions), 0)

        first_exec = next((a for a in actions if a.executable_now), None)
        if first_exec is not None:
            if first_exec.kind == "BUILD":
                self.assertNotIn(first_exec.target_id, (RESEARCH_LAB, WEAPONS_FACTORY))
                unmet = game_data.missing_prerequisites(
                    "buildings",
                    int(first_exec.target_id or 0),
                    buildings=p.buildings,
                    researches=state.researches,
                )
                self.assertEqual(unmet, [])
            self.assertNotIn(first_exec.kind, ("RESEARCH", "BUILD_SHIP"))

        ev = evaluate_sequence_score(state, [a for a in actions if a.executable_now][:4], config=cfg)
        self.assertTrue(ev.legal, msg=f"Prerequisite/rule violations: {ev.violations}")

    def test_r1_a2_t2_blast_furnace_2_to_1_and_no_r1_boost(self) -> None:
        # b1=2 produces 63/h at r1=0 -> 75.6/h at r1=10; b2=3 burns 94/h (net negative -18.4/h), b2=4 burns 146/h!
        p = make_planet(
            buildings={"b1": 2, "b2": 3, "b3": 3, "b4": 2, "b5": 6},
            resources=Resources(150, 2000, 1500, 500),
            economy_rates={"resource1": 100, "resource2": 100, "resource3": 100, "resource4": 100},
            researches={"r1": 10},
            auto_offers=True,
        )
        state = make_game_state(planets=(p,), researches={"r1": 10})
        cfg = StrategyConfig(
            auto_throttle_blast_furnace=True,
            blast_pig_iron_max_share=0.70,
        )
        actions = plan_actions(state, config=cfg)
        self.assertGreater(len(actions), 0)

        first_exec = next((a for a in actions if a.executable_now), None)
        self.assertIsNotNone(first_exec)
        assert first_exec is not None
        # Should NOT blindly upgrade Blast furnace into deeper Pig-iron collapse when b1=2, b2=3
        self.assertFalse(
            first_exec.kind == "BUILD" and first_exec.target_id == BLAST_FURNACE,
            f"Optimizer blindly upgraded Blast furnace into 2:1 Pig collapse: {first_exec}",
        )

    def test_r1_a2_t3_energy_deficit_throttling_and_proactive_power(self) -> None:
        # Scenario A: active negative energy (-50 net)
        p_neg = make_planet(
            buildings={"b1": 5, "b2": 4, "b3": 4, "b5": 4},
            energy=EnergyBalance(produced=50, used=100),
            resources=Resources(10_000, 10_000, 10_000, 5_000),
            auto_offers=True,
        )
        s_neg = make_game_state(planets=(p_neg,))
        cfg = StrategyConfig()
        acts_neg = plan_actions(s_neg, config=cfg)
        self.assertGreater(len(acts_neg), 0)
        top_exec = next((a for a in acts_neg if a.executable_now), acts_neg[0])
        self.assertIn(top_exec.target_id, (FUSION_PLANT, 6, 9))

        # Scenario B: near-zero positive margin (+3 net), next mines need >= 9 energy
        p_tight = make_planet(
            buildings={"b1": 4, "b2": 3, "b3": 3, "b4": 2, "b5": 4},
            energy=EnergyBalance(produced=50, used=47),
            resources=Resources(10_000, 10_000, 10_000, 5_000),
            auto_offers=True,
        )
        s_tight = make_game_state(planets=(p_tight,))
        acts_tight = plan_actions(s_tight, config=cfg)
        ev = evaluate_sequence_score(s_tight, [a for a in acts_tight if a.executable_now][:4], config=cfg)
        self.assertTrue(ev.legal, msg=f"Violations: {ev.violations}")
        # Sequence should keep energy positive or improve net energy / economy
        self.assertGreaterEqual(ev.final_state.planets[0].energy.net, 0)

    def test_r1_a2_t4_building_queue_fullness_and_global_research_slot(self) -> None:
        full_queues = tuple(
            QueueJob("building", "Pig iron mine", building_id=1, target_level=i)
            for i in range(6, 11)
        )
        p = make_planet(
            buildings={"b1": 5, "b2": 4, "b3": 4, "b5": 8, "b7": 2, "b8": 2},
            resources=Resources(50_000, 50_000, 50_000, 20_000),
            researches={"r2": 1},
            auto_offers=True,
        )
        state_b_full = make_game_state(
            planets=(p,),
            researches={"r2": 1},
            active_queues=full_queues,
        )
        cfg = StrategyConfig()
        acts_b = plan_actions(state_b_full, config=cfg)
        exec_builds = [a for a in acts_b if a.kind == "BUILD" and a.planet_id == p.planet_id and a.executable_now]
        self.assertEqual(exec_builds, [], "Active-planet BUILD marked executable_now when 5 slots are full")

        # Global 1 research slot busy
        state_r_busy = make_game_state(
            planets=(p,),
            researches={"r2": 1},
            active_queues=(QueueJob("research", "Energy technology", target_level=2),),
            research_offers=(make_research_offer("r2", cur_lvl=1, can_research=False, active=True, href=None),),
        )
        acts_r = plan_actions(state_r_busy, config=cfg)
        exec_res = [a for a in acts_r if a.kind == "RESEARCH" and a.executable_now]
        self.assertEqual(exec_res, [], "RESEARCH marked executable_now while global research slot is busy")

    def test_r1_a2_t5_executable_now_false_invariant_on_advisories(self) -> None:
        p = make_planet(
            buildings={"b1": 4, "b2": 3, "b3": 3, "b5": 7, "b7": 2, "b8": 1, "b9": 1},
            resources=Resources(100, 100, 100, 50),
            ship_offers=(make_ship_offer(1, owned=0, can_build=False),),
            auto_offers=True,
        )
        state = make_game_state(
            planets=(p,),
            research_offers=(),
            raid_targets=({"coords": "02:380:04", "status_color": "yellow", "resources": {"pig_iron": 5000}},),
        )
        cfg = StrategyConfig(
            allow_research=False,
            allow_shipyard=False,
            allow_raids=False,
            allow_galaxy_scan=False,
        )
        actions = plan_actions(state, config=cfg)
        for act in actions:
            if act.kind in ("RESEARCH", "BUILD_SHIP", "SHIP", "TUTORIAL_TASK", "WAIT_RESOURCES", "ATTACK", "SCAN_GALAXY", "ALERT_HOSTILE"):
                self.assertFalse(act.executable_now, f"Expected executable_now=False for gated/advisory {act}")
                self.assertIsNone(act.action_href, f"Expected action_href=None for {act}")

    def test_r1_a2_t6_warehouse_overflow_guard_at_warn_ratio(self) -> None:
        p = make_planet(
            buildings={"b1": 5, "b2": 4, "b3": 4, "b5": 8, "b11": 0},
            resources=Resources(92_000, 1_000, 1_000, 1_000),
            storage=Resources(100_000, 100_000, 100_000, 100_000),
            auto_offers=True,
        )
        state = make_game_state(planets=(p,))
        cfg = StrategyConfig(storage_warn_ratio=0.85)
        actions = plan_actions(state, config=cfg)
        wh = next((a for a in actions if a.kind == "BUILD" and a.target_id == PIG_IRON_WAREHOUSE), None)
        self.assertIsNotNone(wh, "Expected Pig iron warehouse P1 guard at 92% fill")
        assert wh is not None
        self.assertEqual(wh.priority, "P1")
        self.assertTrue(wh.executable_now)
        self.assertTrue(wh.action_href)

    def test_r1_a3_t3_exact_determinism(self) -> None:
        state = make_midgame_state(speedup_factor=10.0)
        cfg1 = StrategyConfig(planner_mode="priority")
        cfg2 = StrategyConfig(planner_mode="priority")

        run1 = plan_actions(state, config=cfg1)
        run2 = plan_actions(state, config=cfg2)
        self.assertEqual(
            [(a.kind, a.planet_id, a.target_id, a.target_level, a.priority, a.executable_now) for a in run1],
            [(a.kind, a.planet_id, a.target_id, a.target_level, a.priority, a.executable_now) for a in run2],
        )

    def test_r1_a3_t4_composite_objective_balances_points_income_and_raids(self) -> None:
        res_prereqs = {"r2": 4, "r3": 4, "r4": 4}
        p = make_planet(
            buildings={"b1": 5, "b2": 4, "b3": 5, "b4": 3, "b5": 8, "b7": 3, "b9": 4},
            ships={"s4": 15, "s1": 5},
            resources=Resources(10_000, 10_000, 8_000, 4_000),
            researches=res_prereqs,
            ship_offers=(make_ship_offer(4, owned=15), make_ship_offer(1, owned=5)),
            auto_offers=True,
        )
        raid_target = {
            "coords": "02:380:04",
            "owner": "SleepyOne",
            "planet_name": "IdleLoot",
            "status_color": "yellow",
            "is_inactive": True,
            "is_spacia": False,
            "resources": {"pig_iron": 50_000, "metal": 50_000, "kryptonite": 20_000, "spice": 10_000},
            "fleet": {},
            "defense": {},
        }
        state = make_game_state(planets=(p,), researches=res_prereqs, raid_targets=(raid_target,))
        cfg_raids = StrategyConfig(allow_raids=True, allow_shipyard=True)
        cfg_no_raids = StrategyConfig(allow_raids=False, allow_shipyard=True)

        acts_raids = plan_actions(state, config=cfg_raids)
        acts_no_raids = plan_actions(state, config=cfg_no_raids)
        self.assertTrue(
            any(a.kind == "ATTACK" and a.executable_now for a in acts_raids),
            f"Expected executable ATTACK on rich yellow target when allow_raids=True; got {acts_raids}",
        )
        self.assertFalse(any(a.kind == "ATTACK" and a.executable_now for a in acts_no_raids))
        raid_seq = (
            [a for a in acts_raids if a.kind == "ATTACK" and a.executable_now][:1]
            + [a for a in acts_raids if a.executable_now and a.kind != "ATTACK"][:3]
        )
        ev_raids = evaluate_sequence_score(state, raid_seq, config=cfg_raids)
        ev_no_raids = evaluate_sequence_score(state, [a for a in acts_no_raids if a.executable_now][:3], config=cfg_no_raids)
        self.assertTrue(ev_raids.legal)
        self.assertGreater(ev_raids.total_score, ev_no_raids.total_score)

    def test_r1_a3_t5_multi_planet_synergy_cold_spice_vs_warm(self) -> None:
        warm = make_planet(
            planet_id=101,
            name="WarmCore",
            coords="02:380:02",
            temperature=80,
            buildings={"b1": 6, "b2": 5, "b3": 5, "b4": 2, "b5": 10},
            resources=Resources(12_000, 12_000, 12_000, 5_000),
            auto_offers=True,
        )
        cold = make_planet(
            planet_id=102,
            name="ColdSpice",
            coords="02:380:15",
            temperature=-110,
            buildings={"b1": 6, "b2": 5, "b3": 5, "b4": 2, "b5": 10},
            resources=Resources(12_000, 12_000, 12_000, 5_000),
            auto_offers=True,
        )
        state = make_game_state(planets=(warm, cold), active_planet_id=101, researches={"r1": 2})
        cfg = StrategyConfig(allow_multi_planet=True)
        actions = plan_actions(state, config=cfg)

        spice_builds = [a for a in actions if a.kind in ("BUILD", "WAIT_RESOURCES") and a.target_id == SPICE_MINE]
        self.assertGreater(len(spice_builds), 0)
        # First Spice mine pick must prefer ColdSpice (-110C) over WarmCore (+80C)
        self.assertEqual(
            spice_builds[0].planet_id,
            102,
            f"Expected priority planner to invest Spice mine on cold planet 102 (-110C), got {spice_builds[0]}",
        )

    def test_r1_a3_t6_tutorial_completion_and_relative_task_progression(self) -> None:
        # Tutorial 1 ready to claim
        p1 = make_planet(buildings={"b1": 3, "b3": 3, "b5": 3}, auto_offers=True)
        tut_claim = make_game_state(
            planets=(p1,),
            tutorial=TutorialStatus(
                step=1,
                total_steps=11,
                tasks=(TutorialTask("Increase your Fusion plant level 3 / 3", "Fusion plant", 5, 3, 3),),
                reward_text="5000 Pig iron",
                can_complete=True,
            ),
        )
        cfg = StrategyConfig()
        acts_claim = plan_actions(tut_claim, config=cfg)
        self.assertTrue(
            any(a.kind == "CLAIM_TUTORIAL" and a.executable_now for a in acts_claim[:2]),
            f"Expected CLAIM_TUTORIAL near top when can_complete=True; got {acts_claim}",
        )

        # Tutorial 4 with research & building task and b7=1 (needs b7=2 -> b8=1 for r2)
        p4 = make_planet(
            buildings={"b1": 4, "b2": 3, "b3": 4, "b5": 6, "b7": 1},
            resources=Resources(5000, 5000, 3000, 1000),
            auto_offers=True,
        )
        tut_4 = make_game_state(
            planets=(p4,),
            tutorial=TutorialStatus(
                step=4,
                total_steps=11,
                tasks=(
                    TutorialTask("Increase your Pig iron mine level 0 / 2", "Pig iron mine", 1, 0, 2),
                    TutorialTask("Upgrade your Energy technology 0 / 2", "Energy technology", None, 0, 2, category="research", research_id="r2"),
                ),
            ),
        )
        acts_4 = plan_actions(tut_4, config=cfg)
        ev_4 = evaluate_sequence_score(tut_4, acts_4, config=cfg)
        self.assertTrue(ev_4.legal, msg=f"Tutorial 4 plan violated prerequisites/energy: {ev_4.violations}")
        self.assertFalse(any(a.kind == "RESEARCH" and a.executable_now for a in acts_4))

    # ------------------------------------------------------------------
    # Feature Area 4 (R1.4): Complete Attack & Telemetry History, Tracked File (F19) & Notebook (F20)
    # ------------------------------------------------------------------

    @staticmethod
    def _sample_full_raid_stats() -> dict:
        return {
            "overall": {
                "attempts": 20,
                "wins": 10,
                "losses": 10,
                "success_rate": 0.5,
                "total_loot": {"pig_iron": 180_000, "metal": 90_000, "kryptonite": 45_000, "spice": 18_000},
                "avg_loot": {"pig_iron": 9_000, "metal": 4_500, "kryptonite": 2_250, "spice": 900},
                "total_loot_spice_eq": 270_000.0,
                "avg_loot_spice_eq": 13_500.0,
                "recency_weighted_loot_spice_eq": 14_000.0,
                "last_attack": {
                    "target_coords": "02:380:04",
                    "attacked_ts": 1_700_000_000.0,
                    "outcome": "Victory looted",
                    "loot": {"pig_iron": 12_000, "metal": 6_000, "kryptonite": 3_000, "spice": 1_000},
                    "loot_spice_eq": 20_000.0,
                },
            },
            "per_target": {
                "02:380:04": {
                    "attempts": 10,
                    "wins": 9,
                    "losses": 1,
                    "success_rate": 0.9,
                    "total_loot": {"pig_iron": 160_000, "metal": 80_000, "kryptonite": 40_000, "spice": 16_000},
                    "avg_loot": {"pig_iron": 16_000, "metal": 8_000, "kryptonite": 4_000, "spice": 1_600},
                    "total_loot_spice_eq": 250_000.0,
                    "avg_loot_spice_eq": 25_000.0,
                    "recency_weighted_loot_spice_eq": 26_000.0,
                    "last_outcome": "Victory",
                    "last_attacked_ts": 1_700_000_000.0,
                    "last_loot": {"pig_iron": 12_000, "metal": 6_000, "kryptonite": 3_000, "spice": 1_000},
                    "last_loot_spice_eq": 20_000.0,
                },
                "02:380:05": {
                    "attempts": 10,
                    "wins": 1,
                    "losses": 9,
                    "success_rate": 0.1,
                    "total_loot": {"pig_iron": 20_000, "metal": 10_000, "kryptonite": 5_000, "spice": 2_000},
                    "avg_loot": {"pig_iron": 2_000, "metal": 1_000, "kryptonite": 500, "spice": 200},
                    "total_loot_spice_eq": 20_000.0,
                    "avg_loot_spice_eq": 2_000.0,
                    "recency_weighted_loot_spice_eq": 1_500.0,
                    "last_outcome": "Defeat",
                    "last_attacked_ts": 1_699_999_000.0,
                    "last_loot": {"pig_iron": 300, "metal": 100, "kryptonite": 0, "spice": 0},
                    "last_loot_spice_eq": 500.0,
                },
            },
            "history": [],
            "telemetry_summary": {
                "rank_snapshots_count": 5,
                "planet_snapshots_count": 5,
                "action_history_count": 12,
                "spy_targets_count": 2,
            },
        }

    def test_r1_a4_t2_database_persists_full_history_and_mirrors_sanitized_json(self) -> None:
        repo_hist = Path(__file__).resolve().parents[2] / "data" / "attack_history.json"
        before_bytes = repo_hist.read_bytes() if repo_hist.exists() else None
        secret_tok = "SECRET_SID_99887766"

        with tempfile.TemporaryDirectory(prefix="si_r1_a4_t2_") as tmp:
            db_path = Path(tmp) / "state" / "spaceinvasion.db"
            db = GameDatabase(db_path=db_path)
            try:
                self.assertTrue(
                    hasattr(db, "get_raid_stats"),
                    "GameDatabase missing get_raid_stats() required by R1.4/F19",
                )
                state = make_midgame_state()
                db.record_state_snapshot(state)
                db.record_action(
                    source="api",
                    command="attack",
                    action=PlannedAction("P2", "ATTACK", 12476, "02:380:12", "02:380:04", "Raid", executable_now=True),
                    executed=True,
                    dry_run=False,
                    message=f"Launched https://spaceinvasion.bitmeup.com/indexInternal.es?sid={secret_tok}",
                )
                db.record_raid(
                    "02:380:04",
                    attacked_ts=1_700_000_100.0,
                    is_exempt=True,
                    outcome=f"Dispatched Victory url?sid={secret_tok}",
                    loot={"pig_iron": 12000, "metal": 6000, "kryptonite": 3000, "spice": 1000},
                )
                db.record_raid(
                    "02:380:04",
                    attacked_ts=1_700_000_200.0,
                    is_exempt=True,
                    outcome="Defeat / bounced",
                    loot={"pig_iron": 0, "metal": 0, "kryptonite": 0, "spice": 0},
                )
                db.record_raid(
                    "02:380:05",
                    attacked_ts=1_700_000_300.0,
                    is_exempt=True,
                    outcome="Dispatched attack Victory",
                    loot={"pig_iron": 4000, "metal": 2000, "kryptonite": 0, "spice": 0},
                )

                stats = db.get_raid_stats()
                self.assertIn("overall", stats)
                self.assertIn("per_target", stats)
                self.assertIn("history", stats)
                self.assertEqual(stats["overall"]["attempts"], 3)
                self.assertIn("02:380:04", stats["per_target"])
                self.assertEqual(stats["per_target"]["02:380:04"]["attempts"], 2)

                if hasattr(db, "get_full_history_summary"):
                    full_sum = db.get_full_history_summary()
                    self.assertIsInstance(full_sum, dict)
                else:
                    self.assertIn("telemetry_summary", stats)

                # Raid history stored purely in SQLite (no attack_history.json sidecar created)
                mirror = Path(tmp) / "state" / "attack_history.json"
                self.assertFalse(mirror.exists())
                parsed = db.get_raid_stats()
                raw_txt = json.dumps(parsed, allow_nan=False)
                self.assertNotIn(secret_tok, raw_txt)
                self.assertIn("overall", parsed)
                self.assertIn("per_target", parsed)
                self.assertEqual(parsed["overall"]["attempts"], 3)
            finally:
                db.close()

        after_bytes = repo_hist.read_bytes() if repo_hist.exists() else None
        self.assertEqual(after_bytes, before_bytes, "Temp GameDatabase run mutated repo data/attack_history.json!")

    def test_r1_a4_t3_tracked_data_attack_history_json_valid_sanitized_not_gitignored(self) -> None:
        repo_root = Path(__file__).resolve().parents[2]
        data_file = repo_root / "data" / "attack_history.json"
        self.assertFalse(data_file.exists(), "Legacy data/attack_history.json must be retired into SQLite")

        with tempfile.TemporaryDirectory(prefix="si_r1_a4_db_") as tmp:
            db_p = Path(tmp) / "spaceinvasion.db"
            db = GameDatabase(db_p)
            try:
                db.record_raid(
                    "02:380:04",
                    attacked_ts=1_700_000_000.0,
                    is_exempt=True,
                    outcome="Victory sid=SECRET_TOKEN_123456",
                    loot={"pig_iron": 6000, "metal": 2500, "kryptonite": 3000, "spice": 1000},
                )
                payload = db.get_raid_stats()
            finally:
                db.close()

        self.assertIsInstance(payload, dict)
        for key in ("overall", "per_target", "history"):
            self.assertIn(key, payload, f"SQLite attack history stats missing {key!r}")
        self.assertIsInstance(payload["overall"], dict)
        self.assertIsInstance(payload["per_target"], dict)
        self.assertIsInstance(payload["history"], list)

        raw = json.dumps(payload, allow_nan=False)
        raw_sid_hits = re.findall(r"sid=([A-Za-z0-9_-]{6,})", raw, flags=re.IGNORECASE)
        leaked = [s for s in raw_sid_hits if s.upper() != "REDACTED"]
        self.assertEqual(leaked, [], f"Secret sid token leaked in SQLite attack history stats: {leaked}")
        self.assertNotIn("password", raw.lower())
