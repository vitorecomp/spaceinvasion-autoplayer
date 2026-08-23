"""Unit tests for run_bot debug-port lifecycle and session recovery."""

from __future__ import annotations

from pathlib import Path
import unittest
from unittest.mock import MagicMock, patch

import run_bot
from spaceinvasion.auth import Credentials, SpaceInvasionAuthError


class TestRunnerDebugPortLifecycle(unittest.TestCase):
    def test_launches_detached_debug_browser_when_port_closed(self) -> None:
        creds = Credentials(email="pilot@example.com", password="secret", universe="RETRO")
        with (
            patch("run_bot.is_debug_port_open", return_value=False),
            patch("run_bot.load_credentials", return_value=creds),
            patch("run_bot.login_with_browser") as mock_login,
        ):
            run_bot.ensure_debug_browser_ready(
                debug_port=9222, env_file=Path(".env"), account="default"
            )
        mock_login.assert_called_once()
        cfg = mock_login.call_args.kwargs["config"]
        self.assertEqual(cfg.debug_port, 9222)
        self.assertTrue(cfg.detach)
        self.assertFalse(cfg.attach)

    def test_recovers_session_via_attach_when_kicked_out_of_game(self) -> None:
        creds = Credentials(email="pilot@example.com", password="secret", universe="RETRO")
        fake_client = MagicMock()
        fake_client.__enter__.return_value = fake_client
        fake_client.ensure_in_game.side_effect = SpaceInvasionAuthError("not in game")

        with (
            patch("run_bot.is_debug_port_open", return_value=True),
            patch("run_bot.GameBrowserClient.attach", return_value=fake_client),
            patch("run_bot.load_credentials", return_value=creds),
            patch("run_bot.login_with_browser") as mock_login,
        ):
            run_bot.ensure_debug_browser_ready(
                debug_port=9222, env_file=Path(".env"), account="default"
            )
        mock_login.assert_called_once()
        cfg = mock_login.call_args.kwargs["config"]
        self.assertEqual(cfg.debug_port, 9222)
        self.assertTrue(cfg.attach)
        self.assertTrue(cfg.detach)

    def test_run_stack_stop_proc_kills_sigterm_ignoring_child_and_wait_health_early_exit(self) -> None:
        import subprocess
        import sys
        import tempfile
        import time
        import run_stack

        with tempfile.TemporaryDirectory() as tmp:
            ready_file = Path(tmp) / "ready.txt"
            child_code = (
                "import signal, time; "
                "signal.signal(signal.SIGTERM, signal.SIG_IGN); "
                f"open({str(ready_file)!r}, 'w', encoding='utf-8').write('1'); "
                "time.sleep(60)"
            )
            proc = subprocess.Popen(
                [sys.executable, "-c", child_code],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            try:
                deadline = time.monotonic() + 5.0
                while not ready_file.exists() and time.monotonic() < deadline:
                    time.sleep(0.05)
                self.assertTrue(ready_file.exists())
                run_stack._stop_proc(proc)
                self.assertIsNotNone(proc.poll())
            finally:
                if proc.poll() is None:
                    proc.kill()
                proc.wait(timeout=5.0)

            dead_proc = subprocess.Popen(
                [sys.executable, "-c", "raise SystemExit(2)"],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            try:
                dead_proc.wait(timeout=5.0)
                run_stack._stop_proc(dead_proc)
                self.assertEqual(dead_proc.returncode, 2)
                self.assertFalse(run_stack._wait_api_health("http://127.0.0.1:1", timeout=5.0, proc=dead_proc))
            finally:
                dead_proc.wait(timeout=5.0)

            grace_ready = Path(tmp) / "grace.txt"
            grace_code = (
                "import signal, sys, time; "
                "signal.signal(signal.SIGTERM, lambda *_: sys.exit(0)); "
                f"open({str(grace_ready)!r}, 'w', encoding='utf-8').write('1'); "
                "time.sleep(60)"
            )
            grace_proc = subprocess.Popen(
                [sys.executable, "-c", grace_code],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            try:
                deadline = time.monotonic() + 5.0
                while not grace_ready.exists() and time.monotonic() < deadline:
                    time.sleep(0.05)
                self.assertTrue(grace_ready.exists())
                run_stack._stop_proc(grace_proc)
                self.assertIsNotNone(grace_proc.poll())
            finally:
                if grace_proc.poll() is None:
                    grace_proc.kill()
                grace_proc.wait(timeout=5.0)

    def test_run_stack_and_game_api_main_redaction_reload_sigterm_and_cleanup(self) -> None:
        import io
        import signal
        import tempfile
        import game_api
        import run_stack
        from spaceinvasion.api_server import GameApiBridge

        before_sig = signal.getsignal(signal.SIGTERM)
        with patch("sys.stdout", new_callable=io.StringIO):
            with self.assertRaises(SystemExit) as cm_rs:
                run_stack.main(["--help"])
            self.assertEqual(cm_rs.exception.code, 0)
            with self.assertRaises(SystemExit) as cm_ga:
                game_api.main(["--help"])
            self.assertEqual(cm_ga.exception.code, 0)

        err_buf = io.StringIO()
        with (
            patch("run_stack.ensure_debug_browser_ready", side_effect=SpaceInvasionAuthError("bad ?sid=topsecret_stack_99")),
            patch("sys.stderr", err_buf),
        ):
            rc = run_stack.main(["--debug-port", "9222"])
        self.assertEqual(rc, 1)
        self.assertNotIn("topsecret_stack_99", err_buf.getvalue())
        self.assertIn("REDACTED", err_buf.getvalue())
        self.assertEqual(signal.getsignal(signal.SIGTERM), before_sig)

        # run_stack interrupt cleanup calls _stop_proc and restores SIGTERM
        proc1 = MagicMock()
        proc2 = MagicMock()
        with (
            patch("run_stack.ensure_debug_browser_ready", return_value=None),
            patch("run_stack.subprocess.Popen", side_effect=[proc1, proc2]),
            patch("run_stack._wait_api_health", return_value=True),
            patch("run_stack.time.sleep", side_effect=KeyboardInterrupt),
            patch("run_stack._stop_proc") as mock_stop,
        ):
            rc_int = run_stack.main(["--debug-port", "9222", "--api-port", "8787"])
        self.assertEqual(rc_int, 0)
        self.assertEqual(mock_stop.call_count, 2)
        self.assertEqual(signal.getsignal(signal.SIGTERM), before_sig)

        # game_api --reload delegation + auth redaction + clean server/DB/SIGTERM shutdown
        with (
            patch("game_api.should_spawn_reloader", return_value=True),
            patch("game_api.watch_and_run", return_value=0) as mock_watch,
        ):
            self.assertEqual(game_api.main(["--reload"]), 0)
            mock_watch.assert_called_once()

        api_err = io.StringIO()
        with (
            patch("game_api.ensure_debug_browser_ready", side_effect=SpaceInvasionAuthError("bad ?sid=topsecret_api_88")),
            patch("sys.stderr", api_err),
        ):
            rc_api_err = game_api.main(["--debug-port", "9222"])
        self.assertEqual(rc_api_err, 1)
        self.assertNotIn("topsecret_api_88", api_err.getvalue())
        self.assertIn("REDACTED", api_err.getvalue())

        with tempfile.TemporaryDirectory() as tmp:
            tp = Path(tmp)
            cfg_p = tp / "cfg.yaml"
            cfg_p.write_text("planner_mode: priority\n", encoding="utf-8")
            created_bridges: list[GameApiBridge] = []

            def _isolated_bridge(*args, **kwargs):
                kwargs["db_path"] = tp / "test_api.db"
                b = GameApiBridge(*args, **kwargs)
                created_bridges.append(b)
                return b

            real_create_api_server = game_api.create_api_server

            def _interrupt_after_bind(bridge_obj, *, host: str, port: int):
                srv = real_create_api_server(bridge_obj, host=host, port=port)
                srv.serve_forever = MagicMock(side_effect=KeyboardInterrupt)  # type: ignore[method-assign]
                return srv

            with (
                patch("game_api.ensure_debug_browser_ready", return_value=None),
                patch("game_api.GameApiBridge", side_effect=_isolated_bridge),
                patch("game_api.create_api_server", side_effect=_interrupt_after_bind),
                patch("sys.stdout", new_callable=io.StringIO),
            ):
                rc_srv = game_api.main(
                    ["--host", "127.0.0.1", "--port", "0", "--config", str(cfg_p), "--log-dir", str(tp / "logs")]
                )
            self.assertEqual(rc_srv, 0)
            self.assertEqual(len(created_bridges), 1)
            assert created_bridges[0]._db is not None
            with self.assertRaises(Exception):
                created_bridges[0]._db._conn.execute("SELECT 1")
            self.assertEqual(signal.getsignal(signal.SIGTERM), before_sig)

    def test_r7_advisor_run_bot_run_stack_and_logging_redaction_and_bounds(self) -> None:
        import io
        import json
        import math
        import tempfile
        import advisor
        import run_stack
        from spaceinvasion.client import RateLimitError
        from spaceinvasion.logging_utils import append_tick_logs
        from spaceinvasion.state import (
            EnergyBalance,
            GameState,
            PlanetState,
            Resources,
            TutorialStatus,
            TutorialTask,
        )
        from spaceinvasion.strategy import PlannedAction

        secret = "SECRET_RUNNER_SID_999"
        planet = PlanetState(
            planet_id=10**5000,
            name=f"Colony ?sid={secret}",
            coords=f"01:001:01 ?sid={secret}",
            planet_type="normal",
            temperature=25,
            fields_used=10**5000,
            fields_max=200,
            resources=Resources(10**5000, 200, 300, 400),
            storage=Resources(100_000, 100_000, 100_000, 100_000),
            energy=EnergyBalance(10**5000, 50),
            buildings={"b1": 10**5000},
        )
        state = GameState(
            universe_id=10**5000,
            user_id=1,
            nickname=f"Nick ?sid={secret}",
            timestamp=1_700_000_000.0,
            planets=(planet,),
            active_planet_id=10**5000,
            speedup_factor=float("nan"),
            unread_messages={"spy": 10**5000},
            tutorial=TutorialStatus(
                step=10**5000,
                total_steps=11,
                tasks=(TutorialTask(description=f"Task ?sid={secret}", current=0, target=1),),
                reward_text=f"Reward ?sid={secret}",
            ),
        )
        act = PlannedAction(
            priority="P2",
            kind="BUILD",
            planet_id=10**5000,
            planet_coords="01:001:01",
            target_id=1,
            target_level=10**5000,
            target_name=f"Mine ?sid={secret}",
            reason=f"Need ?sid={secret}",
            executable_now=False,
        )

        def _reject_nonfinite(tok: str) -> object:
            raise ValueError(tok)

        with tempfile.TemporaryDirectory() as tmp:
            tp = Path(tmp)
            snap_out = tp / "adv_snap.json"
            fake_cli = MagicMock()
            fake_cli.__enter__.return_value = fake_cli
            fake_cli.read_full_snapshot.return_value = state
            out_json = io.StringIO()
            with (
                patch("advisor.GameBrowserClient.attach", return_value=fake_cli),
                patch("advisor.plan_actions", return_value=[act]),
                patch("sys.stdout", out_json),
            ):
                rc_adv_j = advisor.main(["--json", "--snapshot-out", str(snap_out)])
            self.assertEqual(rc_adv_j, 0)
            self.assertNotIn(secret, out_json.getvalue())
            self.assertNotIn(secret, snap_out.read_text(encoding="utf-8"))
            parsed_j = json.loads(out_json.getvalue(), parse_constant=_reject_nonfinite)
            self.assertIn("state", parsed_j)

            out_txt = io.StringIO()
            with (
                patch("advisor.GameBrowserClient.attach", return_value=fake_cli),
                patch("advisor.plan_actions", return_value=[act]),
                patch("sys.stdout", out_txt),
            ):
                rc_adv_t = advisor.main([])
            self.assertEqual(rc_adv_t, 0)
            self.assertNotIn(secret, out_txt.getvalue())
            self.assertIn("~1e5000", out_txt.getvalue())

            # logging_utils.append_tick_logs handles 10**5000 integer message cleanly
            jl, tl = append_tick_logs(
                tp / "logs",
                source="bot",
                tick=1,
                mode="DRY-RUN",
                state=state,
                actual_action=act,
                actual_result={"message": 10**5000},
                next_action=None,
            )
            self.assertTrue(jl.exists() and tl.exists())
            self.assertNotIn(secret, tl.read_text(encoding="utf-8"))

        # run_bot clamps --interval inf and respects RateLimitError retry_after & redacts sid=
        sleeps: list[float] = []
        bot_out = io.StringIO()
        with (
            patch("run_bot.ensure_debug_browser_ready", return_value=None),
            patch("run_bot.GameBrowserClient.attach", side_effect=RateLimitError(f"throttle ?sid={secret}", retry_after=90.0)),
            patch("run_bot.time.sleep", side_effect=lambda s: sleeps.append(float(s))),
            patch("sys.stdout", bot_out),
        ):
            rc_bot = run_bot.main(["--loop", "--interval", "inf", "--max-ticks", "1"])
        self.assertEqual(rc_bot, 0)
        self.assertNotIn(secret, bot_out.getvalue())
        self.assertTrue(all(math.isfinite(s) and s <= 86400.0 for s in sleeps))
        self.assertIn(90.0, sleeps)

        # run_stack clamps --interval inf before forwarding to commander.py
        popen_cmds: list[list[str]] = []
        p_mock1, p_mock2 = MagicMock(), MagicMock()
        p_mock1.poll.return_value = 0
        p_mock2.poll.return_value = 0

        def _capture_popen(cmd: list[str], **_kw: object) -> MagicMock:
            popen_cmds.append(list(cmd))
            return p_mock1 if len(popen_cmds) == 1 else p_mock2

        with (
            patch("run_stack.ensure_debug_browser_ready", return_value=None),
            patch("run_stack.subprocess.Popen", side_effect=_capture_popen),
            patch("run_stack._wait_api_health", return_value=True),
            patch("run_stack.time.sleep", return_value=None),
            patch("sys.stdout", new_callable=io.StringIO),
        ):
            rc_rs = run_stack.main(["--interval", "inf"])
        self.assertEqual(rc_rs, 0)
        cmd_line = popen_cmds[1]
        iv_idx = cmd_line.index("--interval") + 1
        self.assertTrue(math.isfinite(float(cmd_line[iv_idx])))
        self.assertEqual(float(cmd_line[iv_idx]), 20.0)

    def test_r8_append_tick_logs_and_api_log_huge_10e5000_tick_and_status(self) -> None:
        import json
        import tempfile
        from spaceinvasion.logging_utils import append_api_log, append_tick_logs
        from spaceinvasion.state import EnergyBalance, GameState, PlanetState, Resources

        pl = PlanetState(
            planet_id=1,
            name="Home",
            coords="01:001:01",
            planet_type="normal",
            temperature=25,
            fields_used=10,
            fields_max=200,
            resources=Resources(100, 200, 300, 400),
            storage=Resources(100_000, 100_000, 100_000, 100_000),
            energy=EnergyBalance(100, 50),
        )
        gs = GameState(
            universe_id=31,
            user_id=1,
            nickname="u",
            timestamp=1_700_000_000.0,
            planets=(pl,),
            active_planet_id=1,
        )
        with tempfile.TemporaryDirectory() as tmp:
            log_dir = Path(tmp) / "logs"
            jl, tl = append_tick_logs(
                log_dir,
                source="bot",
                tick=10**5000,
                mode="DRY",
                state=gs,
                actual_action=None,
                actual_result={"message": "ok"},
                next_action=None,
            )
            self.assertTrue(jl.exists() and tl.exists())
            parsed = json.loads(jl.read_text(encoding="utf-8").strip())
            self.assertIsInstance(parsed["tick"], int)
            self.assertIn("[tick ~1e5000 | DRY]", tl.read_text(encoding="utf-8"))

            ajl, atl = append_api_log(
                log_dir,
                method="POST",
                path="/command",
                status=10**5000,  # type: ignore[arg-type]
                payload={"command": "plan"},
                response={"ok": True, "message": "ok"},
            )
            self.assertTrue(ajl.exists() and atl.exists())
            self.assertIn("-> ~1e5000", atl.read_text(encoding="utf-8"))

    def test_r9_run_bot_rate_limit_and_append_api_log_method_redaction(self) -> None:
        import io
        import tempfile
        from spaceinvasion.client import RateLimitError
        from spaceinvasion.logging_utils import append_api_log

        secret = "R9_RUNBOT_SID_777"
        fake_rl = MagicMock()
        fake_rl.__enter__.return_value = fake_rl
        fake_rl.ensure_in_game.side_effect = RateLimitError(f"throttle ?sid={secret}", retry_after=55.0)

        with (
            patch("run_bot.is_debug_port_open", return_value=True),
            patch("run_bot.GameBrowserClient.attach", return_value=fake_rl),
            patch("run_bot.load_credentials") as mock_creds,
            patch("run_bot.login_with_browser") as mock_login,
        ):
            with self.assertRaises(RateLimitError):
                run_bot.ensure_debug_browser_ready(
                    debug_port=19998, env_file=Path(".env"), account="default"
                )
        mock_creds.assert_not_called()
        mock_login.assert_not_called()

        # Startup RateLimitError in --once returns 1 without login_with_browser
        with tempfile.TemporaryDirectory() as tmp:
            tp = Path(tmp)
            env_p = tp / "dummy.env"
            env_p.write_text("", encoding="utf-8")
            out_once = io.StringIO()
            with (
                patch("run_bot.is_debug_port_open", return_value=True),
                patch("run_bot.GameBrowserClient.attach", return_value=fake_rl),
                patch("run_bot.login_with_browser") as mock_login_once,
                patch("sys.stdout", out_once),
            ):
                rc_once = run_bot.main(
                    ["--once", "--debug-port", "19998", "--env-file", str(env_p), "--log-dir", str(tp / "logs")]
                )
            self.assertEqual(rc_once, 1)
            mock_login_once.assert_not_called()
            self.assertNotIn(secret, out_once.getvalue())

            # Startup RateLimitError in --loop backs off cleanly and recovers on next tick
            ensure_calls: list[int] = []

            def _ensure_startup_then_ok(**_kwargs: object) -> None:
                ensure_calls.append(1)
                if len(ensure_calls) == 1:
                    raise RateLimitError(f"startup throttle ?sid={secret}", retry_after=52.0)

            sleeps: list[float] = []
            out_loop = io.StringIO()
            with (
                patch("run_bot.ensure_debug_browser_ready", side_effect=_ensure_startup_then_ok),
                patch("run_bot.GameBrowserClient.attach", side_effect=RateLimitError("tick throttle", retry_after=48.0)),
                patch("run_bot.time.sleep", side_effect=lambda s: sleeps.append(float(s))),
                patch("sys.stdout", out_loop),
            ):
                rc_loop = run_bot.main(
                    ["--loop", "--max-ticks", "2", "--interval", "10", "--debug-port", "19998", "--env-file", str(env_p), "--log-dir", str(tp / "logs")]
                )
            self.assertEqual(rc_loop, 0)
            self.assertEqual(len(ensure_calls), 2)
            self.assertIn(52.0, sleeps)
            self.assertIn(48.0, sleeps)
            self.assertNotIn(secret, out_loop.getvalue())

            # Recovery ensure_debug_browser_ready hitting RateLimitError sleeps cleanly instead of crashing
            rec_sleeps: list[float] = []
            out_rec = io.StringIO()
            rec_calls: list[int] = []

            def _ensure_rec(**_kwargs: object) -> None:
                rec_calls.append(1)
                if len(rec_calls) >= 2:
                    raise RateLimitError(f"recovery throttle ?sid={secret}", retry_after=61.0)

            with (
                patch("run_bot.ensure_debug_browser_ready", side_effect=_ensure_rec),
                patch("run_bot.GameBrowserClient.attach", side_effect=SpaceInvasionAuthError(f"lost ?sid={secret}")),
                patch("run_bot.time.sleep", side_effect=lambda s: rec_sleeps.append(float(s))),
                patch("sys.stdout", out_rec),
            ):
                rc_rec = run_bot.main(
                    ["--loop", "--max-ticks", "1", "--interval", "10", "--debug-port", "19998", "--env-file", str(env_p), "--log-dir", str(tp / "logs")]
                )
            self.assertEqual(rc_rec, 0)
            self.assertIn(61.0, rec_sleeps)
            self.assertNotIn(secret, out_rec.getvalue())

            # logging_utils.append_api_log redacts sid= in method argument on .log as well as .jsonl
            ajl, atl = append_api_log(
                tp / "api_logs",
                method=f"POST?sid={secret}",
                path=f"/command?sid={secret}",
                status=200,
                payload={"command": "plan"},
                response={"ok": True, "message": "ok"},
            )
            self.assertNotIn(secret, ajl.read_text(encoding="utf-8"))
            self.assertNotIn(secret, atl.read_text(encoding="utf-8"))
            self.assertIn("REDACTED", atl.read_text(encoding="utf-8"))

    def test_r10_runner_stack_advisor_intervals_and_cli_json_redaction(self) -> None:
        import io
        import json
        import advisor as advisor_cli
        import login as login_cli
        import run_stack
        from spaceinvasion.client import RateLimitError
        from spaceinvasion.state import EnergyBalance, GameState, PlanetState, Resources

        self.assertEqual(run_bot._safe_interval(10**5000), 86400.0)
        self.assertEqual(run_bot._safe_interval(b"5.5"), 5.5)
        self.assertEqual(run_bot._safe_interval("\u0665"), 20.0)
        self.assertEqual(run_bot._safe_interval(frozenset({1})), 20.0)
        rl_huge = RateLimitError("rl")
        rl_huge.retry_after = 10**5000  # type: ignore[assignment]
        self.assertEqual(
            run_bot._safe_retry_after(rl_huge),
            3600.0,
        )
        self.assertEqual(run_stack._safe_interval(10**5000), 86400.0)
        self.assertEqual(run_stack._safe_interval(b"7.5"), 7.5)
        self.assertEqual(run_stack._safe_interval("\u0665"), 20.0)

        for fn in (advisor_cli._safe_cli_json, login_cli._safe_cli_json):
            out = fn(
                {
                    "url": Path("https://x/?sid=CLI_PATH_SEC"),
                    "s": frozenset({b"?sid=CLI_BYTES_SEC"}),
                    "password": "CLI_PW_SEC",
                }
            )
            parsed = json.loads(out)
            dumped = json.dumps(parsed, allow_nan=False)
            for sec in ("CLI_PATH_SEC", "CLI_BYTES_SEC", "CLI_PW_SEC"):
                self.assertNotIn(sec, dumped)

        p = PlanetState(
            planet_id=1,
            name="P",
            coords="01:001:01",
            planet_type="normal",
            temperature=20,
            fields_used=10,
            fields_max=200,
            resources=Resources(10, 10, 10, 10),
            storage=Resources(100, 100, 100, 100),
            energy=EnergyBalance(50, 10),
        )
        st = GameState(
            31,
            1,
            "u",
            1000.0,
            (p,),
            active_planet_id=1,
            unread_messages={"?sid=UNREAD_SID_SEC": 3, 10**5000: 2},  # type: ignore[dict-item]
        )
        buf = io.StringIO()
        with patch("sys.stdout", buf):
            advisor_cli._print_report(st, [])
        txt = buf.getvalue()
        self.assertNotIn("UNREAD_SID_SEC", txt)
        self.assertIn("1" + ("0" * 200), txt)

    def test_r11_pathlike_hashable_keys_unknown_scalars_and_login_cli_redaction(self) -> None:
        import io
        import json
        import tempfile
        import advisor as advisor_cli
        import login as login_cli
        from spaceinvasion.auth import LoginResult
        from spaceinvasion.browser import BrowserLoginResult
        from spaceinvasion.logging_utils import append_api_log, append_tick_logs, redact_sensitive
        from spaceinvasion.state import EnergyBalance, GameState, PlanetState, Resources

        class _BytesPath:
            def __fspath__(self) -> bytes:
                return b"https://x/?sid=RUNNER_BPATH_SEC"

        red_path = redact_sensitive(Path("https://x/?sid=RUNNER_PATH_SEC"))
        self.assertIsInstance(red_path, str)
        self.assertNotIn("RUNNER_PATH_SEC", red_path)
        red_bpath = redact_sensitive(_BytesPath())
        self.assertIsInstance(red_bpath, str)
        self.assertNotIn("RUNNER_BPATH_SEC", red_bpath)

        keyed = redact_sensitive(
            {
                Path("?sid=KEY_PATH_SEC"): 1,
                (Path("?sid=KEY_TUP_SEC"),): 2,
                frozenset({Path("?sid=KEY_FSET_SEC")}): 3,
            }
        )
        self.assertTrue(any(isinstance(k, tuple) for k in keyed))
        self.assertTrue(any(isinstance(k, frozenset) for k in keyed))
        keyed_str = json.dumps({str(k): v for k, v in keyed.items()}, allow_nan=False)
        for sec in ("KEY_PATH_SEC", "KEY_TUP_SEC", "KEY_FSET_SEC"):
            self.assertNotIn(sec, keyed_str)

        for fn in (advisor_cli._safe_cli_json, login_cli._safe_cli_json):
            out = fn({"err": RuntimeError("?sid=CLI_EXC_SEC"), "cplx": complex(1, 2), "obj": object()})
            parsed = json.loads(out)
            self.assertNotIn("CLI_EXC_SEC", json.dumps(parsed, allow_nan=False))

        p = PlanetState(1, "P", "01:001:01", "normal", 20, 10, 200, Resources(10, 10, 10, 10), Resources(100, 100, 100, 100), EnergyBalance(50, 10))
        st = GameState(31, 1, "u", 1000.0, (p,), active_planet_id=1)
        with tempfile.TemporaryDirectory() as tmp:
            log_dir = Path(tmp)
            tjl, ttl = append_tick_logs(
                log_dir,
                source="r11",
                tick=1,
                mode="DRY-RUN",
                state=st,
                actual_action={"priority": 10**5000, "kind": "BUILD", "target_name": Path("?sid=ACT_OBJ_SEC")},
                actual_result={"message": RuntimeError("?sid=RES_OBJ_SEC"), "extra": object(), "cplx": complex(1, 2)},
                next_action=None,
                outcomes=[{"obj": object()}],
            )
            ajl, atl = append_api_log(
                log_dir,
                method="POST",
                path="/command",
                status=200,
                payload={"err": RuntimeError("?sid=API_PAYLOAD_SEC")},
                response={"ok": True, "message": complex(2, 3), "extra": object()},
            )
            for lp in (tjl, ttl, ajl, atl):
                content = lp.read_text(encoding="utf-8")
                self.assertNotIn("ACT_OBJ_SEC", content)
                self.assertNotIn("RES_OBJ_SEC", content)
                self.assertNotIn("API_PAYLOAD_SEC", content)

        creds = Credentials(email="pilot@example.com", password="pw")
        b_res = BrowserLoginResult(
            account_name="acct?sid=ACC_SEC",
            email="pilot@example.com",
            profile_dir=Path("/tmp/?sid=PROF_SEC/profile"),
            cookie_path=Path("/tmp/?sid=COOK_SEC/cookies.json"),
            cookie_names=("PHPSESSID?sid=CNAME_SEC",),
            already_logged_in=False,
            final_url="https://spaceinvasion.bitmeup.com/?sid=BROWSER_FINAL_SEC",
            page_title="Title",
            entered_universe="",
            universes=(),
            debug_address="127.0.0.1:9222?sid=DBG_SEC",
        )
        h_res = LoginResult(
            account_name="acct?sid=HACC_SEC",
            email="pilot@example.com",
            landing_url="https://bitmeup.com/?sid=HLAND_SEC",
            final_url="https://spaceinvasion.bitmeup.com/?sid=HTTP_FINAL_SEC",
            page_title="Title",
            cookie_path=Path("/tmp/?sid=HCOOK_SEC/cookies.json"),
            cookie_names=("HCOOKIE?sid=HCNAME_SEC",),
            universes=(),
        )
        with (
            patch("login.load_credentials", return_value=creds),
            patch("login.login_with_browser", return_value=b_res),
            patch("login.login", return_value=h_res),
        ):
            for argv in ([], ["--json"], ["--mode", "http"], ["--mode", "http", "--json"]):
                buf = io.StringIO()
                with patch("sys.stdout", buf):
                    rc = login_cli.main(argv)
                self.assertEqual(rc, 0)
                rendered = buf.getvalue()
                for sec in (
                    "ACC_SEC", "PROF_SEC", "COOK_SEC", "CNAME_SEC", "BROWSER_FINAL_SEC", "DBG_SEC",
                    "HACC_SEC", "HLAND_SEC", "HTTP_FINAL_SEC", "HCOOK_SEC", "HCNAME_SEC",
                ):
                    self.assertNotIn(sec, rendered, f"argv={argv} leaked {sec}")
        self.assertIn("sid=BROWSER_FINAL_SEC", b_res.final_url)

    def test_r12_logging_utils_and_advisor_full_secret_redaction_exceptions_and_broken_path(self) -> None:
        import io
        import json
        import tempfile
        import advisor as advisor_cli
        import game_api as game_api_cli
        import run_stack as run_stack_cli
        from spaceinvasion.logging_utils import redact_sensitive

        class _RaiseFspath:
            def __fspath__(self) -> str:
                raise RuntimeError("fspath boom ?password=LOG_FS_PW")

        class _IntPath:
            def __fspath__(self) -> int:
                return 10**5000

        class _RaiseStrRepr:
            def __str__(self) -> str:
                raise RuntimeError("str boom")

            def __repr__(self) -> str:
                raise RuntimeError("repr boom")

        deep_key: object = Path("?password=LOG_DP5_PW")
        for _ in range(5):
            deep_key = (deep_key,)

        red = redact_sensitive(
            {
                "auth_header": "Authorization: Bearer LOG_BEARER_SEC",
                "json_blob": '{"password": "LOG_JPW_SEC", "api_key": "LOG_JKEY_SEC"}',
                "authorization": "LOG_DAUTH_SEC",
                "long_s": "\u017fid=LOG_LONGS_SEC",
                "exc": RuntimeError(10**5000, "?password=LOG_EXC_PW"),
                "bad_fs": _RaiseFspath(),
                "int_fs": _IntPath(),
                deep_key: "ok",
            }
        )
        self.assertIsInstance(red["exc"], str)
        self.assertIsInstance(red["bad_fs"], str)
        self.assertIsInstance(red["int_fs"], str)
        dumped = json.dumps({str(k): v for k, v in red.items()}, allow_nan=False)
        for sec in (
            "LOG_FS_PW",
            "LOG_DP5_PW",
            "LOG_BEARER_SEC",
            "LOG_JPW_SEC",
            "LOG_JKEY_SEC",
            "LOG_DAUTH_SEC",
            "LOG_LONGS_SEC",
            "LOG_EXC_PW",
        ):
            self.assertNotIn(sec, dumped)
        self.assertIn("authorization: bearer redacted", dumped.lower())

        adv_out = advisor_cli._safe_cli_json(
            {
                "speedup_factor": 10**5000,
                "err": RuntimeError(10**5000, "Authorization: Bearer ADV_BEAR_SEC"),
                "bad_fs": _RaiseFspath(),
                "bad_repr": _RaiseStrRepr(),
            }
        )
        parsed_adv = json.loads(adv_out)
        self.assertNotIn("ADV_BEAR_SEC", adv_out)
        self.assertIn("speedup_factor", parsed_adv)

        out_buf = io.StringIO()
        err_buf = io.StringIO()
        with (
            patch.object(
                advisor_cli.GameBrowserClient,
                "attach",
                side_effect=RuntimeError(10**5000, "?password=ADV_MAIN_PW"),
            ),
            patch("sys.stdout", out_buf),
            patch("sys.stderr", err_buf),
        ):
            rc = advisor_cli.main(["--json"])
        self.assertEqual(rc, 1)
        combined = out_buf.getvalue() + err_buf.getvalue()
        self.assertNotIn("ADV_MAIN_PW", combined)
        parsed_err = json.loads(out_buf.getvalue())
        self.assertIn("error", parsed_err)

        from spaceinvasion.logging_utils import append_tick_logs
        from spaceinvasion.state import EnergyBalance, GameState, PlanetState, Resources

        st_log = GameState(
            31,
            1,
            "Authorization: Bearer TICK_NICK_SEC",
            1000.0,
            (
                PlanetState(
                    planet_id=1,
                    name="P1",
                    coords="02:380:12",
                    planet_type="normal",
                    temperature=20,
                    fields_used=1,
                    fields_max=200,
                    resources=Resources(100, 100, 100, 100),
                    storage=Resources(1000, 1000, 1000, 1000),
                    energy=EnergyBalance(10, 5),
                ),
            ),
            active_planet_id=1,
        )
        with tempfile.TemporaryDirectory() as tmp_log:
            jpath, tpath = append_tick_logs(
                Path(tmp_log),
                source="commander",
                tick=1,
                mode="DRY-RUN",
                state=st_log,
                actual_action=None,
                actual_result={"error": RuntimeError(10**5000, "?password=TICK_RES_PW")},
                next_action=None,
                outcomes=[{"authorization": "TICK_DICT_AUTH", "bad_fs": _RaiseFspath()}],
            )
            for lp in (jpath, tpath):
                ltxt = lp.read_text(encoding="utf-8")
                for sec in ("TICK_NICK_SEC", "TICK_RES_PW", "TICK_DICT_AUTH"):
                    self.assertNotIn(sec, ltxt)

        for err_txt in (
            run_bot._safe_bot_str(RuntimeError(10**5000, "Authorization: Bearer RB_SEC")),
            run_stack_cli._safe_err_text(RuntimeError(10**5000, "Authorization: Bearer RS_SEC")),
            game_api_cli._safe_err_text(RuntimeError(10**5000, "Authorization: Bearer GA_SEC")),
            run_bot._safe_bot_str(_RaiseStrRepr()),
        ):
            self.assertIsInstance(err_txt, str)
            for sec in ("RB_SEC", "RS_SEC", "GA_SEC"):
                self.assertNotIn(sec, err_txt)

    def test_r13_logging_and_cli_nested_exc_compound_secrets_unicode_and_quoted_bearer(self) -> None:
        import time
        import advisor as advisor_cli
        import game_api as game_api_cli
        import run_stack as run_stack_cli
        from spaceinvasion.logging_utils import is_secret_dict_key, redact_sensitive

        # 1. Nested >256-char BaseException.args scrubbed before truncation in advisor & cyclic/DAG safety across CLIs
        exc_300 = RuntimeError(10**5000, {"password": "ADV_300_" + ("X" * 300)})
        adv_out = advisor_cli._safe_cli_str(exc_300)
        self.assertNotIn("ADV_300_", adv_out)
        self.assertIn("REDACTED", adv_out)

        cyc = RuntimeError("cyc")
        cyc.args = (10**5000, cyc)
        dag: BaseException = RuntimeError(10**5000, "?password=CLI_DAG_PW")
        for _ in range(18):
            dag = RuntimeError(10**5000, dag, dag, dag, dag, dag, dag, dag)
        for fn in (
            advisor_cli._safe_cli_str,
            run_bot._safe_bot_str,
            run_stack_cli._safe_err_text,
            game_api_cli._safe_err_text,
        ):
            self.assertIsInstance(fn(cyc), str)
            t0 = time.perf_counter()
            out_dag = fn(dag)
            self.assertLess((time.perf_counter() - t0) * 1000.0, 200.0)
            self.assertNotIn("CLI_DAG_PW", out_dag)

        # 2. logging_utils compound keys, quoted/bracketed Bearer, Unicode fast-path, and benign preservation
        self.assertTrue(is_secret_dict_key("client_secret"))
        self.assertTrue(is_secret_dict_key("X-API-Key"))
        self.assertFalse(is_secret_dict_key("has_password"))
        self.assertFalse(is_secret_dict_key("cookie_path"))
        self.assertFalse(is_secret_dict_key("bsid"))

        self.assertEqual(redact_sensitive('Authorization: Bearer "SECRET"'), 'Authorization: Bearer "REDACTED"')
        self.assertEqual(redact_sensitive("Authorization: Bearer [SECRET]"), "Authorization: Bearer [REDACTED]")
        self.assertEqual(redact_sensitive("Bearer <SECRET>"), "Bearer <REDACTED>")
        self.assertEqual(redact_sensitive('Authorization: Bearer ""'), 'Authorization: Bearer ""')
        self.assertEqual(redact_sensitive("Authorization: Bearer []"), "Authorization: Bearer []")
        self.assertEqual(
            redact_sensitive('{"auth_header": "Authorization: Bearer MY_TOK"}'),
            '{"auth_header": "Authorization: Bearer REDACTED"}',
        )
        self.assertEqual(
            redact_sensitive("?s\u0130d=LU_I1&?s\u0131d=LU_I2&?cook\u0130e=LU_CK&?ap\u0131_key=LU_AK&?cl\u0130ent_secret=LU_CS"),
            "?s\u0130d=REDACTED&?s\u0131d=REDACTED&?cook\u0130e=REDACTED&?ap\u0131_key=REDACTED&?cl\u0130ent_secret=REDACTED",
        )
        benign = "has_password=true&has_sid=false&no_secret=false&cookie_path=/x&cookie_names=a&token=ok&bsid=123"
        self.assertEqual(redact_sensitive(benign), benign)

        red_dict = redact_sensitive(
            {
                "client_secret": "LU_CS",
                "user_password": "LU_UP",
                "bot_api_key": "LU_BK",
                "session_cookie": "LU_SC",
                "session_sid": "LU_SS",
                "cookie_path": "/tmp/cookies.json",
                "has_sid": "false",
            }
        )
        self.assertEqual(red_dict["client_secret"], "REDACTED")
        self.assertEqual(red_dict["user_password"], "REDACTED")
        self.assertEqual(red_dict["bot_api_key"], "REDACTED")
        self.assertEqual(red_dict["session_cookie"], "REDACTED")
        self.assertEqual(red_dict["session_sid"], "REDACTED")
        self.assertEqual(red_dict["cookie_path"], "/tmp/cookies.json")
        self.assertEqual(red_dict["has_sid"], "false")


    def test_r14_logging_urlenc_json_esc_quotes_pct20_redos_and_cli_dag20_cycles(self) -> None:
        import time
        from spaceinvasion import logging_utils as lu
        import login as login_mod
        import advisor as advisor_mod
        import commander as cmd_mod
        import run_bot as rb_mod
        import run_stack as rs_mod
        import game_api as ga_mod

        vectors = (
            '%22password%22%3A%22LU_UE_PW%22',
            '%22Bearer%20LU_UE_BR%22',
            'Bearer %3CLU_UE_ANG%3E',
            'Bearer %5BLU_UE_BRK%5D',
            '\\"password\\":\\"LU_ESC_PW\\"',
            '\\\\"password\\\\":\\\\"LU_DESC_PW\\\\"',
            'Bearer [MY LU SECRET]',
            'Bearer <MY LU ANG>',
            'Bearer "UNCLOSED_LU_Q',
            'Bearer LU_SCHEMELESS_BR',
        )
        for v in vectors:
            red = lu.redact_sensitive(v)
            for marker in ("LU_UE_PW", "LU_UE_BR", "LU_UE_ANG", "LU_UE_BRK", "LU_ESC_PW", "LU_DESC_PW", "MY LU SECRET", "MY LU ANG", "UNCLOSED_LU_Q", "LU_SCHEMELESS_BR"):
                self.assertNotIn(marker, red, f"Leaked {marker} in {red!r}")

        self.assertEqual(lu.redact_sensitive("Authorization: Bearer REDACTED"), "Authorization: Bearer REDACTED")
        self.assertEqual(lu.redact_sensitive("has_sid=false&has_password=true&token=benign"), "has_sid=false&has_password=true&token=benign")

        redos_s = 'password=' + ('\\' * 5000) + '"'
        t0 = time.perf_counter()
        _ = lu.redact_sensitive(redos_s)
        self.assertLess((time.perf_counter() - t0) * 1000.0, 250.0)

        cyc: list[object] = []
        cyc.append(cyc)
        exc_cyc = RuntimeError("Bearer LU_EXC_SEC")
        exc_cyc.args = (exc_cyc, {"password": "LU_MAP_SEC"})

        dag_exc: BaseException = RuntimeError("Bearer LU_DAG_SEC")
        for _ in range(20):
            dag_exc = RuntimeError(dag_exc, dag_exc, dag_exc, dag_exc)

        class _RaiseFspath:
            def __fspath__(self) -> str:
                raise RuntimeError("fspath boom")

        t1 = time.perf_counter()
        for fn in (
            lu.redact_sensitive,
            login_mod._safe_cli_str,
            advisor_mod._safe_cli_str,
            cmd_mod._safe_cmd_str,
            rb_mod._safe_bot_str,
            rs_mod._safe_err_text,
            ga_mod._safe_err_text,
        ):
            s_cyc = fn(exc_cyc)
            s_dag = fn(dag_exc)
            self.assertNotIn("LU_EXC_SEC", s_cyc)
            self.assertNotIn("LU_MAP_SEC", s_cyc)
            self.assertNotIn("LU_DAG_SEC", s_dag)
            self.assertIsInstance(fn(_RaiseFspath()), str)
        self.assertEqual(login_mod._coerce_cli_json_scalars(cyc), ["[Cycle]"])
        self.assertEqual(advisor_mod._coerce_cli_json_scalars(cyc), ["[Cycle]"])
        self.assertLess((time.perf_counter() - t1) * 1000.0, 150.0)


if __name__ == "__main__":
    unittest.main()
