"""Config Optimizer, AI-Planner, Fleet Planner, and adaptive drift-replan coordinator collaborator."""

from __future__ import annotations

from collections.abc import Callable, Mapping
import logging
import time

from spaceinvasion.core.config import StrategyConfig, normalize_planner_mode
from spaceinvasion.core.model import GameState
from spaceinvasion.runtime.api.validation import ApiRequestError
from spaceinvasion.runtime.bridge._context import BridgeContext
from spaceinvasion.runtime.bridge._context_init import BridgeConstants
from spaceinvasion.runtime.bridge._helpers import BRIDGE_RUNTIME_ERRORS, safe_api_float, sanitize_json_dict
from spaceinvasion.runtime.bridge._planner_jobs_ai_runner import start_ai_planner_job
from spaceinvasion.runtime.bridge._planner_jobs_ai_views import handle_ai_planner_get, handle_ai_planner_run
from spaceinvasion.runtime.bridge._planner_jobs_co_runner import start_config_optimizer_job
from spaceinvasion.runtime.bridge._planner_jobs_co_views import (
    handle_config_optimizer_get,
    handle_config_optimizer_run,
    handle_config_optimizer_set_specializations,
    handle_config_optimizer_update_planet,
    handle_config_optimizer_yearly_projection,
)
from spaceinvasion.runtime.bridge._planner_jobs_fleet_views import (
    handle_fleet_planner_get,
    handle_resolve_field_conflict,
)
from spaceinvasion.runtime.bridge._planner_jobs_state import (
    get_active_ai_planner,
    get_active_config_optimizer,
    get_config_optimizer_building_goals,
    get_planner_state,
    upsert_field_conflict,
)
from spaceinvasion.runtime.bridge._planner_jobs_telemetry import (
    compute_live_attack_telemetry,
    evaluate_ai_drift_replan,
    evaluate_ai_planner_gate,
    get_planner_jobs_snapshot,
)
from spaceinvasion.runtime.bridge.ledgers import LedgerTelemetryService
from spaceinvasion.runtime.bridge.planet_names import PlanetNameService
from spaceinvasion.runtime.bridge.state_cache import StateCacheService

_LOGGER = logging.getLogger(__name__)
_DRIFT_REPLAN_ERRORS: tuple[type[Exception], ...] = (ApiRequestError, *BRIDGE_RUNTIME_ERRORS)


