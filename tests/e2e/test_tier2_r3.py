"""Tier 2 E2E Boundary & Corner Tests for R3 (ETA Math, API/Config Sync & Operator UI): 19 tests."""

from __future__ import annotations

import json
from pathlib import Path
import re
import tempfile
import unittest

import requests

from tests.e2e.fixtures import (
    make_building_offer,
    make_ga_nn_config,
    make_game_state,
    make_planet,
)
from tests.e2e.harness import E2ETestHarness, FakeSpaceInvasionServer, run_ui_in_node
from spaceinvasion import game_data, ranking
from spaceinvasion.config import StrategyConfig, load_strategy_config
from spaceinvasion.state import EnergyBalance, Resources
from spaceinvasion.strategy import PlannedAction, plan_actions


def _import_eta_helpers(test: unittest.TestCase):
    try:
        from spaceinvasion.ranking import attach_action_etas, compute_action_eta
    except ImportError as exc:
        test.fail(f"R3 ETA helpers missing from spaceinvasion.ranking: {exc}")
    return compute_action_eta, attach_action_etas


def _verify_eta_fields_present(test: unittest.TestCase, obj: dict | PlannedAction) -> None:
    for k in (
        "wait_hours",
        "wait_seconds",
        "wait_eta",
        "duration_seconds",
        "duration_text",
        "total_hours",
        "total_seconds",
        "total_eta",
        "eta_bottleneck",
    ):
        if isinstance(obj, dict):
            test.assertIn(k, obj, f"ETA field {k!r} missing from action dict")
        else:
            test.assertTrue(hasattr(obj, k), f"ETA field {k!r} missing from PlannedAction")


