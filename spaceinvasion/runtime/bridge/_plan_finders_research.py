"""Direct research action finder helpers."""

from __future__ import annotations

from collections.abc import Mapping
import re

from spaceinvasion.core import catalog as game_data
from spaceinvasion.core.model import (
    GameState,
    PlanetState,
    PlannedAction,
    ResearchOffer,
    Resources,
    normalize_planet_type,
)
from spaceinvasion.planner.heuristics import _resources_from_dict
from spaceinvasion.runtime.api.validation import ApiRequestError
from spaceinvasion.runtime.bridge._context import BridgeContext
from spaceinvasion.runtime.bridge._helpers import safe_api_int

_RESEARCH_KEY_RE = re.compile(r"^r?(\d{1,3})$", re.IGNORECASE | re.ASCII)


def parse_research_id(research_key: str | int) -> int:
    """Parse a research identifier from an integer ID, `'r1'` key, or wiki slug."""
    if isinstance(research_key, int) and not isinstance(research_key, bool):
        if research_key < 1:
            raise ApiRequestError(f"Invalid research_id {research_key!r}.")
        return research_key
    raw = str(research_key or "").strip()
    m = _RESEARCH_KEY_RE.fullmatch(raw)
    if m:
        rid = int(m.group(1))
        if rid >= 1:
            return rid
    unit = game_data.resolve_unit("research", raw)
    if isinstance(unit, Mapping):
        uid = safe_api_int(unit.get("id"), 0)
        if uid >= 1:
            return uid
    raise ApiRequestError(f"Unknown research_key {research_key!r}.")


def _research_wiki_cost(rkey: str, nxt_lvl: int, fallback: Resources | None = None) -> tuple[Resources, str]:
    unit = game_data.resolve_unit("research", rkey)
    levels = unit.get("levels") if isinstance(unit, Mapping) else None
    lvl_entry = levels.get(nxt_lvl) if isinstance(levels, Mapping) else None
    raw_cost = lvl_entry.get("cost") if isinstance(lvl_entry, Mapping) else None
    cost = (
        _resources_from_dict(raw_cost)
        if isinstance(raw_cost, dict)
        else (fallback if fallback is not None else Resources())
    )
    name = str(unit.get("name") or "") if isinstance(unit, Mapping) else ""
    return cost, name


def _find_research_without_offer(
    state: GameState,
    planet: PlanetState,
    research_id: int,
    rkey: str,
    nxt_lvl: int,
    *,
    allow_live_verify: bool,
    res_busy: bool,
    missing_prereqs: list[dict[str, object]],
) -> PlannedAction:
    missing_b_prereqs = [m for m in missing_prereqs if m.get("category") == "buildings"]
    if (
        allow_live_verify
        and not res_busy
        and planet.building_level(8) >= 1
        and (
            (not state.research_offers and not missing_b_prereqs)
            or (planet.planet_id != state.active_planet_id and not missing_prereqs)
        )
    ):
        wiki_cost, unit_name = _research_wiki_cost(rkey, nxt_lvl)
        can_exec = not wiki_cost.is_zero() and planet.resources.can_afford(wiki_cost)
        rname = state.research_names.get(rkey, unit_name or f"Research {research_id}")
        return PlannedAction(
            priority="P2",
            kind="RESEARCH",
            planet_id=planet.planet_id,
            planet_coords=planet.coords,
            target_id=research_id,
            target_name=rname,
            target_level=nxt_lvl,
            cost=wiki_cost,
            missing=planet.resources.missing(wiki_cost),
            reason="API research command (live verify)"
            if can_exec
            else "API research command (insufficient resources)",
            executable_now=can_exec,
        )
    return PlannedAction(
        priority="P2",
        kind="RESEARCH",
        planet_id=planet.planet_id,
        planet_coords=planet.coords,
        target_id=research_id,
        target_name=state.research_names.get(rkey, f"Research {research_id}"),
        target_level=nxt_lvl,
        reason="API research command (offer unavailable/slot busy)",
        executable_now=False,
    )


