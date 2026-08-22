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


if __name__ == "__main__":
    unittest.main()
