"""Previous-plan matching, sunk-cost pairwise role alignment, and sticky candidate building."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
import copy

import numpy as np

from backend.core.coercion import coerce_finite_float, coerce_int
from backend.core.coords import normalize_coords
from backend.planner.config_optimizer._protocols import as_int_dict, map_int, map_str
from backend.planner.config_optimizer._search_catalogue import improve_design, planet_moves, smallest_limit
from backend.planner.config_optimizer.constants import (
    SPECIALIZATION_ALLOWED_BUILDINGS,
    WAREHOUSE_KEYS,
    BoolArray,
    FloatArray,
    IntArray,
)
from backend.planner.config_optimizer.model import EmpireProductionModel
from backend.planner.config_optimizer.params import normalize_planet_specialization
from backend.planner.config_optimizer.rows import detect_planet_specialization
from backend.planner.config_optimizer.search import central_reserve_needed, polish_both_ways
from backend.planner.config_optimizer.tech_graph import building_upgrade_cost_spice_eq

SimOutcome = dict[str, FloatArray | IntArray | BoolArray]


def _index_previous_planets(
    raw_planets: Sequence[object],
) -> tuple[
    dict[int, Mapping[str, object]],
    dict[str, Mapping[str, object]],
    dict[int, Mapping[str, object]],
]:
    by_pid: dict[int, Mapping[str, object]] = {}
    by_coords: dict[str, Mapping[str, object]] = {}
    by_placeholder_slot: dict[int, Mapping[str, object]] = {}
    for entry in raw_planets:
        if not isinstance(entry, Mapping):
            continue
        typed: dict[str, object] = {str(k): v for k, v in entry.items()}
        pid = coerce_int(typed.get("planet_id"), 0)
        if pid > 0:
            by_pid[pid] = typed
        raw_c = str(typed.get("coords") or "").strip()
        if raw_c:
            by_coords[raw_c] = typed
            if not raw_c.startswith("EMPTY:"):
                by_coords[normalize_coords(raw_c)] = typed
        if bool(typed.get("is_placeholder")):
            slot = coerce_int(typed.get("slot", typed.get("slot_index")), 0)
            if slot > 0:
                by_placeholder_slot[slot] = typed
    return by_pid, by_coords, by_placeholder_slot


def _extract_prev_building_maps(matched: Mapping[str, object]) -> tuple[dict[str, int], dict[str, int]]:
    planned_map: dict[str, int] = {}
    built_map: dict[str, int] = {}
    raw_built = matched.get("built_floors")
    if isinstance(raw_built, Mapping):
        for bk, lvl in raw_built.items():
            built_map[str(bk)] = max(0, coerce_int(lvl, 0))
    raw_blds = matched.get("buildings")
    if isinstance(raw_blds, (list, tuple)):
        for row in raw_blds:
            if not isinstance(row, Mapping):
                continue
            bk = str(row.get("key") or row.get("building_key") or "").strip()
            if not bk:
                continue
            planned_map[bk] = max(0, coerce_int(row.get("target_level", row.get("level", 0)), 0))
            if bk not in built_map:
                built_map[bk] = max(0, coerce_int(row.get("built_level", 0), 0))
    elif isinstance(raw_blds, Mapping):
        for bk, entry in raw_blds.items():
            if isinstance(entry, Mapping):
                planned_map[str(bk)] = max(0, coerce_int(entry.get("target_level", entry.get("level", 0)), 0))
            else:
                planned_map[str(bk)] = max(0, coerce_int(entry, 0))
    return planned_map, built_map


def _extract_previous_planet_state(
    planet_plans: Sequence[Mapping[str, object]],
    optimized_keys: tuple[str, ...],
    previous_result: Mapping[str, object] | None,
) -> tuple[list[IntArray | None], list[str | None]]:
    """Match current planet plans against a previous Config Optimizer result for sticky warm-start."""
    prev_targets: list[IntArray | None] = [None] * len(planet_plans)
    prev_roles: list[str | None] = [None] * len(planet_plans)
    if not isinstance(previous_result, Mapping):
        return prev_targets, prev_roles
    raw_planets = previous_result.get("planets")
    if not isinstance(raw_planets, (list, tuple)) or not raw_planets:
        return prev_targets, prev_roles

    by_pid, by_coords, by_placeholder_slot = _index_previous_planets(raw_planets)
    for idx, plan in enumerate(planet_plans):
        matched: Mapping[str, object] | None = None
        pid = map_int(plan, "planet_id", 0)
        raw_c = map_str(plan, "coords", "").strip()
        norm_c = normalize_coords(raw_c) if raw_c and not raw_c.startswith("EMPTY:") else raw_c
        slot_index = map_int(plan, "slot_index", 0)
        if pid > 0 and pid in by_pid:
            matched = by_pid[pid]
        elif raw_c and raw_c in by_coords:
            matched = by_coords[raw_c]
        elif norm_c and norm_c in by_coords:
            matched = by_coords[norm_c]
        elif bool(plan["is_placeholder"]) and slot_index in by_placeholder_slot:
            matched = by_placeholder_slot[slot_index]
        if matched is None:
            continue

        planned_map, built_map = _extract_prev_building_maps(matched)
        if planned_map:
            prev_targets[idx] = np.array(
                [max(0, int(planned_map.get(key, 0))) for key in optimized_keys],
                dtype=np.int64,
            )

        explicit_prev_role = normalize_planet_specialization(matched.get("detected_specialization"))
        if explicit_prev_role in SPECIALIZATION_ALLOWED_BUILDINGS:
            prev_roles[idx] = explicit_prev_role
        elif planned_map:
            raw_prod = matched.get("production")
            prod = raw_prod if isinstance(raw_prod, Mapping) else {}
            prev_shipyard = coerce_finite_float(prod.get("shipyard_ships_per_month"), 0.0)
            inferred = detect_planet_specialization(
                planned_map,
                built_levels=built_map,
                shipyard_ships=prev_shipyard,
            )
            if inferred in SPECIALIZATION_ALLOWED_BUILDINGS:
                prev_roles[idx] = inferred

    return prev_targets, prev_roles


def _trim_design_to_fit(
    model: EmpireProductionModel,
    planet_idx: int,
    design: IntArray,
) -> IntArray:
    """Clip ``design`` into ``[min_levels, max_levels]`` and trim excess levels until feasible."""
    lo = model.min_levels[planet_idx]
    hi = model.max_levels[planet_idx]
    cand: IntArray = np.asarray(np.clip(np.asarray(design, dtype=np.int64), lo, hi), dtype=np.int64).copy()
    while not bool(model.is_feasible(cand[None, None, :], [planet_idx])[0]):
        slack = cand - lo
        if not bool((slack > 0).any()):
            return np.asarray(lo.copy(), dtype=np.int64)
        drop_idx = int(np.argmax(slack))
        cand[drop_idx] -= 1
    return cand


def _central_reserve_holds(
    model: EmpireProductionModel,
    outcome: Mapping[str, FloatArray | IntArray | BoolArray],
) -> bool:
    """Return True when the central planet's warehouses cover whole-empire reserve hours."""
    if model.central_planet < 0:
        return True
    net_per_hour = np.asarray(outcome["planet_net_per_hour"], dtype=np.float64)[0].sum(axis=0)
    needed = model.warehouse_hours * np.maximum(net_per_hour, 0.0)
    wh = np.asarray(outcome["planet_warehouse_levels"], dtype=np.int64)[0, model.central_planet]
    if bool((wh > model.top_warehouse_level).any()):
        return False
    held = model.warehouse_capacities[wh]
    return not bool((needed > held).any())


