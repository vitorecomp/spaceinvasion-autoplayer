"""Thin `GameBrowserClient` facade composing explicit browser session collaborators."""

from __future__ import annotations

from collections.abc import Callable, Sequence
import logging
import threading

from spaceinvasion.core.coercion import fmt_compact_int, safe_key
from spaceinvasion.parsing.models import GameState
from spaceinvasion.session.auth import SpaceInvasionAuthError
from spaceinvasion.session.browser import (
    DEFAULT_DEBUG_HOST,
    DEFAULT_DEBUG_PORT,
    attach_chrome_driver,
    close_or_detach_driver,
    is_debug_port_open,
)
from spaceinvasion.session.browser_client._protocols import (
    ARROW_NAV_MAX_DISTANCE,
    SELENIUM_NAV_ERRORS,
    WebDriverLike,
    safe_bounded_float,
    safe_positive_timeout,
)
from spaceinvasion.session.browser_client._state import ClientSessionState
from spaceinvasion.session.browser_client.frames import FrameNavigator
from spaceinvasion.session.browser_client.galaxy import GalaxyNavigator
from spaceinvasion.session.browser_client.guards import PageGuardChecker
from spaceinvasion.session.browser_client.messages import MessageInboxOps
from spaceinvasion.session.browser_client.reader import StateSnapshotReader, extract_selected_planet_id
from spaceinvasion.session.limiter import PageViewLimiter, get_default_page_view_limiter

_LOGGER = logging.getLogger(__name__)


