"""Tier 5 E2E Adversarial & Hardening Verification Suite for Final Milestone M5."""

from __future__ import annotations

import contextlib
from dataclasses import replace
import json
import math
import os
from pathlib import Path
import re
import shutil
import tempfile
import unittest
from unittest.mock import MagicMock, patch

from bs4 import BeautifulSoup
import requests
from selenium.common.exceptions import NoSuchElementException, StaleElementReferenceException

from e2e_tests.fixtures import (
    evaluate_sequence_score,
    make_ga_nn_config,
    make_game_state,
    make_planet,
    make_research_offer,
    make_ship_offer,
)
from e2e_tests.harness import (
    E2ETestHarness,
    FakeClock,
    FakeDriver,
    FakeSpaceInvasionServer,
    run_ui_in_node,
)
from spaceinvasion.api_server import GameApiBridge
from spaceinvasion.client import GameBrowserClient, PageViewLimiter
from spaceinvasion.config import StrategyConfig
from spaceinvasion.executor import ActionExecutor
from spaceinvasion.fleet import GalaxySlot
from spaceinvasion.logging_utils import append_api_log
from spaceinvasion.optimizer import (
    SimAction,
    _resolve_reference_now_ts,
    optimize_plan_actions,
    run_optimizer,
    simulate_action_sequence,
)
from spaceinvasion.ranking import _format_duration, build_buildings_eta, compute_action_eta
from spaceinvasion.state import (
    EnergyBalance,
    GameState,
    PlanetState,
    ResearchOffer,
    Resources,
    UnitOffer,
)
from spaceinvasion.storage import GameDatabase
from spaceinvasion.strategy import PlannedAction, SPICE_MINE, plan_actions


