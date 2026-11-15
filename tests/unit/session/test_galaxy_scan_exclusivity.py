"""Unit tests for galaxy scan action-exclusivity (`galaxy_scan_block_other_actions`) and P0 Escape preemption."""

from __future__ import annotations

from dataclasses import replace
import io
import json
from pathlib import Path
import tempfile
import threading
import time
import unittest
from unittest.mock import patch
import urllib.error
import urllib.request

import bin.commander as commander_mod
from backend.core.config import (
    StrategyConfig,
    strategy_config_from_dict,
)
from backend.db.config_store import (
    load_strategy_config,
    save_strategy_config,
)
from backend.core.model import (
    BuildingOffer,
    EnergyBalance,
    FleetAlert,
    GameState,
    PlanetState,
    Resources,
)
from backend.runtime.api.http_server import create_api_server
from backend.runtime.api.validation import ApiRequestError
from backend.runtime.bridge import GalaxyScanInProgressError, GameApiBridge
from backend.runtime.bridge._helpers_validation import validate_config_patch as _validate_config_patch


def _make_galaxy_html(galaxy: int, system: int) -> str:
    payload = {
        "position": {"galaxy": galaxy, "system": system},
        "planets": {
            "4": {
                "planet_name": "Farm",
                "nickname": "IdleUser",
                "inactive": 1,
                "rubble": {"metal": 0, "kryptonite": 0},
            }
        },
    }
    return (
        f"<html><body><script>var SI_TPL = {{galaxy: {{maxGalaxies: 8, maxSystem: 400}}}};</script>"
        f'<textarea id="json-element">{json.dumps(payload)}</textarea></body></html>'
    )


def _make_state(*, hostile: bool = False) -> GameState:
    home = PlanetState(
        planet_id=101,
        name="Home",
        coords="02:380:01",
        planet_type="normal",
        fields_used=40,
        fields_max=320,
        temperature=25,
        resources=Resources(50_000, 30_000, 20_000, 15_000),
        storage=Resources(500_000, 500_000, 500_000, 500_000),
        energy=EnergyBalance(800, 400),
        buildings={"b1": 10, "b2": 8, "b5": 10, "b8": 5, "b9": 5, "b10": 3},
        ships={"s2": 10, "s10": 5},
        building_offers=(
            BuildingOffer(
                building_id=1,
                name="Pig iron mine",
                current_level=10,
                target_level=11,
                cost=Resources(1000, 500, 0, 0),
                can_build=True,
                status_text="Ready",
                build_href="indexInternal.es?action=internalBuildings&build=1",
            ),
        ),
    )
    col2 = PlanetState(
        planet_id=102,
        name="Colony 2",
        coords="02:380:02",
        planet_type="normal",
        fields_used=20,
        fields_max=310,
        temperature=15,
        resources=Resources(20_000, 10_000, 10_000, 5_000),
        storage=Resources(500_000, 500_000, 500_000, 500_000),
        energy=EnergyBalance(500, 250),
        buildings={"b1": 8, "b5": 8, "b10": 2},
        ships={},
    )
    alerts = (
        (FleetAlert(mission="ATTACK", hostile=True, summary="Hostile fleet incoming to 02:380:01"),) if hostile else ()
    )
    return GameState(
        universe_id=31,
        user_id=7,
        nickname="cmdr",
        timestamp=time.time(),
        planets=(home, col2),
        active_planet_id=101,
        fleet_alerts=alerts,
    )


