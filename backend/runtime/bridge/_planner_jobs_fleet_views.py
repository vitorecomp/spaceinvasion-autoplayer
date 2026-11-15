"""Field-conflict resolution and Fleet Planner command handler helpers."""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import asdict
import re

from backend.core import catalog as game_data
from backend.core.config import StrategyConfig
from backend.core.model import GameState, PlanetState, PlannedAction, Resources
from backend.planner import ai as ai_mod
from backend.planner.graph import build_fleet_planner_graph, merge_strategy_config_overrides
from backend.planner.heuristics import plan_fleet_and_attack_actions
from backend.runtime.api.validation import ApiRequestError
from backend.runtime.bridge._context import BridgeContext
from backend.runtime.bridge._helpers import safe_api_float, safe_api_int, sanitize_json_dict
from backend.runtime.bridge._helpers_validation import validate_config_patch
from backend.runtime.bridge._plan_helpers import check_demolish_energy_safety, extract_ai_resource_targets
from backend.runtime.bridge._planner_jobs_state import (
    get_active_ai_planner,
    get_planner_state,
    list_field_conflicts,
    update_reserved_floor,
)
from backend.runtime.bridge._planner_jobs_telemetry import compute_live_attack_telemetry
from backend.runtime.bridge.ledgers import LedgerTelemetryService
from backend.runtime.bridge.planet_names import PlanetNameService


def _extract_conflict_candidate(
    target_conflict: Mapping[str, object],
    override_bkey: object,
    conflict_key: str,
) -> tuple[int, str, str, int]:
    cand_bkey = str(override_bkey or target_conflict.get("candidate_building_key") or "").strip()
    cand_lvl = safe_api_int(target_conflict.get("candidate_level", 0), 0)
    raw_detail = target_conflict.get("detail")
    detail_m: Mapping[str, object] = raw_detail if isinstance(raw_detail, Mapping) else {}
    raw_cands = detail_m.get("candidates")
    cands_list: Sequence[object] = (
        raw_cands if isinstance(raw_cands, Sequence) and not isinstance(raw_cands, (str, bytes)) else ()
    )
    if not cand_bkey and cands_list:
        first_c = cands_list[0]
        if isinstance(first_c, Mapping):
            cand_bkey = str(first_c.get("building_key") or first_c.get("key") or "").strip()
            cand_lvl = max(cand_lvl, safe_api_int(first_c.get("level", first_c.get("current_level", 0)), 0))
    elif override_bkey and cands_list:
        for c_it in cands_list:
            if isinstance(c_it, Mapping) and str(c_it.get("building_key") or c_it.get("key") or "") == cand_bkey:
                cand_lvl = max(1, safe_api_int(c_it.get("level", c_it.get("current_level", cand_lvl)), cand_lvl))
                break
    m_bid = re.fullmatch(r"b?(\d{1,3})", cand_bkey.lower(), re.ASCII)
    if not m_bid:
        raise ApiRequestError(f"Field conflict {conflict_key!r} has invalid candidate building key {cand_bkey!r}.")
    bid = int(m_bid.group(1))
    binfo = game_data.BUILDING_BY_ID.get(bid)
    bname = str(binfo.get("name") or f"Building b{bid}") if isinstance(binfo, Mapping) else f"Building b{bid}"
    return bid, f"b{bid}", bname, cand_lvl


def _resolve_conflict_demolish_locked(
    ctx: BridgeContext,
    *,
    snap_state: GameState | None,
    planet_obj: PlanetState | None,
    target_conflict: Mapping[str, object],
    conflict_key: str,
    pid: int,
    pcoords: str,
    bid: int,
    cand_bkey: str,
    bname: str,
) -> tuple[dict[str, object], dict[str, object]]:
    safe_ok, safe_reason, live_lvl = check_demolish_energy_safety(snap_state, planet_obj, bid)
    if not safe_ok:
        raise ApiRequestError(safe_reason)
    if ctx.db is None:
        raise ApiRequestError("Database is not configured for manual command queuing.")
    queued_item = dict(
        ctx.db.enqueue_manual_command(
            kind="DEMOLISH_BUILDING",
            priority="P1",
            position="top",
            planet_id=int(pid),
            planet_coords=pcoords,
            target_id=bid,
            target_name=bname,
            target_level=max(0, live_lvl - 1),
            note=f"Approved demolition via field conflict {conflict_key}",
            deduplicate_pending=True,
        )
    )
    resolved_db = ctx.db.resolve_field_conflict_prompt(
        conflict_key, "demolish", detail={"building_key": cand_bkey, "queued_demolish_id": queued_item.get("id")}
    )
    resolved = (
        dict(resolved_db)
        if isinstance(resolved_db, Mapping)
        else {**target_conflict, "status": "resolved", "decision": "demolish"}
    )
    ctx.invalidate_dashboard_cache()
    return resolved, queued_item