class TestTier5Adversarial(unittest.TestCase):
    # ------------------------------------------------------------------
    # T5-01: ColdSpice multi-planet pop=24 seed=42 + 1x..50x speedup raids
    # ------------------------------------------------------------------
    def test_t5_01_cold_spice_pop24_seed42_and_multi_seed_speedup_raids(self) -> None:
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
        st_cs = make_game_state(planets=(warm, cold), active_planet_id=101, researches={"r1": 2})
        cfg_cs = StrategyConfig(
            planner_mode="ga_nn",
            allow_multi_planet=True,
            ga_population_size=24,
            ga_generations=12,
            ga_horizon_steps=6,
            ga_seed=42,
        )
        acts_cs = optimize_plan_actions(st_cs, config=cfg_cs, attack_history_path="")
        spice_builds = [a for a in acts_cs if a.kind in ("BUILD", "WAIT_RESOURCES") and a.target_id == SPICE_MINE]
        self.assertGreater(len(spice_builds), 0)
        self.assertEqual(spice_builds[0].planet_id, 102, f"Expected ColdSpice first, got {spice_builds[0]}")
        ev_cs = evaluate_sequence_score(st_cs, acts_cs, config=cfg_cs)
        self.assertTrue(ev_cs.legal, msg=f"Violations: {ev_cs.violations}")

        p_raid = make_planet(
            buildings={"b1": 4, "b2": 3, "b3": 3, "b4": 2, "b5": 8, "b7": 2, "b9": 2},
            ships={"s4": 25, "s1": 15, "s2": 4},
            resources=Resources(10_000, 6_000, 4_000, 1_500),
            auto_offers=True,
        )
        targets = (
            {
                "coords": "02:380:04",
                "planet_name": "InactiveGold",
                "owner": "Sleeper",
                "is_inactive": True,
                "is_spacia": False,
                "status_color": "yellow",
                "resources": {"pig_iron": 25_000, "metal": 12_000, "kryptonite": 10_000, "spice": 6_000},
                "fleet": {},
                "defense": {},
            },
            {
                "coords": "02:380:09",
                "planet_name": "SpaciaBase",
                "owner": "Spacia",
                "is_inactive": True,
                "is_spacia": True,
                "status_color": "yellow",
                "resources": {"pig_iron": 30_000, "metal": 15_000, "kryptonite": 12_000, "spice": 8_000},
                "fleet": {},
                "defense": {},
            },
        )
        for spd in (1.0, 10.0, 20.0, 50.0):
            st_r = make_game_state(planets=(p_raid,), raid_targets=targets, speedup_factor=spd)
            for style in ("fleeter", "hybrid", "miner"):
                for seed in (7, 19, 42, 77):
                    cfg_r = StrategyConfig(
                        planner_mode="ga_nn",
                        playstyle=style,
                        allow_raids=True,
                        ga_population_size=16,
                        ga_generations=8,
                        ga_horizon_steps=5,
                        ga_seed=seed,
                    )
                    acts_r = optimize_plan_actions(st_r, config=cfg_r, attack_history_path="")
                    has_atk = any(a.kind == "ATTACK" and a.executable_now for a in acts_r[:3])
                    self.assertTrue(
                        has_atk,
                        f"Missing executable ATTACK at spd={spd}, style={style}, seed={seed}: {acts_r[:3]}",
                    )

    # ------------------------------------------------------------------
    # T5-02: raid_stats={} fallback, omitted status_color, 0.5s ETA, ga_nn cooldowns
    # ------------------------------------------------------------------
    def test_t5_02_empty_raid_stats_fallback_omitted_status_color_eta_parity_and_ga_nn_cooldowns(self) -> None:
        self.assertEqual(_format_duration(0.5), "1s")
        self.assertEqual(_format_duration(0.25), "1s")

        p = make_planet(
            planet_id=12476,
            buildings={"b1": 4, "b2": 3, "b3": 4, "b4": 2, "b5": 8, "b6": 1, "b9": 2},
            ships={"s1": 10, "s4": 25},
            resources=Resources(20_000, 15_000, 10_000, 5_000),
            economy_rates={"resource1": 100, "resource2": 100, "resource3": 100, "resource4": 100, "resource5": 100},
            auto_offers=True,
        )
        omitted_active = {
            "coords": "02:380:07",
            "owner": "ActivePlayer",
            "is_inactive": False,
            "is_spacia": False,
            "resources": {"pig_iron": 60_000, "metal": 40_000, "kryptonite": 20_000, "spice": 10_000},
            "fleet": {},
            "defense": {},
        }
        st_active = GameState(
            universe_id=31,
            user_id=103603,
            nickname="u",
            timestamp=1_700_000_000.0,
            planets=(p,),
            active_planet_id=12476,
            raid_targets=(omitted_active,),
            raid_stats={},
        )
        for mode in ("priority", "ga_nn"):
            cfg_m = StrategyConfig(planner_mode=mode, allow_raids=True, allow_pvp_in_az=False, ga_seed=42, ga_generations=4, ga_population_size=8)
            acts = plan_actions(st_active, config=cfg_m)
            self.assertFalse(
                any(a.kind == "ATTACK" and a.executable_now for a in acts),
                f"Mode {mode} attacked active target with omitted status_color & is_inactive=False",
            )

        # state.raid_stats == {} does NOT suppress Layer 4 in plan_actions; explicit raid_stats={} DOES
        cfg_ga = StrategyConfig(planner_mode="ga_nn", ga_seed=42)
        with patch("spaceinvasion.optimizer.optimize_plan_actions", return_value=[]) as mocked_opt:
            plan_actions(
                st_active,
                config=cfg_ga,
                now_ts=1_700_000_100.0,
                scanned_systems={"02:380": 1_700_000_050.0},
                throttle_cooldowns={12476: 1_700_000_080.0},
            )
            mocked_opt.assert_called_once()
            self.assertNotIn("raid_stats", mocked_opt.call_args.kwargs)
            self.assertEqual(mocked_opt.call_args.kwargs.get("now_ts"), 1_700_000_100.0)
            self.assertEqual(mocked_opt.call_args.kwargs.get("scanned_systems"), {"02:380": 1_700_000_050.0})
            self.assertEqual(mocked_opt.call_args.kwargs.get("throttle_cooldowns"), {12476: 1_700_000_080.0})

            supp: dict = {}
            plan_actions(st_active, config=cfg_ga, raid_stats=supp)
            self.assertIs(mocked_opt.call_args.kwargs.get("raid_stats"), supp)

        # Active throttle cooldown + fresh galaxy scan forwarded into optimizer suppress spurious SCAN_GALAXY / throttle oscillation
        acts_cd = optimize_plan_actions(
            st_active,
            config=StrategyConfig(
                planner_mode="ga_nn",
                allow_galaxy_scan=True,
                auto_throttle_blast_furnace=True,
                ga_seed=42,
                ga_generations=4,
                ga_population_size=8,
            ),
            attack_history_path="",
            now_ts=1_700_000_100.0,
            scanned_systems={"02:380": 1_700_000_050.0},
            throttle_cooldowns={12476: 1_700_000_080.0},
        )
        self.assertFalse(any(a.kind == "SCAN_GALAXY" for a in acts_cd))
        self.assertFalse(any(a.kind == "THROTTLE_BUILDING" for a in acts_cd))

        # Exact 0.5s parity on build_buildings_eta & compute_action_eta (b6 L1 @ b7=7, b17=5, speedup=1.0 -> "1s")
        # and 0.5s wait_eta (rate=7200/hr = 2/s, missing=1 Pig iron -> 0.5s -> "1s")
        p_eta = make_planet(
            planet_id=1,
            buildings={"b1": 5, "b2": 2, "b5": 8, "b7": 7, "b17": 5},
            resources=Resources(0, 0, 0, 0),
            energy=EnergyBalance(500, 100),
            economy_rates={"resource1": 100, "resource2": 100, "resource3": 100, "resource4": 100, "resource5": 100},
            auto_offers=True,
        )
        st_eta = make_game_state(planets=(p_eta,), speedup_factor=1.0)
        bld_rows = build_buildings_eta(st_eta)["buildings"]
        b6_row = next(r for r in bld_rows if r["building_id"] == 6)
        eta_b6 = compute_action_eta(st_eta, {"kind": "BUILD", "planet_id": 1, "target_id": 6, "target_level": 1})
        self.assertEqual(b6_row["build_time_text"], "1s")
        self.assertEqual(eta_b6["duration_text"], "1s")

        # Pig iron rate at b1=5, b2=2, speedup=1.0 -> scale speedup so net pig_iron_per_hr ~ 7200/hr (missing=1 => ~0.5s wait)
        from spaceinvasion.ranking import estimate_planet_hourly_economy

        pig_hr = float(estimate_planet_hourly_economy(st_eta, p_eta)["pig_iron_per_hr"])
        spd_half = 7200.0 / max(1.0, pig_hr)
        st_wait_half = make_game_state(planets=(p_eta,), speedup_factor=spd_half)
        eta_w = compute_action_eta(
            st_wait_half,
            {"kind": "BUILD", "planet_id": 1, "target_id": 1, "target_level": 6, "missing": {"pig_iron": 1}},
        )
        self.assertAlmostEqual(float(eta_w["wait_seconds"]), 0.5, delta=0.05)
        self.assertEqual(eta_w["wait_eta"], "1m")

    # ------------------------------------------------------------------
    # T5-03: 0-loot & sub-unit SpEq phantom raids, 2P+1T depletion, NaN knobs
    # ------------------------------------------------------------------
    def test_t5_03_zero_loot_subunit_speq_phantom_raids_dual_planet_depletion_and_nan_knobs(self) -> None:
        p1 = make_planet(
            planet_id=1,
            name="P1",
            coords="01:010:01",
            buildings={"b1": 4, "b2": 3, "b3": 4, "b4": 2, "b5": 8, "b9": 2},
            ships={"s1": 10, "s4": 25},
            resources=Resources(10_000, 8_000, 5_000, 2_000),
            auto_offers=True,
        )
        p2 = make_planet(
            planet_id=2,
            name="P2",
            coords="01:010:02",
            buildings={"b1": 4, "b2": 3, "b3": 4, "b4": 2, "b5": 8, "b9": 2},
            ships={"s1": 10, "s4": 25},
            resources=Resources(10_000, 8_000, 5_000, 2_000),
            auto_offers=True,
        )
        # 1. Zero-loot target blocked even with min_loot=0
        zero_t = {
            "coords": "01:010:05",
            "owner": "EmptyTarget",
            "is_inactive": True,
            "status_color": "yellow",
            "resources": {"pig_iron": 0, "metal": 0, "kryptonite": 0, "spice": 0},
            "fleet": {},
            "defense": {},
        }
        st_z = make_game_state(planets=(p1,), raid_targets=(zero_t,))
        sim_z = simulate_action_sequence(
            st_z,
            [SimAction("ATTACK", 1, target_coords="01:010:05")],
            config=StrategyConfig(allow_raids=True, raid_min_loot_spice_eq=0.0),
            attack_history_path="",
        )
        self.assertEqual(sim_z.valid_steps, 0)
        self.assertEqual(sim_z.final_metrics.simulated_raid_loot_spice_eq, 0.0)

        # 2. Dual-planet single target honors depletion
        one_t = {
            "coords": "01:010:06",
            "owner": "SingleLoot",
            "is_inactive": True,
            "status_color": "yellow",
            "resources": {"pig_iron": 6_000, "metal": 4_000, "kryptonite": 2_000, "spice": 1_000},
            "fleet": {},
            "defense": {},
        }
        st_2p = make_game_state(planets=(p1, p2), active_planet_id=1, raid_targets=(one_t,))
        acts_2p = optimize_plan_actions(
            st_2p,
            config=StrategyConfig(
                planner_mode="ga_nn",
                allow_raids=True,
                allow_multi_planet=True,
                raid_min_loot_spice_eq=2500.0,
                ga_seed=42,
                ga_generations=4,
                ga_population_size=8,
            ),
            attack_history_path="",
        )
        self.assertLessEqual(
            len([a for a in acts_2p if a.kind == "ATTACK" and "01:010:06" in a.target_name and a.executable_now]),
            1,
        )

        # 3. NaN/inf knobs & 365d reference timestamp cutoff
        nan_sum = run_optimizer(
            st_z,
            config=StrategyConfig(planner_mode="ga_nn", ga_seed=7, ga_generations=2, ga_population_size=6),
            storage_warn_ratio=float("nan"),
            mutation_rate=float("inf"),
            nn_weight=float("nan"),
            attack_history_path="",
        )
        self.assertTrue(math.isfinite(nan_sum.baseline_fitness))
        self.assertTrue(math.isfinite(nan_sum.best_fitness))
        self.assertEqual(
            _resolve_reference_now_ts(GameState(31, 1, "u", 0.0, (p1,)), {"history": [{"attacked_ts": 1_000_000.0}]}),
            1_000_000.0,
        )

    # ------------------------------------------------------------------
    # T5-04: API prereq bypass block, sid%3D logs, executor bounce, :memory: DB
    # ------------------------------------------------------------------
    def test_t5_04_api_prereq_bypass_stale_research_sid_pct3d_executor_bounce_and_memory_db(self) -> None:
        # 1. In-memory DB isolation (no disk files, no ResourceWarning)
        with tempfile.TemporaryDirectory(prefix="si_t5_mem_") as tmp:
            cwd_isolated = Path(tmp) / "cwd_clean"
            cwd_isolated.mkdir()
            old_cwd = Path.cwd()
            try:
                os.chdir(cwd_isolated)
                for mem_uri in (":memory:", "file::memory:?cache=shared"):
                    db = GameDatabase(mem_uri)
                    try:
                        self.assertTrue(db._is_memory)
                        self.assertIsNone(db.attack_history_path)
                        db.record_raid(
                            "2:380:4",
                            attacked_ts=float("nan"),
                            is_exempt=False,
                            outcome="Victory",
                            loot={"pig_iron": 1e400, "metal": 100, "kryptonite": 50, "spice": 25},
                        )
                        stats = db.get_raid_stats()
                        json.dumps(stats, allow_nan=False)
                        self.assertIn("02:380:04", stats["per_target"])
                    finally:
                        db.close()
                self.assertEqual(list(cwd_isolated.iterdir()), [])
            finally:
                os.chdir(old_cwd)

            # 2. Live API server prereq check + sid%3D log redaction + stale research cleanup
            srv = FakeSpaceInvasionServer(
                planets={
                    12476: {
                        "planet_id": 12476,
                        "name": "Homeworld",
                        "coords": "02:380:12",
                        "planet_type": "normal",
                        "temperature": 31,
                        "fields_max": 230,
                        "resources": [100_000, 100_000, 100_000, 100_000],
                        "storage": [200_000, 200_000, 200_000, 200_000],
                        "energy": {"produced": 400, "used": 100},
                        "buildings": {"b1": 4, "b5": 6, "b8": 2, "b9": 1},
                        "ships": {},
                        "defense": {},
                        "economy_rates": {},
                        "building_queue": [],
                    }
                },
                researches={"r2": 1},
            )
            with E2ETestHarness(server=srv, config=StrategyConfig()) as h:
                locked_r = h.api.send_command({"command": "research", "research_id": 11, "dry_run": False})
                self.assertFalse(locked_r.get("executed", False))

                locked_s = h.api.send_command({"command": "build_ship", "ship_id": 6, "quantity": 1, "dry_run": False})
                self.assertFalse(locked_s.get("executed", False))

                secret_enc = "SECRET_T5_ENC_TOKEN_9911"
                resp = requests.post(
                    f"{h.base_url}/command?sid%3D{secret_enc}",
                    json={"command": "plan", "note": f"url?sid%3D{secret_enc}"},
                    timeout=5.0,
                )
                self.assertEqual(resp.status_code, 200)
                self.assertNotIn(secret_enc, resp.text)
                for lf in h.log_dir.glob("game_api.*"):
                    self.assertNotIn(secret_enc, lf.read_text(encoding="utf-8"))

            # 3. _merge_cached_offers clears stale active=True + executor bounce + German 'Angriff'
            p0 = make_planet(planet_id=1, buildings={"b8": 2, "b9": 2})
            prev_st = GameState(
                31,
                1,
                "u",
                100.0,
                (p0,),
                active_planet_id=1,
                research_offers=(ResearchOffer(2, "r2", "Energy technology", 0, 1, cost=Resources(), status_text="Active", can_research=False, active=True),),
            )
            cur_st = GameState(31, 1, "u", 110.0, (p0,), active_planet_id=1, research_offers=(), active_queues=())
            bridge = GameApiBridge(
                client_factory=lambda _port: MagicMock(),
                config_path=Path(tmp) / "c.yaml",
                db_path=Path(tmp) / "bridge.db",
                log_dir=Path(tmp) / "logs",
            )
            try:
                bridge._last_state = prev_st
                merged = bridge._merge_cached_offers(cur_st)
                self.assertFalse(merged.research_offers[0].active)
            finally:
                if bridge._db is not None:
                    bridge._db.close()

            html_sel = (
                '<select name="planet">'
                '<option value="indexInternal.es?newPlanet=1" selected="selected">Home</option>'
                '<option value="indexInternal.es?newPlanet=2">Colony</option>'
                "</select>"
            )
            client_b = MagicMock()
            client_b.driver = MagicMock()
            client_b._wait_frame_ready.return_value = html_sel
            ex = ActionExecutor(dry_run=False, action_delay_range=(0.0, 0.0))
            with patch("spaceinvasion.executor.Select"):
                switched, _ = ex._select_planet_if_needed(client_b, 2, html=html_sel, require_confirm=True)
            self.assertFalse(switched)

            soup = BeautifulSoup(
                '<form id="f"><label><input type="radio" name="mission" value="atk">Angriff</label>'
                '<label><input type="radio" name="mission" value="trn">Transportieren</label></form>',
                "html.parser",
            )
            r_atk = MagicMock()
            r_atk.get_attribute.side_effect = lambda k: {"value": "atk", "name": "mission", "id": ""}.get(k, "")
            r_trn = MagicMock()
            r_trn.get_attribute.side_effect = lambda k: {"value": "trn", "name": "mission", "id": ""}.get(k, "")
            self.assertIs(ActionExecutor._match_mission_radio(soup, "form#f", [r_atk, r_trn], "ATTACK"), r_atk)

    # ------------------------------------------------------------------
    # T5-05: UI finiteNum, fmtImprovementPct(-0.01 -> +0.0%), refcounted gaCalcCount
    # ------------------------------------------------------------------
    def test_t5_05_ui_adversarial_refcounted_ga_calc_finite_num_and_neg_zero_in_node(self) -> None:
        cfg = make_ga_nn_config(self, ga_population_size=8, ga_generations=3, ga_seed=42)
        with E2ETestHarness(config=cfg) as h:
            ui_res = run_ui_in_node(
                h.base_url,
                """
                const loaded = await __waitUntil(() => state && state.dashboard !== null, 4000);
                if (!loaded) throw new Error('Dashboard failed to load');

                // 1. finiteNum hardening
                const fObj = finiteNum({}, null);
                const fArr = finiteNum([], null);
                const fBlank = finiteNum('   ', -1);
                const fSym = finiteNum(Symbol('x'), -1);
                const fNan = finiteNum(Number.NaN, 0);
                const fInf = finiteNum(Infinity, 0);
                const fOk = finiteNum(' 42.5 ', 0);

                // 2. fmtImprovementPct -0.0% normalization
                const negTiny = fmtImprovementPct(-0.01, null, null);
                const posTiny = fmtImprovementPct(0.01, null, null);

                // 3. Concurrent refcounted gaCalcCount
                beginGaCalc();
                const afterBegin1 = state.gaCalcRunning;
                const count1 = state.gaCalcCount;
                beginGaCalc();
                const count2 = state.gaCalcCount;
                endGaCalc();
                const afterEnd1 = state.gaCalcRunning;
                const countAfterEnd1 = state.gaCalcCount;
                endGaCalc();
                const afterEnd2 = state.gaCalcRunning;
                const countAfterEnd2 = state.gaCalcCount;

                return {
                  fObj, fArr, fBlank, fSym, fNan, fInf, fOk,
                  negTiny, posTiny,
                  afterBegin1, count1, count2,
                  afterEnd1, countAfterEnd1,
                  afterEnd2, countAfterEnd2,
                };
                """,
            )
            sc = ui_res["scenario"]
            self.assertEqual(ui_res["errors"], [])
            self.assertEqual(ui_res["windowErrors"], [])
            self.assertIsNone(sc["fObj"])
            self.assertIsNone(sc["fArr"])
            self.assertEqual(sc["fBlank"], -1)
            self.assertEqual(sc["fSym"], -1)
            self.assertEqual(sc["fNan"], 0)
            self.assertEqual(sc["fInf"], 0)
            self.assertEqual(sc["fOk"], 42.5)
            self.assertEqual(sc["negTiny"], "+0.0%")
            self.assertEqual(sc["posTiny"], "+0.0%")
            self.assertTrue(sc["afterBegin1"])
            self.assertEqual(sc["count1"], 1)
            self.assertEqual(sc["count2"], 2)
            self.assertTrue(sc["afterEnd1"])
            self.assertEqual(sc["countAfterEnd1"], 1)
            self.assertFalse(sc["afterEnd2"])
            self.assertEqual(sc["countAfterEnd2"], 0)

    # ------------------------------------------------------------------
    # Section B1: Unpadded vs padded BashTracker + executor raid_stats roundtrip
    # ------------------------------------------------------------------
    def test_adv_t5_01_bash_unpadded_vs_padded_and_executor_raid_stats_roundtrip(self) -> None:
        clock = FakeClock(initial=5_000_000.0)
        srv = FakeSpaceInvasionServer(
            planets={
                12476: {
                    "planet_id": 12476,
                    "name": "Home",
                    "coords": "02:380:12",
                    "planet_type": "normal",
                    "temperature": 31,
                    "fields_max": 230,
                    "resources": [50_000, 50_000, 50_000, 20_000],
                    "storage": [500_000, 500_000, 500_000, 500_000],
                    "energy": {"produced": 600, "used": 100},
                    "buildings": {"b1": 4, "b5": 8, "b9": 2},
                    "ships": {"s1": 10, "s4": 25},
                    "defense": {},
                    "economy_rates": {"resource1": 100, "resource2": 100, "resource3": 100, "resource4": 100, "resource5": 100},
                    "building_queue": [],
                }
            },
            active_planet_id=12476,
            clock=clock,
        )
        cfg = StrategyConfig(allow_raids=True, allow_pvp_in_az=True)
        with E2ETestHarness(server=srv, config=cfg) as h:
            assert h.bridge._db is not None
            h.bridge._db.record_galaxy_slots(
                [GalaxySlot("02:380:04", 4, "ActiveHost", "EnemyOne", "light_red")],
                ts=clock.time(),
            )
            for i in range(3):
                h.bridge._db.record_raid("2:380:4", attacked_ts=clock.time() - 600 + i * 30, is_exempt=False, outcome="win")

            blocked = h.api.send_command({"command": "attack", "target_coords": "02:380:04", "dry_run": False})
            self.assertFalse(blocked.get("executed", True))
            self.assertIn("BashTracker", blocked.get("message", ""))

            # Live build execution preserves raid_stats on updated_state via ActionExecutor._build_updated_state
            h.bridge.get_state()
            assert h.bridge._last_state is not None
            h.bridge._last_state = replace(h.bridge._last_state, raid_stats=h.bridge._db.get_raid_stats())
            bld_res = h.api.send_command({"command": "build", "building_id": 1, "dry_run": False})
            self.assertTrue(bld_res.get("executed", False))
            self.assertEqual(bld_res["state"]["raid_stats"]["overall"]["attempts"], 3)

    # ------------------------------------------------------------------
    # Section B2: API GET /state?sid=... & POST /command sid%3D log redaction
    # ------------------------------------------------------------------
    def test_adv_t5_02_api_sid_redaction_log_and_do_post_encoded_payload(self) -> None:
        with E2ETestHarness(config=StrategyConfig()) as h:
            tok_get = "SECRET_GET_TOKEN_4455"
            tok_post = "SECRET_POST_ENC_8877"
            r_get = requests.get(f"{h.base_url}/state?include_buildings=1&sid={tok_get}", timeout=5.0)
            self.assertEqual(r_get.status_code, 200)
            append_api_log(
                h.log_dir,
                method="GET",
                path=f"/state?include_buildings=1&sid={tok_get}",
                status=200,
                payload=None,
                response={"ok": True},
            )
            r_post = requests.post(
                f"{h.base_url}/command?sid%3D{tok_post}",
                json={"command": "plan", "note": f"https://game.test/index?sid%3D{tok_post}&SID={tok_post}"},
                timeout=5.0,
            )
            self.assertEqual(r_post.status_code, 200)
            self.assertNotIn(tok_get, r_post.text)
            self.assertNotIn(tok_post, r_post.text)
            for log_f in h.log_dir.glob("game_api.*"):
                txt = log_f.read_text(encoding="utf-8")
                self.assertNotIn(tok_get, txt)
                self.assertNotIn(tok_post, txt)

    # ------------------------------------------------------------------
    # Section B3: Cross-planet b8=1 r8 isolation, s10 prereq, external cancel
    # ------------------------------------------------------------------
    def test_adv_t5_03_bridge_cross_planet_b8_1_ship_prereq_and_external_research_cancel(self) -> None:
        clock = FakeClock(initial=6000.0)
        planets = {
            12476: {
                "planet_id": 12476,
                "name": "Home",
                "coords": "02:380:12",
                "planet_type": "normal",
                "temperature": 31,
                "fields_max": 230,
                "resources": [100_000, 100_000, 100_000, 50_000],
                "storage": [500_000, 500_000, 500_000, 500_000],
                "energy": {"produced": 600, "used": 100},
                "buildings": {"b1": 5, "b5": 8, "b8": 5, "b9": 1},
                "ships": {},
                "defense": {},
                "economy_rates": {"resource1": 100, "resource2": 100, "resource3": 100, "resource4": 100, "resource5": 100},
                "building_queue": [],
            },
            12477: {
                "planet_id": 12477,
                "name": "Colony",
                "coords": "02:380:14",
                "planet_type": "normal",
                "temperature": 24,
                "fields_max": 190,
                "resources": [100_000, 100_000, 100_000, 50_000],
                "storage": [500_000, 500_000, 500_000, 500_000],
                "energy": {"produced": 600, "used": 100},
                "buildings": {"b1": 4, "b5": 8, "b8": 1, "b9": 1},
                "ships": {},
                "defense": {},
                "economy_rates": {"resource1": 100, "resource2": 100, "resource3": 100, "resource4": 100, "resource5": 100},
                "building_queue": [],
            },
        }
        srv = FakeSpaceInvasionServer(
            planets=planets,
            active_planet_id=12476,
            researches={"r1": 1, "r2": 4, "r4": 2, "r5": 0, "r8": 0},
            clock=clock,
        )
        with E2ETestHarness(server=srv, config=StrategyConfig()) as h:
            h.bridge.get_state(include_buildings=True)
            st0 = h.bridge._last_state
            assert st0 is not None
            self.assertTrue(next(o for o in st0.research_offers if o.research_id == 8).can_research)

            # Cross-planet r8 on b8=1 colony -> executable_now=False and 0 views on dry_run=False
            r8_dry = h.api.send_command({"command": "research", "research_id": 8, "planet_id": 12477, "dry_run": True})
            self.assertFalse(r8_dry["action"]["executable_now"])
            views_before = len(srv.page_view_log)
            r8_live = h.api.send_command({"command": "research", "research_id": 8, "planet_id": 12477, "dry_run": False})
            self.assertFalse(r8_live["executed"])
            self.assertEqual(len(srv.page_view_log), views_before)

            # s10 locked (r5=0) even with b9=1 -> executable_now=False
            s10_dry = h.api.send_command({"command": "build_ship", "ship_id": 10, "quantity": 1, "dry_run": True})
            self.assertFalse(s10_dry["action"]["executable_now"])
            s10_live = h.api.send_command({"command": "build_ship", "ship_id": 10, "quantity": 1, "dry_run": False})
            self.assertFalse(s10_live["executed"])

            # External research cancel clears stale active=True so r8 can execute
            h.bridge._last_state = replace(
                st0,
                research_offers=(
                    ResearchOffer(2, "r2", "Energy technology", 4, 5, Resources(0, 1600, 800, 0), False, "In research (Cancel)", None, active=True),
                    next(o for o in st0.research_offers if o.research_id == 8),
                ),
            )
            merged = h.bridge._merge_cached_offers(replace(st0, research_offers=(), active_queues=()))
            self.assertFalse(any(o.active for o in merged.research_offers))
            self.assertTrue(h.bridge._find_research_action(merged, 8, 12476).executable_now)

    # ------------------------------------------------------------------
    # Section B4: Storage memory URI, inf loot, corrupt cells via /command db_summary & /dashboard
    # ------------------------------------------------------------------
    def test_adv_t5_04_storage_memory_uri_corrupt_cells_and_inf_loot_resilience(self) -> None:
        with tempfile.TemporaryDirectory(prefix="si_adv_db_") as tmp:
            cwd_clean = Path(tmp) / "cwd"
            cwd_clean.mkdir()
            old_cwd = Path.cwd()
            try:
                os.chdir(cwd_clean)
                mem_db = GameDatabase("file::memory:?cache=shared")
                try:
                    mem_db.record_raid(
                        "2:380:4",
                        attacked_ts=float("inf"),
                        is_exempt=False,
                        outcome="Victory",
                        loot={"pig_iron": 1e400, "metal": float("inf"), "spice": 300},
                    )
                    json.dumps(mem_db.get_raid_stats(), allow_nan=False)
                finally:
                    mem_db.close()
                self.assertEqual(list(cwd_clean.iterdir()), [])
            finally:
                os.chdir(old_cwd)

        with E2ETestHarness(config=StrategyConfig()) as h:
            assert h.bridge._db is not None
            h.bridge._db._conn.execute(
                "INSERT INTO economy_rates_cache(planet_id, coords, rates_json, updated_ts) VALUES(1, '01:001:01', 'null', 10.0)"
            )
            h.bridge._db._conn.execute(
                "INSERT INTO spy_targets(coords, owner, fleet_json, defense_json, last_scanned_ts) VALUES('01:001:09', 'Bad', '{bad', '[]', 10.0)"
            )
            h.bridge._db._conn.execute(
                "INSERT INTO planet_snapshots(ts, universe_id, planet_id, coords, name, planet_type, "
                "temperature, fields_used, fields_max, pig_iron, metal, kryptonite, spice, "
                "energy_produced, energy_used, buildings_json, ships_json, defense_json, "
                "economy_rates_json, researches_json) "
                "VALUES(10.0, 31, 1, '01:001:01', 'P', 'normal', 20, 10, 200, 0, 0, 0, 0, 0, 0, "
                "'{bad', 'null', 'null', '{bad', 'null')"
            )
            h.bridge._db._conn.execute(
                "INSERT INTO tutorial_progress(step, total_steps, tasks_json, reward_text, can_complete, first_seen_ts, last_seen_ts) "
                "VALUES(1, 5, '{bad', 'Reward', 0, 10.0, 10.0)"
            )
            h.bridge._db._conn.commit()
            db_res = h.api.send_command({"command": "db_summary"})
            self.assertTrue(db_res.get("ok"))
            dash_res = h.get_dashboard(refresh=0)
            self.assertTrue(dash_res.get("ok"))

    # ------------------------------------------------------------------
    # Section B5: UI concurrent #btn-gann-recalc + #btn-sync refcount & CSS floors
    # ------------------------------------------------------------------
    @unittest.skipUnless(shutil.which("node"), "Node.js not installed")
    def test_adv_t5_05_ui_gann_recalc_concurrent_sync_and_mobile_css_floors(self) -> None:
        cfg = make_ga_nn_config(self, ga_population_size=8, ga_generations=3, ga_seed=42)
        with E2ETestHarness(config=cfg) as h:
            css_txt = re.sub(r"/\*.*?\*/", "", requests.get(f"{h.base_url}/styles.css", timeout=5.0).text, flags=re.DOTALL)
            self.assertRegex(css_txt, r"\.nav-title\s*\{[^}]*font-size\s*:\s*0\.86rem")
            self.assertRegex(css_txt, r"\.eta-pill\s*\{[^}]*font-size\s*:\s*0\.86rem")
            m_1100 = re.search(
                r"@media\s*\(\s*max-width\s*:\s*1100px\s*\)\s*\{((?:[^{}]|\{[^{}]*\})*)\}",
                css_txt,
                flags=re.IGNORECASE | re.DOTALL,
            )
            self.assertIsNotNone(m_1100)
            m_logs = re.search(r"#logs-pre\s*\{[^}]*min-height\s*:\s*(\d+)px", m_1100.group(1))
            self.assertIsNotNone(m_logs)
            self.assertGreaterEqual(int(m_logs.group(1)), 200)

            patch_js = (
                "async (url, opts, fullUrl, nativeFetch) => { "
                "if (url === '/command' && opts && opts.body) { "
                "  const b = JSON.parse(String(opts.body)); "
                "  if (b.command === 'plan') { "
                "    return new Promise((resolve) => { "
                "      globalThis.__resolveSlowPlan = async () => resolve(await nativeFetch(fullUrl, opts)); "
                "    }); "
                "  } "
                "} }"
            )
            ui_res = run_ui_in_node(
                h.base_url,
                """
                const loaded = await __waitUntil(() => state && state.dashboard !== null, 4000);
                if (!loaded) throw new Error('Dashboard failed to load');

                document.getElementById('btn-gann-recalc').click();
                await __sleep(10);
                document.getElementById('btn-sync').click();
                await __waitUntil(() => document.getElementById('feedback-banner').textContent.includes('Synced') && !state.inFlightDashboard, 4000);
                await __sleep(10);
                const activeAfterSync = state.gaCalcRunning;
                const countAfterSync = state.gaCalcCount;
                const hasClassAfterSync = document.getElementById('gann-calc-hud').classList.contains('is-calculating');

                globalThis.__resolveSlowPlan();
                await __waitUntil(() => !state.gaCalcRunning, 4000);
                return {
                  activeAfterSync,
                  countAfterSync,
                  hasClassAfterSync,
                  activeAfterRecalc: state.gaCalcRunning,
                  countAfterRecalc: state.gaCalcCount,
                };
                """,
                fetch_patch_js=patch_js,
            )
            self.assertEqual(ui_res["errors"], [])
            sc = ui_res["scenario"]
            self.assertTrue(sc["activeAfterSync"])
            self.assertEqual(sc["countAfterSync"], 1)
            self.assertTrue(sc["hasClassAfterSync"])
            self.assertFalse(sc["activeAfterRecalc"])
            self.assertEqual(sc["countAfterRecalc"], 0)

    def test_adv_t5_06_unnormalized_coords_api_guards_and_deep_sid_redaction(self) -> None:
        from spaceinvasion.api_server import _redact_api_payload
        from spaceinvasion.fleet import parse_spy_report_html
        from spaceinvasion.logging_utils import redact_sensitive

        clock = FakeClock()
        planets = {
            12476: {
                "planet_id": 12476,
                "name": "Home",
                "coords": "02:380:12",
                "planet_type": "normal",
                "temperature": 31,
                "fields_max": 230,
                "resources": [20000, 20000, 15000, 10000],
                "storage": [100000, 100000, 100000, 100000],
                "energy": {"produced": 200, "used": 100},
                "buildings": {"b1": 5, "b5": 6, "b9": 4},
                "ships": {"s4": 25, "s1": 10, "s12": 5},
                "defense": {},
                "economy_rates": {"resource1": 100, "resource2": 100, "resource3": 100, "resource4": 100},
                "building_queue": [],
            }
        }
        gal = {
            (2, 380): [
                {
                    "position": 4,
                    "name": "YellowTarget",
                    "owner": "IdleY",
                    "status_color": "yellow",
                    "has_debris": False,
                    "is_spacia": False,
                    "spy_href": None,
                },
                {
                    "position": 5,
                    "name": "BlueTarget",
                    "owner": "ProtectedPlayer",
                    "status_color": "blue",
                    "has_debris": False,
                    "is_spacia": False,
                    "spy_href": None,
                },
            ]
        }
        spy_reports = {
            "02:380:04": {
                "owner": "IdleY",
                "resources": {"pig_iron": 30000, "metal": 30000, "kryptonite": 10000, "spice": 5000},
                "fleet": {},
                "defense": {},
            },
            "02:380:05": {
                "owner": "ProtectedPlayer",
                "resources": {"pig_iron": 50000, "metal": 50000, "kryptonite": 20000, "spice": 10000},
                "fleet": {},
                "defense": {},
            },
        }
        srv = FakeSpaceInvasionServer(
            clock=clock,
            planets=planets,
            galaxy_systems=gal,
            spy_reports=spy_reports,
            fleet_form_dialect="standard",
        )
        cfg = StrategyConfig(allow_raids=True, allow_pvp_in_az=True)
        with E2ETestHarness(server=srv, clock=clock, config=cfg, max_actions_per_hour=50) as h:
            h.api.get_state(include_buildings=True)
            h.api.send_command({"command": "scan_galaxy", "galaxy": 2, "system": 380, "dry_run": False})

            # BUG-07 & BUG-09: timestamp clock with interior label is ignored; target coords normalized on save.
            extracted = parse_spy_report_html(
                "<div>Report (12:05:12 - Server) Spying on Planet 2:380:4 "
                "Pig iron: 30.000 Metal: 30.000 Kryptonite: 10.000 Spice: 5.000</div>"
            )
            self.assertEqual(extracted.target_coords, "2:380:4")
            h.bridge._db.record_spy_report(extracted)
            import sqlite3

            con = sqlite3.connect(str(h.db_path))
            try:
                stored_rows = con.execute(
                    "SELECT coords FROM spy_targets ORDER BY coords"
                ).fetchall()
            finally:
                con.close()
            self.assertIn("02:380:04", [str(r[0]) for r in stored_rows])
            self.assertNotIn("2:380:4", [str(r[0]) for r in stored_rows])

            # BUG-01: unpadded own-planet coords blocked for attack and spy.
            res_own_atk = h.api.send_command(
                {"command": "attack", "target_coords": "2:380:12", "dry_run": True}
            )
            self.assertFalse(res_own_atk["executed"])
            self.assertIn("own planet", res_own_atk["message"].lower())

            res_own_spy = h.api.send_command(
                {"command": "spy", "target_coords": "2:380:12", "probes": 1, "dry_run": True}
            )
            self.assertFalse(res_own_spy["executed"])
            self.assertIn("own planet", res_own_spy["message"].lower())

            # BUG-01: unpadded blue-shielded target blocked by normalized status lookup.
            res_blue = h.api.send_command(
                {"command": "attack", "target_coords": "2:380:5", "dry_run": True}
            )
            self.assertFalse(res_blue["executed"])
            self.assertIn("blue", res_blue["message"].lower())

            # BUG-01: unpadded yellow inactive target stays bash-exempt through 4 consecutive raids.
            for idx in range(4):
                res_raid = h.api.send_command(
                    {"command": "attack", "target_coords": "2:380:4", "dry_run": False}
                )
                self.assertTrue(res_raid["executed"], f"Yellow raid {idx + 1} failed: {res_raid}")
            self.assertEqual(len(srv.launched_fleets), 4)

            # BUG-08: multi-encoded sid%253D..., JSON "sid": "...", and mapping {"sid": ...} are redacted.
            raw_samples = {
                "sid": "SECRET_DICT_VAL",
                "url": "https://host/indexInternal.es?sid%253DSECRET_DBL%26action=internalHome",
                "json_blob": '{"sid": "SECRET_JSON_VAL", "nested": {"SID": "SECRET_UPPER_VAL"}}',
            }
            scrubbed_log = redact_sensitive(raw_samples)
            scrubbed_api = _redact_api_payload(raw_samples)
            for scrubbed in (scrubbed_log, scrubbed_api):
                serialized = json.dumps(scrubbed)
                for secret in ("SECRET_DICT_VAL", "SECRET_DBL", "SECRET_JSON_VAL", "SECRET_UPPER_VAL"):
                    self.assertNotIn(secret, serialized)
                self.assertIn("REDACTED", serialized)

            for log_path in h.log_dir.glob("*.log*"):
                self.assertNotIn(srv.sid, log_path.read_text(encoding="utf-8", errors="ignore"))

    def test_adv_t5_07_off_home_sso_redirect_external_cancel_and_corrupt_eta_priority_guards(self) -> None:
        from spaceinvasion.client import SpaceInvasionAuthError
        from spaceinvasion.ranking import build_ranking_evolution

        clock = FakeClock()
        planets = {
            12476: {
                "planet_id": 12476,
                "name": "Home",
                "coords": "02:380:12",
                "planet_type": "normal",
                "temperature": 31,
                "fields_max": 230,
                "resources": [500000, 500000, 500000, 200000],
                "storage": [1000000, 1000000, 1000000, 1000000],
                "energy": {"produced": 400, "used": 100},
                "buildings": {"b1": 4, "b2": 4, "b3": 4, "b4": 4, "b5": 4, "b8": 5},
                "ships": {},
                "defense": {},
                "economy_rates": {"resource1": 100, "resource2": 100, "resource3": 100, "resource4": 100},
                "building_queue": [],
            }
        }
        srv = FakeSpaceInvasionServer(
            clock=clock,
            planets=planets,
            researches={"r1": 1, "r2": 4, "r3": 2, "r4": 4, "r7": 2},
            instant_builds=True,
        )
        cfg = StrategyConfig(planner_mode="priority")
        with E2ETestHarness(server=srv, clock=clock, config=cfg, max_actions_per_hour=50) as h:
            h.api.get_state(include_buildings=True)

            # BUG-04: execute r1 so internalResearch renders "In research (Cancel)",
            # while internalHome reports an idle research queue (external cancel).
            res1 = h.api.send_command({"command": "research", "research_id": 1, "dry_run": False})
            self.assertTrue(res1["executed"], f"Initial research failed: {res1}")
            clock.advance(1.0)
            res2 = h.api.send_command(
                {"command": "research", "research_id": 2, "allow_live_verify": True, "dry_run": False}
            )
            self.assertTrue(res2["executed"], f"Follow-up research stayed stuck after external cancel: {res2}")

            # BUG-02: off-internalHome BitMeUp SSO / universe picker redirect raises SpaceInvasionAuthError cleanly.
            orig_render = srv.render_frame

            def _sso_on_buildings(frame_id: str, url: str, form_data=None):
                if frame_id == "F_Hauptframe" and "action=internalBuildings" in url:
                    return (
                        url,
                        '<html><body><div class="bitmeup-login">Sign in</div>'
                        '<div class="sip-tab-universe">Universe</div></body></html>',
                    )
                return orig_render(frame_id, url, form_data=form_data)

            with patch.object(srv, "render_frame", side_effect=_sso_on_buildings):
                with h._make_client(0) as client:
                    with self.assertRaises(SpaceInvasionAuthError):
                        client.navigate_menu("internalBuildings")
                    with self.assertRaises(SpaceInvasionAuthError):
                        client.read_full_snapshot(
                            include_home=False,
                            include_buildings=False,
                            include_research=False,
                            include_shipyard=False,
                        )

        # Bug A, Bug C, Bug D: corrupt inputs remain finite, bounded, and safe in ETA + priority planning.
        corrupt_planet = make_planet(
            planet_id=9001,
            name="Corrupt",
            coords="02:380:12",
            fields_used=9,
            resources=Resources(pig_iron=95_000, metal=10_000, kryptonite=10_000, spice=5_000),
            storage=Resources(pig_iron=100_000, metal=100_000, kryptonite=100_000, spice=100_000),
            energy=EnergyBalance(produced=200, used=100),
            buildings={"b1": 4, "b8": 5, "b17": "bad", "b7": None},
        )
        corrupt_target = {
            "coords": "02:380:04",
            "name": "BadTarget",
            "owner": "CorruptIdle",
            "status_color": "yellow",
            "is_inactive": True,
            "last_scanned_ts": "bad",
            "last_spied_ts": float("nan"),
            "resources": "bad",
            "fleet": None,
            "defense": float("nan"),
        }
        corrupt_state = make_game_state(
            planets=[corrupt_planet],
            researches={"r1": 2, "r2": "bad"},
            active_planet_id=9001,
        )
        corrupt_state = replace(corrupt_state, raid_targets=(corrupt_target,))
        eta_res = build_buildings_eta(corrupt_state, corrupt_planet)
        self.assertIsInstance(eta_res, dict)
        self.assertGreater(len(eta_res.get("buildings", [])), 0)

        inf_eta = compute_action_eta(
            corrupt_state,
            {
                "kind": "BUILD",
                "planet_id": 9001,
                "target_id": 1,
                "target_level": 5,
                "cost": {"pig_iron": 1_000_000},
                "missing": {"pig_iron": float("inf")},
            },
        )
        self.assertIsNotNone(inf_eta["wait_hours"])
        self.assertGreater(inf_eta["wait_hours"], 0.0)
        self.assertNotEqual(inf_eta["wait_eta"], "Ready")
        self.assertEqual(inf_eta["eta_bottleneck"], "pig_iron")

        rank_evo = build_ranking_evolution(
            corrupt_state,
            [
                {"ts": "bad", "server_points": "bad", "server_rank": None},
                {"ts": 2000, "server_points": float("nan"), "server_rank": float("inf")},
                {"ts": 3000, "server_points": 2500, "server_rank": 100},
            ],
        )
        self.assertIsInstance(rank_evo, dict)

        p1_actions = plan_actions(
            corrupt_state,
            config=StrategyConfig(planner_mode="priority", storage_warn_ratio=float("nan"), allow_raids=True),
            storage_warn_ratio=float("nan"),
            scanned_systems={"2:380": "bad"},
        )
        self.assertTrue(
            any("storage" in a.reason.lower() or "warehouse" in a.reason.lower() for a in p1_actions),
            f"Expected storage overflow warning with NaN warn_ratio, got {[a.reason for a in p1_actions]}",
        )

    def test_adv_t5_08_domain_coords_cc_huge_ints_unicode_and_state_resilience(self) -> None:
        from spaceinvasion import game_data
        from spaceinvasion.auth import universes_from_portal_data
        from spaceinvasion.fleet import fleet_cargo_capacity, plan_escape_flight, simulate_raid_outcome, SpyReport
        from spaceinvasion.optimizer import encode_empire_state
        from spaceinvasion.ranking import build_combat_overview, build_ranking_evolution, simulate_combat_matchup
        from spaceinvasion.state import game_state_from_dict

        # Finding 1 + Finding 2: Unnormalized own coords blocked in ga_nn + simulate_action_sequence;
        # bad construction_commander degrades safely across priority and ga_nn.
        own_unnorm = make_planet(
            planet_id=101,
            name="Own",
            coords="1:10:1",
            buildings={"b1": 4, "b2": 2, "b3": 2, "b4": 2, "b5": 5, "b9": 2},
            ships={"s1": 5, "s4": 10},
        )
        st_own = replace(
            make_game_state(planets=[own_unnorm], active_planet_id=101),
            specs={"construction_commander": "bad"},
            raid_targets=(
                {
                    "coords": "01:010:01",
                    "is_inactive": True,
                    "status_color": "yellow",
                    "resources": {"pig_iron": 999_999, "metal": 999_999, "kryptonite": 999_999, "spice": 999_999},
                    "last_spied_ts": 1000.0,
                },
            ),
        )
        cfg_raid = StrategyConfig(planner_mode="ga_nn", allow_raids=True, ga_population_size=4, ga_generations=1)
        ga_acts = optimize_plan_actions(st_own, config=cfg_raid)
        self.assertFalse(any(a.kind == "ATTACK" and ("01:010:01" in a.target_name or "1:10:1" in a.target_name) for a in ga_acts))
        sim_own = simulate_action_sequence(st_own, (SimAction("ATTACK", 101, target_coords="01:010:01"),), config=cfg_raid)
        self.assertEqual(sim_own.valid_steps, 0)
        self.assertEqual(sim_own.rejected_steps, 1)

        # Finding 3 + Finding 4 + Finding 5 across game_data, state, fleet, ranking, optimizer, auth
        for cat in ("buildings", "research", "ships", "defense"):
            self.assertIsNone(game_data.resolve_unit(cat, "²"))
            eta = compute_action_eta(st_own, {"kind": "BUILD", "planet_id": 101, "target_id": 0, "target_name": "²"})
            self.assertIn("wait_eta", eta)
        self.assertTrue(math.isfinite(game_data.mine_production_per_hour(1, 5, mining_tech_level=10**1_000_000)))

        gs_corrupt = game_state_from_dict(
            {
                "active_planet_id": "bad",
                "speedup_factor": "bad",
                "account_points": "bad",
                "planets": [
                    {
                        "planet_id": 101,
                        "name": "H",
                        "coords": "01:010:01",
                        "buildings": {"b1": 10**400, "b5": 10},
                        "ships": {"s1": 10**1_000_000, "s4": 10**400, "s9": 10**400},
                        "defense": {"d1": 10**1_000_000},
                    }
                ],
                "active_queues": [{"planet_id": 101, "building_id": "bad", "target_level": "bad"}],
                "fleet_alerts": "bad",
                "tutorial": {"tasks": [{"step": 1, "building_id": "bad"}]},
            }
        )
        p_huge = replace(
            own_unnorm,
            buildings={"b1": 10**400, "b5": 10},
            ships={"s1": 10**1_000_000, "s4": 10**400, "s9": 10**400},
            defense={"d1": 10**1_000_000},
        )
        st_huge = replace(
            make_game_state(planets=[p_huge], active_planet_id=101, researches={"r1": 10**400, "r2": 10**400, "r8": 10**400}),
            account_points=10**1_000_000,
        )
        self.assertIsNotNone(gs_corrupt)
        self.assertTrue(all(math.isfinite(x) for x in encode_empire_state(st_huge)))
        self.assertTrue(math.isfinite(run_optimizer(st_huge, config=cfg_raid).best_fitness))
        evo = build_ranking_evolution(st_huge, [{"ts": 1000.0, "server_points": 500.0, "estimated_points": 500.0}])
        ov = build_combat_overview(st_huge, safety_margin="bad")  # type: ignore[arg-type]
        sim_m = simulate_combat_matchup(
            st_huge, attacker_ships={"s4": 10**1_000_000}, defender_ships={"s4": 10**400}, safety_margin=float("nan")
        )
        json.dumps({"evo": evo, "ov": ov, "sim": sim_m}, allow_nan=False)

        self.assertGreater(fleet_cargo_capacity({"s1": 10**400}, miner_spec_level=10**400), 0)
        self.assertTrue(math.isfinite(simulate_raid_outcome({"s4": 10**1_000_000}, SpyReport("02:380:05", fleet={"s4": 10**400})).attacker_power))
        self.assertIn("~1e", plan_escape_flight(p_huge, target_coords="01:010:02").reason)
        unis = universes_from_portal_data("s", [{"id": 31, "name": "G", "speed": "", "flightSpeed": "bad"}], "https://example.test")
        self.assertEqual(unis[0].speed, 1.0)
        self.assertEqual(unis[0].flight_speed, 1.0)

    def test_adv_t5_09_runtime_deep_target_coalescing_stale_cancel_cost_and_clocks(self) -> None:
        from spaceinvasion.fleet import _extract_spy_coords, extract_galaxy_system_coords, normalize_coords, parse_spy_report_html

        # GAP-05: Clock timestamps must never resolve as planet coordinates; Harvest radio rejected for ATTACK
        self.assertEqual(_extract_spy_coords("[12:05:12] Spy report Pig iron 500"), "")
        self.assertEqual(_extract_spy_coords("[12:05:12] target 2:380:4 Pig iron 500"), "2:380:4")
        self.assertEqual(
            normalize_coords(
                parse_spy_report_html("<html><body><div>[12:05:12] target 2:380:4</div><div>Pig iron 500</div></body></html>").target_coords
            ),
            "02:380:04",
        )
        self.assertEqual(extract_galaxy_system_coords("<div>Clock [12:05:12]</div>", default=(7, 77)), (7, 77))

        clock = FakeClock()
        planets = {
            12476: {
                "planet_id": 12476,
                "name": "Home",
                "coords": "02:380:12",
                "planet_type": "normal",
                "temperature": 31,
                "fields_max": 230,
                "resources": [1000, 1000, 1000, 500],
                "storage": [1000000, 1000000, 1000000, 1000000],
                "energy": {"produced": 400, "used": 100},
                "buildings": {"b1": 4, "b2": 4, "b3": 4, "b4": 4, "b5": 4, "b8": 5, "b9": 4},
                "ships": {"s4": 10, "s1": 5},
                "defense": {},
                "economy_rates": {"resource1": 100, "resource2": 100, "resource3": 100, "resource4": 100},
                "building_queue": [],
            }
        }
        srv = FakeSpaceInvasionServer(clock=clock, planets=planets, researches={"r1": 1, "r2": 4, "r3": 2, "r4": 4, "r7": 2})
        cfg = StrategyConfig(planner_mode="priority", allow_raids=True)
        with E2ETestHarness(server=srv, clock=clock, config=cfg, max_actions_per_hour=50) as h:
            assert h.bridge._db is not None
            # GAP-04 + Caveat: legacy unnormalized row + corrupt snapshot rows handled cleanly on state read
            h.bridge._db._conn.execute(
                "INSERT INTO spy_targets(coords, owner, status_color, is_inactive, pig_iron, metal, kryptonite, spice, last_spied_ts, last_scanned_ts) "
                "VALUES('2:380:4', 'OldOwner', 'white', 0, 9999, 100, 0, 0, 800.0, 500.0)"
            )
            h.bridge._db._conn.execute(
                "INSERT INTO rank_snapshots(ts, universe_id, estimated_points, pts_per_hr, spice_value, planets) "
                "VALUES('bad_ts', 31, 'bad_pts', 0.0, 0.0, 1)"
            )
            h.bridge._db._conn.execute(
                "INSERT INTO planet_snapshots(ts, universe_id, planet_id, coords, name, planet_type, "
                "temperature, fields_used, fields_max, pig_iron, metal, kryptonite, spice, "
                "energy_produced, energy_used, buildings_json, ships_json, defense_json, "
                "economy_rates_json, researches_json) "
                "VALUES('bad_ts', 31, 12476, '02:380:12', 'Home', 'normal', 31, 10, 230, 0, 0, 0, 0, 0, 0, '{}', '{}', '{}', '{}', '{}')"
            )
            h.bridge._db._conn.commit()
            h.bridge._db.record_galaxy_slots(
                [GalaxySlot("02:380:04", 4, "Farm", "OldOwner", "yellow")],
                ts=1000.0,
            )
            rows = h.bridge._db._conn.execute("SELECT coords FROM spy_targets WHERE coords LIKE '%380%'").fetchall()
            self.assertEqual([r[0] for r in rows], ["02:380:04"])
            direct = h.bridge._db.get_spy_target_by_coords("2:380:4")
            self.assertIsNotNone(direct)
            self.assertEqual(direct["resources"]["pig_iron"], 9999)

            # Live get_state runs record_state_snapshot cleanly over corrupt pre-existing snapshot rows
            st = h.api.get_state(include_buildings=True)
            self.assertTrue(bool(st.planets))

            # GAP-01: deep blue target ranked beyond top-100 slice still blocked via DB lookup
            many_y = [GalaxySlot(f"05:{idx:03d}:01", 1, f"F{idx}", f"O{idx}", "yellow") for idx in range(1, 106)]
            many_y.append(GalaxySlot("09:009:09", 9, "DeepBlue", "VacDeep", "blue"))
            h.bridge._db.record_galaxy_slots(many_y, ts=1100.0)
            h.bridge._raid_targets = h.bridge._enrich_raid_targets(h.bridge._db.get_spy_targets(limit=100))
            atk_deep = h.api.send_command({"command": "attack", "target_coords": "9:9:9", "dry_run": False})
            self.assertFalse(atk_deep.get("executed"))
            self.assertIn("protected", str(atk_deep.get("message", "")).lower())

            # GAP-03: zero-cost active research restored with real catalog cost after external cancel
            cur_st = h.bridge._last_state
            assert cur_st is not None
            r2_zero = ResearchOffer(2, "r2", "Energy technology", 4, 5, Resources(), False, "In research (Cancel)", None, active=True)
            h.bridge._last_state = replace(cur_st, research_offers=(r2_zero,))
            merged = h.bridge._merge_cached_offers(replace(cur_st, research_offers=(), active_queues=()))
            self.assertFalse(merged.research_offers[0].cost.is_zero())
            self.assertEqual(merged.research_offers[0].status_text, "Not enough resources")

    def test_adv_t5_10_gap05_clocks_dark_red_auth_executor_and_storage_blank_path(self) -> None:
        from selenium.common.exceptions import ElementNotInteractableException
        from spaceinvasion.auth import SpaceInvasionAuthError, universes_from_portal_data
        from spaceinvasion.fleet import extract_galaxy_system_coords, parse_spy_messages_html

        clock = FakeClock()
        planets = {
            12476: {
                "planet_id": 12476,
                "name": "Home",
                "coords": "02:380:12",
                "planet_type": "normal",
                "temperature": 31,
                "fields_max": 230,
                "resources": [100000, 100000, 100000, 100000],
                "storage": [500000, 500000, 500000, 500000],
                "energy": {"produced": 400, "used": 100},
                "buildings": {"b1": 4, "b2": 4, "b3": 4, "b4": 4, "b5": 4, "b8": 4, "b9": 4},
                "ships": {"s1": 5, "s4": 10, "s10": 5},
                "defense": {},
                "economy_rates": {"resource1": 100, "resource2": 100, "resource3": 100, "resource4": 100},
                "building_queue": [],
            }
        }
        srv = FakeSpaceInvasionServer(clock=clock, planets=planets)
        cfg = StrategyConfig(planner_mode="priority", allow_raids=True, allow_pvp_in_az=True)
        with E2ETestHarness(server=srv, clock=clock, config=cfg, max_actions_per_hour=50) as h:
            # GAP-05: non-0M clocks vs coordinate context & preserved 02:38:04 / 12:15:08
            self.assertEqual(
                extract_galaxy_system_coords("<div>[14:15:12]</div>", default=(7, 77)),
                (7, 77),
            )
            self.assertEqual(
                extract_galaxy_system_coords(
                    "<table><tr><td>14:15:12</td></tr><tr><td>14:15:12</td></tr></table>",
                    default=(7, 77),
                ),
                (7, 77),
            )
            self.assertEqual(
                extract_galaxy_system_coords("<div>[14:15:12] System [02:38:04]</div>", default=(7, 77)),
                (2, 38),
            )
            self.assertEqual(
                extract_galaxy_system_coords(
                    "<table><tr><td>02:38:01</td></tr><tr><td>02:38:04</td></tr></table>",
                    default=(2, 380),
                ),
                (2, 38),
            )
            msgs_html = (
                "<table>"
                "<tr><td>[14:15:12] Spy report for Planet [02:38:04] Pig iron: 5000 Metal: 100</td></tr>"
                "<tr><td>[14:15:12] Spy report Pig iron 500</td></tr>"
                "<tr><td>Planet [12:15:08] Pig iron: 5000</td></tr>"
                "</table>"
            )
            parsed_msgs = parse_spy_messages_html(msgs_html)
            coords_list = [r.target_coords for r in parsed_msgs]
            self.assertIn("02:38:04", coords_list)
            self.assertIn("12:15:08", coords_list)
            self.assertEqual(len(parsed_msgs), 2)

            # NEW-01: dark_red blocked on POST /command attack even with allow_pvp_in_az=True
            assert h.bridge._db is not None
            h.bridge._db.record_galaxy_slots(
                [GalaxySlot("05:010:03", 3, "TitanPlanet", "Titan", "dark_red")],
                ts=clock.time(),
            )
            h.bridge._raid_targets = h.bridge._enrich_raid_targets(h.bridge._db.get_spy_targets(limit=100))
            atk_resp = requests.post(
                f"{h.base_url}/command",
                json={"command": "attack", "target_coords": "05:010:03", "dry_run": False},
                timeout=5.0,
            )
            self.assertEqual(atk_resp.status_code, 200)
            atk_data = atk_resp.json()
            self.assertFalse(atk_data.get("executed"))
            self.assertIn("protected", str(atk_data.get("message", "")).lower())
            self.assertEqual(len(srv.launched_fleets), 0)

            # NEW-03: _wait_frame_ready fails fast on portal/login HTML even when expected action stays in URL
            orig_render = srv.render_frame

            def _portal_with_action_href(frame_id: str, url: str, form_data=None):
                if frame_id == "F_Hauptframe" and "action=internalBuildings" in url:
                    return (
                        url,
                        '<html><body><div class="bitmeup-login">Sign in</div>'
                        '<div class="sip-tab-universe">Choose Universe</div></body></html>',
                    )
                return orig_render(frame_id, url, form_data=form_data)

            with patch.object(srv, "render_frame", side_effect=_portal_with_action_href):
                with h._make_client(0) as client:
                    with self.assertRaises(SpaceInvasionAuthError):
                        client.navigate_menu("internalBuildings")

            # NEW-02: ElementNotInteractableException in planet <option> fallback handled without crashing
            ex = ActionExecutor(dry_run=False, max_actions_per_hour=50)
            multi_html = (
                '<select name="planet">'
                '<option value="indexInternal.es?newplanet=101" selected>P1</option>'
                '<option value="indexInternal.es?newplanet=102">P2</option>'
                "</select>"
            )
            mock_client = MagicMock(spec=GameBrowserClient)
            mock_client.driver = MagicMock()
            opt1 = MagicMock()
            opt1.get_attribute.return_value = "indexInternal.es?newplanet=101"
            opt1.is_selected.return_value = True
            opt2 = MagicMock()
            opt2.get_attribute.return_value = "indexInternal.es?newplanet=102"
            opt2.is_selected.return_value = False
            opt2.click.side_effect = ElementNotInteractableException("hidden option")
            sel = MagicMock()
            sel.find_elements.return_value = [opt1, opt2]
            mock_client.driver.find_element.return_value = sel
            with patch("spaceinvasion.executor.Select") as mock_select_cls:
                mock_select_cls.return_value.select_by_value.side_effect = ElementNotInteractableException("hidden")
                switched, waited_html = ex._select_planet_if_needed(mock_client, 102, html=multi_html)
            self.assertFalse(switched)
            self.assertIsNone(waited_html)

            # NEW-04: universes_from_portal_data skips malformed non-Mapping entries
            uni_opts = universes_from_portal_data(
                "sid123",
                ["bad", {"id": True, "name": "BoolId"}, {"id": 31, "name": "Genesis"}],
                "https://spaceinvasion.bitmeup.com",
            )
            self.assertEqual([u.universe_id for u in uni_opts], [31])

            # Finding 2: blank attack_history_path="" disables JSON mirror cleanly
            with tempfile.TemporaryDirectory() as db_tmp:
                blank_db = GameDatabase(Path(db_tmp) / "blank.db", attack_history_path="")
                try:
                    self.assertIsNone(blank_db.attack_history_path)
                    blank_db.record_raid("02:380:04", attacked_ts=100.0, is_exempt=True, outcome="Win", loot={"pig_iron": 100})
                    self.assertIsNone(blank_db.sync_attack_history_file())
                finally:
                    blank_db.close()

    def test_adv_t5_11_extreme_ints_1e1m_nan_rank_history_http_state_and_ui_node(self) -> None:
        import time
        from spaceinvasion import game_data
        from spaceinvasion.logging_utils import append_tick_logs
        from spaceinvasion.ranking import build_combat_overview, build_ranking_evolution, load_rank_history, record_rank_snapshot
        from spaceinvasion.state import BuildingOffer, QueueJob

        def _reject_nonfinite(tok: str):
            raise ValueError(f"Non-standard JSON constant {tok!r}")

        clock = FakeClock()
        with E2ETestHarness(clock=clock, config=StrategyConfig(planner_mode="priority", allow_raids=True)) as h:
            exact_pid = 10**18 + 123456789
            p_huge = PlanetState(
                planet_id=exact_pid,
                name="Colony",
                coords="02:380:12",
                planet_type="normal",
                temperature=31,
                fields_used=5,
                fields_max=10**1_000_000,
                resources=Resources(10**400, 10**400, 10**400, 10**400),
                storage=Resources(10**400, 10**400, 10**400, 10**400),
                energy=EnergyBalance(10**5000, 100),
                buildings={"b1": 10**1_000_000, "b5": 5, "b8": 5, "b9": 10**1_000_000, "b10": 5},
                ships={"s1": 10**1_000_000, "s4": 10**400},
                defense={"d1": 10**1_000_000},
                building_offers=(
                    BuildingOffer(1, "Pig iron mine", 5, 6, Resources(10**400, 0, 0, 0), False, "Need"),
                ),
                economy_rates={"resource1": 100, "resource2": 100, "resource3": 100, "resource4": 100},
            )
            raid_t = {
                "coords": "01:001:09",
                "owner": "IdleY",
                "status_color": "yellow",
                "is_inactive": True,
                "resources": {"pig_iron": 10**400, "metal": 10**308, "kryptonite": 0, "spice": 0},
                "fleet": {},
                "defense": {},
            }
            st_huge = GameState(
                universe_id=31,
                user_id=1,
                nickname="Chaos",
                timestamp=1_700_000_000.0,
                planets=(p_huge,),
                active_planet_id=exact_pid,
                researches={"r1": 10**1_000_000, "r4": 5, "r5": 5, "r6": 5},
                active_queues=(QueueJob("research", "Mining technology", target_level=10**5000),),
                research_offers=(
                    ResearchOffer(1, "r1", "Mining technology", 1, 10**5000, Resources(100, 0, 0, 0), True, "In research", active=True),
                ),
                account_points=10**1_000_000,
                account_rank=10**5000,
                raid_targets=(raid_t,),
            )

            self.assertEqual(_format_duration(0.5), "1s")
            acts_p = plan_actions(st_huge, config=StrategyConfig(planner_mode="priority", allow_raids=True))
            self.assertTrue(len(acts_p) > 0)
            acts_ga = plan_actions(
                st_huge,
                config=StrategyConfig(planner_mode="ga_nn", ga_population_size=4, ga_generations=1, ga_horizon_steps=2, allow_raids=True),
            )
            self.assertTrue(len(acts_ga) > 0)
            sim_res = simulate_action_sequence(
                st_huge,
                [SimAction("BUILD", exact_pid, target_id=1), SimAction("RAID", exact_pid, target_coords="01:001:09")],
            )
            self.assertTrue(math.isfinite(sim_res.fitness))

            eta = build_buildings_eta(st_huge, p_huge)
            cov = build_combat_overview(st_huge, p_huge)
            self.assertEqual(eta["planet"]["planet_id"], exact_pid)
            self.assertEqual(cov["planet_id"], exact_pid)
            json.dumps(eta, allow_nan=False)
            json.dumps(cov, allow_nan=False)

            with tempfile.TemporaryDirectory() as tmp_rk:
                hp = Path(tmp_rk) / "rank_history.json"
                hp.write_text('[{"ts": 1000.0, "estimated_points": NaN, "server_rank": Infinity, "server_points": 1e5000}]', encoding="utf-8")
                json.dumps(load_rank_history(hp), allow_nan=False)
                snaps = record_rank_snapshot(hp, st_huge)
                json.dumps(snaps, allow_nan=False)
                evo = build_ranking_evolution(
                    st_huge,
                    [{"ts": 1000.0, "estimated_points": float("nan"), "server_rank": float("inf"), "server_points": 10**5000}],
                )
                json.dumps(evo, allow_nan=False)

            append_tick_logs(
                h.log_dir,
                source="commander",
                tick=1,
                mode="priority",
                state=st_huge,
                actual_action=None,
                actual_result={"ok": True, "huge": 10**1_000_000, "nan": float("nan")},
                next_action=None,
            )
            append_api_log(
                h.log_dir,
                method="POST",
                path="/command",
                status=200,
                payload={"huge": 10**1_000_000, "inf": float("inf")},
                response={"huge": 10**400, "nan": float("nan")},
            )
            for jf in h.log_dir.glob("*.jsonl"):
                for line in jf.read_text(encoding="utf-8").splitlines():
                    json.loads(line, parse_constant=_reject_nonfinite)

            # Inject extreme state into bridge cache -> test HTTP /state, /dashboard, and POST NaN rejection
            h.bridge._last_state = st_huge
            h.bridge._last_recorded_state_obj = st_huge
            h.bridge._last_snapshot_at = clock.monotonic()
            h.bridge._last_buildings_fetched = True
            h.bridge._last_home_fetched = True
            h.bridge._last_home_at = clock.monotonic()

            st_resp = requests.get(f"{h.base_url}/state", timeout=5.0)
            self.assertEqual(st_resp.status_code, 200)
            st_json = json.loads(st_resp.text, parse_constant=_reject_nonfinite)
            self.assertEqual(st_json["planets"][0]["planet_id"], exact_pid)

            dash_resp = requests.get(f"{h.base_url}/dashboard?refresh=0", timeout=5.0)
            self.assertEqual(dash_resp.status_code, 200)
            dash_json = json.loads(dash_resp.text, parse_constant=_reject_nonfinite)
            self.assertTrue(dash_json["ok"])

            nan_post = requests.post(
                f"{h.base_url}/command",
                data=b'{"command":"plan","bad":NaN}',
                headers={"Content-Type": "application/json"},
                timeout=5.0,
            )
            self.assertEqual(nan_post.status_code, 400)

            if shutil.which("node"):
                ui_patch = (
                    "async (url, opts, fullUrl, nativeFetch) => { "
                    "if (url.startsWith('/dashboard')) { "
                    "  const r = await nativeFetch(fullUrl, opts); "
                    "  const d = await r.json(); "
                    "  d.state.planets[0].energy = { produced: 1e400, used: 1e400 }; "
                    "  d.state.planets[0].fields_used = 1e400; "
                    "  d.state.planets[0].fields_max = 1e400; "
                    "  d.ranking = { "
                    "    current: { estimated_points: 1e400, buildings_points: 1e400, research_points: 0, baseline_points: 1e400, server_rank: 1e400, server_points: 1e400, spice_value: 1e400, attack_zone: { label: 'AZ 1', range_text: '0-500' } }, "
                    "    rates: { pts_per_hr_steady: 1e400, net_resources_per_hr: 1e400, spice_eq_per_hr: 1e400, speedup_factor: 1 }, "
                    "    horizons: [], "
                    "    milestones: [], "
                    "    observed_trend: { sample_count: 3, delta_points: NaN, observed_pts_per_hr: Infinity, elapsed_hours: NaN, samples: [{ estimated_points: NaN }, { estimated_points: 12.5 }, { estimated_points: Infinity }] } "
                    "  }; "
                    "  return { ok: true, status: 200, json: async () => d }; "
                    "} }"
                )
                ui_res = run_ui_in_node(
                    h.base_url,
                    """
                    const loaded = await __waitUntil(() => state && state.dashboard !== null, 4000);
                    if (!loaded) throw new Error('Dashboard failed to load');
                    const valEnergy = document.getElementById('val-energy').textContent;
                    const energyRatio = document.getElementById('energy-ratio').textContent;
                    const planetSpecs = document.getElementById('planet-specs').textContent;
                    setActivePage('ranking');
                    await __sleep(20);
                    const rnkBreakdown = document.getElementById('rnk-breakdown').textContent;
                    const rnkServerPos = document.getElementById('rnk-server-pos').textContent;
                    const svg = document.getElementById('rnk-chart');
                    const svgChildren = svg ? svg.children.map((ch) => ({
                      tag: ch.tagName,
                      cx: ch.getAttribute('cx'),
                      cy: ch.getAttribute('cy'),
                      points: ch.getAttribute('points'),
                      text: ch.textContent,
                    })) : [];
                    return { valEnergy, energyRatio, planetSpecs, rnkBreakdown, rnkServerPos, svgChildren };
                    """,
                    fetch_patch_js=ui_patch,
                )
                self.assertEqual(ui_res["errors"], [])
                sc = ui_res["scenario"]
                for k in ("valEnergy", "energyRatio", "planetSpecs", "rnkBreakdown", "rnkServerPos"):
                    self.assertNotIn("NaN", sc[k])
                    self.assertNotIn("∞", sc[k])
                    self.assertNotIn("Infinity", sc[k])
                self.assertTrue(len(sc["svgChildren"]) >= 1)
                for ch in sc["svgChildren"]:
                    for attr in ("cx", "cy", "points", "text"):
                        v = ch.get(attr)
                        if v is not None:
                            self.assertNotIn("NaN", str(v))
                            self.assertNotIn("Infinity", str(v))

    def test_adv_t5_12_ranking_1e307_1e295_b17_60_and_1e308_delta_strict_http_json(self) -> None:
        from spaceinvasion.state import BuildingOffer

        def _reject_nonfinite(tok: str):
            raise ValueError(f"Non-standard JSON constant {tok!r}")

        clock = FakeClock()
        with E2ETestHarness(clock=clock, config=StrategyConfig(planner_mode="priority")) as h:
            h.bridge._rank_samples = [
                {"ts": -1e308, "server_points": -1e308, "estimated_points": -1e308, "server_rank": 1},
                {"ts": 1e308, "server_points": 1e308, "estimated_points": 1e308, "server_rank": 1},
            ]
            for spd in (1e307, 1e295):
                p_bf = PlanetState(
                    planet_id=12476,
                    name="Colony",
                    coords="02:380:12",
                    planet_type="normal",
                    temperature=31,
                    fields_used=55,
                    fields_max=230,
                    resources=Resources(0, 0, 0, 0),
                    storage=Resources(100_000, 100_000, 100_000, 100_000),
                    energy=EnergyBalance(produced=900, used=600),
                    buildings={"b1": 5, "b2": 2, "b3": 5, "b4": 3, "b5": 5, "b17": 60},
                    building_offers=(
                        BuildingOffer(1, "Pig iron mine", 5, 6, Resources(1000, 0, 0, 0), False, "Need pig iron"),
                    ),
                )
                st_ext = GameState(
                    universe_id=31,
                    user_id=1,
                    nickname="RankAdv",
                    timestamp=1_700_000_000.0,
                    planets=(p_bf,),
                    active_planet_id=12476,
                    researches={"r1": 1},
                    speedup_factor=spd,
                )
                h.bridge._last_state = st_ext
                h.bridge._last_recorded_state_obj = st_ext
                h.bridge._last_snapshot_at = clock.monotonic()
                h.bridge._last_buildings_fetched = True
                h.bridge._last_home_fetched = True
                h.bridge._last_home_at = clock.monotonic()

                dash_resp = requests.get(f"{h.base_url}/dashboard?refresh=0", timeout=5.0)
                self.assertEqual(dash_resp.status_code, 200)
                dash_json = json.loads(dash_resp.text, parse_constant=_reject_nonfinite)
                self.assertTrue(dash_json["ok"])

                for cmd_payload in (
                    {"command": "buildings_eta"},
                    {"command": "ranking_evolution"},
                    {"command": "plan"},
                ):
                    cmd_resp = requests.post(f"{h.base_url}/command", json=cmd_payload, timeout=5.0)
                    self.assertEqual(cmd_resp.status_code, 200)
                    cmd_json = json.loads(cmd_resp.text, parse_constant=_reject_nonfinite)
                    self.assertTrue(cmd_json["ok"], msg=f"Command {cmd_payload} failed for speedup={spd}")

                if spd == 1e307:
                    eta = compute_action_eta(
                        st_ext,
                        PlannedAction(
                            priority="P2",
                            kind="BUILD",
                            planet_id=12476,
                            planet_coords="02:380:12",
                            target_id=1,
                            target_name="Pig iron mine",
                            target_level=6,
                            cost=Resources(1000, 0, 0, 0),
                            missing=Resources(1000, 0, 0, 0),
                            reason="test",
                        ),
                    )
                    self.assertIsNone(eta["wait_hours"])
                    self.assertEqual(eta["wait_eta"], "—")
                    self.assertEqual(eta["eta_bottleneck"], "pig_iron")

    def test_adv_t5_13_10e400_storage_p1_warehouse_and_5000_digit_html_config_sim(self) -> None:
        from spaceinvasion.config import load_strategy_config, save_strategy_config
        from spaceinvasion.optimizer import _parse_timer_seconds
        from spaceinvasion.state import (
            BuildingOffer,
            parse_building_queue,
            parse_buildings_page,
            parse_home_metadata,
            parse_menu_status,
            parse_research_page,
            parse_tutorial_from_home,
            parse_unit_factory_page,
        )

        def _reject_nonfinite(tok: str):
            raise ValueError(f"Non-standard JSON constant {tok!r}")

        clock = FakeClock()
        with E2ETestHarness(clock=clock, config=StrategyConfig(planner_mode="priority")) as h:
            p_over = PlanetState(
                planet_id=101,
                name="Overflow",
                coords="01:010:01",
                planet_type="normal",
                temperature=20,
                fields_used=20,
                fields_max=200,
                resources=Resources(10**400, 10**400, 10**400, 10**400),
                storage=Resources(100_000, 100_000, 100_000, 100_000),
                energy=EnergyBalance(produced=500, used=100),
                buildings={"b1": 5, "b5": 5, "b9": 1, "b11": 5},
                building_offers=(
                    BuildingOffer(
                        11,
                        "Warehouse",
                        5,
                        6,
                        Resources(1000, 0, 0, 0),
                        True,
                        "Build level 6",
                        build_href="indexInternal.es?action=internalBuildItem&buildingID=11#11",
                    ),
                    BuildingOffer(
                        1,
                        "Pig iron mine",
                        5,
                        6,
                        Resources(100, 0, 0, 0),
                        True,
                        "Build level 6",
                        build_href="indexInternal.es?action=internalBuildItem&buildingID=1#1",
                    ),
                ),
            )
            ratios = p_over.storage_fill_ratios()
            for key in ("pig_iron", "metal", "kryptonite", "spice"):
                self.assertTrue(math.isfinite(ratios[key]))
                self.assertGreater(ratios[key], 1.0)
            eq_ratios = replace(p_over, storage=Resources(10**400, 10**400, 10**400, 10**400)).storage_fill_ratios()
            self.assertEqual(eq_ratios, {"pig_iron": 1.0, "metal": 1.0, "kryptonite": 1.0, "spice": 1.0})

            st_over = GameState(
                universe_id=31,
                user_id=1,
                nickname="StorageOverflow",
                timestamp=1_700_000_000.0,
                planets=(p_over,),
                active_planet_id=101,
                researches={"r1": 1},
                speedup_factor=1.0,
            )
            h.bridge._last_state = st_over
            h.bridge._last_recorded_state_obj = st_over
            h.bridge._last_snapshot_at = clock.monotonic()
            h.bridge._last_buildings_fetched = True
            h.bridge._last_home_fetched = True
            h.bridge._last_home_at = clock.monotonic()

            for mode in ("priority", "ga_nn"):
                resp = requests.post(
                    f"{h.base_url}/command",
                    json={"command": "plan", "planner_mode": mode},
                    timeout=10.0,
                )
                self.assertEqual(resp.status_code, 200)
                body = json.loads(resp.text, parse_constant=_reject_nonfinite)
                self.assertTrue(body["ok"])
                p1_wh = [
                    a
                    for a in body["actions"]
                    if a.get("priority") == "P1" and a.get("kind") == "BUILD" and a.get("target_id") == 11
                ]
                self.assertTrue(p1_wh, msg=f"Missing P1 Warehouse action in mode={mode}: {body['actions']}")

        huge = "9" * 5000
        q_jobs = parse_building_queue(
            f"<table><tr><th>Construction queue ( 1 Elements )</th></tr>"
            f"<tr><td>Pig iron mine (Level {huge})</td><td>{huge}:01:02</td></tr></table>"
        )
        self.assertEqual(len(q_jobs), 1)
        self.assertEqual(q_jobs[0].building_id, 1)
        self.assertEqual(q_jobs[0].target_level, 10**200)
        b_offers = parse_buildings_page(
            f'<div class="build-card b-{huge}"><span class="title">Mine (Level {huge})</span></div>'
            f'<table><tr><td><a href="indexInternal.es?action=internalDetail&type=building&id={huge}">Mine</a></td>'
            f'<td>Required: 100 Pig iron</td>'
            f'<td><a class="bau" href="indexInternal.es?action=internalBuildItem&buildingID={huge}#{huge}">Build level {huge}</a></td>'
            f"</tr></table>",
            {},
        )
        self.assertTrue(all(o.building_id <= 10**200 for o in b_offers))
        self.assertIsInstance(
            parse_research_page(
                f'<table><tr><td><a href="indexInternal.es?action=internalDetail&type=research&id={huge}">Mining</a></td>'
                f'<td><a class="bau" href="indexInternal.es?action=internalResearch&researchID={huge}">Research level {huge}</a></td></tr></table>',
                {},
            ),
            tuple,
        )
        self.assertIsInstance(
            parse_unit_factory_page(
                f'<table><tr><td><a href="indexInternal.es?action=internalDetail&type=ship&id={huge}">Ship</a></td>'
                f'<td><form><input name="shipID" value="{huge}"/><input name="count" value="{huge}"/></form></td></tr></table>',
                unit_type="ship",
            ),
            tuple,
        )
        self.assertIsNotNone(
            parse_tutorial_from_home(
                f'<div>Tutorial {huge} / {huge}</div><div id="tutorial.text">Increase mine level {huge} / {huge}'
                f'<form><input name="tutorialComplete" value="{huge}"/></form></div>'
            )
        )
        _, spd_bad, _, _, _ = parse_home_metadata(
            f"<script>SI_TPL.headerLoad={{planetID:{huge}}};</script><div>Up to {huge} points</div><div>active factor: x1.2.3</div>"
        )
        self.assertIsNone(spd_bad)
        self.assertIsInstance(parse_menu_status(f'<a href="x">User ({huge})</a> Points {huge} # {huge}')[0], dict)

        with tempfile.TemporaryDirectory() as cfg_tmp:
            cfg_file = Path(cfg_tmp) / "cfg.yaml"
            save_strategy_config(StrategyConfig(ga_seed=10**5000), cfg_file)  # type: ignore[arg-type]
            reloaded = load_strategy_config(cfg_file)
            self.assertEqual(reloaded.ga_seed, 10**200)

        timer_sec = _parse_timer_seconds(f"{huge}:01:02")
        self.assertTrue(math.isfinite(timer_sec) and 0.0 <= timer_sec <= 3.6e15 + 3600.0)
        self.assertIsNone(_parse_timer_seconds("²:01:02"))
        sim_bad = simulate_action_sequence(
            st_over,
            [
                SimAction(kind="RESEARCH", planet_id=101, target_id=huge),  # type: ignore[arg-type]
                SimAction(kind="BUILD_SHIP", planet_id=101, target_id="²", quantity=1),  # type: ignore[arg-type]
                SimAction(kind="BUILD_DEFENSE", planet_id=101, target_id=10**5000, quantity=1),  # type: ignore[arg-type]
                SimAction(kind="BUILD_SHIP", planet_id=101, target_id=1, quantity=10**5000),  # type: ignore[arg-type]
                SimAction(kind="THROTTLE_BUILDING", planet_id=[1], target_id=10**5000, quantity=10**5000),  # type: ignore[arg-type]
            ],
            config=StrategyConfig(allow_shipyard=True, allow_research=True),
        )
        from spaceinvasion.optimizer import OptimizerRunSummary

        self.assertEqual(sim_bad.valid_steps, 0)
        self.assertEqual(sim_bad.rejected_steps, 5)
        json.dumps([o.rejection_reason for o in sim_bad.step_outcomes], allow_nan=False)
        json.dumps(
            OptimizerRunSummary(
                actions=(),
                best_chromosome=(),
                best_simulation=sim_bad,
                generation_history=(),
                initial_best_fitness=float("nan"),
                final_best_fitness=float("inf"),
                initial_mean_fitness=0.0,
                final_mean_fitness=0.0,
                nn_initial_loss=float("nan"),
                nn_final_loss=0.0,
                nn_weight_delta_norm=float("inf"),
                nn_backend="pure_python",
                seed=1,
                elapsed_ms=float("inf"),
            ).to_dict(),
            allow_nan=False,
        )

    def test_adv_t5_14_bug01_commander_loop_bug02_stop_proc_and_login_sid_redaction(self) -> None:
        from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
        import io
        import subprocess
        import sys
        import threading
        import commander
        import login
        import run_stack
        from spaceinvasion.auth import Credentials, LoginResult, UniverseOption

        routes: dict[str, list[tuple[int, str]]] = {}

        class _LoopMockHandler(BaseHTTPRequestHandler):
            def log_message(self, format, *args) -> None:
                return

            def _reply(self, status: int, body: str) -> None:
                raw = body.encode("utf-8")
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)

            def do_GET(self) -> None:
                path = self.path.split("?", 1)[0]
                seq = routes.get(path)
                if seq:
                    st_code, payload = seq.pop(0) if len(seq) > 1 else seq[0]
                    self._reply(st_code, payload)
                    return
                self._reply(404, '{"ok":false,"error":"not found"}')

            def do_POST(self) -> None:
                length = int(self.headers.get("Content-Length", "0") or "0")
                if length > 0:
                    self.rfile.read(length)
                path = self.path.split("?", 1)[0]
                seq = routes.get(path)
                if seq:
                    st_code, payload = seq.pop(0) if len(seq) > 1 else seq[0]
                    self._reply(st_code, payload)
                    return
                self._reply(404, '{"ok":false,"error":"not found"}')

        srv = ThreadingHTTPServer(("127.0.0.1", 0), _LoopMockHandler)
        t = threading.Thread(target=srv.serve_forever, daemon=True)
        t.start()
        base_url = f"http://127.0.0.1:{srv.server_address[1]}"
        try:
            state_json = json.dumps(
                {
                    "universe_id": 31,
                    "user_id": 1,
                    "nickname": "LoopCheck",
                    "timestamp": 1_700_000_000.0,
                    "active_planet_id": 101,
                    "planets": [
                        {
                            "planet_id": 101,
                            "name": "Home",
                            "coords": "01:010:01",
                            "planet_type": "normal",
                            "temperature": 20,
                            "fields_used": 10,
                            "fields_max": 200,
                            "resources": {"pig_iron": 500, "metal": 100, "kryptonite": 100, "spice": 50},
                            "storage": {"pig_iron": 100_000, "metal": 100_000, "kryptonite": 100_000, "spice": 100_000},
                            "energy": {"produced": 100, "used": 50},
                            "buildings": {"b1": 1},
                        }
                    ],
                    "specs": {"miner": 10**400, "construction_commander": 10**400},
                }
            )
            plan_json = json.dumps({"ok": True, "actions": []})
            corrupt_bot = '{"paused": false, "throttle_cooldowns": {"²": 10.0, "101": ' + ("9" * 500) + "}}"

            with tempfile.TemporaryDirectory() as tmp:
                cfg_p = Path(tmp) / "cfg.yaml"
                cfg_p.write_text("planner_mode: priority\n", encoding="utf-8")
                log_d = Path(tmp) / "logs"

                for err_path, err_resp in (
                    ("/state", (502, '["bad"]')),
                    ("/command", (200, "null")),
                ):
                    routes["/bot_status"] = [(200, corrupt_bot), (200, corrupt_bot)]
                    routes["/state"] = [(200, state_json), (200, state_json)]
                    routes["/command"] = [(200, plan_json), (200, plan_json)]
                    routes[err_path] = [err_resp, (200, state_json if err_path == "/state" else plan_json)]
                    with (
                        patch("commander.time.sleep", return_value=None),
                        patch("sys.stdout", new_callable=io.StringIO) as out_buf,
                    ):
                        rc = commander.main(
                            [
                                "--api-url",
                                base_url,
                                "--config",
                                str(cfg_p),
                                "--log-dir",
                                str(log_d),
                                "--loop",
                                "--max-ticks",
                                "2",
                                "--interval",
                                "0.01",
                            ]
                        )
                    self.assertEqual(rc, 0)
                    self.assertIn("tick 1", out_buf.getvalue())
                    self.assertIn("tick 2", out_buf.getvalue())
        finally:
            srv.shutdown()
            srv.server_close()
            t.join(timeout=5.0)

        env_clean = os.environ.copy()
        env_clean["SPACEINVASION_NO_REEXEC"] = "1"
        env_clean["PYTHONDONTWRITEBYTECODE"] = "1"
        sub = subprocess.run(
            [
                sys.executable,
                "-c",
                "import sys, commander; assert not any(m == 'selenium' or m.startswith('selenium.') for m in sys.modules)",
            ],
            env=env_clean,
            capture_output=True,
            text=True,
            timeout=15,
            check=False,
        )
        self.assertEqual(sub.returncode, 0, msg=sub.stderr)

        # BUG-02: run_stack._stop_proc reaps a child that ignores SIGTERM cleanly with 0 ResourceWarning
        proc = subprocess.Popen(
            [
                sys.executable,
                "-c",
                (
                    "import signal, sys, time\n"
                    "signal.signal(signal.SIGTERM, signal.SIG_IGN)\n"
                    "sys.stdout.write('ready\\n')\n"
                    "sys.stdout.flush()\n"
                    "time.sleep(30)\n"
                ),
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        try:
            ready = proc.stdout.readline().strip() if proc.stdout is not None else ""
            self.assertEqual(ready, "ready")
            run_stack._stop_proc(proc)
            self.assertIsNotNone(proc.poll())
        finally:
            for strm in (proc.stdout, proc.stderr, proc.stdin):
                if strm is not None:
                    try:
                        strm.close()
                    except Exception:
                        pass
            if proc.poll() is None:
                proc.kill()
                proc.wait(timeout=5.0)

        # login.py CLI redacts sid= while in-memory LoginResult.final_url and UniverseOption.url retain tokens
        uni_opt = UniverseOption(
            label="Genesis",
            url="https://u31.spaceinvasion.bitmeup.com/indexInternal.es?sid=UNI_SECRET_T5",
            universe_id=31,
            speed=1.0,
            flight_speed=1.0,
            has_account=True,
        )
        login_res = LoginResult(
            account_name="default",
            email="cmd@example.com",
            landing_url="https://bitmeup.com/?sid=LANDING_SECRET",
            final_url="https://spaceinvasion.bitmeup.com/indexInternal.es?sid=FINAL_SECRET_T5",
            page_title="SpaceInvasion sid=TITLE_SECRET",
            cookie_path=Path("state/cookie.json"),
            cookie_names=("PHPSESSID",),
            universes=(uni_opt,),
        )
        with (
            patch("login.load_credentials", return_value=Credentials("cmd@example.com", "pw")),
            patch("login.login", return_value=login_res),
        ):
            for argv in (["--mode", "http", "--json"], ["--mode", "http"]):
                with patch("sys.stdout", new_callable=io.StringIO) as cli_out:
                    rc = login.main(argv)
                self.assertEqual(rc, 0)
                rendered = cli_out.getvalue()
                self.assertNotIn("FINAL_SECRET_T5", rendered)
                self.assertNotIn("UNI_SECRET_T5", rendered)
                self.assertNotIn("LANDING_SECRET", rendered)
                self.assertNotIn("TITLE_SECRET", rendered)
                self.assertIn("sid=REDACTED", rendered)
        self.assertIn("sid=FINAL_SECRET_T5", login_res.final_url)
        self.assertIn("sid=UNI_SECRET_T5", uni_opt.url)

    def test_adv_t5_15_bug03_non_dict_json_and_neg_zero_rate_in_ui_node(self) -> None:
        self.assertIsNotNone(shutil.which("node"), "Node.js is required for UI runtime verification")
        clock = FakeClock()
        with E2ETestHarness(clock=clock) as h:
            patch_js = """
async (url) => {
  const mp = globalThis.__t5Mock;
  if (!mp || (url !== "/state" && url !== "/command")) return undefined;
  if (mp.jsonOnly) {
    return { ok: mp.ok, status: mp.status, async json() { return mp.jsonVal; } };
  }
  return { ok: mp.ok, status: mp.status, async text() { return mp.body; } };
}
"""
            ui_res = run_ui_in_node(
                h.base_url,
                """
                const loaded = await __waitUntil(() => state && state.dashboard !== null, 4000);
                if (!loaded) throw new Error('Dashboard failed to load');

                const mocks = [
                  { ok: true, status: 200, body: "null" },
                  { ok: false, status: 500, body: "null" },
                  { ok: false, status: 502, body: '["bad"]' },
                  { ok: true, status: 200, body: "[]" },
                  { ok: false, status: 429, body: '["rate"]' },
                  { ok: true, status: 200, jsonOnly: true, jsonVal: null },
                  { ok: false, status: 500, jsonOnly: true, jsonVal: [] },
                ];
                const apiResults = [];
                for (const mp of mocks) {
                  globalThis.__t5Mock = mp;
                  let getErr = null;
                  try { await apiGet("/state"); } catch (e) {
                    getErr = { isTypeErr: e instanceof TypeError, msg: String(e.message || e), rateLimited: Boolean(e.rateLimited) };
                  }
                  let cmdErr = null;
                  try { await apiCommand({ command: "plan" }); } catch (e) {
                    cmdErr = { isTypeErr: e instanceof TypeError, msg: String(e.message || e), rateLimited: Boolean(e.rateLimited) };
                  }
                  apiResults.push({ status: mp.status, getErr, cmdErr });
                }

                // Trigger real UI handler on 429 ["rate"] via #btn-tick-now
                globalThis.__t5Mock = { ok: false, status: 429, body: '["rate"]' };
                document.getElementById("btn-tick-now").click();
                await __waitUntil(() => {
                  const b = document.getElementById("feedback-banner");
                  return b && !b.hidden && b.className.includes("is-warn");
                }, 3000);
                const bannerClass = document.getElementById("feedback-banner").className;
                const bannerText = document.getElementById("feedback-banner").textContent;
                globalThis.__t5Mock = null;

                renderResourceBar("pig", 0, 1000, -0.1);
                return {
                  apiResults,
                  bannerClass,
                  bannerText,
                  fmtRateNegPointOne: fmtRate(-0.1),
                  fmtIntNegZero: fmtInt(-0),
                  pigRateText: document.getElementById("rate-pig").textContent,
                  pigRateClass: document.getElementById("rate-pig").className,
                };
                """,
                fetch_patch_js=patch_js,
            )
            self.assertEqual(ui_res["errors"], [])
            sc = ui_res["scenario"]
            for entry in sc["apiResults"]:
                for key in ("getErr", "cmdErr"):
                    err = entry[key]
                    self.assertIsNotNone(err)
                    self.assertFalse(err["isTypeErr"])
                    self.assertNotIn("Cannot read properties of null", err["msg"])
                    if entry["status"] == 429:
                        self.assertTrue(err["rateLimited"])
            self.assertIn("is-warn", sc["bannerClass"])
            self.assertEqual(sc["fmtRateNegPointOne"], "0/h")
            self.assertEqual(sc["fmtIntNegZero"], "0")
            self.assertEqual(sc["pigRateText"], "0/h")
            self.assertNotIn("is-neg", sc["pigRateClass"])

    def test_adv_t5_16_m5_r6_domain_runtime_boundary_and_lifecycle_hardening(self) -> None:
        import gc
        import socket
        import subprocess
        import sys
        import time
        import urllib.request
        from urllib.parse import urlparse
        import commander
        import scripts.sync_wiki_data as sync_wiki_data
        from spaceinvasion import browser, game_data
        from spaceinvasion.api_server import MAX_REQUEST_BYTES
        from spaceinvasion.auth import SpaceInvasionAuthError
        from spaceinvasion.browser import BrowserConfig
        from spaceinvasion.config import load_strategy_config, save_strategy_config, strategy_config_from_dict
        from spaceinvasion.optimizer import (
            STATE_FEATURE_DIM,
            PurePythonMLP,
            _SimState,
            create_value_network,
            encode_empire_state,
        )
        from spaceinvasion.state import game_state_from_dict

        def _reject_nonfinite(tok: str) -> object:
            raise ValueError(f"Non-finite JSON token: {tok!r}")

        # GAP-02: resolve_unit & dependent helpers safe on 10**5000 + sync_wiki_data parity
        for cat in (None, "building", "buildings", "research", "ships", "defense"):
            self.assertIsNone(game_data.resolve_unit(cat, 10**5000))  # type: ignore[arg-type]
        self.assertEqual(game_data.building_level_stats(10**5000, 1), {})  # type: ignore[arg-type]
        self.assertEqual(game_data.mine_energy_needed(10**5000, 1), 0)  # type: ignore[arg-type]
        self.assertEqual(game_data.building_energy_delta(10**5000, 1), 0)  # type: ignore[arg-type]
        self.assertEqual(
            game_data.missing_prerequisites("building", 10**5000, buildings={}, researches={}),  # type: ignore[arg-type]
            [],
        )
        rendered_gd = sync_wiki_data.render_module(
            {
                "synced_at_utc": game_data.WIKI_SYNCED_AT_UTC,
                "source": game_data.WIKI_SOURCE_URL,
                "merchant_rates_in_spice": game_data.MERCHANT_RATES_IN_SPICE,
                "buildings": game_data.BUILDINGS,
                "researches": game_data.RESEARCHES,
                "ships": game_data.SHIPS,
                "defenses": game_data.DEFENSES,
            }
        )
        self.assertEqual(rendered_gd, Path("spaceinvasion/game_data.py").read_text(encoding="utf-8"))

        # GAP-05: YAML roundtrip preserves '#' and ':' in playstyle without truncating comments
        with tempfile.TemporaryDirectory() as tmp_cfg:
            for ps in ("miner#pro", "a:b # c"):
                p_cfg = Path(tmp_cfg) / "cfg.yaml"
                save_strategy_config(strategy_config_from_dict({"playstyle": ps}), p_cfg)
                self.assertEqual(load_strategy_config(p_cfg).playstyle, ps)

        # GAP-03 & GAP-04: _SimState.to_game_state finite timestamp & PurePythonMLP.from_dict finite predict
        gs_ts = make_game_state(
            planets=(make_planet(buildings={"b1": 2}, auto_offers=True),),
            timestamp=float("nan"),
        )
        self.assertTrue(math.isfinite(_SimState.from_game_state(gs_ts).to_game_state().timestamp))
        gs_ts_big = replace(gs_ts, timestamp=10**5000)  # type: ignore[arg-type]
        res_ts_big = run_optimizer(gs_ts_big, generations=1, population_size=4, horizon_steps=2, seed=1)
        self.assertTrue(math.isfinite(res_ts_big.best_simulation.final_state.timestamp))

        net_d = create_value_network("python", seed=42).to_dict()
        net_d["weights"][0][0][0] = float("nan")
        net_d["biases"][0][0] = float("inf")
        self.assertTrue(math.isfinite(PurePythonMLP.from_dict(net_d).predict([1.0] * STATE_FEATURE_DIM)))

        # GAP-M5-01: browser cleanup reaps SIGTERM-ignoring subprocess cleanly with 0 ResourceWarning
        with tempfile.TemporaryDirectory() as tmp_proc:
            for idx, mode in enumerate(("close", "attach_err"), start=1):
                flag_f = Path(tmp_proc) / f"sigign_{idx}.flag"
                child_code = (
                    "import signal, time; "
                    "signal.signal(signal.SIGTERM, signal.SIG_IGN); "
                    f"open({str(flag_f)!r}, 'w', encoding='utf-8').write('1'); "
                    "time.sleep(60)"
                )
                proc = subprocess.Popen(
                    [sys.executable, "-c", child_code],
                    stdin=subprocess.DEVNULL,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
                try:
                    deadline = time.monotonic() + 5.0
                    while not flag_f.exists() and time.monotonic() < deadline:
                        time.sleep(0.02)
                    self.assertTrue(flag_f.exists())
                    if mode == "close":
                        browser.close_or_detach_driver(MagicMock(), proc, keep_running=False)
                    else:
                        with (
                            patch("spaceinvasion.browser.is_debug_port_open", return_value=False),
                            patch("spaceinvasion.browser.launch_chrome_with_debug_port", return_value=proc),
                            patch("spaceinvasion.browser.attach_chrome_driver", side_effect=SpaceInvasionAuthError("attach fail")),
                        ):
                            with self.assertRaises(SpaceInvasionAuthError):
                                browser.open_browser_session(
                                    BrowserConfig(profile_dir=Path(tmp_proc) / "prof", debug_port=19996)
                                )
                    self.assertIsNotNone(proc.poll())
                finally:
                    if proc.poll() is None:
                        proc.kill()
                    proc.wait(timeout=5.0)
                del proc
                gc.collect()

        # GAP-01 (via live E2ETestHarness POST /command plan ga_nn), GAP-M5-02 (413/400 + /favicon.ico 204), GAP-M5-03
        clock = FakeClock()
        with E2ETestHarness(clock=clock) as h:
            huge_bkey = "b" + ("9" * 5000)
            gs_b5k = game_state_from_dict(
                {
                    "universe_id": 31,
                    "user_id": 1,
                    "nickname": "BKey5K",
                    "timestamp": 1_700_000_000.0,
                    "active_planet_id": 101,
                    "planets": [
                        {
                            "planet_id": 101,
                            "name": "Home",
                            "coords": "01:010:01",
                            "buildings": {"b1": 2, huge_bkey: 5},
                            "resources": {"pig_iron": 5000, "metal": 3000, "kryptonite": 2000, "spice": 500},
                            "storage": {"pig_iron": 100_000, "metal": 100_000, "kryptonite": 100_000, "spice": 100_000},
                            "energy": {"produced": 200, "used": 50},
                        }
                    ],
                }
            )
            self.assertTrue(all(math.isfinite(f) for f in encode_empire_state(gs_b5k)))
            h.bridge._last_state = gs_b5k
            h.bridge._last_recorded_state_obj = gs_b5k
            h.bridge._last_snapshot_at = clock.monotonic()
            h.bridge._last_buildings_fetched = True
            h.bridge._last_home_fetched = True
            h.bridge._last_home_at = clock.monotonic()

            resp = requests.post(
                f"{h.base_url}/command",
                json={"command": "plan", "planner_mode": "ga_nn"},
                timeout=10.0,
            )
            self.assertEqual(resp.status_code, 200)
            plan_body = json.loads(resp.text, parse_constant=_reject_nonfinite)
            self.assertTrue(plan_body["ok"])

            with urllib.request.urlopen(f"{h.base_url}/favicon.ico", timeout=5.0) as fav:
                self.assertEqual(fav.status, 204)

            parsed_url = urlparse(h.base_url)
            host = parsed_url.hostname or "127.0.0.1"
            port = parsed_url.port or 80

            def _send_raw_cl(cl_val: str) -> tuple[bytes, dict]:
                with socket.create_connection((host, port), timeout=5.0) as sock:
                    sock.sendall(
                        f"POST /command HTTP/1.1\r\nHost: {host}\r\nContent-Length: {cl_val}\r\nConnection: close\r\n\r\n".encode(
                            "ascii"
                        )
                    )
                    chunks: list[bytes] = []
                    while True:
                        chunk = sock.recv(4096)
                        if not chunk:
                            break
                        chunks.append(chunk)
                raw = b"".join(chunks)
                head, _, body_b = raw.partition(b"\r\n\r\n")
                return head.split(b"\r\n", 1)[0], json.loads(body_b.decode("utf-8"), parse_constant=_reject_nonfinite)

            st_413, b_413 = _send_raw_cl("9999999999999")
            self.assertIn(b"413", st_413)
            self.assertFalse(b_413["ok"])
            self.assertIn(str(MAX_REQUEST_BYTES), b_413["error"])

            for bad_cl in ("9" * 5000, "-5", "abc"):
                st_400, b_400 = _send_raw_cl(bad_cl)
                self.assertIn(b"400", st_400)
                self.assertFalse(b_400["ok"])

        # GAP-M5-03: direct commander & executor 10**5000 safety
        cmd_payload = commander._action_to_command_payload(
            PlannedAction("P2", "BUILD_SHIP", 10**5000, "01:010:01", "Small transport", "Adv", target_id=1, target_level=10**5000),
            dry_run=True,
        )
        json.dumps(cmd_payload, allow_nan=False)
        ex = ActionExecutor(dry_run=True)
        cl = MagicMock(spec=GameBrowserClient)
        cl.driver = MagicMock()
        self.assertEqual(ex._select_planet_if_needed(cl, 10**5000, html="<select name='planet'></select>"), (False, None))

    def test_adv_t5_17_m5_r7_ui_race_and_corrupt_pct_api_unicode_and_lifecycle_parity(self) -> None:
        import ast
        from pathlib import Path
        import requests
        from spaceinvasion.config import load_strategy_config, save_strategy_config, StrategyConfig
        from spaceinvasion.optimizer import NeuralValueNetwork, PurePythonMLP

        self.assertIsNotNone(shutil.which("node"), "Node.js is required")

        # 1. Repo-wide Check 2 AST allow_nan=False & 0 duplicate tests invariant
        non_compliant: list[str] = []
        prod_py = sorted(
            [*Path(".").glob("*.py"), *Path("spaceinvasion").rglob("*.py"), *Path("scripts").rglob("*.py")]
        )
        for py_p in prod_py:
            tree = ast.parse(py_p.read_text(encoding="utf-8"), str(py_p))
            for node in ast.walk(tree):
                if (
                    isinstance(node, ast.Call)
                    and isinstance(node.func, ast.Attribute)
                    and node.func.attr in ("dump", "dumps")
                    and isinstance(node.func.value, ast.Name)
                    and node.func.value.id == "json"
                ):
                    ok = any(
                        kw.arg == "allow_nan"
                        and isinstance(kw.value, ast.Constant)
                        and kw.value.value is False
                        for kw in node.keywords
                    )
                    if not ok:
                        non_compliant.append(f"{py_p}:{node.lineno}")
        self.assertEqual(non_compliant, [])

        dup_tests: list[str] = []
        for test_p in sorted([*Path("tests").glob("test_*.py"), *Path("e2e_tests").glob("test_*.py")]):
            t_ast = ast.parse(test_p.read_text(encoding="utf-8"), str(test_p))
            for cls_node in t_ast.body:
                if isinstance(cls_node, ast.ClassDef):
                    seen_m: set[str] = set()
                    for m_node in cls_node.body:
                        if isinstance(m_node, ast.FunctionDef):
                            if m_node.name in seen_m:
                                dup_tests.append(f"{test_p}:{cls_node.name}.{m_node.name}")
                            seen_m.add(m_node.name)
        self.assertEqual(dup_tests, [])

        # 2. GAP-R7-01 & GAP-R7-02 end-to-end checks
        for bad_nn in (None, {}, {"layer_sizes": ["9" * 5000, 1]}, {"layer_sizes": [4, 2, 1], "seed": float("nan")}, {"layer_sizes": [4, 1], "weights": {1: 2}}):
            net = NeuralValueNetwork.from_dict(bad_nn, backend="python")  # type: ignore[arg-type]
            json.dumps(net.to_dict(), allow_nan=False)
        json.dumps(PurePythonMLP(layer_sizes=(4, -1, 1), seed=10**5000).to_dict(), allow_nan=False)

        with tempfile.TemporaryDirectory() as tmp_cfg:
            cfg_p = Path(tmp_cfg) / "r7.yaml"
            save_strategy_config(
                StrategyConfig(
                    playstyle="line1\u2028line2",
                    allow_raids=False,
                    shipyard_targets={"unit#1": 5, "a:b": 3, " 12 ": 4, "line1\x0bline2": 7, "small-fighter": 99},
                ),
                cfg_p,
            )
            reloaded_cfg = load_strategy_config(cfg_p)
            self.assertEqual(reloaded_cfg.playstyle, "line1\u2028line2")
            self.assertFalse(reloaded_cfg.allow_raids)
            self.assertEqual(reloaded_cfg.shipyard_targets["unit#1"], 5)
            self.assertEqual(reloaded_cfg.shipyard_targets["a:b"], 3)
            self.assertEqual(reloaded_cfg.shipyard_targets[" 12 "], 4)
            self.assertEqual(reloaded_cfg.shipyard_targets["small-fighter"], 99)

        # 3. Live E2ETestHarness HTTP API Unicode rejection + invalid fleetsave_plan + UI save race & corrupt pct
        clock = FakeClock()
        with E2ETestHarness(clock=clock) as h:
            for bad_cmd in (
                {"command": "spy", "target_coords": "\u0660\u0662:\u0663\u0668\u0660:\u0660\u0664"},
                {"command": "scan_galaxy", "galaxy": "\u0662", "system": 1},
                {"command": "fleetsave_plan", "fuel_reserve_spice": "bad"},
            ):
                r_bad = requests.post(f"{h.base_url}/command", json=bad_cmd, timeout=5)
                self.assertEqual(r_bad.status_code, 400, msg=r_bad.text)

            patch_js = """
async (url, opts, fullUrl, nativeFetch) => {
  if (globalThis.__t5R7Dash === 'hold_prio' && url.startsWith('/dashboard')) {
    globalThis.__t5R7Dash = 'normal';
    const r = await nativeFetch(fullUrl, opts);
    const d = await r.json();
    d.config = { ...(d.config || {}), planner_mode: 'priority', ga_seed: 42 };
    d.planner_mode = 'priority';
    return new Promise((resolve) => {
      globalThis.__resolveT5Dash = () => resolve({ ok: true, status: 200, json: async () => d });
    });
  }
  if (globalThis.__t5R7Dash === 'fail_503' && url.startsWith('/dashboard')) {
    return { ok: false, status: 503, json: async () => ({ ok: false, error: 'down' }) };
  }
}
"""
            ui_res = run_ui_in_node(
                h.base_url,
                """
                const okLoad = await __waitUntil(() => state && state.dashboard !== null, 4000);
                if (!okLoad) throw new Error('Initial dashboard timeout');

                const fmtSmallNeg = fmtInt(-0.0001);
                const imprUndef = fmtImprovementPct(null, undefined, undefined);
                const bfFallback = getSelectedBlastFurnacePct({
                  state: { planets: [{ planet_id: 1, economy_rates: { resource2: 'bad' } }] },
                  buildings_eta: { planet: { planet_id: 1 }, planet_economy: { blast_furnace_pct: 0 } },
                });

                el('cfg-planner_mode').value = 'ga_nn';
                el('cfg-ga_seed').value = '77';
                globalThis.__t5R7Dash = 'hold_prio';
                const pDash = loadDashboard({ refresh: false, preserveConfig: true, force: true });
                await __waitUntil(() => typeof globalThis.__resolveT5Dash === 'function');

                globalThis.__t5R7Dash = 'fail_503';
                el('btn-save-config').click();
                await __sleep(20);
                globalThis.__resolveT5Dash();
                await pDash;
                await __waitUntil(() => state.dashboard?.config?.planner_mode === 'ga_nn' && state.dashboard?.config?.ga_seed === 77);
                await __sleep(30);

                return {
                  fmtSmallNeg,
                  imprUndef,
                  bfFallback,
                  cfgMode: state.dashboard?.config?.planner_mode,
                  cfgSeed: state.dashboard?.config?.ga_seed,
                  domMode: el('cfg-planner_mode').value,
                };
                """,
                fetch_patch_js=patch_js,
            )
            self.assertEqual(ui_res["errors"], [])
            sc = ui_res["scenario"]
            self.assertEqual(sc["fmtSmallNeg"], "0")
            self.assertEqual(sc["imprUndef"], "—")
            self.assertEqual(sc["bfFallback"], 0)
            self.assertEqual(sc["cfgMode"], "ga_nn")
            self.assertEqual(sc["cfgSeed"], 77)
            self.assertEqual(sc["domMode"], "ga_nn")

    def test_adv_t5_18_m5_r8_keepalive_redaction_arabic_float_mlp_horizon_and_ui_guards(self) -> None:
        import json
        import math
        from unittest.mock import MagicMock, patch
        import requests
        import commander
        from spaceinvasion.api_server import ApiRequestError
        from spaceinvasion.client import PageViewLimiter
        from spaceinvasion.commander_client import GameApiClient, GameApiError
        from spaceinvasion.config import StrategyConfig, load_strategy_config
        from spaceinvasion.executor import ActionExecutor
        from spaceinvasion.fleet import normalize_coords
        from spaceinvasion.optimizer import NeuralValueNetwork, simulate_action_sequence
        from spaceinvasion.ranking import build_buildings_eta
        from spaceinvasion.state import game_state_from_dict
        from spaceinvasion.strategy import PlannedAction, plan_actions

        # 1. Domain boundary regressions (config mid-token quotes, non-ASCII coords, ranking horizon, MLP train, sim horizon)
        with tempfile.TemporaryDirectory() as tmp:
            cfg_p = Path(tmp) / "r8.yaml"
            cfg_p.write_text(
                "playstyle: Bob's miner # fav\n"
                "shipyard_targets:\n"
                "  unit'1: 5 # inline\n"
                "  small-fighter: 99\n",
                encoding="utf-8",
            )
            cfg_r8 = load_strategy_config(cfg_p)
            self.assertEqual(cfg_r8.playstyle, "bob's miner")
            self.assertEqual(cfg_r8.shipyard_targets["unit'1"], 5)

        self.assertEqual(normalize_coords("\u0660\u0662:\u0663\u0668\u0660:\u0660\u0664"), "\u0660\u0662:\u0663\u0668\u0660:\u0660\u0664")
        gs_sup = game_state_from_dict(
            {
                "planets": [
                    {
                        "planet_id": 1,
                        "name": "H",
                        "coords": "1:²:3",
                        "resources": {"pig_iron": 10_000, "metal": 5_000, "kryptonite": 5_000, "spice": 2_000},
                        "storage": {"pig_iron": 100_000, "metal": 100_000, "kryptonite": 100_000, "spice": 100_000},
                        "energy": {"produced": 200, "used": 50},
                        "buildings": {"b1": 5, "b5": 3},
                    }
                ],
                "raid_targets": [{"coords": "1:³:4", "name": "T", "is_inactive": True}],
            }
        )
        self.assertIsInstance(plan_actions(gs_sup, config=StrategyConfig(allow_raids=True), now_ts=1700000000.0), list)
        self.assertEqual(len(build_buildings_eta(gs_sup, horizon_count=10_000)["buildings"][0]["horizons"]), 100)
        sim_h = simulate_action_sequence(gs_sup, [], horizon_hours=10**5000, attack_history_path="")  # type: ignore[arg-type]
        self.assertTrue(math.isfinite(sim_h.elapsed_seconds))
        self.assertGreater(sim_h.final_state.planets[0].resources.pig_iron, 10_000)

        net_r8 = NeuralValueNetwork.from_dict({"layer_sizes": [4, 10**8]}, backend="python")
        self.assertTrue(math.isfinite(net_r8.train_step([1.0, 2.0, 3.0, 4.0], 0.5)))

        lim_r8 = PageViewLimiter(max_views_per_minute=float("nan"))  # type: ignore[arg-type]
        self.assertEqual(lim_r8.acquire(cost=float("nan")), 0.0)  # type: ignore[arg-type]
        ex_r8 = ActionExecutor(max_limiter_wait=10**400)
        self.assertTrue(ex_r8.max_limiter_wait is not None and math.isfinite(ex_r8.max_limiter_wait))

        # 2. Live E2ETestHarness HTTP API Arabic-Indic float rejection + direct huge-int ApiRequestError + UI guards
        clock = FakeClock()
        held_err: Exception | None = None
        try:
            with E2ETestHarness(clock=clock) as h:
                for bad_cmd in (
                    {"command": "update_config", "config": {"storage_warn_ratio": "\u0660.\u0665"}},
                    {"command": "simulate_combat", "attacker_ships": {"s4": 1}, "safety_margin": "\u0662"},
                ):
                    r_bad = requests.post(f"{h.base_url}/command", json=bad_cmd, timeout=5)
                    self.assertEqual(r_bad.status_code, 400, msg=r_bad.text)

                with self.assertRaises(ApiRequestError):
                    h.bridge.handle_command({"command": 10**5000})

                try:
                    with GameApiClient(base_url=h.base_url) as cli:
                        cli.send_command({"command": "simulate_combat", "safety_margin": "\u0662"})
                    self.fail("Expected GameApiError")
                except GameApiError as exc:
                    held_err = exc
                self.assertIsNotNone(held_err)

                wait_act = PlannedAction(
                    priority="P2",
                    kind="BUILD",
                    planet_id=1,
                    planet_coords="01:001:01",
                    target_name="Mine ?sid=T5_WAIT_SID",
                    reason="Wait ?sid=T5_REASON_SID",
                    action_href="indexInternal.es?sid=T5_HREF_SID",
                    executable_now=False,
                )
                fake_cli = MagicMock(spec=GameApiClient)
                fake_cli.get_state.return_value = gs_sup
                with patch("commander.plan_actions", return_value=[wait_act]):
                    _, _, wait_res, _, _ = commander.run_commander_tick(fake_cli, live=False, max_actions=1)
                assert wait_res is not None
                wait_json = json.dumps(wait_res, allow_nan=False)
                for s_tok in ("T5_WAIT_SID", "T5_REASON_SID", "T5_HREF_SID"):
                    self.assertNotIn(s_tok, wait_json)

                ui_patch = """
async (url, opts, fullUrl, nativeFetch) => {
  if (url === '/command' && opts?.body) {
    const p = JSON.parse(opts.body);
    if (p.command === 'update_config' && globalThis.__t5R8HoldSave) {
      return new Promise((resolve) => {
        globalThis.__resolveT5R8Save = () => resolve({
          ok: false,
          status: 500,
          json: async () => ({ ok: false, error: 'transient fail' }),
        });
      });
    }
  }
  if (url.startsWith('/dashboard') && globalThis.__t5R8DashPrio) {
    const r = await nativeFetch(fullUrl, opts);
    const d = await r.json();
    d.config = { ...(d.config || {}), planner_mode: 'priority' };
    d.planner_mode = 'priority';
    return { ok: true, status: 200, json: async () => d };
  }
}
"""
                ui_res = run_ui_in_node(
                    h.base_url,
                    """
                    const okLoad = await __waitUntil(() => state && state.dashboard !== null, 4000);
                    if (!okLoad) throw new Error('Dashboard timeout');
                    renderBuildingsView({
                      ...state.dashboard,
                      buildings_eta: {
                        planet: state.dashboard.buildings_eta?.planet || { planet_id: 1 },
                        planet_economy: state.dashboard.buildings_eta?.planet_economy || {},
                        buildings: [{
                          building_id: 1, key: 'b1', name: 'Pig iron mine', category: 'mines',
                          current_level: 1, next_level: 2, missing_total: 0, build_time_text: '10s',
                          wait_eta: 'Ready', reach_eta: '10s', wait_hours: 0, bottleneck: '   ',
                          can_build_live: false, offer_visible: true, unmet_prerequisites: [],
                        }],
                        researches: [],
                      },
                    });
                    const bldText = el('eta-table-tbody').textContent;

                    el('cfg-planner_mode').value = 'ga_nn';
                    state.configDirty = false;
                    globalThis.__t5R8HoldSave = true;
                    globalThis.__t5R8DashPrio = true;
                    el('btn-save-config').click();
                    await __waitUntil(() => typeof globalThis.__resolveT5R8Save === 'function');
                    await loadDashboard({ refresh: false, preserveConfig: true, force: true });
                    const domMid = el('cfg-planner_mode').value;
                    globalThis.__resolveT5R8Save();
                    await __waitUntil(() => state.configSaving === 0 && el('feedback-banner').textContent.includes('Failed saving config'));
                    const domAfterFail = el('cfg-planner_mode').value;
                    return { bldText, domMid, domAfterFail };
                    """,
                    fetch_patch_js=ui_patch,
                )
                self.assertEqual(ui_res["errors"], [])
                self.assertNotIn("()", ui_res["scenario"]["bldText"])
                self.assertEqual(ui_res["scenario"]["domMid"], "ga_nn")
                self.assertEqual(ui_res["scenario"]["domAfterFail"], "ga_nn")
        finally:
            held_err = None

    def test_adv_t5_19_r9_priority_asteroid_full_field_defender_research_rate_limit_and_ascii_parity(self) -> None:
        import run_bot
        from spaceinvasion.api_server import ApiRequestError
        from spaceinvasion.client import RateLimitError
        from spaceinvasion.config import normalize_planner_mode, strategy_config_from_dict
        from spaceinvasion.fleet import (
            _parse_coords_distance,
            extract_galaxy_system_coords,
            parse_galaxy_system_html,
            parse_spy_report_html,
        )
        from spaceinvasion.optimizer import _coords_distance
        from spaceinvasion.state import (
            BuildingOffer,
            parse_economy_rates,
            parse_planets_overview_json,
            parse_si_int,
        )
        from spaceinvasion.storage import _coord_query_candidates

        # 1. GAP-R9-01: priority mode obeys Asteroid REQ-BLD-003 and 0-free-field limit
        ast_p = PlanetState(
            planet_id=99,
            name="Ast",
            coords="02:380:05",
            planet_type="asteroid",
            temperature=-40,
            fields_used=5,
            fields_max=20,
            resources=Resources(99_999, 99_999, 99_999, 99_999),
            storage=Resources(1_000, 1_000, 1_000, 1_000),
            energy=EnergyBalance(0, 10),
            buildings={"b19": 1},
            building_offers=(
                BuildingOffer(1, "Pig iron mine", 0, 1, Resources(78, 0, 25, 0), True, "Build"),
                BuildingOffer(5, "Fusion plant", 0, 1, Resources(80, 0, 35, 0), True, "Build"),
                BuildingOffer(11, "Pig iron warehouse", 0, 1, Resources(2000, 800, 0, 0), True, "Build"),
            ),
        )
        full_home = PlanetState(
            planet_id=1,
            name="Home",
            coords="02:380:12",
            planet_type="normal",
            temperature=20,
            fields_used=200,
            fields_max=200,
            resources=Resources(500_000, 500_000, 500_000, 500_000),
            storage=Resources(100_000, 100_000, 100_000, 100_000),
            energy=EnergyBalance(100, 200),
            buildings={"b1": 10, "b2": 8, "b3": 8, "b4": 7, "b5": 10, "b7": 3, "b8": 3, "b9": 3},
            building_offers=(
                BuildingOffer(5, "Fusion plant", 10, 11, Resources(1000, 1000, 500, 0), True, "Build", "h5"),
                BuildingOffer(11, "Pig iron warehouse", 1, 2, Resources(1000, 500, 0, 0), True, "Build", "h11"),
            ),
        )
        gs_r9 = GameState(31, 1, "u", 1_700_000_000.0, (full_home, ast_p), active_planet_id=1)
        acts_prio = plan_actions(gs_r9, config=StrategyConfig(planner_mode="priority", allow_multi_planet=True))
        self.assertEqual([a for a in acts_prio if a.planet_id == 99 and a.target_id in {1, 2, 3, 4, 5, 6, 8, 11, 12, 13, 14}], [])
        self.assertEqual([a for a in acts_prio if a.planet_id == 1 and a.kind == "BUILD"], [])

        # 2. GAP-R9-03 & GAP-R9-04: Arabic-Indic parser parity + negative level clamp
        ar = "\u0660\u0662:\u0663\u0668\u0660:\u0660\u0664"
        self.assertEqual(_coords_distance(ar, "02:380:05"), 100.0)
        self.assertEqual(_coords_distance(ar, "02:380:05"), _parse_coords_distance(ar, "02:380:05"))
        self.assertEqual(parse_si_int("\u0662.\u0660\u0660\u0660"), 0)
        self.assertEqual(parse_economy_rates('<select name="resource\u0662"><option selected value="80">80%</option></select>'), {})
        self.assertEqual(extract_galaxy_system_coords('<input name="galaxy" value="\u0662"><input name="system" value="\u0663\u0668\u0660">', default=(9, 99)), (9, 99))
        self.assertEqual(parse_galaxy_system_html('<table><tr><td class="pos">1</td><td>P</td></tr></table>', url="?galaxy=\u0662&system=\u0663\u0668\u0660")[0].coords, "01:001:01")
        self.assertEqual(parse_spy_report_html(f"<div>Target [{ar}] Pig iron: \u0665\u0660\u0660</div>").resources.pig_iron, 0)
        self.assertEqual(_coord_query_candidates(ar), (ar, [ar], None))
        neg_gs = parse_planets_overview_json(json.dumps({"planets": [{"id": 1, "name": "P", "coords": "1:2:3", "fields": -5, "buildings": {"b1": {"level": -10}}}]}, allow_nan=False))
        neg_p = neg_gs.active_planet()
        assert neg_p is not None
        self.assertEqual(neg_p.fields_used, 0)
        self.assertEqual(neg_p.fields_max, 0)

        # 3. playstyle 10**5000 & run_bot RateLimitError isolation
        self.assertEqual(strategy_config_from_dict({"playstyle": 10**5000}).playstyle, "fleeter")
        self.assertEqual(normalize_planner_mode(10**5000), "priority")
        fc_rl = MagicMock()
        fc_rl.__enter__.return_value = fc_rl
        fc_rl.ensure_in_game.side_effect = RateLimitError("throttle", retry_after=55.0)
        with (
            patch("run_bot.is_debug_port_open", return_value=True),
            patch("run_bot.GameBrowserClient.attach", return_value=fc_rl),
            patch("run_bot.login_with_browser") as mock_login,
        ):
            with self.assertRaises(RateLimitError):
                run_bot.ensure_debug_browser_ready(debug_port=19998, env_file=Path(".env"), account="default")
        mock_login.assert_not_called()

        # 4. Live E2ETestHarness HTTP API: simulate_combat defender_researches & playstyle validation & log redaction
        with E2ETestHarness() as h:
            r0 = requests.post(
                f"{h.base_url}/command",
                json={"command": "simulate_combat", "attacker_ships": {"s4": 16}, "defender_ships": {"s4": 10}, "defender_researches": {"r8": 0, "r9": 0, "r10": 0}},
                timeout=5,
            )
            r15 = requests.post(
                f"{h.base_url}/command",
                json={"command": "simulate_combat", "attacker_ships": {"s4": 16}, "defender_ships": {"s4": 10}, "defender_researches": {"r8": 15, "r9": 15, "r10": 15}},
                timeout=5,
            )
            self.assertEqual(r0.status_code, 200)
            self.assertEqual(r15.status_code, 200)
            self.assertTrue(r0.json()["simulation"]["safe_to_attack"])
            self.assertEqual(r15.json()["simulation"]["defender_power"], 7375.0)
            self.assertFalse(r15.json()["simulation"]["safe_to_attack"])

            with self.assertRaises(ApiRequestError):
                h.bridge.handle_command({"command": "update_config", "config": {"playstyle": 10**5000}})
            r_ps = requests.post(
                f"{h.base_url}/command",
                json={"command": "update_config", "config": {"playstyle": 123}},
                timeout=5,
            )
            self.assertEqual(r_ps.status_code, 400)

            with tempfile.TemporaryDirectory() as tmp:
                _, log_p = append_api_log(Path(tmp), method="POST?sid=T5_METHOD_SID", path="/command", status=200)
                self.assertNotIn("T5_METHOD_SID", log_p.read_text(encoding="utf-8"))

    def test_adv_t5_20_r10_e2e_unicode_digits_negative_fields_blob_redaction_and_raid_asteroid_parity(self) -> None:
        from spaceinvasion.api_server import GameApiBridge, create_api_server
        from spaceinvasion.client import RateLimitError
        from spaceinvasion.config import StrategyConfig
        from spaceinvasion.executor import ActionExecutor
        from spaceinvasion.optimizer import optimize_plan_actions
        from spaceinvasion.state import (
            BuildingOffer,
            EnergyBalance,
            GameState,
            PlanetState,
            Resources,
            TutorialStatus,
            TutorialTask,
            UnitOffer,
        )
        from spaceinvasion.storage import GameDatabase
        from spaceinvasion.strategy import SOLAR_CELLS_ID, plan_actions

        # 1. SQLite bytes BLOB + literal 'sid' JSON + non-finite cells redacted across all DB readers
        with tempfile.TemporaryDirectory() as tmp:
            tp = Path(tmp)
            db = GameDatabase(tp / "adv20.db", attack_history_path="")
            try:
                sec = "T5_R10_BLOB_SID"
                db._conn.execute(
                    "INSERT INTO planet_snapshots(ts, universe_id, planet_id, coords, name, planet_type, temperature, fields_used, fields_max, pig_iron, metal, kryptonite, spice, energy_produced, energy_used, buildings_json, ships_json, defense_json, economy_rates_json, researches_json) "
                    "VALUES('nan', 31, 7, ?, ?, 'normal', 20, 0, 200, 1, 1, 1, 1, 10, 5, ?, '{}', '{}', '{}', '{}')",
                    (b"02:380:04", f"?sid={sec}".encode("utf-8"), json.dumps({"sid": sec, "b1": 5}, allow_nan=False)),
                )
                db._conn.execute(
                    "INSERT INTO raid_history(target_coords, attacked_ts, is_exempt, outcome, loot_json) VALUES(?, 1000.0, 0, ?, ?)",
                    (b"02:380:04", f"?sid={sec}".encode("utf-8"), json.dumps({"sid": sec, "pig_iron": 500}, allow_nan=False)),
                )
                db._conn.commit()
                self.assertEqual(db.load_bash_tracker(now=1000.0).attacks_last_24h("02:380:04", timestamp=1000.0), 1)
                for obj in (db.get_planet_history(7), db.get_full_history_summary()):
                    self.assertNotIn(sec, json.dumps(obj, allow_nan=False))
            finally:
                db.close()

        # 2. Signed ColdSpice temperature (-110) & negative EnergyBalance.net preserved, while fields clamp >= 0
        neg_cold = PlanetState(
            planet_id=1,
            name="NegCold",
            coords="01:001:01",
            planet_type="normal",
            temperature=-110,
            fields_used=-5,
            fields_max=-10,
            resources=Resources(50_000, 50_000, 50_000, 50_000),
            storage=Resources(200_000, 200_000, 200_000, 200_000),
            energy=EnergyBalance(10, 160),
        )
        self.assertEqual(neg_cold.temperature, -110)
        self.assertEqual(neg_cold.fields_used, 0)
        self.assertEqual(neg_cold.fields_max, 0)
        self.assertEqual(neg_cold.energy.net, -150)

        full_cold = PlanetState(
            planet_id=1,
            name="ColdFull",
            coords="01:001:01",
            planet_type="normal",
            temperature=-110,
            fields_used=200,
            fields_max=200,
            resources=Resources(50_000, 50_000, 50_000, 50_000),
            storage=Resources(200_000, 200_000, 200_000, 200_000),
            energy=EnergyBalance(10, 160),
            buildings={"b9": 1},
            building_offers=(BuildingOffer(5, "Fusion plant", 0, 1, Resources(100, 100, 100, 100), True, "?b=5"),),
            ship_offers=(UnitOffer("ship", SOLAR_CELLS_ID, "Solar cells", 0, Resources(0, 2000, 200, 400), True, "ship[9]"),),
        )
        self.assertEqual(full_cold.temperature, -110)
        self.assertEqual(full_cold.fields_remaining, 0)
        self.assertEqual(full_cold.energy.net, -150)

        raid_p = PlanetState(
            planet_id=2,
            name="RaidCol",
            coords="01:001:02",
            planet_type=" Raid ",
            temperature=20,
            fields_used=2,
            fields_max=20,
            resources=Resources(50_000, 50_000, 50_000, 50_000),
            storage=Resources(200_000, 200_000, 200_000, 200_000),
            energy=EnergyBalance(10, 50),
            buildings={"b1": 2},
            building_offers=(BuildingOffer(1, "Pig iron mine", 2, 3, Resources(100, 50, 0, 0), True, "?b=1"),),
            ship_offers=(UnitOffer("ship", 4, "Small fighter", 0, Resources(200, 100, 50, 0), True, "ship[4]"),),
        )
        st = GameState(
            31,
            1,
            "u",
            1_700_000_000.0,
            (full_cold, raid_p),
            active_planet_id=1,
            tutorial=TutorialStatus(1, 5, (TutorialTask("Build Fusion plant level 1", building_name="Fusion plant", building_id=5, current=0, target=1),), "", False),
        )
        p_acts = plan_actions(st, config=StrategyConfig(planner_mode="priority", allow_multi_planet=True, allow_shipyard=True))
        o_acts = optimize_plan_actions(
            st,
            config=StrategyConfig(planner_mode="ga_nn", allow_multi_planet=True, allow_shipyard=True, ga_population_size=8, ga_generations=3, ga_horizon_steps=3, ga_seed=5),
            attack_history_path="",
        )
        for acts in (p_acts, o_acts):
            self.assertEqual([a for a in acts if a.kind == "BUILD"], [])
            self.assertEqual([a for a in acts if a.planet_id == 2 and a.kind in ("BUILD", "BUILD_SHIP", "THROTTLE_BUILDING")], [])

        # 3. ActionExecutor max_actions_per_hour=0 + None fields + E2ETestHarness port=0 & health/bot_control scrubbing
        ex0 = ActionExecutor(dry_run=False, max_actions_per_hour=0)
        self.assertEqual(ex0.remaining_hourly_budget(), 0)
        with E2ETestHarness() as h:
            r_h = requests.get(f"{h.base_url}/health", timeout=5)
            self.assertEqual(r_h.status_code, 200)
            self.assertEqual(r_h.json()["debug_port"], 0)
            h.bridge._bot_control["password"] = b"E2E_PW_SEC"
            r_ctrl = requests.post(f"{h.base_url}/command", json={"command": "bot_control"}, timeout=5)
            self.assertEqual(r_ctrl.status_code, 200)
            self.assertNotIn("E2E_PW_SEC", r_ctrl.text)

    def test_adv_t5_21_r11_asteroid_raid_parity_pathlike_frozenset_and_api_build_gate(self) -> None:
        import io
        from types import MappingProxyType
        import login as login_cli
        from spaceinvasion.auth import Credentials, LoginResult
        from spaceinvasion.browser import BrowserLoginResult
        from spaceinvasion.config import _sanitize_cfg_json_tree, strategy_config_from_dict
        from spaceinvasion.logging_utils import redact_sensitive
        from spaceinvasion.state import BuildingOffer, QueueJob
        from spaceinvasion.storage import _json_dumps

        class _BytesPath:
            def __fspath__(self) -> bytes:
                return b"https://x/?sid=T5_R11_BPATH_SEC"

        # 1. Asteroid negative energy + 95% storage + 1/1 queue parity (priority vs ga_nn) & Raid simulation gating
        ast_p = PlanetState(
            planet_id=92,
            name="AstT5",
            coords="01:001:03",
            planet_type=" Asteroid ",
            temperature=10,
            fields_used=9,
            fields_max=20,
            resources=Resources(95_000, 95_000, 95_000, 95_000),
            storage=Resources(100_000, 100_000, 100_000, 100_000),
            energy=EnergyBalance(5, 50),
            buildings={"b7": 8, "b19": 1},
            building_offers=(
                BuildingOffer(5, "Fusion plant", 0, 1, Resources(100, 50, 0, 0), True, "Build", "?b=5"),
                BuildingOffer(11, "Pig iron warehouse", 0, 1, Resources(100, 50, 0, 0), True, "Build", "?b=11"),
                BuildingOffer(19, "Galaxy scanner", 1, 2, Resources(20_000, 80_000, 40_000, 16_000), True, "Build", "?b=19"),
            ),
        )
        st_ast = GameState(
            31,
            1,
            "u",
            1000.0,
            (ast_p,),
            active_planet_id=92,
            active_queues=(QueueJob("building", "Fleet base", building_id=10, target_level=1),),
        )
        for mode in ("priority", "ga_nn"):
            acts = plan_actions(
                st_ast,
                config=StrategyConfig(planner_mode=mode, ga_population_size=6, ga_generations=2, ga_horizon_steps=2, ga_seed=5),
            )
            self.assertEqual(
                [a for a in acts if a.kind == "THROTTLE_BUILDING" or a.target_id in (5, 11, 12, 13, 14) or (a.kind == "BUILD" and a.executable_now)],
                [],
                f"mode={mode} emitted illegal asteroid actions: {acts}",
            )

        raid_p = PlanetState(
            planet_id=91,
            name="RaidT5",
            coords="01:001:02",
            planet_type=" Raid ",
            temperature=20,
            fields_used=4,
            fields_max=20,
            resources=Resources(200_000, 200_000, 200_000, 200_000),
            storage=Resources(500_000, 500_000, 500_000, 500_000),
            energy=EnergyBalance(100, 10),
            buildings={"b7": 3, "b8": 2, "b9": 2},
            building_offers=(BuildingOffer(1, "Pig iron mine", 0, 1, Resources(78, 0, 25, 0), True, "Build", "?b=1"),),
            ship_offers=(UnitOffer("ship", 4, "Small fighter", 0, Resources(200, 100, 50, 0), True, "ship[4]"),),
            defense_offers=(UnitOffer("defense", 1, "Rocket launcher", 0, Resources(200, 100, 0, 0), True, "def[1]"),),
        )
        st_raid = GameState(
            31,
            1,
            "u",
            1000.0,
            (raid_p,),
            active_planet_id=91,
            researches={"r2": 4, "r4": 4},
            research_offers=(ResearchOffer(1, "r1", "Mining technology", 0, 1, Resources(1000, 0, 500, 0), True, "ready", "?r=1"),),
        )
        for bad_step in (
            SimAction("RESEARCH", 91, "r1"),
            SimAction("BUILD_SHIP", 91, 4, quantity=1),
            SimAction("BUILD_DEFENSE", 91, 1, quantity=1),
            SimAction("THROTTLE_BUILDING", 91, 2, quantity=0),
            SimAction("BUILD", 91, 1),
        ):
            self.assertEqual(
                simulate_action_sequence(
                    st_raid,
                    [bad_step],
                    config=StrategyConfig(allow_research=True, allow_shipyard=True),
                    attack_history_path="",
                ).valid_steps,
                0,
            )

        # 2. frozenset config defense + PathLike/unknown-scalar redaction + login.main + live E2ETestHarness HTTP
        cfg_f = strategy_config_from_dict(
            {"merchant_mode": frozenset({"false"}), "allow_raids": frozenset({1}), "allow_pvp_in_az": frozenset({1})}
        )
        self.assertEqual((cfg_f.merchant_mode, cfg_f.allow_raids, cfg_f.allow_pvp_in_az), (False, False, False))

        for dumped in (
            json.dumps(_sanitize_cfg_json_tree({"p": Path("?sid=T5_CFG_SEC"), "bp": _BytesPath(), "err": RuntimeError("?sid=T5_CFG_ERR"), "obj": object()}), allow_nan=False),
            _json_dumps({"p": Path("?sid=T5_ST_SEC"), "bp": _BytesPath(), "cplx": complex(1, 2), "m": MappingProxyType({"k": object()})}),
            str(redact_sensitive(Path("?sid=T5_LOG_SEC"))),
        ):
            for sec in ("T5_R11_BPATH_SEC", "T5_CFG_SEC", "T5_CFG_ERR", "T5_ST_SEC", "T5_LOG_SEC"):
                self.assertNotIn(sec, dumped)

        creds = Credentials(email="pilot@example.com", password="pw")
        b_res = BrowserLoginResult(
            account_name="acct?sid=T5_BACC_SEC",
            email="pilot@example.com",
            profile_dir=Path("/tmp/?sid=T5_BPROF_SEC"),
            cookie_path=Path("/tmp/?sid=T5_BCOOK_SEC"),
            cookie_names=("c?sid=T5_BCN_SEC",),
            already_logged_in=False,
            final_url="https://spaceinvasion.bitmeup.com/?sid=T5_BFINAL_SEC",
            page_title="T5",
            entered_universe="",
            universes=(),
            debug_address="127.0.0.1:9222?sid=T5_BDBG_SEC",
        )
        h_res = LoginResult(
            account_name="acct?sid=T5_HACC_SEC",
            email="pilot@example.com",
            landing_url="https://bitmeup.com/?sid=T5_HLAND_SEC",
            final_url="https://spaceinvasion.bitmeup.com/?sid=T5_HFINAL_SEC",
            page_title="T5",
            cookie_path=Path("/tmp/?sid=T5_HCOOK_SEC"),
            cookie_names=("c?sid=T5_HCN_SEC",),
            universes=(),
        )
        with (
            patch("login.load_credentials", return_value=creds),
            patch("login.login_with_browser", return_value=b_res),
            patch("login.login", return_value=h_res),
        ):
            for argv in ([], ["--mode", "http"]):
                buf = io.StringIO()
                with patch("sys.stdout", buf):
                    self.assertEqual(login_cli.main(argv), 0)
                rendered = buf.getvalue()
                for sec in (
                    "T5_BACC_SEC", "T5_BPROF_SEC", "T5_BCOOK_SEC", "T5_BCN_SEC", "T5_BFINAL_SEC", "T5_BDBG_SEC",
                    "T5_HACC_SEC", "T5_HLAND_SEC", "T5_HFINAL_SEC", "T5_HCOOK_SEC", "T5_HCN_SEC",
                ):
                    self.assertNotIn(sec, rendered)
        self.assertIn("sid=T5_BFINAL_SEC", b_res.final_url)

        with E2ETestHarness() as h:
            h.bridge._last_url = Path("https://u/?sid=T5_HTTP_PATH_SEC")  # type: ignore[assignment]
            h.bridge._bot_control["bp"] = _BytesPath()
            h.bridge._bot_control["err"] = RuntimeError("?sid=T5_HTTP_ERR_SEC")
            h.bridge._bot_control["cplx"] = complex(1, 2)
            for ep in ("/health", "/bot_status", "/dashboard"):
                r = requests.get(f"{h.base_url}{ep}", timeout=5)
                self.assertEqual(r.status_code, 200)
                for sec in ("T5_HTTP_PATH_SEC", "T5_R11_BPATH_SEC", "T5_HTTP_ERR_SEC"):
                    self.assertNotIn(sec, r.text)

            r_bad_cfg = requests.post(
                f"{h.base_url}/command",
                json={"command": "update_config", "config": {"allow_raids": ["false"]}},
                timeout=5,
            )
            self.assertEqual(r_bad_cfg.status_code, 200)
            self.assertFalse(r_bad_cfg.json()["config"]["allow_raids"])

            norm_p = replace(
                raid_p,
                planet_id=93,
                name="NormT5",
                coords="01:001:04",
                planet_type="normal",
                buildings={"b7": 8},
                building_offers=(BuildingOffer(19, "Galaxy scanner", 0, 1, Resources(10_000, 40_000, 20_000, 8_000), True, "Build", "?b=19"),),
            )
            st_mix = GameState(31, 1, "u", 1000.0, (ast_p, raid_p, norm_p), active_planet_id=92)
            for bid_bad, pid_bad in ((5, 92), (1, 91), (19, 93)):
                act = h.bridge._find_build_action(st_mix, bid_bad, pid_bad, allow_live_verify=True)
                self.assertFalse(act.executable_now)
                self.assertIsNone(act.action_href)
            r_ct = requests.post(f"{h.base_url}/command", json={"command": "claim_tutorial"}, timeout=5)
            self.assertEqual(r_ct.status_code, 200)
            self.assertNotIn("T5_R11_BPATH_SEC", r_ct.text)

    # ------------------------------------------------------------------
    # T5-22: R12 unified secret redaction, HTTP error safety, Raid/Asteroid
    #        tutorial gating, and broken __fspath__ / __str__ hardening
    # ------------------------------------------------------------------
    def test_adv_t5_22_r12_unified_secret_redaction_http_exc_safety_raid_tutorial_and_broken_pathlike(
        self,
    ) -> None:
        from spaceinvasion.fleet import normalize_coords
        from spaceinvasion.logging_utils import redact_sensitive
        from spaceinvasion.ranking import build_combat_overview
        from spaceinvasion.state import BuildingOffer, TutorialStatus, TutorialTask, normalize_planet_type

        class _RaiseFspath:
            def __fspath__(self) -> str:
                raise RuntimeError("fspath boom ?password=T5_FS_PW")

        class _IntPath:
            def __fspath__(self) -> int:
                return 10**5000

        class _BytesPath:
            def __init__(self, raw: bytes) -> None:
                self._raw = raw

            def __fspath__(self) -> bytes:
                return self._raw

        class _RaiseStrRepr:
            def __str__(self) -> str:
                raise RuntimeError("str boom")

            def __repr__(self) -> str:
                raise RuntimeError("repr boom")

        self.assertEqual(
            normalize_planet_type({"planet_type": Path("   "), "type": " Asteroid "}),
            "asteroid",
        )
        self.assertEqual(normalize_planet_type({"planet_type": _BytesPath(b"   "), "type": "Raid"}), "raid")
        self.assertIsInstance(normalize_coords(_RaiseFspath()), str)
        self.assertIsInstance(normalize_coords(_IntPath()), str)

        raid_p = PlanetState(
            planet_id=951,
            name="RaidR12",
            coords="02:380:15",
            planet_type=" Raid ",
            temperature=20,
            fields_used=2,
            fields_max=10,
            resources=Resources(50_000, 50_000, 50_000, 50_000),
            storage=Resources(100_000, 100_000, 100_000, 100_000),
            energy=EnergyBalance(500, 100),
            buildings={"b7": 1, "b9": 1},
            building_offers=(BuildingOffer(7, "Development center", 1, 2, Resources(400, 120, 200, 0), True, "Build", "?b=7"),),
            ship_offers=(UnitOffer("ship", 4, "Small fighter", 0, Resources(3000, 1000, 0, 0), True, "ship[4]"),),
        )
        ast_nolab = PlanetState(
            planet_id=952,
            name="AstR12",
            coords="02:380:16",
            planet_type=" Asteroid ",
            temperature=10,
            fields_used=1,
            fields_max=20,
            resources=Resources(50_000, 50_000, 50_000, 50_000),
            storage=Resources(100_000, 100_000, 100_000, 100_000),
            energy=EnergyBalance(500, 100),
            buildings={"b7": 1},
        )
        tut_r12 = TutorialStatus(
            step=4,
            total_steps=11,
            tasks=(
                TutorialTask("Upgrade your Mining technology 0 / 1", "Mining technology", None, 0, 1, "research", "r1"),
                TutorialTask("Build a Small fighter 0 / 1", "Small fighter", 4, 0, 1, "ship"),
            ),
            reward_text=10**5000,  # type: ignore[arg-type]
            can_complete=False,
        )
        r_offer = ResearchOffer(1, "r1", "Mining technology", 0, 1, Resources(200, 100, 100, 0), True, "Research", "?r=1")
        for planet_obj in (raid_p, ast_nolab):
            st_x = GameState(
                31,
                1,
                "u",
                1000.0,
                (planet_obj,),
                active_planet_id=planet_obj.planet_id,
                tutorial=tut_r12,
                research_offers=(r_offer,),
            )
            for mode in ("priority", "ga_nn"):
                acts = plan_actions(
                    st_x,
                    config=StrategyConfig(
                        planner_mode=mode,
                        allow_research=True,
                        allow_shipyard=True,
                        ga_population_size=4,
                        ga_generations=2,
                        ga_horizon_steps=2,
                        ga_seed=7,
                    ),
                )
                self.assertFalse(any(a.kind == "RESEARCH" and a.executable_now for a in acts))
                if planet_obj.planet_id == 951:
                    self.assertFalse(any(a.kind == "BUILD_SHIP" for a in acts))
            json.dumps(build_buildings_eta(st_x), allow_nan=False)
            json.dumps(build_combat_overview(st_x), allow_nan=False)

        deep_key: object = Path("?password=T5_DP5")
        for _ in range(5):
            deep_key = (deep_key,)
        with E2ETestHarness() as h:
            h.bridge._bot_control["auth_header"] = "Authorization: Bearer T5_HTTP_BEARER"
            h.bridge._bot_control["json_blob"] = '{"password": "T5_JSON_PW"}'
            h.bridge._bot_control["authorization"] = "T5_DICT_AUTH"
            h.bridge._bot_control["long_s"] = "\u017fid=T5_LONGS_SEC"
            h.bridge._bot_control["bad_fs"] = _RaiseFspath()
            h.bridge._bot_control["int_fs"] = _IntPath()
            h.bridge._bot_control["bad_repr"] = _RaiseStrRepr()
            h.bridge._bot_control["exc"] = RuntimeError(10**5000, "?password=T5_EXC_PW")
            h.bridge._bot_control[deep_key] = "ok"

            for ep in ("/dashboard", "/bot_status"):
                r = requests.get(f"{h.base_url}{ep}", timeout=5)
                self.assertEqual(r.status_code, 200)
                for sec in (
                    "T5_FS_PW",
                    "T5_DP5",
                    "T5_HTTP_BEARER",
                    "T5_JSON_PW",
                    "T5_DICT_AUTH",
                    "T5_LONGS_SEC",
                    "T5_EXC_PW",
                ):
                    self.assertNotIn(sec, r.text)
                self.assertIn("authorization: bearer redacted", r.text.lower())

            with patch.object(
                h.bridge,
                "handle_command",
                side_effect=RuntimeError(10**5000, "Authorization: Bearer T5_POST_BEAR ?password=T5_POST_PW"),
            ):
                r_err = requests.post(f"{h.base_url}/command", json={"command": "plan"}, timeout=5)
            self.assertEqual(r_err.status_code, 500)
            self.assertNotIn("T5_POST_BEAR", r_err.text)
            self.assertNotIn("T5_POST_PW", r_err.text)
            self.assertIn("authorization: bearer redacted", r_err.text.lower())

            with tempfile.TemporaryDirectory() as tmp:
                tp = Path(tmp)
                jl, tl = append_api_log(
                    tp,
                    method="POST",
                    path="/command",
                    status=500,
                    payload={"authorization": "T5_LOG_AUTH", deep_key: "v"},
                    response={"error": RuntimeError(10**5000, "Authorization: Bearer T5_LOG_BEAR")},
                )
                for lp in (jl, tl):
                    txt = lp.read_text(encoding="utf-8")
                    self.assertNotIn("T5_LOG_AUTH", txt)
                    self.assertNotIn("T5_LOG_BEAR", txt)
                    self.assertNotIn("T5_DP5", txt)
            red_exc = redact_sensitive(RuntimeError(10**5000, "?password=T5_RED_PW"))
            self.assertIsInstance(red_exc, str)
            self.assertNotIn("T5_RED_PW", str(red_exc))

    def test_adv_t5_23_r13_b8_b9_raid_tutorial_spy_links_client_cookies_and_compound_redaction(self) -> None:
        import time
        from types import SimpleNamespace
        from spaceinvasion.auth import ConfigurationError, load_session_cookies
        from spaceinvasion.commander_client import GameApiClient, GameApiError
        from spaceinvasion.fleet import parse_spy_messages_html
        from spaceinvasion.logging_utils import redact_sensitive
        from spaceinvasion.state import QueueJob, TutorialStatus, TutorialTask

        # 1. b8=0 / queued b8 RESEARCH & b9=0 / queued b9 BUILD_SHIP blocked across priority & ga_nn
        p_nob8_b9 = PlanetState(
            planet_id=961,
            name="HomeNoLabYard",
            coords="02:380:11",
            planet_type="normal",
            temperature=30,
            fields_used=10,
            fields_max=200,
            resources=Resources(100_000, 100_000, 100_000, 100_000),
            storage=Resources(500_000, 500_000, 500_000, 500_000),
            energy=EnergyBalance(500, 100),
            buildings={"b1": 5, "b5": 5, "b7": 3, "b8": 0, "b9": 0},
            ship_offers=(
                UnitOffer("ship", 4, "Small fighter", 0, Resources(3000, 1000, 0, 0), True, "ship[4]"),
                UnitOffer("ship", 9, "Solar cells", 0, Resources(0, 2000, 200, 400), True, "ship[9]"),
            ),
        )
        r2_offer = ResearchOffer(2, "r2", "Energy technology", 0, 1, Resources(0, 400, 800, 400), True, "Research", "?r=2")
        tut_r_s = TutorialStatus(
            4,
            11,
            (
                TutorialTask("Upgrade Energy technology 0/1", "Energy technology", None, 0, 1, "research", "r2"),
                TutorialTask("Build a Small fighter 0/1", "Small fighter", 4, 0, 1, "ship"),
            ),
        )
        st_q = GameState(
            31,
            1,
            "u",
            1000.0,
            (p_nob8_b9,),
            active_planet_id=961,
            tutorial=tut_r_s,
            research_offers=(r2_offer,),
            active_queues=(
                QueueJob("building", "Research lab", 8, 1, "00:00:00"),
                QueueJob("building", "Weapons factory", 9, 1, "00:00:00"),
            ),
        )
        for mode in ("priority", "ga_nn"):
            acts = plan_actions(
                st_q,
                config=StrategyConfig(
                    planner_mode=mode,
                    allow_research=True,
                    allow_shipyard=True,
                    ga_population_size=8,
                    ga_generations=3,
                    ga_horizon_steps=3,
                    ga_seed=42,
                ),
            )
            self.assertFalse(any(a.kind in ("RESEARCH", "BUILD_SHIP") and a.executable_now for a in acts))

        # 2. Multi-planet Raid + Normal tutorial delegation & Asteroid b8=0 fallback
        raid_p = PlanetState(
            planet_id=962,
            name="Raid962",
            coords="02:380:14",
            planet_type=" Raid ",
            temperature=20,
            fields_used=2,
            fields_max=10,
            resources=Resources(50_000, 50_000, 50_000, 50_000),
            storage=Resources(100_000, 100_000, 100_000, 100_000),
            energy=EnergyBalance(500, 100),
            buildings={"b7": 1, "b9": 1},
        )
        norm_p = PlanetState(
            planet_id=963,
            name="Norm963",
            coords="02:380:15",
            planet_type="normal",
            temperature=25,
            fields_used=5,
            fields_max=200,
            resources=Resources(50_000, 50_000, 50_000, 50_000),
            storage=Resources(100_000, 100_000, 100_000, 100_000),
            energy=EnergyBalance(500, 100),
            buildings={"b7": 2},
        )
        tut_b8 = TutorialStatus(3, 11, (TutorialTask("Build Research lab 0/1", "Research lab", 8, 0, 1, "building"),))
        st_multi = GameState(31, 1, "u", 1000.0, (raid_p, norm_p), active_planet_id=962, tutorial=tut_b8)
        multi_acts = plan_actions(st_multi, config=StrategyConfig(planner_mode="priority", allow_multi_planet=True))
        self.assertTrue(any(a.planet_id == 963 and a.kind == "BUILD" and a.target_id == 8 and a.executable_now for a in multi_acts))

        ast_p = PlanetState(
            planet_id=964,
            name="Ast964",
            coords="02:380:16",
            planet_type=" Asteroid ",
            temperature=10,
            fields_used=2,
            fields_max=20,
            resources=Resources(50_000, 50_000, 50_000, 50_000),
            storage=Resources(100_000, 100_000, 100_000, 100_000),
            energy=EnergyBalance(500, 100),
            buildings={"b7": 1},
        )
        tut_ast = TutorialStatus(
            3,
            11,
            (
                TutorialTask("Build Research lab 0/1", "Research lab", 8, 0, 1, "building"),
                TutorialTask("Upgrade Energy technology 0/1", "Energy technology", None, 0, 1, "research", "r2"),
                TutorialTask("Upgrade Development center 1/2", "Development center", 7, 1, 2, "building"),
            ),
        )
        ast_acts = plan_actions(
            GameState(31, 1, "u", 1000.0, (ast_p,), active_planet_id=964, tutorial=tut_ast, research_offers=(r2_offer,)),
            config=StrategyConfig(planner_mode="priority", allow_multi_planet=False),
        )
        self.assertEqual([(a.kind, a.target_id) for a in ast_acts], [("WAIT_RESOURCES", 7)])

        # 3. Spy messages clock-link filtering + valid preservation
        msg_html = (
            '<table><tr><td>Pig iron: 1000 <a href="internalGalaxy.php?gal=2&sun=150">12:05:12</a></td></tr>'
            '<tr><td>Planet <a href="internalGalaxy.php?gal=2&sun=150">2:150:8</a> Pig iron: 5000 Metal: 2000</td></tr></table>'
        )
        reps = parse_spy_messages_html(msg_html)
        self.assertEqual([r.target_coords for r in reps], ["2:150:8"])

        # 4. load_session_cookies corrupt JSON -> ConfigurationError without body leak
        with tempfile.TemporaryDirectory() as tmp:
            bad_cookie = Path(tmp) / "bad_cookies.json"
            bad_cookie.write_text("{corrupt?password=T5_COOKIE_BODY", encoding="utf-8")
            with self.assertRaises(ConfigurationError) as ctx_ck:
                load_session_cookies(bad_cookie)
            self.assertNotIn("T5_COOKIE_BODY", str(ctx_ck.exception))

        # 5. GameApiClient._decode redaction & HTTP E2E compound/Unicode/Bearer redaction + 5-planet latency < 25ms
        with GameApiClient() as cli:
            with self.assertRaises(GameApiError) as ctx_dec:
                cli._decode(
                    SimpleNamespace(
                        status_code=500,
                        text=("X" * 205) + "?sid=T5_DEC_SID&client_secret=T5_DEC_CSEC",
                        json=lambda: (_ for _ in ()).throw(ValueError("not json")),
                    )
                )
            self.assertNotIn("T5_DEC_SID", str(ctx_dec.exception))
            self.assertNotIn("T5_DEC_CSEC", str(ctx_dec.exception))

        with E2ETestHarness() as h:
            h.bridge._bot_control["client_secret"] = "T5_HTTP_CSEC"
            h.bridge._bot_control["user_password"] = "T5_HTTP_UPW"
            h.bridge._bot_control["quoted_bearer"] = 'Authorization: Bearer "T5_Q_BEAR"'
            h.bridge._bot_control["brk_bearer"] = "Authorization: Bearer [T5_B_BEAR]"
            h.bridge._bot_control["turk_sid"] = "?s\u0130d=T5_TURK_SID&?ap\u0131_key=T5_TURK_AK"
            h.bridge._bot_control["has_sid"] = False
            h.bridge._bot_control["cookie_path"] = "/tmp/cookies.json"
            r = requests.get(f"{h.base_url}/bot_status", timeout=5)
            self.assertEqual(r.status_code, 200)
            for sec in ("T5_HTTP_CSEC", "T5_HTTP_UPW", "T5_Q_BEAR", "T5_B_BEAR", "T5_TURK_SID", "T5_TURK_AK"):
                self.assertNotIn(sec, r.text)
            self.assertIn('Authorization: Bearer \\"REDACTED\\"', r.text)
            self.assertIn("Authorization: Bearer [REDACTED]", r.text)
            body_json = r.json()
            self.assertFalse(body_json["has_sid"])
            self.assertEqual(body_json["cookie_path"], "/tmp/cookies.json")

            planets_5 = tuple(
                PlanetState(
                    planet_id=500 + i,
                    name=f"P{i}",
                    coords=f"02:380:0{i}",
                    planet_type="normal",
                    temperature=30,
                    fields_used=20,
                    fields_max=200,
                    resources=Resources(5000, 3000, 4000, 1000),
                    storage=Resources(100_000, 100_000, 100_000, 100_000),
                    energy=EnergyBalance(500, 200),
                    buildings={"b1": 8, "b2": 6, "b3": 8, "b4": 5, "b5": 10},
                )
                for i in range(1, 6)
            )
            h.bridge._last_state = GameState(31, 1, "u", 1000.0, planets_5, active_planet_id=501)
            _ = h.bridge.dashboard(refresh=False)
            t0 = time.perf_counter()
            dash_5 = h.bridge.dashboard(refresh=False)
            elapsed_ms = (time.perf_counter() - t0) * 1000.0
            self.assertEqual(len(dash_5["state"]["planets"]), 5)
            self.assertLess(elapsed_ms, 25.0)
            self.assertEqual(redact_sensitive("has_password=true&bsid=123"), "has_password=true&bsid=123")

    def test_adv_t5_24_r14_combined_domain_offline_spio_redaction_and_dag20_cycles(self) -> None:
        import time
        from unittest.mock import patch
        from spaceinvasion.auth import SpaceInvasionAuthError
        from spaceinvasion.client import RateLimitError
        from spaceinvasion.state import (
            BuildingOffer,
            EnergyBalance,
            GameState,
            PlanetState,
            ResearchOffer,
            Resources,
            UnitOffer,
        )

        raid_p = PlanetState(
            planet_id=970,
            name="Raid970",
            coords="02:380:70",
            planet_type="raid",
            temperature=10,
            fields_used=0,
            fields_max=50,
            resources=Resources(50_000, 50_000, 50_000, 50_000),
            storage=Resources(100_000, 100_000, 100_000, 100_000),
            energy=EnergyBalance(500, 100),
            buildings={"b1": 2, "b9": 3},
        )
        ast_p = PlanetState(
            planet_id=971,
            name="Ast971",
            coords="02:380:71",
            planet_type="asteroid",
            temperature=10,
            fields_used=1,
            fields_max=20,
            resources=Resources(50_000, 50_000, 50_000, 50_000),
            storage=Resources(100_000, 100_000, 100_000, 100_000),
            energy=EnergyBalance(500, 100),
            buildings={"b1": 1, "b7": 1},
        )
        norm_p = PlanetState(
            planet_id=972,
            name="Norm972",
            coords="02:380:72",
            planet_type="normal",
            temperature=25,
            fields_used=10,
            fields_max=200,
            resources=Resources(50_000, 50_000, 50_000, 50_000),
            storage=Resources(100_000, 100_000, 100_000, 100_000),
            energy=EnergyBalance(500, 100),
            buildings={"b1": 4},
            building_offers=(BuildingOffer(1, "Pig iron mine", 5, 6, Resources(100, 100, 0, 0), True, "Build", "?b=1"),),
            ship_offers=(UnitOffer("ship", 4, "Small fighter", 0, Resources(3000, 1000, 0, 0), True, "ship[4]"),),
        )
        st = GameState(
            31,
            1,
            "u",
            time.time(),
            (raid_p, ast_p, norm_p),
            active_planet_id=972,
            research_offers=(ResearchOffer(2, "r2", "Energy technology", 0, 1, Resources(500, 500, 0, 0), True, "Research", "?r=2"),),
            unread_messages={"spio": 2},
        )

        with E2ETestHarness() as h:
            h.bridge._record_state(
                st,
                fetched_buildings=True,
                fetched_research=True,
                fetched_shipyard=True,
                fetched_home=True,
            )
            h.bridge._last_spio_sync_at = 0.0

            # 1. Offline auth failure during cached plan unread spio sync degrades cleanly
            with patch.object(h.bridge, "_client_factory", side_effect=SpaceInvasionAuthError("offline")):
                r_plan = requests.post(
                    f"{h.base_url}/command",
                    json={"command": "plan", "force_refresh": False},
                    timeout=5,
                )
                self.assertEqual(r_plan.status_code, 200)
                self.assertTrue(r_plan.json()["ok"])

            # 2. Stale BuildingOffer dry_run build stays non-executed; raid/asteroid throttle rejected with 400
            r_stale = requests.post(
                f"{h.base_url}/command",
                json={"command": "build", "planet_id": 972, "building_id": 1, "dry_run": True},
                timeout=5,
            )
            self.assertEqual(r_stale.status_code, 200)
            self.assertFalse(r_stale.json()["executed"])

            for bad_pid in (970, 971):
                r_thr = requests.post(
                    f"{h.base_url}/command",
                    json={"command": "throttle_building", "planet_id": bad_pid, "building_id": 1, "percent": 50},
                    timeout=5,
                )
                self.assertEqual(r_thr.status_code, 400)

            # 3. RateLimitError on unread spio sync still surfaces HTTP 429
            h.bridge._last_spio_sync_at = 0.0
            with patch.object(h.bridge, "_client_factory", side_effect=RateLimitError("slow", retry_after=15.0)):
                r_rl = requests.post(
                    f"{h.base_url}/command",
                    json={"command": "plan", "force_refresh": False},
                    timeout=5,
                )
                self.assertEqual(r_rl.status_code, 429)

            # 4. HTTP /bot_status urlencoded JSON + escaped JSON quotes + dag20/cycle safety
            cyc_d: dict[str, object] = {}
            cyc_d["self"] = cyc_d
            dag_exc: BaseException = RuntimeError("Bearer T5_R14_DAG_SEC")
            for _ in range(20):
                dag_exc = RuntimeError(dag_exc, dag_exc, dag_exc, dag_exc)
            h.bridge._bot_control["ue_json"] = '%22password%22%3A%22T5_R14_UE_PW%22'
            h.bridge._bot_control["esc_json"] = '\\"password\\":\\"T5_R14_ESC_PW\\"'
            h.bridge._bot_control["cyc"] = cyc_d
            h.bridge._bot_control["dag"] = dag_exc
            t0 = time.perf_counter()
            r_bs = requests.get(f"{h.base_url}/bot_status", timeout=5)
            self.assertLess((time.perf_counter() - t0) * 1000.0, 250.0)
            self.assertEqual(r_bs.status_code, 200)
            for sec in ("T5_R14_DAG_SEC", "T5_R14_UE_PW", "T5_R14_ESC_PW"):
                self.assertNotIn(sec, r_bs.text)


if __name__ == "__main__":
    unittest.main()
