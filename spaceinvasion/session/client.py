"""Selenium browser client for reading live SpaceInvasion Classic state."""

from __future__ import annotations

from collections import deque
import contextlib
from dataclasses import replace
import inspect
import math
import random
import re
import secrets
import threading
import time
from typing import Any, Callable, Deque, Optional, Sequence
from urllib.parse import urljoin

from bs4 import BeautifulSoup
from selenium.common.exceptions import (
    ElementClickInterceptedException,
    NoSuchElementException,
    StaleElementReferenceException,
    TimeoutException,
)
from selenium.webdriver.common.by import By

from spaceinvasion.core import game_data
from spaceinvasion.session.auth import AntiBotChallengeError, SpaceInvasionAuthError, detect_antibot_challenge
from spaceinvasion.core.logging_utils import append_crash_debug_log
from spaceinvasion.session.browser import (
    DEFAULT_DEBUG_PORT,
    attach_chrome_driver,
    close_or_detach_driver,
    find_and_switch_game_tab,
    is_debug_port_open,
)
from spaceinvasion.combat.fleet import (
    ARROW_NAV_MAX_DISTANCE,
    extract_galaxy_system_coords,
    extract_galaxy_universe_bounds,
)
from spaceinvasion.core.state import (
    GameState,
    QueueJob,
    normalize_planet_type,
    parse_building_queue,
    parse_buildings_page,
    parse_daily_login_bonus,
    parse_economy_rates,
    parse_home_metadata,
    parse_menu_messages,
    parse_menu_status,
    parse_planets_overview_json,
    parse_research_page,
    parse_research_queue_timer,
    parse_ticker_rates,
    parse_tutorial_from_home,
    parse_unit_factory_page,
    parse_unread_banner_count,
)

DEFAULT_MIN_NAV_DELAY: tuple[float, float] = (0.9, 1.8)
DEFAULT_MAX_VIEWS_PER_MINUTE: int = 24
DEFAULT_RESERVED_INTERACTIVE_VIEWS: int = 6
DEFAULT_RATE_LIMIT_COOLDOWN_SECONDS: float = 45.0
PAGE_VIEW_WINDOW_SECONDS: float = 60.0

_SID_TOKEN_RE = re.compile(r"\bsid=([0-9a-zA-Z_-]+)")


def _safe_clamp_cooldown(val: Optional[float], default: float) -> float:
    base = float(default)
    if not math.isfinite(base) or base <= 0.0:
        base = 45.0
    base = max(1.0, min(3600.0, base))
    if val is None or isinstance(val, (bool, dict, list, tuple, set, frozenset)):
        return base
    if isinstance(val, (bytes, bytearray)):
        val = bytes(val).decode("utf-8", errors="replace")  # type: ignore[assignment]
    if isinstance(val, str):
        s = val.strip()
        if not s or not s.isascii():
            return base
        val = s  # type: ignore[assignment]
    try:
        parsed = float(val)
    except (TypeError, ValueError, OverflowError):
        return base
    if not math.isfinite(parsed):
        return base
    return max(1.0, min(3600.0, parsed))


def _safe_bounded_float(
    val: Optional[float],
    default: float,
    *,
    min_val: float = 0.05,
    max_val: float = 300.0,
) -> float:
    base = float(default)
    if not math.isfinite(base):
        base = min_val
    base = max(min_val, min(max_val, base))
    if val is None or isinstance(val, (bool, dict, list, tuple, set, frozenset)):
        return base
    if isinstance(val, (bytes, bytearray)):
        val = bytes(val).decode("utf-8", errors="replace")  # type: ignore[assignment]
    if isinstance(val, str):
        s = val.strip()
        if not s or not s.isascii():
            return base
        val = s  # type: ignore[assignment]
    try:
        parsed = float(val)
    except (TypeError, ValueError, OverflowError):
        return base
    if not math.isfinite(parsed):
        return base
    return max(min_val, min(max_val, parsed))


def _safe_positive_timeout(
    val: Optional[float],
    default: float,
    *,
    min_val: float = 0.05,
    max_val: float = 300.0,
) -> float:
    base = _safe_bounded_float(default, min_val, min_val=min_val, max_val=max_val)
    if val is None or isinstance(val, (bool, dict, list, tuple, set, frozenset)):
        return base
    if isinstance(val, (bytes, bytearray)):
        val = bytes(val).decode("utf-8", errors="replace")  # type: ignore[assignment]
    if isinstance(val, str):
        s = val.strip()
        if not s or not s.isascii():
            return base
        val = s  # type: ignore[assignment]
    try:
        parsed = float(val)
    except (TypeError, ValueError, OverflowError):
        return base
    if not math.isfinite(parsed) or parsed <= 0.0:
        return base
    return max(min_val, min(max_val, parsed))


def _safe_clamped_int(
    val: Any,
    default: int,
    *,
    min_val: int,
    max_val: int,
) -> int:
    safe_def = max(min_val, min(max_val, default))
    if val is None or isinstance(val, (bool, dict, list, tuple, set, frozenset)):
        return safe_def
    if isinstance(val, int):
        return max(min_val, min(max_val, val))
    if isinstance(val, float):
        if not math.isfinite(val):
            return safe_def
        return max(min_val, min(max_val, int(val)))
    if isinstance(val, (bytes, bytearray)):
        val = bytes(val).decode("utf-8", errors="replace")
    if isinstance(val, str):
        s = val.strip()
        if not s or not s.isascii():
            return safe_def
        try:
            return max(min_val, min(max_val, int(s)))
        except (ValueError, OverflowError):
            try:
                parsed = float(s)
            except (TypeError, ValueError, OverflowError):
                return safe_def
            if not math.isfinite(parsed):
                return safe_def
            return max(min_val, min(max_val, int(parsed)))
    return safe_def


class RateLimitError(SpaceInvasionAuthError):
    """Raised when SpaceInvasion's per-minute page-view throttle or local budget triggers."""

    def __init__(
        self,
        message: str = "SpaceInvasion per-minute page-view throttle hit; waiting before next read.",
        *,
        retry_after: float = DEFAULT_RATE_LIMIT_COOLDOWN_SECONDS,
        server_throttle: bool = True,
    ) -> None:
        super().__init__(message)
        self.retry_after = _safe_clamp_cooldown(retry_after, DEFAULT_RATE_LIMIT_COOLDOWN_SECONDS)
        self.server_throttle = bool(server_throttle)


class PageViewLimiter:
    """Thread-safe process-wide page-view pacer, rolling 60s budget, and cooldown gate."""

    def __init__(
        self,
        *,
        min_delay_range: tuple[float, float] = DEFAULT_MIN_NAV_DELAY,
        max_views_per_minute: int = DEFAULT_MAX_VIEWS_PER_MINUTE,
        reserved_interactive_views: int = DEFAULT_RESERVED_INTERACTIVE_VIEWS,
        interactive_reserve: Optional[int] = None,
        default_cooldown_seconds: float = DEFAULT_RATE_LIMIT_COOLDOWN_SECONDS,
        window_seconds: float = PAGE_VIEW_WINDOW_SECONDS,
        clock: Callable[[], float] = time.monotonic,
        sleeper: Callable[[float], None] = time.sleep,
    ) -> None:
        self.min_delay_range = min_delay_range
        self.max_views_per_minute = _safe_clamped_int(
            max_views_per_minute,
            DEFAULT_MAX_VIEWS_PER_MINUTE,
            min_val=1,
            max_val=100_000,
        )
        reserve = reserved_interactive_views if interactive_reserve is None else interactive_reserve
        self.reserved_interactive_views = _safe_clamped_int(
            reserve,
            DEFAULT_RESERVED_INTERACTIVE_VIEWS,
            min_val=0,
            max_val=100_000,
        )
        self.default_cooldown_seconds = _safe_clamp_cooldown(
            default_cooldown_seconds, DEFAULT_RATE_LIMIT_COOLDOWN_SECONDS
        )
        self.window_seconds = _safe_bounded_float(
            window_seconds, PAGE_VIEW_WINDOW_SECONDS, min_val=1.0, max_val=3600.0
        )
        self._clock = clock
        self._sleeper = sleeper
        self._lock = threading.Lock()
        self._recovery_cond = threading.Condition(self._lock)
        self._views: Deque[float] = deque()
        self._last_view_at: Optional[float] = None
        self._rate_limit_until: float = 0.0
        self._rate_limit_hits: int = 0
        self._throttle_recovery_pending: bool = False
        self._throttle_recovery_in_flight: bool = False
        self._throttle_recovery_token: int = 0
        self._throttle_recovery_generation: int = 0

    @property
    def last_view_at(self) -> float:
        with self._lock:
            return float(self._last_view_at or 0.0)

    @property
    def rate_limit_hits(self) -> int:
        with self._lock:
            return int(self._rate_limit_hits)

    def _prune_locked(self, now: float) -> None:
        cutoff = now - self.window_seconds
        while self._views and self._views[0] <= cutoff:
            self._views.popleft()

    def _effective_cap(self, priority: str) -> int:
        if str(priority or "normal").strip().lower() == "interactive":
            return self.max_views_per_minute
        reserved = min(self.reserved_interactive_views, max(0, self.max_views_per_minute - 1))
        return max(1, self.max_views_per_minute - reserved)

    def rate_limit_remaining(self) -> float:
        with self._lock:
            return max(0.0, self._rate_limit_until - self._clock())

    def views_last_60s(self) -> int:
        with self._lock:
            self._prune_locked(self._clock())
            return len(self._views)

    def available_budget(self, priority: str = "normal") -> int:
        with self._lock:
            self._prune_locked(self._clock())
            return max(0, self._effective_cap(priority) - len(self._views))

    def record_view(self, count: int = 1) -> None:
        n = _safe_clamped_int(count, 1, min_val=1, max_val=10_000)
        with self._lock:
            now = self._clock()
            self._prune_locked(now)
            for _ in range(n):
                self._views.append(now)
            if self._last_view_at is None or now > self._last_view_at:
                self._last_view_at = now

    def record_rate_limit(self, retry_after: Optional[float] = None) -> float:
        dur = _safe_clamp_cooldown(retry_after, self.default_cooldown_seconds)
        with self._recovery_cond:
            now = self._clock()
            self._rate_limit_until = max(self._rate_limit_until, now + dur)
            self._rate_limit_hits += 1
            self._throttle_recovery_pending = True
            self._throttle_recovery_generation += 1
            self._recovery_cond.notify_all()
            return max(1.0, self._rate_limit_until - now)

    def recovery_generation(self) -> int:
        with self._lock:
            return int(self._throttle_recovery_generation)

    def record_rate_limit_if_generation(
        self,
        expected_generation: int,
        retry_after: Optional[float] = None,
    ) -> tuple[bool, float]:
        """Record a server throttle hit atomically only if recovery generation still matches."""
        dur = _safe_clamp_cooldown(retry_after, self.default_cooldown_seconds)
        with self._recovery_cond:
            now = self._clock()
            cd_rem = max(0.0, self._rate_limit_until - now)
            if self._throttle_recovery_generation != expected_generation or cd_rem > 0.0:
                return (False, cd_rem)
            self._rate_limit_until = max(self._rate_limit_until, now + dur)
            self._rate_limit_hits += 1
            self._throttle_recovery_pending = True
            self._throttle_recovery_generation += 1
            self._recovery_cond.notify_all()
            return (True, max(1.0, self._rate_limit_until - now))

    def consume_throttle_recovery(self) -> bool:
        with self._recovery_cond:
            if not self._throttle_recovery_pending or self._throttle_recovery_in_flight:
                return False
            self._throttle_recovery_pending = False
            self._throttle_recovery_generation += 1
            self._recovery_cond.notify_all()
            return True

    def rearm_throttle_recovery(self) -> None:
        with self._recovery_cond:
            self._throttle_recovery_pending = True
            self._throttle_recovery_generation += 1
            self._recovery_cond.notify_all()

    def clear_throttle_recovery(self) -> None:
        with self._recovery_cond:
            was_pending = self._throttle_recovery_pending
            self._throttle_recovery_pending = False
            if was_pending and not self._throttle_recovery_in_flight:
                self._throttle_recovery_generation += 1
                self._recovery_cond.notify_all()

    def wait_if_throttle_recovery_in_flight(
        self,
        *,
        wait_timeout: Optional[float] = 15.0,
    ) -> str:
        """If a recovery reload is currently in flight, wait until it finishes."""
        deadline = (
            None
            if wait_timeout is None
            else (
                time.monotonic()
                + _safe_bounded_float(wait_timeout, 15.0, min_val=0.0, max_val=300.0)
            )
        )
        with self._recovery_cond:
            if not self._throttle_recovery_in_flight:
                return "idle"
            start_gen = self._throttle_recovery_generation
            while (
                self._throttle_recovery_in_flight
                and self._throttle_recovery_generation == start_gen
            ):
                if self._rate_limit_until > self._clock():
                    return "cooldown"
                if deadline is not None:
                    rem = deadline - time.monotonic()
                    if rem <= 0.0:
                        return "timeout"
                    self._recovery_cond.wait(timeout=rem)
                else:
                    self._recovery_cond.wait()
            if self._rate_limit_until > self._clock():
                return "cooldown"
            return "waited"

    def begin_or_wait_throttle_recovery(
        self,
        *,
        wait_timeout: Optional[float] = 15.0,
    ) -> tuple[str, int]:
        """Claim single-flight post-cooldown recovery or wait for an active recovery."""
        deadline = (
            None
            if wait_timeout is None
            else (
                time.monotonic()
                + _safe_bounded_float(wait_timeout, 15.0, min_val=0.0, max_val=300.0)
            )
        )
        with self._recovery_cond:
            while True:
                if self._rate_limit_until > self._clock():
                    return ("cooldown", self._throttle_recovery_generation)
                if self._throttle_recovery_pending and not self._throttle_recovery_in_flight:
                    self._throttle_recovery_in_flight = True
                    self._throttle_recovery_token += 1
                    return ("leader", self._throttle_recovery_token)
                if not self._throttle_recovery_in_flight:
                    return ("none", self._throttle_recovery_generation)
                start_gen = self._throttle_recovery_generation
                if deadline is not None:
                    rem = deadline - time.monotonic()
                    if rem <= 0.0:
                        return ("timeout", start_gen)
                    self._recovery_cond.wait(timeout=rem)
                else:
                    self._recovery_cond.wait()
                if (
                    not self._throttle_recovery_in_flight
                    or self._throttle_recovery_generation != start_gen
                ):
                    if self._rate_limit_until > self._clock():
                        return ("cooldown", self._throttle_recovery_generation)
                    return ("waited", self._throttle_recovery_generation)

    def finish_throttle_recovery(
        self,
        token: int,
        *,
        recovered: bool,
        rearm: bool = False,
    ) -> None:
        """Complete an in-flight throttle recovery attempt and wake waiting callers."""
        with self._recovery_cond:
            if self._throttle_recovery_token != token:
                return
            self._throttle_recovery_in_flight = False
            self._throttle_recovery_pending = False if recovered else bool(rearm)
            self._throttle_recovery_generation += 1
            self._recovery_cond.notify_all()

    def acquire(
        self,
        cost: int = 1,
        priority: str = "normal",
        *,
        delay_range: Optional[tuple[float, float]] = None,
        max_wait: Optional[float] = None,
        record: bool = True,
    ) -> float:
        n = _safe_clamped_int(cost, 1, min_val=1, max_val=10_000)
        low, high = delay_range if delay_range is not None else self.min_delay_range
        safe_low = _safe_bounded_float(low, 0.0, min_val=0.0, max_val=60.0)
        safe_high = _safe_bounded_float(high, 0.0, min_val=0.0, max_val=60.0)
        if safe_high <= 0.0:
            target_gap = 0.0
        elif safe_low >= safe_high:
            target_gap = safe_low
        else:
            target_gap = random.uniform(safe_low, safe_high)
        safe_max_wait = (
            None
            if max_wait is None
            else _safe_bounded_float(max_wait, 60.0, min_val=0.0, max_val=3600.0)
        )

        waited_total = 0.0
        while True:
            with self._lock:
                now = self._clock()
                self._prune_locked(now)
                cd_rem = max(0.0, self._rate_limit_until - now)
                if cd_rem > 0.0:
                    err = RateLimitError(
                        f"SpaceInvasion rate-limit cooldown active ({cd_rem:.1f}s remaining; retry after {cd_rem:.1f}s).",
                        retry_after=cd_rem,
                        server_throttle=True,
                    )
                    setattr(err, "_recorded_limiter_id", id(self))
                    raise err

                eff_cap = self._effective_cap(priority)
                budget_wait = 0.0
                if len(self._views) + n > eff_cap and self._views:
                    needed = (len(self._views) + n) - eff_cap
                    expire_idx = min(len(self._views) - 1, needed - 1)
                    budget_wait = max(0.0, (self._views[expire_idx] + self.window_seconds) - now)

                gap_wait = (
                    max(0.0, target_gap - (now - self._last_view_at))
                    if self._last_view_at is not None
                    else 0.0
                )
                wait_s = max(budget_wait, gap_wait)

                if wait_s <= 0.0:
                    if record:
                        for _ in range(n):
                            self._views.append(now)
                        self._last_view_at = now
                    return waited_total

                if safe_max_wait is not None and (waited_total + wait_s) > safe_max_wait:
                    win_s = max(1, int(round(self.window_seconds)))
                    raise RateLimitError(
                        f"Page-view budget reached ({len(self._views)}/{eff_cap} in {win_s}s); retry after {wait_s:.1f}s.",
                        retry_after=wait_s,
                        server_throttle=False,
                    )

            self._sleeper(wait_s)
            waited_total += wait_s

    def stats(self) -> dict[str, Any]:
        with self._lock:
            now = self._clock()
            self._prune_locked(now)
            cd_rem = max(0.0, self._rate_limit_until - now)
            return {
                "views_last_60s": len(self._views),
                "max_views_per_minute": self.max_views_per_minute,
                "reserved_interactive_views": self.reserved_interactive_views,
                "rate_limited": cd_rem > 0.0,
                "retry_after": round(cd_rem, 1),
                "rate_limit_hits": self._rate_limit_hits,
            }

    def reset(self) -> None:
        with self._recovery_cond:
            self._views.clear()
            self._last_view_at = None
            self._rate_limit_until = 0.0
            self._rate_limit_hits = 0
            self._throttle_recovery_pending = False
            self._throttle_recovery_in_flight = False
            self._throttle_recovery_token += 1
            self._throttle_recovery_generation += 1
            self._recovery_cond.notify_all()


