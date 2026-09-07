"""Unit tests for Selenium browser login and session persistence."""

from __future__ import annotations

from pathlib import Path
import stat
import tempfile
import unittest
from unittest.mock import MagicMock

from selenium.common.exceptions import NoSuchElementException
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys

from spaceinvasion.auth import (
    AntiBotChallengeError,
    ConfigurationError,
    Credentials,
    LoginRejectedError,
    SpaceInvasionAuthError,
    TwoFactorRequiredError,
)
from spaceinvasion.browser import (
    BrowserConfig,
    close_or_detach_driver,
    login_with_browser,
    open_browser_session,
)

LOGIN_PAGE_HTML = """
<!DOCTYPE html><html><head><title>SpaceInvasion</title></head><body>
<div id="bitmeup-login"></div>
</body></html>
"""

PORTAL_PAGE_HTML = """
<!DOCTYPE html><html><head><title>SpaceInvasion Portal</title></head><body>
<div id="sip-tab-universe">universe selection</div>
<script>
SI_TPL.startPortal={SID:"sid=abc999",instances:[
  {id:46,si2:false,name:"Sirius",descr:'Endless',acc:{title:"Sirius"},speed:1.5,flightSpeed:2},
  {id:40,si2:false,name:"GeNeSIS 2527",descr:'New',acc:"",speed:2,flightSpeed:3}
],campaign:{available:false}};
</script>
</body></html>
"""


class FakeElement:
    """Minimal mock WebElement supporting visibility, typing, and clicks."""

    def __init__(self, text: str = "", displayed: bool = True, selected: bool = True):
        self.text = text
        self._displayed = displayed
        self._selected = selected
        self.typed: list[str] = []
        self.clicked = 0

    def is_displayed(self) -> bool:
        return self._displayed

    def is_selected(self) -> bool:
        return self._selected

    def click(self) -> None:
        self.clicked += 1

    def clear(self) -> None:
        self.typed.clear()

    def send_keys(self, value: str) -> None:
        self.typed.append(value)

    @property
    def value_text(self) -> str:
        return "".join(self.typed)


class FakeDriver:
    """Stateful Selenium WebDriver test double simulating BitMeUp login UI."""

    def __init__(self, *, scenario: str = "login_ok") -> None:
        self.scenario = scenario
        self.current_url = "about:blank"
        self.page_source = ""
        self.title = ""
        self.elements: dict[tuple[str, str], FakeElement] = {}
        self.quit_called = False
        self.visited_urls: list[str] = []
        self._cookies = [
            {
                "name": "bitmeup_id",
                "value": "400028783",
                "domain": ".bitmeup.com",
                "path": "/",
                "secure": True,
            },
            {
                "name": "sisabc999",
                "value": "sess-val",
                "domain": "spaceinvasion.bitmeup.com",
                "path": "/",
                "secure": True,
            },
        ]

    def get(self, url: str) -> None:
        self.visited_urls.append(url)
        self.current_url = url
        if self.scenario == "antibot":
            self.page_source = '<div class="cf-turnstile"></div>'
            self.title = "Just a moment..."
            return
        if self.scenario == "already_logged_in" or "action=externalLogin" in url:
            self.current_url = (
                "https://uni46.spaceinvasion.bitmeup.com/"
                if "action=externalLogin" in url
                else "https://spaceinvasion.bitmeup.com/?sid=abc999"
            )
            self.page_source = PORTAL_PAGE_HTML
            self.title = "SpaceInvasion Portal"
            self.elements = {(By.ID, "sip-tab-universe"): FakeElement("universe selection")}
            return

        self.page_source = LOGIN_PAGE_HTML
        self.title = "SpaceInvasion"
        email_el = FakeElement()
        pass_el = FakeElement()
        stay_el = FakeElement(selected=True)
        btn_el = FakeElement()

        def _on_submit() -> None:
            if self.scenario == "login_ok":
                self.current_url = "https://spaceinvasion.bitmeup.com/?sid=abc999"
                self.page_source = PORTAL_PAGE_HTML
                self.title = "SpaceInvasion Portal"
                self.elements = {(By.ID, "sip-tab-universe"): FakeElement("universe selection")}
            elif self.scenario == "bad_password":
                self.elements[(By.ID, "form.error.login")] = FakeElement(
                    "⚠ email or password wrong"
                )
            elif self.scenario == "2fa":
                tfa_el = FakeElement()

                def _on_tfa_send(val: str) -> None:
                    tfa_el.typed.append(val)
                    if val in (Keys.RETURN, Keys.ENTER):
                        self.current_url = "https://spaceinvasion.bitmeup.com/?sid=abc999"
                        self.page_source = PORTAL_PAGE_HTML
                        self.title = "SpaceInvasion Portal"
                        self.elements = {
                            (By.ID, "sip-tab-universe"): FakeElement("universe selection")
                        }

                tfa_el.send_keys = _on_tfa_send  # type: ignore[method-assign]
                self.elements[(By.ID, "2fa-bitmeup-code")] = tfa_el

        btn_el.click = _on_submit  # type: ignore[method-assign]
        self.elements = {
            (By.ID, "bitmeup.login.email"): email_el,
            (By.ID, "bitmeup.login.password"): pass_el,
            (By.ID, "slideLoggedIn"): stay_el,
            (By.CSS_SELECTOR, ".loginbutton"): btn_el,
        }

    def find_element(self, by: str, value: str) -> FakeElement:
        if (by, value) not in self.elements:
            raise NoSuchElementException(f"Missing {(by, value)}")
        return self.elements[(by, value)]

    def execute_script(self, script: str):
        return "SI_TPL.startPortal" in self.page_source

    def get_cookies(self) -> list[dict]:
        return list(self._cookies)

    def quit(self) -> None:
        self.quit_called = True


