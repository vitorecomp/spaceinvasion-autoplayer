"""Thread-safe sliding-window page-view rate limiter and server cooldown gate (stdlib only)."""

from __future__ import annotations

from collections import deque
from collections.abc import Callable
import math
import random
import re
import threading
import time

DEFAULT_MIN_NAV_DELAY: tuple[float, float] = (0.9, 1.8)
DEFAULT_MAX_VIEWS_PER_MINUTE: int = 24
DEFAULT_RESERVED_INTERACTIVE_VIEWS: int = 6
DEFAULT_RATE_LIMIT_COOLDOWN_SECONDS: float = 45.0
PAGE_VIEW_WINDOW_SECONDS: float = 60.0

_EMPTY_PAGEVIEW_MODAL_RE = re.compile(
    r"made\s+more\s+than\s*<span\b[^>]*\bid=[\"']?pageview-amount[\"']?[^>]*>"
    r"(?:\s|&nbsp;|&#160;|0|-)*</span>\s*page\s+views",
    re.IGNORECASE,
)


def _parse_optional_float(val: object) -> float | None:
    if isinstance(val, bool) or not isinstance(val, (int, float, str, bytes, bytearray)):
        return None
    if isinstance(val, (int, float)):
        parsed = float(val)
        return parsed if math.isfinite(parsed) else None
    text = bytes(val).decode("utf-8", errors="replace") if isinstance(val, (bytes, bytearray)) else val
    s = text.strip()
    if not s or not s.isascii():
        return None
    try:
        parsed = float(s)
    except ValueError:
        return None
    return parsed if math.isfinite(parsed) else None


def _safe_bounded_float(val: object, default: float, *, min_val: float = 0.05, max_val: float = 300.0) -> float:
    base = min_val if not math.isfinite(default) else max(min_val, min(max_val, float(default)))
    parsed = _parse_optional_float(val)
    return base if parsed is None else max(min_val, min(max_val, parsed))


def _safe_clamp_cooldown(val: object, default: float) -> float:
    safe_def = default if (math.isfinite(default) and default > 0.0) else DEFAULT_RATE_LIMIT_COOLDOWN_SECONDS
    return _safe_bounded_float(val, safe_def, min_val=1.0, max_val=3600.0)


def _safe_clamped_int(val: object, default: int, *, min_val: int, max_val: int) -> int:
    safe_def = max(min_val, min(max_val, default))
    if isinstance(val, bool) or not isinstance(val, (int, float, str, bytes, bytearray)):
        return safe_def
    if isinstance(val, int):
        return max(min_val, min(max_val, val))
    parsed = _parse_optional_float(val)
    return safe_def if parsed is None else max(min_val, min(max_val, int(parsed)))


class RateLimitError(RuntimeError):
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
        self.recorded_limiter_id: int | None = None