_DEFAULT_PAGE_VIEW_LIMITER = PageViewLimiter()


def get_default_page_view_limiter() -> PageViewLimiter:
    """Return the process-wide shared `PageViewLimiter`."""
    return _DEFAULT_PAGE_VIEW_LIMITER


def reset_default_page_view_limiter() -> None:
    """Reset the process-wide `PageViewLimiter` (primarily for unit tests)."""
    _DEFAULT_PAGE_VIEW_LIMITER.reset()


def _call_with_optional_priority(
    fn: Callable[..., Any],
    *args: Any,
    priority: str = "normal",
    **kwargs: Any,
) -> Any:
    call_kwargs = dict(kwargs)
    targets: list[Any] = [fn]
    for attr in ("side_effect", "_mock_wraps"):
        wrapped = getattr(fn, attr, None)
        if callable(wrapped) and not isinstance(wrapped, type):
            targets.append(wrapped)
    accepts_priority = True
    for target in targets:
        try:
            sig = inspect.signature(target)
        except (TypeError, ValueError):
            accepts_priority = False
            break
        if "priority" not in sig.parameters and not any(
            p.kind == inspect.Parameter.VAR_KEYWORD for p in sig.parameters.values()
        ):
            accepts_priority = False
            break
    if accepts_priority:
        call_kwargs["priority"] = priority
    return fn(*args, **call_kwargs)


def _normalize_sid_query(raw_sid: str) -> str:
    cleaned = str(raw_sid or "").strip().lstrip("?&")
    if not cleaned:
        return ""
    if cleaned.lower().startswith("sid="):
        token = cleaned[4:].strip()
        return f"sid={token}" if token else ""
    return f"sid={cleaned}"


_EMPTY_PAGEVIEW_MODAL_RE = re.compile(
    r"made\s+more\s+than\s*<span\b[^>]*\bid=[\"']?pageview-amount[\"']?[^>]*>(?:\s|&nbsp;|&#160;|0|-)*</span>\s*page\s+views",
    re.IGNORECASE,
)


def is_server_rate_limit_html(html: str) -> bool:
    """Return `True` only on active server page-view throttle HTML, ignoring empty AJAX modal templates."""
    if not isinstance(html, str) or not html:
        return False
    low = html.lower()
    if "made more than" not in low or "page views" not in low:
        return False
    cleaned = _EMPTY_PAGEVIEW_MODAL_RE.sub("", html).lower()
    return "made more than" in cleaned and "page views" in cleaned


