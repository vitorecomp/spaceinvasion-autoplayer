"""Deterministic unit tests for `PageViewLimiter` and `GameBrowserClient`."""

from __future__ import annotations

import json
import re
import threading
import unittest
from unittest.mock import MagicMock, patch

from selenium.common.exceptions import ElementClickInterceptedException, NoSuchElementException
from selenium.webdriver.common.by import By

from spaceinvasion.auth import AntiBotChallengeError, SpaceInvasionAuthError
from spaceinvasion.client import (
    GameBrowserClient,
    PageViewLimiter,
    RateLimitError,
    reset_default_page_view_limiter,
)


class _FakeElement:
    def __init__(
        self,
        *,
        tag: str = "a",
        attrs: dict[str, str] | None = None,
        text: str = "",
        on_click=None,
        intercept_click: bool = False,
        frame_ref=None,
    ) -> None:
        self.tag_name = tag
        self._attrs = dict(attrs or {})
        self._text = text
        self._on_click = on_click
        self.intercept_click = intercept_click
        self.frame_ref = frame_ref
        self.value = self._attrs.get("value", "")
        self.clicks = 0
        self.js_clicks = 0
        self.scrolled = False

    def get_attribute(self, name: str) -> str:
        if name == "textContent":
            return self._text
        if name == "value":
            return self.value
        return self._attrs.get(name, "")

    def clear(self) -> None:
        self.value = ""

    def send_keys(self, txt: str) -> None:
        self.value += str(txt)

    def click(self) -> None:
        if self.intercept_click:
            raise ElementClickInterceptedException("intercepted by rahmen")
        self.clicks += 1
        if self._on_click is not None:
            self._on_click(self)


class _FakeFrame:
    def __init__(self, frame_id: str, href: str, html: str = "") -> None:
        self.frame_id = frame_id
        self.href = href
        self.html = html
        self.ready_state = "complete"
        self.nav_token: str | None = None
        self.reload_pending = False
        self.si_sid = ""
        self.elements_by_id: dict[str, _FakeElement] = {}
        self.elements_by_name: dict[str, list[_FakeElement]] = {}
        self.links: list[_FakeElement] = []
        self.submits: list[_FakeElement] = []
        self.on_navigate = None


class _FakeSwitchTo:
    def __init__(self, driver: _FakeDriver) -> None:
        self._driver = driver

    def default_content(self) -> None:
        self._driver.active_frame = None

    def frame(self, frame_el) -> None:
        if isinstance(frame_el, _FakeElement) and frame_el.frame_ref is not None:
            self._driver.active_frame = frame_el.frame_ref
        elif isinstance(frame_el, _FakeFrame):
            self._driver.active_frame = frame_el
        else:
            self._driver.active_frame = None


class _FakeDriver:
    def __init__(self, top_url: str = "https://spaceinvasion.bitmeup.com/indexGame.es?sid=abc123") -> None:
        self.current_url = top_url
        self.top_html = "<html><body>shell</body></html>"
        self.frames: dict[str, _FakeFrame] = {
            "F_Navi": _FakeFrame(
                "F_Navi",
                "https://spaceinvasion.bitmeup.com/indexInternal.es?action=internalMenu",
                "<html><body>menu</body></html>",
            ),
            "F_planets": _FakeFrame(
                "F_planets",
                "https://spaceinvasion.bitmeup.com/indexInternal.es?action=internalPlanetsOverview",
                "<html><body></body></html>",
            ),
            "F_Hauptframe": _FakeFrame(
                "F_Hauptframe",
                "https://spaceinvasion.bitmeup.com/indexInternal.es?action=internalHome",
                "<html><body>home</body></html>",
            ),
        }
        self.active_frame: _FakeFrame | None = None
        self.switch_to = _FakeSwitchTo(self)
        self.executed_scripts: list[str] = []
        self.navigated_urls: list[str] = []

    @property
    def page_source(self) -> str:
        if self.active_frame is None:
            return self.top_html
        return self.active_frame.html

    def find_element(self, by: str, value: str):
        if self.active_frame is None:
            if by == By.ID and value in self.frames:
                fr = self.frames[value]
                return _FakeElement(tag="iframe", attrs={"id": value, "src": fr.href}, frame_ref=fr)
            raise NoSuchElementException(f"No frame {value}")

        fr = self.active_frame
        if by == By.ID:
            if value in fr.elements_by_id:
                return fr.elements_by_id[value]
            raise NoSuchElementException(f"Missing #{value} in {fr.frame_id}")
        if by == By.NAME:
            els = fr.elements_by_name.get(value, [])
            if els:
                return els[0]
            raise NoSuchElementException(f"Missing name={value} in {fr.frame_id}")
        if by == By.CSS_SELECTOR:
            found = self.find_elements(by, value)
            if found:
                return found[0]
            raise NoSuchElementException(f"Missing css={value} in {fr.frame_id}")
        raise NoSuchElementException(f"Unsupported locator {by}={value}")

    def find_elements(self, by: str, value: str) -> list[_FakeElement]:
        if self.active_frame is None:
            return []
        fr = self.active_frame
        if by == By.NAME:
            return list(fr.elements_by_name.get(value, []))
        if by == By.CSS_SELECTOR:
            if 'type="submit"' in value:
                return list(fr.submits)
            if 'a[href*="sid="]' in value:
                return [el for el in fr.links if "sid=" in el.get_attribute("href")]
            if 'onclick*="p_galaxy"' in value:
                return [el for el in fr.links if "p_galaxy" in el.get_attribute("onclick")]
            if "action=" in value:
                m = re.search(r'action=([a-zA-Z0-9_]+)', value)
                act = m.group(1) if m else ""
                return [el for el in fr.links if f"action={act}" in el.get_attribute("href")]
        return []

    def execute_script(self, script: str, *args):
        self.executed_scripts.append(script)
        fr = self.active_frame
        if "scrollIntoView" in script and args:
            args[0].scrolled = True
            return None
        if "arguments[0].click()" in script and args:
            args[0].js_clicks += 1
            if args[0]._on_click is not None:
                args[0]._on_click(args[0])
            return None
        if "window.__siNavToken = arguments[0]" in script and fr is not None:
            fr.nav_token = args[0]
            return None
        if "window.__siReloadPending = 1" in script and fr is not None:
            if "location.replace" in script and args:
                fr.href = str(args[0])
                self.navigated_urls.append(fr.href)
            fr.reload_pending = False
            if callable(fr.on_navigate):
                fr.on_navigate(fr.href)
            return None
        if "SI_TPL.menu.sid" in script:
            return self.frames["F_Navi"].si_sid
        if "location.href = arguments[0]" in script and fr is not None and args:
            target = str(args[0])
            self.navigated_urls.append(target)
            fr.href = target
            fr.nav_token = None
            if callable(fr.on_navigate):
                fr.on_navigate(target)
            return None
        if "typeof window.__siNavToken" in script and fr is not None:
            if callable(fr.on_navigate):
                fr.on_navigate("__poll__")
            return {
                "ready": fr.ready_state,
                "href": fr.href,
                "token": fr.nav_token,
            }
        if "return Boolean(window.__siReloadPending)" in script and fr is not None:
            return fr.reload_pending
        if "return Boolean(document.getElementById('json-container'))" in script and fr is not None:
            return "json-container" in fr.elements_by_id
        if "return document.readyState" in script and fr is not None:
            return fr.ready_state
        if "return String(location.href" in script or "return location.href" in script:
            return fr.href if fr is not None else self.current_url
        return None


def _sample_planets_json() -> str:
    return json.dumps(
        {
            "info": {
                "ts": 1700000000,
                "user": {"userID": 103603, "nickname": "ventkx86"},
                "universe": {"id": 31},
            },
            "planets": [
                {
                    "id": 12476,
                    "name": "Colony",
                    "coords": "02:380:12",
                    "type": "normal",
                    "temperature": 31,
                    "fields": 230,
                    "resources": [5000, 3000, 4000, 1000],
                    "storage": [100000, 100000, 100000, 100000],
                    "energy": {"produced": 200, "used": 100},
                    "buildings": {"b1": {"level": 5, "name": "Pig iron mine"}},
                    "ships": {"s1": {"level": 2}},
                    "defense": {},
                }
            ],
            "researches": {"r1": {"level": 1, "name": "Mining technology"}},
            "specs": {},
        }
    )