def _resolve_conflict_keep_reserve_locked(
    ctx: BridgeContext,
    *,
    planet_obj: PlanetState | None,
    target_conflict: dict[str, object],
    conflict_key: str,
    pid: int,
    pcoords: str,
    bid: int,
    cand_bkey: str,
    cand_lvl: int,
) -> tuple[dict[str, object], dict[str, dict[str, int]]]:
    live_lvl = planet_obj.building_level(bid) if planet_obj is not None else 0
    min_lvl = max(1, cand_lvl, live_lvl)
    planet_key = str(pid) if pid > 0 else pcoords
    reserved_floors = update_reserved_floor(
        ctx, planet_key, cand_bkey, min_lvl, reason=f"Field conflict {conflict_key}"
    )
    if ctx.db is not None:
        resolved_db = ctx.db.resolve_field_conflict_prompt(
            conflict_key,
            "keep_reserve",
            detail={"planet_key": planet_key, "building_key": cand_bkey, "min_level": min_lvl},
        )
        resolved = (
            dict(resolved_db)
            if isinstance(resolved_db, Mapping)
            else {**target_conflict, "status": "resolved", "decision": "keep_reserve"}
        )
    else:
        target_conflict["status"] = "resolved"
        target_conflict["decision"] = "keep_reserve"
        ctx.field_conflicts_mem[conflict_key] = dict(target_conflict)
        resolved = dict(target_conflict)
    ctx.invalidate_dashboard_cache()
    return resolved, reserved_floors


