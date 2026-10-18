"""Memoized dashboard plan, static telemetry, and action-graph cache builders."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import asdict
import json
import time

from spaceinvasion.combat.ranking import (
    build_buildings_eta,
    build_combat_overview,
    build_ranking_evolution,
    estimate_planet_hourly_economy,
)
from spaceinvasion.core.state import GameState
from spaceinvasion.planner.action_graph import build_action_graph
from spaceinvasion.planner.strategy import PlannedAction, plan_actions, plan_fleet_and_attack_actions
from spaceinvasion.runtime.bridge._context import BridgeContext
from spaceinvasion.runtime.bridge._helpers import sanitize_finite_json, sanitize_json_dict
from spaceinvasion.runtime.bridge._helpers_sig import (
    freeze_sig_tree,
    redact_action_graph_strings,
    safe_sig_eq,
    summary_without_elapsed,
    supports_kwarg,
)
from spaceinvasion.runtime.bridge._views_health import ViewHooks
from spaceinvasion.runtime.bridge._views_telemetry import compute_current_hourly_production
from spaceinvasion.runtime.bridge.planet_names import PlanetNameService


def _build_plan_cache_key(
    ctx: BridgeContext,
    *,
    dash_sig: object,
    custom_names_sig: object,
    cfg_sig: object,
    eff_mode: str,
    db_tok: tuple[int, int, int] | None,
    mq_sig: object,
    now_mono: float,
    now_wall: float,
) -> tuple[object, ...]:
    with ctx.planner_jobs_lock:
        ai_job_sig = (ctx.ai_planner_job.get("run_id"), ctx.ai_planner_job.get("updated_ts"))
    scanned_sig: object = (
        (len(ctx.scanned_systems), max(ctx.scanned_systems.values(), default=0.0))
        if db_tok is not None
        else tuple(sorted(ctx.scanned_systems.items()))
    )
    throttle_sig = tuple(sorted((int(pid), round(float(ts), 1)) for pid, ts in ctx.throttle_timestamps.items()))
    overflow_sig = tuple(sorted(int(pid) for pid in ctx.metal_overflow_paused_planets))
    fleet_cd_sig = tuple(sorted(int(pid) for pid, ts in ctx.fleet_slot_cooldown_until.items() if ts > now_mono))
    target_blk_sig = tuple(
        sorted((str(c), round(float(u), 1), str(r)) for c, (u, r) in ctx.target_blocks.items() if u > now_wall)
    )
    ai_mem_sig = None if db_tok is not None else freeze_sig_tree(ctx.active_ai_plan_mem)
    return (
        dash_sig,
        custom_names_sig,
        cfg_sig,
        eff_mode,
        db_tok,
        mq_sig,
        ai_job_sig,
        ai_mem_sig,
        scanned_sig,
        throttle_sig,
        overflow_sig,
        fleet_cd_sig,
        ctx.observed_fleet_slot_base_offset,
        tuple(sorted(ctx.observed_fleet_slots_by_pid.items())),
        target_blk_sig,
        plan_actions,
        plan_fleet_and_attack_actions,
        round(float(ctx.transport_realized_utilization), 4),
    )


def resolve_cached_plan_for_dashboard(
    ctx: BridgeContext,
    hooks: ViewHooks,
    dash_state: GameState | None,
    display_state: GameState | None,
    *,
    dash_sig: object,
    custom_names_sig: object,
    cfg_sig: object,
    eff_mode: str,
    mq_pre_sig: object,
    refresh: bool,
) -> tuple[list[PlannedAction], dict[str, object] | None, bool]:
    """Compute or retrieve memoized dashboard action plan."""
    if dash_state is None:
        return [], None, False
    db_tok = ctx.db_total_changes_token()
    now_mono = time.monotonic()
    now_wall = time.time()
    plan_key = _build_plan_cache_key(
        ctx,
        dash_sig=dash_sig,
        custom_names_sig=custom_names_sig,
        cfg_sig=cfg_sig,
        eff_mode=eff_mode,
        db_tok=db_tok,
        mq_sig=mq_pre_sig,
        now_mono=now_mono,
        now_wall=now_wall,
    )
    plan_state_in = display_state if display_state is not None else dash_state
    if not refresh and ctx.dash_plan_cache_val is not None and safe_sig_eq(ctx.dash_plan_cache_key, plan_key):
        cached_acts, opt_summary, cached_early = ctx.dash_plan_cache_val
        if cached_early:
            ctx.last_early_transport_needs = {int(k): dict(v) for k, v in cached_early.items()}
            ctx.last_early_transport_needs_sig = id(plan_state_in)
        return list(cached_acts), opt_summary, True

    if hooks.on_plan_with_summary is not None:
        fn = hooks.on_plan_with_summary
        if supports_kwarg(fn, "_already_enriched"):
            actions, opt_summary = fn(plan_state_in, _already_enriched=True)
        else:
            actions, opt_summary = fn(plan_state_in)
    else:
        actions, opt_summary = plan_actions(plan_state_in, config=ctx.strategy_config), None

    early_copy = (
        {int(k): dict(v) for k, v in ctx.last_early_transport_needs.items()}
        if ctx.last_early_transport_needs_sig == id(plan_state_in)
        else {}
    )
    post_db_tok = ctx.db_total_changes_token()
    post_mq_sig = (
        freeze_sig_tree(ctx.db.get_manual_queue(status="pending", limit=200))
        if (post_db_tok != db_tok and ctx.db is not None)
        else mq_pre_sig
    )
    ctx.dash_plan_cache_key = _build_plan_cache_key(
        ctx,
        dash_sig=dash_sig,
        custom_names_sig=custom_names_sig,
        cfg_sig=cfg_sig,
        eff_mode=eff_mode,
        db_tok=post_db_tok,
        mq_sig=post_mq_sig,
        now_mono=now_mono,
        now_wall=now_wall,
    )
    ctx.dash_plan_cache_val = (list(actions), opt_summary, early_copy)
    return list(actions), opt_summary, False


def resolve_cached_static_for_dashboard(
    ctx: BridgeContext,
    names: PlanetNameService,
    dash_state: GameState | None,
    display_state: GameState | None,
    *,
    custom_names: Mapping[str, str],
    cfg_raw: dict[str, object],
    dash_sig: object,
    cfg_sig: object,
    custom_names_sig: object,
) -> dict[str, object]:
    """Compute or load memoized static telemetry (state, ranking, buildings_eta, combat, economies)."""
    static_key = (
        dash_sig,
        cfg_sig,
        custom_names_sig,
        build_ranking_evolution,
        build_buildings_eta,
        build_combat_overview,
        estimate_planet_hourly_economy,
    )
    if ctx.dash_static_cache_json is None or not safe_sig_eq(ctx.dash_static_cache_key, static_key):
        ranking = build_ranking_evolution(display_state, ctx.rank_samples) if display_state is not None else None
        buildings_eta = build_buildings_eta(display_state) if display_state is not None else None
        planet_economies = (
            {str(p.planet_id): estimate_planet_hourly_economy(display_state, p) for p in display_state.planets}
            if display_state is not None
            else {}
        )
        combat = (
            build_combat_overview(display_state, safety_margin=ctx.strategy_config.combat_safety_margin)
            if display_state is not None
            else None
        )
        static_clean = sanitize_finite_json(
            {
                "config": cfg_raw,
                "state": (
                    names.serialize_state_with_custom_names(dash_state, display_state, custom_names)
                    if dash_state is not None
                    else None
                ),
                "buildings_eta": buildings_eta,
                "planet_economies": planet_economies,
                "current_hourly_production_base": compute_current_hourly_production(dash_state),
                "combat": combat,
                "ranking": ranking,
            }
        )
        ctx.dash_cache_state_ref = ctx.last_state
        ctx.dash_static_cache_key = static_key
        ctx.dash_static_cache_json = json.dumps(static_clean, allow_nan=False)
    loaded = json.loads(ctx.dash_static_cache_json)
    return dict(loaded) if isinstance(loaded, Mapping) else {}


def resolve_cached_action_graph_for_dashboard(
    ctx: BridgeContext,
    hooks: ViewHooks,
    dash_state: GameState | None,
    display_state: GameState | None,
    actions: list[PlannedAction],
    planner_status: Mapping[str, object],
    *,
    dash_sig: object,
    cfg_sig: object,
    custom_names_sig: object,
    eff_mode: str,
    mq_sig: object,
) -> dict[str, object]:
    """Compute or load memoized serialized plan, action graph, and building resource requests."""
    ag_opt = None
    ag_opt_sig = freeze_sig_tree(summary_without_elapsed(ag_opt))
    early_needs_map: dict[int, dict[str, object]] = (
        ctx.last_early_transport_needs
        if (
            dash_state is not None
            and planner_status.get("effective_planner_mode") == "ai_planner"
            and ctx.last_early_transport_needs_sig in (id(dash_state), id(display_state))
        )
        else {}
    )
    ai_job = planner_status.get("ai_planner_job")
    ai_run_id = ai_job.get("run_id") if isinstance(ai_job, Mapping) else None
    ag_key = (
        dash_sig,
        cfg_sig,
        custom_names_sig,
        eff_mode,
        planner_status.get("effective_planner_mode"),
        ai_run_id,
        ag_opt_sig,
        mq_sig,
        freeze_sig_tree(early_needs_map),
        tuple(actions),
        build_action_graph,
    )
    if ctx.dash_ag_cache_json is None or not safe_sig_eq(ctx.dash_ag_cache_key, ag_key):
        eff_state = display_state if display_state is not None else dash_state
        if hooks.on_serialize_action is not None:
            fn_ser = hooks.on_serialize_action
            plan_json = [fn_ser(a) for a in actions]
        else:
            plan_json = [sanitize_json_dict(asdict(a)) for a in actions]
        raw_ag = build_action_graph(
            eff_state,
            actions,
            effective_config=ctx.strategy_config,
            active_config=ctx.strategy_config,
            simulated=False,
            optimizer_summary=ag_opt,
            sanitize=False,
        )
        ag_json = sanitize_finite_json(redact_action_graph_strings(raw_ag))
        raw_reqs = (
            hooks.on_build_building_resource_requests(eff_state, actions, early_needs_map)
            if hooks.on_build_building_resource_requests is not None
            else []
        )
        ag_clean = {
            "plan": plan_json,
            "action_graph": ag_json,
            "building_resource_requests": sanitize_finite_json(raw_reqs),
        }
        ctx.dash_cache_state_ref = ctx.last_state
        ctx.dash_ag_cache_key = ag_key
        ctx.dash_ag_cache_json = json.dumps(ag_clean, allow_nan=False)
    loaded = json.loads(ctx.dash_ag_cache_json)
    return dict(loaded) if isinstance(loaded, Mapping) else {}