class PageViewLimiter:
    """Thread-safe process-wide page-view pacer, rolling 60s budget, and cooldown gate."""

    def __init__(
        self,
        *,
        min_delay_range: tuple[float, float] = DEFAULT_MIN_NAV_DELAY,
        max_views_per_minute: int = DEFAULT_MAX_VIEWS_PER_MINUTE,
        reserved_interactive_views: int = DEFAULT_RESERVED_INTERACTIVE_VIEWS,
        interactive_reserve: int | None = None,
        default_cooldown_seconds: float = DEFAULT_RATE_LIMIT_COOLDOWN_SECONDS,
        window_seconds: float = PAGE_VIEW_WINDOW_SECONDS,
        clock: Callable[[], float] = time.monotonic,
        sleeper: Callable[[float], None] = time.sleep,
        rng: Callable[[float, float], float] = random.uniform,
    ) -> None:
        self.min_delay_range = min_delay_range
        self.max_views_per_minute = _safe_clamped_int(
            max_views_per_minute, DEFAULT_MAX_VIEWS_PER_MINUTE, min_val=1, max_val=100_000
        )
        reserve = reserved_interactive_views if interactive_reserve is None else interactive_reserve
        self.reserved_interactive_views = _safe_clamped_int(
            reserve, DEFAULT_RESERVED_INTERACTIVE_VIEWS, min_val=0, max_val=100_000
        )
        self.default_cooldown_seconds = _safe_clamp_cooldown(
            default_cooldown_seconds, DEFAULT_RATE_LIMIT_COOLDOWN_SECONDS
        )
        self.window_seconds = _safe_bounded_float(window_seconds, PAGE_VIEW_WINDOW_SECONDS, min_val=1.0, max_val=3600.0)
        self._clock = clock
        self._sleeper = sleeper
        self._rng = rng
        self._lock = threading.Lock()
        self._recovery_cond = threading.Condition(self._lock)
        self._views: deque[float] = deque()
        self._last_view_at: float | None = None
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

    def configure(
        self,
        *,
        max_views_per_minute: int | None = None,
        min_delay_range: tuple[float, float] | None = None,
        reserved_interactive_views: int | None = None,
    ) -> None:
        with self._lock:
            if max_views_per_minute is not None:
                self.max_views_per_minute = _safe_clamped_int(
                    max_views_per_minute, DEFAULT_MAX_VIEWS_PER_MINUTE, min_val=1, max_val=100_000
                )
            if min_delay_range is not None:
                self.min_delay_range = min_delay_range
            if reserved_interactive_views is not None:
                self.reserved_interactive_views = _safe_clamped_int(
                    reserved_interactive_views, DEFAULT_RESERVED_INTERACTIVE_VIEWS, min_val=0, max_val=100_000
                )

    def _prune_locked(self, now: float) -> None:
        cutoff = now - self.window_seconds
        while self._views and self._views[0] <= cutoff:
            self._views.popleft()

    def _effective_cap(self, priority: str) -> int:
        if str(priority or "normal").strip().lower() == "interactive":
            return self.max_views_per_minute
        reserved = min(self.reserved_interactive_views, max(0, self.max_views_per_minute - 1))
        return max(1, self.max_views_per_minute - reserved)

    def _apply_throttle_locked(self, now: float, dur: float) -> float:
        self._rate_limit_until = max(self._rate_limit_until, now + dur)
        self._rate_limit_hits += 1
        self._throttle_recovery_pending = True
        self._throttle_recovery_generation += 1
        self._recovery_cond.notify_all()
        return max(1.0, self._rate_limit_until - now)

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

    def record_rate_limit(self, retry_after: float | None = None) -> float:
        dur = _safe_clamp_cooldown(retry_after, self.default_cooldown_seconds)
        with self._recovery_cond:
            return self._apply_throttle_locked(self._clock(), dur)

    def recovery_generation(self) -> int:
        with self._lock:
            return int(self._throttle_recovery_generation)

    def record_rate_limit_if_generation(
        self, expected_generation: int, retry_after: float | None = None
    ) -> tuple[bool, float]:
        dur = _safe_clamp_cooldown(retry_after, self.default_cooldown_seconds)
        with self._recovery_cond:
            now = self._clock()
            cd_rem = max(0.0, self._rate_limit_until - now)
            if self._throttle_recovery_generation != expected_generation or cd_rem > 0.0:
                return (False, cd_rem)
            return (True, self._apply_throttle_locked(now, dur))

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

    def _wait_deadline(self, wait_timeout: float | None) -> float | None:
        if wait_timeout is None:
            return None
        return self._clock() + _safe_bounded_float(wait_timeout, 15.0, min_val=0.0, max_val=300.0)

    def _cond_wait_until(self, deadline: float | None) -> bool:
        if deadline is None:
            self._recovery_cond.wait()
            return True
        rem = deadline - self._clock()
        if rem <= 0.0:
            return False
        self._recovery_cond.wait(timeout=rem)
        return True

    def wait_if_throttle_recovery_in_flight(self, *, wait_timeout: float | None = 15.0) -> str:
        deadline = self._wait_deadline(wait_timeout)
        with self._recovery_cond:
            if not self._throttle_recovery_in_flight:
                return "idle"
            start_gen = self._throttle_recovery_generation
            while self._throttle_recovery_in_flight and self._throttle_recovery_generation == start_gen:
                if self._rate_limit_until > self._clock():
                    return "cooldown"
                if not self._cond_wait_until(deadline):
                    return "timeout"
            return "cooldown" if self._rate_limit_until > self._clock() else "waited"

    def begin_or_wait_throttle_recovery(self, *, wait_timeout: float | None = 15.0) -> tuple[str, int]:
        deadline = self._wait_deadline(wait_timeout)
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
                if not self._cond_wait_until(deadline):
                    return ("timeout", start_gen)
                if not self._throttle_recovery_in_flight or self._throttle_recovery_generation != start_gen:
                    state = "cooldown" if self._rate_limit_until > self._clock() else "waited"
                    return (state, self._throttle_recovery_generation)

    def finish_throttle_recovery(self, token: int, *, recovered: bool, rearm: bool = False) -> None:
        with self._recovery_cond:
            if self._throttle_recovery_token != token:
                return
            self._throttle_recovery_in_flight = False
            self._throttle_recovery_pending = False if recovered else bool(rearm)
            self._throttle_recovery_generation += 1
            self._recovery_cond.notify_all()

    def _compute_target_gap(self, delay_range: tuple[float, float] | None) -> float:
        low, high = delay_range if delay_range is not None else self.min_delay_range
        safe_low = _safe_bounded_float(low, 0.0, min_val=0.0, max_val=60.0)
        safe_high = _safe_bounded_float(high, 0.0, min_val=0.0, max_val=60.0)
        if safe_high <= 0.0:
            return 0.0
        return safe_low if safe_low >= safe_high else self._rng(safe_low, safe_high)

    def acquire(
        self,
        cost: int = 1,
        priority: str = "normal",
        *,
        delay_range: tuple[float, float] | None = None,
        max_wait: float | None = None,
        record: bool = True,
    ) -> float:
        n = _safe_clamped_int(cost, 1, min_val=1, max_val=10_000)
        target_gap = self._compute_target_gap(delay_range)
        safe_max_wait = None if max_wait is None else _safe_bounded_float(max_wait, 60.0, min_val=0.0, max_val=3600.0)
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
                    err.recorded_limiter_id = id(self)
                    raise err
                eff_cap = self._effective_cap(priority)
                budget_wait = 0.0
                if len(self._views) + n > eff_cap and self._views:
                    expire_idx = min(len(self._views) - 1, (len(self._views) + n) - eff_cap - 1)
                    budget_wait = max(0.0, (self._views[expire_idx] + self.window_seconds) - now)
                gap_wait = max(0.0, target_gap - (now - self._last_view_at)) if self._last_view_at is not None else 0.0
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

    def stats(self) -> dict[str, object]:
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
    return _DEFAULT_PAGE_VIEW_LIMITER


def reset_default_page_view_limiter() -> None:
    _DEFAULT_PAGE_VIEW_LIMITER.reset()


def is_server_rate_limit_html(html: str) -> bool:
    """Return `True` only on active server page-view throttle HTML, ignoring empty AJAX modal templates."""
    if not isinstance(html, str) or not html:
        return False
    low = html.lower()
    if "made more than" not in low or "page views" not in low:
        return False
    cleaned = _EMPTY_PAGEVIEW_MODAL_RE.sub("", html).lower()
    return "made more than" in cleaned and "page views" in cleaned
