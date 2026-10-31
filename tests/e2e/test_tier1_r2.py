"""Tier 1 E2E Feature Coverage Tests for R2 (Universe/Galaxy Navigation, Throttling & Attack/Spy): 18 tests."""

from __future__ import annotations

import unittest

import requests

import commander
from tests.e2e.harness import E2ETestHarness, FakeClock, FakeSpaceInvasionServer
from spaceinvasion.core.config import StrategyConfig


def _r2_default_planets(
    *,
    coords: str = "02:380:12",
    ships: dict[str, int] | None = None,
) -> dict[int, dict]:
    return {
        12476: {
            "planet_id": 12476,
            "name": "Homeworld",
            "coords": coords,
            "planet_type": "normal",
            "temperature": 31,
            "fields_max": 230,
            "resources": [20000, 15000, 12000, 6000],
            "storage": [100000, 100000, 100000, 100000],
            "energy": {"produced": 250, "used": 140},
            "buildings": {"b1": 5, "b2": 4, "b3": 4, "b4": 2, "b5": 7, "b7": 3, "b8": 2, "b9": 3},
            "ships": ships if ships is not None else {"s4": 20, "s1": 6, "s10": 5},
            "defense": {"d1": 2},
            "economy_rates": {"resource1": 100, "resource2": 100, "resource3": 100, "resource4": 100, "resource5": 100},
            "building_queue": [],
        }
    }