def _cached_upgrade_cost(cost_cache: dict[tuple[str, int, int], float], key: str, frm: int, to: int) -> float:
    if to <= frm:
        return 0.0
    ckey = (key, frm, to)
    val = cost_cache.get(ckey)
    if val is None:
        val = building_upgrade_cost_spice_eq(key, frm, to)
        cost_cache[ckey] = val
    return val


def _evaluate_stability_metrics(
    model: EmpireProductionModel,
    levels: IntArray,
    planet_plans: Sequence[Mapping[str, object]],
    prev_roles: Sequence[str | None],
    cost_cache: dict[tuple[str, int, int], float],
) -> tuple[float, int, float, list[str], SimOutcome]:
    """Return ``(ships_per_month, role_changes, upgrade_cost_spice_eq, detected_roles, outcome)``."""
    outcome = model.simulate(levels[None])
    planet_limits = np.asarray(outcome["planet_limits"], dtype=np.float64)
    planet_wh = np.asarray(outcome["planet_warehouse_levels"], dtype=np.int64)
    limit_totals = planet_limits[0].sum(axis=0)
    ships = float(limit_totals.min())
    shipyard_col = model.limit_names.index("shipyards")

    roles: list[str] = []
    role_changes = 0
    total_cost = 0.0
    for idx, plan in enumerate(planet_plans):
        built = as_int_dict(plan["built_levels"])
        min_lvls = as_int_dict(plan["min_levels"])
        wh_lvls = planet_wh[0, idx]
        planned_opt = dict(zip(model.optimized_keys, (int(v) for v in levels[idx]), strict=True))
        role = detect_planet_specialization(
            planned_opt,
            min_levels=min_lvls,
            built_levels=built,
            shipyard_ships=float(planet_limits[0, idx, shipyard_col]),
            locked_specialization=map_str(plan, "locked_specialization", "auto"),
        )
        roles.append(role)
        prev_role = prev_roles[idx]
        if prev_role in SPECIALIZATION_ALLOWED_BUILDINGS and role != prev_role:
            role_changes += 1

        for b_idx, key in enumerate(model.optimized_keys):
            total_cost += _cached_upgrade_cost(cost_cache, key, int(built[key]), int(levels[idx, b_idx]))
        for w_idx, wkey in enumerate(WAREHOUSE_KEYS):
            total_cost += _cached_upgrade_cost(cost_cache, wkey, int(built[wkey]), int(wh_lvls[w_idx]))

    return ships, role_changes, total_cost, roles, outcome


