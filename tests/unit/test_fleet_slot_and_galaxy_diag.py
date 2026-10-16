"""Regression tests for galaxy diagnostic sanitization, internalFleet slot parsing, and RECYCLE pre-dispatch guards."""

from __future__ import annotations

import io
from contextlib import redirect_stderr
import unittest
from unittest.mock import MagicMock, patch

from spaceinvasion.core.config import StrategyConfig
from spaceinvasion.core.model import (
    EnergyBalance,
    GameState,
    PlanetState,
    QueueJob,
    Resources,
)
from spaceinvasion.core.model import PlannedAction
from spaceinvasion.runtime.api_server import GameApiBridge
from spaceinvasion.session.client import GameBrowserClient
from spaceinvasion.session.executor import ActionExecutor


def _mk_planet(
    planet_id: int,
    coords: str,
    name: str,
    *,
    b10: int = 5,
    ships: dict[str, int] | None = None,
) -> PlanetState:
    return PlanetState(
        planet_id=planet_id,
        name=name,
        coords=coords,
        planet_type="normal",
        temperature=20,
        fields_used=40,
        fields_max=320,
        resources=Resources(100_000, 100_000, 100_000, 100_000),
        storage=Resources(500_000, 500_000, 500_000, 500_000),
        energy=EnergyBalance(2000, 500),
        buildings={"b1": 12, "b2": 10, "b3": 11, "b4": 9, "b5": 14, "b9": 4, "b10": b10},
        ships=dict(ships or {}),
    )


