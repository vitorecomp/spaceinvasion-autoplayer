"""Smart 2-phase galaxy scan batch selection with smooth circular walk ordering."""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from typing import Any, Optional

from spaceinvasion.combat._scanner_sectors import (
    extract_own_colonies_and_coords,
    format_age_short,
    score_galaxy_scan_sectors,
)
from spaceinvasion.combat._scanner_walk import (
    order_sectors_for_smooth_walk,
    select_contiguous_exploratory_sectors,
)
from spaceinvasion.core.catalog import safe_finite_float
from spaceinvasion.core.coercion import coerce_int


def _build_scanned_pool(items: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    pool = [dict(it, phase="targeted_refresh") for it in items if it["scanned"]]
    pool.sort(
        key=lambda it: (
            0 if not it["is_fresh"] else 1,
            -it["score"],
            -it["importance"],
            it["distance"],
            it["galaxy"],
            it["system"],
        )
    )
    for it in pool:
        if str(it.get("reason") or "").startswith("unscanned"):
            it["reason"] = f"d={it['distance']}, age {format_age_short(it['age_seconds'])}"
    return pool


def _maybe_walk(
    sectors: list[dict[str, Any]],
    *,
    smooth_walk: bool,
    primary_sys_by_gal: Mapping[int, int],
    cursor: Optional[tuple[int, int]],
    n_sys: int,
) -> list[dict[str, Any]]:
    if smooth_walk and len(sectors) > 1:
        return order_sectors_for_smooth_walk(
            sectors, primary_colony_by_galaxy=primary_sys_by_gal, current_cursor=cursor, systems_per_galaxy=n_sys
        )
    return sectors


def _cursor_after(walk: list[dict[str, Any]], fallback: Optional[tuple[int, int]]) -> Optional[tuple[int, int]]:
    return (int(walk[-1]["galaxy"]), int(walk[-1]["system"])) if walk else fallback


def _select_explicit_split(
    ranked: Sequence[dict[str, Any]],
    eff_batch: int,
    eff_refresh_pct: float,
    colony_gal_set: set[int],
    primary_sys_by_gal: Mapping[int, int],
    current_cursor: Optional[tuple[int, int]],
    smooth_walk: bool,
    n_sys: int,
) -> tuple[str, list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    unscanned_pool = [dict(it, phase="exploratory") for it in ranked if not it["scanned"]]
    scanned_pool = _build_scanned_pool(ranked)
    target_ref = min(eff_batch, max(0, int(round(eff_batch * (eff_refresh_pct / 100.0)))))
    take_ref = list(scanned_pool[:target_ref])
    eff_exp_budget = max(0, eff_batch - target_ref) + max(0, target_ref - len(take_ref))
    take_exp = select_contiguous_exploratory_sectors(
        unscanned_pool,
        eff_exp_budget,
        colony_gal_set=colony_gal_set,
        current_cursor=current_cursor if eff_refresh_pct < 100.0 else None,
        systems_per_galaxy=n_sys,
    )
    if len(take_exp) < eff_exp_budget:
        take_ref.extend(scanned_pool[len(take_ref) : len(take_ref) + (eff_exp_budget - len(take_exp))])
    if eff_refresh_pct >= 100.0:
        walk_ref = _maybe_walk(
            take_ref, smooth_walk=smooth_walk, primary_sys_by_gal=primary_sys_by_gal, cursor=current_cursor, n_sys=n_sys
        )
        walk_exp = _maybe_walk(
            take_exp,
            smooth_walk=smooth_walk,
            primary_sys_by_gal=primary_sys_by_gal,
            cursor=_cursor_after(walk_ref, current_cursor),
            n_sys=n_sys,
        )
        priority_selected, selected_sectors = [*take_ref, *take_exp], [*walk_ref, *walk_exp]
    else:
        walk_exp = _maybe_walk(
            take_exp, smooth_walk=smooth_walk, primary_sys_by_gal=primary_sys_by_gal, cursor=current_cursor, n_sys=n_sys
        )
        walk_ref = _maybe_walk(
            take_ref,
            smooth_walk=smooth_walk,
            primary_sys_by_gal=primary_sys_by_gal,
            cursor=_cursor_after(walk_exp, current_cursor),
            n_sys=n_sys,
        )
        priority_selected, selected_sectors = [*take_exp, *take_ref], [*walk_exp, *walk_ref]
    phase = "mixed" if (walk_exp and walk_ref) else ("exploratory" if walk_exp else "targeted_refresh")
    return phase, priority_selected, selected_sectors, walk_exp, walk_ref


def select_smart_galaxy_scan_batch(
    *,
    own_coords: Sequence[str] | str = (),
    sector_intel: Optional[Mapping[str, Any]] = None,
    galaxies: Optional[Sequence[int]] = None,
    scanned_systems: Optional[Mapping[Any, Any] | Iterable[Any]] = None,
    raid_targets: Optional[Sequence[Mapping[str, Any]]] = None,
    raid_stats: Optional[Mapping[str, Any]] = None,
    raid_history: Optional[Sequence[Mapping[str, Any]]] = None,
    batch_size: int = 100,
    explore_first: bool = True,
    refresh_pct: Optional[float] = None,
    near_weight: float = 2.0,
    attack_weight: float = 3.0,
    collect_colonies: bool = True,
    min_freshness_seconds: float = 120.0,
    max_radius: Optional[int] = None,
    smooth_walk: bool = True,
    current_cursor: Optional[tuple[int, int]] = None,
    systems_per_galaxy: int = 400,
    now_ts: Optional[float] = None,
) -> dict[str, Any]:
    """Select a smart galaxy scan batch (``exploratory``, ``mixed``, or ``targeted_refresh``)."""
    n_sys, eff_batch = max(1, coerce_int(systems_per_galaxy, 400)), max(1, coerce_int(batch_size, 100))
    raw_refresh = max(0.0, min(100.0, safe_finite_float(refresh_pct, 0.0))) if refresh_pct is not None else None
    use_split = raw_refresh is not None and raw_refresh > 0.0
    eff_refresh_pct: float = (
        float(raw_refresh) if raw_refresh is not None and raw_refresh > 0.0 else (0.0 if explore_first else 100.0)
    )
    own_colonies, _ = extract_own_colonies_and_coords(own_coords, systems_per_galaxy=n_sys)
    ranked = score_galaxy_scan_sectors(
        own_coords=own_coords,
        sector_intel=sector_intel,
        galaxies=galaxies,
        scanned_systems=scanned_systems,
        raid_targets=raid_targets,
        raid_stats=raid_stats,
        raid_history=raid_history,
        explore_first=bool(explore_first or use_split),
        near_weight=near_weight,
        attack_weight=attack_weight,
        collect_colonies=collect_colonies,
        min_freshness_seconds=min_freshness_seconds,
        max_radius=max_radius,
        systems_per_galaxy=n_sys,
        now_ts=now_ts,
    )
    active_galaxies = list(dict.fromkeys(int(it["galaxy"]) for it in ranked)) or [1]
    total_systems = len(active_galaxies) * n_sys if max_radius is None else len(ranked)
    explored_systems = sum(1 for it in ranked if it["scanned"])
    unscanned_systems = max(0, total_systems - explored_systems)
    coverage_pct = round((explored_systems / total_systems) * 100.0, 2) if total_systems > 0 else 0.0
    primary_sys_by_gal: dict[int, int] = {}
    colony_gal_set: set[int] = set()
    for cg, cs in own_colonies:
        primary_sys_by_gal.setdefault(cg, cs)
        colony_gal_set.add(cg)
    if use_split:
        assert raw_refresh is not None
        phase, priority_selected, selected_sectors, walk_exp, walk_ref = _select_explicit_split(
            ranked, eff_batch, eff_refresh_pct, colony_gal_set, primary_sys_by_gal, current_cursor, smooth_walk, n_sys
        )
    elif explore_first and unscanned_systems > 0:
        phase = "exploratory"
        unscanned_pool = [dict(it, phase="exploratory") for it in ranked if not it["scanned"]]
        take_exp = select_contiguous_exploratory_sectors(
            unscanned_pool,
            eff_batch,
            colony_gal_set=colony_gal_set,
            current_cursor=current_cursor,
            systems_per_galaxy=n_sys,
        )
        rem_budget = max(0, eff_batch - len(take_exp))
        take_ref = _build_scanned_pool(ranked)[:rem_budget] if rem_budget > 0 else []
        priority_selected = [*take_exp, *take_ref]
        walk_exp = _maybe_walk(
            take_exp, smooth_walk=smooth_walk, primary_sys_by_gal=primary_sys_by_gal, cursor=current_cursor, n_sys=n_sys
        )
        walk_ref = _maybe_walk(
            take_ref,
            smooth_walk=smooth_walk,
            primary_sys_by_gal=primary_sys_by_gal,
            cursor=_cursor_after(walk_exp, current_cursor),
            n_sys=n_sys,
        )
        selected_sectors = [*walk_exp, *walk_ref]
    else:
        phase = "targeted_refresh"
        ref_ranked = score_galaxy_scan_sectors(
            own_coords=own_coords,
            sector_intel=sector_intel,
            galaxies=galaxies,
            scanned_systems=scanned_systems,
            raid_targets=raid_targets,
            raid_stats=raid_stats,
            raid_history=raid_history,
            explore_first=False,
            near_weight=near_weight,
            attack_weight=attack_weight,
            collect_colonies=collect_colonies,
            min_freshness_seconds=min_freshness_seconds,
            max_radius=max_radius,
            systems_per_galaxy=n_sys,
            now_ts=now_ts,
        )
        priority_selected = [dict(it, phase="targeted_refresh") for it in ref_ranked[:eff_batch]]
        walk_exp = []
        walk_ref = _maybe_walk(
            priority_selected,
            smooth_walk=smooth_walk,
            primary_sys_by_gal=primary_sys_by_gal,
            cursor=current_cursor,
            n_sys=n_sys,
        )
        selected_sectors = walk_ref
    sample_targets = [
        {
            k: it[k]
            for k in (
                "sector",
                "galaxy",
                "system",
                "phase",
                "score",
                "distance",
                "direct_raid_count",
                "owner_affinity_raid_count",
                "reason",
            )
        }
        for it in priority_selected[:5]
    ]
    return {
        "phase": phase,
        "scan_phase": phase,
        "galaxies": active_galaxies,
        "explored_systems": explored_systems,
        "unscanned_systems": unscanned_systems,
        "total_systems": total_systems,
        "coverage_pct": coverage_pct,
        "refresh_pct": round(eff_refresh_pct, 1),
        "discovery_pct": round(100.0 - eff_refresh_pct, 1),
        "exploratory_count": len(walk_exp),
        "refresh_count": len(walk_ref),
        "selected_sectors": selected_sectors,
        "sample_targets": sample_targets,
    }
