"""Explicit shared session state injected into all `spaceinvasion.session.client` collaborators."""

from __future__ import annotations

from dataclasses import dataclass, field
import threading

from spaceinvasion.session.client._protocols import ClientFacadeHooks, WebDriverLike
from spaceinvasion.session.limiter import PageViewLimiter


@dataclass
class ClientSessionState:
    """Mutable state shared by `FrameNavigator`, `GalaxyNavigator`, `MessageInboxOps`, and `StateSnapshotReader`."""

    driver: WebDriverLike
    detach_on_close: bool
    page_limiter: PageViewLimiter
    min_nav_delay_override: tuple[float, float] | None
    min_nav_delay: tuple[float, float]
    timeout: float
    max_limiter_wait: float | None
    last_galaxy_coords: tuple[int, int] | None = None
    last_galaxy_max_galaxies: int | None = None
    last_galaxy_max_system: int | None = None
    last_galaxy_nav_method: str = ""
    last_galaxy_step_clicks: int = 0
    last_snapshot_observed_active_planet: bool = True
    last_home_daily_bonus_present: bool | None = None
    driver_lock: threading.RLock = field(default_factory=threading.RLock)
    dom_healthy_epoch: int = 0
    hooks: ClientFacadeHooks | None = None

    def pace(self, *, priority: str = "normal", cost: int = 1) -> float:
        return self.page_limiter.acquire(
            cost=cost,
            priority=priority,
            delay_range=self.min_nav_delay_override,
            max_wait=self.max_limiter_wait,
        )
