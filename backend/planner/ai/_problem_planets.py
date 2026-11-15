"""Planet-matching and planet-entry builders for ``build_path_problem``."""

from __future__ import annotations

from collections.abc import Mapping, Sequence

from backend.core.coercion import coerce_int, coerce_optional_int
from backend.core.coords import normalize_coords
from backend.planner.ai._types import PlanetLike, resources_tuple
from backend.planner.config_optimizer import normalize_planet_name


def _resolve_planet_display_name(
    planet: PlanetLike, slot_index: int, planet_names: Mapping[str, str] | None
) -> tuple[str, str]:
    raw_coords = str(planet.coords or "").strip()
    state_name = str(normalize_planet_name(planet.name))
    state_game_name = state_name or raw_coords
    override_name = ""
    if planet_names:
        pid = int(planet.planet_id)
        pid_str = str(pid) if pid > 0 else ""
        norm_c = normalize_coords(raw_coords) if raw_coords and not raw_coords.startswith("EMPTY:") else raw_coords
        slot_key = f"slot:{int(slot_index)}"
        if pid_str and pid_str in planet_names:
            override_name = str(normalize_planet_name(planet_names[pid_str]))
        if not override_name and raw_coords and raw_coords in planet_names:
            override_name = str(normalize_planet_name(planet_names[raw_coords]))
        if not override_name:
            slot_fallback = ""
            for lookup_key, raw_name in planet_names.items():
                lk = str(lookup_key).strip()
                if not lk:
                    continue
                if ":" in lk and not lk.startswith("EMPTY:") and not lk.lower().startswith("slot:"):
                    if norm_c and normalize_coords(lk) == norm_c and (cand := str(normalize_planet_name(raw_name))):
                        override_name = cand
                        break
                elif lk.lower() == slot_key and not slot_fallback and (cand := str(normalize_planet_name(raw_name))):
                    slot_fallback = cand
            if not override_name and slot_fallback:
                override_name = slot_fallback
    return (override_name or state_name or state_game_name or raw_coords), state_game_name


def _extract_target_buildings(entry: Mapping[str, object]) -> dict[str, int]:
    raw_buildings = entry.get("buildings")
    out: dict[str, int] = {}
    if isinstance(raw_buildings, Mapping):
        for k, v in raw_buildings.items():
            lvl = max(0, coerce_int(v, 0))
            if lvl > 0:
                out[str(k)] = lvl
    elif isinstance(raw_buildings, Sequence) and not isinstance(raw_buildings, (str, bytes)):
        for row in raw_buildings:
            if not isinstance(row, Mapping):
                continue
            k = str(row.get("key") or "")
            lvl = max(0, coerce_int(row.get("target_level"), 0))
            if k and lvl > 0:
                out[k] = lvl
    return out


def _extract_explicit_demolition_targets(entry: Mapping[str, object]) -> dict[str, int]:
    """Return ``{building_key: target_level}`` for rows explicitly planned below ``built_level``."""
    raw_buildings = entry.get("buildings")
    raw_floors = entry.get("built_floors")
    built_floors: Mapping[str, object] = raw_floors if isinstance(raw_floors, Mapping) else {}
    out: dict[str, int] = {}
    if isinstance(raw_buildings, Sequence) and not isinstance(raw_buildings, (str, bytes)):
        for row in raw_buildings:
            if not isinstance(row, Mapping):
                continue
            k = str(row.get("key") or "").strip()
            if not k:
                continue
            built = max(0, coerce_int(row.get("built_level", built_floors.get(k, 0)), 0))
            tgt = max(0, coerce_int(row.get("target_level"), built))
            if tgt < built:
                out[k] = tgt
    elif isinstance(raw_buildings, Mapping):
        for k, v in raw_buildings.items():
            bk = str(k).strip()
            built = max(0, coerce_int(built_floors.get(bk, 0), 0))
            if isinstance(v, Mapping):
                built = max(built, max(0, coerce_int(v.get("built_level"), 0)))
                tgt = max(0, coerce_int(v.get("target_level", v.get("level", built)), built))
            else:
                tgt = max(0, coerce_int(v, built))
            if tgt < built:
                out[bk] = tgt
    return out