class TestPageViewLimiterAndClient(unittest.TestCase):
    def setUp(self) -> None:
        reset_default_page_view_limiter()

    def test_page_view_limiter_enforces_cross_instance_inter_nav_delay(self) -> None:
        now = [100.0]
        sleeps: list[float] = []

        def fake_clock() -> float:
            return now[0]

        def fake_sleep(secs: float) -> None:
            sleeps.append(secs)
            now[0] += secs

        limiter = PageViewLimiter(
            min_delay_range=(1.0, 1.0),
            max_views_per_minute=24,
            reserved_interactive_views=6,
            clock=fake_clock,
            sleeper=fake_sleep,
        )
        d1 = _FakeDriver()
        d2 = _FakeDriver()
        d1.frames["F_Navi"].si_sid = "sid=aaa111"
        d2.frames["F_Navi"].si_sid = "sid=bbb222"

        c1 = GameBrowserClient(d1, page_limiter=limiter, min_nav_delay=(1.0, 1.0))
        c2 = GameBrowserClient(d2, page_limiter=limiter, min_nav_delay=(1.0, 1.0))

        c1.open_galaxy(galaxy=2, system=380)
        self.assertEqual(sleeps, [])
        now[0] = 100.2
        c2.open_galaxy(galaxy=2, system=381)
        self.assertEqual(len(sleeps), 1)
        self.assertAlmostEqual(sleeps[0], 0.8, places=6)
        self.assertEqual(limiter.views_last_60s(), 2)

    def test_page_view_limiter_rolling_60s_budget_and_window_pruning(self) -> None:
        now = [100.0]
        sleeps: list[float] = []

        limiter = PageViewLimiter(
            min_delay_range=(0.0, 0.0),
            max_views_per_minute=4,
            reserved_interactive_views=0,
            clock=lambda: now[0],
            sleeper=lambda s: (sleeps.append(s), now.__setitem__(0, now[0] + s))[0],
        )
        for t in (100.0, 101.0, 102.0, 103.0):
            now[0] = t
            limiter.acquire(priority="normal")
        self.assertEqual(sleeps, [])

        now[0] = 104.0
        limiter.acquire(priority="normal")
        self.assertEqual(len(sleeps), 1)
        self.assertAlmostEqual(sleeps[0], 56.0, places=6)
        self.assertGreaterEqual(now[0], 160.0)
        self.assertEqual(limiter.views_last_60s(), 4)

    def test_page_view_limiter_reserves_interactive_headroom_and_thread_safety(self) -> None:
        now = [200.0]
        sleeps: list[float] = []

        limiter = PageViewLimiter(
            min_delay_range=(0.0, 0.0),
            max_views_per_minute=6,
            reserved_interactive_views=2,
            clock=lambda: now[0],
            sleeper=lambda s: (sleeps.append(s), now.__setitem__(0, now[0] + s))[0],
        )
        for offset in range(4):
            now[0] = 200.0 + offset
            limiter.acquire(priority="normal")

        now[0] = 204.0
        with self.assertRaises(RateLimitError):
            limiter.acquire(priority="background", max_wait=1.0)
        # Interactive uses the 2 reserved slots immediately with 0 wait
        limiter.acquire(priority="interactive", max_wait=1.0)
        limiter.acquire(priority="interactive", max_wait=1.0)
        self.assertEqual(sleeps, [])
        self.assertEqual(limiter.views_last_60s(), 6)

        # Multi-threaded acquire serialization never double-books beyond cap
        mt_limiter = PageViewLimiter(
            min_delay_range=(0.0, 0.0),
            max_views_per_minute=20,
            reserved_interactive_views=0,
        )
        errors: list[Exception] = []

        def _worker() -> None:
            try:
                mt_limiter.acquire(priority="interactive", max_wait=0.5)
            except Exception as exc:
                errors.append(exc)

        threads = [threading.Thread(target=_worker) for _ in range(12)]
        for th in threads:
            th.start()
        for th in threads:
            th.join()
        self.assertEqual(errors, [])
        self.assertEqual(mt_limiter.views_last_60s(), 12)

    def test_page_view_limiter_cooldown_fast_fail_and_expiry(self) -> None:
        now = [500.0]
        sleeps: list[float] = []
        limiter = PageViewLimiter(
            min_delay_range=(0.0, 0.0),
            clock=lambda: now[0],
            sleeper=lambda s: sleeps.append(s),
        )
        limiter.record_rate_limit(45.0)
        self.assertEqual(limiter.rate_limit_hits, 1)
        self.assertAlmostEqual(limiter.rate_limit_remaining(), 45.0)

        with self.assertRaises(RateLimitError) as ctx:
            limiter.acquire(priority="interactive")
        self.assertAlmostEqual(ctx.exception.retry_after, 45.0)
        self.assertEqual(sleeps, [])  # Never sleeps 45s inside API lock

        now[0] = 546.0
        self.assertEqual(limiter.rate_limit_remaining(), 0.0)
        limiter.acquire(priority="interactive")
        self.assertEqual(limiter.views_last_60s(), 1)

    def test_read_planets_overview_refresh_paced_and_non_refresh_free(self) -> None:
        now = [300.0]
        sleeps: list[float] = []
        limiter = PageViewLimiter(
            min_delay_range=(1.0, 1.0),
            clock=lambda: now[0],
            sleeper=lambda s: (sleeps.append(s), now.__setitem__(0, now[0] + s))[0],
        )
        driver = _FakeDriver()
        driver.frames["F_planets"].elements_by_id["json-container"] = _FakeElement(
            tag="script",
            text=_sample_planets_json(),
        )
        client = GameBrowserClient(driver, page_limiter=limiter, min_nav_delay=(1.0, 1.0))

        # Non-refresh read consumes 0 views
        st_cached = client.read_planets_overview(refresh=False)
        self.assertEqual(st_cached.planets[0].coords, "02:380:12")
        self.assertEqual(limiter.views_last_60s(), 0)

        # Refresh read consumes 1 view and obeys inter-nav pacing
        client.read_planets_overview(refresh=True)
        self.assertEqual(limiter.views_last_60s(), 1)
        now[0] = 300.3
        client.read_planets_overview(refresh=True)
        self.assertEqual(len(sleeps), 1)
        self.assertAlmostEqual(sleeps[0], 0.7, places=6)

        # Wrong iframe page triggers location.replace(frame_src)
        driver.frames["F_planets"].href = "https://spaceinvasion.bitmeup.com/indexInternal.es?action=internalHome"
        driver.frames["F_planets"].frame_src = "https://spaceinvasion.bitmeup.com/indexInternal.es?action=internalPlanetsOverview"
        orig_find = driver.find_element

        def _find_with_src(by: str, val: str):
            el = orig_find(by, val)
            if driver.active_frame is None and val == "F_planets":
                el._attrs["src"] = driver.frames["F_planets"].frame_src
            return el

        with patch.object(driver, "find_element", side_effect=_find_with_src):
            client.read_planets_overview(refresh=False)
        self.assertIn(driver.frames["F_planets"].frame_src, driver.navigated_urls)

    def test_wait_frame_ready_nav_token_prevents_stale_galaxy_and_sendfleet_dom(self) -> None:
        now = [100.0]
        limiter = PageViewLimiter(
            min_delay_range=(0.0, 0.0),
            clock=lambda: now[0],
            sleeper=lambda s: now.__setitem__(0, now[0] + s),
        )
        driver = _FakeDriver()
        driver.frames["F_Navi"].si_sid = "sid=tok999"
        main = driver.frames["F_Hauptframe"]
        main.href = "https://spaceinvasion.bitmeup.com/indexInternal.es?action=internalGalaxy&galaxy=2&system=380"
        main.html = "<table><tr><td>1</td><td>Old380</td><td>OldOwner</td></tr></table>"

        # Simulate delayed frame replacement: poll #1 keeps old token & stale DOM, poll #2 swaps to sys381
        polls = [0]

        def _on_nav(url: str) -> None:
            if url == "__poll__":
                polls[0] += 1
                if polls[0] >= 2:
                    main.href = "https://spaceinvasion.bitmeup.com/indexInternal.es?action=internalGalaxy&galaxy=2&system=381"
                    main.html = "<table><tr><td>2</td><td>New381</td><td>NewOwner</td></tr></table>"
                    main.nav_token = None

        main.on_navigate = _on_nav
        # Keep token intact when script sets location.href so poll #1 catches stale DOM
        orig_exec = driver.execute_script

        def _exec_keeping_stale_token(script: str, *args):
            if "location.href = arguments[0]" in script:
                driver.navigated_urls.append(str(args[0]))
                return None  # Still showing old 380 DOM + token until poll #2!
            return orig_exec(script, *args)

        client = GameBrowserClient(driver, page_limiter=limiter, min_nav_delay=(0.0, 0.0), timeout=2.0)
        with patch.object(driver, "execute_script", side_effect=_exec_keeping_stale_token):
            with patch("spaceinvasion.client.time.sleep", return_value=None):
                html = client.open_galaxy(galaxy=2, system=381)
        self.assertIn("New381", html)
        self.assertNotIn("Old380", html)

        # Now test internalFleet -> internalSendFleet with nav_token
        main.on_navigate = None
        main.href = "https://spaceinvasion.bitmeup.com/indexInternal.es?action=internalFleet"
        main.html = "<form name='fleet'></form>"
        tok = client._mark_frame_navigation("F_Hauptframe")
        fleet_polls = [0]

        def _on_fleet_poll(url: str) -> None:
            if url == "__poll__":
                fleet_polls[0] += 1
                if fleet_polls[0] >= 2:
                    main.href = "https://spaceinvasion.bitmeup.com/indexInternal.es?action=internalSendFleet"
                    main.html = "<form action='internalSendFleet'><input type='radio' value='1'>Attack</form>"
                    main.nav_token = None

        main.on_navigate = _on_fleet_poll
        with patch("spaceinvasion.client.time.sleep", return_value=None):
            send_html = client._wait_frame_ready(
                "F_Hauptframe",
                expected_action="internalSendFleet",
                nav_token=tok,
            )
        self.assertIn("internalSendFleet", send_html)
        self.assertGreaterEqual(fleet_polls[0], 2)

    def test_wait_frame_ready_and_ensure_in_game_trigger_guards_immediately(self) -> None:
        now = [100.0]
        limiter = PageViewLimiter(
            min_delay_range=(0.0, 0.0),
            clock=lambda: now[0],
            sleeper=lambda s: now.__setitem__(0, now[0] + s),
        )
        driver = _FakeDriver()
        main = driver.frames["F_Hauptframe"]
        client = GameBrowserClient(driver, page_limiter=limiter, timeout=20.0)

        sleep_calls = [0]

        def _fail_on_excess_sleep(_sec: float) -> None:
            sleep_calls[0] += 1
            if sleep_calls[0] > 2:
                raise AssertionError("_wait_frame_ready spun instead of triggering page guard immediately")

        # (a) Rate limit HTML on URL lacking expected_action raises RateLimitError on poll #1
        main.href = "https://spaceinvasion.bitmeup.com/indexInternal.es?error=limit"
        main.html = "<div>You've made more than 30 page views at last minute.</div>"
        with patch("spaceinvasion.client.time.sleep", side_effect=_fail_on_excess_sleep):
            with self.assertRaises(RateLimitError):
                client._wait_frame_ready("F_Hauptframe", expected_action="internalBuildings")
        self.assertEqual(sleep_calls[0], 0)
        self.assertGreater(limiter.rate_limit_remaining(), 0.0)

        # (b) Cloudflare turnstile raises AntiBotChallengeError immediately
        limiter.reset()
        main.html = "<div class='cf-turnstile'>Verify</div>"
        with patch("spaceinvasion.client.time.sleep", side_effect=_fail_on_excess_sleep):
            with self.assertRaises(AntiBotChallengeError):
                client._wait_frame_ready("F_Hauptframe", expected_action="internalBuildings")
        self.assertEqual(sleep_calls[0], 0)

        # (c) ensure_in_game missing #json-container with throttle text raises RateLimitError
        driver.frames["F_planets"].elements_by_id.clear()
        driver.frames["F_planets"].html = "<div>You've made more than 30 page views at last minute!</div>"
        with self.assertRaises(RateLimitError):
            client.ensure_in_game()

        driver.frames["F_planets"].html = "<div class='cf-turnstile'>Challenge</div>"
        with self.assertRaises(AntiBotChallengeError):
            client.ensure_in_game()

    def test_open_galaxy_normalizes_sid_same_origin_p_galaxy_and_form_fallback(self) -> None:
        now = [400.0]
        limiter = PageViewLimiter(
            min_delay_range=(0.0, 0.0),
            clock=lambda: now[0],
            sleeper=lambda s: now.__setitem__(0, now[0] + s),
        )
        driver = _FakeDriver()
        client = GameBrowserClient(driver, page_limiter=limiter, min_nav_delay=(0.0, 0.0), timeout=2.0)

        # (a) SI_TPL.menu.sid = "sid=abc123" (no double sid=sid=)
        driver.frames["F_Navi"].si_sid = "sid=abc123"
        driver.frames["F_Hauptframe"].html = "<table><tr><td>4</td><td>P4</td><td>Sleepy</td></tr></table>"
        client.open_galaxy(galaxy=2, system=380)
        self.assertIn("action=internalGalaxy&sid=abc123&galaxy=2&system=380", driver.navigated_urls[-1])
        self.assertNotIn("sid=sid=", driver.navigated_urls[-1])

        # (b) Raw SI_TPL.menu.sid = "raw999" (adds sid= prefix)
        driver.frames["F_Navi"].si_sid = "raw999"
        client.open_galaxy(galaxy=2, system=381)
        self.assertIn("action=internalGalaxy&sid=raw999&galaxy=2&system=381", driver.navigated_urls[-1])

        # (c) F_Hauptframe at about:blank builds same-origin URL from F_Navi
        driver.frames["F_Hauptframe"].href = "about:blank"
        client.open_galaxy(galaxy=2, system=382)
        self.assertTrue(driver.navigated_urls[-1].startswith("https://spaceinvasion.bitmeup.com/indexInternal.es?"))

        # (d) Empty SI_TPL.menu.sid recovers sid from F_Navi link when present
        driver.frames["F_Navi"].si_sid = ""
        driver.current_url = "https://spaceinvasion.bitmeup.com/indexGame.es"
        driver.frames["F_Navi"].links = [
            _FakeElement(tag="a", attrs={"href": "indexInternal.es?action=internalHome&sid=link555"}),
        ]
        client.open_galaxy(galaxy=2, system=383)
        self.assertIn("sid=link555&galaxy=2&system=383", driver.navigated_urls[-1])

        # (e) No sid anywhere -> p_galaxy() fallback with scrollIntoView + ElementClickInterceptedException + form switch
        driver.frames["F_Navi"].si_sid = ""
        main = driver.frames["F_Hauptframe"]
        gal_inp = _FakeElement(tag="input", attrs={"name": "galaxy", "value": "1"})
        sys_inp = _FakeElement(tag="input", attrs={"name": "system", "value": "1"})
        main.elements_by_name["galaxy"] = [gal_inp]
        main.elements_by_name["system"] = [sys_inp]

        def _on_form_submit(_btn) -> None:
            main.href = f"https://spaceinvasion.bitmeup.com/indexInternal.es?action=internalGalaxy&galaxy={gal_inp.value}&system={sys_inp.value}"
            main.html = f"<input name='galaxy' value='{gal_inp.value}'><input name='system' value='{sys_inp.value}'><table><tr><td>5</td><td>Spacia</td><td>Spacia</td></tr></table>"
            main.nav_token = None

        submit_btn = _FakeElement(tag="input", attrs={"type": "submit"}, on_click=_on_form_submit)
        main.submits = [submit_btn]

        def _on_p_galaxy(_el) -> None:
            main.href = "https://spaceinvasion.bitmeup.com/indexInternal.es?action=internalGalaxy"
            main.html = "<input name='galaxy' value='1'><input name='system' value='1'><table><tr><td>1</td><td>Home</td><td>Me</td></tr></table>"
            main.nav_token = None

        p_gal_link = _FakeElement(
            tag="a",
            attrs={"href": "#", "onclick": "p_galaxy()"},
            intercept_click=True,
            on_click=_on_p_galaxy,
        )
        driver.frames["F_Navi"].links = [p_gal_link]

        html, actual_gal, actual_sys = client.open_galaxy_with_coords(galaxy=2, system=385)
        self.assertTrue(p_gal_link.scrolled)
        self.assertEqual(p_gal_link.js_clicks, 1)
        self.assertEqual((actual_gal, actual_sys), (2, 385))
        self.assertEqual(client.last_galaxy_coords, (2, 385))
        self.assertIn("Spacia", html)

        # (f) open_messages("spio") navigates to internalMessages&type=spio
        driver.frames["F_Navi"].si_sid = "sid=msg777"
        main.html = "<div>Espionage report 02:385:05 Pig iron: 12.000</div>"
        msg_html = client.open_messages("spio")
        self.assertIn("action=internalMessages&type=spio&sid=msg777", driver.navigated_urls[-1])
        self.assertIn("Espionage report", msg_html)

    def test_local_budget_rejection_uses_window_seconds_and_server_throttle_false(self) -> None:
        now = [100.0]
        sleeps: list[float] = []
        limiter = PageViewLimiter(
            min_delay_range=(0.0, 0.0),
            max_views_per_minute=10,
            reserved_interactive_views=3,
            window_seconds=30.0,
            clock=lambda: now[0],
            sleeper=lambda s: (sleeps.append(s), now.__setitem__(0, now[0] + s))[0],
        )
        for _ in range(7):
            limiter.acquire(priority="normal", max_wait=2.0)

        with self.assertRaises(RateLimitError) as ctx:
            limiter.acquire(priority="normal", max_wait=2.0)
        self.assertFalse(ctx.exception.server_throttle)
        self.assertIn("in 30s", str(ctx.exception))
        self.assertEqual(limiter.rate_limit_remaining(), 0.0)
        self.assertEqual(limiter.rate_limit_hits, 0)

        for _ in range(3):
            limiter.acquire(priority="interactive", max_wait=2.0)
        self.assertEqual(sleeps, [])

        limiter.record_rate_limit(45.0)
        with self.assertRaises(RateLimitError) as ctx2:
            limiter.acquire(priority="interactive", max_wait=2.0)
        self.assertTrue(ctx2.exception.server_throttle)

    def test_submit_galaxy_coords_form_scopes_to_enclosing_form(self) -> None:
        now = [200.0]
        limiter = PageViewLimiter(
            min_delay_range=(0.0, 0.0),
            clock=lambda: now[0],
            sleeper=lambda s: now.__setitem__(0, now[0] + s),
        )
        driver = _FakeDriver()
        main = driver.frames["F_Hauptframe"]
        main.href = "https://spaceinvasion.bitmeup.com/indexInternal.es?action=internalGalaxy"

        switch_btn = _FakeElement(tag="input", attrs={"type": "submit"})
        gal_submit_btn = _FakeElement(tag="input", attrs={"type": "submit"})

        class _ScopedForm(_FakeElement):
            def __init__(self, submits):
                super().__init__(tag="form")
                self._submits = submits
                self.submitted = 0

            def find_elements(self, by: str, value: str):
                if by == By.CSS_SELECTOR and 'type="submit"' in value:
                    return list(self._submits)
                return []

        gal_form = _ScopedForm([gal_submit_btn])

        class _ScopedInput(_FakeElement):
            def __init__(self, form_el, **kw):
                super().__init__(**kw)
                self._form_el = form_el

            def find_element(self, by: str, value: str):
                if by == By.XPATH and value == "ancestor::form[1]":
                    return self._form_el
                raise NoSuchElementException(value)

        gal_inp = _ScopedInput(gal_form, tag="input", attrs={"name": "galaxy", "value": "1"})
        sys_inp = _ScopedInput(gal_form, tag="input", attrs={"name": "system", "value": "1"})
        main.elements_by_name["galaxy"] = [gal_inp]
        main.elements_by_name["system"] = [sys_inp]
        main.submits = [switch_btn]

        def _on_gal_click(_btn) -> None:
            main.href = "https://spaceinvasion.bitmeup.com/indexInternal.es?action=internalGalaxy&galaxy=2&system=388"
            main.html = "<input name='galaxy' value='2'><input name='system' value='388'>"
            main.nav_token = None

        gal_submit_btn._on_click = _on_gal_click
        client = GameBrowserClient(driver, page_limiter=limiter, min_nav_delay=(0.0, 0.0), timeout=2.0)

        html = client._submit_galaxy_coords_form(galaxy=2, system=388, priority="interactive")
        self.assertIsNotNone(html)
        self.assertEqual(gal_submit_btn.clicks, 1)
        self.assertEqual(switch_btn.clicks, 0)

        # When enclosing form has no submit button, execute_script form.submit() is called without touching switch_btn
        gal_form._submits = []
        orig_exec = driver.execute_script

        def _exec_submit(script: str, *args):
            if "arguments[0].submit()" in script and args and args[0] is gal_form:
                gal_form.submitted += 1
                main.nav_token = None
                return None
            return orig_exec(script, *args)

        with patch.object(driver, "execute_script", side_effect=_exec_submit):
            client._submit_galaxy_coords_form(galaxy=2, system=389, priority="interactive")
        self.assertEqual(gal_form.submitted, 1)
        self.assertEqual(switch_btn.clicks, 0)

    def test_post_cooldown_recovery_reloads_stale_throttle_planets_and_home_dom(self) -> None:
        now = [100.0]
        limiter = PageViewLimiter(
            min_delay_range=(0.0, 0.0),
            clock=lambda: now[0],
            sleeper=lambda s: now.__setitem__(0, now[0] + s),
        )
        driver = _FakeDriver()
        driver.frames["F_Navi"].si_sid = "sid=rec123"
        planets_fr = driver.frames["F_planets"]
        planets_fr.elements_by_id.clear()
        planets_fr.html = "<div>You've made more than 30 page views at last minute!</div>"

        client = GameBrowserClient(driver, page_limiter=limiter, min_nav_delay=(0.0, 0.0), timeout=2.0)

        # First call detects throttle HTML and sets cooldown + recovery flag
        with self.assertRaises(RateLimitError):
            client.ensure_in_game()
        self.assertEqual(limiter.rate_limit_hits, 1)

        # Advance past cooldown; wire F_planets reload to restore #json-container
        now[0] += 46.0

        def _restore_planets(_url: str) -> None:
            planets_fr.html = "<html><body><script id='json-container'></script></body></html>"
            planets_fr.elements_by_id["json-container"] = _FakeElement(tag="script", text=_sample_planets_json())

        planets_fr.on_navigate = _restore_planets
        client.ensure_in_game()
        self.assertIn("json-container", planets_fr.elements_by_id)

        # Stale throttle HTML inside F_Hauptframe whose href still keeps action=internalHome must trigger reload
        main = driver.frames["F_Hauptframe"]
        main.href = "https://spaceinvasion.bitmeup.com/indexInternal.es?action=internalHome&sid=rec123"
        main.html = "<div>You've made more than 30 page views at last minute!</div>"

        def _restore_home(_url: str) -> None:
            main.href = "https://spaceinvasion.bitmeup.com/indexInternal.es?action=internalHome&sid=rec123"
            main.html = "<html><body><div>active factor: x20.0</div></body></html>"
            main.nav_token = None

        main.on_navigate = _restore_home
        state = client.read_full_snapshot(
            include_buildings=False,
            include_research=False,
            include_shipyard=False,
        )
        self.assertEqual(state.planets[0].coords, "02:380:12")
        self.assertEqual(state.speedup_factor, 20.0)
        self.assertTrue(any("action=internalHome" in u for u in driver.navigated_urls))

    def test_ensure_in_game_post_cooldown_recovery_preserves_flag_and_supports_interactive_priority(self) -> None:
        now = [200.0]
        limiter = PageViewLimiter(
            min_delay_range=(0.0, 0.0),
            max_views_per_minute=24,
            reserved_interactive_views=6,
            clock=lambda: now[0],
            sleeper=lambda s: now.__setitem__(0, now[0] + s),
        )
        driver = _FakeDriver()
        driver.frames["F_Navi"].si_sid = "sid=rec999"
        planets_fr = driver.frames["F_planets"]
        planets_fr.elements_by_id.clear()
        planets_fr.html = "<div>You've made more than 30 page views at last minute!</div>"

        def _restore_planets(_url: str) -> None:
            planets_fr.html = "<html><body><script id='json-container'></script></body></html>"
            planets_fr.elements_by_id["json-container"] = _FakeElement(
                tag="script",
                text=_sample_planets_json(),
            )

        planets_fr.on_navigate = _restore_planets
        client = GameBrowserClient(
            driver,
            page_limiter=limiter,
            min_nav_delay=(0.0, 0.0),
            timeout=2.0,
            max_limiter_wait=2.0,
        )

        # Record 18 normal views at t=200.0, then hit server throttle at t=202.0
        limiter.record_view(18)
        now[0] = 202.0
        with self.assertRaises(RateLimitError) as ctx1:
            client.ensure_in_game()
        self.assertTrue(ctx1.exception.server_throttle)
        self.assertEqual(limiter.rate_limit_hits, 1)
        self.assertTrue(limiter._throttle_recovery_pending)

        # Part (a): At t=248.0 (cooldown expired, 18 normal views still in 60s window),
        # normal-priority ensure_in_game() fails locally WITHOUT clearing _throttle_recovery_pending.
        now[0] = 248.0
        with self.assertRaises(RateLimitError) as ctx2:
            client.ensure_in_game()
        self.assertFalse(ctx2.exception.server_throttle)
        self.assertEqual(limiter.rate_limit_hits, 1)
        self.assertTrue(limiter._throttle_recovery_pending)

        # At t=261.0 (60s window clears), normal ensure_in_game() recovers cleanly with no spurious re-cooldown.
        now[0] = 261.0
        client.ensure_in_game()
        self.assertIn("json-container", planets_fr.elements_by_id)
        self.assertEqual(limiter.rate_limit_hits, 1)
        self.assertFalse(limiter._throttle_recovery_pending)

        # Part (b): After a failed normal ensure_in_game() at cooldown expiry, an interactive
        # read_full_snapshot(..., priority="interactive") recovers #json-container immediately via interactive slots.
        now[0] = 500.0
        limiter.reset()
        limiter.record_view(18)
        planets_fr.elements_by_id.clear()
        planets_fr.html = "<div>You've made more than 30 page views at last minute!</div>"
        main = driver.frames["F_Hauptframe"]
        main.href = "https://spaceinvasion.bitmeup.com/indexInternal.es?action=internalHome&sid=rec999"
        main.html = "<html><body><div>active factor: x20.0</div></body></html>"

        now[0] = 502.0
        with self.assertRaises(RateLimitError) as ctx3:
            client.ensure_in_game()
        self.assertTrue(ctx3.exception.server_throttle)
        self.assertEqual(limiter.rate_limit_hits, 1)

        now[0] = 548.0
        with self.assertRaises(RateLimitError) as ctx4:
            client.ensure_in_game(priority="normal")
        self.assertFalse(ctx4.exception.server_throttle)
        self.assertTrue(limiter._throttle_recovery_pending)
        self.assertEqual(limiter.rate_limit_hits, 1)

        state = client.read_full_snapshot(
            include_buildings=False,
            include_research=False,
            include_shipyard=False,
            priority="interactive",
        )
        self.assertEqual(state.planets[0].coords, "02:380:12")
        self.assertEqual(limiter.rate_limit_hits, 1)
        self.assertLessEqual(limiter.views_last_60s(), 24)
        self.assertFalse(limiter._throttle_recovery_pending)

    def test_concurrent_ensure_in_game_post_cooldown_recovery_and_failure_unblocking(self) -> None:
        # Scenario A: Challenger 1 Probe 2C repro (gated F_planets reload + concurrent interactive callers)
        now = [200.0]
        limiter = PageViewLimiter(
            min_delay_range=(0.0, 0.0),
            max_views_per_minute=24,
            reserved_interactive_views=6,
            clock=lambda: now[0],
            sleeper=lambda s: now.__setitem__(0, now[0] + s),
        )
        driver = _FakeDriver()
        driver.frames["F_Navi"].si_sid = "sid=conc1"
        planets_fr = driver.frames["F_planets"]
        planets_fr.elements_by_id.clear()
        planets_fr.html = "<div>You've made more than 30 page views at last minute!</div>"

        client = GameBrowserClient(
            driver,
            page_limiter=limiter,
            min_nav_delay=(0.0, 0.0),
            timeout=2.0,
            max_limiter_wait=1.0,
        )
        limiter.record_view(18)
        now[0] = 202.0
        with self.assertRaises(RateLimitError):
            client.ensure_in_game(priority="interactive")
        self.assertEqual(limiter.rate_limit_hits, 1)
        self.assertTrue(limiter._throttle_recovery_pending)

        now[0] = 248.0  # past 45s cooldown while 18 normal views still occupy window
        reload_entered = threading.Event()
        allow_finish = threading.Event()

        def _gated_restore(_url: str) -> None:
            reload_entered.set()
            assert allow_finish.wait(timeout=3.0)
            planets_fr.html = "<html><body><script id='json-container'></script></body></html>"
            planets_fr.elements_by_id["json-container"] = _FakeElement(
                tag="script",
                text=_sample_planets_json(),
            )

        planets_fr.on_navigate = _gated_restore
        res_a: list[tuple[int, str]] = []
        res_lock = threading.Lock()

        def _run_ensure(idx: int, c: GameBrowserClient = client, prio: str = "interactive") -> None:
            try:
                c.ensure_in_game(priority=prio)
                with res_lock:
                    res_a.append((idx, "ok"))
            except Exception as exc:
                with res_lock:
                    res_a.append((idx, f"{type(exc).__name__}:{getattr(exc, 'server_throttle', None)}:{exc}"))

        t0 = threading.Thread(target=_run_ensure, args=(0,))
        t0.start()
        self.assertTrue(reload_entered.wait(timeout=2.0))
        # Calling limiter.rate_limit_remaining() while leader is paused must never deadlock
        self.assertEqual(limiter.rate_limit_remaining(), 0.0)
        self.assertTrue(limiter._throttle_recovery_in_flight)

        t1 = threading.Thread(target=_run_ensure, args=(1,))
        t1.start()
        threading.Event().wait(0.05)
        allow_finish.set()
        t0.join(timeout=3.0)
        t1.join(timeout=3.0)
        self.assertEqual(sorted(res_a), [(0, "ok"), (1, "ok")])
        self.assertEqual(limiter.rate_limit_hits, 1)
        self.assertEqual(limiter.views_last_60s(), 19)
        self.assertFalse(limiter._throttle_recovery_pending)
        self.assertFalse(limiter._throttle_recovery_in_flight)

        # Scenario B: Free-running Barrier(4) concurrent post-cooldown recovery
        now[0] = 500.0
        limiter.reset()
        limiter.record_view(18)
        planets_fr.elements_by_id.clear()
        planets_fr.html = "<div>You've made more than 30 page views at last minute!</div>"
        planets_fr.on_navigate = lambda _url: (
            setattr(planets_fr, "html", "<html><body><script id='json-container'></script></body></html>"),
            planets_fr.elements_by_id.__setitem__(
                "json-container",
                _FakeElement(tag="script", text=_sample_planets_json()),
            ),
        )
        now[0] = 502.0
        with self.assertRaises(RateLimitError):
            client.ensure_in_game(priority="interactive")
        now[0] = 548.0

        barrier = threading.Barrier(4)
        res_b: list[tuple[int, str]] = []

        def _run_barrier(idx: int) -> None:
            barrier.wait(timeout=3.0)
            try:
                client.ensure_in_game(priority="interactive")
                with res_lock:
                    res_b.append((idx, "ok"))
            except Exception as exc:
                with res_lock:
                    res_b.append((idx, f"{type(exc).__name__}:{exc}"))

        b_threads = [threading.Thread(target=_run_barrier, args=(i,)) for i in range(4)]
        for th in b_threads:
            th.start()
        for th in b_threads:
            th.join(timeout=3.0)
        self.assertEqual(sorted(res_b), [(0, "ok"), (1, "ok"), (2, "ok"), (3, "ok")])
        self.assertEqual(limiter.rate_limit_hits, 1)
        self.assertEqual(limiter.views_last_60s(), 19)

        # Scenario C: Concurrent normal at 18/18 fails locally while interactive recovers cleanly
        now[0] = 800.0
        limiter.reset()
        limiter.record_view(18)
        planets_fr.elements_by_id.clear()
        planets_fr.html = "<div>You've made more than 30 page views at last minute!</div>"
        now[0] = 802.0
        with self.assertRaises(RateLimitError):
            client.ensure_in_game(priority="normal")
        now[0] = 848.0

        res_c: dict[str, str] = {}

        def _run_c(role: str, prio: str) -> None:
            try:
                client.ensure_in_game(priority=prio)
                res_c[role] = "ok"
            except RateLimitError as exc:
                res_c[role] = f"RateLimitError:{exc.server_throttle}"

        tc_norm = threading.Thread(target=_run_c, args=("normal", "normal"))
        tc_norm.start()
        tc_norm.join(timeout=2.0)
        self.assertEqual(res_c["normal"], "RateLimitError:False")
        self.assertTrue(limiter._throttle_recovery_pending)
        self.assertEqual(limiter.rate_limit_hits, 1)

        tc_int = threading.Thread(target=_run_c, args=("interactive", "interactive"))
        tc_int.start()
        tc_int.join(timeout=2.0)
        self.assertEqual(res_c["interactive"], "ok")
        self.assertEqual(limiter.rate_limit_hits, 1)
        self.assertFalse(limiter._throttle_recovery_pending)

        # Scenario D1: Leader reload returns server throttle HTML -> both unblock with server_throttle=True, hits == 2
        now[0] = 1100.0
        limiter.reset()
        limiter.record_view(18)
        planets_fr.elements_by_id.clear()
        planets_fr.html = "<div>You've made more than 30 page views at last minute!</div>"
        planets_fr.on_navigate = None
        now[0] = 1102.0
        with self.assertRaises(RateLimitError):
            client.ensure_in_game(priority="interactive")
        now[0] = 1148.0

        d1_entered = threading.Event()
        d1_release = threading.Event()

        def _still_throttled(_url: str) -> None:
            d1_entered.set()
            assert d1_release.wait(timeout=3.0)
            planets_fr.html = "<div>You've made more than 30 page views at last minute!</div>"

        planets_fr.on_navigate = _still_throttled
        res_d1: list[tuple[int, str]] = []

        def _run_d1(idx: int) -> None:
            try:
                client.ensure_in_game(priority="interactive")
                with res_lock:
                    res_d1.append((idx, "ok"))
            except RateLimitError as exc:
                with res_lock:
                    res_d1.append((idx, f"RateLimitError:{exc.server_throttle}"))

        td0 = threading.Thread(target=_run_d1, args=(0,))
        td0.start()
        self.assertTrue(d1_entered.wait(timeout=2.0))
        td1 = threading.Thread(target=_run_d1, args=(1,))
        td1.start()
        threading.Event().wait(0.05)
        d1_release.set()
        td0.join(timeout=3.0)
        td1.join(timeout=3.0)
        self.assertEqual(sorted(res_d1), [(0, "RateLimitError:True"), (1, "RateLimitError:True")])
        self.assertEqual(limiter.rate_limit_hits, 2)
        self.assertFalse(limiter._throttle_recovery_in_flight)

        # Scenario D2: Leader raises unexpected RuntimeError -> re-arms & unblocks waiter, retry recovers cleanly
        now[0] = 1400.0
        limiter.reset()
        limiter.record_view(18)
        planets_fr.elements_by_id.clear()
        planets_fr.html = "<div>You've made more than 30 page views at last minute!</div>"
        planets_fr.on_navigate = None
        now[0] = 1402.0
        with self.assertRaises(RateLimitError):
            client.ensure_in_game(priority="interactive")
        now[0] = 1448.0

        d2_entered = threading.Event()
        d2_release = threading.Event()
        orig_read_ov = client.read_planets_overview

        def _failing_once_read_ov(*, refresh: bool = False, priority: str = "normal"):
            if not d2_entered.is_set():
                d2_entered.set()
                assert d2_release.wait(timeout=3.0)
                raise RuntimeError("transient selenium glitch")
            planets_fr.html = "<html><body><script id='json-container'></script></body></html>"
            planets_fr.elements_by_id["json-container"] = _FakeElement(
                tag="script",
                text=_sample_planets_json(),
            )
            return orig_read_ov(refresh=False, priority=priority)

        res_d2: list[tuple[int, str]] = []
        with patch.object(client, "read_planets_overview", side_effect=_failing_once_read_ov):
            def _run_d2(idx: int) -> None:
                try:
                    client.ensure_in_game(priority="interactive")
                    with res_lock:
                        res_d2.append((idx, "ok"))
                except Exception as exc:
                    with res_lock:
                        res_d2.append((idx, type(exc).__name__))

            t_err0 = threading.Thread(target=_run_d2, args=(0,))
            t_err0.start()
            self.assertTrue(d2_entered.wait(timeout=2.0))
            t_err1 = threading.Thread(target=_run_d2, args=(1,))
            t_err1.start()
            threading.Event().wait(0.05)
            d2_release.set()
            t_err0.join(timeout=3.0)
            t_err1.join(timeout=3.0)

        self.assertEqual(sorted(res_d2), [(0, "RuntimeError"), (1, "ok")])
        self.assertEqual(limiter.rate_limit_hits, 1)
        self.assertFalse(limiter._throttle_recovery_pending)
        self.assertFalse(limiter._throttle_recovery_in_flight)

        # Scenario D3: Stalled leader + short waiter timeout unblocks with server_throttle=False
        now[0] = 1700.0
        limiter.reset()
        limiter.record_view(18)
        planets_fr.elements_by_id.clear()
        planets_fr.html = "<div>You've made more than 30 page views at last minute!</div>"
        d3_entered = threading.Event()
        d3_release = threading.Event()

        def _slow_restore(_url: str) -> None:
            d3_entered.set()
            assert d3_release.wait(timeout=3.0)
            planets_fr.html = "<html><body><script id='json-container'></script></body></html>"
            planets_fr.elements_by_id["json-container"] = _FakeElement(
                tag="script",
                text=_sample_planets_json(),
            )

        planets_fr.on_navigate = _slow_restore
        now[0] = 1702.0
        with self.assertRaises(RateLimitError):
            client.ensure_in_game(priority="interactive")
        now[0] = 1748.0

        waiter_client = GameBrowserClient(
            driver,
            page_limiter=limiter,
            min_nav_delay=(0.0, 0.0),
            timeout=0.12,
            max_limiter_wait=1.0,
        )
        res_d3: dict[str, str] = {}

        def _run_d3_leader() -> None:
            client.ensure_in_game(priority="interactive")
            res_d3["leader"] = "ok"

        def _run_d3_waiter() -> None:
            try:
                waiter_client.ensure_in_game(priority="interactive")
                res_d3["waiter"] = "ok"
            except RateLimitError as exc:
                res_d3["waiter"] = f"RateLimitError:{exc.server_throttle}"

        tl = threading.Thread(target=_run_d3_leader)
        tl.start()
        self.assertTrue(d3_entered.wait(timeout=2.0))
        tw = threading.Thread(target=_run_d3_waiter)
        tw.start()
        tw.join(timeout=2.0)
        self.assertEqual(res_d3["waiter"], "RateLimitError:False")
        d3_release.set()
        tl.join(timeout=3.0)
        self.assertEqual(res_d3["leader"], "ok")
        self.assertEqual(limiter.rate_limit_hits, 1)

    def test_read_full_snapshot_include_home_false_and_selected_planet_extraction(self) -> None:
        two_planet_json = json.dumps(
            {
                "info": {
                    "ts": 1700000000,
                    "user": {"userID": 103603, "nickname": "ventkx86"},
                    "universe": {"id": 31},
                },
                "planets": [
                    {
                        "id": 12476,
                        "name": "Home",
                        "coords": "02:380:12",
                        "type": "normal",
                        "temperature": 31,
                        "fields": 230,
                        "resources": [5000, 3000, 4000, 1000],
                        "storage": [100000, 100000, 100000, 100000],
                        "energy": {"produced": 200, "used": 100},
                        "buildings": {"b1": {"level": 5, "name": "Pig iron mine"}},
                        "ships": {},
                        "defense": {},
                    },
                    {
                        "id": 12477,
                        "name": "Colony",
                        "coords": "02:380:14",
                        "type": "normal",
                        "temperature": 25,
                        "fields": 190,
                        "resources": [1000, 1000, 1000, 500],
                        "storage": [100000, 100000, 100000, 100000],
                        "energy": {"produced": 100, "used": 50},
                        "buildings": {"b1": {"level": 2, "name": "Pig iron mine"}},
                        "ships": {},
                        "defense": {},
                    },
                ],
                "researches": {"r1": {"level": 1}},
                "specs": {},
            }
        )
        now = [100.0]
        limiter = PageViewLimiter(
            min_delay_range=(0.0, 0.0),
            clock=lambda: now[0],
            sleeper=lambda s: now.__setitem__(0, now[0] + s),
        )
        driver = _FakeDriver()
        driver.frames["F_planets"].elements_by_id["json-container"] = _FakeElement(
            tag="script",
            text=two_planet_json,
        )
        main = driver.frames["F_Hauptframe"]
        nav_clicks: list[str] = []

        def _make_nav_link(act: str) -> _FakeElement:
            def _on_click(_el) -> None:
                nav_clicks.append(act)
                main.href = f"https://spaceinvasion.bitmeup.com/indexInternal.es?action={act}&sid=abc"
                if act == "internalHome":
                    main.html = "<script>SI_TPL={planetID:12476};</script><div>active factor: x20.0</div>"
                elif act == "internalBuildings":
                    main.html = (
                        '<select name="planet">'
                        '<option value="indexInternal.es?action=internalBuildings&newPlanet=12476">Home</option>'
                        '<option selected value="indexInternal.es?action=internalBuildings&newPlanet=12477">Colony</option>'
                        "</select>"
                        '<table><tr><td><a href="indexInternal.es?action=internalDetail&type=building&id=1">Pig iron mine</a> '
                        'Required: 100 Pig iron <a class="bau" href="indexInternal.es?action=internalBuildItem&buildingID=1">Build level 3</a></td></tr></table>'
                    )
                main.nav_token = None

            return _FakeElement(attrs={"href": f"indexInternal.es?action={act}&sid=abc"}, on_click=_on_click)

        driver.frames["F_Navi"].links = [_make_nav_link("internalHome"), _make_nav_link("internalBuildings")]

        client = GameBrowserClient(driver, page_limiter=limiter, min_nav_delay=(0.0, 0.0))

        # 1. On internalGalaxy with include_home=False: skips internalHome and flags observed_active_planet=False
        main.href = "https://spaceinvasion.bitmeup.com/indexInternal.es?action=internalGalaxy&sid=abc"
        main.html = "<div>System [02:380]</div>"
        st1 = client.read_full_snapshot(
            include_buildings=False,
            include_research=False,
            include_shipyard=False,
            include_home=False,
            return_home=False,
        )
        self.assertEqual(nav_clicks, [])
        self.assertFalse(client.last_snapshot_observed_active_planet)
        self.assertEqual(limiter.views_last_60s(), 1)
        self.assertEqual(st1.active_planet_id, 12476)

        # 2. On internalBuildings with <select name="planet"> selected=12477 and include_home=False:
        # skips internalHome, extracts active_planet_id=12477, and flags observed_active_planet=True
        main.href = "https://spaceinvasion.bitmeup.com/indexInternal.es?action=internalBuildings&sid=abc"
        main.html = (
            '<select name="planet">'
            '<option value="indexInternal.es?action=internalBuildings&newPlanet=12476">Home</option>'
            '<option selected value="indexInternal.es?action=internalBuildings&newPlanet=12477">Colony</option>'
            "</select>"
        )
        st2 = client.read_full_snapshot(
            include_buildings=False,
            include_research=False,
            include_shipyard=False,
            include_home=False,
            return_home=False,
        )
        self.assertEqual(nav_clicks, [])
        self.assertTrue(client.last_snapshot_observed_active_planet)
        self.assertEqual(st2.active_planet_id, 12477)

        # 3. On internalFleet (<select name="planet"> is favourites), never treat option as active planet
        self.assertIsNone(
            GameBrowserClient._extract_selected_planet_id(
                '<select name="planet"><option selected value="indexInternal.es?action=internalFleet&newPlanet=12477">Fav</option></select>',
                "indexInternal.es?action=internalFleet",
            )
        )

        # 4. Default include_home=True still navigates to internalHome when off home
        main.href = "https://spaceinvasion.bitmeup.com/indexInternal.es?action=internalGalaxy&sid=abc"
        main.html = "<div>System [02:380]</div>"
        st3 = client.read_full_snapshot(
            include_buildings=False,
            include_research=False,
            include_shipyard=False,
            return_home=False,
        )
        self.assertEqual(nav_clicks, ["internalHome"])
        self.assertTrue(client.last_snapshot_observed_active_planet)
        self.assertEqual(st3.speedup_factor, 20.0)

    def test_ensure_in_game_race1_stale_none_after_leader_recovery_and_initial_double_count(self) -> None:
        planets_json = _sample_planets_json()
        now = [100.0]
        limiter = PageViewLimiter(
            min_delay_range=(0.0, 0.0),
            max_views_per_minute=24,
            reserved_interactive_views=6,
            clock=lambda: now[0],
            sleeper=lambda s: now.__setitem__(0, now[0] + s),
        )
        driver = _FakeDriver()
        driver.frames["F_Navi"].si_sid = "sid=race1"
        planets_fr = driver.frames["F_planets"]
        client = GameBrowserClient(
            driver,
            page_limiter=limiter,
            min_nav_delay=(0.0, 0.0),
            timeout=3.0,
            max_limiter_wait=1.0,
        )

        def _restore_planets(_url: str = "") -> None:
            planets_fr.html = "<html><body><script id='json-container'></script></body></html>"
            planets_fr.elements_by_id["json-container"] = _FakeElement(
                tag="script",
                text=planets_json,
            )

        # Subtest A: Race 1 (status == "none" after _driver_lock release when leader already recovered)
        limiter.record_view(18)
        planets_fr.elements_by_id.clear()
        planets_fr.html = "<div>You've made more than 30 page views at last minute!</div>"
        planets_fr.on_navigate = None
        now[0] = 101.0
        with self.assertRaises(RateLimitError):
            client.ensure_in_game(priority="interactive")
        self.assertEqual(limiter.rate_limit_hits, 1)
        now[0] = 147.0
        planets_fr.on_navigate = _restore_planets

        orig_begin_or_wait = limiter.begin_or_wait_throttle_recovery
        t1_before_begin = threading.Event()
        allow_t1_begin = threading.Event()
        begin_calls = [0]
        call_lock = threading.Lock()

        def _gated_begin(*, wait_timeout: float | None = 15.0):
            with call_lock:
                begin_calls[0] += 1
                is_first = begin_calls[0] == 1
            if is_first:
                t1_before_begin.set()
                assert allow_t1_begin.wait(timeout=3.0)
            return orig_begin_or_wait(wait_timeout=wait_timeout)

        res_a: list[tuple[int, str]] = []
        res_lock = threading.Lock()
        with patch.object(limiter, "begin_or_wait_throttle_recovery", side_effect=_gated_begin):
            def _t1() -> None:
                try:
                    client.ensure_in_game(priority="interactive")
                    with res_lock:
                        res_a.append((1, "ok"))
                except Exception as exc:
                    with res_lock:
                        res_a.append((1, f"{type(exc).__name__}:{getattr(exc, 'server_throttle', None)}"))

            t1 = threading.Thread(target=_t1)
            t1.start()
            self.assertTrue(t1_before_begin.wait(timeout=2.0))
            client.ensure_in_game(priority="interactive")
            res_a.append((0, "ok"))
            self.assertIn("json-container", planets_fr.elements_by_id)
            self.assertFalse(limiter._throttle_recovery_pending)
            allow_t1_begin.set()
            t1.join(timeout=3.0)

        self.assertEqual(sorted(res_a), [(0, "ok"), (1, "ok")])
        self.assertEqual(limiter.rate_limit_hits, 1)
        self.assertEqual(limiter.rate_limit_remaining(), 0.0)
        self.assertEqual(limiter.views_last_60s(), 19)

        # Subtest B: Initial-throttle concurrent double-count (Race 2a) & recovery-before-record (Race 2b)
        now[0] = 300.0
        limiter.reset()
        planets_fr.elements_by_id.clear()
        planets_fr.html = "<div>You've made more than 30 page views at last minute!</div>"
        planets_fr.on_navigate = None

        orig_rec_if_gen = limiter.record_rate_limit_if_generation
        t1_before_rec = threading.Event()
        allow_t1_rec = threading.Event()
        rec_calls = [0]

        def _gated_rec(expected_generation: int, retry_after: float | None = None):
            with call_lock:
                rec_calls[0] += 1
                is_first = rec_calls[0] == 1
            if is_first:
                t1_before_rec.set()
                assert allow_t1_rec.wait(timeout=3.0)
            return orig_rec_if_gen(expected_generation, retry_after=retry_after)

        res_b1: list[tuple[int, str]] = []
        with patch.object(limiter, "record_rate_limit_if_generation", side_effect=_gated_rec):
            def _t1_b() -> None:
                try:
                    client.ensure_in_game(priority="interactive")
                    with res_lock:
                        res_b1.append((1, "ok"))
                except RateLimitError as exc:
                    with res_lock:
                        res_b1.append((1, f"RL:{exc.server_throttle}:{getattr(exc, '_recorded_limiter_id', None) == id(limiter)}"))

            t1 = threading.Thread(target=_t1_b)
            t1.start()
            self.assertTrue(t1_before_rec.wait(timeout=2.0))
            try:
                client.ensure_in_game(priority="interactive")
                res_b1.append((0, "ok"))
            except RateLimitError as exc:
                res_b1.append((0, f"RL:{exc.server_throttle}:{getattr(exc, '_recorded_limiter_id', None) == id(limiter)}"))
            allow_t1_rec.set()
            t1.join(timeout=3.0)

        self.assertEqual(sorted(res_b1), [(0, "RL:True:True"), (1, "RL:True:True")])
        self.assertEqual(limiter.rate_limit_hits, 1)

        # Race 2b: Thread 1 stalls across cooldown + Thread 2 recovery before calling record_rate_limit_if_generation
        now[0] = 500.0
        limiter.reset()
        planets_fr.elements_by_id.clear()
        planets_fr.html = "<div>You've made more than 30 page views at last minute!</div>"
        planets_fr.on_navigate = _restore_planets
        t1_before_rec.clear()
        allow_t1_rec.clear()
        rec_calls[0] = 0
        res_b2: list[tuple[int, str]] = []

        with patch.object(limiter, "record_rate_limit_if_generation", side_effect=_gated_rec):
            def _t1_b2() -> None:
                try:
                    client.ensure_in_game(priority="interactive")
                    with res_lock:
                        res_b2.append((1, "ok"))
                except Exception as exc:
                    with res_lock:
                        res_b2.append((1, type(exc).__name__))

            t1 = threading.Thread(target=_t1_b2)
            t1.start()
            self.assertTrue(t1_before_rec.wait(timeout=2.0))
            with self.assertRaises(RateLimitError):
                client.ensure_in_game(priority="interactive")
            now[0] = 546.0
            client.ensure_in_game(priority="interactive")
            allow_t1_rec.set()
            t1.join(timeout=3.0)

        self.assertEqual(res_b2, [(1, "ok")])
        self.assertEqual(limiter.rate_limit_hits, 1)
        self.assertEqual(limiter.rate_limit_remaining(), 0.0)

        # Subtest C1: clear_throttle_recovery cross-client non-starvation + external DOM recovery
        now[0] = 700.0
        limiter.reset()
        healthy_drv = _FakeDriver()
        healthy_drv.frames["F_planets"].elements_by_id["json-container"] = _FakeElement(
            tag="script",
            text=planets_json,
        )
        healthy_client = GameBrowserClient(healthy_drv, page_limiter=limiter, min_nav_delay=(0.0, 0.0))
        gen_start = limiter.recovery_generation()
        healthy_client.ensure_in_game(priority="normal")
        self.assertEqual(limiter.recovery_generation(), gen_start)

        planets_fr.elements_by_id.clear()
        planets_fr.html = "<div>You've made more than 30 page views at last minute!</div>"
        planets_fr.on_navigate = None
        now[0] = 701.0
        with self.assertRaises(RateLimitError):
            client.ensure_in_game(priority="interactive")
        now[0] = 747.0

        t1_before_begin.clear()
        allow_t1_begin.clear()
        begin_calls[0] = 0
        res_c1: list[str] = []
        with patch.object(limiter, "begin_or_wait_throttle_recovery", side_effect=_gated_begin):
            def _t1_c1() -> None:
                client.ensure_in_game(priority="interactive")
                res_c1.append("ok")

            t1 = threading.Thread(target=_t1_c1)
            t1.start()
            self.assertTrue(t1_before_begin.wait(timeout=2.0))
            _restore_planets()
            client.ensure_in_game(priority="interactive")
            allow_t1_begin.set()
            t1.join(timeout=3.0)
        self.assertEqual(res_c1, ["ok"])
        self.assertEqual(limiter.rate_limit_hits, 1)

        # Subtest C2: rearm=False AntiBotChallengeError on leader unblocks waiter onto AntiBotChallengeError
        now[0] = 900.0
        limiter.reset()
        planets_fr.elements_by_id.clear()
        planets_fr.html = "<div>You've made more than 30 page views at last minute!</div>"
        planets_fr.on_navigate = None
        now[0] = 901.0
        with self.assertRaises(RateLimitError):
            client.ensure_in_game(priority="interactive")
        now[0] = 947.0

        def _inject_captcha(_url: str = "") -> None:
            planets_fr.elements_by_id.clear()
            planets_fr.html = "<div class='cf-turnstile'>Challenge</div>"

        planets_fr.on_navigate = _inject_captcha
        t1_before_begin.clear()
        allow_t1_begin.clear()
        begin_calls[0] = 0
        res_c2: list[tuple[int, str]] = []
        with patch.object(limiter, "begin_or_wait_throttle_recovery", side_effect=_gated_begin):
            def _t1_c2() -> None:
                try:
                    client.ensure_in_game(priority="interactive")
                    with res_lock:
                        res_c2.append((1, "ok"))
                except Exception as exc:
                    with res_lock:
                        res_c2.append((1, type(exc).__name__))

            t1 = threading.Thread(target=_t1_c2)
            t1.start()
            self.assertTrue(t1_before_begin.wait(timeout=2.0))
            try:
                client.ensure_in_game(priority="interactive")
            except Exception as exc:
                res_c2.append((0, type(exc).__name__))
            allow_t1_begin.set()
            t1.join(timeout=3.0)
        self.assertEqual(sorted(res_c2), [(0, "AntiBotChallengeError"), (1, "AntiBotChallengeError")])

        # Subtest C3: Barrier(16) clean + Barrier(16) 15 cascading transient leader RuntimeErrors + 16th succeeds
        now[0] = 1100.0
        limiter.reset()
        limiter.record_view(18)
        planets_fr.elements_by_id.clear()
        planets_fr.html = "<div>You've made more than 30 page views at last minute!</div>"
        planets_fr.on_navigate = _restore_planets
        now[0] = 1101.0
        with self.assertRaises(RateLimitError):
            client.ensure_in_game(priority="interactive")
        now[0] = 1147.0

        barrier16 = threading.Barrier(16)
        res_c3: list[str] = []

        def _bar16() -> None:
            barrier16.wait(timeout=5.0)
            client.ensure_in_game(priority="interactive")
            with res_lock:
                res_c3.append("ok")

        b16_threads = [threading.Thread(target=_bar16) for _ in range(16)]
        for th in b16_threads:
            th.start()
        for th in b16_threads:
            th.join(timeout=5.0)
        self.assertEqual(res_c3, ["ok"] * 16)
        self.assertEqual(limiter.rate_limit_hits, 1)
        self.assertEqual(limiter.views_last_60s(), 19)

        # Cascading 15 RuntimeErrors + 16th recovers
        now[0] = 1300.0
        limiter.reset()
        limiter.record_view(18)
        planets_fr.elements_by_id.clear()
        planets_fr.html = "<div>You've made more than 30 page views at last minute!</div>"
        planets_fr.on_navigate = None
        now[0] = 1301.0
        with self.assertRaises(RateLimitError):
            client.ensure_in_game(priority="interactive")
        now[0] = 1347.0

        orig_rov = client.read_planets_overview
        fail_count = [0]

        def _fail_15_times(*, refresh: bool = False, priority: str = "normal"):
            with call_lock:
                fail_count[0] += 1
                n = fail_count[0]
            if n <= 15:
                raise RuntimeError("transient glitch")
            _restore_planets()
            return orig_rov(refresh=False, priority=priority)

        barrier16_2 = threading.Barrier(16)
        res_c4: list[str] = []
        with patch.object(client, "read_planets_overview", side_effect=_fail_15_times):
            def _bar16_fail() -> None:
                barrier16_2.wait(timeout=5.0)
                try:
                    client.ensure_in_game(priority="interactive")
                    with res_lock:
                        res_c4.append("ok")
                except RuntimeError:
                    with res_lock:
                        res_c4.append("RuntimeError")

            b16_f_threads = [threading.Thread(target=_bar16_fail) for _ in range(16)]
            for th in b16_f_threads:
                th.start()
            for th in b16_f_threads:
                th.join(timeout=5.0)
        self.assertEqual(res_c4.count("RuntimeError"), 15)
        self.assertEqual(res_c4.count("ok"), 1)

    def test_read_full_snapshot_reuses_raw_main_html_and_locks_page_source_safe(self) -> None:
        two_planet_json = json.dumps(
            {
                "info": {
                    "ts": 1700000000,
                    "user": {"userID": 103603, "nickname": "ventkx86"},
                    "universe": {"id": 31},
                },
                "planets": [
                    {
                        "id": 12476,
                        "name": "Home",
                        "coords": "02:380:12",
                        "type": "normal",
                        "temperature": 31,
                        "fields": 230,
                        "resources": [5000, 3000, 4000, 1000],
                        "storage": [100000, 100000, 100000, 100000],
                        "energy": {"produced": 200, "used": 100},
                        "buildings": {"b1": {"level": 5, "name": "Pig iron mine"}},
                        "ships": {},
                        "defense": {},
                    },
                    {
                        "id": 12477,
                        "name": "Colony",
                        "coords": "02:380:14",
                        "type": "normal",
                        "temperature": 25,
                        "fields": 190,
                        "resources": [1000, 1000, 1000, 500],
                        "storage": [100000, 100000, 100000, 100000],
                        "energy": {"produced": 100, "used": 50},
                        "buildings": {"b1": {"level": 2, "name": "Pig iron mine"}},
                        "ships": {},
                        "defense": {},
                    },
                ],
                "researches": {"r1": {"level": 1}},
                "specs": {},
            }
        )
        now = [100.0]
        limiter = PageViewLimiter(
            min_delay_range=(0.0, 0.0),
            clock=lambda: now[0],
            sleeper=lambda s: now.__setitem__(0, now[0] + s),
        )
        driver = _FakeDriver()
        driver.frames["F_planets"].elements_by_id["json-container"] = _FakeElement(
            tag="script",
            text=two_planet_json,
        )
        main = driver.frames["F_Hauptframe"]
        main.href = "https://spaceinvasion.bitmeup.com/indexInternal.es?action=internalHome&sid=abc"
        main.html = "<script>SI_TPL={planetID:12477};</script><div>active factor: x20.0</div>"

        client = GameBrowserClient(driver, page_limiter=limiter, min_nav_delay=(0.0, 0.0))
        orig_guards = client._check_page_guards
        lock_state_inside_guards: list[bool] = []
        guard_calls = [0]

        def _guarded_spy(html: str) -> None:
            lock_state_inside_guards.append(client._driver_lock._is_owned())
            if "active factor: x20.0" in html and guard_calls[0] == 0:
                guard_calls[0] += 1
                # Simulate concurrent thread switching frame between L1215 and L1245
                client.ensure_in_game()
            orig_guards(html)

        with patch.object(client, "_check_page_guards", side_effect=_guarded_spy), patch.object(
            client,
            "page_source_safe",
            side_effect=AssertionError("read_full_snapshot must reuse raw_main_html instead of calling page_source_safe()"),
        ):
            snap = client.read_full_snapshot(
                include_buildings=False,
                include_research=False,
                include_shipyard=False,
                include_home=False,
                return_home=False,
            )

        self.assertEqual(snap.active_planet_id, 12477)
        self.assertEqual(snap.speedup_factor, 20.0)
        self.assertEqual(guard_calls, [1])
        self.assertTrue(lock_state_inside_guards)
        self.assertTrue(all(not owned for owned in lock_state_inside_guards))

        # Direct page_source_safe() reads page_source under _driver_lock and releases lock before _check_page_guards
        page_source_lock_states: list[bool] = []
        guards_lock_states: list[bool] = []
        orig_ps_prop = _FakeDriver.page_source

        def _checked_ps(drv_self):
            page_source_lock_states.append(client._driver_lock._is_owned())
            return orig_ps_prop.fget(drv_self)

        client._switch_frame("F_Hauptframe")
        with patch.object(_FakeDriver, "page_source", property(_checked_ps)), patch.object(
            client,
            "_check_page_guards",
            side_effect=lambda h: (guards_lock_states.append(client._driver_lock._is_owned()), orig_guards(h))[1],
        ):
            out_html = client.page_source_safe()

        self.assertIn("active factor: x20.0", out_html)
        self.assertEqual(page_source_lock_states, [True])
        self.assertEqual(guards_lock_states, [False])

    def test_ensure_in_game_concurrency_4b_4c_external_recovery_and_lock_disjointness(self) -> None:
        planets_json = _sample_planets_json()

        def _make_throttled_driver(*, top_level: bool = False):
            drv = _FakeDriver()
            drv.frames["F_Navi"].si_sid = "sid=conc4bc"
            planets_fr = drv.frames["F_planets"]
            if top_level:
                orig_find = drv.find_element

                def _missing_frames(by: str, value: str):
                    if drv.active_frame is None and value == "F_planets":
                        raise NoSuchElementException("missing F_planets")
                    return orig_find(by, value)

                drv.top_html = "<div>You've made more than 30 page views at last minute!</div>"
                drv.find_element = _missing_frames
            else:
                planets_fr.elements_by_id.clear()
                planets_fr.html = "<div>You've made more than 30 page views at last minute!</div>"

            def _restore(_url: str = "") -> None:
                if top_level:
                    drv.find_element = _FakeDriver.find_element.__get__(drv, _FakeDriver)
                    drv.top_html = "<html><body>shell</body></html>"
                planets_fr.html = "<html><body><script id='json-container'></script></body></html>"
                planets_fr.elements_by_id["json-container"] = _FakeElement(
                    tag="script",
                    text=planets_json,
                )

            return drv, _restore

        # (1) Lock disjointness on find_and_switch_game_tab recovery + full limiter-method lock audit
        now = [100.0]
        limiter = PageViewLimiter(
            min_delay_range=(0.0, 0.0),
            clock=lambda: now[0],
            sleeper=lambda s: now.__setitem__(0, now[0] + s),
        )
        drv, _ = _make_throttled_driver(top_level=False)
        drv.frames["F_planets"].html = "<html><body>wrong tab</body></html>"
        client = GameBrowserClient(drv, page_limiter=limiter, min_nav_delay=(0.0, 0.0), timeout=2.0)

        lock_owned_on_tab_clear: list[bool] = []
        orig_clear = limiter.clear_throttle_recovery

        def _spy_clear() -> None:
            lock_owned_on_tab_clear.append(client._driver_lock._is_owned())
            return orig_clear()

        limiter.record_rate_limit(45.0)
        now[0] += 46.0
        epoch_before = client._dom_healthy_epoch
        drv_lock_owned_anywhere: list[tuple[str, bool]] = []
        spy_patches = []
        for m_name in (
            "wait_if_throttle_recovery_in_flight",
            "recovery_generation",
            "clear_throttle_recovery",
            "rate_limit_remaining",
            "begin_or_wait_throttle_recovery",
            "finish_throttle_recovery",
            "record_rate_limit_if_generation",
        ):
            orig_m = getattr(limiter, m_name)

            def _make_spy(name: str, fn):
                def _spy(*a, **kw):
                    drv_lock_owned_anywhere.append((name, client._driver_lock._is_owned()))
                    return fn(*a, **kw)

                return _spy

            spy_patches.append(patch.object(limiter, m_name, side_effect=_make_spy(m_name, orig_m)))

        with patch.object(limiter, "clear_throttle_recovery", side_effect=_spy_clear), patch(
            "spaceinvasion.client.find_and_switch_game_tab", return_value=True
        ):
            client.ensure_in_game(priority="interactive")
        self.assertEqual(lock_owned_on_tab_clear, [False])
        self.assertEqual(client._dom_healthy_epoch, epoch_before + 1)

        try:
            for sp in spy_patches:
                sp.start()
            with patch("spaceinvasion.client.find_and_switch_game_tab", return_value=True):
                client.ensure_in_game(priority="interactive")
            drv.frames["F_planets"].html = "<div>You've made more than 30 page views at last minute!</div>"
            with self.assertRaises(RateLimitError):
                client.ensure_in_game(priority="interactive")
        finally:
            for sp in spy_patches:
                sp.stop()
        self.assertTrue(drv_lock_owned_anywhere)
        self.assertEqual([pair for pair in drv_lock_owned_anywhere if pair[1]], [])

        # (2) Concurrency 4b (in_planets_frame=True and top-level in_planets_frame=False):
        # Initial unrecorded throttle (_throttle_recovery_pending=False); Thread 1 stalls after L625
        # right before L697/L730 while external read_planets_overview(refresh=True) restores #json-container.
        for top_level in (False, True):
            now = [200.0]
            lim = PageViewLimiter(
                min_delay_range=(0.0, 0.0),
                clock=lambda: now[0],
                sleeper=lambda s: now.__setitem__(0, now[0] + s),
            )
            d_4b, restore_4b = _make_throttled_driver(top_level=top_level)
            d_4b.frames["F_planets"].on_navigate = restore_4b
            c_4b = GameBrowserClient(d_4b, page_limiter=lim, min_nav_delay=(0.0, 0.0), timeout=2.0)

            t1_ready = threading.Event()
            allow_t1 = threading.Event()
            hook_name = "rate_limit_remaining" if top_level else "begin_or_wait_throttle_recovery"
            orig_hook = getattr(lim, hook_name)
            calls = [0]
            call_lock = threading.Lock()

            def _gated_4b(*args, **kwargs):
                res = orig_hook(*args, **kwargs)
                with call_lock:
                    calls[0] += 1
                    is_first = calls[0] == 1
                if is_first:
                    t1_ready.set()
                    assert allow_t1.wait(timeout=3.0)
                return res

            res_4b: list[str] = []
            with patch.object(lim, hook_name, side_effect=_gated_4b):
                def _run_4b() -> None:
                    try:
                        c_4b.ensure_in_game(priority="interactive")
                        res_4b.append("ok")
                    except Exception as exc:
                        res_4b.append(type(exc).__name__)

                th = threading.Thread(target=_run_4b)
                th.start()
                self.assertTrue(t1_ready.wait(timeout=2.0))
                if top_level:
                    restore_4b()
                c_4b.read_planets_overview(refresh=True, priority="interactive")
                allow_t1.set()
                th.join(timeout=3.0)

            self.assertEqual(res_4b, ["ok"], f"top_level={top_level}")
            self.assertEqual(lim.rate_limit_hits, 0)
            self.assertEqual(lim.rate_limit_remaining(), 0.0)

        # (3) Concurrency 4c (in_planets_frame=True and top-level in_planets_frame=False):
        # Initial unrecorded throttle (_throttle_recovery_pending=False); Thread 1 stalls after L625
        # while external browser refresh + Thread 0 ensure_in_game() leaves generation unchanged.
        for top_level in (False, True):
            now = [300.0]
            lim = PageViewLimiter(
                min_delay_range=(0.0, 0.0),
                clock=lambda: now[0],
                sleeper=lambda s: now.__setitem__(0, now[0] + s),
            )
            d_4c, restore_4c = _make_throttled_driver(top_level=top_level)
            c_4c = GameBrowserClient(d_4c, page_limiter=lim, min_nav_delay=(0.0, 0.0), timeout=2.0)

            t1_ready = threading.Event()
            allow_t1 = threading.Event()
            hook_name = "rate_limit_remaining" if top_level else "begin_or_wait_throttle_recovery"
            orig_hook = getattr(lim, hook_name)
            calls = [0]
            call_lock = threading.Lock()
            gen_initial = lim.recovery_generation()

            def _gated_4c(*args, **kwargs):
                res = orig_hook(*args, **kwargs)
                with call_lock:
                    calls[0] += 1
                    is_first = calls[0] == 1
                if is_first:
                    t1_ready.set()
                    assert allow_t1.wait(timeout=3.0)
                return res

            res_4c: list[str] = []
            with patch.object(lim, hook_name, side_effect=_gated_4c):
                def _run_4c() -> None:
                    try:
                        c_4c.ensure_in_game(priority="interactive")
                        res_4c.append("ok")
                    except Exception as exc:
                        res_4c.append(type(exc).__name__)

                th = threading.Thread(target=_run_4c)
                th.start()
                self.assertTrue(t1_ready.wait(timeout=2.0))
                restore_4c()
                c_4c.ensure_in_game(priority="interactive")
                self.assertEqual(lim.recovery_generation(), gen_initial)
                allow_t1.set()
                th.join(timeout=3.0)

            self.assertEqual(res_4c, ["ok"], f"top_level={top_level}")
            self.assertEqual(lim.rate_limit_hits, 0)
            self.assertEqual(lim.rate_limit_remaining(), 0.0)

        # (4) Pure external browser F5 (no Thread 0 / read_planets_overview) + recover-then-rethrottle edge case
        now = [400.0]
        lim_d = PageViewLimiter(
            min_delay_range=(0.0, 0.0),
            clock=lambda: now[0],
            sleeper=lambda s: now.__setitem__(0, now[0] + s),
        )
        d_4d, restore_4d = _make_throttled_driver(top_level=False)
        c_4d = GameBrowserClient(d_4d, page_limiter=lim_d, min_nav_delay=(0.0, 0.0), timeout=2.0)
        t1_ready = threading.Event()
        allow_t1 = threading.Event()
        orig_begin_d = lim_d.begin_or_wait_throttle_recovery
        calls_d = [0]
        call_lock = threading.Lock()

        def _gated_4d(*args, **kwargs):
            res = orig_begin_d(*args, **kwargs)
            with call_lock:
                calls_d[0] += 1
                is_first = calls_d[0] == 1
            if is_first:
                t1_ready.set()
                assert allow_t1.wait(timeout=3.0)
            return res

        res_4d: list[str] = []
        with patch.object(lim_d, "begin_or_wait_throttle_recovery", side_effect=_gated_4d):
            def _run_4d() -> None:
                try:
                    c_4d.ensure_in_game(priority="interactive")
                    res_4d.append("ok")
                except Exception as exc:
                    res_4d.append(type(exc).__name__)

            th = threading.Thread(target=_run_4d)
            th.start()
            self.assertTrue(t1_ready.wait(timeout=2.0))
            restore_4d()
            allow_t1.set()
            th.join(timeout=3.0)
        self.assertEqual(res_4d, ["ok"])
        self.assertEqual(lim_d.rate_limit_hits, 0)

        # Recover via Thread 0 then throttle AGAIN before Thread 1 resumes -> accurate throttle detection
        now = [500.0]
        lim_e = PageViewLimiter(
            min_delay_range=(0.0, 0.0),
            clock=lambda: now[0],
            sleeper=lambda s: now.__setitem__(0, now[0] + s),
        )
        d_4e, restore_4e = _make_throttled_driver(top_level=False)
        c_4e = GameBrowserClient(d_4e, page_limiter=lim_e, min_nav_delay=(0.0, 0.0), timeout=2.0)
        t1_ready.clear()
        allow_t1.clear()
        calls_e = [0]
        orig_begin_e = lim_e.begin_or_wait_throttle_recovery

        def _gated_4e(*args, **kwargs):
            res = orig_begin_e(*args, **kwargs)
            with call_lock:
                calls_e[0] += 1
                is_first = calls_e[0] == 1
            if is_first:
                t1_ready.set()
                assert allow_t1.wait(timeout=3.0)
            return res

        res_4e: list[str] = []
        with patch.object(lim_e, "begin_or_wait_throttle_recovery", side_effect=_gated_4e):
            def _run_4e() -> None:
                try:
                    c_4e.ensure_in_game(priority="interactive")
                    res_4e.append("ok")
                except RateLimitError as exc:
                    res_4e.append(f"RateLimitError:{exc.server_throttle}")

            th = threading.Thread(target=_run_4e)
            th.start()
            self.assertTrue(t1_ready.wait(timeout=2.0))
            restore_4e()
            c_4e.ensure_in_game(priority="interactive")
            d_4e.frames["F_planets"].elements_by_id.clear()
            d_4e.frames["F_planets"].html = "<div>You've made more than 30 page views at last minute!</div>"
            allow_t1.set()
            th.join(timeout=3.0)
        self.assertEqual(res_4e, ["RateLimitError:True"])
        self.assertEqual(lim_e.rate_limit_hits, 1)

    def test_adv_ensure_in_game_concurrency_4f_tab_switch(self) -> None:
        planets_json = _sample_planets_json()
        now = [600.0]
        lim_f = PageViewLimiter(
            min_delay_range=(0.0, 0.0),
            clock=lambda: now[0],
            sleeper=lambda s: now.__setitem__(0, now[0] + s),
        )
        drv_f = _FakeDriver()
        drv_f.frames["F_Navi"].si_sid = "sid=conc4f"
        orig_find = drv_f.find_element

        def _missing_f_planets(by: str, value: str):
            if drv_f.active_frame is None and value == "F_planets":
                raise NoSuchElementException("missing F_planets")
            return orig_find(by, value)

        drv_f.top_html = "<div>You've made more than 30 page views at last minute!</div>"
        drv_f.find_element = _missing_f_planets

        def _restore_4f(_url: str = "") -> None:
            drv_f.find_element = _FakeDriver.find_element.__get__(drv_f, _FakeDriver)
            drv_f.top_html = "<html><body>shell</body></html>"
            planets_fr = drv_f.frames["F_planets"]
            planets_fr.html = "<html><body><script id='json-container'></script></body></html>"
            planets_fr.elements_by_id["json-container"] = _FakeElement(tag="script", text=planets_json)

        c_4f = GameBrowserClient(drv_f, page_limiter=lim_f, min_nav_delay=(0.0, 0.0), timeout=2.0)
        epoch_before = c_4f._dom_healthy_epoch

        t1_ready = threading.Event()
        allow_t1 = threading.Event()
        call_lock = threading.Lock()
        rem_calls = [0]
        orig_rem = lim_f.rate_limit_remaining

        def _gated_rem(*args, **kwargs):
            res = orig_rem(*args, **kwargs)
            with call_lock:
                rem_calls[0] += 1
                first = rem_calls[0] == 1
            if first:
                t1_ready.set()
                assert allow_t1.wait(timeout=3.0)
            return res

        def _switch_tab_recover(_drv) -> bool:
            _restore_4f()
            return True

        res_4f: list[str] = []
        with patch.object(lim_f, "rate_limit_remaining", side_effect=_gated_rem), patch(
            "spaceinvasion.client.find_and_switch_game_tab",
            side_effect=_switch_tab_recover,
        ):
            def _run_t1() -> None:
                try:
                    c_4f.ensure_in_game(priority="interactive")
                    res_4f.append("t1:ok")
                except Exception as exc:
                    res_4f.append(f"t1:{type(exc).__name__}")

            th = threading.Thread(target=_run_t1)
            th.start()
            self.assertTrue(t1_ready.wait(timeout=2.0))
            drv_f.top_html = "<html><body>wrong tab</body></html>"
            c_4f.ensure_in_game(priority="interactive")
            res_4f.append("t0:ok")
            allow_t1.set()
            th.join(timeout=3.0)

        self.assertEqual(sorted(res_4f), ["t0:ok", "t1:ok"])
        self.assertEqual(lim_f.rate_limit_hits, 0)
        self.assertEqual(lim_f.rate_limit_remaining(), 0.0)
        self.assertGreater(c_4f._dom_healthy_epoch, epoch_before)

    def test_nan_inf_cooldown_clamping_and_page_guards_when_include_home_false(self) -> None:
        err_nan = RateLimitError("throttled", retry_after=float("nan"))
        self.assertTrue(1.0 <= err_nan.retry_after <= 3600.0)
        err_inf = RateLimitError("throttled", retry_after=float("inf"))
        self.assertTrue(1.0 <= err_inf.retry_after <= 3600.0)

        t_now = [100.0]
        lim = PageViewLimiter(default_cooldown_seconds=60.0, clock=lambda: t_now[0])
        rem = lim.record_rate_limit(float("nan"))
        self.assertEqual(rem, 60.0)
        t_now[0] += 61.0
        ok, rem2 = lim.record_rate_limit_if_generation(lim.recovery_generation(), float("inf"))
        self.assertTrue(ok)
        self.assertEqual(rem2, 60.0)

        from selenium.common.exceptions import NoSuchElementException

        drv = MagicMock()
        planets_el = MagicMock()
        planets_el.get_attribute.return_value = "indexInternal.es?action=internalPlanetsOverview"

        def _find_el(by, val):
            if val == "F_planets":
                return planets_el
            raise NoSuchElementException(val)

        drv.find_element.side_effect = _find_el
        drv.execute_script.return_value = "https://uni/indexInternal.es?action=internalPlanetsOverview"
        drv.page_source = "<html><body>You've made more than 30 page views at last minute!</body></html>"
        t_now[0] += 61.0
        c = GameBrowserClient(drv, page_limiter=lim, min_nav_delay=(0.0, 0.0))
        with self.assertRaises(RateLimitError):
            c.read_full_snapshot(include_home=False, include_buildings=False)

        # Off-internalHome F_Hauptframe redirecting to BitMeUp login or universe picker raises SpaceInvasionAuthError
        from spaceinvasion.state import GameState

        haupt_el = MagicMock()
        haupt_el.get_attribute.return_value = "indexInternal.es?action=internalBuildings"

        def _find_el_portal(by, val):
            if val == "F_Hauptframe":
                return haupt_el
            if val == "F_planets":
                return planets_el
            raise NoSuchElementException(val)

        for marker_html in (
            '<html><body><div id="bitmeup-login" class="bitmeup-login">Login</div></body></html>',
            '<html><body><div id="sip-tab-universe" class="sip-tab-universe">Choose Universe</div></body></html>',
        ):
            drv_portal = MagicMock()
            drv_portal.find_element.side_effect = _find_el_portal
            drv_portal.execute_script.return_value = "https://uni/indexInternal.es?action=internalBuildings"
            drv_portal.page_source = marker_html
            c_portal = GameBrowserClient(drv_portal, page_limiter=lim, min_nav_delay=(0.0, 0.0))
            with patch.object(c_portal, "ensure_in_game", return_value=None), patch.object(
                c_portal,
                "read_planets_overview",
                return_value=GameState(
                    universe_id=1,
                    user_id=1,
                    nickname="Test",
                    timestamp=100.0,
                    planets=(),
                    researches={},
                ),
            ), patch.object(c_portal, "read_menu_status", return_value=({}, None, None)):
                with self.assertRaises(SpaceInvasionAuthError):
                    c_portal.read_full_snapshot(
                        include_home=False,
                        include_buildings=False,
                        include_research=False,
                        include_shipyard=False,
                    )

    def test_wait_frame_ready_raises_on_portal_html_even_with_expected_action_in_href(self) -> None:
        lim = PageViewLimiter(max_views_per_minute=60)
        haupt_el = MagicMock()
        for marker_html in (
            '<html><body><div id="bitmeup-login" class="bitmeup-login">Login</div></body></html>',
            '<html><body><div id="sip-tab-universe" class="sip-tab-universe">Choose Universe</div></body></html>',
        ):
            drv = MagicMock()
            drv.find_element.return_value = haupt_el
            drv.execute_script.return_value = "https://uni/indexInternal.es?action=internalBuildings"
            drv.page_source = marker_html
            client = GameBrowserClient(drv, page_limiter=lim, min_nav_delay=(0.0, 0.0), timeout=0.5)
            with self.assertRaises(SpaceInvasionAuthError):
                client._wait_frame_ready("F_Hauptframe", expected_action="internalBuildings")
            with self.assertRaises(SpaceInvasionAuthError):
                client._wait_frame_ready("F_Hauptframe", expected_action=None)
            drv_ov = MagicMock()
            planets_frame = MagicMock()
            planets_frame.get_attribute.return_value = "indexInternal.es?action=internalPlanetsOverview"

            def _ov_find(by, val):
                if val == "F_planets":
                    return planets_frame
                raise NoSuchElementException(val)

            drv_ov.find_element.side_effect = _ov_find
            drv_ov.page_source = marker_html
            drv_ov.execute_script.side_effect = [
                "https://uni/indexInternal.es?action=internalPlanetsOverview",
                None,
                False,
                "complete",
                False,
            ]
            client_ov = GameBrowserClient(drv_ov, page_limiter=lim, min_nav_delay=(0.0, 0.0), timeout=0.5)
            with patch.object(client_ov, "ensure_in_game", return_value=None):
                with self.assertRaises(SpaceInvasionAuthError):
                    client_ov.read_planets_overview(refresh=True)

    def test_extract_selected_planet_id_ignores_huge_5000_digit_values(self) -> None:
        huge = "9" * 5000
        html_huge = (
            f'<select name="planet">'
            f'<option selected="selected" value="indexInternal.es?action=internalHome&newplanet={huge}">Huge</option>'
            f"</select>"
        )
        self.assertIsNone(GameBrowserClient._extract_selected_planet_id(html_huge, "https://uni/indexInternal.es?action=internalHome"))

        html_ok = (
            '<select name="planet">'
            '<option selected="selected" value="indexInternal.es?action=internalHome&newplanet=12476">Ok</option>'
            "</select>"
        )
        self.assertEqual(GameBrowserClient._extract_selected_planet_id(html_ok, "https://uni/indexInternal.es?action=internalHome"), 12476)

    def test_r7_client_and_limiter_clamp_nan_inf_waits_without_overflow_error(self) -> None:
        import math
        import threading

        lim = PageViewLimiter(
            max_views_per_minute=2,
            window_seconds=float("nan"),
            default_cooldown_seconds=float("inf"),
            min_delay_range=(float("nan"), float("inf")),
            clock=lambda: 100.0,
            sleeper=lambda _s: None,
        )
        self.assertTrue(math.isfinite(lim.window_seconds))
        self.assertTrue(math.isfinite(lim.default_cooldown_seconds))
        self.assertLessEqual(lim.default_cooldown_seconds, 3600.0)
        waited = lim.acquire(max_wait=float("inf"), delay_range=(float("nan"), float("inf")))
        self.assertTrue(math.isfinite(waited))

        # Trigger Condition.wait() while a concurrent recovery is in flight with wait_timeout=inf / nan
        lim._throttle_recovery_in_flight = True
        lim._throttle_recovery_generation = 1

        def _finish_soon() -> None:
            with lim._recovery_cond:
                lim._throttle_recovery_in_flight = False
                lim._throttle_recovery_generation = 2
                lim._recovery_cond.notify_all()

        t = threading.Timer(0.02, _finish_soon)
        t.start()
        try:
            self.assertEqual(lim.wait_if_throttle_recovery_in_flight(wait_timeout=float("inf")), "waited")
            lim._throttle_recovery_in_flight = True
            t2 = threading.Timer(0.02, _finish_soon)
            t2.start()
            try:
                status, _ = lim.begin_or_wait_throttle_recovery(wait_timeout=float("nan"))
                self.assertEqual(status, "waited")
            finally:
                t2.join(timeout=2.0)
        finally:
            t.join(timeout=2.0)

        drv = MagicMock()
        client = GameBrowserClient(
            drv,
            page_limiter=lim,
            timeout=float("inf"),
            max_limiter_wait=float("nan"),
        )
        self.assertTrue(math.isfinite(client.timeout) and client.timeout <= 300.0)
        self.assertTrue(client.max_limiter_wait is not None and math.isfinite(client.max_limiter_wait))
        client.timeout = float("nan")
        drv.execute_script.side_effect = [{"ready": "complete", "href": "https://uni/indexInternal.es", "token": "t"}]
        drv.page_source = "<html>ok</html>"
        self.assertEqual(client._wait_frame_ready("F_Hauptframe"), "<html>ok</html>")

    def test_r8_limiter_sanitizes_nan_inf_huge_max_views_reserve_count_and_cost(self) -> None:
        lim_nan = PageViewLimiter(
            max_views_per_minute=float("nan"),  # type: ignore[arg-type]
            reserved_interactive_views=float("inf"),  # type: ignore[arg-type]
            min_delay_range=(0.0, 0.0),
            clock=lambda: 100.0,
            sleeper=lambda _s: None,
        )
        self.assertEqual(lim_nan.max_views_per_minute, 24)
        self.assertEqual(lim_nan.reserved_interactive_views, 6)
        lim_nan.record_view(float("nan"))  # type: ignore[arg-type]
        self.assertEqual(lim_nan.views_last_60s(), 1)
        self.assertEqual(lim_nan.acquire(cost=float("nan")), 0.0)  # type: ignore[arg-type]
        self.assertEqual(lim_nan.acquire(cost=float("inf")), 0.0)  # type: ignore[arg-type]
        self.assertEqual(lim_nan.acquire(cost=True), 0.0)  # type: ignore[arg-type]

        lim_huge = PageViewLimiter(
            max_views_per_minute=10**400,
            reserved_interactive_views=10**400,
            min_delay_range=(0.0, 0.0),
            clock=lambda: 100.0,
            sleeper=lambda _s: None,
        )
        self.assertLessEqual(lim_huge.max_views_per_minute, 100_000)
        self.assertLessEqual(lim_huge.reserved_interactive_views, 100_000)
        lim_huge.record_view(10**400)
        self.assertLessEqual(lim_huge.views_last_60s(), 10_000)

    def test_r10_bytes_nonascii_bounds_and_commander_client_timeout(self) -> None:
        from spaceinvasion.client import GameBrowserClient
        from spaceinvasion.commander_client import GameApiClient, _safe_api_timeout

        lim = PageViewLimiter(
            max_views_per_minute=b"30",  # type: ignore[arg-type]
            reserved_interactive_views="\u0665",  # type: ignore[arg-type]
            min_delay_range=(b"0.0", "\u0661"),  # type: ignore[arg-type]
            default_cooldown_seconds="\u0661\u0660",  # type: ignore[arg-type]
            clock=lambda: 100.0,
            sleeper=lambda _s: None,
        )
        self.assertEqual(lim.max_views_per_minute, 30)
        self.assertEqual(lim.reserved_interactive_views, 6)
        self.assertIsNone(GameBrowserClient._extract_selected_planet_id('<option selected value="\u0661\u0662\u0663">P</option>'))
        cli = GameBrowserClient(MagicMock(), timeout=b"25.0")  # type: ignore[arg-type]
        self.assertEqual(cli.timeout, 25.0)
        cli2 = GameBrowserClient(MagicMock(), timeout="\u0662\u0665")  # type: ignore[arg-type]
        self.assertEqual(cli2.timeout, 20.0)

        self.assertEqual(_safe_api_timeout(10**5000), 30.0)
        self.assertEqual(_safe_api_timeout(10**6), 3600.0)
        self.assertEqual(_safe_api_timeout(b"3.5"), 3.5)
        self.assertEqual(_safe_api_timeout("\u0665"), 30.0)
        self.assertEqual(_safe_api_timeout(frozenset({1})), 30.0)
        api_cli = GameApiClient("http://127.0.0.1:8787", timeout=10**5000)  # type: ignore[arg-type]
        self.assertEqual(api_cli.timeout, 30.0)

    def test_empty_pageview_modal_ignored_popup_tab_recovery_and_crash_debug_log(self) -> None:
        import json
        import tempfile
        from pathlib import Path
        from unittest.mock import patch
        from spaceinvasion.client import GameBrowserClient, RateLimitError, is_server_rate_limit_html
        from spaceinvasion.logging_utils import append_crash_debug_log

        # 1. Hidden unpopulated `internalGalaxy` modal does NOT trigger false RateLimitError
        gal_modal_html = (
            '<div style="display:none;"><div class="ecoshadow">'
            'You\'ve made more than <span id="pageview-amount"></span> page views at last minute.'
            "</div></div>"
        )
        self.assertFalse(is_server_rate_limit_html(gal_modal_html))
        real_modal_html = (
            '<div class="ecoshadow">'
            'You\'ve made more than <span id="pageview-amount">30</span> page views at last minute.'
            "</div>"
        )
        self.assertTrue(is_server_rate_limit_html(real_modal_html))
        self.assertTrue(is_server_rate_limit_html("<div>You've made more than 30 page views at last minute.</div>"))

        cli = GameBrowserClient(MagicMock())
        cli._check_page_guards(gal_modal_html)  # must not raise
        with self.assertRaises(RateLimitError):
            cli._check_page_guards(real_modal_html)

        # 2. Popup window recovery (`p_galaxy()` window without frames -> switches back via `find_and_switch_game_tab`)
        drv = MagicMock()
        drv.window_handles = ["popup_gal", "game_main"]
        from selenium.common.exceptions import NoSuchElementException

        drv.find_element.side_effect = NoSuchElementException("no F_planets in popup")
        cli_pop = GameBrowserClient(drv)
        with patch("spaceinvasion.client.find_and_switch_game_tab", return_value=True) as mock_switch:
            cli_pop.ensure_in_game()
        mock_switch.assert_called_once_with(drv)

        # 3. Gitignored crash debug logger writes redacted JSONL + human log cleanly
        with tempfile.TemporaryDirectory() as tmp:
            paths = append_crash_debug_log(
                component="test.comp",
                event="test_event",
                error=RuntimeError("boom ?sid=SECRET_CRASH_SID"),
                details={"url": "https://spaceinvasion.bitmeup.com/indexInternal.es?sid=SECRET_URL_SID"},
                crash_dir=Path(tmp),
            )
            self.assertIsNotNone(paths)
            assert paths is not None
            j_txt = paths[0].read_text(encoding="utf-8")
            l_txt = paths[1].read_text(encoding="utf-8")
            self.assertNotIn("SECRET_CRASH_SID", j_txt)
            self.assertNotIn("SECRET_URL_SID", j_txt)
            self.assertNotIn("SECRET_CRASH_SID", l_txt)
            rec = json.loads(j_txt.strip())
            self.assertEqual(rec["component"], "test.comp")

        # 4. Edge cases: &nbsp;/0 modal placeholders, HTML <input> quote/unquoted redaction,
        #    2D `g`/`s` form fallback, full-warehouse `#ress` ticker guard, & commander sync_economy 429 propagation
        self.assertFalse(
            is_server_rate_limit_html(
                '<div>You\'ve made more than <span class="amt" id="pageview-amount">&nbsp;</span> page views.</div>'
            )
        )
        self.assertFalse(
            is_server_rate_limit_html(
                '<div>You\'ve made more than <span id="pageview-amount">0</span> page views.</div>'
            )
        )
        from spaceinvasion.logging_utils import redact_sensitive

        self.assertEqual(
            redact_sensitive('<input type="password" name="password" value="p@ss\'word123">'),
            '<input type="password" name="password" value="REDACTED">',
        )
        self.assertEqual(
            redact_sensitive("<input type=hidden name=sid value=abcdef0123456789/>"),
            "<input type=hidden name=sid value=REDACTED/>",
        )
        self.assertEqual(
            redact_sensitive('<input name="has_sid" value="1">'),
            '<input name="has_sid" value="1">',
        )

        # Full Metal warehouse (`metal >= storage.metal`) must NOT flip `resource2=100` to `0` on `#metall=0`
        from spaceinvasion.state import EnergyBalance, GameState, PlanetState, Resources

        p_full_met = PlanetState(
            planet_id=12476,
            name="Colony",
            coords="02:380:12",
            planet_type="normal",
            temperature=31,
            fields_used=50,
            fields_max=230,
            resources=Resources(50_000, 100_000, 50_000, 20_000),
            storage=Resources(200_000, 100_000, 200_000, 200_000),
            energy=EnergyBalance(3000, 2000),
            buildings={"b1": 18, "b2": 18, "b5": 20},
            economy_rates={"resource2": 100},
        )
        st_full = GameState(31, 1, "u", 1000.0, (p_full_met,), active_planet_id=12476)
        ress_zero_html = (
            '<form id="ress"><input type="hidden" id="roheisen" value="25.0"/>'
            '<input type="hidden" id="metall" value="0"/></form>'
            "<script>SI_TPL={planetID:12476};</script>"
        )
        drv_full = MagicMock()
        drv_full.execute_script.return_value = "https://spaceinvasion.bitmeup.com/indexInternal.es?action=internalHome"
        drv_full.page_source = ress_zero_html
        cli_full = GameBrowserClient(drv_full, min_nav_delay=(0.0, 0.0))
        with (
            patch.object(cli_full, "ensure_in_game"),
            patch.object(cli_full, "read_planets_overview", return_value=st_full),
            patch.object(cli_full, "read_menu_status", return_value=({}, 100, 10)),
            patch.object(cli_full, "_switch_frame"),
        ):
            snap_full = cli_full.read_full_snapshot(
                include_buildings=False,
                include_research=False,
                include_shipyard=False,
                return_home=False,
            )
            self.assertEqual(snap_full.planets[0].economy_rates.get("resource2"), 100)

            p_not_full = p_full_met.__class__(
                **{**p_full_met.__dict__, "resources": Resources(50_000, 20_000, 50_000, 20_000)}
            )
            cli_full.read_planets_overview.return_value = GameState(
                31, 1, "u", 1000.0, (p_not_full,), active_planet_id=12476
            )
            snap_paused = cli_full.read_full_snapshot(
                include_buildings=False,
                include_research=False,
                include_shipyard=False,
                return_home=False,
            )
            self.assertEqual(snap_paused.planets[0].economy_rates.get("resource2"), 0)

        # Commander `sync_economy` propagates HTTP 429 rate-limit instead of swallowing it
        import commander
        from spaceinvasion.commander_client import GameApiError
        from dataclasses import asdict

        fake_api = MagicMock()
        fake_api.get_state.return_value = GameState(
            31, 1, "u", 1000.0, (p_not_full,), active_planet_id=12476
        )
        fake_api.send_command.side_effect = GameApiError(
            "HTTP 429: SpaceInvasion per-minute page-view throttle hit; retry after 45.0s."
        )
        with self.assertRaises(GameApiError):
            commander.run_commander_tick(
                fake_api,
                live=True,
                max_actions=1,
                include_buildings=True,
                economy_sync_timestamps={12476: 0.0},
            )
        _ = asdict(p_not_full)

    def test_resolve_sid_query_returns_empty_without_sid_and_never_wraps_urls(self) -> None:
        driver = _FakeDriver()
        driver.current_url = "https://spaceinvasion.bitmeup.com/indexGame.es"
        driver.frames["F_Navi"].si_sid = ""
        driver.frames["F_Navi"].href = "https://spaceinvasion.bitmeup.com/indexInternal.es?action=internalMenu"
        driver.frames["F_Navi"].links = []
        driver.frames["F_Hauptframe"].href = "https://spaceinvasion.bitmeup.com/indexInternal.es?action=internalHome"
        driver.frames["F_Hauptframe"].links = []

        client = GameBrowserClient(driver, min_nav_delay=(0.0, 0.0), timeout=2.0)
        self.assertEqual(client._resolve_sid_query(), "")

        driver.frames["F_Hauptframe"].href = "about:blank"
        self.assertEqual(client._resolve_sid_query(), "")

        # Even if a custom driver returns a raw URL from F_Hauptframe JS, never wrap it into "sid=https://..."
        custom_drv = MagicMock()
        custom_drv.current_url = "https://spaceinvasion.bitmeup.com/indexGame.es"
        custom_drv.find_elements.return_value = []
        custom_drv.execute_script.side_effect = [
            "",
            "https://spaceinvasion.bitmeup.com/indexInternal.es?action=internalMenu",
            "https://spaceinvasion.bitmeup.com/indexInternal.es?action=internalHome",
        ]
        custom_cli = GameBrowserClient(custom_drv, min_nav_delay=(0.0, 0.0), timeout=2.0)
        self.assertEqual(custom_cli._resolve_sid_query(), "")

        # Valid token or sid= link inside F_Hauptframe is still extracted cleanly
        custom_drv.execute_script.side_effect = [
            "",
            "https://spaceinvasion.bitmeup.com/indexInternal.es?action=internalMenu",
            "https://spaceinvasion.bitmeup.com/indexInternal.es?action=internalHome&sid=hfToken99",
        ]
        self.assertEqual(custom_cli._resolve_sid_query(), "sid=hfToken99")

        custom_drv.execute_script.side_effect = [
            "",
            "about:blank",
            "rawWindowSid42",
        ]
        self.assertEqual(custom_cli._resolve_sid_query(), "sid=rawWindowSid42")

    def test_check_page_guards_ignores_passive_turnstile_and_live_dom_hidden_gate(self) -> None:
        driver = _FakeDriver()
        client = GameBrowserClient(driver, min_nav_delay=(0.0, 0.0), timeout=2.0)
        passive_html = (
            '<html><head><script src="https://challenges.cloudflare.com/turnstile/v0/api.js"></script></head>'
            '<body><div id="si-captcha-gate" style="display:none;"><div class="cf-turnstile"></div></div>'
            '<div>Normal game frame</div></body></html>'
        )
        client._check_page_guards(passive_html)

        visible_class_only_html = '<html><body><div id="si-captcha-gate"><div class="cf-turnstile"></div></div></body></html>'
        with patch.object(client, "_is_antibot_visible_in_live_dom", return_value=False):
            client._check_page_guards(visible_class_only_html)
        with patch.object(client, "_is_antibot_visible_in_live_dom", return_value=True):
            with self.assertRaises(AntiBotChallengeError):
                client._check_page_guards(visible_class_only_html)

    def test_step_galaxy_system_uses_live_ajax_result_coords_and_refreshes_json_element(self) -> None:
        # Regression: after initial full load on 02:129, AJAX `window.si.navigate(2, 387)` leaves
        # `<input value="129">`, inline `SI_TPL.galaxy`, and `location.href` stale at 129.
        # `step_galaxy_system` MUST return `(2, 387)` AND `_sync_galaxy_json_textarea` must
        # overwrite stale `#json-element` from live `window.si.result` (not skip when non-empty).
        stale_html = (
            "<html><body>"
            "<script>SI_TPL.galaxy = { galaxy: 2, sun: 129 };</script>"
            '<input name="galaxy" value="2"><input name="system" value="129">'
            '<textarea id="json-element">{"position":{"galaxy":2,"system":129},"planets":{}}</textarea>'
            "</body></html>"
        )
        drv = MagicMock()
        drv.page_source = stale_html
        scripts_run: list[str] = []

        def _exec(script: str, *args):
            scripts_run.append(script)
            if "typeof window.si.navigate === 'function'" in script:
                return {"ok": True, "g": 2, "s": 129, "busy": False}
            if "window.si.navigate(targetG, targetS)" in script:
                return None
            if "resG:" in script:
                return {"busy": False, "g": 2, "s": 387, "resG": 2, "resS": 387}
            if "getElementById('json-element')" in script:
                # Assert live window.si.result is always serialized even when je.value is non-empty
                self.assertIn("if (window.si && window.si.result && window.si.result.planets)", script)
                self.assertNotIn("if ((!v || v === 'null') && window.si", script)
                return None
            if "location.href" in script:
                return "https://spaceinvasion.bitmeup.com/indexInternal.es?action=internalGalaxy&g=2&s=129"
            return None

        drv.execute_script.side_effect = _exec
        cli = GameBrowserClient(drv, min_nav_delay=(0.0, 0.0), timeout=2.0)
        with patch.object(cli, "_switch_frame"):
            _html, actual_gal, actual_sys = cli.step_galaxy_system(galaxy=2, system=387, pace=False)
        self.assertEqual((actual_gal, actual_sys), (2, 387))
        self.assertEqual(cli.last_galaxy_coords, (2, 387))

    def test_step_galaxy_system_prioritizes_arrow_button_when_distance_under_5_and_falls_back(self) -> None:
        def _make_driver(start_g: int, start_s: int, *, arrow_available: bool = True):
            state = {"g": start_g, "s": start_s, "arrow_clicks": [], "nav_calls": []}
            drv = MagicMock()
            drv.page_source = (
                '<html><body><textarea id="json-element">'
                f'{{"position":{{"galaxy":{start_g},"system":{start_s}}},"planets":{{}}}}'
                "</textarea></body></html>"
            )

            def _exec(script: str, *args):
                if "typeof window.si.navigate === 'function'" in script:
                    return {"ok": True, "g": state["g"], "s": state["s"], "maxS": 400, "busy": False}
                if "navigateSystem(1)" in script and "querySelectorAll" in script:
                    if not arrow_available:
                        return {"clicked": "none"}
                    delta = int(args[0])
                    state["arrow_clicks"].append(delta)
                    state["s"] = ((state["s"] - 1 + delta) % 400) + 1
                    drv.page_source = (
                        '<html><body><textarea id="json-element">'
                        f'{{"position":{{"galaxy":{state["g"]},"system":{state["s"]}}},"planets":{{}}}}'
                        "</textarea></body></html>"
                    )
                    return {"clicked": "dom"}
                if "window.si.navigate(targetG, targetS)" in script:
                    state["g"] = int(args[0])
                    state["s"] = int(args[1])
                    state["nav_calls"].append((state["g"], state["s"]))
                    drv.page_source = (
                        '<html><body><textarea id="json-element">'
                        f'{{"position":{{"galaxy":{state["g"]},"system":{state["s"]}}},"planets":{{}}}}'
                        "</textarea></body></html>"
                    )
                    return None
                if "resG:" in script:
                    return {
                        "busy": False,
                        "g": state["g"],
                        "s": state["s"],
                        "resG": state["g"],
                        "resS": state["s"],
                    }
                return None

            drv.execute_script.side_effect = _exec
            return drv, state

        with patch("spaceinvasion.session.client.time.sleep"):
            # 1. Forward dist=1 -> single +1 arrow click, zero si.navigate calls
            drv1, st1 = _make_driver(2, 380)
            cli1 = GameBrowserClient(drv1, min_nav_delay=(0.0, 0.0), timeout=2.0)
            with patch.object(cli1, "_switch_frame"):
                _h, g, s = cli1.step_galaxy_system(galaxy=2, system=381, pace=False)
            self.assertEqual((g, s), (2, 381))
            self.assertEqual(cli1.last_galaxy_nav_method, "arrow")
            self.assertEqual(cli1.last_galaxy_step_clicks, 1)
            self.assertEqual(st1["arrow_clicks"], [1])
            self.assertEqual(st1["nav_calls"], [])

            # 2. Backward dist=3 (< 5) -> 3 backward arrow clicks + intermediate callback for 379 & 378
            drv2, st2 = _make_driver(2, 380)
            cli2 = GameBrowserClient(drv2, min_nav_delay=(0.0, 0.0), timeout=2.0)
            inter_seen: list[tuple[int, int]] = []
            with patch.object(cli2, "_switch_frame"):
                _h, g, s = cli2.step_galaxy_system(
                    galaxy=2,
                    system=377,
                    pace=False,
                    on_intermediate_page=lambda _html, ig, isys: inter_seen.append((ig, isys)),
                )
            self.assertEqual((g, s), (2, 377))
            self.assertEqual(cli2.last_galaxy_nav_method, "arrow")
            self.assertEqual(cli2.last_galaxy_step_clicks, 3)
            self.assertEqual(st2["arrow_clicks"], [-1, -1, -1])
            self.assertEqual(inter_seen, [(2, 379), (2, 378)])
            self.assertEqual(st2["nav_calls"], [])

            # 3. Circular wraparound dist=2 (400 -> 2) -> 2 forward arrow clicks + intermediate (2, 1)
            drv3, st3 = _make_driver(2, 400)
            cli3 = GameBrowserClient(drv3, min_nav_delay=(0.0, 0.0), timeout=2.0)
            wrap_inter: list[tuple[int, int]] = []
            with patch.object(cli3, "_switch_frame"):
                _h, g, s = cli3.step_galaxy_system(
                    galaxy=2,
                    system=2,
                    pace=False,
                    on_intermediate_page=lambda _html, ig, isys: wrap_inter.append((ig, isys)),
                )
            self.assertEqual((g, s), (2, 2))
            self.assertEqual(cli3.last_galaxy_nav_method, "arrow")
            self.assertEqual(st3["arrow_clicks"], [1, 1])
            self.assertEqual(wrap_inter, [(2, 1)])

            # 4. Distance == 5 (NOT < 5) -> direct si.navigate jump, zero arrow clicks
            drv4, st4 = _make_driver(2, 380)
            cli4 = GameBrowserClient(drv4, min_nav_delay=(0.0, 0.0), timeout=2.0)
            with patch.object(cli4, "_switch_frame"):
                _h, g, s = cli4.step_galaxy_system(galaxy=2, system=385, pace=False)
            self.assertEqual((g, s), (2, 385))
            self.assertEqual(cli4.last_galaxy_nav_method, "ajax_navigate")
            self.assertEqual(st4["arrow_clicks"], [])
            self.assertEqual(st4["nav_calls"], [(2, 385)])

            # 5. Cross-galaxy (2:380 -> 3:381) -> direct si.navigate jump, zero arrow clicks
            drv5, st5 = _make_driver(2, 380)
            cli5 = GameBrowserClient(drv5, min_nav_delay=(0.0, 0.0), timeout=2.0)
            with patch.object(cli5, "_switch_frame"):
                _h, g, s = cli5.step_galaxy_system(galaxy=3, system=381, pace=False)
            self.assertEqual((g, s), (3, 381))
            self.assertEqual(cli5.last_galaxy_nav_method, "ajax_navigate")
            self.assertEqual(st5["arrow_clicks"], [])
            self.assertEqual(st5["nav_calls"], [(3, 381)])

            # 6. Arrow DOM missing -> clean fallback to si.navigate
            drv6, st6 = _make_driver(2, 380, arrow_available=False)
            cli6 = GameBrowserClient(drv6, min_nav_delay=(0.0, 0.0), timeout=2.0)
            with patch.object(cli6, "_switch_frame"):
                _h, g, s = cli6.step_galaxy_system(galaxy=2, system=381, pace=False)
            self.assertEqual((g, s), (2, 381))
            self.assertEqual(cli6.last_galaxy_nav_method, "ajax_navigate")
            self.assertEqual(st6["nav_calls"], [(2, 381)])

    def test_renderer_timeout_recovery_ensure_wait_navigate_and_open_url(self) -> None:
        from selenium.common.exceptions import TimeoutException

        raw_timeout_msg = (
            "timeout: Timed out receiving message from renderer: 20.000\n"
            "  (Session info: chrome=158.0.8127.36)\n"
            "Stacktrace:\n"
            "#0 0x5635f98bdf7a <unknown>\n"
            "#24 0x7f7fbe121678 <unknown>"
        )

        # 1. _recover_renderer_timeout bounds script timeout to 3.0s, calls window.stop(), and restores timeout
        drv_rec = MagicMock()
        script_timeouts: list[float] = []
        drv_rec.set_script_timeout.side_effect = lambda v: script_timeouts.append(float(v))
        cli_rec = GameBrowserClient(drv_rec, min_nav_delay=(0.0, 0.0), timeout=25.0)
        with patch("spaceinvasion.session.client.time.sleep"):
            cli_rec._recover_renderer_timeout()
        self.assertEqual(script_timeouts, [3.0, 25.0])
        stop_calls = [
            call.args[0] for call in drv_rec.execute_script.call_args_list if call.args and "window.stop()" in str(call.args[0])
        ]
        self.assertGreaterEqual(len(stop_calls), 2)

        # 2. ensure_in_game recovers once from TimeoutException and raises clean SpaceInvasionAuthError on repeat
        drv_ens = _FakeDriver()
        drv_ens.frames["F_planets"].elements_by_id["json-container"] = _FakeElement(
            tag="script",
            text=_sample_planets_json(),
        )
        cli_ens = GameBrowserClient(drv_ens, min_nav_delay=(0.0, 0.0), timeout=2.0)
        orig_find = drv_ens.find_element
        fail_left = [1]

        def _flaky_find(by, val):
            if fail_left[0] > 0 and val == "F_planets":
                fail_left[0] -= 1
                raise TimeoutException(raw_timeout_msg)
            return orig_find(by, val)

        drv_ens.find_element = _flaky_find  # type: ignore[method-assign]
        with patch.object(cli_ens, "_recover_renderer_timeout") as mock_rec:
            cli_ens.ensure_in_game()
        self.assertEqual(mock_rec.call_count, 1)

        fail_left[0] = 5
        with patch.object(cli_ens, "_recover_renderer_timeout") as mock_rec2:
            with self.assertRaises(SpaceInvasionAuthError) as ctx_ens:
                cli_ens.ensure_in_game()
        self.assertEqual(mock_rec2.call_count, 1)
        self.assertIn("Renderer timed out", str(ctx_ens.exception))
        self.assertNotIn("Stacktrace:", str(ctx_ens.exception))

        # 3. _wait_frame_ready recovers once from TimeoutException and accepts interactive landed frame
        drv_wait = MagicMock()
        valid_html = "<html><body><div class='buildings-panel'>" + ("x" * 100) + "</div></body></html>"
        drv_wait.page_source = valid_html
        wait_probes = [
            TimeoutException(raw_timeout_msg),
            {
                "ready": "interactive",
                "href": "https://spaceinvasion.bitmeup.com/indexInternal.es?action=internalBuildings&sid=tok",
                "token": None,
            },
        ]
        def _wait_exec(script, *args):
            if "document.readyState" in script:
                val = wait_probes.pop(0)
                if isinstance(val, BaseException):
                    raise val
                return val
            return None

        drv_wait.execute_script.side_effect = _wait_exec
        cli_wait = GameBrowserClient(drv_wait, min_nav_delay=(0.0, 0.0), timeout=2.0)
        with patch.object(cli_wait, "_switch_frame"), patch.object(cli_wait, "_recover_renderer_timeout") as mock_wrec:
            got_html = cli_wait._wait_frame_ready("F_Hauptframe", expected_action="internalBuildings", nav_token="tok-old")
        self.assertEqual(got_html, valid_html)
        self.assertEqual(mock_wrec.call_count, 1)

        # Repeated TimeoutException inside _wait_frame_ready raises clean SpaceInvasionAuthError
        drv_wait2 = MagicMock()
        drv_wait2.execute_script.side_effect = TimeoutException(raw_timeout_msg)
        cli_wait2 = GameBrowserClient(drv_wait2, min_nav_delay=(0.0, 0.0), timeout=2.0)
        with patch.object(cli_wait2, "_switch_frame"), patch.object(cli_wait2, "_recover_renderer_timeout") as mock_wrec2:
            with self.assertRaises(SpaceInvasionAuthError) as ctx_w:
                cli_wait2._wait_frame_ready("F_Hauptframe", expected_action="internalBuildings", nav_token="tok-old")
        self.assertEqual(mock_wrec2.call_count, 1)
        self.assertIn("Renderer timed out waiting for frame F_Hauptframe", str(ctx_w.exception))
        self.assertNotIn("Stacktrace:", str(ctx_w.exception))

        # 4. navigate_menu: (a) already-landed after click timeout returns without double-click
        drv_nav1 = MagicMock()
        drv_nav1.page_source = valid_html
        click_count_1 = [0]
        link1 = MagicMock()

        def _click_timeout_once():
            click_count_1[0] += 1
            raise TimeoutException(raw_timeout_msg)

        link1.click.side_effect = _click_timeout_once
        drv_nav1.find_element.return_value = link1
        drv_nav1.execute_script.side_effect = lambda script, *args: (
            {
                "ready": "interactive",
                "href": "https://spaceinvasion.bitmeup.com/indexInternal.es?action=internalBuildings",
                "token": None,
            }
            if "document.readyState" in script
            else None
        )
        cli_nav1 = GameBrowserClient(drv_nav1, min_nav_delay=(0.0, 0.0), timeout=2.0)
        with patch.object(cli_nav1, "_switch_frame"), patch.object(cli_nav1, "_recover_renderer_timeout") as mock_nrec1:
            html_nav1 = cli_nav1.navigate_menu("internalBuildings")
        self.assertEqual(html_nav1, valid_html)
        self.assertEqual(click_count_1[0], 1)
        self.assertEqual(mock_nrec1.call_count, 1)

        # (b) stale frame (same nav_token) causes navigate_menu to retry click once and succeed
        drv_nav2 = MagicMock()
        drv_nav2.page_source = valid_html
        click_count_2 = [0]
        last_token = [""]
        link2 = MagicMock()

        def _click_fail_first():
            click_count_2[0] += 1
            if click_count_2[0] == 1:
                raise TimeoutException(raw_timeout_msg)

        link2.click.side_effect = _click_fail_first
        drv_nav2.find_element.return_value = link2

        def _nav2_exec(script, *args):
            if "__siNavToken = arguments[0]" in script:
                last_token[0] = str(args[0])
                return None
            if "document.readyState" in script:
                # Attempt 1 recovery sees stale token -> returns None; Attempt 2 sees cleared token -> succeeds
                return {
                    "ready": "complete",
                    "href": "https://spaceinvasion.bitmeup.com/indexInternal.es?action=internalBuildings",
                    "token": last_token[0] if click_count_2[0] == 1 else None,
                }
            return None

        drv_nav2.execute_script.side_effect = _nav2_exec
        cli_nav2 = GameBrowserClient(drv_nav2, min_nav_delay=(0.0, 0.0), timeout=2.0)
        with patch.object(cli_nav2, "_switch_frame"), patch.object(cli_nav2, "_recover_renderer_timeout") as mock_nrec2:
            html_nav2 = cli_nav2.navigate_menu("internalBuildings")
        self.assertEqual(html_nav2, valid_html)
        self.assertEqual(click_count_2[0], 2)
        self.assertEqual(mock_nrec2.call_count, 1)

        # (c) _open_frame_url_with_retry raises clean SpaceInvasionAuthError after 2 consecutive timeouts
        drv_url = MagicMock()
        drv_url.execute_script.side_effect = lambda script, *args: (
            (_ for _ in ()).throw(TimeoutException(raw_timeout_msg)) if "location.href =" in script else None
        )
        cli_url = GameBrowserClient(drv_url, min_nav_delay=(0.0, 0.0), timeout=2.0)
        with patch.object(cli_url, "_switch_frame"), patch.object(cli_url, "_recover_renderer_timeout") as mock_urec:
            with self.assertRaises(SpaceInvasionAuthError) as ctx_u:
                cli_url._open_frame_url_with_retry(
                    "https://spaceinvasion.bitmeup.com/indexInternal.es?action=internalMessages&sid=abc",
                    expected_action="internalMessages",
                )
        self.assertEqual(mock_urec.call_count, 1)
        self.assertIn("Renderer timed out loading internalMessages", str(ctx_u.exception))
        self.assertNotIn("Stacktrace:", str(ctx_u.exception))

    def test_step_galaxy_system_max_galaxies_wrap_widescreen_swing_and_real_arrow_click(self) -> None:
        # 1. Live RETRO 8-galaxy wrap: browser on (1, 343) with maxG=8, maxS=400; requesting (9, 344)
        #    normalizes 9 -> 1, records last_galaxy_max_galaxies=8, and clicks +1 system arrow (zero full reload).
        state1 = {"g": 1, "s": 343, "sys_arrows": [], "gal_arrows": [], "nav_calls": [], "scripts": []}
        drv1 = MagicMock()
        drv1.page_source = (
            '<html><body><textarea id="json-element">'
            '{"position":{"galaxy":1,"system":343},"planets":{}}'
            "</textarea></body></html>"
        )

        def _exec1(script: str, *args):
            state1["scripts"].append(script)
            if "typeof window.si.navigate === 'function'" in script:
                return {"ok": True, "g": state1["g"], "s": state1["s"], "maxS": 400, "maxG": 8, "busy": False}
            if "navigateSystem(1)" in script and "querySelectorAll" in script:
                self.assertIn("g2w.nav(\\'sys\\',1)", script)
                self.assertIn("parentElement", script)
                delta = int(args[0])
                state1["sys_arrows"].append(delta)
                state1["s"] = ((state1["s"] - 1 + delta) % 400) + 1
                drv1.page_source = (
                    '<html><body><textarea id="json-element">'
                    f'{{"position":{{"galaxy":{state1["g"]},"system":{state1["s"]}}},"planets":{{}}}}'
                    "</textarea></body></html>"
                )
                return {"clicked": "dom"}
            if "navigateGalaxy(1)" in script and "querySelectorAll" in script:
                self.assertIn("g2w.nav(\\'gal\\',1)", script)
                delta = int(args[0])
                state1["gal_arrows"].append(delta)
                state1["g"] = ((state1["g"] - 1 + delta) % 8) + 1
                drv1.page_source = (
                    '<html><body><textarea id="json-element">'
                    f'{{"position":{{"galaxy":{state1["g"]},"system":{state1["s"]}}},"planets":{{}}}}'
                    "</textarea></body></html>"
                )
                return {"clicked": "dom"}
            if "window.si.navigate(targetG, targetS)" in script:
                state1["nav_calls"].append((int(args[0]), int(args[1])))
                state1["g"], state1["s"] = int(args[0]), int(args[1])
                return None
            if "resG:" in script:
                return {
                    "busy": False,
                    "animating": False,
                    "g": state1["g"],
                    "s": state1["s"],
                    "resG": state1["g"],
                    "resS": state1["s"],
                }
            return None

        drv1.execute_script.side_effect = _exec1
        cli1 = GameBrowserClient(drv1, min_nav_delay=(0.0, 0.0), timeout=2.0)
        with patch.object(cli1, "_switch_frame"), patch("spaceinvasion.session.client.time.sleep"):
            _h, g1, s1 = cli1.step_galaxy_system(galaxy=9, system=344, pace=False)
            self.assertEqual((g1, s1), (1, 344))
            self.assertEqual(cli1.last_galaxy_max_galaxies, 8)
            self.assertEqual(cli1.last_galaxy_max_system, 400)
            self.assertEqual(cli1.last_galaxy_nav_method, "arrow")
            self.assertEqual(state1["sys_arrows"], [1])
            self.assertEqual(state1["nav_calls"], [])

            # 2. Same-system adjacent galaxy (1:344 -> 2:344) clicks the galaxy arrow button
            _h2, g2, s2 = cli1.step_galaxy_system(galaxy=2, system=344, pace=False)
            self.assertEqual((g2, s2), (2, 344))
            self.assertEqual(cli1.last_galaxy_nav_method, "arrow")
            self.assertEqual(state1["gal_arrows"], [1])
            self.assertEqual(state1["nav_calls"], [])

        # 3. Widescreen g2w 420ms swing() animation: idle & unchanged for >0.30s before si.navigate fires
        #    must NOT prematurely bail at 0.30s and must complete via arrow!
        clock_ticks = [100.0, 100.15, 100.35, 100.55, 100.70, 100.80]
        tick_idx = [0]

        def _fake_mono() -> float:
            val = clock_ticks[min(tick_idx[0], len(clock_ticks) - 1)]
            tick_idx[0] += 1
            return val

        poll_n = [0]
        drv_w = MagicMock()
        drv_w.page_source = (
            '<html><body><textarea id="json-element">'
            '{"position":{"galaxy":2,"system":381},"planets":{}}'
            "</textarea></body></html>"
        )

        def _exec_w(script: str, *args):
            if "typeof window.si.navigate === 'function'" in script:
                return {"ok": True, "g": 2, "s": 380, "maxS": 400, "maxG": 8, "busy": False}
            if "navigateSystem(1)" in script and "querySelectorAll" in script:
                return {"clicked": "dom"}
            if "resG:" in script:
                poll_n[0] += 1
                if poll_n[0] <= 2:
                    # 0.15s & 0.35s: still in 420ms g2w swing animation (not busy yet, coords still 2:380)
                    return {"busy": False, "animating": True, "g": 2, "s": 380, "resG": 2, "resS": 380}
                if poll_n[0] == 3:
                    # 0.55s: swing ended, XHR in flight
                    return {"busy": True, "animating": False, "g": 2, "s": 381, "resG": 2, "resS": 380}
                return {"busy": False, "animating": False, "g": 2, "s": 381, "resG": 2, "resS": 381}
            if "window.si.navigate(targetG, targetS)" in script:
                raise AssertionError("Widescreen swing should not fall back to si.navigate!")
            return None

        drv_w.execute_script.side_effect = _exec_w
        cli_w = GameBrowserClient(drv_w, min_nav_delay=(0.0, 0.0), timeout=4.0)
        with (
            patch.object(cli_w, "_switch_frame"),
            patch("spaceinvasion.session.client.time.sleep"),
            patch("spaceinvasion.session.client.time.monotonic", side_effect=_fake_mono),
        ):
            _hw, gw, sw = cli_w.step_galaxy_system(galaxy=2, system=381, pace=False)
        self.assertEqual((gw, sw), (2, 381))
        self.assertEqual(cli_w.last_galaxy_nav_method, "arrow")

        # 4. Trusted Selenium element click + ElementClickInterceptedException JS fallback
        el_ok = _FakeElement(tag="img", attrs={"onclick": "si.navigateSystem(1);"})
        el_int = _FakeElement(tag="span", attrs={"onclick": "g2w.nav('sys',1)"}, intercept_click=True)
        drv_el = MagicMock()
        js_clicks: list[Any] = []

        def _exec_el(script: str, *args):
            if "arguments[0].click();" in script:
                js_clicks.append(args[0])
                return None
            return None

        drv_el.execute_script.side_effect = _exec_el
        cli_el = GameBrowserClient(drv_el, min_nav_delay=(0.0, 0.0), timeout=2.0)
        self.assertEqual(
            cli_el._finalize_galaxy_arrow_click({"clicked": "element", "el": el_ok}, fallback_js="return false;", step_dir=1),
            "dom",
        )
        self.assertEqual(el_ok.clicks, 1)
        self.assertEqual(js_clicks, [])

        self.assertEqual(
            cli_el._finalize_galaxy_arrow_click({"clicked": "element", "el": el_int}, fallback_js="return false;", step_dir=1),
            "dom",
        )
        self.assertEqual(js_clicks, [el_int])

    def test_open_galaxy_2d_skips_form_submit_and_ajax_ready_ignores_planet_list_dropdown(self) -> None:
        # Live RETRO 2D Galaxy HTML has NO <form> tags, a static hidden #planet-list dropdown, and
        # SI_TPL.galaxy with nested json:{...} before maxSystem:400,maxGalaxies:8.
        live_2d_html = (
            '<html><body>'
            '<div id="planet-list" style="display:none;"><table><tr><td>Own Home</td></tr></table></div>'
            '<div id="galaxy-container"><div class="planet-hit" data-planet="1"></div></div>'
            '<textarea id="json-element">{"position":{"galaxy":1,"system":344},"planets":{}}</textarea>'
            '<script>SI_TPL.galaxy={galaxy:1,sun:344,json:{"b":{"k":"x"},"header":{"universe":{"id":31}}},ts:1,maxSystem:400,maxGalaxies:8};</script>'
            "</body></html>"
        )
        ajax_scripts: list[str] = []
        drv = MagicMock()
        drv.page_source = live_2d_html

        def _exec(script: str, *args):
            if "galaxy-container" in script or "planet-list" in script:
                ajax_scripts.append(script)
                return True
            if "window.si.maxGalaxies" in script and "maxG:" in script:
                return {"ok": True, "maxG": 8, "maxS": 400}
            if "location.href" in script:
                return "https://spaceinvasion.bitmeup.com/indexInternal.es?action=internalGalaxy&g=9&s=344"
            return None

        drv.execute_script.side_effect = _exec
        cli = GameBrowserClient(drv, min_nav_delay=(0.0, 0.0), timeout=2.0)
        with (
            patch.object(cli, "_switch_frame"),
            patch.object(cli, "_resolve_sid_query", return_value="sid=abc123"),
            patch.object(cli, "_build_internal_url", return_value="https://spaceinvasion.bitmeup.com/indexInternal.es?action=internalGalaxy&sid=abc123&g=9&s=344"),
            patch.object(cli, "_open_frame_url_with_retry", return_value=live_2d_html),
            patch.object(cli, "_submit_galaxy_coords_form", side_effect=AssertionError("Must not call _submit_galaxy_coords_form on 2D Galaxy")),
        ):
            _html, ag, asys = cli.open_galaxy_with_coords(galaxy=9, system=344)

        self.assertEqual((ag, asys), (1, 344))
        self.assertEqual(cli.last_galaxy_max_galaxies, 8)
        self.assertEqual(cli.last_galaxy_max_system, 400)
        self.assertTrue(ajax_scripts)
        self.assertNotIn("planet-list", ajax_scripts[0])
        self.assertIn("galaxy-container", ajax_scripts[0])

        # Also verify mismatch on 2D Galaxy (e.g. p_galaxy() landed on 1:344 when 1:345 wanted)
        # steps via step_galaxy_system(_allow_full_load=False) instead of _submit_galaxy_coords_form!
        stepped_args: list[dict[str, Any]] = []

        def _fake_step(**kw):
            stepped_args.append(kw)
            return live_2d_html, kw["galaxy"], kw["system"]

        with (
            patch.object(cli, "_switch_frame"),
            patch.object(cli, "_resolve_sid_query", return_value="sid=abc123"),
            patch.object(cli, "_build_internal_url", return_value="https://spaceinvasion.bitmeup.com/indexInternal.es?action=internalGalaxy&sid=abc123&g=1&s=345"),
            patch.object(cli, "_open_frame_url_with_retry", return_value=live_2d_html),
            patch.object(cli, "step_galaxy_system", side_effect=_fake_step),
            patch.object(cli, "_submit_galaxy_coords_form", side_effect=AssertionError("Must not call _submit_galaxy_coords_form on 2D Galaxy")),
        ):
            _html2, ag2, asys2 = cli.open_galaxy_with_coords(galaxy=1, system=345)
        self.assertEqual((ag2, asys2), (1, 345))
        self.assertEqual(len(stepped_args), 1)
        self.assertEqual(stepped_args[0]["galaxy"], 1)
        self.assertEqual(stepped_args[0]["system"], 345)
        self.assertFalse(stepped_args[0]["_allow_full_load"])


if __name__ == "__main__":
    unittest.main()