class TestTier1R2(unittest.TestCase):
    # ------------------------------------------------------------------
    # Area R2-A: Universe / Galaxy Opening, Multi-System Nav & Parsing
    # ------------------------------------------------------------------

    def test_r2a_t1_01_navigate_internal_galaxy_via_f_navi_sid(self) -> None:
        srv = FakeSpaceInvasionServer(sid="abc999", sid_mode="prefixed")
        with E2ETestHarness(server=srv) as h:
            res = h.api.send_command({"command": "navigate", "action": "internalGalaxy", "galaxy": 2, "system": 380})
            self.assertTrue(res["ok"])
            self.assertEqual(res["action"], "internalGalaxy")
            self.assertGreaterEqual(res["scanned_targets"], 2)
            targets = h.api.send_command({"command": "db_summary"})["db"]["spy_targets"]
            coords = {t["coords"] for t in targets}
            self.assertTrue(any(c.startswith("02:380:") for c in coords), f"Expected 02:380:PP in {coords}")

    def test_r2a_t1_02_scan_galaxy_specific_system_persists_slots(self) -> None:
        srv = FakeSpaceInvasionServer()
        with E2ETestHarness(server=srv) as h:
            h.api.get_state(include_buildings=True)
            res = h.api.send_command({"command": "scan_galaxy", "galaxy": 2, "system": 381, "dry_run": False})
            self.assertTrue(res["ok"])
            self.assertTrue(res["executed"])
            self.assertIn("02:381", res["message"])

            targets = {t["coords"]: t for t in h.api.send_command({"command": "db_summary"})["db"]["spy_targets"]}
            self.assertIn("02:381:05", targets)
            self.assertEqual(targets["02:381:05"]["status_color"], "yellow")
            self.assertIn("02:381:11", targets)
            self.assertTrue(targets["02:381:11"]["is_spacia"])
            self.assertIn("internalGalaxy", h.driver.frame_url("F_Hauptframe"))

    def test_r2a_t1_03_scan_galaxy_defaults_to_origin_planet_coords(self) -> None:
        planets = _r2_default_planets(coords="03:115:07")
        gal = {
            (3, 115): [
                {
                    "position": 4,
                    "name": "Sys3Target",
                    "owner": "Sleeper3",
                    "status_color": "yellow",
                    "has_debris": False,
                    "is_spacia": False,
                    "spy_href": None,
                },
            ]
        }
        srv = FakeSpaceInvasionServer(planets=planets, galaxy_systems=gal)
        with E2ETestHarness(server=srv) as h:
            res = h.api.send_command({"command": "scan_galaxy", "dry_run": False})
            self.assertTrue(res["executed"])
            self.assertIn("03:115", res["message"])
            coords = {t["coords"] for t in h.api.send_command({"command": "db_summary"})["db"]["spy_targets"]}
            self.assertIn("03:115:04", coords)
            self.assertFalse(any(c.startswith("01:001:") for c in coords))

    def test_r2a_t1_04_navigate_galaxy_persists_resolved_coords_not_01_001(self) -> None:
        gal = {
            (2, 385): [
                {
                    "position": 8,
                    "name": "Outpost385",
                    "owner": "Idle385",
                    "status_color": "yellow",
                    "has_debris": False,
                    "is_spacia": False,
                    "spy_href": None,
                },
            ]
        }
        srv = FakeSpaceInvasionServer(galaxy_systems=gal)
        with E2ETestHarness(server=srv) as h:
            res = h.api.send_command({"command": "navigate", "action": "internalGalaxy", "galaxy": 2, "system": 385})
            self.assertTrue(res["ok"])
            self.assertGreaterEqual(res["scanned_targets"], 1)
            coords = {t["coords"] for t in h.api.send_command({"command": "db_summary"})["db"]["spy_targets"]}
            self.assertIn("02:385:08", coords)
            self.assertFalse(any(c.startswith("01:001:") for c in coords))

    def test_r2a_t1_05_galaxy_color_and_spacia_classification(self) -> None:
        gal = {
            (2, 380): [
                {
                    "position": 2,
                    "name": "YellowTarget",
                    "owner": "Idle",
                    "status_color": "yellow",
                    "has_debris": False,
                    "is_spacia": False,
                    "spy_href": "indexInternal.es?action=internalFleet&mission=spio&gal=2&sun=380&pla=2",
                },
                {
                    "position": 4,
                    "name": "Spacia World",
                    "owner": "Spacia",
                    "status_color": "yellow",
                    "has_debris": True,
                    "is_spacia": True,
                    "spy_href": None,
                },
                {
                    "position": 6,
                    "name": "ActiveAZ",
                    "owner": "Rival",
                    "status_color": "light_red",
                    "has_debris": False,
                    "is_spacia": False,
                    "spy_href": None,
                },
                {
                    "position": 8,
                    "name": "StrongAcct",
                    "owner": "Titan",
                    "status_color": "dark_red",
                    "has_debris": False,
                    "is_spacia": False,
                    "spy_href": None,
                },
                {
                    "position": 10,
                    "name": "Vacation",
                    "owner": "Vac",
                    "status_color": "blue",
                    "has_debris": False,
                    "is_spacia": False,
                    "spy_href": None,
                },
                {
                    "position": 11,
                    "name": "Ally",
                    "owner": "Mate",
                    "status_color": "green",
                    "has_debris": False,
                    "is_spacia": False,
                    "spy_href": None,
                },
                {
                    "position": 13,
                    "name": "Ghost",
                    "owner": "Spec",
                    "status_color": "white",
                    "has_debris": False,
                    "is_spacia": False,
                    "spy_href": None,
                },
            ]
        }
        srv = FakeSpaceInvasionServer(galaxy_systems=gal)
        cfg = StrategyConfig(allow_pvp_in_az=False, allow_raids=True)
        with E2ETestHarness(server=srv, config=cfg) as h:
            h.api.get_state(include_buildings=True)
            h.api.send_command({"command": "scan_galaxy", "galaxy": 2, "system": 380, "dry_run": False})
            by_coords = {t["coords"]: t for t in h.api.send_command({"command": "db_summary"})["db"]["spy_targets"]}
            # Free positions (e.g., 1, 3, 5) are excluded
            self.assertNotIn("02:380:01", by_coords)
            self.assertTrue(by_coords["02:380:02"]["is_inactive"])
            self.assertTrue(by_coords["02:380:04"]["is_inactive"])
            self.assertTrue(by_coords["02:380:04"]["is_spacia"])
            self.assertEqual(by_coords["02:380:06"]["status_color"], "light_red")
            self.assertEqual(by_coords["02:380:08"]["status_color"], "dark_red")
            self.assertEqual(by_coords["02:380:10"]["status_color"], "blue")
            self.assertEqual(by_coords["02:380:11"]["status_color"], "green")
            self.assertEqual(by_coords["02:380:13"]["status_color"], "white")

            plan = h.api.send_command({"command": "plan"})["actions"]
            atk_targets = [a["target_name"] for a in plan if a["kind"] == "ATTACK"]
            for protected in ("02:380:06", "02:380:08", "02:380:10", "02:380:11", "02:380:13"):
                self.assertFalse(
                    any(protected in t for t in atk_targets), f"Protected slot {protected} recommended for attack"
                )

    def test_r2a_t1_06_scan_galaxy_dry_run_makes_zero_server_views(self) -> None:
        srv = FakeSpaceInvasionServer()
        with E2ETestHarness(server=srv) as h:
            h.api.get_state(include_buildings=True)
            srv.page_view_log.clear()
            before_targets = h.api.send_command({"command": "db_summary"})["db"]["spy_targets"]

            res = h.api.send_command({"command": "scan_galaxy", "galaxy": 2, "system": 380, "dry_run": True})
            self.assertTrue(res["ok"])
            self.assertFalse(res["executed"])
            self.assertTrue(res["dry_run"])
            gal_views = [v for v in srv.page_view_log if v["action"] == "internalGalaxy"]
            self.assertEqual(len(gal_views), 0)
            after_targets = h.api.send_command({"command": "db_summary"})["db"]["spy_targets"]
            self.assertEqual(after_targets, before_targets)

    # ------------------------------------------------------------------
    # Area R2-B: Page-View Rate-Limit Prevention, Cooldown & Recovery
    # ------------------------------------------------------------------

    def test_r2b_t1_01_shared_pacing_across_fresh_clients(self) -> None:
        clock = FakeClock()
        srv = FakeSpaceInvasionServer(clock=clock)
        with E2ETestHarness(server=srv, clock=clock, min_nav_delay=(1.0, 1.0)) as h:
            srv.page_view_log.clear()
            clock.sleep_calls.clear()
            for action in ("internalBuildings", "internalEconomy", "internalFleet"):
                res = h.api.send_command({"command": "navigate", "action": action})
                self.assertTrue(res["ok"])
            haupt_views = [v for v in srv.page_view_log if v["frame"] == "F_Hauptframe"]
            self.assertEqual(len(haupt_views), 3)
            self.assertGreaterEqual(len(clock.sleep_calls), 2, f"Expected pacing sleeps: {clock.sleep_calls}")
            self.assertGreaterEqual(
                sum(clock.sleep_calls), 1.4, f"Expected cumulative pacing sleep >=1.4s: {clock.sleep_calls}"
            )
            deltas = [haupt_views[i]["ts"] - haupt_views[i - 1]["ts"] for i in range(1, len(haupt_views))]
            for d in deltas:
                self.assertGreaterEqual(d, 0.70, f"Inter-request nav delta {d:.4f}s < 0.70s: {deltas}")

    def test_r2b_t1_02_rate_limit_error_returns_structured_payload_and_keeps_in_game(self) -> None:
        srv = FakeSpaceInvasionServer()
        with E2ETestHarness(server=srv) as h:
            h.api.get_state(include_buildings=True)
            srv.force_rate_limit_remaining = 1
            resp = requests.post(
                f"{h.base_url}/command",
                json={"command": "scan_galaxy", "galaxy": 2, "system": 380, "dry_run": False},
                timeout=5.0,
            )
            self.assertIn(resp.status_code, (429, 503))
            body = resp.json()
            self.assertFalse(body["ok"])
            self.assertTrue(body.get("rate_limited"), f"Expected rate_limited=True in {body}")
            self.assertGreater(float(body.get("retry_after") or 0), 0.0)

            health = requests.get(f"{h.base_url}/health", timeout=5.0).json()
            self.assertTrue(health["in_game"], f"Rate limit must preserve in_game=True: {health}")
            self.assertTrue(health.get("rate_limited"))

            dash = requests.get(f"{h.base_url}/dashboard?refresh=0", timeout=5.0).json()
            self.assertTrue(dash["health"]["in_game"])
            self.assertTrue(dash["health"].get("rate_limited"))

    def test_r2b_t1_03_active_cooldown_rejects_without_hitting_server(self) -> None:
        srv = FakeSpaceInvasionServer(server_cooldown_s=45.0)
        with E2ETestHarness(server=srv) as h:
            h.api.get_state(include_buildings=True)
            srv.force_rate_limit_remaining = 1
            r1 = requests.post(
                f"{h.base_url}/command",
                json={"command": "scan_galaxy", "galaxy": 2, "system": 380, "dry_run": False},
                timeout=5.0,
            )
            self.assertIn(r1.status_code, (429, 503))
            srv.clear_throttle()
            views_before = len(srv.page_view_log)

            r_state = requests.get(f"{h.base_url}/state?include_buildings=1", timeout=5.0)
            self.assertIn(r_state.status_code, (429, 503))
            self.assertTrue(r_state.json().get("rate_limited"))

            r_scan = requests.post(
                f"{h.base_url}/command",
                json={"command": "scan_galaxy", "galaxy": 2, "system": 380, "dry_run": False},
                timeout=5.0,
            )
            self.assertIn(r_scan.status_code, (429, 503))
            self.assertTrue(r_scan.json().get("rate_limited"))
            self.assertEqual(
                len(srv.page_view_log),
                views_before,
                "Cooldown made live requests to server instead of short-circuiting",
            )

    def test_r2b_t1_04_cooldown_expiry_recovers_without_ensure_session(self) -> None:
        clock = FakeClock()
        srv = FakeSpaceInvasionServer(clock=clock, server_cooldown_s=45.0)
        with E2ETestHarness(server=srv, clock=clock) as h:
            h.api.get_state(include_buildings=True)
            srv.force_rate_limit_remaining = 1
            r1 = requests.post(
                f"{h.base_url}/command",
                json={"command": "scan_galaxy", "galaxy": 2, "system": 380, "dry_run": False},
                timeout=5.0,
            )
            self.assertIn(r1.status_code, (429, 503))
            self.assertTrue(r1.json().get("rate_limited"))

            clock.advance(65.0)
            srv.clear_throttle()

            scan_ok = h.api.send_command({"command": "scan_galaxy", "galaxy": 2, "system": 380, "dry_run": False})
            self.assertTrue(scan_ok["executed"])
            health = h.api.health()
            self.assertTrue(health["in_game"])
            self.assertFalse(health.get("rate_limited"))
            self.assertEqual(len(h.ensure_session_calls), 0)

    def test_r2b_t1_05_commander_backs_off_full_retry_after_and_recovers(self) -> None:
        clock = FakeClock()
        srv = FakeSpaceInvasionServer(clock=clock, server_cooldown_s=35.0, rate_limit_on_frame="F_Hauptframe")
        with E2ETestHarness(server=srv, clock=clock) as h:
            srv.force_rate_limit_remaining = 1
            rc = commander.main(
                [
                    "--api-url",
                    h.base_url,
                    "--loop",
                    "--interval",
                    "20",
                    "--max-ticks",
                    "2",
                    "--config",
                    str(h.config_path),
                    "--log-dir",
                    str(h.log_dir),
                ]
            )
            self.assertEqual(rc, 0)
            self.assertTrue(
                any(s >= 45.0 for s in clock.sleep_calls),
                f"Expected Commander rate-limit backoff >= 45s (got sleep_calls={clock.sleep_calls})",
            )
            self.assertEqual(len(h.ensure_session_calls), 0)
            cmd_log = (h.log_dir / "commander.log").read_text(encoding="utf-8")
            self.assertIn("ACTUAL:", cmd_log)
            self.assertIn("NEXT:", cmd_log)

    def test_r2b_t1_06_scan_galaxy_and_attack_minimize_redundant_views(self) -> None:
        planets = _r2_default_planets(ships={"s4": 20, "s1": 5})
        srv = FakeSpaceInvasionServer(planets=planets)
        cfg = StrategyConfig(allow_galaxy_scan=True, allow_raids=True)
        with E2ETestHarness(server=srv, config=cfg) as h:
            h.api.get_state(include_buildings=True)
            srv.page_view_log.clear()

            scan_res = h.api.send_command({"command": "scan_galaxy", "galaxy": 2, "system": 380, "dry_run": False})
            self.assertTrue(scan_res["executed"])
            self.assertLessEqual(
                len(srv.page_view_log),
                2,
                f"scan_galaxy burst {len(srv.page_view_log)} server views: {srv.page_view_log}",
            )
            self.assertFalse(
                any(v["action"] == "internalBuildings" for v in srv.page_view_log),
                "scan_galaxy unnecessarily invoked read_full_snapshot(include_buildings=True)",
            )

            srv.page_view_log.clear()
            atk_res = h.api.send_command({"command": "attack", "target_coords": "02:380:04", "dry_run": False})
            self.assertTrue(atk_res["executed"])
            self.assertFalse(
                any(v["action"] == "internalWeaponFactory" for v in srv.page_view_log),
                "attack unnecessarily fetched internalWeaponFactory",
            )

    # ------------------------------------------------------------------
    # Area R2-C: Exploration Sweep, Spy Intelligence, Attack & Safety
    # ------------------------------------------------------------------

    def test_r2c_t1_01_multi_system_exploration_sweep_advances_systems(self) -> None:
        gal = {
            (2, 380): [
                {
                    "position": 12,
                    "name": "Homeworld",
                    "owner": "ventkx86",
                    "status_color": "white",
                    "has_debris": False,
                    "is_spacia": False,
                    "spy_href": None,
                },
            ],
            (2, 381): [
                {
                    "position": 6,
                    "name": "RichIdle381",
                    "owner": "Idle381",
                    "status_color": "yellow",
                    "has_debris": False,
                    "is_spacia": False,
                    "spy_href": None,
                },
            ],
            (2, 379): [
                {
                    "position": 5,
                    "name": "RichIdle379",
                    "owner": "Idle379",
                    "status_color": "yellow",
                    "has_debris": False,
                    "is_spacia": False,
                    "spy_href": None,
                },
            ],
        }
        planets = _r2_default_planets()
        planets[12476]["resources"] = [0, 0, 0, 0]
        srv = FakeSpaceInvasionServer(planets=planets, galaxy_systems=gal)
        cfg = StrategyConfig(allow_galaxy_scan=True, allow_raids=True, auto_throttle_blast_furnace=False)
        with E2ETestHarness(server=srv, config=cfg) as h:
            h.api.get_state(include_buildings=True)
            # Tick 1 scans 02:380 (finds no raid targets)
            res1 = h.api.send_command({"command": "scan_galaxy", "galaxy": 2, "system": 380, "dry_run": False})
            self.assertTrue(res1["executed"])

            # Next planner decision must advance to neighboring system instead of looping [02:380]
            plan = h.api.send_command({"command": "plan"})["actions"]
            scans = [a for a in plan if a["kind"] == "SCAN_GALAXY" and a["executable_now"]]
            self.assertGreater(len(scans), 0, f"Expected SCAN_GALAXY sweep candidate; got {plan}")
            self.assertNotIn(
                "02:380", scans[0]["target_name"], f"Scanner looped on already-scanned [02:380]: {scans[0]}"
            )

            # Execute planned scan via public commander tick & verify neighbor target discovered
            _st, act, res2, _nxt, _out = commander.run_commander_tick(
                h.api,
                live=True,
                max_actions=1,
                config=cfg,
            )
            self.assertIsNotNone(act)
            self.assertEqual(act.kind, "SCAN_GALAXY")
            self.assertIsNotNone(res2)
            assert res2 is not None
            self.assertTrue(res2["executed"])
            coords = {t["coords"] for t in h.api.send_command({"command": "db_summary"})["db"]["spy_targets"]}
            self.assertTrue("02:381:06" in coords or "02:379:05" in coords, f"Neighbor target not discovered: {coords}")

    def test_r2c_t1_02_live_spy_recon_populates_spy_targets(self) -> None:
        spy_reports = {
            "02:380:04": {
                "owner": "SleepyOne",
                "resources": {"pig_iron": 30000, "metal": 20000, "kryptonite": 10000, "spice": 4000},
                "fleet": {},
                "defense": {"d1": 1},
                "dug_in": False,
                "is_spacia": False,
            }
        }
        planets = _r2_default_planets()
        planets[12476]["buildings"]["b10"] = 2
        srv = FakeSpaceInvasionServer(planets=planets, spy_reports=spy_reports)
        cfg = StrategyConfig(allow_galaxy_scan=True, allow_raids=True)
        with E2ETestHarness(server=srv, config=cfg) as h:
            h.api.get_state(include_buildings=True)
            h.api.send_command({"command": "scan_galaxy", "galaxy": 2, "system": 380, "dry_run": False})

            spy_res = h.api.send_command(
                {"command": "spy", "target_coords": "02:380:04", "planet_id": 12476, "probe_count": 1, "dry_run": False}
            )
            self.assertTrue(spy_res["ok"])
            self.assertTrue(spy_res["executed"])
            sync_res = h.api.send_command({"command": "sync_messages"})
            self.assertGreaterEqual(sync_res.get("synced_reports", 0), 1)

            targets = {t["coords"]: t for t in h.api.send_command({"command": "db_summary"})["db"]["spy_targets"]}
            t04 = targets["02:380:04"]
            self.assertEqual(t04["resources"]["pig_iron"], 30000)
            self.assertEqual(t04["resources"]["metal"], 20000)
            self.assertEqual(t04["resources"]["kryptonite"], 10000)
            self.assertEqual(t04["resources"]["spice"], 4000)
            self.assertEqual(t04["defense"].get("d1"), 1)

            plan = h.api.send_command({"command": "plan"})["actions"]
            atk = next((a for a in plan if a["kind"] == "ATTACK" and "02:380:04" in a["target_name"]), None)
            self.assertIsNotNone(atk)
            assert atk is not None
            self.assertTrue(atk["executable_now"])

    def test_r2c_t1_03_live_attack_dispatches_fleet_and_records_raid(self) -> None:
        spy_reports = {
            "02:380:04": {
                "owner": "SleepyOne",
                "resources": {"pig_iron": 20000, "metal": 20000, "kryptonite": 10000, "spice": 4000},
                "fleet": {},
                "defense": {"d1": 1},
                "dug_in": False,
                "is_spacia": False,
            }
        }
        planets = _r2_default_planets(ships={"s4": 20, "s1": 5})
        srv = FakeSpaceInvasionServer(planets=planets, spy_reports=spy_reports)
        cfg = StrategyConfig(allow_raids=True)
        with E2ETestHarness(server=srv, config=cfg) as h:
            h.api.get_state(include_buildings=True)
            h.api.send_command({"command": "scan_galaxy", "galaxy": 2, "system": 380, "dry_run": False})

            res = h.api.send_command({"command": "attack", "target_coords": "02:380:04", "dry_run": False})
            self.assertTrue(res["ok"])
            self.assertTrue(res["executed"])
            self.assertGreaterEqual(len(srv.launched_fleets), 1)
            last = srv.launched_fleets[-1]
            self.assertEqual(last["mission"], "1")
            self.assertEqual(last["target_coords"], "02:380:04")
            self.assertEqual(last["ships"], {"s4": 20, "s1": 5})

            # Raid history recorded with is_exempt=1 for yellow inactive
            assert h.bridge._db is not None
            with h.bridge._db._lock:
                rows = h.bridge._db._conn.execute(
                    "SELECT target_coords, is_exempt FROM raid_history ORDER BY id DESC LIMIT 1"
                ).fetchall()
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0]["target_coords"], "02:380:04")
            self.assertEqual(rows[0]["is_exempt"], 1)

    def test_r2c_t1_04_allow_raids_false_guards_live_attack(self) -> None:
        srv = FakeSpaceInvasionServer()
        cfg = StrategyConfig(allow_raids=False)
        with E2ETestHarness(server=srv, config=cfg) as h:
            h.api.get_state(include_buildings=True)
            dry_res = h.api.send_command({"command": "attack", "target_coords": "02:380:04", "dry_run": True})
            self.assertTrue(dry_res["ok"])
            self.assertTrue(dry_res["dry_run"])
            self.assertFalse(dry_res["executed"])

            live_res = h.api.send_command({"command": "attack", "target_coords": "02:380:04", "dry_run": False})
            self.assertTrue(live_res["ok"])
            self.assertFalse(live_res["executed"])
            self.assertIn("allow_raids", live_res["message"])
            self.assertEqual(len(srv.launched_fleets), 0)
            self.assertEqual(h.api.send_command({"command": "db_summary"})["db"]["counts"]["raid_history"], 0)

    def test_r2c_t1_05_update_config_syncs_allow_raids_and_max_actions_per_hour(self) -> None:
        srv = FakeSpaceInvasionServer()
        cfg = StrategyConfig(allow_raids=False, max_actions_per_hour=60)
        with E2ETestHarness(server=srv, config=cfg) as h:
            h.api.get_state(include_buildings=True)
            h.api.send_command({"command": "update_config", "config": {"allow_raids": True, "max_actions_per_hour": 2}})
            self.assertEqual(h.api.health()["remaining_hourly_actions"], 2)

            r1 = h.api.send_command({"command": "attack", "target_coords": "02:380:04", "dry_run": False})
            r2 = h.api.send_command({"command": "attack", "target_coords": "02:380:09", "dry_run": False})
            r3 = h.api.send_command({"command": "attack", "target_coords": "02:380:04", "dry_run": False})
            self.assertTrue(r1["executed"])
            self.assertTrue(r2["executed"])
            self.assertFalse(r3["executed"])
            self.assertIn("rate cap", r3["message"].lower())

    def test_r2c_t1_06_simulate_combat_enforces_1_4x_and_stealth_first_strike(self) -> None:
        with E2ETestHarness() as h:
            weak = h.api.send_command(
                {
                    "command": "simulate_combat",
                    "attacker_ships": {"s4": 2, "s1": 2},
                    "defender_defense": {"d1": 25},
                    "target_resources": {"pig_iron": 10000, "metal": 10000, "kryptonite": 10000, "spice": 4000},
                    "target_coords": "02:381:05",
                }
            )["simulation"]
            self.assertFalse(weak["safe_to_attack"])
            self.assertLess(weak["win_ratio"], 1.4)

            strong = h.api.send_command(
                {
                    "command": "simulate_combat",
                    "attacker_ships": {"s7": 10, "s3": 4, "s14": 2},
                    "defender_defense": {"d1": 25},
                    "target_resources": {"pig_iron": 10000, "metal": 10000, "kryptonite": 10000, "spice": 4000},
                    "target_coords": "02:381:05",
                }
            )["simulation"]
            self.assertTrue(strong["safe_to_attack"])
            self.assertGreaterEqual(strong["win_ratio"], 1.4)
            self.assertGreater(strong["stealth_first_strike_power"], 0)
            self.assertEqual(
                strong["expected_plunder"],
                {"pig_iron": 5000, "metal": 5000, "kryptonite": 5000, "spice": 2000},
            )
