"""Central-planet warehouse reserve polish and self-consistent round finalization helpers."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

import numpy as np

from spaceinvasion.planner.config_opt._search_catalogue import ProgressCallback, planet_moves
from spaceinvasion.planner.config_opt.constants import RESOURCE_KEYS, FloatArray, IntArray
from spaceinvasion.planner.config_opt.model import EmpireProductionModel

GAHistoryRow = dict[str, int | float]
RoundRow = dict[str, object]
SearchRoundResult = tuple[list[IntArray], IntArray, list[GAHistoryRow], IntArray, IntArray]
RefinedRoundResult = tuple[EmpireProductionModel, SearchRoundResult, float, FloatArray, FloatArray]


@dataclass(frozen=True)
class SearchSettings:
    """Internal bundle of GA + catalogue + polish parameters passed across search rounds."""

    grid_size: int
    priority_steps: int
    step_sizes: tuple[int, ...]
    population_size: int
    generations: int
    tournament_size: int
    crossover_rate: float
    mutation_rate: float
    elite_count: int
    seed: int


RunSearchFn = Callable[
    [EmpireProductionModel, SearchSettings, ProgressCallback | None, tuple[float, float]],
    SearchRoundResult,
]


def central_reserve_needed(model: EmpireProductionModel, levels: IntArray) -> FloatArray:
    """Capacity per resource holding ``warehouse_hours`` of whole-empire net hourly production."""
    net_per_hour = np.asarray(model.simulate(levels[None])["planet_net_per_hour"], dtype=np.float64)[0].sum(axis=0)
    return np.asarray(model.warehouse_hours * np.maximum(net_per_hour, 0.0), dtype=np.float64)


def _best_reserve_planet_step(
    model: EmpireProductionModel,
    planet: int,
    cur_planet: IntArray,
    cur_limits: FloatArray,
    cur_net: FloatArray,
    other_limits: FloatArray,
    other_net: FloatArray,
    cp_wh_fixed: IntArray,
    moves: IntArray,
) -> tuple[IntArray, FloatArray, FloatArray, IntArray] | None:
    """Return the best improving reserve-preserving neighbour for ``planet``, or ``None``."""
    cp = model.central_planet
    built_vec = model.built_opt_levels[planet]
    cands: IntArray = np.asarray(cur_planet + moves, dtype=np.int64)
    cands = cands[model.in_range(cands[:, None, :], [planet])]
    if len(cands) == 0:
        return None
    out = model.simulate(cands[:, None, :], [planet])
    fits = np.asarray(out["planet_fits"], dtype=np.bool_)[:, 0]
    if not bool(fits.any()):
        return None
    cands = cands[fits]
    own_limits = np.asarray(out["planet_limits"], dtype=np.float64)[fits, 0]
    own_net = np.asarray(out["planet_net_per_hour"], dtype=np.float64)[fits, 0]
    own_wh = np.asarray(out["planet_warehouse_levels"], dtype=np.int64)[fits, 0]

    tot_limits = other_limits[None, :] + own_limits
    tot_net = other_net[None, :] + own_net
    needed = model.warehouse_hours * np.maximum(tot_net, 0.0)
    held: FloatArray = (
        model.warehouse_capacities[own_wh] if planet == cp else model.warehouse_capacities[cp_wh_fixed][None, :]
    )
    excess = np.maximum(0.0, needed - held)
    rel_excess = (excess / np.maximum(held, 1.0)).sum(axis=1)
    ships = tot_limits.min(axis=1)
    scores: FloatArray = np.asarray(
        np.where(rel_excess <= 1e-12, ships, -1e12 * rel_excess + 1e-6 * ships),
        dtype=np.float64,
    )
    finite_mask = np.isfinite(scores)
    if not bool(finite_mask.any()):
        return None
    if not bool(finite_mask.all()):
        cands = cands[finite_mask]
        own_limits = own_limits[finite_mask]
        own_net = own_net[finite_mask]
        own_wh = own_wh[finite_mask]
        scores = scores[finite_mask]

    change_totals = np.abs(cands - built_vec).sum(axis=1)
    lvl_tot = cands.sum(axis=1)

    cur_needed = model.warehouse_hours * np.maximum(other_net + cur_net, 0.0)
    cur_held = model.warehouse_capacities[cp_wh_fixed]
    cur_rel_ex = float((np.maximum(0.0, cur_needed - cur_held) / np.maximum(cur_held, 1.0)).sum())
    cur_ships = float((other_limits + cur_limits).min())
    cur_score = cur_ships if cur_rel_ex <= 1e-12 else (-1e12 * cur_rel_ex + 1e-6 * cur_ships)
    cur_rank = (cur_score, -int(np.abs(cur_planet - built_vec).sum()), -int(cur_planet.sum()))

    best = int(np.lexsort((-lvl_tot, -change_totals, scores))[-1])
    best_rank = (float(scores[best]), -int(change_totals[best]), -int(lvl_tot[best]))
    if best_rank > cur_rank:
        return cands[best], own_limits[best], own_net[best], own_wh[best]
    return None


def _polish_holding_central_reserve(
    model: EmpireProductionModel,
    levels: IntArray,
    step_sizes: tuple[int, ...],
) -> IntArray:
    """Coordinate-ascent over the empire enforcing ``needed <= held`` on ``model.central_planet``."""
    cp = model.central_planet
    if cp < 0 or model.warehouse_hours <= 0.0:
        return levels
    moves = planet_moves(levels.shape[1], step_sizes)
    cur: IntArray = np.array(levels, dtype=np.int64, copy=True)
    sim0 = model.simulate(cur[None])
    p_limits = np.asarray(sim0["planet_limits"], dtype=np.float64)[0].copy()
    p_net = np.asarray(sim0["planet_net_per_hour"], dtype=np.float64)[0].copy()
    p_wh = np.asarray(sim0["planet_warehouse_levels"], dtype=np.int64)[0].copy()

    changed = True
    while changed:
        changed = False
        for planet in range(len(cur)):
            step = _best_reserve_planet_step(
                model,
                planet,
                cur[planet],
                p_limits[planet],
                p_net[planet],
                p_limits.sum(axis=0) - p_limits[planet],
                p_net.sum(axis=0) - p_net[planet],
                p_wh[cp].copy(),
                moves,
            )
            if step is not None:
                cur[planet], p_limits[planet], p_net[planet], p_wh[planet] = step
                changed = True
    return cur


def _finalize_self_consistent_round(
    base_model: EmpireProductionModel,
    round_model: EmpireProductionModel,
    result: SearchRoundResult,
    step_sizes: tuple[int, ...],
) -> RefinedRoundResult | None:
    """Polish ``result`` so ``needed <= held``, tighten central warehouses to ``needed``, and re-polish freed fields."""
    cats, gd, gh, gl, pol = result
    pol_h = _polish_holding_central_reserve(round_model, pol, step_sizes)
    for _ in range(3):
        needed_h = central_reserve_needed(round_model, pol_h)
        try:
            tight_model = base_model.with_central_reserve(needed_h)
        except ValueError:
            return None
        sim_h = tight_model.simulate(pol_h[None])
        if not bool(np.asarray(sim_h["planet_fits"], dtype=np.bool_).all()):
            return None
        pol_next = _polish_holding_central_reserve(tight_model, pol_h, step_sizes)
        if np.array_equal(pol_next, pol_h):
            pol_h = pol_next
            break
        pol_h = pol_next
        round_model = tight_model
    needed_final = central_reserve_needed(base_model, pol_h)
    try:
        final_model = base_model.with_central_reserve(needed_final)
    except ValueError:
        return None
    sim_f = final_model.simulate(pol_h[None])
    if not bool(np.asarray(sim_f["planet_fits"], dtype=np.bool_).all()):
        return None
    cp = final_model.central_planet
    wh_final = np.asarray(sim_f["planet_warehouse_levels"], dtype=np.int64)
    held_final: FloatArray = (
        final_model.warehouse_capacities[wh_final[0, cp]]
        if cp >= 0
        else np.full(len(RESOURCE_KEYS), np.inf, dtype=np.float64)
    )
    if bool((needed_final > held_final).any()):
        return None
    limits_final = np.asarray(sim_f["planet_limits"], dtype=np.float64)
    ships_final = float(limits_final[0].sum(axis=0).min())
    return final_model, (cats, gd, gh, gl, pol_h), ships_final, needed_final, held_final


def _build_round_row(
    round_num: int,
    ships_per_month: float,
    reserved: FloatArray,
    needed: FloatArray,
    held: FloatArray,
    *,
    holds_the_production: bool,
) -> RoundRow:
    """Format one central-reserve convergence telemetry row."""
    return {
        "round": round_num,
        "ships_per_month": round(float(ships_per_month), 2),
        **{f"{resource}_reserved": float(amount) for resource, amount in zip(RESOURCE_KEYS, reserved, strict=True)},
        **{
            f"{resource}_needed": round(float(amount), 1)
            for resource, amount in zip(RESOURCE_KEYS, needed, strict=True)
        },
        **{f"{resource}_held": float(amount) for resource, amount in zip(RESOURCE_KEYS, held, strict=True)},
        "holds_the_production": bool(holds_the_production),
    }


def _refine_overshot_central_reserve(
    model: EmpireProductionModel,
    final_model: EmpireProductionModel,
    result: SearchRoundResult,
    wh_reserved: IntArray,
    wh_needed: IntArray,
    rounds: list[RoundRow],
    *,
    max_rounds: int,
    settings: SearchSettings,
    run_search: RunSearchFn,
    progress_cb: ProgressCallback | None,
) -> tuple[EmpireProductionModel, SearchRoundResult]:
    """Step down intermediate central warehouse brackets so freed fields are spent up to equilibrium."""
    best_levels = result[-1]
    best_rank = (
        float(final_model.ships_per_month(best_levels[None])[0]),
        -int(np.abs(best_levels - model.built_opt_levels).sum()),
        -int(best_levels.sum()),
    )
    best_choice: tuple[EmpireProductionModel, SearchRoundResult] = (final_model, result)
    refined_base = _finalize_self_consistent_round(model, final_model, result, settings.step_sizes)
    if refined_base is not None:
        fm0, res0, ships0, _n0, _h0 = refined_base
        lvls0 = res0[-1]
        rk0 = (ships0, -int(np.abs(lvls0 - model.built_opt_levels).sum()), -int(lvls0.sum()))
        if rk0 > best_rank:
            best_rank = rk0
            best_choice = (fm0, res0)

    max_gap = int((wh_reserved - wh_needed).max())
    budget = max(0, max_rounds - len(rounds))
    for step_down in range(1, min(max_gap, budget) + 1):
        cand_wh = np.maximum(wh_needed, wh_reserved - step_down)
        cand_reserve = model.warehouse_capacities[cand_wh]
        try:
            cand_round_model = model.with_central_reserve(cand_reserve)
        except ValueError:
            break
        p_s = min(0.92, 0.80 + 0.04 * step_down)
        p_e = min(0.95, p_s + 0.04)
        cand_raw = run_search(cand_round_model, settings, progress_cb, (p_s, p_e))
        refined = _finalize_self_consistent_round(model, cand_round_model, cand_raw, settings.step_sizes)
        if refined is None:
            continue
        fm_c, res_c, ships_c, needed_c, held_c = refined
        lvls_c = res_c[-1]
        rounds.append(
            _build_round_row(len(rounds) + 1, ships_c, cand_reserve, needed_c, held_c, holds_the_production=True)
        )
        rk_c = (ships_c, -int(np.abs(lvls_c - model.built_opt_levels).sum()), -int(lvls_c.sum()))
        if rk_c > best_rank:
            best_rank = rk_c
            best_choice = (fm_c, res_c)
        elif ships_c < best_rank[0] * (1.0 - 1e-4):
            break
    return best_choice


__all__ = [
    "GAHistoryRow",
    "RefinedRoundResult",
    "RoundRow",
    "RunSearchFn",
    "SearchRoundResult",
    "SearchSettings",
    "_build_round_row",
    "_finalize_self_consistent_round",
    "_polish_holding_central_reserve",
    "_refine_overshot_central_reserve",
    "central_reserve_needed",
]
