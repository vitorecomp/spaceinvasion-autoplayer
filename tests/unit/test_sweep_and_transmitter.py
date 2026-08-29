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


if __name__ == "__main__":
    unittest.main()
