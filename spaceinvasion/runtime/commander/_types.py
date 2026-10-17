"""Core constants, read-only protocols, and dataclasses for ``runtime.commander``."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol, runtime_checkable

import requests

from spaceinvasion.core.model import GameState, PlannedAction, Resources
from spaceinvasion.runtime.commander_client import GameApiError

RATE_LIMIT_MARKERS: tuple[str, ...] = (
    "rate-limit",
    "rate limit",
    "page-view throttle",
    "page-view budget",
    "page views",
    "cooldown",
    "too many requests",
    "http 429",
    "retry after",
)

CAPTCHA_STOP_MARKERS: tuple[str, ...] = (
    "antibot",
    "anti-bot",
    "captcha",
    "turnstile",
    "sicherheitsprüfung",
    "sicherheitspruefung",
    "kein bot",
    "not a bot",
    "cloudflare gate",
)

CONNECTION_REFUSED_MARKERS: tuple[str, ...] = (
    "connection refused",
    "errno 111",
    "failed to establish a new connection",
    "remote end closed connection",
    "connection aborted",
)

SESSION_RECOVERY_MARKERS: tuple[str, ...] = (
    "not inside spaceinvasion game shell",
    "timed out waiting for frame",
    "session expired",
    "missing #json-container",
)

FLEET_ACTION_KINDS: frozenset[str] = frozenset(
    {"ATTACK", "SPY", "TRANSPORT", "STATION", "RECYCLE", "ESCAPE_FLIGHT", "COLONIZE"}
)

PARALLEL_BUILD_KINDS: frozenset[str] = frozenset({"BUILD", "DEMOLISH_BUILDING"})

# Concrete non-broad runtime error tuple for process-boundary loop recovery.
LOOP_BOUNDARY_ERRORS: tuple[type[Exception], ...] = (
    requests.RequestException,
    GameApiError,
    OSError,
    RuntimeError,
    ValueError,
    TypeError,
    KeyError,
    IndexError,
    LookupError,
    AttributeError,
    ArithmeticError,
    UnicodeError,
    AssertionError,
)


@runtime_checkable
class ResourcesCmdLike(Protocol):
    """Minimal resource interface for commander payload and summary helpers."""

    @property
    def pig_iron(self) -> int: ...

    @property
    def metal(self) -> int: ...

    @property
    def kryptonite(self) -> int: ...

    @property
    def spice(self) -> int: ...

    def is_zero(self) -> bool: ...

    def format_short(self) -> str: ...


@runtime_checkable
class BuildingOfferCmdLike(Protocol):
    """Minimal building-offer interface for commander tick refresh checks."""

    @property
    def building_id(self) -> int: ...

    @property
    def can_build(self) -> bool: ...


@runtime_checkable
class QueueJobCmdLike(Protocol):
    """Minimal queue-job interface for commander wakeup computation."""

    @property
    def category(self) -> str: ...

    @property
    def name(self) -> str: ...

    @property
    def remaining_text(self) -> str: ...


@runtime_checkable
class FleetAlertCmdLike(Protocol):
    """Minimal fleet-alert interface for commander tick economy-sync guards."""

    @property
    def hostile(self) -> bool: ...


@runtime_checkable
class EnergyCmdLike(Protocol):
    """Minimal energy interface for commander tick logging."""

    @property
    def produced(self) -> int: ...

    @property
    def used(self) -> int: ...


@runtime_checkable
class PlanetCmdLike(Protocol):
    """Minimal planet interface satisfied by ``core.model.PlanetState`` and ``core.state.PlanetState``."""

    @property
    def planet_id(self) -> int: ...

    @property
    def coords(self) -> str: ...

    @property
    def planet_type(self) -> str: ...

    @property
    def resources(self) -> ResourcesCmdLike: ...

    @property
    def energy(self) -> EnergyCmdLike: ...

    @property
    def economy_rates(self) -> Mapping[str, int]: ...

    @property
    def building_offers(self) -> Sequence[BuildingOfferCmdLike]: ...

    def building_level(self, building_id: int) -> int: ...


@runtime_checkable
class GameStateCmdLike(Protocol):
    """Minimal game-state interface satisfied by ``core.model.GameState`` and ``core.state.GameState``."""

    @property
    def universe_id(self) -> int: ...

    @property
    def planets(self) -> Sequence[PlanetCmdLike]: ...

    @property
    def active_queues(self) -> Sequence[QueueJobCmdLike]: ...

    @property
    def fleet_alerts(self) -> Sequence[FleetAlertCmdLike]: ...

    @property
    def specs(self) -> Mapping[str, object]: ...

    def active_planet(self) -> PlanetCmdLike | None: ...


@runtime_checkable
class PlannedActionCmdLike(Protocol):
    """Minimal planned-action contract satisfied by both canonical and legacy ``PlannedAction``."""

    @property
    def priority(self) -> str: ...

    @property
    def kind(self) -> str: ...

    @property
    def planet_id(self) -> int: ...

    @property
    def planet_coords(self) -> str: ...

    @property
    def target_name(self) -> str: ...

    @property
    def reason(self) -> str: ...

    @property
    def target_id(self) -> int | None: ...

    @property
    def target_level(self) -> int | None: ...

    @property
    def cost(self) -> ResourcesCmdLike: ...

    @property
    def missing(self) -> ResourcesCmdLike: ...

    @property
    def executable_now(self) -> bool: ...

    @property
    def wait_seconds(self) -> float | None: ...

    @property
    def manual_queue_id(self) -> int | None: ...


@runtime_checkable
class CommanderConfigLike(Protocol):
    """Minimal strategy config interface read by the commander loop and tick executor."""

    @property
    def planner_mode(self) -> str: ...

    @property
    def max_planets(self) -> int: ...

    @property
    def action_batch_interval_seconds(self) -> int: ...

    @property
    def allow_multi_planet(self) -> bool: ...

    @property
    def galaxy_scan_block_other_actions(self) -> bool: ...


@runtime_checkable
class CommanderApiLike(Protocol):
    """HTTP API client contract consumed by ``runtime.commander`` (zero Selenium imports)."""

    def health(self) -> Mapping[str, object]: ...

    def get_bot_control(self) -> Mapping[str, object]: ...

    def get_state(self, *, include_buildings: bool = True) -> object: ...

    def send_command(self, payload: dict[str, object]) -> Mapping[str, object]: ...

    def close(self) -> None: ...


@dataclass(frozen=True)
class CommanderOptions:
    """Parsed CLI options for the commander process."""

    api_url: str = "http://127.0.0.1:8787"
    live: bool = False
    once: bool = False
    loop: bool = False
    interval: float = 0.0
    max_ticks: int = 0
    max_actions: int | None = None
    config: Path | None = None
    log_dir: Path | None = Path("logs")
    reload: bool = False


@dataclass(frozen=True)
class BotControlSnapshot:
    """Parsed `/bot_status` control state for a single commander loop tick."""

    captcha_blocked: bool = False
    captcha_message: str = ""
    paused: bool = False
    galaxy_scan_blocking: bool = False
    mode_override: str | None = None
    scanned_systems: dict[str, float] | None = None
    throttle_cooldowns: dict[int, float] | None = None
    economy_sync_timestamps: dict[int, float] | None = None
    fleet_slot_cooldowns: dict[int, float] | None = None
    manual_queue: list[Mapping[str, object]] | None = None
    transmitter_status: dict[str, object] | None = None
    strategy_config_raw: Mapping[str, object] | None = None


@dataclass
class LoopRuntimeState:
    """Mutable bookkeeping counters persisted across commander loop iterations."""

    tick: int = 0
    last_recovery_at: float = 0.0
    last_full_buildings_at: float = 0.0
    captcha_alert_logged: bool = False


@dataclass(frozen=True)
class TickResult:
    """Typed summary returned by a single commander tick execution."""

    state: GameState
    actual_action: PlannedAction | None
    actual_result: dict[str, object] | None
    next_action: PlannedAction | None
    outcomes: list[dict[str, object]] = field(default_factory=list)


__all__ = [
    "BotControlSnapshot",
    "BuildingOfferCmdLike",
    "CAPTCHA_STOP_MARKERS",
    "CONNECTION_REFUSED_MARKERS",
    "CommanderApiLike",
    "CommanderConfigLike",
    "CommanderOptions",
    "EnergyCmdLike",
    "FLEET_ACTION_KINDS",
    "FleetAlertCmdLike",
    "GameStateCmdLike",
    "LOOP_BOUNDARY_ERRORS",
    "LoopRuntimeState",
    "PARALLEL_BUILD_KINDS",
    "PlanetCmdLike",
    "PlannedActionCmdLike",
    "QueueJobCmdLike",
    "RATE_LIMIT_MARKERS",
    "Resources",
    "ResourcesCmdLike",
    "SESSION_RECOVERY_MARKERS",
    "TickResult",
]
