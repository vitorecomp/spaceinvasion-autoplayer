"""Shared coercion, offer-map, queue-slot, and effective-level helpers for heuristic planning."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
import os
import re

from spaceinvasion.core import catalog
from spaceinvasion.core.config import StrategyConfig
from spaceinvasion.core.state import (
    BuildingOffer,
    GameState,
    PlanetState,
    ResearchOffer,
    Resources,
    UnitOffer,
    is_building_allowed_on_planet_type,
    normalize_planet_type,
)
from spaceinvasion.planner.heuristics._types import (
    _SLUG_TO_SHIP_KEY,
    RESEARCH_LAB,
    SHIP_SLUG_BY_KEY,
    PlannedAction,
)


def _safe_strategy_int(val: object, default: int = 0) -> int:
    if isinstance(val, (bytes, bytearray)):
        val = bytes(val).decode("utf-8", errors="replace")
    if isinstance(val, str):
        txt = val.strip()
        if not txt or not txt.isascii() or txt.lower() in ("null", "none", "~"):
            return default
    return catalog.safe_int(val, default=default)


def _safe_strategy_float(val: object, default: float = 0.0) -> float:
    if isinstance(val, (bytes, bytearray)):
        val = bytes(val).decode("utf-8", errors="replace")
    if isinstance(val, str):
        txt = val.strip()
        if not txt or not txt.isascii() or txt.lower() in ("null", "none", "~"):
            return default
    return catalog.safe_finite_float(val, default=default)


def _safe_strategy_str(val: object) -> str:
    if val is None:
        return ""
    if isinstance(val, os.PathLike):
        try:
            val = os.fspath(val)
        except (TypeError, ValueError, OSError):
            return catalog.safe_key(val)
        if val is None:
            return ""
    if isinstance(val, str):
        return val
    if isinstance(val, int) and not isinstance(val, bool):
        return catalog.fmt_compact_int(val) if abs(val).bit_length() >= 12_000 else str(val)
    if isinstance(val, (bytes, bytearray)):
        return bytes(val).decode("utf-8", errors="replace")
    return catalog.safe_key(val)


def _resources_from_dict(raw: object) -> Resources:
    if not isinstance(raw, Mapping):
        return Resources()
    return Resources(
        pig_iron=max(0, _safe_strategy_int(raw.get("pig_iron", 0), 0)),
        metal=max(0, _safe_strategy_int(raw.get("metal", 0), 0)),
        kryptonite=max(0, _safe_strategy_int(raw.get("kryptonite", 0), 0)),
        spice=max(0, _safe_strategy_int(raw.get("spice", 0), 0)),
    )


def _normalize_manual_coords(raw: object) -> str:
    txt = _safe_strategy_str(raw).strip()
    if not txt:
        return ""
    m3 = re.search(r"(\d{1,2}):(\d{1,3}):(\d{1,2})", txt, re.ASCII)
    if m3:
        g, s, p = int(m3.group(1)), int(m3.group(2)), int(m3.group(3))
        if 1 <= g <= 14 and 1 <= s <= 400 and 1 <= p <= 16:
            return f"{g:02d}:{s:03d}:{p:02d}"
    m2 = re.search(r"(\d{1,2}):(\d{1,3})", txt, re.ASCII)
    if m2:
        g, s = int(m2.group(1)), int(m2.group(2))
        if 1 <= g <= 14 and 1 <= s <= 400:
            return f"{g:02d}:{s:03d}"
    return txt[:32]


def _parse_gal_sys_pos(coords: object) -> tuple[int, int, int]:
    coords_s = catalog.safe_key(coords) if coords is not None else ""
    parts = [p.strip() for p in coords_s.split(":")]
    if len(parts) == 3 and all(p.isascii() and p.isdigit() for p in parts):
        try:
            return int(parts[0][:9]), int(parts[1][:9]), int(parts[2][:9])
        except ValueError:
            return 1, 1, 1
    return 1, 1, 1


def _resolve_storage_warn_ratio(cfg: StrategyConfig, override: float | None) -> float:
    cfg_val = _safe_strategy_float(getattr(cfg, "storage_warn_ratio", 0.85), 0.85)
    if cfg_val <= 0.0:
        cfg_val = 0.85
    cfg_val = max(0.05, min(1.0, cfg_val))
    if override is None:
        return cfg_val
    val = _safe_strategy_float(override, cfg_val)
    if val <= 0.0:
        val = cfg_val
    return max(0.05, min(1.0, val))


def _offer_map(planet: PlanetState) -> dict[int, BuildingOffer]:
    out: dict[int, BuildingOffer] = {}
    for offer in getattr(planet, "building_offers", ()) or ():
        bid = catalog.safe_optional_int(getattr(offer, "building_id", None), None)
        if bid is not None:
            out[bid] = offer
    return out


def _research_offer_map(state: GameState) -> dict[str, ResearchOffer]:
    out: dict[str, ResearchOffer] = {}
    for offer in getattr(state, "research_offers", ()) or ():
        rkey = catalog.safe_key(getattr(offer, "research_key", "") or "")
        if rkey:
            out[rkey] = offer
    return out


def _ship_offer_map(planet: PlanetState) -> dict[int, UnitOffer]:
    out: dict[int, UnitOffer] = {}
    for offer in getattr(planet, "ship_offers", ()) or ():
        uid = catalog.safe_optional_int(getattr(offer, "unit_id", None), None)
        if uid is not None:
            out[uid] = offer
    return out


def _apply_queue_cap_if_full(action: PlannedAction, queue_full: bool) -> PlannedAction:
    if not (queue_full and action.executable_now):
        return action
    return PlannedAction(
        priority=action.priority,
        kind="WAIT_RESOURCES",
        planet_id=action.planet_id,
        planet_coords=action.planet_coords,
        target_id=action.target_id,
        target_name=action.target_name,
        target_level=action.target_level,
        cost=action.cost,
        missing=action.missing,
        reason=f"{_safe_strategy_str(action.reason)} (build queue full)",
        executable_now=False,
    )


def _resolve_shipyard_targets(raw_targets: object) -> dict[str, int]:
    if not isinstance(raw_targets, Mapping):
        return {}
    resolved: dict[str, int] = {}
    for raw_k, raw_v in raw_targets.items():
        key_s = _safe_strategy_str(raw_k).strip()
        if not key_s or (key_s.isascii() and key_s.isdigit()):
            continue
        cnt = _safe_strategy_int(raw_v, 0)
        low_s = key_s.lower()
        canon_slug: str | None = None
        if low_s in SHIP_SLUG_BY_KEY:
            canon_slug = SHIP_SLUG_BY_KEY[low_s]
        else:
            unit_info = catalog.resolve_unit("ships", key_s)
            if unit_info:
                u_slug = unit_info.get("slug")
                if isinstance(u_slug, str) and u_slug in _SLUG_TO_SHIP_KEY:
                    canon_slug = u_slug
        if canon_slug is not None:
            resolved[canon_slug] = cnt
    return resolved


def _resolve_fleet_research_targets(raw_targets: object) -> dict[str, int]:
    """Resolve configured `fleet_research_targets` into canonical `r1`..`r15` positive level caps."""
    if not isinstance(raw_targets, Mapping):
        return {}
    resolved: dict[str, int] = {}
    for raw_k, raw_v in raw_targets.items():
        key_s = _safe_strategy_str(raw_k).strip()
        if not key_s or (key_s.isascii() and key_s.isdigit()):
            continue
        lvl = max(0, min(100, _safe_strategy_int(raw_v, 0)))
        low_s = key_s.lower()
        canon_rk: str | None = None
        if low_s in catalog.RESEARCH_BY_ID:
            canon_rk = low_s
        else:
            unit_info = catalog.resolve_unit("research", key_s)
            if unit_info:
                u_id = unit_info.get("id")
                if isinstance(u_id, str) and u_id in catalog.RESEARCH_BY_ID:
                    canon_rk = u_id
        if canon_rk is None:
            continue
        if lvl > 0:
            resolved[canon_rk] = lvl
        else:
            resolved.pop(canon_rk, None)
    return resolved


def _norm_planet_type(planet: PlanetState | None) -> str:
    return str(normalize_planet_type(planet))


def _is_building_allowed_on_planet(
    planet: PlanetState,
    building_id: int,
    offers: Mapping[int, BuildingOffer] | Sequence[BuildingOffer] | None = None,
) -> bool:
    if isinstance(offers, Mapping):
        has_b8 = RESEARCH_LAB in offers
    elif offers:
        has_b8 = any(catalog.safe_optional_int(getattr(o, "building_id", None), None) == RESEARCH_LAB for o in offers)
    else:
        has_b8 = False
    return bool(is_building_allowed_on_planet_type(planet, building_id, has_b8_offer=has_b8))


def _planet_has_free_building_fields(planet: PlanetState) -> bool:
    f_max = _safe_strategy_int(getattr(planet, "fields_max", 0), 0)
    if f_max <= 0:
        return True
    f_used = max(0, _safe_strategy_int(getattr(planet, "fields_used", 0), 0))
    return f_used < f_max


def _is_stale_remote_resource_wait(offer: BuildingOffer | None) -> bool:
    if offer is None or offer.can_build:
        return False
    st = _safe_strategy_str(getattr(offer, "status_text", "")).strip().lower()
    if not st:
        return True
    blocking_tokens = (
        "in progress",
        "construction",
        "queue",
        "full",
        "require",
        "prereq",
        "locked",
        "blocked",
        "field",
        "max",
        "unmet",
        "demolish",
        "research",
        "occupied",
        "already",
        "not allowed",
        "unavailable",
    )
    return not any(tok in st for tok in blocking_tokens)