def _match_live_planet(
    entry: Mapping[str, object], owned_normal: Sequence[PlanetLike], used_ids: set[int]
) -> PlanetLike | None:
    entry_pid = coerce_optional_int(entry.get("planet_id"), None)
    if entry_pid is not None and entry_pid > 0:
        for p in owned_normal:
            if int(p.planet_id) == entry_pid and int(p.planet_id) not in used_ids:
                return p
    entry_coords = str(entry.get("coords") or "").strip()
    if entry_coords and not entry_coords.startswith("EMPTY:"):
        norm_entry = normalize_coords(entry_coords)
        for p in owned_normal:
            if int(p.planet_id) in used_ids:
                continue
            p_coords = str(p.coords or "").strip()
            if p_coords == entry_coords or (p_coords and normalize_coords(p_coords) == norm_entry):
                return p
    slot = coerce_optional_int(entry.get("slot", entry.get("slot_index")), None)
    if slot is not None and 1 <= slot <= len(owned_normal):
        cand = owned_normal[slot - 1]
        if int(cand.planet_id) not in used_ids:
            return cand
    return None


def _planet_entry_from_live(
    live_planet: PlanetLike,
    raw_entry: Mapping[str, object] | None,
    index: int,
    *,
    allow_demolition: bool,
    merged_planet_names: Mapping[str, str],
) -> dict[str, object]:
    levels = {str(k): max(0, int(v or 0)) for k, v in live_planet.buildings.items() if max(0, int(v or 0)) > 0}
    transport_ships = {
        sk: max(0, int(live_planet.ships.get(sk, 0) or 0))
        for sk in ("s1", "s2", "s3")
        if max(0, int(live_planet.ships.get(sk, 0) or 0)) > 0
    }
    raw_target = _extract_target_buildings(raw_entry) if raw_entry is not None else {}
    demolish_floors = (
        _extract_explicit_demolition_targets(raw_entry) if (allow_demolition and raw_entry is not None) else {}
    )
    target_levels = dict(raw_target)
    for bk, blvl in levels.items():
        if bk in demolish_floors:
            df_val = min(blvl, int(demolish_floors[bk]))
            if df_val > 0:
                target_levels[bk] = df_val
            else:
                target_levels.pop(bk, None)
        elif blvl > target_levels.get(bk, 0):
            target_levels[bk] = blvl
    slot_idx = (
        coerce_int(raw_entry.get("slot", raw_entry.get("slot_index")), index + 1)
        if raw_entry is not None
        else (index + 1)
    )
    resolved_name, resolved_game_name = _resolve_planet_display_name(live_planet, slot_idx, merged_planet_names or None)
    b2_lvl, eco_rates = levels.get("b2", 0), live_planet.economy_rates
    b2_pct = max(0, min(100, coerce_int(eco_rates.get("resource2", 100), 100))) if b2_lvl > 0 else 0
    raw_prod = raw_entry.get("production") if raw_entry is not None else None
    return {
        "index": index,
        "slot_index": slot_idx,
        "planet_id": int(live_planet.planet_id),
        "coords": str(live_planet.coords),
        "name": str(resolved_name),
        "game_name": str(resolved_game_name),
        "planet_status": "colonized",
        "is_placeholder": False,
        "temperature_c": int(live_planet.temperature),
        "fields_max": max(int(live_planet.fields_max), sum(levels.values())),
        "founded": True,
        "is_central_planet": bool(raw_entry.get("is_central_planet", False)) if raw_entry is not None else False,
        "pays_for_research": bool(raw_entry.get("pays_for_research", False)) if raw_entry is not None else False,
        "levels": levels,
        "target_levels": target_levels,
        "demolish_floors": dict(demolish_floors),
        "solar_cells": max(0, int(live_planet.ships.get("s9", 0) or 0)),
        "resources": resources_tuple(live_planet.resources),
        "ships": transport_ships,
        "blast_furnace_pct": b2_pct,
        "blast_furnace_enabled": bool(b2_lvl > 0 and b2_pct > 0),
        "blast_furnace_rate_known": "resource2" in eco_rates,
        "saved_production": dict(raw_prod) if isinstance(raw_prod, Mapping) else {},
        "missing_ships": {},
    }


