"""Shared tutorial prerequisite emission and effective-research helpers."""

from __future__ import annotations

from collections.abc import Mapping

from spaceinvasion.core import catalog
from spaceinvasion.core.config import StrategyConfig
from spaceinvasion.core.state import BuildingOffer, GameState, PlanetState, ResearchOffer
from spaceinvasion.planner.heuristics._actions import _make_build_action, _make_research_action
from spaceinvasion.planner.heuristics._common import (
    _apply_queue_cap_if_full,
    _is_building_allowed_on_planet,
    _planet_has_free_building_fields,
    _safe_strategy_int,
)
from spaceinvasion.planner.heuristics._econ_helpers import _resolve_prereq_chain
from spaceinvasion.planner.heuristics._queues import _active_research_increments, _eff_level
from spaceinvasion.planner.heuristics._types import PlannedAction


def _build_effective_researches(state: GameState) -> tuple[dict[str, int], dict[str, int]]:
    """Return `(active_research_increments, effective_research_levels)` for `state`."""
    active_r_inc = _active_research_increments(state)
    eff_researches: dict[str, int] = {
        catalog.safe_key(k): max(0, _safe_strategy_int(v, 0)) for k, v in (state.researches or {}).items()
    }
    for rk_i, delta_i in active_r_inc.items():
        eff_researches[rk_i] = eff_researches.get(rk_i, 0) + delta_i
    return active_r_inc, eff_researches


def _emit_tutorial_prereq_actions(
    *,
    tut_step_s: str,
    tut_total_s: str,
    category: str,
    slug_or_id: int | str,
    goal_label: str,
    state: GameState,
    build_planet: PlanetState,
    build_offers: Mapping[int, BuildingOffer],
    eff_buildings: Mapping[str, int],
    queue_full: bool,
    allow_remote_colony_build: bool,
    research_planet: PlanetState | None,
    research_offers: Mapping[str, ResearchOffer],
    research_queue_busy: bool,
    allow_live_research: bool,
    config: StrategyConfig,
    emitted_build_ids: set[tuple[int, int]],
    emitted_res_keys: set[str],
    eff_researches: Mapping[str, int] | None = None,
) -> list[PlannedAction]:
    """Resolve leaf building/research prerequisites for a tutorial target and emit actionable P2 unlocks."""
    res_map: Mapping[str, int] = eff_researches if eff_researches is not None else dict(state.researches or {})
    steps = _resolve_prereq_chain(
        category,
        str(slug_or_id),
        goal_label,
        eff_buildings,
        res_map,
    )
    out: list[PlannedAction] = []
    for req in steps:
        req_cat = str(req.get("category") or "")
        req_cur_s = catalog.fmt_compact_int(_safe_strategy_int(req.get("current", 0), 0))
        req_req_s = catalog.fmt_compact_int(_safe_strategy_int(req.get("required", 0), 0))
        req_name = str(req.get("name") or "")
        if req_cat == "buildings":
            req_info = catalog.resolve_unit("buildings", req["slug"])
            if not req_info or req_info.get("id") is None:
                continue
            req_bid = int(req_info["id"])
            if (build_planet.planet_id, req_bid) in emitted_build_ids:
                continue
            if not _is_building_allowed_on_planet(build_planet, req_bid, build_offers):
                continue
            if not _planet_has_free_building_fields(build_planet):
                continue
            emitted_build_ids.add((build_planet.planet_id, req_bid))
            pr_reason = (
                f"Tutorial {tut_step_s}/{tut_total_s}: unlock {req_name} ({req_cur_s}/{req_req_s}) for {goal_label}"
            )
            act = _make_build_action(
                "P2",
                build_planet,
                req_bid,
                req_name,
                pr_reason,
                build_offers,
                effective_level=_eff_level(build_planet, eff_buildings, req_bid),
                researches=state.researches,
                allow_remote_colony_build=allow_remote_colony_build,
            )
            out.append(_apply_queue_cap_if_full(act, queue_full))
        elif req_cat == "research" and research_planet is not None:
            res_info = catalog.resolve_unit("research", req["slug"])
            rkey = str((res_info or {}).get("id") or "")
            if not rkey or rkey in emitted_res_keys:
                continue
            emitted_res_keys.add(rkey)
            pr_reason = (
                f"Tutorial {tut_step_s}/{tut_total_s}: unlock {req_name} ({req_cur_s}/{req_req_s}) for {goal_label}"
            )
            out.append(
                _make_research_action(
                    "P2",
                    research_planet,
                    state,
                    rkey,
                    pr_reason,
                    research_offers,
                    research_queue_busy,
                    allow_live=config.allow_research and allow_live_research,
                )
            )
    return out
