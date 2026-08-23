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
from typing import Any, Callable, Deque, Optional
from urllib.parse import urljoin

from bs4 import BeautifulSoup
from selenium.common.exceptions import (
    ElementClickInterceptedException,
    NoSuchElementException,
    StaleElementReferenceException,
)
from selenium.webdriver.common.by import By

from spaceinvasion import game_data
from spaceinvasion.auth import AntiBotChallengeError, SpaceInvasionAuthError, detect_antibot_challenge
from spaceinvasion.browser import (
    DEFAULT_DEBUG_PORT,
    attach_chrome_driver,
    close_or_detach_driver,
    find_and_switch_game_tab,
    is_debug_port_open,
)
from spaceinvasion.fleet import extract_galaxy_system_coords
from spaceinvasion.state import (
    GameState,
    QueueJob,
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


class GameBrowserClient:
    """Read-only / player-like browser client operating inside the 3-iframe game shell."""

    page_limiter: PageViewLimiter = _DEFAULT_PAGE_VIEW_LIMITER
    max_limiter_wait: Optional[float] = None
    last_galaxy_coords: Optional[tuple[int, int]] = None
    last_snapshot_observed_active_planet: bool = True

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
        self.last_snapshot_observed_active_planet: bool = True
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

    def _check_page_guards(self, html: str) -> None:
        issue = detect_antibot_challenge(200, html)
        if issue:
            raise AntiBotChallengeError(
                f"{issue}. Stopping automation; handle verification in Chrome."
            )
        low = html.lower()
        if "made more than" in low and "page views" in low:
            cooldown = self.page_limiter.record_rate_limit()
            err = RateLimitError(
                f"SpaceInvasion per-minute page-view throttle hit; cooldown active ({cooldown:.1f}s remaining; retry after {cooldown:.1f}s).",
                retry_after=cooldown,
                server_throttle=True,
            )
            setattr(err, "_recorded_limiter_id", id(self.page_limiter))
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
            missing_exc: Optional[NoSuchElementException] = None
            dom_epoch_seen = getattr(self, "_dom_healthy_epoch", 0)
            with (drv_lock if drv_lock is not None else contextlib.nullcontext()):
                self.driver.switch_to.default_content()
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

            if missing_exc is None:
                self.page_limiter.clear_throttle_recovery()
                return

            if frame_html:
                issue = detect_antibot_challenge(200, frame_html)
                if issue:
                    raise AntiBotChallengeError(
                        f"{issue}. Stopping automation; handle verification in Chrome."
                    ) from missing_exc
                low_frame = frame_html.lower()
                if "made more than" in low_frame and "page views" in low_frame:
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
        while True:
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
            except Exception:
                probe = None
            if isinstance(probe, dict):
                ready = probe.get("ready")
                href = str(probe.get("href") or "")
                cur_token = probe.get("token")
            else:
                ready = self.driver.execute_script("return document.readyState;")
                href = str(self.driver.execute_script("return location.href;") or "")
                cur_token = None

            if ready == "complete" and (nav_token is None or cur_token != nav_token):
                html = self.driver.page_source or ""
                self._check_page_guards(html)
                html_low = html.lower()
                if "bitmeup-login" in html_low or "sip-tab-universe" in html_low:
                    raise SpaceInvasionAuthError(
                        "Browser redirected to BitMeUp portal/login while waiting for game frame."
                    )
                if not expected_action or expected_action in href:
                    return html

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

    def navigate_menu(self, action: str, *, priority: str = "normal") -> str:
        """Click a navigation link in `F_Navi` and wait for `F_Hauptframe` to load."""
        if action == "internalGalaxy":
            return self.open_galaxy(priority=priority)
        self._pace(priority=priority)
        nav_token = self._mark_frame_navigation("F_Hauptframe")
        self._switch_frame("F_Navi")
        link = self.driver.find_element(By.CSS_SELECTOR, f'a[href*="action={action}"]')
        self.driver.execute_script("arguments[0].scrollIntoView({block: 'center'});", link)
        try:
            link.click()
        except ElementClickInterceptedException:
            self.driver.execute_script("arguments[0].click();", link)
        return self._wait_frame_ready("F_Hauptframe", expected_action=action, nav_token=nav_token)

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
        for name in ("galaxy", "gal"):
            try:
                els = self.driver.find_elements(By.NAME, name)
                if els:
                    gal_inp = els[0]
                    break
            except Exception:
                pass
        for name in ("system", "sun"):
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
                'form:has(input[name="system"]) input[type="submit"]',
                'form:has(input[name="system"]) button[type="submit"]',
                'form:has(input[name="sun"]) input[type="submit"]',
                'form:has(input[name="sun"]) button[type="submit"]',
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
            params = ["action=internalGalaxy", sid_query]
            if gal_arg is not None:
                params.append(f"galaxy={gal_arg}")
            if sys_arg is not None:
                params.append(f"system={sys_arg}")
            target_url = self._build_internal_url("&".join(params))
            nav_token = self._mark_frame_navigation("F_Hauptframe")
            self.driver.execute_script("location.href = arguments[0];", target_url)
            html = self._wait_frame_ready("F_Hauptframe", expected_action="internalGalaxy", nav_token=nav_token)
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

        self._switch_frame("F_Hauptframe")
        try:
            cur_url = str(self.driver.execute_script("return location.href;") or "")
        except Exception:
            cur_url = ""
        fallback_gal = gal_arg if gal_arg is not None else 1
        fallback_sys = sys_arg if sys_arg is not None else 1
        actual_gal, actual_sys = extract_galaxy_system_coords(
            html,
            url=cur_url,
            default=(fallback_gal, fallback_sys),
        )

        want_gal = gal_arg if gal_arg is not None else actual_gal
        want_sys = sys_arg if sys_arg is not None else actual_sys
        if (galaxy is not None or system is not None) and (actual_gal != want_gal or actual_sys != want_sys):
            switched_html = self._submit_galaxy_coords_form(
                galaxy=galaxy,
                system=system,
                priority=priority,
            )
            if switched_html is not None:
                html = switched_html
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

    def open_messages(
        self,
        msg_type: str = "spio",
        *,
        priority: str = "interactive",
    ) -> str:
        """Open `internalMessages` (e.g. `type=spio` espionage reports) in `F_Hauptframe`."""
        clean_type = re.sub(r"[^a-zA-Z0-9_-]", "", str(msg_type or "spio")) or "spio"
        self._pace(priority=priority)
        sid_query = self._resolve_sid_query()
        if sid_query:
            target_url = self._build_internal_url(
                f"action=internalMessages&type={clean_type}&{sid_query}"
            )
            nav_token = self._mark_frame_navigation("F_Hauptframe")
            self.driver.execute_script("location.href = arguments[0];", target_url)
            return self._wait_frame_ready(
                "F_Hauptframe",
                expected_action="internalMessages",
                nav_token=nav_token,
            )

        nav_token = self._mark_frame_navigation("F_Hauptframe")
        self._switch_frame("F_Navi")
        link = None
        for css in (
            f'a[href*="action=internalMessages"][href*="type={clean_type}"]',
            'a[href*="action=internalMessages"]',
        ):
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
        return self._wait_frame_ready(
            "F_Hauptframe",
            expected_action="internalMessages",
            nav_token=nav_token,
        )

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

    def read_full_snapshot(
        self,
        *,
        include_buildings: bool = True,
        include_research: bool = True,
        include_shipyard: bool = True,
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
                or ("made more than" in raw_main_low and "page views" in raw_main_low)
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
        else:
            tutorial = None
            active_pid = self._extract_selected_planet_id(raw_main_html, main_url)
            speedup, newbie, queues, alerts = None, False, (), ()

        merged_queues = list(queues)
        research_offers = state.research_offers

        planets = list(state.planets)
        target_pid = active_pid or (planets[0].planet_id if planets else None)
        active_idx = next(
            (i for i, p in enumerate(planets) if p.planet_id == target_pid),
            0 if planets else None,
        )

        if include_buildings and planets and active_idx is not None:
            b_html = self.navigate_menu("internalBuildings", priority=priority)
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
                res_jobs = [
                    QueueJob(category="research", name=o.name, target_level=o.target_level)
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

        if return_home and (include_buildings or include_research or include_shipyard):
            self.navigate_menu("internalHome", priority=priority)

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
