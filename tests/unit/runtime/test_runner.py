"""Unit tests for game_api, login, commander, and logging redaction."""

from __future__ import annotations

import json
from pathlib import Path
import unittest
from unittest.mock import MagicMock, patch

from backend.session.auth import SpaceInvasionAuthError


class TestRunnerDebugPortLifecycle(unittest.TestCase):
    def test_game_api_main_redaction_reload_sigterm_and_cleanup(self) -> None:
        import io
        import signal
        import tempfile
        import game_api
        from backend.runtime.bridge import GameApiBridge

        before_sig = signal.getsignal(signal.SIGTERM)
        with patch("sys.stdout", new_callable=io.StringIO):
            with self.assertRaises(SystemExit) as cm_ga:
                game_api.main(["--help"])
            self.assertEqual(cm_ga.exception.code, 0)

        # game_api --reload delegation + auth redaction + clean server/DB/SIGTERM shutdown
        with (
            patch("game_api.should_spawn_reloader", return_value=True),
            patch("game_api.watch_and_run", return_value=0) as mock_watch,
        ):
            self.assertEqual(game_api.main(["--reload"]), 0)
            mock_watch.assert_called_once()

        api_err = io.StringIO()
        with (
            patch(
                "game_api.ensure_debug_browser_ready", side_effect=SpaceInvasionAuthError("bad ?sid=topsecret_api_88")
            ),
            patch("sys.stderr", api_err),
        ):
            rc_api_err = game_api.main(["--debug-port", "9222"])
        self.assertEqual(rc_api_err, 1)
        self.assertNotIn("topsecret_api_88", api_err.getvalue())
        self.assertIn("REDACTED", api_err.getvalue())

        with tempfile.TemporaryDirectory() as tmp:
            tp = Path(tmp)
            cfg_p = tp / "cfg.json"
            cfg_p.write_text('{"planner_mode": "priority"}', encoding="utf-8")
            created_bridges: list[GameApiBridge] = []

            def _isolated_bridge(*args, **kwargs):
                kwargs["db_path"] = tp / "test_api.db"
                b = GameApiBridge(*args, **kwargs)
                created_bridges.append(b)
                return b

            real_create_api_server = game_api.create_api_server

            def _interrupt_after_bind(bridge_obj, *, host: str, port: int, ui_dir=None):
                srv = real_create_api_server(bridge_obj, host=host, port=port, ui_dir=ui_dir)
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
            self.assertTrue(created_bridges[0].ctx.db_closed)
            self.assertEqual(signal.getsignal(signal.SIGTERM), before_sig)

    def test_r7_logging_redaction_and_bounds(self) -> None:
        import tempfile
        from backend.runtime.logs import append_tick_logs
        from backend.core.model import (
            EnergyBalance,
            GameState,
            PlanetState,
            Resources,
            TutorialStatus,
            TutorialTask,
        )
        from backend.core.model import PlannedAction

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

        with tempfile.TemporaryDirectory() as tmp:
            tp = Path(tmp)
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

    def test_r8_append_tick_logs_and_api_log_huge_10e5000_tick_and_status(self) -> None:
        import json
        import tempfile
        from backend.runtime.logs import append_api_log, append_tick_logs
        from backend.core.model import (
            EnergyBalance,
            GameState,
            PlanetState,
            Resources,
        )

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

    def test_r9_append_api_log_method_redaction(self) -> None:
        import tempfile
        from backend.runtime.logs import append_api_log

        secret = "R9_RUNBOT_SID_777"
        with tempfile.TemporaryDirectory() as tmp:
            tp = Path(tmp)
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

    def test_r10_cli_json_redaction(self) -> None:
        import json
        import login as login_cli

        out = login_cli._safe_cli_json(
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

    def test_r11_pathlike_hashable_keys_unknown_scalars_and_login_cli_redaction(self) -> None:
        import io
        import json
        import tempfile
        import login as login_cli
        from backend.session.auth import (
            Credentials,
            LoginResult,
        )
        from backend.session.browser import BrowserLoginResult
        from backend.core.redaction import redact_sensitive
        from backend.runtime.logs import append_api_log, append_tick_logs
        from backend.core.model import (
            EnergyBalance,
            GameState,
            PlanetState,
            Resources,
        )

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

        out = login_cli._safe_cli_json(
            {"err": RuntimeError("?sid=CLI_EXC_SEC"), "cplx": complex(1, 2), "obj": object()}
        )
        parsed = json.loads(out)
        self.assertNotIn("CLI_EXC_SEC", json.dumps(parsed, allow_nan=False))

        p = PlanetState(
            1,
            "P",
            "01:001:01",
            "normal",
            20,
            10,
            200,
            Resources(10, 10, 10, 10),
            Resources(100, 100, 100, 100),
            EnergyBalance(50, 10),
        )
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
                    "ACC_SEC",
                    "PROF_SEC",
                    "COOK_SEC",
                    "CNAME_SEC",
                    "BROWSER_FINAL_SEC",
                    "DBG_SEC",
                    "HACC_SEC",
                    "HLAND_SEC",
                    "HTTP_FINAL_SEC",
                    "HCOOK_SEC",
                    "HCNAME_SEC",
                ):
                    self.assertNotIn(sec, rendered, f"argv={argv} leaked {sec}")
        self.assertIn("sid=BROWSER_FINAL_SEC", b_res.final_url)

    def test_r12_logging_utils_full_secret_redaction_exceptions_and_broken_path(self) -> None:
        import tempfile
        import game_api as game_api_cli
        from backend.core.redaction import redact_sensitive

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

        from backend.runtime.logs import append_tick_logs
        from backend.core.model import (
            EnergyBalance,
            GameState,
            PlanetState,
            Resources,
        )

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

        err_txt = game_api_cli._safe_err_text(RuntimeError(10**5000, "Authorization: Bearer GA_SEC"))
        self.assertIsInstance(err_txt, str)
        self.assertNotIn("GA_SEC", err_txt)

    def test_r13_logging_and_cli_nested_exc_compound_secrets_unicode_and_quoted_bearer(self) -> None:
        import time
        import game_api as game_api_cli
        from backend.core.redaction import is_secret_dict_key, redact_sensitive

        cyc = RuntimeError("cyc")
        cyc.args = (10**5000, cyc)
        dag: BaseException = RuntimeError(10**5000, "?password=CLI_DAG_PW")
        for _ in range(18):
            dag = RuntimeError(10**5000, dag, dag, dag, dag, dag, dag, dag)
        for fn in (game_api_cli._safe_err_text,):
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
            redact_sensitive(
                "?s\u0130d=LU_I1&?s\u0131d=LU_I2&?cook\u0130e=LU_CK&?ap\u0131_key=LU_AK&?cl\u0130ent_secret=LU_CS"
            ),
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
        from backend.core import redaction as lu
        import login as login_cli
        import commander as cmd_mod
        import game_api as ga_mod

        vectors = (
            "%22password%22%3A%22LU_UE_PW%22",
            "%22Bearer%20LU_UE_BR%22",
            "Bearer %3CLU_UE_ANG%3E",
            "Bearer %5BLU_UE_BRK%5D",
            '\\"password\\":\\"LU_ESC_PW\\"',
            '\\\\"password\\\\":\\\\"LU_DESC_PW\\\\"',
            "Bearer [MY LU SECRET]",
            "Bearer <MY LU ANG>",
            'Bearer "UNCLOSED_LU_Q',
            "Bearer LU_SCHEMELESS_BR",
        )
        for v in vectors:
            red = lu.redact_sensitive(v)
            for marker in (
                "LU_UE_PW",
                "LU_UE_BR",
                "LU_UE_ANG",
                "LU_UE_BRK",
                "LU_ESC_PW",
                "LU_DESC_PW",
                "MY LU SECRET",
                "MY LU ANG",
                "UNCLOSED_LU_Q",
                "LU_SCHEMELESS_BR",
            ):
                self.assertNotIn(marker, red, f"Leaked {marker} in {red!r}")

        self.assertEqual(lu.redact_sensitive("Authorization: Bearer REDACTED"), "Authorization: Bearer REDACTED")
        self.assertEqual(
            lu.redact_sensitive("has_sid=false&has_password=true&token=benign"),
            "has_sid=false&has_password=true&token=benign",
        )

        redos_s = "password=" + ("\\" * 5000) + '"'
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
            login_cli._safe_cli_str,
            cmd_mod._safe_cmd_str,
            ga_mod._safe_err_text,
        ):
            s_cyc = fn(exc_cyc)
            s_dag = fn(dag_exc)
            self.assertNotIn("LU_EXC_SEC", s_cyc)
            self.assertNotIn("LU_MAP_SEC", s_cyc)
            self.assertNotIn("LU_DAG_SEC", s_dag)
            self.assertIsInstance(fn(_RaiseFspath()), str)
        self.assertEqual(login_cli._coerce_cli_json_scalars(cyc), ["[Cycle]"])
        self.assertLess((time.perf_counter() - t1) * 1000.0, 150.0)


if __name__ == "__main__":
    unittest.main()
