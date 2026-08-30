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

        bld_html_after = """
        <select name="planet">
          <option value="indexInternal.es?action=internalBuildings&newPlanet=11111">Home</option>
          <option value="indexInternal.es?action=internalBuildings&newPlanet=22222" selected="selected">RaidColony</option>
        </select>
        """
        client = MagicMock(spec=GameBrowserClient)
        client.driver = driver
        client.navigate_menu.side_effect = [bld_html, fleet_html]
        client._wait_frame_ready.side_effect = [bld_html_after, send_html, "<div>Fleet sent</div>"]
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
        from tests.e2e.harness import FakeDriver, FakeSpaceInvasionServer

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
        from tests.e2e.harness import FakeDriver, FakeSpaceInvasionServer

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

        # (d) Live SpaceInvasion RETRO wraps <select name="planet"> in <div style="display:none;">
        #     so Select.select_by_value and option.click() raise ElementNotInteractableException ->
        #     JS onchange fallback switches planets cleanly and confirms active planet ID.
        from selenium.common.exceptions import ElementNotInteractableException

        opt_home = MagicMock()
        opt_home.get_attribute.return_value = "indexInternal.es?action=internalBuildings&newPlanet=12476"
        opt_home.is_selected.return_value = True
        opt_col = MagicMock()
        opt_col.get_attribute.return_value = "indexInternal.es?action=internalBuildings&newPlanet=12453"
        opt_col.is_selected.return_value = False
        opt_col.click.side_effect = ElementNotInteractableException("hidden inside display:none")

        sel_hidden = MagicMock()
        sel_hidden.tag_name = "select"
        sel_hidden.get_dom_attribute.return_value = None
        sel_hidden.find_elements.return_value = [opt_home, opt_col]

        client_retro = MagicMock(spec=GameBrowserClient)
        client_retro.driver = MagicMock()
        client_retro.driver.find_element.return_value = sel_hidden
        client_retro.driver.execute_script.side_effect = lambda script, *args: (
            True if "sel.onchange" in str(script) else None
        )
        retro_before_html = (
            '<div style="display:none;"><form name="fselect"><select name="planet" '
            'onchange="location.href=this.options[this.selectedIndex].value;">'
            '<option selected="selected" value="indexInternal.es?action=internalBuildings&newPlanet=12476">Home</option>'
            '<option value="indexInternal.es?action=internalBuildings&newPlanet=12453">Colony</option>'
            "</select></form></div>"
        )
        retro_after_html = (
            '<div style="display:none;"><form name="fselect"><select name="planet">'
            '<option value="indexInternal.es?action=internalBuildings&newPlanet=12476">Home</option>'
            '<option selected="selected" value="indexInternal.es?action=internalBuildings&newPlanet=12453">Colony</option>'
            "</select></form></div>"
        )
        client_retro._wait_frame_ready.return_value = retro_after_html

        ex_d = ActionExecutor(dry_run=False, action_delay_range=(0.0, 0.0))
        with patch("spaceinvasion.executor.Select") as mock_sel_cls:
            mock_sel_cls.return_value.select_by_value.side_effect = ElementNotInteractableException("hidden")
            switched_d, updated_d = ex_d._select_planet_if_needed(
                client_retro, 12453, html=retro_before_html, require_confirm=True
            )
        self.assertTrue(switched_d)
        self.assertEqual(updated_d, retro_after_html)
        client_retro._wait_frame_ready.assert_called_once()
        self.assertEqual(ex_d.last_observed_active_planet_id, 12453)
        self.assertTrue(ex_d.last_observed_active_planet_confirmed)

    def test_tier5_bounce_detection_raid_stats_preserved_stale_retry_and_german_missions(self) -> None:
        from bs4 import BeautifulSoup
        from selenium.common.exceptions import NoSuchElementException, StaleElementReferenceException

        # 1. Server bounce back to old planet detected as switched=False
        html_with_select = (
            '<select name="planet">'
            '<option value="indexInternal.es?newPlanet=12476" selected="selected">Home</option>'
            '<option value="indexInternal.es?newPlanet=12477">Colony</option>'
            "</select>"
        )
        bounced_html = (
            '<select name="planet">'
            '<option value="indexInternal.es?newPlanet=12476" selected="selected">Home</option>'
            '<option value="indexInternal.es?newPlanet=12477">Colony</option>'
            "</select>"
        )
        client_bounce = MagicMock(spec=GameBrowserClient)
        client_bounce.driver = MagicMock()
        client_bounce._wait_frame_ready.return_value = bounced_html
        ex_b = ActionExecutor(dry_run=False, action_delay_range=(0.0, 0.0))
        with patch("spaceinvasion.executor.Select"):
            switched_b, _ = ex_b._select_planet_if_needed(client_bounce, 12477, html=html_with_select, require_confirm=True)
        self.assertFalse(switched_b)
        self.assertEqual(ex_b.last_observed_active_planet_id, 12476)

        # 2. _build_updated_state forwards prev_state.raid_stats
        p0 = PlanetState(
            planet_id=12476,
            name="Home",
            coords="02:380:12",
            planet_type="normal",
            temperature=20,
            fields_used=10,
            fields_max=200,
            resources=Resources(),
            storage=Resources(100_000, 100_000, 100_000, 100_000),
            energy=EnergyBalance(100, 50),
        )
        prev_st = GameState(31, 1, "u", 100.0, (p0,), active_planet_id=12476, raid_stats={"overall": {"attempts": 5}})
        act_b = PlannedAction(
            priority="P2",
            kind="BUILD",
            planet_id=12476,
            planet_coords="02:380:12",
            target_id=1,
            target_name="Pig",
            reason="Test build",
            executable_now=True,
        )
        client_u = MagicMock()
        client_u.wait_for_home_frame.return_value = "<html></html>"
        client_u.read_menu_status.return_value = {}
        ex_u = ActionExecutor(dry_run=False, action_delay_range=(0.0, 0.0))
        updated_ov = GameState(31, 1, "u", 105.0, (p0,), active_planet_id=12476)
        st_out = ex_u._build_updated_state(client_u, prev_st, act_b, updated_ov, page_html="<html></html>", is_home=True)
        self.assertIsNotNone(st_out)
        assert st_out is not None
        self.assertEqual(st_out.raid_stats, {"overall": {"attempts": 5}})

        # 3. Stale retry once -> clean failure on NoSuchElementException in claim_tutorial & build_ship
        client_s = MagicMock(spec=GameBrowserClient)
        client_s.driver = MagicMock()
        tut_div = MagicMock()
        tut_div.is_displayed.return_value = True
        client_s.navigate_menu.return_value = '<div id="tutorial.text"><form action="indexInternal.es?action=internalHome"><input type="hidden" name="tutorialComplete" value="1"><input type="submit" value="Claim"></form></div>'
        client_s.driver.find_element.side_effect = [
            tut_div,
            StaleElementReferenceException("stale"),
            tut_div,
            NoSuchElementException("missing"),
        ]
        client_s._wait_frame_ready.return_value = '<div id="tutorial.text"></div>'
        tut_act = PlannedAction(
            priority="P1",
            kind="CLAIM_TUTORIAL",
            planet_id=12476,
            planet_coords="02:380:12",
            target_id=1,
            target_name="Claim",
            reason="Claim tutorial reward",
            action_href="indexInternal.es?action=internalTutorial&claim=1",
            executable_now=True,
        )
        res_tut = ex_u._execute_claim_tutorial(client_s, tut_act)
        self.assertFalse(res_tut.executed)
        self.assertIn("not found", res_tut.message.lower())

        ship_act = PlannedAction(
            priority="P2",
            kind="BUILD_SHIP",
            planet_id=12476,
            planet_coords="02:380:12",
            target_id=1,
            target_level=2,
            target_name="Small transporter",
            reason="Build ships",
            executable_now=True,
        )
        client_ship = MagicMock(spec=GameBrowserClient)
        client_ship.driver = MagicMock()
        client_ship.navigate_menu.return_value = (
            '<html><body><form action="indexInternal.es?action=internalBuildItem&buildShip=1" method="POST">'
            '<table><tr><td><a href="indexInternal.es?action=internalDetail&type=ship&id=1">Small transport</a> '
            'Available: 0 Required: 3.000 Metal 3.000 Kryptonite '
            '<input type="text" name="ship[1]" value="0"/></td></tr></table>'
            '<input type="submit" value="Build"/></form></body></html>'
        )
        client_ship.driver.find_element.side_effect = [
            StaleElementReferenceException("stale"),
            NoSuchElementException("no input"),
        ]
        res_ship = ex_u._execute_build_ship(client_ship, prev_st, ship_act)
        self.assertFalse(res_ship.executed)
        self.assertIn("not found", res_ship.message.lower())

        # 4. German mission verb support ('Angriff', 'Angreifen', 'Spionieren', 'Hinweis: Tipp', Pass 2 skipping 'Halten')
        def _mk_r(val: str, name: str = "mission", rid: str = "") -> MagicMock:
            el = MagicMock()
            attrs = {"value": val, "name": name, "id": rid}
            el.get_attribute.side_effect = lambda k: attrs.get(k, "")
            return el

        soup = BeautifulSoup(
            '<form id="f"><label><input type="radio" name="mission" value="atk">Angriff</label>'
            '<label><input type="radio" name="mission" value="trn">Transportieren</label></form>',
            "html.parser",
        )
        r_atk, r_trn = _mk_r("atk"), _mk_r("trn")
        matched = ActionExecutor._match_mission_radio(soup, "form#f", [r_atk, r_trn], "ATTACK")
        self.assertIs(matched, r_atk)

        soup_tr = BeautifulSoup(
            '<form id="f2"><table>'
            '<tr><td>Angreifen</td><td><input type="radio" name="m" value="1"></td><td>Hinweis: Tipp</td></tr>'
            '<tr><td>Spionieren</td><td><input type="radio" name="m" value="6"></td><td>Hinweis: Tipp</td></tr>'
            '</table></form>',
            "html.parser",
        )
        r_m1, r_m6 = _mk_r("1", "m"), _mk_r("6", "m")
        self.assertIs(ActionExecutor._match_mission_radio(soup_tr, "form#f2", [r_m1, r_m6], "SPY"), r_m6)
        self.assertIs(ActionExecutor._match_mission_radio(soup_tr, "form#f2", [r_m1, r_m6], "ATTACK"), r_m1)

        soup_p2 = BeautifulSoup(
            '<form id="f3">'
            '<label><input type="radio" name="m" value="1">Halten</label>'
            '<label><input type="radio" name="m" value="2">Angreifen</label>'
            '</form>',
            "html.parser",
        )
        h1, a2 = _mk_r("1", "m"), _mk_r("2", "m")
        self.assertIs(ActionExecutor._match_mission_radio(soup_p2, "form#f3", [h1, a2], "ATTACK"), a2)

        soup_hold_only = BeautifulSoup(
            '<form id="f4">'
            '<label><input type="radio" name="m" value="1">Halten</label>'
            '<label><input type="radio" name="m" value="6">Spionieren</label>'
            '</form>',
            "html.parser",
        )
        self.assertIsNone(
            ActionExecutor._match_mission_radio(
                soup_hold_only, "form#f4", [_mk_r("1", "m"), _mk_r("6", "m")], "ATTACK"
            )
        )

        for label_txt in ("Defend", "Verteidigen", "Escort", "Eskortieren", "Deploy", "Hold", "Expedition", "Explore"):
            soup_non_atk = BeautifulSoup(
                f'<form id="f_na"><label><input type="radio" name="mission" value="1">{label_txt}</label>'
                '<label><input type="radio" name="mission" value="6">Spionieren</label></form>',
                "html.parser",
            )
            self.assertIsNone(
                ActionExecutor._match_mission_radio(
                    soup_non_atk, "form#f_na", [_mk_r("1"), _mk_r("6")], "ATTACK"
                ),
                f"Pass 2 wrongly matched {label_txt!r} as ATTACK",
            )

        # 5. _execute_throttle_building stale/missing handling
        econ_html = (
            '<html><body><form id="ress" action="indexInternal.es?action=internalEconomy" method="POST">'
            '<select name="resource2"><option value="100" selected>100%</option><option value="0">0%</option></select>'
            '<input type="submit" value="Save"/></form></body></html>'
        )
        th_act = PlannedAction(
            priority="P1",
            kind="THROTTLE_BUILDING",
            planet_id=12476,
            planet_coords="02:380:12",
            target_id=2,
            target_name="Blast furnace 0%",
            target_level=0,
            reason="Save Pig iron",
            executable_now=True,
        )
        client_th_stale = MagicMock(spec=GameBrowserClient)
        client_th_stale.driver = MagicMock()
        client_th_stale.navigate_menu.return_value = econ_html
        client_th_stale.driver.find_element.side_effect = [
            StaleElementReferenceException("stale1"),
            StaleElementReferenceException("stale2"),
        ]
        res_th_stale = ex_u._execute_throttle_building(client_th_stale, prev_st, th_act)
        self.assertFalse(res_th_stale.executed)
        self.assertIn("became stale", res_th_stale.message.lower())

        client_th_miss = MagicMock(spec=GameBrowserClient)
        client_th_miss.driver = MagicMock()
        client_th_miss.navigate_menu.return_value = econ_html
        client_th_miss.driver.find_element.side_effect = [
            StaleElementReferenceException("stale1"),
            NoSuchElementException("missing"),
        ]
        res_th_miss = ex_u._execute_throttle_building(client_th_miss, prev_st, th_act)
        self.assertFalse(res_th_miss.executed)
        self.assertIn("not found", res_th_miss.message.lower())

        # 6. _click_dom_link_with_retry double-stale + _dispatch_fleet_mission Step 1/2 stale handling
        client_clk = MagicMock(spec=GameBrowserClient)
        client_clk.driver = MagicMock()
        client_clk.driver.find_element.side_effect = [
            StaleElementReferenceException("clk1"),
            StaleElementReferenceException("clk2"),
        ]
        client_clk._wait_frame_ready.return_value = "<html>ok</html>"
        bld_act = PlannedAction(
            priority="P2",
            kind="BUILD",
            planet_id=12476,
            planet_coords="02:380:12",
            target_id=1,
            target_name="Pig iron mine",
            target_level=2,
            reason="Upgrade",
            executable_now=True,
        )
        after_clk, err_clk = ex_u._click_dom_link_with_retry(
            client_clk, bld_act, "<html>ok</html>", "a.bau", lambda _h: True
        )
        self.assertIsNone(after_clk)
        assert err_clk is not None
        self.assertFalse(err_clk.executed)
        self.assertIn("became stale", err_clk.message.lower())

        fleet_step1_html = (
            '<html><body><form action="indexInternal.es?action=internalSendFleet" method="POST">'
            '<input type="text" name="ship_4" value="0"/>'
            '<input type="text" name="gal" value="2"/><input type="text" name="sun" value="380"/><input type="text" name="pla" value="4"/>'
            '<input type="submit" value="Continue"/></form></body></html>'
        )
        fleet_step2_html = (
            '<html><body><form action="indexInternal.es?action=internalSendFleet" method="POST">'
            '<label><input type="radio" name="mission" value="1"/>Attack</label>'
            '<input type="submit" value="Launch"/></form></body></html>'
        )
        atk_act = PlannedAction(
            priority="P2",
            kind="ATTACK",
            planet_id=12476,
            planet_coords="02:380:12",
            target_name="02:380:04 (Idle)",
            reason="Raid",
            executable_now=True,
        )
        st_flt = GameState(
            31,
            1,
            "u",
            100.0,
            (
                PlanetState(
                    planet_id=12476,
                    name="Home",
                    coords="02:380:12",
                    planet_type="normal",
                    temperature=31,
                    fields_used=10,
                    fields_max=200,
                    resources=Resources(),
                    storage=Resources(),
                    energy=EnergyBalance(),
                    ships={"s4": 10},
                ),
            ),
            active_planet_id=12476,
        )
        client_f1 = MagicMock(spec=GameBrowserClient)
        client_f1.driver = MagicMock()
        client_f1.navigate_menu.return_value = fleet_step1_html
        client_f1.driver.find_elements.side_effect = StaleElementReferenceException("step1 ship stale")
        res_f1_stale = ex_u._dispatch_fleet_mission(
            client_f1, st_flt, atk_act, ships_to_send={"s4": 5}, mission_kind="ATTACK"
        )
        self.assertFalse(res_f1_stale.executed)
        self.assertIn("became stale", res_f1_stale.message.lower())

        client_f2 = MagicMock(spec=GameBrowserClient)
        client_f2.driver = MagicMock()
        client_f2.navigate_menu.return_value = fleet_step1_html
        client_f2._wait_frame_ready.return_value = fleet_step2_html
        ship_inp = MagicMock()
        gal_inp, sun_inp, pla_inp = MagicMock(), MagicMock(), MagicMock()
        cont_btn = MagicMock()
        client_f2.driver.find_elements.side_effect = [
            [ship_inp],
            StaleElementReferenceException("step2 radio stale1"),
            StaleElementReferenceException("step2 radio stale2"),
        ]
        client_f2.driver.find_element.side_effect = [gal_inp, sun_inp, pla_inp, cont_btn]
        res_f2_stale = ex_u._dispatch_fleet_mission(
            client_f2, st_flt, atk_act, ships_to_send={"s4": 5}, mission_kind="ATTACK"
        )
        self.assertFalse(res_f2_stale.executed)
        self.assertIn("became stale", res_f2_stale.message.lower())

        # GAP-05: Pass 2 mission radio matcher must never select Harvest/Recycle/Sammeln value="1" as ATTACK
        harvest_soup = BeautifulSoup(
            '<form id="f"><input type="radio" id="m_harv" name="mission" value="1">'
            '<label for="m_harv">Harvest debris</label></form>',
            "html.parser",
        )
        harv_radio = MagicMock()
        harv_radio.get_attribute.side_effect = lambda attr: {
            "value": "1",
            "id": "m_harv",
            "name": "mission",
        }.get(attr, "")
        self.assertIsNone(ex_u._match_mission_radio(harvest_soup, "form#f", [harv_radio], "ATTACK"))

        # GAP-02: _select_planet_if_needed recovers cleanly on attempt 0 stale -> attempt 1 success,
        # and aborts cleanly without uncaught StaleElementReferenceException on persistent staleness.
        multi_html = (
            '<select name="planet">'
            '<option value="indexInternal.es?newplanet=101" selected>P1</option>'
            '<option value="indexInternal.es?newplanet=102">P2</option>'
            "</select>"
        )
        client_rec = MagicMock(spec=GameBrowserClient)
        client_rec.driver = MagicMock()
        opt1 = MagicMock()
        opt1.get_attribute.return_value = "indexInternal.es?newplanet=101"
        opt1.is_selected.return_value = True
        opt2_ok = MagicMock()
        opt2_ok.get_attribute.return_value = "indexInternal.es?newplanet=102"
        opt2_ok.is_selected.return_value = False
        sel_stale_once = MagicMock()
        sel_stale_once.find_elements.return_value = [opt1, opt2_ok]
        sel_ok = MagicMock()
        sel_ok.find_elements.return_value = [opt1, opt2_ok]
        switched_html = (
            '<select name="planet">'
            '<option value="indexInternal.es?newplanet=101">P1</option>'
            '<option value="indexInternal.es?newplanet=102" selected>P2</option>'
            "</select>"
        )
        client_rec.driver.find_element.side_effect = [
            StaleElementReferenceException("select disappeared attempt 0"),
            sel_ok,
        ]
        client_rec._wait_frame_ready.return_value = switched_html
        with patch("spaceinvasion.executor.Select") as mock_select_cls:
            mock_select_cls.return_value.select_by_value.side_effect = [None]
            switched, waited_html = ex_u._select_planet_if_needed(client_rec, 102, html=multi_html)
        self.assertTrue(switched)
        self.assertEqual(waited_html, switched_html)

        client_dbl_stale = MagicMock(spec=GameBrowserClient)
        client_dbl_stale.driver = MagicMock()
        client_dbl_stale.driver.find_element.side_effect = StaleElementReferenceException("select stays stale")
        switched_fail, waited_fail = ex_u._select_planet_if_needed(client_dbl_stale, 102, html=multi_html)
        self.assertFalse(switched_fail)
        self.assertIsNone(waited_fail)
        self.assertEqual(ex_u.last_observed_active_planet_id, 101)
        self.assertTrue(ex_u.last_observed_active_planet_confirmed)

    def test_select_planet_option_click_element_not_interactable_handled(self) -> None:
        from selenium.common.exceptions import ElementNotInteractableException, WebDriverException

        lim = PageViewLimiter(max_views_per_minute=60)
        ex = ActionExecutor(dry_run=False, max_actions_per_hour=100, page_limiter=lim)
        multi_html = (
            '<select name="planet">'
            '<option value="indexInternal.es?newplanet=101" selected>P1</option>'
            '<option value="indexInternal.es?newplanet=102">P2</option>'
            "</select>"
        )
        client = MagicMock(spec=GameBrowserClient)
        client.driver = MagicMock()
        opt1 = MagicMock()
        opt1.get_attribute.return_value = "indexInternal.es?newplanet=101"
        opt1.is_selected.return_value = True
        opt2 = MagicMock()
        opt2.get_attribute.return_value = "indexInternal.es?newplanet=102"
        opt2.is_selected.return_value = False
        opt2.click.side_effect = ElementNotInteractableException("hidden option")
        sel = MagicMock()
        sel.find_elements.return_value = [opt1, opt2]
        client.driver.find_element.return_value = sel
        with patch("spaceinvasion.executor.Select") as mock_select_cls:
            mock_select_cls.return_value.select_by_value.side_effect = ElementNotInteractableException("select hidden")
            switched, waited_html = ex._select_planet_if_needed(client, 102, html=multi_html)
        self.assertFalse(switched)
        self.assertIsNone(waited_html)

        state, action = _make_state_and_action()
        client_dom = MagicMock(spec=GameBrowserClient)
        client_dom.driver = MagicMock()
        client_dom.driver.find_elements.side_effect = ElementNotInteractableException("button hidden")
        client_dom.driver.find_element.side_effect = ElementNotInteractableException("input hidden")
        link_el, click_res = ex._click_dom_link_with_retry(
            client_dom,
            action,
            "<html></html>",
            "a[href*='buildingID=5']",
            lambda _h: True,
        )
        self.assertIsNone(link_el)
        self.assertIsNotNone(click_res)
        assert click_res is not None
        self.assertFalse(click_res.executed)

        ship_act = PlannedAction(
            priority="P2",
            kind="BUILD_SHIP",
            planet_id=12476,
            planet_coords="02:380:12",
            target_id=4,
            target_level=2,
            target_name="Falcon",
            reason="Test",
            executable_now=True,
        )
        res_ship = ex._execute_build_ship(client_dom, state, ship_act)
        self.assertFalse(res_ship.executed)

        throttle_act = PlannedAction(
            priority="P1",
            kind="THROTTLE_BUILDING",
            planet_id=12476,
            planet_coords="02:380:12",
            target_id=2,
            target_level=0,
            target_name="Blast furnace",
            reason="Test",
            executable_now=True,
        )
        client_dom.navigate_menu.return_value = "<html></html>"
        res_thr = ex._execute_throttle_building(client_dom, state, throttle_act)
        self.assertFalse(res_thr.executed)

        fleet_act = PlannedAction(
            priority="P2",
            kind="FLEETSAVE",
            planet_id=12476,
            planet_coords="02:380:12",
            target_name="02:380:13",
            reason="Test",
            executable_now=True,
        )
        client_dom.navigate_menu.return_value = (
            '<form name="fleet" action="indexInternal.es?action=internalSendFleet">'
            '<input name="ship_1" value="0"/><input type="submit"/></form>'
        )
        client_dom.driver.find_elements.side_effect = WebDriverException("fleet input hidden")
        client_dom.driver.find_element.side_effect = WebDriverException("fleet input hidden")
        res_flt = ex._dispatch_fleet_mission(
            client_dom,
            state,
            fleet_act,
            ships_to_send={"s1": 1},
            mission_kind="transport",
        )
        self.assertFalse(res_flt.executed)

    def test_executor_planet_id_5000_digits_and_corrupt_action_fields_safe(self) -> None:
        huge = "9" * 5000
        html_huge = (
            f'<select name="planet">'
            f'<option selected="selected" value="indexInternal.es?newplanet={huge}">Huge</option>'
            f"</select>"
        )
        self.assertIsNone(ActionExecutor._extract_selected_planet_id(html_huge, "https://uni/indexInternal.es"))
        html_ok = (
            '<select name="planet">'
            '<option selected="selected" value="indexInternal.es?newplanet=12476">Ok</option>'
            "</select>"
        )
        self.assertEqual(ActionExecutor._extract_selected_planet_id(html_ok, "https://uni/indexInternal.es"), 12476)

        state, _ = _make_state_and_action()
        ex = ActionExecutor(dry_run=True)
        client_dom = MagicMock()
        client_dom.navigate_menu.return_value = "<html></html>"
        client_dom.driver = MagicMock()

        bad_act = PlannedAction(
            priority="P2",
            kind="BUILD_SHIP",
            planet_id=12476,
            planet_coords="02:380:12",
            target_id=10**5000,  # type: ignore[arg-type]
            target_level=10**5000,
            target_name="9" * 5000,
            reason="Test",
            executable_now=True,
        )
        res_bad_ship = ex._execute_build_ship(client_dom, state, bad_act)
        self.assertFalse(res_bad_ship.executed)

        res_bad_fleet = ex._dispatch_fleet_mission(
            client_dom,
            state,
            bad_act,
            ships_to_send={"s1": 10**5000, "s2": "bad"},  # type: ignore[dict-item]
            mission_kind="transport",
        )
        self.assertFalse(res_bad_fleet.executed)

    def test_adv_executor_10e5000_planet_switch_and_build_ship_and_actions(self) -> None:
        from spaceinvasion import game_data
        from spaceinvasion.state import UnitOffer

        state, _ = _make_state_and_action()
        client = MagicMock(spec=GameBrowserClient)
        client.driver = MagicMock()
        ex_dry = ActionExecutor(dry_run=True)
        ex_live = ActionExecutor(dry_run=False, action_delay_range=(0.0, 0.0))

        # _find_newplanet_switch_target & _select_planet_if_needed with 10**5000
        sel_html = (
            '<select name="planet">'
            '<option value="indexInternal.es?newplanet=12476" selected>Home</option>'
            '<option value="indexInternal.es?newplanet=99999">Colony</option>'
            "</select>"
        )
        self.assertEqual(ActionExecutor._find_newplanet_switch_target(sel_html, 10**5000), (False, None))
        self.assertEqual(ex_live._select_planet_if_needed(client, 10**5000, html=sel_html), (False, None))
        self.assertEqual(ex_live._select_planet_if_needed(client, 10**5000, html=""), (False, None))

        # _execute_build_ship with target_level=10**5000 sends capped int string cleanly
        wf_html = (
            '<form action="indexInternal.es?action=internalBuildItem&buildShip=1" method="POST">'
            '<table><tr><td>'
            '<a href="indexInternal.es?action=internalDetail&type=ship&id=1">Small transport</a>'
            'Required: 3.000 Metal 3.000 Kryptonite '
            '<input type="text" name="ship[1]" value="0"/>'
            "</td></tr></table></form>"
        )
        qty_inp = MagicMock()
        submit_btn = MagicMock()
        form_mock = MagicMock()
        form_mock.find_element.return_value = submit_btn
        client.driver.find_element.side_effect = [qty_inp, form_mock]
        client.navigate_menu.return_value = wf_html
        client._wait_frame_ready.return_value = wf_html
        client.read_planets_overview.return_value = state

        huge_ship = PlannedAction(
            priority="P2",
            kind="BUILD_SHIP",
            planet_id=12476,
            planet_coords="02:380:12",
            target_id=1,
            target_level=10**5000,
            target_name="Small transport",
            reason="Adv huge qty",
            executable_now=True,
        )
        ship_res = ex_live._execute_build_ship(client, state, huge_ship)
        self.assertTrue(ship_res.executed)
        qty_inp.send_keys.assert_called_once_with(str(game_data.MAX_JSON_SAFE_INT))
        self.assertIn(str(game_data.MAX_JSON_SAFE_INT), ship_res.message)

        # Dry-run and direct _execute_build / _execute_research with 10**5000 fields
        for kind in ("BUILD", "RESEARCH", "BUILD_SHIP", "THROTTLE_BUILDING", "SPY"):
            act_big = PlannedAction(
                priority="P2",
                kind=kind,
                planet_id=10**5000,
                planet_coords="02:380:12",
                target_id=10**5000,
                target_level=10**5000,
                target_name="Unit",
                reason="Test",
                executable_now=True,
            )
            dry_res = ex_dry.execute_action(client, state, act_big)
            self.assertTrue(dry_res.dry_run)
            self.assertIn("[DRY-RUN]", dry_res.message)

        client.driver.find_element.side_effect = NoSuchElementException("missing")
        client.navigate_menu.return_value = "<html></html>"
        for fn, kind in ((ex_live._execute_build, "BUILD"), (ex_live._execute_research, "RESEARCH")):
            act_k = PlannedAction("P2", kind, 10**5000, "02:380:12", "T", "R", target_id=10**5000, target_level=10**5000, executable_now=True)
            res_k = fn(client, state, act_k)
            self.assertFalse(res_k.executed)

    def test_r7_throttle_building_empty_html_10e5000_planet_id_and_compact_new_lvl(self) -> None:
        state, _ = _make_state_and_action()
        client = MagicMock(spec=GameBrowserClient)
        client.driver = MagicMock()
        ex_live = ActionExecutor(dry_run=False, action_delay_range=(0.0, 0.0))

        client.navigate_menu.return_value = ""
        act_throttle = PlannedAction(
            priority="P1",
            kind="THROTTLE_BUILDING",
            planet_id=10**5000,
            planet_coords="02:380:12",
            target_id=2,
            target_level=0,
            target_name="Blast furnace",
            reason="Starving",
            executable_now=True,
        )
        res_empty = ex_live.execute_action(client, state, act_throttle)
        self.assertFalse(res_empty.executed)
        self.assertIn("~1e5000", res_empty.message)

    def test_r8_executor_sanitizes_huge_and_nonfinite_max_limiter_wait(self) -> None:
        import math

        self.assertIsNone(ActionExecutor(max_limiter_wait=None).max_limiter_wait)
        self.assertIsNone(ActionExecutor(max_limiter_wait=True).max_limiter_wait)  # type: ignore[arg-type]
        self.assertEqual(ActionExecutor(max_limiter_wait=8.0).max_limiter_wait, 8.0)

        for bad_wait in (10**400, float("nan"), float("inf"), float("-inf"), -5.0):
            ex = ActionExecutor(max_limiter_wait=bad_wait)
            self.assertIsNotNone(ex.max_limiter_wait)
            assert ex.max_limiter_wait is not None
            self.assertTrue(math.isfinite(ex.max_limiter_wait))
            self.assertGreaterEqual(ex.max_limiter_wait, 0.0)
            self.assertLessEqual(ex.max_limiter_wait, 3600.0)

    def test_r9_scan_galaxy_and_fleet_dispatch_reject_arabic_indic_coords(self) -> None:
        state, _ = _make_state_and_action()
        client = MagicMock(spec=GameBrowserClient)
        client.driver = MagicMock()
        client.open_galaxy.return_value = "<html></html>"
        client.last_galaxy_coords = (1, 1)

        ex_live = ActionExecutor(dry_run=False, action_delay_range=(0.0, 0.0))
        scan_act = PlannedAction(
            priority="P2",
            kind="SCAN_GALAXY",
            planet_id=12476,
            planet_coords="\u0660\u0661:\u0660\u0660\u0661:\u0660\u0661",
            target_name="Galaxy [\u0660\u0662:\u0663\u0668\u0660]",
            reason="Arabic coords scan test",
            executable_now=True,
        )
        scan_res = ex_live._execute_scan_galaxy(client, state, scan_act)
        self.assertTrue(scan_res.executed)
        client.open_galaxy.assert_called_once_with(galaxy=1, system=1)

        atk_act = PlannedAction(
            priority="P2",
            kind="ATTACK",
            planet_id=12476,
            planet_coords="02:380:12",
            target_name="\u0660\u0662:\u0663\u0668\u0660:\u0660\u0664",
            reason="Arabic coords attack test",
            executable_now=True,
        )
        atk_res = ex_live._dispatch_fleet_mission(
            client,
            state,
            atk_act,
            ships_to_send={"s4": 5},
            mission_kind="ATTACK",
        )
        self.assertFalse(atk_res.executed)
        self.assertIn("could not parse target coordinates", atk_res.message.lower())
        client.navigate_menu.assert_not_called()

    def test_r10_none_fields_logging_bytes_redaction_and_hourly_cap_zero(self) -> None:
        import json
        import tempfile
        from pathlib import Path
        from unittest.mock import MagicMock
        from spaceinvasion.logging_utils import append_api_log, append_tick_logs, redact_sensitive
        from spaceinvasion.state import EnergyBalance, GameState, PlanetState, Resources
        from spaceinvasion.strategy import PlannedAction

        ex_zero = ActionExecutor(dry_run=False, max_actions_per_hour=0)
        self.assertEqual(ex_zero.max_actions_per_hour, 0)
        self.assertEqual(ex_zero.remaining_hourly_budget(), 0)
        ex_huge = ActionExecutor(max_actions_per_hour=10**5000)
        self.assertLessEqual(ex_huge.max_actions_per_hour, 1_000_000)

        p = PlanetState(
            planet_id=1,
            name="Home",
            coords="01:001:01",
            planet_type="normal",
            temperature=20,
            fields_used=10,
            fields_max=200,
            resources=Resources(100, 100, 100, 100),
            storage=Resources(1000, 1000, 1000, 1000),
            energy=EnergyBalance(50, 10),
        )
        st = GameState(31, 1, "u", 1000.0, (p,), active_planet_id=1)

        none_act = PlannedAction(
            priority="P2",
            kind="BUILD",
            planet_id=1,
            planet_coords=None,  # type: ignore[arg-type]
            target_id=1,
            target_name=None,  # type: ignore[arg-type]
            target_level="bad",  # type: ignore[arg-type]
            reason=None,  # type: ignore[arg-type]
            executable_now=True,
        )
        dry_res = ActionExecutor(dry_run=True).execute_action(MagicMock(), st, none_act)
        self.assertFalse(dry_res.executed)
        self.assertTrue(dry_res.dry_run)

        blk_res = ex_zero.execute_action(MagicMock(), st, none_act)
        self.assertFalse(blk_res.executed)
        self.assertIn("hourly action rate cap reached", blk_res.message.lower())

        client = MagicMock()
        client.open_galaxy.return_value = "<html></html>"
        client.last_galaxy_coords = (1, 1)
        scan_none = PlannedAction(
            priority="P2",
            kind="SCAN_GALAXY",
            planet_id=1,
            planet_coords=None,  # type: ignore[arg-type]
            target_name=None,  # type: ignore[arg-type]
            reason=None,  # type: ignore[arg-type]
            executable_now=True,
        )
        scan_res = ActionExecutor(dry_run=False, action_delay_range=(0.0, 0.0))._execute_scan_galaxy(client, st, scan_none)
        self.assertTrue(scan_res.executed)

        atk_none = PlannedAction(
            priority="P2",
            kind="ATTACK",
            planet_id=1,
            planet_coords=None,  # type: ignore[arg-type]
            target_name=None,  # type: ignore[arg-type]
            reason=None,  # type: ignore[arg-type]
            executable_now=True,
        )
        disp_res = ActionExecutor(dry_run=False, action_delay_range=(0.0, 0.0))._dispatch_fleet_mission(
            client, st, atk_none, ships_to_send={"s4": 1}, mission_kind="ATTACK"
        )
        self.assertFalse(disp_res.executed)
        self.assertIn("could not parse target coordinates", disp_res.message.lower())

        # Bytes / secret dict keys / frozenset / Path double-redaction
        red = redact_sensitive(
            {
                "raw": b"https://x/?sid=RAW_BYTES_SECRET",
                "password": b"PW_SECRET",
                "api_key": "API_SECRET",
                "fset": frozenset({b"?sid=FSET_SECRET"}),
            }
        )
        self.assertNotIn("RAW_BYTES_SECRET", json.dumps(red, allow_nan=False))
        self.assertNotIn("PW_SECRET", json.dumps(red, allow_nan=False))
        self.assertNotIn("API_SECRET", json.dumps(red, allow_nan=False))
        self.assertNotIn("FSET_SECRET", json.dumps(red, allow_nan=False))

        with tempfile.TemporaryDirectory() as tmp:
            tp = Path(tmp)
            jl, tl = append_tick_logs(
                tp,
                source="r10",
                tick=1,
                mode="DRY-RUN",
                state=st,
                actual_action=none_act,
                actual_result={"password": "PW_IN_RES", "path": Path("https://x/?sid=PATH_SECRET")},
                next_action=None,
            )
            ajl, atl = append_api_log(
                tp,
                method=b"POST?sid=M_BYTES",  # type: ignore[arg-type]
                path="/command",
                status=200,
                payload={"password": "API_PW_SECRET"},
                response={"error": b"boom ?sid=RESP_BYTES"},
            )
            for p_log in (jl, tl, ajl, atl):
                txt = p_log.read_text(encoding="utf-8")
                for sec in ("PW_IN_RES", "PATH_SECRET", "M_BYTES", "API_PW_SECRET", "RESP_BYTES"):
                    self.assertNotIn(sec, txt)

    def test_r12_executor_safe_str_resists_broken_fspath_and_raise_str(self) -> None:
        from spaceinvasion import executor as ex_mod

        class _RaiseFspath:
            def __fspath__(self) -> str:
                raise RuntimeError("fspath boom")

        class _IntPath:
            def __fspath__(self) -> int:
                return 10**5000

        class _BytesPath:
            def __fspath__(self) -> bytes:
                return b"bytes_path_ok"

        class _RaiseStrRepr:
            def __str__(self) -> str:
                raise RuntimeError("str boom")

            def __repr__(self) -> str:
                raise RuntimeError("repr boom")

        for val in (_RaiseFspath(), _IntPath(), _BytesPath(), _RaiseStrRepr(), RuntimeError(10**5000, "err")):
            self.assertIsInstance(ex_mod._safe_str(val), str)

        _, st = _make_state_and_action()
        bad_act = PlannedAction(
            priority="P2",
            kind="BUILD",
            planet_id=12476,
            planet_coords=_RaiseFspath(),  # type: ignore[arg-type]
            target_id=1,
            target_name=_RaiseStrRepr(),  # type: ignore[arg-type]
            target_level=1,
            reason=_IntPath(),  # type: ignore[arg-type]
            executable_now=True,
            action_href=_BytesPath(),  # type: ignore[arg-type]
        )
        res = ActionExecutor(dry_run=True).execute_action(MagicMock(), st, bad_act)
        self.assertFalse(res.executed)
        self.assertTrue(res.dry_run)

    def test_r13_executor_safe_str_handles_deep_cyclic_exceptions(self) -> None:
        import time
        from spaceinvasion import executor as ex_mod

        class _SelfPath:
            def __fspath__(self) -> "_SelfPath":
                return self

        self.assertEqual(ex_mod._safe_str(_SelfPath()), "<_SelfPath>")

        cyc = RuntimeError("x")
        cyc.args = (10**5000, cyc)
        cyc_str = ex_mod._safe_str(cyc)
        self.assertIsInstance(cyc_str, str)
        self.assertIn("RuntimeError", cyc_str)

        deep: BaseException = RuntimeError("leaf")
        for _ in range(25):
            deep = RuntimeError(10**5000, deep)
        deep_str = ex_mod._safe_str(deep)
        self.assertIsInstance(deep_str, str)
        self.assertIn("RuntimeError", deep_str)

        dag: BaseException = RuntimeError("dag_leaf")
        for _ in range(18):
            dag = RuntimeError(10**5000, dag, dag, dag, dag, dag, dag, dag)
        t0 = time.perf_counter()
        dag_str = ex_mod._safe_str(dag)
        self.assertLess((time.perf_counter() - t0) * 1000.0, 200.0)
        self.assertIsInstance(dag_str, str)

        _, st = _make_state_and_action()
        bad_act = PlannedAction(
            priority="P2",
            kind="BUILD",
            planet_id=12476,
            planet_coords="02:380:12",
            target_id=1,
            target_name=cyc,  # type: ignore[arg-type]
            target_level=1,
            reason=deep,  # type: ignore[arg-type]
            executable_now=True,
        )
        res = ActionExecutor(dry_run=True).execute_action(MagicMock(), st, bad_act)
        self.assertFalse(res.executed)
        self.assertTrue(res.dry_run)

    def test_r14_executor_safe_str_dag20_and_throttle_blocks_asteroid_and_raid(self) -> None:
        import time
        from spaceinvasion import executor as ex_mod
        from spaceinvasion.state import EnergyBalance, GameState, PlanetState, Resources
        from spaceinvasion.strategy import PlannedAction

        dag: BaseException = RuntimeError("leaf")
        for _ in range(20):
            dag = RuntimeError(10**5000, dag, dag, dag, dag)
        t0 = time.perf_counter()
        self.assertIsInstance(ex_mod._safe_str(dag), str)
        self.assertLess((time.perf_counter() - t0) * 1000.0, 50.0)

        ex = ex_mod.ActionExecutor(dry_run=False)
        for ptype in ("asteroid", "raid"):
            p = PlanetState(
                planet_id=910,
                name="Special",
                coords="02:380:09",
                planet_type=ptype,
                temperature=10,
                fields_used=1,
                fields_max=50,
                resources=Resources(1000, 1000, 1000, 1000),
                storage=Resources(10_000, 10_000, 10_000, 10_000),
                energy=EnergyBalance(50, 10),
                buildings={"b1": 1},
            )
            st = GameState(31, 1, "u", 1000.0, (p,), active_planet_id=910)
            act = PlannedAction(
                priority="P2",
                kind="THROTTLE_BUILDING",
                planet_id=910,
                planet_coords="02:380:09",
                target_id=1,
                target_name="Pig iron mine",
                target_level=50,
                reason="test throttle guard",
                executable_now=True,
            )
            client = MagicMock()
            res = ex._execute_throttle_building(client, st, act)
            self.assertFalse(res.executed)
            self.assertEqual(client.method_calls, [])

    def test_real_three_form_economy_calculate_save_resource6_and_persist_check(self) -> None:
        base_state, _ = _make_state_and_action()
        real_econ_before = """
        <form name="ress" id="ress" style="display:none;">
          <input type="hidden" id="roheisen" value="29.13" />
          <input type="hidden" id="metall" value="0" />
          <input type="submit" value="WrongHiddenSubmit" />
        </form>
        <form name="fselect">
          <select name="planet"><option selected value="indexInternal.es?action=internalEconomy&newPlanet=12476">Colony</option></select>
        </form>
        <form name="economy" action="" method="POST">
          <select name="resource1"><option value="100" selected>100%</option></select>
          <select name="resource2"><option value="100">100%</option><option value="0" selected>0%</option></select>
          <select name="resource5"><option value="100" selected>100%</option></select>
          <select name="resource6"><option value="100" selected>100%</option><option value="0">0%</option></select>
          <input type="hidden" name="economy" value="1" />
          <input type="submit" value="Calculate" class="planet" />
        </form>
        """
        real_econ_after = real_econ_before.replace(
            '<select name="resource2"><option value="100">100%</option><option value="0" selected>0%</option></select>',
            '<select name="resource2"><option value="100" selected>100%</option><option value="0">0%</option></select>',
        )
        sel_mock = MagicMock()
        calc_btn = MagicMock()
        driver = MagicMock()
        driver.find_element.side_effect = [sel_mock, calc_btn]
        client = MagicMock(spec=GameBrowserClient)
        client.driver = driver
        client.navigate_menu.return_value = real_econ_before
        client._wait_frame_ready.return_value = real_econ_after
        client.read_planets_overview.return_value = base_state

        resume_bf = PlannedAction(
            priority="P1",
            kind="THROTTLE_BUILDING",
            planet_id=12476,
            planet_coords="02:380:12",
            target_id=2,
            target_name="Blast furnace 100%",
            target_level=100,
            reason="Resume metal smelting",
            executable_now=True,
        )
        ex = ActionExecutor(dry_run=False, action_delay_range=(0.0, 0.0))
        with patch("spaceinvasion.executor.Select") as mock_sel:
            res = ex.execute_action(client, base_state, resume_bf)
        self.assertTrue(res.executed)
        mock_sel.return_value.select_by_value.assert_called_once_with("100")
        self.assertEqual(
            driver.find_element.call_args_list[1].args[1],
            'form[name="economy"] input[type="submit"][value="Calculate"]',
        )
        calc_btn.click.assert_called_once()
        self.assertEqual(ex.last_economy_rates[12476]["resource2"], 100)

        # Unsaved Calculate submit is detected and reported as executed=False
        driver_fail = MagicMock()
        driver_fail.find_element.side_effect = [MagicMock(), MagicMock()]
        client_fail = MagicMock(spec=GameBrowserClient)
        client_fail.driver = driver_fail
        client_fail.navigate_menu.return_value = real_econ_before
        client_fail._wait_frame_ready.return_value = real_econ_before  # still 0%!
        with patch("spaceinvasion.executor.Select"):
            res_fail = ex.execute_action(client_fail, base_state, resume_bf)
        self.assertFalse(res_fail.executed)
        self.assertIn("did not persist", res_fail.message)

        # Thermosolar (b6) uses resource6 when present in 6-select live DOM
        thermo_after = real_econ_before.replace(
            '<select name="resource6"><option value="100" selected>100%</option><option value="0">0%</option></select>',
            '<select name="resource6"><option value="100">100%</option><option value="0" selected>0%</option></select>',
        )
        driver_b6 = MagicMock()
        driver_b6.find_element.side_effect = [MagicMock(), MagicMock()]
        client_b6 = MagicMock(spec=GameBrowserClient)
        client_b6.driver = driver_b6
        client_b6.navigate_menu.return_value = real_econ_before
        client_b6._wait_frame_ready.return_value = thermo_after
        client_b6.read_planets_overview.return_value = base_state
        pause_b6 = PlannedAction(
            priority="P1",
            kind="THROTTLE_BUILDING",
            planet_id=12476,
            planet_coords="02:380:12",
            target_id=6,
            target_name="Thermosolar plant 0%",
            target_level=0,
            reason="Pause thermosolar",
            executable_now=True,
        )
        with patch("spaceinvasion.executor.Select"):
            res_b6 = ex.execute_action(client_b6, base_state, pause_b6)
        self.assertTrue(res_b6.executed)
        self.assertEqual(driver_b6.find_element.call_args_list[0].args[1], "resource6")
        self.assertEqual(ex.last_economy_rates[12476]["resource6"], 0)

    def test_live_internal_send_fleet_finish_gobutton_maxlength_and_html_sid_redaction(self) -> None:
        from spaceinvasion.logging_utils import redact_sensitive

        raw_html = (
            '<form id="ress"><input name="sid" type="hidden" value="181659802557cf6e707aa6177d2b66a6"/>'
            '<input type="hidden" value="abcdef0123456789" name="sid"/></form>'
        )
        redacted = redact_sensitive(raw_html)
        self.assertNotIn("181659802557cf6e707aa6177d2b66a6", redacted)
        self.assertNotIn("abcdef0123456789", redacted)
        self.assertEqual(redacted.count('value="REDACTED"'), 2)

        step1_html = """
        <form name="fleet" action="indexInternal.es?action=internalSendFleet&sid=s1" method="POST">
          <input type="text" name="ship_1" maxlength="1" value="0"/>
          <input type="text" name="ship_4" maxlength="2" value="0"/>
          <input type="text" name="gal" value="2"/>
          <input type="text" name="sun" value="380"/>
          <input type="text" name="pla" value="12"/>
          <input type="submit" value="continue"/>
        </form>
        """
        step2_html = """
        <form action="indexInternal.es?action=internalSendFleet&sid=s1" method="POST" name="thissetup">
          <input name="ship_4" type="hidden" value="12"/>
        </form>
        <form action="indexInternal.es?action=internalSendFleetFinish&sid=s1" id="fleet" method="POST" name="fleet">
          <div><span>Transport (forbidden): the target is inactive</span></div>
          <label><input name="todo" onclick="javascript:goFleet(this.value)" type="radio" value="SAVE"/> Escape flight</label>
          <label><input name="todo" onclick="javascript:goFleet(this.value)" type="radio" value="ATTACK_a8a41403ec"/> Attack</label>
          <input class="planet" onclick="reloadFormData(1);" type="button" value="longer distance"/>
          <input class="fleet-start-button ecoshadow" id="goButton" onclick="startFleet()" type="button" value="LAUNCH"/>
        </form>
        """
        soup2 = BeautifulSoup(step2_html, "html.parser")
        self.assertEqual(ActionExecutor._pick_send_fleet_form_css(soup2), 'form[action*="internalSendFleetFinish"]')

        class _MaxLengthInput:
            def __init__(self, maxlen: int) -> None:
                self.maxlen = maxlen
                self.value = "0"

            def clear(self) -> None:
                # Simulate real SpaceInvasion onblur="if(this.value=='') this.value='0';"
                self.value = "0"

            def send_keys(self, txt: str) -> None:
                self.value = (self.value + str(txt))[: self.maxlen]

            def get_attribute(self, name: str) -> str:
                return self.value if name == "value" else ""

        s1_inp = _MaxLengthInput(1)
        s4_inp = _MaxLengthInput(2)
        gal_inp = _MaxLengthInput(2)
        sun_inp = _MaxLengthInput(3)
        pla_inp = _MaxLengthInput(2)

        save_radio = MagicMock()
        save_radio.get_attribute.side_effect = lambda k: {"value": "SAVE", "name": "todo", "id": ""}.get(k, "")
        save_radio.is_selected.return_value = False
        atk_radio = MagicMock()
        atk_radio.get_attribute.side_effect = lambda k: {"value": "ATTACK_a8a41403ec", "name": "todo", "id": ""}.get(k, "")
        atk_radio.is_selected.return_value = False
        cont_btn = MagicMock()
        go_btn = MagicMock()

        driver = MagicMock()
        driver.execute_script.side_effect = lambda script, el=None, val=None: setattr(el, "value", val) if (el is not None and val is not None) else "tok"
        driver.find_elements.side_effect = lambda by, sel: (
            [s4_inp] if sel == "ship_4" else ([s1_inp] if sel == "ship_1" else ([save_radio, atk_radio] if "radio" in str(sel) else []))
        )

        def _find_el(by: str, sel: str):
            if sel == "gal":
                return gal_inp
            if sel == "sun":
                return sun_inp
            if sel == "pla":
                return pla_inp
            if "internalSendFleetFinish" in sel and "goButton" in sel:
                return go_btn
            return cont_btn

        driver.find_element.side_effect = _find_el

        planet = PlanetState(
            planet_id=12476,
            name="Colony",
            coords="02:380:12",
            planet_type="normal",
            temperature=31,
            fields_used=100,
            fields_max=230,
            resources=Resources(100_000, 17_000, 200_000, 45_000),
            storage=Resources(200_000, 100_000, 800_000, 200_000),
            energy=EnergyBalance(3075, 2532),
            buildings={"b9": 2},
            ships={"s1": 6, "s4": 12},
        )
        st = GameState(31, 103603, "u", 1000.0, (planet,), active_planet_id=12476)
        atk_act = PlannedAction(
            priority="P2",
            kind="ATTACK",
            planet_id=12476,
            planet_coords="02:380:12",
            target_name="02:383:02 (Spacia)",
            reason="Tutorial fight",
            executable_now=True,
        )
        client = MagicMock(spec=GameBrowserClient)
        client.driver = driver
        client.navigate_menu.return_value = step1_html
        client._wait_frame_ready.side_effect = [step2_html, "<html><body>Fleet dispatched</body></html>"]
        client.read_planets_overview.return_value = st

        ex = ActionExecutor(dry_run=False, allow_raids=True, action_delay_range=(0.0, 0.0))
        res = ex.execute_action(client, st, atk_act)
        self.assertTrue(res.executed, msg=res.message)
        self.assertEqual(s1_inp.value, "6")
        self.assertEqual(s4_inp.value, "12")
        atk_radio.click.assert_called_once()
        go_btn.click.assert_called_once()

        # Step 1 redirect with fleetErr.noFleet aborts cleanly without hanging
        client_err = MagicMock(spec=GameBrowserClient)
        client_err.driver = driver
        client_err.navigate_menu.return_value = step1_html
        client._wait_frame_ready.side_effect = None
        client_err._wait_frame_ready.return_value = '<a href="indexInternal.es?action=internalFleet&err=fleetErr.noFleet">err</a>'
        res_err = ex.execute_action(client_err, st, atk_act)
        self.assertFalse(res_err.executed)
        self.assertIn("fleetErr.noFleet", res_err.message)

    def test_claim_tutorial_fallback_selector_and_build_ship_onblur_js_recovery(self) -> None:
        from dataclasses import replace

        planet = PlanetState(
            planet_id=12476,
            name="Colony",
            coords="02:380:12",
            planet_type="normal",
            temperature=31,
            fields_used=100,
            fields_max=230,
            resources=Resources(100_000, 30_000, 200_000, 45_000),
            storage=Resources(500_000, 500_000, 800_000, 500_000),
            energy=EnergyBalance(3075, 2532),
            buildings={"b9": 3},
        )
        st = GameState(31, 103603, "u", 1000.0, (planet,), active_planet_id=12476)

        # 1. CLAIM_TUTORIAL succeeds when form action="" and submit is a nested button
        claim_html = (
            '<html><body><div>Tutorial 6 / 11</div>'
            '<form action="" method="POST">'
            '<input type="hidden" name="tutorialComplete" value="1"/>'
            '<div><button type="submit">Mission complete</button></div>'
            '</form></body></html>'
        )
        submit_btn = MagicMock()
        driver_claim = MagicMock()
        driver_claim.find_element.return_value = submit_btn
        client_claim = MagicMock(spec=GameBrowserClient)
        client_claim.driver = driver_claim
        client_claim.navigate_menu.return_value = claim_html
        client_claim._wait_frame_ready.return_value = '<html><body><div>Tutorial 7 / 11</div></body></html>'
        client_claim.read_planets_overview.return_value = st

        ex = ActionExecutor(dry_run=False, action_delay_range=(0.0, 0.0))
        claim_act = PlannedAction(
            priority="P2",
            kind="CLAIM_TUTORIAL",
            planet_id=12476,
            planet_coords="02:380:12",
            target_name="Tutorial 6/11 Reward",
            reason="Claim reward",
            executable_now=True,
        )
        res_claim = ex.execute_action(client_claim, st, claim_act)
        self.assertTrue(res_claim.executed, msg=res_claim.message)
        submit_btn.click.assert_called_once()

        # 2. BUILD_SHIP recovers via JS value assignment when send_keys leaves value="0" (onblur/maxlength)
        wf_html = (
            '<html><body><form action="indexInternal.es?action=internalBuildItem&buildShip=1" method="POST">'
            '<table><tr><td><a href="indexInternal.es?action=internalDetail&type=ship&id=10">Spy probe</a> '
            'Available: 0 Required: 1.000 Kryptonite '
            '<input type="text" name="ship_10" value="0" maxlength="2"/></td></tr></table>'
            '<input type="submit" value="Build"/></form></body></html>'
        )
        qty_el = MagicMock()
        qty_el.get_attribute.return_value = "0"  # Simulates onblur reset after clear/send_keys
        wf_form = MagicMock()
        wf_submit = MagicMock()
        wf_form.find_element.return_value = wf_submit
        driver_wf = MagicMock()
        driver_wf.find_element.side_effect = [qty_el, wf_form]
        client_wf = MagicMock(spec=GameBrowserClient)
        client_wf.driver = driver_wf
        client_wf.navigate_menu.return_value = wf_html
        client_wf._wait_frame_ready.return_value = wf_html
        client_wf.read_planets_overview.return_value = st

        ship_act = PlannedAction(
            priority="P2",
            kind="BUILD_SHIP",
            planet_id=12476,
            planet_coords="02:380:12",
            target_id=10,
            target_level=2,
            target_name="Spy probe",
            reason="Tutorial 7/11: Build Spy probes 0 / 2",
            executable_now=True,
        )
        res_ship = ex.execute_action(client_wf, st, ship_act)
        self.assertTrue(res_ship.executed, msg=res_ship.message)
        self.assertTrue(
            any("arguments[0].value = arguments[1]" in str(c.args[0]) and c.args[2] == "2" for c in driver_wf.execute_script.call_args_list)
        )
        wf_submit.click.assert_called_once()

        # 3. ESCAPE_FLIGHT live Step 2 DOM (value="SAVE" + #goButton LAUNCH):
        #    - Tutorial reason sends 1x s1 (s1 preferred)
        #    - P0 survival reason sends all stationed non-s9 ships
        step1_esc_html = (
            '<html><body><form name="fleet" action="indexInternal.es?action=internalSendFleet" method="POST">'
            '<input type="text" name="ship_1" value="0"/>'
            '<input type="text" name="ship_4" value="0"/>'
            '<input type="text" name="gal" value="2"/>'
            '<input type="text" name="sun" value="380"/>'
            '<input type="text" name="pla" value="12"/>'
            '<input type="submit" value="Continue"/></form></body></html>'
        )
        step2_esc_html = (
            '<html><body><form id="sendForm" action="indexInternal.es?action=internalSendFleet&send=1" method="POST">'
            '<label><input name="todo" onclick="javascript:goFleet(this.value)" type="radio" value="TRANSPORT"/> Transport</label>'
            '<label><input name="todo" onclick="javascript:goFleet(this.value)" type="radio" value="SAVE"/> Escape flight</label>'
            '<input id="goButton" class="fleet-start-button" onclick="startFleet()" type="button" value="LAUNCH"/>'
            '</form></body></html>'
        )

        def _mk_inp() -> MagicMock:
            el = MagicMock()
            el.value = ""

            def _sk(v: str) -> None:
                el.value = v

            el.send_keys.side_effect = _sk
            el.get_attribute.side_effect = lambda k: el.value if k == "value" else ""
            return el

        for is_tut, exp_s1, exp_s4_called in ((True, "1", False), (False, "6", True)):
            s1_el, s4_el = _mk_inp(), _mk_inp()
            gal_el, sun_el, pla_el = _mk_inp(), _mk_inp(), _mk_inp()
            cont_el, launch_el = MagicMock(), MagicMock()
            r_trn = MagicMock()
            r_trn.get_attribute.side_effect = lambda k: {"name": "todo", "value": "TRANSPORT", "id": ""}.get(k, "")
            r_trn.is_selected.return_value = False
            r_save = MagicMock()
            r_save.get_attribute.side_effect = lambda k: {"name": "todo", "value": "SAVE", "id": ""}.get(k, "")
            r_save.is_selected.return_value = False

            drv_esc = MagicMock()
            by_name_els = {"ship_1": [s1_el], "ship_4": [s4_el]}
            by_name_el = {"gal": gal_el, "sun": sun_el, "pla": pla_el}
            drv_esc.find_elements.side_effect = lambda by, sel: (
                by_name_els.get(sel, []) if by == "name" else [r_trn, r_save]
            )
            drv_esc.find_element.side_effect = lambda by, sel: (
                by_name_el[sel]
                if by == "name"
                else (launch_el if "goButton" in sel else cont_el)
            )
            p_esc = replace(planet, ships={"s1": 6, "s4": 12, "s9": 5})
            st_esc = GameState(31, 103603, "u", 1000.0, (p_esc,), active_planet_id=12476)
            cli_esc = MagicMock(spec=GameBrowserClient)
            cli_esc.driver = drv_esc
            cli_esc.navigate_menu.return_value = step1_esc_html
            cli_esc._wait_frame_ready.side_effect = [step2_esc_html, "<html><body>Dispatched</body></html>"]
            cli_esc.read_planets_overview.return_value = st_esc

            ex_esc = ActionExecutor(dry_run=False, allow_fleetsave=True, action_delay_range=(0.0, 0.0))
            esc_act = PlannedAction(
                priority="P2" if is_tut else "P0",
                kind="ESCAPE_FLIGHT",
                planet_id=12476,
                planet_coords="02:380:12",
                target_name="Save -> 02:380:13",
                reason="Tutorial 7/11: Make an escape flight 0 / 1" if is_tut else "Survival fleetsave",
                executable_now=True,
            )
            res_esc = ex_esc.execute_action(cli_esc, st_esc, esc_act)
            self.assertTrue(res_esc.executed, msg=res_esc.message)
            self.assertEqual(s1_el.value, exp_s1)
            self.assertEqual(bool(s4_el.value), exp_s4_called)
            if exp_s4_called:
                self.assertEqual(s4_el.value, "12")
            r_save.click.assert_called_once()
            launch_el.click.assert_called_once()

    def test_attackzone_forbidden_and_missing_step2_mission_radios_return_6h_target_cooldown(self) -> None:
        from dataclasses import replace as dc_replace
        from spaceinvasion.session import executor as ex_mod

        # 1. 1-word "attackzone" / "angriffszone" / "urlaubsmodus" / "can't be attacked" detection
        for banner, mkind in (
            ("Spy (forbidden): out of your attackzone range.", "SPY"),
            ("Attack (forbidden): out of your attackzone range.", "ATTACK"),
            ("Angriff (verboten): außerhalb deiner angriffszone.", "ATTACK"),
            ("Spieler befindet sich im Urlaubsmodus.", "ATTACK"),
            ("This user can't be attacked!.", "ATTACK"),
        ):
            html = f"<html><body><div class='error'>{banner}</div></body></html>"
            self.assertTrue(ex_mod._is_unattackable_target_html(html), banner)
            self.assertIn(banner.split(".")[0][:18].lower(), (ex_mod._extract_mission_forbidden_reason(html, mkind) or "").lower())

        base_st, _ = _make_state_and_action()
        p_fleet = dc_replace(
            base_st.planets[0],
            ships={"s1": 10, "s4": 25, "s10": 5},
            resources=Resources(50_000, 30_000, 20_000, 20_000),
        )
        st = dc_replace(base_st, planets=(p_fleet,))

        step1_html = (
            '<html><body><form name="fleet" action="indexInternal.es?action=internalSendFleet" method="POST">'
            '<input type="text" name="ship_1" value="0"/>'
            '<input type="text" name="ship_4" value="0"/>'
            '<input type="text" name="ship_10" value="0"/>'
            '<input type="text" name="gal" value="2"/>'
            '<input type="text" name="sun" value="381"/>'
            '<input type="text" name="pla" value="6"/>'
            '<input type="submit" value="Continue"/></form></body></html>'
        )
        # Step 2 form#fleet present with TRANSPORT + SAVE radios, but ATTACK & SPY radios omitted!
        step2_no_attack_or_spy_html = (
            '<html><body><form id="fleet" action="indexInternal.es?action=internalSendFleet&send=1" method="POST">'
            '<label><input name="todo" type="radio" value="TRANSPORT"/> Transport</label>'
            '<label><input name="todo" type="radio" value="SAVE"/> Escape flight</label>'
            '<input id="goButton" type="button" value="LAUNCH"/>'
            '</form></body></html>'
        )

        def _mk_inp() -> MagicMock:
            el = MagicMock()
            el.value = ""

            def _sk(v: str) -> None:
                el.value = v

            el.send_keys.side_effect = _sk
            el.get_attribute.side_effect = lambda k: el.value if k == "value" else ""
            return el

        # 2. _execute_attack on Step 2 without ATTACK radio -> 6h target_cooldown_seconds + validated=False
        s1_el, s4_el, s10_el = _mk_inp(), _mk_inp(), _mk_inp()
        gal_el, sun_el, pla_el = _mk_inp(), _mk_inp(), _mk_inp()
        cont_el = MagicMock()
        r_trn = MagicMock()
        r_trn.get_attribute.side_effect = lambda k: {"name": "todo", "value": "TRANSPORT", "disabled": None}.get(k, "")
        r_trn.is_selected.return_value = False
        r_save = MagicMock()
        r_save.get_attribute.side_effect = lambda k: {"name": "todo", "value": "SAVE", "disabled": None}.get(k, "")
        r_save.is_selected.return_value = False

        drv_atk = MagicMock()
        by_name_els = {"ship_1": [s1_el], "ship_4": [s4_el], "ship_10": [s10_el]}
        by_name_el = {"gal": gal_el, "sun": sun_el, "pla": pla_el}
        drv_atk.find_elements.side_effect = lambda by, sel: (
            by_name_els.get(sel, []) if by == "name" else [r_trn, r_save]
        )
        drv_atk.find_element.side_effect = lambda by, sel: by_name_el[sel] if by == "name" else cont_el
        drv_atk.page_source = step2_no_attack_or_spy_html

        cli_atk = MagicMock(spec=GameBrowserClient)
        cli_atk.driver = drv_atk
        cli_atk.navigate_menu.return_value = step1_html
        cli_atk._wait_frame_ready.return_value = step2_no_attack_or_spy_html
        cli_atk.read_planets_overview.return_value = st

        ex = ActionExecutor(dry_run=False, allow_raids=True, action_delay_range=(0.0, 0.0))
        atk_act = PlannedAction(
            priority="P2",
            kind="ATTACK",
            planet_id=12476,
            planet_coords="02:380:12",
            target_name="02:381:06",
            reason="Inactive raid",
            executable_now=True,
        )
        res_atk = ex.execute_action(cli_atk, st, atk_act)
        self.assertFalse(res_atk.executed)
        self.assertFalse(res_atk.validated)
        self.assertEqual(res_atk.target_cooldown_seconds, 6 * 3600.0)
        self.assertTrue(bool(res_atk.target_block_reason))
        self.assertIn("available radios: TRANSPORT, SAVE", res_atk.message)

        # 3. _execute_spy on Step 2 with Spy (forbidden): out of your attackzone range -> 6h cooldown
        step2_az_html = (
            '<html><body><div class="error">Spy (forbidden): out of your attackzone range.</div>'
            '<form id="fleet"><input name="todo" type="radio" value="SAVE"/></form></body></html>'
        )
        cli_spy = MagicMock(spec=GameBrowserClient)
        drv_spy = MagicMock()
        drv_spy.find_elements.side_effect = lambda by, sel: (
            {"ship_10": [_mk_inp()]}.get(sel, []) if by == "name" else [r_save]
        )
        drv_spy.find_element.side_effect = lambda by, sel: (
            {"gal": _mk_inp(), "sun": _mk_inp(), "pla": _mk_inp()}[sel] if by == "name" else MagicMock()
        )
        drv_spy.page_source = step2_az_html
        cli_spy.driver = drv_spy
        cli_spy.navigate_menu.return_value = step1_html
        cli_spy._wait_frame_ready.return_value = step2_az_html
        cli_spy.read_planets_overview.return_value = st

        spy_act = PlannedAction(
            priority="P2",
            kind="SPY",
            planet_id=12476,
            planet_coords="02:380:12",
            target_id=10,
            target_level=1,
            target_name="02:322:15",
            reason="Scout inactive",
            executable_now=True,
        )
        res_spy = ex.execute_action(cli_spy, st, spy_act)
        self.assertFalse(res_spy.executed)
        self.assertFalse(res_spy.validated)
        self.assertEqual(res_spy.target_cooldown_seconds, 6 * 3600.0)
        self.assertIn("attackzone", (res_spy.target_block_reason or "").lower())


if __name__ == "__main__":
    unittest.main()
