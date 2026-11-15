"""Extended typed bridge Protocol consumed by browser-capable planning and execution handlers."""

from __future__ import annotations

from collections.abc import Callable
from typing import Protocol

from backend.core.config import StrategyConfig
from backend.core.model import GameState, PlanetState, PlannedAction
from backend.runtime.api.handlers._bridge_proto import BridgeApiFacade
from backend.session.client import GameBrowserClient


class BrowserBridgeFacade(BridgeApiFacade, Protocol):
    """Typed facade for browser-capable handlers (superset of `BridgeApiFacade`)."""

    def get_config_optimizer_building_goals_locked(self) -> dict[str, object] | None: ...

    def evaluate_ai_planner_gate_locked(
        self,
        state: GameState,
        *,
        cfg: StrategyConfig | None = None,
    ) -> dict[str, object]: ...

    def get_planner_state_locked(self) -> GameState | None: ...

    def plan_with_context(
        self,
        state: GameState,
        *,
        config: StrategyConfig | None = None,
    ) -> list[PlannedAction]: ...

    def plan_with_summary_ex(
        self,
        state: GameState,
        *,
        config: StrategyConfig | None = None,
        include_manual_queue: bool = True,
        record_summary: bool = True,
        already_enriched: bool = False,
    ) -> tuple[list[PlannedAction], dict[str, object] | None]: ...

    def max_fleet_slots_for_planet_locked(self, planet: PlanetState) -> int: ...

    def record_observed_fleet_slots_locked(self, planet: PlanetState | None, available: int) -> None: ...

    def note_daily_login_bonus_seen_locked(self, *, present: bool) -> None: ...

    def observe_galaxy_universe_bounds_locked(
        self,
        *,
        client: GameBrowserClient | None = None,
        html: str | None = None,
    ) -> None: ...

    def persist_galaxy_scan(self, galaxy: int, system: int, slots: list[object]) -> None: ...

    def persist_galaxy_slots(self, slots: list[object]) -> int: ...

    def own_planet_coords(self) -> set[str]: ...

    def ingest_and_delete_page_messages(
        self,
        client: GameBrowserClient,
        html: str,
        *,
        folder_type: str,
        own_coords: set[str],
        priority: str = "normal",
        always_persist_spy: bool = False,
    ) -> tuple[list[object], int, bool, str]: ...

    def sync_spio_if_unread_ex(
        self,
        client: GameBrowserClient | None,
        state: GameState,
        *,
        force: bool = False,
        priority: str = "normal",
        get_client: Callable[[], GameBrowserClient] | None = None,
        ignore_auth_error: bool = False,
    ) -> int: ...

    def find_research_action_by_id(
        self,
        state: GameState,
        research_id: int,
        planet_id: int | None,
        *,
        allow_live_verify: bool = False,
    ) -> PlannedAction: ...

    def find_ship_action_by_id(
        self,
        state: GameState,
        ship_id: int,
        quantity: int,
        planet_id: int | None,
        *,
        allow_live_verify: bool = False,
    ) -> PlannedAction: ...

    def execute_and_summarize_browser(
        self,
        cmd: str,
        client: GameBrowserClient | None,
        state: GameState,
        action: PlannedAction,
        *,
        dry_run: bool,
        return_home: bool = False,
    ) -> dict[str, object]: ...
