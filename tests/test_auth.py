"""Unit tests for SpaceInvasion `.env` credential loading and auth flow."""

from __future__ import annotations

import json
from pathlib import Path
import stat
import tempfile
import unittest
from unittest.mock import MagicMock

import requests

from spaceinvasion.auth import (
    AntiBotChallengeError,
    ConfigurationError,
    Credentials,
    LoginRejectedError,
    TwoFactorRequiredError,
    detect_antibot_challenge,
    extract_universes,
    load_credentials,
    load_session_cookies,
    login,
    parse_login_tokens,
    verify_saved_session,
)

SAMPLE_LANDING_HTML = """
<!DOCTYPE html><html lang="en"><head><title>SpaceInvasion</title></head><body>
<div id="bitmeup-login"></div>
<script>
window.onload=function(){
  bitmeup.initPortalClient(
    1,
    'aaaa1111|bbbb2222|cccc3333'.split('|'),
    'r1|r2|r3|r4|r5|r6|r7'.split('|'),
    function(){ bitmeup.sid="sid=deadbeefcafebabe1234"; }
  );
};
</script></body></html>
"""

SAMPLE_PORTAL_HTML = """
<!DOCTYPE html><html lang="en"><head><title>BitMeUp Portal</title></head><body>
<h1>Universes</h1>
<a href="https://uni1.spaceinvasion.bitmeup.com/play">RETRO (1x)</a>
<a href="/universe/nexus">Nexus (3x)</a>
</body></html>
"""


def _mock_response(
    *,
    status_code: int = 200,
    text: str = "",
    json_data: object = None,
    url: str = "https://spaceinvasion.bitmeup.com/?locale=en",
) -> MagicMock:
    resp = MagicMock(spec=requests.Response)
    resp.status_code = status_code
    resp.text = text
    resp.url = url
    resp.raise_for_status = MagicMock()
    if json_data is not None:
        resp.json.return_value = json_data
    else:
        resp.json.side_effect = ValueError("not json")
    return resp