class TestFleetSlotAndGalaxyDiag(unittest.TestCase):
    def test_open_galaxy_with_coords_does_not_emit_open_galaxy_loaded_or_hidden_pageview_modal(self) -> None:
        sample_galaxy_html = """
        <html><body>
          <div id="pageview-error" style="display:none;">
            You've made more than <span id="pageview-amount"></span> page views at last minute.
            Please wait until you can continue:
          </div>
          <div id="starbase-modal" hidden>Imperial Starbase - explosion</div>
          <div class="galaxy-view">02:390:10 Colony (TargetPlayer)</div>
        </body></html>
        """
        driver = MagicMock()
        driver.__class__.__module__ = "selenium.webdriver.chrome.webdriver"
        driver.current_url = "https://spaceinvasion.bitmeup.com/indexGame.es"
        driver.window_handles = ["w1"]
        driver.execute_script.return_value = "https://spaceinvasion.bitmeup.com/indexInternal.es?action=internalGalaxy"

        client = GameBrowserClient(driver, detach_on_close=True)
        diag = client._capture_dom_diag(sample_galaxy_html)
        self.assertIsNone(diag["matched_made_more_than"])
        self.assertIsNone(diag["matched_page_views"])
        self.assertNotIn("made more than", diag["visible_text_preview"].lower())
        self.assertNotIn("Imperial Starbase", diag["visible_text_preview"])
        self.assertIn("02:390:10 Colony", diag["visible_text_preview"])

        stderr_buf = io.StringIO()
        with (
            patch.object(client, "ensure_in_game"),
            patch.object(client, "_resolve_sid_query", return_value="sid=testtoken123"),
            patch.object(client, "_switch_frame"),
            patch.object(client, "_open_frame_url_with_retry", return_value=sample_galaxy_html),
            patch.object(client, "_wait_galaxy_ajax_ready", return_value=sample_galaxy_html),
            patch.object(client, "_record_galaxy_universe_bounds", return_value=(8, 400)),
            patch("spaceinvasion.session.client.append_crash_debug_log") as mock_crash_log,
            redirect_stderr(stderr_buf),
        ):
            out_html, out_g, out_s = client.open_galaxy_with_coords(galaxy=2, system=390)

        self.assertIn("02:390:10", out_html)
        self.assertEqual((out_g, out_s), (2, 390))
        mock_crash_log.assert_not_called()
        self.assertEqual(stderr_buf.getvalue(), "")

    def test_executor_parses_filled_fleet_bases_and_recovers_wrong_planet_on_internal_fleet(self) -> None:
        p1 = _mk_planet(101, "02:380:08", "#TA Planet", b10=5, ships={"s10": 5, "s11": 0})
        p2 = _mk_planet(102, "02:381:07", "#TG Planet", b10=5, ships={"s11": 3})
        state = GameState(
            31,
            1,
            "cmdr",
            1000.0,
            (p1, p2),
            active_planet_id=102,
        )
        act = PlannedAction(
            priority="P2",
            kind="RECYCLE",
            planet_id=102,
            planet_coords="02:381:07",
            target_id=11,
            target_level=3,
            target_name="Rubble -> 02:390:10",
            reason="Harvest debris",
            executable_now=True,
        )

        # 1) Live RETRO `Fleet bases (available: 5, filled: 5)` reports observed slot cap (5/5) cleanly.
        full_html = """
        <html><body>
          <div>Only flights from here: #TG Planet [02:381:07]</div>
          <form name="fleet" action="indexInternal.es?action=internalSendFleet">
            <table>
              <tr><td>3</td><td>Recycler</td><td>Filled</td></tr>
              <tr><td colspan="3">Fleet bases (available: 5, filled: 5)</td></tr>
            </table>
            <input type="text" name="gal" value="" />
            <input type="text" name="sun" value="" />
            <input type="text" name="pla" value="" />
          </form>
        </body></html>
        """
        ex = ActionExecutor(dry_run=False, action_delay_range=(0.0, 0.0), allow_raids=True)
        client_full = MagicMock()
        client_full.driver.find_elements.return_value = []
        with patch.object(ex, "_navigate_and_select_planet", return_value=full_html) as mock_nav:
            res_full = ex.execute_action(client_full, state, act, active_planet_confirmed=True)
        self.assertFalse(res_full.executed)
        self.assertEqual(res_full.observed_fleet_slots_available, 5)
        self.assertEqual(res_full.observed_fleet_slots_filled, 5)
        self.assertIn("5/5 filled", res_full.message)
        self.assertIn("no ship input fields found on internalFleet", res_full.message)
        self.assertEqual(mock_nav.call_count, 1)

        # 2) Stale active_planet_confirmed=True lands on #TA Planet [02:380:08] (0 s11) -> auto-reconfirms to #TG [02:381:07]!
        wrong_planet_html = """
        <html><body>
          <div>Only flights from here: #TA Planet [02:380:08]</div>
          <form name="fleet" action="indexInternal.es?action=internalSendFleet">
            <input type="text" name="ship_10" value="0" />
            <div>Fleet bases (available: 5, filled: 2)</div>
            <input type="text" name="gal" value="" />
            <input type="text" name="sun" value="" />
            <input type="text" name="pla" value="" />
            <input type="submit" value="Continue" />
          </form>
        </body></html>
        """
        right_planet_html = """
        <html><body>
          <div>Only flights from here: #TG Planet [02:381:07]</div>
          <form name="fleet" action="indexInternal.es?action=internalSendFleet">
            <input type="text" name="ship_11" value="0" />
            <div>Fleet bases (available: 5, filled: 4)</div>
            <input type="text" name="gal" value="" />
            <input type="text" name="sun" value="" />
            <input type="text" name="pla" value="" />
            <input type="submit" value="Continue" />
          </form>
        </body></html>
        """
        step2_html = """
        <html><body>
          <form id="fleet" action="indexInternal.es?action=internalSendFleetFinish">
            <input type="radio" name="todo" value="RECYCLE" />
            <input id="goButton" type="button" onclick="startFleet()" value="LAUNCH" />
          </form>
        </body></html>
        """
        confirm_html = "<html><body>Mission started successfully!</body></html>"

        client_recover = MagicMock()
        ship_inp = MagicMock()
        ship_inp.get_attribute.return_value = "3"
        coord_inp = MagicMock()
        coord_inp.get_attribute.return_value = "2"
        radio_el = MagicMock()
        radio_el.get_attribute.side_effect = lambda attr: "RECYCLE" if attr == "value" else "todo"
        radio_el.is_selected.return_value = False
        btn_el = MagicMock()

        def _find_els(by, sel):
            if "ship_11" in str(sel):
                return [ship_inp]
            if "radio" in str(sel):
                return [radio_el]
            return []

        client_recover.driver.find_elements.side_effect = _find_els
        client_recover.driver.find_element.side_effect = lambda by, sel: (
            coord_inp if sel in ("gal", "sun", "pla") else btn_el
        )
        client_recover._wait_frame_ready.side_effect = [step2_html, confirm_html]
        updated_p2 = _mk_planet(102, "02:381:07", "#TG Planet", b10=5, ships={"s11": 0})
        client_recover.read_planets_overview.return_value = GameState(
            31,
            1,
            "cmdr",
            1005.0,
            (p1, updated_p2),
            active_planet_id=102,
        )

        nav_calls: list[bool] = []

        def _fake_nav(_c, _st, _menu, _pid, *, active_planet_confirmed=True):
            nav_calls.append(active_planet_confirmed)
            return wrong_planet_html if len(nav_calls) == 1 else right_planet_html

        with patch.object(ex, "_navigate_and_select_planet", side_effect=_fake_nav):
            res_ok = ex.execute_action(client_recover, state, act, active_planet_confirmed=True)

        self.assertEqual(nav_calls, [True, False])
        self.assertTrue(res_ok.executed)
        self.assertEqual(res_ok.observed_fleet_slots_available, 5)
        self.assertEqual(res_ok.observed_fleet_slots_filled, 5)
        self.assertIsNotNone(res_ok.updated_state)
        assert res_ok.updated_state is not None
        self.assertTrue(
            any("02:390:10" in str(q.name) and q.origin_coords == "02:381:07" for q in res_ok.updated_state.active_queues)
        )

    def test_bridge_learns_retro_b10_slot_cap_and_skips_recycle_galaxy_load_when_slots_full(self) -> None:
        p_tg = _mk_planet(12487, "02:381:07", "#TG Planet", b10=5, ships={"s11": 3})
        p_pp = _mk_planet(12476, "02:380:12", "#PP Planet", b10=7, ships={"s11": 8})
        queues = tuple(
            QueueJob(
                category="fleet",
                name=f"Recycle → [02:10{i}:07] Debris",
                remaining_text="00:15:00",
                remaining_seconds=900.0,
                detail="Outbound · 3 Recycler",
                origin_coords="02:381:07",
            )
            for i in range(5)
        ) + tuple(
            QueueJob(
                category="fleet",
                name=f"Recycle → [02:20{i}:07] Debris",
                remaining_text="00:20:00",
                remaining_seconds=1200.0,
                detail="Outbound · 4 Recycler",
                origin_coords="02:380:12",
            )
            for i in range(7)
        )
        state = GameState(
            31,
            1,
            "cmdr",
            2000.0,
            (p_tg, p_pp),
            active_planet_id=12487,
            active_queues=queues,
        )
        bridge = GameApiBridge(
            db_path=":memory:",
            log_dir=None,
            ensure_session_fn=lambda: None,
        )
        self.addCleanup(bridge.close)
        bridge.strategy_config = StrategyConfig(allow_raids=True, reserved_spy_slots=0)
        bridge._last_state = state

        # Simulate observing RETRO `Fleet bases (available: 5, filled: 5)` once on #TG (b10=5):
        bridge._record_observed_fleet_slots_locked(p_tg, 5)
        self.assertEqual(bridge._observed_fleet_slot_base_offset, 0)
        # Both #TG (b10=5 -> 5) and #PP (b10=7 -> 7) immediately recognize exact RETRO slot limits!
        self.assertEqual(bridge._max_fleet_slots_for_planet_locked(p_tg), 5)
        self.assertEqual(bridge._max_fleet_slots_for_planet_locked(p_pp), 7)

        rec_act = PlannedAction(
            priority="P2",
            kind="RECYCLE",
            planet_id=12487,
            planet_coords="02:381:07",
            target_id=11,
            target_level=3,
            target_name="Rubble -> 02:390:10",
            reason="Harvest debris",
            executable_now=True,
        )
        held = bridge._apply_fleet_slot_holds(state, [rec_act])
        self.assertFalse(held[0].executable_now)
        self.assertIn("5/5 in flight", held[0].reason)

        # Even if `recycle` command is called directly, `_execute_and_summarize` aborts BEFORE `open_galaxy`!
        fake_client = MagicMock()
        fake_client.open_galaxy = MagicMock(side_effect=AssertionError("open_galaxy should not be called!"))
        res = bridge._execute_and_summarize(fake_client, state, "recycle", rec_act, dry_run=False)
        self.assertFalse(res["executed"])
        self.assertIn("5/5", res["message"])
        fake_client.open_galaxy.assert_not_called()


if __name__ == "__main__":
    unittest.main()
