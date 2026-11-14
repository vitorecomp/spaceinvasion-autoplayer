"""Typed `BrowserBridgeFacade` adapter delegating `@command` calls to bridge collaborators."""

from __future__ import annotations

from collections.abc import Callable, Mapping

from spaceinvasion.core.config import StrategyConfig
from spaceinvasion.core.model import GameState, PlannedAction
from spaceinvasion.runtime.bridge._command_facade_intel import _CommandFacadeIntelMixin
from spaceinvasion.runtime.bridge._context import BridgeContext
from spaceinvasion.runtime.bridge.execution import ActionExecutionService
from spaceinvasion.runtime.bridge.galaxy_sweep import GalaxySweepService
from spaceinvasion.runtime.bridge.intel_sync import IntelSyncService
from spaceinvasion.runtime.bridge.ledgers import LedgerTelemetryService
from spaceinvasion.runtime.bridge.lifecycle import BridgeLifecycleService
from spaceinvasion.runtime.bridge.plan_assembly import PlanAssemblyService
from spaceinvasion.runtime.bridge.planet_names import PlanetNameService
from spaceinvasion.runtime.bridge.planner_jobs import PlannerJobService
from spaceinvasion.runtime.bridge.snapshot import PublishedBridgeSnapshot
from spaceinvasion.runtime.bridge.state_cache import StateCacheService
from spaceinvasion.runtime.bridge.views import DashboardViewService
from spaceinvasion.session.client import GameBrowserClient


