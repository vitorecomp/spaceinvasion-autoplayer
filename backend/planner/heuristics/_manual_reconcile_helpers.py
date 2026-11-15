"""Queue-order resource walk and target-percentage helpers for manual Metal reconciliation."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from backend.core import catalog
from backend.core.model import PlanetState, Resources
from backend.planner.heuristics._common import _safe_strategy_int, _safe_strategy_str
from backend.planner.heuristics._types import (
    _METAL_CONSUMER_KINDS,
    BLAST_FURNACE,
    PIG_IRON_MINE,
    PlannedAction,
)


@dataclass(frozen=True)
class _ReconcilePlanetMetrics:
    """Per-planet Blast furnace rates, stocks, and Pig-iron balance for queue reconciliation."""

    cur_b2_pct: int
    res_pig: int
    sto_pig: int
    metal_is_full: bool
    p_pig_gross: float
    bf_pig_used_full: float
    net_pig_at_100: float


def _compute_reconcile_planet_metrics(
    planet: PlanetState,
    b2_lvl: int,
    *,
    mining_tech: int,
    speedup: float,
) -> _ReconcilePlanetMetrics:
    rates = planet.economy_rates or {}
    cur_b2_pct = max(0, min(100, _safe_strategy_int(rates.get("resource2", 100), 100)))
    cur_b1_pct = max(0, min(100, _safe_strategy_int(rates.get("resource1", 100), 100)))
    res_pig = _safe_strategy_int(getattr(planet.resources, "pig_iron", 0), 0)
    res_met = _safe_strategy_int(getattr(planet.resources, "metal", 0), 0)
    sto_pig = _safe_strategy_int(getattr(planet.storage, "pig_iron", 0), 0)
    sto_met = _safe_strategy_int(getattr(planet.storage, "metal", 0), 0)
    metal_is_full = sto_met > 0 and res_met * 100 >= sto_met * 98

    e_prod = _safe_strategy_int(getattr(planet.energy, "produced", 0), 0)
    e_used = _safe_strategy_int(getattr(planet.energy, "used", 0), 0)
    prod_ratio = catalog.safe_big_ratio(e_prod, e_used, cap=1.0) if (e_used > 0 and e_prod < e_used) else 1.0
    factor = speedup * prod_ratio
    p_pig_gross = (
        catalog.mine_production_per_hour(
            PIG_IRON_MINE,
            planet.building_level(PIG_IRON_MINE),
            temperature=_safe_strategy_int(planet.temperature, 0),
            mining_tech_level=mining_tech,
        )
        * factor
        * (max(0, cur_b1_pct) / 100.0)
    )
    bf_pig_used_full = catalog.blast_furnace_pig_iron_used(b2_lvl) * factor
    return _ReconcilePlanetMetrics(
        cur_b2_pct=cur_b2_pct,
        res_pig=res_pig,
        sto_pig=sto_pig,
        metal_is_full=metal_is_full,
        p_pig_gross=p_pig_gross,
        bf_pig_used_full=bf_pig_used_full,
        net_pig_at_100=p_pig_gross - bf_pig_used_full,
    )


def _evaluate_planet_queue_order(
    planet: PlanetState,
    p_acts_all: Sequence[PlannedAction],
) -> list[tuple[PlannedAction, Resources, int, int]]:
    avail_pig = _safe_strategy_int(getattr(planet.resources, "pig_iron", 0), 0)
    avail_met = _safe_strategy_int(getattr(planet.resources, "metal", 0), 0)
    queue_eval: list[tuple[PlannedAction, Resources, int, int]] = []
    for act in p_acts_all:
        if act.kind not in _METAL_CONSUMER_KINDS:
            continue
        rsn_low = _safe_strategy_str(act.reason).lower()
        if (
            "[unmet prereqs:" in rsn_low
            or "(requires research lab" in rsn_low
            or "(requires weapons factory" in rsn_low
        ):
            continue
        qty_m = (
            max(1, _safe_strategy_int(act.target_level, 1))
            if (act.kind == "BUILD_SHIP" and act.manual_queue_id is None)
            else 1
        )
        spend = Resources(
            pig_iron=max(0, _safe_strategy_int(act.cost.pig_iron, 0)) * qty_m,
            metal=max(0, _safe_strategy_int(act.cost.metal, 0)) * qty_m,
            kryptonite=max(0, _safe_strategy_int(act.cost.kryptonite, 0)) * qty_m,
            spice=max(0, _safe_strategy_int(act.cost.spice, 0)) * qty_m,
        )
        need_pig = max(0, spend.pig_iron - avail_pig)
        need_met = max(0, spend.metal - avail_met)
        avail_pig = max(0, avail_pig - spend.pig_iron)
        avail_met = max(0, avail_met - spend.metal)
        queue_eval.append((act, spend, need_pig, need_met))
    return queue_eval


def _compute_reconcile_target_pct(
    planet: PlanetState,
    *,
    b2_lvl: int,
    cur_b2_pct: int,
    m_need_pig: int,
    m_spend: Resources,
    res_pig: int,
    p_pig_gross: float,
    bf_pig_used_full: float,
) -> int:
    target_pct = 100
    e_net_q = _safe_strategy_int(getattr(planet.energy, "net", 0), 0)
    b2_full_e_q = abs(_safe_strategy_int(catalog.building_level_stats(BLAST_FURNACE, b2_lvl).get("energy", 0) or 0, 0))
    if b2_full_e_q > 0:
        delta_e_full = int(round(b2_full_e_q * max(0, 100 - cur_b2_pct) / 100.0))
        if e_net_q < delta_e_full:
            e_avail_total = max(0, e_net_q) + int(round(b2_full_e_q * cur_b2_pct / 100.0))
            energy_pct = (e_avail_total * 100 // b2_full_e_q) // 10 * 10
            target_pct = max(max(10, cur_b2_pct), min(100, int(energy_pct)))
    if m_need_pig > 0 and bf_pig_used_full > p_pig_gross > 0 and res_pig < m_spend.pig_iron:
        sust_pct = 10
        for cand_pct in (90, 80, 70, 60, 50, 40, 30, 20, 10):
            if bf_pig_used_full * (cand_pct / 100.0) <= p_pig_gross * 0.85:
                sust_pct = cand_pct
                break
        if sust_pct < target_pct:
            target_pct = sust_pct
    return target_pct
