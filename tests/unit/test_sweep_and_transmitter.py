"""Unit tests for periodic randomized 100-page galaxy sweeps, colony cap gating, and daily free IDT activation."""

from __future__ import annotations

from pathlib import Path
import tempfile
import unittest
from unittest.mock import MagicMock

from spaceinvasion.core.config import StrategyConfig
from spaceinvasion.core.model import (
    EnergyBalance,
    GameState,
    PlanetState,
    Resources,
    count_allocated_normal_planets,
)
from spaceinvasion.parsing.account_pages import (
    parse_daily_login_bonus,
    parse_transmitter_page,
)
from spaceinvasion.db.storage import GameDatabase
from spaceinvasion.core.model import PlannedAction
from spaceinvasion.planner.heuristics import plan_actions
from spaceinvasion.runtime.bridge import GameApiBridge
from spaceinvasion.session.executor import ActionExecutor


_IDT_FREE_HTML_BEFORE = """
<html><body>
<script>_freeSpins=2;_games=0;_isIncrease=false;window.SI_TPL={transmitterSpin:{initDelay:0,useText:"activate for %n% %up%",autoAddSpin:false}};</script>
<form action="/indexInternal.es" id="tm.form" method="POST">
  <table><tr><td>charges: 2</td></tr></table>
  <input name="action" type="hidden" value="internalTransmitter"/>
  <input id="tm.play" name="play" type="hidden" value="1"/>
  <input name="idtSrc" type="hidden" value="1"/>
  <a class="idt-srcpick__opt is-on" href="indexInternal.es?action=internalTransmitter&amp;idtSrc=1">charges 2</a>
  <a class="idt-srcpick__opt" href="indexInternal.es?action=internalTransmitter&amp;idtSrc=3">Urplasma 0</a>
  <button class="buy-btn buy--blue" id="tm.submit" type="button">
    <span class="buy__label">FREE</span><span class="buy__inner"> activate imediately </span>
  </button>
</form>
<table>
  <tr><td>date</td><td>winning</td><td>energy reserves</td><td>free activation</td></tr>
  <tr><td>---</td><td>---</td><td>---</td><td>---</td></tr>
</table>
</body></html>
"""

_IDT_FREE_HTML_AFTER = """
<html><body>
<script>_freeSpins=1;_games=0;_isIncrease=false;window.SI_TPL={transmitterSpin:{initDelay:0,useText:"activate for %n% %up%",autoAddSpin:false}};</script>
<form action="/indexInternal.es" id="tm.form" method="POST">
  <table><tr><td>charges: 1</td></tr></table>
  <input name="action" type="hidden" value="internalTransmitter"/>
  <input id="tm.play" name="play" type="hidden" value="1"/>
  <input name="idtSrc" type="hidden" value="1"/>
  <button class="buy-btn buy--blue" id="tm.submit" type="button">
    <span class="buy__label">FREE</span><span class="buy__inner"> activate imediately </span>
  </button>
</form>
<table>
  <tr><td>date</td><td>winning</td><td>energy reserves</td><td>free activation</td></tr>
  <tr><td>2026-11-17 04:05:00</td><td>25.000 Kryptonite</td><td>0</td><td>1</td></tr>
</table>
</body></html>
"""

_IDT_PAID_URPLASMA_HTML = """
<html><body>
<script>_freeSpins=0;_games=1;_isIncrease=false;window.SI_TPL={transmitterSpin:{initDelay:0,useText:"activate for %n% %up%",autoAddSpin:true}};</script>
<form action="/indexInternal.es" id="tm.form" method="POST">
  <table><tr><td>charges: 0</td></tr></table>
  <input name="action" type="hidden" value="internalTransmitter"/>
  <input id="tm.play" name="play" type="hidden" value="1"/>
  <input name="idtSrc" type="hidden" value="3"/>
  <button class="buy-btn buy--blue" id="tm.submit" type="button">
    <span class="buy__label">URPLASMA</span>
    <span class="buy__inner">activate for 20 <div class="urplasma-icon-30"></div></span>
  </button>
</form>
</body></html>
"""


def _make_planet(pid: int, coords: str, *, ptype: str = "normal", ships: dict[str, int] | None = None) -> PlanetState:
    return PlanetState(
        planet_id=pid,
        name=f"Colony {pid}",
        coords=coords,
        planet_type=ptype,
        fields_used=20,
        fields_max=320,
        temperature=25,
        resources=Resources(50_000, 30_000, 40_000, 25_000),
        storage=Resources(500_000, 500_000, 500_000, 500_000),
        energy=EnergyBalance(1000, 500),
        buildings={"b1": 10, "b2": 8, "b5": 11, "b9": 4, "b10": 3},
        ships=dict(ships or {"s4": 10, "s8": 1, "s10": 5, "s11": 3}),
    )


def _make_galaxy_html(galaxy: int, system: int, *, include_debris: bool = True) -> str:
    rubble_div = '<div class="rubble-orbit" name="rubblefield"></div>' if include_debris else ""
    return f"""
    <html><body>
    <script>window.SI_TPL={{galaxy:{{sid:'abc',spacia:97563,galaxy:{galaxy},sun:{system}}}}};</script>
    <div id="planet-list">02:380:12 Colony 2 System</div>
    <span id="planet-coords">02:380:12</span>
    <div class="planet-hit" data-planet="{10000 + system * 10 + 4}">
      {rubble_div}
      <div><span style="color:#c9d324;"><b>FarmColony</b><br/>(FarmUser_{system})</span><div>4</div></div>
    </div>
    <div class="planet-hit" data-planet="{10000 + system * 10 + 8}">
      <div><span style="color:#cacaca;">X: 520 Y: 90</span><div>8</div></div>
    </div>
    </body></html>
    """


