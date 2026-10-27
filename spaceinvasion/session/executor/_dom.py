"""Low-level Selenium DOM click, input-fill, and planet-select helpers for `exec_ops`."""

from __future__ import annotations

import contextlib
import re

from bs4 import BeautifulSoup
from selenium.common.exceptions import ElementClickInterceptedException, WebDriverException

from spaceinvasion.core import catalog
from spaceinvasion.session.client._protocols import SELENIUM_NAV_ERRORS, WebElementLike
from spaceinvasion.session.executor._protocols import BrowserClientLike


def mark_frame_nav(client: BrowserClientLike, frame_id: str = "F_Hauptframe") -> str | None:
    try:
        tok = client._mark_frame_navigation(frame_id)
    except AttributeError:
        return None
    return tok if isinstance(tok, str) else None


def safe_click(client: BrowserClientLike, el: WebElementLike) -> None:
    with contextlib.suppress(*SELENIUM_NAV_ERRORS):
        client.driver.execute_script("arguments[0].scrollIntoView({block: 'center'});", el)
    try:
        el.click()
    except ElementClickInterceptedException:
        client.driver.execute_script("arguments[0].click();", el)


def set_text_input_value(client: BrowserClientLike, el: WebElementLike, value: str) -> None:
    el.clear()
    el.send_keys(value)
    with contextlib.suppress(WebDriverException, OSError, RuntimeError, ValueError, TypeError):
        cur_val = el.get_attribute("value")
        if isinstance(cur_val, (str, int, float)) and not isinstance(cur_val, bool) and str(cur_val) != value:
            client.driver.execute_script(
                "arguments[0].value = arguments[1]; "
                "arguments[0].dispatchEvent(new Event('input', {bubbles: true})); "
                "arguments[0].dispatchEvent(new Event('keyup', {bubbles: true})); "
                "arguments[0].dispatchEvent(new Event('change', {bubbles: true}));",
                el,
                value,
            )


def find_newplanet_switch_target(html: str, planet_id: int) -> tuple[bool, str | None]:
    """Inspect `<select name='planet'>` in `html` via BS4; return `(needs_switch, target_value)`."""
    pid = catalog.safe_optional_int(planet_id, None)
    if pid is None or pid < 0 or pid > catalog.MAX_JSON_SAFE_INT:
        return False, None
    if not html or "newplanet=" not in html.lower():
        return False, None
    soup = BeautifulSoup(html, "html.parser")
    select_el = soup.find("select", attrs={"name": "planet"})
    if select_el is None:
        return False, None
    target_re = re.compile(rf"newplanet={pid}(?!\d)", re.IGNORECASE | re.ASCII)
    target_value: str | None = None
    selected_value: str | None = None
    has_newplanet = False
    for opt in select_el.find_all("option"):
        val = str(opt.get("value") or "")
        if "newplanet=" in val.lower():
            has_newplanet = True
        if target_re.search(val):
            target_value = val
        if opt.has_attr("selected"):
            selected_value = val
    if not has_newplanet or target_value is None:
        return False, None
    if selected_value is not None and target_re.search(selected_value):
        return False, None
    return True, target_value