def _transfer_design_for_swap(
    model: EmpireProductionModel,
    src_idx: int,
    dst_idx: int,
    src_design: IntArray,
) -> IntArray:
    """Transfer ``src_idx``'s actively chosen buildings onto ``dst_idx`` without leaking unrelated built floors."""
    active = src_design > model.min_levels[src_idx]
    transferred: IntArray = np.asarray(np.where(active, src_design, model.min_levels[dst_idx]), dtype=np.int64)
    return _trim_design_to_fit(model, dst_idx, transferred)


def _polish_pair_in_place(
    model: EmpireProductionModel,
    levels: IntArray,
    pair: tuple[int, int],
    moves: IntArray,
) -> IntArray:
    """Locally polish only the two swapped planets against the rest of the empire."""
    cand: IntArray = np.array(levels, dtype=np.int64, copy=True)
    planet_limits = np.asarray(model.simulate(cand[None])["planet_limits"], dtype=np.float64)[0]
    for _pass in range(2):
        changed = False
        for planet in pair:
            others: FloatArray = np.asarray(planet_limits.sum(axis=0) - planet_limits[planet], dtype=np.float64)

            def empire_score(own: FloatArray, others: FloatArray = others) -> FloatArray:
                return smallest_limit(np.asarray(others + own, dtype=np.float64))

            design = improve_design(model, planet, cand[planet], empire_score, moves)
            if bool((design != cand[planet]).any()):
                cand[planet] = design
                planet_limits[planet] = np.asarray(
                    model.simulate(design[None, None, :], [planet])["planet_limits"], dtype=np.float64
                )[0, 0]
                changed = True
        if not changed:
            break
    return cand


