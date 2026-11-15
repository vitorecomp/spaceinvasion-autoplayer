"""Protocols, exception tuples, and timeout/priority helpers for `backend.session.client`."""

from __future__ import annotations

from collections.abc import Callable, Sequence
import inspect
import math
import re
from typing import Protocol

from selenium.common.exceptions import (
    ElementClickInterceptedException,
    ElementNotInteractableException,
    NoSuchElementException,
    NoSuchFrameException,
    NoSuchWindowException,
    StaleElementReferenceException,
    TimeoutException,
    UnexpectedAlertPresentException,
    WebDriverException,
)

ARROW_NAV_MAX_DISTANCE: int = 5
_SID_TOKEN_RE: re.Pattern[str] = re.compile(r"^[A-Za-z0-9._-]{1,256}$")
_EMPTY_PAGEVIEW_MODAL_RE: re.Pattern[str] = re.compile(
    r"made\s+more\s+than\s*<span\b[^>]*\bid=[\"']?pageview-amount[\"']?[^>]*>"
    r"(?:\s|&nbsp;|&#160;|0|-)*</span>\s*page\s+views",
    re.IGNORECASE,
)

SELENIUM_NAV_ERRORS: tuple[type[BaseException], ...] = (
    WebDriverException,
    NoSuchElementException,
    NoSuchFrameException,
    NoSuchWindowException,
    StaleElementReferenceException,
    TimeoutException,
    ElementClickInterceptedException,
    ElementNotInteractableException,
    UnexpectedAlertPresentException,
    OSError,
    TimeoutError,
    RuntimeError,
    ValueError,
    TypeError,
    KeyError,
)


class WebElementLike(Protocol):
    """Structural protocol satisfied by Selenium `WebElement` and `FakeElement`."""

    @property
    def text(self) -> str: ...

    def click(self) -> None: ...

    def clear(self) -> None: ...

    def send_keys(self, value: str, /) -> None: ...

    def get_attribute(self, name: str, /) -> str | None: ...

    def is_displayed(self) -> bool: ...

    def is_enabled(self) -> bool: ...

    def is_selected(self) -> bool: ...

    def find_element(self, by: str, value: str, /) -> WebElementLike: ...

    def find_elements(self, by: str, value: str, /) -> Sequence[WebElementLike]: ...


class SwitchToLike(Protocol):
    """Structural protocol for `driver.switch_to`."""

    def default_content(self) -> None: ...

    @property
    def frame(self) -> Callable[..., None]: ...

    def window(self, window_name: str, /) -> None: ...


class WebDriverLike(Protocol):
    """Structural protocol satisfied by Selenium `WebDriver` and `FakeDriver`."""

    @property
    def current_url(self) -> str: ...

    @property
    def page_source(self) -> str: ...

    @property
    def window_handles(self) -> Sequence[str]: ...

    @property
    def switch_to(self) -> SwitchToLike: ...

    def find_element(self, by: str, value: str, /) -> WebElementLike: ...

    def find_elements(self, by: str, value: str, /) -> Sequence[WebElementLike]: ...

    def execute_script(self, script: str, *args: object) -> object: ...


class ClientFacadeHooks(Protocol):
    """Late-binding facade hooks so `patch.object(client, ...)` intercepts collaborator calls."""

    def _switch_frame(self, frame_id: str) -> None: ...

    def _resolve_sid_query(self) -> str: ...

    def _build_internal_url(self, query: str) -> str: ...

    def _open_frame_url_with_retry(
        self,
        target_url: str,
        *,
        expected_action: str,
        frame_id: str = "F_Hauptframe",
    ) -> str: ...

    def _recover_renderer_timeout(self) -> None: ...

    def _check_page_guards(self, html: str) -> None: ...

    def _is_antibot_visible_in_live_dom(self) -> bool | None: ...

    def _wait_galaxy_ajax_ready(self, html: str) -> str: ...

    def _record_galaxy_universe_bounds(
        self,
        *,
        max_galaxies: object = None,
        max_system: object = None,
        html: str = "",
    ) -> tuple[int | None, int | None]: ...

    def _submit_galaxy_coords_form(
        self,
        *,
        galaxy: int | None,
        system: int | None,
        priority: str = "interactive",
    ) -> str | None: ...

    def step_galaxy_system(
        self,
        *,
        galaxy: int,
        system: int,
        pace: bool = False,
        priority: str = "normal",
        max_arrow_distance: int = ...,
        on_intermediate_page: Callable[[str, int, int], None] | None = None,
        _allow_full_load: bool = True,
    ) -> tuple[str, int, int]: ...

    def read_planets_overview(self, *, refresh: bool = False, priority: str = "normal") -> object: ...

    def read_menu_status(self) -> tuple[dict[str, int], int | None, int | None]: ...

    def ensure_in_game(self, *, priority: str = "normal") -> None: ...


def client_monotonic() -> float:
    """Read monotonic clock via package-level `client.time` so clock patches apply cleanly."""
    import backend.session.client as client_pkg

    return float(client_pkg.time.monotonic())


def client_sleep(seconds: float) -> None:
    """Sleep via package-level `client.time` so test/harness time patches apply cleanly."""
    import backend.session.client as client_pkg

    client_pkg.time.sleep(seconds)


def client_find_and_switch_game_tab(driver: object) -> bool:
    """Invoke package-level `client.find_and_switch_game_tab` so unit-test patches apply."""
    import backend.session.client as client_pkg

    return bool(client_pkg.find_and_switch_game_tab(driver))


def is_real_selenium_driver(driver: object) -> bool:
    """Return True only for genuine Selenium WebDriver instances (not test fakes)."""
    return type(driver).__module__.startswith("selenium")


def safe_bounded_float(val: object, default: float, *, min_val: float = 0.05, max_val: float = 300.0) -> float:
    base = min_val if not math.isfinite(default) else max(min_val, min(max_val, float(default)))
    if isinstance(val, bool) or not isinstance(val, (int, float, str, bytes, bytearray)):
        return base
    if isinstance(val, (int, float)):
        parsed = float(val)
    else:
        txt = (bytes(val).decode("utf-8", errors="replace") if isinstance(val, (bytes, bytearray)) else val).strip()
        if not txt or not txt.isascii():
            return base
        try:
            parsed = float(txt)
        except ValueError:
            return base
    return base if not math.isfinite(parsed) else max(min_val, min(max_val, parsed))


def safe_positive_timeout(
    val: object, default: float = 20.0, *, min_val: float = 0.05, max_val: float = 300.0
) -> float:
    return safe_bounded_float(val, default, min_val=min_val, max_val=max_val)


def call_with_optional_priority(
    fn: Callable[..., object],
    *args: object,
    priority: str = "normal",
    **kwargs: object,
) -> object:
    """Forward `priority=` only if `fn` declares a `priority` parameter or `**kwargs`."""
    try:
        sig = inspect.signature(fn)
    except (TypeError, ValueError):
        return fn(*args, **kwargs)
    params = sig.parameters
    if "priority" in params or any(p.kind is inspect.Parameter.VAR_KEYWORD for p in params.values()):
        return fn(*args, priority=priority, **kwargs)
    return fn(*args, **kwargs)
