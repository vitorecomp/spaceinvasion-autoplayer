"""Colony builder/idle state diagnostics and Step-5 gate metrics for the AI-Planner."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
import math

from spaceinvasion.core import catalog as game_data
from spaceinvasion.core.coercion import coerce_int
from spaceinvasion.core.config import StrategyConfig
from spaceinvasion.core.model import Resources, is_building_allowed_on_planet_type
from spaceinvasion.planner.ai._problem_in_progress import compute_in_progress_build_rem_by_planet
from spaceinvasion.planner.ai._problem_queues import compute_inbound_friendly_cargo_by_planet
from spaceinvasion.planner.ai._reconcile_helpers import (
    PlannedActionLike,
    ReadConfigLike,
    ReadResourcesLike,
    _resolve_in_flight_buildings,
    energy_net_value,
    index_planets,
    match_step_planet,
    offers_by_building_id,
    resource_value,
    resources_can_afford,
    resources_is_zero,
)
from spaceinvasion.planner.ai._types import RESOURCE_DISPLAY_LABELS, GameStateLike, PlanetLike, coerce_resources_obj
from spaceinvasion.planner.ai.live_reconcile import reconcile_live_ai_actions
from spaceinvasion.planner.ai.telemetry import fmt_path_time
from spaceinvasion.planner.config_optimizer import RESOURCE_KEYS, full_cost

AI_COLONY_IDLE_STATES: tuple[str, ...] = (
    "building",
    "ready_not_started",
    "field_conflict",
    "storage_blocked",
    "transport_inbound",
    "waiting_metal",
    "waiting_pig_iron",
    "waiting_kryptonite",
    "waiting_spice",
    "idle_complete",
)

_RESOURCE_TIE_PRIORITY: dict[str, int] = {
    "metal": 4,
    "kryptonite": 3,
    "spice": 2,
    "pig_iron": 1,
}


def _empty_diagnostics(counts: dict[str, int]) -> dict[str, object]:
    return {
        "active_builders": 0,
        "total_colonies": 0,
        "colonies": [],
        "counts": counts,
        "idle_with_affordable_count": 0,
        "ready_not_started_count": 0,
    }


def _scan_colony_steps(
    state: GameStateLike,
    planet: PlanetLike,
    planet_steps: Sequence[Mapping[str, object]],
    in_flight_bmap: Mapping[str, int],
) -> tuple[tuple[str, str, int, int] | None, str | None, str | None, bool]:
    """Scan planned BUILD steps for blockers (storage, field, prereq) and locally affordable steps."""
    offers_by_id = offers_by_building_id(planet)
    fields_full = bool(planet.fields_max > 0 and planet.fields_used >= planet.fields_max)
    first_storage_blocked: tuple[str, str, int, int] | None = None
    first_field_blocked: str | None = None
    first_prereq_blocked: str | None = None
    has_affordable_target_step = False

    for step in planet_steps:
        entity_key = str(step.get("entity_key") or "").strip()
        target_level = coerce_int(step.get("target_level"), 0)
        if not (entity_key.startswith("b") and entity_key[1:].isdigit()) or target_level <= 0:
            continue
        bid = int(entity_key[1:])
        b_info = game_data.BUILDING_BY_ID.get(bid)
        if not b_info:
            continue
        live_level = planet.building_level(bid)
        if max(live_level, in_flight_bmap.get(entity_key, 0)) >= target_level or target_level != live_level + 1:
            continue
        b_name = str(step.get("entity_name") or b_info["name"])
        step_lbl = f"{b_name} L{target_level}"
        if not is_building_allowed_on_planet_type(planet, bid, has_b8_offer=(8 in offers_by_id)):
            continue
        if game_data.missing_prerequisites("buildings", bid, buildings=planet.buildings, researches=state.researches):
            if first_prereq_blocked is None:
                first_prereq_blocked = step_lbl
            continue
        if fields_full:
            if first_field_blocked is None:
                first_field_blocked = step_lbl
            continue

        wiki_cost = Resources(**full_cost(game_data.building_cost(bid, target_level)))
        storage_blocker: tuple[str, int, int] | None = None
        if bid not in (11, 12, 13, 14):
            for res_key in RESOURCE_KEYS:
                cap = resource_value(planet.storage, res_key)
                need_r = resource_value(wiki_cost, res_key)
                if cap > 0 and need_r > cap:
                    storage_blocker = (RESOURCE_DISPLAY_LABELS[res_key], need_r, cap)
                    break
        if storage_blocker is not None:
            if first_storage_blocked is None:
                first_storage_blocked = (step_lbl, storage_blocker[0], storage_blocker[1], storage_blocker[2])
            continue

        if (not resources_is_zero(wiki_cost)) and resources_can_afford(planet.resources, wiki_cost):
            has_affordable_target_step = True
    return first_storage_blocked, first_field_blocked, first_prereq_blocked, has_affordable_target_step


def _classify_waiting_resources(b_act: PlannedActionLike, inbound_res: ReadResourcesLike) -> tuple[str, str, str]:
    next_label = f"{b_act.target_name} L{b_act.target_level}" if b_act.target_level else str(b_act.target_name)
    rem_missing = {r: max(0, resource_value(b_act.missing, r) - resource_value(inbound_res, r)) for r in RESOURCE_KEYS}
    if sum(rem_missing.values()) == 0:
        return "transport_inbound", f"Inbound friendly cargo covers {next_label}", next_label
    top_res = max(
        (r for r, amt in rem_missing.items() if amt > 0),
        key=lambda r: (
            rem_missing[r] / max(1e-9, float(game_data.MERCHANT_RATES_IN_SPICE[r])),
            _RESOURCE_TIE_PRIORITY[r],
        ),
    )
    parts: list[str] = []
    for res_key in ("metal", "pig_iron", "kryptonite", "spice"):
        rem_amt = rem_missing[res_key]
        if rem_amt > 0:
            inb_amt = resource_value(inbound_res, res_key)
            label = RESOURCE_DISPLAY_LABELS[res_key]
            parts.append(f"{rem_amt:,} {label} ({inb_amt:,} inbound)" if inb_amt > 0 else f"{rem_amt:,} {label}")
    return f"waiting_{top_res}", f"Waiting for {', '.join(parts)} for {next_label}", next_label


def _classify_colony_state(
    planet: PlanetLike,
    *,
    active_build_count: int,
    in_flight_bmap: Mapping[str, int],
    rem_s: float,
    b_act: PlannedActionLike | None,
    conflict: Mapping[str, object] | None,
    inbound_res: ReadResourcesLike,
    first_storage_blocked: tuple[str, str, int, int] | None,
    first_field_blocked: str | None,
    first_prereq_blocked: str | None,
) -> tuple[str, str, str | None]:
    """Classify one normal colony into ``(state_bucket, human_reason, next_label)``."""
    if active_build_count > 0:
        next_label: str | None = None
        if in_flight_bmap:
            first_bkey, first_lvl = next(iter(in_flight_bmap.items()))
            if first_bkey.startswith("b") and first_bkey[1:].isdigit():
                binfo = game_data.BUILDING_BY_ID.get(int(first_bkey[1:]))
                bname = str(binfo["name"]) if binfo else first_bkey
            else:
                bname = first_bkey
            next_label = f"{bname} L{int(first_lvl)}"
        elif b_act is not None:
            next_label = f"{b_act.target_name} L{b_act.target_level}" if b_act.target_level else str(b_act.target_name)
        if next_label and rem_s > 0.0:
            reason = f"Building {next_label} ({fmt_path_time(rem_s / 3600.0)} left)"
        elif next_label:
            reason = f"Building {next_label}"
        else:
            reason = "Build queue active"
        return "building", reason, next_label

    if b_act is not None and b_act.executable_now:
        if b_act.kind == "DEMOLISH_BUILDING":
            next_label = (
                f"Demolish {b_act.target_name} -> L{b_act.target_level}"
                if b_act.target_level is not None
                else f"Demolish {b_act.target_name}"
            )
        else:
            next_label = f"{b_act.target_name} L{b_act.target_level}" if b_act.target_level else str(b_act.target_name)
        return "ready_not_started", str(b_act.reason or f"Ready to start {next_label}"), next_label

    if conflict is not None or first_field_blocked is not None:
        if conflict is not None:
            blk_name = str(conflict.get("blocked_building_name") or conflict.get("blocked_building_key") or "building")
            blk_lvl = coerce_int(conflict.get("blocked_target_level"), 0)
            next_label = f"{blk_name} L{blk_lvl}" if blk_lvl > 0 else blk_name
        else:
            next_label = first_field_blocked
        reason = (
            f"Fields full ({planet.fields_used}/{planet.fields_max}) blocking {next_label}"
            if next_label
            else f"Fields full ({planet.fields_used}/{planet.fields_max})"
        )
        return "field_conflict", reason, next_label

    if b_act is not None and not resources_is_zero(b_act.missing):
        return _classify_waiting_resources(b_act, inbound_res)

    if first_storage_blocked is not None:
        next_label = first_storage_blocked[0]
        reason = (
            f"{next_label} exceeds {first_storage_blocked[1]} storage "
            f"({first_storage_blocked[2]:,} > {first_storage_blocked[3]:,})"
        )
        return "storage_blocked", reason, next_label

    if b_act is not None:
        next_label = f"{b_act.target_name} L{b_act.target_level}" if b_act.target_level else str(b_act.target_name)
        return "idle_complete", str(b_act.reason or f"Waiting on {next_label}"), next_label
    if first_prereq_blocked is not None:
        return "idle_complete", f"Waiting on prerequisites for {first_prereq_blocked}", first_prereq_blocked
    return "idle_complete", "All planned steps complete", None


def _group_build_steps_by_planet(
    steps_seq: Sequence[object],
    planets_by_id: Mapping[int, PlanetLike],
    planets_by_coords: Mapping[str, PlanetLike],
) -> dict[int, list[Mapping[str, object]]]:
    steps_by_pid: dict[int, list[Mapping[str, object]]] = {}
    for step in steps_seq:
        if not isinstance(step, Mapping) or str(step.get("kind") or "").strip().upper() != "BUILD":
            continue
        p_match = match_step_planet(step, planets_by_id, planets_by_coords)
        if p_match is not None:
            steps_by_pid.setdefault(int(p_match.planet_id), []).append(step)
    return steps_by_pid


def _build_colony_diagnostic_row(
    state: GameStateLike,
    planet: PlanetLike,
    *,
    planet_steps: Sequence[Mapping[str, object]],
    in_flight_bmap: Mapping[str, int],
    active_build_count: int,
    rem_s: float,
    b_act: PlannedActionLike | None,
    conflict: Mapping[str, object] | None,
    inbound_res: ReadResourcesLike,
) -> tuple[str, dict[str, object]]:
    pid = int(planet.planet_id)
    coords_s = str(planet.coords or "")
    b2_level = max(0, planet.building_level(2))
    eco_rates = planet.economy_rates or {}
    b2_pct = max(0, min(100, coerce_int(eco_rates.get("resource2", 100), 100))) if b2_level > 0 else 0
    energy_net = energy_net_value(planet.energy)
    b2_full_e = game_data.mine_energy_needed(2, b2_level) if b2_level > 0 else 0
    b2_extra_e = int(math.ceil(b2_full_e * max(0, 100 - b2_pct) / 100.0)) if b2_level > 0 else 0

    first_storage, first_field, first_prereq, has_affordable = _scan_colony_steps(
        state, planet, planet_steps, in_flight_bmap
    )
    col_state, reason, next_label = _classify_colony_state(
        planet,
        active_build_count=active_build_count,
        in_flight_bmap=in_flight_bmap,
        rem_s=rem_s,
        b_act=b_act,
        conflict=conflict,
        inbound_res=inbound_res,
        first_storage_blocked=first_storage,
        first_field_blocked=first_field,
        first_prereq_blocked=first_prereq,
    )
    idle_with_affordable = bool(has_affordable and col_state not in ("building", "ready_not_started"))
    return col_state, {
        "planet_id": pid,
        "coords": coords_s,
        "name": str(planet.name or coords_s),
        "state": col_state,
        "reason": reason,
        "next_label": next_label,
        "idle_with_affordable_target_step": idle_with_affordable,
        "b2_level": b2_level,
        "b2_pct": b2_pct,
        "energy_net": energy_net,
        "b2_energy_margin_at_100": int(energy_net - b2_extra_e),
    }


def diagnose_ai_colony_idle_states(
    state: GameStateLike | None,
    plan_result: Mapping[str, object],
    ai_actions: Sequence[PlannedActionLike] = (),
    config: ReadConfigLike | None = None,
    *,
    field_conflicts: Sequence[Mapping[str, object]] = (),
    inbound_cargo_by_planet: Mapping[int, ReadResourcesLike] | None = None,
) -> dict[str, object]:
    """Classify every normal colony's live AI-Planner builder/idle state and Step-5 gate metrics."""
    counts: dict[str, int] = {st: 0 for st in AI_COLONY_IDLE_STATES}
    if state is None:
        return _empty_diagnostics(counts)
    planets_by_id, planets_by_coords, normal_planets = index_planets(state.planets or (), normal_only=True)
    if not normal_planets:
        return _empty_diagnostics(counts)

    raw_steps = plan_result.get("steps") if isinstance(plan_result, Mapping) else None
    steps_seq: Sequence[object] = (
        raw_steps if isinstance(raw_steps, Sequence) and not isinstance(raw_steps, (str, bytes)) else ()
    )
    eff_actions: Sequence[PlannedActionLike] = ai_actions
    eff_conflicts: Sequence[Mapping[str, object]] = field_conflicts
    if not eff_actions and not eff_conflicts and steps_seq:
        eff_cfg: ReadConfigLike = config if config is not None else StrategyConfig()
        eff_actions, eff_conflicts = reconcile_live_ai_actions(state, plan_result, eff_cfg)

    inbound_map: dict[int, Resources] = (
        compute_inbound_friendly_cargo_by_planet(state)
        if inbound_cargo_by_planet is None
        else {coerce_int(pid, 0): coerce_resources_obj(val) for pid, val in inbound_cargo_by_planet.items()}
    )
    in_flight_buildings, planet_queue_counts = _resolve_in_flight_buildings(state)
    in_prog_rem_by_pid = compute_in_progress_build_rem_by_planet(state)

    building_action_by_pid: dict[int, PlannedActionLike] = {}
    for act in eff_actions or ():
        if act.kind in ("BUILD", "DEMOLISH_BUILDING", "WAIT_RESOURCES"):
            building_action_by_pid.setdefault(int(act.planet_id), act)
    conflicts_by_pid: dict[int, Mapping[str, object]] = {}
    for conflict in eff_conflicts or ():
        if isinstance(conflict, Mapping):
            cpid = coerce_int(conflict.get("planet_id"), 0)
            if cpid > 0 and cpid not in conflicts_by_pid:
                conflicts_by_pid[cpid] = conflict
    steps_by_pid = _group_build_steps_by_planet(steps_seq, planets_by_id, planets_by_coords)

    colonies_out: list[dict[str, object]] = []
    for planet in normal_planets:
        pid = int(planet.planet_id)
        col_state, row = _build_colony_diagnostic_row(
            state,
            planet,
            planet_steps=steps_by_pid.get(pid, ()),
            in_flight_bmap=in_flight_buildings.get(pid, {}),
            active_build_count=planet_queue_counts.get(pid, 0),
            rem_s=in_prog_rem_by_pid.get(pid, 0.0),
            b_act=building_action_by_pid.get(pid),
            conflict=conflicts_by_pid.get(pid),
            inbound_res=inbound_map.get(pid, Resources()),
        )
        counts[col_state] += 1
        colonies_out.append(row)

    return {
        "active_builders": int(counts["building"]),
        "total_colonies": int(len(colonies_out)),
        "colonies": colonies_out,
        "counts": counts,
        "idle_with_affordable_count": int(sum(1 for row in colonies_out if row["idle_with_affordable_target_step"])),
        "ready_not_started_count": int(counts["ready_not_started"]),
    }


__all__ = ["AI_COLONY_IDLE_STATES", "diagnose_ai_colony_idle_states"]
