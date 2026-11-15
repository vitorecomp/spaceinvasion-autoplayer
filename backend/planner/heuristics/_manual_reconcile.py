"""Queue-position Metal production reconciliation for pinned manual queue actions."""

from __future__ import annotations

from dataclasses import replace

from backend.core.model import GameState, PlanetState, Resources
from backend.planner.heuristics._common import _safe_strategy_float, _safe_strategy_int, _safe_strategy_str
from backend.planner.heuristics._manual_reconcile_helpers import (
    _compute_reconcile_planet_metrics,
    _compute_reconcile_target_pct,
    _evaluate_planet_queue_order,
)
from backend.planner.heuristics._types import BLAST_FURNACE, PlannedAction
from backend.planner.heuristics.blast_furnace import p1_metal_shortfall, plan_p1_metal_resume_throttle


def _reconcile_no_manual_metal_frontier(
    state: GameState,
    planet: PlanetState,
    out_list: list[PlannedAction],
    cur_b2_pct: int,
    projected_rates_by_pid: dict[int, int],
) -> list[PlannedAction]:
    pid = planet.planet_id
    p1_need_met_q, p1_act_q = p1_metal_shortfall(planet, out_list)
    if p1_need_met_q <= 0 or p1_act_q is None:
        return out_list
    filtered = [
        a
        for a in out_list
        if not (
            a.planet_id == pid
            and a.manual_queue_id is None
            and a.kind == "THROTTLE_BUILDING"
            and a.target_id == BLAST_FURNACE
            and _safe_strategy_int(a.target_level, -1) == 0
            and "metal storage full" not in _safe_strategy_str(a.reason).lower()
            and "negative energy" not in _safe_strategy_str(a.reason).lower()
        )
    ]
    has_resume = any(
        a.planet_id == pid
        and a.kind == "THROTTLE_BUILDING"
        and a.target_id == BLAST_FURNACE
        and _safe_strategy_int(a.target_level, 0) > 0
        for a in filtered
    )
    if cur_b2_pct == 0 and not has_resume:
        resume_p1 = plan_p1_metal_resume_throttle(state, planet, p1_need_met_q, p1_act_q)
        if resume_p1 is not None:
            p1_idx_out = next((i for i, x in enumerate(filtered) if x is p1_act_q), 0)
            filtered.insert(p1_idx_out, resume_p1)
            projected_rates_by_pid[pid] = _safe_strategy_int(resume_p1.target_level, 100)
    return filtered


def _reconcile_earlier_pig_blocker(
    planet: PlanetState,
    out_list: list[PlannedAction],
    cur_b2_pct: int,
    m_act: PlannedAction,
    blk_act: PlannedAction,
    blk_need_pig: int,
    projected_rates_by_pid: dict[int, int],
) -> list[PlannedAction]:
    pid = planet.planet_id
    filtered = [
        a
        for a in out_list
        if not (
            a.planet_id == pid
            and a.manual_queue_id is None
            and a.kind == "THROTTLE_BUILDING"
            and a.target_id == BLAST_FURNACE
            and _safe_strategy_int(a.target_level, 0) > 0
        )
    ]
    has_pause = any(
        a.planet_id == pid
        and a.kind == "THROTTLE_BUILDING"
        and a.target_id == BLAST_FURNACE
        and _safe_strategy_int(a.target_level, -1) == 0
        for a in filtered
    )
    if cur_b2_pct > 0 and not has_pause:
        pause_act = PlannedAction(
            priority=blk_act.priority,
            kind="THROTTLE_BUILDING",
            planet_id=pid,
            planet_coords=_safe_strategy_str(planet.coords),
            target_id=BLAST_FURNACE,
            target_name="Blast furnace 0%",
            target_level=0,
            reason=(
                f"Hold Blast furnace ({cur_b2_pct}% -> 0%) for earlier queued "
                f"{blk_act.target_name} (missing {blk_need_pig:,} Pig iron) before "
                f"smelting Metal for #{m_act.manual_queue_id} {m_act.target_name}"
            ),
            executable_now=True,
        )
        ins_pos = next((i for i, x in enumerate(filtered) if x is blk_act), 0)
        filtered.insert(ins_pos, pause_act)
        projected_rates_by_pid[pid] = 0
    return [
        replace(
            x,
            reason=f"{x.reason} [Metal production queued after {blk_act.target_name} Pig iron completes]",
        )
        if x is m_act and "[Metal production queued" not in _safe_strategy_str(x.reason)
        else x
        for x in filtered
    ]


def _apply_active_metal_frontier_resume(
    planet: PlanetState,
    out_list: list[PlannedAction],
    *,
    b2_lvl: int,
    cur_b2_pct: int,
    metal_is_full: bool,
    m_act: PlannedAction,
    m_spend: Resources,
    m_need_pig: int,
    m_need_met: int,
    res_pig: int,
    p_pig_gross: float,
    bf_pig_used_full: float,
    projected_rates_by_pid: dict[int, int],
) -> list[PlannedAction]:
    pid = planet.planet_id
    filtered = [
        a
        for a in out_list
        if not (
            a.planet_id == pid
            and a.manual_queue_id is None
            and a.kind == "THROTTLE_BUILDING"
            and a.target_id == BLAST_FURNACE
            and _safe_strategy_int(a.target_level, -1) == 0
        )
    ]
    if metal_is_full or cur_b2_pct >= 100:
        return filtered
    target_pct = _compute_reconcile_target_pct(
        planet,
        b2_lvl=b2_lvl,
        cur_b2_pct=cur_b2_pct,
        m_need_pig=m_need_pig,
        m_spend=m_spend,
        res_pig=res_pig,
        p_pig_gross=p_pig_gross,
        bf_pig_used_full=bf_pig_used_full,
    )
    if cur_b2_pct == target_pct:
        return filtered
    filtered = [
        a
        for a in filtered
        if not (
            a.planet_id == pid
            and a.manual_queue_id is None
            and a.kind == "THROTTLE_BUILDING"
            and a.target_id == BLAST_FURNACE
        )
    ]
    resume_act = PlannedAction(
        priority=m_act.priority,
        kind="THROTTLE_BUILDING",
        planet_id=pid,
        planet_coords=_safe_strategy_str(planet.coords),
        target_id=BLAST_FURNACE,
        target_name=f"Blast furnace {target_pct}%",
        target_level=target_pct,
        reason=(
            f"Enable Metal production ({cur_b2_pct}% -> {target_pct}%) for pinned "
            f"#{m_act.manual_queue_id} {m_act.target_name} (needs {m_need_met:,} Metal)"
        ),
        executable_now=True,
    )
    m_idx_out = next((i for i, x in enumerate(filtered) if x is m_act), 0)
    filtered.insert(m_idx_out, resume_act)
    projected_rates_by_pid[pid] = target_pct
    return filtered