class TestTier2R3(unittest.TestCase):
    # ------------------------------------------------------------------
    # Area R3.A Boundary & Corner Cases (6 tests)
    # ------------------------------------------------------------------

    def test_t2_r3a_01_blast_furnace_paused_0pct_blocks_metal_unblocks_pig(self) -> None:
        compute_action_eta, _ = _import_eta_helpers(self)
        p = make_planet(
            buildings={"b1": 4, "b2": 5, "b3": 4, "b5": 8, "b7": 2},
            resources=Resources(0, 0, 10_000, 10_000),
            economy_rates={"resource1": 100, "resource2": 0, "resource3": 100, "resource4": 100},
        )
        state = make_game_state(planets=(p,), speedup_factor=1.0)
        metal_act = PlannedAction(
            "P2", "BUILD", p.planet_id, p.coords, "Development center", "needs metal",
            target_id=7, target_level=3, cost=Resources(0, 2400, 1200, 800), missing=Resources(0, 500, 0, 0),
        )
        e_met = compute_action_eta(state, metal_act)
        self.assertIsNone(e_met["wait_hours"])
        self.assertIsNone(e_met["wait_seconds"])
        self.assertEqual(e_met["wait_eta"], "—")
        self.assertIsNone(e_met["total_hours"])
        self.assertIsNone(e_met["total_seconds"])
        self.assertEqual(e_met["total_eta"], "—")
        self.assertEqual(e_met["eta_bottleneck"], "metal")

        pig_act = PlannedAction(
            "P2", "BUILD", p.planet_id, p.coords, "Pig iron mine", "pig only",
            target_id=1, target_level=5, cost=Resources(400, 0, 100, 0), missing=Resources(200, 0, 0, 0),
        )
        e_pig = compute_action_eta(state, pig_act)
        self.assertIsNotNone(e_pig["wait_hours"])
        self.assertGreater(e_pig["wait_hours"], 0.0)
        self.assertEqual(e_pig["eta_bottleneck"], "pig_iron")

    def test_t2_r3a_02_blast_furnace_2_1_over_drain_blocks_pig_no_zero_div(self) -> None:
        compute_action_eta, _ = _import_eta_helpers(self)
        # b1=2 (63/h) vs b2=6 (290/h used -> net -227/h Pig)
        p = make_planet(
            buildings={"b1": 2, "b2": 6, "b5": 9},
            resources=Resources(0, 5000, 5000, 5000),
            economy_rates={"resource1": 100, "resource2": 100, "resource3": 100, "resource4": 100},
        )
        state = make_game_state(planets=(p,))
        act = PlannedAction(
            "P2", "BUILD", p.planet_id, p.coords, "Pig iron mine", "over-drained",
            target_id=1, target_level=3, missing=Resources(100, 0, 0, 0),
        )
        eta = compute_action_eta(state, act)
        self.assertIsNone(eta["wait_hours"])
        self.assertEqual(eta["wait_eta"], "—")
        self.assertEqual(eta["total_eta"], "—")
        self.assertEqual(eta["eta_bottleneck"], "pig_iron")

    def test_t2_r3a_03_multi_planet_isolation_temp_and_r1_mining_tech(self) -> None:
        compute_action_eta, _ = _import_eta_helpers(self)
        p1_cold = make_planet(
            planet_id=101, coords="02:380:15", temperature=-120,
            buildings={"b4": 5, "b5": 10, "b7": 6, "b17": 1},
            resources=Resources(10_000, 10_000, 10_000, 0),
        )
        p2_hot = make_planet(
            planet_id=202, coords="02:380:02", temperature=120,
            buildings={"b4": 5, "b5": 10, "b7": 0, "b17": 0},
            resources=Resources(10_000, 10_000, 10_000, 0),
        )
        state = make_game_state(planets=(p1_cold, p2_hot), active_planet_id=202, researches={"r1": 5}, speedup_factor=1.0)
        act_p1 = PlannedAction(
            "P2", "BUILD", 101, p1_cold.coords, "Spice mine", "cold spice",
            target_id=4, target_level=6, missing=Resources(0, 0, 0, 1000),
        )
        act_p2 = PlannedAction(
            "P2", "BUILD", 202, p2_hot.coords, "Spice mine", "hot spice",
            target_id=4, target_level=6, missing=Resources(0, 0, 0, 1000),
        )
        e1 = compute_action_eta(state, act_p1)
        e2 = compute_action_eta(state, act_p2)
        expected_spice_p1 = 75.0 * (1.0 + 0.02 * 5)  # 82.5/h
        self.assertAlmostEqual(e1["wait_hours"], 1000.0 / expected_spice_p1, delta=0.1)
        self.assertLess(e1["wait_hours"], e2["wait_hours"])
        base_s = float(game_data.building_level_stats(4, 6)["base_time_s"])
        self.assertAlmostEqual(e1["duration_seconds"], base_s / ((6 + 1) * 2), delta=0.2)
        self.assertAlmostEqual(e2["duration_seconds"], base_s / 1.0, delta=0.2)

    def test_t2_r3a_04_wait_resources_building_vs_ship_advisory_durations(self) -> None:
        compute_action_eta, _ = _import_eta_helpers(self)
        p = make_planet(buildings={"b3": 4, "b5": 8, "b7": 2, "b17": 0})
        state = make_game_state(planets=(p,), speedup_factor=1.0)
        wait_b = PlannedAction(
            "P2", "WAIT_RESOURCES", p.planet_id, p.coords, "Kryptonite mine", "queued/waiting",
            target_id=3, target_level=5, cost=Resources(pig_iron=600, kryptonite=300), missing=Resources(0, 0, 100, 0),
        )
        ship_adv = PlannedAction(
            "P3", "SHIP", p.planet_id, p.coords, "Small transport", "advisory",
            target_id=None, cost=Resources(0, 3000, 3000, 0), missing=Resources(0, 0, 500, 0),
        )
        ew = compute_action_eta(state, wait_b)
        es = compute_action_eta(state, ship_adv)
        self.assertGreater(ew["wait_hours"], 0.0)
        self.assertGreater(ew["duration_seconds"], 0.0)
        self.assertAlmostEqual(ew["total_seconds"], ew["wait_seconds"] + ew["duration_seconds"], delta=0.2)

        self.assertGreater(es["wait_hours"], 0.0)
        self.assertEqual(es["duration_seconds"], 0.0)
        self.assertEqual(es["duration_text"], "Instant")
        self.assertAlmostEqual(es["total_seconds"], es["wait_seconds"], delta=0.2)

    def test_t2_r3a_05_zero_weapons_factory_and_total_energy_blackout(self) -> None:
        compute_action_eta, _ = _import_eta_helpers(self)
        p = make_planet(
            buildings={"b1": 4, "b3": 4, "b9": 0},
            energy=EnergyBalance(produced=0, used=100),
        )
        state = make_game_state(planets=(p,), speedup_factor=1.0)
        act = PlannedAction(
            "P2", "BUILD_SHIP", p.planet_id, p.coords, "Small transport", "blackout",
            target_id=1, target_level=1, cost=Resources(0, 3000, 3000, 0), missing=Resources(0, 0, 500, 0),
        )
        eta = compute_action_eta(state, act)
        base_s = float(game_data.SHIPS["small-transport"]["base_build_time_s"])
        self.assertAlmostEqual(eta["duration_seconds"], base_s / 1.0, delta=0.2)
        self.assertIsNone(eta["wait_hours"])
        self.assertEqual(eta["wait_eta"], "—")
        self.assertEqual(eta["total_eta"], "—")

    def test_t2_r3a_06_extrapolated_research_l42_and_unknown_target_id(self) -> None:
        compute_action_eta, _ = _import_eta_helpers(self)
        p = make_planet(buildings={"b8": 4}, resources=Resources(0, 0, 0, 0))
        state = make_game_state(planets=(p,), researches={"r1": 41}, speedup_factor=1.0)
        res_42 = PlannedAction(
            "P2", "RESEARCH", p.planet_id, p.coords, "Mining technology", "l42",
            target_id=1, target_level=42, missing=Resources(),
        )
        e42 = compute_action_eta(state, res_42)
        l40_s = float(game_data.RESEARCH_BY_ID["r1"]["levels"][40]["base_time_s"])
        self.assertAlmostEqual(e42["duration_seconds"], (l40_s * 4.0) / 5.0, delta=2.0)

        bad_b = PlannedAction("P2", "BUILD", p.planet_id, p.coords, "Unknown", "bad", target_id=999, target_level=1)
        e_bad = compute_action_eta(state, bad_b)
        self.assertEqual(e_bad["duration_seconds"], 0.0)

    # ------------------------------------------------------------------
    # Area R3.B Boundary & Corner Cases (6 tests)
    # ------------------------------------------------------------------

    def test_t2_r3b_01_empty_planets_returns_empty_plan_cleanly(self) -> None:
        _import_eta_helpers(self)
        empty = make_game_state(planets=(), active_planet_id=None)
        self.assertEqual(plan_actions(empty, config=StrategyConfig()), [])
        srv = FakeSpaceInvasionServer(planets={})
        with E2ETestHarness(server=srv) as h:
            self.assertEqual(h.api.send_command({"command": "plan"})["actions"], [])
            dash = requests.get(f"{h.base_url}/dashboard?refresh=0", timeout=5.0).json()
            self.assertEqual(dash["plan"], [])

    def test_t2_r3b_02_corrupted_planner_mode_yaml_and_http_update(self) -> None:
        _import_eta_helpers(self)
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "config.yaml"
            p.write_text("planner_mode: invalid_xyz\nplaystyle: miner\n", encoding="utf-8")
            loaded = load_strategy_config(p)
            self.assertTrue(hasattr(loaded, "planner_mode"), "StrategyConfig missing planner_mode")
            self.assertEqual(loaded.planner_mode, "priority")

        with E2ETestHarness(raw_config_text="planner_mode: invalid_xyz\nplaystyle: fleeter\n") as h:
            cfg = h.api.send_command({"command": "config"})["config"]
            self.assertEqual(cfg["planner_mode"], "priority")
            resp = requests.post(
                f"{h.base_url}/command",
                json={"command": "update_config", "config": {"planner_mode": "not_a_mode"}},
                timeout=5.0,
            )
            self.assertIn(resp.status_code, (200, 400))
            stored = load_strategy_config(h.config_path)
            self.assertEqual(stored.planner_mode, "priority")

    def test_t2_r3b_03_update_config_syncs_live_and_dry_executors(self) -> None:
        with E2ETestHarness() as h:
            h.api.get_state(include_buildings=True)
            h.api.send_command({"command": "scan_galaxy", "galaxy": 2, "system": 380, "dry_run": False})
            atk_blocked = h.api.send_command({"command": "attack", "target_coords": "02:380:04", "dry_run": False})
            self.assertFalse(atk_blocked["executed"])

            h.api.send_command(
                {
                    "command": "update_config",
                    "config": {"max_actions_per_hour": 2, "allow_raids": True, "allow_fleetsave": True},
                }
            )
            self.assertEqual(h.api.health()["remaining_hourly_actions"], 2)
            dash = h.get_dashboard(refresh=0)
            self.assertEqual(dash["config"]["max_actions_per_hour"], 2)
            self.assertTrue(dash["config"]["allow_raids"])
            self.assertTrue(dash["config"]["allow_fleetsave"])

            # Dry executor synced allow_raids=True -> produces [DRY-RUN] preview instead of disabled guard
            atk_dry = h.api.send_command({"command": "attack", "target_coords": "02:380:04", "dry_run": True})
            self.assertTrue(atk_dry["dry_run"])
            self.assertFalse(atk_dry["executed"])
            self.assertIn("[DRY-RUN]", atk_dry["message"])

            # Live executor synced allow_raids=True and max_actions_per_hour=2
            atk_live_1 = h.api.send_command({"command": "attack", "target_coords": "02:380:04", "dry_run": False})
            self.assertTrue(atk_live_1["executed"], atk_live_1)
            self.assertEqual(h.api.health()["remaining_hourly_actions"], 1)

            atk_live_2 = h.api.send_command({"command": "attack", "target_coords": "02:380:04", "dry_run": False})
            self.assertTrue(atk_live_2["executed"], atk_live_2)
            self.assertEqual(h.api.health()["remaining_hourly_actions"], 0)

            # 3rd action blocked by synced max_actions_per_hour=2 until budget raised
            atk_capped = h.api.send_command({"command": "attack", "target_coords": "02:380:04", "dry_run": False})
            self.assertFalse(atk_capped["executed"])
            h.api.send_command({"command": "update_config", "config": {"max_actions_per_hour": 5}})
            self.assertEqual(h.api.health()["remaining_hourly_actions"], 3)

    def test_t2_r3b_04_cached_economy_rates_preserved_in_dashboard_and_plan(self) -> None:
        _import_eta_helpers(self)
        with E2ETestHarness() as h:
            h.api.get_state(include_buildings=True)
            res = h.api.send_command({"command": "throttle_building", "building_id": 2, "percentage": 0, "dry_run": False})
            self.assertTrue(res["executed"])
            plan = h.api.send_command({"command": "plan"})["actions"]
            dash = requests.get(f"{h.base_url}/dashboard?refresh=0", timeout=5.0).json()

            # Metal-missing actions should report blocked wait_eta="—" with eta_bottleneck="metal"
            metal_missing = [a for a in dash["plan"] if a["missing"]["metal"] > 0]
            for a in metal_missing:
                self.assertEqual(a["wait_eta"], "—")
                self.assertEqual(a["eta_bottleneck"], "metal")
            self.assertEqual(
                [a["wait_eta"] for a in plan[:3]],
                [a["wait_eta"] for a in dash["plan"][:3]],
            )

    def test_t2_r3b_05_large_deficits_format_into_days(self) -> None:
        compute_action_eta, _ = _import_eta_helpers(self)
        p = make_planet(buildings={"b3": 10, "b5": 15, "b7": 3}, energy=EnergyBalance(1000, 500))
        state = make_game_state(planets=(p,), speedup_factor=1.0)
        kry_rate = float(ranking.estimate_planet_hourly_economy(state, p)["kryptonite_per_hr"])
        miss_kry = round(200.0 * kry_rate)
        act = PlannedAction(
            "P2", "BUILD", p.planet_id, p.coords, "Kryptonite mine", "long wait",
            target_id=3, target_level=11, missing=Resources(0, 0, miss_kry, 0),
        )
        eta = compute_action_eta(state, act)
        self.assertAlmostEqual(eta["wait_hours"], 200.0, delta=0.5)
        self.assertIn("d", eta["wait_eta"])
        self.assertIn("d", eta["total_eta"])

    def test_t2_r3b_06_sid_redaction_preserved_with_etas_in_logs(self) -> None:
        _import_eta_helpers(self)
        srv = FakeSpaceInvasionServer(sid="deadbeef1234cafe")
        with E2ETestHarness(server=srv) as h:
            h.api.get_state(include_buildings=True)
            tick = h.api.send_command({"command": "tick_once", "dry_run": True})
            self.assertTrue(tick["ok"])
            _verify_eta_fields_present(self, tick["action"])
            dash = requests.get(f"{h.base_url}/dashboard?refresh=0", timeout=5.0).json()
            self.assertNotIn("deadbeef1234cafe", json.dumps(dash["plan"]))
            api_jsonl = (h.log_dir / "game_api.jsonl").read_text(encoding="utf-8")
            self.assertNotIn("deadbeef1234cafe", api_jsonl)
            last_entry = json.loads(api_jsonl.strip().splitlines()[-1])
            _verify_eta_fields_present(self, last_entry["actual_action"])

    # ------------------------------------------------------------------
    # Area R3.C Boundary & Corner Cases (6 tests)
    # ------------------------------------------------------------------

    def test_t2_r3c_01_ui_empty_queue_renders_matching_colspan_placeholders(self) -> None:
        srv = FakeSpaceInvasionServer(planets={})
        with E2ETestHarness(server=srv) as h:
            res = run_ui_in_node(
                h.base_url,
                """
                await __waitUntil(() => state.dashboard !== null);
                await __sleep(30);
                return {
                  dashRows: __serializeTable("dash-plan-tbody"),
                  planRows: __serializeTable("plan-tbody"),
                  navCount: __snapshotElement("nav-queue-count").textContent,
                  planBadge: __snapshotElement("plan-count-badge").textContent
                };
                """,
            )
            self.assertEqual(res["errors"], [])
            sc = res["scenario"]
            dash_th = res["htmlInspection"]["dash_plan"]["th_count"]
            ops_th = res["htmlInspection"]["ops_plan"]["th_count"]
            self.assertGreaterEqual(dash_th, 6)
            self.assertGreaterEqual(ops_th, 7)
            self.assertEqual(len(sc["dashRows"]), 1)
            self.assertEqual(sc["dashRows"][0][0]["colSpan"], dash_th)
            self.assertEqual(len(sc["planRows"]), 1)
            self.assertEqual(sc["planRows"][0][0]["colSpan"], ops_th)
            self.assertEqual(sc["navCount"], "0")
            self.assertEqual(sc["planBadge"], "0 TASKS")

    def test_t2_r3c_02_ui_10_vs_15_actions_cap_on_dashboard_vs_operations(self) -> None:
        synth_plan = [
            {
                "priority": "P2",
                "kind": "BUILD",
                "planet_id": 12476,
                "planet_coords": "02:380:12",
                "target_id": i + 1,
                "target_name": f"Building {i + 1}",
                "target_level": i + 1,
                "cost": {"pig_iron": 100, "metal": 50, "kryptonite": 25, "spice": 0},
                "missing": {"pig_iron": 0, "metal": 0, "kryptonite": 0, "spice": 0},
                "reason": "Synthetic row",
                "executable_now": True,
                "action_href": None,
                "wait_hours": 0.0,
                "wait_seconds": 0.0,
                "wait_eta": "Ready",
                "duration_seconds": 120.0,
                "duration_text": "2m 00s",
                "total_hours": 0.033,
                "total_seconds": 120.0,
                "total_eta": "2m 00s",
                "eta_bottleneck": None,
            }
            for i in range(15)
        ]
        with E2ETestHarness() as h:
            h.api.get_state(include_buildings=True)
            patch_js = (
                "async (url, opts, fullUrl, nativeFetch) => { "
                "if (url.startsWith('/dashboard')) { "
                "  const r = await nativeFetch(fullUrl, opts); "
                "  const d = await r.json(); "
                f"  d.plan = {json.dumps(synth_plan)}; "
                "  return { ok: true, status: 200, json: async () => d }; "
                "} }"
            )
            res = run_ui_in_node(
                h.base_url,
                """
                await __waitUntil(() => __serializeTable("plan-tbody").length === 15);
                return {
                  dashCount: __serializeTable("dash-plan-tbody").length,
                  planCount: __serializeTable("plan-tbody").length,
                  navCount: __snapshotElement("nav-queue-count").textContent
                };
                """,
                fetch_patch_js=patch_js,
            )
            self.assertEqual(res["errors"], [])
            sc = res["scenario"]
            self.assertEqual(sc["dashCount"], 10)
            self.assertEqual(sc["planCount"], 15)
            self.assertEqual(sc["navCount"], "15")

    def test_t2_r3c_03_ui_null_undefined_legacy_eta_graceful_fallback(self) -> None:
        legacy_row = [
            {
                "priority": "P2",
                "kind": "BUILD",
                "planet_id": 12476,
                "planet_coords": "02:380:12",
                "target_id": 1,
                "target_name": "Pig iron mine",
                "target_level": 2,
                "cost": {"pig_iron": 100, "metal": 0, "kryptonite": 0, "spice": 0},
                "missing": {"pig_iron": 0, "metal": 0, "kryptonite": 0, "spice": 0},
                "reason": "Legacy payload omitting ETA keys",
                "executable_now": True,
                "total_eta": None,
            }
        ]
        with E2ETestHarness() as h:
            h.api.get_state(include_buildings=True)
            patch_js = (
                "async (url, opts, fullUrl, nativeFetch) => { "
                "if (url.startsWith('/dashboard')) { "
                "  const r = await nativeFetch(fullUrl, opts); "
                "  const d = await r.json(); "
                f"  d.plan = {json.dumps(legacy_row)}; "
                "  return { ok: true, status: 200, json: async () => d }; "
                "} }"
            )
            res = run_ui_in_node(
                h.base_url,
                """
                const ok = await __waitUntil(() => __serializeTable("dash-plan-tbody").length === 1 && __serializeTable("dash-plan-tbody")[0].length > 1 && !document.querySelector("#dash-plan-tbody td.empty-cell"));
                if (!ok) throw new Error("Legacy row failed to render in dash-plan-tbody");
                return {
                  dashRows: __serializeTable("dash-plan-tbody"),
                  planRows: __serializeTable("plan-tbody")
                };
                """,
                fetch_patch_js=patch_js,
            )
            self.assertEqual(res["errors"], [])
            self.assertEqual(res["windowErrors"], [])
            self.assertEqual(len(res["scenario"]["dashRows"]), 1)
            self.assertEqual(len(res["scenario"]["planRows"]), 1)
            dash_th = res["htmlInspection"]["dash_plan"]["th_count"]
            self.assertGreaterEqual(dash_th, 6)
            joined = " ".join(c["text"] for r in res["scenario"]["dashRows"] for c in r)
            self.assertNotIn("undefined", joined.lower())
            self.assertNotIn("null", joined.lower())

    def test_t2_r3c_04_ui_planet_select_switch_updates_bf_pill_and_etas(self) -> None:
        planets = {
            101: {
                "planet_id": 101, "name": "Alpha", "coords": "02:380:12", "planet_type": "normal", "temperature": 30, "fields_max": 230,
                "resources": [5000, 5000, 5000, 2000], "storage": [100000, 100000, 100000, 100000],
                "energy": {"produced": 200, "used": 100}, "buildings": {"b1": 5, "b2": 4, "b5": 7},
                "ships": {}, "defense": {}, "economy_rates": {"resource1": 100, "resource2": 100, "resource3": 100, "resource4": 100}, "building_queue": [],
            },
            102: {
                "planet_id": 102, "name": "Beta", "coords": "02:380:14", "planet_type": "normal", "temperature": -80, "fields_max": 230,
                "resources": [5000, 0, 5000, 2000], "storage": [100000, 100000, 100000, 100000],
                "energy": {"produced": 200, "used": 100}, "buildings": {"b1": 5, "b2": 4, "b5": 7},
                "ships": {}, "defense": {}, "economy_rates": {"resource1": 100, "resource2": 100, "resource3": 100, "resource4": 100}, "building_queue": [],
            },
        }
        srv = FakeSpaceInvasionServer(planets=planets, active_planet_id=101)
        with E2ETestHarness(server=srv) as h:
            h.api.get_state(include_buildings=True)
            thr = h.api.send_command({"command": "throttle_building", "building_id": 2, "percentage": 0, "planet_id": 102, "dry_run": False})
            self.assertTrue(thr["executed"], thr)
            # Switch active planet cleanly back to 101 at 100% before loading UI on planet 101
            h.api.send_command({"command": "throttle_building", "building_id": 2, "percentage": 90, "planet_id": 101, "dry_run": False})
            h.api.send_command({"command": "throttle_building", "building_id": 2, "percentage": 100, "planet_id": 101, "dry_run": False})
            st_home = h.api.get_state(include_buildings=True)
            self.assertEqual(st_home.active_planet_id, 101)
            res = run_ui_in_node(
                h.base_url,
                """
                const initOk = await __waitUntil(() => __snapshotElement("planet-select").options.length === 2 && !document.querySelector("#dash-plan-tbody td.empty-cell"));
                if (!initOk) throw new Error("Initial planet-select / dash-plan-tbody did not populate");
                const initialPill = __snapshotElement("hud-bf-pill").textContent;
                const sel = document.getElementById("planet-select");
                sel.value = "102";
                sel.dispatchEvent("change");
                const switchedOk = await __waitUntil(() => __snapshotElement("hud-bf-pill").textContent.includes("0% PAUSED") && __snapshotElement("btn-b2-pause").disabled === true);
                if (!switchedOk) throw new Error("Switching planet-select to 102 did not update Blast furnace pill to 0% PAUSED");
                return {
                  initialPill,
                  bfPill: __snapshotElement("hud-bf-pill").textContent,
                  btnPauseDisabled: __snapshotElement("btn-b2-pause").disabled,
                  dashRows: __serializeTable("dash-plan-tbody")
                };
                """,
            )
            self.assertEqual(res["errors"], [])
            sc = res["scenario"]
            self.assertNotIn("0% PAUSED", sc["initialPill"])
            self.assertIn("0% PAUSED", sc["bfPill"])
            self.assertTrue(sc["btnPauseDisabled"])
            self.assertGreaterEqual(len(sc["dashRows"]), 1)

    def test_t2_r3c_05_ui_action_sim_button_posts_command_and_refreshes(self) -> None:
        with E2ETestHarness() as h:
            h.api.get_state(include_buildings=True)
            res = run_ui_in_node(
                h.base_url,
                """
                await __waitUntil(() => __serializeTable("dash-plan-tbody").length > 0 && __serializeTable("dash-plan-tbody")[0].length > 1);
                const tbody = document.getElementById("dash-plan-tbody");
                const simBtn = Array.from(tbody.querySelectorAll("button")).find((b) => b.textContent.trim() === "Sim");
                if (!simBtn) throw new Error("No Sim button found in #dash-plan-tbody");
                simBtn.click();
                await __waitUntil(() => fetchLog.some((f) => f.url === "/command" && f.body && f.body.dry_run === true));
                await __sleep(80);
                return {
                  banner: __snapshotElement("feedback-banner"),
                  postBodies: fetchLog.filter((f) => f.url === "/command").map((f) => f.body)
                };
                """,
            )
            self.assertEqual(res["errors"], [])
            sc = res["scenario"]
            self.assertTrue(any(b.get("dry_run") is True for b in sc["postBodies"]))
            self.assertIn("[DRY-RUN]", sc["banner"]["textContent"])

    def test_t2_r3c_06_ui_server_boot_id_change_triggers_reload(self) -> None:
        with E2ETestHarness() as h:
            h.api.get_state(include_buildings=True)
            patch_js = (
                "async (url, opts, fullUrl, nativeFetch) => { "
                "if (url === '/ui-version') { "
                "  globalThis.__verCalls = (globalThis.__verCalls || 0) + 1; "
                "  const r = await nativeFetch(fullUrl, opts); "
                "  const d = await r.json(); "
                "  if (globalThis.__verCalls >= 2) d.server_boot_id = 'restarted_boot_2'; "
                "  return { ok: true, status: 200, json: async () => d }; "
                "} }"
            )
            res = run_ui_in_node(
                h.base_url,
                """
                await __waitUntil(() => reloadCount >= 1, 3500);
                return {
                  reloadCount,
                  banner: __snapshotElement("feedback-banner")
                };
                """,
                fetch_patch_js=patch_js,
            )
            self.assertEqual(res["errors"], [])
            self.assertGreaterEqual(res["reloadCount"], 1)
            self.assertIn("restarted", res["scenario"]["banner"]["textContent"].lower())

    def test_t2_r3c_07_ui_ga_nn_hud_priority_hidden_and_partial_telemetry_safe(self) -> None:
        hud_js_helpers = """
                function isElementHidden(el) {
                  let cur = el;
                  while (cur && cur.nodeType === 1) {
                    if (cur.hidden) return true;
                    if (cur.classList && (cur.classList.contains('hidden') || cur.classList.contains('is-hidden'))) return true;
                    if (cur.getAttribute && cur.getAttribute('aria-hidden') === 'true') return true;
                    if (cur.style && (cur.style.display === 'none' || cur.style.visibility === 'hidden')) return true;
                    cur = cur.parentElement || cur.parentNode;
                  }
                  return false;
                }
                function findGaNnHudPanel() {
                  const svgs = Array.from(document.querySelectorAll('svg')).filter((s) => s.id !== 'rnk-chart');
                  const stopClasses = ['app-shell', 'app-layout', 'hud-sidebar', 'main-column', 'hud-canvas', 'page-view'];
                  const candidates = [];
                  for (const svg of svgs) {
                    let cur = svg.parentElement || svg.parentNode;
                    let steps = 0;
                    while (
                      cur &&
                      cur.nodeType === 1 &&
                      cur !== document.body &&
                      cur !== document.documentElement &&
                      cur.tagName !== 'BODY' &&
                      cur.tagName !== 'HTML' &&
                      cur.id !== 'view-stage' &&
                      cur.id !== 'app-sidebar' &&
                      !(cur.classList && stopClasses.some((cls) => cur.classList.contains(cls)))
                    ) {
                      const txt = (cur.textContent || '').toLowerCase();
                      if (txt.includes('generation') && (txt.includes('fitness') || txt.includes('neural'))) {
                        let depth = 0;
                        let anc = cur;
                        while (anc && anc.nodeType === 1) {
                          depth += 1;
                          anc = anc.parentElement || anc.parentNode;
                        }
                        const childCount = cur.querySelectorAll ? cur.querySelectorAll('*').length : 0;
                        candidates.push({ el: cur, depth, childCount, steps });
                        break;
                      }
                      steps += 1;
                      cur = cur.parentElement || cur.parentNode;
                    }
                  }
                  if (candidates.length === 0) return null;
                  const innermost = candidates.filter(
                    (c) => !candidates.some((other) => {
                      if (other.el === c.el) return false;
                      let p = other.el.parentElement || other.el.parentNode;
                      while (p) {
                        if (p === c.el) return true;
                        p = p.parentElement || p.parentNode;
                      }
                      return false;
                    })
                  );
                  const pool = innermost.length > 0 ? innermost : candidates;
                  pool.sort((a, b) => (b.depth - a.depth) || (a.childCount - b.childCount) || (a.steps - b.steps));
                  return pool[0].el;
                }
        """

        # 1) Verify F21 HTML/CSS contracts + priority mode keeps GA+NN radial-ring calculation HUD hidden/inactive
        cfg_prio = make_ga_nn_config(self, planner_mode="priority")
        srv = FakeSpaceInvasionServer()
        with E2ETestHarness(server=srv, config=cfg_prio) as h:
            h.api.get_state(include_buildings=True)
            html_resp = requests.get(f"{h.base_url}/", timeout=5.0)
            css_resp = requests.get(f"{h.base_url}/styles.css", timeout=5.0)
            self.assertEqual(html_resp.status_code, 200)
            self.assertEqual(css_resp.status_code, 200)
            html_txt = html_resp.text
            css_txt = css_resp.text
            stripped_css = re.sub(r"/\*.*?\*/", "", css_txt, flags=re.DOTALL)
            low_html = html_txt.lower()

            non_chart_svgs = [b for b in re.findall(r"<svg\b[^>]*>.*?</svg>", html_txt, flags=re.IGNORECASE | re.DOTALL) if "rnk-chart" not in b]
            self.assertGreater(len(non_chart_svgs), 0, "Expected radial-ring scanner <svg> element in index.html (F21)")
            has_concentric_primitives = any(
                (len(re.findall(r"<(?:circle|path)\b", b, flags=re.IGNORECASE)) >= 2)
                for b in non_chart_svgs
            )
            self.assertTrue(has_concentric_primitives, f"Radial-ring scanner <svg> must contain concentric <circle>/<path> rings: {non_chart_svgs}")
            for metric_kw in ("generation", "neural", "fitness", "genome"):
                self.assertIn(metric_kw, low_html, f"Expected GA+NN HUD metric keyword {metric_kw!r} in index.html (F21)")

            # CSS #06090b HUD ground, selector-bound @keyframes, and active reduced-motion suppression
            self.assertIn("#06090b", stripped_css.lower(), "Expected dark #06090b HUD ground in styles.css (F21)")
            keyframes = re.findall(r"@keyframes\s+([A-Za-z0-9_-]+)", stripped_css)
            self.assertGreater(len(keyframes), 0, "Expected @keyframes animation in styles.css (F21)")

            bound_selectors: list[str] = []
            kf_alt = "|".join(re.escape(k) for k in keyframes)
            for m_rule in re.finditer(r"([^{}@]+)\{([^{}]+)\}", stripped_css):
                sel_raw, body = m_rule.group(1).strip(), m_rule.group(2)
                if re.search(rf"animation(?:-name)?\s*:[^;]*\b(?:{kf_alt})\b", body):
                    for part in sel_raw.split(","):
                        clean_sel = re.sub(r"::[A-Za-z0-9_-]+|:[A-Za-z0-9_-]+(?:\([^)]*\))?", "", part).strip()
                        if clean_sel:
                            bound_selectors.append(clean_sel)
            self.assertGreater(len(bound_selectors), 0, f"Expected CSS selector binding @keyframes {keyframes} to a rule in styles.css")

            rm_matches = re.findall(
                r"@media\s*\([^)]*prefers-reduced-motion\s*:\s*reduce[^)]*\)\s*\{((?:[^{}]|\{[^{}]*\})*)\}",
                stripped_css,
                flags=re.IGNORECASE | re.DOTALL,
            )
            self.assertGreater(len(rm_matches), 0, "Expected @media (prefers-reduced-motion: reduce) block in styles.css (F21)")
            rm_anim_selectors: list[str] = []
            for blk in rm_matches:
                for m_rm in re.finditer(r"([^{}@]+)\{([^{}]+)\}", blk):
                    sel_raw, body = m_rm.group(1).strip(), m_rm.group(2)
                    if re.search(
                        r"animation(?:-duration|-name)?\s*:\s*(?:0(?:\.\d+)?\s*(?:ms|s)?|none)",
                        body,
                        flags=re.IGNORECASE,
                    ):
                        for part in sel_raw.split(","):
                            clean_sel = re.sub(r"::[A-Za-z0-9_-]+|:[A-Za-z0-9_-]+(?:\([^)]*\))?", "", part).strip()
                            if clean_sel:
                                rm_anim_selectors.append(clean_sel)
            self.assertGreater(
                len(rm_anim_selectors),
                0,
                f"@media (prefers-reduced-motion: reduce) block does not disable animations: {rm_matches}",
            )

            static_text_norm = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", html_txt)).strip()

            res_prio = run_ui_in_node(
                h.base_url,
                f"""
                const readyOk = await __waitUntil(() => document.querySelectorAll('#dash-plan-tbody tr').length > 0 && !document.querySelector('#dash-plan-tbody td.empty-cell'), 4000);
                if (!readyOk) throw new Error("dash-plan-tbody failed to populate in priority mode");
                {hud_js_helpers}
                const hudPanel = findGaNnHudPanel();
                const matched = Boolean(hudPanel);
                const hudHidden = hudPanel ? isElementHidden(hudPanel) : false;
                const bodyTxt = (document.getElementById('view-stage') || document.documentElement).textContent || '';
                return {{ matched, hudHidden, hasUndef: bodyTxt.includes('undefined'), hasNan: bodyTxt.includes('NaN') }};
                """,
            )
            self.assertEqual(res_prio["errors"], [])
            self.assertTrue(res_prio["scenario"]["matched"], "GA+NN radial-ring HUD container missing in DOM")
            self.assertTrue(res_prio["scenario"]["hudHidden"], "GA+NN HUD must stay hidden/inactive in 'priority' mode")
            self.assertFalse(res_prio["scenario"]["hasUndef"])
            self.assertFalse(res_prio["scenario"]["hasNan"])

        # 2) ga_nn mode: live CSS binding + reduced-motion target match + dynamic non-zero telemetry propagation + partial/missing fallback
        cfg_gann = make_ga_nn_config(self, planner_mode="ga_nn", ga_generations=10, ga_population_size=16)
        with E2ETestHarness(server=srv, config=cfg_gann) as h2:
            h2.api.get_state(include_buildings=True)

            # 2A) Live unpatched ga_nn telemetry + CSS selector & prefers-reduced-motion binding inside HUD
            res_live = run_ui_in_node(
                h2.base_url,
                f"""
                const boundSelectors = {json.dumps(bound_selectors)};
                const rmAnimSelectors = {json.dumps(rm_anim_selectors)};
                const readyOk = await __waitUntil(() => document.querySelectorAll('#dash-plan-tbody tr').length > 0 && !document.querySelector('#dash-plan-tbody td.empty-cell'), 4000);
                if (!readyOk) throw new Error("dash-plan-tbody failed to populate in ga_nn mode");
                {hud_js_helpers}
                const hudPanel = findGaNnHudPanel();
                const hudAnimatedEls = [];
                if (hudPanel) {{
                  for (const sel of boundSelectors) {{
                    try {{
                      for (const el of document.querySelectorAll(sel)) {{
                        let p = el;
                        while (p) {{
                          if (p === hudPanel) {{
                            if (!hudAnimatedEls.includes(el)) hudAnimatedEls.push(el);
                            break;
                          }}
                          p = p.parentElement || p.parentNode;
                        }}
                      }}
                    }} catch (_) {{}}
                  }}
                }}
                const rmMatchedSet = new Set();
                for (const rmSel of rmAnimSelectors) {{
                  try {{
                    for (const el of document.querySelectorAll(rmSel)) rmMatchedSet.add(el);
                  }} catch (_) {{}}
                }}
                const matchedAnimatedEl = hudAnimatedEls.length > 0;
                const matchedReducedMotionEl = hudAnimatedEls.some((el) => rmMatchedSet.has(el));
                const hudText = hudPanel ? hudPanel.textContent.replace(/\\s+/g, ' ').trim() : '';
                return {{
                  matched: Boolean(hudPanel),
                  hudHidden: hudPanel ? isElementHidden(hudPanel) : true,
                  matchedAnimatedEl,
                  matchedReducedMotionEl,
                  hudText,
                  hasUndef: hudText.includes('undefined'),
                  hasNan: hudText.includes('NaN')
                }};
                """,
            )
            self.assertEqual(res_live["errors"], [])
            sc_live = res_live["scenario"]
            self.assertTrue(sc_live["matched"], "Innermost GA+NN radial-ring HUD container missing in ga_nn mode")
            self.assertFalse(sc_live["hudHidden"], "Expected GA+NN radial-ring HUD visible when planner_mode == 'ga_nn'")
            self.assertTrue(sc_live["matchedAnimatedEl"], f"None of @keyframes CSS selectors {bound_selectors} matched elements inside GA+NN HUD")
            self.assertTrue(sc_live["matchedReducedMotionEl"], f"@media (prefers-reduced-motion: reduce) selectors {rm_anim_selectors} did not cover bound HUD animated elements")
            self.assertFalse(sc_live["hasUndef"])
            self.assertFalse(sc_live["hasNan"])
            self.assertRegex(sc_live["hudText"], r"[1-9]", f"Expected non-zero live telemetry in GA+NN HUD; got {sc_live['hudText']!r}")
            self.assertNotIn(sc_live["hudText"], static_text_norm, "GA+NN HUD text matches static index.html placeholder without dynamic telemetry updates")

            # 2B) Omitted telemetry -> non-zero optimizer_summary propagation on refresh
            patch_propagate_js = (
                "async (url, opts, fullUrl, nativeFetch) => {"
                "if (url.startsWith('/dashboard')) {"
                "  globalThis.__tw3DashCount = (globalThis.__tw3DashCount || 0) + 1;"
                "  const r = await nativeFetch(fullUrl, opts);"
                "  const d = await r.json();"
                "  if (d.config) d.config.planner_mode = 'ga_nn';"
                "  if (globalThis.__tw3DashCount === 1) {"
                "    delete d.optimizer_summary;"
                "    delete d.ga_nn_telemetry;"
                "    delete d.optimizer_metrics;"
                "  } else {"
                "    d.optimizer_summary = {"
                "      generations: 9, generation: 9, population_size: 24,"
                "      nn_evaluations: 216, neural_evals: 216,"
                "      baseline_fitness: 500.0, best_fitness: 842.5, fitness_score: 842.5,"
                "      improvement_pct: 68.5, actions_count: 5, genome_length: 5,"
                "      best_chromosome: [{kind:'BUILD'},{kind:'BUILD'},{kind:'RESEARCH'},{kind:'BUILD_SHIP'},{kind:'ATTACK'}]"
                "    };"
                "  }"
                "  return { ok: true, status: 200, json: async () => d };"
                "} }"
            )
            res_prop = run_ui_in_node(
                h2.base_url,
                f"""
                const readyOk = await __waitUntil(() => document.querySelectorAll('#dash-plan-tbody tr').length > 0 && !document.querySelector('#dash-plan-tbody td.empty-cell'), 4000);
                if (!readyOk) throw new Error("dash-plan-tbody failed to populate under omitted telemetry");
                {hud_js_helpers}
                const hudPanel = findGaNnHudPanel();
                if (!hudPanel) throw new Error("GA+NN radial-ring HUD missing under omitted telemetry");
                const hudHiddenOmitted = isElementHidden(hudPanel);
                const hudOmitted = hudPanel.textContent.replace(/\\s+/g, ' ').trim();
                const syncBtn = document.getElementById('btn-sync');
                if (!syncBtn) throw new Error("Missing #btn-sync for telemetry refresh check");
                const dashBefore = fetchLog.filter((f) => String(f.url || '').startsWith('/dashboard')).length;
                syncBtn.click();
                const fetched = await __waitUntil(() => fetchLog.filter((f) => String(f.url || '').startsWith('/dashboard')).length > dashBefore, 3000);
                if (!fetched) throw new Error("Sync click did not trigger /dashboard refresh");
                const updated = await __waitUntil(() => {{
                  const txt = hudPanel.textContent.replace(/\\s+/g, ' ').trim();
                  return txt !== hudOmitted && /[1-9]/.test(txt);
                }}, 4000);
                if (!updated) throw new Error("GA+NN HUD telemetry did not propagate non-zero optimizer_summary values");
                const hudPopulated = hudPanel.textContent.replace(/\\s+/g, ' ').trim();
                const bodyTxt = document.documentElement.textContent || '';
                return {{
                  rows: __serializeTable('dash-plan-tbody').length,
                  hudHiddenOmitted,
                  hudHiddenPopulated: isElementHidden(hudPanel),
                  hudOmitted,
                  hudPopulated,
                  hasUndef: bodyTxt.includes('undefined'),
                  hasNan: bodyTxt.includes('NaN')
                }};
                """,
                fetch_patch_js=patch_propagate_js,
            )
            self.assertEqual(res_prop["errors"], [])
            sc_prop = res_prop["scenario"]
            self.assertGreater(sc_prop["rows"], 0)
            self.assertFalse(sc_prop["hudHiddenOmitted"], "GA+NN HUD should stay visible when telemetry is omitted")
            self.assertFalse(sc_prop["hudHiddenPopulated"], "GA+NN HUD should stay visible when telemetry updates")
            self.assertNotEqual(sc_prop["hudPopulated"], sc_prop["hudOmitted"])
            self.assertRegex(sc_prop["hudPopulated"], r"[1-9]")
            self.assertFalse(sc_prop["hasUndef"], "Rendered 'undefined' during omitted->populated telemetry transition")
            self.assertFalse(sc_prop["hasNan"], "Rendered 'NaN' during omitted->populated telemetry transition")

            # 2C) Partial optimizer_summary fields ({ generation: 4 }) render safely without undefined/NaN
            patch_partial_js = (
                "async (url, opts, fullUrl, nativeFetch) => {"
                "if (url.startsWith('/dashboard')) {"
                "  const r = await nativeFetch(fullUrl, opts);"
                "  const d = await r.json();"
                "  if (d.config) d.config.planner_mode = 'ga_nn';"
                "  d.optimizer_summary = { generation: 4, generations: 4 };"
                "  delete d.ga_nn_telemetry;"
                "  return { ok: true, status: 200, json: async () => d };"
                "}"
                "if (url === '/command' && opts && opts.body) {"
                "  const b = JSON.parse(String(opts.body));"
                "  if (b.command === 'plan') {"
                "    const r = await nativeFetch(fullUrl, opts);"
                "    const d = await r.json();"
                "    d.optimizer_summary = { generation: 4, generations: 4 };"
                "    return { ok: true, status: 200, json: async () => d };"
                "  }"
                "} }"
            )
            res_partial = run_ui_in_node(
                h2.base_url,
                f"""
                const readyOk = await __waitUntil(() => document.querySelectorAll('#dash-plan-tbody tr').length > 0 && !document.querySelector('#dash-plan-tbody td.empty-cell'), 4000);
                if (!readyOk) throw new Error("dash-plan-tbody failed to populate under partial telemetry");
                {hud_js_helpers}
                const hudPanel = findGaNnHudPanel();
                const matched = Boolean(hudPanel);
                const hudHidden = hudPanel ? isElementHidden(hudPanel) : true;
                const bodyTxt = document.documentElement.textContent || '';
                return {{
                  rows: __serializeTable('dash-plan-tbody').length,
                  matched,
                  hudHidden,
                  hasUndef: bodyTxt.includes('undefined'),
                  hasNan: bodyTxt.includes('NaN')
                }};
                """,
                fetch_patch_js=patch_partial_js,
            )
            self.assertEqual(res_partial["errors"], [])
            self.assertGreater(res_partial["scenario"]["rows"], 0)
            self.assertTrue(res_partial["scenario"]["matched"], "GA+NN radial-ring HUD missing under partial telemetry")
            self.assertFalse(res_partial["scenario"]["hudHidden"], "GA+NN HUD should stay visible under partial ga_nn telemetry")
            self.assertFalse(res_partial["scenario"]["hasUndef"], "Rendered 'undefined' with partial optimizer telemetry")
            self.assertFalse(res_partial["scenario"]["hasNan"], "Rendered 'NaN' with partial optimizer telemetry")
