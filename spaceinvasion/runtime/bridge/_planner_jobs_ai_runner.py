"""AI-Planner background/sync job runner and optional live refresh / Config Optimizer bootstrap helpers."""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
import logging
import threading
import time

from spaceinvasion.core.config import StrategyConfig
from spaceinvasion.core.model import GameState
from spaceinvasion.planner import ai as ai_mod
from spaceinvasion.planner import config_optimizer as co_mod
from spaceinvasion.runtime.api.validation import ApiRequestError
from spaceinvasion.runtime.bridge._context import BridgeContext
from spaceinvasion.runtime.bridge._helpers import (
    BRIDGE_RUNTIME_ERRORS,
    redact_api_payload,
    safe_api_float,
    safe_api_int,
)
from spaceinvasion.runtime.bridge._planner_jobs_co_runner import (
    WORKER_ERRORS,
    claim_planner_job,
    finish_job_record,
    update_job_progress,
)
from spaceinvasion.runtime.bridge._planner_jobs_persist import (
    persist_ai_planner_result,
    persist_config_optimizer_result,
)
from spaceinvasion.runtime.bridge._planner_jobs_state import (
    get_active_ai_planner,
    get_active_config_optimizer,
    get_planner_state,
    get_reserved_floors,
)
from spaceinvasion.runtime.bridge._planner_jobs_telemetry import (
    snapshot_attack_history,
    snapshot_universe_research_max_map,
)
from spaceinvasion.runtime.bridge.ledgers import LedgerTelemetryService
from spaceinvasion.runtime.bridge.planet_names import PlanetNameService
from spaceinvasion.runtime.bridge.state_cache import StateCacheService
from spaceinvasion.session.auth import AntiBotChallengeError, SpaceInvasionAuthError
from spaceinvasion.session.client import RateLimitError

_LOGGER = logging.getLogger(__name__)
_REFRESH_ERRORS: tuple[type[Exception], ...] = (SpaceInvasionAuthError, *BRIDGE_RUNTIME_ERRORS)


def _maybe_refresh_live_state_for_ai(
    ctx: BridgeContext,
    state_cache: StateCacheService,
    *,
    on_antibot_pause: Callable[[BaseException], None] | None,
) -> None:
    with ctx.lock:
        if (
            not ctx.is_browser_port_open()
            or ctx.page_limiter.rate_limit_remaining() > 0.0
            or ctx.bot_control.get("captcha_blocked")
        ):
            return
        try:
            with ctx.client_factory(ctx.debug_port) as client:
                fresh_snap = state_cache.read_snapshot(
                    client,
                    include_buildings=True,
                    include_research=True,
                    include_shipyard=True,
                    return_home=False,
                    priority="normal",
                )
                obs_active = bool(client.last_snapshot_observed_active_planet)
                state_cache.record_state(
                    fresh_snap,
                    fetched_buildings=True,
                    fetched_research=True,
                    fetched_shipyard=True,
                    fetched_home=True,
                    observed_active_planet=obs_active,
                )
                cur_url = str(client.driver.current_url or "")
                if cur_url:
                    ctx.last_url = cur_url
        except RateLimitError as exc:
            ctx.record_server_rate_limit(exc)
        except AntiBotChallengeError as exc:
            if on_antibot_pause is not None:
                on_antibot_pause(exc)
        except _REFRESH_ERRORS:
            _LOGGER.warning(
                "Optional live state refresh before AI-Planner run failed; using cached state", exc_info=True
            )


def _bootstrap_config_optimizer_for_ai(
    ctx: BridgeContext,
    display_state: GameState,
    *,
    uni_max: Mapping[str, int],
    reserved_floors: Mapping[str, Mapping[str, int]],
    custom_names: Mapping[str, str],
    cfg: StrategyConfig,
    base_params: Mapping[str, object],
) -> tuple[int, dict[str, object]]:
    co_params: dict[str, object] = dict(co_mod.DEFAULT_CONFIG_OPTIMIZER_PARAMS)
    co_params["max_planets"] = max(1, safe_api_int(cfg.max_planets, 11))
    co_params["pad_generic_colonies"] = bool(base_params.get("pad_generic_colonies", False))
    co_params["reserved_floors_by_planet"] = reserved_floors
    co_params["planet_names"] = dict(custom_names)
    co_params["custom_planet_names"] = dict(custom_names)
    co_params["catalogue_grid_size"] = min(safe_api_int(co_params.get("catalogue_grid_size", 25000), 25000), 4000)
    co_params["ga_population_size"] = min(safe_api_int(co_params.get("ga_population_size", 200), 200), 32)
    co_params["ga_generations"] = min(safe_api_int(co_params.get("ga_generations", 150), 150), 24)
    t_co = time.monotonic()
    co_res = co_mod.run_config_optimizer(display_state, universe_research_max=uni_max, params=co_params)
    co_elapsed = round((time.monotonic() - t_co) * 1000.0, 2)
    run_id = persist_config_optimizer_result(
        ctx,
        co_res,
        params=co_params,
        reserved_floors=reserved_floors,
        trigger_source="auto_for_ai_planner",
        elapsed_ms=co_elapsed,
    )
    return run_id, dict(co_res)


