"""Tier 2 E2E Boundary & Corner Tests for R1 (GA+NN Optimizer + History/Notebook Edge Cases): 21 tests."""

from __future__ import annotations

import math
import time
import unittest

import commander
from tests.e2e.fixtures import (
    evaluate_sequence_score,
    make_building_offer,
    make_game_state,
    make_planet,
    make_research_offer,
    make_ship_offer,
)
from tests.e2e.harness import E2ETestHarness
from spaceinvasion.core.config import (
    StrategyConfig,
    strategy_config_from_dict,
)
from spaceinvasion.core.model import (
    EnergyBalance,
    FleetAlert,
    GameState,
    Resources,
)
from spaceinvasion.planner.heuristics import (
    BLAST_FURNACE,
    PIG_IRON_MINE,
    SOLAR_CELLS_ID,
    plan_actions,
)


class TestTier2R1(unittest.TestCase):
    def test_r1_a1_t7_invalid_whitespace_planner_mode_normalizes(self) -> None:
        probe = StrategyConfig()
        self.assertTrue(hasattr(probe, "planner_mode"), "StrategyConfig missing planner_mode")

        c_ws = strategy_config_from_dict({"planner_mode": " GA_NN "})
        self.assertEqual(c_ws.planner_mode, "priority")

        c_ai = strategy_config_from_dict({"planner_mode": " AI_PLANNER "})
        self.assertEqual(c_ai.planner_mode, "ai_planner")

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
            }
        )
        self.assertEqual(cfg.planner_mode, "priority")
        self.assertGreaterEqual(cfg.ga_population_size, 1)
        self.assertGreaterEqual(cfg.ga_generations, 1)

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

            res = h.api.send_command({"command": "plan", "planner_mode": "ai_planner"})
            self.assertTrue(res["ok"])
            self.assertEqual(res.get("planner_mode"), "ai_planner")
            self.assertGreater(len(res["actions"]), 0)

            res_legacy = h.api.send_command({"command": "plan", "planner_mode": "ga_nn"})
            self.assertTrue(res_legacy["ok"])
            self.assertEqual(res_legacy.get("planner_mode"), "priority")

            cfg_after = h.api.send_command({"command": "config"})["config"]
            self.assertEqual(cfg_after["planner_mode"], "priority")

    def test_r1_a1_t11_empty_empire_and_zero_resources_edge_cases(self) -> None:
        cfg = StrategyConfig()
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

    def test_r1_a1_t12_p0_hostile_attack_override_preserved(self) -> None:
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
            cfg = StrategyConfig(allow_fleetsave=allow_fs)
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
        cfg = StrategyConfig()
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
        cfg = StrategyConfig(allow_research=True, allow_shipyard=True)
        acts = plan_actions(state, config=cfg)
        exec_builds = [a for a in acts if a.kind == "BUILD" and a.executable_now]
        self.assertEqual(exec_builds, [], f"Executable BUILD emitted with 0 free fields: {exec_builds}")
        self.assertTrue(
            any(a.kind in ("BUILD_SHIP", "RESEARCH") and a.executable_now for a in acts),
            f"Expected 0-field s9 BUILD_SHIP or RESEARCH when fields_remaining==0; got {acts}",
        )

    def test_r1_a2_t9_raid_colony_excluded_from_economy_builds(self) -> None:
        home = make_planet(
            planet_id=1, coords="02:380:12", buildings={"b1": 4, "b2": 3, "b3": 4, "b5": 7}, auto_offers=True
        )
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
        cfg = StrategyConfig(allow_multi_planet=True)
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
        cfg = StrategyConfig(merchant_mode=True, allow_multi_planet=False)
        acts = plan_actions(state, config=cfg)

        self.assertFalse(any(a.kind == "BUILD" and a.target_id == BLAST_FURNACE for a in acts))
        self.assertFalse(any(a.planet_id == 2 and a.kind in ("BUILD", "BUILD_SHIP") for a in acts))

    def test_r1_a2_t11_can_build_false_never_marked_executable_now(self) -> None:
        p = make_planet(
            buildings={"b1": 3, "b5": 6, "b7": 2, "b8": 1},
            resources=Resources(50_000, 50_000, 50_000, 50_000),
            building_offers=(make_building_offer(1, 3, can_build=False, status_text="In construction", href=None),),
        )
        state = make_game_state(
            planets=(p,),
            researches={"r2": 1},
            research_offers=(
                make_research_offer(
                    "r2", cur_lvl=1, can_research=False, status_text="In research", active=True, href=None
                ),
            ),
        )
        cfg = StrategyConfig()
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
        cfg = StrategyConfig(allow_multi_planet=True)
        acts = plan_actions(state, config=cfg)
        self.assertIsInstance(acts, list)
        seen = set()
        for act in acts:
            key = (act.kind, act.planet_id, act.target_id, act.target_name)
            self.assertNotIn(key, seen, f"Duplicate action emitted: {key}")
            seen.add(key)

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
        cfg = StrategyConfig(allow_raids=True, allow_pvp_in_az=False, combat_safety_margin=1.4)
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
        cfg = StrategyConfig(auto_throttle_blast_furnace=True)
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
        cfg = StrategyConfig()
        t0 = time.monotonic()
        acts = plan_actions(state, config=cfg)
        elapsed = time.monotonic() - t0
        self.assertLess(elapsed, 5.0, f"Planner took {elapsed:.2f}s (> 5.0s interactive budget)")
        self.assertGreater(len(acts), 0)

    def test_r1_a3_t11_minimal_ga_params(self) -> None:
        state = make_game_state(planets=(make_planet(buildings={"b1": 3, "b5": 4}, auto_offers=True),))
        cfg = StrategyConfig(
            ga_population_size=2,
            ga_generations=1,
        )
        acts = plan_actions(state, config=cfg)
        self.assertIsInstance(acts, list)
        self.assertGreater(len(acts), 0)

    def test_r1_a3_t12_p0_live_safety_invariance_commander_tick(self) -> None:
        cfg = StrategyConfig()
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
    # Area R1.4 Boundary & Corner Cases (R1-A4-T7)
    # ------------------------------------------------------------------

    def test_r1_a4_t7_zero_history_cold_start_resilience(self) -> None:
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
        cfg = StrategyConfig(allow_raids=True)
        empty_stats = {
            "overall": {"attempts": 0, "wins": 0, "losses": 0, "success_rate": 0.0},
            "per_target": {},
            "history": [],
        }

        state_kw = {"planets": (p,), "raid_targets": (raid_target,)}
        if hasattr(GameState, "raid_stats"):
            state_kw["raid_stats"] = empty_stats
        state = make_game_state(**state_kw)

        acts_cold = plan_actions(state, config=cfg, raid_stats=empty_stats)
        self.assertGreater(len(acts_cold), 0)
        for a in acts_cold:
            self.assertTrue(math.isfinite(float(getattr(a.cost, "pig_iron", 0))))