def handle_resolve_field_conflict(
    ctx: BridgeContext,
    payload: Mapping[str, object],
    *,
    start_co_fn: Callable[..., dict[str, object]],
    on_publish_snapshot: Callable[[], None] | None = None,
) -> dict[str, object]:
    """Resolve a pending field conflict by queuing a demolition or recording a reserved floor & rerunning."""
    raw_ck = payload.get("conflict_key")
    if not isinstance(raw_ck, str) or not raw_ck.strip():
        raise ApiRequestError("'resolve_field_conflict' requires a non-empty 'conflict_key'.")
    conflict_key = raw_ck.strip()
    raw_dec = payload.get("decision") if payload.get("decision") is not None else payload.get("action")
    if not isinstance(raw_dec, str) or raw_dec.strip().lower() not in ("demolish", "keep_reserve"):
        raise ApiRequestError("'resolve_field_conflict' requires 'decision' of 'demolish' or 'keep_reserve'.")
    decision = raw_dec.strip().lower()
    sync_raw = payload.get("sync", False)
    if not isinstance(sync_raw, bool):
        raise ApiRequestError("'resolve_field_conflict' field 'sync' must be a boolean.")
    override_bkey = payload.get("candidate_building_key") or payload.get("building_key")

    with ctx.state_lock:
        all_conflicts = list_field_conflicts(ctx, status=None)
        target_conflict = next((c for c in all_conflicts if str(c.get("conflict_key") or "") == conflict_key), None)
        if target_conflict is None:
            raise ApiRequestError(f"Unknown field conflict {conflict_key!r}.")
        pid = safe_api_int(target_conflict.get("planet_id", 0), 0)
        pcoords = str(target_conflict.get("planet_coords") or "")
        bid, cand_bkey, bname, cand_lvl = _extract_conflict_candidate(target_conflict, override_bkey, conflict_key)
        snap_state = get_planner_state(ctx)
        planet_obj: PlanetState | None = None
        if snap_state is not None:
            planet_obj = next(
                (
                    p
                    for p in snap_state.planets
                    if (pid > 0 and p.planet_id == pid) or (pcoords and p.coords == pcoords)
                ),
                None,
            )
            if planet_obj is not None:
                pid, pcoords = planet_obj.planet_id, planet_obj.coords

        if decision == "demolish":
            resolved, queued_item = _resolve_conflict_demolish_locked(
                ctx,
                snap_state=snap_state,
                planet_obj=planet_obj,
                target_conflict=target_conflict,
                conflict_key=conflict_key,
                pid=pid,
                pcoords=pcoords,
                bid=bid,
                cand_bkey=cand_bkey,
                bname=bname,
            )
            if on_publish_snapshot is not None:
                on_publish_snapshot()
            return sanitize_json_dict(
                {"ok": True, "command": "resolve_field_conflict", "conflict": resolved, "queued_demolish": queued_item}
            )

        resolved, reserved_floors = _resolve_conflict_keep_reserve_locked(
            ctx,
            planet_obj=planet_obj,
            target_conflict=target_conflict,
            conflict_key=conflict_key,
            pid=pid,
            pcoords=pcoords,
            bid=bid,
            cand_bkey=cand_bkey,
            cand_lvl=cand_lvl,
        )

    if on_publish_snapshot is not None:
        on_publish_snapshot()
    rerun_resp = start_co_fn(
        params=None, auto_replan_ai=True, sync=sync_raw, trigger_source="field_conflict_keep_reserve"
    )
    out_res: dict[str, object] = {
        "ok": True,
        "command": "resolve_field_conflict",
        "conflict": resolved,
        "reserved_floors": reserved_floors,
        "job": rerun_resp.get("job"),
    }
    if "ai_planner_job" in rerun_resp:
        out_res["ai_planner_job"] = rerun_resp["ai_planner_job"]
    return sanitize_json_dict(out_res)


def _build_fleet_graph_for_state(
    ledgers: LedgerTelemetryService,
    enriched_fp: GameState,
    effective_fp_cfg: StrategyConfig,
    active_ai_res: Mapping[str, object] | None,
    atk_tel: dict[str, object],
) -> tuple[dict[str, object], dict[str, object], dict[str, object]]:
    reserved_cost = Resources()
    priority_resource_need: dict[int, Resources] = {}
    spare_pct: Mapping[str, float] | float = float(effective_fp_cfg.ai_resource_spare_pct)
    ai_acts: list[PlannedAction] = []
    if active_ai_res is not None:
        meta_p = active_ai_res.get("meta_parameters")
        raw_sp = meta_p.get("resource_spare_percent") if isinstance(meta_p, Mapping) else None
        if isinstance(raw_sp, Mapping):
            spare_pct = {
                str(k): safe_api_float(v, float(effective_fp_cfg.ai_resource_spare_pct)) for k, v in raw_sp.items()
            }
        elif raw_sp is not None:
            spare_pct = safe_api_float(raw_sp, float(effective_fp_cfg.ai_resource_spare_pct))
        ai_acts, _ = ai_mod.reconcile_live_ai_actions(enriched_fp, active_ai_res, effective_fp_cfg)
        reserved_cost, priority_resource_need = extract_ai_resource_targets(ai_acts)

    fp_adaptive_on = bool(effective_fp_cfg.ai_adaptive_execution)
    fp_transport_demand: dict[str, object] | None = None
    eff_tel = atk_tel
    if fp_adaptive_on:
        eff_tel = ledgers.overlay_realized_loot_locked(eff_tel)
        raw_td = active_ai_res.get("transport_demand") if active_ai_res is not None else None
        if isinstance(raw_td, Mapping):
            fp_transport_demand = dict(raw_td)
            fp_rt = max(0.25, safe_api_float(fp_transport_demand.get("round_trips_per_hour", 2.5), 2.5))
            fp_transport_demand["realized_utilization"] = ledgers.recompute_transport_utilization_locked(
                rt_per_hour=fp_rt
            )
            fp_transport_demand["infra_cargo_ordered_last_hour"] = ledgers.get_infra_cargo_ordered_last_hour_locked()
    spare_budget = ledgers.compute_spare_ship_budget_locked(
        enriched_fp,
        spare_pct,
        attack_telemetry=eff_tel,
        reserved_cost=reserved_cost,
        adaptive_execution=fp_adaptive_on,
        config=effective_fp_cfg,
    )
    fleet_actions = plan_fleet_and_attack_actions(
        enriched_fp,
        config=effective_fp_cfg,
        scanned_systems=ledgers.get_scanned_systems(),
        transmitter_status=ledgers.get_transmitter_status_locked(),
        existing_plan=ai_acts,
        priority_resource_need=priority_resource_need,
        spare_budget=spare_budget,
        transport_demand=fp_transport_demand,
    )
    fleet_actions = ledgers.apply_fleet_slot_cooldowns(fleet_actions, re_sort_priority=True)
    fleet_actions = ledgers.apply_fleet_slot_holds(
        enriched_fp, fleet_actions, cfg=effective_fp_cfg, re_sort_priority=True
    )
    graph_obj = build_fleet_planner_graph(
        enriched_fp, fleet_actions, effective_fp_cfg, attack_telemetry=eff_tel, spare_budget=spare_budget
    )
    return dict(graph_obj), eff_tel, spare_budget