class TestBrowserLogin(unittest.TestCase):
    def test_browser_login_types_credentials_syncs_cookies_and_parses_universes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            driver = FakeDriver(scenario="login_ok")
            cfg = BrowserConfig(
                headless=True,
                profile_dir=Path(tmp) / "profile",
                timeout=3.0,
                typing_delay_range=(0.0, 0.0),
            )
            creds = Credentials(email="pilot@example.com", password="secret-password")

            result = login_with_browser(
                creds=creds,
                config=cfg,
                session_dir=Path(tmp) / ".sessions",
                driver_factory=lambda _c: driver,
            )

            self.assertTrue(driver.quit_called)
            self.assertFalse(result.already_logged_in)
            self.assertEqual(result.final_url, "https://spaceinvasion.bitmeup.com/?sid=abc999")
            self.assertEqual(len(result.universes), 2)
            self.assertEqual(result.universes[0].label, "Sirius")
            self.assertTrue(result.universes[0].has_account)
            self.assertTrue(result.cookie_path.exists())
            self.assertEqual(stat.S_IMODE(result.cookie_path.stat().st_mode), 0o600)

    def test_reuses_already_authenticated_browser_profile(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            driver = FakeDriver(scenario="already_logged_in")
            cfg = BrowserConfig(
                profile_dir=Path(tmp) / "profile",
                timeout=3.0,
                typing_delay_range=(0.0, 0.0),
            )
            creds = Credentials(email="pilot@example.com", password="secret")

            result = login_with_browser(
                creds=creds,
                config=cfg,
                session_dir=Path(tmp) / ".sessions",
                enter_universe="Sirius",
                driver_factory=lambda _c: driver,
            )

            self.assertTrue(result.already_logged_in)
            self.assertEqual(result.entered_universe, "Sirius")
            self.assertIn(
                "https://spaceinvasion.bitmeup.com/?action=externalLogin&instance=46&sid=abc999",
                driver.visited_urls,
            )

    def test_refuses_to_enter_unjoined_universe_without_allow_join(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            driver = FakeDriver(scenario="already_logged_in")
            cfg = BrowserConfig(profile_dir=Path(tmp) / "profile", timeout=3.0)
            creds = Credentials(email="pilot@example.com", password="secret")

            with self.assertRaises(ConfigurationError):
                login_with_browser(
                    creds=creds,
                    config=cfg,
                    session_dir=Path(tmp) / ".sessions",
                    enter_universe="GeNeSIS 2527",
                    allow_join=False,
                    driver_factory=lambda _c: driver,
                )

    def test_handles_two_factor_prompt_and_missing_code(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            driver = FakeDriver(scenario="2fa")
            cfg = BrowserConfig(
                profile_dir=Path(tmp) / "profile",
                timeout=3.0,
                typing_delay_range=(0.0, 0.0),
            )
            creds = Credentials(email="pilot@example.com", password="secret")

            result = login_with_browser(
                creds=creds,
                config=cfg,
                session_dir=Path(tmp) / ".sessions",
                two_factor_provider=lambda: "123456",
                driver_factory=lambda _c: driver,
            )
            self.assertEqual(result.final_url, "https://spaceinvasion.bitmeup.com/?sid=abc999")

            driver2 = FakeDriver(scenario="2fa")
            with self.assertRaises(TwoFactorRequiredError):
                login_with_browser(
                    creds=creds,
                    config=cfg,
                    session_dir=Path(tmp) / ".sessions",
                    driver_factory=lambda _c: driver2,
                )

    def test_surfaces_login_error_without_exposing_password(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            driver = FakeDriver(scenario="bad_password")
            cfg = BrowserConfig(
                profile_dir=Path(tmp) / "profile",
                timeout=3.0,
                typing_delay_range=(0.0, 0.0),
            )
            creds = Credentials(email="pilot@example.com", password="my-secret-password")

            with self.assertRaises(LoginRejectedError) as ctx:
                login_with_browser(
                    creds=creds,
                    config=cfg,
                    session_dir=Path(tmp) / ".sessions",
                    driver_factory=lambda _c: driver,
                )
            self.assertIn("email or password wrong", str(ctx.exception))
            self.assertNotIn("my-secret-password", str(ctx.exception))

    def test_antibot_gate_raises_when_headless_or_non_interactive(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            driver = FakeDriver(scenario="antibot")
            cfg = BrowserConfig(
                headless=True,
                profile_dir=Path(tmp) / "profile",
                timeout=3.0,
            )
            creds = Credentials(email="pilot@example.com", password="secret")

            with self.assertRaises(AntiBotChallengeError):
                login_with_browser(
                    creds=creds,
                    config=cfg,
                    session_dir=Path(tmp) / ".sessions",
                    driver_factory=lambda _c: driver,
                )

    def test_attach_fails_cleanly_when_debug_port_closed(self) -> None:
        cfg = BrowserConfig(attach=True, debug_port=19999)
        with unittest.mock.patch(
            "spaceinvasion.browser.is_debug_port_open", return_value=False
        ):
            with self.assertRaises(SpaceInvasionAuthError):
                open_browser_session(cfg)

    def test_attach_reuses_live_ingame_page_and_detaches_without_quitting(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            driver = FakeDriver(scenario="already_logged_in")
            driver.current_url = "https://spaceinvasion.bitmeup.com/indexGame.es?sid=live123"
            driver.page_source = '<iframe id="F_Hauptframe"></iframe>'
            driver.elements = {(By.ID, "F_Hauptframe"): FakeElement("frame")}
            driver.service = MagicMock()
            driver.quit = MagicMock()

            cfg = BrowserConfig(
                profile_dir=Path(tmp) / "profile",
                debug_port=9222,
                attach=True,
                detach=True,
            )
            creds = Credentials(
                email="pilot@example.com", password="secret", universe="RETRO"
            )

            with unittest.mock.patch(
                "spaceinvasion.browser.open_browser_session",
                return_value=(driver, None, True),
            ):
                result = login_with_browser(
                    creds=creds,
                    config=cfg,
                    session_dir=Path(tmp) / ".sessions",
                )

            self.assertTrue(result.already_logged_in)
            self.assertEqual(result.debug_address, "127.0.0.1:9222")
            self.assertEqual(driver.visited_urls, [])
            driver.service.stop.assert_called_once()
            driver.quit.assert_not_called()

    def test_close_or_detach_driver_respects_keep_running(self) -> None:
        driver = MagicMock()
        close_or_detach_driver(driver, None, keep_running=True)
        driver.service.stop.assert_called_once()
        driver.quit.assert_not_called()

        driver2 = MagicMock()
        close_or_detach_driver(driver2, None, keep_running=False)
        driver2.quit.assert_called_once()

    def test_multi_tab_switches_to_ingame_and_ignores_control_ui_tab(self) -> None:
        from spaceinvasion.client import GameBrowserClient

        tab_ui = MagicMock()
        tab_ui_switch = MagicMock()
        tab_portal = MagicMock()

        state_by_tab = {
            "ui": {"url": "http://127.0.0.1:8787/", "frames": False},
            "game": {"url": "https://spaceinvasion.bitmeup.com/indexGame.es?sid=live123", "frames": True},
        }
        active = {"handle": "ui"}

        driver = MagicMock()
        type(driver).window_handles = property(lambda _s: ["ui", "game"])
        type(driver).current_window_handle = property(lambda _s: active["handle"])
        type(driver).current_url = property(lambda _s: state_by_tab[active["handle"]]["url"])

        def _switch_window(h: str) -> None:
            active["handle"] = h

        driver.switch_to.window.side_effect = _switch_window

        def _find_el(_by: str, val: str):
            if active["handle"] != "game" or val not in ("F_planets", "F_Hauptframe", "F_Navi", "json-container"):
                raise NoSuchElementException(val)
            return FakeElement(val)

        driver.find_element.side_effect = _find_el

        client = GameBrowserClient(driver, detach_on_close=True)
        client.ensure_in_game()
        self.assertEqual(active["handle"], "game")

        # Also reset to UI tab and verify login_with_browser does not overwrite 127.0.0.1:8787
        active["handle"] = "ui"
        driver.page_source = '<iframe id="F_Hauptframe"></iframe>'
        with tempfile.TemporaryDirectory() as tmp:
            driver.get_cookies.return_value = []
            with unittest.mock.patch(
                "spaceinvasion.browser.open_browser_session",
                return_value=(driver, None, True),
            ):
                res = login_with_browser(
                    creds=Credentials(email="pilot@example.com", password="secret", universe="RETRO"),
                    config=BrowserConfig(
                        profile_dir=Path(tmp) / "profile",
                        debug_port=9222,
                        attach=True,
                        detach=True,
                    ),
                    session_dir=Path(tmp) / ".sessions",
                    require_in_game=True,
                )
        driver.get.assert_not_called()
        self.assertEqual(active["handle"], "game")
        self.assertTrue(res.already_logged_in)

    def test_require_in_game_auto_enters_single_active_universe_and_errors_if_stuck(self) -> None:
        class PortalStickyDriver(FakeDriver):
            def get(self, url: str) -> None:
                self.visited_urls.append(url)
                self.current_url = "https://spaceinvasion.bitmeup.com/?sid=abc999"
                self.page_source = PORTAL_PAGE_HTML
                self.title = "SpaceInvasion Portal"
                self.elements = {(By.ID, "sip-tab-universe"): FakeElement("universe selection")}

        with tempfile.TemporaryDirectory() as tmp:
            driver = PortalStickyDriver(scenario="already_logged_in")
            with self.assertRaises(SpaceInvasionAuthError):
                login_with_browser(
                    creds=Credentials(email="pilot@example.com", password="secret", universe=None),
                    config=BrowserConfig(
                        profile_dir=Path(tmp) / "profile",
                        timeout=0.4,
                    ),
                    session_dir=Path(tmp) / ".sessions",
                    require_in_game=True,
                    driver_factory=lambda _c: driver,
                )
            self.assertIn(
                "https://spaceinvasion.bitmeup.com/?action=externalLogin&instance=46&sid=abc999",
                driver.visited_urls,
            )

    def test_adv_browser_reaps_sigign_child_across_all_cleanup_paths_no_resource_warning(self) -> None:
        import gc
        import subprocess
        import sys
        import time
        from spaceinvasion import browser

        def _spawn_sigign(flag_path: Path) -> subprocess.Popen:
            code = (
                "import signal, time; "
                "signal.signal(signal.SIGTERM, signal.SIG_IGN); "
                f"open({str(flag_path)!r}, 'w', encoding='utf-8').write('1'); "
                "time.sleep(60)"
            )
            proc = subprocess.Popen(
                [sys.executable, "-c", code],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            deadline = time.monotonic() + 5.0
            while not flag_path.exists() and time.monotonic() < deadline:
                time.sleep(0.02)
            self.assertTrue(flag_path.exists())
            return proc

        with tempfile.TemporaryDirectory() as tmp:
            tp = Path(tmp)
            # 1. close_or_detach_driver(keep_running=False) kills SIG_IGN child
            p1 = _spawn_sigign(tp / "f1.flag")
            try:
                browser.close_or_detach_driver(MagicMock(), p1, keep_running=False)
                self.assertIsNotNone(p1.poll())
            finally:
                if p1.poll() is None:
                    p1.kill()
                p1.wait(timeout=5.0)
            del p1
            gc.collect()

            # 2. open_browser_session cleanup when attach_chrome_driver raises
            p2 = _spawn_sigign(tp / "f2.flag")
            try:
                cfg = BrowserConfig(profile_dir=tp / "prof2", debug_port=19998)
                with (
                    unittest.mock.patch("spaceinvasion.browser.is_debug_port_open", return_value=False),
                    unittest.mock.patch("spaceinvasion.browser.launch_chrome_with_debug_port", return_value=p2),
                    unittest.mock.patch("spaceinvasion.browser.attach_chrome_driver", side_effect=RuntimeError("attach boom")),
                ):
                    with self.assertRaises(RuntimeError):
                        browser.open_browser_session(cfg)
                self.assertIsNotNone(p2.poll())
            finally:
                if p2.poll() is None:
                    p2.kill()
                p2.wait(timeout=5.0)
            del p2
            gc.collect()

            # 3. launch_chrome_with_debug_port timeout cleanup reaps SIG_IGN child
            p3 = _spawn_sigign(tp / "f3.flag")
            try:
                cfg3 = BrowserConfig(profile_dir=tp / "prof3", debug_port=19997, timeout=0.1)
                with (
                    unittest.mock.patch("spaceinvasion.browser.find_chrome_binary", return_value="/usr/bin/true"),
                    unittest.mock.patch("spaceinvasion.browser.is_debug_port_open", return_value=False),
                    unittest.mock.patch("spaceinvasion.browser.subprocess.Popen", return_value=p3),
                    unittest.mock.patch("spaceinvasion.browser.time.monotonic", side_effect=[0.0, 0.0, 20.0]),
                ):
                    with self.assertRaises(SpaceInvasionAuthError):
                        browser.launch_chrome_with_debug_port(cfg3, 19997)
                self.assertIsNotNone(p3.poll())
            finally:
                if p3.poll() is None:
                    p3.kill()
                p3.wait(timeout=5.0)
            del p3
            gc.collect()

    def test_r7_sync_browser_cookies_closes_http_session_and_clamps_nan_inf_timeouts(self) -> None:
        from spaceinvasion import browser

        with tempfile.TemporaryDirectory() as tmp:
            tp = Path(tmp)
            closed_flags: list[bool] = []

            class _TrackedSession:
                def __init__(self) -> None:
                    import requests

                    self._inner = requests.Session()
                    self.cookies = self._inner.cookies
                    self.closed = False

                def __enter__(self) -> "_TrackedSession":
                    return self

                def __exit__(self, *_exc: object) -> None:
                    self.close()

                def close(self) -> None:
                    self.closed = True
                    closed_flags.append(True)
                    self._inner.close()

            drv = FakeDriver(scenario="already_logged_in")
            creds = Credentials(email="pilot@example.com", password="secret")
            with unittest.mock.patch("spaceinvasion.browser.build_http_session", side_effect=_TrackedSession):
                cpath, cnames = browser._sync_browser_cookies(
                    drv, tp / ".sessions", creds, "https://spaceinvasion.bitmeup.com/"
                )
            self.assertTrue(cpath.exists())
            self.assertIn("bitmeup_id", cnames)
            self.assertEqual(closed_flags, [True])

            seen_urlopen_timeouts: list[object] = []

            def _fake_urlopen(url: str, timeout: object = None):
                seen_urlopen_timeouts.append(timeout)
                resp = MagicMock()
                resp.status = 200
                resp.__enter__.return_value = resp
                resp.__exit__.return_value = None
                return resp

            with unittest.mock.patch("spaceinvasion.browser.urllib.request.urlopen", side_effect=_fake_urlopen):
                self.assertTrue(browser.is_debug_port_open(9222, timeout=float("nan")))
                self.assertTrue(browser.is_debug_port_open(9222, timeout=float("inf")))
                self.assertTrue(browser.is_debug_port_open(9222, timeout=1e9))
            self.assertEqual(seen_urlopen_timeouts, [0.8, 0.8, 60.0])

            for bad_t in (float("nan"), float("inf"), float("-inf")):
                self.assertEqual(
                    browser._finite_bounded_timeout(bad_t, default=25.0, min_val=10.0, max_val=300.0),
                    25.0,
                )


class TestLoginCli(unittest.TestCase):
    def test_login_cli_help_verify_http_browser_redaction_and_raw_dataclass_preserved(self) -> None:
        import io
        import json
        import os
        from unittest.mock import patch
        import login as login_cli
        from spaceinvasion.auth import LoginResult, UniverseOption
        from spaceinvasion.browser import BrowserLoginResult

        with patch.dict(os.environ, {"SPACEINVASION_NO_REEXEC": "1"}):
            with patch("sys.stdout", new_callable=io.StringIO):
                with self.assertRaises(SystemExit) as cm:
                    login_cli.main(["--help"])
                self.assertEqual(cm.exception.code, 0)

            secret_sid = "SECRET_SID_LOGIN_777"
            raw_url = f"https://uni31.bitmeup.com/indexGame.es?sid={secret_sid}"
            raw_uni = UniverseOption(
                label="Retro",
                url=raw_url,
                universe_id=31,
                speed=float("inf"),
                flight_speed=float("nan"),
                has_account=True,
            )
            # In-memory dataclasses must keep raw URLs untouched
            self.assertEqual(raw_uni.url, raw_url)

            # --verify-session active & expired + strict JSON + sid= redaction
            for ok_flag, exp_rc in ((True, 0), (False, 1)):
                out = io.StringIO()
                with (
                    patch.object(login_cli, "verify_saved_session", return_value=(ok_flag, raw_url, f"Title ?sid={secret_sid}")),
                    patch("sys.stdout", out),
                ):
                    rc = login_cli.main(["--verify-session", "--json"])
                self.assertEqual(rc, exp_rc)
                txt = out.getvalue()
                self.assertNotIn(secret_sid, txt)
                parsed = json.loads(txt, parse_constant=lambda c: (_ for _ in ()).throw(ValueError(c)))
                self.assertEqual(parsed["authenticated"], ok_flag)

            creds = Credentials(email="pilot@example.com", password="secret", universe="Retro")
            http_res = LoginResult(
                account_name="default",
                email=f"pilot+sid={secret_sid}@example.com",
                landing_url="https://spaceinvasion.bitmeup.com/",
                final_url=raw_url,
                page_title="Game",
                cookie_path=Path("/tmp/cookies.json"),
                cookie_names=("PHPSESSID",),
                universes=(raw_uni,),
            )
            self.assertEqual(http_res.final_url, raw_url)
            out_http = io.StringIO()
            with (
                patch.object(login_cli, "load_credentials", return_value=creds),
                patch.object(login_cli, "login", return_value=http_res),
                patch("sys.stdout", out_http),
            ):
                rc_http = login_cli.main(["--mode", "http", "--json"])
            self.assertEqual(rc_http, 0)
            self.assertNotIn(secret_sid, out_http.getvalue())
            json.loads(out_http.getvalue(), parse_constant=lambda c: (_ for _ in ()).throw(ValueError(c)))

            br_res = BrowserLoginResult(
                account_name="default",
                email="pilot@example.com",
                final_url=raw_url,
                page_title="Game",
                profile_dir=Path("/tmp/prof"),
                cookie_path=Path("/tmp/cookies.json"),
                cookie_names=("PHPSESSID",),
                already_logged_in=True,
                universes=(raw_uni,),
                entered_universe=f"Retro ?sid={secret_sid}",
            )
            self.assertEqual(br_res.final_url, raw_url)
            out_br = io.StringIO()
            with (
                patch.object(login_cli, "load_credentials", return_value=creds),
                patch.object(login_cli, "login_with_browser", return_value=br_res),
                patch("sys.stdout", out_br),
            ):
                rc_br = login_cli.main(["--mode", "browser", "--headless"])
            self.assertEqual(rc_br, 0)
            self.assertNotIn(secret_sid, out_br.getvalue())

            err_buf = io.StringIO()
            with (
                patch.object(login_cli, "load_credentials", side_effect=SpaceInvasionAuthError(f"boom ?sid={secret_sid}")),
                patch("sys.stderr", err_buf),
            ):
                rc_err = login_cli.main(["--mode", "http"])
            self.assertEqual(rc_err, 1)
            self.assertNotIn(secret_sid, err_buf.getvalue())
            self.assertIn("REDACTED", err_buf.getvalue())

            huge_uni = UniverseOption(
                label="Extreme ?sid=CLI_SECRET_999",
                url=raw_url,
                universe_id=10**5000,
                speed=10**400,
                flight_speed=10**400,
                has_account=True,
            )
            uni_out = io.StringIO()
            with patch("sys.stdout", uni_out):
                login_cli._print_universes((huge_uni,))
            printed = uni_out.getvalue()
            self.assertIn("id=~1e5000", printed)
            self.assertIn("?", printed)
            self.assertNotIn("CLI_SECRET_999", printed)
            self.assertEqual(huge_uni.url, raw_url)

    def test_r10_browser_port_bool_bounds_and_timeout_bytes(self) -> None:
        from spaceinvasion.browser import _finite_bounded_timeout, is_debug_port_open

        for bad_port in (True, False, 0, -1, 65536, 10**5000):
            self.assertFalse(is_debug_port_open(bad_port))  # type: ignore[arg-type]

        self.assertEqual(_finite_bounded_timeout(b"12.5", default=15.0), 12.5)
        self.assertEqual(_finite_bounded_timeout("\u0661\u0662", default=15.0), 15.0)
        self.assertEqual(_finite_bounded_timeout({1: 2}, default=15.0), 15.0)
        self.assertEqual(_finite_bounded_timeout(frozenset({1}), default=15.0), 15.0)

    def test_r12_login_cli_redacts_password_auth_and_emits_json_error_when_json_flag_set(self) -> None:
        import io
        import json
        from unittest.mock import patch
        import login as login_cli
        from spaceinvasion.auth import LoginResult
        from spaceinvasion.browser import _safe_browser_err

        out_json = io.StringIO()
        err_json = io.StringIO()
        with (
            patch.object(
                login_cli,
                "load_credentials",
                side_effect=SpaceInvasionAuthError("Authorization: Bearer CLI_BEAR_SEC ?password=CLI_PW_SEC"),
            ),
            patch("sys.stdout", out_json),
            patch("sys.stderr", err_json),
        ):
            rc_json = login_cli.main(["--json"])
        self.assertEqual(rc_json, 1)
        parsed = json.loads(out_json.getvalue())
        self.assertIn("error", parsed)
        combined = out_json.getvalue() + err_json.getvalue()
        self.assertNotIn("CLI_BEAR_SEC", combined)
        self.assertNotIn("CLI_PW_SEC", combined)
        self.assertIn("authorization: bearer redacted", combined.lower())

        out_http_err = io.StringIO()
        err_http_err = io.StringIO()
        creds = Credentials(
            account_name="default",
            email="u@example.com",
            password="pw",
            universe="RETRO",
        )
        with (
            patch.object(login_cli, "load_credentials", return_value=creds),
            patch.object(
                login_cli,
                "login",
                side_effect=SpaceInvasionAuthError(
                    10**5000,
                    "http err ?sid=HTTP_ERR_SID&password=HTTP_ERR_PW Authorization: Bearer HTTP_ERR_BEAR",
                ),
            ),
            patch("sys.stdout", out_http_err),
            patch("sys.stderr", err_http_err),
        ):
            rc_http_err = login_cli.main(["--mode", "http", "--json"])
        self.assertEqual(rc_http_err, 1)
        parsed_http_err = json.loads(out_http_err.getvalue())
        self.assertFalse(parsed_http_err.get("ok"))
        self.assertIn("error", parsed_http_err)
        comb_http_err = out_http_err.getvalue() + err_http_err.getvalue()
        for sec in ("HTTP_ERR_SID", "HTTP_ERR_PW", "HTTP_ERR_BEAR"):
            self.assertNotIn(sec, comb_http_err)

        out_http_ok = io.StringIO()
        h_res = LoginResult(
            account_name="default",
            email="u@example.com",
            landing_url="https://example.com/?password=RAW_LAND_PW",
            final_url="https://example.com/?password=RAW_HTTP_PW&sid=RAW_HTTP_SID",
            page_title="Title?password=RAW_TITLE_PW",
            cookie_path=Path("/tmp/?password=RAW_COOK_PW/cookies.json"),
            cookie_names=("CK",),
            universes=(),
        )
        with (
            patch.object(login_cli, "load_credentials", return_value=creds),
            patch.object(login_cli, "login", return_value=h_res),
            patch("sys.stdout", out_http_ok),
        ):
            rc_http_ok = login_cli.main(["--mode", "http", "--json"])
        self.assertEqual(rc_http_ok, 0)
        for sec in ("RAW_LAND_PW", "RAW_HTTP_PW", "RAW_HTTP_SID", "RAW_TITLE_PW", "RAW_COOK_PW"):
            self.assertNotIn(sec, out_http_ok.getvalue())

        err_plain = io.StringIO()
        with (
            patch.object(
                login_cli,
                "load_credentials",
                side_effect=SpaceInvasionAuthError("?password=PLAIN_PW_SEC"),
            ),
            patch("sys.stderr", err_plain),
        ):
            rc_plain = login_cli.main([])
        self.assertEqual(rc_plain, 1)
        self.assertNotIn("PLAIN_PW_SEC", err_plain.getvalue())
        self.assertIn("REDACTED", err_plain.getvalue())

        br_err = _safe_browser_err(RuntimeError(10**5000, "Authorization: Bearer BR_BEAR_SEC"))
        self.assertNotIn("BR_BEAR_SEC", br_err)
        self.assertIn("authorization: bearer redacted", br_err.lower())

    def test_r13_login_cli_nested_exc_redacts_before_truncation(self) -> None:
        import time
        import login as login_cli

        exc_300 = RuntimeError(10**5000, {"password": "LEAK300_" + ("X" * 300)})
        out_300 = login_cli._safe_cli_str(exc_300)
        self.assertNotIn("LEAK300_", out_300)
        self.assertIn("REDACTED", out_300)

        cyc = RuntimeError("cyc")
        cyc.args = (10**5000, cyc)
        self.assertIsInstance(login_cli._safe_cli_str(cyc), str)

        # 8-way diamond DAG does not hang
        node: BaseException = RuntimeError(10**5000, "?password=DAG_PW_SEC")
        for _ in range(18):
            node = RuntimeError(10**5000, node, node, node, node, node, node, node)
        t0 = time.perf_counter()
        dag_out = login_cli._safe_cli_str(node)
        self.assertLess((time.perf_counter() - t0) * 1000.0, 200.0)
        self.assertNotIn("DAG_PW_SEC", dag_out)

    def test_attach_and_close_configures_timeouts_and_reaps_chromedriver_process(self) -> None:
        import subprocess
        import sys
        from selenium.common.exceptions import WebDriverException
        from spaceinvasion.session import browser as browser_mod

        # 1. _configure_driver_timeouts sets page/script timeouts and finite command_executor pool timeout
        class _FakeCfg:
            timeout = None

        class _FakePool:
            def __init__(self) -> None:
                self.cleared = False

            def clear(self) -> None:
                self.cleared = True

        class _FakeExecutor:
            def __init__(self) -> None:
                self._client_config = _FakeCfg()
                self._conn = _FakePool()
                self.rebuilt = 0
                self.closed = False

            def _get_connection_manager(self) -> _FakePool:
                self.rebuilt += 1
                return _FakePool()

            def close(self) -> None:
                self.closed = True

        class _FakeDriver:
            def __init__(self, service) -> None:
                self.page_load_timeout = None
                self.script_timeout = None
                self.command_executor = _FakeExecutor()
                self.service = service

            def set_page_load_timeout(self, t: float) -> None:
                self.page_load_timeout = t

            def set_script_timeout(self, t: float) -> None:
                self.script_timeout = t

        stub_proc = subprocess.Popen(
            [sys.executable, "-c", "import time; time.sleep(30)"],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        try:
            class _StubbornService:
                def __init__(self, proc: subprocess.Popen) -> None:
                    self.process = proc
                    self.stopped = 0

                def stop(self) -> None:
                    self.stopped += 1  # Simulates chromedriver ignoring /shutdown

            svc = _StubbornService(stub_proc)
            drv = _FakeDriver(svc)
            old_pool = drv.command_executor._conn
            browser_mod._configure_driver_timeouts(drv, 25.0)
            self.assertEqual(drv.page_load_timeout, 25.0)
            self.assertEqual(drv.script_timeout, 25.0)
            self.assertEqual(drv.command_executor._client_config.timeout, 35.0)
            self.assertTrue(old_pool.cleared)
            self.assertEqual(drv.command_executor.rebuilt, 1)

            close_or_detach_driver(drv, keep_running=True)
            self.assertEqual(svc.stopped, 1)
            self.assertTrue(drv.command_executor.closed)
            self.assertIsNotNone(stub_proc.poll())
        finally:
            if stub_proc.poll() is None:
                stub_proc.kill()
            stub_proc.wait(timeout=5.0)

        # 2. Failed attach_chrome_driver reaps ChromeService process instead of leaking it
        fail_proc = subprocess.Popen(
            [sys.executable, "-c", "import time; time.sleep(30)"],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        try:
            class _FailService:
                def __init__(self) -> None:
                    self.process = fail_proc
                    self.stopped = 0

                def stop(self) -> None:
                    self.stopped += 1

            created_svc = _FailService()
            with (
                unittest.mock.patch.object(browser_mod, "ChromeService", return_value=created_svc),
                unittest.mock.patch.object(
                    browser_mod.webdriver,
                    "Chrome",
                    side_effect=WebDriverException("session not created"),
                ),
            ):
                with self.assertRaises(SpaceInvasionAuthError):
                    browser_mod.attach_chrome_driver(port=9222, timeout=20.0)
            self.assertEqual(created_svc.stopped, 1)
            self.assertIsNotNone(fail_proc.poll())
        finally:
            if fail_proc.poll() is None:
                fail_proc.kill()
            fail_proc.wait(timeout=5.0)


if __name__ == "__main__":
    unittest.main()