def _run_ai_planner_worker(
    ctx: BridgeContext,
    *,
    job_id: str,
    started_ts: float,
    display_state: GameState,
    target_config: Mapping[str, object] | None,
    config_run_id: int | None,
    uni_max: Mapping[str, int],
    reserved_floors: Mapping[str, Mapping[str, int]],
    custom_names: Mapping[str, str],
    cfg: StrategyConfig,
    base_params: Mapping[str, object],
    persisted_params: Mapping[str, object],
    raid_rows: Sequence[Mapping[str, object]],
    message_rows: Sequence[Mapping[str, object]],
    realized_loot: Mapping[str, object] | None,
    trigger_source: str,
    on_publish_snapshot: Callable[[], None] | None,
    outcome: dict[str, object],
) -> None:
    def _progress(phase: str, pct: float) -> None:
        update_job_progress(ctx, kind="ai_planner", job_id=job_id, started_ts=started_ts, phase=phase, pct=pct)

    eff_target = target_config
    eff_co_run_id = config_run_id
    t0 = time.monotonic()
    try:
        if eff_target is None:
            _progress("config_optimizer_bootstrap", 5.0)
            eff_co_run_id, eff_target = _bootstrap_config_optimizer_for_ai(
                ctx,
                display_state,
                uni_max=uni_max,
                reserved_floors=reserved_floors,
                custom_names=custom_names,
                cfg=cfg,
                base_params=base_params,
            )
        _progress("attack_telemetry", 10.0)
        if realized_loot is not None:
            raw_hl = realized_loot.get("hourly_loot")
            hl_map = dict(raw_hl) if isinstance(raw_hl, Mapping) else None
            telemetry = ai_mod.compute_attack_telemetry(
                raid_rows,
                message_rows,
                state=display_state,
                realized_hourly_loot=hl_map,
                realized_missions_per_hour=safe_api_float(realized_loot.get("missions_per_hour"), 0.0),
            )
        else:
            telemetry = ai_mod.compute_attack_telemetry(raid_rows, message_rows, state=display_state)
        res = ai_mod.run_ai_planner(
            display_state,
            eff_target,
            attack_telemetry=telemetry,
            params=base_params,
            progress_cb=_progress,
        )
        elapsed_ms = round((time.monotonic() - t0) * 1000.0, 2)
        run_id = persist_ai_planner_result(
            ctx,
            res,
            config_run_id=eff_co_run_id,
            params=persisted_params,
            trigger_source=str(trigger_source or "manual"),
            elapsed_ms=elapsed_ms,
            on_publish_snapshot=None,
        )
        final_job, _ = finish_job_record(ctx, kind="ai_planner", job_id=job_id, elapsed_ms=elapsed_ms, run_id=run_id)
        outcome["result"] = res
        outcome["run_id"] = run_id
        outcome["job"] = final_job
        if on_publish_snapshot is not None:
            on_publish_snapshot()
    except WORKER_ERRORS as exc:
        elapsed_ms = round((time.monotonic() - t0) * 1000.0, 2)
        final_job, _ = finish_job_record(
            ctx, kind="ai_planner", job_id=job_id, elapsed_ms=elapsed_ms, error=str(redact_api_payload(exc))
        )
        outcome["job"] = final_job
        outcome["exc"] = exc
        if on_publish_snapshot is not None:
            on_publish_snapshot()