def handle_fleet_planner_get(
    ctx: BridgeContext,
    names: PlanetNameService,
    ledgers: LedgerTelemetryService,
    payload: Mapping[str, object],
) -> dict[str, object]:
    """Assemble the `fleet_planner_get` tactical fleet DAG, slot budgets, and spare-ship allowance."""
    raw_fp_cfg = payload.get("config")
    if raw_fp_cfg is not None:
        if not isinstance(raw_fp_cfg, Mapping):
            raise ApiRequestError("'fleet_planner_get' field 'config' must be a dictionary.")
        validate_config_patch(raw_fp_cfg, strict_bools=False)

    with ctx.state_lock:
        effective_fp_cfg, _ = merge_strategy_config_overrides(ctx.strategy_config, raw_fp_cfg)
        snap_state = get_planner_state(ctx)
        custom_names = names.get_custom_planet_names_locked()
        raw_enriched = (
            ledgers.merge_colony_candidates_if_needed(ledgers.attach_raid_stats(snap_state), effective_fp_cfg)
            if snap_state is not None
            else None
        )
        enriched_fp = (
            names.apply_custom_planet_names_to_state(raw_enriched, custom_names) if raw_enriched is not None else None
        )
        atk_tel = compute_live_attack_telemetry(ctx, ledgers, enriched_fp)
        active_ai = get_active_ai_planner(ctx)
        raw_ai_res = active_ai.get("result") if isinstance(active_ai, Mapping) else None
        active_ai_res: Mapping[str, object] | None = raw_ai_res if isinstance(raw_ai_res, Mapping) else None

        if enriched_fp is not None:
            graph_obj, atk_tel, spare_budget = _build_fleet_graph_for_state(
                ledgers, enriched_fp, effective_fp_cfg, active_ai_res, atk_tel
            )
        else:
            spare_budget = {
                "spare_percent": {"pig_iron": 10.0, "metal": 10.0, "kryptonite": 10.0, "spice": 10.0},
                "available_budget": {"pig_iron": 0.0, "metal": 0.0, "kryptonite": 0.0, "spice": 0.0},
                "urgent_zero_count_ships": [],
            }
            graph_obj = dict(
                build_fleet_planner_graph(
                    None, [], effective_fp_cfg, attack_telemetry=atk_tel, spare_budget=spare_budget
                )
            )

    return sanitize_json_dict(
        {
            "ok": True,
            "command": "fleet_planner_get",
            "graph": graph_obj,
            "slot_budgets": graph_obj.get("slot_budgets", []),
            "attack_telemetry": atk_tel,
            "spare_budget": spare_budget,
            "config": asdict(effective_fp_cfg),
            "custom_planet_names": custom_names,
        }
    )
