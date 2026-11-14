"""Config Optimizer background/sync job runner and shared job-lifecycle state helpers."""

from __future__ import annotations

from collections.abc import Callable, Mapping
import logging
import threading
import time

from spaceinvasion.core.config import StrategyConfig
from spaceinvasion.core.model import GameState
from spaceinvasion.planner import config_optimizer as co_mod
from spaceinvasion.runtime.api.validation import ApiRequestError
from spaceinvasion.runtime.bridge._context import BridgeContext
from spaceinvasion.runtime.bridge._helpers import BRIDGE_RUNTIME_ERRORS, redact_api_payload, safe_api_int
from spaceinvasion.runtime.bridge._planner_jobs_persist import persist_config_optimizer_result
from spaceinvasion.runtime.bridge._planner_jobs_state import (
    get_active_config_optimizer,
    get_planet_specializations,
    get_planner_state,
    get_reserved_floors,
    normalize_progress_pct,
)
from spaceinvasion.runtime.bridge._planner_jobs_telemetry import snapshot_universe_research_max_map
from spaceinvasion.runtime.bridge.planet_names import PlanetNameService
from spaceinvasion.runtime.bridge.state_cache import StateCacheService

_LOGGER = logging.getLogger(__name__)
WORKER_ERRORS: tuple[type[Exception], ...] = (ApiRequestError, *BRIDGE_RUNTIME_ERRORS)


def claim_planner_job(
    ctx: BridgeContext,
    *,
    kind: str,
    command: str,
    sync: bool,
    trigger_source: str,
    auto_replan_ai: bool,
) -> tuple[str | None, int, float, dict[str, object]]:
    """Claim a running planner job slot or return an `already_running` envelope."""
    with ctx.planner_jobs_lock:
        current = ctx.config_opt_job if kind == "config_optimizer" else ctx.ai_planner_job
        if not sync and current.get("status") == "running":
            return None, 0, 0.0, {"ok": True, "command": command, "already_running": True, "job": dict(current)}
        ctx.planner_job_seq += 1
        seq = ctx.planner_job_seq
        started_ts = time.time()
        job_id = f"{kind}-{seq}-{int(started_ts)}"
        new_job: dict[str, object] = {
            "job_id": job_id,
            "kind": kind,
            "status": "running",
            "phase": "starting",
            "progress": 0.0,
            "progress_pct": 0.0,
            "started_ts": started_ts,
            "finished_ts": None,
            "elapsed_ms": 0.0,
            "trigger_source": str(trigger_source or "manual"),
            "run_id": None,
            "error": None,
            "auto_replan_ai": bool(auto_replan_ai),
        }
        if kind == "config_optimizer":
            ctx.config_opt_job = new_job
        else:
            ctx.ai_planner_job = new_job
        return job_id, seq, started_ts, dict(new_job)


def update_job_progress(
    ctx: BridgeContext,
    *,
    kind: str,
    job_id: str,
    started_ts: float,
    phase: str,
    pct: float,
) -> None:
    """Update live phase and progress percentage for an active planner job."""
    norm_p = normalize_progress_pct(pct)
    with ctx.planner_jobs_lock:
        target = ctx.config_opt_job if kind == "config_optimizer" else ctx.ai_planner_job
        if target.get("job_id") == job_id:
            target["phase"] = str(phase or "running")
            target["progress"] = norm_p
            target["progress_pct"] = norm_p
            target["elapsed_ms"] = round(max(0.0, (time.time() - started_ts) * 1000.0), 1)


def finish_job_record(
    ctx: BridgeContext,
    *,
    kind: str,
    job_id: str,
    elapsed_ms: float,
    run_id: int | None = None,
    error: str | None = None,
) -> tuple[dict[str, object], bool]:
    """Mark a planner job completed or errored and return `(job_copy, planner_closed)`."""
    with ctx.planner_jobs_lock:
        target = ctx.config_opt_job if kind == "config_optimizer" else ctx.ai_planner_job
        if target.get("job_id") == job_id:
            if error is None:
                target.update(
                    {
                        "status": "completed",
                        "phase": "completed",
                        "progress": 100.0,
                        "progress_pct": 100.0,
                        "finished_ts": time.time(),
                        "elapsed_ms": elapsed_ms,
                        "run_id": run_id,
                        "error": None,
                    }
                )
            else:
                target.update(
                    {
                        "status": "error",
                        "phase": "error",
                        "finished_ts": time.time(),
                        "elapsed_ms": elapsed_ms,
                        "error": error,
                    }
                )
        return dict(target), ctx.planner_closed


