"""Catalog index maps, unit key normalization, resolution, and tech-tree prerequisites."""

from __future__ import annotations

from collections.abc import Mapping
import os
from typing import Any

from spaceinvasion.core.catalog.coercion import safe_int
from spaceinvasion.core.catalog.data_buildings import BUILDINGS
from spaceinvasion.core.catalog.data_defenses import DEFENSES
from spaceinvasion.core.catalog.data_research import RESEARCHES
from spaceinvasion.core.catalog.data_ships import SHIPS

BUILDING_BY_ID: dict[int, dict[str, Any]] = {
    int(info["id"]): info for info in BUILDINGS.values() if info.get("id") is not None
}
BUILDING_LEVELS: dict[str, dict[int, dict[str, Any]]] = {slug: info["levels"] for slug, info in BUILDINGS.items()}
RESEARCH_BY_ID: dict[str, dict[str, Any]] = {
    str(info["id"]): info for info in RESEARCHES.values() if info.get("id") is not None
}
RESEARCH_LEVELS: dict[str, dict[int, dict[str, Any]]] = {slug: info["levels"] for slug, info in RESEARCHES.items()}

# Canonical `#json-container` / form ship keys (`s1`..`s16`) mapped to hyphenated wiki slugs.
SHIP_SLUG_BY_KEY: dict[str, str] = {
    "s1": "small-transport",
    "s2": "large-transport",
    "s3": "transmitter",
    "s4": "small-fighter",
    "s5": "warship",
    "s6": "starcruiser",
    "s7": "phoenix",
    "s8": "colony-ship",
    "s9": "solar-cells",
    "s10": "spy-probe",
    "s11": "recycler",
    "s13": "battleship",
    "s14": "stealth-bomber",
    "s15": "destroyer",
    "s16": "imperial-starbase",
}

DEFENSE_SLUG_BY_KEY: dict[str, str] = {
    "d1": "mortar",
    "d2": "laser-cannon-battery",
    "d3": "ion-cannon",
    "d4": "positron-lighter",
    "d5": "turboflak",
    "d6": "interval-cannon",
    "d7": "solar-cannon",
    "d8": "light-graviton-cannon",
    "d9": "heavy-graviton-cannon",
    "d10": "small-shield-dome",
    "d11": "large-shield-dome",
}

_CATALOG_BY_CATEGORY: dict[str, dict[str, dict[str, Any]]] = {
    "buildings": BUILDINGS,
    "research": RESEARCHES,
    "ships": SHIPS,
    "defense": DEFENSES,
}


def normalize_unit_key(value: str) -> str:
    """Normalize names/slugs across US and UK spellings (defense/defence, analyzer/analyser)."""
    norm = value.strip().lower().replace("_", " ").replace("-", " ")
    norm = norm.replace("defence", "defense").replace("analyser", "analyzer")
    return " ".join(norm.split())


def _build_unit_alias_index() -> dict[str, tuple[str, str]]:
    index: dict[str, tuple[str, str]] = {}
    for cat, catalog in _CATALOG_BY_CATEGORY.items():
        for slug, info in catalog.items():
            index[slug] = (cat, slug)
            index[normalize_unit_key(slug)] = (cat, slug)
            index[normalize_unit_key(str(info["name"]))] = (cat, slug)
            if info.get("id") is not None:
                uid = str(info["id"])
                index[uid] = (cat, slug)
                if cat == "buildings" and uid.isascii() and uid.isdigit():
                    index[f"b{uid}"] = (cat, slug)
    for skey, sslug in SHIP_SLUG_BY_KEY.items():
        if sslug in SHIPS:
            index[skey] = ("ships", sslug)
    for dkey, dslug in DEFENSE_SLUG_BY_KEY.items():
        if dslug in DEFENSES:
            index[dkey] = ("defense", dslug)
    return index


_UNIT_ALIAS_INDEX: dict[str, tuple[str, str]] = _build_unit_alias_index()


