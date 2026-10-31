"""Self-tests verifying E2E harness isolation, FakeDriver/Select, FakeServer throttle, fixtures, and Node UI runner."""

from __future__ import annotations

import json
from pathlib import Path
import socket
import time as _real_time
import unittest
from unittest.mock import patch

from selenium.common.exceptions import (
    ElementClickInterceptedException,
    StaleElementReferenceException,
)
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import Select

from tests.e2e.fixtures import (
    evaluate_sequence_score,
    make_building_offer,
    make_game_state,
    make_planet,
    make_ship_offer,
)
from tests.e2e.harness import (
    E2ETestHarness,
    FakeClock,
    FakeDriver,
    FakeSpaceInvasionServer,
    inspect_ui_html,
    run_ui_in_node,
)
from spaceinvasion.session.client import (
    GameBrowserClient,
    RateLimitError,
)
from spaceinvasion.runtime.commander_client import GameApiError
from spaceinvasion.core.config import StrategyConfig
from spaceinvasion.core.model import Resources
from spaceinvasion.core.model import PlannedAction


class TestHarnessSelf(unittest.TestCase):
    def test_self_01_ephemeral_isolation_no_live_ports_or_workspace_writes(self) -> None:
        with E2ETestHarness() as h:
            self.assertNotIn(h.port, (8787, 9222))
            self.assertIsNotNone(h.tmp_dir)
            assert h.tmp_dir is not None
            self.assertTrue(h.config_path.exists())
            self.assertTrue(h.env_file.exists())
            self.assertNotEqual(h.config_path.resolve(), Path("config.json").resolve())
            self.assertNotEqual(h.env_file.resolve(), Path(".env").resolve())

            health = h.api.health()
            self.assertTrue(health["ok"])
            self.assertTrue(health["in_game"])
            self.assertEqual(health["debug_port"], 0)

            state = h.api.get_state(include_buildings=True)
            self.assertEqual(state.universe_id, 31)
            self.assertTrue(h.db_path.exists())
            tmp_path = h.tmp_dir

        self.assertFalse(tmp_path.exists())

    def test_self_02_fake_driver_select_and_async_nav_and_fault_injection(self) -> None:
        clock = FakeClock()
        srv = FakeSpaceInvasionServer(clock=clock)
        drv = FakeDriver(srv)
        client = GameBrowserClient(drv, min_nav_delay=(0.0, 0.0), timeout=5.0)
        client.ensure_in_game()

        # Selenium Select compatibility on internalEconomy
        html_econ = client.navigate_menu("internalEconomy")
        self.assertIn("resource2", html_econ)
        client._switch_frame("F_Hauptframe")
        sel = Select(drv.find_element(By.NAME, "resource2"))
        sel.select_by_value("0")
        drv.find_element(By.CSS_SELECTOR, 'form#ress input[type="submit"]').click()
        after_econ = client._wait_frame_ready("F_Hauptframe")
        self.assertIn('value="0" selected', after_econ)
        self.assertEqual(srv.planets[12476]["economy_rates"]["resource2"], 0)

        # ElementClickInterceptedException & StaleElementReferenceException injection
        srv.inject_intercepted_clicks = 1
        submit_link = drv.find_element(By.CSS_SELECTOR, 'form#ress input[type="submit"]')
        with self.assertRaises(ElementClickInterceptedException):
            submit_link.click()
        # JS click bypasses interception cleanly
        drv.execute_script("arguments[0].click();", submit_link)
        self.assertIn("internalEconomy", drv.frame_url("F_Hauptframe"))
        self.assertIn("resource2", drv.frame_html("F_Hauptframe"))

        # Galaxy coordinate <form> & form_data work under sid_mode="empty"
        srv_empty = FakeSpaceInvasionServer(clock=clock, sid_mode="empty", p_galaxy_mode="in_frame")
        drv_empty = FakeDriver(srv_empty)
        c_empty = GameBrowserClient(drv_empty, min_nav_delay=(0.0, 0.0), timeout=5.0)
        g_html, g_gal, g_sys = c_empty.open_galaxy_with_coords(galaxy=2, system=381)
        self.assertEqual((g_gal, g_sys), (2, 381))
        self.assertIn("Galaxy [02:381]", g_html)
        self.assertIn("DustyMoon", g_html)
        self.assertIn("gal=2&sun=381&pla=5", g_html)
        self.assertNotIn("sid=", g_html)

        srv.inject_stale_clicks = 1
        fresh_btn = drv.find_element(By.CSS_SELECTOR, 'form#ress input[type="submit"]')
        with self.assertRaises(StaleElementReferenceException):
            fresh_btn.click()
        # Old handle remains stale after gen_id increment
        with self.assertRaises(StaleElementReferenceException):
            fresh_btn.is_enabled()

        # async_nav_polls simulates multi-poll loading delay before frame commits
        srv.async_nav_polls = 2
        client._switch_frame("F_Hauptframe")
        drv.execute_script("location.href = arguments[0];", "indexInternal.es?action=internalBuildings")
        self.assertEqual(drv.execute_script("return document.readyState;"), "loading")
        self.assertEqual(drv.execute_script("return document.readyState;"), "loading")
        self.assertEqual(drv.execute_script("return document.readyState;"), "complete")

        # Also verify GameBrowserClient.navigate_menu awaits multi-poll transitions transparently
        b_html = client.navigate_menu("internalBuildings")
        self.assertIn("Pig iron mine", b_html)

    def test_self_03_fake_server_rolling_throttle_and_stale_f_planets(self) -> None:
        import spaceinvasion.session.client as client_mod

        clock = FakeClock()
        srv = FakeSpaceInvasionServer(clock=clock, rate_limit_threshold=3, server_cooldown_s=50.0)
        drv = FakeDriver(srv)
        lim = (
            client_mod.PageViewLimiter(
                min_delay_range=(0.0, 0.0),
                clock=clock.monotonic,
                sleeper=clock.sleep,
            )
            if hasattr(client_mod, "PageViewLimiter")
            else None
        )
        client_kwargs = {"min_nav_delay": (0.0, 0.0)}
        if lim is not None:
            client_kwargs["page_limiter"] = lim
        # Constructor consumed 3 views across F_Navi, F_planets, F_Hauptframe
        # Next view exceeds threshold=3 within rolling 60s window
        with self.assertRaises(RateLimitError):
            GameBrowserClient(drv, **client_kwargs).navigate_menu("internalBuildings")

        # Cooldown remains active until FakeClock advances past cooldown & 60s window
        clock.advance(61.0)
        srv.force_rate_limit_remaining = 0
        client = GameBrowserClient(drv, **client_kwargs)
        b_html = client.navigate_menu("internalBuildings")
        self.assertIn("Pig iron mine", b_html)

        # Verify stale F_planets snapshot until location.reload()
        before_json = client.read_planets_overview(refresh=False)
        before_pig = before_json.planets[0].resources.pig_iron
        # Mutate server via direct build click in F_Hauptframe
        client._switch_frame("F_Hauptframe")
        drv.find_element(By.CSS_SELECTOR, 'a.bau[href*="buildingID=1"]').click()
        stale_read = client.read_planets_overview(refresh=False)
        self.assertEqual(stale_read.planets[0].resources.pig_iron, before_pig)
        fresh_read = client.read_planets_overview(refresh=True)
        self.assertLess(fresh_read.planets[0].resources.pig_iron, before_pig)

    def test_self_04_fixtures_and_independent_sequence_evaluator(self) -> None:
        # Cold spice planet (-120C) + Mining tech + Blast furnace 2:1 check
        cold = make_planet(
            planet_id=101,
            coords="02:380:15",
            temperature=-120,
            resources=Resources(5000, 5000, 5000, 1000),
            buildings={"b1": 4, "b2": 3, "b3": 4, "b4": 4, "b5": 8, "b9": 1},
            ship_offers=(make_ship_offer(9, owned=0),),
            auto_offers=True,
        )
        warm = make_planet(
            planet_id=102,
            coords="02:380:02",
            temperature=100,
            resources=Resources(5000, 5000, 5000, 1000),
            buildings={"b1": 4, "b2": 3, "b3": 4, "b4": 4, "b5": 8, "b9": 1},
            auto_offers=True,
        )
        state = make_game_state(planets=(cold, warm), researches={"r1": 5}, speedup_factor=1.0)
        act_cold = PlannedAction(
            "P2", "BUILD", 101, "02:380:15", "Spice mine", "test", target_id=4, target_level=5, executable_now=True
        )
        act_warm = PlannedAction(
            "P2", "BUILD", 102, "02:380:02", "Spice mine", "test", target_id=4, target_level=5, executable_now=True
        )

        eval_cold = evaluate_sequence_score(state, [act_cold], config=StrategyConfig())
        eval_warm = evaluate_sequence_score(state, [act_warm], config=StrategyConfig())
        self.assertTrue(eval_cold.legal)
        self.assertTrue(eval_warm.legal)
        self.assertGreater(eval_cold.final_income_spice_eq, eval_warm.final_income_spice_eq)

        # Illegal asteroid mine detected as violation
        astro = make_planet(
            planet_id=200, coords="02:380:05", planet_type="asteroid", fields_max=30, buildings={"b7": 2}
        )
        astro_state = make_game_state(planets=(astro,))
        bad_act = PlannedAction(
            "P2",
            "BUILD",
            200,
            "02:380:05",
            "Pig iron mine",
            "illegal",
            target_id=1,
            target_level=1,
            executable_now=True,
        )
        eval_bad = evaluate_sequence_score(astro_state, [bad_act])
        self.assertFalse(eval_bad.legal)
        self.assertGreaterEqual(len(eval_bad.violations), 1)

    def test_self_05_real_ui_in_node_loads_and_interacts_with_live_http_api(self) -> None:
        with E2ETestHarness() as h:
            h.api.get_state(include_buildings=True)
            dash = h.get_dashboard(refresh=0)
            self.assertTrue(dash["ok"])
            res = run_ui_in_node(
                h.base_url,
                """
                const ok = await __waitUntil(() => __serializeTable("dash-plan-tbody").length > 0 && !document.querySelector("#dash-plan-tbody td.empty-cell"), 4000);
                if (!ok) throw new Error("Timed out waiting for dash-plan-tbody");
                document.querySelector('[data-page="operations"]').click();
                const opsOk = await __waitUntil(() => !document.getElementById("view-operations").hidden, 2000);
                if (!opsOk) throw new Error("Operations view remained hidden");
                const probe = document.createElement("div");
                probe.style.setProperty("display", "none");
                const propDisplay = probe.style.getPropertyValue("display");
                const inlineAttr = probe.getAttribute("style");
                const dashRows = __serializeTable("dash-plan-tbody");
                const planRows = __serializeTable("plan-tbody");
                const opsHidden = document.getElementById("view-operations").hidden;
                const badge = __snapshotElement("plan-count-badge");
                return { dashRows, planRows, opsHidden, badge, propDisplay, inlineAttr };
                """,
            )
            self.assertEqual(res["errors"], [])
            sc = res["scenario"]
            self.assertFalse(sc["opsHidden"])
            self.assertEqual(sc["propDisplay"], "none")
            self.assertIn("display: none", sc["inlineAttr"])
            self.assertGreaterEqual(len(sc["dashRows"]), 1)
            self.assertGreaterEqual(len(sc["planRows"]), 1)
            self.assertIn("TASKS", sc["badge"]["textContent"])
            self.assertTrue(res["htmlInspection"]["dash_plan"]["found"])
            self.assertTrue(res["htmlInspection"]["ops_plan"]["found"])

    def test_self_06_bridge_restart_persists_sqlite_and_config_in_same_tempdir(self) -> None:
        with E2ETestHarness() as h:
            h.api.get_state(include_buildings=True)
            if h.page_limiter is not None:
                before_views = h.api.health()["page_views_last_60s"]
                h.page_limiter.record_view(1)
                self.assertEqual(h.api.health()["page_views_last_60s"], before_views + 1)
            h.api.send_command(
                {"command": "update_config", "config": {"playstyle": "miner", "max_actions_per_hour": 19}}
            )
            h.api.send_command({"command": "scan_galaxy", "galaxy": 2, "system": 381, "dry_run": False})
            port_before = h.port
            h.restart_bridge()
            if h.page_limiter is not None:
                after_views = h.api.health()["page_views_last_60s"]
                h.page_limiter.record_view(1)
                self.assertEqual(h.api.health()["page_views_last_60s"], after_views + 1)
            cfg = h.api.send_command({"command": "config"})["config"]
            self.assertEqual(cfg["playstyle"], "miner")
            self.assertEqual(cfg["max_actions_per_hour"], 19)
            db_sum = h.api.send_command({"command": "db_summary"})["db"]
            coords = {t["coords"] for t in db_sum["spy_targets"]}
            self.assertIn("02:381:05", coords)
            self.assertGreater(h.port, 0)
            self.assertNotEqual(port_before, 8787)

            # Default executor_max_limiter_wait=None allows FakeClock rollover when view #25 lands in ActionExecutor._pace_action
            if h.page_limiter is not None:
                h.clock.advance(61.0)
                h.api.get_state(include_buildings=True)
                while h.page_limiter.views_last_60s() < 23:
                    h.page_limiter.record_view(1)
                h.clock.sleep_calls.clear()
                rollover_build = h.api.send_command({"command": "build", "building_id": 1, "dry_run": False})
                self.assertTrue(rollover_build["executed"], rollover_build)
                self.assertTrue(
                    any(s >= 50.0 for s in h.clock.sleep_calls),
                    f"Expected FakeClock 60s rollover sleep in ActionExecutor; got {h.clock.sleep_calls}",
                )

        # Explicit executor_max_limiter_wait=8.0 fails fast on view #25 in ActionExecutor both before & after restart_bridge()
        with E2ETestHarness(executor_max_limiter_wait=8.0) as h8:
            self.assertEqual(h8.executor_max_limiter_wait, 8.0)
            if h8.page_limiter is not None:
                h8.api.get_state(include_buildings=True)
                while h8.page_limiter.views_last_60s() < 23:
                    h8.page_limiter.record_view(1)
                with self.assertRaisesRegex(GameApiError, "Page-view budget reached"):
                    h8.api.send_command({"command": "build", "building_id": 1, "dry_run": False})

                h8.restart_bridge()
                h8.clock.advance(61.0)
                h8.api.get_state(include_buildings=True)
                while h8.page_limiter.views_last_60s() < 23:
                    h8.page_limiter.record_view(1)
                with self.assertRaisesRegex(GameApiError, "Page-view budget reached"):
                    h8.api.send_command({"command": "build", "building_id": 1, "dry_run": False})

    def test_self_07_enter_exception_rolls_back_patches_and_tmpdir(self) -> None:
        import spaceinvasion.runtime.api_server as api_server_mod
        import spaceinvasion.session.client as client_mod
        import spaceinvasion.combat._rank_history as rank_history_mod

        orig_client_time = client_mod.time
        orig_ranking_time = rank_history_mod.time
        orig_dbg_fn = api_server_mod.is_debug_port_open
        orig_default_lim = getattr(client_mod, "_DEFAULT_PAGE_VIEW_LIMITER", None)
        orig_cls_lim = getattr(GameBrowserClient, "page_limiter", None)

        h = E2ETestHarness()
        created_tmp: list[Path] = []
        orig_start = h._start_bridge_and_server

        def _boom() -> None:
            if h.tmp_dir is not None:
                created_tmp.append(h.tmp_dir)
            raise RuntimeError("Simulated setup failure in __enter__")

        with patch.object(h, "_start_bridge_and_server", side_effect=_boom):
            with self.assertRaisesRegex(RuntimeError, "Simulated setup failure"):
                h.__enter__()

        self.assertIs(client_mod.time, orig_client_time)
        self.assertIs(rank_history_mod.time, orig_ranking_time)
        self.assertIs(api_server_mod.is_debug_port_open, orig_dbg_fn)
        self.assertIs(getattr(client_mod, "_DEFAULT_PAGE_VIEW_LIMITER", None), orig_default_lim)
        self.assertIs(getattr(GameBrowserClient, "page_limiter", None), orig_cls_lim)
        self.assertIs(_real_time, orig_client_time)
        self.assertIsNone(h.tmp_dir)
        self.assertEqual(len(created_tmp), 1)
        self.assertFalse(created_tmp[0].exists())
        del orig_start