def _resolve_ai_job_params(
    active_co: Mapping[str, object] | None,
    active_ai: Mapping[str, object] | None,
    *,
    params: Mapping[str, object] | None,
    auto_run_config_if_missing: bool,
    override_config_run_id: int | None,
    override_config_result: Mapping[str, object] | None,
) -> tuple[Mapping[str, object] | None, int | None, dict[str, object], dict[str, object]]:
    target_config: Mapping[str, object] | None = override_config_result
    config_run_id: int | None = override_config_run_id
    if target_config is None and isinstance(active_co, Mapping) and isinstance(active_co.get("result"), Mapping):
        raw_co_res = active_co["result"]
        if isinstance(raw_co_res, Mapping):
            target_config = raw_co_res
        config_run_id = safe_api_int(active_co.get("run_id"), 0) or None
    if target_config is None and not auto_run_config_if_missing:
        raise ApiRequestError("No active Config Optimizer target found. Run Config Optimization first.")
    base_params: dict[str, object] = dict(ai_mod.DEFAULT_AI_PLANNER_PARAMS)
    if isinstance(active_ai, Mapping) and isinstance(active_ai.get("params"), Mapping):
        raw_ai_p = active_ai["params"]
        if isinstance(raw_ai_p, Mapping):
            base_params.update({k: v for k, v in raw_ai_p.items() if k != "warm_start_genes"})
    if isinstance(params, Mapping):
        base_params.update(dict(params))
    persisted_params = {k: v for k, v in base_params.items() if k != "warm_start_genes"}
    return target_config, config_run_id, base_params, persisted_params


def start_ai_planner_job(
    ctx: BridgeContext,
    names: PlanetNameService,
    state_cache: StateCacheService,
    ledgers: LedgerTelemetryService,
    *,
    params: Mapping[str, object] | None = None,
    sync: bool = False,
    refresh: bool = False,
    trigger_source: str = "manual",
    auto_run_config_if_missing: bool = True,
    override_config_run_id: int | None = None,
    override_config_result: Mapping[str, object] | None = None,
    on_publish_snapshot: Callable[[], None] | None = None,
    on_antibot_pause: Callable[[BaseException], None] | None = None,
) -> dict[str, object]:
    """Start (synchronously or in a daemon thread) an AI-Planner trajectory optimization run."""
    if refresh:
        _maybe_refresh_live_state_for_ai(ctx, state_cache, on_antibot_pause=on_antibot_pause)

    with ctx.state_lock:
        snap_state = get_planner_state(ctx)
        if snap_state is None:
            raise ApiRequestError("No game state snapshot available yet; open /state or /dashboard first.")
        snap_state = state_cache.merge_economy_rates(snap_state)
        custom_names = names.get_custom_planet_names_locked()
        display_state = names.apply_custom_planet_names_to_state(snap_state, custom_names) or snap_state
        active_co = get_active_config_optimizer(ctx)
        active_ai = get_active_ai_planner(ctx)
        raid_rows, message_rows = snapshot_attack_history(ctx, ledgers)
        uni_max = snapshot_universe_research_max_map(state_cache, snap_state)
        reserved_floors = get_reserved_floors(ctx)
        cfg = ctx.strategy_config
        realized_loot = ledgers.get_realized_loot_hourly_locked() if cfg.ai_adaptive_execution else None

    target_config, config_run_id, base_params, persisted_params = _resolve_ai_job_params(
        active_co,
        active_ai,
        params=params,
        auto_run_config_if_missing=auto_run_config_if_missing,
        override_config_run_id=override_config_run_id,
        override_config_result=override_config_result,
    )
    job_id, seq, started_ts, initial_or_busy = claim_planner_job(
        ctx, kind="ai_planner", command="ai_planner_run", sync=sync, trigger_source=trigger_source, auto_replan_ai=False
    )
    if job_id is None:
        return initial_or_busy
    if on_publish_snapshot is not None:
        on_publish_snapshot()

    outcome: dict[str, object] = {}

    def _worker() -> None:
        _run_ai_planner_worker(
            ctx,
            job_id=job_id,
            started_ts=started_ts,
            display_state=display_state,
            target_config=target_config,
            config_run_id=config_run_id,
            uni_max=uni_max,
            reserved_floors=reserved_floors,
            custom_names=custom_names,
            cfg=cfg,
            base_params=base_params,
            persisted_params=persisted_params,
            raid_rows=raid_rows,
            message_rows=message_rows,
            realized_loot=realized_loot,
            trigger_source=trigger_source,
            on_publish_snapshot=on_publish_snapshot,
            outcome=outcome,
        )

    if sync:
        _worker()
        exc_obj = outcome.get("exc")
        if isinstance(exc_obj, ApiRequestError):
            raise exc_obj
        if isinstance(exc_obj, Exception):
            raise ApiRequestError(f"AI-Planner failed: {redact_api_payload(exc_obj)}") from exc_obj
        return {
            "ok": True,
            "command": "ai_planner_run",
            "job": outcome["job"],
            "run_id": outcome.get("run_id"),
            "result": outcome.get("result"),
        }

    threading.Thread(target=_worker, name=f"si-ai-planner-{seq}", daemon=True).start()
    return {"ok": True, "command": "ai_planner_run", "job": initial_or_busy}
