"""Persistence helpers for Config Optimizer and AI-Planner run records."""

from __future__ import annotations

from collections.abc import Callable, Mapping
import time

from spaceinvasion.runtime.bridge._context import BridgeContext


def persist_config_optimizer_result(
    ctx: BridgeContext,
    result: Mapping[str, object],
    *,
    params: Mapping[str, object],
    reserved_floors: Mapping[str, object],
    trigger_source: str,
    elapsed_ms: float,
    on_publish_snapshot: Callable[[], None] | None = None,
) -> int:
    """Persist a Config Optimizer run to DB or in-memory store and refresh caches."""
    with ctx.state_lock:
        if ctx.planner_closed:
            return 0
        if ctx.db is not None:
            run_id = int(
                ctx.db.record_config_optimizer_run(
                    result,
                    params=params,
                    reserved_floors=reserved_floors,
                    trigger_source=trigger_source,
                    elapsed_ms=elapsed_ms,
                    set_active=True,
                )
            )
        else:
            run_id = len(ctx.config_opt_runs_mem) + 1
            rec: dict[str, object] = {
                "run_id": run_id,
                "updated_ts": round(time.time(), 3),
                "trigger_source": trigger_source,
                "elapsed_ms": round(elapsed_ms, 2),
                "params": dict(params),
                "reserved_floors": {str(k): dict(v) for k, v in reserved_floors.items() if isinstance(v, Mapping)},
                "result": dict(result),
            }
            ctx.config_opt_runs_mem.append(rec)
            ctx.active_config_opt_mem = rec
            raw_specs = params.get("planet_specializations")
            if isinstance(raw_specs, Mapping):
                ctx.planet_specializations_mem = {
                    str(k): str(v) for k, v in raw_specs.items() if str(v) and str(v) != "auto"
                }
        ctx.invalidate_dashboard_cache()
    if on_publish_snapshot is not None:
        on_publish_snapshot()
    return run_id


def persist_ai_planner_result(
    ctx: BridgeContext,
    result: Mapping[str, object],
    *,
    config_run_id: int | None,
    params: Mapping[str, object],
    trigger_source: str,
    elapsed_ms: float,
    on_publish_snapshot: Callable[[], None] | None = None,
) -> int:
    """Persist an AI-Planner run to DB or in-memory store and refresh caches."""
    with ctx.state_lock:
        if ctx.planner_closed:
            return 0
        if ctx.db is not None:
            run_id = int(
                ctx.db.record_ai_planner_run(
                    result,
                    config_run_id=config_run_id,
                    params=params,
                    trigger_source=trigger_source,
                    elapsed_ms=elapsed_ms,
                    set_active=True,
                )
            )
        else:
            run_id = len(ctx.ai_planner_runs_mem) + 1
            raw_sum = result.get("summary")
            rec: dict[str, object] = {
                "run_id": run_id,
                "config_run_id": config_run_id,
                "updated_ts": round(time.time(), 3),
                "trigger_source": trigger_source,
                "elapsed_ms": round(elapsed_ms, 2),
                "params": dict(params),
                "summary": dict(raw_sum) if isinstance(raw_sum, Mapping) else {},
                "result": dict(result),
            }
            ctx.ai_planner_runs_mem.append(rec)
            ctx.active_ai_plan_mem = rec
        ctx.invalidate_dashboard_cache()
    if on_publish_snapshot is not None:
        on_publish_snapshot()
    return run_id
