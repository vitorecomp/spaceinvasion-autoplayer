"""Tier 2 E2E Boundary & Corner Tests for R1 (GA+NN Optimizer + History/Notebook Edge Cases): 21 tests."""

from __future__ import annotations

import inspect
import json
import math
import os
from pathlib import Path
import re
import sys
import tempfile
import time
import unittest

import commander
from e2e_tests.fixtures import (
    evaluate_sequence_score,
    make_building_offer,
    make_ga_nn_config,
    make_game_state,
    make_midgame_state,
    make_planet,
    make_research_offer,
    make_ship_offer,
)
from e2e_tests.harness import E2ETestHarness
from spaceinvasion.config import StrategyConfig, strategy_config_from_dict
from spaceinvasion.state import EnergyBalance, FleetAlert, GameState, Resources
from spaceinvasion.storage import GameDatabase
from spaceinvasion.strategy import (
    BLAST_FURNACE,
    FUSION_PLANT,
    PIG_IRON_MINE,
    RESEARCH_LAB,
    SOLAR_CELLS_ID,
    plan_actions,
)


class TestTier2R1(unittest.TestCase):
    def test_r1_a1_t7_invalid_whitespace_planner_mode_normalizes(self) -> None:
        probe = StrategyConfig()
        self.assertTrue(hasattr(probe, "planner_mode"), "StrategyConfig missing planner_mode")

        c_ws = strategy_config_from_dict({"planner_mode": " GA_NN "})
        self.assertEqual(c_ws.planner_mode, "ga_nn")

        for raw in ("invalid_algo", "", None, 123):
            cfg = strategy_config_from_dict({"planner_mode": raw})
            self.assertEqual(cfg.planner_mode, "priority", f"Unexpected mode for {raw!r}: {cfg.planner_mode}")

    def test_r1_a1_t8_invalid_planner_mode_via_http_update_config(self) -> None:
        with E2ETestHarness() as h:
            h.api.get_state(include_buildings=True)
            h.api.send_command({"command": "update_config", "config": {"planner_mode": "priority"}})
            import requests

            resp = requests.post(
                f"{h.base_url}/command",
                json={"command": "update_config", "config": {"planner_mode": "bogus"}},
                timeout=5.0,
            )
            self.assertIn(resp.status_code, (200, 400))
            body = resp.json()
            self.assertNotEqual(resp.status_code, 500)
            stored = h.api.send_command({"command": "config"})["config"]
            self.assertTrue(hasattr(StrategyConfig(), "planner_mode"), "StrategyConfig missing planner_mode")
            self.assertEqual(stored["planner_mode"], "priority")
            if resp.status_code == 200:
                self.assertEqual(body["config"]["planner_mode"], "priority")

    def test_r1_a1_t9_extreme_clamped_ga_hyperparameter_boundaries(self) -> None:
        cfg_probe = StrategyConfig()
        self.assertTrue(hasattr(cfg_probe, "planner_mode"), "StrategyConfig missing planner_mode")

        cfg = strategy_config_from_dict(
            {
                "planner_mode": "ga_nn",
                "ga_population_size": 0,
                "ga_generations": -5,
                "ga_horizon_steps": 0,
                "ga_seed": "123",
            }
        )
        self.assertEqual(cfg.planner_mode, "ga_nn")
        self.assertGreaterEqual(cfg.ga_population_size, 1)
        self.assertGreaterEqual(cfg.ga_generations, 1)
        self.assertGreaterEqual(cfg.ga_horizon_steps, 1)
        self.assertEqual(cfg.ga_seed, 123)

        state = make_game_state(planets=(make_planet(buildings={"b1": 2, "b5": 2}, auto_offers=True),))
        acts = plan_actions(state, config=cfg)
        self.assertIsInstance(acts, list)

    def test_r1_a1_t10_plan_per_request_planner_mode_does_not_mutate_config(self) -> None:
        with E2ETestHarness() as h:
            h.api.get_state(include_buildings=True)
            cfg0 = h.api.send_command({"command": "config"})["config"]
            self.assertIn("planner_mode", cfg0)
            self.assertEqual(cfg0["planner_mode"], "priority")

            pri_res = h.api.send_command({"command": "plan"})
            self.assertTrue(pri_res["ok"])
            sig_pri = [
                (a["kind"], a["target_id"], a["target_level"], a["planet_id"])
                for a in pri_res["actions"]
            ]

            res = h.api.send_command({"command": "plan", "planner_mode": "ga_nn"})
            self.assertTrue(res["ok"])
            self.assertEqual(res.get("planner_mode"), "ga_nn")
            self.assertGreater(len(res["actions"]), 0)
            sig_ga = [
                (a["kind"], a["target_id"], a["target_level"], a["planet_id"])
                for a in res["actions"]
            ]
            self.assertNotEqual(
                sig_ga,
                sig_pri,
                "Per-request /command plan planner_mode='ga_nn' matched 'priority' plan",
            )

            cfg_after = h.api.send_command({"command": "config"})["config"]
            self.assertEqual(cfg_after["planner_mode"], "priority")

    def test_r1_a1_t11_empty_empire_and_zero_resources_edge_cases(self) -> None:
        cfg = make_ga_nn_config(self, ga_seed=1)
        empty_state = make_game_state(planets=(), active_planet_id=None)
        self.assertEqual(plan_actions(empty_state, config=cfg), [])

        zero_p = make_planet(
            buildings={},
            resources=Resources(0, 0, 0, 0),
            energy=EnergyBalance(0, 0),
            building_offers=(make_building_offer(1, 0, can_build=False, href=None),),
        )
        zero_state = make_game_state(planets=(zero_p,))
        acts = plan_actions(zero_state, config=cfg)
        self.assertIsInstance(acts, list)
        self.assertFalse(any(a.executable_now and a.kind in ("BUILD", "RESEARCH", "BUILD_SHIP") for a in acts))

    def test_r1_a1_t12_p0_hostile_attack_override_preserved_in_ga_nn(self) -> None:
        p = make_planet(
            buildings={"b1": 5, "b2": 4, "b3": 4, "b5": 8},
            resources=Resources(10_000, 10_000, 5_000, 3_000),
            ships={"s1": 3},
            auto_offers=True,
        )
        p2 = make_planet(planet_id=2, coords="02:380:13")
        state = make_game_state(
            planets=(p, p2),
            active_planet_id=p.planet_id,
            fleet_alerts=(FleetAlert("ATTACK", True, "Incoming attack in 00:08:00"),),
        )

        for allow_fs in (False, True):
            cfg = make_ga_nn_config(self, allow_fleetsave=allow_fs, ga_seed=42)
            acts = plan_actions(state, config=cfg)
            self.assertGreaterEqual(len(acts), 2)
            self.assertTrue(all(a.priority == "P0" for a in acts[:2]))
            self.assertEqual({a.kind for a in acts[:2]}, {"ALERT_HOSTILE", "ESCAPE_FLIGHT"})
            alert = next(a for a in acts[:2] if a.kind == "ALERT_HOSTILE")
            self.assertFalse(alert.executable_now)

            esc = next((a for a in acts if a.kind == "ESCAPE_FLIGHT"), None)
            self.assertIsNotNone(esc)
            assert esc is not None
            self.assertEqual(esc.priority, "P0")
            self.assertEqual(esc.executable_now, allow_fs)

    def test_r1_a2_t7_asteroid_whitelist_excludes_non_asteroid_buildings(self) -> None:
        astro = make_planet(
            planet_id=777,
            name="Asteroid-1",
            coords="02:380:05",
            planet_type="asteroid",
            fields_max=30,
            buildings={"b7": 2},
            resources=Resources(500_000, 500_000, 500_000, 200_000),
            auto_offers=True,
        )
        state = make_game_state(planets=(astro,), researches={"r2": 10})
        cfg = make_ga_nn_config(self, ga_seed=9)
        acts = plan_actions(state, config=cfg)

        forbidden_on_astro = {1, 2, 3, 4, 5, 6, 8, 11, 12, 13, 14, 15, 16, 18, 20, 21}
        for act in acts:
            if act.planet_id == 777 and act.kind in ("BUILD", "WAIT_RESOURCES") and act.target_id is not None:
                self.assertNotIn(
                    act.target_id,
                    forbidden_on_astro,
                    f"Illegal building b{act.target_id} planned on Asteroid: {act}",
                )
        ev = evaluate_sequence_score(state, acts, config=cfg)
        self.assertTrue(ev.legal, msg=f"Asteroid violations: {ev.violations}")

    def test_r1_a2_t8_zero_free_fields_permits_only_0field_s9_research_ships(self) -> None:
        p = make_planet(
            planet_id=12476,
            temperature=85,
            fields_max=230,
            fields_used=230,
            buildings={"b1": 10, "b2": 8, "b3": 9, "b4": 7, "b5": 10, "b7": 5, "b8": 4, "b9": 3},
            energy=EnergyBalance(produced=400, used=450),
            resources=Resources(40_000, 40_000, 40_000, 20_000),
            building_offers=(make_building_offer(1, 10), make_building_offer(5, 10)),
            ship_offers=(make_ship_offer(SOLAR_CELLS_ID, owned=0),),
        )
        state = make_game_state(
            planets=(p,),
            researches={"r2": 2},
            research_offers=(make_research_offer("r2", cur_lvl=2),),
        )
        cfg = make_ga_nn_config(self, allow_research=True, allow_shipyard=True, ga_seed=14)
        acts = plan_actions(state, config=cfg)
        exec_builds = [a for a in acts if a.kind == "BUILD" and a.executable_now]
        self.assertEqual(exec_builds, [], f"Executable BUILD emitted with 0 free fields: {exec_builds}")
        self.assertTrue(
            any(a.kind in ("BUILD_SHIP", "RESEARCH") and a.executable_now for a in acts),
            f"Expected 0-field s9 BUILD_SHIP or RESEARCH when fields_remaining==0; got {acts}",
        )

    def test_r1_a2_t9_raid_colony_excluded_from_economy_builds(self) -> None:
        home = make_planet(planet_id=1, coords="02:380:12", buildings={"b1": 4, "b2": 3, "b3": 4, "b5": 7}, auto_offers=True)
        raid_col = make_planet(
            planet_id=999,
            name="RK Base",
            coords="02:385:04",
            planet_type="raid",
            buildings={"b9": 1},
            resources=Resources(500_000, 500_000, 500_000, 500_000),
            auto_offers=True,
        )
        state = make_game_state(planets=(home, raid_col), active_planet_id=1)
        cfg = make_ga_nn_config(self, allow_multi_planet=True, ga_seed=17)
        acts = plan_actions(state, config=cfg)

        forbidden_raid_bids = {1, 2, 3, 4, 5, 6, 8, 11, 12, 13, 14, 19, 20, 21}
        for act in acts:
            if act.planet_id == 999 and act.kind in ("BUILD", "WAIT_RESOURCES") and act.target_id is not None:
                self.assertNotIn(act.target_id, forbidden_raid_bids, f"Economy build on raid colony: {act}")

    def test_r1_a2_t10_merchant_mode_suppresses_b2_and_single_planet_isolation(self) -> None:
        home = make_planet(
            planet_id=1,
            coords="02:380:12",
            buildings={"b1": 5, "b2": 2, "b3": 5, "b4": 3, "b5": 8},
            resources=Resources(20_000, 20_000, 20_000, 10_000),
            auto_offers=True,
        )
        colony = make_planet(
            planet_id=2,
            coords="02:380:15",
            temperature=-120,
            buildings={"b1": 2, "b4": 2, "b5": 5},
            resources=Resources(500_000, 500_000, 500_000, 500_000),
            auto_offers=True,
        )
        state = make_game_state(planets=(home, colony), active_planet_id=1)
        cfg = make_ga_nn_config(self, merchant_mode=True, allow_multi_planet=False, ga_seed=25)
        acts = plan_actions(state, config=cfg)

        self.assertFalse(any(a.kind == "BUILD" and a.target_id == BLAST_FURNACE for a in acts))
        self.assertFalse(any(a.planet_id == 2 and a.kind in ("BUILD", "BUILD_SHIP") for a in acts))

    def test_r1_a2_t11_can_build_false_never_marked_executable_now(self) -> None:
        p = make_planet(
            buildings={"b1": 3, "b5": 6, "b7": 2, "b8": 1},
            resources=Resources(50_000, 50_000, 50_000, 50_000),
            building_offers=(
                make_building_offer(1, 3, can_build=False, status_text="In construction", href=None),
            ),
        )
        state = make_game_state(
            planets=(p,),
            researches={"r2": 1},
            research_offers=(
                make_research_offer("r2", cur_lvl=1, can_research=False, status_text="In research", active=True, href=None),
            ),
        )
        cfg = make_ga_nn_config(self, ga_seed=30)
        acts = plan_actions(state, config=cfg)
        for act in acts:
            if act.kind == "BUILD" and act.target_id == 1:
                self.assertFalse(act.executable_now)
                self.assertIsNone(act.action_href)
            if act.kind == "RESEARCH" and act.target_id == 2:
                self.assertFalse(act.executable_now)
                self.assertIsNone(act.action_href)

    def test_r1_a2_t12_missing_malformed_offers_and_zero_speedup_resilience(self) -> None:
        p1 = make_planet(
            planet_id=1,
            coords="02:380:12",
            storage=Resources(0, 0, 0, 0),
            buildings={"b1": 4, "b2": 3, "b3": 4, "b5": 6},
            building_offers=(),
        )
        p2 = make_planet(
            planet_id=2,
            coords="02:380:14",
            storage=Resources(0, 0, 0, 0),
            buildings={"b1": 3, "b3": 3, "b5": 5},
            building_offers=(),
        )
        state = make_game_state(planets=(p1, p2), active_planet_id=1, speedup_factor=0.0)
        cfg = make_ga_nn_config(self, allow_multi_planet=True, ga_seed=13)
        acts = plan_actions(state, config=cfg)
        self.assertIsInstance(acts, list)
        seen = set()
        for act in acts:
            key = (act.kind, act.planet_id, act.target_id, act.target_name)
            self.assertNotIn(key, seen, f"Duplicate action emitted: {key}")
            seen.add(key)

    def test_r1_a3_t7_multi_step_unlock_chain_horizon_8_vs_1(self) -> None:
        try:
            from spaceinvasion.optimizer import optimize_plan_actions
        except ImportError as exc:
            self.fail(f"R1 optimizer module missing: {exc}")

        p = make_planet(
            buildings={"b1": 6, "b2": 5, "b3": 6, "b4": 4, "b5": 9, "b7": 1, "b8": 0},
            resources=Resources(25_000, 25_000, 25_000, 15_000),
            researches={"r1": 0, "r2": 0},
            auto_offers=True,
        )
        state = make_game_state(planets=(p,), researches={"r1": 0, "r2": 0}, speedup_factor=10.0)
        cfg_h8 = make_ga_nn_config(self, ga_horizon_steps=8, ga_generations=14, ga_population_size=24, ga_seed=42)
        cfg_h1 = make_ga_nn_config(self, ga_horizon_steps=1, ga_generations=14, ga_population_size=24, ga_seed=42)

        acts_h8 = optimize_plan_actions(state, config=cfg_h8)
        acts_h1 = optimize_plan_actions(state, config=cfg_h1)
        self.assertGreater(len(acts_h8), 0)
        ev_h8 = evaluate_sequence_score(state, acts_h8, config=cfg_h8, horizon_hours=6.0)
        ev_h1 = evaluate_sequence_score(state, acts_h1, config=cfg_h1, horizon_hours=6.0)
        self.assertTrue(ev_h8.legal)
        self.assertGreaterEqual(ev_h8.total_score, ev_h1.total_score)

    def test_r1_a3_t8_combat_safety_and_pvp_guards_never_bypassed(self) -> None:
        p = make_planet(
            buildings={"b1": 5, "b2": 4, "b3": 4, "b5": 8, "b9": 3},
            ships={"s4": 2, "s1": 1},
            resources=Resources(5000, 5000, 5000, 2000),
            auto_offers=True,
        )
        pvp_target = {
            "coords": "02:380:06",
            "owner": "RichRival",
            "status_color": "light_red",
            "is_inactive": False,
            "is_spacia": False,
            "resources": {"pig_iron": 1_000_000, "metal": 1_000_000, "kryptonite": 1_000_000, "spice": 1_000_000},
            "fleet": {},
            "defense": {},
        }
        fortress_target = {
            "coords": "02:380:04",
            "owner": "IdleFort",
            "status_color": "yellow",
            "is_inactive": True,
            "is_spacia": False,
            "resources": {"pig_iron": 500_000, "metal": 500_000, "kryptonite": 500_000, "spice": 500_000},
            "fleet": {},
            "defense": {"d9": 50},
        }
        state = make_game_state(planets=(p,), raid_targets=(pvp_target, fortress_target))
        cfg = make_ga_nn_config(self, allow_raids=True, allow_pvp_in_az=False, combat_safety_margin=1.4, ga_seed=42)
        acts = plan_actions(state, config=cfg)

        exec_attacks = [a for a in acts if a.kind == "ATTACK" and a.executable_now]
        self.assertEqual(exec_attacks, [], f"Unsafe or disallowed PvP attack marked executable: {exec_attacks}")

    def test_r1_a3_t9_negative_pig_iron_blast_furnace_starvation_recovery(self) -> None:
        p = make_planet(
            buildings={"b1": 2, "b2": 8, "b3": 4, "b5": 9},
            resources=Resources(20, 5000, 5000, 2000),
            economy_rates={"resource1": 100, "resource2": 100, "resource3": 100, "resource4": 100},
            auto_offers=True,
        )
        state = make_game_state(planets=(p,))
        cfg = make_ga_nn_config(self, auto_throttle_blast_furnace=True, ga_seed=42)
        acts = plan_actions(state, config=cfg)
        exec_acts = [a for a in acts if a.executable_now]
        self.assertTrue(
            any(
                (a.kind == "THROTTLE_BUILDING" and a.target_id == BLAST_FURNACE and a.target_level == 0)
                or (a.kind == "BUILD" and a.target_id == PIG_IRON_MINE)
                for a in exec_acts
            ),
            f"Expected Blast furnace 0% pause or Pig iron mine recovery; got {acts}",
        )

    def test_r1_a3_t10_large_empire_performance_and_finite_stability(self) -> None:
        planets = tuple(
            make_planet(
                planet_id=500 + i,
                coords=f"02:38{i}:10",
                temperature=-40 + i * 30,
                buildings={"b1": 26, "b2": 22, "b3": 25, "b4": 24, "b5": 28, "b7": 12, "b8": 10, "b9": 10, "b17": 3},
                resources=Resources(1_000_000_000, 1_000_000_000, 1_000_000_000, 500_000_000),
                storage=Resources(2_000_000_000, 2_000_000_000, 2_000_000_000, 2_000_000_000),
                auto_offers=True,
            )
            for i in range(5)
        )
        state = make_game_state(planets=planets, researches={"r1": 12, "r2": 12}, speedup_factor=20.0)
        cfg = make_ga_nn_config(self, ga_population_size=24, ga_generations=12, ga_horizon_steps=6, ga_seed=42)
        t0 = time.monotonic()
        acts = plan_actions(state, config=cfg)
        elapsed = time.monotonic() - t0
        self.assertLess(elapsed, 5.0, f"Optimizer took {elapsed:.2f}s (> 5.0s interactive budget)")
        self.assertGreater(len(acts), 0)

    def test_r1_a3_t11_minimal_pop_gen_horizon_and_seed_none(self) -> None:
        state = make_game_state(planets=(make_planet(buildings={"b1": 3, "b5": 4}, auto_offers=True),))
        cfg = make_ga_nn_config(
            self,
            ga_population_size=2,
            ga_generations=1,
            ga_horizon_steps=1,
            ga_seed=None,
        )
        acts = plan_actions(state, config=cfg)
        self.assertIsInstance(acts, list)
        self.assertGreater(len(acts), 0)

    def test_r1_a3_t12_p0_ga_nn_live_safety_invariance_commander_tick(self) -> None:
        cfg = make_ga_nn_config(self, ga_seed=42, ga_generations=6, ga_population_size=12)
        with E2ETestHarness(config=cfg) as h:
            st, actual, actual_res, next_act, outcomes = commander.run_commander_tick(
                h.api,
                live=False,
                max_actions=1,
                config=cfg,
            )
            self.assertEqual(st.universe_id, 31)
            self.assertIsNotNone(actual)
            self.assertIsNotNone(actual_res)
            assert actual_res is not None
            self.assertTrue(actual_res["ok"])
            self.assertFalse(actual_res["executed"])
            self.assertTrue(actual_res["dry_run"])
            self.assertEqual(len(outcomes), 1)
            self.assertIsNotNone(next_act)

    # ------------------------------------------------------------------
    # Area R1.4 Boundary & Corner Cases (R1-A4-T6..T8)
    # ------------------------------------------------------------------

    def test_r1_a4_t6_identical_last_loot_differing_complete_history_drives_preference(self) -> None:
        try:
            from spaceinvasion.optimizer import optimize_plan_actions
        except ImportError as exc:
            self.fail(f"R1 optimizer module missing: {exc}")

        p = make_planet(
            buildings={"b1": 4, "b2": 3, "b3": 4, "b5": 8, "b9": 2},
            ships={"s4": 25, "s1": 8},
            resources=Resources(8000, 6000, 4000, 2000),
            auto_offers=True,
        )
        identical_last = {"pig_iron": 10_000, "metal": 0, "kryptonite": 0, "spice": 0}
        # Targets omit per-target historical metrics so complete attack/telemetry history must come solely from raid_stats
        t_consistent = {
            "coords": "02:380:04",
            "owner": "ReliableFarm",
            "planet_name": "Steady9",
            "status_color": "yellow",
            "is_inactive": True,
            "is_spacia": False,
            "resources": {"pig_iron": 15_000, "metal": 15_000, "kryptonite": 10_000, "spice": 5_000},
            "fleet": {},
            "defense": {},
        }
        t_fluke = {
            "coords": "02:380:05",
            "owner": "FlukeTrap",
            "planet_name": "OneLuckyWin",
            "status_color": "yellow",
            "is_inactive": True,
            "is_spacia": False,
            "resources": {"pig_iron": 15_000, "metal": 15_000, "kryptonite": 10_000, "spice": 5_000},
            "fleet": {},
            "defense": {},
        }
        raid_stats = {
            "overall": {"attempts": 20, "wins": 10, "losses": 10, "success_rate": 0.5},
            "per_target": {
                "02:380:04": {
                    "attempts": 10,
                    "wins": 9,
                    "losses": 1,
                    "success_rate": 0.9,
                    "total_loot_spice_eq": 320_000.0,
                    "avg_loot_spice_eq": 32_000.0,
                    "recency_weighted_loot_spice_eq": 30_000.0,
                    "last_outcome": "Dispatched attack; Victory",
                    "last_loot": identical_last,
                    "last_loot_spice_eq": 4_000.0,
                },
                "02:380:05": {
                    "attempts": 10,
                    "wins": 1,
                    "losses": 9,
                    "success_rate": 0.1,
                    "total_loot_spice_eq": 12_000.0,
                    "avg_loot_spice_eq": 1_200.0,
                    "recency_weighted_loot_spice_eq": 1_800.0,
                    "last_outcome": "Dispatched attack; Victory",
                    "last_loot": identical_last,
                    "last_loot_spice_eq": 4_000.0,
                },
            },
            "history": [],
            "telemetry_summary": {
                "rank_snapshots_count": 5,
                "planet_snapshots_count": 5,
                "action_history_count": 20,
                "spy_targets_count": 2,
            },
        }
        sig = inspect.signature(optimize_plan_actions)
        self.assertTrue(
            "raid_stats" in sig.parameters or hasattr(GameState, "raid_stats"),
            "R1.4 full-history raid_stats interface missing",
        )
        state_kw = {"planets": (p,), "raid_targets": (t_fluke, t_consistent)}
        if hasattr(GameState, "raid_stats"):
            state_kw["raid_stats"] = raid_stats
        state = make_game_state(**state_kw)
        cfg = make_ga_nn_config(self, allow_raids=True, ga_seed=42, ga_generations=12, ga_population_size=24)
        opt_kwargs = {"config": cfg}
        if "raid_stats" in sig.parameters:
            opt_kwargs["raid_stats"] = raid_stats
        acts = optimize_plan_actions(state, **opt_kwargs)
        atks = [a for a in acts if a.kind == "ATTACK" and a.executable_now]
        self.assertGreater(len(atks), 0, f"Expected ATTACK action; got {acts}")
        self.assertIn("02:380:04", atks[0].target_name, f"Last-only bias: expected 9/10 win 02:380:04 first, got {atks}")

    def test_r1_a4_t7_zero_history_cold_start_and_corrupt_history_json_resilience(self) -> None:
        try:
            from spaceinvasion.optimizer import optimize_plan_actions
        except ImportError as exc:
            self.fail(f"R1 optimizer module missing: {exc}")

        p = make_planet(
            buildings={"b1": 4, "b2": 3, "b3": 4, "b5": 8, "b9": 2},
            ships={"s4": 15, "s1": 5},
            resources=Resources(8000, 6000, 4000, 2000),
            auto_offers=True,
        )
        raid_target = {
            "coords": "02:380:04",
            "owner": "SleepyOne",
            "planet_name": "IdlePrime",
            "status_color": "yellow",
            "is_inactive": True,
            "is_spacia": False,
            "resources": {"pig_iron": 20_000, "metal": 15_000, "kryptonite": 10_000, "spice": 5_000},
            "fleet": {},
            "defense": {},
        }
        cfg = make_ga_nn_config(self, allow_raids=True, ga_seed=42, ga_generations=6, ga_population_size=12)
        sig = inspect.signature(optimize_plan_actions)
        empty_stats = {"overall": {"attempts": 0, "wins": 0, "losses": 0, "success_rate": 0.0}, "per_target": {}, "history": []}

        state_kw = {"planets": (p,), "raid_targets": (raid_target,)}
        if hasattr(GameState, "raid_stats"):
            state_kw["raid_stats"] = empty_stats
        state = make_game_state(**state_kw)

        kwargs_cold = {"config": cfg}
        if "raid_stats" in sig.parameters:
            kwargs_cold["raid_stats"] = empty_stats
        acts_cold = optimize_plan_actions(state, **kwargs_cold)
        self.assertGreater(len(acts_cold), 0)

        with tempfile.TemporaryDirectory(prefix="si_bad_hist_") as tmp:
            corrupt = Path(tmp) / "corrupt_attack_history.json"
            corrupt.write_text("{corrupt json!!!", encoding="utf-8")
            missing = Path(tmp) / "does_not_exist.json"
            for bad_path in (corrupt, missing):
                kw = {"config": cfg}
                if "attack_history_path" in sig.parameters:
                    kw["attack_history_path"] = bad_path
                acts_fb = optimize_plan_actions(state, **kw)
                self.assertGreater(len(acts_fb), 0)
                for a in acts_fb:
                    self.assertTrue(math.isfinite(float(getattr(a.cost, "pig_iron", 0))))

    def test_r1_a4_t8_notebook_cold_start_empty_telemetry_and_output_safety(self) -> None:
        repo_root = Path(__file__).resolve().parents[1]
        nb_path = repo_root / "notebooks" / "optimizer_experimentation.ipynb"
        self.assertTrue(nb_path.exists(), "Experimentation notebook notebooks/optimizer_experimentation.ipynb missing (F20)")

        raw = nb_path.read_text(encoding="utf-8")
        nb = json.loads(raw)
        code_cells = [c for c in nb.get("cells", []) if c.get("cell_type") == "code"]
        self.assertGreater(len(code_cells), 0)

        # Ensure zero raw sid tokens anywhere in saved notebook cell outputs
        for cell in nb.get("cells", []):
            out_txt = json.dumps(cell.get("outputs") or [])
            hits = re.findall(r"sid=([A-Za-z0-9_-]{6,})", out_txt, flags=re.IGNORECASE)
            leaked = [s for s in hits if s.upper() != "REDACTED"]
            self.assertEqual(leaked, [], f"Notebook cell output contains raw sid: {leaked}")

        orig_cwd = os.getcwd()
        orig_mpl = os.environ.get("MPLBACKEND")
        added_sys_path = False
        try:
            with tempfile.TemporaryDirectory(prefix="si_nb_cold_") as tmp:
                tmp_path = Path(tmp)
                (tmp_path / "data").mkdir()
                (tmp_path / "state").mkdir()
                empty_db = GameDatabase(tmp_path / "state" / "spaceinvasion.db", legacy_rank_json=None)
                empty_db.close()
                (tmp_path / "data" / "attack_history.json").write_text(
                    json.dumps({"overall": {"attempts": 0, "wins": 0, "losses": 0, "success_rate": 0.0}, "per_target": {}, "history": []}),
                    encoding="utf-8",
                )
                os.environ["MPLBACKEND"] = "Agg"
                if str(repo_root) not in sys.path:
                    sys.path.insert(0, str(repo_root))
                    added_sys_path = True
                os.chdir(tmp_path)
                ns: dict = {"__name__": "__main__"}
                for idx, cell in enumerate(code_cells):
                    raw_src = "".join(cell.get("source") or []) if isinstance(cell.get("source"), list) else str(cell.get("source") or "")
                    filtered = "\n".join(
                        ln for ln in raw_src.splitlines() if not ln.lstrip().startswith(("%", "!"))
                    )
                    if filtered.strip():
                        exec(compile(filtered, f"{nb_path}#cold_cell_{idx}", "exec"), ns, ns)
        finally:
            os.chdir(orig_cwd)
            if orig_mpl is None:
                os.environ.pop("MPLBACKEND", None)
            else:
                os.environ["MPLBACKEND"] = orig_mpl
            if added_sys_path and str(repo_root) in sys.path:
                sys.path.remove(str(repo_root))
