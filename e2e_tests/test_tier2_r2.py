"""Tier 2 E2E Boundary & Corner Tests for R2 (Galaxy, Throttling, Fleet, Safety & DB+History Sync): 24 tests."""

from __future__ import annotations

import json
from pathlib import Path
import re
import tempfile
import threading
import unittest

import requests

import commander
from e2e_tests.fixtures import make_midgame_state
from e2e_tests.harness import E2ETestHarness, FakeClock, FakeSpaceInvasionServer
from spaceinvasion.config import StrategyConfig
from spaceinvasion.storage import GameDatabase


def _make_two_planet_fleet_server(**kwargs) -> FakeSpaceInvasionServer:
    planets = {
        12476: {
            "planet_id": 12476,
            "name": "HomeEmpty",
            "coords": "02:380:12",
            "planet_type": "normal",
            "temperature": 31,
            "fields_max": 230,
            "resources": [10000, 10000, 10000, 5000],
            "storage": [100000, 100000, 100000, 100000],
            "energy": {"produced": 200, "used": 100},
            "buildings": {"b1": 5, "b5": 6},
            "ships": {},
            "defense": {},
            "economy_rates": {"resource1": 100, "resource2": 100, "resource3": 100, "resource4": 100},
            "building_queue": [],
        },
        12477: {
            "planet_id": 12477,
            "name": "RaidMoon",
            "coords": "02:380:14",
            "planet_type": "normal",
            "temperature": 15,
            "fields_max": 230,
            "resources": [10000, 10000, 10000, 5000],
            "storage": [100000, 100000, 100000, 100000],
            "energy": {"produced": 200, "used": 100},
            "buildings": {"b1": 5, "b5": 6, "b9": 4},
            "ships": {"s4": 25, "s1": 8},
            "defense": {},
            "economy_rates": {"resource1": 100, "resource2": 100, "resource3": 100, "resource4": 100},
            "building_queue": [],
        },
    }
    return FakeSpaceInvasionServer(planets=planets, active_planet_id=12476, **kwargs)


