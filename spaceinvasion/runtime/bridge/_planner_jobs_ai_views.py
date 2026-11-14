"""AI-Planner `get` and `run` command handler helpers."""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
import math

from spaceinvasion.core.coords import normalize_coords
from spaceinvasion.core.model import GameState
from spaceinvasion.planner import ai as ai_mod
from spaceinvasion.planner.graph import build_ai_planner_graph
from spaceinvasion.runtime.api.validation import ApiRequestError
from spaceinvasion.runtime.bridge._context import BridgeContext
from spaceinvasion.runtime.bridge._context_init import BridgeConstants
from spaceinvasion.runtime.bridge._helpers import (
    GalaxyScanInProgressError,
    bounded_api_str,
    parse_command_int,
    safe_api_int,
    sanitize_json_dict,
)
from spaceinvasion.runtime.bridge._planner_jobs_state import get_planner_state, list_ai_planner_runs
from spaceinvasion.runtime.bridge._planner_jobs_telemetry import (
    compute_live_attack_telemetry,
    evaluate_ai_drift_replan,
    evaluate_ai_planner_gate,
    get_planner_jobs_snapshot,
)
from spaceinvasion.runtime.bridge.ledgers import LedgerTelemetryService
from spaceinvasion.runtime.bridge.planet_names import PlanetNameService

_DELAY_KEYS: tuple[str, ...] = (
    "transport_dispatch_delay_minutes",
    "PATH_TRANSPORT_DISPATCH_DELAY_MINUTES",
    "fleet_dispatch_delay_minutes",
)


def _parse_ai_get_filters(
    payload: Mapping[str, object],
) -> tuple[int, int | None, int | str | None, int, int, str, int]:
    window_size = parse_command_int("window_size", payload.get("window_size", 40), min_val=5, max_val=200) or 40
    week_filter = parse_command_int("week", payload.get("week"), allow_none=True, min_val=1, max_val=520)
    raw_pid = payload.get("planet_id")
    planet_filter: int | str | None = None
    if raw_pid not in (None, "", "all"):
        if isinstance(raw_pid, int) and not isinstance(raw_pid, bool):
            planet_filter = int(raw_pid)
        elif isinstance(raw_pid, str) and raw_pid.strip():
            s_pf = raw_pid.strip()
            planet_filter = int(s_pf) if s_pf.isascii() and s_pf.isdigit() else s_pf
        else:
            raise ApiRequestError("'ai_planner_get' field 'planet_id' must be an integer or string.")
    page = parse_command_int("page", payload.get("page", 1), min_val=1, max_val=100_000) or 1
    page_size = parse_command_int("page_size", payload.get("page_size", 50), min_val=5, max_val=500) or 50
    search_raw = payload.get("search")
    search_q = search_raw.strip().lower() if isinstance(search_raw, str) else ""
    runs_limit = parse_command_int("limit", payload.get("limit", 20), min_val=1, max_val=200) or 20
    return int(window_size), week_filter, planet_filter, int(page), int(page_size), search_q, int(runs_limit)


def _filter_ai_steps(
    all_steps: Sequence[Mapping[str, object]],
    *,
    week_filter: int | None,
    planet_filter: int | str | None,
    search_q: str,
    page: int,
    page_size: int,
) -> tuple[list[dict[str, object]], int]:
    filtered: list[dict[str, object]] = []
    for st_item in all_steps:
        if week_filter is not None and safe_api_int(st_item.get("week"), 0) != int(week_filter):
            continue
        if planet_filter is not None:
            spid = st_item.get("planet_id")
            scoords = str(st_item.get("planet_coords") or "")
            if isinstance(planet_filter, int):
                if safe_api_int(spid, -1) != planet_filter:
                    continue
            elif (
                str(spid) != planet_filter
                and scoords != planet_filter
                and str(st_item.get("planet_name") or "") != planet_filter
            ):
                continue
        if search_q:
            haystack = " ".join(
                str(st_item.get(k) or "")
                for k in ("seq", "kind", "entity_key", "entity_name", "planet_name", "planet_coords", "week")
            ).lower()
            if search_q not in haystack and f"w{st_item.get('week', '')}" not in search_q:
                continue
        filtered.append(dict(st_item))
    offset = (page - 1) * page_size
    return filtered[offset : offset + page_size], len(filtered)


