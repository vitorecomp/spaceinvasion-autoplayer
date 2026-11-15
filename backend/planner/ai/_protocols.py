"""Read-only structural protocols shared by ``backend.planner.ai`` modules."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Protocol, runtime_checkable


@runtime_checkable
class ResourcesLike(Protocol):
    """Read-only structural protocol satisfied by ``core.model.Resources`` and ``core.state.Resources``."""

    @property
    def pig_iron(self) -> int: ...

    @property
    def metal(self) -> int: ...

    @property
    def kryptonite(self) -> int: ...

    @property
    def spice(self) -> int: ...


@runtime_checkable
class EnergyLike(Protocol):
    """Read-only structural protocol for planetary energy balance."""

    @property
    def produced(self) -> int: ...

    @property
    def used(self) -> int: ...


@runtime_checkable
class BuildingOfferLike(Protocol):
    """Read-only structural protocol for a parsed building card."""

    @property
    def building_id(self) -> int: ...

    @property
    def name(self) -> str: ...

    @property
    def current_level(self) -> int: ...

    @property
    def target_level(self) -> int: ...

    @property
    def cost(self) -> ResourcesLike: ...

    @property
    def can_build(self) -> bool: ...

    @property
    def status_text(self) -> str: ...

    @property
    def build_href(self) -> str | None: ...


@runtime_checkable
class ResearchOfferLike(Protocol):
    """Read-only structural protocol for a parsed research card."""

    @property
    def research_id(self) -> int: ...

    @property
    def research_key(self) -> str: ...

    @property
    def name(self) -> str: ...

    @property
    def current_level(self) -> int: ...

    @property
    def target_level(self) -> int: ...

    @property
    def cost(self) -> ResourcesLike: ...

    @property
    def can_research(self) -> bool: ...

    @property
    def status_text(self) -> str: ...

    @property
    def research_href(self) -> str | None: ...

    @property
    def active(self) -> bool: ...


@runtime_checkable
class QueueJobLike(Protocol):
    """Read-only structural protocol for an active queue job."""

    @property
    def category(self) -> str: ...

    @property
    def name(self) -> str: ...

    @property
    def building_id(self) -> int | None: ...

    @property
    def target_level(self) -> int | None: ...

    @property
    def remaining_text(self) -> str: ...

    @property
    def remaining_seconds(self) -> float | None: ...

    @property
    def finish_ts(self) -> float | None: ...

    @property
    def detail(self) -> str: ...

    @property
    def origin_coords(self) -> str: ...

    @property
    def cargo(self) -> object: ...


@runtime_checkable
class HostileFlagged(Protocol):
    """Optional ``hostile`` marker on synthetic fleet queue entries."""

    @property
    def hostile(self) -> object: ...


@runtime_checkable
class PlanetLike(Protocol):
    """Read-only structural protocol satisfied by ``core.model.PlanetState`` and ``core.state.PlanetState``."""

    @property
    def planet_id(self) -> int: ...

    @property
    def name(self) -> str: ...

    @property
    def coords(self) -> str: ...

    @property
    def planet_type(self) -> str: ...

    @property
    def temperature(self) -> int: ...

    @property
    def fields_used(self) -> int: ...

    @property
    def fields_max(self) -> int: ...

    @property
    def resources(self) -> ResourcesLike: ...

    @property
    def storage(self) -> ResourcesLike: ...

    @property
    def energy(self) -> EnergyLike: ...

    @property
    def buildings(self) -> Mapping[str, int]: ...

    @property
    def ships(self) -> Mapping[str, int]: ...

    @property
    def economy_rates(self) -> Mapping[str, int]: ...

    @property
    def building_offers(self) -> Sequence[BuildingOfferLike]: ...

    def building_level(self, building_id: int) -> int: ...


@runtime_checkable
class GameStateLike(Protocol):
    """Read-only structural protocol satisfied by ``core.model.GameState`` and ``core.state.GameState``."""

    @property
    def timestamp(self) -> float: ...

    @property
    def planets(self) -> Sequence[PlanetLike]: ...

    @property
    def active_planet_id(self) -> int | None: ...

    @property
    def researches(self) -> Mapping[str, int]: ...

    @property
    def active_queues(self) -> Sequence[QueueJobLike]: ...

    @property
    def research_offers(self) -> Sequence[ResearchOfferLike]: ...

    @property
    def speedup_factor(self) -> float | None: ...

    def active_planet(self) -> PlanetLike | None: ...


@runtime_checkable
class ConfigLike(Protocol):
    """Read-only structural protocol for planner strategy configuration flags."""

    @property
    def allow_multi_planet(self) -> bool: ...

    @property
    def ai_adaptive_execution(self) -> bool: ...