class PlannerJobService:
    """Owns Config Optimizer, AI-Planner, Fleet Planner, field conflicts, and adaptive drift replanning."""

    def __init__(
        self,
        ctx: BridgeContext,
        names: PlanetNameService,
        state_cache: StateCacheService,
        ledgers: LedgerTelemetryService,
        *,
        on_publish_snapshot: Callable[[], None] | None = None,
        on_persist_runtime_state: Callable[[], None] | None = None,
        on_antibot_pause: Callable[[BaseException], None] | None = None,
        on_is_galaxy_scan_blocking: Callable[[], bool] | None = None,
        on_galaxy_scan_busy_msg: Callable[[], str] | None = None,
    ) -> None:
        self._ctx = ctx
        self._names = names
        self._state_cache = state_cache
        self._ledgers = ledgers
        self._on_publish_snapshot = on_publish_snapshot
        self._on_persist_runtime_state = on_persist_runtime_state
        self._on_antibot_pause = on_antibot_pause
        self._on_is_galaxy_scan_blocking = on_is_galaxy_scan_blocking
        self._on_galaxy_scan_busy_msg = on_galaxy_scan_busy_msg

    def bind_hooks(
        self,
        *,
        on_publish_snapshot: Callable[[], None] | None = None,
        on_persist_runtime_state: Callable[[], None] | None = None,
        on_antibot_pause: Callable[[BaseException], None] | None = None,
        on_is_galaxy_scan_blocking: Callable[[], bool] | None = None,
        on_galaxy_scan_busy_msg: Callable[[], str] | None = None,
    ) -> None:
        """Bind optional post-construction lifecycle, sweep, and snapshot callbacks explicitly."""
        if on_publish_snapshot is not None:
            self._on_publish_snapshot = on_publish_snapshot
        if on_persist_runtime_state is not None:
            self._on_persist_runtime_state = on_persist_runtime_state
        if on_antibot_pause is not None:
            self._on_antibot_pause = on_antibot_pause
        if on_is_galaxy_scan_blocking is not None:
            self._on_is_galaxy_scan_blocking = on_is_galaxy_scan_blocking
        if on_galaxy_scan_busy_msg is not None:
            self._on_galaxy_scan_busy_msg = on_galaxy_scan_busy_msg

    def get_planner_state_locked(self) -> GameState | None:
        """Return latest in-memory or persisted `GameState` for planner operations."""
        return get_planner_state(self._ctx)

    def get_active_config_optimizer_locked(self) -> dict[str, object] | None:
        """Return active Config Optimizer record from DB or in-memory fallback."""
        return get_active_config_optimizer(self._ctx)

    def get_config_optimizer_building_goals_locked(self) -> dict[str, object] | None:
        """Return cached per-planet Config Optimizer building target levels."""
        return get_config_optimizer_building_goals(self._ctx)

    def get_active_ai_planner_locked(self) -> dict[str, object] | None:
        """Return active AI-Planner run record, cached by database change token."""
        return get_active_ai_planner(self._ctx)

    def evaluate_ai_planner_gate_locked(
        self,
        state: GameState | None,
        *,
        cfg: StrategyConfig | None = None,
    ) -> dict[str, object]:
        """Evaluate AI-Planner live unlock gate and effective planner mode."""
        return evaluate_ai_planner_gate(self._ctx, state, cfg=cfg)

    def upsert_field_conflict_locked(self, conflict: Mapping[str, object]) -> dict[str, object]:
        """Insert or update a pending field-conflict prompt and raise a deduped notification."""
        return upsert_field_conflict(self._ctx, conflict)

    def compute_live_attack_telemetry_locked(self, state: GameState | None) -> dict[str, object]:
        """Compute live raid telemetry (using realized 24h loot when adaptive execution is enabled)."""
        return compute_live_attack_telemetry(self._ctx, self._ledgers, state)

    def get_planner_jobs_snapshot_locked(
        self,
        state: GameState | None = None,
        *,
        cfg: StrategyConfig | None = None,
    ) -> dict[str, object]:
        """Return the raw planner jobs snapshot dict for dashboard assembly."""
        return get_planner_jobs_snapshot(self._ctx, state, cfg=cfg)

    def get_planner_jobs_status(self) -> dict[str, object]:
        """Return the sanitized `planner_jobs_status` API response without acquiring the browser lock."""
        with self._ctx.state_lock:
            snap = get_planner_jobs_snapshot(self._ctx)
        return sanitize_json_dict({"ok": True, "command": "planner_jobs_status", **snap})

    def _start_ai_job(
        self,
        *,
        params: Mapping[str, object] | None = None,
        sync: bool = False,
        refresh: bool = False,
        trigger_source: str = "manual",
        auto_run_config_if_missing: bool = True,
        override_config_run_id: int | None = None,
        override_config_result: Mapping[str, object] | None = None,
    ) -> dict[str, object]:
        return start_ai_planner_job(
            self._ctx,
            self._names,
            self._state_cache,
            self._ledgers,
            params=params,
            sync=sync,
            refresh=refresh,
            trigger_source=trigger_source,
            auto_run_config_if_missing=auto_run_config_if_missing,
            override_config_run_id=override_config_run_id,
            override_config_result=override_config_result,
            on_publish_snapshot=self._on_publish_snapshot,
            on_antibot_pause=self._on_antibot_pause,
        )

    def _start_co_job(
        self,
        *,
        params: Mapping[str, object] | None = None,
        auto_replan_ai: bool = True,
        sync: bool = False,
        trigger_source: str = "manual",
    ) -> dict[str, object]:
        return start_config_optimizer_job(
            self._ctx,
            self._names,
            self._state_cache,
            params=params,
            auto_replan_ai=auto_replan_ai,
            sync=sync,
            trigger_source=trigger_source,
            start_ai_fn=self._start_ai_job,
            on_publish_snapshot=self._on_publish_snapshot,
        )

    def config_optimizer_get(self, limit: int = 15) -> dict[str, object]:
        """Return the `config_optimizer_get` API response."""
        return handle_config_optimizer_get(self._ctx, self._names, limit=limit)

    def config_optimizer_set_specializations(
        self,
        specializations: Mapping[str, object],
        *,
        replace: bool = False,
    ) -> dict[str, object]:
        """Validate and persist Config Optimizer planet specialization locks."""
        return handle_config_optimizer_set_specializations(
            self._ctx,
            specializations,
            replace=replace,
            on_publish_snapshot=self._on_publish_snapshot,
        )

    def config_optimizer_update_planet(self, payload: Mapping[str, object]) -> dict[str, object]:
        """Apply a manual planet target-building override in Config Optimizer."""
        return handle_config_optimizer_update_planet(
            self._ctx,
            self._names,
            self._state_cache,
            payload,
            start_ai_fn=self._start_ai_job,
            on_publish_snapshot=self._on_publish_snapshot,
        )

    def config_optimizer_yearly_projection(self, payload: Mapping[str, object]) -> dict[str, object]:
        """Return cached multi-year ships-per-week projection for Config Optimizer."""
        return handle_config_optimizer_yearly_projection(self._ctx, payload)

    def config_optimizer_run(self, payload: Mapping[str, object]) -> dict[str, object]:
        """Validate and launch a Config Optimizer background or synchronous run."""
        return handle_config_optimizer_run(payload, start_co_fn=self._start_co_job)

    def ai_planner_get(self, payload: Mapping[str, object]) -> dict[str, object]:
        """Return the `ai_planner_get` response including timeline, smelting, adaptive status, and DAG."""
        return handle_ai_planner_get(self._ctx, self._names, self._ledgers, payload)

    def ai_planner_run(self, payload: Mapping[str, object]) -> dict[str, object]:
        """Validate and launch an AI-Planner background or synchronous run."""
        return handle_ai_planner_run(
            self._ctx,
            payload,
            start_ai_fn=self._start_ai_job,
            on_is_galaxy_scan_blocking=self._on_is_galaxy_scan_blocking,
            on_galaxy_scan_busy_msg=self._on_galaxy_scan_busy_msg,
        )

    def resolve_field_conflict(self, payload: Mapping[str, object]) -> dict[str, object]:
        """Resolve a pending field conflict (`demolish` or `keep_reserve`)."""
        return handle_resolve_field_conflict(
            self._ctx,
            payload,
            start_co_fn=self._start_co_job,
            on_publish_snapshot=self._on_publish_snapshot,
        )

    def fleet_planner_get(self, payload: Mapping[str, object]) -> dict[str, object]:
        """Return the `fleet_planner_get` tactical fleet DAG and spare-ship allowance."""
        return handle_fleet_planner_get(self._ctx, self._names, self._ledgers, payload)

    def maybe_trigger_ai_drift_replan(self, *, now_ts: float | None = None) -> bool:
        """Check adaptive AI-Planner drift triggers non-blockingly and launch a background replan when due."""
        ctx = self._ctx
        cfg_ref = ctx.strategy_config
        if (
            not cfg_ref.ai_adaptive_execution
            or ctx.planner_closed
            or normalize_planner_mode(cfg_ref.planner_mode) != "ai_planner"
            or ctx.bot_control.get("paused")
            or ctx.bot_control.get("captcha_blocked")
        ):
            return False
        eff_now = safe_api_float(now_ts, time.time()) if now_ts is not None else time.time()
        if eff_now <= 0.0:
            eff_now = time.time()
        if (
            ctx.last_auto_ai_replan_ts > 0.0
            and (eff_now - ctx.last_auto_ai_replan_ts) < BridgeConstants.AI_DRIFT_REPLAN_COOLDOWN_SECONDS
        ):
            return False
        now_mono = time.monotonic()
        if (
            now_ts is None
            and ctx.last_drift_check_mono > 0.0
            and (now_mono - ctx.last_drift_check_mono) < BridgeConstants.AI_DRIFT_CHECK_MIN_INTERVAL_SECONDS
        ):
            return False
        with ctx.planner_jobs_lock:
            if ctx.config_opt_job.get("status") == "running" or ctx.ai_planner_job.get("status") == "running":
                return False

        if not ctx.state_lock.acquire(blocking=False):
            return False
        reason: str | None = None
        warm_genes: dict[str, object] | None = None
        try:
            if now_ts is None:
                now_mono_in = time.monotonic()
                if (
                    ctx.last_drift_check_mono > 0.0
                    and (now_mono_in - ctx.last_drift_check_mono) < BridgeConstants.AI_DRIFT_CHECK_MIN_INTERVAL_SECONDS
                ):
                    return False
                ctx.last_drift_check_mono = now_mono_in
            ev = evaluate_ai_drift_replan(ctx, self._ledgers, ctx.last_state, cfg=ctx.strategy_config, now_ts=now_ts)
            if not ev.get("eligible") or not ev.get("reason"):
                return False
            reason = str(ev["reason"])
            raw_warm = ev.get("warm_genes")
            warm_genes = dict(raw_warm) if isinstance(raw_warm, Mapping) else None
            ctx.last_auto_ai_replan_ts = safe_api_float(ev.get("now_ts"), eff_now)
            ctx.last_auto_ai_replan_reason = reason
            if self._on_persist_runtime_state is not None:
                self._on_persist_runtime_state()
        finally:
            ctx.state_lock.release()
        try:
            self._start_ai_job(
                params={"warm_start_genes": warm_genes} if warm_genes else None,
                sync=False,
                refresh=False,
                trigger_source=f"auto_drift:{reason}",
            )
            return True
        except _DRIFT_REPLAN_ERRORS:
            _LOGGER.warning("Background adaptive AI drift replan failed to start", exc_info=True)
            return False

    def record_adaptive_idle_if_due(self, state: GameState, now_ts: float | None = None) -> None:
        """Record one 10-minute adaptive colony-idle diagnostic bucket when the current bucket is unseen."""
        eff_now = safe_api_float(now_ts, time.time()) if now_ts is not None else time.time()
        if eff_now <= 0.0:
            eff_now = time.time()
        bucket_idx = int(eff_now // BridgeConstants.ADAPTIVE_IDLE_BUCKET_SECONDS)
        if bucket_idx in self._ctx.adaptive_idle_buckets:
            return
        active_ai_rec = self.get_active_ai_planner_locked()
        raw_res = active_ai_rec.get("result") if isinstance(active_ai_rec, Mapping) else None
        if isinstance(raw_res, Mapping):
            self._ledgers.record_adaptive_idle_bucket_locked(
                state,
                raw_res,
                cfg=self._ctx.strategy_config,
                now_ts=eff_now,
            )