def _patch_smelting_custom_names(
    raw_smelting: object,
    snap_state: GameState | None,
    custom_names: Mapping[str, str],
) -> dict[str, object] | None:
    if not isinstance(raw_smelting, Mapping):
        return None
    smelting_snap: dict[str, object] = dict(raw_smelting)
    coords_to_cname: dict[str, str] = {}
    if snap_state is not None:
        for pl_s in snap_state.planets:
            cn_s = custom_names.get(str(pl_s.planet_id))
            nc_s = normalize_coords(pl_s.coords)
            if cn_s and nc_s:
                coords_to_cname[nc_s] = cn_s
    for list_key in ("planets", "initial_planets_needing_toggle"):
        raw_rows = smelting_snap.get(list_key)
        if isinstance(raw_rows, Sequence) and not isinstance(raw_rows, (str, bytes)):
            patched_rows: list[dict[str, object]] = []
            for row in raw_rows:
                if not isinstance(row, Mapping):
                    continue
                rd = dict(row)
                rpid = safe_api_int(rd.get("planet_id"), 0)
                if rpid > 0 and custom_names.get(str(rpid)):
                    rd["name"] = custom_names[str(rpid)]
                patched_rows.append(rd)
            smelting_snap[list_key] = patched_rows
    raw_evs = smelting_snap.get("events")
    if isinstance(raw_evs, Sequence) and not isinstance(raw_evs, (str, bytes)):
        patched_evs: list[dict[str, object]] = []
        for ev in raw_evs:
            if not isinstance(ev, Mapping):
                continue
            evd = dict(ev)
            ev_pid = safe_api_int(evd.get("step_planet_id"), 0)
            ev_nc = normalize_coords(str(evd.get("step_planet_coords") or ""))
            ev_cn = (custom_names.get(str(ev_pid)) if ev_pid > 0 else None) or (
                coords_to_cname.get(ev_nc) if ev_nc else None
            )
            if ev_cn:
                evd["step_planet_name"] = ev_cn
            patched_evs.append(evd)
        smelting_snap["events"] = patched_evs
    return smelting_snap


def _extract_search_snap(active_result: Mapping[str, object] | None) -> dict[str, object] | None:
    if not isinstance(active_result, Mapping):
        return None
    raw_s = active_result.get("search")
    if not isinstance(raw_s, Mapping):
        return None
    raw_bp = raw_s.get("best_parameters")
    raw_tg = raw_s.get("tuned_genes")
    return {
        "best_strategy": raw_s.get("best_strategy"),
        "best_parameters": dict(raw_bp) if isinstance(raw_bp, Mapping) else None,
        "tuned_genes": dict(raw_tg) if isinstance(raw_tg, Mapping) else None,
        "best_reward": raw_s.get("best_reward"),
        "genetic_algorithm_ran": bool(raw_s.get("genetic_algorithm_ran", False)),
        "why": raw_s.get("why"),
    }