class TestCredentialsLoading(unittest.TestCase):
    def test_loads_primary_credentials_from_env_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            env_path = Path(tmp) / ".env"
            env_path.write_text(
                "SPACEINVASION_EMAIL=pilot@example.com\n"
                "SPACEINVASION_PASSWORD=secret-pass\n"
                "SPACEINVASION_UNIVERSE=Nexus\n"
                "SPACEINVASION_STAY_LOGGED_IN=0\n",
                encoding="utf-8",
            )
            creds = load_credentials(env_path=env_path, environ={})
            self.assertEqual(creds.email, "pilot@example.com")
            self.assertEqual(creds.password, "secret-pass")
            self.assertEqual(creds.universe, "Nexus")
            self.assertFalse(creds.stay_logged_in)
            self.assertEqual(creds.account_name, "default")

    def test_loads_named_account_profile(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            env_path = Path(tmp) / ".env"
            env_path.write_text(
                "SPACEINVASION_EMAIL=main@example.com\n"
                "SPACEINVASION_PASSWORD=mainpass\n"
                "SPACEINVASION_ALT_1_EMAIL=alt@example.com\n"
                "SPACEINVASION_ALT_1_PASSWORD=altpass\n",
                encoding="utf-8",
            )
            creds = load_credentials(env_path=env_path, account="alt-1", environ={})
            self.assertEqual(creds.email, "alt@example.com")
            self.assertEqual(creds.password, "altpass")
            self.assertEqual(creds.account_name, "alt_1")

    def test_missing_credentials_raises_configuration_error(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            env_path = Path(tmp) / ".env"
            env_path.write_text("SPACEINVASION_EMAIL=\nSPACEINVASION_PASSWORD=\n", encoding="utf-8")
            with self.assertRaises(ConfigurationError):
                load_credentials(env_path=env_path, environ={})


class TestTokenAndAntibotParsing(unittest.TestCase):
    def test_parse_login_tokens_extracts_fields_and_sid(self) -> None:
        tokens = parse_login_tokens(SAMPLE_LANDING_HTML)
        self.assertEqual(tokens.submit_field, "aaaa1111")
        self.assertEqual(tokens.email_field, "bbbb2222")
        self.assertEqual(tokens.password_field, "cccc3333")
        self.assertEqual(tokens.sid, "deadbeefcafebabe1234")

    def test_detect_antibot_ignores_normal_page_and_flags_turnstile(self) -> None:
        self.assertIsNone(detect_antibot_challenge(200, SAMPLE_LANDING_HTML))
        flagged = detect_antibot_challenge(
            200, '<div class="cf-turnstile" data-sitekey="abc"></div>'
        )
        self.assertIsNotNone(flagged)

    def test_extract_universes_parses_start_portal_instances(self) -> None:
        html = """
        <script>
        SI_TPL.startPortal={SID:"sid=abc123",instances:[
          {id:46,si2:false,name:"Sirius",descr:'Desc {braces}',acc:{title:"Sirius",vm:false},speed:1.5,flightSpeed:2},
          {id:31,si2:false,name:"RETRO",descr:'Classic',acc:"Commander",speed:1,flightSpeed:1},
          {id:36,si2:true,name:"SI2 Endless",descr:'Desc',acc:"",speed:1,flightSpeed:1}
        ],campaign:{available:false}};
        </script>
        """
        unis = extract_universes(html, "https://spaceinvasion.bitmeup.com/?sid=abc123")
        self.assertEqual(len(unis), 3)
        self.assertEqual(unis[0].label, "Sirius")
        self.assertEqual(unis[0].universe_id, 46)
        self.assertTrue(unis[0].has_account)
        self.assertFalse(unis[0].is_si2)
        self.assertEqual(unis[0].speed, 1.5)
        self.assertEqual(unis[0].flight_speed, 2.0)
        self.assertEqual(
            unis[0].url,
            "https://spaceinvasion.bitmeup.com/?action=externalLogin&instance=46&sid=abc123",
        )
        self.assertEqual(unis[1].label, "RETRO")
        self.assertTrue(unis[1].has_account)
        self.assertEqual(unis[2].label, "SI2 Endless")
        self.assertTrue(unis[2].is_si2)
        self.assertFalse(unis[2].has_account)

    def test_extract_universes_handles_trailing_script_and_reordered_fields(self) -> None:
        html = """
        <script>
        SI_TPL.startPortal = {
          instances: [
            {id: 31, si2: false, name: "RETRO", acc: {title: "Colony"}}
          ],
          SID: "sid=xyz789"
        };
        window.__extraInit = true;
        </script>
        """
        unis = extract_universes(html, "https://spaceinvasion.bitmeup.com/?locale=en")
        self.assertEqual(len(unis), 1)
        self.assertEqual(unis[0].label, "RETRO")
        self.assertEqual(unis[0].universe_id, 31)
        self.assertTrue(unis[0].has_account)
        self.assertIn("instance=31&sid=xyz789", unis[0].url)


class TestLoginFlow(unittest.TestCase):
    def _make_session(self) -> requests.Session:
        sess = requests.Session()
        sess.cookies.set("sis123", "token-val", domain="spaceinvasion.bitmeup.com", path="/")
        return sess

    def test_successful_login_saves_cookies_with_secure_permissions(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            session_dir = Path(tmp) / ".sessions"
            sess = self._make_session()
            sess.get = MagicMock(
                side_effect=[
                    _mock_response(text=SAMPLE_LANDING_HTML),
                    _mock_response(text="bitmeup.setData(...)"),
                    _mock_response(
                        text=SAMPLE_PORTAL_HTML,
                        url="https://bitmeup.com/portal",
                    ),
                ]
            )
            sess.post = MagicMock(
                return_value=_mock_response(
                    json_data={"result": {"result": "ok", "login": "https://bitmeup.com/auth?t=xyz"}}
                )
            )

            creds = Credentials(email="pilot@example.com", password="secret")
            result = login(creds=creds, session_dir=session_dir, session=sess)

            self.assertEqual(result.final_url, "https://bitmeup.com/portal")
            self.assertEqual(result.page_title, "BitMeUp Portal")
            self.assertEqual(len(result.universes), 2)
            self.assertEqual(result.universes[0].label, "RETRO (1x)")
            self.assertTrue(result.cookie_path.exists())

            mode = stat.S_IMODE(result.cookie_path.stat().st_mode)
            self.assertEqual(mode, 0o600)

            post_data = sess.post.call_args.kwargs["data"]
            self.assertEqual(
                post_data,
                {
                    "aaaa1111": "1",
                    "bbbb2222": "pilot@example.com",
                    "cccc3333": "secret",
                    "l": "1",
                    "sid": "deadbeefcafebabe1234",
                },
            )

            restored_sess, meta = load_session_cookies(result.cookie_path)
            self.assertEqual(meta["last_url"], "https://bitmeup.com/portal")
            self.assertEqual(restored_sess.cookies.get("sis123"), "token-val")

    def test_two_factor_login_prompts_and_retries(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            sess = self._make_session()
            sess.get = MagicMock(
                side_effect=[
                    _mock_response(text=SAMPLE_LANDING_HTML),
                    _mock_response(text="tpl"),
                    _mock_response(text=SAMPLE_PORTAL_HTML, url="https://bitmeup.com/portal"),
                ]
            )
            sess.post = MagicMock(
                side_effect=[
                    _mock_response(json_data={"result": {"result": "2fa"}}),
                    _mock_response(
                        json_data={"result": {"result": "ok", "login": "https://bitmeup.com/auth"}}
                    ),
                ]
            )

            creds = Credentials(email="pilot@example.com", password="secret")
            result = login(
                creds=creds,
                session_dir=Path(tmp) / ".sessions",
                two_factor_provider=lambda: "654321",
                session=sess,
            )
            self.assertEqual(result.final_url, "https://bitmeup.com/portal")
            second_post = sess.post.call_args_list[1].kwargs["data"]
            self.assertEqual(second_post["2fa"], "654321")

    def test_two_factor_without_code_raises(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            sess = self._make_session()
            sess.get = MagicMock(
                side_effect=[
                    _mock_response(text=SAMPLE_LANDING_HTML),
                    _mock_response(text="tpl"),
                ]
            )
            sess.post = MagicMock(
                return_value=_mock_response(json_data={"result": {"result": "2fa"}})
            )
            creds = Credentials(email="pilot@example.com", password="secret")
            with self.assertRaises(TwoFactorRequiredError):
                login(creds=creds, session_dir=Path(tmp) / ".sessions", session=sess)

    def test_rejected_login_translates_error_and_omits_password(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            sess = self._make_session()
            sess.get = MagicMock(
                side_effect=[
                    _mock_response(text=SAMPLE_LANDING_HTML),
                    _mock_response(text="tpl"),
                ]
            )
            sess.post = MagicMock(
                return_value=_mock_response(
                    json_data={"result": {"result": "error", "error": "b.external.login.error.1"}}
                )
            )
            creds = Credentials(email="pilot@example.com", password="super-secret-value")
            with self.assertRaises(LoginRejectedError) as ctx:
                login(creds=creds, session_dir=Path(tmp) / ".sessions", session=sess)
            self.assertIn("Invalid email or password", str(ctx.exception))
            self.assertNotIn("super-secret-value", str(ctx.exception))

    def test_antibot_gate_aborts_immediately(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            sess = self._make_session()
            sess.get = MagicMock(
                return_value=_mock_response(
                    status_code=403,
                    text="<html><title>Just a moment...</title>Cloudflare</html>",
                )
            )
            creds = Credentials(email="pilot@example.com", password="secret")
            with self.assertRaises(AntiBotChallengeError):
                login(creds=creds, session_dir=Path(tmp) / ".sessions", session=sess)

    def test_verify_saved_session_detects_active_vs_logged_out(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            session_dir = Path(tmp) / ".sessions"
            session_dir.mkdir(parents=True)
            cookie_file = session_dir / "default.json"
            cookie_file.write_text(
                json.dumps(
                    {
                        "metadata": {"last_url": "https://bitmeup.com/portal", "locale": "en"},
                        "cookies": [{"name": "sis", "value": "v", "domain": ".bitmeup.com", "path": "/"}],
                    }
                ),
                encoding="utf-8",
            )
            with unittest.mock.patch("spaceinvasion.auth.load_session_cookies") as mock_load:
                mock_sess = MagicMock()
                mock_sess.get.return_value = _mock_response(
                    text=SAMPLE_PORTAL_HTML, url="https://bitmeup.com/portal"
                )
                mock_load.return_value = (mock_sess, {"last_url": "https://bitmeup.com/portal"})
                ok, url, title = verify_saved_session(session_dir=session_dir)
                self.assertTrue(ok)
                self.assertEqual(title, "BitMeUp Portal")
                self.assertEqual(url, "https://bitmeup.com/portal")

                mock_sess.get.return_value = _mock_response(
                    text=SAMPLE_LANDING_HTML, url="https://spaceinvasion.bitmeup.com/?locale=en"
                )
                ok2, _, _ = verify_saved_session(session_dir=session_dir)
                self.assertFalse(ok2)

    def test_universes_from_portal_data_handles_empty_and_malformed_speeds(self) -> None:
        from spaceinvasion.auth import universes_from_portal_data

        uni_opts = universes_from_portal_data(
            "sid123",
            [
                {"id": 31, "name": "Genesis", "speed": "", "flightSpeed": "bad", "playable": 1},
                {"id": 32, "name": "Retro", "speed": float("nan"), "flightSpeed": True, "playable": True},
                {"id": 33, "name": "NoneSpd", "speed": None, "flightSpeed": None, "playable": True},
                {"id": 34, "name": "Fast", "speed": "3.0", "flightSpeed": "2.5", "playable": True},
            ],
            "https://spaceinvasion.bitmeup.com",
        )
        by_id = {u.universe_id: u for u in uni_opts}
        self.assertEqual(by_id[31].speed, 1.0)
        self.assertEqual(by_id[31].flight_speed, 1.0)
        self.assertEqual(by_id[32].speed, 1.0)
        self.assertEqual(by_id[32].flight_speed, 1.0)
        self.assertIsNone(by_id[33].speed)
        self.assertIsNone(by_id[33].flight_speed)
        self.assertEqual(by_id[34].speed, 3.0)
        self.assertEqual(by_id[34].flight_speed, 2.5)

    def test_universes_from_portal_data_skips_non_mapping_entries(self) -> None:
        from spaceinvasion.auth import universes_from_portal_data

        uni_opts = universes_from_portal_data(
            "sid123",
            ["bad", 123, None, {"id": True, "name": "BadBool"}, {"id": 31, "name": "Genesis"}],  # type: ignore[list-item]
            "https://spaceinvasion.bitmeup.com",
        )
        self.assertEqual(len(uni_opts), 1)
        self.assertEqual(uni_opts[0].universe_id, 31)
        self.assertEqual(uni_opts[0].label, "Genesis")
        self.assertEqual(universes_from_portal_data("sid123", "not_a_list", "https://e.test"), ())  # type: ignore[arg-type]

        huge_opts = universes_from_portal_data(
            "sid123",
            [{"id": 31, "name": 10**5000}],
            "https://e.test",
        )
        self.assertEqual(len(huge_opts), 1)
        self.assertEqual(huge_opts[0].label, "~1e5000")

    def test_r7_http_keepalive_login_and_verify_saved_session_close_session_cleanly(self) -> None:
        from http.server import BaseHTTPRequestHandler, HTTPServer
        import threading
        from spaceinvasion.auth import universes_from_portal_data

        mode = {"reject": False}

        class _KeepAliveHandler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, format: str, *args: object) -> None:
                return

            def do_GET(self) -> None:
                if "bitmeup.php" in self.path:
                    body = b"bitmeup.setData({});"
                    ctype = "application/javascript"
                elif "/portal" in self.path:
                    body = SAMPLE_PORTAL_HTML.encode("utf-8")
                    ctype = "text/html; charset=utf-8"
                else:
                    body = SAMPLE_LANDING_HTML.encode("utf-8")
                    ctype = "text/html; charset=utf-8"
                self.send_response(200)
                self.send_header("Content-Type", ctype)
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Connection", "keep-alive")
                self.end_headers()
                self.wfile.write(body)

            def do_POST(self) -> None:
                raw_len = int(self.headers.get("Content-Length", "0"))
                if raw_len > 0:
                    self.rfile.read(raw_len)
                if mode["reject"]:
                    payload = {"result": {"result": "error", "error": "b.external.login.error.1"}}
                else:
                    payload = {"result": {"result": "ok", "login": "/portal"}}
                body = json.dumps(payload, allow_nan=False).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Connection", "keep-alive")
                self.end_headers()
                self.wfile.write(body)

        srv = HTTPServer(("127.0.0.1", 0), _KeepAliveHandler)
        srv_thread = threading.Thread(target=srv.serve_forever, daemon=True)
        srv_thread.start()
        base_url = f"http://127.0.0.1:{srv.server_address[1]}"
        try:
            with tempfile.TemporaryDirectory() as tmp:
                sdir = Path(tmp) / ".sessions"
                creds = Credentials(email="pilot@example.com", password="secret")

                res = login(creds=creds, session_dir=sdir, base_url=base_url)
                self.assertEqual(res.page_title, "BitMeUp Portal")

                ok, checked_url, title = verify_saved_session(
                    account="default", session_dir=sdir, base_url=base_url
                )
                self.assertTrue(ok)
                self.assertIn("/portal", checked_url)
                self.assertEqual(title, "BitMeUp Portal")

                mode["reject"] = True
                with self.assertRaises(LoginRejectedError) as ctx:
                    login(creds=creds, session_dir=sdir, base_url=base_url)
                self.assertIn("Invalid email or password", str(ctx.exception))
        finally:
            sd_thread = threading.Thread(target=srv.shutdown, daemon=True)
            sd_thread.start()
            sd_thread.join(timeout=2.0)
            self.assertFalse(sd_thread.is_alive(), "HTTPServer.shutdown() deadlocked on leaked keep-alive socket")
            srv.server_close()
            srv_thread.join(timeout=2.0)

    def test_r8_keepalive_held_exception_login_and_verify_saved_session_no_deadlock(self) -> None:
        from http.server import BaseHTTPRequestHandler, HTTPServer
        import threading

        mode = {"status": 200, "reject": True}

        class _HeldKeepAliveHandler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, format: str, *args: object) -> None:
                return

            def do_GET(self) -> None:
                if mode["status"] >= 500:
                    body = b"Server Error"
                    self.send_response(mode["status"])
                    self.send_header("Content-Type", "text/plain")
                    self.send_header("Content-Length", str(len(body)))
                    self.send_header("Connection", "keep-alive")
                    self.end_headers()
                    self.wfile.write(body)
                    return
                if "bitmeup.php" in self.path:
                    body = b"bitmeup.setData({});"
                    ctype = "application/javascript"
                else:
                    body = SAMPLE_LANDING_HTML.encode("utf-8")
                    ctype = "text/html; charset=utf-8"
                self.send_response(200)
                self.send_header("Content-Type", ctype)
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Connection", "keep-alive")
                self.end_headers()
                self.wfile.write(body)

            def do_POST(self) -> None:
                raw_len = int(self.headers.get("Content-Length", "0"))
                if raw_len > 0:
                    self.rfile.read(raw_len)
                payload = {"result": {"result": "error", "error": "b.external.login.error.1"}}
                body = json.dumps(payload, allow_nan=False).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Connection", "keep-alive")
                self.end_headers()
                self.wfile.write(body)

        srv = HTTPServer(("127.0.0.1", 0), _HeldKeepAliveHandler)
        srv_thread = threading.Thread(target=srv.serve_forever, daemon=True)
        srv_thread.start()
        base_url = f"http://127.0.0.1:{srv.server_address[1]}"
        held_login_exc: Exception | None = None
        held_verify_exc: Exception | None = None
        try:
            with tempfile.TemporaryDirectory() as tmp:
                sdir = Path(tmp) / ".sessions"
                creds = Credentials(email="pilot@example.com", password="secret")

                # Caller-owned session must remain open when passed explicitly
                caller_sess = requests.Session()
                caller_closed = False
                orig_close = caller_sess.close

                def _track_close() -> None:
                    nonlocal caller_closed
                    caller_closed = True
                    orig_close()

                caller_sess.close = _track_close  # type: ignore[method-assign]
                try:
                    try:
                        login(creds=creds, session_dir=sdir, base_url=base_url, session=caller_sess)
                        self.fail("Expected LoginRejectedError")
                    except LoginRejectedError as exc:
                        self.assertFalse(caller_closed)
                        self.assertIn("Invalid email or password", str(exc))
                finally:
                    orig_close()

                # Owned session held in except block must NOT deadlock HTTPServer.shutdown()
                try:
                    login(creds=creds, session_dir=sdir, base_url=base_url)
                    self.fail("Expected LoginRejectedError")
                except LoginRejectedError as exc:
                    held_login_exc = exc

                sdir.mkdir(parents=True, exist_ok=True)
                (sdir / "default.json").write_text(
                    json.dumps(
                        {
                            "metadata": {"last_url": f"{base_url}/portal", "locale": "en"},
                            "cookies": [],
                        },
                        allow_nan=False,
                    ),
                    encoding="utf-8",
                )
                mode["status"] = 500
                try:
                    verify_saved_session(account="default", session_dir=sdir, base_url=base_url)
                    self.fail("Expected requests.HTTPError")
                except requests.HTTPError as exc:
                    held_verify_exc = exc

                # Shutdown BEFORE releasing held exception references
                sd_thread = threading.Thread(target=srv.shutdown, daemon=True)
                sd_thread.start()
                sd_thread.join(timeout=1.5)
                self.assertFalse(
                    sd_thread.is_alive(),
                    "HTTPServer.shutdown() deadlocked while caller retained LoginRejectedError/HTTPError",
                )
                self.assertIsNotNone(held_login_exc)
                self.assertIsNotNone(held_verify_exc)
        finally:
            held_login_exc = None
            held_verify_exc = None
            srv.shutdown()
            srv.server_close()
            srv_thread.join(timeout=2.0)

    def test_r10_portal_ascii_bytes_and_safe_key_metadata(self) -> None:
        from spaceinvasion import auth
        from spaceinvasion.auth import extract_universes, save_session_cookies, universes_from_portal_data

        self.assertEqual(auth._safe_portal_speed(b"4.5"), 4.5)
        self.assertEqual(auth._safe_portal_speed("\u0664.\u0665"), 1.0)
        self.assertIsNone(auth._safe_portal_speed(None))
        self.assertEqual(auth._safe_portal_speed([1]), 1.0)
        self.assertEqual(auth._safe_portal_label(10**5000), "~1e5000")
        self.assertTrue(isinstance(auth._safe_portal_label((10**5000, "x")), str))
        self.assertTrue(isinstance(auth._safe_portal_label(frozenset({10**5000})), str))

        unis_ar = extract_universes(
            '<a href="https://uni1.example/?sid=abc">Uni \u0661\u0662\u0663</a>',
            "https://uni1.example/",
        )
        self.assertTrue(all(u.universe_id != 123 for u in unis_ar))
        unis_ar_portal = universes_from_portal_data(
            "SECRET_SID",
            [{"id": "\u0661\u0662\u0663", "speed": "\u0665", "name": "Ar"}],
            "https://uni1.example/",
        )
        self.assertEqual(unis_ar_portal, ())

        unis_bytes = universes_from_portal_data(
            "SECRET_99",
            [
                {
                    "id": b"31",
                    "name": b"Uni 31",
                    "speed": b"2.5",
                    "flightSpeed": b"1.5",
                }
            ],
            "https://uni31.example/",
        )
        self.assertEqual(len(unis_bytes), 1)
        self.assertEqual(unis_bytes[0].universe_id, 31)
        self.assertEqual(unis_bytes[0].speed, 2.5)
        self.assertEqual(unis_bytes[0].flight_speed, 1.5)

        with tempfile.TemporaryDirectory() as tmp:
            import requests

            cookie_path = Path(tmp) / "r10_cookies.json"
            save_session_cookies(
                requests.Session(),
                cookie_path,
                metadata={(10**5000, "k"): frozenset({10**5000})},  # type: ignore[arg-type]
            )
            payload = json.loads(cookie_path.read_text(encoding="utf-8"))
            self.assertIn("metadata", payload)

    def test_r13_load_session_cookies_malformed_json_raises_configuration_error(self) -> None:
        from unittest.mock import patch
        from spaceinvasion.auth import ConfigurationError, SpaceInvasionAuthError, load_session_cookies

        with tempfile.TemporaryDirectory() as tmp:
            tp = Path(tmp)
            corrupt = tp / "corrupt.json"
            corrupt.write_text('{corrupt?password=FILE_BODY_SECRET', encoding="utf-8")
            with self.assertRaises(ConfigurationError) as ctx_bad:
                load_session_cookies(corrupt)
            self.assertIsInstance(ctx_bad.exception, SpaceInvasionAuthError)
            self.assertNotIn("FILE_BODY_SECRET", str(ctx_bad.exception))

            bad_bytes = tp / "bad_bytes.json"
            bad_bytes.write_bytes(b"\xff\xfe{bad")
            with self.assertRaises(ConfigurationError):
                load_session_cookies(bad_bytes)

            # Corrupt cookie insertion closes locally created session
            bad_cookie_file = tp / "bad_item.json"
            bad_cookie_file.write_text(
                json.dumps({"cookies": [{"name": "c1", "value": "v1"}], "metadata": {}}, allow_nan=False),
                encoding="utf-8",
            )
            import requests

            fake_sess = requests.Session()
            closed = False
            orig_close = fake_sess.close

            def _track() -> None:
                nonlocal closed
                closed = True
                orig_close()

            fake_sess.close = _track  # type: ignore[method-assign]
            with (
                patch("spaceinvasion.auth.build_http_session", return_value=fake_sess),
                patch.object(fake_sess.cookies, "set", side_effect=RuntimeError("jar boom")),
            ):
                with self.assertRaises(RuntimeError):
                    load_session_cookies(bad_cookie_file)
            self.assertTrue(closed)


if __name__ == "__main__":
    unittest.main()
