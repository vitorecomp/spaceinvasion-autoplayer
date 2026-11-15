"""Explicit typed runtime state container injected into every bridge collaborator."""

from __future__ import annotations

from collections import deque
from collections.abc import Callable
from contextlib import AbstractContextManager
from pathlib import Path
import threading
import time
import weakref

from backend.core import catalog as game_data
from backend.core.config import StrategyConfig
from backend.core.model import GameState, PlanetState, PlannedAction
from backend.runtime.api.validation import ApiRequestError
from backend.runtime.bridge._context_init import (
    BridgeConstants,
    build_action_executors,
    build_db_backend_status_dict,
    default_bot_control_state,
    default_galaxy_sweep_state,
    default_transmitter_state,
    new_idle_planner_job,
    open_bridge_database,
    resolve_initial_strategy_config,
)
from backend.runtime.bridge._helpers import (
    BRIDGE_RUNTIME_ERRORS,
    ensure_debug_browser_ready,
    safe_api_int,
    safe_debug_port,
    safe_hourly_budget,
    safe_limiter_wait,
)
from backend.runtime.bridge.snapshot import SnapshotPublisher
from backend.runtime.postgres_ctl import is_container_runtime
from backend.session import browser as browser_mod
from backend.session.browser import DEFAULT_DEBUG_HOST, DEFAULT_DEBUG_PORT
from backend.session.client import GameBrowserClient, PageViewLimiter, RateLimitError
from backend.session.executor import ActionExecutor

ClientFactory = Callable[[int], AbstractContextManager[GameBrowserClient]]