def _snapshot_ai_planner_view_state(
    ctx: BridgeContext,
    names: PlanetNameService,
    ledgers: LedgerTelemetryService,
    runs_limit: int,
) -> tuple[
    GameState | None,
    GameState | None,
    dict[str, str],
    dict[str, object],
    dict[str, object],
    list[dict[str, object]],
    Mapping[str, object] | None,
    dict[str, object],
    dict[str, object] | None,
    dict[str, object],
]:
    with ctx.state_lock:
        snap_state = get_planner_state(ctx)
        gate = evaluate_ai_planner_gate(ctx, snap_state)
        custom_names = names.get_custom_planet_names_locked()
        display_state = (
            names.apply_custom_planet_names_to_state(snap_state, custom_names) if snap_state is not None else None
        )
        jobs_snap = get_planner_jobs_snapshot(ctx, snap_state)
        runs = list_ai_planner_runs(ctx, limit=runs_limit)
        raw_active = gate.get("active_result")
        active_result: Mapping[str, object] | None = raw_active if isinstance(raw_active, Mapping) else None
        live_telemetry = compute_live_attack_telemetry(ctx, ledgers, snap_state)
        cfg_snap = ctx.strategy_config
        raw_td = active_result.get("transport_demand") if active_result is not None else None
        td_map: Mapping[str, object] | None = raw_td if isinstance(raw_td, Mapping) else None
        transport_fleet_snap = ledgers.build_transport_fleet_summary_locked(snap_state, td_map)
        realized_util_snap = (
            transport_fleet_snap["realized_utilization"]
            if isinstance(transport_fleet_snap, Mapping)
            and transport_fleet_snap.get("realized_utilization") is not None
            else round(
                min(
                    BridgeConstants.TRANSPORT_UTIL_MAX,
                    max(BridgeConstants.TRANSPORT_UTIL_MIN, float(ctx.transport_realized_utilization)),
                ),
                4,
            )
        )
        transport_demand_snap: dict[str, object] | None = None
        if td_map is not None:
            transport_demand_snap = dict(td_map)
            transport_demand_snap["realized_utilization"] = realized_util_snap
        drift_eval = evaluate_ai_drift_replan(ctx, ledgers, snap_state, cfg=cfg_snap, gate=gate)
        auto_replan_snap: dict[str, object] = {
            "cooldown_seconds": int(BridgeConstants.AI_DRIFT_REPLAN_COOLDOWN_SECONDS),
            "last_auto_replan_ts": (
                round(float(ctx.last_auto_ai_replan_ts), 3) if ctx.last_auto_ai_replan_ts > 0.0 else None
            ),
            "last_trigger_reason": str(ctx.last_auto_ai_replan_reason or ""),
            "drift_ready": bool(drift_eval.get("drift_ready", False)),
        }
        raw_conflicts = jobs_snap.get("pending_field_conflicts")
        conflicts_seq: Sequence[Mapping[str, object]] = (
            [c for c in raw_conflicts if isinstance(c, Mapping)] if isinstance(raw_conflicts, Sequence) else ()
        )
        adaptive_status = ledgers.build_adaptive_status_locked(
            display_state if display_state is not None else snap_state,
            active_result,
            cfg=cfg_snap,
            field_conflicts=conflicts_seq,
            custom_names=custom_names,
            transport_fleet=transport_fleet_snap,
            auto_replan=auto_replan_snap,
        )
    return (
        snap_state,
        display_state,
        custom_names,
        gate,
        jobs_snap,
        runs,
        active_result,
        live_telemetry,
        transport_demand_snap,
        adaptive_status,
    )


def handle_ai_planner_get(
    ctx: BridgeContext,
    names: PlanetNameService,
    ledgers: LedgerTelemetryService,
    payload: Mapping[str, object],
) -> dict[str, object]:
    """Assemble the complete `ai_planner_get` response including timeline, smelting, adaptive status, and DAG."""
    window_size, week_filter, planet_filter, page, page_size, search_q, runs_limit = _parse_ai_get_filters(payload)
    default_params = dict(ai_mod.DEFAULT_AI_PLANNER_PARAMS)
    (
        snap_state,
        display_state,
        custom_names,
        gate,
        jobs_snap,
        runs,
        active_result,
        live_telemetry,
        transport_demand_snap,
        adaptive_status,
    ) = _snapshot_ai_planner_view_state(ctx, names, ledgers, runs_limit)

    raw_sum = active_result.get("summary") if active_result is not None else None
    raw_mp = active_result.get("meta_parameters") if active_result is not None else None
    raw_wb = active_result.get("weekly_breakdown") if active_result is not None else None
    raw_pt = active_result.get("attack_telemetry") if active_result is not None else None
    raw_steps = active_result.get("steps") if active_result is not None else None
    raw_ip = active_result.get("in_progress") if active_result is not None else None
    raw_tm = active_result.get("transport_matrix") if active_result is not None else None

    all_steps = names.overlay_custom_names_on_ai_items(
        [s for s in raw_steps if isinstance(s, Mapping)]
        if isinstance(raw_steps, Sequence) and not isinstance(raw_steps, (str, bytes))
        else (),
        snap_state,
        custom_names,
    )
    page_items, total_steps = _filter_ai_steps(
        all_steps,
        week_filter=week_filter,
        planet_filter=planet_filter,
        search_q=search_q,
        page=page,
        page_size=page_size,
    )
    eff_display = display_state if display_state is not None else snap_state
    graph_obj = build_ai_planner_graph(
        eff_display,
        active_result,
        window_size=window_size,
        week_filter=week_filter,
        planet_filter=planet_filter,
    )
    return sanitize_json_dict(
        {
            "ok": True,
            "command": "ai_planner_get",
            "has_plan": active_result is not None,
            "active_summary": dict(raw_sum) if isinstance(raw_sum, Mapping) else None,
            "meta_parameters": dict(raw_mp) if isinstance(raw_mp, Mapping) else dict(default_params),
            "weekly_breakdown": list(raw_wb)
            if isinstance(raw_wb, Sequence) and not isinstance(raw_wb, (str, bytes))
            else [],
            "attack_telemetry": dict(raw_pt) if isinstance(raw_pt, Mapping) else live_telemetry,
            "live_attack_telemetry": live_telemetry,
            "in_progress": dict(raw_ip) if isinstance(raw_ip, Mapping) else None,
            "smelting": _patch_smelting_custom_names(
                active_result.get("smelting") if active_result else None, snap_state, custom_names
            ),
            "live_blast_furnace": ai_mod.summarize_live_blast_furnace_state(eff_display),
            "transport_matrix": dict(raw_tm) if isinstance(raw_tm, Mapping) else None,
            "transport_demand": transport_demand_snap,
            "search": _extract_search_snap(active_result),
            "adaptive_status": adaptive_status,
            "graph": graph_obj,
            "steps_page": {"items": page_items, "total": total_steps, "page": page, "page_size": page_size},
            "job": jobs_snap["ai_planner_job"],
            "runs": runs,
            "custom_planet_names": custom_names,
            "colonies_owned": gate["colonies_owned"],
            "max_planets": gate["max_planets"],
            "live_ai_unlocked": gate["live_ai_unlocked"],
            "active_planner_mode": gate["active_planner_mode"],
            "effective_planner_mode": gate["effective_planner_mode"],
            "fallback_reason": gate["fallback_reason"],
            "pending_field_conflicts": jobs_snap["pending_field_conflicts"],
            "default_params": default_params,
        }
    )


