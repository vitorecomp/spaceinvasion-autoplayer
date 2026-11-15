"""Manual operator queue merging, resource reservation, and Metal production reconciliation."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import replace

from backend.core import catalog
from backend.core.config import StrategyConfig
from backend.core.model import GameState, PlanetState, Resources
from backend.planner.eta import attach_action_etas
from backend.planner.heuristics._common import (
    _norm_planet_type,
    _normalize_manual_coords,
    _safe_strategy_int,
    _safe_strategy_str,
)
from backend.planner.heuristics._manual_builders import (
    _build_manual_build_item,
    _build_manual_demolish_item,
    _build_manual_research_item,
    _build_manual_ship_item,
    _build_manual_throttle_item,
    _ManualBuildResult,
    _ManualItemContext,
)
from backend.planner.heuristics._manual_fleet import _build_manual_fleet_item
from backend.planner.heuristics._manual_reconcile import _reconcile_queue_metal_production
from backend.planner.heuristics._manual_utility import _build_manual_utility_item
from backend.planner.heuristics._queues import _count_planet_building_queues
from backend.planner.heuristics._types import PlannedAction

__all__ = [
    "_reconcile_queue_metal_production",
    "merge_manual_queue_actions",
]

_PRIO_RANK: dict[str, int] = {"P0": 0, "P1": 1, "P2": 2, "P3": 3}
_FLEET_MANUAL_KINDS: frozenset[str] = frozenset({"SPY", "TRANSPORT", "STATION", "ATTACK", "RECYCLE", "COLONIZE"})
_COORD_SIG_KINDS: frozenset[str] = frozenset({"SPY", "ATTACK", "RECYCLE", "COLONIZE", "SCAN_GALAXY"})


def _dispatch_manual_item(
    ctx: _ManualItemContext,
    kind: str,
    item: Mapping[str, object],
    *,
    pid_req: int,
) -> _ManualBuildResult:
    if kind == "BUILD":
        return _build_manual_build_item(ctx, item)
    if kind == "DEMOLISH_BUILDING":
        return _build_manual_demolish_item(ctx, item)
    if kind == "RESEARCH":
        return _build_manual_research_item(ctx, item)
    if kind == "BUILD_SHIP":
        return _build_manual_ship_item(ctx, item)
    if kind == "THROTTLE_BUILDING":
        return _build_manual_throttle_item(ctx, item)
    if kind in _FLEET_MANUAL_KINDS:
        return _build_manual_fleet_item(ctx, kind, item)
    return _build_manual_utility_item(ctx, kind, item, pid_req=pid_req)


def _accumulate_reserved_resources(
    reserved_by_pid: dict[int, tuple[int, Resources]],
    planet_id: int,
    prio_rank_val: int,
    reserve_bundle: Resources,
) -> None:
    prev_entry = reserved_by_pid.get(planet_id)
    if prev_entry is None:
        reserved_by_pid[planet_id] = (prio_rank_val, reserve_bundle)
        return
    prev_pr, prev_res = prev_entry
    reserved_by_pid[planet_id] = (
        min(prev_pr, prio_rank_val),
        Resources(
            pig_iron=prev_res.pig_iron + reserve_bundle.pig_iron,
            metal=prev_res.metal + reserve_bundle.metal,
            kryptonite=prev_res.kryptonite + reserve_bundle.kryptonite,
            spice=prev_res.spice + reserve_bundle.spice,
        ),
    )


def _manual_action_signature(act: PlannedAction) -> tuple[str, int, int | None, str]:
    norm_k = "BUILD" if act.kind in ("BUILD", "WAIT_RESOURCES") else act.kind
    c_tok = _normalize_manual_coords(act.target_name) if norm_k in _COORD_SIG_KINDS else ""
    return (norm_k, act.planet_id, act.target_id, c_tok)


def _filter_auto_actions(
    planned_actions: Sequence[PlannedAction],
    manual_sigs: set[tuple[str, int, int | None, str]],
    reserved_by_pid: Mapping[int, tuple[int, Resources]],
    planets_by_id: Mapping[int, PlanetState],
) -> list[PlannedAction]:
    filtered_auto: list[PlannedAction] = []
    for act in planned_actions:
        if _manual_action_signature(act) in manual_sigs:
            continue
        act_pr = _PRIO_RANK.get(act.priority, 9)
        res_entry = reserved_by_pid.get(act.planet_id)
        if res_entry is not None and act.executable_now and act_pr > res_entry[0] and not act.cost.is_zero():
            _, res_need = res_entry
            p_obj = planets_by_id.get(act.planet_id)
            qty_mult = (
                max(1, _safe_strategy_int(act.target_level, 1))
                if (act.kind == "BUILD_SHIP" and act.manual_queue_id is None)
                else 1
            )
            act_spend = Resources(
                pig_iron=act.cost.pig_iron * qty_mult,
                metal=act.cost.metal * qty_mult,
                kryptonite=act.cost.kryptonite * qty_mult,
                spice=act.cost.spice * qty_mult,
            )
            overlaps = (
                (res_need.pig_iron > 0 and act_spend.pig_iron > 0)
                or (res_need.metal > 0 and act_spend.metal > 0)
                or (res_need.kryptonite > 0 and act_spend.kryptonite > 0)
                or (res_need.spice > 0 and act_spend.spice > 0)
            )
            combined = Resources(
                pig_iron=res_need.pig_iron + act_spend.pig_iron,
                metal=res_need.metal + act_spend.metal,
                kryptonite=res_need.kryptonite + act_spend.kryptonite,
                spice=res_need.spice + act_spend.spice,
            )
            if overlaps and p_obj is not None and not p_obj.resources.can_afford(combined):
                act = replace(
                    act,
                    executable_now=False,
                    reason=f"{act.reason} [Saving resources for prioritized user queue]",
                )
        filtered_auto.append(act)
    return filtered_auto


def _merge_sort_key(item_idx_pair: tuple[int, PlannedAction]) -> tuple[int, int, int, int]:
    orig_idx, act = item_idx_pair
    p_band = _PRIO_RANK.get(act.priority, 9)
    if act.kind in ("ALERT_HOSTILE", "ESCAPE_FLIGHT") and act.priority == "P0" and act.manual_queue_id is None:
        return (-1, 0, 0, orig_idx)
    if act.manual_queue_id is not None:
        return (p_band, 0, _safe_strategy_int(act.manual_sort_rank, 1000), orig_idx)
    return (p_band, 1, 0 if act.executable_now else 1, orig_idx)


def _build_manual_item_context(
    state: GameState,
    cfg: StrategyConfig,
    item: Mapping[str, object],
    idx_m: int,
    *,
    qid: int,
    active_p: PlanetState,
    planets_by_id: Mapping[int, PlanetState],
    planets_by_coords: Mapping[str, PlanetState],
    has_cmd: bool,
    r_queue_full: bool,
) -> tuple[_ManualItemContext, str, int]:
    raw_prio = (_safe_strategy_str(item.get("priority")) or "P1").strip().upper()
    prio = raw_prio if raw_prio in ("P0", "P1", "P2", "P3") else "P1"
    srank = _safe_strategy_int(item.get("sort_rank"), (idx_m + 1) * 10)
    kind = _safe_strategy_str(item.get("kind")).strip().upper()
    pid_req = _safe_strategy_int(item.get("planet_id"), 0)
    pcoords_req = _normalize_manual_coords(item.get("planet_coords"))
    planet = planets_by_id.get(pid_req) or (planets_by_coords.get(pcoords_req) if pcoords_req else None) or active_p
    is_active_planet = planet.planet_id == active_p.planet_id
    b_cap = 1 if _norm_planet_type(planet) == "asteroid" else (6 if has_cmd else 5)
    b_queue_full = _count_planet_building_queues(state, planet, is_active_planet=is_active_planet) >= b_cap
    note_s = _safe_strategy_str(item.get("note")).strip()
    ctx = _ManualItemContext(
        state=state,
        cfg=cfg,
        planet=planet,
        planets_by_id=planets_by_id,
        planets_by_coords=planets_by_coords,
        is_active_planet=is_active_planet,
        b_queue_full=b_queue_full,
        r_queue_full=r_queue_full,
        qid=qid,
        prio=prio,
        srank=srank,
        base_tag=f"User Queue #{qid}",
        note_s=note_s,
        note_suffix=f" · {note_s}" if note_s else "",
    )
    return ctx, kind, pid_req


def merge_manual_queue_actions(
    state: GameState,
    planned_actions: Sequence[PlannedAction],
    manual_queue: Sequence[Mapping[str, object]] | None,
    *,
    config: StrategyConfig | None = None,
    reconcile_metal_production: bool = True,
) -> tuple[list[PlannedAction], list[int]]:
    """Merge operator-enqueued manual commands into `planned_actions` with priority & resource reservation.

    Returns `(merged_actions, auto_satisfied_queue_ids)`.
    """
    if not manual_queue or not state.planets:
        return list(planned_actions), []

    cfg = config or StrategyConfig()
    active_p = state.active_planet() or state.planets[0]
    planets_by_id = {p.planet_id: p for p in state.planets}
    planets_by_coords = {_normalize_manual_coords(p.coords): p for p in state.planets if p.coords}

    r_queued_total = sum(
        1 for q in (getattr(state, "active_queues", ()) or ()) if getattr(q, "category", "") == "research"
    )
    has_cmd = catalog.safe_int((getattr(state, "specs", None) or {}).get("construction_commander", 0), 0) > 0

    sorted_items = sorted(
        (m for m in manual_queue if isinstance(m, Mapping) and str(m.get("status", "pending")).lower() == "pending"),
        key=lambda m: (
            _PRIO_RANK.get((_safe_strategy_str(m.get("priority")) or "P1").strip().upper(), 1),
            _safe_strategy_int(m.get("sort_rank"), 1000),
            _safe_strategy_int(m.get("id"), 0),
        ),
    )

    manual_actions: list[PlannedAction] = []
    auto_satisfied_ids: list[int] = []
    reserved_by_pid: dict[int, tuple[int, Resources]] = {}

    for idx_m, item in enumerate(sorted_items):
        qid = _safe_strategy_int(item.get("id"), 0)
        if qid <= 0:
            continue
        ctx, kind, pid_req = _build_manual_item_context(
            state,
            cfg,
            item,
            idx_m,
            qid=qid,
            active_p=active_p,
            planets_by_id=planets_by_id,
            planets_by_coords=planets_by_coords,
            has_cmd=has_cmd,
            r_queue_full=r_queued_total >= 1,
        )
        res = _dispatch_manual_item(ctx, kind, item, pid_req=pid_req)
        if res.auto_satisfied:
            auto_satisfied_ids.append(qid)
            continue
        if res.action is not None:
            manual_actions.append(res.action)
            if ctx.prio in ("P0", "P1") and res.prereq_met_for_reserve and not res.reserve_bundle.is_zero():
                _accumulate_reserved_resources(
                    reserved_by_pid, ctx.planet.planet_id, _PRIO_RANK.get(ctx.prio, 1), res.reserve_bundle
                )

    if not manual_actions:
        return list(planned_actions), auto_satisfied_ids

    manual_sigs = {_manual_action_signature(ma) for ma in manual_actions}
    filtered_auto = _filter_auto_actions(planned_actions, manual_sigs, reserved_by_pid, planets_by_id)
    combined_list = list(manual_actions) + filtered_auto
    ordered = [act for _, act in sorted(enumerate(combined_list), key=_merge_sort_key)]
    if reconcile_metal_production:
        ordered, eta_state = _reconcile_queue_metal_production(state, ordered)
    else:
        eta_state = state
    eta_attached = [act for act in attach_action_etas(eta_state, ordered) if isinstance(act, PlannedAction)]
    return eta_attached, auto_satisfied_ids
