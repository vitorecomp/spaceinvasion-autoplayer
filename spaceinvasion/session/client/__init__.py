"""Decomposed browser client package (`GameBrowserClient` and collaborators)."""

from __future__ import annotations

import time

from spaceinvasion.session.auth import AntiBotChallengeError, SpaceInvasionAuthError
from spaceinvasion.session.browser import find_and_switch_game_tab
from spaceinvasion.session.client.client import GameBrowserClient
from spaceinvasion.session.client.frames import FrameNavigator
from spaceinvasion.session.client.galaxy import GalaxyNavigator
from spaceinvasion.session.client.guards import PageGuardChecker
from spaceinvasion.session.client.messages import MessageInboxOps
from spaceinvasion.session.client.reader import StateSnapshotReader, extract_selected_planet_id
from spaceinvasion.session.limiter import (
    _DEFAULT_PAGE_VIEW_LIMITER,
    DEFAULT_MAX_VIEWS_PER_MINUTE,
    DEFAULT_MIN_NAV_DELAY,
    DEFAULT_RATE_LIMIT_COOLDOWN_SECONDS,
    DEFAULT_RESERVED_INTERACTIVE_VIEWS,
    PAGE_VIEW_WINDOW_SECONDS,
    PageViewLimiter,
    RateLimitError,
    _safe_bounded_float,
    _safe_clamp_cooldown,
    _safe_clamped_int,
    is_server_rate_limit_html,
)

SpaceInvasionClient = GameBrowserClient


def get_default_page_view_limiter() -> PageViewLimiter:
    """Return the active process-wide default `PageViewLimiter` (respecting test patches)."""
    return _DEFAULT_PAGE_VIEW_LIMITER


def reset_default_page_view_limiter() -> None:
    """Reset the active process-wide default `PageViewLimiter`."""
    _DEFAULT_PAGE_VIEW_LIMITER.reset()


def append_crash_debug_log(*_args: object, **_kwargs: object) -> list[object]:
    """Backwards-compatible no-op hook retained for tests asserting no crash-log writes."""
    return []


__all__ = [
    "AntiBotChallengeError",
    "DEFAULT_MAX_VIEWS_PER_MINUTE",
    "DEFAULT_MIN_NAV_DELAY",
    "DEFAULT_RATE_LIMIT_COOLDOWN_SECONDS",
    "DEFAULT_RESERVED_INTERACTIVE_VIEWS",
    "FrameNavigator",
    "GalaxyNavigator",
    "GameBrowserClient",
    "MessageInboxOps",
    "PAGE_VIEW_WINDOW_SECONDS",
    "PageGuardChecker",
    "PageViewLimiter",
    "RateLimitError",
    "SpaceInvasionAuthError",
    "SpaceInvasionClient",
    "StateSnapshotReader",
    "_DEFAULT_PAGE_VIEW_LIMITER",
    "_safe_bounded_float",
    "_safe_clamp_cooldown",
    "_safe_clamped_int",
    "append_crash_debug_log",
    "extract_selected_planet_id",
    "find_and_switch_game_tab",
    "get_default_page_view_limiter",
    "is_server_rate_limit_html",
    "reset_default_page_view_limiter",
    "time",
]