class BridgeCommandFacade(_CommandFacadeIntelMixin):
    """Implements `BridgeApiFacade` and `BrowserBridgeFacade` via explicit collaborators."""

    def __init__(
        self,
        *,
        ctx: BridgeContext,
        names: PlanetNameService,
        lifecycle: BridgeLifecycleService,
        state_cache: StateCacheService,
        ledgers: LedgerTelemetryService,
        plan_assembly: PlanAssemblyService,
        planner_jobs: PlannerJobService,
        intel_sync: IntelSyncService,
        galaxy_sweep: GalaxySweepService,
        execution: ActionExecutionService,
        views: DashboardViewService,
    ) -> None:
        self.ctx = ctx
        self.names = names
        self.lifecycle = lifecycle
        self.state_cache = state_cache
        self._ledgers = ledgers
        self._plan = plan_assembly
        self._jobs = planner_jobs
        self._intel = intel_sync
        self._sweep = galaxy_sweep
        self._exec = execution
        self._views = views

    def publish_snapshot(self) -> PublishedBridgeSnapshot:
        return self._views.publish_snapshot()

    def health(self) -> dict[str, object]:
        return self._views.health()

    def get_bot_control(self) -> dict[str, object]:
        return self._views.get_bot_control()

    def get_state(self, *, include_buildings: bool = True) -> dict[str, object]:
        return self.state_cache.get_state(include_buildings=include_buildings)

    def dashboard(self, *, refresh: bool = False) -> dict[str, object]:
        return self._views.dashboard(refresh=refresh)

    def dashboard_delta(
        self,
        *,
        client_section_versions: Mapping[str, str] | None = None,
        since_minute_bucket: int | None = None,
        since_loot_hour: int | None = None,
    ) -> dict[str, object]:
        return self._views.dashboard_delta(
            client_section_versions=client_section_versions,
            since_minute_bucket=since_minute_bucket,
            since_loot_hour=since_loot_hour,
        )

    def sync_executors_from_config(self) -> None:
        self.ctx.sync_executors_from_config()

    def abort_auto_galaxy_sweep_if_disabled(self) -> None:
        self._sweep.abort_auto_galaxy_sweep_if_disabled()

    def start_galaxy_sweep_background(
        self,
        *,
        manual: bool = False,
        pages: int | None = None,
        start_galaxy: int | None = None,
        start_system: int | None = None,
    ) -> dict[str, object]:
        return self._sweep.start_galaxy_sweep_background(
            manual=manual, pages=pages, start_galaxy=start_galaxy, start_system=start_system
        )

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
    ) -> dict[str, object]:
        return self._sweep.run_galaxy_sweep(
            pages=pages,
            galaxy=galaxy,
            start_system=start_system,
            delay_range=delay_range,
            dry_run=dry_run,
            priority=priority,
            automatic=automatic,
            manual=manual,
        )

    def stop_galaxy_sweep_now(self) -> dict[str, object]:
        return self._sweep.stop_galaxy_sweep_now()

    def get_galaxy_sweep_state_locked(self) -> dict[str, object]:
        return self._sweep.get_galaxy_sweep_state_locked()

    def reset_galaxy_sweep_cursor_locked(self, *, galaxy: int = 1, system: int = 1) -> dict[str, object]:
        return self._sweep.reset_galaxy_sweep_cursor_locked(galaxy=galaxy, system=system)

    def is_galaxy_scan_blocking_locked(self) -> bool:
        return self._sweep.is_galaxy_scan_blocking_locked()

    def galaxy_scan_busy_error_message(self) -> str:
        return self._sweep.galaxy_scan_busy_error_message()

    def preempt_galaxy_sweep_if_running(self, reason: str, *, wait_timeout: float = 3.0) -> bool:
        return self._sweep.preempt_galaxy_sweep_if_running(reason, wait_timeout=wait_timeout)

    def attach_raid_stats(self, state: GameState) -> GameState:
        return self._ledgers.attach_raid_stats(state)

    def plan_with_summary(
        self, state: GameState, *, config: StrategyConfig | None = None, include_manual_queue: bool = True
    ) -> tuple[list[PlannedAction], dict[str, object] | None]:
        return self._plan.plan_with_summary(state, config=config, include_manual_queue=include_manual_queue)

    def plan_with_summary_ex(
        self,
        state: GameState,
        *,
        config: StrategyConfig | None = None,
        include_manual_queue: bool = True,
        record_summary: bool = True,
        already_enriched: bool = False,
    ) -> tuple[list[PlannedAction], dict[str, object] | None]:
        return self._plan.plan_with_summary(
            state,
            config=config,
            include_manual_queue=include_manual_queue,
            record_summary=record_summary,
            already_enriched=already_enriched,
        )

    def plan_with_context(self, state: GameState, *, config: StrategyConfig | None = None) -> list[PlannedAction]:
        return self._plan.plan_with_context(state, config=config)

    def serialize_action(self, action: PlannedAction, state: GameState | None = None) -> dict[str, object]:
        return self._plan.serialize_action(action, state)

    def build_action_graph_payload(
        self,
        state: GameState,
        *,
        config: StrategyConfig | None = None,
        include_manual_queue: bool = True,
        max_nodes: int = 36,
        planet_id: int | None = None,
    ) -> dict[str, object]:
        return self._plan.build_action_graph_payload(
            state, config=config, include_manual_queue=include_manual_queue, max_nodes=max_nodes, planet_id=planet_id
        )

    def find_build_action(
        self, state: GameState, building_id: int, planet_id: int | None, *, allow_live_verify: bool = False
    ) -> PlannedAction:
        return self._plan.find_build_action(state, building_id, planet_id, allow_live_verify=allow_live_verify)

    def find_demolish_action(self, state: GameState, building_id: int, planet_id: int | None) -> PlannedAction:
        return self._plan.find_demolish_action(state, building_id, planet_id)

    def find_research_action(
        self, state: GameState, research_key: str, *, allow_live_verify: bool = False
    ) -> PlannedAction:
        return self._plan.find_research_action(state, research_key, allow_live_verify=allow_live_verify)

    def find_research_action_by_id(
        self, state: GameState, research_id: int, planet_id: int | None, *, allow_live_verify: bool = False
    ) -> PlannedAction:
        return self._plan.find_research_action(state, f"r{research_id}", planet_id, allow_live_verify=allow_live_verify)

    def find_ship_action(
        self, state: GameState, ship_key: str, count: int, planet_id: int | None, *, allow_live_verify: bool = False
    ) -> PlannedAction:
        return self._plan.find_ship_action(state, ship_key, count, planet_id, allow_live_verify=allow_live_verify)

    def find_ship_action_by_id(
        self, state: GameState, ship_id: int, quantity: int, planet_id: int | None, *, allow_live_verify: bool = False
    ) -> PlannedAction:
        return self._plan.find_ship_action(
            state, f"s{ship_id}", quantity, planet_id, allow_live_verify=allow_live_verify
        )

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
    ) -> dict[str, object]:
        return self._exec.execute_and_summarize(
            cmd,
            client,
            state,
            action,
            dry_run=dry_run,
            active_planet_confirmed=active_planet_confirmed,
            manual_queue_id=manual_queue_id,
            source=source,
        )

    def execute_and_summarize_browser(
        self,
        cmd: str,
        client: GameBrowserClient | None,
        state: GameState,
        action: PlannedAction,
        *,
        dry_run: bool,
        return_home: bool = False,
    ) -> dict[str, object]:
        return self._exec.execute_and_summarize(cmd, client, state, action, dry_run=dry_run, return_home=return_home)

    def get_planner_jobs_status(self) -> dict[str, object]:
        return self._jobs.get_planner_jobs_status()

    def get_config_optimizer_building_goals_locked(self) -> dict[str, object] | None:
        return self._jobs.get_config_optimizer_building_goals_locked()

    def evaluate_ai_planner_gate_locked(
        self, state: GameState, *, cfg: StrategyConfig | None = None
    ) -> dict[str, object]:
        return self._jobs.evaluate_ai_planner_gate_locked(state, cfg=cfg)

    def get_planner_state_locked(self) -> GameState | None:
        return self._jobs.get_planner_state_locked()

    def config_optimizer_get(self, *, limit: int = 15) -> dict[str, object]:
        return self._jobs.config_optimizer_get(limit=limit)

    def config_optimizer_set_specializations(self, specializations: Mapping[str, object]) -> dict[str, object]:
        return self._jobs.config_optimizer_set_specializations(specializations)

    def config_optimizer_update_planet(self, payload: Mapping[str, object]) -> dict[str, object]:
        return self._jobs.config_optimizer_update_planet(payload)

    def config_optimizer_yearly_projection(self, payload: Mapping[str, object]) -> dict[str, object]:
        return self._jobs.config_optimizer_yearly_projection(payload)

    def config_optimizer_run(self, payload: Mapping[str, object]) -> dict[str, object]:
        return self._jobs.config_optimizer_run(payload)

    def ai_planner_get(self, payload: Mapping[str, object]) -> dict[str, object]:
        return self._jobs.ai_planner_get(payload)

    def ai_planner_run(self, payload: Mapping[str, object]) -> dict[str, object]:
        return self._jobs.ai_planner_run(payload)

    def resolve_field_conflict(self, payload: Mapping[str, object]) -> dict[str, object]:
        return self._jobs.resolve_field_conflict(payload)

    def fleet_planner_get(self, payload: Mapping[str, object]) -> dict[str, object]:
        return self._jobs.fleet_planner_get(payload)

    def maybe_trigger_ai_drift_replan(self) -> str | None:
        return self._jobs.maybe_trigger_ai_drift_replan()

    def sync_spio_if_unread(
        self, client: GameBrowserClient, state: GameState, *, force: bool = False, priority: str = "normal"
    ) -> int:
        return self._intel.sync_spio_if_unread(client, state, force=force, priority=priority)

    def sync_spio_if_unread_ex(
        self,
        client: GameBrowserClient | None,
        state: GameState,
        *,
        force: bool = False,
        priority: str = "normal",
        get_client: Callable[[], GameBrowserClient] | None = None,
        ignore_auth_error: bool = False,
    ) -> int:
        return self._intel.sync_spio_if_unread(
            client, state, force=force, priority=priority, get_client=get_client, ignore_auth_error=ignore_auth_error
        )

    def ingest_messages_locked(
        self,
        client: GameBrowserClient,
        state: GameState,
        *,
        include_folders: bool = True,
        delete_processed: bool = False,
        priority: str = "interactive",
    ) -> dict[str, object]:
        return self._intel.ingest_messages_locked(
            client, state, include_folders=include_folders, delete_processed=delete_processed, priority=priority
        )

    def ingest_and_delete_page_messages(
        self,
        client: GameBrowserClient,
        html: str,
        *,
        folder_type: str,
        own_coords: set[str],
        priority: str = "normal",
        always_persist_spy: bool = False,
    ) -> tuple[list[object], int, bool, str]:
        reps, cnt, del_ok, post = self._intel.ingest_and_delete_page_messages(
            client,
            html,
            folder_type=folder_type,
            own_coords=own_coords,
            priority=priority,
            always_persist_spy=always_persist_spy,
        )
        return list(reps), cnt, del_ok, post
