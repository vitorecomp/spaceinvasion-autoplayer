"""Intel, galaxy, ledger, and manual-queue review delegators for `BridgeCommandFacade`."""

from __future__ import annotations

from collections.abc import Mapping, Sequence

from spaceinvasion.core.model import GameState, PlanetState
from spaceinvasion.parsing import GalaxySlot
from spaceinvasion.runtime.bridge.intel_sync import IntelSyncService
from spaceinvasion.runtime.bridge.ledgers import LedgerTelemetryService
from spaceinvasion.runtime.bridge.plan_assembly import PlanAssemblyService
from spaceinvasion.session.client import GameBrowserClient


class _CommandFacadeIntelMixin:
    """Delegation mixin keeping `BridgeCommandFacade` well under 400 LOC."""

    _intel: IntelSyncService
    _ledgers: LedgerTelemetryService
    _plan: PlanAssemblyService

    def record_galaxy_scan_html_locked(
        self, state: GameState, html: str, *, galaxy: int | None = None, system: int | None = None
    ) -> dict[str, object]:
        return self._intel.record_galaxy_scan_html_locked(state, html, galaxy=galaxy, system=system)

    def observe_galaxy_universe_bounds_locked(
        self, *, client: GameBrowserClient | None = None, html: str | None = None
    ) -> None:
        self._intel.observe_galaxy_universe_bounds_locked(client=client, html=html)

    def persist_galaxy_scan(self, galaxy: int, system: int, slots: list[object]) -> None:
        self._intel.persist_galaxy_scan(galaxy, system, [s for s in slots if isinstance(s, GalaxySlot)])

    def persist_galaxy_slots(self, slots: list[object]) -> int:
        return self._intel.persist_galaxy_slots([s for s in slots if isinstance(s, GalaxySlot)])

    def own_planet_coords(self) -> set[str]:
        return self._intel.own_planet_coords()

    def get_colony_candidates_payload(
        self, *, limit: int = 50, min_fields: int | None = None, include_discarded: bool = False
    ) -> dict[str, object]:
        return self._intel.get_colony_candidates_payload(
            limit=limit, min_fields=min_fields, include_discarded=include_discarded
        )

    def get_scanned_sectors_payload(self, *, galaxy: int | None = None, limit: int = 200) -> dict[str, object]:
        return self._intel.get_scanned_sectors_payload(galaxy=galaxy, limit=limit)

    def max_fleet_slots_for_planet_locked(self, planet: PlanetState) -> int:
        return self._ledgers.max_fleet_slots_for_planet_locked(planet)

    def record_observed_fleet_slots_locked(self, planet: PlanetState | None, available: int) -> None:
        self._ledgers.record_observed_fleet_slots_locked(planet, available)

    def note_daily_login_bonus_seen_locked(self, *, present: bool) -> None:
        self._ledgers.note_daily_login_bonus_seen_locked(present)

    def get_transmitter_status_locked(self) -> dict[str, object]:
        return self._ledgers.get_transmitter_status_locked()

    def note_transmitter_activation_locked(
        self, *, reward: str = "", free_spins: int | None = None, daily_bonus_collected: bool = False
    ) -> dict[str, object]:
        return self._ledgers.note_transmitter_activation_locked(
            reward=reward, free_spins=free_spins, daily_bonus_collected=daily_bonus_collected
        )

    def get_hourly_production_history_locked(self, *, hours: int = 168) -> list[dict[str, object]]:
        return self._ledgers.get_hourly_production_history_locked(hours=hours)

    def get_loot_recycle_hourly_locked(self, *, hours: int = 168) -> dict[str, object]:
        return self._ledgers.get_loot_recycle_hourly_locked(hours=hours)

    def record_manual_queue_metal_review(self, queue_items: Sequence[Mapping[str, object]]) -> list[dict[str, object]]:
        return self._plan.record_manual_queue_metal_review(queue_items)


__all__ = ["_CommandFacadeIntelMixin"]