def _reconcile_planet_metal_production(
    state: GameState,
    planet: PlanetState,
    out_list: list[PlannedAction],
    *,
    mining_tech: int,
    speedup: float,
    projected_rates_by_pid: dict[int, int],
) -> list[PlannedAction]:
    pid = planet.planet_id
    b2_lvl = planet.building_level(BLAST_FURNACE)
    if b2_lvl <= 0:
        return out_list
    p_acts_all = [a for a in out_list if a.planet_id == pid]
    if not any(a.manual_queue_id is not None for a in p_acts_all):
        return out_list
    if any(
        a.manual_queue_id is not None and a.kind == "THROTTLE_BUILDING" and a.target_id == BLAST_FURNACE
        for a in p_acts_all
    ):
        return out_list

    metrics = _compute_reconcile_planet_metrics(planet, b2_lvl, mining_tech=mining_tech, speedup=speedup)
    queue_eval = _evaluate_planet_queue_order(planet, p_acts_all)
    first_metal_idx = next(
        (
            idx_q
            for idx_q, (act_q, spend_q, _np, nm) in enumerate(queue_eval)
            if act_q.manual_queue_id is not None and spend_q.metal > 0 and nm > 0
        ),
        None,
    )
    if first_metal_idx is None:
        return _reconcile_no_manual_metal_frontier(state, planet, out_list, metrics.cur_b2_pct, projected_rates_by_pid)

    m_act, m_spend, m_need_pig, m_need_met = queue_eval[first_metal_idx]
    earlier_items = queue_eval[:first_metal_idx]
    p1_need_met_q, _ = p1_metal_shortfall(planet, out_list)
    earlier_pig_blocker = (
        None
        if p1_need_met_q > 0
        else next(
            (
                (ea, esp, enp)
                for (ea, esp, enp, enm) in earlier_items
                if enp > 0 and enm == 0 and not (metrics.sto_pig > 0 and esp.pig_iron > metrics.sto_pig)
            ),
            None,
        )
    )
    if earlier_pig_blocker is not None:
        blk_act, _blk_spend, blk_need_pig = earlier_pig_blocker
        return _reconcile_earlier_pig_blocker(
            planet, out_list, metrics.cur_b2_pct, m_act, blk_act, blk_need_pig, projected_rates_by_pid
        )

    sum_pig_ahead_waiting = sum(esp.pig_iron for (ea, esp, _enp, _enm) in earlier_items if not ea.executable_now)
    if (
        p1_need_met_q <= 0
        and sum_pig_ahead_waiting > 0
        and metrics.net_pig_at_100 < 0
        and metrics.res_pig < sum_pig_ahead_waiting + 500
    ):
        return out_list

    return _apply_active_metal_frontier_resume(
        planet,
        out_list,
        b2_lvl=b2_lvl,
        cur_b2_pct=metrics.cur_b2_pct,
        metal_is_full=metrics.metal_is_full,
        m_act=m_act,
        m_spend=m_spend,
        m_need_pig=m_need_pig,
        m_need_met=m_need_met,
        res_pig=metrics.res_pig,
        p_pig_gross=metrics.p_pig_gross,
        bf_pig_used_full=metrics.bf_pig_used_full,
        projected_rates_by_pid=projected_rates_by_pid,
    )


def _reconcile_queue_metal_production(
    state: GameState,
    ordered: list[PlannedAction],
) -> tuple[list[PlannedAction], GameState]:
    """Review and adjust Blast furnace (`b2`) Metal production based on pinned queue position."""
    if not ordered or not state.planets:
        return ordered, state

    mining_tech = max(0, _safe_strategy_int((state.researches or {}).get("r1", 0), 0))
    speedup = _safe_strategy_float(getattr(state, "speedup_factor", 1.0), 1.0)
    if speedup <= 0:
        speedup = 1.0

    projected_rates_by_pid: dict[int, int] = {}
    out_list = list(ordered)
    for planet in state.planets:
        out_list = _reconcile_planet_metal_production(
            state,
            planet,
            out_list,
            mining_tech=mining_tech,
            speedup=speedup,
            projected_rates_by_pid=projected_rates_by_pid,
        )

    if not projected_rates_by_pid:
        return out_list, state

    adj_planets = tuple(
        replace(
            p,
            economy_rates={**(p.economy_rates or {}), "resource2": projected_rates_by_pid[p.planet_id]},
        )
        if p.planet_id in projected_rates_by_pid
        else p
        for p in state.planets
    )
    return out_list, replace(state, planets=adj_planets)
