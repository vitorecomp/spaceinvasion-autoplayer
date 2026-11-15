"""Tutorial fleet-milestone ship construction, prerequisite unlock, and escape-flight helpers."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from backend.core import catalog
from backend.core.config import StrategyConfig
from backend.core.model import (
    GameState,
    PlanetState,
    ResearchOffer,
    Resources,
    UnitOffer,
    is_in_flight_fleet_job,
)
from backend.planner.heuristics._common import (
    _is_building_allowed_on_planet,
    _norm_planet_type,
    _offer_map,
    _parse_gal_sys_pos,
    _resources_from_dict,
    _safe_strategy_int,
    _safe_strategy_str,
    _ship_offer_map,
)
from backend.planner.heuristics._queues import (
    _count_planet_building_queues,
    _eff_level,
    _effective_building_levels_and_energy,
    _max_build_slots,
)
from backend.planner.heuristics._tutorial_prereqs import (
    _build_effective_researches,
    _emit_tutorial_prereq_actions,
)
from backend.planner.heuristics._tutorial_tasks import _milestone_helper_ship_id
from backend.planner.heuristics._types import (
    RESEARCH_LAB,
    SHIP_SLUG_BY_KEY,
    WEAPONS_FACTORY,
    PlannedAction,
)

_ShipCandidate = tuple[
    PlanetState,
    UnitOffer | None,
    list[Mapping[str, object]],
    bool,
    str,
    Resources,
    bool,
]


@dataclass(frozen=True)
class _MilestoneContext:
    state: GameState
    active: PlanetState
    config: StrategyConfig
    tut_step_s: str
    tut_total_s: str
    non_raid_candidates: list[PlanetState]
    active_queue_full: bool
    research_queue_busy: bool
    active_r_offers: dict[str, ResearchOffer]
    eff_researches: dict[str, int]
    seen_build_ids: set[tuple[int, int]]
    seen_res_keys: set[str]


def _select_tutorial_ship_candidate(
    ctx: _MilestoneContext,
    sid_num: int,
    fallback_ship_name: str,
    *,
    hold_in_flight: bool,
) -> tuple[str | None, _ShipCandidate | None]:
    slug = SHIP_SLUG_BY_KEY.get(f"s{sid_num}")
    info = catalog.SHIPS.get(slug or "") if slug else None
    best_rank: tuple[bool, bool, bool, bool, bool, bool, bool, bool] | None = None
    best_tuple: _ShipCandidate | None = None
    for cand in ctx.non_raid_candidates:
        is_active = cand.planet_id == ctx.active.planet_id
        c_offer = _ship_offer_map(cand).get(sid_num)
        c_missing_all = (
            catalog.missing_prerequisites("ships", slug, buildings=cand.buildings, researches=ctx.state.researches)
            if slug
            else []
        )
        c_missing_b: list[Mapping[str, object]] = [m for m in c_missing_all if m.get("category") == "buildings"]
        c_wf_lvl = cand.building_level(WEAPONS_FACTORY)
        c_has_wf = c_wf_lvl >= 1 and not c_missing_b
        c_prereqs_ok = not c_missing_all
        if c_offer is not None:
            c_ship_name = _safe_strategy_str(c_offer.name) or f"Ship s{sid_num}"
            c_cost = c_offer.cost
            c_can_exec = bool(
                c_has_wf
                and c_prereqs_ok
                and not hold_in_flight
                and ctx.config.allow_shipyard
                and c_offer.can_build
                and cand.resources.can_afford(c_cost)
            )
        else:
            c_ship_name = fallback_ship_name or str((info or {}).get("name") or f"Ship s{sid_num}")
            c_cost = _resources_from_dict((info or {}).get("base_cost", {}))
            c_can_exec = False
        rank = (
            c_can_exec,
            c_prereqs_ok and c_offer is not None,
            c_has_wf and c_offer is not None,
            c_prereqs_ok,
            c_has_wf,
            c_offer is not None,
            c_wf_lvl >= 1,
            is_active,
        )
        if best_rank is None or rank > best_rank:
            best_rank = rank
            best_tuple = (cand, c_offer, c_missing_b, c_has_wf, c_ship_name, c_cost, c_can_exec)
    return slug, best_tuple


def _emit_tutorial_ship_and_prereqs(
    ctx: _MilestoneContext,
    out: list[PlannedAction],
    sid_num: int,
    needed_qty: int,
    base_reason: str,
    fallback_ship_name: str,
    *,
    hold_in_flight: bool = False,
) -> None:
    if not ctx.non_raid_candidates:
        return
    slug, best_tuple = _select_tutorial_ship_candidate(ctx, sid_num, fallback_ship_name, hold_in_flight=hold_in_flight)
    if best_tuple is None:
        return
    cand, offer, missing_b, _, ship_name, cost, can_exec = best_tuple
    if slug:
        is_cand_active = cand.planet_id == ctx.active.planet_id
        cand_offers = _offer_map(cand)
        cand_eff, _ = _effective_building_levels_and_energy(ctx.state, cand, is_active_planet=is_cand_active)
        res_planet: PlanetState | None = None
        cand_ptype = _norm_planet_type(cand)
        if cand_ptype != "raid" and (
            cand_ptype != "asteroid"
            or _eff_level(cand, cand_eff, RESEARCH_LAB) >= 1
            or _is_building_allowed_on_planet(cand, RESEARCH_LAB, cand_offers)
        ):
            res_planet = cand
        elif _norm_planet_type(ctx.active) not in ("raid", "asteroid"):
            res_planet = ctx.active
        out.extend(
            _emit_tutorial_prereq_actions(
                tut_step_s=ctx.tut_step_s,
                tut_total_s=ctx.tut_total_s,
                category="ships",
                slug_or_id=slug,
                goal_label=ship_name,
                state=ctx.state,
                build_planet=cand,
                build_offers=cand_offers,
                eff_buildings=cand_eff,
                queue_full=ctx.active_queue_full if is_cand_active else False,
                allow_remote_colony_build=ctx.config.allow_multi_planet and not is_cand_active,
                research_planet=res_planet,
                research_offers=ctx.active_r_offers
                if (res_planet and res_planet.planet_id == ctx.active.planet_id)
                else {},
                research_queue_busy=ctx.research_queue_busy,
                allow_live_research=bool(res_planet and res_planet.planet_id == ctx.active.planet_id),
                config=ctx.config,
                emitted_build_ids=ctx.seen_build_ids,
                emitted_res_keys=ctx.seen_res_keys,
                eff_researches=ctx.eff_researches,
            )
        )
    reason = base_reason
    if hold_in_flight:
        reason += " (hold while own fleet is in transit)"
    elif cand.building_level(WEAPONS_FACTORY) < 1:
        reason += " (Weapons factory b9 required)"
    elif missing_b:
        first_req = missing_b[0]
        cur_s = catalog.fmt_compact_int(_safe_strategy_int(first_req.get("current", 0), 0))
        req_s = catalog.fmt_compact_int(_safe_strategy_int(first_req.get("required", 0), 0))
        reason += f" (requires {first_req['name']} {cur_s}/{req_s})"
    out.append(
        PlannedAction(
            priority="P2",
            kind="BUILD_SHIP",
            planet_id=cand.planet_id,
            planet_coords=_safe_strategy_str(cand.coords),
            target_id=sid_num,
            target_name=ship_name,
            target_level=max(1, needed_qty) if offer is None else 1,
            cost=cost,
            missing=cand.resources.missing(cost),
            reason=reason,
            executable_now=can_exec,
        )
    )


def _handle_escape_milestone(
    ctx: _MilestoneContext,
    out: list[PlannedAction],
    desc_s: str,
    *,
    own_fleet_in_flight: bool,
) -> bool:
    esc_planets = [
        p
        for p in ctx.non_raid_candidates
        if any(k != "s9" and _safe_strategy_int(v, 0) > 0 for k, v in (p.ships or {}).items())
    ]
    if not esc_planets:
        if not ctx.non_raid_candidates:
            return False
        _emit_tutorial_ship_and_prereqs(
            ctx,
            out,
            1,
            1,
            f"Tutorial {ctx.tut_step_s}/{ctx.tut_total_s}: build Small transport for {desc_s}",
            "Small transport",
            hold_in_flight=own_fleet_in_flight,
        )
        return True
    esc_planet = next(
        (p for p in esc_planets if _safe_strategy_int(p.ships.get("s1", 0), 0) > 0),
        esc_planets[0],
    )
    e_gal, e_sys, e_pos = _parse_gal_sys_pos(esc_planet.coords)
    target_coords = f"{e_gal:02d}:{e_sys:03d}:{(e_pos % 16) + 1:02d}"
    esc_reason = f"Tutorial {ctx.tut_step_s}/{ctx.tut_total_s}: {desc_s}"
    if own_fleet_in_flight:
        esc_reason += " [Hold: Own fleet in transit]"
    out.append(
        PlannedAction(
            priority="P2",
            kind="ESCAPE_FLIGHT",
            planet_id=esc_planet.planet_id,
            planet_coords=_safe_strategy_str(esc_planet.coords),
            target_name=f"Save -> {target_coords}",
            reason=esc_reason,
            executable_now=bool(ctx.config.allow_fleetsave and not own_fleet_in_flight),
        )
    )
    return True


def _handle_helper_ship_milestone(
    ctx: _MilestoneContext,
    out: list[PlannedAction],
    desc_s: str,
    helper_sid: int,
) -> bool:
    """Emit helper ship build for spy/recycler/colony milestones. Returns True to skip P3 fallback."""
    helper_specs: dict[int, tuple[str, str, bool]] = {
        10: ("s10", "Spy probe", True),
        11: ("s11", "Recycler", False),
        8: ("s8", "Colony ship", False),
    }
    spec = helper_specs.get(helper_sid)
    if spec is None:
        return False
    skey, label, skip_p3_on_build = spec
    has_any = any(_safe_strategy_int(p.ships.get(skey, 0), 0) > 0 for p in ctx.non_raid_candidates)
    if has_any:
        return True
    if ctx.non_raid_candidates:
        _emit_tutorial_ship_and_prereqs(
            ctx,
            out,
            helper_sid,
            1,
            f"Tutorial {ctx.tut_step_s}/{ctx.tut_total_s}: build {label} for {desc_s}",
            label,
        )
        return skip_p3_on_build
    return False


def _plan_tutorial_fleet_milestones(
    state: GameState,
    active: PlanetState,
    config: StrategyConfig,
    *,
    planets_to_check: Sequence[PlanetState] | None = None,
    emitted_build_ids: set[tuple[int, int]] | None = None,
    emitted_res_keys: set[str] | None = None,
) -> list[PlannedAction]:
    """Emit ship build, prerequisite unlock, and non-combat milestone actions for Tutorial Steps 5-11."""
    if state.tutorial is None:
        return []
    tut = state.tutorial
    tut_step_s = catalog.fmt_compact_int(tut.step)
    tut_total_s = catalog.fmt_compact_int(tut.total_steps)
    if planets_to_check is not None:
        candidates_all = list(planets_to_check)
    elif config.allow_multi_planet:
        candidates_all = [active] + [p for p in state.planets if p.planet_id != active.planet_id]
    else:
        candidates_all = [active]
    queued_b_cnt = _count_planet_building_queues(state, active, is_active_planet=True)
    _, eff_researches = _build_effective_researches(state)
    ctx = _MilestoneContext(
        state=state,
        active=active,
        config=config,
        tut_step_s=tut_step_s,
        tut_total_s=tut_total_s,
        non_raid_candidates=[p for p in candidates_all if _norm_planet_type(p) != "raid"],
        active_queue_full=queued_b_cnt >= _max_build_slots(active, state),
        research_queue_busy=(
            any(q.category == "research" for q in state.active_queues) or any(o.active for o in state.research_offers)
        ),
        active_r_offers={o.research_key: o for o in state.research_offers if o.research_key},
        eff_researches=eff_researches,
        seen_build_ids=emitted_build_ids if emitted_build_ids is not None else set(),
        seen_res_keys=emitted_res_keys if emitted_res_keys is not None else set(),
    )
    out: list[PlannedAction] = []
    active_coords_s = _safe_strategy_str(active.coords)
    own_fleet_in_flight = any(is_in_flight_fleet_job(q) for q in state.active_queues)

    for task in tut.tasks:
        if task.completed:
            continue
        sid = catalog.safe_optional_int(task.building_id, None)
        if task.category == "ship" and sid is not None and sid > 0:
            t_cur = _safe_strategy_int(task.current, 0)
            t_tgt = _safe_strategy_int(task.target, 0)
            _emit_tutorial_ship_and_prereqs(
                ctx,
                out,
                sid,
                max(1, t_tgt - t_cur),
                f"Tutorial {tut_step_s}/{tut_total_s}: {_safe_strategy_str(task.description)}",
                _safe_strategy_str(task.building_name),
            )
        elif task.category == "milestone":
            desc_s = _safe_strategy_str(task.description)
            desc_l = desc_s.lower()
            if any(word in desc_l for word in ("fight", "attack", "combat", "battle")):
                continue
            if any(word in desc_l for word in ("escape flight", "escape", "flucht", "fleetsave")):
                if _handle_escape_milestone(ctx, out, desc_s, own_fleet_in_flight=own_fleet_in_flight):
                    continue
            helper_sid = _milestone_helper_ship_id(desc_l)
            if helper_sid is not None and _handle_helper_ship_milestone(ctx, out, desc_s, helper_sid):
                continue
            out.append(
                PlannedAction(
                    priority="P3",
                    kind="TUTORIAL_TASK",
                    planet_id=active.planet_id,
                    planet_coords=active_coords_s,
                    target_name=desc_s,
                    reason=f"Tutorial {tut_step_s}/{tut_total_s}: {desc_s}",
                    executable_now=False,
                )
            )
    return out