class TestTier2R2(unittest.TestCase):
    # ------------------------------------------------------------------
    # Area R2-A Boundary & Corner Cases (7 tests)
    # ------------------------------------------------------------------

    def test_r2a_t2_01_consecutive_galaxy_switches_with_async_nav_lag_no_bleed(self) -> None:
        gal = {
            (2, 380): [{"position": 3, "name": "Sys380", "owner": "Own380", "status_color": "yellow", "has_debris": False, "is_spacia": False, "spy_href": None}],
            (2, 381): [{"position": 5, "name": "Sys381", "owner": "Own381", "status_color": "yellow", "has_debris": False, "is_spacia": False, "spy_href": None}],
            (2, 382): [{"position": 7, "name": "Sys382", "owner": "Own382", "status_color": "yellow", "has_debris": False, "is_spacia": False, "spy_href": None}],
        }
        srv = FakeSpaceInvasionServer(galaxy_systems=gal, async_nav_polls=2, async_href_lag=True)
        with E2ETestHarness(server=srv) as h:
            h.api.get_state(include_buildings=True)
            for sys_num, exp_coord in ((380, "02:380:03"), (381, "02:381:05"), (382, "02:382:07")):
                res = h.api.send_command({"command": "scan_galaxy", "galaxy": 2, "system": sys_num, "dry_run": False})
                self.assertTrue(res["executed"])
                coords = {t["coords"] for t in h.api.send_command({"command": "db_summary"})["db"]["spy_targets"]}
                self.assertIn(exp_coord, coords, f"Stale bleed-over during switch to 02:{sys_num}: got {coords}")

    def test_r2a_t2_02_sid_dialects_prefixed_raw_and_empty_p_galaxy_fallback(self) -> None:
        for mode, intercept in (("prefixed", 0), ("raw", 0), ("empty", 1)):
            srv = FakeSpaceInvasionServer(sid="deadbeef", sid_mode=mode, p_galaxy_mode="in_frame")
            if intercept:
                srv.inject_intercepted_clicks = intercept
            with E2ETestHarness(server=srv) as h:
                res = h.api.send_command({"command": "scan_galaxy", "galaxy": 2, "system": 380, "dry_run": False})
                self.assertTrue(res["executed"], f"Failed under sid_mode={mode}")
                self.assertNotIn("sid=sid=", h.driver.frame_url("F_Hauptframe"))

    def test_r2a_t2_03_empty_sid_and_popup_noop_fails_explicitly(self) -> None:
        srv = FakeSpaceInvasionServer(sid="", sid_mode="empty", p_galaxy_mode="popup_noop")
        with E2ETestHarness(server=srv, client_timeout=1.0) as h:
            h.api.get_state(include_buildings=True)
            resp = requests.post(
                f"{h.base_url}/command",
                json={"command": "scan_galaxy", "galaxy": 2, "system": 380, "dry_run": False},
                timeout=5.0,
            )
            self.assertEqual(resp.status_code, 503)
            self.assertFalse(resp.json()["ok"])

    def test_r2a_t2_04_icon_table_brackets_and_empty_system_parsing(self) -> None:
        gal = {
            (2, 380): [
                {"position": 4, "name": "IconWorld", "owner": "Sleepy4", "status_color": "yellow", "has_debris": False, "is_spacia": False, "spy_href": None},
            ]
        }
        srv_icon = FakeSpaceInvasionServer(galaxy_systems=gal, galaxy_layout="icon_table")
        with E2ETestHarness(server=srv_icon) as h:
            res = h.api.send_command({"command": "scan_galaxy", "galaxy": 2, "system": 380, "dry_run": False})
            self.assertTrue(res["executed"])
            targets = {t["coords"]: t for t in h.api.send_command({"command": "db_summary"})["db"]["spy_targets"]}
            self.assertIn("02:380:04", targets, f"icon_table failed to parse [04]; got {list(targets.keys())}")
            self.assertEqual(targets["02:380:04"]["status_color"], "yellow")

        srv_empty = FakeSpaceInvasionServer(galaxy_layout="empty")
        with E2ETestHarness(server=srv_empty) as h:
            res_empty = h.api.send_command({"command": "scan_galaxy", "galaxy": 2, "system": 380, "dry_run": False})
            self.assertTrue(res_empty["executed"])
            self.assertIn("0 occupied", res_empty["message"])

    def test_r2a_t2_05_turnstile_redirect_aborts_immediately_not_timeout(self) -> None:
        clock = FakeClock()
        srv = FakeSpaceInvasionServer(clock=clock)
        srv.inject_antibot = "turnstile"
        srv.inject_antibot_on_action = "internalGalaxy"
        with E2ETestHarness(server=srv, clock=clock, client_timeout=10.0) as h:
            h.api.get_state(include_buildings=True)
            t0 = clock.monotonic()
            resp = requests.post(
                f"{h.base_url}/command",
                json={"command": "scan_galaxy", "galaxy": 2, "system": 380, "dry_run": False},
                timeout=5.0,
            )
            elapsed = clock.monotonic() - t0
            self.assertEqual(resp.status_code, 503)
            self.assertLess(elapsed, 1.5, f"Turnstile redirect hung for {elapsed:.2f}s instead of aborting immediately")
            err = resp.json().get("error", "").lower()
            self.assertTrue("anti-bot" in err or "captcha" in err or "turnstile" in err, err)

    def test_r2a_t2_06_rescan_preserves_existing_spied_resources_and_defense(self) -> None:
        srv = FakeSpaceInvasionServer(
            spy_reports={
                "02:380:04": {
                    "owner": "SleepyOne",
                    "resources": {"pig_iron": 40000, "metal": 20000, "kryptonite": 5000, "spice": 1000},
                    "fleet": {},
                    "defense": {"d1": 2},
                }
            }
        )
        with E2ETestHarness(server=srv) as h:
            h.api.get_state(include_buildings=True)
            h.api.send_command({"command": "scan_galaxy", "galaxy": 2, "system": 380, "dry_run": False})
            spy_res = h.api.send_command({"command": "spy", "target_coords": "02:380:04", "probes": 1, "dry_run": False})
            self.assertTrue(spy_res["executed"])
            h.api.send_command({"command": "sync_spy_reports"})

            h.api.send_command({"command": "scan_galaxy", "galaxy": 2, "system": 380, "dry_run": False})
            targets = {t["coords"]: t for t in h.api.send_command({"command": "db_summary"})["db"]["spy_targets"]}
            self.assertEqual(targets["02:380:04"]["resources"]["pig_iron"], 40000)
            self.assertEqual(targets["02:380:04"]["resources"]["metal"], 20000)
            self.assertEqual(targets["02:380:04"]["defense"].get("d1"), 2)

    def test_r2a_t2_07_boundary_coords_and_invalid_navigate_action(self) -> None:
        gal = {
            (1, 1): [{"position": 1, "name": "MinSlot", "owner": "IdleMin", "status_color": "yellow", "has_debris": False, "is_spacia": False, "spy_href": None}],
            (5, 499): [{"position": 16, "name": "MaxSlot", "owner": "IdleMax", "status_color": "yellow", "has_debris": False, "is_spacia": False, "spy_href": None}],
        }
        srv = FakeSpaceInvasionServer(galaxy_systems=gal)
        with E2ETestHarness(server=srv) as h:
            h.api.get_state(include_buildings=True)
            h.api.send_command({"command": "scan_galaxy", "galaxy": 1, "system": 1, "dry_run": False})
            h.api.send_command({"command": "scan_galaxy", "galaxy": 5, "system": 499, "dry_run": False})
            coords = {t["coords"] for t in h.api.send_command({"command": "db_summary"})["db"]["spy_targets"]}
            self.assertIn("01:001:01", coords)
            self.assertIn("05:499:16", coords)

            views_before = len(srv.page_view_log)
            bad_resp = requests.post(
                f"{h.base_url}/command",
                json={"command": "navigate", "action": "internalInvalid"},
                timeout=5.0,
            )
            self.assertEqual(bad_resp.status_code, 400)
            self.assertEqual(len(srv.page_view_log), views_before)

    # ------------------------------------------------------------------
    # Area R2-B Boundary & Corner Cases (7 tests)
    # ------------------------------------------------------------------

    def test_r2b_t2_01_rate_limit_on_f_planets_reload_not_session_expired(self) -> None:
        clock = FakeClock()
        srv = FakeSpaceInvasionServer(clock=clock, rate_limit_on_frame="F_planets")
        with E2ETestHarness(server=srv, clock=clock) as h:
            srv.force_rate_limit_remaining = 1
            resp = requests.get(f"{h.base_url}/state?include_buildings=1", timeout=5.0)
            self.assertIn(resp.status_code, (429, 503))
            body = resp.json()
            self.assertTrue(body.get("rate_limited"), f"Expected rate_limited=True on F_planets throttle: {body}")
            self.assertNotIn("session expired", body.get("error", "").lower())
            health = requests.get(f"{h.base_url}/health", timeout=5.0).json()
            self.assertTrue(health["in_game"])

            # Clear throttle, advance clock past retry_after cooldown, and verify clean /state + scan_galaxy recovery
            retry_after = float(body.get("retry_after") or 60.0)
            srv.clear_throttle()
            clock.advance(retry_after + 5.0)
            rec_state = requests.get(f"{h.base_url}/state?include_buildings=1", timeout=5.0)
            self.assertEqual(rec_state.status_code, 200, f"Post-cooldown /state failed: {rec_state.text}")
            rec_json = rec_state.json()
            self.assertIn("planets", rec_json)
            self.assertGreaterEqual(len(rec_json["planets"]), 1)
            rec_scan = h.api.send_command({"command": "scan_galaxy", "galaxy": 2, "system": 380, "dry_run": False})
            self.assertTrue(rec_scan["ok"])
            self.assertTrue(rec_scan["executed"])

    def test_r2b_t2_02_redirect_on_rate_limit_aborts_wait_immediately(self) -> None:
        clock = FakeClock()
        srv = FakeSpaceInvasionServer(clock=clock, redirect_on_rate_limit=True)
        with E2ETestHarness(server=srv, clock=clock, client_timeout=10.0) as h:
            h.api.get_state(include_buildings=True)
            srv.force_rate_limit_remaining = 1
            t0 = clock.monotonic()
            resp = requests.post(
                f"{h.base_url}/command",
                json={"command": "scan_galaxy", "galaxy": 2, "system": 380, "dry_run": False},
                timeout=5.0,
            )
            elapsed = clock.monotonic() - t0
            self.assertIn(resp.status_code, (429, 503))
            self.assertLess(elapsed, 1.5, f"Rate-limit redirect hung {elapsed:.2f}s waiting for expected_action")
            self.assertTrue(resp.json().get("rate_limited"))
            self.assertEqual(len(h.ensure_session_calls), 0)

    def test_r2b_t2_03_dashboard_refresh_rate_limit_preserves_cached_state_and_in_game(self) -> None:
        srv = FakeSpaceInvasionServer()
        with E2ETestHarness(server=srv) as h:
            h.api.get_state(include_buildings=True)
            srv.force_rate_limit_remaining = 1
            dash = requests.get(f"{h.base_url}/dashboard?refresh=1", timeout=5.0).json()
            self.assertTrue(dash["ok"])
            self.assertTrue(dash["health"]["in_game"], f"Rate limit wiped in_game=False: {dash['health']}")
            self.assertTrue(dash["health"].get("rate_limited"), f"Expected rate_limited=True: {dash['health']}")
            self.assertIsNotNone(dash["state"])
            self.assertEqual(dash["state"]["planets"][0]["coords"], "02:380:12")

    def test_r2b_t2_04_commander_4_ticks_under_25_views_per_min_threshold(self) -> None:
        clock = FakeClock()
        srv = FakeSpaceInvasionServer(clock=clock, rate_limit_threshold=25)
        with E2ETestHarness(server=srv, clock=clock) as h:
            for _ in range(4):
                st, _act, _res, _nxt, _out = commander.run_commander_tick(
                    h.api,
                    live=False,
                    max_actions=1,
                    config=StrategyConfig(),
                )
                self.assertEqual(st.universe_id, 31)
                clock.advance(20.0)
            self.assertEqual(len(h.ensure_session_calls), 0)

    def test_r2b_t2_05_interactive_command_headroom_over_background_reads(self) -> None:
        clock = FakeClock()
        srv = FakeSpaceInvasionServer(clock=clock, rate_limit_threshold=35)
        with E2ETestHarness(server=srv, clock=clock) as h:
            h.api.get_state(include_buildings=True)
            lim = h.page_limiter
            self.assertIsNotNone(lim, "PageViewLimiter missing from spaceinvasion.client")
            # Fill background reads right up to soft cap (max_views - reserved)
            cap_bg = lim.max_views_per_minute - lim.reserved_interactive_views
            while lim.views_last_60s() < cap_bg:
                lim.record_view(1)
            self.assertEqual(lim.available_budget("normal"), 0)
            self.assertGreater(lim.available_budget("interactive"), 0)

            res = h.api.send_command({"command": "scan_galaxy", "galaxy": 2, "system": 380, "dry_run": False})
            self.assertTrue(res["executed"])

    def test_r2b_t2_06_blast_furnace_hysteresis_prevents_flip_flop_storm(self) -> None:
        # Planet with b1=4, b2=6 -> net Pig drain << 0 at 100%, resources=[0, 5000, 5000, 5000]
        # Tick 1 emits & executes THROTTLE_BUILDING 0%; Tick 2 (+60s) suppresses 100% flip during cooldown;
        # Tick 3 (+250s, total +310s >= 300s) resumes 100% cleanly once cooldown expires.
        clock = FakeClock()
        planets = {
            12476: {
                "planet_id": 12476,
                "name": "Homeworld",
                "coords": "02:380:12",
                "planet_type": "normal",
                "temperature": 31,
                "fields_max": 230,
                "resources": [0, 5000, 5000, 5000],
                "storage": [100000, 100000, 100000, 100000],
                "energy": {"produced": 250, "used": 120},
                "buildings": {"b1": 4, "b2": 6, "b3": 4, "b4": 2, "b5": 7, "b7": 3},
                "ships": {},
                "defense": {},
                "economy_rates": {"resource1": 100, "resource2": 100, "resource3": 100, "resource4": 100},
                "building_queue": [],
            }
        }
        srv = FakeSpaceInvasionServer(planets=planets, clock=clock)
        cfg = StrategyConfig(auto_throttle_blast_furnace=True)
        with E2ETestHarness(server=srv, clock=clock, config=cfg) as h:
            # Load initial internalEconomy rates into the bridge without touching throttle_cooldowns
            h.api.send_command({"command": "navigate", "action": "internalEconomy"})
            h.api.get_state(include_buildings=True)

            # Tick 1 via commander CLI main (reads throttle_cooldowns from API/SQLite): MUST pause to 0%
            rc1 = commander.main(
                ["--api-url", h.base_url, "--live", "--once", "--config", str(h.config_path), "--log-dir", str(h.log_dir)]
            )
            self.assertEqual(rc1, 0)
            self.assertEqual(srv.planets[12476]["economy_rates"]["resource2"], 0)
            db = GameDatabase(h.db_path)
            try:
                throttles_1 = [a for a in db.get_recent_actions(limit=20) if a["kind"] == "THROTTLE_BUILDING" and a["executed"]]
            finally:
                db.close()
            self.assertEqual(len(throttles_1), 1, f"Expected 1 THROTTLE_BUILDING on Tick 1, got {throttles_1}")
            self.assertEqual(throttles_1[0]["target_level"], 0)
            self.assertIn("12476", {str(k): v for k, v in h.api.get_bot_control()["throttle_cooldowns"].items()})

            # Tick 2 (+60s): Pig iron recovers & Metal depleted with Metal-needing mines (b1=12) so strategy
            # WOULD immediately resume 100% if cooldown were ignored — 300s hysteresis MUST keep resource2 at 0%
            clock.advance(60.0)
            srv.planets[12476]["buildings"] = {"b1": 12, "b2": 6, "b3": 4, "b4": 4, "b5": 11, "b7": 2}
            srv.planets[12476]["resources"] = [80_000, 0, 50_000, 50_000]
            rc2 = commander.main(
                ["--api-url", h.base_url, "--live", "--once", "--config", str(h.config_path), "--log-dir", str(h.log_dir)]
            )
            self.assertEqual(rc2, 0)
            self.assertEqual(
                srv.planets[12476]["economy_rates"]["resource2"],
                0,
                "Blast furnace flipped back to 100% inside 300s hysteresis cooldown!",
            )
            db = GameDatabase(h.db_path)
            try:
                throttles_2 = [a for a in db.get_recent_actions(limit=20) if a["kind"] == "THROTTLE_BUILDING" and a["executed"]]
            finally:
                db.close()
            self.assertEqual(len(throttles_2), 1)

            # Tick 3 (+250s, total +310s past 300s cooldown): MUST resume Blast furnace to 100%
            clock.advance(250.0)
            rc3 = commander.main(
                ["--api-url", h.base_url, "--live", "--once", "--config", str(h.config_path), "--log-dir", str(h.log_dir)]
            )
            self.assertEqual(rc3, 0)
            self.assertEqual(srv.planets[12476]["economy_rates"]["resource2"], 100)
            db = GameDatabase(h.db_path)
            try:
                throttles_3 = [a for a in db.get_recent_actions(limit=20) if a["kind"] == "THROTTLE_BUILDING" and a["executed"]]
            finally:
                db.close()
            self.assertEqual(len(throttles_3), 2)
            self.assertEqual(throttles_3[0]["target_level"], 100)

    def test_r2b_t2_07_concurrent_threads_bridge_lock_and_sqlite_safety(self) -> None:
        srv = FakeSpaceInvasionServer(render_yield_s=0.015)
        with E2ETestHarness(server=srv) as h:
            h.api.get_state(include_buildings=True)
            results: list[tuple[str, int, bool]] = []
            errors: list[str] = []
            lock = threading.Lock()
            iters = 4

            def _worker(tag: str, fn) -> None:
                try:
                    for i in range(iters):
                        code, ok = fn(i)
                        with lock:
                            results.append((f"{tag}_{i}", code, ok))
                except Exception as exc:
                    with lock:
                        errors.append(f"{tag}: {exc}")

            def _req_health(_i: int) -> tuple[int, bool]:
                r = requests.get(f"{h.base_url}/health", timeout=10.0)
                return r.status_code, bool(r.json().get("ok"))

            def _req_dash(_i: int) -> tuple[int, bool]:
                r = requests.get(f"{h.base_url}/dashboard?refresh=1", timeout=10.0)
                body = r.json()
                return r.status_code, bool(body.get("ok") and isinstance(body.get("state"), dict))

            def _req_plan(_i: int) -> tuple[int, bool]:
                r = requests.post(f"{h.base_url}/command", json={"command": "plan"}, timeout=10.0)
                return r.status_code, bool(r.json().get("ok"))

            def _req_scan(i: int) -> tuple[int, bool]:
                r = requests.post(
                    f"{h.base_url}/command",
                    json={"command": "scan_galaxy", "galaxy": 2, "system": 380 + (i % 2), "dry_run": False},
                    timeout=10.0,
                )
                body = r.json()
                return r.status_code, bool(body.get("ok") and body.get("executed"))

            threads = [
                threading.Thread(target=_worker, args=("health", _req_health)),
                threading.Thread(target=_worker, args=("dash", _req_dash)),
                threading.Thread(target=_worker, args=("plan", _req_plan)),
                threading.Thread(target=_worker, args=("scan", _req_scan)),
            ]
            for t in threads:
                t.start()
            for t in threads:
                t.join(timeout=20.0)
            self.assertEqual(errors, [], f"Thread errors: {errors}")
            self.assertEqual(
                srv.concurrent_render_collisions,
                0,
                f"Detected {srv.concurrent_render_collisions} overlapping unserialized driver/server renders across threads!",
            )
            self.assertEqual(len(results), 4 * iters)
            for tag, code, ok in results:
                self.assertEqual(code, 200, f"{tag} returned HTTP {code}")
                self.assertTrue(ok, f"{tag} returned ok=False")

    # ------------------------------------------------------------------
    # Area R2-C Boundary & Corner Cases (8 tests)
    # ------------------------------------------------------------------

    def test_r2c_t2_01_internal_fleet_planet_switch_ignores_target_favourites_select(self) -> None:
        srv = _make_two_planet_fleet_server(fleet_form_dialect="standard")
        cfg = StrategyConfig(allow_raids=True)
        with E2ETestHarness(server=srv, config=cfg) as h:
            h.api.get_state(include_buildings=True)
            res = h.api.send_command(
                {"command": "attack", "target_coords": "02:380:04", "planet_id": 12477, "dry_run": False}
            )
            self.assertTrue(res["executed"], f"Attack from colony 12477 failed: {res['message']}")
            self.assertGreaterEqual(len(srv.launched_fleets), 1)
            self.assertEqual(srv.launched_fleets[-1]["origin_planet_id"], 12477)

    def test_r2c_t2_02_standard_sendfleet_form_decoy_fav_form_and_speed_radio(self) -> None:
        planets = {
            12476: {
                "planet_id": 12476,
                "name": "Home",
                "coords": "02:380:12",
                "planet_type": "normal",
                "temperature": 31,
                "fields_max": 230,
                "resources": [10000, 10000, 10000, 5000],
                "storage": [100000, 100000, 100000, 100000],
                "energy": {"produced": 200, "used": 100},
                "buildings": {"b1": 5, "b5": 6, "b9": 3},
                "ships": {"s4": 10, "s1": 3},
                "defense": {},
                "economy_rates": {},
                "building_queue": [],
            }
        }
        srv = FakeSpaceInvasionServer(planets=planets, fleet_form_dialect="standard", async_nav_polls=2)
        cfg = StrategyConfig(allow_raids=True)
        with E2ETestHarness(server=srv, config=cfg) as h:
            h.api.get_state(include_buildings=True)
            res = h.api.send_command({"command": "attack", "target_coords": "02:380:04", "dry_run": False})
            self.assertTrue(res["executed"], f"Standard SendFleet dispatch failed: {res['message']}")
            last = srv.launched_fleets[-1]
            self.assertEqual(last["mission"], "1")
            self.assertEqual(last["target_coords"], "02:380:04")
            self.assertEqual(last["ships"], {"s4": 10, "s1": 3})

    def test_r2c_t2_03_recovers_from_intercepted_and_stale_clicks_no_duplicate_launch(self) -> None:
        srv = FakeSpaceInvasionServer(fleet_form_dialect="standard")
        cfg = StrategyConfig(allow_raids=True)
        with E2ETestHarness(server=srv, config=cfg) as h:
            h.api.get_state(include_buildings=True)
            srv.inject_intercepted_clicks = 1
            srv.inject_stale_clicks = 1
            res = h.api.send_command({"command": "attack", "target_coords": "02:380:04", "dry_run": False})
            self.assertTrue(res["executed"], f"Failed recovery from intercepted/stale clicks: {res['message']}")
            self.assertEqual(len(srv.launched_fleets), 1)

    def test_r2c_t2_04_bash_tracker_3_24h_active_blocks_4th_yellow_spacia_exempt(self) -> None:
        clock = FakeClock()
        spy_reports = {
            "02:380:04": {"owner": "IdleY", "resources": {"pig_iron": 10000, "metal": 10000, "kryptonite": 5000, "spice": 1000}, "fleet": {}, "defense": {}},
            "02:380:06": {"owner": "PlayerX", "resources": {"pig_iron": 10000, "metal": 10000, "kryptonite": 5000, "spice": 1000}, "fleet": {}, "defense": {}},
            "02:380:09": {"owner": "Spacia", "resources": {"pig_iron": 20000, "metal": 15000, "kryptonite": 8000, "spice": 2000}, "fleet": {}, "defense": {}, "is_spacia": True},
        }
        srv = FakeSpaceInvasionServer(clock=clock, spy_reports=spy_reports)
        cfg = StrategyConfig(allow_raids=True, allow_pvp_in_az=True)
        with E2ETestHarness(
            server=srv,
            clock=clock,
            config=cfg,
            max_actions_per_hour=30,
            executor_max_limiter_wait=None,
        ) as h:
            initial_views = len(srv.page_view_log)
            h.api.get_state(include_buildings=True)
            h.api.send_command({"command": "scan_galaxy", "galaxy": 2, "system": 380, "dry_run": False})
            for coords in ("02:380:04", "02:380:06", "02:380:09"):
                h.api.send_command({"command": "spy", "target_coords": coords, "probes": 1, "dry_run": False})
            h.api.send_command({"command": "sync_spy_reports"})

            # 3 real live attacks on active light_red 02:380:06
            for _ in range(3):
                ok_atk = h.api.send_command({"command": "attack", "target_coords": "02:380:06", "dry_run": False})
                self.assertTrue(ok_atk["executed"], ok_atk)
            attack_fleets = lambda: [f for f in srv.launched_fleets if f.get("mission") == "1"]
            self.assertEqual(len(attack_fleets()), 3)

            # 4th attack on active light_red MUST be blocked by 3/24h Bash tracker
            blocked = h.api.send_command({"command": "attack", "target_coords": "02:380:06", "dry_run": False})
            self.assertFalse(blocked["executed"])
            self.assertIn("bash", blocked["message"].lower())
            self.assertEqual(len(attack_fleets()), 3)

            # Yellow and Spacia remain exempt even past 3 raids
            for _ in range(4):
                ok_y = h.api.send_command({"command": "attack", "target_coords": "02:380:04", "dry_run": False})
                ok_s = h.api.send_command({"command": "attack", "target_coords": "02:380:09", "dry_run": False})
                self.assertTrue(ok_y["executed"], ok_y)
                self.assertTrue(ok_s["executed"], ok_s)

            # Verify PageViewLimiter rolled over FakeClock cleanly across >24 views and never exceeded 24 views in any 60s window.
            # Note: 59.5s window accounts for ~1ms FakeClock pre-acquire vs post-render jitter while killing any >24/60s mutant.
            auto_ts = [v["ts"] for v in srv.page_view_log[initial_views:]]
            self.assertGreater(len(auto_ts), 24)
            rollover_sleeps = [s for s in clock.sleep_calls if s >= 50.0]
            self.assertGreaterEqual(
                len(rollover_sleeps),
                2,
                f"Expected >=2 FakeClock 60s budget rollovers; sleep_calls={clock.sleep_calls}",
            )
            for ts in auto_ts:
                win_count = sum(1 for x in auto_ts if ts - 59.5 < x <= ts)
                self.assertLessEqual(
                    win_count,
                    24,
                    f"Page-view budget exceeded in rolling 60s window: {win_count} > 24 views",
                )
            self.assertLessEqual(h.api.health()["page_views_last_60s"], 24)

            # Advance clock past 24h -> active 02:380:06 unblocks
            clock.advance(86401.0)
            unblocked = h.api.send_command({"command": "attack", "target_coords": "02:380:06", "dry_run": False})
            self.assertTrue(unblocked["executed"], unblocked)
            all_auto_ts = [v["ts"] for v in srv.page_view_log[initial_views:]]
            for ts in all_auto_ts:
                win_count = sum(1 for x in all_auto_ts if ts - 59.5 < x <= ts)
                self.assertLessEqual(
                    win_count,
                    24,
                    f"Post-24h page-view budget exceeded in rolling 60s window: {win_count} > 24 views",
                )

    def test_r2c_t2_05_live_attack_blocks_unsafe_win_ratio_below_1_4x(self) -> None:
        planets = {
            12476: {
                "planet_id": 12476,
                "name": "Home",
                "coords": "02:380:12",
                "planet_type": "normal",
                "temperature": 31,
                "fields_max": 230,
                "resources": [5000, 5000, 5000, 2000],
                "storage": [100000, 100000, 100000, 100000],
                "energy": {"produced": 200, "used": 100},
                "buildings": {"b1": 4, "b5": 6, "b9": 2},
                "ships": {"s4": 2, "s1": 1, "s10": 5},
                "defense": {},
                "economy_rates": {},
                "building_queue": [],
            }
        }
        spy_reports = {
            "02:380:04": {
                "owner": "Bunker",
                "resources": {"pig_iron": 20000, "metal": 20000, "kryptonite": 10000, "spice": 5000},
                "fleet": {},
                "defense": {"d1": 30},
            }
        }
        srv = FakeSpaceInvasionServer(planets=planets, spy_reports=spy_reports)
        cfg = StrategyConfig(allow_raids=True, combat_safety_margin=1.4)
        with E2ETestHarness(server=srv, config=cfg) as h:
            h.api.get_state(include_buildings=True)
            h.api.send_command({"command": "scan_galaxy", "galaxy": 2, "system": 380, "dry_run": False})
            spy_res = h.api.send_command({"command": "spy", "target_coords": "02:380:04", "probes": 1, "dry_run": False})
            self.assertTrue(spy_res["executed"])
            h.api.send_command({"command": "sync_spy_reports"})

            res = h.api.send_command({"command": "attack", "target_coords": "02:380:04", "dry_run": False})
            self.assertFalse(res["executed"])
            self.assertEqual([f for f in srv.launched_fleets if f.get("mission") == "1"], [])
            self.assertTrue("1.4" in res["message"] or "safety" in res["message"].lower(), res["message"])

    def test_r2c_t2_06_scan_galaxy_does_not_consume_hourly_mutation_budget(self) -> None:
        srv = FakeSpaceInvasionServer()
        cfg = StrategyConfig(allow_galaxy_scan=True, max_actions_per_hour=2)
        with E2ETestHarness(server=srv, config=cfg) as h:
            h.api.get_state(include_buildings=True)
            for sys_n in (380, 381, 382):
                res = h.api.send_command({"command": "scan_galaxy", "galaxy": 2, "system": sys_n, "dry_run": False})
                self.assertTrue(res["executed"])
            self.assertEqual(h.api.health()["remaining_hourly_actions"], 2)

            b_res = h.api.send_command({"command": "build", "building_id": 1, "dry_run": False})
            self.assertTrue(b_res["executed"])
            self.assertEqual(h.api.health()["remaining_hourly_actions"], 1)

    def test_r2c_t2_07_s9_only_fleet_and_malformed_target_coords_rejected(self) -> None:
        planets = {
            12476: {
                "planet_id": 12476,
                "name": "Home",
                "coords": "02:380:12",
                "planet_type": "normal",
                "temperature": 31,
                "fields_max": 230,
                "resources": [5000, 5000, 5000, 2000],
                "storage": [100000, 100000, 100000, 100000],
                "energy": {"produced": 200, "used": 100},
                "buildings": {"b1": 4, "b5": 6, "b9": 1},
                "ships": {"s9": 10},
                "defense": {},
                "economy_rates": {},
                "building_queue": [],
            }
        }
        srv = FakeSpaceInvasionServer(planets=planets)
        cfg = StrategyConfig(allow_raids=True)
        with E2ETestHarness(server=srv, config=cfg) as h:
            h.api.get_state(include_buildings=True)
            res = h.api.send_command({"command": "attack", "target_coords": "02:380:04", "dry_run": False})
            self.assertFalse(res["executed"])
            self.assertIn("no stationed", res["message"].lower())

            views_before = len(srv.page_view_log)
            bad = requests.post(
                f"{h.base_url}/command",
                json={"command": "attack", "target_coords": "bad-coords", "dry_run": False},
                timeout=5.0,
            )
            self.assertEqual(bad.status_code, 400)
            self.assertEqual(len(srv.page_view_log), views_before)

    def test_r2c_t2_08_target_colors_filtered_by_allow_pvp_in_az(self) -> None:
        gal = {
            (2, 380): [
                {"position": 4, "name": "InactiveY", "owner": "IdleY", "status_color": "yellow", "has_debris": False, "is_spacia": False, "spy_href": None},
                {"position": 6, "name": "RivalAZ", "owner": "PlayerX", "status_color": "light_red", "has_debris": False, "is_spacia": False, "spy_href": None},
                {"position": 7, "name": "WeakBlue", "owner": "Newb", "status_color": "blue", "has_debris": False, "is_spacia": False, "spy_href": None},
                {"position": 8, "name": "AllyGreen", "owner": "Mate", "status_color": "green", "has_debris": False, "is_spacia": False, "spy_href": None},
                {"position": 10, "name": "StrongDark", "owner": "Titan", "status_color": "dark_red", "has_debris": False, "is_spacia": False, "spy_href": None},
            ]
        }
        spy_loot = {"pig_iron": 20000, "metal": 10000, "kryptonite": 10000, "spice": 5000}
        spy_reports = {
            "02:380:04": {"owner": "IdleY", "resources": spy_loot, "fleet": {}, "defense": {}},
            "02:380:06": {"owner": "PlayerX", "resources": spy_loot, "fleet": {}, "defense": {}},
            "02:380:07": {"owner": "Newb", "resources": spy_loot, "fleet": {}, "defense": {}},
            "02:380:08": {"owner": "Mate", "resources": spy_loot, "fleet": {}, "defense": {}},
            "02:380:10": {"owner": "Titan", "resources": spy_loot, "fleet": {}, "defense": {}},
        }
        srv = FakeSpaceInvasionServer(galaxy_systems=gal, spy_reports=spy_reports)
        cfg = StrategyConfig(allow_raids=True, allow_pvp_in_az=False)
        with E2ETestHarness(server=srv, config=cfg) as h:
            h.api.get_state(include_buildings=True)
            h.api.send_command({"command": "scan_galaxy", "galaxy": 2, "system": 380, "dry_run": False})
            for coords in ("02:380:04", "02:380:06", "02:380:07", "02:380:08", "02:380:10"):
                h.api.send_command({"command": "spy", "target_coords": coords, "probes": 1, "dry_run": False})
            h.api.send_command({"command": "sync_spy_reports"})
            plan_off = h.api.send_command({"command": "plan", "refresh": True})["actions"]
            atks_off = [a["target_name"] for a in plan_off if a["kind"] == "ATTACK"]
            self.assertTrue(any("02:380:04" in t for t in atks_off), f"Expected yellow 02:380:04 in {atks_off}")
            self.assertFalse(any("02:380:06" in t for t in atks_off))

            h.api.send_command({"command": "update_config", "config": {"allow_pvp_in_az": True}})
            plan_on = h.api.send_command({"command": "plan", "refresh": True})["actions"]
            atks_on = [a["target_name"] for a in plan_on if a["kind"] == "ATTACK"]
            self.assertTrue(any("02:380:06" in t for t in atks_on), f"Expected light_red 02:380:06 in {atks_on}")
            for forbidden in ("02:380:07", "02:380:08", "02:380:10"):
                self.assertFalse(any(forbidden in t for t in atks_on))

    def test_r2c_t2_09_db_attack_history_mirror_redacts_sid_and_isolates_repo_file(self) -> None:
        repo_file = Path(__file__).resolve().parents[1] / "data" / "attack_history.json"
        before_bytes = repo_file.read_bytes() if repo_file.exists() else None
        secret_tok = "SID_SHOULD_BE_REDACTED_445566"

        with tempfile.TemporaryDirectory(prefix="si_r2c_t2_09_") as tmp:
            db_path = Path(tmp) / "state" / "spaceinvasion.db"
            db = GameDatabase(db_path=db_path)
            try:
                self.assertTrue(hasattr(db, "get_raid_stats"), "GameDatabase missing get_raid_stats() (F19)")
                db.record_raid(
                    "02:380:04",
                    attacked_ts=1_700_000_000.0,
                    is_exempt=True,
                    outcome=f"Dispatched https://spaceinvasion.bitmeup.com/indexInternal.es?sid={secret_tok}",
                    loot={"pig_iron": 5000, "metal": 2000, "kryptonite": 1000, "spice": 500},
                )
                mirror = Path(tmp) / "state" / "attack_history.json"
                if not mirror.exists() and getattr(db, "attack_history_path", None):
                    mirror = Path(db.attack_history_path)
                self.assertTrue(mirror.exists(), "Expected temp DB dir attack_history.json mirror")
                raw = mirror.read_text(encoding="utf-8")
                self.assertNotIn(secret_tok, raw, "Raw sid leaked into temp attack_history.json mirror!")
                raw_hits = [s for s in re.findall(r"sid=([A-Za-z0-9_-]{6,})", raw, flags=re.IGNORECASE) if s.upper() != "REDACTED"]
                self.assertEqual(raw_hits, [])
            finally:
                db.close()

        after_bytes = repo_file.read_bytes() if repo_file.exists() else None
        self.assertEqual(after_bytes, before_bytes, "Temp GameDatabase overwrote repo data/attack_history.json!")

    def test_r2c_t2_10_complete_raid_history_and_telemetry_aggregates_survive_reopen(self) -> None:
        with tempfile.TemporaryDirectory(prefix="si_r2c_t2_10_") as tmp:
            db_path = Path(tmp) / "state" / "spaceinvasion.db"
            db1 = GameDatabase(db_path=db_path)
            try:
                self.assertTrue(hasattr(db1, "get_raid_stats"), "GameDatabase missing get_raid_stats() (F19)")
                db1.record_state_snapshot(make_midgame_state())
                # 7 wins & 3 losses across 2 targets, newer attacks yield higher loot on 02:380:04
                for i in range(6):
                    db1.record_raid(
                        "02:380:04",
                        attacked_ts=1_700_000_000.0 + i * 3600.0,
                        is_exempt=True,
                        outcome="Victory looted",
                        loot={"pig_iron": 6000 * (i + 1), "metal": 0, "kryptonite": 0, "spice": 0},
                    )
                for i in range(3):
                    db1.record_raid(
                        "02:380:05",
                        attacked_ts=1_700_000_000.0 + i * 3600.0,
                        is_exempt=False,
                        outcome="Aborted: combat defeated / lost fleet",
                        loot={},
                    )
                db1.record_raid(
                    "02:380:05",
                    attacked_ts=1_700_020_000.0,
                    is_exempt=False,
                    outcome="Victory looted",
                    loot={"pig_iron": 0, "metal": 0, "kryptonite": 0, "spice": 2000},
                )
            finally:
                db1.close()

            db2 = GameDatabase(db_path=db_path)
            try:
                stats = db2.get_raid_stats()
                ov = stats["overall"]
                self.assertEqual(ov["attempts"], 10)
                self.assertEqual(ov["wins"], 7)
                self.assertEqual(ov["losses"], 3)
                self.assertAlmostEqual(float(ov["success_rate"]), 0.7, places=4)
                self.assertGreater(float(ov["total_loot_spice_eq"]), 0.0)
                pt4 = stats["per_target"]["02:380:04"]
                self.assertEqual(pt4["attempts"], 6)
                self.assertEqual(pt4["wins"], 6)
                # Increasing recent loot on 02:380:04 means recency_weighted > uniform avg
                self.assertGreaterEqual(float(pt4["recency_weighted_loot_spice_eq"]), float(pt4["avg_loot_spice_eq"]))
                self.assertEqual(stats["per_target"]["02:380:05"]["attempts"], 4)
            finally:
                db2.close()