def _prepare_config_opt_params(
    active_co: Mapping[str, object] | None,
    persisted_specs: Mapping[str, str],
    custom_names: Mapping[str, str],
    reserved_floors: Mapping[str, Mapping[str, int]],
    cfg: StrategyConfig,
    params: Mapping[str, object] | None,
) -> tuple[dict[str, object], dict[str, dict[str, int]], dict[str, object] | None]:
    raw_prev = active_co.get("result") if isinstance(active_co, Mapping) else None
    previous_result: dict[str, object] | None = dict(raw_prev) if isinstance(raw_prev, Mapping) else None
    base_params: dict[str, object] = dict(co_mod.DEFAULT_CONFIG_OPTIMIZER_PARAMS)
    if isinstance(active_co, Mapping) and isinstance(active_co.get("params"), Mapping):
        raw_ap = active_co["params"]
        if isinstance(raw_ap, Mapping):
            base_params.update(dict(raw_ap))
    base_params["planet_specializations"] = dict(persisted_specs)
    base_params["planet_names"] = dict(custom_names)
    base_params["custom_planet_names"] = dict(custom_names)
    base_params["max_planets"] = max(1, safe_api_int(cfg.max_planets, 11))
    if isinstance(params, Mapping):
        base_params.update(dict(params))
        if "planet_specializations" in params:
            base_params["planet_specializations"] = co_mod.normalize_planet_specializations_map(
                params.get("planet_specializations")
            )
    merged_floors: dict[str, dict[str, int]] = {
        str(pk): {str(bk): max(0, safe_api_int(lv, 0)) for bk, lv in bm.items()}
        for pk, bm in reserved_floors.items()
        if isinstance(bm, Mapping)
    }
    raw_param_floors = base_params.get("reserved_floors_by_planet")
    if isinstance(raw_param_floors, Mapping):
        for pk, bm in raw_param_floors.items():
            if not isinstance(bm, Mapping):
                continue
            slot_f = merged_floors.setdefault(str(pk), {})
            for bk, lv in bm.items():
                slot_f[str(bk)] = max(slot_f.get(str(bk), 0), max(0, safe_api_int(lv, 0)))
    base_params["reserved_floors_by_planet"] = merged_floors
    return base_params, merged_floors, previous_result


def _run_config_opt_worker(
    ctx: BridgeContext,
    *,
    job_id: str,
    started_ts: float,
    snap_state: GameState,
    uni_max: Mapping[str, int],
    base_params: Mapping[str, object],
    merged_floors: Mapping[str, Mapping[str, int]],
    custom_names: Mapping[str, str],
    previous_result: Mapping[str, object] | None,
    trigger_source: str,
    auto_replan_ai: bool,
    sync: bool,
    start_ai_fn: Callable[..., dict[str, object]] | None,
    on_publish_snapshot: Callable[[], None] | None,
    outcome: dict[str, object],
) -> None:
    def _progress(phase: str, pct: float) -> None:
        update_job_progress(ctx, kind="config_optimizer", job_id=job_id, started_ts=started_ts, phase=phase, pct=pct)

    t0 = time.monotonic()
    try:
        res = co_mod.run_config_optimizer(
            snap_state,
            universe_research_max=uni_max,
            params=base_params,
            planet_names=custom_names,
            previous_result=previous_result,
            progress_cb=_progress,
        )
        elapsed_ms = round((time.monotonic() - t0) * 1000.0, 2)
        run_id = persist_config_optimizer_result(
            ctx,
            res,
            params=base_params,
            reserved_floors=merged_floors,
            trigger_source=str(trigger_source or "manual"),
            elapsed_ms=elapsed_ms,
            on_publish_snapshot=None,
        )
        final_job, closed_now = finish_job_record(
            ctx, kind="config_optimizer", job_id=job_id, elapsed_ms=elapsed_ms, run_id=run_id
        )
        outcome["result"] = res
        outcome["run_id"] = run_id
        outcome["job"] = final_job
        if on_publish_snapshot is not None:
            on_publish_snapshot()
        if auto_replan_ai and not closed_now and start_ai_fn is not None:
            try:
                ai_resp = start_ai_fn(
                    params=None,
                    sync=sync,
                    trigger_source=f"auto_replan:{trigger_source}",
                    auto_run_config_if_missing=False,
                    override_config_run_id=run_id,
                    override_config_result=res,
                )
                outcome["ai_planner_job"] = ai_resp.get("job")
                if sync and "result" in ai_resp:
                    outcome["ai_planner_result"] = ai_resp["result"]
            except WORKER_ERRORS as ai_exc:
                _LOGGER.warning("Auto AI-Planner replan after Config Optimizer failed", exc_info=True)
                outcome["ai_planner_error"] = str(redact_api_payload(ai_exc))
    except WORKER_ERRORS as exc:
        elapsed_ms = round((time.monotonic() - t0) * 1000.0, 2)
        final_job, _ = finish_job_record(
            ctx, kind="config_optimizer", job_id=job_id, elapsed_ms=elapsed_ms, error=str(redact_api_payload(exc))
        )
        outcome["job"] = final_job
        outcome["exc"] = exc
        if on_publish_snapshot is not None:
            on_publish_snapshot()


