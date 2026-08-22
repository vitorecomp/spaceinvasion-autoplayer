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


if __name__ == "__main__":
    unittest.main()