def handle_ai_planner_run(
    ctx: BridgeContext,
    payload: Mapping[str, object],
    *,
    start_ai_fn: Callable[..., dict[str, object]],
    on_is_galaxy_scan_blocking: Callable[[], bool] | None = None,
    on_galaxy_scan_busy_msg: Callable[[], str] | None = None,
) -> dict[str, object]:
    """Validate `ai_planner_run` payload and dispatch the AI-Planner job."""
    raw_params = payload.get("params")
    if raw_params is not None and not isinstance(raw_params, Mapping):
        raise ApiRequestError("'ai_planner_run' field 'params' must be a dictionary.")
    if isinstance(raw_params, Mapping):
        for delay_key in _DELAY_KEYS:
            if delay_key in raw_params and raw_params.get(delay_key) is not None:
                raw_delay = raw_params.get(delay_key)
                if isinstance(raw_delay, bool) or not isinstance(raw_delay, (int, float, str)):
                    raise ApiRequestError(f"'ai_planner_run' param '{delay_key}' must be a number between 0 and 1440.")
                try:
                    delay_val = float(raw_delay)
                except (TypeError, ValueError) as exc:
                    raise ApiRequestError(
                        f"'ai_planner_run' param '{delay_key}' must be a number between 0 and 1440."
                    ) from exc
                if not math.isfinite(delay_val) or delay_val < 0.0 or delay_val > 1440.0:
                    raise ApiRequestError(f"'ai_planner_run' param '{delay_key}' must be between 0 and 1440 minutes.")
    sync_raw = payload.get("sync", False)
    if not isinstance(sync_raw, bool):
        raise ApiRequestError("'ai_planner_run' field 'sync' must be a boolean.")
    refresh_raw = payload.get("refresh", False)
    if not isinstance(refresh_raw, bool):
        raise ApiRequestError("'ai_planner_run' field 'refresh' must be a boolean.")
    auto_cfg_raw = payload.get("auto_run_config_if_missing", True)
    if not isinstance(auto_cfg_raw, bool):
        raise ApiRequestError("'ai_planner_run' field 'auto_run_config_if_missing' must be a boolean.")
    trig = bounded_api_str(payload.get("trigger_source") or "manual", default="manual")[:64]
    if refresh_raw:
        with ctx.lock:
            if on_is_galaxy_scan_blocking is not None and on_is_galaxy_scan_blocking():
                busy_msg = (
                    on_galaxy_scan_busy_msg() if on_galaxy_scan_busy_msg is not None else "Galaxy scan in progress."
                )
                raise GalaxyScanInProgressError(busy_msg)
    resp = start_ai_fn(
        params=raw_params,
        sync=sync_raw,
        refresh=refresh_raw,
        trigger_source=trig,
        auto_run_config_if_missing=auto_cfg_raw,
    )
    return sanitize_json_dict(resp)
