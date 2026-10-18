"""Full dashboard payload assembly and Phase 4.2/4.3 snapshot section extraction."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import asdict
import time

from spaceinvasion.core.config import normalize_planner_mode
from spaceinvasion.core.state import GameState
from spaceinvasion.runtime.bridge._context import BridgeContext
from spaceinvasion.runtime.bridge._helpers import (
    SERVER_BOOT_ID,
    sanitize_finite_json,
    sanitize_json_dict,
)
from spaceinvasion.runtime.bridge._helpers_sig import (
    compute_ui_assets_state,
    dashboard_state_sig,
    freeze_sig_tree,
)
from spaceinvasion.runtime.bridge._views_cache import (
    resolve_cached_action_graph_for_dashboard,
    resolve_cached_plan_for_dashboard,
    resolve_cached_static_for_dashboard,
)
from spaceinvasion.runtime.bridge._views_health import ViewHooks, build_dashboard_embedded_health
from spaceinvasion.runtime.bridge._views_telemetry import (
    build_planner_status_for_dashboard,
    compute_current_hourly_production,
    count_active_builders,
    get_db_summary_cached,
    get_hourly_production_history_for_dashboard,
    get_loot_recycle_for_dashboard,
)
from spaceinvasion.runtime.bridge.lifecycle import BridgeLifecycleService
from spaceinvasion.runtime.bridge.planet_names import PlanetNameService
from spaceinvasion.runtime.bridge.state_cache import StateCacheService

_SNAPSHOT_SECTION_KEYS: tuple[str, ...] = (
    "state",
    "config",
    "plan",
    "action_graph",
    "building_resource_requests",
    "manual_queue",
    "notifications",
    "transmitter_status",
    "galaxy_sweep",
    "universe_research_max",
    "current_hourly_production",
    "loot_recycle_summary",
    "optimizer_summary",
    "config_optimizer_goals",
    "buildings_eta",
    "planet_economies",
    "combat",
    "ranking",
    "db_summary",
    "planner_status",
    "custom_planet_names",
)


def _assemble_dashboard_envelope(
    ctx: BridgeContext,
    state_cache: StateCacheService,
    lifecycle: BridgeLifecycleService,
    hooks: ViewHooks,
    dash_state: GameState | None,
    *,
    custom_names: Mapping[str, str],
    eff_mode: str,
    planner_status: Mapping[str, object],
    manual_queue_pending: list[dict[str, object]],
    port_open: bool,
    in_game: bool,
    current_url: object,
) -> dict[str, object]:
    tm_status = (
        hooks.on_get_transmitter_status()
        if hooks.on_get_transmitter_status is not None
        else dict(ctx.transmitter_state_mem)
    )
    sw_state = (
        hooks.on_get_galaxy_sweep_state() if hooks.on_get_galaxy_sweep_state is not None else dict(ctx.galaxy_sweep_mem)
    )
    return sanitize_json_dict(
        {
            "ok": True,
            "server_boot_id": SERVER_BOOT_ID,
            "health": build_dashboard_embedded_health(
                ctx, port_open=port_open, in_game=in_game, current_url=current_url
            ),
            "bot_control": dict(ctx.bot_control),
            "custom_planet_names": dict(custom_names),
            "planner_mode": eff_mode,
            "effective_planner_mode": planner_status["effective_planner_mode"],
            "fallback_reason": planner_status["fallback_reason"],
            "live_ai_unlocked": planner_status["live_ai_unlocked"],
            "planner_status": planner_status,
            "optimizer_summary": None,
            "manual_queue": manual_queue_pending,
            "notifications": lifecycle.get_notifications_summary_locked(),
            "transmitter_status": tm_status,
            "galaxy_sweep": sw_state,
            "universe_research_max": state_cache.get_universe_research_max_locked(dash_state),
            "novnc_url": ctx.novnc_url,
        }
    )


def _assemble_dashboard_result(
    ctx: BridgeContext,
    hooks: ViewHooks,
    *,
    envelope: Mapping[str, object],
    static_val: Mapping[str, object],
    ag_val: Mapping[str, object],
    hourly_prod_hist: list[dict[str, object]],
    current_hourly_prod: dict[str, object],
    loot_rec: Mapping[str, object],
    db_summary_clean: dict[str, object] | None,
) -> dict[str, object]:
    ui_assets_clean = sanitize_json_dict(compute_ui_assets_state())
    co_goals_raw = hooks.on_get_co_building_goals() if hooks.on_get_co_building_goals is not None else {}
    return {
        "ok": envelope["ok"],
        "server_boot_id": envelope["server_boot_id"],
        **ui_assets_clean,
        "health": envelope["health"],
        "bot_control": envelope["bot_control"],
        "custom_planet_names": envelope["custom_planet_names"],
        "planner_mode": envelope["planner_mode"],
        "effective_planner_mode": envelope["effective_planner_mode"],
        "fallback_reason": envelope["fallback_reason"],
        "live_ai_unlocked": envelope["live_ai_unlocked"],
        "planner_status": envelope["planner_status"],
        "config": static_val.get("config"),
        "state": static_val.get("state"),
        "plan": ag_val.get("plan", []),
        "action_graph": ag_val.get("action_graph"),
        "building_resource_requests": ag_val.get("building_resource_requests", []),
        "manual_queue": envelope["manual_queue"],
        "notifications": envelope["notifications"],
        "transmitter_status": envelope["transmitter_status"],
        "galaxy_sweep": envelope["galaxy_sweep"],
        "universe_research_max": envelope["universe_research_max"],
        "hourly_production_history": hourly_prod_hist,
        "current_hourly_production": current_hourly_prod,
        "loot_recycle_history": loot_rec.get("history", []),
        "loot_recycle_summary": loot_rec.get("summary", {}),
        "optimizer_summary": envelope["optimizer_summary"],
        "config_optimizer_goals": sanitize_finite_json(co_goals_raw),
        "buildings_eta": static_val.get("buildings_eta"),
        "planet_economies": static_val.get("planet_economies", {}),
        "combat": static_val.get("combat"),
        "ranking": static_val.get("ranking"),
        "db_summary": db_summary_clean,
        "db_backend": ctx.build_db_backend_status(include_ping=False),
        "novnc_url": envelope.get("novnc_url"),
    }


def _compute_live_hourly_prod(static_val: Mapping[str, object], dash_state: GameState | None) -> dict[str, object]:
    now_ts = time.time()
    cur_base = static_val.get("current_hourly_production_base")
    if isinstance(cur_base, Mapping):
        out: dict[str, object] = dict(cur_base)
        out["minute_bucket"] = int(now_ts // 60) * 60
        out["recorded_at"] = round(now_ts, 3)
        out["active_builders"] = count_active_builders(dash_state, now_ts=now_ts)
        return out
    return compute_current_hourly_production(dash_state, now_ts=now_ts)


def _sync_plan_cache_db_token(ctx: BridgeContext, dash_state: GameState | None, plan_cache_hit: bool) -> None:
    if (
        dash_state is not None
        and not plan_cache_hit
        and isinstance(ctx.dash_plan_cache_key, tuple)
        and len(ctx.dash_plan_cache_key) >= 5
    ):
        final_db_tok = ctx.db_total_changes_token()
        if ctx.dash_plan_cache_key[4] != final_db_tok:
            ctx.dash_plan_cache_key = ctx.dash_plan_cache_key[:4] + (final_db_tok,) + ctx.dash_plan_cache_key[5:]


def _resolve_dashboard_derived_sections(
    ctx: BridgeContext,
    names: PlanetNameService,
    hooks: ViewHooks,
    dash_state: GameState | None,
    display_state: GameState | None,
    *,
    custom_names: Mapping[str, str],
    eff_mode: str,
    refresh: bool,
) -> tuple[dict[str, object], dict[str, object], dict[str, object], list[dict[str, object]], bool]:
    custom_names_sig = tuple(sorted(custom_names.items()))
    dash_sig = dashboard_state_sig(ctx.last_state, dash_state, ctx.rank_samples)
    cfg_raw: dict[str, object] = asdict(ctx.strategy_config)
    cfg_sig = freeze_sig_tree(cfg_raw)
    mq_pre = ctx.db.get_manual_queue(status="pending", limit=200) if ctx.db is not None else []
    mq_pre_sig = freeze_sig_tree(mq_pre)

    actions, _opt_summary, plan_cache_hit = resolve_cached_plan_for_dashboard(
        ctx,
        hooks,
        dash_state,
        display_state,
        dash_sig=dash_sig,
        custom_names_sig=custom_names_sig,
        cfg_sig=cfg_sig,
        eff_mode=eff_mode,
        mq_pre_sig=mq_pre_sig,
        refresh=refresh,
    )
    static_val = resolve_cached_static_for_dashboard(
        ctx,
        names,
        dash_state,
        display_state,
        custom_names=custom_names,
        cfg_raw=cfg_raw,
        dash_sig=dash_sig,
        cfg_sig=cfg_sig,
        custom_names_sig=custom_names_sig,
    )
    planner_status = build_planner_status_for_dashboard(ctx, hooks, dash_state)
    manual_queue_pending = (
        mq_pre
        if plan_cache_hit
        else (ctx.db.get_manual_queue(status="pending", limit=200) if ctx.db is not None else [])
    )
    ag_val = resolve_cached_action_graph_for_dashboard(
        ctx,
        hooks,
        dash_state,
        display_state,
        actions,
        planner_status,
        dash_sig=dash_sig,
        cfg_sig=cfg_sig,
        custom_names_sig=custom_names_sig,
        eff_mode=eff_mode,
        mq_sig=mq_pre_sig if plan_cache_hit else freeze_sig_tree(manual_queue_pending),
    )
    return static_val, planner_status, ag_val, manual_queue_pending, plan_cache_hit


def build_dashboard_payload_from_state(
    ctx: BridgeContext,
    state_cache: StateCacheService,
    names: PlanetNameService,
    lifecycle: BridgeLifecycleService,
    hooks: ViewHooks,
    *,
    refresh: bool,
    port_open: bool,
    in_game: bool,
    current_url: object,
) -> dict[str, object]:
    """Assemble the complete 36-key legacy dashboard payload from current `ctx` state."""
    dash_state = (
        hooks.on_attach_raid_stats(ctx.last_state)
        if (ctx.last_state is not None and hooks.on_attach_raid_stats is not None)
        else ctx.last_state
    )
    if refresh and dash_state is not None and hooks.on_record_hourly_production is not None:
        hooks.on_record_hourly_production(dash_state)
    custom_names = names.get_custom_planet_names_locked()
    display_state = (
        names.apply_custom_planet_names_to_state(dash_state, custom_names) if dash_state is not None else None
    )
    eff_mode = normalize_planner_mode(ctx.strategy_config.planner_mode)
    static_val, planner_status, ag_val, manual_queue_pending, plan_cache_hit = _resolve_dashboard_derived_sections(
        ctx,
        names,
        hooks,
        dash_state,
        display_state,
        custom_names=custom_names,
        eff_mode=eff_mode,
        refresh=refresh,
    )
    envelope = _assemble_dashboard_envelope(
        ctx,
        state_cache,
        lifecycle,
        hooks,
        dash_state,
        custom_names=custom_names,
        eff_mode=eff_mode,
        planner_status=planner_status,
        manual_queue_pending=manual_queue_pending,
        port_open=port_open,
        in_game=in_game,
        current_url=current_url,
    )
    result = _assemble_dashboard_result(
        ctx,
        hooks,
        envelope=envelope,
        static_val=static_val,
        ag_val=ag_val,
        hourly_prod_hist=get_hourly_production_history_for_dashboard(ctx, hooks, limit=1440),
        current_hourly_prod=_compute_live_hourly_prod(static_val, dash_state),
        loot_rec=get_loot_recycle_for_dashboard(ctx, hooks, hours=24),
        db_summary_clean=get_db_summary_cached(ctx),
    )
    _sync_plan_cache_db_token(ctx, dash_state, plan_cache_hit)
    ctx.last_dashboard_payload = result
    return result


def extract_snapshot_sections(
    dash_payload: Mapping[str, object],
) -> tuple[dict[str, object], list[Mapping[str, object]], list[Mapping[str, object]]]:
    """Partition a full dashboard dict into named sections plus incremental time-series rows."""
    sections: dict[str, object] = {}
    for key in _SNAPSHOT_SECTION_KEYS:
        if key in dash_payload:
            sections[key] = dash_payload[key]
    sections["ui_meta"] = {
        "server_boot_id": dash_payload.get("server_boot_id"),
        "ui_version": dash_payload.get("ui_version"),
        "code_version": dash_payload.get("code_version"),
        "css_version": dash_payload.get("css_version"),
        "planner_mode": dash_payload.get("planner_mode"),
        "effective_planner_mode": dash_payload.get("effective_planner_mode"),
        "fallback_reason": dash_payload.get("fallback_reason"),
        "live_ai_unlocked": dash_payload.get("live_ai_unlocked"),
        "novnc_url": dash_payload.get("novnc_url"),
    }
    raw_prod = dash_payload.get("hourly_production_history")
    prod_rows: list[Mapping[str, object]] = (
        [r for r in raw_prod if isinstance(r, Mapping)]
        if isinstance(raw_prod, Sequence) and not isinstance(raw_prod, (str, bytes))
        else []
    )
    raw_loot = dash_payload.get("loot_recycle_history")
    loot_rows: list[Mapping[str, object]] = (
        [r for r in raw_loot if isinstance(r, Mapping)]
        if isinstance(raw_loot, Sequence) and not isinstance(raw_loot, (str, bytes))
        else []
    )
    return sections, prod_rows, loot_rows