class GameBrowserClient:
    """Read-only / player-like browser client operating inside the 3-iframe game shell."""

    def __init__(
        self,
        driver: WebDriverLike,
        *,
        detach_on_close: bool = True,
        min_nav_delay: tuple[float, float] | None = None,
        timeout: float = 20.0,
        page_limiter: PageViewLimiter | None = None,
        limiter: PageViewLimiter | None = None,
        max_limiter_wait: float | None = None,
    ) -> None:
        resolved_limiter = page_limiter or limiter or get_default_page_view_limiter()
        resolved_delay = min_nav_delay if min_nav_delay is not None else resolved_limiter.min_delay_range
        resolved_timeout = safe_positive_timeout(timeout, 20.0, min_val=0.05, max_val=300.0)
        resolved_max_wait = (
            None
            if max_limiter_wait is None
            else safe_bounded_float(max_limiter_wait, 60.0, min_val=0.0, max_val=3600.0)
        )
        self._state = ClientSessionState(
            driver=driver,
            detach_on_close=detach_on_close,
            page_limiter=resolved_limiter,
            min_nav_delay_override=min_nav_delay,
            min_nav_delay=resolved_delay,
            timeout=resolved_timeout,
            max_limiter_wait=resolved_max_wait,
        )
        self._guards = PageGuardChecker(self._state)
        self._frames = FrameNavigator(self._state, self._guards)
        self._galaxy = GalaxyNavigator(self._state, self._frames, self._guards)
        self._messages = MessageInboxOps(self._state, self._frames)
        self._reader = StateSnapshotReader(self._state, self._frames, self._guards)

    @property
    def driver(self) -> WebDriverLike:
        return self._state.driver

    @driver.setter
    def driver(self, value: WebDriverLike) -> None:
        self._state.driver = value

    @property
    def detach_on_close(self) -> bool:
        return self._state.detach_on_close

    @detach_on_close.setter
    def detach_on_close(self, value: bool) -> None:
        self._state.detach_on_close = value

    @property
    def page_limiter(self) -> PageViewLimiter:
        return self._state.page_limiter

    @page_limiter.setter
    def page_limiter(self, value: PageViewLimiter) -> None:
        self._state.page_limiter = value

    @property
    def min_nav_delay(self) -> tuple[float, float]:
        return self._state.min_nav_delay

    @min_nav_delay.setter
    def min_nav_delay(self, value: tuple[float, float]) -> None:
        self._state.min_nav_delay = value

    @property
    def timeout(self) -> float:
        return self._state.timeout

    @timeout.setter
    def timeout(self, value: float) -> None:
        self._state.timeout = value

    @property
    def max_limiter_wait(self) -> float | None:
        return self._state.max_limiter_wait

    @max_limiter_wait.setter
    def max_limiter_wait(self, value: float | None) -> None:
        self._state.max_limiter_wait = value

    @property
    def last_galaxy_coords(self) -> tuple[int, int] | None:
        return self._state.last_galaxy_coords

    @last_galaxy_coords.setter
    def last_galaxy_coords(self, value: tuple[int, int] | None) -> None:
        self._state.last_galaxy_coords = value

    @property
    def last_galaxy_max_galaxies(self) -> int | None:
        return self._state.last_galaxy_max_galaxies

    @last_galaxy_max_galaxies.setter
    def last_galaxy_max_galaxies(self, value: int | None) -> None:
        self._state.last_galaxy_max_galaxies = value

    @property
    def last_galaxy_max_system(self) -> int | None:
        return self._state.last_galaxy_max_system

    @last_galaxy_max_system.setter
    def last_galaxy_max_system(self, value: int | None) -> None:
        self._state.last_galaxy_max_system = value

    @property
    def last_galaxy_nav_method(self) -> str:
        return self._state.last_galaxy_nav_method

    @last_galaxy_nav_method.setter
    def last_galaxy_nav_method(self, value: str) -> None:
        self._state.last_galaxy_nav_method = value

    @property
    def last_galaxy_step_clicks(self) -> int:
        return self._state.last_galaxy_step_clicks

    @last_galaxy_step_clicks.setter
    def last_galaxy_step_clicks(self, value: int) -> None:
        self._state.last_galaxy_step_clicks = value

    @property
    def last_snapshot_observed_active_planet(self) -> bool:
        return self._state.last_snapshot_observed_active_planet

    @last_snapshot_observed_active_planet.setter
    def last_snapshot_observed_active_planet(self, value: bool) -> None:
        self._state.last_snapshot_observed_active_planet = value

    @property
    def last_home_daily_bonus_present(self) -> bool | None:
        return self._state.last_home_daily_bonus_present

    @last_home_daily_bonus_present.setter
    def last_home_daily_bonus_present(self, value: bool | None) -> None:
        self._state.last_home_daily_bonus_present = value

    @property
    def _driver_lock(self) -> threading.RLock:
        return self._state.driver_lock

    @_driver_lock.setter
    def _driver_lock(self, value: threading.RLock) -> None:
        self._state.driver_lock = value

    @property
    def _dom_healthy_epoch(self) -> int:
        return self._state.dom_healthy_epoch

    @_dom_healthy_epoch.setter
    def _dom_healthy_epoch(self, value: int) -> None:
        self._state.dom_healthy_epoch = value

    @classmethod
    def attach(
        cls,
        port: int = DEFAULT_DEBUG_PORT,
        host: str = DEFAULT_DEBUG_HOST,
        timeout: float = 20.0,
        *,
        page_limiter: PageViewLimiter | None = None,
        max_limiter_wait: float | None = None,
    ) -> GameBrowserClient:
        """Attach to an already-open Chrome session inside the game."""
        if not is_debug_port_open(port, host):
            port_s = fmt_compact_int(port) if (isinstance(port, int) and not isinstance(port, bool)) else safe_key(port)
            raise SpaceInvasionAuthError(
                f"No Chrome debug session is listening on {host}:{port_s}. "
                f"Start Chrome with `python3 login.py --debug-port {port_s} --detach`."
            )
        driver = attach_chrome_driver(port=port, timeout=timeout, host=host)
        return cls(
            driver,
            detach_on_close=True,
            timeout=timeout,
            page_limiter=page_limiter,
            max_limiter_wait=max_limiter_wait,
        )

    def __enter__(self) -> GameBrowserClient:
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()

    def close(self) -> None:
        try:
            self._state.driver.switch_to.default_content()
        except SELENIUM_NAV_ERRORS as exc:
            _LOGGER.debug("switch_to.default_content failed during close: %s", exc)
        close_or_detach_driver(self._state.driver, keep_running=self._state.detach_on_close)

    def _pace(self, *, priority: str = "normal", cost: int = 1) -> float:
        return self._state.pace(priority=priority, cost=cost)

    def _check_page_guards(self, html: str) -> None:
        self._guards.check_page_guards(html)

    def _switch_frame(self, frame_id: str) -> None:
        self._frames.switch_frame(frame_id)

    def _mark_frame_navigation(self, frame_id: str) -> str:
        return self._frames.mark_frame_navigation(frame_id)

    def _wait_frame_ready(
        self,
        frame_id: str,
        expected_action: str | None = None,
        *,
        nav_token: str | None = None,
    ) -> str:
        return self._frames.wait_frame_ready(frame_id, expected_action=expected_action, nav_token=nav_token)

    @staticmethod
    def _extract_selected_planet_id(html: str, url: str = "") -> int | None:
        return extract_selected_planet_id(html, url)

    def ensure_in_game(self, *, priority: str = "normal") -> None:
        self._guards.ensure_in_game(priority=priority, refresh_planets_fn=self.read_planets_overview)

    def navigate_menu(self, action: str, *, priority: str = "normal") -> str:
        return self._frames.navigate_menu(action, priority=priority, open_galaxy_fn=self.open_galaxy)

    def open_galaxy_with_coords(
        self,
        *,
        galaxy: int | None = None,
        system: int | None = None,
        priority: str = "interactive",
    ) -> tuple[str, int, int]:
        return self._galaxy.open_galaxy_with_coords(galaxy=galaxy, system=system, priority=priority)

    def open_galaxy(
        self,
        *,
        galaxy: int | None = None,
        system: int | None = None,
        priority: str = "interactive",
    ) -> str:
        return self._galaxy.open_galaxy(galaxy=galaxy, system=system, priority=priority)

    def step_galaxy_system(
        self,
        *,
        galaxy: int,
        system: int,
        pace: bool = False,
        priority: str = "normal",
        max_arrow_distance: int = ARROW_NAV_MAX_DISTANCE,
        on_intermediate_page: Callable[[str, int, int], None] | None = None,
        _allow_full_load: bool = True,
    ) -> tuple[str, int, int]:
        return self._galaxy.step_galaxy_system(
            galaxy=galaxy,
            system=system,
            pace=pace,
            priority=priority,
            max_arrow_distance=max_arrow_distance,
            on_intermediate_page=on_intermediate_page,
            _allow_full_load=_allow_full_load,
        )

    def dispatch_galaxy_spy(
        self,
        *,
        galaxy: int,
        system: int,
        position: int,
        quantity: int = 1,
        is_asteroid: bool = False,
        priority: str = "interactive",
    ) -> dict[str, object]:
        return self._galaxy.dispatch_galaxy_spy(
            galaxy=galaxy,
            system=system,
            position=position,
            quantity=quantity,
            is_asteroid=is_asteroid,
            priority=priority,
        )

    def open_messages(self, msg_type: str = "spio", *, priority: str = "interactive") -> str:
        return self._messages.open_messages(msg_type, priority=priority)

    def open_building_detail(self, building_id: int, *, priority: str = "interactive") -> str:
        return self._frames.open_building_detail(building_id, priority=priority)

    def expand_collapsed_messages_on_page(self, *, max_fetches: int = 12) -> str | None:
        return self._messages.expand_collapsed_messages_on_page(max_fetches=max_fetches)

    def delete_messages_on_current_page(
        self,
        server_msg_ids: Sequence[str] = (),
        *,
        all_on_page_persisted: bool = False,
        priority: str = "normal",
    ) -> tuple[bool, str]:
        return self._messages.delete_messages_on_current_page(
            server_msg_ids,
            all_on_page_persisted=all_on_page_persisted,
            priority=priority,
        )

    def read_planets_overview(self, *, refresh: bool = False, priority: str = "normal") -> GameState:
        return self._reader.read_planets_overview(refresh=refresh, priority=priority)

    def read_menu_status(self) -> tuple[dict[str, int], int | None, int | None]:
        return self._reader.read_menu_status()

    def read_full_snapshot(
        self,
        *,
        include_buildings: bool = True,
        include_research: bool = True,
        include_shipyard: bool = True,
        include_economy: bool = False,
        include_home: bool = True,
        return_home: bool = True,
        priority: str = "normal",
    ) -> GameState:
        return self._reader.read_full_snapshot(
            include_buildings=include_buildings,
            include_research=include_research,
            include_shipyard=include_shipyard,
            include_economy=include_economy,
            include_home=include_home,
            return_home=return_home,
            priority=priority,
            ensure_in_game_fn=self.ensure_in_game,
        )

    def page_source_safe(self) -> str:
        return self._frames.page_source_safe()