def _find_research_with_offer(
    state: GameState,
    planet: PlanetState,
    offer: ResearchOffer,
    rkey: str,
    nxt_lvl: int,
    *,
    allow_live_verify: bool,
    res_busy: bool,
    missing_prereqs: list[dict[str, object]],
) -> PlannedAction:
    if (
        allow_live_verify
        and offer.target_level != nxt_lvl
        and not offer.active
        and not res_busy
        and planet.building_level(8) >= 1
        and not missing_prereqs
    ):
        wiki_cost, _ = _research_wiki_cost(rkey, nxt_lvl, fallback=offer.cost)
        can_exec = not wiki_cost.is_zero() and planet.resources.can_afford(wiki_cost)
        return PlannedAction(
            priority="P2",
            kind="RESEARCH",
            planet_id=planet.planet_id,
            planet_coords=planet.coords,
            target_id=offer.research_id,
            target_name=offer.name,
            target_level=nxt_lvl,
            cost=wiki_cost,
            missing=planet.resources.missing(wiki_cost),
            reason="API research command (live verify)",
            executable_now=can_exec,
        )
    can_exec = (
        offer.can_research
        and not res_busy
        and planet.building_level(8) >= 1
        and not missing_prereqs
        and planet.resources.can_afford(offer.cost)
    )
    if (
        not can_exec
        and allow_live_verify
        and not offer.active
        and not res_busy
        and planet.building_level(8) >= 1
        and not missing_prereqs
    ):
        if "not enough" in (offer.status_text or "").lower() and planet.resources.can_afford(offer.cost):
            can_exec = True
        elif planet.planet_id != state.active_planet_id:
            wiki_cost, _ = _research_wiki_cost(rkey, nxt_lvl)
            live_cost = offer.cost if not offer.cost.is_zero() else wiki_cost
            if not live_cost.is_zero() and planet.resources.can_afford(live_cost):
                return PlannedAction(
                    priority="P2",
                    kind="RESEARCH",
                    planet_id=planet.planet_id,
                    planet_coords=planet.coords,
                    target_id=offer.research_id,
                    target_name=offer.name,
                    target_level=nxt_lvl,
                    cost=live_cost,
                    missing=planet.resources.missing(live_cost),
                    reason="API research command (cross-planet check)",
                    executable_now=True,
                )
    return PlannedAction(
        priority="P2",
        kind="RESEARCH",
        planet_id=planet.planet_id,
        planet_coords=planet.coords,
        target_id=offer.research_id,
        target_name=offer.name,
        target_level=offer.target_level,
        cost=offer.cost,
        missing=planet.resources.missing(offer.cost),
        reason="API research command",
        executable_now=can_exec,
        action_href=offer.research_href if can_exec else None,
    )


def find_research_action(
    state: GameState,
    research_key: str | int,
    planet_id: int | None = None,
    *,
    allow_live_verify: bool = False,
) -> PlannedAction:
    """Resolve an explicit `RESEARCH` `PlannedAction` for `research_key` on `planet_id`."""
    research_id = parse_research_id(research_key)
    planet = BridgeContext.resolve_planet(state, planet_id)
    rkey = f"r{research_id}"
    nxt_lvl = safe_api_int(state.researches.get(rkey, 0)) + 1
    ptype_norm = normalize_planet_type(planet)
    if ptype_norm == "raid":
        return PlannedAction(
            priority="P2",
            kind="RESEARCH",
            planet_id=planet.planet_id,
            planet_coords=planet.coords,
            target_id=research_id,
            target_name=state.research_names.get(rkey, f"Research {research_id}"),
            target_level=nxt_lvl,
            reason=f"API research command (research not permitted on planet_type={ptype_norm})",
            executable_now=False,
            action_href=None,
        )
    res_busy = any(q.category == "research" for q in state.active_queues) or any(
        o.active for o in state.research_offers
    )
    offer = next((o for o in state.research_offers if o.research_id == research_id), None)
    missing_prereqs = [
        dict(m)
        for m in game_data.missing_prerequisites(
            "research", rkey, buildings=planet.buildings, researches=state.researches
        )
        if isinstance(m, Mapping)
    ]
    if offer is None:
        return _find_research_without_offer(
            state,
            planet,
            research_id,
            rkey,
            nxt_lvl,
            allow_live_verify=allow_live_verify,
            res_busy=res_busy,
            missing_prereqs=missing_prereqs,
        )
    return _find_research_with_offer(
        state,
        planet,
        offer,
        rkey,
        nxt_lvl,
        allow_live_verify=allow_live_verify,
        res_busy=res_busy,
        missing_prereqs=missing_prereqs,
    )
