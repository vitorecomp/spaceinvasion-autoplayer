"""Pure normalization and KPI extraction helpers for `PlannerRunsRepository`."""

from __future__ import annotations

from collections.abc import Mapping

from backend.db.models import ConfigOptimizerActive
from backend.db.repos._helpers import (
    clamp_int64,
    loads_json_dict,
    normalize_building_key,
    sanitize_text,
    to_float,
    to_int,
)

_VALID_SPECS: frozenset[str] = frozenset({"metal", "kryptonite", "spice", "shipyard", "spice_shipyard"})
_SPEC_ALIASES: dict[str, str] = {
    "metal": "metal",
    "metal_mine": "metal",
    "blast_furnace": "metal",
    "pig_iron_metal": "metal",
    "kryptonite": "kryptonite",
    "kryptonite_mine": "kryptonite",
    "krypt": "kryptonite",
    "spice": "spice",
    "spice_mine": "spice",
    "shipyard": "shipyard",
    "weapons_factory": "shipyard",
    "fleet": "shipyard",
    "spice_shipyard": "spice_shipyard",
    "spice+shipyard": "spice_shipyard",
    "spice-shipyard": "spice_shipyard",
    "shipyard_spice": "spice_shipyard",
}


def normalize_floors(raw: object) -> dict[str, dict[str, int]]:
    """Normalize per-planet reserved building level floors."""
    if not isinstance(raw, Mapping):
        return {}
    out: dict[str, dict[str, int]] = {}
    for pk_raw, b_map in raw.items():
        pk = sanitize_text(pk_raw, 64).strip()
        if not pk or not isinstance(b_map, Mapping):
            continue
        clean_b = {
            bk: clamp_int64(lvl)
            for bk_raw, lvl_raw in b_map.items()
            if (bk := normalize_building_key(str(bk_raw))) and (lvl := max(0, to_int(lvl_raw, 0))) > 0
        }
        if clean_b:
            out[pk] = clean_b
    return out


def normalize_specs(raw: object) -> dict[str, str]:
    """Normalize per-planet specialization locks."""
    if not isinstance(raw, Mapping):
        return {}
    out: dict[str, str] = {}
    for pk_raw, spec_raw in raw.items():
        pk = sanitize_text(pk_raw, 64).strip()
        norm = sanitize_text(spec_raw).strip().lower().replace(" ", "_") if spec_raw is not None else ""
        if pk and norm and norm != "auto":
            spec = _SPEC_ALIASES.get(norm, norm)
            if spec in _VALID_SPECS:
                out[pk] = spec
    return out


def merge_floors(
    active_row: ConfigOptimizerActive | None,
    explicit_floors: Mapping[str, Mapping[str, int]] | None,
    clean_params: Mapping[str, object],
) -> dict[str, dict[str, int]]:
    """Merge explicit, active, and parameter-supplied reserved building floors."""
    if explicit_floors is not None:
        return normalize_floors(explicit_floors)
    existing = normalize_floors(loads_json_dict(active_row.reserved_floors_json) if active_row is not None else {})
    from_params = normalize_floors(clean_params.get("reserved_floors_by_planet"))
    eff = {pk: dict(bmap) for pk, bmap in existing.items()}
    for pk, bmap in from_params.items():
        merged = eff.setdefault(pk, {})
        for bk, lvl in bmap.items():
            merged[bk] = max(lvl, merged.get(bk, 0))
    return eff


def extract_ai_kpis(
    result: Mapping[str, object],
    clean_params: Mapping[str, object],
    meta_obj: Mapping[str, object],
    summary_obj: Mapping[str, object],
) -> tuple[int, float, float, float, int]:
    """Extract `(reward_weeks, weekly_discount, discounted_reward, total_net, actions_count)`."""
    rw_raw = clean_params.get("reward_weeks", meta_obj.get("reward_weeks", summary_obj.get("reward_weeks", 26)))
    wd_raw = clean_params.get(
        "weekly_discount", meta_obj.get("weekly_discount", summary_obj.get("weekly_discount", 0.9))
    )
    disc_raw = summary_obj.get(
        "discounted_reward_spice_eq",
        summary_obj.get(
            "discounted_reward",
            summary_obj.get("net_discounted_reward", result.get("discounted_reward_spice_eq", 0.0)),
        ),
    )
    tnet_raw = summary_obj.get(
        "total_net_spice_eq",
        summary_obj.get(
            "total_net_production_spice_eq",
            summary_obj.get("total_net", result.get("total_net_spice_eq", 0.0)),
        ),
    )
    steps = result.get("steps")
    cnt = (
        len(steps)
        if isinstance(steps, list)
        else max(0, to_int(summary_obj.get("actions_count", summary_obj.get("total_steps", 0)), 0))
    )
    return max(1, to_int(rw_raw, 26)), to_float(wd_raw, 0.9), to_float(disc_raw, 0.0), to_float(tnet_raw, 0.0), cnt


def apply_floor_update(
    existing: dict[str, dict[str, int]],
    planet_key: str,
    building_key: str,
    min_level: int,
) -> dict[str, dict[str, int]]:
    """Mutate a copy of `existing` floors with a single planet/building floor update."""
    floors = {pk: dict(bmap) for pk, bmap in existing.items()}
    pk = sanitize_text(planet_key, 64).strip()
    bk = normalize_building_key(building_key)
    lvl = max(0, to_int(min_level, 0))
    if pk and bk:
        if lvl > 0:
            floors.setdefault(pk, {})[bk] = clamp_int64(lvl)
        elif pk in floors:
            floors[pk].pop(bk, None)
            if not floors[pk]:
                floors.pop(pk, None)
    return floors


def apply_spec_updates(
    existing: dict[str, str],
    updates: Mapping[str, object],
    *,
    replace: bool,
) -> dict[str, str]:
    """Merge or replace per-planet specialization locks, treating `auto`/empty as unlock."""
    if replace:
        return normalize_specs(updates)
    next_specs = dict(existing)
    for pk_raw, spec_raw in updates.items():
        pk = sanitize_text(pk_raw, 64).strip()
        if not pk:
            continue
        norm = sanitize_text(spec_raw).strip().lower().replace(" ", "_") if spec_raw is not None else ""
        if not norm or norm == "auto":
            next_specs.pop(pk, None)
        else:
            spec = _SPEC_ALIASES.get(norm, norm)
            if spec in _VALID_SPECS:
                next_specs[pk] = spec
    return next_specs


__all__ = [
    "apply_floor_update",
    "apply_spec_updates",
    "extract_ai_kpis",
    "merge_floors",
    "normalize_floors",
    "normalize_specs",
]