class GameBrowserClient:
    """Read-only / player-like browser client operating inside the 3-iframe game shell."""

    page_limiter: PageViewLimiter = _DEFAULT_PAGE_VIEW_LIMITER
    max_limiter_wait: Optional[float] = None
    last_galaxy_coords: Optional[tuple[int, int]] = None
    last_galaxy_max_galaxies: Optional[int] = None
    last_galaxy_max_system: Optional[int] = None
    last_galaxy_nav_method: str = ""
    last_galaxy_step_clicks: int = 0
    last_snapshot_observed_active_planet: bool = True
    last_home_daily_bonus_present: Optional[bool] = None

    def __init__(
        self,
        driver,
        *,
        detach_on_close: bool = True,
        min_nav_delay: Optional[tuple[float, float]] = None,
        timeout: float = 20.0,
        page_limiter: Optional[PageViewLimiter] = None,
        limiter: Optional[PageViewLimiter] = None,
        max_limiter_wait: Optional[float] = None,
    ) -> None:
        self.driver = driver
        self.detach_on_close = detach_on_close
        self.page_limiter = page_limiter or limiter or get_default_page_view_limiter()
        self._min_nav_delay_override = min_nav_delay
        self.min_nav_delay = (
            min_nav_delay if min_nav_delay is not None else self.page_limiter.min_delay_range
        )
        self.timeout = _safe_positive_timeout(timeout, 20.0, min_val=0.05, max_val=300.0)
        self.max_limiter_wait = (
            None
            if max_limiter_wait is None
            else _safe_bounded_float(max_limiter_wait, 60.0, min_val=0.0, max_val=3600.0)
        )
        self.last_galaxy_coords: Optional[tuple[int, int]] = None
        self.last_galaxy_max_galaxies: Optional[int] = None
        self.last_galaxy_max_system: Optional[int] = None
        self.last_galaxy_nav_method: str = ""
        self.last_galaxy_step_clicks: int = 0
        self.last_snapshot_observed_active_planet: bool = True
        self.last_home_daily_bonus_present: Optional[bool] = None
        self._driver_lock = threading.RLock()
        self._dom_healthy_epoch: int = 0

    @property
    def _last_nav_at(self) -> float:
        return self.page_limiter.last_view_at

    @classmethod
    def attach(
        cls,
        port: int = DEFAULT_DEBUG_PORT,
        host: str = "127.0.0.1",
        timeout: float = 20.0,
        *,
        page_limiter: Optional[PageViewLimiter] = None,
        max_limiter_wait: Optional[float] = None,
    ) -> GameBrowserClient:
        """Attach to an already-open Chrome session inside the game."""
        if not is_debug_port_open(port, host):
            port_s = game_data.fmt_compact_int(port) if (isinstance(port, int) and not isinstance(port, bool)) else game_data.safe_key(port)
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

    def __exit__(self, *_exc) -> None:
        self.close()

    def close(self) -> None:
        try:
            self.driver.switch_to.default_content()
        except Exception:
            pass
        close_or_detach_driver(self.driver, keep_running=self.detach_on_close)

    def _pace(self, *, priority: str = "normal", cost: int = 1) -> float:
        return self.page_limiter.acquire(
            cost=cost,
            priority=priority,
            delay_range=self._min_nav_delay_override,
            max_wait=self.max_limiter_wait,
        )

    def _capture_dom_diag(self, html: str = "") -> dict[str, Any]:
        diag: dict[str, Any] = {
            "page_views_last_60s": self.page_limiter.views_last_60s()
            if hasattr(self.page_limiter, "views_last_60s")
            else None,
            "rate_limit_remaining": self.page_limiter.rate_limit_remaining()
            if hasattr(self.page_limiter, "rate_limit_remaining")
            else None,
        }
        try:
            diag["top_url"] = str(getattr(self.driver, "current_url", "") or "")
        except Exception as exc:
            diag["top_url_err"] = str(exc)
        try:
            handles = getattr(self.driver, "window_handles", None)
            diag["window_handles_count"] = len(handles) if isinstance(handles, (list, tuple)) else None
        except Exception:
            pass
        try:
            diag["frame_href"] = str(self.driver.execute_script("return String(location.href || '');") or "")
        except Exception:
            pass
        if html:
            low = html.lower()
            idx_m = low.find("made more than")
            idx_p = low.find("page views")
            diag["html_len"] = len(html)
            diag["matched_made_more_than"] = html[max(0, idx_m - 160) : idx_m + 320] if idx_m >= 0 else None
            diag["matched_page_views"] = html[max(0, idx_p - 160) : idx_p + 320] if idx_p >= 0 else None
            try:
                soup = BeautifulSoup(html, "html.parser")
                diag["visible_text_preview"] = " ".join(soup.get_text(" ", strip=True).split())[:1000]
            except Exception:
                diag["raw_preview"] = html[:1000]
        return diag

    def _is_antibot_visible_in_live_dom(self) -> Optional[bool]:
        """Read-only visibility check for real Selenium drivers to ignore hidden/dismissed modal residue."""
        if not getattr(self.driver, "__class__", object).__module__.startswith("selenium"):
            return None
        try:
            res = self.driver.execute_script(
                "function isVis(el) {"
                "  if (!el) return false;"
                "  var cur = el;"
                "  while (cur && cur.nodeType === 1) {"
                "    if (cur.hidden || cur.getAttribute('aria-hidden') === 'true') return false;"
                "    var st = window.getComputedStyle(cur);"
                "    if (!st || st.display === 'none' || st.visibility === 'hidden' || Number(st.opacity) === 0) return false;"
                "    cur = cur.parentElement;"
                "  }"
                "  var r = el.getBoundingClientRect();"
                "  return r.width > 0 && r.height > 0;"
                "}"
                "var sels = ['#si-captcha-gate', '.cf-turnstile', '.cf-challenge', '.g-recaptcha', '.h-captcha', 'iframe[src*=\"challenges.cloudflare.com\"]'];"
                "for (var i = 0; i < sels.length; i++) {"
                "  var nodes = document.querySelectorAll(sels[i]);"
                "  for (var j = 0; j < nodes.length; j++) {"
                "    if (isVis(nodes[j])) return true;"
                "  }"
                "}"
                "var t = (document.title || '').toLowerCase();"
                "if (t.indexOf('just a moment') !== -1 || t.indexOf('attention required!') !== -1) return true;"
                "var bodyTxt = (document.body && document.body.innerText ? document.body.innerText : '').toLowerCase();"
                "if (bodyTxt.indexOf('sicherheitsprüfung') !== -1 || bodyTxt.indexOf('sicherheitspruefung') !== -1 || bodyTxt.indexOf('confirm that you are not a bot') !== -1 || bodyTxt.indexOf('dass du kein bot bist') !== -1) return true;"
                "return false;"
            )
            return res if isinstance(res, bool) else None
        except Exception:
            return None

    def _check_page_guards(self, html: str) -> None:
        issue = detect_antibot_challenge(200, html)
        if issue and self._is_antibot_visible_in_live_dom() is False:
            issue = None
        if issue:
            err_ab = AntiBotChallengeError(
                f"{issue}. Stopping automation; handle verification in Chrome."
            )
            if self.detach_on_close and getattr(self.driver, "__class__", object).__module__.startswith("selenium"):
                append_crash_debug_log(
                    component="client._check_page_guards",
                    event="antibot_challenge",
                    error=err_ab,
                    details=self._capture_dom_diag(html),
                )
            raise err_ab
        if is_server_rate_limit_html(html):
            cooldown = self.page_limiter.record_rate_limit()
            err = RateLimitError(
                f"SpaceInvasion per-minute page-view throttle hit; cooldown active ({cooldown:.1f}s remaining; retry after {cooldown:.1f}s).",
                retry_after=cooldown,
                server_throttle=True,
            )
            setattr(err, "_recorded_limiter_id", id(self.page_limiter))
            if self.detach_on_close and getattr(self.driver, "__class__", object).__module__.startswith("selenium"):
                append_crash_debug_log(
                    component="client._check_page_guards",
                    event="rate_limit_guard_triggered",
                    error=err,
                    details=self._capture_dom_diag(html),
                )
            raise err

    def _supports_coordinated_recovery(self) -> bool:
        limiter = self.page_limiter
        return (
            isinstance(limiter, PageViewLimiter)
            and callable(getattr(limiter, "wait_if_throttle_recovery_in_flight", None))
            and callable(getattr(limiter, "begin_or_wait_throttle_recovery", None))
            and callable(getattr(limiter, "finish_throttle_recovery", None))
            and not hasattr(limiter, "_mock_return_value")
        )

    def _dom_recovered_before_record(self, dom_epoch_seen: int) -> bool:
        """Re-check under `_driver_lock` (without holding limiter lock) if DOM recovered."""
        drv_lock = getattr(self, "_driver_lock", None)
        with (drv_lock if drv_lock is not None else contextlib.nullcontext()):
            if getattr(self, "_dom_healthy_epoch", 0) != dom_epoch_seen:
                return True
            try:
                self.driver.switch_to.default_content()
                f_planets = self.driver.find_element(By.ID, "F_planets")
                self.driver.find_element(By.ID, "F_Hauptframe")
                self.driver.find_element(By.ID, "F_Navi")
                self.driver.switch_to.frame(f_planets)
                self.driver.find_element(By.ID, "json-container")
                self._dom_healthy_epoch = getattr(self, "_dom_healthy_epoch", 0) + 1
                return True
            except NoSuchElementException:
                return False
            except StaleElementReferenceException:
                return True
            finally:
                try:
                    self.driver.switch_to.default_content()
                except Exception:
                    pass

    def ensure_in_game(self, *, priority: str = "normal") -> None:
        """Verify the attached browser has an active `indexGame.es` tab with game frames."""
        wait_budget = _safe_positive_timeout(
            getattr(self, "timeout", 20.0), 20.0, min_val=0.05, max_val=300.0
        )
        deadline = time.monotonic() + wait_budget
        drv_lock = getattr(self, "_driver_lock", None)
        renderer_recoveries = 0

        for _attempt in range(64):
            coordinated = self._supports_coordinated_recovery()
            if coordinated:
                pre_status = self.page_limiter.wait_if_throttle_recovery_in_flight(
                    wait_timeout=max(0.0, deadline - time.monotonic()),
                )
                if pre_status == "cooldown":
                    cd_now = max(1.0, self.page_limiter.rate_limit_remaining())
                    err = RateLimitError(
                        f"SpaceInvasion rate-limit cooldown active ({cd_now:.1f}s remaining; retry after {cd_now:.1f}s).",
                        retry_after=cd_now,
                        server_throttle=True,
                    )
                    setattr(err, "_recorded_limiter_id", id(self.page_limiter))
                    raise err
                if pre_status == "timeout":
                    raise RateLimitError(
                        "Timed out waiting for concurrent F_planets throttle recovery; retry shortly.",
                        retry_after=1.0,
                        server_throttle=False,
                    )
                gen_before = self.page_limiter.recovery_generation()
            else:
                gen_before = 0

            in_planets_frame = False
            frame_html = ""
            top_shell_html = ""
            missing_exc: Optional[NoSuchElementException] = None
            dom_epoch_seen = getattr(self, "_dom_healthy_epoch", 0)
            renderer_timed_out = False
            with (drv_lock if drv_lock is not None else contextlib.nullcontext()):
                try:
                    self.driver.switch_to.default_content()
                    try:
                        top_src = getattr(self.driver, "page_source", "")
                        top_shell_html = top_src if isinstance(top_src, str) else ""
                    except Exception as src_exc:
                        if self._is_renderer_timeout(src_exc):
                            raise
                        top_shell_html = ""
                    try:
                        f_planets = self.driver.find_element(By.ID, "F_planets")
                        self.driver.find_element(By.ID, "F_Hauptframe")
                        self.driver.find_element(By.ID, "F_Navi")
                        self.driver.switch_to.frame(f_planets)
                        in_planets_frame = True
                        self.driver.find_element(By.ID, "json-container")
                        self.driver.switch_to.default_content()
                        self._dom_healthy_epoch = getattr(self, "_dom_healthy_epoch", 0) + 1
                    except NoSuchElementException as exc:
                        missing_exc = exc
                        dom_epoch_seen = getattr(self, "_dom_healthy_epoch", 0)
                        try:
                            frame_html = str(getattr(self.driver, "page_source", "") or "")
                        except Exception:
                            frame_html = ""
                except Exception as drv_exc:
                    if self._is_renderer_timeout(drv_exc):
                        if renderer_recoveries < 1:
                            renderer_recoveries += 1
                            self._recover_renderer_timeout()
                            renderer_timed_out = True
                        else:
                            raise SpaceInvasionAuthError(
                                "Renderer timed out while verifying game session."
                            ) from drv_exc
                    else:
                        raise
            if renderer_timed_out:
                if time.monotonic() >= deadline:
                    deadline = time.monotonic() + min(5.0, wait_budget)
                continue

            if top_shell_html:
                top_issue = detect_antibot_challenge(200, top_shell_html)
                if top_issue and missing_exc is None:
                    with (drv_lock if drv_lock is not None else contextlib.nullcontext()):
                        self.driver.switch_to.default_content()
                        if self._is_antibot_visible_in_live_dom() is False:
                            top_issue = None
                if top_issue:
                    err_top_ab = AntiBotChallengeError(
                        f"{top_issue}. Stopping automation; handle verification in Chrome."
                    )
                    if self.detach_on_close and getattr(self.driver, "__class__", object).__module__.startswith("selenium"):
                        append_crash_debug_log(
                            component="client.ensure_in_game",
                            event="antibot_challenge",
                            error=err_top_ab,
                            details=self._capture_dom_diag(top_shell_html),
                        )
                    raise err_top_ab

            if missing_exc is None:
                self.page_limiter.clear_throttle_recovery()
                return

            # If a popup window (e.g. `p_galaxy()` `"Galaxy"` window) stole focus, switch back to `indexGame.es`
            if not in_planets_frame:
                switched_tab = False
                with (drv_lock if drv_lock is not None else contextlib.nullcontext()):
                    try:
                        handles = list(getattr(self.driver, "window_handles", None) or [])
                        if len(handles) > 1 and find_and_switch_game_tab(self.driver):
                            switched_tab = True
                    except Exception:
                        switched_tab = False
                if switched_tab:
                    self.page_limiter.clear_throttle_recovery()
                    return

            if frame_html:
                issue = detect_antibot_challenge(200, frame_html)
                if issue:
                    raise AntiBotChallengeError(
                        f"{issue}. Stopping automation; handle verification in Chrome."
                    ) from missing_exc
                if is_server_rate_limit_html(frame_html):
                    if coordinated:
                        with (drv_lock if drv_lock is not None else contextlib.nullcontext()):
                            cur_dom_epoch = getattr(self, "_dom_healthy_epoch", 0)
                        if cur_dom_epoch != dom_epoch_seen:
                            continue
                    cd_rem = self.page_limiter.rate_limit_remaining()
                    if cd_rem > 0.0:
                        err = RateLimitError(
                            f"SpaceInvasion rate-limit cooldown active ({cd_rem:.1f}s remaining; retry after {cd_rem:.1f}s).",
                            retry_after=cd_rem,
                            server_throttle=True,
                        )
                        setattr(err, "_recorded_limiter_id", id(self.page_limiter))
                        raise err from missing_exc
                    if in_planets_frame:
                        if coordinated:
                            status, token_or_gen = self.page_limiter.begin_or_wait_throttle_recovery(
                                wait_timeout=max(0.0, deadline - time.monotonic()),
                            )
                            if status == "cooldown":
                                cd_now = max(1.0, self.page_limiter.rate_limit_remaining())
                                err = RateLimitError(
                                    f"SpaceInvasion rate-limit cooldown active ({cd_now:.1f}s remaining; retry after {cd_now:.1f}s).",
                                    retry_after=cd_now,
                                    server_throttle=True,
                                )
                                setattr(err, "_recorded_limiter_id", id(self.page_limiter))
                                raise err from missing_exc
                            if status == "waited":
                                continue
                            if status == "timeout":
                                raise RateLimitError(
                                    "Timed out waiting for concurrent F_planets throttle recovery; retry shortly.",
                                    retry_after=1.0,
                                    server_throttle=False,
                                ) from missing_exc
                            if status == "leader":
                                recovered = False
                                rearm = True
                                try:
                                    _call_with_optional_priority(
                                        self.read_planets_overview,
                                        refresh=True,
                                        priority=priority,
                                    )
                                    with (drv_lock if drv_lock is not None else contextlib.nullcontext()):
                                        self.driver.switch_to.default_content()
                                    recovered = True
                                    rearm = False
                                    return
                                except RateLimitError:
                                    rearm = True
                                    raise
                                except (AntiBotChallengeError, SpaceInvasionAuthError):
                                    rearm = False
                                    raise
                                except NoSuchElementException:
                                    rearm = False
                                    with (drv_lock if drv_lock is not None else contextlib.nullcontext()):
                                        try:
                                            frame_html = str(getattr(self.driver, "page_source", "") or "")
                                        except Exception:
                                            frame_html = ""
                                except Exception:
                                    rearm = True
                                    raise
                                finally:
                                    self.page_limiter.finish_throttle_recovery(
                                        token_or_gen,
                                        recovered=recovered,
                                        rearm=rearm,
                                    )
                            if status == "none":
                                if token_or_gen != gen_before:
                                    continue
                                if self._dom_recovered_before_record(dom_epoch_seen):
                                    continue
                                recorded, cooldown = self.page_limiter.record_rate_limit_if_generation(
                                    gen_before
                                )
                                if not recorded and cooldown <= 0.0:
                                    continue
                                cd_val = max(1.0, cooldown)
                                err = RateLimitError(
                                    f"SpaceInvasion per-minute page-view throttle hit; cooldown active ({cd_val:.1f}s remaining; retry after {cd_val:.1f}s).",
                                    retry_after=cd_val,
                                    server_throttle=True,
                                )
                                setattr(err, "_recorded_limiter_id", id(self.page_limiter))
                                raise err from missing_exc
                        elif self.page_limiter.consume_throttle_recovery():
                            try:
                                _call_with_optional_priority(
                                    self.read_planets_overview,
                                    refresh=True,
                                    priority=priority,
                                )
                                with (drv_lock if drv_lock is not None else contextlib.nullcontext()):
                                    self.driver.switch_to.default_content()
                                return
                            except RateLimitError:
                                self.page_limiter.rearm_throttle_recovery()
                                raise
                            except NoSuchElementException:
                                with (drv_lock if drv_lock is not None else contextlib.nullcontext()):
                                    try:
                                        frame_html = str(getattr(self.driver, "page_source", "") or "")
                                    except Exception:
                                        frame_html = ""
                    elif coordinated:
                        if self._dom_recovered_before_record(dom_epoch_seen):
                            continue
                        recorded, cooldown = self.page_limiter.record_rate_limit_if_generation(
                            gen_before
                        )
                        if not recorded and cooldown <= 0.0:
                            continue
                        cd_val = max(1.0, cooldown)
                        err = RateLimitError(
                            f"SpaceInvasion per-minute page-view throttle hit; cooldown active ({cd_val:.1f}s remaining; retry after {cd_val:.1f}s).",
                            retry_after=cd_val,
                            server_throttle=True,
                        )
                        setattr(err, "_recorded_limiter_id", id(self.page_limiter))
                        raise err from missing_exc
                if frame_html:
                    self._check_page_guards(frame_html)

            tab_recovered = False
            top_html = ""
            cur_url = ""
            with (drv_lock if drv_lock is not None else contextlib.nullcontext()):
                try:
                    self.driver.switch_to.default_content()
                except Exception:
                    pass
                tab_recovered = bool(find_and_switch_game_tab(self.driver))
                if tab_recovered:
                    self._dom_healthy_epoch = getattr(self, "_dom_healthy_epoch", 0) + 1
                else:
                    try:
                        top_html = str(getattr(self.driver, "page_source", "") or "")
                    except Exception:
                        top_html = ""
                    cur_url = str(getattr(self.driver, "current_url", "") or "")
            if tab_recovered:
                self.page_limiter.clear_throttle_recovery()
                return
            if top_html:
                self._check_page_guards(top_html)
            raise SpaceInvasionAuthError(
                f"Browser is not inside SpaceInvasion game shell (current URL: {cur_url}). "
                "Run `python3 login.py --attach` to enter the configured universe first."
            ) from missing_exc

        raise RateLimitError(
            "Timed out waiting for concurrent F_planets throttle recovery; retry shortly.",
            retry_after=1.0,
            server_throttle=False,
        )

    def _switch_frame(self, frame_id: str) -> None:
        drv_lock = getattr(self, "_driver_lock", None)
        with (drv_lock if drv_lock is not None else contextlib.nullcontext()):
            self.driver.switch_to.default_content()
            frame = self.driver.find_element(By.ID, frame_id)
            self.driver.switch_to.frame(frame)

    def _mark_frame_navigation(self, frame_id: str) -> str:
        token = secrets.token_hex(6)
        self._switch_frame(frame_id)
        try:
            self.driver.execute_script("window.__siNavToken = arguments[0];", token)
        except Exception:
            pass
        return token

    @staticmethod
    def _is_renderer_timeout(exc: BaseException) -> bool:
        """Return True when `exc` is a ChromeDriver/Selenium renderer or script timeout."""
        if isinstance(exc, TimeoutException):
            return True
        low = str(exc or "").lower()
        return (
            "timed out receiving message from renderer" in low
            or "timeout: timed out" in low
            or "script timeout" in low
        )

    def _recover_renderer_timeout(self) -> None:
        """Abort stuck subresource/page loads (`window.stop()`) to unblock ChromeDriver's renderer queue."""
        restore_timeout = _safe_positive_timeout(
            getattr(self, "timeout", 20.0), 20.0, min_val=5.0, max_val=300.0
        )
        set_script = getattr(self.driver, "set_script_timeout", None)
        if callable(set_script):
            try:
                set_script(3.0)
            except Exception:
                pass
        try:
            try:
                self.driver.execute_script("try { window.stop(); } catch (e) {}")
            except Exception:
                pass
            try:
                self.driver.switch_to.default_content()
                self.driver.execute_script("try { window.stop(); } catch (e) {}")
            except Exception:
                pass
        finally:
            if callable(set_script):
                try:
                    set_script(restore_timeout)
                except Exception:
                    pass
        time.sleep(0.25)

    @staticmethod
    def _frame_matches_expected_action(
        expected_action: Optional[str],
        href: str,
        html: str = "",
    ) -> bool:
        if not expected_action:
            return True
        if expected_action in href:
            return True
        href_l = href.lower()
        if expected_action == "internalSendFleet" and "fleeterr." in href_l:
            return True
        if (
            expected_action == "internalTransmitter"
            and "indexinternal.es" in href_l
            and (
                'id="tm.form"' in html
                or 'value="internalTransmitter"' in html
                or "action=internalTransmitter" in html
            )
        ):
            return True
        return False

    def _try_read_landed_frame(
        self,
        frame_id: str,
        expected_action: Optional[str],
        *,
        nav_token: Optional[str] = None,
    ) -> Optional[str]:
        """After `window.stop()`, return validated frame HTML if `expected_action` already landed."""
        try:
            self._switch_frame(frame_id)
            probe = self.driver.execute_script(
                "return {"
                "ready: String(document.readyState || ''), "
                "href: String(location.href || ''), "
                "token: (typeof window.__siNavToken === 'string') ? window.__siNavToken : null"
                "};"
            )
            if isinstance(probe, dict):
                ready = str(probe.get("ready") or "")
                href = str(probe.get("href") or "")
                cur_token = probe.get("token")
            else:
                href = str(self.driver.execute_script("return String(location.href || '');") or "")
                ready = str(self.driver.execute_script("return String(document.readyState || '');") or "")
                cur_token = None
            if ready not in ("interactive", "complete"):
                return None
            if nav_token is not None and cur_token == nav_token:
                return None
            html = self.driver.page_source or ""
            if len(html) < 64:
                return None
            if not self._frame_matches_expected_action(expected_action, href, html):
                return None
        except Exception:
            return None
        self._check_page_guards(html)
        html_low = html.lower()
        if "bitmeup-login" in html_low or "sip-tab-universe" in html_low:
            raise SpaceInvasionAuthError(
                "Browser redirected to BitMeUp portal/login while waiting for game frame."
            )
        return html

    def _wait_frame_ready(
        self,
        frame_id: str,
        expected_action: Optional[str] = None,
        *,
        nav_token: Optional[str] = None,
    ) -> str:
        wait_budget = _safe_positive_timeout(
            getattr(self, "timeout", 20.0), 20.0, min_val=0.05, max_val=300.0
        )
        deadline = time.monotonic() + wait_budget
        recovered_timeout = False
        while True:
            try:
                self._switch_frame(frame_id)
                probe = None
                try:
                    probe = self.driver.execute_script(
                        "return {"
                        "ready: document.readyState, "
                        "href: String(location.href || ''), "
                        "token: (typeof window.__siNavToken === 'string') ? window.__siNavToken : null"
                        "};"
                    )
                except Exception as probe_exc:
                    if self._is_renderer_timeout(probe_exc):
                        raise
                    probe = None
                if isinstance(probe, dict):
                    ready = probe.get("ready")
                    href = str(probe.get("href") or "")
                    cur_token = probe.get("token")
                else:
                    ready = self.driver.execute_script("return document.readyState;")
                    href = str(self.driver.execute_script("return location.href;") or "")
                    cur_token = None

                if (
                    ready == "complete" or (recovered_timeout and ready == "interactive")
                ) and (nav_token is None or cur_token != nav_token):
                    html = self.driver.page_source or ""
                    self._check_page_guards(html)
                    html_low = html.lower()
                    if "bitmeup-login" in html_low or "sip-tab-universe" in html_low:
                        raise SpaceInvasionAuthError(
                            "Browser redirected to BitMeUp portal/login while waiting for game frame."
                        )
                    if self._frame_matches_expected_action(expected_action, href, html):
                        return html
            except Exception as wait_exc:
                if not self._is_renderer_timeout(wait_exc):
                    raise
                if not recovered_timeout:
                    recovered_timeout = True
                    self._recover_renderer_timeout()
                    landed_html = self._try_read_landed_frame(
                        frame_id,
                        expected_action,
                        nav_token=nav_token,
                    )
                    if landed_html is not None:
                        return landed_html
                    if time.monotonic() >= deadline:
                        deadline = time.monotonic() + min(5.0, wait_budget)
                    continue
                raise SpaceInvasionAuthError(
                    f"Renderer timed out waiting for frame {frame_id} ({expected_action or 'ready'})."
                ) from wait_exc

            if time.monotonic() >= deadline:
                break
            time.sleep(0.2)
        raise SpaceInvasionAuthError(
            f"Timed out waiting for frame {frame_id} ({expected_action or 'ready'})."
        )

    def _resolve_sid_query(self) -> str:
        self._switch_frame("F_Navi")
        try:
            raw_sid = self.driver.execute_script(
                "return (window.SI_TPL && SI_TPL.menu && SI_TPL.menu.sid) ? SI_TPL.menu.sid : '';"
            )
        except Exception:
            raw_sid = ""
        if isinstance(raw_sid, str):
            norm = _normalize_sid_query(raw_sid)
            if norm:
                return norm

        try:
            for link in self.driver.find_elements(By.CSS_SELECTOR, 'a[href*="sid="]'):
                href = str(link.get_attribute("href") or "")
                m = _SID_TOKEN_RE.search(href)
                if m:
                    return f"sid={m.group(1)}"
        except Exception:
            pass

        try:
            nav_href = str(self.driver.execute_script("return String(location.href || '');") or "")
            m_nav = _SID_TOKEN_RE.search(nav_href)
            if m_nav:
                return f"sid={m_nav.group(1)}"
        except Exception:
            pass

        try:
            top_url = str(getattr(self.driver, "current_url", "") or "")
            m_top = _SID_TOKEN_RE.search(top_url)
            if m_top:
                return f"sid={m_top.group(1)}"
        except Exception:
            pass

        try:
            self._switch_frame("F_Hauptframe")
            hf_sid = self.driver.execute_script(
                "if (window.sid) return String(window.sid);"
                "if (window.SI_TPL && SI_TPL.galaxyMap && SI_TPL.galaxyMap.sid) return String(SI_TPL.galaxyMap.sid);"
                "var a = document.querySelector('a[href*=\"sid=\"]');"
                "if (a && a.href) return String(a.href);"
                "var oc = document.querySelector('[onclick*=\"sid=\"]');"
                "if (oc) return String(oc.getAttribute('onclick') || '');"
                "return '';"
            )
            if isinstance(hf_sid, str) and hf_sid.strip():
                hf_clean = hf_sid.strip().lstrip("?&")
                m_hf = _SID_TOKEN_RE.search(hf_clean)
                if m_hf:
                    return f"sid={m_hf.group(1)}"
                if re.fullmatch(r"(?:sid=)?[0-9a-zA-Z_-]+", hf_clean, flags=re.IGNORECASE):
                    norm_hf = _normalize_sid_query(hf_clean)
                    if norm_hf:
                        return norm_hf
        except Exception:
            pass

        return ""

    def _build_internal_url(self, query: str) -> str:
        base_href = ""
        for frame_id in ("F_Hauptframe", "F_Navi"):
            try:
                self._switch_frame(frame_id)
                href = str(self.driver.execute_script("return String(location.href || '');") or "")
                if href.startswith(("http://", "https://")):
                    base_href = href
                    break
            except Exception:
                continue
        if not base_href:
            try:
                top_url = str(getattr(self.driver, "current_url", "") or "")
                if top_url.startswith(("http://", "https://")):
                    base_href = top_url
            except Exception:
                pass
        if not base_href:
            base_href = "https://spaceinvasion.bitmeup.com/indexInternal.es"
        return urljoin(base_href, f"indexInternal.es?{query}")

    def _open_frame_url_with_retry(
        self,
        target_url: str,
        *,
        expected_action: str,
        frame_id: str = "F_Hauptframe",
    ) -> str:
        """Navigate `frame_id` to `target_url` via `location.href`, recovering once on renderer timeout."""
        for attempt in range(2):
            nav_token: Optional[str] = None
            try:
                nav_token = self._mark_frame_navigation(frame_id)
                self._switch_frame(frame_id)
                self.driver.execute_script("location.href = arguments[0];", target_url)
                return self._wait_frame_ready(frame_id, expected_action=expected_action, nav_token=nav_token)
            except Exception as nav_exc:
                if not self._is_renderer_timeout(nav_exc):
                    raise
                if attempt >= 1:
                    raise SpaceInvasionAuthError(
                        f"Renderer timed out loading {expected_action}."
                    ) from nav_exc
                self._recover_renderer_timeout()
                landed_html = self._try_read_landed_frame(
                    frame_id,
                    expected_action,
                    nav_token=nav_token,
                )
                if landed_html is not None:
                    return landed_html
        raise SpaceInvasionAuthError(f"Renderer timed out loading {expected_action}.")

    def navigate_menu(self, action: str, *, priority: str = "normal") -> str:
        """Click a navigation link in `F_Navi` and wait for `F_Hauptframe` to load."""
        if action == "internalGalaxy":
            return self.open_galaxy(priority=priority)
        for attempt in range(2):
            self._pace(priority=priority)
            nav_token: Optional[str] = None
            try:
                nav_token = self._mark_frame_navigation("F_Hauptframe")
                self._switch_frame("F_Navi")
                link = self.driver.find_element(By.CSS_SELECTOR, f'a[href*="action={action}"]')
                self.driver.execute_script("arguments[0].scrollIntoView({block: 'center'});", link)
                try:
                    link.click()
                except ElementClickInterceptedException:
                    self.driver.execute_script("arguments[0].click();", link)
                return self._wait_frame_ready("F_Hauptframe", expected_action=action, nav_token=nav_token)
            except Exception as nav_exc:
                if not self._is_renderer_timeout(nav_exc):
                    raise
                if attempt >= 1:
                    raise SpaceInvasionAuthError(
                        f"Renderer timed out navigating to {action}."
                    ) from nav_exc
                self._recover_renderer_timeout()
                landed_html = self._try_read_landed_frame(
                    "F_Hauptframe",
                    action,
                    nav_token=nav_token,
                )
                if landed_html is not None:
                    return landed_html
        raise SpaceInvasionAuthError(f"Renderer timed out navigating to {action}.")

    def _submit_galaxy_coords_form(
        self,
        *,
        galaxy: Optional[int],
        system: Optional[int],
        priority: str,
    ) -> Optional[str]:
        self._switch_frame("F_Hauptframe")
        gal_inp = None
        sys_inp = None
        for name in ("galaxy", "gal", "g"):
            try:
                els = self.driver.find_elements(By.NAME, name)
                if els:
                    gal_inp = els[0]
                    break
            except Exception:
                pass
        for name in ("system", "sun", "s"):
            try:
                els = self.driver.find_elements(By.NAME, name)
                if els:
                    sys_inp = els[0]
                    break
            except Exception:
                pass
        if gal_inp is None and sys_inp is None:
            return None

        self._pace(priority=priority)
        nav_token = self._mark_frame_navigation("F_Hauptframe")
        if galaxy is not None and gal_inp is not None:
            gal_inp.clear()
            gal_inp.send_keys(str(game_data.cap_json_int(max(1, game_data.safe_int(galaxy, 1)))))
        if system is not None and sys_inp is not None:
            sys_inp.clear()
            sys_inp.send_keys(str(game_data.cap_json_int(max(1, game_data.safe_int(system, 1)))))

        target_form = None
        for anchor_inp in (gal_inp, sys_inp):
            if anchor_inp is None:
                continue
            find_el_fn = getattr(anchor_inp, "find_element", None)
            if callable(find_el_fn):
                try:
                    target_form = find_el_fn(By.XPATH, "ancestor::form[1]")
                    if target_form is not None:
                        break
                except Exception:
                    pass
            try:
                candidate = self.driver.execute_script(
                    "return arguments[0] && arguments[0].form ? arguments[0].form : null;",
                    anchor_inp,
                )
                if candidate is not None:
                    target_form = candidate
                    break
            except Exception:
                pass

        submit_btn = None
        if target_form is not None and callable(getattr(target_form, "find_elements", None)):
            for css in ('input[type="submit"]', 'button[type="submit"]'):
                try:
                    els = target_form.find_elements(By.CSS_SELECTOR, css)
                    if els:
                        submit_btn = els[0]
                        break
                except Exception:
                    pass
        if submit_btn is None and target_form is None:
            for css in (
                'form:has(input[name="galaxy"]) input[type="submit"]',
                'form:has(input[name="galaxy"]) button[type="submit"]',
                'form:has(input[name="gal"]) input[type="submit"]',
                'form:has(input[name="gal"]) button[type="submit"]',
                'form:has(input[name="g"]) input[type="submit"]',
                'form:has(input[name="g"]) button[type="submit"]',
                'form:has(input[name="system"]) input[type="submit"]',
                'form:has(input[name="system"]) button[type="submit"]',
                'form:has(input[name="sun"]) input[type="submit"]',
                'form:has(input[name="sun"]) button[type="submit"]',
                'form:has(input[name="s"]) input[type="submit"]',
                'form:has(input[name="s"]) button[type="submit"]',
                'form input[type="submit"]',
                'form button[type="submit"]',
                'input[type="submit"]',
            ):
                try:
                    els = self.driver.find_elements(By.CSS_SELECTOR, css)
                    if els:
                        submit_btn = els[0]
                        break
                except Exception:
                    pass
        if submit_btn is not None:
            self.driver.execute_script("arguments[0].scrollIntoView({block: 'center'});", submit_btn)
            try:
                submit_btn.click()
            except ElementClickInterceptedException:
                self.driver.execute_script("arguments[0].click();", submit_btn)
        elif target_form is not None:
            self.driver.execute_script("arguments[0].submit();", target_form)
        else:
            form_els = self.driver.find_elements(By.TAG_NAME, "form")
            if not form_els:
                return None
            self.driver.execute_script("arguments[0].submit();", form_els[0])
        return self._wait_frame_ready("F_Hauptframe", expected_action="internalGalaxy", nav_token=nav_token)

    def _sync_galaxy_json_textarea(self) -> None:
        """Mirror live 2D Galaxy `window.si.result` into `<textarea id="json-element">` so `page_source` stays fresh across AJAX steps."""
        try:
            self.driver.execute_script(
                "var je = document.getElementById('json-element');"
                "if (!je) return;"
                "var v = '';"
                "if (window.si && window.si.result && window.si.result.planets) {"
                "  try {"
                "    var rawPl = window.si.result.planets;"
                "    var outPl = {};"
                "    for (var pk in rawPl) {"
                "      if (!Object.prototype.hasOwnProperty.call(rawPl, pk)) continue;"
                "      var item = rawPl[pk];"
                "      if (item && typeof item === 'object') {"
                "        var copy = {};"
                "        for (var ik in item) {"
                "          if (Object.prototype.hasOwnProperty.call(item, ik) && typeof item[ik] !== 'function') {"
                "            copy[ik] = item[ik];"
                "          }"
                "        }"
                "        outPl[String(Number(pk) + (Array.isArray(rawPl) ? 1 : 0))] = copy;"
                "      }"
                "    }"
                "    var posObj = Object.assign({}, window.si.result.position || {});"
                "    if (!posObj.galaxy && window.si.galaxy) posObj.galaxy = Number(window.si.galaxy);"
                "    if (!posObj.system && !posObj.sun && window.si.sun) posObj.system = Number(window.si.sun);"
                "    v = JSON.stringify({position: posObj, planets: outPl});"
                "  } catch (e) { v = ''; }"
                "}"
                "if (!v || v === 'null') {"
                "  v = (typeof je.value === 'string') ? je.value.trim() : '';"
                "}"
                "if (v && v !== 'null') {"
                "  if (je.value !== v) je.value = v;"
                "  if (je.textContent !== v) je.textContent = v;"
                "}"
            )
        except Exception:
            pass

    def _is_real_selenium_driver(self) -> bool:
        return getattr(self.driver, "__class__", object).__module__.startswith("selenium")

    def _record_galaxy_universe_bounds(
        self,
        *,
        max_galaxies: Any = None,
        max_system: Any = None,
        html: str = "",
    ) -> tuple[Optional[int], Optional[int]]:
        """Record dynamically observed universe `(max_galaxies, max_system)` bounds from live Galaxy DOM/JS."""
        mg = game_data.safe_int(max_galaxies, 0) if max_galaxies is not None else 0
        ms = game_data.safe_int(max_system, 0) if max_system is not None else 0
        if (mg <= 0 or ms <= 0) and isinstance(html, str) and html:
            html_g, html_s = extract_galaxy_universe_bounds(html)
            if mg <= 0 and html_g is not None:
                mg = html_g
            if ms <= 0 and html_s is not None:
                ms = html_s
        if 1 <= mg <= 50:
            self.last_galaxy_max_galaxies = mg
        if 1 <= ms <= 1000:
            self.last_galaxy_max_system = ms
        return self.last_galaxy_max_galaxies, self.last_galaxy_max_system

    def _wait_galaxy_ajax_ready(self, html: str) -> str:
        """Wait briefly for SpaceInvasion 2D/Modern Galaxy (`SI_TPL.galaxy` -> `si.getData()`) to populate planets."""
        if "SI_TPL.galaxy" not in html:
            return html
        self._record_galaxy_universe_bounds(html=html)
        ajax_deadline = time.monotonic() + min(3.5, max(0.2, self.timeout * 0.25))
        while time.monotonic() < ajax_deadline:
            self._switch_frame("F_Hauptframe")
            try:
                rendered = bool(
                    self.driver.execute_script(
                        "if (window.si) {"
                        "  if (window.si._getStatus) return false;"
                        "  if (window.si.result && window.si.result.position && window.si.result.planets) return true;"
                        "}"
                        "var gc = document.getElementById('galaxy-container');"
                        "if (gc && gc.querySelector && gc.querySelector('.planet-hit')) return true;"
                        "var je = document.getElementById('json-element');"
                        "if (je && typeof je.value === 'string' && je.value.length > 20) return true;"
                        "var bodyTxt = document.body ? (document.body.innerText || '') : '';"
                        "return bodyTxt.indexOf('X:') !== -1;"
                    )
                )
            except Exception:
                rendered = True
            if rendered:
                self._sync_galaxy_json_textarea()
                html = self.driver.page_source or html
                self._check_page_guards(html)
                break
            time.sleep(0.15)
        self._record_galaxy_universe_bounds(html=html)
        return html

    def open_galaxy_with_coords(
        self,
        *,
        galaxy: Optional[int] = None,
        system: Optional[int] = None,
        priority: str = "interactive",
    ) -> tuple[str, int, int]:
        """Open `internalGalaxy` inside `F_Hauptframe` and return `(html, actual_galaxy, actual_system)`."""
        self._pace(priority=priority)
        sid_query = self._resolve_sid_query()
        gal_arg = (
            game_data.cap_json_int(max(1, game_data.safe_int(galaxy, 1)))
            if galaxy is not None
            else None
        )
        sys_arg = (
            game_data.cap_json_int(max(1, game_data.safe_int(system, 1)))
            if system is not None
            else None
        )
        if sid_query:
            p_gal_js = None
            if self.detach_on_close and self._is_real_selenium_driver():
                try:
                    self._switch_frame("F_Navi")
                    p_gal_js = self.driver.execute_script(
                        "return (typeof window.p_galaxy === 'function') ? String(window.p_galaxy) : null;"
                    )
                except Exception as exc:
                    p_gal_js = f"<err:{exc}>"
            params = ["action=internalGalaxy", sid_query]
            if gal_arg is not None:
                params.append(f"galaxy={gal_arg}")
            if sys_arg is not None:
                params.append(f"system={sys_arg}")
            if gal_arg is not None:
                params.append(f"g={gal_arg}")
            if sys_arg is not None:
                params.append(f"s={sys_arg}")
            target_url = self._build_internal_url("&".join(params))
            try:
                html = self._open_frame_url_with_retry(target_url, expected_action="internalGalaxy")
            except BaseException as exc:
                if self.detach_on_close and self._is_real_selenium_driver():
                    append_crash_debug_log(
                        component="client.open_galaxy_with_coords",
                        event="open_galaxy_failed",
                        error=exc,
                        details={
                            "target_url": target_url,
                            "p_galaxy_js": p_gal_js,
                            **self._capture_dom_diag(),
                        },
                    )
                raise
            if self.detach_on_close and self._is_real_selenium_driver():
                append_crash_debug_log(
                    component="client.open_galaxy_with_coords",
                    event="open_galaxy_loaded",
                    details={
                        "target_url": target_url,
                        "p_galaxy_js": p_gal_js,
                        **self._capture_dom_diag(html),
                    },
                )
        else:
            nav_token = self._mark_frame_navigation("F_Hauptframe")
            self._switch_frame("F_Navi")
            gal_a = self.driver.find_element(By.CSS_SELECTOR, 'a[onclick*="p_galaxy"]')
            self.driver.execute_script("arguments[0].scrollIntoView({block: 'center'});", gal_a)
            try:
                gal_a.click()
            except ElementClickInterceptedException:
                self.driver.execute_script("arguments[0].click();", gal_a)
            html = self._wait_frame_ready("F_Hauptframe", expected_action="internalGalaxy", nav_token=nav_token)

        html = self._wait_galaxy_ajax_ready(html)
        self._switch_frame("F_Hauptframe")
        try:
            cur_url = str(self.driver.execute_script("return location.href;") or "")
        except Exception:
            cur_url = ""
        obs_max_g, _ = self._record_galaxy_universe_bounds(html=html)
        has_si_2d = False
        try:
            si_meta = self.driver.execute_script(
                "if (!window.si || typeof window.si.navigate !== 'function') return null;"
                "var maxG = Number(window.si.maxGalaxies || (window.SI_TPL && window.SI_TPL.galaxy && window.SI_TPL.galaxy.maxGalaxies) || 0);"
                "var maxS = Number(window.si.maxSystem || (window.SI_TPL && window.SI_TPL.galaxy && window.SI_TPL.galaxy.maxSystem) || 0);"
                "return {ok: true, maxG: maxG, maxS: maxS};"
            )
            if isinstance(si_meta, dict) and si_meta.get("ok"):
                has_si_2d = True
                obs_max_g, _ = self._record_galaxy_universe_bounds(
                    max_galaxies=si_meta.get("maxG"),
                    max_system=si_meta.get("maxS"),
                )
        except Exception:
            has_si_2d = False

        fallback_gal = gal_arg if gal_arg is not None else 1
        fallback_sys = sys_arg if sys_arg is not None else 1
        actual_gal, actual_sys = extract_galaxy_system_coords(
            html,
            url=cur_url,
            default=(fallback_gal, fallback_sys),
        )

        want_gal = gal_arg if gal_arg is not None else actual_gal
        want_sys = sys_arg if sys_arg is not None else actual_sys
        if obs_max_g is not None and obs_max_g >= 1 and want_gal > obs_max_g:
            want_gal = ((want_gal - 1) % obs_max_g) + 1
        if (galaxy is not None or system is not None) and (actual_gal != want_gal or actual_sys != want_sys):
            if has_si_2d:
                html, actual_gal, actual_sys = self.step_galaxy_system(
                    galaxy=want_gal,
                    system=want_sys,
                    pace=False,
                    priority=priority,
                    _allow_full_load=False,
                )
            else:
                switched_html = self._submit_galaxy_coords_form(
                    galaxy=want_gal if galaxy is not None else None,
                    system=system,
                    priority=priority,
                )
                if switched_html is not None:
                    html = self._wait_galaxy_ajax_ready(switched_html)
                    self._record_galaxy_universe_bounds(html=html)
                    self._switch_frame("F_Hauptframe")
                    try:
                        cur_url = str(self.driver.execute_script("return location.href;") or "")
                    except Exception:
                        cur_url = ""
                    actual_gal, actual_sys = extract_galaxy_system_coords(
                        html,
                        url=cur_url,
                        default=(want_gal, want_sys),
                    )

        self.last_galaxy_coords = (actual_gal, actual_sys)
        return html, actual_gal, actual_sys

    def open_galaxy(
        self,
        *,
        galaxy: Optional[int] = None,
        system: Optional[int] = None,
        priority: str = "interactive",
    ) -> str:
        """Open `internalGalaxy` inside `F_Hauptframe` using `F_Navi`'s live `SI_TPL.menu.sid`."""
        html, _, _ = self.open_galaxy_with_coords(
            galaxy=galaxy,
            system=system,
            priority=priority,
        )
        return html

    def _wait_galaxy_step_landed(
        self,
        *,
        expected_galaxy: int,
        expected_system: int,
        before_coords: Optional[tuple[int, int]] = None,
        bail_if_idle_unchanged: bool = False,
        max_galaxy: Optional[int] = None,
    ) -> tuple[bool, Optional[tuple[int, int]]]:
        """Poll `window.si` inside `F_Hauptframe` until `(expected_galaxy, expected_system)` lands or times out."""
        started = time.monotonic()
        deadline = started + min(4.0, max(0.4, self.timeout * 0.35))
        while time.monotonic() < deadline:
            time.sleep(0.06)
            self._switch_frame("F_Hauptframe")
            try:
                status = self.driver.execute_script(
                    "var pvErr = document.getElementById('pageview-error');"
                    "var pvAmt = document.getElementById('pageview-amount');"
                    "if (pvErr && pvErr.style && pvErr.style.display !== 'none' && pvAmt && (pvAmt.textContent || '').trim()) {"
                    "  return {throttled: true};"
                    "}"
                    "if (!window.si) return {missing: true};"
                    "var trk = document.getElementById('g2w-track');"
                    "var wideAnim = Boolean(trk && trk.style && trk.style.transform && trk.style.transform !== 'none' && trk.style.transform !== 'translate3d(0px, 0px, 0px)');"
                    "var swapAnim = Boolean((document.body && document.body.classList && document.body.classList.contains('system-changing')) || window.si._hardNavPending || wideAnim);"
                    "return {"
                    "  busy: Boolean(window.si._getStatus),"
                    "  animating: swapAnim,"
                    "  g: Number(window.si.galaxy || 0),"
                    "  s: Number(window.si.sun || 0),"
                    "  resG: window.si.result && window.si.result.position ? Number(window.si.result.position.galaxy || 0) : 0,"
                    "  resS: window.si.result && window.si.result.position ? Number(window.si.result.position.system || window.si.result.position.sun || 0) : 0"
                    "};"
                )
            except Exception:
                break
            if isinstance(status, dict):
                if status.get("throttled"):
                    html_err = self.driver.page_source or ""
                    self._check_page_guards(html_err)
                    raise RateLimitError(
                        "SpaceInvasion 2D Galaxy AJAX per-minute page-view throttle hit.",
                        server_throttle=True,
                    )
                if status.get("missing"):
                    break
                busy = bool(status.get("busy"))
                animating = bool(status.get("animating"))
                res_g = game_data.safe_int(status.get("resG"), 0)
                res_s = game_data.safe_int(status.get("resS"), 0)
                cur_g = game_data.safe_int(status.get("g"), 0)
                cur_s = game_data.safe_int(status.get("s"), 0)
                if not busy and res_g == expected_galaxy and res_s == expected_system:
                    return True, (expected_galaxy, expected_system)
                if (
                    not busy
                    and max_galaxy is not None
                    and max_galaxy >= 1
                    and expected_galaxy > max_galaxy
                    and 1 <= res_g <= max_galaxy
                    and res_s == expected_system
                ):
                    return True, (res_g, res_s)
                # Widescreen `g2w.swing(dir)` animates for 420ms before calling `si.navigate` / setting `_getStatus`;
                # wait at least 0.90s while idle and not animating before bailing out early.
                if bail_if_idle_unchanged and not busy and not animating and (time.monotonic() - started) >= 0.90:
                    if before_coords is not None and (cur_g, cur_s) == before_coords and (res_g, res_s) == before_coords:
                        return False, None
                    if res_g > 0 and res_g != expected_galaxy:
                        return False, None
        return False, None

    def _finalize_galaxy_arrow_click(
        self,
        res: Any,
        *,
        fallback_js: str,
        step_dir: int,
    ) -> str:
        """Complete a located galaxy/system arrow click via trusted Selenium `.click()` with JS fallback."""
        if not isinstance(res, dict):
            return "none"
        clicked = str(res.get("clicked") or "none")
        if clicked != "element":
            return clicked
        el = res.get("el")
        if el is None:
            return "none"
        try:
            self.driver.execute_script(
                "if (arguments[0] && arguments[0].scrollIntoView) arguments[0].scrollIntoView({block: 'nearest', inline: 'nearest'});",
                el,
            )
        except Exception:
            pass
        try:
            el.click()
            return "dom"
        except (SpaceInvasionAuthError, AntiBotChallengeError, RateLimitError):
            raise
        except Exception:
            try:
                self.driver.execute_script("arguments[0].click();", el)
                return "dom"
            except Exception:
                try:
                    ok_fn = bool(self.driver.execute_script(fallback_js, step_dir))
                    return "fn" if ok_fn else "none"
                except Exception:
                    return "none"

    def _click_galaxy_system_arrow(self, delta: int) -> str:
        """Click the live 2D Galaxy next/prev system arrow (desktop, mobile, or widescreen `g2w`) in `F_Hauptframe`."""
        step_dir = 1 if int(delta) >= 0 else -1
        use_selenium_click = self._is_real_selenium_driver()
        self._switch_frame("F_Hauptframe")
        res = self.driver.execute_script(
            "var delta = Number(arguments[0]);"
            "var returnEl = Boolean(arguments[1]);"
            "var sels = delta > 0"
            "  ? ["
            "      '[onclick*=\"g2w.nav(\\'sys\\',1)\"]',"
            "      '[onclick*=\"g2w.nav(\\'sys\\', 1)\"]',"
            "      'img[onclick*=\"navigateSystem(1)\"]',"
            "      '[onclick*=\"navigateSystem(1)\"]',"
            "      'img[onclick*=\"navigateSystem(+1)\"]',"
            "      '[onclick*=\"navigateSystem( 1)\"]'"
            "    ]"
            "  : ["
            "      '[onclick*=\"g2w.nav(\\'sys\\',-1)\"]',"
            "      '[onclick*=\"g2w.nav(\\'sys\\', -1)\"]',"
            "      'img[onclick*=\"navigateSystem(-1)\"]',"
            "      '[onclick*=\"navigateSystem(-1)\"]',"
            "      '[onclick*=\"navigateSystem( -1)\"]'"
            "    ];"
            "function isVis(el) {"
            "  if (!el) return false;"
            "  var r = el.getBoundingClientRect ? el.getBoundingClientRect() : null;"
            "  var hasBox = (el.offsetWidth > 0 && el.offsetHeight > 0) || (r && r.width > 0 && r.height > 0);"
            "  if (!hasBox) return false;"
            "  var cur = el;"
            "  while (cur && cur.nodeType === 1) {"
            "    var cs = window.getComputedStyle ? window.getComputedStyle(cur) : null;"
            "    if (cs && (cs.display === 'none' || cs.visibility === 'hidden' || cs.opacity === '0')) return false;"
            "    cur = cur.parentElement;"
            "  }"
            "  return true;"
            "}"
            "var cands = [];"
            "if (document.querySelectorAll) {"
            "  for (var si = 0; si < sels.length; si++) {"
            "    try {"
            "      var list = document.querySelectorAll(sels[si]);"
            "      for (var li = 0; li < list.length; li++) {"
            "        if (cands.indexOf(list[li]) === -1) cands.push(list[li]);"
            "      }"
            "    } catch (e) {}"
            "  }"
            "  if (!cands.length) {"
            "    var rx = delta > 0"
            "      ? /(?:g2w\\.nav\\(\\s*['\"]sys['\"]\\s*,\\s*\\+?1\\s*\\)|navigateSystem\\(\\s*\\+?1\\s*\\))/"
            "      : /(?:g2w\\.nav\\(\\s*['\"]sys['\"]\\s*,\\s*-1\\s*\\)|navigateSystem\\(\\s*-1\\s*\\))/;"
            "    var allOc = document.querySelectorAll('[onclick]');"
            "    for (var ai = 0; ai < allOc.length; ai++) {"
            "      var oc = String(allOc[ai].getAttribute('onclick') || '');"
            "      if (rx.test(oc)) cands.push(allOc[ai]);"
            "    }"
            "  }"
            "}"
            "var chosen = null;"
            "for (var i = 0; i < cands.length; i++) {"
            "  if (isVis(cands[i])) { chosen = cands[i]; break; }"
            "}"
            "if (!chosen && cands.length > 0) chosen = cands[0];"
            "if (chosen) {"
            "  if (returnEl) return {clicked: 'element', el: chosen};"
            "  if (typeof chosen.click === 'function') {"
            "    chosen.click();"
            "    return {clicked: 'dom'};"
            "  }"
            "}"
            "if (window.si && typeof window.si.navigateSystem === 'function') {"
            "  window.si.navigateSystem(delta);"
            "  return {clicked: 'fn'};"
            "}"
            "return {clicked: 'none'};",
            step_dir,
            use_selenium_click,
        )
        return self._finalize_galaxy_arrow_click(
            res,
            fallback_js=(
                "if (window.si && typeof window.si.navigateSystem === 'function') {"
                "  window.si.navigateSystem(Number(arguments[0])); return true;"
                "} return false;"
            ),
            step_dir=step_dir,
        )

    def _click_galaxy_arrow(self, delta: int) -> str:
        """Click the live 2D Galaxy next/prev galaxy arrow (desktop, mobile, or widescreen `g2w`) in `F_Hauptframe`."""
        step_dir = 1 if int(delta) >= 0 else -1
        use_selenium_click = self._is_real_selenium_driver()
        self._switch_frame("F_Hauptframe")
        res = self.driver.execute_script(
            "var delta = Number(arguments[0]);"
            "var returnEl = Boolean(arguments[1]);"
            "var sels = delta > 0"
            "  ? ["
            "      '[onclick*=\"g2w.nav(\\'gal\\',1)\"]',"
            "      '[onclick*=\"g2w.nav(\\'gal\\', 1)\"]',"
            "      'img[onclick*=\"navigateGalaxy(1)\"]',"
            "      '[onclick*=\"navigateGalaxy(1)\"]',"
            "      'img[onclick*=\"navigateGalaxy(+1)\"]',"
            "      '[onclick*=\"navigateGalaxy( 1)\"]'"
            "    ]"
            "  : ["
            "      '[onclick*=\"g2w.nav(\\'gal\\',-1)\"]',"
            "      '[onclick*=\"g2w.nav(\\'gal\\', -1)\"]',"
            "      'img[onclick*=\"navigateGalaxy(-1)\"]',"
            "      '[onclick*=\"navigateGalaxy(-1)\"]',"
            "      '[onclick*=\"navigateGalaxy( -1)\"]'"
            "    ];"
            "function isVis(el) {"
            "  if (!el) return false;"
            "  var r = el.getBoundingClientRect ? el.getBoundingClientRect() : null;"
            "  var hasBox = (el.offsetWidth > 0 && el.offsetHeight > 0) || (r && r.width > 0 && r.height > 0);"
            "  if (!hasBox) return false;"
            "  var cur = el;"
            "  while (cur && cur.nodeType === 1) {"
            "    var cs = window.getComputedStyle ? window.getComputedStyle(cur) : null;"
            "    if (cs && (cs.display === 'none' || cs.visibility === 'hidden' || cs.opacity === '0')) return false;"
            "    cur = cur.parentElement;"
            "  }"
            "  return true;"
            "}"
            "var cands = [];"
            "if (document.querySelectorAll) {"
            "  for (var si = 0; si < sels.length; si++) {"
            "    try {"
            "      var list = document.querySelectorAll(sels[si]);"
            "      for (var li = 0; li < list.length; li++) {"
            "        if (cands.indexOf(list[li]) === -1) cands.push(list[li]);"
            "      }"
            "    } catch (e) {}"
            "  }"
            "  if (!cands.length) {"
            "    var rx = delta > 0"
            "      ? /(?:g2w\\.nav\\(\\s*['\"]gal['\"]\\s*,\\s*\\+?1\\s*\\)|navigateGalaxy\\(\\s*\\+?1\\s*\\))/"
            "      : /(?:g2w\\.nav\\(\\s*['\"]gal['\"]\\s*,\\s*-1\\s*\\)|navigateGalaxy\\(\\s*-1\\s*\\))/;"
            "    var allOc = document.querySelectorAll('[onclick]');"
            "    for (var ai = 0; ai < allOc.length; ai++) {"
            "      var oc = String(allOc[ai].getAttribute('onclick') || '');"
            "      if (rx.test(oc)) cands.push(allOc[ai]);"
            "    }"
            "  }"
            "}"
            "var chosen = null;"
            "for (var i = 0; i < cands.length; i++) {"
            "  if (isVis(cands[i])) { chosen = cands[i]; break; }"
            "}"
            "if (!chosen && cands.length > 0) chosen = cands[0];"
            "if (chosen) {"
            "  if (returnEl) return {clicked: 'element', el: chosen};"
            "  if (typeof chosen.click === 'function') {"
            "    chosen.click();"
            "    return {clicked: 'dom'};"
            "  }"
            "}"
            "if (window.si && typeof window.si.navigateGalaxy === 'function') {"
            "  window.si.navigateGalaxy(delta);"
            "  return {clicked: 'fn'};"
            "}"
            "return {clicked: 'none'};",
            step_dir,
            use_selenium_click,
        )
        return self._finalize_galaxy_arrow_click(
            res,
            fallback_js=(
                "if (window.si && typeof window.si.navigateGalaxy === 'function') {"
                "  window.si.navigateGalaxy(Number(arguments[0])); return true;"
                "} return false;"
            ),
            step_dir=step_dir,
        )

    def step_galaxy_system(
        self,
        *,
        galaxy: int,
        system: int,
        pace: bool = False,
        priority: str = "normal",
        max_arrow_distance: int = ARROW_NAV_MAX_DISTANCE,
        on_intermediate_page: Optional[Callable[[str, int, int], None]] = None,
        _allow_full_load: bool = True,
    ) -> tuple[str, int, int]:
        """Step to `(galaxy, system)` in-place on `internalGalaxy`, prioritizing arrow clicks when same-galaxy distance `< 5`."""
        gal_arg = game_data.cap_json_int(max(1, min(1000, game_data.safe_int(galaxy, 1))))
        sys_arg = game_data.cap_json_int(max(1, min(1000, game_data.safe_int(system, 1))))
        eff_arrow_max = max(1, game_data.safe_int(max_arrow_distance, ARROW_NAV_MAX_DISTANCE))
        self.last_galaxy_nav_method = ""
        self.last_galaxy_step_clicks = 0

        can_ajax_step = False
        cur_g = 0
        cur_s = 0
        max_sys = 400
        max_gal = 0
        try:
            self._switch_frame("F_Hauptframe")
            probe = self.driver.execute_script(
                "var cg = document.getElementById('si-captcha-gate');"
                "if (cg && window.getComputedStyle(cg).display !== 'none' && window.getComputedStyle(cg).visibility !== 'hidden' && cg.offsetWidth > 0 && cg.offsetHeight > 0) return 'captcha';"
                "if (window.si && typeof window.si.navigate === 'function' && typeof window.si.getData === 'function') {"
                "  var resPos = (window.si.result && window.si.result.position) ? window.si.result.position : null;"
                "  var resG = Number((resPos && resPos.galaxy) || 0);"
                "  var resS = Number((resPos && (resPos.system || resPos.sun)) || 0);"
                "  if (!window.si._getStatus && resG > 0 && resS > 0) {"
                "    if (Number(window.si.galaxy || 0) !== resG) window.si.galaxy = resG;"
                "    if (Number(window.si.sun || 0) !== resS) window.si.sun = resS;"
                "  }"
                "  var cgVal = resG || Number(window.si.galaxy || 0);"
                "  var csVal = resS || Number(window.si.sun || 0);"
                "  var maxS = Number(window.si.maxSystem || (window.SI_TPL && window.SI_TPL.galaxy && window.SI_TPL.galaxy.maxSystem) || 400);"
                "  if (!maxS || maxS < 1) maxS = 400;"
                "  var maxG = Number(window.si.maxGalaxies || (window.SI_TPL && window.SI_TPL.galaxy && window.SI_TPL.galaxy.maxGalaxies) || 0);"
                "  if (!maxG || maxG < 1) maxG = 0;"
                "  return {ok: true, g: cgVal, s: csVal, maxS: maxS, maxG: maxG, busy: Boolean(window.si._getStatus)};"
                "}"
                "return null;"
            )
            if probe == "captcha":
                html_cap = self.driver.page_source or ""
                self._check_page_guards(html_cap)
            if isinstance(probe, dict) and probe.get("ok"):
                can_ajax_step = True
                cur_g = game_data.safe_int(probe.get("g"), 0)
                cur_s = game_data.safe_int(probe.get("s"), 0)
                max_sys = max(1, min(1000, game_data.safe_int(probe.get("maxS"), 400)))
                raw_max_g = game_data.safe_int(probe.get("maxG"), 0)
                if 1 <= raw_max_g <= 50:
                    max_gal = raw_max_g
                self._record_galaxy_universe_bounds(
                    max_galaxies=max_gal if max_gal > 0 else None,
                    max_system=max_sys,
                )
                if probe.get("busy"):
                    wait_busy_until = time.monotonic() + min(1.5, max(0.2, self.timeout * 0.15))
                    while time.monotonic() < wait_busy_until:
                        time.sleep(0.06)
                        try:
                            b_probe = self.driver.execute_script(
                                "if (!window.si) return null;"
                                "var rp = (window.si.result && window.si.result.position) ? window.si.result.position : null;"
                                "var rg = Number((rp && rp.galaxy) || 0);"
                                "var rs = Number((rp && (rp.system || rp.sun)) || 0);"
                                "if (!window.si._getStatus && rg > 0 && rs > 0) {"
                                "  if (Number(window.si.galaxy || 0) !== rg) window.si.galaxy = rg;"
                                "  if (Number(window.si.sun || 0) !== rs) window.si.sun = rs;"
                                "}"
                                "return {busy: Boolean(window.si._getStatus), g: rg || Number(window.si.galaxy || 0), s: rs || Number(window.si.sun || 0)};"
                            )
                        except Exception:
                            break
                        if isinstance(b_probe, dict) and not b_probe.get("busy"):
                            cur_g = game_data.safe_int(b_probe.get("g"), cur_g)
                            cur_s = game_data.safe_int(b_probe.get("s"), cur_s)
                            break
        except SpaceInvasionAuthError:
            raise
        except Exception:
            can_ajax_step = False

        if not can_ajax_step:
            self.last_galaxy_nav_method = "full_load"
            self.last_galaxy_step_clicks = 1
            if _allow_full_load:
                return self.open_galaxy_with_coords(
                    galaxy=gal_arg,
                    system=sys_arg,
                    priority=priority,
                )
            self._switch_frame("F_Hauptframe")
            self._sync_galaxy_json_textarea()
            html_fb = self.driver.page_source or ""
            self._check_page_guards(html_fb)
            self._record_galaxy_universe_bounds(html=html_fb)
            actual_gal, actual_sys = extract_galaxy_system_coords(html_fb, default=(gal_arg, sys_arg))
            self.last_galaxy_coords = (actual_gal, actual_sys)
            return html_fb, actual_gal, actual_sys

        # SpaceInvasion wraps out-of-range galaxy numbers modulo `maxGalaxies` (e.g. RETRO has 8 galaxies: 9 -> 1).
        # Normalize against observed `maxGalaxies` so we never spin in 4s timeout + full reload loops.
        eff_gal = ((gal_arg - 1) % max_gal) + 1 if (max_gal > 0 and gal_arg > max_gal) else gal_arg

        if pace:
            self._pace(priority=priority)

        landed = False
        landed_coords: Optional[tuple[int, int]] = None

        # 1. Prioritize clicking the in-page next/prev system arrow button whenever in the same galaxy with distance < 5.
        if cur_g == eff_gal and 1 <= cur_s <= max_sys and 1 <= sys_arg <= max_sys:
            fwd_steps = (sys_arg - cur_s) % max_sys
            bwd_steps = (cur_s - sys_arg) % max_sys
            circ_dist = min(fwd_steps, bwd_steps)
            if 1 <= circ_dist < eff_arrow_max:
                step_dir = 1 if fwd_steps <= bwd_steps else -1
                step_cur_s = cur_s
                arrow_ok = True
                for step_i in range(circ_dist):
                    if step_i > 0:
                        if pace:
                            self._pace(priority=priority)
                        elif self.min_nav_delay[1] > 0.0:
                            inter_s = min(0.25, max(0.08, self.min_nav_delay[0] * 0.15))
                            time.sleep(inter_s)
                    exp_s = ((step_cur_s - 1 + step_dir) % max_sys) + 1
                    clicked_mode = self._click_galaxy_system_arrow(step_dir)
                    if clicked_mode == "none":
                        arrow_ok = False
                        break
                    self.last_galaxy_step_clicks += 1
                    step_ok, _ = self._wait_galaxy_step_landed(
                        expected_galaxy=eff_gal,
                        expected_system=exp_s,
                        before_coords=(eff_gal, step_cur_s),
                        bail_if_idle_unchanged=True,
                        max_galaxy=max_gal if max_gal > 0 else None,
                    )
                    if not step_ok:
                        arrow_ok = False
                        break
                    step_cur_s = exp_s
                    if step_i < circ_dist - 1 and callable(on_intermediate_page):
                        self._switch_frame("F_Hauptframe")
                        self._sync_galaxy_json_textarea()
                        inter_html = self.driver.page_source or ""
                        self._check_page_guards(inter_html)
                        on_intermediate_page(inter_html, eff_gal, exp_s)
                if arrow_ok:
                    landed = True
                    landed_coords = (eff_gal, sys_arg)
                    self.last_galaxy_nav_method = "arrow"

        # 2. Single-click adjacent galaxy arrow when staying on the same system (same 1-XHR cost as `si.navigate`).
        if (
            not landed
            and cur_s == sys_arg
            and 1 <= cur_s <= max_sys
            and cur_g > 0
            and eff_gal > 0
            and abs(eff_gal - cur_g) == 1
        ):
            gal_dir = 1 if eff_gal > cur_g else -1
            clicked_gal = self._click_galaxy_arrow(gal_dir)
            if clicked_gal != "none":
                self.last_galaxy_step_clicks += 1
                gal_ok, gal_coords = self._wait_galaxy_step_landed(
                    expected_galaxy=eff_gal,
                    expected_system=sys_arg,
                    before_coords=(cur_g, cur_s),
                    bail_if_idle_unchanged=True,
                    max_galaxy=max_gal if max_gal > 0 else None,
                )
                if gal_ok:
                    landed = True
                    landed_coords = gal_coords or (eff_gal, sys_arg)
                    self.last_galaxy_nav_method = "arrow"

        if not landed:
            self._switch_frame("F_Hauptframe")
            self.driver.execute_script(
                "window.__siSweepDone = false;"
                "var targetG = Number(arguments[0]), targetS = Number(arguments[1]);"
                "if (Number(window.si.galaxy) === targetG && Number(window.si.sun) === targetS && !window.si._getStatus && window.si.result) {"
                "  window.si.getData();"
                "} else {"
                "  window.si.navigate(targetG, targetS);"
                "}",
                eff_gal,
                sys_arg,
            )
            self.last_galaxy_step_clicks += 1
            landed, landed_coords = self._wait_galaxy_step_landed(
                expected_galaxy=eff_gal,
                expected_system=sys_arg,
                max_galaxy=max_gal if max_gal > 0 else None,
            )
            if landed:
                self.last_galaxy_nav_method = (
                    "ajax_refresh" if (cur_g == eff_gal and cur_s == sys_arg) else "ajax_navigate"
                )

        if not landed and _allow_full_load:
            self.last_galaxy_nav_method = "full_load"
            self.last_galaxy_step_clicks += 1
            return self.open_galaxy_with_coords(
                galaxy=eff_gal,
                system=sys_arg,
                priority=priority,
            )

        self._switch_frame("F_Hauptframe")
        self._sync_galaxy_json_textarea()
        html = self.driver.page_source or ""
        self._check_page_guards(html)
        self._record_galaxy_universe_bounds(html=html)
        if landed_coords is not None:
            actual_gal, actual_sys = landed_coords
        else:
            try:
                cur_url = str(self.driver.execute_script("return location.href;") or "")
            except Exception:
                cur_url = ""
            actual_gal, actual_sys = extract_galaxy_system_coords(
                html,
                url=cur_url,
                default=(eff_gal, sys_arg),
            )
        self.last_galaxy_coords = (actual_gal, actual_sys)
        return html, actual_gal, actual_sys

    def dispatch_galaxy_spy(
        self,
        *,
        galaxy: int,
        system: int,
        position: int,
        quantity: int = 1,
        is_asteroid: bool = False,
        priority: str = "interactive",
    ) -> dict[str, Any]:
        """Dispatch a 2D Galaxy quick-spy (`startFleetAction=spy`) via `window.si.getData(...)`."""
        gal_arg = game_data.cap_json_int(max(1, min(1000, game_data.safe_int(galaxy, 1))))
        sys_arg = game_data.cap_json_int(max(1, min(1000, game_data.safe_int(system, 1))))
        pos_arg = game_data.cap_json_int(max(1, min(16, game_data.safe_int(position, 1))))
        qty_arg = game_data.cap_json_int(max(1, min(9999, game_data.safe_int(quantity, 1))))

        html, actual_gal, actual_sys = self.step_galaxy_system(
            galaxy=gal_arg,
            system=sys_arg,
            pace=True,
            priority=priority,
        )
        self._switch_frame("F_Hauptframe")
        has_si = False
        try:
            has_si = bool(
                self.driver.execute_script(
                    "return Boolean(window.si && typeof window.si.getData === 'function');"
                )
            )
        except Exception:
            has_si = False
        if not has_si:
            return {"supported": False, "started": False, "error": "2D Galaxy si.getData unavailable", "html": html}

        self._pace(priority=priority)
        self._switch_frame("F_Hauptframe")
        ta_part = "&ta=1" if is_asteroid else ""
        param = (
            f"&startFleet=1&fleets={qty_arg}&startFleetAction=spy"
            f"&gal={gal_arg}&sun={sys_arg}&p={pos_arg}{ta_part}&m=1"
        )
        self.driver.execute_script(
            "var g = document.getElementById('fa-gal'); if (g) g.value = String(arguments[0]);"
            "var s = document.getElementById('fa-sun'); if (s) s.value = String(arguments[1]);"
            "var p = document.getElementById('fa-pla'); if (p) p.value = String(arguments[2]);"
            "var f = document.getElementById('fleet-spy'); if (f) f.value = String(arguments[3]);"
            "var mf = document.getElementById('message-fleetstart'); if (mf) mf.style.display = 'none';"
            "var mf2 = document.getElementById('message-fleetstart-2'); if (mf2) mf2.style.display = 'none';"
            "var me = document.getElementById('message-error'); if (me) me.style.display = 'none';"
            "var met = document.getElementById('message-error-text'); if (met) met.textContent = '';"
            "if (window.si && window.si.result) { delete window.si.result.flightStarted; delete window.si.result.err; }"
            "window.si.getData(arguments[4]);",
            gal_arg,
            sys_arg,
            pos_arg,
            qty_arg,
            param,
        )
        deadline = time.monotonic() + min(4.5, max(0.5, self.timeout * 0.4))
        outcome: dict[str, Any] = {"supported": True, "started": False, "error": ""}
        while time.monotonic() < deadline:
            time.sleep(0.1)
            self._switch_frame("F_Hauptframe")
            try:
                st = self.driver.execute_script(
                    "var pvErr = document.getElementById('pageview-error');"
                    "var pvAmt = document.getElementById('pageview-amount');"
                    "if (pvErr && pvErr.style && pvErr.style.display !== 'none' && pvAmt && (pvAmt.textContent || '').trim()) {"
                    "  return {throttled: true};"
                    "}"
                    "if (!window.si) return {done: true, err: 'window.si lost'};"
                    "if (window.si._getStatus) return {busy: true};"
                    "var r = window.si.result || {};"
                    "var errEl = document.getElementById('message-error-text') || document.getElementById('message-error-text-mobile');"
                    "var domErr = errEl ? (errEl.textContent || '').trim() : '';"
                    "return {"
                    "  busy: false,"
                    "  flightStarted: r.flightStarted !== undefined ? r.flightStarted : null,"
                    "  err: r.err !== undefined ? String(r.err || '') : domErr"
                    "};"
                )
            except Exception as exc:
                outcome["error"] = str(exc)
                break
            if isinstance(st, dict):
                if st.get("throttled"):
                    html_err = self.driver.page_source or ""
                    self._check_page_guards(html_err)
                    raise RateLimitError(
                        "SpaceInvasion 2D Galaxy AJAX per-minute page-view throttle hit.",
                        server_throttle=True,
                    )
                if st.get("busy"):
                    continue
                fs = st.get("flightStarted")
                err_s = str(st.get("err") or "").strip()
                outcome["started"] = bool(fs)
                outcome["flight_started"] = fs
                outcome["error"] = err_s
                break
        self._switch_frame("F_Hauptframe")
        self._sync_galaxy_json_textarea()
        post_html = self.driver.page_source or html
        self._check_page_guards(post_html)
        outcome["html"] = post_html
        outcome["galaxy"] = actual_gal
        outcome["system"] = actual_sys
        return outcome

    def open_messages(
        self,
        msg_type: str = "spio",
        *,
        priority: str = "interactive",
    ) -> str:
        """Open `internalMessages` (e.g. `type=spio` espionage reports or main inbox) in `F_Hauptframe`."""
        raw_type = str(msg_type) if msg_type is not None else "spio"
        clean_type = re.sub(r"[^a-zA-Z0-9_-]", "", raw_type.strip())
        self._pace(priority=priority)
        sid_query = self._resolve_sid_query()
        if sid_query:
            if clean_type and clean_type.lower() != "all":
                q_str = f"action=internalMessages&type={clean_type}&{sid_query}"
            else:
                q_str = f"action=internalMessages&{sid_query}"
            target_url = self._build_internal_url(q_str)
            return self._open_frame_url_with_retry(target_url, expected_action="internalMessages")

        nav_token = self._mark_frame_navigation("F_Hauptframe")
        self._switch_frame("F_Navi")
        link = None
        selectors = (
            (
                f'a[href*="action=internalMessages"][href*="type={clean_type}"]',
                'a[href*="action=internalMessages"]',
            )
            if (clean_type and clean_type.lower() != "all")
            else ('a[href*="action=internalMessages"]',)
        )
        for css in selectors:
            try:
                els = self.driver.find_elements(By.CSS_SELECTOR, css)
                if els:
                    link = els[0]
                    break
            except Exception:
                pass
        if link is None:
            link = self.driver.find_element(By.CSS_SELECTOR, 'a[href*="action=internalMessages"]')
        self.driver.execute_script("arguments[0].scrollIntoView({block: 'center'});", link)
        try:
            link.click()
        except ElementClickInterceptedException:
            self.driver.execute_script("arguments[0].click();", link)
        html = self._wait_frame_ready(
            "F_Hauptframe",
            expected_action="internalMessages",
            nav_token=nav_token,
        )
        if clean_type and clean_type.lower() != "all":
            try:
                self._switch_frame("F_Hauptframe")
                cur_loc = str(self.driver.execute_script("return String(location.href || '');") or "")
                if f"type={clean_type}" not in cur_loc:
                    sub_el = None
                    for sub_css in (
                        f'[onclick*="action=internalMessages"][onclick*="type={clean_type}"]',
                        f'a[href*="action=internalMessages"][href*="type={clean_type}"]',
                    ):
                        els = self.driver.find_elements(By.CSS_SELECTOR, sub_css)
                        if els:
                            sub_el = els[0]
                            break
                    if sub_el is not None:
                        sub_tok = self._mark_frame_navigation("F_Hauptframe")
                        try:
                            sub_el.click()
                        except ElementClickInterceptedException:
                            self.driver.execute_script("arguments[0].click();", sub_el)
                        html = self._wait_frame_ready(
                            "F_Hauptframe",
                            expected_action="internalMessages",
                            nav_token=sub_tok,
                        )
            except Exception:
                pass
        return html

    def open_building_detail(
        self,
        building_id: int,
        *,
        priority: str = "interactive",
    ) -> str:
        """Open `internalDetail&type=building&id=<building_id>` in `F_Hauptframe`."""
        bid = max(1, game_data.cap_json_int(game_data.safe_int(building_id, 1)))
        self._pace(priority=priority)
        sid_query = self._resolve_sid_query()
        if sid_query:
            target_url = self._build_internal_url(f"action=internalDetail&type=building&id={bid}&{sid_query}")
            return self._open_frame_url_with_retry(target_url, expected_action="internalDetail")
        self.navigate_menu("internalBuildings", priority=priority)
        nav_token = self._mark_frame_navigation("F_Hauptframe")
        self._switch_frame("F_Hauptframe")
        link = self.driver.find_element(
            By.CSS_SELECTOR,
            f'a[href*="action=internalDetail"][href*="id={bid}"]',
        )
        self.driver.execute_script("arguments[0].scrollIntoView({block: 'center'});", link)
        try:
            link.click()
        except ElementClickInterceptedException:
            self.driver.execute_script("arguments[0].click();", link)
        return self._wait_frame_ready(
            "F_Hauptframe",
            expected_action="internalDetail",
            nav_token=nav_token,
        )

    def expand_collapsed_messages_on_page(self, *, max_fetches: int = 12) -> Optional[str]:
        """Expand collapsed AJAX/sub-report message rows on `internalMessages` (`spio`, `battle`, `fleet`, etc.)."""
        drv_lock = getattr(self, "_driver_lock", None)
        expanded = 0
        fetches = 0
        sid_query = ""
        with contextlib.suppress(Exception):
            sid_query = self._resolve_sid_query()
        cap_fetches = max(1, min(25, game_data.safe_int(max_fetches, 12)))

        # Limit sub-fetches to available rolling page-view headroom so we never trip server rate limits
        limiter = getattr(self, "_page_limiter", None)
        if limiter is not None:
            with contextlib.suppress(Exception):
                with limiter._lock:
                    now_m = limiter._clock()
                    cutoff = now_m - limiter.window_seconds
                    while limiter._views and limiter._views[0] <= cutoff:
                        limiter._views.popleft()
                    avail = max(
                        1,
                        limiter.max_views_per_minute
                        - limiter.reserved_interactive_views
                        - len(limiter._views)
                        - 2,
                    )
                    cap_fetches = max(1, min(cap_fetches, avail))

        with (drv_lock if drv_lock is not None else contextlib.nullcontext()):
            try:
                self._switch_frame("F_Hauptframe")
                res = self.driver.execute_script(
                    """
                    var triggers = document.querySelectorAll(
                        '[onclick*="showMessage"], [onclick*="openMessage"], ' +
                        '[onclick*="toggleMessage"], [onclick*="readMessage"], ' +
                        '[onclick*="loadMessage"], .msg-header:not(.open), .message-header:not(.open)'
                    );
                    var count = 0;
                    for (var i = 0; i < triggers.length && count < 25; i++) {
                        var el = triggers[i];
                        if (el.tagName === 'INPUT' || el.tagName === 'SELECT') continue;
                        var oc = (el.getAttribute('onclick') || '').toLowerCase();
                        if (oc.indexOf('del') !== -1) continue;
                        try { el.click(); count++; } catch (e) {}
                    }
                    return count;
                    """
                )
                if isinstance(res, int) and not isinstance(res, bool) and res > 0:
                    expanded += res
            except Exception:
                return None

            async_fn = getattr(self.driver, "execute_async_script", None)
            if callable(async_fn):
                with contextlib.suppress(Exception):
                    self.driver.set_script_timeout(30)
                try:
                    ares = async_fn(
                        """
                        var sidQuery = String(arguments[0] || '');
                        var maxFetches = Number(arguments[1] || 12);
                        var done = arguments[arguments.length - 1];
                        var tasks = [];
                        var rows = Array.prototype.slice.call(document.querySelectorAll('tr'));

                        for (var i = 0; i < rows.length; i++) {
                            var tr = rows[i];
                            if (tr.getAttribute('data-si-expanded') === '1') continue;
                            if (tr.querySelector('.si-expanded-body')) {
                                tr.setAttribute('data-si-expanded', '1');
                                continue;
                            }
                            var spioEl = tr.querySelector('[onclick*="showSpionageReport"]');
                            if (spioEl) {
                                var oc = String(spioEl.getAttribute('onclick') || '');
                                var mSp = oc.match(/showSpionageReport\\s*\\(\\s*['"]?(\\d+)['"]?/i);
                                if (mSp) {
                                    tasks.push({
                                        tr: tr,
                                        kind: 'spio',
                                        prio: 0,
                                        url: 'indexInternal.es?action=internalSpionageReport&reportID=' + mSp[1] + (sidQuery ? '&' + sidQuery : '')
                                    });
                                    continue;
                                }
                            }
                            var bsumCell = tr.querySelector('[data-bsum]') || (tr.getAttribute('data-bsum') ? tr : null);
                            var bsumOc = tr.querySelector('[onclick*="showBattleReport"]');
                            var bid = '';
                            if (bsumCell) bid = String(bsumCell.getAttribute('data-bsum') || '').trim();
                            if (!bid && bsumOc) {
                                var mBt = String(bsumOc.getAttribute('onclick') || '').match(/showBattleReport\\s*\\(\\s*['"]?(\\d+)['"]?/i);
                                if (mBt) bid = mBt[1];
                            }
                            if (bid && /^\\d+$/.test(bid)) {
                                tasks.push({
                                    tr: tr,
                                    kind: 'battle',
                                    prio: 0,
                                    bid: bid,
                                    url: '/indexGame.es?battle=' + bid + (sidQuery ? '&' + sidQuery : '')
                                });
                                continue;
                            }
                            var readLink = tr.querySelector('a[href*="action=internalMessages"][href*="read="]');
                            if (readLink) {
                                var href = String(readLink.getAttribute('href') || '').trim();
                                var rtxt = String(tr.textContent || '').toLowerCase();
                                var isRoutineReturn = (
                                    (rtxt.indexOf('fleet return') !== -1 || rtxt.indexOf('zurückgekehrt') !== -1 || rtxt.indexOf('zurueckgekehrt') !== -1) &&
                                    rtxt.indexOf('recycl') === -1 &&
                                    rtxt.indexOf('rubble') === -1 &&
                                    rtxt.indexOf('coloniz') === -1 &&
                                    rtxt.indexOf('transport') === -1
                                );
                                if (href) {
                                    tasks.push({
                                        tr: tr,
                                        kind: 'read',
                                        prio: isRoutineReturn ? 1 : 0,
                                        url: href
                                    });
                                }
                            }
                        }

                        tasks.sort(function(a, b) { return a.prio - b.prio; });
                        if (tasks.length === 0 || typeof window.fetch !== 'function') {
                            done({expanded: 0, fetches: 0});
                            return;
                        }

                        var idx = 0;
                        var expCount = 0;
                        var fetchCount = 0;
                        var resNames = {1: 'Pig iron', 2: 'Metal', 3: 'Kryptonite', 4: 'Spice'};

                        function isRateLimitedHtml(raw) {
                            var low = String(raw || '').toLowerCase();
                            if (low.indexOf('made more than') === -1 || low.indexOf('page views') === -1) return false;
                            if (/id=["']pageview-amount["'][^>]*>\\s*<\\/span>/i.test(raw)) return false;
                            return true;
                        }

                        function attachDiv(tr, text, extraAttrName, extraAttrVal) {
                            var cells = tr.querySelectorAll('td');
                            var host = cells.length > 0 ? cells[cells.length - 1] : tr;
                            var div = document.createElement('div');
                            div.className = 'si-expanded-body';
                            div.setAttribute('data-si-expanded', '1');
                            div.style.display = 'none';
                            div.textContent = ' ' + text + ' ';
                            host.appendChild(div);
                            if (extraAttrName && extraAttrVal) {
                                tr.setAttribute(extraAttrName, extraAttrVal);
                            }
                            tr.setAttribute('data-si-expanded', '1');
                        }

                        function step() {
                            if (idx >= tasks.length || fetchCount >= maxFetches) {
                                done({expanded: expCount, fetches: fetchCount});
                                return;
                            }
                            var t = tasks[idx++];
                            fetchCount++;
                            window.fetch(t.url, {credentials: 'same-origin'})
                                .then(function(resp) { return resp.text(); })
                                .then(function(raw) {
                                    if (!raw || isRateLimitedHtml(raw)) {
                                        fetchCount = maxFetches;
                                        return;
                                    }
                                    if (t.kind === 'battle') {
                                        try {
                                            var bj = JSON.parse(raw);
                                            if (bj && bj.ok) {
                                                var parts = ['Battle report #' + t.bid];
                                                if (bj.rounds !== undefined) parts.push('Rounds: ' + bj.rounds);
                                                if (bj.winner !== undefined) parts.push('Winner: ' + bj.winner);
                                                if (bj.attacker && bj.attacker.name) {
                                                    parts.push('Attacker: ' + bj.attacker.name + ' (Lost: ' + (bj.attacker.lost || 0) + ')');
                                                }
                                                if (bj.defender && bj.defender.name) {
                                                    parts.push('Defender: ' + bj.defender.name + ' (Lost: ' + (bj.defender.lost || 0) + ')');
                                                }
                                                if (Array.isArray(bj.stolen) && bj.stolen.length > 0) {
                                                    var sp = [];
                                                    for (var s = 0; s < bj.stolen.length; s++) {
                                                        var si = bj.stolen[s] || {};
                                                        var rn = resNames[si.r] || si.n || ('R' + si.r);
                                                        sp.push(rn + ': ' + (si.v || 0));
                                                    }
                                                    parts.push('Plundered ' + sp.join(' '));
                                                }
                                                if (Array.isArray(bj.rubble) && bj.rubble.length > 0) {
                                                    var rp = [];
                                                    for (var r = 0; r < bj.rubble.length; r++) {
                                                        var ri = bj.rubble[r] || {};
                                                        var rrn = resNames[ri.r] || ri.n || ('R' + ri.r);
                                                        rp.push(rrn + ': ' + (ri.v || 0));
                                                    }
                                                    parts.push('Debris field ' + rp.join(' '));
                                                }
                                                attachDiv(t.tr, parts.join(' | '), 'data-battle-json', raw.slice(0, 8000));
                                                expCount++;
                                            }
                                        } catch (e) {}
                                        return;
                                    }
                                    var parser = new DOMParser();
                                    var doc = parser.parseFromString(raw, 'text/html');
                                    var junk = doc.querySelectorAll(
                                        'script, style, #planet-coords, #planet-list, form#ress, #ress, ' +
                                        '#roheisen, #metall, #kryptonit, #spice, #tutorial, .tutorial, #speedup, .speedup, ' +
                                        'select[name="planet"], .messagebox, div[id^="msg-"], form[name="messages"]'
                                    );
                                    for (var j = 0; j < junk.length; j++) {
                                        if (t.kind === 'read' && junk[j].tagName === 'FORM') continue;
                                        if (junk[j].parentNode) junk[j].parentNode.removeChild(junk[j]);
                                    }
                                    var bodyEl = doc.body || doc.documentElement;
                                    if (t.kind === 'read') {
                                        var formEl = doc.querySelector('form[name="messages"]');
                                        if (formEl) {
                                            var cbs = formEl.querySelectorAll('input[type="checkbox"], select[name="todo"]');
                                            for (var k = 0; k < cbs.length; k++) {
                                                var rowP = cbs[k].closest ? cbs[k].closest('tr') : null;
                                                if (rowP && rowP.parentNode && !rowP.querySelector('.nachricht')) {
                                                    rowP.parentNode.removeChild(rowP);
                                                }
                                            }
                                        }
                                    }
                                    var cleanTxt = String((bodyEl && bodyEl.textContent) || '').replace(/\\s+/g, ' ').trim();
                                    if (t.kind === 'read' && cleanTxt) {
                                        var mInner = cleanTxt.match(/(?:Message\\s+from|Nachricht\\s+von)\\s+[^:]{1,60}:[\\s\\S]*?(?=\\s*['"]?\\s*(?:Fleet\\s+reports|Battle\\s+reports|Espionage|System|User|Alliance)\\s*[\\u2605*]|\\s*[\\u2605*]\\s*Favourites|\\s*Received\\s+on:\\s*From:|\\s*Empfangen\\s+am:|$)/i);
                                        if (mInner && mInner[0] && mInner[0].trim().length > 4) {
                                            cleanTxt = mInner[0].trim();
                                        }
                                    }
                                    if (cleanTxt.length > 4) {
                                        attachDiv(t.tr, cleanTxt.slice(0, 6000), '', '');
                                        expCount++;
                                    }
                                })
                                .catch(function() {})
                                .then(function() {
                                    if (idx < tasks.length && fetchCount < maxFetches) {
                                        setTimeout(step, 140);
                                    } else {
                                        done({expanded: expCount, fetches: fetchCount});
                                    }
                                });
                        }
                        step();
                        """,
                        sid_query,
                        cap_fetches,
                    )
                    if isinstance(ares, dict):
                        expanded += game_data.safe_int(ares.get("expanded"), 0)
                        fetches += game_data.safe_int(ares.get("fetches"), 0)
                except Exception:
                    pass

        if fetches > 0 and limiter is not None:
            with contextlib.suppress(Exception):
                with limiter._lock:
                    now_m = limiter._clock()
                    for _ in range(fetches):
                        limiter._views.append(now_m)
                    limiter._last_view_at = now_m

        if expanded <= 0:
            return None
        if fetches <= 0:
            time.sleep(0.25)
        with (drv_lock if drv_lock is not None else contextlib.nullcontext()):
            try:
                self._switch_frame("F_Hauptframe")
                return str(self.driver.page_source or "")
            except Exception:
                return None

    def delete_messages_on_current_page(
        self,
        server_msg_ids: Sequence[str] = (),
        *,
        all_on_page_persisted: bool = False,
        priority: str = "normal",
    ) -> tuple[bool, str]:
        """Delete already-persisted messages on `internalMessages` inside `F_Hauptframe`.

        Probes the live DOM before pacing so pages without delete controls incur zero extra views.
        """
        clean_ids = [
            str(mid).strip()
            for mid in (server_msg_ids or ())
            if str(mid).strip() and str(mid).strip().isascii()
        ]
        if not clean_ids and not all_on_page_persisted:
            return (False, "")

        probe_js = """
        var targetIds = arguments[0] || [];
        var allowAll = Boolean(arguments[1]);
        var idSet = {};
        for (var i = 0; i < targetIds.length; i++) idSet[String(targetIds[i])] = true;

        function isSelectAll(cb) {
            var nm = String(cb.name || '').trim().toLowerCase();
            var cid = String(cb.id || '').trim().toLowerCase();
            if (nm === 'check' || cid === 'check') return true;
            var blob = [
                nm, cid, cb.className || '',
                cb.getAttribute('onclick') || '', cb.getAttribute('onchange') || ''
            ].join(' ').toLowerCase();
            return (
                blob.indexOf('allmessages') !== -1 ||
                blob.indexOf('checkall') !== -1 ||
                blob.indexOf('selectall') !== -1 ||
                blob.indexOf('markall') !== -1 ||
                blob.indexOf('toggleall') !== -1 ||
                blob.indexOf('setall') !== -1
            );
        }

        function matchCbId(cb) {
            var v = String(cb.value || '').trim();
            if (v && idSet[v]) return true;
            var nm = String(cb.name || '');
            var cid = String(cb.id || '');
            for (var k in idSet) {
                if (!Object.prototype.hasOwnProperty.call(idSet, k)) continue;
                if (
                    nm.indexOf('[' + k + ']') !== -1 ||
                    nm === 'check_' + k || nm === 'check-' + k ||
                    nm === 'del_' + k || nm === 'msg_' + k ||
                    cid === 'check_' + k || cid === 'msg_' + k || cid === 'del_' + k || cid === k
                ) return true;
            }
            var row = cb.closest ? cb.closest('tr, div.message, div.msg, [data-msg-id], [data-id]') : null;
            if (row) {
                var dm = String(row.getAttribute('data-msg-id') || row.getAttribute('data-id') || row.getAttribute('data-bsum') || '');
                var rid = String(row.id || '').replace(/^(?:msg|message|row|tr_msg)[_-]?/i, '');
                if ((dm && idSet[dm]) || (rid && idSet[rid])) return true;
                var mfav = row.querySelector ? row.querySelector('[data-mfav], [data-bsum]') : null;
                if (mfav) {
                    var mf = String(mfav.getAttribute('data-mfav') || mfav.getAttribute('data-bsum') || '');
                    if (mf && idSet[mf]) return true;
                }
            }
            return false;
        }

        var cbs = Array.prototype.slice.call(document.querySelectorAll('input[type="checkbox"]'));
        var msgCbs = cbs.filter(function(cb) { return !isSelectAll(cb); });
        var matchedCbs = msgCbs.filter(matchCbId);
        if (matchedCbs.length === 0 && allowAll && msgCbs.length > 0) {
            matchedCbs = msgCbs;
        }
        if (matchedCbs.length > 0) {
            var form = matchedCbs[0].form || document.querySelector('form[action*="internalMessages"], form');
            if (form) return 'checkbox_form';
        }

        var links = Array.prototype.slice.call(
            document.querySelectorAll('a[href*="del"], a[onclick*="del"], button[onclick*="del"], input[onclick*="del"]')
        );
        for (var j = 0; j < links.length; j++) {
            var el = links[j];
            var h = String(el.getAttribute('href') || '') + ' ' + String(el.getAttribute('onclick') || '');
            for (var idKey in idSet) {
                if (Object.prototype.hasOwnProperty.call(idSet, idKey) && h.indexOf(idKey) !== -1) {
                    return 'direct_link';
                }
            }
        }
        if (allowAll) {
            for (var m = 0; m < links.length; m++) {
                var hl = (String(links[m].getAttribute('href') || '') + ' ' + String(links[m].textContent || '')).toLowerCase();
                if (hl.indexOf('delete all') !== -1 || hl.indexOf('alle l') !== -1 || hl.indexOf('del=all') !== -1 || hl.indexOf('deleteall') !== -1) {
                    return 'delete_all_link';
                }
            }
        }
        return '';
        """

        exec_delete_js = """
        var targetIds = arguments[0] || [];
        var allowAll = Boolean(arguments[1]);
        var mode = String(arguments[2] || '');
        var idSet = {};
        for (var i = 0; i < targetIds.length; i++) idSet[String(targetIds[i])] = true;

        function isSelectAll(cb) {
            var nm = String(cb.name || '').trim().toLowerCase();
            var cid = String(cb.id || '').trim().toLowerCase();
            if (nm === 'check' || cid === 'check') return true;
            var blob = [
                nm, cid, cb.className || '',
                cb.getAttribute('onclick') || '', cb.getAttribute('onchange') || ''
            ].join(' ').toLowerCase();
            return (
                blob.indexOf('allmessages') !== -1 ||
                blob.indexOf('checkall') !== -1 ||
                blob.indexOf('selectall') !== -1 ||
                blob.indexOf('markall') !== -1 ||
                blob.indexOf('toggleall') !== -1 ||
                blob.indexOf('setall') !== -1
            );
        }

        function matchCbId(cb) {
            var v = String(cb.value || '').trim();
            if (v && idSet[v]) return true;
            var nm = String(cb.name || '');
            var cid = String(cb.id || '');
            for (var k in idSet) {
                if (!Object.prototype.hasOwnProperty.call(idSet, k)) continue;
                if (
                    nm.indexOf('[' + k + ']') !== -1 ||
                    nm === 'check_' + k || nm === 'check-' + k ||
                    nm === 'del_' + k || nm === 'msg_' + k ||
                    cid === 'check_' + k || cid === 'msg_' + k || cid === 'del_' + k || cid === k
                ) return true;
            }
            var row = cb.closest ? cb.closest('tr, div.message, div.msg, [data-msg-id], [data-id]') : null;
            if (row) {
                var dm = String(row.getAttribute('data-msg-id') || row.getAttribute('data-id') || row.getAttribute('data-bsum') || '');
                var rid = String(row.id || '').replace(/^(?:msg|message|row|tr_msg)[_-]?/i, '');
                if ((dm && idSet[dm]) || (rid && idSet[rid])) return true;
                var mfav = row.querySelector ? row.querySelector('[data-mfav], [data-bsum]') : null;
                if (mfav) {
                    var mf = String(mfav.getAttribute('data-mfav') || mfav.getAttribute('data-bsum') || '');
                    if (mf && idSet[mf]) return true;
                }
            }
            return false;
        }

        if (mode === 'checkbox_form') {
            var cbs = Array.prototype.slice.call(document.querySelectorAll('input[type="checkbox"]'));
            var msgCbs = cbs.filter(function(cb) { return !isSelectAll(cb); });
            var matched = msgCbs.filter(matchCbId);
            if (matched.length === 0 && allowAll) matched = msgCbs;
            if (matched.length === 0) return false;
            for (var c = 0; c < matched.length; c++) {
                matched[c].checked = true;
            }
            var form = matched[0].form || document.querySelector('form[action*="internalMessages"], form');
            if (!form) return false;
            var selects = form.querySelectorAll('select');
            for (var s = 0; s < selects.length; s++) {
                var sel = selects[s];
                if ((sel.name || '').toLowerCase() === 'planet') continue;
                var pickedIdx = -1;
                for (var o = 0; o < sel.options.length; o++) {
                    var rawVal = String(sel.options[o].value || '').trim().toLowerCase();
                    var ov = (rawVal + ' ' + String(sel.options[o].text || '')).toLowerCase();
                    if (rawVal === 'deleted') {
                        pickedIdx = o;
                        break;
                    }
                    if (
                        (ov.indexOf('marked') !== -1 || ov.indexOf('selected') !== -1 || ov.indexOf('markiert') !== -1 || ov.indexOf('ausgew') !== -1) &&
                        (ov.indexOf('del') !== -1 || ov.indexOf('lösch') !== -1 || ov.indexOf('loesch') !== -1 || ov.indexOf('remove') !== -1)
                    ) {
                        pickedIdx = o;
                        break;
                    }
                }
                if (pickedIdx < 0) {
                    for (var o2 = 0; o2 < sel.options.length; o2++) {
                        var rv2 = String(sel.options[o2].value || '').trim().toLowerCase();
                        var ov2 = (rv2 + ' ' + String(sel.options[o2].text || '')).toLowerCase();
                        if (
                            (ov2.indexOf('del') !== -1 || ov2.indexOf('lösch') !== -1 || ov2.indexOf('loesch') !== -1) &&
                            ov2.indexOf('all') === -1 && ov2.indexOf('alle') === -1
                        ) {
                            pickedIdx = o2;
                            break;
                        }
                    }
                }
                if (pickedIdx < 0) {
                    for (var o3 = 0; o3 < sel.options.length; o3++) {
                        var ov3 = (sel.options[o3].value + ' ' + sel.options[o3].text).toLowerCase();
                        if (ov3.indexOf('marked') !== -1 || ov3.indexOf('selected') !== -1 || ov3.indexOf('markiert') !== -1 || ov3.indexOf('ausgew') !== -1) {
                            pickedIdx = o3;
                            break;
                        }
                    }
                }
                if (pickedIdx >= 0) sel.selectedIndex = pickedIdx;
            }
            var cands = Array.prototype.slice.call(
                form.querySelectorAll('input[type="submit"], button[type="submit"], input[type="button"], button')
            );
            var delBtn = null;
            for (var b = 0; b < cands.length; b++) {
                var bt = cands[b];
                var btxt = [
                    bt.name || '', bt.id || '', bt.value || '',
                    bt.getAttribute('onclick') || '', bt.textContent || ''
                ].join(' ').toLowerCase();
                if (
                    btxt.indexOf('del') !== -1 ||
                    btxt.indexOf('lösch') !== -1 ||
                    btxt.indexOf('loesch') !== -1 ||
                    btxt.indexOf('remove') !== -1 ||
                    btxt.indexOf('trash') !== -1
                ) {
                    delBtn = bt;
                    break;
                }
            }
            if (delBtn) { delBtn.click(); return true; }
            var nonReadSubmits = cands.filter(function(bt) {
                var tp = (bt.type || '').toLowerCase();
                if (tp !== 'submit') return false;
                var btxt = [bt.name || '', bt.value || '', bt.textContent || ''].join(' ').toLowerCase();
                return btxt.indexOf('read') === -1 && btxt.indexOf('gelesen') === -1 && btxt.indexOf('ungelesen') === -1;
            });
            if (nonReadSubmits.length > 0) { nonReadSubmits[0].click(); return true; }
            form.submit();
            return true;
        }

        var links = Array.prototype.slice.call(
            document.querySelectorAll('a[href*="del"], a[onclick*="del"], button[onclick*="del"], input[onclick*="del"]')
        );
        if (mode === 'direct_link') {
            for (var j = 0; j < links.length; j++) {
                var h = String(links[j].getAttribute('href') || '') + ' ' + String(links[j].getAttribute('onclick') || '');
                for (var idKey in idSet) {
                    if (Object.prototype.hasOwnProperty.call(idSet, idKey) && h.indexOf(idKey) !== -1) {
                        links[j].click();
                        return true;
                    }
                }
            }
        }
        if (mode === 'delete_all_link' && allowAll) {
            for (var m = 0; m < links.length; m++) {
                var hl = (String(links[m].getAttribute('href') || '') + ' ' + String(links[m].textContent || '')).toLowerCase();
                if (hl.indexOf('delete all') !== -1 || hl.indexOf('alle l') !== -1 || hl.indexOf('del=all') !== -1 || hl.indexOf('deleteall') !== -1) {
                    links[m].click();
                    return true;
                }
            }
        }
        return false;
        """

        drv_lock = getattr(self, "_driver_lock", None)
        mode_found = ""
        current_html = ""
        with (drv_lock if drv_lock is not None else contextlib.nullcontext()):
            try:
                self._switch_frame("F_Hauptframe")
                current_html = str(self.driver.page_source or "")
                res_mode = self.driver.execute_script(probe_js, clean_ids, bool(all_on_page_persisted))
                if isinstance(res_mode, str):
                    mode_found = res_mode
            except Exception:
                return (False, current_html)

        if not mode_found:
            return (False, current_html)

        self._pace(priority=priority)
        nav_token = self._mark_frame_navigation("F_Hauptframe")
        triggered = False
        with (drv_lock if drv_lock is not None else contextlib.nullcontext()):
            try:
                self._switch_frame("F_Hauptframe")
                triggered = bool(
                    self.driver.execute_script(
                        exec_delete_js,
                        clean_ids,
                        bool(all_on_page_persisted),
                        mode_found,
                    )
                )
            except Exception:
                triggered = False
        if not triggered:
            return (False, current_html)
        try:
            new_html = self._wait_frame_ready(
                "F_Hauptframe",
                expected_action="internalMessages",
                nav_token=nav_token,
            )
            return (True, new_html)
        except (RateLimitError, AntiBotChallengeError):
            raise
        except Exception:
            with (drv_lock if drv_lock is not None else contextlib.nullcontext()):
                try:
                    self._switch_frame("F_Hauptframe")
                    return (True, str(self.driver.page_source or ""))
                except Exception:
                    return (True, current_html)

    def read_planets_overview(self, *, refresh: bool = False, priority: str = "normal") -> GameState:
        """Read `#json-container` from `F_planets`, optionally reloading the iframe first."""
        drv_lock = getattr(self, "_driver_lock", None)
        json_text = ""
        missing_html = ""
        missing_exc: Optional[NoSuchElementException] = None
        need_reload = False
        frame_src = ""
        wrong_frame_page = False

        with (drv_lock if drv_lock is not None else contextlib.nullcontext()):
            self.driver.switch_to.default_content()
            planets_frame = self.driver.find_element(By.ID, "F_planets")
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
            need_reload = bool(refresh or wrong_frame_page)
            if not need_reload:
                try:
                    jc = self.driver.find_element(By.ID, "json-container")
                    json_text = jc.get_attribute("textContent") or ""
                    self._dom_healthy_epoch = getattr(self, "_dom_healthy_epoch", 0) + 1
                except NoSuchElementException as exc:
                    missing_exc = exc
                    missing_html = self.driver.page_source or ""

        if need_reload:
            self._pace(priority=priority)
            with (drv_lock if drv_lock is not None else contextlib.nullcontext()):
                self._switch_frame("F_planets")
                if wrong_frame_page:
                    self.driver.execute_script(
                        "window.__siReloadPending = 1; location.replace(arguments[0]);",
                        frame_src,
                    )
                else:
                    self.driver.execute_script("window.__siReloadPending = 1; location.reload();")
            wait_budget = _safe_positive_timeout(
                getattr(self, "timeout", 20.0), 12.0, min_val=0.05, max_val=12.0
            )
            deadline = time.monotonic() + wait_budget
            while True:
                html_to_check: Optional[str] = None
                ready_with_json = False
                with (drv_lock if drv_lock is not None else contextlib.nullcontext()):
                    self._switch_frame("F_planets")
                    pending = self.driver.execute_script("return Boolean(window.__siReloadPending);")
                    ready = self.driver.execute_script("return document.readyState;")
                    if not pending and ready == "complete":
                        html_to_check = self.driver.page_source or ""
                        ready_with_json = bool(
                            self.driver.execute_script(
                                "return Boolean(document.getElementById('json-container'));"
                            )
                        )
                if html_to_check is not None:
                    self._check_page_guards(html_to_check)
                    html_low = html_to_check.lower()
                    if "bitmeup-login" in html_low or "sip-tab-universe" in html_low:
                        raise SpaceInvasionAuthError(
                            "Browser redirected to BitMeUp portal/login while reading planets overview."
                        )
                    if ready_with_json:
                        break
                if time.monotonic() >= deadline:
                    break
                time.sleep(0.2)

            with (drv_lock if drv_lock is not None else contextlib.nullcontext()):
                self._switch_frame("F_planets")
                try:
                    jc = self.driver.find_element(By.ID, "json-container")
                    json_text = jc.get_attribute("textContent") or ""
                    self._dom_healthy_epoch = getattr(self, "_dom_healthy_epoch", 0) + 1
                except NoSuchElementException as exc:
                    missing_exc = exc
                    missing_html = self.driver.page_source or ""

        if missing_exc is not None:
            self._check_page_guards(missing_html)
            missing_low = missing_html.lower()
            if "bitmeup-login" in missing_low or "sip-tab-universe" in missing_low:
                raise SpaceInvasionAuthError(
                    "Browser redirected to BitMeUp portal/login while reading planets overview."
                ) from missing_exc
            raise SpaceInvasionAuthError(
                "SpaceInvasion session expired (missing #json-container in F_planets)."
            ) from missing_exc
        return parse_planets_overview_json(json_text)

    def read_menu_status(self) -> tuple[dict[str, int], Optional[int], Optional[int]]:
        """Read unread message counters, server points, and rank from `F_Navi`."""
        drv_lock = getattr(self, "_driver_lock", None)
        with (drv_lock if drv_lock is not None else contextlib.nullcontext()):
            self._switch_frame("F_Navi")
            html = self.driver.page_source or ""
        return parse_menu_status(html)

    def read_unread_messages(self) -> dict[str, int]:
        """Read unread message category counters from `F_Navi`."""
        drv_lock = getattr(self, "_driver_lock", None)
        with (drv_lock if drv_lock is not None else contextlib.nullcontext()):
            self._switch_frame("F_Navi")
            html = self.driver.page_source or ""
        return parse_menu_messages(html)

    @staticmethod
    def _extract_selected_planet_id(html: str, url: str = "") -> Optional[int]:
        if not html or "internalfleet" in (url or "").lower() or "newplanet=" not in html.lower():
            return None
        soup = BeautifulSoup(html, "html.parser")
        select_el = soup.find("select", attrs={"name": "planet"})
        if select_el is None:
            return None
        for opt in select_el.find_all("option"):
            val = str(opt.get("value") or "")
            if "action=internalfleet" in val.lower():
                return None
            if opt.has_attr("selected"):
                m = re.search(r"newplanet=(\d+)", val, re.IGNORECASE | re.ASCII)
                if m:
                    pid_s = m.group(1)
                    if pid_s.isascii() and pid_s.isdigit() and len(pid_s) <= 18:
                        return int(pid_s)
                    return None
        return None

    def read_economy_rates(self, *, priority: str = "normal") -> dict[str, int]:
        """Navigate to `internalEconomy` and extract active planet `resource1`..`resource6` rates."""
        html = self.navigate_menu("internalEconomy", priority=priority)
        return parse_economy_rates(html)

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
        """Collect an empire snapshot across `F_planets`, `F_Navi`, and `F_Hauptframe`."""
        _call_with_optional_priority(self.ensure_in_game, priority=priority)

        state = self.read_planets_overview(refresh=True, priority=priority)
        unread, account_points, account_rank = self.read_menu_status()

        # Check current main frame URL; navigate to internalHome if on another view or stale throttle/login DOM
        drv_lock = getattr(self, "_driver_lock", None)
        with (drv_lock if drv_lock is not None else contextlib.nullcontext()):
            self._switch_frame("F_Hauptframe")
            main_url = str(self.driver.execute_script("return location.href;") or "")
            raw_main_html = str(self.driver.page_source or "")

        home_html = ""
        raw_main_low = raw_main_html.lower()
        has_portal_redirect = (
            "bitmeup-login" in raw_main_low
            or "sip-tab-universe" in raw_main_low
        )
        if "action=internalHome" in main_url:
            is_stale_home = (
                not raw_main_low.strip()
                or is_server_rate_limit_html(raw_main_html)
                or bool(detect_antibot_challenge(200, raw_main_html))
                or has_portal_redirect
            )
            if is_stale_home:
                if include_home:
                    try:
                        home_html = self.navigate_menu("internalHome", priority=priority)
                    except NoSuchElementException:
                        sid_query = self._resolve_sid_query()
                        nav_token = self._mark_frame_navigation("F_Hauptframe")
                        if sid_query:
                            target_url = self._build_internal_url(f"action=internalHome&{sid_query}")
                            self.driver.execute_script("location.href = arguments[0];", target_url)
                        else:
                            self.driver.execute_script("window.__siReloadPending = 1; location.reload();")
                        home_html = self._wait_frame_ready(
                            "F_Hauptframe",
                            expected_action="internalHome",
                            nav_token=nav_token,
                        )
                elif raw_main_html.strip():
                    self._check_page_guards(raw_main_html)
                    if has_portal_redirect:
                        raise SpaceInvasionAuthError(
                            "Browser redirected to BitMeUp portal/login while reading game snapshot."
                        )
            else:
                self._check_page_guards(raw_main_html)
                home_html = raw_main_html
        elif include_home:
            home_html = self.navigate_menu("internalHome", priority=priority)
        elif raw_main_html.strip():
            self._check_page_guards(raw_main_html)
            if has_portal_redirect:
                raise SpaceInvasionAuthError(
                    "Browser redirected to BitMeUp portal/login while reading game snapshot."
                )

        if home_html:
            tutorial = parse_tutorial_from_home(home_html)
            active_pid, speedup, newbie, queues, alerts = parse_home_metadata(home_html)
            self.last_home_daily_bonus_present = bool(
                parse_daily_login_bonus(home_html).get("present", False)
            )
        else:
            tutorial = None
            active_pid = self._extract_selected_planet_id(raw_main_html, main_url)
            speedup, newbie, queues, alerts = None, False, (), ()
            self.last_home_daily_bonus_present = None

        merged_queues = list(queues)
        research_offers = state.research_offers

        planets = list(state.planets)
        target_pid = active_pid or (planets[0].planet_id if planets else None)
        active_idx = next(
            (i for i, p in enumerate(planets) if p.planet_id == target_pid),
            0 if planets else None,
        )

        b_html = ""
        if include_buildings and planets and active_idx is not None:
            can_reuse_b = (
                not include_home
                and getattr(self.driver, "__class__", object).__module__.startswith("selenium")
                and "action=internalBuildings" in main_url
                and "internalbuilditem" not in main_url.lower()
                and bool(raw_main_html.strip())
                and not is_server_rate_limit_html(raw_main_html)
                and not has_portal_redirect
            )
            b_html = raw_main_html if can_reuse_b else self.navigate_menu("internalBuildings", priority=priority)
            if active_pid is None:
                sel_pid = self._extract_selected_planet_id(b_html, "internalBuildings")
                if sel_pid is not None:
                    active_pid = sel_pid
                    target_pid = sel_pid
                    active_idx = next(
                        (i for i, p in enumerate(planets) if p.planet_id == target_pid),
                        active_idx,
                    )
            if tutorial is None:
                tutorial = parse_tutorial_from_home(b_html)
            b_queues = parse_building_queue(b_html)
            if b_queues:
                merged_queues = [q for q in merged_queues if q.category != "building"] + list(b_queues)
            planet = planets[active_idx]
            offers = parse_buildings_page(b_html, planet.buildings)
            planets[active_idx] = replace(planet, building_offers=offers)

        # Passively detect Metal production enabled/disabled from `<form id="ress">` ticker on zero extra views
        if active_idx is not None and planets:
            cur_ap = planets[active_idx]
            if (
                normalize_planet_type(cur_ap) not in ("asteroid", "raid")
                and cur_ap.building_level(2) >= 1
            ):
                ticker_src = b_html or home_html
                if not ticker_src and raw_main_html:
                    raw_sel = self._extract_selected_planet_id(raw_main_html, main_url)
                    if raw_sel is None or raw_sel == cur_ap.planet_id:
                        ticker_src = raw_main_html
                ticker = parse_ticker_rates(ticker_src)
                if "metal" in ticker:
                    econ = dict(cur_ap.economy_rates or {})
                    metal_storage_full = (
                        cur_ap.storage.metal > 0
                        and cur_ap.resources.metal >= cur_ap.storage.metal
                    )
                    has_pig_feed = (
                        cur_ap.resources.pig_iron > 0
                        or ticker.get("pig_iron", 0.0) > 0.0
                        or cur_ap.building_level(1) >= 1
                    )
                    if (
                        ticker["metal"] <= 0.0
                        and cur_ap.energy.produced > 0
                        and not metal_storage_full
                        and has_pig_feed
                    ):
                        if econ.get("resource2") != 0:
                            econ["resource2"] = 0
                            planets[active_idx] = replace(cur_ap, economy_rates=econ)
                    elif ticker["metal"] > 0.0 and econ.get("resource2") == 0:
                        econ["resource2"] = 100
                        planets[active_idx] = replace(cur_ap, economy_rates=econ)

        # Optionally fetch exact `internalEconomy` dropdown rates (`resource1`..`resource6`)
        if (
            include_economy
            and active_idx is not None
            and planets
            and normalize_planet_type(planets[active_idx]) not in ("asteroid", "raid")
        ):
            e_html = self.navigate_menu("internalEconomy", priority=priority)
            if active_pid is None:
                sel_pid = self._extract_selected_planet_id(e_html, "internalEconomy")
                if sel_pid is not None:
                    active_pid = sel_pid
                    target_pid = sel_pid
                    active_idx = next(
                        (i for i, p in enumerate(planets) if p.planet_id == target_pid),
                        active_idx,
                    )
            econ_rates = parse_economy_rates(e_html)
            if econ_rates:
                planets[active_idx] = replace(planets[active_idx], economy_rates=econ_rates)

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
            r_html = self.navigate_menu("internalResearch", priority=priority)
            if active_pid is None:
                sel_pid = self._extract_selected_planet_id(r_html, "internalResearch")
                if sel_pid is not None:
                    active_pid = sel_pid
                    target_pid = sel_pid
                    active_idx = next(
                        (i for i, p in enumerate(planets) if p.planet_id == target_pid),
                        active_idx,
                    )
                    active_p = planets[active_idx]
            research_offers = parse_research_page(r_html, state.researches)
            active_res = [o for o in research_offers if o.active]
            if active_res:
                r_rem_txt, r_rem_s, r_fin_ts, r_det = parse_research_queue_timer(r_html)
                prev_home_res = next((q for q in merged_queues if q.category == "research"), None)
                if r_fin_ts is None and prev_home_res is not None:
                    r_rem_txt = r_rem_txt or prev_home_res.remaining_text
                    r_rem_s = prev_home_res.remaining_seconds
                    r_fin_ts = prev_home_res.finish_ts
                    r_det = r_det or prev_home_res.detail
                res_jobs = [
                    QueueJob(
                        category="research",
                        name=o.name,
                        target_level=o.target_level,
                        remaining_text=r_rem_txt,
                        remaining_seconds=r_rem_s,
                        finish_ts=r_fin_ts,
                        queue_slot=1,
                        detail=r_det or "Researching",
                    )
                    for o in active_res
                ]
                merged_queues = [q for q in merged_queues if q.category != "research"] + res_jobs
            elif research_offers:
                merged_queues = [q for q in merged_queues if q.category != "research"]

        # Fetch Weapons factory offers when Weapons factory L1+ exists
        if include_shipyard and active_p is not None and active_p.building_level(9) >= 1:
            wf_html = self.navigate_menu("internalWeaponFactory", priority=priority)
            if active_pid is None:
                sel_pid = self._extract_selected_planet_id(wf_html, "internalWeaponFactory")
                if sel_pid is not None:
                    active_pid = sel_pid
                    target_pid = sel_pid
                    active_idx = next(
                        (i for i, p in enumerate(planets) if p.planet_id == target_pid),
                        active_idx,
                    )
                    active_p = planets[active_idx]
            ship_offers = parse_unit_factory_page(
                wf_html,
                unit_type="ship",
                owned_counts=active_p.ships,
            )
            planets[active_idx] = replace(planets[active_idx], ship_offers=ship_offers)

        if return_home and (include_buildings or include_research or include_shipyard or include_economy):
            self.navigate_menu("internalHome", priority=priority)

        banner_unread = parse_unread_banner_count(b_html or home_html or raw_main_html)
        if banner_unread > 0:
            unread = dict(unread)
            unread["total"] = max(unread.get("total", 0), banner_unread)

        self.last_snapshot_observed_active_planet = active_pid is not None
        with (drv_lock if drv_lock is not None else contextlib.nullcontext()):
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
        drv_lock = getattr(self, "_driver_lock", None)
        with (drv_lock if drv_lock is not None else contextlib.nullcontext()):
            html = str(getattr(self.driver, "page_source", "") or "")
        self._check_page_guards(html)
        return html