class TestSweepAndTransmitter(unittest.TestCase):
    def test_parse_transmitter_page_free_vs_urplasma_guard(self) -> None:
        free_info = parse_transmitter_page(_IDT_FREE_HTML_BEFORE)
        self.assertTrue(free_info["has_form"])
        self.assertEqual(free_info["free_spins"], 2)
        self.assertEqual(free_info["charges"], 2)
        self.assertEqual(free_info["idt_src"], 1)
        self.assertFalse(free_info["has_urplasma_cost"])
        self.assertTrue(free_info["can_activate_free"])

        after_info = parse_transmitter_page(_IDT_FREE_HTML_AFTER)
        self.assertEqual(after_info["free_spins"], 1)
        self.assertEqual(after_info["latest_reward"], "25.000 Kryptonite")

        paid_info = parse_transmitter_page(_IDT_PAID_URPLASMA_HTML)
        self.assertEqual(paid_info["free_spins"], 0)
        self.assertTrue(paid_info["has_urplasma_cost"])
        self.assertFalse(paid_info["can_activate_free"])

    def test_executor_activates_free_spin_and_blocks_paid_urplasma(self) -> None:
        p1 = _make_planet(101, "02:380:12")
        st = GameState(31, 7, "cmdr", 1_700_000_000.0, (p1,), active_planet_id=101)
        act = PlannedAction(
            priority="P2",
            kind="ACTIVATE_TRANSMITTER",
            planet_id=101,
            planet_coords="02:380:12",
            target_name="Interdimensional Transmitter",
            reason="Daily free spin",
            executable_now=True,
        )

        # Case 1: Free spin succeeds and decrements charges 2 -> 1
        client_ok = MagicMock()
        client_ok.navigate_menu.return_value = _IDT_FREE_HTML_BEFORE
        client_ok._wait_frame_ready.return_value = _IDT_FREE_HTML_AFTER
        client_ok.driver.execute_script.return_value = {"ok": True}
        btn_mock = MagicMock()
        client_ok.driver.find_element.return_value = btn_mock
        client_ok.read_planets_overview.return_value = (p1,)

        ex = ActionExecutor(dry_run=False, allow_transmitter=True)
        res_ok = ex.execute_action(client_ok, st, act)
        self.assertTrue(res_ok.executed)
        self.assertTrue(res_ok.validated)
        self.assertIn("25.000 Kryptonite", res_ok.message)
        btn_mock.click.assert_called_once()

        # Case 2: 0 free spins -> skipped safely without clicking or raising false error
        client_zero = MagicMock()
        client_zero.navigate_menu.return_value = _IDT_PAID_URPLASMA_HTML
        res_zero = ex.execute_action(client_zero, st, act)
        self.assertFalse(res_zero.executed)
        self.assertTrue(res_zero.validated)
        client_zero.driver.find_element.assert_not_called()

        # Case 3: Tampered HTML claiming _freeSpins=1 but showing Urplasma cost -> hard abort!
        tampered_html = _IDT_PAID_URPLASMA_HTML.replace("_freeSpins=0", "_freeSpins=1")
        client_tampered = MagicMock()
        client_tampered.navigate_menu.return_value = tampered_html
        res_tampered = ex.execute_action(client_tampered, st, act)
        self.assertFalse(res_tampered.executed)
        self.assertFalse(res_tampered.validated)
        client_tampered.driver.find_element.assert_not_called()

    def test_daily_transmitter_persistence_and_planning_once_per_day(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "test.db"
            db = GameDatabase(db_path)
            self.addCleanup(db.close)

            day1_ts = 1_794_890_000.0  # Fixed UTC timestamp
            status_before = db.get_transmitter_status(now_ts=day1_ts)
            self.assertTrue(status_before["due"])

            p1 = _make_planet(101, "02:380:12")
            st = GameState(31, 7, "cmdr", day1_ts, (p1,), active_planet_id=101)
            cfg = StrategyConfig(allow_transmitter=True)

            acts_due = plan_actions(st, config=cfg, transmitter_status=status_before, now_ts=day1_ts)
            self.assertTrue(any(a.kind == "ACTIVATE_TRANSMITTER" and a.executable_now for a in acts_due))

            status_after = db.record_transmitter_state(
                activated=True,
                free_spins_remaining=1,
                reward="10.000 Spice",
                ts=day1_ts + 10.0,
            )
            self.assertFalse(status_after["due"])
            self.assertEqual(status_after["total_activations"], 1)

            acts_same_day = plan_actions(st, config=cfg, transmitter_status=status_after, now_ts=day1_ts + 3600.0)
            self.assertFalse(any(a.kind == "ACTIVATE_TRANSMITTER" for a in acts_same_day))

            # Next day (+25 hours) -> due again!
            status_next_day = db.get_transmitter_status(now_ts=day1_ts + 25 * 3600.0)
            self.assertTrue(status_next_day["due"])

    def test_parse_daily_login_bonus_live_popup_vs_clean_home(self) -> None:
        popup_html = """
        <html><body>
        <div>Today, its your <b>3</b> consecutive Login for <b>RETRO-Universe</b></div>
        <div><img src="/img/payment/transmitter.png" style="height:50px"/> 1x free interdimensional Transmitter Game</div>
        <div onclick="location.href='indexInternal.es?action=internalHome&amp;hideLoginBonus=1&amp;showTutorial=1&amp;sid=secret';">
          show on next login x
        </div>
        <div style="margin:10px;position:relative;display:none;">
          <select id="tpid" style="font-size:12px;">
            <option value="101">02:380:12 Colony</option>
            <option value="102" selected="selected">02:381:05 Colony 2</option>
          </select>
        </div>
        <script>
          sendDailyLogin=function(){
            var e=document.getElementById("tpid");
            var tpid=e[e.selectedIndex].value;
            location.href='indexInternal.es?action=internalHome&getLoginBonus=1&tpid=' + tpid + '&sid=secret';
          };
        </script>
        <div onclick="sendDailyLogin();" style="display:inline-block;cursor:pointer;">
          <table><tbody><tr><td align="center"><span> collect daily bonus </span></td></tr></tbody></table>
        </div>
        </body></html>
        """
        parsed = parse_daily_login_bonus(popup_html)
        self.assertTrue(parsed["present"])
        self.assertEqual(parsed["button_label"], "collect daily bonus")
        self.assertTrue(parsed["has_send_daily_login"])
        self.assertEqual(parsed["tpid_options"], [101, 102])
        self.assertEqual(parsed["default_tpid"], 102)
        self.assertEqual(parsed["consecutive_day"], 3)
        self.assertTrue(parsed["grants_free_transmitter"])
        self.assertNotIn("secret", repr(parsed))

        clean_home = "<html><body><div>Command Central Overview</div></body></html>"
        self.assertFalse(parse_daily_login_bonus(clean_home)["present"])

        hidden_popup = popup_html.replace(
            'style="display:inline-block;cursor:pointer;"',
            'style="display:none;cursor:pointer;"',
        )
        self.assertFalse(parse_daily_login_bonus(hidden_popup)["present"])

    def test_executor_collects_daily_bonus_and_switches_to_homeworld_before_spin(self) -> None:
        p1 = _make_planet(101, "02:380:12")
        p2 = _make_planet(102, "02:381:05")
        st = GameState(31, 7, "cmdr", 1_700_000_000.0, (p1, p2), active_planet_id=102)
        # Even if action mistakenly points to secondary colony 102, executor must target Homeworld 101.
        act = PlannedAction(
            priority="P2",
            kind="ACTIVATE_TRANSMITTER",
            planet_id=102,
            planet_coords="02:381:05",
            target_name="Interdimensional Transmitter",
            reason="Daily free spin",
            executable_now=True,
        )

        home_with_popup = """
        <html><body>
        <div>Today, its your <b>3</b> consecutive Login for <b>RETRO-Universe</b></div>
        <div style="display:none;"><select id="tpid">
          <option value="101">02:380:12 Home</option>
          <option value="102" selected="selected">02:381:05 Colony</option>
        </select></div>
        <script>sendDailyLogin=function(){};</script>
        <div onclick="sendDailyLogin();"><span>collect daily bonus</span></div>
        </body></html>
        """
        home_after_bonus = "<html><body><div>Command Central - Bonus collected</div></body></html>"

        def _make_tm_html(
            selected_pid: int, free_spins: int, reward_row: str = "<tr><td>---</td><td>---</td></tr>"
        ) -> str:
            sel101 = ' selected="selected"' if selected_pid == 101 else ""
            sel102 = ' selected="selected"' if selected_pid == 102 else ""
            return f"""
            <html><body>
            <form name="fselect">
              <select name="planet">
                <option value="indexInternal.es?action=internalTransmitter&amp;newPlanet=101"{sel101}>Home [02:380:12]</option>
                <option value="indexInternal.es?action=internalTransmitter&amp;newPlanet=102"{sel102}>Colony [02:381:05]</option>
              </select>
            </form>
            <script>_freeSpins={free_spins};window.SI_TPL={{transmitterSpin:{{autoAddSpin:false}}}};</script>
            <form action="/indexInternal.es" id="tm.form" method="POST">
              <table><tr><td>charges: {free_spins}</td></tr></table>
              <input name="action" type="hidden" value="internalTransmitter"/>
              <input id="tm.play" name="play" type="hidden" value="1"/>
              <input name="idtSrc" type="hidden" value="1"/>
              <button id="tm.submit" type="button"><span class="buy__label">FREE</span> activate</button>
            </form>
            <table><tr><td>date</td><td>winning</td></tr>{reward_row}</table>
            </body></html>
            """

        tm_on_colony_102 = _make_tm_html(102, 1)
        tm_on_homeworld_101 = _make_tm_html(101, 1)
        tm_after_spin = _make_tm_html(101, 0, "<tr><td>2026-11-19 00:05:00</td><td>50.000 Metal</td></tr>")

        nav_calls: list[str] = []
        tpid_script_args: list[tuple] = []

        client = MagicMock()

        def _nav_side_effect(action_name: str, **_kw: object) -> str:
            nav_calls.append(action_name)
            if action_name == "internalHome":
                return home_with_popup
            if action_name == "internalTransmitter":
                return tm_on_colony_102
            return ""

        client.navigate_menu.side_effect = _nav_side_effect

        bonus_btn = MagicMock()
        bonus_btn.get_attribute.side_effect = lambda a: "sendDailyLogin();" if a == "onclick" else ""
        client.driver.find_elements.return_value = [bonus_btn]

        opt_101 = MagicMock()
        opt_101.get_attribute.return_value = "indexInternal.es?action=internalTransmitter&newPlanet=101"
        opt_101.is_selected.return_value = False
        opt_102 = MagicMock()
        opt_102.get_attribute.return_value = "indexInternal.es?action=internalTransmitter&newPlanet=102"
        opt_102.is_selected.return_value = True
        planet_select_el = MagicMock()
        planet_select_el.tag_name = "select"
        planet_select_el.find_elements.return_value = [opt_101, opt_102]

        tm_btn = MagicMock()

        def _find_el(by: object, val: str) -> MagicMock:
            if val == 'select[name="planet"]':
                return planet_select_el
            if val == "tm.submit":
                return tm_btn
            raise AssertionError(f"Unexpected find_element({by!r}, {val!r})")

        client.driver.find_element.side_effect = _find_el

        def _exec_script(script: str, *args: object) -> object:
            if "getElementById('tpid')" in script:
                tpid_script_args.append(args)
                return True
            if "sel.onchange" in script:
                return True
            if "document.getElementById('tm.form')" in script:
                return {"ok": True}
            return True

        client.driver.execute_script.side_effect = _exec_script
        client._wait_frame_ready.side_effect = [
            home_after_bonus,  # after clicking daily bonus
            tm_on_homeworld_101,  # after switching planet 102 -> 101 on internalTransmitter
            tm_after_spin,  # after clicking #tm.submit
        ]
        client.read_planets_overview.return_value = GameState(
            31, 7, "cmdr", 1_700_000_010.0, (p1, p2), active_planet_id=102
        )

        ex = ActionExecutor(dry_run=False, allow_transmitter=True)
        res = ex.execute_action(client, st, act)

        self.assertTrue(res.executed, res.message)
        self.assertTrue(res.validated, res.message)
        self.assertEqual(nav_calls, ["internalHome", "internalTransmitter"])
        self.assertIn(("101",), tpid_script_args)
        bonus_btn.click.assert_called_once()
        tm_btn.click.assert_called_once()
        self.assertEqual(res.action.planet_id, 101)
        self.assertEqual(res.action.planet_coords, "02:380:12")
        self.assertIsNotNone(res.updated_state)
        self.assertEqual(res.updated_state.active_planet_id, 101)
        self.assertTrue(ex.last_transmitter_info["daily_bonus_checked"])
        self.assertTrue(ex.last_transmitter_info["daily_bonus_collected"])
        self.assertEqual(ex.last_transmitter_info["homeworld_planet_id"], 101)
        self.assertIn("50.000 Metal", res.message)
        self.assertIn("02:380:12", res.message)

    def test_executor_aborts_transmitter_if_homeworld_selection_fails(self) -> None:
        p1 = _make_planet(101, "02:380:12")
        p2 = _make_planet(102, "02:381:05")
        st = GameState(31, 7, "cmdr", 1_700_000_000.0, (p1, p2), active_planet_id=102)
        act = PlannedAction(
            priority="P2",
            kind="ACTIVATE_TRANSMITTER",
            planet_id=101,
            planet_coords="02:380:12",
            target_name="Interdimensional Transmitter",
            reason="Daily free spin",
            executable_now=True,
        )
        tm_stuck_on_102 = """
        <html><body>
        <form name="fselect">
          <select name="planet">
            <option value="indexInternal.es?action=internalTransmitter&amp;newPlanet=101">Home [02:380:12]</option>
            <option value="indexInternal.es?action=internalTransmitter&amp;newPlanet=102" selected="selected">Colony [02:381:05]</option>
          </select>
        </form>
        <script>_freeSpins=1;window.SI_TPL={transmitterSpin:{autoAddSpin:false}};</script>
        <form id="tm.form"><input name="idtSrc" value="1"/><input id="tm.play" name="play" value="1"/>
          <button id="tm.submit" type="button">FREE activate</button>
        </form>
        </body></html>
        """
        client = MagicMock()
        client.navigate_menu.side_effect = lambda a, **_kw: (
            "<html><body>Command Central</body></html>" if a == "internalHome" else tm_stuck_on_102
        )
        opt_101 = MagicMock()
        opt_101.get_attribute.return_value = "indexInternal.es?action=internalTransmitter&newPlanet=101"
        opt_101.is_selected.return_value = False
        opt_102 = MagicMock()
        opt_102.get_attribute.return_value = "indexInternal.es?action=internalTransmitter&newPlanet=102"
        opt_102.is_selected.return_value = True
        sel_el = MagicMock()
        sel_el.find_elements.return_value = [opt_101, opt_102]
        client.driver.find_element.return_value = sel_el
        # Planet switch reload remains stuck on 102!
        client._wait_frame_ready.return_value = tm_stuck_on_102
        client.driver.execute_script.return_value = True

        ex = ActionExecutor(dry_run=False, allow_transmitter=True)
        res = ex.execute_action(client, st, act)
        self.assertFalse(res.executed)
        self.assertFalse(res.validated)
        self.assertIn("Homeworld", res.validation_error or "")
        # #tm.submit must never be queried or clicked when stuck on colony 102
        for call in client.driver.find_element.call_args_list:
            self.assertNotEqual(call.args[1], "tm.submit")

    def test_plan_transmitter_targets_homeworld_and_db_recovers_missing_daily_bonus_check(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db = GameDatabase(Path(tmp) / "test.db")
            self.addCleanup(db.close)

            day_ts = 1_794_960_100.0
            # Simulate legacy/pre-fix check that went straight to internalTransmitter (0 free spins,
            # no daily_bonus_checked_day): must remain due=True so the bot checks Command Central!
            db.save_meta_json(
                "transmitter_state",
                {
                    "last_activated_day": "",
                    "last_checked_day": db.get_transmitter_status(now_ts=day_ts)["today_day_key"],
                    "free_spins_remaining": 0,
                    "total_activations": 0,
                },
                ts=day_ts,
            )
            st_legacy = db.get_transmitter_status(now_ts=day_ts + 5.0)
            self.assertTrue(st_legacy["due"])

            # Planning on multi-planet empire with secondary colony active must target Homeworld (101)
            p1 = _make_planet(101, "02:380:12")
            p2 = _make_planet(102, "02:381:05")
            gs = GameState(31, 7, "cmdr", day_ts + 5.0, (p1, p2), active_planet_id=102)
            cfg = StrategyConfig(allow_transmitter=True)
            acts = plan_actions(gs, config=cfg, transmitter_status=st_legacy, now_ts=day_ts + 5.0)
            tm_acts = [a for a in acts if a.kind == "ACTIVATE_TRANSMITTER"]
            self.assertEqual(len(tm_acts), 1)
            self.assertEqual(tm_acts[0].planet_id, 101)
            self.assertEqual(tm_acts[0].planet_coords, "02:380:12")

            # After Command Central check confirms 0 charges -> due=False (no infinite loop)
            st_checked = db.record_transmitter_state(
                activated=False,
                free_spins_remaining=0,
                daily_bonus_checked=True,
                daily_bonus_collected=False,
                ts=day_ts + 20.0,
            )
            self.assertFalse(st_checked["due"])

            # Passive Command Central read later spots Daily Login Bonus popup -> re-arms due=True!
            st_rearmed = db.note_daily_login_bonus_seen(present=True, ts=day_ts + 40.0)
            self.assertTrue(st_rearmed["due"])
            self.assertTrue(st_rearmed["daily_bonus_available"])

            # Once activated today -> due=False and passive popup note cannot re-arm same day
            st_done = db.record_transmitter_state(
                activated=True,
                free_spins_remaining=0,
                reward="25.000 Pig iron",
                daily_bonus_checked=True,
                daily_bonus_collected=True,
                ts=day_ts + 60.0,
            )
            self.assertFalse(st_done["due"])
            st_done_after_note = db.note_daily_login_bonus_seen(present=True, ts=day_ts + 80.0)
            self.assertFalse(st_done_after_note["due"])

    def test_api_activate_transmitter_enforces_homeworld_planet(self) -> None:
        p1 = _make_planet(101, "02:380:12")
        p2 = _make_planet(102, "02:381:05")
        gs = GameState(31, 7, "cmdr", 1_700_000_000.0, (p1, p2), active_planet_id=102)
        with tempfile.TemporaryDirectory() as tmp:
            bridge = GameApiBridge(
                db_path=Path(tmp) / "api.db",
                config_path=Path(tmp) / "cfg.json",
            )
            self.addCleanup(bridge.close)
            bridge._last_state = gs
            bridge._last_snapshot_at = 1_700_000_000.0
            res = bridge.handle_command(
                {"command": "activate_transmitter", "planet_id": 102, "dry_run": True},
            )
            self.assertTrue(res["ok"])
            self.assertEqual(res["action"]["planet_id"], 101)
            self.assertEqual(res["action"]["planet_coords"], "02:380:12")

    def test_wait_frame_ready_accepts_post_internal_transmitter_without_query_action(self) -> None:
        from spaceinvasion.session.client import GameBrowserClient

        driver = MagicMock()
        driver.execute_script.return_value = {
            "ready": "complete",
            "href": "https://spaceinvasion.bitmeup.com/indexInternal.es",
            "token": None,
        }
        driver.page_source = _IDT_FREE_HTML_AFTER
        client = GameBrowserClient(driver, timeout=1.0)
        client._switch_frame = MagicMock()
        html = client._wait_frame_ready(
            "F_Hauptframe",
            expected_action="internalTransmitter",
            nav_token="old-token",
        )
        self.assertIn("25.000 Kryptonite", html)

    def test_colony_allocation_cap_gates_colonies_but_keeps_players_and_debris(self) -> None:
        raid_targets = (
            {
                "coords": "02:380:04",
                "owner": "InactiveUser",
                "status_color": "yellow",
                "is_inactive": True,
                "has_debris": True,
                "last_scanned_ts": 1_700_000_000.0,
            },
            {
                "coords": "02:380:08",
                "owner": "",
                "status_color": "free",
                "is_inactive": False,
                "has_debris": False,
                "uninhabited_fields": 340,
                "last_scanned_ts": 1_700_000_000.0,
            },
        )
        cfg = StrategyConfig(
            allow_galaxy_scan=True,
            allow_raids=True,
            allow_colonize=True,
            max_planets=11,
            min_colony_fields=300,
        )

        # 2 normal planets (< 11 max_planets) + 1 asteroid + 1 raid colony -> colonies ARE planned
        planets_under_cap = (
            _make_planet(101, "02:380:12", ptype="normal"),
            _make_planet(102, "02:381:09", ptype="normal"),
            _make_planet(201, "02:380:16", ptype="asteroid"),
            _make_planet(301, "02:382:05", ptype="raid"),
        )
        st_under = GameState(
            31,
            7,
            "cmdr",
            1_700_000_050.0,
            planets_under_cap,
            active_planet_id=101,
            raid_targets=raid_targets,
        )
        self.assertEqual(count_allocated_normal_planets(st_under), 2)
        acts_under = plan_actions(st_under, config=cfg, now_ts=1_700_000_050.0)
        kinds_under = {a.kind for a in acts_under}
        self.assertIn("COLONIZE", kinds_under)
        self.assertIn("SPY", kinds_under)
        self.assertIn("RECYCLE", kinds_under)

        # 11 normal planets (== 11 max_planets) -> COLONIZE / free-slot SPY suppressed, SPY player + RECYCLE debris remain!
        planets_at_cap = tuple(_make_planet(100 + i, f"02:{370 + i:03d}:07", ptype="normal") for i in range(1, 12))
        st_full = GameState(
            31,
            7,
            "cmdr",
            1_700_000_050.0,
            planets_at_cap,
            active_planet_id=101,
            raid_targets=raid_targets,
        )
        self.assertEqual(count_allocated_normal_planets(st_full), 11)
        acts_full = plan_actions(st_full, config=cfg, now_ts=1_700_000_050.0)
        kinds_full = {a.kind for a in acts_full}
        self.assertNotIn("COLONIZE", kinds_full)
        self.assertFalse(any("Free slot" in (a.target_name or "") for a in acts_full))
        self.assertIn("SPY", kinds_full)
        self.assertIn("RECYCLE", kinds_full)

    def test_100_page_galaxy_sweep_persists_players_colonies_debris_and_schedules_under_5_min(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_dir = Path(tmp)
            db_path = tmp_dir / "state.db"
            visited_coords: list[tuple[int, int]] = []

            class _FakeSweepClient:
                def __enter__(self):
                    return self

                def __exit__(self, *_exc):
                    return None

                def ensure_in_game(self, **_kw):
                    return None

                def step_galaxy_system(self, *, galaxy: int, system: int, **_kw):
                    visited_coords.append((galaxy, system))
                    html = _make_galaxy_html(galaxy, system, include_debris=(system % 5 == 0))
                    return html, galaxy, system

            cfg_path = tmp_dir / "cfg.json"
            bridge = GameApiBridge(
                debug_port=9222,
                config_path=cfg_path,
                db_path=db_path,
                client_factory=lambda _port: _FakeSweepClient(),
            )
            self.addCleanup(bridge.close)
            bridge._last_state = GameState(
                31,
                7,
                "cmdr",
                1_700_000_000.0,
                (_make_planet(101, "02:380:12"),),
                active_planet_id=101,
            )

            res = bridge.handle_command(
                {
                    "command": "galaxy_sweep",
                    "pages": 100,
                    "galaxy": 2,
                    "start_system": 351,
                    "delay_min_ms": 0,
                    "delay_max_ms": 0,
                }
            )
            self.assertTrue(res["ok"])
            self.assertTrue(res["executed"])
            self.assertEqual(res["pages_scanned"], 100)
            self.assertEqual(len(visited_coords), 100)
            # Verifies wrap-around 351..400 (50 systems) -> 1..50 (50 systems) -> next cursor 51!
            self.assertEqual(visited_coords[0], (2, 351))
            self.assertEqual(visited_coords[49], (2, 400))
            self.assertEqual(visited_coords[50], (2, 1))
            self.assertEqual(visited_coords[-1], (2, 50))
            self.assertEqual(res["players_found"], 100)
            self.assertEqual(res["colonies_found"], 100)
            self.assertEqual(res["debris_found"], 20)
            self.assertTrue(res["collect_colonies"])

            sw = res["galaxy_sweep"]
            self.assertEqual(sw["cursor_galaxy"], 2)
            self.assertEqual(sw["cursor_system"], 51)
            self.assertEqual(sw["total_sweeps"], 1)
            self.assertEqual(sw["total_pages_scanned"], 100)
            # Guaranteed <= 5 minutes (300s) start-to-start cadence!
            self.assertLessEqual(sw["next_sweep_due_ts"] - sw["last_sweep_started_ts"], 300.0)
            self.assertGreater(sw["next_sweep_due_ts"], sw["last_sweep_started_ts"])

            # Verify when all 11 planets are allocated, sweep skips free colonies while still recording players + debris
            bridge._last_state = GameState(
                31,
                7,
                "cmdr",
                1_700_000_100.0,
                tuple(_make_planet(100 + i, f"02:{100 + i:03d}:07") for i in range(1, 12)),
                active_planet_id=101,
            )
            res_full = bridge.handle_command(
                {
                    "command": "galaxy_sweep",
                    "pages": 10,
                    "galaxy": 2,
                    "start_system": 60,
                    "delay_min_ms": 0,
                    "delay_max_ms": 0,
                }
            )
            self.assertEqual(res_full["pages_scanned"], 10)
            self.assertFalse(res_full["collect_colonies"])
            self.assertEqual(res_full["players_found"], 10)
            self.assertEqual(res_full["colonies_found"], 0)
            self.assertEqual(res_full["debris_found"], 2)

    def test_building_and_home_active_queues_extract_executing_timers_and_finish_ts(self) -> None:
        from spaceinvasion.parsing.overview import parse_home_metadata
        from spaceinvasion.parsing.queues import parse_building_queue

        ref_ts = 1_794_890_000.0
        b_html = """
        <html><body>
        <table class="ecoshadow"><tbody>
          <tr><td><b>Construction queue</b> ( 3 Elements )</td></tr>
          <tr>
            <td>Fusion plant (Level 23)</td>
            <td><table><tr><td><span id="sicntbar-1-1">01:28:29</span></td><td><span id="sicntbar-1-2">8%</span></td></tr><tr><td><a>Cancel</a></td></tr></table></td>
          </tr>
          <tr class="nachricht">
            <td>Pig iron warehouse ( Level 5 ) <a>abort</a></td>
            <td>00:03:53 <div>2026-11-17 03:14:11 => 2026-11-17 03:18:04</div></td>
          </tr>
          <tr class="nachricht">
            <td>Spice warehouse ( Level 3 ) <a>abort</a></td>
            <td>00:00:58 <div>2026-11-17 03:18:04 => 2026-11-17 03:19:02</div></td>
          </tr>
        </tbody></table>
        </body></html>
        """
        jobs = parse_building_queue(b_html, now_ts=ref_ts)
        self.assertEqual(len(jobs), 3)
        self.assertEqual(jobs[0].name, "Fusion plant")
        self.assertEqual(jobs[0].building_id, 5)
        self.assertEqual(jobs[0].target_level, 23)
        self.assertEqual(jobs[0].queue_slot, 1)
        self.assertEqual(jobs[0].remaining_seconds, 5309.0)
        self.assertAlmostEqual(jobs[0].finish_ts or 0.0, ref_ts + 5309.0, places=2)
        self.assertIn("8%", jobs[0].detail)

        # Row 2 stripped "Cancel"/"abort" and chained finish_ts = Row1 + 233s
        self.assertEqual(jobs[1].name, "Pig iron warehouse")
        self.assertEqual(jobs[1].building_id, 11)
        self.assertEqual(jobs[1].queue_slot, 2)
        self.assertAlmostEqual(jobs[1].finish_ts or 0.0, ref_ts + 5309.0 + 233.0, places=2)

        self.assertEqual(jobs[2].name, "Spice warehouse")
        self.assertEqual(jobs[2].building_id, 14)
        self.assertEqual(jobs[2].queue_slot, 3)
        self.assertAlmostEqual(jobs[2].finish_ts or 0.0, ref_ts + 5309.0 + 233.0 + 58.0, places=2)

        home_html = """
        <html><body>
        <script>SI_TPL.headerLoad={planetID:12476};
        var faData=[{"title":"Own fleet","type":"friendly","rows":[,{"id":119301143,"t":3142,"n":6,"rc":1,"tt":"6 Small transport<br>12 Small fighter<br><hr>None","a":'ATTACK',"c":0,"start":"[02:380:12] Colony","dest":"[02:378:15] Colony"}]},{"title":"Returning Fleets","type":"friendly","rows":[,{"id":119300941,"t":1582,"n":'4w',"tt":"12 Recycler<br><hr>16.378 Pig iron","a":'RECYCLE',"c":0,"return":true,"save":false,"start":"Colony [02:380:12]","dest":"[02:381:06] " + "Debris"}]}];
        </script>
        <div id="sp-own-building"> Fusion plant (Level 23) </div>
        <div data-left="5310" data-start-ts="1794890000000" data-total="5713" id="sp-own-building-left"></div>
        <div id="sp-own-research"> Mining technology (Level 9) </div>
        <div data-left="3934" data-start-ts="1794890000000" data-total="8889" id="sp-own-research-left"></div>
        <div id="sp-own-fleet"> Small transport </div>
        <div data-left="18" data-start-ts="1794890000000" data-total="21" id="sp-own-fleet-left"></div>
        </body></html>
        """
        pid, _, _, h_queues, _ = parse_home_metadata(home_html, now_ts=ref_ts)
        self.assertEqual(pid, 12476)
        self.assertEqual(len(h_queues), 5)
        by_cat = {}
        for q in h_queues:
            by_cat.setdefault(q.category, []).append(q)
        self.assertAlmostEqual(by_cat["building"][0].finish_ts or 0.0, ref_ts + 5310.0, places=2)
        self.assertAlmostEqual(by_cat["research"][0].finish_ts or 0.0, ref_ts + 3934.0, places=2)
        self.assertEqual(by_cat["research"][0].target_level, 9)
        self.assertEqual(len(by_cat["fleet"]), 3)
        self.assertTrue(any("Recycle ↩" in q.name and q.remaining_seconds == 1582.0 for q in by_cat["fleet"]))
        self.assertTrue(any("Attack →" in q.name and q.remaining_seconds == 3142.0 for q in by_cat["fleet"]))

    def test_galaxy_json_colonize_metadata_click_delay_jitter_and_discard_colony_candidate(self) -> None:
        from unittest.mock import MagicMock, patch
        from spaceinvasion.parsing.galaxy import parse_galaxy_system_html
        from spaceinvasion.parsing.models import (
            GalaxySlot,
            SpyReport,
        )
        from spaceinvasion.runtime.api.validation import ApiRequestError
        from spaceinvasion.runtime.bridge import GameApiBridge

        # 1. Passive #json-element colonize extraction on 2D Galaxy view
        gal_2d_html = """
        <html><body>
          <script id="json-element" type="application/json">
            {"galaxy": 2, "sun": 380, "planets": {"9": {"colonize": [295, 355]}, "10": {"colonize": 340}}}
          </script>
          <table>
            <tr><td>9</td><td></td></tr>
            <tr><td>10</td><td></td></tr>
          </table>
        </body></html>
        """
        slots = parse_galaxy_system_html(gal_2d_html, galaxy=2, system=380)
        by_pos = {s.position: s for s in slots}
        self.assertEqual(by_pos[9].colonize_min_fields, 295)
        self.assertEqual(by_pos[9].colonize_max_fields, 355)
        self.assertEqual(by_pos[10].uninhabited_fields, 340)

        # 2. galaxy_scan_click_delay_seconds + galaxy_scan_click_jitter_seconds + discard/restore colony candidate
        with tempfile.TemporaryDirectory(prefix="si_sweep_delay_") as tmp:
            tp = Path(tmp)
            cfg_path = tp / "config.json"
            cfg_path.write_text(
                '{"allow_galaxy_scan": true, "allow_colonize": true, "max_planets": 4, "galaxy_scan_click_delay_seconds": 1.8, "galaxy_scan_click_jitter_seconds": 0.4}',
                encoding="utf-8",
            )
            fake_client = MagicMock()
            fake_client.__enter__.return_value = fake_client
            fake_client.ensure_in_game.return_value = None
            fake_client.step_galaxy_system = lambda galaxy, system, **_kw: (gal_2d_html, galaxy, system)

            bridge = GameApiBridge(
                debug_port=19999,
                config_path=cfg_path,
                db_path=tp / "sweep.db",
                log_dir=None,
                client_factory=lambda _p: fake_client,
                ensure_session_fn=lambda: None,
            )
            try:
                assert bridge._db is not None
                with self.assertRaises(ApiRequestError):
                    bridge.handle_command(
                        {"command": "update_config", "config": {"galaxy_scan_click_delay_seconds": 0.05}}
                    )
                with self.assertRaises(ApiRequestError):
                    bridge.handle_command(
                        {"command": "update_config", "config": {"galaxy_scan_click_jitter_seconds": -1.0}}
                    )

                dry_sw = bridge.handle_command({"command": "galaxy_sweep", "pages": 3, "dry_run": True})
                self.assertAlmostEqual(dry_sw["click_delay_seconds"], 1.8)
                self.assertAlmostEqual(dry_sw["click_jitter_seconds"], 0.4)

                waits: list[float] = []
                with patch.object(bridge._sweep_stop_event, "wait", side_effect=lambda s: (waits.append(s), False)[1]):
                    live_sw = bridge.run_galaxy_sweep(pages=3, galaxy=2, start_system=380)
                self.assertEqual(live_sw["pages_scanned"], 3)
                self.assertEqual(len(waits), 2)
                for w in waits:
                    self.assertGreaterEqual(w, 1.4 - 1e-6)
                    self.assertLessEqual(w, 2.2 + 1e-6)

                # Discard & restore colony candidate excludes/restores from colony_candidates & planner
                bridge._db.record_galaxy_slots(
                    [
                        GalaxySlot("02:380:08", 8, status_color="free"),
                        GalaxySlot("02:380:09", 9, status_color="free"),
                    ],
                    ts=2000.0,
                )
                bridge._db.record_spy_report(
                    SpyReport(target_coords="02:380:08", owner="", resources=Resources(), uninhabited_fields=330),
                    status_color="free",
                    ts=2005.0,
                )
                before_col = bridge.handle_command({"command": "colony_candidates"})
                before_coords = [c["coords"] for c in before_col["candidates"]]
                self.assertIn("02:380:08", before_coords)
                self.assertEqual(before_col["discarded_count"], 0)

                disc = bridge.handle_command({"command": "discard_colony_candidate", "target_coords": "2:380:8"})
                self.assertTrue(disc["ok"])
                self.assertEqual(disc["target_coords"], "02:380:08")
                self.assertEqual(disc["discarded_count"], 1)

                after_disc = bridge.handle_command({"command": "colony_candidates"})
                self.assertNotIn("02:380:08", [c["coords"] for c in after_disc["candidates"]])
                self.assertEqual(len(after_disc["candidates"]), len(before_coords) - 1)
                self.assertEqual(after_disc["discarded_count"], 1)

                assert bridge._db is not None
                self.assertTrue(bridge._db.restore_colony_candidate("02:380:08"))
                after_rest = bridge.handle_command({"command": "colony_candidates"})
                self.assertIn("02:380:08", [c["coords"] for c in after_rest["candidates"]])
                self.assertEqual(len(after_rest["candidates"]), len(before_coords))
            finally:
                bridge.close()

    def test_retro_free_slot_ecology_probing_priority_and_noop_batch_continuation(self) -> None:
        from unittest.mock import MagicMock
        import bin.commander as commander_mod
        from spaceinvasion.combat.colonies import grade_colony_candidate
        from spaceinvasion.parsing.models import GalaxySlot
        from spaceinvasion.session.executor import ActionExecutor
        from spaceinvasion.runtime.bridge import GameApiBridge

        # 1. Range-based ecology grading (140-340 fields @ 28C -> Grade S, meets 300+ target; 100-220 -> Grade D, skipped)
        g_prime = grade_colony_candidate(
            "02:380:12",
            "02:380:08",
            colonize_min_fields=140,
            colonize_max_fields=340,
            temperature=28,
            min_fields_target=300,
        )
        self.assertTrue(g_prime["probed"])
        self.assertTrue(g_prime["meets_target"])
        self.assertEqual(g_prime["grade"], "S")
        self.assertEqual(g_prime["fields_display"], "140–340 fields")

        g_small = grade_colony_candidate(
            "02:380:12",
            "02:380:11",
            colonize_min_fields=100,
            colonize_max_fields=220,
            temperature=43,
            min_fields_target=300,
        )
        self.assertTrue(g_small["probed"])
        self.assertFalse(g_small["meets_target"])
        self.assertEqual(g_small["grade"], "D")

        # 2. Free-slot SPY outranks raid SPY in both priority and ga_nn modes & ignores stale "can't be attacked" block
        origin_p = _make_planet(101, "02:380:12", ships={"s10": 5, "s8": 1, "s1": 10})
        raid_targets = (
            {
                "coords": "02:362:16",
                "owner": "Mooee",
                "planet_name": "Colony",
                "status_color": "yellow",
                "is_inactive": True,
                "is_spacia": False,
                "last_spied_ts": 0.0,
            },
            {
                "coords": "02:380:09",
                "owner": "",
                "planet_name": "",
                "status_color": "free",
                "is_inactive": False,
                "is_spacia": False,
                "uninhabited_fields": None,
                "blocked_until_ts": 1_700_050_000.0,
                "block_reason": "This user can't be attacked!",
            },
        )
        cfg_m = StrategyConfig(
            planner_mode="priority",
            playstyle="fleeter",
            allow_raids=True,
            allow_galaxy_scan=True,
            allow_colonize=True,
            min_colony_fields=300,
            max_planets=11,
        )
        st_m = GameState(31, 7, "cmdr", 1_700_000_100.0, (origin_p,), active_planet_id=101, raid_targets=raid_targets)
        acts_m = plan_actions(st_m, config=cfg_m, now_ts=1_700_000_100.0)
        spy_acts = [a for a in acts_m if a.kind == "SPY" and a.executable_now]
        self.assertGreaterEqual(len(spy_acts), 2)
        self.assertEqual(spy_acts[0].target_name, "02:380:09 (Free slot)")

        # 3. _execute_spy probes free slot via 2D Galaxy ecology even when RETRO returns "This user can't be attacked!"
        retro_gal_html = """
        <html><body>
          <textarea id="json-element">{"position":{"galaxy":2,"system":380},"planets":{"8":{"position":8,"uid":0,"temperature":28,"colonize":[140,340,0]}}}</textarea>
        </body></html>
        """
        fake_browser = MagicMock()

        def _fake_dispatch_spy(*, galaxy: int, system: int, position: int, **_kw):
            return {
                "supported": True,
                "started": False,
                "error": "This user can't be attacked!",
                "html": retro_gal_html,
                "galaxy": galaxy,
                "system": system,
            }

        _fake_dispatch_spy.__module__ = "spaceinvasion.session.client"
        fake_browser.dispatch_galaxy_spy = _fake_dispatch_spy

        exec_inst = ActionExecutor(dry_run=False, action_delay_range=(0.0, 0.0))
        probe_act = spy_acts[0]
        probe_act_08 = type(probe_act)(
            priority="P2",
            kind="SPY",
            planet_id=101,
            planet_coords="02:380:12",
            target_id=10,
            target_level=1,
            target_name="02:380:08 (Free slot)",
            reason="Probe free slot 02:380:08",
            executable_now=True,
        )
        res_probe = exec_inst._execute_spy(fake_browser, st_m, probe_act_08, active_planet_confirmed=True)
        self.assertTrue(res_probe.executed)
        self.assertIn("140–340 fields (28°C)", res_probe.message)
        self.assertEqual(len(exec_inst.last_galaxy_slots), 1)
        self.assertEqual(exec_inst.last_galaxy_slots[0].colonize_max_fields, 340)

        # 4. V010 persistence + Commander continuing past No-op THROTTLE_BUILDING to execute SPY
        with tempfile.TemporaryDirectory(prefix="si_v10_col_") as tmp_v10:
            tp10 = Path(tmp_v10)
            bridge10 = GameApiBridge(
                debug_port=19998,
                config_path=tp10 / "cfg.json",
                db_path=tp10 / "v10.db",
                log_dir=None,
                ensure_session_fn=lambda: None,
            )
            try:
                assert bridge10._db is not None
                bridge10._db.record_target_block(
                    "02:380:08", blocked_until_ts=9_999_999_999.0, reason="This user can't be attacked!"
                )
                bridge10._db.record_galaxy_slots(exec_inst.last_galaxy_slots, ts=3000.0)
                cands10 = bridge10._db.get_colony_candidates(origin_coords="02:380:12", min_fields_target=300)
                self.assertEqual(len(cands10), 1)
                self.assertTrue(cands10[0]["probed"])
                self.assertTrue(cands10[0]["meets_target"])
                self.assertEqual(cands10[0]["blocked_until_ts"], 0.0)
            finally:
                bridge10.close()

        sent_cmds: list[str] = []

        class _FakeCmdApi:
            def get_state(self, **_kw):
                return st_m

            def send_command(self, payload):
                sent_cmds.append(str(payload.get("command")))
                if payload.get("command") == "throttle_building":
                    return {
                        "ok": True,
                        "executed": False,
                        "message": "No-op: Blast furnace 100% (resource2) is already at 100%.",
                    }
                return {"ok": True, "executed": True, "command": payload.get("command"), "message": "Probed free slot."}

        mq_noop = [
            {
                "id": 1,
                "status": "pending",
                "priority": "P1",
                "sort_rank": 1.0,
                "kind": "THROTTLE_BUILDING",
                "planet_id": 101,
                "planet_coords": "02:380:12",
                "target_id": 2,
                "target_level": 100,
                "target_name": "Blast furnace 100%",
                "reason": "Ensure 100%",
            }
        ]
        _, _, _, _, tick_outcomes = commander_mod.run_commander_tick(
            _FakeCmdApi(),  # type: ignore[arg-type]
            live=True,
            max_actions=2,
            config=StrategyConfig(
                allow_galaxy_scan=True,
                allow_colonize=True,
                min_colony_fields=300,
                max_planets=11,
            ),
            manual_queue=mq_noop,
        )
        self.assertEqual(sent_cmds, ["throttle_building", "spy"])
        self.assertTrue(any(o.get("executed") for o in tick_outcomes))

    def test_undersized_colony_auto_deletion_and_top_queue_colony_priority(self) -> None:
        from dataclasses import replace
        from spaceinvasion.core.model import (
            EnergyBalance,
            QueueJob,
            homeworld_planet_id,
            is_undersized_fresh_colony,
        )

        # Homeworld has 138/230 fields (< 300!), plus a fresh 215-field roll at 02:380:08 and a developed 240-field colony
        homeworld = PlanetState(
            planet_id=12476,
            name="Colony",
            coords="02:380:12",
            planet_type="normal",
            temperature=15,
            fields_used=138,
            fields_max=230,
            resources=Resources(80_000, 80_000, 80_000, 80_000),
            storage=Resources(200_000, 200_000, 200_000, 200_000),
            energy=EnergyBalance(500, 400),
            buildings={"b1": 18, "b2": 14, "b3": 16, "b4": 14, "b5": 16, "b6": 4, "b7": 8, "b8": 6, "b9": 5, "b10": 5},
            ships={"s1": 10, "s8": 1, "s10": 5, "s11": 3},
        )
        fresh_undersized = PlanetState(
            planet_id=12999,
            name="Colony",
            coords="02:380:08",
            planet_type="normal",
            temperature=28,
            fields_used=0,
            fields_max=215,
            resources=Resources(500, 500, 0, 0),
            storage=Resources(100_000, 100_000, 100_000, 100_000),
            energy=EnergyBalance(0, 0),
            buildings={},
            ships={},
            defense={},
        )
        developed_small = replace(
            fresh_undersized,
            planet_id=13000,
            coords="02:380:07",
            fields_used=1,
            buildings={"b1": 1},
        )
        good_colony = replace(
            fresh_undersized,
            planet_id=13001,
            coords="02:380:06",
            fields_max=318,
        )
        st_check = GameState(
            31,
            7,
            "cmdr",
            1_700_000_200.0,
            (homeworld, fresh_undersized, developed_small, good_colony),
            active_planet_id=12476,
            researches={"r1": 4, "r2": 4, "r3": 4, "r4": 8, "r7": 4},
        )
        self.assertEqual(homeworld_planet_id(st_check), 12476)
        self.assertFalse(is_undersized_fresh_colony(homeworld, st_check, 300))
        self.assertFalse(
            is_undersized_fresh_colony(replace(homeworld, fields_used=0, buildings={}, ships={}), st_check, 300)
        )
        self.assertTrue(is_undersized_fresh_colony(fresh_undersized, st_check, 300))
        self.assertFalse(is_undersized_fresh_colony(developed_small, st_check, 300))
        self.assertFalse(is_undersized_fresh_colony(good_colony, st_check, 300))
        self.assertFalse(is_undersized_fresh_colony(replace(fresh_undersized, ships={"s1": 1}), st_check, 300))

        # 2. Both priority and ga_nn put ABANDON_COLONY -> COLONIZE at the VERY TOP of P1 (even with 3/6 fleets in flight
        #    and Thermosolar demolition + raid/recycle/spy available), and NEVER plan buildings on planet 12999!
        in_flight_three = tuple(
            QueueJob(category="fleet", name=f"Attack -> [02:381:0{i}]", remaining_seconds=120.0, detail="Outbound")
            for i in (1, 2, 3)
        )
        raid_targets = (
            {
                "coords": "02:380:09",
                "owner": "",
                "status_color": "free",
                "colonize_min_fields": 140,
                "colonize_max_fields": 340,
                "temperature": 12,
                "last_scanned_ts": 1_700_000_195.0,
            },
            {
                "coords": "02:380:10",
                "owner": "",
                "status_color": "free",
                "last_scanned_ts": 1_700_000_190.0,
            },
            {
                "coords": "02:380:04",
                "owner": "IdlePlayer",
                "status_color": "yellow",
                "is_inactive": True,
                "has_debris": True,
                "has_spy_intel": True,
                "last_spied_ts": 1_700_000_180.0,
                "pig_iron": 50_000,
                "metal": 30_000,
                "kryptonite": 20_000,
                "spice": 10_000,
            },
        )
        st_col_top = GameState(
            31,
            7,
            "cmdr",
            1_700_000_200.0,
            (homeworld, fresh_undersized),
            active_planet_id=12476,
            researches={"r1": 4, "r2": 4, "r3": 4, "r4": 8, "r7": 4},
            active_queues=in_flight_three,
            raid_targets=raid_targets,
        )
        cfg_top = StrategyConfig(
            planner_mode="priority",
            playstyle="fleeter",
            allow_multi_planet=True,
            allow_shipyard=True,
            allow_raids=True,
            allow_galaxy_scan=True,
            allow_colonize=True,
            forbid_planetary_solar=True,
            min_colony_fields=300,
            max_planets=11,
            reserved_spy_slots=1,
        )
        acts_top = plan_actions(st_col_top, config=cfg_top, now_ts=1_700_000_200.0)
        self.assertGreaterEqual(len(acts_top), 3)
        # #0 = ABANDON_COLONY (delete 215-field roll), then ready SPY (fleet priority #1), then COLONIZE -> 02:380:09
        # (fleet priority #2, executable even with 3/6 fleets in flight + 1 reserved spy slot!)
        self.assertEqual(
            (acts_top[0].priority, acts_top[0].kind, acts_top[0].planet_id, acts_top[0].executable_now),
            ("P1", "ABANDON_COLONY", 12999, True),
        )
        col_idx = next(i for i, a in enumerate(acts_top) if a.kind == "COLONIZE")
        self.assertTrue(all(a.kind == "SPY" and a.priority == "P1" for a in acts_top[1:col_idx]))
        self.assertEqual(
            (acts_top[col_idx].priority, acts_top[col_idx].kind, acts_top[col_idx].executable_now),
            ("P1", "COLONIZE", True),
        )
        self.assertIn("02:380:09", acts_top[col_idx].target_name)
        # Zero buildings/ships planned on undersized colony 12999 slated for deletion
        self.assertFalse(
            any(a.planet_id == 12999 and a.kind != "ABANDON_COLONY" for a in acts_top),
        )

        # When 0 Colony ships (s8) are stationed, P1 BUILD_SHIP Colony ship (target_id=8) is emitted at the top of the queue!
        st_need_s8 = replace(st_col_top, planets=(replace(homeworld, ships={"s1": 10, "s10": 5}),))
        acts_s8 = plan_actions(
            st_need_s8,
            config=StrategyConfig(
                planner_mode="priority",
                allow_colonize=True,
                allow_shipyard=True,
                allow_galaxy_scan=True,
                min_colony_fields=300,
                max_planets=11,
            ),
            now_ts=1_700_000_200.0,
        )
        s8_act = next((a for a in acts_s8 if a.kind == "BUILD_SHIP" and a.target_id == 8), None)
        self.assertIsNotNone(s8_act)
        assert s8_act is not None
        self.assertEqual((s8_act.priority, s8_act.executable_now), ("P1", True))

        # 3. _execute_abandon_colony clicks confirmPlanetDeletion -> executeDeletePlanet on internalSettings & blocks Pos. 1!
        settings_before = """
        <html><body>
          <form method="POST">
            <table>
              <tr>
                <td><b>Pos. 1</b></td>
                <td><input name="planet_12476" type="text" value="Colony"/></td>
                <td>02:380:12</td>
                <td>138 / 230 Fields</td>
                <td><a onclick="confirmPlanetDeletion('02:380:12', 12476)">Delete</a></td>
              </tr>
              <tr>
                <td><b>Pos. 2</b></td>
                <td><input name="planet_12999" type="text" value="Colony"/></td>
                <td>02:380:08</td>
                <td>0 / 215 Fields</td>
                <td><a onclick="confirmPlanetDeletion('02:380:08', 12999)">Delete</a></td>
              </tr>
            </table>
          </form>
          <div id="delete-confirm" style="display:block">
            <div onclick="executeDeletePlanet();">delete planet 02:380:08</div>
          </div>
        </body></html>
        """
        settings_after = """
        <html><body>
          <form method="POST">
            <table>
              <tr>
                <td><b>Pos. 1</b></td>
                <td><input name="planet_12476" type="text" value="Colony"/></td>
                <td>02:380:12</td>
                <td>138 / 230 Fields</td>
              </tr>
            </table>
          </form>
        </body></html>
        """
        clicked_els: list[str] = []

        class _FakeEl:
            def __init__(self, oc: str) -> None:
                self._oc = oc

            def get_attribute(self, name: str) -> str:
                return self._oc if name == "onclick" else ""

            def click(self) -> None:
                clicked_els.append(self._oc)

        fake_client = MagicMock()
        fake_client.navigate_menu.return_value = settings_before
        fake_client._wait_frame_ready.return_value = settings_after
        fake_client.read_planets_overview.return_value = replace(st_col_top, planets=(homeworld,))

        def _find_els(_by, sel: str):
            if "confirmPlanetDeletion" in sel:
                return [
                    _FakeEl("confirmPlanetDeletion('02:380:12', 12476)"),
                    _FakeEl("confirmPlanetDeletion('02:380:08', 12999)"),
                ]
            if "executeDeletePlanet" in sel:
                return [_FakeEl("executeDeletePlanet();")]
            return []

        fake_client.driver.find_elements.side_effect = _find_els
        exec_ab = ActionExecutor(dry_run=False, allow_colonize=True, action_delay_range=(0.0, 0.0))
        ab_act = plan_actions(
            st_col_top,
            config=StrategyConfig(allow_colonize=True, min_colony_fields=300, max_planets=11),
            now_ts=1_700_000_200.0,
        )[0]
        res_ab = exec_ab.execute_action(fake_client, st_col_top, ab_act)
        self.assertTrue(res_ab.executed)
        self.assertTrue(res_ab.validated)
        self.assertEqual(clicked_els, ["confirmPlanetDeletion('02:380:08', 12999)", "executeDeletePlanet();"])

        # 3b. Non-interactable/hidden del_trigger + delayed visible #delete-confirm falls back to JS click safely!
        from selenium.common.exceptions import ElementNotInteractableException

        js_clicks: list[str] = []
        modal_state = {"visible": False, "pid": 0, "coords": ""}

        class _HiddenFakeEl:
            def __init__(self, oc: str, *, displayed_fn) -> None:
                self._oc = oc
                self._displayed_fn = displayed_fn

            def get_attribute(self, name: str) -> str:
                return self._oc if name == "onclick" else ""

            def is_displayed(self) -> bool:
                return bool(self._displayed_fn())

            def click(self) -> None:
                raise ElementNotInteractableException("element not interactable")

        def _exec_js(script: str, *args):
            if "arguments[0].click()" in script and args:
                oc = args[0].get_attribute("onclick")
                js_clicks.append(oc)
                if "confirmPlanetDeletion" in oc:
                    modal_state["visible"] = True
                    modal_state["pid"] = 12999
                    modal_state["coords"] = "02:380:08"
                return None
            if "_planet_deleteID" in script:
                return {"pid": modal_state["pid"], "coords": modal_state["coords"]}
            return None

        fake_hidden_client = MagicMock()
        fake_hidden_client.navigate_menu.return_value = settings_before
        fake_hidden_client._wait_frame_ready.return_value = settings_after
        fake_hidden_client.read_planets_overview.return_value = replace(st_col_top, planets=(homeworld,))
        fake_hidden_client.driver.execute_script.side_effect = _exec_js

        def _find_hidden_els(_by, sel: str):
            if "confirmPlanetDeletion" in sel:
                return [
                    _HiddenFakeEl("confirmPlanetDeletion('02:380:12', 12476)", displayed_fn=lambda: False),
                    _HiddenFakeEl("confirmPlanetDeletion('02:380:08', 12999)", displayed_fn=lambda: False),
                ]
            if "executeDeletePlanet" in sel:
                return [_HiddenFakeEl("executeDeletePlanet();", displayed_fn=lambda: modal_state["visible"])]
            return []

        fake_hidden_client.driver.find_elements.side_effect = _find_hidden_els
        res_hidden_ab = exec_ab.execute_action(fake_hidden_client, st_col_top, ab_act)
        self.assertTrue(res_hidden_ab.executed, res_hidden_ab.message)
        self.assertTrue(res_hidden_ab.validated)
        self.assertEqual(js_clicks, ["confirmPlanetDeletion('02:380:08', 12999)", "executeDeletePlanet();"])

        # Attempting to execute ABANDON_COLONY against Homeworld 12476 is strictly blocked!
        bad_hw_act = replace(ab_act, planet_id=12476, planet_coords="02:380:12")
        res_hw = exec_ab.execute_action(fake_client, st_col_top, bad_hw_act)
        self.assertFalse(res_hw.executed)
        self.assertIn("Aborted safety check", res_hw.message)

        # Commander maps ABANDON_COLONY -> {"command": "abandon_colony", ...}
        import importlib.util

        spec_c = importlib.util.spec_from_file_location(
            "commander_cli_ab", Path(__file__).resolve().parents[2] / "bin" / "commander.py"
        )
        assert spec_c is not None and spec_c.loader is not None
        cmd_mod_ab = importlib.util.module_from_spec(spec_c)
        spec_c.loader.exec_module(cmd_mod_ab)
        cmd_payload = cmd_mod_ab._action_to_command_payload(ab_act, dry_run=False)
        self.assertEqual(cmd_payload["command"], "abandon_colony")
        self.assertEqual(cmd_payload["planet_id"], 12999)
        self.assertEqual(cmd_payload["min_colony_fields"], 300)

    def test_notification_code_badge_and_notification_get_lookup(self) -> None:
        with tempfile.TemporaryDirectory(prefix="si_ntf_id_") as tmp_dir:
            tp = Path(tmp_dir)
            bridge = GameApiBridge(
                debug_port=19997,
                config_path=tp / "cfg.json",
                db_path=tp / "ntf.db",
                log_dir=None,
                ensure_session_fn=lambda: None,
            )
            try:
                assert bridge._db is not None
                created = bridge._db.create_or_bump_notification(
                    title="Spy Dispatch Blocked",
                    message="Aborted: target [02:380:08] rejected Spy probe.",
                    help_prompt="Share this notification ID in chat to inspect.",
                    severity="warning",
                    category="action_validation",
                    action_kind="SPY",
                    planet_coords="02:380:12",
                    target_name="02:380:08",
                    details={"raw_error": "This user can't be attacked!"},
                    ts=1_700_000_500.0,
                )
                assert created is not None
                nid = int(created["id"])
                expected_code = f"NTF-{nid:04d}"
                self.assertEqual(created["notification_code"], expected_code)

                # Lookup via POST /command {"command": "notification_get", "notification_id": "NTF-0001"} and "#1"
                res_by_code = bridge.handle_command({"command": "notification_get", "notification_id": expected_code})
                self.assertTrue(res_by_code["ok"])
                self.assertEqual(res_by_code["item"]["notification_code"], expected_code)
                self.assertEqual(res_by_code["item"]["details"]["raw_error"], "This user can't be attacked!")

                res_by_hash = bridge.handle_command({"command": "notification_get", "id": f"#{nid}"})
                self.assertEqual(res_by_hash["item"]["id"], nid)

                # Acknowledge using formatted code "NTF-0001"
                res_ack = bridge.handle_command(
                    {"command": "notification_ack", "notification_id": expected_code, "status": "read"}
                )
                self.assertTrue(res_ack["ok"])
                self.assertEqual(res_ack["item"]["status"], "read")
                self.assertEqual(res_ack["item"]["notification_code"], expected_code)
            finally:
                bridge.close()

    def test_captcha_freeze_stops_all_browser_refreshes_until_confirmed_in_ui(self) -> None:
        from unittest.mock import patch
        from spaceinvasion.session.auth import AntiBotChallengeError

        client_open_count = 0

        class _CountingClientCtx:
            def __enter__(self):
                nonlocal client_open_count
                client_open_count += 1
                raise AntiBotChallengeError("Cloudflare Turnstile challenge active")

            def __exit__(self, *_a):
                return False

        with tempfile.TemporaryDirectory(prefix="si_cap_freeze_") as tmp_dir:
            tp = Path(tmp_dir)
            with patch("spaceinvasion.session.browser.is_debug_port_open", return_value=True):
                bridge = GameApiBridge(
                    debug_port=19996,
                    config_path=tp / "cfg.json",
                    db_path=tp / "cap.db",
                    log_dir=None,
                    client_factory=lambda _p: _CountingClientCtx(),  # type: ignore[arg-type]
                    ensure_session_fn=lambda: None,
                )
                try:
                    # 1. First dashboard(refresh=True) detects CAPTCHA -> enters captcha_blocked=True (1 browser call)
                    dash1 = bridge.dashboard(refresh=True)
                    self.assertEqual(client_open_count, 1)
                    self.assertTrue(dash1["bot_control"]["captcha_blocked"])
                    self.assertTrue(dash1["bot_control"]["paused"])

                    # 2. While captcha_blocked=True, ZERO further browser calls happen across health(),
                    #    dashboard(refresh=True), get_state(), run_galaxy_sweep(), or live commands!
                    h = bridge.health()
                    self.assertFalse(h["in_game"])
                    self.assertTrue(h["bot_control"]["captcha_blocked"])

                    dash2 = bridge.dashboard(refresh=True)
                    self.assertTrue(dash2["bot_control"]["captcha_blocked"])

                    sw = bridge.run_galaxy_sweep(pages=5)
                    self.assertFalse(sw["executed"])
                    self.assertTrue(sw.get("captcha_blocked"))

                    with self.assertRaises(AntiBotChallengeError):
                        bridge.get_state(include_buildings=True)

                    with self.assertRaises(AntiBotChallengeError):
                        bridge.handle_command({"command": "navigate", "action": "internalHome"})

                    self.assertEqual(
                        client_open_count,
                        1,
                        "Expected ZERO Chrome sessions/refreshes while captcha_blocked=True!",
                    )

                    # 3. Only after operator confirms in UI (bot_control paused=False) does browser access resume
                    res_resume = bridge.handle_command(
                        {"command": "bot_control", "paused": False, "captcha_solved": True}
                    )
                    self.assertFalse(res_resume["bot_control"]["captcha_blocked"])
                    self.assertFalse(res_resume["bot_control"]["paused"])
                    bridge.health()
                    self.assertEqual(client_open_count, 2)
                finally:
                    bridge.close()

    def test_parallel_colonize_bounded_by_planet_slots_and_spy_colonize_attack_fleet_priority(self) -> None:
        from dataclasses import replace
        from spaceinvasion.core.model import QueueJob

        now = 1_700_000_300.0
        home = PlanetState(
            planet_id=101,
            name="Home",
            coords="02:380:12",
            planet_type="normal",
            fields_used=40,
            fields_max=330,
            temperature=25,
            resources=Resources(500_000, 500_000, 500_000, 500_000),
            storage=Resources(900_000, 900_000, 900_000, 900_000),
            energy=EnergyBalance(1000, 500),
            buildings={"b1": 12, "b2": 10, "b5": 12, "b9": 6, "b10": 5},  # b10=5 -> 6 fleet slots
            ships={"s8": 3, "s10": 6, "s4": 40, "s1": 20},
        )
        raid_targets = tuple(
            {
                "coords": f"02:38{i}:08",
                "owner": "",
                "status_color": "free",
                "colonize_min_fields": 300,
                "colonize_max_fields": 340 - i,
                "temperature": 20,
                "last_scanned_ts": now - 50.0,
            }
            for i in range(1, 5)
        ) + (
            {  # spied inactive raid target (ATTACK candidate)
                "coords": "02:379:04",
                "owner": "IdleFarm",
                "planet_name": "Farm",
                "status_color": "yellow",
                "is_inactive": True,
                "has_spy_intel": True,
                "last_spied_ts": now - 60.0,
                "resources": {"pig_iron": 90_000, "metal": 60_000, "kryptonite": 40_000, "spice": 20_000},
            },
            {  # unspied inactive (SPY candidate)
                "coords": "02:378:06",
                "owner": "SleepyUser",
                "planet_name": "Colony",
                "status_color": "yellow",
                "is_inactive": True,
                "last_spied_ts": 0.0,
            },
        )
        three_in_flight = tuple(
            QueueJob(category="fleet", name=f"Attack -> [02:370:0{i}]", remaining_seconds=300.0, detail="Outbound")
            for i in (1, 2, 3)
        )
        st = GameState(
            31,
            7,
            "cmdr",
            now,
            (home,),
            active_planet_id=101,
            researches={"r1": 6, "r2": 6, "r3": 6, "r4": 8, "r7": 6},
            raid_targets=raid_targets,
            active_queues=three_in_flight,
        )
        cfg = StrategyConfig(
            planner_mode="priority",
            playstyle="fleeter",
            allow_raids=True,
            allow_galaxy_scan=True,
            allow_colonize=True,
            allow_shipyard=True,
            min_colony_fields=300,
            max_planets=11,
            reserved_spy_slots=1,
        )
        acts = plan_actions(st, config=cfg, now_ts=now)
        cols = [a for a in acts if a.kind == "COLONIZE"]
        # 10 open planet slots, 3 stationed s8 -> 3 distinct parallel COLONIZE targets
        self.assertEqual(len(cols), 3)
        self.assertEqual(len({a.target_name for a in cols}), 3)
        # 3 in flight, 6 slots, 1 reserved for SPY -> dispatch #1 (slot 4) and #2 (slot 5) ready, #3 held for SPY
        ready_cols = [a for a in cols if a.executable_now]
        held_cols = [a for a in cols if not a.executable_now]
        self.assertEqual(len(ready_cols), 2)
        self.assertEqual(len(held_cols), 1)
        self.assertIn("reserved for Spy probes", held_cols[0].reason)
        self.assertIn("Parallel colony", ready_cols[0].reason)
        # SPY (#1) stays executable; ATTACK (#3) is held for the parallel Colony ships
        spies = [a for a in acts if a.kind == "SPY" and "02:378:06" in a.target_name]
        self.assertTrue(spies and spies[0].executable_now)
        atks = [a for a in acts if a.kind == "ATTACK" and "02:379:04" in a.target_name]
        if atks:
            self.assertFalse(atks[0].executable_now)
            self.assertRegex(atks[0].reason, r"reserved for (Colony ship|Spy probes)")
        # Ordering: ready SPY ahead of ready COLONIZE ahead of any ATTACK
        kinds_ready = [a.kind for a in acts if a.executable_now and a.kind in ("SPY", "COLONIZE", "ATTACK")]
        if "SPY" in kinds_ready and "COLONIZE" in kinds_ready:
            first_col = kinds_ready.index("COLONIZE")
            self.assertTrue(all(k != "ATTACK" for k in kinds_ready[:first_col]))
        # Proactive s8 build: 3 stationed < min(10 open planets, 6 slots - 1 spy reserve = 5)
        self.assertTrue(any(a.kind == "BUILD_SHIP" and a.target_id == 8 for a in acts))

        # Only 1 free planet slot -> exactly 1 COLONIZE (never more flights than available planets)
        acts_one = plan_actions(st, config=replace(cfg, max_planets=2), now_ts=now)
        self.assertEqual(len([a for a in acts_one if a.kind == "COLONIZE"]), 1)
        self.assertFalse(any(a.kind == "BUILD_SHIP" and a.target_id == 8 for a in acts_one))

        # 1 free planet slot already consumed by an in-flight colonize mission -> zero COLONIZE
        col_flight = QueueJob(
            category="fleet", name="Colonize -> [02:390:08]", remaining_seconds=500.0, detail="Outbound"
        )
        st_inflight = replace(st, active_queues=three_in_flight + (col_flight,))
        acts_full = plan_actions(st_inflight, config=replace(cfg, max_planets=2), now_ts=now)
        self.assertFalse(any(a.kind == "COLONIZE" for a in acts_full))

    def test_smart_galaxy_sweep_explores_unscanned_first_then_weighted_near_and_frequent_attack_refresh(self) -> None:
        import time as _time
        from unittest.mock import patch
        from spaceinvasion.parsing.models import GalaxySlot

        with tempfile.TemporaryDirectory(prefix="si_smart_sweep_") as tmp:
            tp = Path(tmp)
            visited: list[tuple[int, int]] = []

            class _FakeSweepClient:
                def __enter__(self):
                    return self

                def __exit__(self, *_exc):
                    return None

                def ensure_in_game(self, **_kw):
                    return None

                def step_galaxy_system(self, *, galaxy: int, system: int, **_kw):
                    visited.append((galaxy, system))
                    return _make_galaxy_html(galaxy, system, include_debris=False), galaxy, system

            bridge = GameApiBridge(
                debug_port=19995,
                config_path=tp / "cfg.json",
                db_path=tp / "smart.db",
                log_dir=None,
                client_factory=lambda _p: _FakeSweepClient(),
                ensure_session_fn=lambda: None,
            )
            try:
                assert bridge._db is not None
                bridge._last_state = GameState(
                    31, 7, "cmdr", _time.time(), (_make_planet(101, "02:380:12"),), active_planet_id=101
                )
                now = _time.time()
                # Pre-scan the home system 02:380 and 02:381 (stale) -> exploratory must skip them.
                for s_pre in (380, 381):
                    bridge._db.record_galaxy_scan(2, s_pre, [], ts=now - 7200.0)
                bridge._scanned_systems.update({"02:380": now - 7200.0, "02:381": now - 7200.0})

                # 1. Dry-run: Phase 1 exploratory, smart selection, coverage telemetry + top-5 sample targets
                dry = bridge.handle_command({"command": "galaxy_sweep", "pages": 10, "dry_run": True})
                self.assertTrue(dry["ok"])
                self.assertFalse(dry["executed"])
                self.assertEqual(dry["selection_mode"], "smart")
                self.assertEqual(dry["scan_phase"], "exploratory")
                self.assertEqual(dry["total_systems"], 400)
                self.assertEqual(dry["explored_systems"], 2)
                self.assertAlmostEqual(dry["coverage_pct"], 0.5)
                self.assertEqual(dry["pages_planned"], 10)
                self.assertEqual(len(dry["sample_targets"]), 5)
                planned = dry["planned_targets"]
                self.assertEqual(len(planned), 10)
                self.assertEqual(len(set(planned)), 10)
                self.assertNotIn("02:380", planned)
                self.assertNotIn("02:381", planned)
                # Exploration radiates outward from the colony (nearest unscanned systems first)
                for sk in planned:
                    self.assertTrue(sk.startswith("02:"))
                    self.assertLessEqual(abs(int(sk[3:]) - 380), 8, sk)
                for st_item in dry["sample_targets"]:
                    self.assertEqual(st_item["phase"], "exploratory")
                    self.assertIn("unscanned", st_item["reason"])
                for k in ("scan_phase", "explored_systems", "total_systems", "coverage_pct", "sample_targets"):
                    self.assertIn(k, dry["galaxy_sweep"])

                # 2. Live smart sweep visits ONLY unscanned systems (no duplicates) and updates coverage
                with patch.object(bridge._sweep_stop_event, "wait", return_value=False):
                    live = bridge.run_galaxy_sweep(pages=6, delay_range=(0.0, 0.0))
                self.assertEqual(live["pages_scanned"], 6)
                self.assertEqual(live["selection_mode"], "smart")
                self.assertEqual(live["scan_phase"], "exploratory")
                self.assertEqual(len(visited), 6)
                self.assertEqual(len(set(visited)), 6)
                self.assertTrue(all(v not in ((2, 380), (2, 381)) for v in visited))
                sw = live["galaxy_sweep"]
                self.assertEqual(sw["explored_systems"], 8)
                self.assertAlmostEqual(sw["coverage_pct"], 2.0)
                self.assertEqual(sw["total_sweeps"], 1)
                st_dash = bridge._get_galaxy_sweep_state_locked()
                self.assertEqual(st_dash["explored_systems"], 8)

                # 3. Once the whole galaxy is explored -> Phase 2 targeted refresh favors frequently-raided sectors
                #    (02:200 raided 6x, far away) and near sectors (02:37x..38x) over stale background sectors.
                old_ts = now - 6 * 3600.0
                for s_all in range(1, 401):
                    slots_all = (
                        [GalaxySlot("02:200:05", 5, name="Farm", owner="FarmKing", status_color="yellow")]
                        if s_all == 200
                        else []
                    )
                    bridge._db.record_galaxy_scan(2, s_all, slots_all, ts=old_ts)
                bridge._scanned_systems.update({f"02:{s:03d}": old_ts for s in range(1, 401)})
                for i in range(6):
                    bridge._db.record_raid("02:200:05", attacked_ts=now - 3600.0 * (i + 1), outcome="win")
                bridge._smart_sweep_summary_ts = 0.0

                dry2 = bridge.handle_command({"command": "galaxy_sweep", "pages": 12, "dry_run": True})
                self.assertEqual(dry2["scan_phase"], "targeted_refresh")
                self.assertEqual(dry2["explored_systems"], 400)
                self.assertAlmostEqual(dry2["coverage_pct"], 100.0)
                planned2 = dry2["planned_targets"]
                self.assertIn("02:200", planned2)
                # Near sectors (within 12 systems of the 02:380 colony) fill most of the remaining refresh budget
                near_planned = [sk for sk in planned2 if abs(int(sk[3:]) - 380) <= 12]
                self.assertGreaterEqual(len(near_planned), 8, planned2)
                sample_secs = [s["sector"] for s in dry2["sample_targets"]]
                self.assertIn("02:200", sample_secs)
                farm = next(s for s in dry2["sample_targets"] if s["sector"] == "02:200")
                self.assertGreaterEqual(farm["direct_raid_count"], 6)
                self.assertIn("raided", farm["reason"])
                # Far background sectors (e.g. 02:100) are NOT refreshed ahead of near / frequent-attack sectors
                self.assertNotIn("02:100", planned2)
                self.assertTrue(all(s["phase"] == "targeted_refresh" for s in dry2["sample_targets"]))
                self.assertEqual(bridge._get_galaxy_sweep_state_locked()["scan_phase"], "targeted_refresh")
            finally:
                bridge.close()

    def test_planner_scan_galaxy_prefers_unscanned_then_near_and_frequent_attack_sectors(self) -> None:
        from spaceinvasion.combat.scanner import _next_unscanned_system, select_planner_scan_system

        now = 1_700_100_000.0
        cfg = StrategyConfig(allow_galaxy_scan=True)
        # Home system 02:380 scanned 1h ago (off cooldown but already explored), 02:381 never scanned.
        scanned = {"02:380": now - 3600.0}
        # Legacy GA-simulation hot path (no history) keeps the cheap spiral.
        self.assertEqual(_next_unscanned_system("02:380:12", set()), (2, 380))
        # Smart path explores the never-scanned neighbour first instead of re-scanning 02:380.
        self.assertEqual(
            _next_unscanned_system("02:380:12", set(), scanned_ts=scanned, cfg=cfg, now_ts=now),
            (2, 381),
        )
        pick = select_planner_scan_system("02:380:12", scanned_systems=scanned, cfg=cfg, now_ts=now)
        assert pick is not None
        self.assertEqual(pick["phase"], "exploratory")

        # Every system within radius 15 explored 2h ago -> weighted refresh. 02:390 hosts a planet of a
        # frequently-raided owner (and direct raid history) so it outranks the plain home system 02:380.
        all_scanned = {f"02:{s:03d}": now - 7200.0 for s in range(365, 396)}
        raid_targets = [
            {"coords": "02:390:06", "owner": "FarmKing", "status_color": "yellow", "is_inactive": True},
            {"coords": "02:150:04", "owner": "FarmKing", "status_color": "yellow", "is_inactive": True},
        ]
        raid_stats = {
            "history": [
                {"target_coords": "02:150:04", "attacked_ts": now - 3600.0 * i, "outcome": "win"} for i in range(1, 5)
            ]
            + [{"target_coords": "02:390:06", "attacked_ts": now - 1800.0, "outcome": "win"}]
        }
        pick_ref = select_planner_scan_system(
            "02:380:12",
            scanned_systems=all_scanned,
            raid_targets=raid_targets,
            raid_stats=raid_stats,
            cfg=cfg,
            now_ts=now,
        )
        assert pick_ref is not None
        self.assertEqual((pick_ref["galaxy"], pick_ref["system"], pick_ref["phase"]), (2, 390, "targeted_refresh"))
        self.assertGreater(pick_ref["attack_score"], 0.0)

        # attack_weight=0 + near_weight high -> proximity wins (home system 02:380).
        cfg_near = StrategyConfig(allow_galaxy_scan=True, galaxy_scan_attack_weight=0.0, galaxy_scan_near_weight=10.0)
        pick_near = select_planner_scan_system(
            "02:380:12",
            scanned_systems=all_scanned,
            raid_targets=raid_targets,
            raid_stats=raid_stats,
            cfg=cfg_near,
            now_ts=now,
        )
        assert pick_near is not None
        self.assertEqual((pick_near["galaxy"], pick_near["system"]), (2, 380))

        # Systems on the 30-min scan cooldown are never re-picked.
        fresh_all = {f"02:{s:03d}": now - 60.0 for s in range(1, 401)}
        self.assertIsNone(select_planner_scan_system("02:380:12", scanned_systems=fresh_all, cfg=cfg, now_ts=now))

        # plan_actions (priority planner) emits SCAN_GALAXY on the never-scanned neighbour, tagged as exploration.
        st = GameState(31, 7, "cmdr", now, (_make_planet(101, "02:380:12", ships={"s4": 10}),), active_planet_id=101)
        cfg_m = StrategyConfig(planner_mode="priority", allow_galaxy_scan=True, allow_raids=True)
        acts = plan_actions(st, config=cfg_m, scanned_systems=scanned, now_ts=now)
        scans = [a for a in acts if a.kind == "SCAN_GALAXY"]
        self.assertTrue(scans)
        self.assertEqual(scans[0].target_name, "Galaxy [02:381]")
        self.assertIn("[explore:", scans[0].reason)
        self.assertTrue(scans[0].executable_now)

    def test_espionage_intel_attaches_origin_and_distances(self) -> None:
        import json
        from spaceinvasion.parsing.models import (
            GalaxySlot,
            SpyReport,
        )

        with tempfile.TemporaryDirectory(prefix="si_esp_dist_") as tmp_dir:
            tp = Path(tmp_dir)
            bridge = GameApiBridge(
                debug_port=19992,
                config_path=tp / "cfg.json",
                db_path=tp / "esp.db",
                log_dir=None,
                ensure_session_fn=lambda: None,
            )
            try:
                assert bridge._db is not None
                origin = _make_planet(101, "02:380:12", ships={"s10": 8})
                bridge._last_state = GameState(31, 7, "cmdr", 1_700_000_500.0, (origin,), active_planet_id=101)
                bridge._db.record_galaxy_slots(
                    [
                        GalaxySlot(
                            coords="02:383:02",
                            position=2,
                            name="Spacia",
                            owner="Spacia",
                            status_color="yellow",
                            is_spacia=True,
                        ),
                        GalaxySlot(coords="03:392:05", position=5, name="Far", owner="FarUser", status_color="yellow"),
                    ],
                    ts=1_700_000_400.0,
                )
                bridge._db.record_spy_report(
                    SpyReport(
                        target_coords="02:383:02",
                        planet_name="Spacia",
                        owner="Spacia",
                        is_spacia=True,
                        resources=Resources(24000, 10000, 6000, 1000),
                    ),
                    ts=1_700_000_450.0,
                )
                resp = bridge.handle_command({"command": "espionage_intel"})
                self.assertTrue(resp["ok"])
                self.assertEqual(resp["origin_coords"], "02:380:12")
                by_coords = {t["coords"]: t for t in resp["targets"]}
                self.assertEqual(by_coords["02:383:02"]["system_distance"], 3)
                self.assertEqual(by_coords["02:383:02"]["galaxy_delta"], 0)
                self.assertEqual(by_coords["02:383:02"]["system_delta"], 3)
                self.assertEqual(by_coords["02:383:02"]["position_delta"], 10)
                self.assertAlmostEqual(by_coords["02:383:02"]["distance"], 2985.0)
                self.assertEqual(by_coords["03:392:05"]["galaxy_delta"], 1)
                self.assertEqual(by_coords["03:392:05"]["system_delta"], 12)
                self.assertEqual(by_coords["03:392:05"]["system_distance"], 412)
                self.assertAlmostEqual(by_coords["03:392:05"]["distance"], 20000.0)
                self.assertEqual(resp["reports"][0]["system_distance"], 3)
                self.assertAlmostEqual(resp["reports"][0]["distance"], 2985.0)
                json.dumps(resp, allow_nan=False)
            finally:
                bridge.close()

    def test_smart_sweep_updates_coverage_live_mid_sweep_and_resumes_unscanned_after_abort(self) -> None:
        import time as _time
        from unittest.mock import patch

        with tempfile.TemporaryDirectory(prefix="si_live_cov_") as tmp:
            tp = Path(tmp)
            visited_batch1: list[tuple[int, int]] = []
            visited_batch2: list[tuple[int, int]] = []
            mid_sweep_snapshots: list[tuple[int, float, float]] = []
            active_batch = {"num": 1}

            class _LiveProgressClient:
                def __init__(self, br_ref):
                    self._br = br_ref

                def __enter__(self):
                    return self

                def __exit__(self, *_exc):
                    return None

                def ensure_in_game(self, **_kw):
                    return None

                def step_galaxy_system(self, *, galaxy: int, system: int, **_kw):
                    if active_batch["num"] == 1:
                        visited_batch1.append((galaxy, system))
                        # Snapshot live in-progress summary AFTER page 1 has been persisted
                        if len(visited_batch1) == 2:
                            sm = self._br._smart_sweep_summary or {}
                            mid_sweep_snapshots.append(
                                (
                                    int(sm.get("explored_systems", 0)),
                                    float(sm.get("coverage_pct", 0.0)),
                                    float(sm.get("universe_coverage_pct", 0.0)),
                                )
                            )
                        # Simulate interruption right after page 3 (e.g. reload/pause)
                        if len(visited_batch1) == 3:
                            self._br._bot_control["paused"] = True
                    else:
                        visited_batch2.append((galaxy, system))
                    return _make_galaxy_html(galaxy, system, include_debris=False), galaxy, system

            bridge_holder: list[GameApiBridge] = []
            bridge = GameApiBridge(
                debug_port=19991,
                config_path=tp / "cfg.json",
                db_path=tp / "livecov.db",
                log_dir=None,
                client_factory=lambda _p: _LiveProgressClient(bridge_holder[0]),
                ensure_session_fn=lambda: None,
            )
            bridge_holder.append(bridge)
            try:
                bridge._last_state = GameState(
                    31, 7, "cmdr", _time.time(), (_make_planet(101, "02:380:12"),), active_planet_id=101
                )
                with patch.object(bridge._sweep_stop_event, "wait", return_value=False):
                    r1 = bridge.run_galaxy_sweep(pages=6, delay_range=(0.0, 0.0))
                # Live summary incremented mid-sweep right after page 1 (before batch 1 finished!)
                self.assertEqual(mid_sweep_snapshots, [(1, 0.25, 0.02)])
                self.assertEqual(r1["pages_scanned"], 3)
                self.assertEqual(r1["explored_systems"], 3)
                self.assertAlmostEqual(r1["coverage_pct"], 0.75)

                # Unpause and run next batch -> resumes strictly from remaining unscanned systems (zero overlap!)
                bridge._bot_control["paused"] = False
                active_batch["num"] = 2
                with patch.object(bridge._sweep_stop_event, "wait", return_value=False):
                    r2 = bridge.run_galaxy_sweep(pages=4, delay_range=(0.0, 0.0))
                self.assertEqual(r2["pages_scanned"], 4)
                self.assertEqual(r2["explored_systems"], 7)
                self.assertAlmostEqual(r2["coverage_pct"], 1.75)
                self.assertEqual(set(visited_batch1).intersection(set(visited_batch2)), set())
            finally:
                bridge.close()

    def test_suspend_scans_aborts_active_sweep_and_records_intermediate_arrow_pages(self) -> None:
        import time as _time
        from unittest.mock import patch

        with tempfile.TemporaryDirectory(prefix="si_suspend_arrow_") as tmp:
            tp = Path(tmp)
            cfg_path = tp / "cfg.json"
            cfg_path.write_text('{"allow_galaxy_scan": true, "galaxy_sweep_pages": 10}', encoding="utf-8")
            visited: list[tuple[int, int]] = []
            bridge_holder: list[GameApiBridge] = []

            class _ArrowSuspendClient:
                last_galaxy_nav_method = "arrow"

                def __enter__(self):
                    return self

                def __exit__(self, *_exc):
                    return None

                def ensure_in_game(self, **_kw):
                    return None

                def step_galaxy_system(
                    self,
                    *,
                    galaxy: int,
                    system: int,
                    on_intermediate_page=None,
                    **_kw,
                ):
                    visited.append((galaxy, system))
                    # Simulate a 2-step arrow walk emitting an intermediate page on step 1
                    if len(visited) == 1 and callable(on_intermediate_page):
                        inter_s = ((system - 2) % 400) + 1
                        on_intermediate_page(
                            _make_galaxy_html(galaxy, inter_s, include_debris=True),
                            galaxy,
                            inter_s,
                        )
                    # Simulate operator clicking top-bar "SUSPEND SCANS" after 2nd page
                    if len(visited) == 2:
                        br = bridge_holder[0]
                        br.strategy_config = replace(br.strategy_config, allow_galaxy_scan=False)
                    return _make_galaxy_html(galaxy, system, include_debris=False), galaxy, system

            from dataclasses import replace

            bridge = GameApiBridge(
                debug_port=19992,
                config_path=cfg_path,
                db_path=tp / "suspend.db",
                log_dir=None,
                client_factory=lambda _p: _ArrowSuspendClient(),
                ensure_session_fn=lambda: None,
            )
            bridge_holder.append(bridge)
            try:
                bridge._last_state = GameState(
                    31, 7, "cmdr", _time.time(), (_make_planet(101, "02:380:12"),), active_planet_id=101
                )
                with patch.object(bridge._sweep_stop_event, "wait", return_value=False):
                    res = bridge.run_galaxy_sweep(pages=6, galaxy=2, start_system=380, delay_range=(0.0, 0.0))
                self.assertEqual(res["pages_scanned"], 2)
                self.assertEqual(res["arrow_steps"], 2)
                self.assertIn("suspended", str(res.get("aborted_reason") or "").lower())
                # Both planned pages (380, 381) AND the intermediate arrow page (379) were persisted & XHR-counted!
                scanned_keys = set((bridge._get_scanned_systems() or {}).keys())
                self.assertIn("02:379", scanned_keys)
                self.assertIn("02:380", scanned_keys)
                self.assertIn("02:381", scanned_keys)
                self.assertGreaterEqual(bridge._sweep_views_last_60s(), 3)
            finally:
                bridge.close()


if __name__ == "__main__":
    unittest.main()
