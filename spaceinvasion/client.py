"""Selenium browser client for reading live SpaceInvasion Classic state."""

from __future__ import annotations

from dataclasses import replace
import random
import time
from typing import Optional

from selenium.common.exceptions import ElementClickInterceptedException, NoSuchElementException
from selenium.webdriver.common.by import By

from spaceinvasion.auth import AntiBotChallengeError, SpaceInvasionAuthError, detect_antibot_challenge
from spaceinvasion.browser import (
    DEFAULT_DEBUG_PORT,
    attach_chrome_driver,
    close_or_detach_driver,
    find_and_switch_game_tab,
    is_debug_port_open,
)
from spaceinvasion.state import (
    GameState,
    parse_building_queue,
    parse_buildings_page,
    parse_home_metadata,
    parse_menu_messages,
    parse_menu_status,
    parse_planets_overview_json,
    parse_research_page,
    parse_tutorial_from_home,
    parse_unit_factory_page,
)


class RateLimitError(SpaceInvasionAuthError):
    """Raised when SpaceInvasion's per-minute page-view throttle triggers."""


class GameBrowserClient:
    """Read-only / player-like browser client operating inside the 3-iframe game shell."""

    def __init__(
        self,
        driver,
        *,
        detach_on_close: bool = True,
        min_nav_delay: tuple[float, float] = (0.9, 1.8),
        timeout: float = 20.0,
    ) -> None:
        self.driver = driver
        self.detach_on_close = detach_on_close
        self.min_nav_delay = min_nav_delay
        self.timeout = timeout
        self._last_nav_at = 0.0

    @classmethod
    def attach(
        cls,
        port: int = DEFAULT_DEBUG_PORT,
        host: str = "127.0.0.1",
        timeout: float = 20.0,
    ) -> GameBrowserClient:
        """Attach to an already-open Chrome session inside the game."""
        if not is_debug_port_open(port, host):
            raise SpaceInvasionAuthError(
                f"No Chrome debug session is listening on {host}:{port}. "
                f"Start Chrome with `python3 login.py --debug-port {port} --detach`."
            )
        driver = attach_chrome_driver(port=port, timeout=timeout, host=host)
        return cls(driver, detach_on_close=True, timeout=timeout)

    def __enter__(self) -> GameBrowserClient:
        return self

    def __exit__(self, *_exc) -> None:
        self.close()

    def close(self) -> None:
        try:
            self.driver.switch_to.default_content()
        except Exception:
            pass
        close_or_detach_driver(self.driver, keep_running=self.detach_on_close)

    def _pace(self) -> None:
        low, high = self.min_nav_delay
        target_gap = random.uniform(low, high)
        elapsed = time.monotonic() - self._last_nav_at
        if self._last_nav_at > 0 and elapsed < target_gap:
            time.sleep(target_gap - elapsed)
        self._last_nav_at = time.monotonic()

    def _check_page_guards(self, html: str) -> None:
        issue = detect_antibot_challenge(200, html)
        if issue:
            raise AntiBotChallengeError(
                f"{issue}. Stopping automation; handle verification in Chrome."
            )
        if "made more than" in html.lower() and "page views" in html.lower():
            raise RateLimitError(
                "SpaceInvasion per-minute page-view throttle hit; waiting before next read."
            )

    def ensure_in_game(self) -> None:
        """Verify the attached browser has an active `indexGame.es` tab with game frames."""
        self.driver.switch_to.default_content()
        try:
            f_planets = self.driver.find_element(By.ID, "F_planets")
            self.driver.find_element(By.ID, "F_Hauptframe")
            self.driver.find_element(By.ID, "F_Navi")
            self.driver.switch_to.frame(f_planets)
            self.driver.find_element(By.ID, "json-container")
            self.driver.switch_to.default_content()
        except NoSuchElementException as exc:
            try:
                self.driver.switch_to.default_content()
            except Exception:
                pass
            if find_and_switch_game_tab(self.driver):
                return
            raise SpaceInvasionAuthError(
                f"Browser is not inside SpaceInvasion game shell (current URL: {self.driver.current_url}). "
                "Run `python3 login.py --attach` to enter the configured universe first."
            ) from exc

    def _switch_frame(self, frame_id: str) -> None:
        self.driver.switch_to.default_content()
        frame = self.driver.find_element(By.ID, frame_id)
        self.driver.switch_to.frame(frame)

    def _wait_frame_ready(self, frame_id: str, expected_action: Optional[str] = None) -> str:
        deadline = time.monotonic() + self.timeout
        while time.monotonic() < deadline:
            self._switch_frame(frame_id)
            ready = self.driver.execute_script("return document.readyState;")
            href = str(self.driver.execute_script("return location.href;") or "")
            if ready == "complete" and (not expected_action or expected_action in href):
                html = self.driver.page_source or ""
                self._check_page_guards(html)
                return html
            time.sleep(0.2)
        raise SpaceInvasionAuthError(
            f"Timed out waiting for frame {frame_id} ({expected_action or 'ready'})."
        )

    def navigate_menu(self, action: str) -> str:
        """Click a navigation link in `F_Navi` and wait for `F_Hauptframe` to load."""
        self._pace()
        self._switch_frame("F_Navi")
        link = self.driver.find_element(By.CSS_SELECTOR, f'a[href*="action={action}"]')
        self.driver.execute_script("arguments[0].scrollIntoView({block: 'center'});", link)
        try:
            link.click()
        except ElementClickInterceptedException:
            self.driver.execute_script("arguments[0].click();", link)
        return self._wait_frame_ready("F_Hauptframe", expected_action=action)

    def open_galaxy(self) -> str:
        """Open the Galaxy view via `F_Navi`'s `p_galaxy()` trigger."""
        self._pace()
        self._switch_frame("F_Navi")
        gal_link = self.driver.find_element(By.CSS_SELECTOR, 'a[onclick*="p_galaxy"]')
        self.driver.execute_script("arguments[0].scrollIntoView({block: 'center'});", gal_link)
        try:
            gal_link.click()
        except ElementClickInterceptedException:
            self.driver.execute_script("arguments[0].click();", gal_link)
        return self._wait_frame_ready("F_Hauptframe")

    def read_planets_overview(self, *, refresh: bool = False) -> GameState:
        """Read `#json-container` from `F_planets`, optionally reloading the iframe first."""
        self.driver.switch_to.default_content()
        planets_frame = self.driver.find_element(By.ID, "F_planets")
        frame_src = ""
        try:
            frame_src = str(planets_frame.get_attribute("src") or "")
        except Exception:
            frame_src = ""
        self.driver.switch_to.frame(planets_frame)

        inner_href = str(self.driver.execute_script("return location.href;") or "")
        wrong_frame_page = (
            bool(frame_src)
            and "internalplanetsoverview" not in inner_href.lower()
            and "internalplanetsoverview" in frame_src.lower()
        )
        if refresh or wrong_frame_page:
            if wrong_frame_page:
                self.driver.execute_script(
                    "window.__siReloadPending = 1; location.replace(arguments[0]);",
                    frame_src,
                )
            else:
                self.driver.execute_script("window.__siReloadPending = 1; location.reload();")
            deadline = time.monotonic() + min(self.timeout, 12.0)
            while time.monotonic() < deadline:
                self._switch_frame("F_planets")
                pending = self.driver.execute_script("return Boolean(window.__siReloadPending);")
                ready = self.driver.execute_script("return document.readyState;")
                if not pending and ready == "complete":
                    html = self.driver.page_source or ""
                    self._check_page_guards(html)
                    if self.driver.execute_script(
                        "return Boolean(document.getElementById('json-container'));"
                    ):
                        break
                time.sleep(0.2)
        try:
            jc = self.driver.find_element(By.ID, "json-container")
        except NoSuchElementException as exc:
            html = self.driver.page_source or ""
            self._check_page_guards(html)
            raise SpaceInvasionAuthError(
                "SpaceInvasion session expired (missing #json-container in F_planets)."
            ) from exc
        json_text = jc.get_attribute("textContent") or ""
        return parse_planets_overview_json(json_text)

    def read_menu_status(self) -> tuple[dict[str, int], Optional[int], Optional[int]]:
        """Read unread message counters, server points, and rank from `F_Navi`."""
        self._switch_frame("F_Navi")
        return parse_menu_status(self.driver.page_source or "")

    def read_unread_messages(self) -> dict[str, int]:
        """Read unread message category counters from `F_Navi`."""
        self._switch_frame("F_Navi")
        return parse_menu_messages(self.driver.page_source or "")

    def read_full_snapshot(
        self,
        *,
        include_buildings: bool = True,
        include_research: bool = True,
        include_shipyard: bool = True,
        return_home: bool = True,
    ) -> GameState:
        """Collect an empire snapshot across `F_planets`, `F_Navi`, and `F_Hauptframe`."""
        self.ensure_in_game()

        state = self.read_planets_overview(refresh=True)
        unread, account_points, account_rank = self.read_menu_status()

        # Check current main frame URL; navigate to internalHome if on another view
        self._switch_frame("F_Hauptframe")
        main_url = str(self.driver.execute_script("return location.href;") or "")
        if "action=internalHome" in main_url:
            home_html = self.page_source_safe()
        else:
            home_html = self.navigate_menu("internalHome")

        tutorial = parse_tutorial_from_home(home_html)
        active_pid, speedup, newbie, queues, alerts = parse_home_metadata(home_html)
        merged_queues = list(queues)
        research_offers = state.research_offers

        planets = list(state.planets)
        target_pid = active_pid or (planets[0].planet_id if planets else None)
        active_idx = next(
            (i for i, p in enumerate(planets) if p.planet_id == target_pid),
            0 if planets else None,
        )

        if include_buildings and planets and active_idx is not None:
            b_html = self.navigate_menu("internalBuildings")
            if tutorial is None:
                tutorial = parse_tutorial_from_home(b_html)
            b_queues = parse_building_queue(b_html)
            if b_queues:
                merged_queues = [q for q in merged_queues if q.category != "building"] + list(b_queues)
            planet = planets[active_idx]
            offers = parse_buildings_page(b_html, planet.buildings)
            planets[active_idx] = replace(planet, building_offers=offers)

        # Fetch live Research cards only when a Research lab exists and slot is idle or needed by tutorial
        active_p = planets[active_idx] if active_idx is not None else None
        research_busy = any(q.category == "research" for q in merged_queues)
        tut_needs_research = bool(
            tutorial and any(t.category == "research" and not t.completed for t in tutorial.tasks)
        )
        if (
            include_research
            and active_p is not None
            and active_p.building_level(8) >= 1
            and (not research_busy or tut_needs_research)
        ):
            r_html = self.navigate_menu("internalResearch")
            research_offers = parse_research_page(r_html, state.researches)

        # Fetch Weapons factory offers when Weapons factory L1+ exists
        if include_shipyard and active_p is not None and active_p.building_level(9) >= 1:
            wf_html = self.navigate_menu("internalWeaponFactory")
            ship_offers = parse_unit_factory_page(
                wf_html,
                unit_type="ship",
                owned_counts=active_p.ships,
            )
            planets[active_idx] = replace(planets[active_idx], ship_offers=ship_offers)

        if return_home and (include_buildings or include_research or include_shipyard):
            self.navigate_menu("internalHome")

        self.driver.switch_to.default_content()
        return replace(
            state,
            planets=tuple(planets),
            active_planet_id=target_pid or state.active_planet_id,
            unread_messages=unread,
            tutorial=tutorial,
            active_queues=tuple(merged_queues),
            fleet_alerts=alerts,
            research_offers=research_offers,
            speedup_factor=speedup,
            newbie_protection=newbie,
            account_points=account_points,
            account_rank=account_rank,
        )

    def page_source_safe(self) -> str:
        html = self.driver.page_source or ""
        self._check_page_guards(html)
        return html
