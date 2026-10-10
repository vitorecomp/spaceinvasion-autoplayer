"""Per-kind builders for core manual operator queue items (`BUILD`, `DEMOLISH`, `RESEARCH`, `SHIP`, `THROTTLE`)."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, replace

from spaceinvasion.core import catalog
from spaceinvasion.core.config import StrategyConfig
from spaceinvasion.core.state import GameState, PlanetState, Resources
from spaceinvasion.planner.heuristics._actions import _make_build_action
from spaceinvasion.planner.heuristics._common import (
    _is_building_allowed_on_planet,
    _norm_planet_type,
    _offer_map,
    _research_offer_map,
    _resources_from_dict,
    _safe_strategy_int,
    _safe_strategy_str,
    _ship_offer_map,
)
from spaceinvasion.planner.heuristics._types import (
    RESEARCH_LAB,
    SHIP_SLUG_BY_KEY,
    SOLAR_CELLS_ID,
    WEAPONS_FACTORY,
    PlannedAction,
)


@dataclass(frozen=True)
class _ManualItemContext:
    """Resolved planet and queue metadata shared across manual queue item builders."""

    state: GameState
    cfg: StrategyConfig
    planet: PlanetState
    planets_by_id: Mapping[int, PlanetState]
    planets_by_coords: Mapping[str, PlanetState]
    is_active_planet: bool
    b_queue_full: bool
    r_queue_full: bool
    qid: int
    prio: str
    srank: int
    base_tag: str
    note_s: str
    note_suffix: str


@dataclass(frozen=True)
class _ManualBuildResult:
    """Result from building a single manual queue item."""

    action: PlannedAction | None = None
    auto_satisfied: bool = False
    prereq_met_for_reserve: bool = False
    reserve_bundle: Resources = Resources()


def _build_manual_build_item(ctx: _ManualItemContext, item: Mapping[str, object]) -> _ManualBuildResult:
    bid = _safe_strategy_int(item.get("target_id"), 0)
    b_info = catalog.resolve_unit("buildings", bid) if bid > 0 else None
    if bid <= 0 or not b_info:
        return _ManualBuildResult()
    b_name = _safe_strategy_str(b_info.get("name") or item.get("target_name") or f"Building b{bid}")
    cur_lvl = ctx.planet.building_level(bid)
    goal_lvl = catalog.safe_optional_int(item.get("target_level"), None)
    if goal_lvl is not None and goal_lvl > 0 and cur_lvl >= goal_lvl:
        return _ManualBuildResult(auto_satisfied=True)

    b_offers = _offer_map(ctx.planet)
    offer = b_offers.get(bid)
    offer_tgt = catalog.safe_optional_int(offer.target_level, None) if offer is not None else None
    if goal_lvl is not None and offer_tgt is not None and offer_tgt > goal_lvl:
        return _ManualBuildResult(
            action=PlannedAction(
                priority=ctx.prio,
                kind="BUILD",
                planet_id=ctx.planet.planet_id,
                planet_coords=ctx.planet.coords,
                target_id=bid,
                target_name=b_name,
                target_level=goal_lvl,
                cost=Resources(),
                missing=Resources(),
                reason=f"{ctx.base_tag}: In-game build queue already covers L{goal_lvl}{ctx.note_suffix}",
                executable_now=False,
                manual_queue_id=ctx.qid,
                manual_sort_rank=ctx.srank,
            )
        )

    eff_cur = max(cur_lvl, (offer_tgt - 1) if (offer_tgt is not None and offer_tgt > cur_lvl) else cur_lvl)
    goal_hint = f" (goal L{goal_lvl})" if (goal_lvl is not None and goal_lvl > eff_cur + 1) else ""
    raw_b = _make_build_action(
        ctx.prio,
        ctx.planet,
        bid,
        b_name,
        f"{ctx.base_tag}: Queued upgrade{goal_hint}{ctx.note_suffix}",
        b_offers,
        effective_level=eff_cur,
        researches=ctx.state.researches,
        allow_remote_colony_build=bool(ctx.cfg.allow_multi_planet and not ctx.is_active_planet),
    )
    exec_now = raw_b.executable_now and not ctx.b_queue_full
    rsn = f"{raw_b.reason} (build queue full)" if (raw_b.executable_now and ctx.b_queue_full) else raw_b.reason
    built_act = replace(
        raw_b,
        priority=ctx.prio,
        kind="BUILD",
        executable_now=exec_now,
        reason=rsn,
        manual_queue_id=ctx.qid,
        manual_sort_rank=ctx.srank,
    )
    unmet = catalog.missing_prerequisites(
        "buildings",
        bid,
        buildings=ctx.planet.buildings,
        researches=ctx.state.researches or {},
    )
    return _ManualBuildResult(
        action=built_act,
        prereq_met_for_reserve=not unmet and _is_building_allowed_on_planet(ctx.planet, bid, b_offers),
        reserve_bundle=built_act.cost,
    )


def _build_manual_demolish_item(ctx: _ManualItemContext, item: Mapping[str, object]) -> _ManualBuildResult:
    bid = _safe_strategy_int(item.get("target_id"), 6) or 6
    b_info = catalog.resolve_unit("buildings", bid)
    b_name = _safe_strategy_str((b_info or {}).get("name") or item.get("target_name") or f"Building b{bid}")
    cur_lvl = ctx.planet.building_level(bid)
    raw_goal = catalog.safe_optional_int(item.get("target_level"), None)
    if cur_lvl <= 0 or (raw_goal is not None and cur_lvl <= max(0, raw_goal)):
        return _ManualBuildResult(auto_satisfied=True)

    step_lvl = max(0, cur_lvl - 1)
    energy_loss = max(0, catalog.building_energy_delta(bid, cur_lvl))
    energy_ok = not (energy_loss > 0 and getattr(ctx.planet.energy, "net", 0) - energy_loss < 0)
    exec_now = (not ctx.b_queue_full) and energy_ok
    goal_hint = f" (goal L{max(0, raw_goal)})" if (raw_goal is not None and raw_goal < step_lvl) else ""
    rsn = f"{ctx.base_tag}: Demolish L{cur_lvl} -> L{step_lvl}{goal_hint}{ctx.note_suffix}"
    if ctx.b_queue_full:
        rsn += " (build queue full)"
    elif not energy_ok:
        rsn += " (insufficient spare energy — upgrade Fusion plant first)"
    return _ManualBuildResult(
        action=PlannedAction(
            priority=ctx.prio,
            kind="DEMOLISH_BUILDING",
            planet_id=ctx.planet.planet_id,
            planet_coords=ctx.planet.coords,
            target_id=bid,
            target_name=b_name,
            target_level=step_lvl,
            cost=Resources(),
            missing=Resources(),
            reason=rsn,
            executable_now=exec_now,
            manual_queue_id=ctx.qid,
            manual_sort_rank=ctx.srank,
        )
    )


def _build_manual_research_item(ctx: _ManualItemContext, item: Mapping[str, object]) -> _ManualBuildResult:
    rid = _safe_strategy_int(item.get("target_id"), 0)
    rkey = f"r{rid}"
    r_info = catalog.resolve_unit("research", rkey) if rid > 0 else None
    if rid <= 0 or not r_info:
        return _ManualBuildResult()
    r_name = _safe_strategy_str(r_info.get("name") or item.get("target_name") or rkey)
    cur_lvl = max(0, _safe_strategy_int((ctx.state.researches or {}).get(rkey, 0), 0))
    goal_lvl = catalog.safe_optional_int(item.get("target_level"), None)
    if goal_lvl is not None and goal_lvl > 0 and cur_lvl >= goal_lvl:
        return _ManualBuildResult(auto_satisfied=True)

    nxt_lvl = cur_lvl + 1
    r_off = _research_offer_map(ctx.state).get(rkey)
    if r_off is not None and catalog.safe_optional_int(r_off.target_level, None) == nxt_lvl:
        cost = r_off.cost
    else:
        levels_raw = r_info.get("levels")
        lvl_row = levels_raw.get(nxt_lvl) if isinstance(levels_raw, Mapping) else None
        cost = _resources_from_dict(lvl_row.get("cost") if isinstance(lvl_row, Mapping) else None)
    missing = ctx.planet.resources.missing(cost) if not cost.is_zero() else Resources()
    unmet = catalog.missing_prerequisites(
        "research",
        rkey,
        buildings=ctx.planet.buildings,
        researches=ctx.state.researches or {},
    )
    has_lab = ctx.planet.building_level(RESEARCH_LAB) >= 1
    can_afford = not cost.is_zero() and ctx.planet.resources.can_afford(cost)
    exec_now = (
        has_lab
        and not unmet
        and not ctx.r_queue_full
        and (bool(r_off.can_research) if (r_off is not None and ctx.is_active_planet) else can_afford)
    )
    goal_hint = f" (goal L{goal_lvl})" if (goal_lvl is not None and goal_lvl > nxt_lvl) else ""
    rsn = f"{ctx.base_tag}: Queued research{goal_hint}{ctx.note_suffix}"
    if not has_lab:
        rsn += " (requires Research lab)"
    elif unmet:
        rsn += f" (needs {unmet[0]['name']} L{unmet[0]['required']})"
    elif ctx.r_queue_full:
        rsn += " (research queue busy)"
    return _ManualBuildResult(
        action=PlannedAction(
            priority=ctx.prio,
            kind="RESEARCH",
            planet_id=ctx.planet.planet_id,
            planet_coords=ctx.planet.coords,
            target_id=rid,
            target_name=r_name,
            target_level=nxt_lvl,
            cost=cost,
            missing=missing,
            reason=rsn,
            executable_now=exec_now,
            manual_queue_id=ctx.qid,
            manual_sort_rank=ctx.srank,
        ),
        prereq_met_for_reserve=has_lab and not unmet,
        reserve_bundle=cost,
    )


def _build_manual_ship_item(ctx: _ManualItemContext, item: Mapping[str, object]) -> _ManualBuildResult:
    sid = _safe_strategy_int(item.get("target_id"), 0)
    s_slug = SHIP_SLUG_BY_KEY.get(f"s{sid}", f"s{sid}")
    s_info = (
        (
            catalog.resolve_unit("ships", s_slug)
            or catalog.resolve_unit("ships", f"s{sid}")
            or catalog.resolve_unit("ships", sid)
        )
        if sid > 0
        else None
    )
    if sid <= 0 or not s_info:
        return _ManualBuildResult()
    s_name = _safe_strategy_str(s_info.get("name") or item.get("target_name") or f"Ship s{sid}")
    qty = max(1, _safe_strategy_int(item.get("target_level"), 1))
    s_off = _ship_offer_map(ctx.planet).get(sid)
    unit_cost = s_off.cost if s_off is not None else _resources_from_dict(s_info.get("base_cost"))
    total_cost = Resources(
        pig_iron=unit_cost.pig_iron * qty,
        metal=unit_cost.metal * qty,
        kryptonite=unit_cost.kryptonite * qty,
        spice=unit_cost.spice * qty,
    )
    missing = ctx.planet.resources.missing(total_cost) if not total_cost.is_zero() else Resources()
    raw_slug = s_info.get("slug")
    ship_slug_s = raw_slug if isinstance(raw_slug, str) and raw_slug else f"s{sid}"
    unmet = catalog.missing_prerequisites(
        "ships",
        ship_slug_s,
        buildings=ctx.planet.buildings,
        researches=ctx.state.researches or {},
    )
    has_wf = ctx.planet.building_level(WEAPONS_FACTORY) >= 1
    solar_blocked = (
        sid == SOLAR_CELLS_ID
        and bool(getattr(ctx.cfg, "forbid_planetary_solar", False))
        and _norm_planet_type(ctx.planet) != "asteroid"
    )
    exec_now = (
        has_wf
        and not unmet
        and not solar_blocked
        and not total_cost.is_zero()
        and ctx.planet.resources.can_afford(total_cost)
    )
    rsn = f"{ctx.base_tag}: Build {qty}x {s_name}{ctx.note_suffix}"
    if solar_blocked:
        rsn += " (blocked on normal planets by forbid_planetary_solar)"
    elif not has_wf:
        rsn += " (requires Weapons factory)"
    elif unmet:
        rsn += f" (needs {unmet[0]['name']} L{unmet[0]['required']})"
    return _ManualBuildResult(
        action=PlannedAction(
            priority=ctx.prio,
            kind="BUILD_SHIP",
            planet_id=ctx.planet.planet_id,
            planet_coords=ctx.planet.coords,
            target_id=sid,
            target_name=s_name,
            target_level=qty,
            cost=unit_cost,
            missing=missing,
            reason=rsn,
            executable_now=exec_now,
            manual_queue_id=ctx.qid,
            manual_sort_rank=ctx.srank,
        ),
        prereq_met_for_reserve=has_wf and not unmet and not solar_blocked,
        reserve_bundle=total_cost,
    )


def _build_manual_throttle_item(ctx: _ManualItemContext, item: Mapping[str, object]) -> _ManualBuildResult:
    bid = _safe_strategy_int(item.get("target_id"), 2) or 2
    b_info = catalog.resolve_unit("buildings", bid)
    b_name = _safe_strategy_str((b_info or {}).get("name") or item.get("target_name") or f"Building b{bid}")
    raw_pct = _safe_strategy_int(item.get("target_level"), 100)
    pct = max(0, min(100, (raw_pct // 10) * 10))
    cur_rate = (ctx.planet.economy_rates or {}).get(f"resource{bid}")
    if cur_rate is not None and _safe_strategy_int(cur_rate, -1) == pct:
        return _ManualBuildResult(auto_satisfied=True)
    return _ManualBuildResult(
        action=PlannedAction(
            priority=ctx.prio,
            kind="THROTTLE_BUILDING",
            planet_id=ctx.planet.planet_id,
            planet_coords=ctx.planet.coords,
            target_id=bid,
            target_name=b_name,
            target_level=pct,
            cost=Resources(),
            missing=Resources(),
            reason=f"{ctx.base_tag}: Set {b_name} rate to {pct}%{ctx.note_suffix}",
            executable_now=ctx.planet.building_level(bid) > 0,
            manual_queue_id=ctx.qid,
            manual_sort_rank=ctx.srank,
        )
    )
