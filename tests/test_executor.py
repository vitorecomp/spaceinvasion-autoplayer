"""Unit tests for safe Chrome ActionExecutor."""

from __future__ import annotations

import unittest
from unittest.mock import MagicMock

from selenium.common.exceptions import StaleElementReferenceException

from spaceinvasion.client import GameBrowserClient
from spaceinvasion.executor import ActionExecutor
from spaceinvasion.state import BuildingOffer, EnergyBalance, GameState, PlanetState, Resources
from spaceinvasion.strategy import FUSION_PLANT, PlannedAction


def _make_state_and_action() -> tuple[GameState, PlannedAction]:
    offer = BuildingOffer(
        building_id=FUSION_PLANT,
        name="Fusion plant",
        current_level=0,
        target_level=1,
        cost=Resources(80, 0, 35, 0),
        can_build=True,
        status_text="Build level 1",
        build_href="indexInternal.es?action=internalBuildItem&buildingID=5&sid=abc#5",
    )
    planet = PlanetState(
        planet_id=12476,
        name="Colony",
        coords="02:380:12",
        planet_type="normal",
        temperature=31,
        fields_used=0,
        fields_max=230,
        resources=Resources(1200, 0, 800, 0),
        storage=Resources(100000, 100000, 100000, 100000),
        energy=EnergyBalance(0, 0),
        buildings={"b5": 0},
        building_offers=(offer,),
    )
    state = GameState(
        universe_id=31,
        user_id=103603,
        nickname="ventkx86",
        timestamp=0.0,
        planets=(planet,),
        active_planet_id=12476,
    )
    action = PlannedAction(
        priority="P2",
        kind="BUILD",
        planet_id=12476,
        planet_coords="02:380:12",
        target_name="Fusion plant",
        reason="Tutorial 1/11",
        target_id=FUSION_PLANT,
        target_level=1,
        cost=Resources(80, 0, 35, 0),
        executable_now=True,
        action_href=offer.build_href,
    )
    return state, action