def resolve_ship_slug(key_or_slug: str) -> str | None:
    """Resolve a ship key (`s1`..`s16`) or slug/name to its canonical hyphenated wiki slug."""
    cleaned = key_or_slug.strip().lower()
    if not cleaned:
        return None
    if cleaned in SHIP_SLUG_BY_KEY:
        return SHIP_SLUG_BY_KEY[cleaned]
    if cleaned in SHIPS:
        return cleaned
    hit = _UNIT_ALIAS_INDEX.get(normalize_unit_key(cleaned))
    if hit and hit[0] == "ships":
        return hit[1]
    return None


def resolve_unit(category: str | None, identifier: object) -> dict[str, Any] | None:
    """Look up a unit dict by slug, numeric building ID, `'b1'`/`'r1'`/`'s1'` key, or display name."""
    if identifier is None or isinstance(identifier, bool):
        return None
    raw: object = identifier
    if isinstance(raw, os.PathLike):
        try:
            raw = os.fspath(raw)
        except (TypeError, ValueError, RuntimeError, OSError):
            return None
        if raw is None or isinstance(raw, bool):
            return None
    if isinstance(raw, int):
        b_info = BUILDING_BY_ID.get(raw)
        if b_info is not None and category in (None, "buildings"):
            return b_info
        return None
    if isinstance(raw, (bytes, bytearray)):
        key = bytes(raw).decode("utf-8", errors="replace").strip()
    elif isinstance(raw, str):
        key = raw.strip()
    else:
        try:
            key = str(raw).strip()
        except (TypeError, ValueError, RuntimeError, RecursionError):
            return None
    if key.isascii() and key.isdigit() and len(key) <= 12:
        b_info = BUILDING_BY_ID.get(int(key))
        if b_info is not None and category in (None, "buildings"):
            return b_info
    hit = _UNIT_ALIAS_INDEX.get(key) or _UNIT_ALIAS_INDEX.get(normalize_unit_key(key))
    if not hit:
        return None
    found_cat, slug = hit
    if category is not None and found_cat != category:
        return None
    return _CATALOG_BY_CATEGORY[found_cat].get(slug)


def _current_level_for_req(
    req_cat: str,
    req_slug: str,
    buildings: Mapping[str, int],
    researches: Mapping[str, int],
) -> int:
    b_map = buildings if isinstance(buildings, Mapping) else {}
    r_map = researches if isinstance(researches, Mapping) else {}
    if req_cat == "buildings":
        info = BUILDINGS.get(req_slug)
        if not info or info.get("id") is None:
            return 0
        bid = int(info["id"])
        raw: object = b_map.get(f"b{bid}", b_map.get(str(bid), b_map.get(req_slug, 0)))
        return max(0, safe_int(raw, 0))
    if req_cat == "research":
        info = RESEARCHES.get(req_slug)
        if info and info.get("id"):
            rid = str(info["id"])
            if rid in r_map:
                return max(0, safe_int(r_map[rid], 0))
        norm_req = normalize_unit_key(req_slug)
        for k, v in r_map.items():
            if not isinstance(k, str):
                continue
            if normalize_unit_key(k) == norm_req:
                return max(0, safe_int(v, 0))
        return 0
    return 0


def missing_prerequisites(
    category: str,
    slug_or_id: str | int,
    *,
    buildings: Mapping[str, int],
    researches: Mapping[str, int],
) -> list[dict[str, Any]]:
    """Return list of unmet prerequisites `{category, slug, name, required, current}`."""
    info = resolve_unit(category, slug_or_id)
    if not info:
        return []
    missing: list[dict[str, Any]] = []
    for req in info.get("requirements", []):
        req_cat = str(req["category"])
        req_slug = str(req["slug"])
        req_lvl = int(req["level"])
        cur_lvl = _current_level_for_req(req_cat, req_slug, buildings, researches)
        if cur_lvl < req_lvl:
            missing.append(
                {
                    "category": req_cat,
                    "slug": req_slug,
                    "name": str(req["name"]),
                    "required": req_lvl,
                    "current": cur_lvl,
                }
            )
    return missing