class GalaxyScanExclusivityTest(unittest.TestCase):
    def test_config_roundtrip_and_strict_validation(self) -> None:
        default_cfg = StrategyConfig()
        self.assertTrue(default_cfg.galaxy_scan_block_other_actions)

        parsed_off = strategy_config_from_dict({"galaxy_scan_block_other_actions": False})
        self.assertFalse(parsed_off.galaxy_scan_block_other_actions)

        with tempfile.TemporaryDirectory() as tmp_dir:
            tp = Path(tmp_dir)
            json_path = tp / "cfg.json"
            save_strategy_config(parsed_off, json_path)
            self.assertFalse(load_strategy_config(json_path).galaxy_scan_block_other_actions)

            db_path = tp / "cfg.db"
            save_strategy_config(replace(parsed_off, galaxy_scan_block_other_actions=True), db_path)
            self.assertTrue(load_strategy_config(db_path).galaxy_scan_block_other_actions)

        _validate_config_patch({"galaxy_scan_block_other_actions": False}, strict_bools=True)
        with self.assertRaises(ApiRequestError):
            _validate_config_patch({"galaxy_scan_block_other_actions": "yes"}, strict_bools=True)

    def test_active_sweep_blocks_non_sweep_browser_actions_while_serving_cached_reads(self) -> None:
        step1_entered = threading.Event()
        release_step2 = threading.Event()
        non_sweep_browser_opens = 0
        sweep_steps: list[tuple[int, int]] = []

        class _Client:
            last_galaxy_nav_method = "arrow"
            last_galaxy_max_galaxies = 8

            def __init__(self) -> None:
                self._used_for_sweep = False

            def __enter__(self):
                return self

            def __exit__(self, *_exc):
                nonlocal non_sweep_browser_opens
                if not self._used_for_sweep:
                    non_sweep_browser_opens += 1
                return None

            def ensure_in_game(self, **_kw) -> None:
                return None

            def step_galaxy_system(self, *, galaxy: int, system: int, **_kw):
                self._used_for_sweep = True
                sweep_steps.append((galaxy, system))
                if len(sweep_steps) == 1:
                    step1_entered.set()
                return _make_galaxy_html(galaxy, system), galaxy, system

            def navigate_menu(self, *_a, **_kw) -> str:
                return "<html></html>"

        with tempfile.TemporaryDirectory() as tmp_dir:
            tp = Path(tmp_dir)
            cfg_path = tp / "cfg.json"
            save_strategy_config(
                StrategyConfig(
                    allow_galaxy_scan=True,
                    galaxy_scan_block_other_actions=True,
                    galaxy_scan_click_delay_seconds=3.0,
                    galaxy_scan_click_jitter_seconds=0.0,
                ),
                cfg_path,
            )
            with patch("backend.session.browser.is_debug_port_open", return_value=True):
                bridge = GameApiBridge(
                    debug_port=19981,
                    config_path=cfg_path,
                    db_path=tp / "state.db",
                    log_dir=None,
                    client_factory=lambda _p: _Client(),  # type: ignore[arg-type]
                    ensure_session_fn=lambda: None,
                )
                server = create_api_server(bridge, host="127.0.0.1", port=0)
                port = server.server_address[1]
                srv_thread = threading.Thread(target=server.serve_forever, daemon=True)
                srv_thread.start()
                try:
                    bridge.ctx.last_state = _make_state()
                    bridge.ctx.last_snapshot_at = time.monotonic() - 120.0  # older than 15s cache TTL
                    bridge.ctx.last_buildings_fetched = True

                    orig_wait = bridge.ctx.sweep_stop_event.wait

                    def _controlled_wait(timeout=None):
                        if step1_entered.is_set() and not release_step2.is_set():
                            release_step2.wait(timeout=5.0)
                            return bridge.ctx.sweep_stop_event.is_set()
                        return orig_wait(0.0)

                    sweep_out: dict[str, object] = {}

                    def _run_sw() -> None:
                        with patch.object(bridge.ctx.sweep_stop_event, "wait", side_effect=_controlled_wait):
                            sweep_out["res"] = bridge.run_galaxy_sweep(
                                pages=2, galaxy=2, start_system=380, delay_range=(1.0, 1.0)
                            )

                    t_sw = threading.Thread(target=_run_sw, daemon=True)
                    t_sw.start()
                    self.assertTrue(step1_entered.wait(timeout=5.0))
                    time.sleep(0.05)

                    # 1. /bot_status & /health report active sweep without touching Chrome
                    bot_st = bridge.get_bot_control()
                    self.assertTrue(bot_st["galaxy_sweep_in_progress"])
                    self.assertTrue(bot_st["galaxy_scan_blocking"])
                    self.assertTrue(bot_st["galaxy_sweep"]["in_progress"])
                    self.assertTrue(bot_st["galaxy_sweep"]["blocking_other_actions"])

                    h_st = bridge.health()
                    self.assertTrue(h_st["in_game"])
                    self.assertTrue(h_st["galaxy_sweep_in_progress"])

                    # 2. Cached state, dry-run commands, queue_add, and dashboard(refresh=True) never touch Chrome
                    st_cached = bridge.get_state(include_buildings=True)
                    self.assertTrue(st_cached.get("sweep_in_progress"))

                    dash_ref = bridge.dashboard(refresh=True)
                    self.assertTrue(dash_ref.get("sweep_deferred_refresh"))
                    self.assertIn("galaxy scan in progress", str(dash_ref.get("sweep_message", "")).lower())

                    dash_poll = bridge.dashboard(refresh=False)
                    self.assertFalse(dash_poll.get("sweep_deferred_refresh", False))

                    dry_bld = bridge.handle_command(
                        {"command": "build", "building_id": 1, "planet_id": 101, "dry_run": True}
                    )
                    self.assertTrue(dry_bld["ok"])
                    self.assertTrue(dry_bld["dry_run"])

                    q_res = bridge.handle_command(
                        {"command": "queue_add", "kind": "BUILD", "target_id": 1, "planet_id": 101}
                    )
                    self.assertTrue(q_res["ok"])

                    # 3. Live browser mutations and live refreshes raise HTTP 409 Conflict with sweep_blocked=True
                    for bad_cmd in (
                        {"command": "build", "building_id": 1, "planet_id": 101, "dry_run": False},
                        {"command": "navigate", "action": "internalBuildings"},
                        {"command": "combat_overview", "planet_id": 101, "refresh": True},
                        {"command": "ensure_session"},
                        {"command": "universe_research_max", "sync": True},
                    ):
                        with self.assertRaises(GalaxyScanInProgressError):
                            bridge.handle_command(bad_cmd)

                    req = urllib.request.Request(
                        f"http://127.0.0.1:{port}/command",
                        data=json.dumps({"command": "navigate", "action": "internalHome"}).encode("utf-8"),
                        headers={"Content-Type": "application/json"},
                        method="POST",
                    )
                    with self.assertRaises(urllib.error.HTTPError) as ctx:
                        urllib.request.urlopen(req, timeout=5.0)
                    self.assertEqual(ctx.exception.code, 409)
                    err_body = json.loads(ctx.exception.read().decode("utf-8"))
                    ctx.exception.close()
                    self.assertTrue(err_body["sweep_blocked"])
                    self.assertIn("galaxy scan in progress", err_body["error"].lower())

                    self.assertEqual(non_sweep_browser_opens, 0)

                    # Release sweep step 2 and verify sweep completes both pages cleanly
                    release_step2.set()
                    t_sw.join(timeout=5.0)
                    res_final = sweep_out.get("res")
                    assert isinstance(res_final, dict)
                    self.assertEqual(res_final["pages_scanned"], 2)
                    self.assertFalse(bridge.get_bot_control()["galaxy_scan_blocking"])
                finally:
                    release_step2.set()
                    server.shutdown()
                    server.server_close()
                    bridge.close()

    def test_p0_escape_flight_preempts_active_galaxy_sweep_immediately(self) -> None:
        step1_done = threading.Event()
        escape_executed: list[str] = []

        class _PreemptClient:
            last_galaxy_nav_method = "arrow"
            last_galaxy_max_galaxies = 8
            driver = type("_Drv", (), {"current_url": "https://spaceinvasion.bitmeup.com/indexGame.es"})()

            def __enter__(self):
                return self

            def __exit__(self, *_exc):
                return None

            def ensure_in_game(self, **_kw) -> None:
                return None

            def step_galaxy_system(self, *, galaxy: int, system: int, **_kw):
                step1_done.set()
                return _make_galaxy_html(galaxy, system), galaxy, system

        with tempfile.TemporaryDirectory() as tmp_dir:
            tp = Path(tmp_dir)
            cfg_path = tp / "cfg.json"
            save_strategy_config(
                StrategyConfig(
                    allow_galaxy_scan=True,
                    galaxy_scan_block_other_actions=True,
                    allow_fleetsave=True,
                    galaxy_scan_click_delay_seconds=8.0,
                    galaxy_scan_click_jitter_seconds=0.0,
                ),
                cfg_path,
            )
            with patch("backend.session.browser.is_debug_port_open", return_value=True):
                bridge = GameApiBridge(
                    debug_port=19982,
                    config_path=cfg_path,
                    db_path=tp / "preempt.db",
                    log_dir=None,
                    client_factory=lambda _p: _PreemptClient(),  # type: ignore[arg-type]
                    ensure_session_fn=lambda: None,
                )
                try:
                    bridge.ctx.last_state = _make_state(hostile=False)
                    bridge.ctx.last_snapshot_at = time.monotonic()

                    def _fake_exec_summary(cmd, client, state, planned, *, dry_run, **_kw):
                        escape_executed.append(f"{cmd}:{planned.kind}:{client is not None}")
                        return {
                            "ok": True,
                            "command": cmd,
                            "executed": True,
                            "dry_run": dry_run,
                            "message": "Launched Escape Flight.",
                        }

                    sweep_holder: dict[str, object] = {}

                    def _run_sw() -> None:
                        sweep_holder["res"] = bridge.run_galaxy_sweep(
                            pages=5, galaxy=2, start_system=380, delay_range=(5.0, 5.0)
                        )

                    t_sw = threading.Thread(target=_run_sw, daemon=True)
                    t_sw.start()
                    self.assertTrue(step1_done.wait(timeout=5.0))
                    time.sleep(0.05)

                    t0 = time.monotonic()
                    with patch.object(bridge.execution, "execute_and_summarize", side_effect=_fake_exec_summary):
                        esc_res = bridge.handle_command(
                            {"command": "escape_flight", "planet_id": 101, "dry_run": False}
                        )
                    elapsed = time.monotonic() - t0
                    self.assertTrue(esc_res["executed"])
                    self.assertEqual(escape_executed, ["escape_flight:ESCAPE_FLIGHT:True"])
                    # Preempted the 5.0s pacing wait in well under 1.5s!
                    self.assertLess(elapsed, 1.5)

                    t_sw.join(timeout=5.0)
                    sw_res = sweep_holder.get("res")
                    assert isinstance(sw_res, dict)
                    self.assertEqual(sw_res["pages_scanned"], 1)
                    self.assertIn("escape flight", str(sw_res.get("aborted_reason") or "").lower())
                finally:
                    bridge.close()

    def test_commander_defers_during_galaxy_scan_unless_p0_escape_ready(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            tp = Path(tmp_dir)
            cfg_path = tp / "cfg.json"
            save_strategy_config(
                StrategyConfig(
                    allow_galaxy_scan=True,
                    galaxy_scan_block_other_actions=True,
                    allow_fleetsave=True,
                ),
                cfg_path,
            )

            state_calls = 0

            class _FakeApiBlocking:
                def __init__(self, *, p0_ready: bool) -> None:
                    self._p0_ready = p0_ready

                def get_bot_control(self):
                    return {
                        "ok": True,
                        "paused": False,
                        "captcha_blocked": False,
                        "galaxy_sweep_in_progress": True,
                        "galaxy_scan_blocking": True,
                        "p0_escape_flight_ready": self._p0_ready,
                        "galaxy_sweep": {"in_progress": True, "blocking_other_actions": True},
                    }

                def get_state(self, **_kw):
                    nonlocal state_calls
                    state_calls += 1
                    return _make_state(hostile=self._p0_ready)

                def send_command(self, payload):
                    return {
                        "ok": True,
                        "command": payload.get("command"),
                        "executed": True,
                        "dry_run": False,
                        "message": "Executed P0 escape",
                    }

                def close(self) -> None:
                    return None

            # 1. Normal scan in progress (no P0 hostile attack) -> Commander defers with 0 get_state calls
            buf = io.StringIO()
            with (
                patch.object(commander_mod, "GameApiClient", return_value=_FakeApiBlocking(p0_ready=False)),
                patch("sys.stdout", buf),
            ):
                rc = commander_mod.main(
                    [
                        "--live",
                        "--loop",
                        "--max-ticks",
                        "1",
                        "--interval",
                        "0.02",
                        "--config",
                        str(cfg_path),
                        "--log-dir",
                        "",
                    ]
                )
            self.assertEqual(rc, 0)
            self.assertEqual(state_calls, 0)
            self.assertIn("Galaxy scan in progress; deferring actions", buf.getvalue())

            # 2. P0 Escape Flight ready -> Commander runs immediately despite galaxy_scan_blocking=True
            buf_p0 = io.StringIO()
            with (
                patch.object(commander_mod, "GameApiClient", return_value=_FakeApiBlocking(p0_ready=True)),
                patch("sys.stdout", buf_p0),
            ):
                rc_p0 = commander_mod.main(
                    [
                        "--live",
                        "--loop",
                        "--max-ticks",
                        "1",
                        "--interval",
                        "0.02",
                        "--config",
                        str(cfg_path),
                        "--log-dir",
                        "",
                    ]
                )
            self.assertEqual(rc_p0, 0)
            self.assertGreaterEqual(state_calls, 1)
            self.assertIn("ESCAPE_FLIGHT", buf_p0.getvalue())


if __name__ == "__main__":
    unittest.main()
