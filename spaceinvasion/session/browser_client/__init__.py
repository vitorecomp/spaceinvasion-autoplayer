"""Decomposed browser client package (`GameBrowserClient` and collaborators)."""

from __future__ import annotations

from spaceinvasion.session.browser_client.client import GameBrowserClient
from spaceinvasion.session.browser_client.frames import FrameNavigator
from spaceinvasion.session.browser_client.galaxy import GalaxyNavigator
from spaceinvasion.session.browser_client.guards import PageGuardChecker
from spaceinvasion.session.browser_client.messages import MessageInboxOps
from spaceinvasion.session.browser_client.reader import StateSnapshotReader
from spaceinvasion.session.limiter import (
    DEFAULT_MAX_VIEWS_PER_MINUTE,
    DEFAULT_MIN_NAV_DELAY,
    DEFAULT_RATE_LIMIT_COOLDOWN_SECONDS,
    DEFAULT_RESERVED_INTERACTIVE_VIEWS,
    PAGE_VIEW_WINDOW_SECONDS,
    PageViewLimiter,
    RateLimitError,
    get_default_page_view_limiter,
    is_server_rate_limit_html,
    reset_default_page_view_limiter,
)

SpaceInvasionClient = GameBrowserClient

__all__ = [
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
    "SpaceInvasionClient",
    "StateSnapshotReader",
    "get_default_page_view_limiter",
    "is_server_rate_limit_html",
    "reset_default_page_view_limiter",
]
