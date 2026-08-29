"""Unit tests for periodic randomized 100-page galaxy sweeps, colony cap gating, and daily free IDT activation."""

from __future__ import annotations

from pathlib import Path
import tempfile
import unittest
from unittest.mock import MagicMock

from spaceinvasion.core.config import StrategyConfig
from spaceinvasion.core.state import (
    EnergyBalance,
    GameState,
    PlanetState,
    Resources,
    count_allocated_normal_planets,
    parse_transmitter_page,
)
from spaceinvasion.db.storage import GameDatabase
from spaceinvasion.planner.strategy import PlannedAction, plan_actions
from spaceinvasion.runtime.api_server import GameApiBridge
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
        planets_at_cap = tuple(
            _make_planet(100 + i, f"02:{370 + i:03d}:07", ptype="normal")
            for i in range(1, 12)
        )
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

            cfg_path = tmp_dir / "cfg.yaml"
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
        from spaceinvasion.core.state import parse_building_queue, parse_home_metadata

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
        from spaceinvasion.combat.fleet import GalaxySlot, SpyReport, parse_galaxy_system_html
        from spaceinvasion.runtime.api_server import ApiRequestError, GameApiBridge

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
            cfg_path = tp / "config.yaml"
            cfg_path.write_text(
                "allow_galaxy_scan: true\nallow_colonize: true\nmax_planets: 4\n"
                "galaxy_scan_click_delay_seconds: 1.8\ngalaxy_scan_click_jitter_seconds: 0.4\n",
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
                    bridge.handle_command({"command": "update_config", "config": {"galaxy_scan_click_delay_seconds": 0.05}})
                with self.assertRaises(ApiRequestError):
                    bridge.handle_command({"command": "update_config", "config": {"galaxy_scan_click_jitter_seconds": -1.0}})

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

                rest = bridge.handle_command({"command": "restore_colony_candidate", "target_coords": "02:380:08"})
                self.assertTrue(rest["ok"])
                self.assertEqual(rest["discarded_count"], 0)
                after_rest = bridge.handle_command({"command": "colony_candidates"})
                self.assertIn("02:380:08", [c["coords"] for c in after_rest["candidates"]])
                self.assertEqual(len(after_rest["candidates"]), len(before_coords))
            finally:
                bridge.close()

    def test_retro_free_slot_ecology_probing_priority_and_noop_batch_continuation(self) -> None:
        from unittest.mock import MagicMock
        import bin.commander as commander_mod
        from spaceinvasion.combat.fleet import GalaxySlot, grade_colony_candidate
        from spaceinvasion.session.executor import ActionExecutor
        from spaceinvasion.runtime.api_server import GameApiBridge

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
        for mode in ("priority", "ga_nn"):
            cfg_m = StrategyConfig(
                planner_mode=mode,
                playstyle="fleeter",
                allow_raids=True,
                allow_galaxy_scan=True,
                allow_colonize=True,
                min_colony_fields=300,
                max_planets=11,
                ga_population_size=6,
                ga_generations=2,
            )
            st_m = GameState(31, 7, "cmdr", 1_700_000_100.0, (origin_p,), active_planet_id=101, raid_targets=raid_targets)
            acts_m = plan_actions(st_m, config=cfg_m, now_ts=1_700_000_100.0)
            spy_acts = [a for a in acts_m if a.kind == "SPY" and a.executable_now]
            self.assertGreaterEqual(len(spy_acts), 2, mode)
            self.assertEqual(spy_acts[0].target_name, "02:380:09 (Free slot)", mode)

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
                config_path=tp10 / "cfg.yaml",
                db_path=tp10 / "v10.db",
                log_dir=None,
                ensure_session_fn=lambda: None,
            )
            try:
                assert bridge10._db is not None
                bridge10._db.record_target_block("02:380:08", blocked_until_ts=9_999_999_999.0, reason="This user can't be attacked!")
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
                    return {"ok": True, "executed": False, "message": "No-op: Blast furnace 100% (resource2) is already at 100%."}
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
        from spaceinvasion.core.state import (
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
        self.assertFalse(is_undersized_fresh_colony(replace(homeworld, fields_used=0, buildings={}, ships={}), st_check, 300))
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
        for mode in ("priority", "ga_nn"):
            cfg_top = StrategyConfig(
                planner_mode=mode,
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
                ga_population_size=6,
                ga_generations=2,
            )
            acts_top = plan_actions(st_col_top, config=cfg_top, now_ts=1_700_000_200.0)
            self.assertGreaterEqual(len(acts_top), 3, mode)
            # #0 = ABANDON_COLONY (delete 215-field roll), #1 = COLONIZE -> 02:380:09 (executable even with 3/6 fleets in flight!)
            self.assertEqual((acts_top[0].priority, acts_top[0].kind, acts_top[0].planet_id, acts_top[0].executable_now), ("P1", "ABANDON_COLONY", 12999, True), mode)
            self.assertEqual((acts_top[1].priority, acts_top[1].kind, acts_top[1].executable_now), ("P1", "COLONIZE", True), mode)
            self.assertIn("02:380:09", acts_top[1].target_name, mode)
            # Zero buildings/ships planned on undersized colony 12999 slated for deletion
            self.assertFalse(
                any(a.planet_id == 12999 and a.kind != "ABANDON_COLONY" for a in acts_top),
                mode,
            )

        # When 0 Colony ships (s8) are stationed, P1 BUILD_SHIP Colony ship (target_id=8) is emitted at the top of the queue!
        st_need_s8 = replace(st_col_top, planets=(replace(homeworld, ships={"s1": 10, "s10": 5}),))
        for mode in ("priority", "ga_nn"):
            acts_s8 = plan_actions(
                st_need_s8,
                config=StrategyConfig(
                    planner_mode=mode,
                    allow_colonize=True,
                    allow_shipyard=True,
                    allow_galaxy_scan=True,
                    min_colony_fields=300,
                    max_planets=11,
                    ga_population_size=6,
                    ga_generations=2,
                ),
                now_ts=1_700_000_200.0,
            )
            s8_act = next((a for a in acts_s8 if a.kind == "BUILD_SHIP" and a.target_id == 8), None)
            self.assertIsNotNone(s8_act, mode)
            assert s8_act is not None
            self.assertEqual((s8_act.priority, s8_act.executable_now), ("P1", True), mode)

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

        # Attempting to execute ABANDON_COLONY against Homeworld 12476 is strictly blocked!
        bad_hw_act = replace(ab_act, planet_id=12476, planet_coords="02:380:12")
        res_hw = exec_ab.execute_action(fake_client, st_col_top, bad_hw_act)
        self.assertFalse(res_hw.executed)
        self.assertIn("Aborted safety check", res_hw.message)

        # Commander maps ABANDON_COLONY -> {"command": "abandon_colony", ...}
        import importlib.util
        spec_c = importlib.util.spec_from_file_location("commander_cli_ab", Path(__file__).resolve().parents[2] / "bin" / "commander.py")
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
                config_path=tp / "cfg.yaml",
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


if __name__ == "__main__":
    unittest.main()
