"""Typed bridge facade Protocol consumed by Phase 4.1 `@command` handlers."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Protocol

from spaceinvasion.core.config import StrategyConfig
from spaceinvasion.core.model import GameState, PlannedAction
from spaceinvasion.runtime.bridge._context import BridgeContext
from spaceinvasion.runtime.bridge.lifecycle import BridgeLifecycleService
from spaceinvasion.runtime.bridge.planet_names import PlanetNameService
from spaceinvasion.runtime.bridge.snapshot import PublishedBridgeSnapshot
from spaceinvasion.runtime.bridge.state_cache import StateCacheService
from spaceinvasion.session.client import GameBrowserClient


class BridgeApiFacade(Protocol):
    """Explicit typed bridge interface accepted by all `@command` handlers."""

    ctx: BridgeContext
    names: PlanetNameService
    lifecycle: BridgeLifecycleService
    state_cache: StateCacheService

    def publish_snapshot(self) -> PublishedBridgeSnapshot: ...

    def health(self) -> dict[str, object]: ...

    def get_bot_control(self) -> dict[str, object]: ...

    def get_state(self, *, include_buildings: bool = True) -> dict[str, object]: ...

    def dashboard(self, *, refresh: bool = False) -> dict[str, object]: ...

    def dashboard_delta(
        self,
        *,
        client_section_versions: Mapping[str, str] | None = None,
        since_minute_bucket: int | None = None,
        since_loot_hour: int | None = None,
    ) -> dict[str, object]: ...

    def sync_executors_from_config(self) -> None: ...

    def abort_auto_galaxy_sweep_if_disabled(self) -> None: ...

    def start_galaxy_sweep_background(
        self,
        *,
        manual: bool = False,
        pages: int | None = None,
        start_galaxy: int | None = None,
        start_system: int | None = None,
    ) -> dict[str, object]: ...

    def run_galaxy_sweep(
        self,
        *,
        pages: int | None = None,
        galaxy: int | None = None,
        start_system: int | None = None,
        delay_range: tuple[float, float] | None = None,
        dry_run: bool = False,
        priority: str = "normal",
        automatic: bool = False,
        manual: bool | None = None,
    ) -> dict[str, object]: ...

    def stop_galaxy_sweep_now(self) -> dict[str, object]: ...

    def get_galaxy_sweep_state_locked(self) -> dict[str, object]: ...

    def reset_galaxy_sweep_cursor_locked(self, *, galaxy: int = 1, system: int = 1) -> dict[str, object]: ...

    def is_galaxy_scan_blocking_locked(self) -> bool: ...

    def galaxy_scan_busy_error_message(self) -> str: ...

    def preempt_galaxy_sweep_if_running(self, reason: str, *, wait_timeout: float = 3.0) -> bool: ...

    def attach_raid_stats(self, state: GameState) -> GameState: ...

    def plan_with_summary(
        self,
        state: GameState,
        *,
        config: StrategyConfig | None = None,
        include_manual_queue: bool = True,
    ) -> tuple[list[PlannedAction], dict[str, object] | None]: ...

    def serialize_action(
        self,
        action: PlannedAction,
        state: GameState | None = None,
    ) -> dict[str, object]: ...

    def build_action_graph_payload(
        self,
        state: GameState,
        *,
        config: StrategyConfig | None = None,
        include_manual_queue: bool = True,
        max_nodes: int = 36,
        planet_id: int | None = None,
    ) -> dict[str, object]: ...

    def find_build_action(
        self,
        state: GameState,
        building_id: int,
        planet_id: int | None,
        *,
        allow_live_verify: bool = False,
    ) -> PlannedAction: ...

    def find_demolish_action(
        self,
        state: GameState,
        building_id: int,
        planet_id: int | None,
    ) -> PlannedAction: ...

    def find_research_action(
        self,
        state: GameState,
        research_key: str,
        *,
        allow_live_verify: bool = False,
    ) -> PlannedAction: ...

    def find_ship_action(
        self,
        state: GameState,
        ship_key: str,
        count: int,
        planet_id: int | None,
        *,
        allow_live_verify: bool = False,
    ) -> PlannedAction: ...

    def execute_and_summarize(
        self,
        cmd: str,
        client: GameBrowserClient,
        state: GameState,
        action: PlannedAction,
        *,
        dry_run: bool,
        active_planet_confirmed: bool = True,
        manual_queue_id: int | None = None,
        source: str = "api",
    ) -> dict[str, object]: ...

    def get_planner_jobs_status(self) -> dict[str, object]: ...

    def config_optimizer_get(self, *, limit: int = 15) -> dict[str, object]: ...

    def config_optimizer_set_specializations(self, specializations: Mapping[str, object]) -> dict[str, object]: ...

    def config_optimizer_update_planet(self, payload: Mapping[str, object]) -> dict[str, object]: ...

    def config_optimizer_yearly_projection(self, payload: Mapping[str, object]) -> dict[str, object]: ...

    def config_optimizer_run(self, payload: Mapping[str, object]) -> dict[str, object]: ...

    def ai_planner_get(self, payload: Mapping[str, object]) -> dict[str, object]: ...

    def ai_planner_run(self, payload: Mapping[str, object]) -> dict[str, object]: ...

    def resolve_field_conflict(self, payload: Mapping[str, object]) -> dict[str, object]: ...

    def fleet_planner_get(self, payload: Mapping[str, object]) -> dict[str, object]: ...

    def maybe_trigger_ai_drift_replan(self) -> str | None: ...

    def sync_spio_if_unread(
        self,
        client: GameBrowserClient,
        state: GameState,
        *,
        force: bool = False,
        priority: str = "normal",
    ) -> int: ...

    def ingest_messages_locked(
        self,
        client: GameBrowserClient,
        state: GameState,
        *,
        include_folders: bool = True,
        delete_processed: bool = False,
        priority: str = "interactive",
    ) -> dict[str, object]: ...

    def record_galaxy_scan_html_locked(
        self,
        state: GameState,
        html: str,
        *,
        galaxy: int | None = None,
        system: int | None = None,
    ) -> dict[str, object]: ...

    def get_colony_candidates_payload(
        self,
        *,
        limit: int = 50,
        min_fields: int | None = None,
        include_discarded: bool = False,
    ) -> dict[str, object]: ...

    def get_scanned_sectors_payload(
        self,
        *,
        galaxy: int | None = None,
        limit: int = 200,
    ) -> dict[str, object]: ...

    def get_transmitter_status_locked(self) -> dict[str, object]: ...

    def note_transmitter_activation_locked(
        self,
        *,
        reward: str = "",
        free_spins: int | None = None,
        daily_bonus_collected: bool = False,
    ) -> dict[str, object]: ...

    def get_hourly_production_history_locked(self, *, hours: int = 168) -> list[dict[str, object]]: ...

    def get_loot_recycle_hourly_locked(self, *, hours: int = 168) -> dict[str, object]: ...

    def record_manual_queue_metal_review(
        self,
        queue_items: Sequence[Mapping[str, object]],
    ) -> list[dict[str, object]]: ...
