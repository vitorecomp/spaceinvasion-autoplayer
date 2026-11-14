"""Phase 4 modular GameApiBridge composing explicit collaborators and CommandRegistry."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from pathlib import Path
import threading
from typing import TYPE_CHECKING

from spaceinvasion.core.config import StrategyConfig
from spaceinvasion.runtime.api.registry import CommandRegistry
from spaceinvasion.runtime.bridge._bridge_wiring import wire_bridge_collaborators
from spaceinvasion.runtime.bridge._command_facade import BridgeCommandFacade
from spaceinvasion.runtime.bridge._context import BridgeContext, ClientFactory
from spaceinvasion.runtime.bridge._context_init import BridgeConstants
from spaceinvasion.runtime.bridge._db_gateway import RuntimeDatabaseGateway
from spaceinvasion.runtime.bridge._helpers import sanitize_json_dict
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
from spaceinvasion.session.browser import DEFAULT_DEBUG_HOST, DEFAULT_DEBUG_PORT
from spaceinvasion.session.client import PageViewLimiter
from spaceinvasion.session.executor import ActionExecutor

if TYPE_CHECKING:
    from spaceinvasion.runtime.api.handlers import BrowserBridgeFacade


class ModularGameApiBridge:
    """Explicitly composed bridge delegating reads, planning, execution, sweeps, and commands."""

    REALIZED_INCOME_BASIS: str = BridgeConstants.REALIZED_INCOME_BASIS
    SPARE_SHIP_BUDGET_WINDOW_HOURS: float = BridgeConstants.SPARE_SHIP_BUDGET_WINDOW_HOURS

    def __init__(
        self,
        *,
        debug_port: int = DEFAULT_DEBUG_PORT,
        debug_host: str = DEFAULT_DEBUG_HOST,
        novnc_url: str | None = None,
        env_file: Path = Path(".env"),
        account: str = "default",
        config_path: Path | None = None,
        max_actions_per_hour: int | None = None,
        log_dir: Path | None = Path("logs"),
        db_path: Path | str | None = None,
        db_backend: str | None = None,
        db_url: str | None = None,
        client_factory: ClientFactory | None = None,
        ensure_session_fn: Callable[[], None] | None = None,
        page_limiter: PageViewLimiter | None = None,
        max_limiter_wait: float = 8.0,
        executor_max_limiter_wait: float | None = 8.0,
        registry: CommandRegistry[BrowserBridgeFacade] | None = None,
    ) -> None:
        self.ctx = BridgeContext(
            debug_port=debug_port,
            debug_host=debug_host,
            novnc_url=novnc_url,
            env_file=env_file,
            account=account,
            config_path=config_path,
            max_actions_per_hour=max_actions_per_hour,
            log_dir=log_dir,
            db_path=db_path,
            db_backend=db_backend,
            db_url=db_url,
            client_factory=client_factory,
            ensure_session_fn=ensure_session_fn,
            page_limiter=page_limiter,
            max_limiter_wait=max_limiter_wait,
            executor_max_limiter_wait=executor_max_limiter_wait,
        )
        self.names = PlanetNameService(self.ctx)
        self.lifecycle = BridgeLifecycleService(self.ctx)
        self.state_cache = StateCacheService(self.ctx, self.names, self.lifecycle)
        self.ledgers = LedgerTelemetryService(self.ctx)
        self.planner_jobs = PlannerJobService(self.ctx, self.names, self.state_cache, self.ledgers)
        self.plan_assembly = PlanAssemblyService(self.ctx, self.names, self.ledgers, planner_jobs=self.planner_jobs)
        self.intel_sync = IntelSyncService(self.ctx, self.state_cache)
        self.galaxy_sweep = GalaxySweepService(self.ctx, self.state_cache, self.intel_sync, self.lifecycle)
        self.execution = ActionExecutionService(self.ctx, self.state_cache, self.intel_sync, self.lifecycle)
        self.views = DashboardViewService(self.ctx, self.state_cache, self.names, self.lifecycle)
        self._facade = BridgeCommandFacade(
            ctx=self.ctx,
            names=self.names,
            lifecycle=self.lifecycle,
            state_cache=self.state_cache,
            ledgers=self.ledgers,
            plan_assembly=self.plan_assembly,
            planner_jobs=self.planner_jobs,
            intel_sync=self.intel_sync,
            galaxy_sweep=self.galaxy_sweep,
            execution=self.execution,
            views=self.views,
        )
        if registry is not None:
            self.registry: CommandRegistry[BrowserBridgeFacade] = registry
        else:
            from spaceinvasion.runtime.api.handlers import build_command_registry

            self.registry = build_command_registry()

        wire_bridge_collaborators(
            ctx=self.ctx,
            lifecycle=self.lifecycle,
            state_cache=self.state_cache,
            ledgers=self.ledgers,
            plan_assembly=self.plan_assembly,
            planner_jobs=self.planner_jobs,
            intel_sync=self.intel_sync,
            galaxy_sweep=self.galaxy_sweep,
            execution=self.execution,
            views=self.views,
        )
        self.lifecycle.hydrate_persisted_bridge_state()
        self.ctx.init_executors()
        if self.ctx.db is not None:
            self.state_cache.refresh_raid_targets_locked(limit=100)
            self.intel_sync.hydrate_colony_attempt_seen()
        self.views.publish_snapshot()

    @property
    def debug_port(self) -> int:
        return self.ctx.debug_port

    @property
    def log_dir(self) -> Path | None:
        return self.ctx.log_dir

    @log_dir.setter
    def log_dir(self, value: Path | None) -> None:
        self.ctx.log_dir = value

    @property
    def config_path(self) -> Path | None:
        return self.ctx.config_path

    @config_path.setter
    def config_path(self, value: Path | None) -> None:
        self.ctx.config_path = value

    @property
    def strategy_config(self) -> StrategyConfig:
        return self.ctx.strategy_config

    @strategy_config.setter
    def strategy_config(self, value: StrategyConfig) -> None:
        self.ctx.strategy_config = value

    @property
    def _lock(self) -> threading.Lock:
        return self.ctx.lock

    @property
    def _page_limiter(self) -> PageViewLimiter:
        return self.ctx.page_limiter

    @property
    def _live_executor(self) -> ActionExecutor:
        return self.ctx.live_executor

    @_live_executor.setter
    def _live_executor(self, value: ActionExecutor) -> None:
        self.ctx.live_executor = value

    @property
    def _dry_executor(self) -> ActionExecutor:
        return self.ctx.dry_executor

    @_dry_executor.setter
    def _dry_executor(self, value: ActionExecutor) -> None:
        self.ctx.dry_executor = value

    @property
    def _db(self) -> RuntimeDatabaseGateway | None:
        return self.ctx.db

    @_db.setter
    def _db(self, value: RuntimeDatabaseGateway | None) -> None:
        self.ctx.db = value

    def close(self) -> None:
        self.lifecycle.close()

    def __enter__(self) -> ModularGameApiBridge:
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()

    def handle_command(self, payload: object) -> dict[str, object]:
        """Dispatch a `POST /command` JSON payload via `CommandRegistry` with lock-aware locking."""
        raw_cmd = payload.get("command") if isinstance(payload, Mapping) else None
        cmd_norm = raw_cmd.strip().lower() if isinstance(raw_cmd, str) else ""
        if (
            cmd_norm == "escape_flight"
            and isinstance(payload, Mapping)
            and not bool(payload.get("dry_run", False))
            and self.ctx.sweep_in_progress
            and self.ctx.strategy_config.allow_fleetsave
        ):
            self.galaxy_sweep.preempt_galaxy_sweep_if_running("Preempted by P0 emergency Escape Flight.")
        resp = self.registry.dispatch(self._facade, payload, browser_lock=self.ctx.lock)
        if cmd_norm in ("plan", "tick_once"):
            self.planner_jobs.maybe_trigger_ai_drift_replan()
        return sanitize_json_dict(resp)

    def publish_snapshot(self) -> PublishedBridgeSnapshot:
        return self.views.publish_snapshot()

    def health(self) -> dict[str, object]:
        return self.views.health()

    def get_bot_control(self) -> dict[str, object]:
        return self.views.get_bot_control()

    def get_state(self, *, include_buildings: bool = True) -> dict[str, object]:
        return self.state_cache.get_state(include_buildings=include_buildings)

    def dashboard(self, *, refresh: bool = False) -> dict[str, object]:
        return self.views.dashboard(refresh=refresh)

    get_dashboard = dashboard

    def dashboard_delta(
        self,
        *,
        client_section_versions: Mapping[str, str] | None = None,
        since_minute_bucket: int | None = None,
        since_loot_hour: int | None = None,
    ) -> dict[str, object]:
        return self.views.dashboard_delta(
            client_section_versions=client_section_versions,
            since_minute_bucket=since_minute_bucket,
            since_loot_hour=since_loot_hour,
        )

    def rate_limit_remaining(self) -> float:
        return self.ctx.rate_limit_remaining()

    def record_rate_limit(self, retry_after: float | None = None) -> float:
        return self.ctx.record_rate_limit(retry_after)

    def record_antibot_pause(self, exc: BaseException) -> None:
        self.lifecycle.record_antibot_pause(exc)
        self.views.publish_snapshot()

    def start_galaxy_sweep_worker(self) -> None:
        self.galaxy_sweep.start_galaxy_sweep_worker()

    def stop_galaxy_sweep_worker(self) -> None:
        self.galaxy_sweep.stop_galaxy_sweep_worker()

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
        return self.galaxy_sweep.run_galaxy_sweep(
            pages=pages,
            galaxy=galaxy,
            start_system=start_system,
            delay_range=delay_range,
            dry_run=dry_run,
            priority=priority,
            automatic=automatic,
            manual=manual,
        )


GameApiBridge = ModularGameApiBridge
