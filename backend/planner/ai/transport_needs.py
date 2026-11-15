"""Distance- and dispatch-delay-aware live transport needs for AI-Planner steps."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
import math

from backend.core import catalog as game_data
from backend.core.coercion import coerce_finite_float, coerce_int
from backend.planner.ai._reconcile_helpers import (
    PlannedActionLike,
    ReadConfigLike,
    index_planets,
    match_step_planet,
    resource_value,
    resources_float_list,
)
from backend.planner.ai._transport_helpers import (
    best_donor_travel_sec,
    extract_in_progress_maps,
    make_base_hours_fn,
    merge_need_entry,
    planet_hourly_rates,
    process_rule_a_actions,
    select_lab_planet_index,
    storage_cap_ratio_for,
)
from backend.planner.ai._types import GameStateLike, PlanetLike, safe_speedup
from backend.planner.ai.telemetry import fmt_path_time
from backend.planner.config_optimizer import RESOURCE_KEYS, full_cost, research_catalog


def _projected_gap(
    planet: PlanetLike,
    cost_vals: Sequence[float],
    spent: Sequence[float],
    inb: Sequence[float],
    rates_p: Sequence[float],
    rem_hr: float,
) -> list[int]:
    local_vals = resources_float_list(planet.resources)
    gap: list[int] = []
    for pos in range(4):
        local_after = max(0.0, local_vals[pos] - spent[pos])
        proj = local_after + inb[pos] + rates_p[pos] * rem_hr
        gap.append(max(0, int(math.ceil(cost_vals[pos] - proj))))
    return gap


def _apply_early_build_step(
    step: Mapping[str, object],
    *,
    entity_key: str,
    target_level: int,
    seq: int,
    planets_by_id: Mapping[int, PlanetLike],
    planets_by_coords: Mapping[str, PlanetLike],
    normal_planets: Sequence[PlanetLike],
    empire_ships: Mapping[str, int],
    researches: Mapping[str, int],
    research_now: Mapping[str, int],
    eff_buildings_by_pid: Mapping[int, Mapping[str, int]],
    eff_researches: Mapping[str, int],
    queue_rem_by_pid: Mapping[int, float],
    exec_dur_by_pid: Mapping[int, float],
    exec_cost_by_pid: Mapping[int, Sequence[float]],
    inbound_cargo_by_pid: Mapping[int, Sequence[float]],
    early_checked_planets: set[int],
    needs: dict[int, dict[str, object]],
    dispatch_delay_sec: float,
    speedup: float,
) -> None:
    planet = match_step_planet(step, planets_by_id, planets_by_coords)
    if planet is None:
        return
    pid = int(planet.planet_id)
    if pid in early_checked_planets or not (entity_key.startswith("b") and entity_key[1:].isdigit()):
        return
    bid = int(entity_key[1:])
    b_info = game_data.BUILDING_BY_ID.get(bid)
    if not b_info:
        return
    eff_b = eff_buildings_by_pid.get(pid, {})
    cur_eff = eff_b.get(entity_key, 0)
    if cur_eff >= target_level or target_level != cur_eff + 1:
        return
    if game_data.missing_prerequisites("buildings", bid, buildings=eff_b, researches=eff_researches):
        return
    if planet.fields_max > 0 and planet.fields_used >= planet.fields_max:
        early_checked_planets.add(pid)
        return

    rem_s = max(queue_rem_by_pid.get(pid, 0.0), exec_dur_by_pid.get(pid, 0.0))
    if rem_s <= 0.0:
        return
    lead_s = dispatch_delay_sec + best_donor_travel_sec(planet, normal_planets, empire_ships, researches)
    early_checked_planets.add(pid)
    if rem_s > lead_s + 1e-6:
        return

    cost_dict = full_cost(game_data.building_cost(bid, target_level))
    cost_vals = [float(cost_dict[r]) for r in RESOURCE_KEYS]
    if bid not in (11, 12, 13, 14) and any(
        (cap := resource_value(planet.storage, r)) > 0 and cost_vals[pos] > cap for pos, r in enumerate(RESOURCE_KEYS)
    ):
        return

    rem_hr = rem_s / 3600.0
    gap = _projected_gap(
        planet,
        cost_vals,
        exec_cost_by_pid.get(pid, (0.0, 0.0, 0.0, 0.0)),
        inbound_cargo_by_pid.get(pid, (0.0, 0.0, 0.0, 0.0)),
        planet_hourly_rates(planet, speedup=speedup, research_now=research_now),
        rem_hr,
    )
    if sum(gap) <= 0:
        return
    b_name = str(step.get("entity_name") or b_info["name"])
    needs[pid] = {
        "pig_iron": gap[0],
        "metal": gap[1],
        "kryptonite": gap[2],
        "spice": gap[3],
        "label": (
            f"early pre-ship #{seq}: {b_name} L{target_level} "
            f"(queue free in {fmt_path_time(rem_hr)}, lead {fmt_path_time(lead_s / 3600.0)})"
        ),
        "need_by_sec": round(rem_s, 1),
        "lead_sec": round(lead_s, 1),
        "storage_cap_ratio": storage_cap_ratio_for(planet, cost_vals),
    }


def _apply_early_research_step(
    step: Mapping[str, object],
    *,
    entity_key: str,
    target_level: int,
    seq: int,
    lab_planet: PlanetLike,
    normal_planets: Sequence[PlanetLike],
    empire_ships: Mapping[str, int],
    researches: Mapping[str, int],
    research_now: Mapping[str, int],
    eff_buildings_by_pid: Mapping[int, Mapping[str, int]],
    eff_researches: Mapping[str, int],
    research_rem_sec: float,
    exec_res_dur: float,
    exec_cost_by_pid: Mapping[int, Sequence[float]],
    inbound_cargo_by_pid: Mapping[int, Sequence[float]],
    needs: dict[int, dict[str, object]],
    dispatch_delay_sec: float,
    speedup: float,
) -> bool:
    """Return True when the global early-research slot has been evaluated (consuming the single check)."""
    r_info = game_data.RESEARCH_BY_ID.get(entity_key)
    if not r_info:
        return False
    cur_eff = eff_researches.get(entity_key, 0)
    if cur_eff >= target_level or target_level != cur_eff + 1:
        return False
    lab_pid = int(lab_planet.planet_id)
    if game_data.missing_prerequisites(
        "research", entity_key, buildings=eff_buildings_by_pid.get(lab_pid, {}), researches=eff_researches
    ):
        return False

    rem_s = max(research_rem_sec, exec_res_dur)
    if rem_s <= 0.0:
        return False
    lead_s = dispatch_delay_sec + best_donor_travel_sec(lab_planet, normal_planets, empire_ships, researches)
    if rem_s > lead_s + 1e-6:
        return True

    levels_raw = r_info.get("levels")
    levels_map: Mapping[object, object] = levels_raw if isinstance(levels_raw, Mapping) else {}
    lvl_row = levels_map.get(target_level)
    raw_c = lvl_row.get("cost") if isinstance(lvl_row, Mapping) else None
    cost_dict = full_cost(raw_c or {})
    cost_vals = [float(cost_dict[r]) for r in RESOURCE_KEYS]
    rem_hr = rem_s / 3600.0
    gap = _projected_gap(
        lab_planet,
        cost_vals,
        exec_cost_by_pid.get(lab_pid, (0.0, 0.0, 0.0, 0.0)),
        inbound_cargo_by_pid.get(lab_pid, (0.0, 0.0, 0.0, 0.0)),
        planet_hourly_rates(lab_planet, speedup=speedup, research_now=research_now),
        rem_hr,
    )
    if sum(gap) <= 0:
        return True
    r_name = str(step.get("entity_name") or r_info["name"])
    merge_need_entry(
        needs,
        lab_pid,
        gap=gap,
        label=(
            f"early pre-ship #{seq}: {r_name} L{target_level} "
            f"(lab free in {fmt_path_time(rem_hr)}, lead {fmt_path_time(lead_s / 3600.0)})"
        ),
        need_by_sec=rem_s,
        lead_sec=lead_s,
        cap_ratio=storage_cap_ratio_for(lab_planet, cost_vals),
    )
    return True


def _apply_early_preship_steps(
    steps: Sequence[object],
    *,
    planets_by_id: Mapping[int, PlanetLike],
    planets_by_coords: Mapping[str, PlanetLike],
    normal_planets: Sequence[PlanetLike],
    lab_planet: PlanetLike | None,
    empire_ships: Mapping[str, int],
    researches: Mapping[str, int],
    research_now: Mapping[str, int],
    eff_buildings_by_pid: Mapping[int, Mapping[str, int]],
    eff_researches: Mapping[str, int],
    queue_rem_by_pid: Mapping[int, float],
    exec_dur_by_pid: Mapping[int, float],
    research_rem_sec: float,
    exec_res_dur: float,
    exec_cost_by_pid: Mapping[int, Sequence[float]],
    inbound_cargo_by_pid: Mapping[int, Sequence[float]],
    needs: dict[int, dict[str, object]],
    dispatch_delay_sec: float,
    speedup: float,
) -> None:
    early_checked_planets: set[int] = set(needs.keys())
    early_checked_research = False
    for step in steps:
        if not isinstance(step, Mapping):
            continue
        kind = str(step.get("kind") or "").strip().upper()
        entity_key = str(step.get("entity_key") or "").strip()
        target_level = coerce_int(step.get("target_level"), 0)
        seq = coerce_int(step.get("seq"), 0)
        if not entity_key or target_level <= 0:
            continue
        if kind == "BUILD":
            _apply_early_build_step(
                step,
                entity_key=entity_key,
                target_level=target_level,
                seq=seq,
                planets_by_id=planets_by_id,
                planets_by_coords=planets_by_coords,
                normal_planets=normal_planets,
                empire_ships=empire_ships,
                researches=researches,
                research_now=research_now,
                eff_buildings_by_pid=eff_buildings_by_pid,
                eff_researches=eff_researches,
                queue_rem_by_pid=queue_rem_by_pid,
                exec_dur_by_pid=exec_dur_by_pid,
                exec_cost_by_pid=exec_cost_by_pid,
                inbound_cargo_by_pid=inbound_cargo_by_pid,
                early_checked_planets=early_checked_planets,
                needs=needs,
                dispatch_delay_sec=dispatch_delay_sec,
                speedup=speedup,
            )
        elif kind == "RESEARCH" and not early_checked_research and lab_planet is not None:
            early_checked_research = _apply_early_research_step(
                step,
                entity_key=entity_key,
                target_level=target_level,
                seq=seq,
                lab_planet=lab_planet,
                normal_planets=normal_planets,
                empire_ships=empire_ships,
                researches=researches,
                research_now=research_now,
                eff_buildings_by_pid=eff_buildings_by_pid,
                eff_researches=eff_researches,
                research_rem_sec=research_rem_sec,
                exec_res_dur=exec_res_dur,
                exec_cost_by_pid=exec_cost_by_pid,
                inbound_cargo_by_pid=inbound_cargo_by_pid,
                needs=needs,
                dispatch_delay_sec=dispatch_delay_sec,
                speedup=speedup,
            )


def compute_early_transport_needs(
    state: GameStateLike,
    plan_result: Mapping[str, object],
    ai_actions: Sequence[PlannedActionLike] = (),
    *,
    config: ReadConfigLike | None = None,
) -> dict[int, dict[str, object]]:
    """Compute distance- & dispatch-delay-aware live transport needs for AI-Planner steps."""
    del config
    if not isinstance(plan_result, Mapping):
        return {}
    steps = plan_result.get("steps")
    if not isinstance(steps, Sequence) or isinstance(steps, (str, bytes)) or not steps:
        return {}
    planets_by_id, planets_by_coords, normal_planets = index_planets(state.planets or (), normal_only=True)
    if len(normal_planets) < 2:
        return {}

    raw_meta = plan_result.get("meta_parameters")
    meta: Mapping[str, object] = raw_meta if isinstance(raw_meta, Mapping) else {}
    dispatch_delay_sec = (
        min(1440.0, max(0.0, coerce_finite_float(meta.get("transport_dispatch_delay_minutes"), 120.0))) * 60.0
    )
    empire_ships: dict[str, int] = {"s1": 0, "s2": 0, "s3": 0}
    for planet in normal_planets:
        for sk in ("s1", "s2", "s3"):
            empire_ships[sk] += max(0, int((planet.ships or {}).get(sk, 0) or 0))

    speedup = safe_speedup(state, {})
    research_now = {
        str(key): max(0, int((state.researches or {}).get(str(key), 0) or 0)) for key, _ in research_catalog()
    }
    lab_idx = select_lab_planet_index(state, normal_planets)
    base_hours_fn = make_base_hours_fn()

    inbound_by_pid, queue_rem_by_pid, eff_buildings_by_pid, research_rem_sec, eff_researches = extract_in_progress_maps(
        state,
        normal_planets,
        speedup=speedup,
        research_now=research_now,
        lab_idx=lab_idx,
        base_hours_fn=base_hours_fn,
    )
    needs, exec_cost_by_pid, exec_dur_by_pid, exec_res_dur = process_rule_a_actions(
        ai_actions,
        planets_by_id=planets_by_id,
        normal_planets=normal_planets,
        empire_ships=empire_ships,
        researches=state.researches or {},
        inbound_cargo_by_pid=inbound_by_pid,
        eff_buildings_by_pid=eff_buildings_by_pid,
        eff_researches=eff_researches,
        dispatch_delay_sec=dispatch_delay_sec,
        speedup=speedup,
        base_hours_fn=base_hours_fn,
    )
    lab_planet = normal_planets[lab_idx] if 0 <= lab_idx < len(normal_planets) else None
    _apply_early_preship_steps(
        steps,
        planets_by_id=planets_by_id,
        planets_by_coords=planets_by_coords,
        normal_planets=normal_planets,
        lab_planet=lab_planet,
        empire_ships=empire_ships,
        researches=state.researches or {},
        research_now=research_now,
        eff_buildings_by_pid=eff_buildings_by_pid,
        eff_researches=eff_researches,
        queue_rem_by_pid=queue_rem_by_pid,
        exec_dur_by_pid=exec_dur_by_pid,
        research_rem_sec=research_rem_sec,
        exec_res_dur=exec_res_dur,
        exec_cost_by_pid=exec_cost_by_pid,
        inbound_cargo_by_pid=inbound_by_pid,
        needs=needs,
        dispatch_delay_sec=dispatch_delay_sec,
        speedup=speedup,
    )
    return needs


__all__ = ["compute_early_transport_needs"]