def start_config_optimizer_job(
    ctx: BridgeContext,
    names: PlanetNameService,
    state_cache: StateCacheService,
    *,
    params: Mapping[str, object] | None = None,
    auto_replan_ai: bool = True,
    sync: bool = False,
    trigger_source: str = "manual",
    start_ai_fn: Callable[..., dict[str, object]] | None = None,
    on_publish_snapshot: Callable[[], None] | None = None,
) -> dict[str, object]:
    """Start (synchronously or in a daemon thread) a Config Optimizer optimization run."""
    with ctx.state_lock:
        snap_state = get_planner_state(ctx)
        if snap_state is None:
            raise ApiRequestError("No game state snapshot available yet; open /state or /dashboard first.")
        uni_max = snapshot_universe_research_max_map(state_cache, snap_state)
        reserved_floors = get_reserved_floors(ctx)
        persisted_specs = get_planet_specializations(ctx)
        custom_names = names.get_custom_planet_names_locked()
        active_co = get_active_config_optimizer(ctx)
        cfg = ctx.strategy_config

    base_params, merged_floors, previous_result = _prepare_config_opt_params(
        active_co, persisted_specs, custom_names, reserved_floors, cfg, params
    )
    job_id, seq, started_ts, initial_or_busy = claim_planner_job(
        ctx,
        kind="config_optimizer",
        command="config_optimizer_run",
        sync=sync,
        trigger_source=trigger_source,
        auto_replan_ai=auto_replan_ai,
    )
    if job_id is None:
        return initial_or_busy
    if on_publish_snapshot is not None:
        on_publish_snapshot()

    outcome: dict[str, object] = {}

    def _worker() -> None:
        _run_config_opt_worker(
            ctx,
            job_id=job_id,
            started_ts=started_ts,
            snap_state=snap_state,
            uni_max=uni_max,
            base_params=base_params,
            merged_floors=merged_floors,
            custom_names=custom_names,
            previous_result=previous_result,
            trigger_source=trigger_source,
            auto_replan_ai=auto_replan_ai,
            sync=sync,
            start_ai_fn=start_ai_fn,
            on_publish_snapshot=on_publish_snapshot,
            outcome=outcome,
        )

    if sync:
        _worker()
        exc_obj = outcome.get("exc")
        if isinstance(exc_obj, ApiRequestError):
            raise exc_obj
        if isinstance(exc_obj, Exception):
            raise ApiRequestError(f"Config Optimizer failed: {redact_api_payload(exc_obj)}") from exc_obj
        resp: dict[str, object] = {
            "ok": True,
            "command": "config_optimizer_run",
            "job": outcome["job"],
            "run_id": outcome.get("run_id"),
            "result": outcome.get("result"),
        }
        if "ai_planner_job" in outcome:
            resp["ai_planner_job"] = outcome["ai_planner_job"]
        return resp

    threading.Thread(target=_worker, name=f"si-config-opt-{seq}", daemon=True).start()
    return {"ok": True, "command": "config_optimizer_run", "job": initial_or_busy}