class BridgeContext:
    """Explicit shared state container injected into every bridge collaborator."""

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
    ) -> None:
        self.debug_port = safe_debug_port(debug_port, DEFAULT_DEBUG_PORT)
        self.debug_host = str(debug_host or DEFAULT_DEBUG_HOST).strip() or "127.0.0.1"
        cleaned_novnc = (novnc_url or "").strip()
        self.novnc_url: str | None = cleaned_novnc or (
            "http://127.0.0.1:7900/vnc.html?autoconnect=true&resize=scale"
            if (is_container_runtime() or self.debug_host not in ("127.0.0.1", "localhost", "::1"))
            else None
        )
        self.env_file = env_file
        self.account = account
        self.config_file_explicit = config_path is not None
        self.config_path = config_path
        self.max_actions_per_hour_override = max_actions_per_hour
        self.log_dir = log_dir
        self.strategy_config: StrategyConfig = resolve_initial_strategy_config(
            config_path=config_path,
            log_dir=log_dir,
            db_backend=db_backend,
            db_url=db_url,
        )
        self.lock = threading.Lock()
        self.state_lock = threading.RLock()
        self.intel_read_lock = threading.RLock()
        self.sweep_run_lock = threading.Lock()
        self.planner_jobs_lock = threading.Lock()
        self.snapshot_publisher = SnapshotPublisher()
        self.custom_page_limiter_injected = page_limiter is not None
        self.page_limiter = page_limiter or PageViewLimiter()
        if not self.custom_page_limiter_injected:
            self.sync_page_limiter_from_config(self.strategy_config)
        self.max_limiter_wait = safe_limiter_wait(max_limiter_wait, 8.0)
        if executor_max_limiter_wait is None:
            self.executor_max_limiter_wait: float | None = None
        elif isinstance(executor_max_limiter_wait, (int, float)) and not isinstance(executor_max_limiter_wait, bool):
            val = safe_limiter_wait(executor_max_limiter_wait, self.max_limiter_wait)
            self.executor_max_limiter_wait = val if val != 8.0 else self.max_limiter_wait
        else:
            self.executor_max_limiter_wait = self.max_limiter_wait

        self.db_closed = False
        self.db, self.db_selection_source, self.resolved_postgres_url = open_bridge_database(
            db_path=db_path,
            db_backend=db_backend,
            db_url=db_url,
            log_dir=log_dir,
        )
        self._init_runtime_caches()
        self._init_control_and_planner_state()
        self._init_browser_factories(client_factory=client_factory, ensure_session_fn=ensure_session_fn)
        self.live_executor: ActionExecutor
        self.dry_executor: ActionExecutor
        self.init_executors()

    def _init_runtime_caches(self) -> None:
        self.last_state: GameState | None = None
        self.last_url: str | None = None
        self.last_snapshot_at: float = 0.0
        self.last_buildings_fetched: bool = False
        self.last_home_fetched: bool = False
        self.last_home_at: float | None = None
        self.last_recorded_state_obj: GameState | None = None
        self.active_planet_confirmed: bool = False
        self.last_research_sync_at: float = 0.0
        self.last_research_sync_sig: tuple[int, tuple[tuple[str, int], ...], bool] | None = None
        self.last_shipyard_sync_at: float = 0.0
        self.last_shipyard_sync_sig: tuple[int, int, tuple[tuple[str, int], ...]] | None = None
        self.last_spio_sync_at: float = 0.0
        self.pending_spy_sync: bool = False
        self.last_spy_dispatched_at: float = 0.0
        self.last_synced_unread_total: int = 0
        self.economy_rates: dict[int, dict[str, int]] = {}
        self.economy_sync_timestamps: dict[int, float] = {}
        self.scanned_systems: dict[str, float] = {}
        self.throttle_timestamps: dict[int, float] = {}
        self.metal_overflow_paused_planets: dict[int, float] = {}
        self.fleet_slot_cooldown_until: dict[int, float] = {}
        self.observed_fleet_slots_by_pid: dict[int, tuple[int, int]] = {}
        self.observed_fleet_slot_base_offset: int | None = None
        self.target_blocks: dict[str, tuple[float, str]] = {}
        self.colony_attempt_seen: set[tuple[int, str]] = set()
        self.colony_attempt_pending_pids: set[int] = set()
        self.colony_attempt_failed_recent: dict[str, tuple[str, float]] = {}
        self.dash_cache_state_ref: GameState | None = None
        self.dash_static_cache_key: object = None
        self.dash_static_cache_json: str | None = None
        self.dash_plan_cache_key: object = None
        self.dash_plan_cache_val: (
            tuple[list[PlannedAction], dict[str, object] | None, dict[int, dict[str, object]]] | None
        ) = None
        self.dash_ag_cache_key: object = None
        self.dash_ag_cache_json: str | None = None
        self.db_summary_cache_key: object = None
        self.db_summary_cache: dict[str, object] | None = None
        self.raid_stats_cache_key: object = None
        self.raid_stats_cache: dict[str, object] | None = None
        self.raid_stats_cache_ts: float = 0.0
        self.raid_targets_cache_key: object = None
        self.co_goals_cache_key: object = None
        self.co_goals_cache: dict[str, object] | None = None
        self.active_ai_cache_key: object = None
        self.active_ai_cache: dict[str, object] | None = None
        self.last_bot_control_payload: dict[str, object] | None = None
        self.last_dashboard_payload: dict[str, object] | None = None
        self.dash_full_cache_key: object = None
        self.dash_full_cache_json: str | None = None
        self.snapshot_dirty: bool = True
        self.rank_history_path: Path | None = None
        self.rank_samples: list[dict[str, object]] = []
        self.raid_targets: tuple[dict[str, object], ...] = ()
        self._recorded_rate_limit_errors: weakref.WeakSet[RateLimitError] = weakref.WeakSet()

    def _init_control_and_planner_state(self) -> None:
        self.bot_control: dict[str, object] = default_bot_control_state()
        self.transmitter_state_mem: dict[str, object] = default_transmitter_state()
        self.galaxy_sweep_mem: dict[str, object] = default_galaxy_sweep_state()
        self.sweep_xhr_timestamps: deque[float] = deque()
        self.last_galaxy_browser_coords: tuple[int, int] | None = None
        self.sweep_in_progress: bool = False
        self.sweep_is_manual: bool = False
        self.sweep_stop_event = threading.Event()
        self.sweep_wake_event = threading.Event()
        self.sweep_preempt_event = threading.Event()
        self.sweep_preempt_reason: str | None = None
        self.sweep_done_event = threading.Event()
        self.sweep_done_event.set()
        self.last_live_command_mono: float = 0.0
        self.sweep_thread: threading.Thread | None = None
        self.smart_sweep_summary: dict[str, object] = {}
        self.smart_sweep_summary_ts: float = 0.0
        self.planner_closed: bool = False
        self.planner_job_seq: int = 0
        self.config_opt_job: dict[str, object] = new_idle_planner_job("config_optimizer")
        self.ai_planner_job: dict[str, object] = new_idle_planner_job("ai_planner")
        self.active_config_opt_mem: dict[str, object] | None = None
        self.active_ai_plan_mem: dict[str, object] | None = None
        self.config_opt_runs_mem: list[dict[str, object]] = []
        self.ai_planner_runs_mem: list[dict[str, object]] = []
        self.reserved_floors_mem: dict[str, dict[str, int]] = {}
        self.planet_specializations_mem: dict[str, str] = {}
        self.custom_planet_names: dict[str, str] = {}
        self.co_yearly_proj_cache: dict[tuple[object, ...], dict[str, object]] = {}
        self.recent_transport_dispatches: list[dict[str, object]] = []
        self.galaxy_debris_amounts: dict[str, dict[str, int]] = {}
        self.last_early_transport_needs: dict[int, dict[str, object]] = {}
        self.last_early_transport_needs_sig: object = None
        self.universe_max_galaxies: int | None = None
        self.last_recorded_prod_minute: int | None = None
        self.hourly_prod_cache_key: object = None
        self.hourly_prod_cache: list[dict[str, object]] = []
        self.loot_recycle_cache_key: object = None
        self.loot_recycle_cache: dict[str, object] | None = None
        self.field_conflicts_mem: dict[str, dict[str, object]] = {}
        self.notified_field_conflict_keys: set[str] = set()
        self.adaptive_idle_buckets: dict[int, dict[str, object]] = {}
        self.transport_realized_utilization: float = BridgeConstants.TRANSPORT_UTIL_DEFAULT
        self.delivered_transport_events: list[dict[str, object]] = []
        self.infra_transport_spend_ledger: list[dict[str, object]] = []
        self.infra_transport_last_spend_ts: float = time.time()
        self.spare_ship_spend_ledger: list[dict[str, object]] = []
        self.last_auto_ai_replan_ts: float = 0.0
        self.last_auto_ai_replan_reason: str = ""
        self.last_drift_check_mono: float = 0.0

    def _init_browser_factories(
        self,
        *,
        client_factory: ClientFactory | None,
        ensure_session_fn: Callable[[], None] | None,
    ) -> None:
        def _default_client_factory(port: int) -> AbstractContextManager[GameBrowserClient]:
            if self.debug_host == "127.0.0.1":
                return GameBrowserClient.attach(
                    port=port, page_limiter=self.page_limiter, max_limiter_wait=self.max_limiter_wait
                )
            return GameBrowserClient.attach(
                port=port, host=self.debug_host, page_limiter=self.page_limiter, max_limiter_wait=self.max_limiter_wait
            )

        def _default_ensure_session() -> None:
            if self.debug_host == "127.0.0.1":
                ensure_debug_browser_ready(debug_port=self.debug_port, env_file=self.env_file, account=self.account)
            else:
                ensure_debug_browser_ready(
                    debug_port=self.debug_port,
                    debug_host=self.debug_host,
                    env_file=self.env_file,
                    account=self.account,
                )

        self.client_factory: ClientFactory = client_factory or _default_client_factory
        self.ensure_session_fn: Callable[[], None] = ensure_session_fn or _default_ensure_session

    def init_executors(self) -> None:
        """Build or refresh live and dry-run `ActionExecutor` instances from current strategy config."""
        self.live_executor, self.dry_executor = build_action_executors(
            self.strategy_config,
            max_actions_per_hour_override=self.max_actions_per_hour_override,
            page_limiter=self.page_limiter,
            executor_max_limiter_wait=self.executor_max_limiter_wait,
        )

    def sync_executors_from_config(self) -> None:
        """Propagate strategy config permission flags and hourly budget to both executors."""
        cfg = self.strategy_config
        for executor in (self.live_executor, self.dry_executor):
            executor.allow_fleetsave = cfg.allow_fleetsave
            executor.allow_raids = cfg.allow_raids
            executor.allow_colonize = cfg.allow_colonize
            executor.allow_transport = cfg.allow_transport
            executor.allow_transmitter = cfg.allow_transmitter
            if self.max_actions_per_hour_override is None:
                executor.max_actions_per_hour = safe_hourly_budget(cfg.max_actions_per_hour, 60)

    def is_browser_port_open(self) -> bool:
        """Return True when the configured Chrome DevTools debug port is open."""
        if self.debug_host == "127.0.0.1":
            return bool(browser_mod.is_debug_port_open(self.debug_port))
        return bool(browser_mod.is_debug_port_open(self.debug_port, self.debug_host))

    def record_rate_limit(self, retry_after: float | None = None) -> float:
        """Record a server rate-limit cooldown on the page limiter."""
        return float(self.page_limiter.record_rate_limit(retry_after))

    def rate_limit_remaining(self) -> float:
        """Return remaining seconds on the active rate-limit cooldown."""
        return float(self.page_limiter.rate_limit_remaining())

    def record_server_rate_limit(self, exc: RateLimitError) -> None:
        """Record a server-sent `RateLimitError` once per limiter instance."""
        if not exc.server_throttle or exc in self._recorded_rate_limit_errors:
            return
        rem = safe_limiter_wait(self.page_limiter.rate_limit_remaining(), 0.0, min_val=0.0)
        req = safe_limiter_wait(exc.retry_after, 45.0, min_val=1.0)
        self._recorded_rate_limit_errors.add(exc)
        if rem > 0.0 and req <= rem + 1.0:
            return
        self.page_limiter.record_rate_limit(req)

    def raise_if_rate_limited(self) -> None:
        """Raise `RateLimitError` immediately if a cooldown window is active."""
        rem = self.page_limiter.rate_limit_remaining()
        if rem > 0.0:
            err = RateLimitError(
                f"SpaceInvasion rate-limit cooldown active ({rem:.1f}s remaining; retry after {rem:.1f}s).",
                retry_after=rem,
                server_throttle=True,
            )
            self._recorded_rate_limit_errors.add(err)
            raise err

    def build_db_backend_status(self, *, include_ping: bool = False) -> dict[str, object]:
        """Build the `db_backend` diagnostic mapping for health and DB endpoints."""
        return build_db_backend_status_dict(
            self.db,
            selection_source=self.db_selection_source,
            resolved_postgres_url=self.resolved_postgres_url,
            include_ping=include_ping,
        )

    def sync_page_limiter_from_config(self, cfg: StrategyConfig) -> None:
        """Update non-injected `PageViewLimiter` rate limits from `StrategyConfig`."""
        if self.custom_page_limiter_injected:
            return
        limit = max(6, min(600, safe_api_int(cfg.page_view_limit_per_minute, 60)))
        if limit <= 24:
            delay_range = (0.9, 1.8)
        else:
            scale = min(1.0, 24.0 / float(limit))
            delay_range = (max(0.25, round(0.9 * scale, 2)), max(0.45, round(1.8 * scale, 2)))
        reserve = min(6, max(1, limit // 4))
        self.page_limiter.configure(
            max_views_per_minute=limit, min_delay_range=delay_range, reserved_interactive_views=reserve
        )

    def invalidate_dashboard_cache(self) -> None:
        """Clear derived dashboard, plan, action-graph, and planner caches."""
        self.dash_cache_state_ref = None
        self.dash_static_cache_key = None
        self.dash_static_cache_json = None
        self.dash_plan_cache_key = None
        self.dash_plan_cache_val = None
        self.dash_ag_cache_key = None
        self.dash_ag_cache_json = None
        self.co_goals_cache_key = None
        self.co_goals_cache = None
        self.active_ai_cache_key = None
        self.active_ai_cache = None
        self.dash_full_cache_key = None
        self.dash_full_cache_json = None
        self.snapshot_dirty = True

    def db_total_changes_token(self) -> tuple[int, int, int] | None:
        """Return the database change token used to memoize read-only DB projections."""
        if self.db is None:
            return None
        try:
            tok = self.db.change_token()
        except BRIDGE_RUNTIME_ERRORS:
            return None
        if isinstance(tok, tuple) and len(tok) == 3:
            return (int(tok[0]), int(tok[1]), int(tok[2]))
        return None

    def pick_executor(self, dry_run: bool) -> ActionExecutor:
        """Return either the dry-run or live `ActionExecutor`."""
        return self.dry_executor if dry_run else self.live_executor

    @staticmethod
    def resolve_planet(state: GameState, planet_id: int | None) -> PlanetState:
        """Resolve a target `PlanetState` by ID or active planet, raising `ApiRequestError` if absent."""
        planet = (
            next((p for p in state.planets if p.planet_id == planet_id), None)
            if planet_id is not None
            else state.active_planet()
        )
        if planet is None:
            pid_txt = game_data.fmt_compact_int(planet_id) if planet_id is not None else "None"
            raise ApiRequestError(f"Planet {pid_txt} not found in state.")
        return planet