def _align_unlocked_planet_roles_for_sunk_cost(
    model: EmpireProductionModel,
    levels: IntArray,
    planet_plans: Sequence[Mapping[str, object]],
    prev_roles: Sequence[str | None],
    cost_cache: dict[tuple[str, int, int], float],
    step_sizes: tuple[int, ...] = (1, 2, 4, 8),
) -> IntArray:
    """Pairwise swap unlocked planets' target designs when it keeps/improves ships/mo and reduces role flips/cost."""
    unlocked = [
        idx
        for idx, plan in enumerate(planet_plans)
        if normalize_planet_specialization(plan.get("locked_specialization")) == "auto"
    ]
    if len(unlocked) < 2:
        return levels

    moves = planet_moves(levels.shape[1], step_sizes)
    cur: IntArray = np.array(levels, dtype=np.int64, copy=True)
    cur_ships, cur_changes, cur_cost, _roles, _outcome = _evaluate_stability_metrics(
        model, cur, planet_plans, prev_roles, cost_cache
    )
    for _pass in range(len(unlocked) * 2):
        improved = False
        for pos_a in range(len(unlocked)):
            i = unlocked[pos_a]
            for pos_b in range(pos_a + 1, len(unlocked)):
                j = unlocked[pos_b]
                if np.array_equal(cur[i], cur[j]):
                    continue
                cand = cur.copy()
                cand[i] = _transfer_design_for_swap(model, j, i, cur[j])
                cand[j] = _transfer_design_for_swap(model, i, j, cur[i])
                if np.array_equal(cand, cur) or not bool(model.is_feasible(cand[None])[0]):
                    continue
                cand = _polish_pair_in_place(model, cand, (i, j), moves)
                if not bool(model.is_feasible(cand[None])[0]):
                    continue
                cand_ships, cand_changes, cand_cost, _c_roles, cand_out = _evaluate_stability_metrics(
                    model, cand, planet_plans, prev_roles, cost_cache
                )
                if not _central_reserve_holds(model, cand_out):
                    continue
                better = False
                if cand_ships > cur_ships + 1e-6:
                    better = True
                elif cand_ships >= cur_ships - 1e-6:
                    if cand_changes < cur_changes or (cand_changes == cur_changes and cand_cost < cur_cost - 1e-3):
                        better = True
                if better:
                    cur = cand
                    cur_ships, cur_changes, cur_cost = cand_ships, cand_changes, cand_cost
                    improved = True
        if not improved:
            break
    return cur


def _build_sticky_candidate(
    best_model: EmpireProductionModel,
    fresh_levels: IntArray,
    planet_plans: Sequence[Mapping[str, object]],
    prev_targets: Sequence[IntArray | None],
    prev_roles: Sequence[str | None],
    step_sizes: tuple[int, ...],
) -> tuple[EmpireProductionModel | None, IntArray | None, float | None]:
    """Build and polish a role-preserving candidate anchored to previous planet specializations."""
    sticky_model = copy.copy(best_model)
    sticky_max = best_model.max_levels.copy()
    for idx, plan in enumerate(planet_plans):
        eff_role = normalize_planet_specialization(plan.get("locked_specialization"))
        if eff_role == "auto" and prev_roles[idx] in SPECIALIZATION_ALLOWED_BUILDINGS:
            eff_role = str(prev_roles[idx])
        allowed = SPECIALIZATION_ALLOWED_BUILDINGS.get(eff_role)
        if allowed is not None:
            for b_idx, key in enumerate(best_model.optimized_keys):
                if key not in allowed:
                    sticky_max[idx, b_idx] = best_model.min_levels[idx, b_idx]
    sticky_model.max_levels = sticky_max

    seed: IntArray = np.zeros_like(fresh_levels)
    for idx in range(len(planet_plans)):
        prev_vec = prev_targets[idx]
        base_vec = prev_vec if prev_vec is not None else fresh_levels[idx]
        seed[idx] = _trim_design_to_fit(sticky_model, idx, base_vec)

    sticky_levels = polish_both_ways(sticky_model, seed, step_sizes)
    for _round in range(3):
        outcome = sticky_model.simulate(sticky_levels[None])
        if _central_reserve_holds(sticky_model, outcome):
            break
        needed = central_reserve_needed(sticky_model, sticky_levels)
        next_reserve = np.maximum(sticky_model.central_reserve, needed)
        try:
            sticky_model = sticky_model.with_central_reserve(next_reserve)
        except ValueError:
            return None, None, None
        for idx in range(len(planet_plans)):
            sticky_levels[idx] = _trim_design_to_fit(sticky_model, idx, sticky_levels[idx])
        sticky_levels = polish_both_ways(sticky_model, sticky_levels, step_sizes)

    if not bool(sticky_model.is_feasible(sticky_levels[None])[0]):
        return None, None, None
    final_out = sticky_model.simulate(sticky_levels[None])
    if not _central_reserve_holds(sticky_model, final_out):
        return None, None, None
    sticky_limits = np.asarray(final_out["planet_limits"], dtype=np.float64)
    sticky_ships = float(sticky_limits[0].sum(axis=0).min())
    return sticky_model, sticky_levels, sticky_ships


__all__ = [
    "_align_unlocked_planet_roles_for_sunk_cost",
    "_build_sticky_candidate",
    "_central_reserve_holds",
    "_evaluate_stability_metrics",
    "_extract_previous_planet_state",
    "_polish_pair_in_place",
    "_transfer_design_for_swap",
    "_trim_design_to_fit",
]
