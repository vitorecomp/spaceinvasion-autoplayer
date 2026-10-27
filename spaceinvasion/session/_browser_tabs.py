"""Browser tab focus, authentication detection, and element typing helpers."""

from __future__ import annotations

import logging
import random
import time
from typing import Any

from spaceinvasion.session._browser_driver import (
    BROWSER_DOM_ERRORS,
    By,
    NoSuchElementException,
)

_LOGGER = logging.getLogger(__name__)


def _type_naturally(element: Any, text: str, delay_range: tuple[float, float]) -> None:
    """Clear an input element and type characters with light human-like cadence."""
    element.click()
    element.clear()
    low, high = delay_range
    for ch in text:
        element.send_keys(ch)
        if high > 0:
            time.sleep(random.uniform(low, high))


def _element_if_visible(driver: Any, by: Any, selector: str) -> Any:
    """Return matching element if present and displayed, else `None`."""
    try:
        el = driver.find_element(by, selector)
        return el if el.is_displayed() else None
    except NoSuchElementException:
        return None


def _is_portal_authenticated(driver: Any) -> bool:
    """Detect whether the BitMeUp portal or in-game view is authenticated."""
    try:
        has_shell_state = bool(
            driver.execute_script(
                "return Boolean(window.SI_TPL && (window.SI_TPL.startPortal || window.SI_TPL.gameTop));"
            )
        )
    except BROWSER_DOM_ERRORS:
        has_shell_state = False

    if has_shell_state:
        return True
    if _element_if_visible(driver, By.ID, "sip-tab-universe") is not None:
        return True
    if _element_if_visible(driver, By.ID, "F_Hauptframe") is not None:
        return True

    try:
        current_url = (driver.current_url or "").lower()
    except BROWSER_DOM_ERRORS:
        current_url = ""
    if "spaceinvasion.bitmeup.com" in current_url and "sid=" in current_url:
        if _element_if_visible(driver, By.ID, "bitmeup-login") is None:
            return True
    return False


def is_in_game_tab(driver: Any) -> bool:
    """Return True if the currently focused window holds a live SpaceInvasion game session."""
    try:
        driver.switch_to.default_content()
    except BROWSER_DOM_ERRORS as exc:
        _LOGGER.debug("switch_to.default_content failed in is_in_game_tab: %s", exc)
    try:
        f_planets = driver.find_element(By.ID, "F_planets")
        driver.find_element(By.ID, "F_Hauptframe")
        driver.find_element(By.ID, "F_Navi")
        try:
            driver.switch_to.frame(f_planets)
            driver.find_element(By.ID, "json-container")
            return True
        except BROWSER_DOM_ERRORS:
            return False
        finally:
            try:
                driver.switch_to.default_content()
            except BROWSER_DOM_ERRORS as exc:
                _LOGGER.debug("default_content restore failed in is_in_game_tab: %s", exc)
    except NoSuchElementException:
        cur = (getattr(driver, "current_url", "") or "").lower()
        return "indexgame.es" in cur and _element_if_visible(driver, By.ID, "F_Hauptframe") is not None
    except BROWSER_DOM_ERRORS:
        return False


def find_and_switch_game_tab(driver: Any) -> bool:
    """Scan open browser tabs and switch WebDriver to an active SpaceInvasion game tab."""
    if is_in_game_tab(driver):
        return True
    handles = list(getattr(driver, "window_handles", None) or [])
    if len(handles) <= 1:
        return False

    ordered: list[str] = []
    other: list[str] = []
    current_handle = getattr(driver, "current_window_handle", None)
    for handle in handles:
        if handle == current_handle:
            continue
        try:
            driver.switch_to.window(handle)
            url = (getattr(driver, "current_url", "") or "").lower()
        except BROWSER_DOM_ERRORS:
            continue
        if "indexgame.es" in url:
            ordered.append(handle)
        elif "spaceinvasion.bitmeup.com" in url:
            other.append(handle)

    for handle in ordered + other:
        try:
            driver.switch_to.window(handle)
            if is_in_game_tab(driver):
                return True
        except BROWSER_DOM_ERRORS:
            continue

    if current_handle in handles:
        try:
            driver.switch_to.window(current_handle)
        except BROWSER_DOM_ERRORS as exc:
            _LOGGER.debug("Failed restoring original tab handle: %s", exc)
    return False


def _select_spaceinvasion_tab(driver: Any, *, allow_new_tab: bool = True) -> None:
    """Focus any existing SpaceInvasion tab so Control UI (`127.0.0.1`) tabs stay untouched."""
    if find_and_switch_game_tab(driver):
        return

    handles = list(getattr(driver, "window_handles", None) or [])
    current_handle = getattr(driver, "current_window_handle", None)
    portal_candidates: list[str] = []
    login_candidates: list[str] = []

    for handle in handles:
        try:
            driver.switch_to.window(handle)
            url = (getattr(driver, "current_url", "") or "").lower()
        except BROWSER_DOM_ERRORS:
            continue
        if "spaceinvasion.bitmeup.com" in url:
            if _is_portal_authenticated(driver):
                portal_candidates.append(handle)
            else:
                login_candidates.append(handle)

    chosen: str | None = (
        portal_candidates[0] if portal_candidates else (login_candidates[0] if login_candidates else None)
    )
    if chosen is not None:
        driver.switch_to.window(chosen)
        return

    if current_handle in handles:
        try:
            driver.switch_to.window(current_handle)
        except BROWSER_DOM_ERRORS as exc:
            _LOGGER.debug("Failed restoring current tab handle: %s", exc)

    if allow_new_tab:
        try:
            cur = (getattr(driver, "current_url", "") or "").lower()
        except BROWSER_DOM_ERRORS:
            cur = ""
        if "127.0.0.1" in cur or "localhost" in cur or cur.startswith("chrome://"):
            switch_to = getattr(driver, "switch_to", None)
            new_win = getattr(switch_to, "new_window", None)
            if callable(new_win):
                try:
                    new_win("tab")
                except BROWSER_DOM_ERRORS as exc:
                    _LOGGER.debug("Failed opening fresh tab: %s", exc)


__all__ = [
    "_element_if_visible",
    "_is_portal_authenticated",
    "_select_spaceinvasion_tab",
    "_type_naturally",
    "find_and_switch_game_tab",
    "is_in_game_tab",
]
