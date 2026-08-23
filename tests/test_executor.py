"""Unit tests for safe Chrome ActionExecutor."""

from __future__ import annotations

import json
import re
import unittest
from unittest.mock import MagicMock, patch

from bs4 import BeautifulSoup
from selenium.common.exceptions import (
    ElementClickInterceptedException,
    NoSuchElementException,
    StaleElementReferenceException,
)
from selenium.webdriver.common.by import By

from spaceinvasion.client import GameBrowserClient, PageViewLimiter
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

    def test_throttle_building_dry_and_live_internal_economy(self) -> None:
        state, _ = _make_state_and_action()
        throttle_action = PlannedAction(
            priority="P1",
            kind="THROTTLE_BUILDING",
            planet_id=12476,
            planet_coords="02:380:12",
            target_id=2,
            target_name="Blast furnace 0%",
            target_level=0,
            reason="Pause Blast furnace to boost Pig iron",
            executable_now=True,
        )

        # Dry-run formats -> 0% cleanly (including target_level == 0)
        dry_ex = ActionExecutor(dry_run=True)
        dry_out = dry_ex.execute_action(MagicMock(spec=GameBrowserClient), state, throttle_action)
        self.assertFalse(dry_out.executed)
        self.assertIn("-> 0%", dry_out.message)

        econ_before = """
        <form id="ress"><select name="resource2">
          <option value="100" selected="selected">100%</option>
          <option value="0">0%</option>
        </select><input type="submit" value="Calculate"></form>
        """
        econ_after = """
        <form id="ress"><select name="resource2">
          <option value="100">100%</option>
          <option value="0" selected="selected">0%</option>
        </select></form>
        """
        sel_mock = MagicMock()
        submit_mock = MagicMock()
        driver = MagicMock()
        driver.find_element.side_effect = [sel_mock, submit_mock]
        client = MagicMock(spec=GameBrowserClient)
        client.driver = driver
        client.navigate_menu.return_value = econ_before
        client._wait_frame_ready.return_value = econ_after
        client.read_planets_overview.return_value = state

        live_ex = ActionExecutor(dry_run=False, action_delay_range=(0.0, 0.0))
        with patch("spaceinvasion.executor.Select") as mock_select_cls:
            res = live_ex.execute_action(client, state, throttle_action)

        self.assertTrue(res.executed)
        mock_select_cls.return_value.select_by_value.assert_called_once_with("0")
        submit_mock.click.assert_called_once()
        self.assertEqual(live_ex.last_economy_rates[12476]["resource2"], 0)

    def test_scan_galaxy_and_guarded_attack_execution(self) -> None:
        base_state, _ = _make_state_and_action()
        planet_with_ships = PlanetState(
            planet_id=12476,
            name="Colony",
            coords="02:380:12",
            planet_type="normal",
            temperature=31,
            fields_used=20,
            fields_max=230,
            resources=Resources(5000, 5000, 5000, 2000),
            storage=Resources(100000, 100000, 100000, 100000),
            energy=EnergyBalance(300, 120),
            buildings={"b5": 5, "b9": 2},
            ships={"s1": 2, "s4": 4},
        )
        state = GameState(31, 103603, "ventkx86", 0.0, (planet_with_ships,), active_planet_id=12476)

        scan_act = PlannedAction(
            priority="P2",
            kind="SCAN_GALAXY",
            planet_id=12476,
            planet_coords="02:380:12",
            target_name="Galaxy [02:380]",
            reason="Scan system for inactive targets",
            executable_now=True,
        )
        gal_html = """
        <html><body><table>
          <tr><td>4</td><td><span class="galaxy_yellow">IdlePlanet</span></td><td>Sleepy</td></tr>
          <tr><td>9</td><td><span class="galaxy_purple">Spacia</span></td><td>Spacia</td></tr>
        </table></body></html>
        """
        scan_client = MagicMock(spec=GameBrowserClient)
        scan_client.open_galaxy.return_value = gal_html
        ex = ActionExecutor(dry_run=False, action_delay_range=(0.0, 0.0), allow_raids=False)
        scan_res = ex.execute_action(scan_client, state, scan_act)
        self.assertTrue(scan_res.executed)
        scan_client.open_galaxy.assert_called_once_with(galaxy=2, system=380)
        self.assertTrue(ex.last_galaxy_slots)
        self.assertIn("Scanned Galaxy [02:380]", scan_res.message)

        atk_act = PlannedAction(
            priority="P2",
            kind="ATTACK",
            planet_id=12476,
            planet_coords="02:380:12",
            target_name="02:380:04 (Sleepy)",
            reason="Inactive raid",
            executable_now=True,
        )
        # Blocked when allow_raids=False
        blocked_res = ex.execute_action(scan_client, state, atk_act)
        self.assertFalse(blocked_res.executed)
        self.assertIn("allow_raids", blocked_res.message)

        # Allowed when allow_raids=True -> fills fleet, confirms Attack mission radio, launches
        s4_inp = MagicMock()
        s1_inp = MagicMock()
        gal_inp = MagicMock()
        sun_inp = MagicMock()
        pla_inp = MagicMock()
        cont_btn = MagicMock()
        attack_radio = MagicMock()
        attack_radio.get_attribute.side_effect = lambda attr: {"value": "1", "id": "mission_1", "name": "mission"}.get(attr, "")
        attack_radio.is_selected.return_value = True
        launch_btn = MagicMock()

        driver = MagicMock()
        driver.find_elements.side_effect = [
            [s4_inp],  # ship_4
            [s1_inp],  # ship_1
            [attack_radio],  # radios on internalSendFleet
        ]
        driver.find_element.side_effect = [
            gal_inp,
            sun_inp,
            pla_inp,
            cont_btn,
            launch_btn,
        ]
        atk_client = MagicMock(spec=GameBrowserClient)
        atk_client.driver = driver
        atk_client.navigate_menu.return_value = '<form name="fleet"></form>'
        atk_client._wait_frame_ready.side_effect = ["<form><input type='radio' value='1'>Attack</form>", "<div>Fleet sent</div>"]
        atk_client.read_planets_overview.return_value = state

        raid_ex = ActionExecutor(dry_run=False, action_delay_range=(0.0, 0.0), allow_raids=True)
        atk_res = raid_ex.execute_action(atk_client, state, atk_act)
        self.assertTrue(atk_res.executed)
        s4_inp.send_keys.assert_called_once_with("4")
        s1_inp.send_keys.assert_called_once_with("2")
        gal_inp.send_keys.assert_called_once_with("2")
        sun_inp.send_keys.assert_called_once_with("380")
        pla_inp.send_keys.assert_called_once_with("4")
        cont_btn.click.assert_called_once()
        launch_btn.click.assert_called_once()
        self.assertIn("02:380:04", atk_res.message)

    def test_execute_attack_switches_planet_via_newplanet_without_touching_fleet_favourites(self) -> None:
        p1 = PlanetState(
            planet_id=11111,
            name="Home",
            coords="02:380:12",
            planet_type="normal",
            temperature=31,
            fields_used=20,
            fields_max=230,
            resources=Resources(5000, 5000, 5000, 2000),
            storage=Resources(100000, 100000, 100000, 100000),
            energy=EnergyBalance(300, 120),
            ships={},
        )
        p2 = PlanetState(
            planet_id=22222,
            name="RaidColony",
            coords="02:381:08",
            planet_type="normal",
            temperature=20,
            fields_used=15,
            fields_max=230,
            resources=Resources(5000, 5000, 5000, 2000),
            storage=Resources(100000, 100000, 100000, 100000),
            energy=EnergyBalance(300, 120),
            ships={"s4": 3},
        )
        state = GameState(31, 103603, "ventkx86", 0.0, (p1, p2), active_planet_id=11111)
        atk_act = PlannedAction(
            priority="P2",
            kind="ATTACK",
            planet_id=22222,
            planet_coords="02:381:08",
            target_name="02:381:04 (Sleepy)",
            reason="Raid from colony",
            executable_now=True,
        )

        bld_html = """
        <select name="planet">
          <option value="indexInternal.es?action=internalBuildings&newPlanet=11111" selected="selected">Home</option>
          <option value="indexInternal.es?action=internalBuildings&newPlanet=22222">RaidColony</option>
        </select>
        """
        fleet_html = """
        <form action="indexInternal.es?action=internalSendFleet">
          <select name="planet"><option value="02:380:04">Fav Target</option></select>
          <input name="ship_4" value="0"/>
          <input name="gal"/><input name="sun"/><input name="pla"/>
          <input type="submit" value="Continue"/>
        </form>
        """
        send_html = '<form action="indexInternal.es?action=internalSendFleet"><input type="radio" name="mission" value="1">Attack<input type="submit" value="Launch"/></form>'

        planet_switch_select = MagicMock()
        fav_select = MagicMock()
        s4_inp = MagicMock()
        gal_inp = MagicMock()
        sun_inp = MagicMock()
        pla_inp = MagicMock()
        cont_btn = MagicMock()
        attack_radio = MagicMock()
        attack_radio.get_attribute.side_effect = lambda a: {"value": "1", "id": "m1", "name": "mission"}.get(a, "")
        attack_radio.is_selected.return_value = True
        launch_btn = MagicMock()

        def _find_el(by, val):
            if by == By.CSS_SELECTOR and 'select[name="planet"]' in val:
                return planet_switch_select
            if by == By.NAME and val == "gal":
                return gal_inp
            if by == By.NAME and val == "sun":
                return sun_inp
            if by == By.NAME and val == "pla":
                return pla_inp
            if "internalSendFleet" in val and "submit" in val:
                return cont_btn if cont_btn.click.call_count == 0 else launch_btn
            return MagicMock()

        opt_11 = MagicMock()
        opt_11.get_attribute.return_value = "indexInternal.es?action=internalBuildings&newPlanet=11111"
        opt_11.is_selected.return_value = True
        opt_22 = MagicMock()
        opt_22.get_attribute.return_value = "indexInternal.es?action=internalBuildings&newPlanet=22222"
        opt_22.is_selected.return_value = False
        planet_switch_select.find_elements.return_value = [opt_11, opt_22]

        driver = MagicMock()
        driver.find_element.side_effect = _find_el
        driver.find_elements.side_effect = [
            [s4_inp],
            [attack_radio],
        ]

        client = MagicMock(spec=GameBrowserClient)
        client.driver = driver
        client.navigate_menu.side_effect = [bld_html, fleet_html]
        client._wait_frame_ready.side_effect = [bld_html, send_html, "<div>Fleet sent</div>"]
        client.read_planets_overview.return_value = state

        ex = ActionExecutor(dry_run=False, action_delay_range=(0.0, 0.0), allow_raids=True)
        with patch("spaceinvasion.executor.Select") as mock_select_cls:
            res = ex.execute_action(client, state, atk_act)

        self.assertTrue(res.executed)
        self.assertEqual(
            [c.args[0] for c in client.navigate_menu.call_args_list],
            ["internalBuildings", "internalFleet"],
        )
        mock_select_cls.return_value.select_by_value.assert_called_once_with(
            "indexInternal.es?action=internalBuildings&newPlanet=22222"
        )
        fav_select.assert_not_called()
        s4_inp.send_keys.assert_called_once_with("3")

    def test_execute_attack_supports_internal_send_fleet_form_ship_brackets_and_scoped_submit(self) -> None:
        base_state, _ = _make_state_and_action()
        planet = PlanetState(
            planet_id=12476,
            name="Colony",
            coords="02:380:12",
            planet_type="normal",
            temperature=31,
            fields_used=20,
            fields_max=230,
            resources=Resources(5000, 5000, 5000, 2000),
            storage=Resources(100000, 100000, 100000, 100000),
            energy=EnergyBalance(300, 120),
            ships={"s4": 5},
        )
        state = GameState(31, 103603, "ventkx86", 0.0, (planet,), active_planet_id=12476)
        atk_act = PlannedAction(
            priority="P2",
            kind="ATTACK",
            planet_id=12476,
            planet_coords="02:380:12",
            target_name="02:380:04 (Sleeper)",
            reason="Inactive raid",
            executable_now=True,
        )

        step1_html = """
        <form action="indexInternal.es?action=internalSendFleet" method="post">
          <input type="text" name="ship[4]" value="0"/>
          <input type="text" name="gal"/><input type="text" name="sun"/><input type="text" name="pla"/>
          <input type="submit" value="Continue"/>
        </form>
        """
        step2_html = """
        <form id="planet-switch">
          <input type="radio" name="speed" value="1"/> 10%
          <input type="submit" value="Switch"/>
        </form>
        <form action="indexInternal.es?action=internalSendFleet&confirm=1" method="post">
          <label><input type="radio" name="mission" id="m_atk" value="1"/> Attack</label>
          <input type="submit" value="Send Fleet"/>
        </form>
        """

        s4_inp = MagicMock()
        gal_inp = MagicMock()
        sun_inp = MagicMock()
        pla_inp = MagicMock()
        cont_btn = MagicMock()
        attack_radio = MagicMock()
        attack_radio.get_attribute.side_effect = lambda a: {"value": "1", "id": "m_atk", "name": "mission"}.get(a, "")
        attack_radio.is_selected.return_value = False
        launch_btn = MagicMock()

        seen_selectors: list[str] = []

        def _find_el(by, val):
            seen_selectors.append(f"{by}:{val}")
            if by == By.NAME and val == "gal":
                return gal_inp
            if by == By.NAME and val == "sun":
                return sun_inp
            if by == By.NAME and val == "pla":
                return pla_inp
            if by == By.CSS_SELECTOR and 'form[action*="internalSendFleet"]' in val and "submit" in val:
                return cont_btn if len([s for s in seen_selectors if "submit" in s]) == 1 else launch_btn
            raise AssertionError(f"Unexpected unscoped find_element: {by}={val}")

        def _find_els(by, val):
            seen_selectors.append(f"{by}:{val}")
            if by == By.NAME and val == "ship[4]":
                return [s4_inp]
            if by == By.CSS_SELECTOR and val == 'form[action*="internalSendFleet"] input[type="radio"]':
                return [attack_radio]
            raise AssertionError(f"Unexpected unscoped find_elements: {by}={val}")

        driver = MagicMock()
        driver.find_element.side_effect = _find_el
        driver.find_elements.side_effect = _find_els
        client = MagicMock(spec=GameBrowserClient)
        client.driver = driver
        client.navigate_menu.return_value = step1_html
        client._mark_frame_navigation.side_effect = ["tok1", "tok2"]
        client._wait_frame_ready.side_effect = [step2_html, "<div>Fleet sent</div>"]
        client.read_planets_overview.return_value = state

        ex = ActionExecutor(dry_run=False, action_delay_range=(0.0, 0.0), allow_raids=True)
        res = ex.execute_action(client, state, atk_act)

        self.assertTrue(res.executed)
        s4_inp.send_keys.assert_called_once_with("5")
        attack_radio.click.assert_called_once()
        cont_btn.click.assert_called_once()
        launch_btn.click.assert_called_once()
        self.assertEqual(
            client._wait_frame_ready.call_args_list[0].kwargs["expected_action"],
            "internalSendFleet",
        )
        self.assertEqual(
            client._wait_frame_ready.call_args_list[0].kwargs["nav_token"],
            "tok1",
        )

    def test_execute_attack_recovers_from_click_intercepted_and_stale_elements(self) -> None:
        planet = PlanetState(
            planet_id=12476,
            name="Colony",
            coords="02:380:12",
            planet_type="normal",
            temperature=31,
            fields_used=20,
            fields_max=230,
            resources=Resources(5000, 5000, 5000, 2000),
            storage=Resources(100000, 100000, 100000, 100000),
            energy=EnergyBalance(300, 120),
            ships={"s4": 2},
        )
        state = GameState(31, 103603, "ventkx86", 0.0, (planet,), active_planet_id=12476)
        atk_act = PlannedAction(
            priority="P2",
            kind="ATTACK",
            planet_id=12476,
            planet_coords="02:380:12",
            target_name="02:380:04 (Sleepy)",
            reason="Inactive raid",
            executable_now=True,
        )

        s4_inp = MagicMock()
        gal_inp = MagicMock()
        sun_inp = MagicMock()
        pla_inp = MagicMock()
        cont_btn = MagicMock()
        cont_btn.click.side_effect = ElementClickInterceptedException("rahmen overlay")

        attack_radio = MagicMock()
        attack_radio.get_attribute.side_effect = lambda a: {"value": "1", "id": "m1", "name": "mission"}.get(a, "")
        attack_radio.is_selected.return_value = True

        stale_launch = MagicMock()
        stale_launch.click.side_effect = StaleElementReferenceException("reloaded")
        fresh_launch = MagicMock()

        driver = MagicMock()
        driver.find_elements.side_effect = [[s4_inp], [attack_radio], [attack_radio]]
        driver.find_element.side_effect = [
            gal_inp,
            sun_inp,
            pla_inp,
            cont_btn,
            stale_launch,
            fresh_launch,
        ]
        client = MagicMock(spec=GameBrowserClient)
        client.driver = driver
        client.navigate_menu.return_value = '<form name="fleet"></form>'
        client._wait_frame_ready.side_effect = [
            "<form><input type='radio' value='1'>Attack</form>",
            "<div>Fleet dispatched</div>",
        ]
        client.read_planets_overview.return_value = state

        ex = ActionExecutor(dry_run=False, action_delay_range=(0.0, 0.0), allow_raids=True)
        res = ex.execute_action(client, state, atk_act)

        self.assertTrue(res.executed)
        # JS click fallback ran for cont_btn
        self.assertTrue(
            any("arguments[0].click();" in c.args[0] and c.args[1] is cont_btn for c in driver.execute_script.call_args_list)
        )
        fresh_launch.click.assert_called_once()

    def test_scan_galaxy_does_not_consume_hourly_mutation_budget(self) -> None:
        base_state, _ = _make_state_and_action()
        scan_act = PlannedAction(
            priority="P2",
            kind="SCAN_GALAXY",
            planet_id=12476,
            planet_coords="02:380:12",
            target_name="Galaxy [02:380]",
            reason="Scan system",
            executable_now=True,
        )
        scan_client = MagicMock(spec=GameBrowserClient)
        scan_client.open_galaxy.return_value = "<table></table>"
        scan_client.last_galaxy_coords = (2, 380)

        ex = ActionExecutor(dry_run=False, max_actions_per_hour=2, action_delay_range=(0.0, 0.0))
        for _ in range(3):
            res = ex.execute_action(scan_client, base_state, scan_act)
            self.assertTrue(res.executed)
        self.assertEqual(ex.remaining_hourly_budget(), 2)

    def test_execute_spy_dispatch_and_report_parse(self) -> None:
        from spaceinvasion.fleet import parse_spy_report_html

        planet = PlanetState(
            planet_id=12476,
            name="Colony",
            coords="02:380:12",
            planet_type="normal",
            temperature=31,
            fields_used=20,
            fields_max=230,
            resources=Resources(5000, 5000, 5000, 2000),
            storage=Resources(100000, 100000, 100000, 100000),
            energy=EnergyBalance(300, 120),
            ships={"s10": 5},
        )
        state = GameState(31, 103603, "ventkx86", 0.0, (planet,), active_planet_id=12476)
        spy_act = PlannedAction(
            priority="P2",
            kind="SPY",
            planet_id=12476,
            planet_coords="02:380:12",
            target_id=10,
            target_level=1,
            target_name="02:380:04 (Sleepy)",
            reason="Send Spy probe",
            executable_now=True,
        )
        step1_html = '<form action="indexInternal.es?action=internalSendFleet"><input name="ship[10]"><input name="gal"><input name="sun"><input name="pla"><input type="submit"></form>'
        step2_html = """
        <form action="indexInternal.es?action=internalSendFleet">
          <label><input type="radio" name="mission" value="1"> Attack</label>
          <label><input type="radio" name="mission" value="6"> Spying</label>
          <input type="submit" value="Send"/>
        </form>
        """
        s10_inp = MagicMock()
        gal_inp = MagicMock()
        sun_inp = MagicMock()
        pla_inp = MagicMock()
        cont_btn = MagicMock()
        atk_radio = MagicMock()
        atk_radio.get_attribute.side_effect = lambda a: {"value": "1", "name": "mission"}.get(a, "")
        spy_radio = MagicMock()
        spy_radio.get_attribute.side_effect = lambda a: {"value": "6", "name": "mission"}.get(a, "")
        spy_radio.is_selected.return_value = False
        launch_btn = MagicMock()

        driver = MagicMock()
        driver.find_elements.side_effect = [[s10_inp], [atk_radio, spy_radio]]
        driver.find_element.side_effect = [gal_inp, sun_inp, pla_inp, cont_btn, launch_btn]
        client = MagicMock(spec=GameBrowserClient)
        client.driver = driver
        client.navigate_menu.return_value = step1_html
        client._wait_frame_ready.side_effect = [step2_html, "<div>Spy probe dispatched</div>"]
        client.read_planets_overview.return_value = state

        ex = ActionExecutor(dry_run=False, action_delay_range=(0.0, 0.0), max_actions_per_hour=5)
        res = ex.execute_action(client, state, spy_act)
        self.assertTrue(res.executed)
        s10_inp.send_keys.assert_called_once_with("1")
        spy_radio.click.assert_called_once()
        atk_radio.click.assert_not_called()
        self.assertEqual(ex.remaining_hourly_budget(), 4)

        rep = parse_spy_report_html(
            "<div>02:380:04 Player: Sleepy Pig iron: 12.000 Metal: 8.000 Kryptonite: 4.000 Spice: 1.500</div>"
        )
        self.assertEqual(rep.target_coords, "02:380:04")
        self.assertEqual(rep.owner, "Sleepy")
        self.assertEqual(rep.resources.pig_iron, 12000)

    def test_match_mission_radio_handles_table_rows_flat_containers_and_speed_radios(self) -> None:
        def _mk_radio(val: str, name: str = "mission", rid: str = ""):
            el = MagicMock()
            el.get_attribute.side_effect = lambda a: {"value": val, "name": name, "id": rid}.get(a, "")
            return el

        # 1. Table row layout with speed radio procent=1 (10%) before mission radios in separate <td>
        tbl_html = """
        <form action="indexInternal.es?action=internalSendFleet">
          <table>
            <tr><td><input type="radio" name="procent" value="1" id="p1"/></td><td>10%</td></tr>
            <tr><td><input type="radio" name="mission" value="1" id="m1"/></td><td>Attack</td></tr>
            <tr><td><input type="radio" name="mission" value="6" id="m6"/></td><td>Spying</td></tr>
          </table>
        </form>
        """
        soup1 = BeautifulSoup(tbl_html, "html.parser")
        p1, m1, m6 = _mk_radio("1", "procent", "p1"), _mk_radio("1", "mission", "m1"), _mk_radio("6", "mission", "m6")
        self.assertIs(
            ActionExecutor._match_mission_radio(soup1, 'form[action*="internalSendFleet"]', [p1, m1, m6], "ATTACK"),
            m1,
        )
        self.assertIs(
            ActionExecutor._match_mission_radio(soup1, 'form[action*="internalSendFleet"]', [p1, m1, m6], "SPY"),
            m6,
        )

        # 2. Flat <form> without <label> tags
        flat_html = """
        <form action="indexInternal.es?action=internalSendFleet">
          <input type="radio" name="mission" value="3" id="f3"/> Transport
          <input type="radio" name="mission" value="1" id="f1"/> Attack
          <input type="radio" name="mission" value="6" id="f6"/> Spying
        </form>
        """
        soup2 = BeautifulSoup(flat_html, "html.parser")
        f3, f1, f6 = _mk_radio("3", "mission", "f3"), _mk_radio("1", "mission", "f1"), _mk_radio("6", "mission", "f6")
        self.assertIs(
            ActionExecutor._match_mission_radio(soup2, 'form[action*="internalSendFleet"]', [f3, f1, f6], "ATTACK"),
            f1,
        )
        self.assertIs(
            ActionExecutor._match_mission_radio(soup2, 'form[action*="internalSendFleet"]', [f3, f1, f6], "SPY"),
            f6,
        )

        # 3. Multi-radio <tr> row (Attack + Spying side by side)
        multi_tr_html = """
        <form action="indexInternal.es?action=internalSendFleet">
          <table>
            <tr>
              <td><input type="radio" name="mission" value="1" id="r1"/></td><td>Attack</td>
              <td><input type="radio" name="mission" value="6" id="r6"/></td><td>Spying</td>
            </tr>
          </table>
        </form>
        """
        soup3 = BeautifulSoup(multi_tr_html, "html.parser")
        r1, r6 = _mk_radio("1", "mission", "r1"), _mk_radio("6", "mission", "r6")
        self.assertIs(
            ActionExecutor._match_mission_radio(soup3, 'form[action*="internalSendFleet"]', [r1, r6], "ATTACK"),
            r1,
        )
        self.assertIs(
            ActionExecutor._match_mission_radio(soup3, 'form[action*="internalSendFleet"]', [r1, r6], "SPY"),
            r6,
        )

        # 4. Unlabelled value="1" fallback alongside geschwindigkeit=1 (10%) and route=1
        unlab_html = """
        <form action="indexInternal.es?action=internalSendFleet">
          <input type="radio" name="geschwindigkeit" value="1"/> 10%
          <input type="radio" name="route" value="1"/> Direct
          <input type="radio" name="mission" value="1"/>
        </form>
        """
        soup4 = BeautifulSoup(unlab_html, "html.parser")
        g1, rt1, ms1 = _mk_radio("1", "geschwindigkeit"), _mk_radio("1", "route"), _mk_radio("1", "mission")
        self.assertIs(
            ActionExecutor._match_mission_radio(soup4, 'form[action*="internalSendFleet"]', [g1, rt1, ms1], "ATTACK"),
            ms1,
        )

        # 5. Immediate preceding speed radio (no intervening route radio) does NOT poison unlabelled mission=1
        speed_only_html = """
        <form action="indexInternal.es?action=internalSendFleet">
          <input type="radio" name="geschwindigkeit" value="1" id="g1"/> 10%
          <input type="radio" name="mission" value="1" id="gm1"/>
        </form>
        """
        soup5 = BeautifulSoup(speed_only_html, "html.parser")
        sg1, sgm1 = _mk_radio("1", "geschwindigkeit", "g1"), _mk_radio("1", "mission", "gm1")
        self.assertIs(
            ActionExecutor._match_mission_radio(soup5, 'form[action*="internalSendFleet"]', [sg1, sgm1], "ATTACK"),
            sgm1,
        )
        self.assertIsNone(
            ActionExecutor._match_mission_radio(soup5, 'form[action*="internalSendFleet"]', [sg1, sgm1], "SPY")
        )

        # 5b. Wrapped speed radio and no-name radios
        wrapped_speed_html = """
        <form action="indexInternal.es?action=internalSendFleet">
          <span><input type="radio" name="geschwindigkeit" value="1" id="wg1"/></span> 10%
          <input type="radio" name="mission" value="1" id="wgm1"/>
        </form>
        """
        soup5b = BeautifulSoup(wrapped_speed_html, "html.parser")
        wg1, wgm1 = _mk_radio("1", "geschwindigkeit", "wg1"), _mk_radio("1", "mission", "wgm1")
        self.assertIs(
            ActionExecutor._match_mission_radio(soup5b, 'form[action*="internalSendFleet"]', [wg1, wgm1], "ATTACK"),
            wgm1,
        )
        self.assertIsNone(
            ActionExecutor._match_mission_radio(soup5b, 'form[action*="internalSendFleet"]', [wg1, wgm1], "SPY")
        )

        noname_html = """
        <form action="indexInternal.es?action=internalSendFleet">
          <input type="radio" value="1" id="ng1"/> 10%
          <input type="radio" value="1" id="ngm1"/>
        </form>
        """
        soup5c = BeautifulSoup(noname_html, "html.parser")
        ng1, ngm1 = _mk_radio("1", "", "ng1"), _mk_radio("1", "", "ngm1")
        self.assertIs(
            ActionExecutor._match_mission_radio(soup5c, 'form[action*="internalSendFleet"]', [ng1, ngm1], "ATTACK"),
            ngm1,
        )
        self.assertIsNone(
            ActionExecutor._match_mission_radio(soup5c, 'form[action*="internalSendFleet"]', [ng1, ngm1], "SPY")
        )

        # 6. Genuine pre-radio labels after <br/> and after <input type="hidden"/>
        pre_br_html = """
        <form action="indexInternal.es?action=internalSendFleet">
          <input type="radio" name="geschwindigkeit" value="1" id="g1"/> 10%
          <br/> Attack <input type="radio" name="mission" value="1" id="gm1"/>
          <br/> Spying <input type="radio" name="mission" value="6" id="gm6"/>
        </form>
        """
        soup6 = BeautifulSoup(pre_br_html, "html.parser")
        pg1, pgm1, pgm6 = _mk_radio("1", "geschwindigkeit", "g1"), _mk_radio("1", "mission", "gm1"), _mk_radio("6", "mission", "gm6")
        self.assertIs(
            ActionExecutor._match_mission_radio(soup6, 'form[action*="internalSendFleet"]', [pg1, pgm1, pgm6], "ATTACK"),
            pgm1,
        )
        self.assertIs(
            ActionExecutor._match_mission_radio(soup6, 'form[action*="internalSendFleet"]', [pg1, pgm1, pgm6], "SPY"),
            pgm6,
        )

        hidden_pre_html = """
        <form action="indexInternal.es?action=internalSendFleet">
          <input type="hidden" name="sid" value="x"/> Attack <input type="radio" name="mission" value="1" id="hm1"/>
          <br/> Spying <input type="radio" name="mission" value="6" id="hm6"/>
        </form>
        """
        soup6b = BeautifulSoup(hidden_pre_html, "html.parser")
        hm1, hm6 = _mk_radio("1", "mission", "hm1"), _mk_radio("6", "mission", "hm6")
        self.assertIs(
            ActionExecutor._match_mission_radio(soup6b, 'form[action*="internalSendFleet"]', [hm1, hm6], "ATTACK"),
            hm1,
        )
        self.assertIs(
            ActionExecutor._match_mission_radio(soup6b, 'form[action*="internalSendFleet"]', [hm1, hm6], "SPY"),
            hm6,
        )

        # 7. Multi-radio <tr> label-before-input
        before_tr_html = """
        <form action="indexInternal.es?action=internalSendFleet">
          <table>
            <tr>
              <td>Attack</td><td><input type="radio" name="mission" value="1" id="mb1"/></td>
              <td>Spying</td><td><input type="radio" name="mission" value="6" id="mb6"/></td>
            </tr>
          </table>
        </form>
        """
        soup7 = BeautifulSoup(before_tr_html, "html.parser")
        mb1, mb6 = _mk_radio("1", "mission", "mb1"), _mk_radio("6", "mission", "mb6")
        self.assertIs(
            ActionExecutor._match_mission_radio(soup7, 'form[action*="internalSendFleet"]', [mb1, mb6], "ATTACK"),
            mb1,
        )
        self.assertIs(
            ActionExecutor._match_mission_radio(soup7, 'form[action*="internalSendFleet"]', [mb1, mb6], "SPY"),
            mb6,
        )

        # 8. Multi-radio <tr> with leading <th>Mission:</th> + label-after-input + wrapped <span>
        hdr_tr_html = """
        <form action="indexInternal.es?action=internalSendFleet">
          <table>
            <tr>
              <th>Mission:</th>
              <td><span><input type="radio" name="mission" value="1" id="tr1"/></span></td><td>Attack</td>
              <td><span><input type="radio" name="mission" value="6" id="tr6"/></span></td><td>Spying</td>
            </tr>
          </table>
        </form>
        """
        soup8 = BeautifulSoup(hdr_tr_html, "html.parser")
        tr1, tr6 = _mk_radio("1", "mission", "tr1"), _mk_radio("6", "mission", "tr6")
        self.assertIs(
            ActionExecutor._match_mission_radio(soup8, 'form[action*="internalSendFleet"]', [tr1, tr6], "ATTACK"),
            tr1,
        )
        self.assertIs(
            ActionExecutor._match_mission_radio(soup8, 'form[action*="internalSendFleet"]', [tr1, tr6], "SPY"),
            tr6,
        )

    def test_match_mission_radio_handles_trailing_cells_nested_wrappers_and_sibling_controls(self) -> None:
        def _mk_radio(val: str, name: str = "mission", rid: str = ""):
            el = MagicMock()
            el.get_attribute.side_effect = lambda a: {"value": val, "name": name, "id": rid}.get(a, "")
            return el

        # 1. Label-before multi-radio <tr> with trailing control, link, plain text, or foreign label
        for tail in (
            '<td><button type="submit">Send</button></td>',
            '<td><select><option>100%</option></select></td>',
            '<td><a href="#">Help</a></td>',
            "<td>Help</td>",
            "<td>Note: Spying requires probes</td>",
            '<td><label for="sp100">100%</label></td>',
        ):
            bt1, bt6 = _mk_radio("1", "mission", "bt1"), _mk_radio("6", "mission", "bt6")
            html = (
                '<form action="indexInternal.es?action=internalSendFleet"><table><tr>'
                '<td>Attack</td><td><input type="radio" name="mission" value="1" id="bt1"/></td>'
                '<td>Spying</td><td><input type="radio" name="mission" value="6" id="bt6"/></td>'
                f"{tail}</tr></table></form>"
            )
            soup = BeautifulSoup(html, "html.parser")
            self.assertIs(
                ActionExecutor._match_mission_radio(soup, 'form[action*="internalSendFleet"]', [bt1, bt6], "ATTACK"),
                bt1,
            )
            self.assertIs(
                ActionExecutor._match_mission_radio(soup, 'form[action*="internalSendFleet"]', [bt1, bt6], "SPY"),
                bt6,
            )

            sw1, sw6 = _mk_radio("1", "mission", "sw1"), _mk_radio("6", "mission", "sw6")
            sw_html = (
                '<form action="indexInternal.es?action=internalSendFleet"><table><tr>'
                '<td>Spying</td><td><input type="radio" name="mission" value="1" id="sw1"/></td>'
                '<td>Attack</td><td><input type="radio" name="mission" value="6" id="sw6"/></td>'
                f"{tail}</tr></table></form>"
            )
            sw_soup = BeautifulSoup(sw_html, "html.parser")
            self.assertIs(
                ActionExecutor._match_mission_radio(sw_soup, 'form[action*="internalSendFleet"]', [sw1, sw6], "ATTACK"),
                sw6,
            )
            self.assertIs(
                ActionExecutor._match_mission_radio(sw_soup, 'form[action*="internalSendFleet"]', [sw1, sw6], "SPY"),
                sw1,
            )

        # 2. Leading plain-text / header cells on label-after, 3-mission before, escort+Help, and speed rows
        lead_help_after = (
            '<form action="indexInternal.es?action=internalSendFleet"><table><tr>'
            '<td>Help</td><td><input type="radio" name="mission" value="1" id="lh1"/></td>'
            '<td>Attack</td><td><input type="radio" name="mission" value="6" id="lh6"/></td><td>Spying</td>'
            "</tr></table></form>"
        )
        lh1, lh6 = _mk_radio("1", "mission", "lh1"), _mk_radio("6", "mission", "lh6")
        lh_soup = BeautifulSoup(lead_help_after, "html.parser")
        self.assertIs(
            ActionExecutor._match_mission_radio(lh_soup, 'form[action*="internalSendFleet"]', [lh1, lh6], "ATTACK"),
            lh1,
        )
        self.assertIs(
            ActionExecutor._match_mission_radio(lh_soup, 'form[action*="internalSendFleet"]', [lh1, lh6], "SPY"),
            lh6,
        )

        tr3_html = (
            '<form action="indexInternal.es?action=internalSendFleet"><table><tr>'
            "<th>Mission:</th>"
            '<td>Transport</td><td><input type="radio" name="mission" value="3" id="t3"/></td>'
            '<td>Attack</td><td><input type="radio" name="mission" value="1" id="t1"/></td>'
            '<td>Spying</td><td><input type="radio" name="mission" value="6" id="t6"/></td>'
            "<td>Help</td></tr></table></form>"
        )
        t3, t1, t6 = _mk_radio("3", "mission", "t3"), _mk_radio("1", "mission", "t1"), _mk_radio("6", "mission", "t6")
        tr3_soup = BeautifulSoup(tr3_html, "html.parser")
        self.assertIs(
            ActionExecutor._match_mission_radio(tr3_soup, 'form[action*="internalSendFleet"]', [t3, t1, t6], "ATTACK"),
            t1,
        )
        self.assertIs(
            ActionExecutor._match_mission_radio(tr3_soup, 'form[action*="internalSendFleet"]', [t3, t1, t6], "SPY"),
            t6,
        )

        esc_html = (
            '<form action="indexInternal.es?action=internalSendFleet"><table><tr>'
            '<td>Escort</td><td><input type="radio" name="mission" value="1" id="e1"/></td>'
            '<td>Spying</td><td><input type="radio" name="mission" value="6" id="e6"/></td>'
            "<td>Help</td></tr></table></form>"
        )
        e1, e6 = _mk_radio("1", "mission", "e1"), _mk_radio("6", "mission", "e6")
        self.assertIs(
            ActionExecutor._match_mission_radio(
                BeautifulSoup(esc_html, "html.parser"),
                'form[action*="internalSendFleet"]',
                [e1, e6],
                "SPY",
            ),
            e6,
        )

        for spd_tail in ("<td><button>OK</button></td>", "<td>Help</td>"):
            g1, g10 = _mk_radio("1", "g", "g1"), _mk_radio("10", "g", "g10")
            spd_html = (
                '<form action="indexInternal.es?action=internalSendFleet"><table><tr>'
                '<td>10%</td><td><input type="radio" name="g" value="1" id="g1"/></td>'
                '<td>100%</td><td><input type="radio" name="g" value="10" id="g10"/></td>'
                f"{spd_tail}</tr></table></form>"
            )
            spd_soup = BeautifulSoup(spd_html, "html.parser")
            self.assertIsNone(
                ActionExecutor._match_mission_radio(spd_soup, 'form[action*="internalSendFleet"]', [g1, g10], "ATTACK")
            )

        # 3. Double/nested inline & empty containers
        for dw_html in (
            '<form action="indexInternal.es?action=internalSendFleet"><span><span><input type="radio" name="mission" value="1" id="dw1"/></span></span> Attack <span><span><input type="radio" name="mission" value="6" id="dw6"/></span></span> Spying</form>',
            '<form action="indexInternal.es?action=internalSendFleet"><div><span><span><input type="radio" name="mission" value="1" id="dw1"/></span></span> Attack</div><div><span><span><input type="radio" name="mission" value="6" id="dw6"/></span></span> Spying</div></form>',
            '<form action="indexInternal.es?action=internalSendFleet"><div><div><input type="radio" name="mission" value="1" id="dw1"/></div></div> Attack <div><div><input type="radio" name="mission" value="6" id="dw6"/></div></div> Spying</form>',
            '<form action="indexInternal.es?action=internalSendFleet"><span><label><span><input type="radio" name="mission" value="1" id="dw1"/></span></label></span> Attack <span><label><span><input type="radio" name="mission" value="6" id="dw6"/></span></label></span> Spying</form>',
        ):
            dw1, dw6 = _mk_radio("1", "mission", "dw1"), _mk_radio("6", "mission", "dw6")
            dw_soup = BeautifulSoup(dw_html, "html.parser")
            self.assertIs(
                ActionExecutor._match_mission_radio(dw_soup, 'form[action*="internalSendFleet"]', [dw1, dw6], "ATTACK"),
                dw1,
            )
            self.assertIs(
                ActionExecutor._match_mission_radio(dw_soup, 'form[action*="internalSendFleet"]', [dw1, dw6], "SPY"),
                dw6,
            )

        # 4. Single-radio containers with <select> / <legend> and invalid multi-radio <label>
        for single_html in (
            '<form action="indexInternal.es?action=internalSendFleet"><select name="speed"><option>100%</option></select><input type="radio" name="mission" value="1" id="m1"/> Attack</form>',
            '<form action="indexInternal.es?action=internalSendFleet"><div><select name="speed"><option>100%</option></select><span><span><input type="radio" name="mission" value="1" id="m1"/></span></span> Attack</div></form>',
            '<form action="indexInternal.es?action=internalSendFleet"><section><select name="speed"><option>100%</option></select><input type="radio" name="mission" value="1" id="m1"/> Attack</section></form>',
            '<form action="indexInternal.es?action=internalSendFleet"><fieldset><legend>Speed 100% / Spying</legend><input type="radio" name="mission" value="1" id="m1"/> Attack</fieldset></form>',
            '<form action="indexInternal.es?action=internalSendFleet"><table><tr><td><select name="speed"><option>100%</option></select></td><td><input type="radio" name="mission" value="1" id="m1"/></td><td>Attack</td></tr></table></form>',
            '<form action="indexInternal.es?action=internalSendFleet"><table><tr><td><select name="speed"><option>100%</option></select><input type="radio" name="mission" value="1" id="m1"/> Attack</td></tr></table></form>',
        ):
            m1 = _mk_radio("1", "mission", "m1")
            s_soup = BeautifulSoup(single_html, "html.parser")
            self.assertIs(
                ActionExecutor._match_mission_radio(s_soup, 'form[action*="internalSendFleet"]', [m1], "ATTACK"),
                m1,
            )
            self.assertIsNone(
                ActionExecutor._match_mission_radio(s_soup, 'form[action*="internalSendFleet"]', [m1], "SPY"),
            )

        inv_lbl_html = (
            '<form action="indexInternal.es?action=internalSendFleet"><label>'
            '<input type="radio" name="mission" value="1" id="il1"/> Attack '
            '<input type="radio" name="mission" value="6" id="il6"/> Spying'
            "</label></form>"
        )
        il1, il6 = _mk_radio("1", "mission", "il1"), _mk_radio("6", "mission", "il6")
        inv_soup = BeautifulSoup(inv_lbl_html, "html.parser")
        self.assertIs(
            ActionExecutor._match_mission_radio(inv_soup, 'form[action*="internalSendFleet"]', [il1, il6], "ATTACK"),
            il1,
        )
        self.assertIs(
            ActionExecutor._match_mission_radio(inv_soup, 'form[action*="internalSendFleet"]', [il1, il6], "SPY"),
            il6,
        )

    def test_dispatch_fleet_mission_scopes_step1_coords_on_multiform_internal_fleet(self) -> None:
        planet = PlanetState(
            planet_id=12476,
            name="Colony",
            coords="02:380:12",
            planet_type="normal",
            temperature=31,
            fields_used=20,
            fields_max=230,
            resources=Resources(5000, 5000, 5000, 2000),
            storage=Resources(100000, 100000, 100000, 100000),
            energy=EnergyBalance(300, 120),
            ships={"s4": 6},
        )
        state = GameState(31, 103603, "ventkx86", 0.0, (planet,), active_planet_id=12476)
        atk_act = PlannedAction(
            priority="P2",
            kind="ATTACK",
            planet_id=12476,
            planet_coords="02:380:12",
            target_name="02:385:04 (Sleepy)",
            reason="Inactive raid",
            executable_now=True,
        )
        step1_html = """
        <form id="top-nav" action="indexInternal.es?action=internalGalaxy">
          <input type="text" name="gal" value="0"/>
          <input type="text" name="sun" value="0"/>
          <input type="text" name="pla" value="0"/>
          <input type="submit" value="Go"/>
        </form>
        <form action="indexInternal.es?action=internalSendFleet" method="post">
          <input type="text" name="ship_4" value="0"/>
          <input type="text" name="gal"/><input type="text" name="sun"/><input type="text" name="pla"/>
          <input type="submit" value="Continue"/>
        </form>
        """
        step2_html = """
        <form action="indexInternal.es?action=internalSendFleet">
          <label><input type="radio" name="mission" value="1"/> Attack</label>
          <input type="submit" value="Send"/>
        </form>
        """
        nav_gal, nav_sun, nav_pla = MagicMock(), MagicMock(), MagicMock()
        fleet_s4, fleet_gal, fleet_sun, fleet_pla = MagicMock(), MagicMock(), MagicMock(), MagicMock()
        cont_btn, launch_btn = MagicMock(), MagicMock()
        atk_radio = MagicMock()
        atk_radio.get_attribute.side_effect = lambda a: {"value": "1", "name": "mission"}.get(a, "")
        atk_radio.is_selected.return_value = False

        def _find_el(by, val):
            if by == By.CSS_SELECTOR and val == 'form[action*="internalSendFleet"] input[name="gal"]':
                return fleet_gal
            if by == By.CSS_SELECTOR and val == 'form[action*="internalSendFleet"] input[name="sun"]':
                return fleet_sun
            if by == By.CSS_SELECTOR and val == 'form[action*="internalSendFleet"] input[name="pla"]':
                return fleet_pla
            if by == By.CSS_SELECTOR and 'form[action*="internalSendFleet"]' in val and "submit" in val:
                return cont_btn if cont_btn.click.call_count == 0 else launch_btn
            raise AssertionError(f"Unexpected unscoped selector: {by}={val}")

        def _find_els(by, val):
            if by == By.NAME and val == "ship_4":
                return [fleet_s4]
            if by == By.CSS_SELECTOR and val == 'form[action*="internalSendFleet"] input[type="radio"]':
                return [atk_radio]
            raise AssertionError(f"Unexpected find_elements: {by}={val}")

        driver = MagicMock()
        driver.find_element.side_effect = _find_el
        driver.find_elements.side_effect = _find_els
        client = MagicMock(spec=GameBrowserClient)
        client.driver = driver
        client.navigate_menu.return_value = step1_html
        client._wait_frame_ready.side_effect = [step2_html, "<div>Fleet sent</div>"]
        client.read_planets_overview.return_value = state

        ex = ActionExecutor(dry_run=False, action_delay_range=(0.0, 0.0), allow_raids=True)
        res = ex.execute_action(client, state, atk_act)
        self.assertTrue(res.executed)
        fleet_s4.send_keys.assert_called_once_with("6")
        fleet_gal.send_keys.assert_called_once_with("2")
        fleet_sun.send_keys.assert_called_once_with("385")
        fleet_pla.send_keys.assert_called_once_with("4")
        nav_gal.send_keys.assert_not_called()
        nav_sun.send_keys.assert_not_called()
        nav_pla.send_keys.assert_not_called()

    def test_interactive_execution_uses_interactive_priority_and_stays_compatible_with_minimal_fakes(self) -> None:
        # Part (a): Real PageViewLimiter + GameBrowserClient with 17/18 and 18/18 normal views occupied
        now = [200.0]
        limiter = PageViewLimiter(
            min_delay_range=(0.0, 0.0),
            max_views_per_minute=24,
            reserved_interactive_views=6,
            clock=lambda: now[0],
            sleeper=lambda s: now.__setitem__(0, now[0] + s),
        )

        planets_json = json.dumps(
            {
                "info": {
                    "ts": 1700000000,
                    "user": {"userID": 103603, "nickname": "ventkx86"},
                    "universe": {"id": 31},
                },
                "planets": [
                    {
                        "id": 12476,
                        "name": "Colony",
                        "coords": "02:380:12",
                        "type": "normal",
                        "temperature": 31,
                        "fields": 230,
                        "resources": [5000, 3000, 4000, 1000],
                        "storage": [100000, 100000, 100000, 100000],
                        "energy": {"produced": 300, "used": 100},
                        "buildings": {
                            "b1": {"level": 5, "name": "Pig iron mine"},
                            "b5": {"level": 5, "name": "Fusion plant"},
                        },
                        "ships": {"s4": {"level": 5}, "s10": {"level": 4}},
                        "defense": {},
                    }
                ],
                "researches": {"r1": {"level": 1}},
                "specs": {},
            }
        )
        buildings_html = """
        <table><tr>
          <td><a href="indexInternal.es?action=internalDetail&type=building&id=5">Fusion plant</a></td>
          <td>Required: 80 Pig iron 35 Kryptonite</td>
          <td><a class="bau" href="indexInternal.es?action=internalBuildItem&buildingID=5#5">Build level 1</a></td>
        </tr></table>
        """
        fleet_step1_html = """
        <form action="indexInternal.es?action=internalSendFleet" method="post">
          <input type="text" name="ship_4" value="0"/>
          <input type="text" name="ship_10" value="0"/>
          <input type="text" name="gal"/><input type="text" name="sun"/><input type="text" name="pla"/>
          <input type="submit" value="Continue"/>
        </form>
        """
        fleet_step2_html = """
        <form action="indexInternal.es?action=internalSendFleet">
          <input type="radio" name="mission" value="1" id="m1"/> Attack
          <input type="radio" name="mission" value="6" id="m6"/> Spying
          <input type="submit" value="Send"/>
        </form>
        """

        class _El:
            def __init__(self, attrs=None, text="", on_click=None):
                self._attrs = dict(attrs or {})
                self._text = text
                self._on_click = on_click
                self.value = self._attrs.get("value", "")
                self.clicks = 0

            def get_attribute(self, name: str) -> str:
                if name == "textContent":
                    return self._text
                if name == "value":
                    return self.value
                return self._attrs.get(name, "")

            def clear(self) -> None:
                self.value = ""

            def send_keys(self, txt: str) -> None:
                self.value += str(txt)

            def is_selected(self) -> bool:
                return False

            def click(self) -> None:
                self.clicks += 1
                if self._on_click is not None:
                    self._on_click(self)

        class _StatefulDriver:
            def __init__(self) -> None:
                self.current_url = "https://spaceinvasion.bitmeup.com/indexGame.es?sid=live1"
                self.active_frame = None
                self.frames = {
                    "F_Navi": {
                        "href": "https://spaceinvasion.bitmeup.com/indexInternal.es?action=internalMenu",
                        "html": "<html><body>menu</body></html>",
                    },
                    "F_planets": {
                        "href": "https://spaceinvasion.bitmeup.com/indexInternal.es?action=internalPlanetsOverview",
                        "html": "<html><body><script id='json-container'></script></body></html>",
                    },
                    "F_Hauptframe": {
                        "href": "https://spaceinvasion.bitmeup.com/indexInternal.es?action=internalHome",
                        "html": "<html><body>home</body></html>",
                    },
                }
                self.nav_token = None
                self.build_btn = _El(on_click=self._on_build)
                self.s4_inp = _El(attrs={"name": "ship_4", "value": "0"})
                self.s10_inp = _El(attrs={"name": "ship_10", "value": "0"})
                self.gal_inp = _El(attrs={"name": "gal"})
                self.sun_inp = _El(attrs={"name": "sun"})
                self.pla_inp = _El(attrs={"name": "pla"})
                self.step1_btn = _El(on_click=self._on_step1)
                self.step2_btn = _El(on_click=self._on_step2)
                self.r_atk = _El(attrs={"value": "1", "id": "m1", "name": "mission"})
                self.r_spy = _El(attrs={"value": "6", "id": "m6", "name": "mission"})
                self.switch_to = self

            def default_content(self) -> None:
                self.active_frame = None

            def frame(self, frame_el) -> None:
                self.active_frame = frame_el.get_attribute("id")

            def _on_build(self, _el) -> None:
                self.frames["F_Hauptframe"]["html"] = "<div>Construction queue</div>"
                self.nav_token = None

            def _on_step1(self, _el) -> None:
                self.frames["F_Hauptframe"]["href"] = "https://spaceinvasion.bitmeup.com/indexInternal.es?action=internalSendFleet"
                self.frames["F_Hauptframe"]["html"] = fleet_step2_html
                self.nav_token = None

            def _on_step2(self, _el) -> None:
                self.frames["F_Hauptframe"]["html"] = "<div>Fleet dispatched</div>"
                self.nav_token = None

            @property
            def page_source(self) -> str:
                if self.active_frame is None:
                    return "<html></html>"
                return self.frames[self.active_frame]["html"]

            def find_element(self, by: str, value: str):
                if self.active_frame is None:
                    if by == By.ID and value in self.frames:
                        return _El(attrs={"id": value, "src": self.frames[value]["href"]})
                    raise NoSuchElementException(value)
                if self.active_frame == "F_planets" and by == By.ID and value == "json-container":
                    return _El(text=planets_json)
                if self.active_frame == "F_Navi" and by == By.CSS_SELECTOR and "action=" in value:
                    m_act = re.search(r"action=([a-zA-Z0-9_]+)", value)
                    act = m_act.group(1) if m_act else ""

                    def _on_menu(_el, action_name=act) -> None:
                        self.frames["F_Hauptframe"]["href"] = (
                            f"https://spaceinvasion.bitmeup.com/indexInternal.es?action={action_name}&sid=live1"
                        )
                        if action_name == "internalBuildings":
                            self.frames["F_Hauptframe"]["html"] = buildings_html
                        elif action_name == "internalFleet":
                            self.frames["F_Hauptframe"]["html"] = fleet_step1_html
                        elif action_name == "internalHome":
                            self.frames["F_Hauptframe"]["html"] = "<html><body>home</body></html>"
                        self.nav_token = None

                    return _El(
                        attrs={"href": f"indexInternal.es?action={act}&sid=live1"},
                        on_click=_on_menu,
                    )
                if self.active_frame == "F_Hauptframe":
                    if by == By.NAME:
                        return {"gal": self.gal_inp, "sun": self.sun_inp, "pla": self.pla_inp}[value]
                    if by == By.CSS_SELECTOR:
                        if "buildingID=" in value:
                            return self.build_btn
                        if "internalSendFleet" in value and "submit" in value:
                            if "action=internalSendFleet" in self.frames["F_Hauptframe"]["href"]:
                                return self.step2_btn
                            return self.step1_btn
                raise NoSuchElementException(f"{self.active_frame}:{by}={value}")

            def find_elements(self, by: str, value: str):
                if self.active_frame == "F_Hauptframe":
                    if by == By.NAME and value == "ship_4":
                        return [self.s4_inp]
                    if by == By.NAME and value == "ship_10":
                        return [self.s10_inp]
                    if by == By.CSS_SELECTOR and 'input[type="radio"]' in value:
                        return [self.r_atk, self.r_spy]
                return []

            def execute_script(self, script: str, *args):
                if "SI_TPL.menu.sid" in script:
                    return "sid=live1"
                if "window.__siNavToken = arguments[0]" in script:
                    self.nav_token = args[0]
                    return None
                if "location.href = arguments[0]" in script:
                    url = str(args[0])
                    self.frames["F_Hauptframe"]["href"] = url
                    if "internalBuildings" in url:
                        self.frames["F_Hauptframe"]["html"] = buildings_html
                    elif "internalFleet" in url:
                        self.frames["F_Hauptframe"]["html"] = fleet_step1_html
                    self.nav_token = None
                    return None
                if "typeof window.__siNavToken" in script:
                    return {
                        "ready": "complete",
                        "href": self.frames["F_Hauptframe"]["href"],
                        "token": self.nav_token,
                    }
                if "return Boolean(window.__siReloadPending)" in script:
                    return False
                if "return Boolean(document.getElementById('json-container'))" in script:
                    return True
                if "return document.readyState" in script:
                    return "complete"
                if "location.href" in script:
                    fr = self.active_frame or "F_Hauptframe"
                    return self.frames[fr]["href"]
                return None

        state, build_act = _make_state_and_action()
        planet_with_ships = PlanetState(
            planet_id=12476,
            name="Colony",
            coords="02:380:12",
            planet_type="normal",
            temperature=31,
            fields_used=20,
            fields_max=230,
            resources=Resources(5000, 5000, 5000, 2000),
            storage=Resources(100000, 100000, 100000, 100000),
            energy=EnergyBalance(300, 120),
            buildings={"b1": 5, "b5": 5},
            ships={"s4": 5, "s10": 4},
        )
        fleet_state = GameState(31, 103603, "ventkx86", 0.0, (planet_with_ships,), active_planet_id=12476)
        atk_act = PlannedAction(
            priority="P2",
            kind="ATTACK",
            planet_id=12476,
            planet_coords="02:380:12",
            target_name="02:380:04 (Sleepy)",
            reason="Inactive raid",
            executable_now=True,
        )
        spy_act = PlannedAction(
            priority="P2",
            kind="SPY",
            planet_id=12476,
            planet_coords="02:380:12",
            target_id=10,
            target_level=1,
            target_name="02:380:04 (Sleepy)",
            reason="Spy probe",
            executable_now=True,
        )

        # 17/18 normal views: BUILD post-click overview must still use interactive priority
        drv = _StatefulDriver()
        client = GameBrowserClient(
            drv,
            page_limiter=limiter,
            min_nav_delay=(0.0, 0.0),
            timeout=2.0,
            max_limiter_wait=0.5,
        )
        ex = ActionExecutor(
            dry_run=False,
            action_delay_range=(0.0, 0.0),
            allow_raids=True,
            page_limiter=limiter,
            max_limiter_wait=0.5,
        )
        limiter.record_view(17)
        res_b17 = ex.execute_action(client, state, build_act)
        self.assertTrue(res_b17.executed)
        self.assertLessEqual(limiter.views_last_60s(), 24)

        # 18/18 normal views: BUILD, ATTACK, and SPY each succeed inside the 6 reserved interactive slots
        for act_case, st_case in ((build_act, state), (atk_act, fleet_state), (spy_act, fleet_state)):
            now[0] += 100.0
            limiter.reset()
            limiter.record_view(18)
            drv.frames["F_Hauptframe"]["href"] = "https://spaceinvasion.bitmeup.com/indexInternal.es?action=internalHome"
            res = ex.execute_action(client, st_case, act_case)
            self.assertTrue(res.executed, f"{act_case.kind} failed: {res.message}")
            self.assertLessEqual(limiter.views_last_60s(), 24)

        # Part (b): Minimal fake client without `priority` kwarg on any method
        class _MinimalFakeClient:
            def __init__(self) -> None:
                self.driver = MagicMock()
                self.driver.find_element.return_value = MagicMock()
                self.last_galaxy_coords = (2, 380)

            def ensure_in_game(self) -> None:
                return None

            def navigate_menu(self, action: str) -> str:
                return buildings_html

            def read_planets_overview(self, refresh: bool = False) -> GameState:
                return state

            def open_galaxy(self, galaxy=None, system=None) -> str:
                return "<table><tr><td>4</td><td><span class='galaxy_yellow'>Idle</span></td><td>Sleepy</td></tr></table>"

            def _switch_frame(self, frame_id: str) -> None:
                return None

            def _wait_frame_ready(self, frame_id: str, expected_action=None, *, nav_token=None) -> str:
                return "<div>Construction queue</div>"

        min_client = _MinimalFakeClient()
        min_ex = ActionExecutor(dry_run=False, action_delay_range=(0.0, 0.0))
        min_build_res = min_ex.execute_action(min_client, state, build_act)
        self.assertTrue(min_build_res.executed)

        scan_act = PlannedAction(
            priority="P2",
            kind="SCAN_GALAXY",
            planet_id=12476,
            planet_coords="02:380:12",
            target_name="Galaxy [02:380]",
            reason="Scan",
            executable_now=True,
        )
        min_scan_res = min_ex.execute_action(min_client, state, scan_act)
        self.assertTrue(min_scan_res.executed)

    def test_execution_result_populates_updated_state_and_preserves_mocks(self) -> None:
        from spaceinvasion.state import TutorialStatus

        p1 = PlanetState(
            planet_id=12476,
            name="Home",
            coords="02:380:12",
            planet_type="normal",
            temperature=31,
            fields_used=20,
            fields_max=230,
            resources=Resources(50000, 50000, 50000, 50000),
            storage=Resources(200000, 200000, 200000, 200000),
            energy=EnergyBalance(400, 120),
            buildings={"b1": 5, "b2": 3, "b5": 9, "b8": 1, "b9": 1},
            ships={"s1": 2, "s4": 6, "s10": 4},
        )
        p2 = PlanetState(
            planet_id=12477,
            name="Colony",
            coords="02:380:14",
            planet_type="normal",
            temperature=25,
            fields_used=10,
            fields_max=190,
            resources=Resources(30000, 30000, 30000, 10000),
            storage=Resources(200000, 200000, 200000, 200000),
            energy=EnergyBalance(200, 60),
            buildings={"b1": 3, "b5": 5, "b9": 1},
            ships={"s4": 5, "s10": 3},
        )
        state = GameState(
            31,
            103603,
            "ventkx86",
            100.0,
            (p1, p2),
            active_planet_id=12476,
            researches={"r1": 1},
            tutorial=TutorialStatus(step=1, total_steps=11, can_complete=True),
        )

        # 1. Non-GameState MagicMock overview yields updated_state=None cleanly
        mock_ex = ActionExecutor(dry_run=False, action_delay_range=(0.0, 0.0))
        mock_client = MagicMock(spec=GameBrowserClient)
        mock_client.driver = MagicMock()
        mock_client._wait_frame_ready.return_value = "<html><body>Tutorial 2 / 11</body></html>"
        mock_client.read_planets_overview.return_value = MagicMock()
        tut_act = PlannedAction(
            priority="P2",
            kind="CLAIM_TUTORIAL",
            planet_id=12476,
            planet_coords="02:380:12",
            target_name="Tutorial 1/11",
            reason="Claim",
            executable_now=True,
        )
        tut_mock_res = mock_ex._execute_claim_tutorial(mock_client, tut_act)  # 2-arg compat
        self.assertTrue(tut_mock_res.executed)
        self.assertIsNone(tut_mock_res.updated_state)

        # 2. Real GameState returns populated updated_state across live action kinds
        buildings_html = (
            '<table><tr><td><a href="indexInternal.es?action=internalDetail&type=building&id=1">Pig iron mine</a></td>'
            '<td>Required: 100 Pig iron</td>'
            '<td><a class="bau" href="indexInternal.es?action=internalBuildItem&buildingID=1">Build level 6</a></td></tr></table>'
        )
        buildings_after_html = (
            '<select name="planet"><option selected value="indexInternal.es?action=internalBuildings&newPlanet=12476">Home</option></select>'
            '<table><tr><td>Construction queue ( 1 Elements ) Pig iron mine (Level 6)</td></tr></table>'
            '<table><tr><td><a href="indexInternal.es?action=internalDetail&type=building&id=1">Pig iron mine</a></td>'
            '<td>Required: 150 Pig iron</td><td><span>In construction</span></td></tr></table>'
        )
        research_html = (
            '<table><tr><td><a href="indexInternal.es?action=internalDetail&type=research&id=1">Mining technology</a></td>'
            '<td>Required: 200 Pig iron</td>'
            '<td><a class="bau" href="indexInternal.es?action=internalBuildItem&researchID=1">Research level 2</a></td></tr></table>'
        )
        research_after_html = (
            '<table><tr><td><a href="indexInternal.es?action=internalDetail&type=research&id=1">Mining technology</a></td>'
            '<td>Required: 400 Pig iron</td><td><span>In research (Cancel)</span></td></tr></table>'
        )
        ship_html = (
            '<form action="indexInternal.es?action=internalBuildItem&buildShip=1">'
            '<table><tr><td><a href="indexInternal.es?action=internalDetail&type=ship&id=1">Small transport</a></td>'
            '<td>Required: 3.000 Metal 3.000 Kryptonite <input type="text" name="ship[1]" value="0"/></td></tr></table>'
            '<input type="submit" value="Build"/></form>'
        )
        econ_html = (
            '<form id="ress"><select name="resource2"><option selected value="100">100%</option>'
            '<option value="0">0%</option></select><input type="submit" value="Save"/></form>'
        )
        econ_after_html = (
            '<form id="ress"><select name="resource2"><option value="100">100%</option>'
            '<option selected value="0">0%</option></select></form>'
        )
        tut_after_html = (
            '<script>SI_TPL={planetID:12476};</script>'
            '<div>Tutorial 2 / 11</div><div>active factor: x20.0</div>'
        )

        ov_after_ship = GameState(
            31,
            103603,
            "ventkx86",
            105.0,
            (
                PlanetState(
                    planet_id=12476,
                    name="Home",
                    coords="02:380:12",
                    planet_type="normal",
                    temperature=31,
                    fields_used=20,
                    fields_max=230,
                    resources=p1.resources,
                    storage=p1.storage,
                    energy=p1.energy,
                    buildings=p1.buildings,
                    ships={"s1": 4, "s4": 6, "s10": 4},
                ),
                p2,
            ),
            active_planet_id=12476,
            researches={"r1": 1},
        )

        # Build
        ex = ActionExecutor(dry_run=False, action_delay_range=(0.0, 0.0), allow_raids=True)
        c_build = MagicMock(spec=GameBrowserClient)
        c_build.driver = MagicMock()
        c_build.read_menu_status.return_value = ({"spio": 1}, 500, 12)
        c_build.navigate_menu.return_value = buildings_html
        c_build._wait_frame_ready.return_value = buildings_after_html
        c_build.read_planets_overview.return_value = state
        res_b = ex.execute_action(
            c_build,
            state,
            PlannedAction("P2", "BUILD", 12476, "02:380:12", "Pig iron mine", "b1", target_id=1, target_level=6, executable_now=True),
        )
        self.assertTrue(res_b.executed)
        assert res_b.updated_state is not None
        self.assertEqual(res_b.updated_state.unread_messages, {"spio": 1})
        self.assertEqual(res_b.updated_state.account_points, 500)
        self.assertTrue(any(q.category == "building" for q in res_b.updated_state.active_queues))
        self.assertEqual(len(res_b.updated_state.planets[0].building_offers), 1)

        # Research
        c_res = MagicMock(spec=GameBrowserClient)
        c_res.driver = MagicMock()
        c_res.read_menu_status.return_value = ({}, 500, 12)
        c_res.navigate_menu.return_value = research_html
        c_res._wait_frame_ready.return_value = research_after_html
        c_res.read_planets_overview.return_value = state
        res_r = ex.execute_action(
            c_res,
            state,
            PlannedAction("P2", "RESEARCH", 12476, "02:380:12", "Mining technology", "r1", target_id=1, target_level=2, executable_now=True),
        )
        self.assertTrue(res_r.executed)
        assert res_r.updated_state is not None
        self.assertEqual(len(res_r.updated_state.research_offers), 1)
        self.assertTrue(any(q.category == "research" for q in res_r.updated_state.active_queues))

        # Build ship
        c_ship = MagicMock(spec=GameBrowserClient)
        c_ship.driver = MagicMock()
        c_ship.read_menu_status.return_value = ({}, 510, 11)
        c_ship.navigate_menu.return_value = ship_html
        c_ship._wait_frame_ready.return_value = ship_html
        c_ship.read_planets_overview.return_value = ov_after_ship
        res_s = ex.execute_action(
            c_ship,
            state,
            PlannedAction("P2", "BUILD_SHIP", 12476, "02:380:12", "Small transport", "s1", target_id=1, target_level=2, executable_now=True),
        )
        self.assertTrue(res_s.executed)
        assert res_s.updated_state is not None
        self.assertEqual(res_s.updated_state.planets[0].ships["s1"], 4)
        self.assertEqual(len(res_s.updated_state.planets[0].ship_offers), 1)

        # Throttle building
        c_thr = MagicMock(spec=GameBrowserClient)
        c_thr.driver = MagicMock()
        c_thr.read_menu_status.return_value = ({}, 510, 11)
        c_thr.navigate_menu.return_value = econ_html
        c_thr._wait_frame_ready.return_value = econ_after_html
        c_thr.read_planets_overview.return_value = state
        with patch("spaceinvasion.executor.Select"):
            res_t = ex.execute_action(
                c_thr,
                state,
                PlannedAction("P1", "THROTTLE_BUILDING", 12476, "02:380:12", "Blast furnace 0%", "Pause", target_id=2, target_level=0, executable_now=True),
            )
        self.assertTrue(res_t.executed)
        assert res_t.updated_state is not None
        self.assertEqual(res_t.updated_state.planets[0].economy_rates["resource2"], 0)

        # Claim tutorial (via execute_action passing state)
        c_tut = MagicMock(spec=GameBrowserClient)
        c_tut.driver = MagicMock()
        c_tut.read_menu_status.return_value = ({}, 520, 10)
        c_tut._wait_frame_ready.return_value = tut_after_html
        c_tut.read_planets_overview.return_value = state
        res_tut = ex.execute_action(c_tut, state, tut_act)
        self.assertTrue(res_tut.executed)
        assert res_tut.updated_state is not None
        assert res_tut.updated_state.tutorial is not None
        self.assertEqual(res_tut.updated_state.tutorial.step, 2)
        self.assertEqual(res_tut.updated_state.speedup_factor, 20.0)

        # Cross-planet fleet mission updates active_planet_id to action.planet_id (12477)
        step1_html = (
            '<select name="planet"><option selected value="indexInternal.es?action=internalBuildings&newPlanet=12476">Home</option>'
            '<option value="indexInternal.es?action=internalBuildings&newPlanet=12477">Colony</option></select>'
        )
        step1_fleet = (
            '<form action="indexInternal.es?action=internalSendFleet">'
            '<input name="ship_4"/><input name="gal"/><input name="sun"/><input name="pla"/><input type="submit"/></form>'
        )
        step2_fleet = (
            '<form action="indexInternal.es?action=internalSendFleet">'
            '<input type="radio" name="mission" value="1" id="m1"/> Attack<input type="submit"/></form>'
        )
        c_flt = MagicMock(spec=GameBrowserClient)
        c_flt.driver = MagicMock()
        c_flt.read_menu_status.return_value = ({}, 520, 10)
        c_flt.navigate_menu.side_effect = [step1_html, step1_fleet]
        c_flt._wait_frame_ready.side_effect = ["<div>switched</div>", step2_fleet, "<div>Fleet dispatched</div>"]
        c_flt.read_planets_overview.return_value = state
        planet_sel = MagicMock()
        opt_1 = MagicMock()
        opt_1.get_attribute.return_value = "indexInternal.es?action=internalBuildings&newPlanet=12476"
        opt_1.is_selected.return_value = True
        opt_2 = MagicMock()
        opt_2.get_attribute.return_value = "indexInternal.es?action=internalBuildings&newPlanet=12477"
        opt_2.is_selected.return_value = False
        planet_sel.find_elements.return_value = [opt_1, opt_2]
        c_flt.driver.find_element.side_effect = lambda by, val: planet_sel if 'select[name="planet"]' in str(val) else MagicMock()
        atk_radio = MagicMock()
        atk_radio.get_attribute.side_effect = lambda a: {"value": "1", "name": "mission", "id": "m1"}.get(a, "")
        atk_radio.is_selected.return_value = False
        c_flt.driver.find_elements.side_effect = [[MagicMock()], [atk_radio]]
        with patch("spaceinvasion.executor.Select"):
            res_flt = ex.execute_action(
                c_flt,
                state,
                PlannedAction("P2", "ATTACK", 12477, "02:380:14", "02:380:04", "Raid", executable_now=True),
            )
        self.assertTrue(res_flt.executed)
        assert res_flt.updated_state is not None
        self.assertEqual(res_flt.updated_state.active_planet_id, 12477)

    def test_unconfirmed_active_planet_verifies_internal_buildings_before_fleet_and_tracks_noop_switch(self) -> None:
        from e2e_tests.harness import FakeDriver, FakeSpaceInvasionServer

        srv = FakeSpaceInvasionServer(
            planets={
                12476: {
                    "planet_id": 12476,
                    "name": "Home",
                    "coords": "02:380:12",
                    "planet_type": "normal",
                    "temperature": 31,
                    "fields_max": 230,
                    "resources": [10000, 10000, 10000, 10000],
                    "storage": [500000, 500000, 500000, 500000],
                    "energy": {"produced": 400, "used": 80},
                    "buildings": {"b1": 4, "b2": 3, "b5": 8, "b9": 1},
                    "ships": {"s4": 10, "s10": 4},
                    "defense": {},
                    "economy_rates": {
                        "resource1": 100,
                        "resource2": 100,
                        "resource3": 100,
                        "resource4": 100,
                        "resource5": 100,
                    },
                    "building_queue": [],
                },
                12477: {
                    "planet_id": 12477,
                    "name": "Colony",
                    "coords": "02:380:14",
                    "planet_type": "normal",
                    "temperature": 24,
                    "fields_max": 190,
                    "resources": [10000, 10000, 10000, 10000],
                    "storage": [500000, 500000, 500000, 500000],
                    "energy": {"produced": 300, "used": 60},
                    "buildings": {"b1": 2, "b2": 2, "b5": 6, "b9": 1},
                    "ships": {"s4": 5, "s10": 4},
                    "defense": {},
                    "economy_rates": {
                        "resource1": 100,
                        "resource2": 100,
                        "resource3": 100,
                        "resource4": 100,
                        "resource5": 100,
                    },
                    "building_queue": [],
                },
            },
            active_planet_id=12477,
        )
        drv = FakeDriver(srv)
        lim = PageViewLimiter(
            min_delay_range=(0.0, 0.0),
            max_views_per_minute=24,
            reserved_interactive_views=6,
        )
        client = GameBrowserClient(drv, page_limiter=lim, min_nav_delay=(0.0, 0.0), timeout=2.0)
        ex = ActionExecutor(dry_run=False, action_delay_range=(0.0, 0.0))

        # Cold off-home snapshot falls back to planets[0]=12476 because driver starts on internalGalaxy
        drv._navigate_frame("F_Hauptframe", "indexInternal.es?action=internalGalaxy")
        state = client.read_full_snapshot(
            include_buildings=False,
            include_research=False,
            include_shipyard=False,
            include_home=False,
            return_home=False,
        )
        self.assertFalse(client.last_snapshot_observed_active_planet)
        self.assertEqual(state.active_planet_id, 12476)

        spy_act = PlannedAction("P2", "SPY", 12476, "02:380:12", "02:380:04", "Recon", target_level=1, executable_now=True)
        res = ex.execute_action(client, state, spy_act, active_planet_confirmed=False)
        self.assertTrue(res.executed, res.message)
        self.assertEqual(srv.launched_fleets[-1]["origin_planet_id"], 12476)
        self.assertEqual(res.observed_active_planet_id, 12476)
        self.assertTrue(res.observed_active_planet_confirmed)

        # Cross-planet no-op throttle (already at 100%) on 12477 still reports observed_active_planet_id=12477
        noop_act = PlannedAction(
            "P1",
            "THROTTLE_BUILDING",
            12477,
            "02:380:14",
            "Blast furnace 100%",
            "No-op",
            target_id=2,
            target_level=100,
            executable_now=True,
        )
        noop_res = ex.execute_action(client, state, noop_act, active_planet_confirmed=True)
        self.assertFalse(noop_res.executed)
        self.assertEqual(srv.active_planet_id, 12477)
        self.assertEqual(noop_res.observed_active_planet_id, 12477)
        self.assertTrue(noop_res.observed_active_planet_confirmed)

        # Boundary check: newplanet=124 MUST NOT match newplanet=12476
        prefix_html = (
            '<select name="planet">'
            '<option selected value="indexInternal.es?action=internalBuildings&newPlanet=12476">Home</option>'
            '<option value="indexInternal.es?action=internalBuildings&newPlanet=12477">Colony</option>'
            "</select>"
        )
        self.assertEqual(ActionExecutor._find_newplanet_switch_target(prefix_html, 124), (False, None))

        # Defense-in-depth mismatch abort: missing target option in <select> aborts instead of launching from 12477
        orig_switcher = srv._planet_switcher_html
        srv._planet_switcher_html = lambda act: (
            '<select name="planet">'
            '<option selected value="indexInternal.es?action=internalBuildings&newPlanet=12477">Colony</option>'
            "</select>"
        )
        srv.active_planet_id = 12477
        fleet_count_before = len(srv.launched_fleets)
        bad_res = ex.execute_action(client, state, spy_act, active_planet_confirmed=False)
        srv._planet_switcher_html = orig_switcher
        self.assertFalse(bad_res.executed)
        self.assertIn("not requested origin 12476", bad_res.message)
        self.assertEqual(len(srv.launched_fleets), fleet_count_before)
        self.assertEqual(bad_res.observed_active_planet_id, 12477)
        self.assertTrue(bad_res.observed_active_planet_confirmed)

    def test_non_fleet_confirmed_mismatch_aborts_without_mutating_wrong_planet_or_economy_rates(self) -> None:
        from e2e_tests.harness import FakeDriver, FakeSpaceInvasionServer

        srv = FakeSpaceInvasionServer(
            planets={
                12476: {
                    "planet_id": 12476,
                    "name": "Home",
                    "coords": "02:380:12",
                    "planet_type": "normal",
                    "temperature": 31,
                    "fields_max": 230,
                    "resources": [10000, 10000, 10000, 10000],
                    "storage": [500000, 500000, 500000, 500000],
                    "energy": {"produced": 400, "used": 80},
                    "buildings": {"b1": 4, "b2": 3, "b5": 8, "b8": 5, "b9": 2},
                    "ships": {"s1": 2, "s4": 10},
                    "defense": {},
                    "economy_rates": {
                        "resource1": 100,
                        "resource2": 100,
                        "resource3": 100,
                        "resource4": 100,
                        "resource5": 100,
                    },
                    "building_queue": [],
                },
                12477: {
                    "planet_id": 12477,
                    "name": "Colony",
                    "coords": "02:380:14",
                    "planet_type": "normal",
                    "temperature": 24,
                    "fields_max": 190,
                    "resources": [10000, 10000, 10000, 10000],
                    "storage": [500000, 500000, 500000, 500000],
                    "energy": {"produced": 300, "used": 60},
                    "buildings": {"b1": 2, "b2": 2, "b5": 6, "b8": 5, "b9": 2},
                    "ships": {"s1": 1, "s4": 5},
                    "defense": {},
                    "economy_rates": {
                        "resource1": 100,
                        "resource2": 100,
                        "resource3": 100,
                        "resource4": 100,
                        "resource5": 100,
                    },
                    "building_queue": [],
                },
            },
            active_planet_id=12476,
            researches={"r1": 0, "r2": 1},
        )
        srv._planet_switcher_html = lambda act: (
            f'<select name="planet">'
            f'<option selected="selected" value="indexInternal.es?action={act}&newPlanet=12476">Home</option>'
            f"</select>"
        )
        drv = FakeDriver(srv)
        lim = PageViewLimiter(
            min_delay_range=(0.0, 0.0),
            max_views_per_minute=24,
            reserved_interactive_views=6,
        )
        client = GameBrowserClient(drv, page_limiter=lim, min_nav_delay=(0.0, 0.0), timeout=2.0)
        state = client.read_full_snapshot(
            include_buildings=False,
            include_research=False,
            include_shipyard=False,
            include_home=True,
            return_home=False,
        )
        ex = ActionExecutor(dry_run=False, action_delay_range=(0.0, 0.0))

        actions = [
            PlannedAction("P2", "BUILD", 12477, "02:380:14", "Pig iron mine", "b1", target_id=1, target_level=3, executable_now=True),
            PlannedAction("P2", "RESEARCH", 12477, "02:380:14", "Mining technology", "r1", target_id=1, target_level=1, executable_now=True),
            PlannedAction("P2", "BUILD_SHIP", 12477, "02:380:14", "Small transport", "s1", target_id=1, target_level=2, executable_now=True),
            PlannedAction("P1", "THROTTLE_BUILDING", 12477, "02:380:14", "Blast furnace 0%", "Pause", target_id=2, target_level=0, executable_now=True),
        ]
        for act in actions:
            res = ex.execute_action(client, state, act)
            self.assertFalse(res.executed, f"{act.kind} unexpectedly executed on wrong planet: {res}")
            self.assertIn("active planet is 12476", res.message)
            self.assertIn("not requested planet 12477", res.message)
            self.assertEqual(res.observed_active_planet_id, 12476)
            self.assertTrue(res.observed_active_planet_confirmed)

        self.assertEqual(srv.planets[12476]["buildings"]["b1"], 4)
        self.assertEqual(srv.planets[12477]["buildings"]["b1"], 2)
        self.assertEqual(srv.researches["r1"], 0)
        self.assertEqual(srv.planets[12476]["resources"][0], 10000)
        self.assertEqual(srv.planets[12476]["ships"]["s1"], 2)
        self.assertEqual(srv.planets[12477]["ships"]["s1"], 1)
        self.assertEqual(srv.planets[12476]["economy_rates"]["resource2"], 100)
        self.assertNotIn(12477, ex.last_economy_rates)

        # Pre-existing cached economy rates for 12477 must not be overwritten with 12476's rates
        ex.last_economy_rates[12477] = {"resource2": 50}
        res_thr2 = ex.execute_action(client, state, actions[-1])
        self.assertFalse(res_thr2.executed)
        self.assertEqual(ex.last_economy_rates[12477]["resource2"], 50)

    def test_select_planet_if_needed_no_wait_stall_when_switch_fails_with_require_confirm(self) -> None:
        stale_html = (
            '<select name="planet">'
            '<option selected="selected" value="indexInternal.es?action=internalBuildings&newPlanet=12476">Home</option>'
            '<option value="indexInternal.es?action=internalBuildings&newPlanet=12477">Colony</option>'
            "</select>"
        )
        # (a) Live DOM <select> contains only 12476 (omits 12477) -> aborts immediately without wait or nav token
        opt_12476 = MagicMock()
        opt_12476.get_attribute.return_value = "indexInternal.es?action=internalBuildings&newPlanet=12476"
        opt_12476.is_selected.return_value = True
        sel_dom = MagicMock()
        sel_dom.tag_name = "select"
        sel_dom.get_dom_attribute.return_value = None
        sel_dom.find_elements.return_value = [opt_12476]
        client_dom = MagicMock(spec=GameBrowserClient)
        client_dom.driver = MagicMock()
        client_dom.driver.find_element.return_value = sel_dom

        ex = ActionExecutor(dry_run=False, action_delay_range=(0.0, 0.0))
        switched, updated = ex._select_planet_if_needed(client_dom, 12477, html=stale_html, require_confirm=True)
        self.assertFalse(switched)
        self.assertIsNone(updated)
        client_dom._wait_frame_ready.assert_not_called()
        client_dom._mark_frame_navigation.assert_not_called()
        client_dom._pace.assert_not_called()
        self.assertEqual(ex.last_observed_active_planet_id, 12476)
        self.assertTrue(ex.last_observed_active_planet_confirmed)

        # (b) Minimal mock <select> returning [] on TAG_NAME where Select.select_by_value raises NoSuchElementException
        sel_empty = MagicMock()
        sel_empty.tag_name = "select"
        sel_empty.get_dom_attribute.return_value = None
        sel_empty.find_elements.return_value = []
        client_empty = MagicMock(spec=GameBrowserClient)
        client_empty.driver = MagicMock()
        client_empty.driver.find_element.return_value = sel_empty

        ex_b = ActionExecutor(dry_run=False, action_delay_range=(0.0, 0.0))
        switched_b, updated_b = ex_b._select_planet_if_needed(client_empty, 12477, html=stale_html, require_confirm=True)
        self.assertFalse(switched_b)
        self.assertIsNone(updated_b)
        client_empty._wait_frame_ready.assert_not_called()
        self.assertEqual(ex_b.last_observed_active_planet_id, 12476)
        self.assertTrue(ex_b.last_observed_active_planet_confirmed)

        # (c) Static mock compatibility (auditor Caveat #2): patched Select + minimal updated_html preserves switch
        client_ok = MagicMock(spec=GameBrowserClient)
        client_ok.driver = MagicMock()
        client_ok.driver.find_element.return_value = sel_empty
        client_ok._wait_frame_ready.return_value = "<div>switched</div>"
        ex_c = ActionExecutor(dry_run=False, action_delay_range=(0.0, 0.0))
        with patch("spaceinvasion.executor.Select"):
            switched_c, updated_c = ex_c._select_planet_if_needed(client_ok, 12477, html=stale_html, require_confirm=True)
        self.assertTrue(switched_c)
        self.assertEqual(updated_c, "<div>switched</div>")
        client_ok._wait_frame_ready.assert_called_once()
        self.assertEqual(ex_c.last_observed_active_planet_id, 12477)
        self.assertTrue(ex_c.last_observed_active_planet_confirmed)


if __name__ == "__main__":
    unittest.main()