def _placeholder_planet_entry(raw_entry: Mapping[str, object], index: int) -> dict[str, object]:
    raw_prod = raw_entry.get("production")
    return {
        "index": index,
        "slot_index": coerce_int(raw_entry.get("slot", raw_entry.get("slot_index")), index + 1),
        "planet_id": coerce_int(raw_entry.get("planet_id"), -(index + 1)),
        "coords": str(raw_entry.get("coords") or f"EMPTY:{index + 1:02d}"),
        "name": str(raw_entry.get("name") or f"Empty Colony Slot {index + 1}"),
        "planet_status": "empty_colony_slot",
        "is_placeholder": True,
        "temperature_c": coerce_int(raw_entry.get("temperature", raw_entry.get("temperature_c")), 0),
        "fields_max": max(1, coerce_int(raw_entry.get("fields_max"), 300)),
        "founded": False,
        "is_central_planet": bool(raw_entry.get("is_central_planet", False)),
        "pays_for_research": bool(raw_entry.get("pays_for_research", False)),
        "levels": {},
        "target_levels": dict(_extract_target_buildings(raw_entry)),
        "solar_cells": 0,
        "resources": (0.0, 0.0, 0.0, 0.0),
        "ships": {},
        "blast_furnace_pct": 0,
        "blast_furnace_enabled": False,
        "blast_furnace_rate_known": False,
        "saved_production": dict(raw_prod) if isinstance(raw_prod, Mapping) else {},
        "missing_ships": {},
    }


def resolve_problem_planets(
    raw_planets: Sequence[object],
    owned_normal: Sequence[PlanetLike],
    *,
    allow_demolition: bool,
    pad_generic_colonies: bool,
    merged_planet_names: Mapping[str, str],
) -> tuple[list[dict[str, object]], set[int]]:
    """Bind live normal colonies to ``target_config`` planet rows and optional empty placeholders."""
    problem_planets: list[dict[str, object]] = []
    used_ids: set[int] = set()
    deferred: list[Mapping[str, object]] = []

    for raw_entry in raw_planets:
        if not isinstance(raw_entry, Mapping):
            continue
        is_ph = bool(
            raw_entry.get("is_placeholder", False)
            or str(raw_entry.get("planet_status") or "") == "empty_colony_slot"
            or coerce_int(raw_entry.get("planet_id"), 0) < 0
        )
        if is_ph:
            deferred.append(raw_entry)
            continue
        live_p = _match_live_planet(raw_entry, owned_normal, used_ids)
        if live_p is not None:
            used_ids.add(int(live_p.planet_id))
            problem_planets.append(
                _planet_entry_from_live(
                    live_p,
                    raw_entry,
                    len(problem_planets),
                    allow_demolition=allow_demolition,
                    merged_planet_names=merged_planet_names,
                )
            )
        elif pad_generic_colonies or not owned_normal:
            problem_planets.append(_placeholder_planet_entry(raw_entry, len(problem_planets)))

    cursor = 0
    for live_p in owned_normal:
        if int(live_p.planet_id) in used_ids:
            continue
        tmpl = deferred[cursor] if cursor < len(deferred) else None
        if tmpl is not None:
            cursor += 1
        used_ids.add(int(live_p.planet_id))
        problem_planets.append(
            _planet_entry_from_live(
                live_p,
                tmpl,
                len(problem_planets),
                allow_demolition=allow_demolition,
                merged_planet_names=merged_planet_names,
            )
        )
    if pad_generic_colonies or not problem_planets:
        for raw_entry in deferred[cursor:]:
            if not pad_generic_colonies and owned_normal:
                continue
            problem_planets.append(_placeholder_planet_entry(raw_entry, len(problem_planets)))
    if not problem_planets:
        raise ValueError("No matching planets between GameState and target_config")
    return problem_planets, used_ids