class TestActionExecutor(unittest.TestCase):
    def test_dry_run_never_navigates_or_clicks(self) -> None:
        state, action = _make_state_and_action()
        client = MagicMock()
        executor = ActionExecutor(dry_run=True)

        result = executor.execute_action(client, state, action)
        self.assertFalse(result.executed)
        self.assertTrue(result.dry_run)
        self.assertIn("[DRY-RUN]", result.message)
        client.navigate_menu.assert_not_called()

    def test_live_mode_clicks_build_button_and_verifies_state(self) -> None:
        state, action = _make_state_and_action()
        updated_planet = PlanetState(
            planet_id=12476,
            name="Colony",
            coords="02:380:12",
            planet_type="normal",
            temperature=31,
            fields_used=1,
            fields_max=230,
            resources=Resources(1120, 0, 765, 0),
            storage=Resources(100000, 100000, 100000, 100000),
            energy=EnergyBalance(10, 0),
            buildings={"b5": 1},
        )
        updated_state = GameState(
            universe_id=31,
            user_id=103603,
            nickname="ventkx86",
            timestamp=1.0,
            planets=(updated_planet,),
            active_planet_id=12476,
        )

        buildings_html = """
        <table><tr>
          <td><a href="indexInternal.es?action=internalDetail&type=building&id=5">Fusion plant</a></td>
          <td>Required: 80 Pig iron 35 Kryptonite</td>
          <td><a class="bau" href="indexInternal.es?action=internalBuildItem&buildingID=5#5">Build level 1</a></td>
        </tr></table>
        """

        btn = MagicMock()
        driver = MagicMock()
        driver.find_element.return_value = btn
        client = MagicMock(spec=GameBrowserClient)
        client.driver = driver
        client.navigate_menu.return_value = buildings_html
        client._wait_frame_ready.return_value = "<div>in construction ... Cancel</div>"
        client.read_planets_overview.return_value = updated_state

        executor = ActionExecutor(dry_run=False, action_delay_range=(0.0, 0.0))
        result = executor.execute_action(client, state, action)

        self.assertTrue(result.executed)
        self.assertFalse(result.dry_run)
        btn.click.assert_called_once()
        self.assertEqual(executor.remaining_hourly_budget(), 29)

    def test_hourly_budget_cap_blocks_excess_actions(self) -> None:
        state, action = _make_state_and_action()
        client = MagicMock(spec=GameBrowserClient)
        executor = ActionExecutor(dry_run=False, max_actions_per_hour=0)

        result = executor.execute_action(client, state, action)
        self.assertFalse(result.executed)
        self.assertIn("hourly action rate cap", result.message)
        client.ensure_in_game.assert_not_called()

    def test_retries_once_on_stale_element_auto_reload(self) -> None:
        state, action = _make_state_and_action()
        buildings_html = """
        <table><tr>
          <td><a href="indexInternal.es?action=internalDetail&type=building&id=5">Fusion plant</a></td>
          <td>Required: 80 Pig iron 35 Kryptonite</td>
          <td><a class="bau" href="indexInternal.es?action=internalBuildItem&buildingID=5#5">Build level 1</a></td>
        </tr></table>
        """
        stale_btn = MagicMock()
        stale_btn.click.side_effect = StaleElementReferenceException("reloaded")
        fresh_btn = MagicMock()

        driver = MagicMock()
        driver.find_element.side_effect = [stale_btn, fresh_btn]
        client = MagicMock(spec=GameBrowserClient)
        client.driver = driver
        client.navigate_menu.return_value = buildings_html
        client._wait_frame_ready.side_effect = [buildings_html, "<div>Construction queue</div>"]
        client.read_planets_overview.return_value = state

        executor = ActionExecutor(dry_run=False, action_delay_range=(0.0, 0.0))
        result = executor.execute_action(client, state, action)

        self.assertTrue(result.executed)
        fresh_btn.click.assert_called_once()

    def test_live_research_and_build_ship_and_guarded_fleetsave(self) -> None:
        state, _ = _make_state_and_action()
        res_html = """
        <table><tr>
          <td><a href="indexInternal.es?action=internalDetail&type=research&id=2">Energy technology</a></td>
          <td>Required: 400 Metal 800 Kryptonite 400 Spice</td>
          <td><a class="bau" href="indexInternal.es?action=internalBuildItem&researchID=2">Research level 1</a></td>
        </tr></table>
        """
        r_btn = MagicMock()
        driver = MagicMock()
        driver.find_element.return_value = r_btn
        client = MagicMock(spec=GameBrowserClient)
        client.driver = driver
        client.navigate_menu.return_value = res_html
        client._wait_frame_ready.return_value = "<div>Cancel</div>"
        client.read_planets_overview.return_value = state

        ex = ActionExecutor(dry_run=False, action_delay_range=(0.0, 0.0), allow_fleetsave=False)
        res_action = PlannedAction(
            priority="P2",
            kind="RESEARCH",
            planet_id=12476,
            planet_coords="02:380:12",
            target_name="Energy technology",
            reason="Tutorial 4/11",
            target_id=2,
            target_level=1,
            executable_now=True,
        )
        out = ex.execute_action(client, state, res_action)
        self.assertTrue(out.executed)
        r_btn.click.assert_called_once()

        # BUILD_SHIP fills input and clicks form submit
        wf_html = """
        <form action="indexInternal.es?action=internalBuildItem&buildShip=1" method="POST">
          <table><tr><td>
            <a href="indexInternal.es?action=internalDetail&type=ship&id=1">Small transport</a>
            Required: 3.000 Metal 3.000 Kryptonite
            <input type="text" name="ship[1]" value="0"/>
          </td></tr></table>
        </form>
        """
        qty_inp = MagicMock()
        submit_btn = MagicMock()
        form_mock = MagicMock()
        form_mock.find_element.return_value = submit_btn
        driver2 = MagicMock()
        driver2.find_element.side_effect = [qty_inp, form_mock]
        client2 = MagicMock(spec=GameBrowserClient)
        client2.driver = driver2
        client2.navigate_menu.return_value = wf_html
        client2._wait_frame_ready.return_value = wf_html
        client2.read_planets_overview.return_value = state

        ship_action = PlannedAction(
            priority="P2",
            kind="BUILD_SHIP",
            planet_id=12476,
            planet_coords="02:380:12",
            target_name="Small transport",
            reason="Baseline transport",
            target_id=1,
            target_level=2,
            executable_now=True,
        )
        ship_out = ex.execute_action(client2, state, ship_action)
        self.assertTrue(ship_out.executed)
        qty_inp.send_keys.assert_called_once_with("2")
        submit_btn.click.assert_called_once()

        # ESCAPE_FLIGHT stays guarded when allow_fleetsave=False
        fs_action = PlannedAction(
            priority="P0",
            kind="ESCAPE_FLIGHT",
            planet_id=12476,
            planet_coords="02:380:12",
            target_name="Save -> 02:380:13",
            reason="Packed 4.5k Spice",
            executable_now=True,
        )
        fs_out = ex.execute_action(client, state, fs_action)
        self.assertFalse(fs_out.executed)
        self.assertIn("allow_fleetsave", fs_out.message)


if __name__ == "__main__":
    unittest.main()
