"""Arrow-friendly circular system-walk ordering and contiguous exploratory sector selection."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any, Optional

from spaceinvasion.core.coercion import coerce_int
from spaceinvasion.core.coords import ARROW_NAV_MAX_DISTANCE


def circular_system_delta(from_sys: int, to_sys: int, *, systems_per_galaxy: int = 400) -> tuple[int, int]:
    """Return ``(shortest_circular_distance, direction)`` where direction is ``+1``, ``-1``, or ``0``."""
    n_sys = max(1, coerce_int(systems_per_galaxy, 400))
    fwd = (int(to_sys) - int(from_sys)) % n_sys
    bwd = (int(from_sys) - int(to_sys)) % n_sys
    if fwd == 0:
        return 0, 0
    return (fwd, 1) if fwd <= bwd else (bwd, -1)


def _score_walk_sequence(
    seq: Sequence[int], *, start_sys: Optional[int], systems_per_galaxy: int, arrow_max_distance: int
) -> tuple[int, int, int]:
    if not seq:
        return (0, 0, 0)
    non_arrow, total_steps, first_dist = 0, 0, 0
    if start_sys is not None:
        first_dist, _ = circular_system_delta(start_sys, seq[0], systems_per_galaxy=systems_per_galaxy)
        if first_dist >= arrow_max_distance:
            non_arrow += 1
        total_steps += first_dist
    for idx in range(len(seq) - 1):
        d, _ = circular_system_delta(seq[idx], seq[idx + 1], systems_per_galaxy=systems_per_galaxy)
        if d >= arrow_max_distance:
            non_arrow += 1
        total_steps += d
    return (non_arrow, total_steps, first_dist)


def _parse_cursor(current_cursor: Optional[tuple[int, int]], n_sys: int) -> tuple[Optional[int], Optional[int]]:
    if isinstance(current_cursor, (tuple, list)) and len(current_cursor) == 2:
        cg_v, cs_v = coerce_int(current_cursor[0], 0), coerce_int(current_cursor[1], 0)
        if 1 <= cg_v <= 14 and 1 <= cs_v <= n_sys:
            return cg_v, cs_v
    return None, None


def _pick_arc_start(
    sys_sorted: list[int],
    sys_map: dict[int, dict[str, Any]],
    anchor_sys: int,
    n_sys: int,
    cur_match: bool,
    cur_s: Optional[int],
) -> int:
    k = len(sys_sorted)
    if k >= n_sys:
        if cur_match and cur_s is not None and cur_s in sys_map:
            return sys_sorted.index(cur_s)
        return sys_sorted.index(anchor_sys) if anchor_sys in sys_map else 0
    best_gap_key: Optional[tuple[int, int, int]] = None
    start_idx = 0
    for idx in range(k):
        cur_slot, nxt_s = sys_sorted[idx], sys_sorted[(idx + 1) % k]
        gap = (nxt_s - cur_slot) if idx + 1 < k else ((nxt_s + n_sys) - cur_slot)
        delta_anchor = abs(nxt_s - anchor_sys)
        circ_anchor = min(delta_anchor, max(0, n_sys - delta_anchor))
        cand_key = (gap, -circ_anchor, -nxt_s)
        if best_gap_key is None or cand_key > best_gap_key:
            best_gap_key, start_idx = cand_key, (idx + 1) % k
    return start_idx


def _nearest_neighbor_walk(sys_sorted: list[int], cur_s: int, n_sys: int, eff_arrow_max: int) -> list[int]:
    rem_set, nn_walk, head, pref_dir = set(sys_sorted), [], cur_s, 1
    while rem_set:
        chosen: Optional[int] = None
        for step_d in range(0, eff_arrow_max):
            if step_d == 0:
                if head in rem_set:
                    chosen = head
                    break
                continue
            c_pref = ((head - 1 + pref_dir * step_d) % n_sys) + 1
            if c_pref in rem_set:
                chosen = c_pref
                break
            c_opp = ((head - 1 - pref_dir * step_d) % n_sys) + 1
            if c_opp in rem_set:
                chosen, pref_dir = c_opp, -pref_dir
                break
        if chosen is None:
            best_s, best_k = None, None
            for cand in rem_set:
                cd, cdir = circular_system_delta(head, cand, systems_per_galaxy=n_sys)
                ck = (cd, 0 if cdir == pref_dir else 1, cand)
                if best_k is None or ck < best_k:
                    best_k, best_s = ck, cand
            chosen = best_s
            if chosen is not None and (cdir := circular_system_delta(head, chosen, systems_per_galaxy=n_sys)[1]) != 0:
                pref_dir = cdir
        assert chosen is not None
        rem_set.remove(chosen)
        nn_walk.append(chosen)
        head = chosen
    return nn_walk


def order_sectors_for_smooth_walk(
    sectors: Sequence[Mapping[str, Any]],
    *,
    primary_colony_by_galaxy: Optional[Mapping[int, int]] = None,
    current_cursor: Optional[tuple[int, int]] = None,
    systems_per_galaxy: int = 400,
    arrow_max_distance: int = ARROW_NAV_MAX_DISTANCE,
) -> list[dict[str, Any]]:
    """Order selected ``(galaxy, system)`` sector dicts along the smoothest arrow-friendly circular walk per galaxy."""
    n_sys = max(1, coerce_int(systems_per_galaxy, 400))
    eff_arrow_max = max(2, coerce_int(arrow_max_distance, ARROW_NAV_MAX_DISTANCE))
    by_gal: dict[int, dict[int, dict[str, Any]]] = {}
    gal_order: list[int] = []
    for item in sectors:
        if not isinstance(item, Mapping):
            continue
        g, s = coerce_int(item.get("galaxy"), 0), coerce_int(item.get("system"), 0)
        if not (1 <= g <= 14 and 1 <= s <= n_sys):
            continue
        if g not in by_gal:
            by_gal[g] = {}
            gal_order.append(g)
        by_gal[g].setdefault(s, dict(item))
    cur_g, cur_s = _parse_cursor(current_cursor, n_sys)
    if cur_g is not None and cur_g in by_gal and gal_order and gal_order[0] != cur_g:
        gal_order = [cur_g] + [g for g in gal_order if g != cur_g]
    col_map = primary_colony_by_galaxy if isinstance(primary_colony_by_galaxy, Mapping) else {}
    ordered: list[dict[str, Any]] = []
    for g in gal_order:
        sys_map = by_gal[g]
        sys_sorted = sorted(sys_map.keys())
        if len(sys_sorted) <= 1:
            ordered.extend(sys_map[s] for s in sys_sorted)
            continue
        anchor_sys = coerce_int(col_map.get(g), sys_sorted[0])
        if not (1 <= anchor_sys <= n_sys):
            anchor_sys = sys_sorted[0]
        start_idx = _pick_arc_start(sys_sorted, sys_map, anchor_sys, n_sys, cur_g == g, cur_s)
        arc_fwd = [sys_sorted[(start_idx + off) % len(sys_sorted)] for off in range(len(sys_sorted))]
        if cur_g == g and cur_s is not None:
            best_seq = min(
                (arc_fwd, list(reversed(arc_fwd)), _nearest_neighbor_walk(sys_sorted, cur_s, n_sys, eff_arrow_max)),
                key=lambda seq: _score_walk_sequence(
                    seq, start_sys=cur_s, systems_per_galaxy=n_sys, arrow_max_distance=eff_arrow_max
                ),
            )
            ordered.extend(sys_map[s_val] for s_val in best_seq)
        else:
            ordered.extend(sys_map[s_val] for s_val in arc_fwd)
    return ordered


def _pick_cursor_anchored_run(
    rem: dict[int, tuple[int, dict[str, Any]]],
    picked: list[dict[str, Any]],
    target_count: int,
    cur_s: int,
    n_sys: int,
    eff_arrow_max: int,
) -> None:
    s0 = min(
        rem.keys(),
        key=lambda s: (
            circular_system_delta(cur_s, s, systems_per_galaxy=n_sys)[0],
            0 if circular_system_delta(cur_s, s, systems_per_galaxy=n_sys)[1] >= 0 else 1,
            rem[s][0],
        ),
    )
    _, init_dir = circular_system_delta(cur_s, s0, systems_per_galaxy=n_sys)
    pref_dir = init_dir if init_dir != 0 else 1
    picked.append(rem.pop(s0)[1])
    head = s0
    while len(picked) < target_count and rem:
        nxt = next((c for d in range(1, eff_arrow_max) if (c := ((head - 1 + pref_dir * d) % n_sys) + 1) in rem), None)
        if nxt is None:
            for d in range(1, eff_arrow_max):
                c_o = ((head - 1 - pref_dir * d) % n_sys) + 1
                if c_o in rem:
                    nxt, pref_dir = c_o, -pref_dir
                    break
        if nxt is None:
            nxt = min(
                rem.keys(),
                key=lambda s: (
                    circular_system_delta(head, s, systems_per_galaxy=n_sys)[0],
                    0 if circular_system_delta(head, s, systems_per_galaxy=n_sys)[1] == pref_dir else 1,
                    rem[s][0],
                ),
            )
            if (jdir := circular_system_delta(head, nxt, systems_per_galaxy=n_sys)[1]) != 0:
                pref_dir = jdir
        picked.append(rem.pop(nxt)[1])
        head = nxt


def _grow_arc_run(
    rem: dict[int, tuple[int, dict[str, Any]]],
    picked: list[dict[str, Any]],
    target_count: int,
    n_sys: int,
    eff_arrow_max: int,
) -> None:
    s0 = min(rem.keys(), key=lambda s: rem[s][0])
    picked.append(rem.pop(s0)[1])
    arc_left, arc_right = s0, s0
    while len(picked) < target_count and rem:
        r_cand = next(
            ((d, c) for d in range(1, eff_arrow_max) if (c := ((arc_right - 1 + d) % n_sys) + 1) in rem), None
        )
        l_cand = next(((d, c) for d in range(1, eff_arrow_max) if (c := ((arc_left - 1 - d) % n_sys) + 1) in rem), None)
        if r_cand is not None and l_cand is not None:
            r_k = (coerce_int(rem[r_cand[1]][1].get("distance"), 0), r_cand[0], 0, rem[r_cand[1]][0])
            l_k = (coerce_int(rem[l_cand[1]][1].get("distance"), 0), l_cand[0], 1, rem[l_cand[1]][0])
            if r_k <= l_k:
                picked.append(rem.pop(r_cand[1])[1])
                arc_right = r_cand[1]
            else:
                picked.append(rem.pop(l_cand[1])[1])
                arc_left = l_cand[1]
        elif r_cand is not None:
            picked.append(rem.pop(r_cand[1])[1])
            arc_right = r_cand[1]
        elif l_cand is not None:
            picked.append(rem.pop(l_cand[1])[1])
            arc_left = l_cand[1]
        else:
            s_new = min(rem.keys(), key=lambda s: rem[s][0])
            picked.append(rem.pop(s_new)[1])
            arc_left, arc_right = s_new, s_new


def select_contiguous_exploratory_sectors(
    unscanned_pool: Sequence[dict[str, Any]],
    target_count: int,
    *,
    colony_gal_set: set[int],
    current_cursor: Optional[tuple[int, int]] = None,
    systems_per_galaxy: int = 400,
    arrow_max_distance: int = ARROW_NAV_MAX_DISTANCE,
) -> list[dict[str, Any]]:
    """Select up to ``target_count`` unscanned sectors favoring contiguous ``< arrow_max_distance`` runs."""
    if target_count <= 0 or not unscanned_pool:
        return []
    if len(unscanned_pool) <= target_count:
        return list(unscanned_pool)
    n_sys = max(1, coerce_int(systems_per_galaxy, 400))
    eff_arrow_max = max(2, coerce_int(arrow_max_distance, ARROW_NAV_MAX_DISTANCE))
    cur_g, cur_s = _parse_cursor(current_cursor, n_sys)
    tiers: list[list[tuple[int, dict[str, Any]]]] = [[], []]
    for rank_idx, item in enumerate(unscanned_pool):
        tiers[0 if coerce_int(item.get("galaxy"), 0) in colony_gal_set else 1].append((rank_idx, item))
    picked: list[dict[str, Any]] = []
    for tier_items in tiers:
        if len(picked) >= target_count or not tier_items:
            continue
        by_gal: dict[int, dict[int, tuple[int, dict[str, Any]]]] = {}
        gal_order: list[int] = []
        for rank_idx, item in tier_items:
            g, s = coerce_int(item.get("galaxy"), 0), coerce_int(item.get("system"), 0)
            if 1 <= g <= 14 and 1 <= s <= n_sys:
                if g not in by_gal:
                    by_gal[g] = {}
                    gal_order.append(g)
                by_gal[g].setdefault(s, (rank_idx, item))
        if cur_g is not None and cur_g in by_gal:
            gal_order = [cur_g] + [g for g in gal_order if g != cur_g]
        for g in gal_order:
            if len(picked) >= target_count:
                break
            rem = by_gal[g]
            if not rem:
                continue
            if cur_g == g and cur_s is not None:
                _pick_cursor_anchored_run(rem, picked, target_count, cur_s, n_sys, eff_arrow_max)
                cur_g = None
            else:
                _grow_arc_run(rem, picked, target_count, n_sys, eff_arrow_max)
    return picked
