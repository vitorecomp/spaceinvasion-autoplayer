"""In-flight fleet ship counter extracted from legacy ``core.state``."""

from __future__ import annotations

from collections.abc import Mapping
import re

from spaceinvasion.core import catalog
from spaceinvasion.core._model_helpers import normalize_planet_type, safe_state_str
from spaceinvasion.core.coercion import coerce_int, parse_game_int
from spaceinvasion.core.coords import extract_coords_from_text

_INFLIGHT_SHIP_SLUG_BY_KEY: dict[str, str] = {
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
_INFLIGHT_SHIP_KEY_BY_SLUG: dict[str, str] = {slug: skey for skey, slug in _INFLIGHT_SHIP_SLUG_BY_KEY.items()}


def _build_inflight_ship_alias_map() -> dict[str, str]:
    mapping: dict[str, str] = {}
    for skey, slug in _INFLIGHT_SHIP_SLUG_BY_KEY.items():
        mapping[skey.lower()] = skey
        mapping[slug.lower()] = skey
        mapping[catalog.normalize_unit_key(slug)] = skey
        info = catalog.SHIPS.get(slug) or catalog.resolve_unit("ships", slug)
        if isinstance(info, Mapping):
            nm = str(info.get("name") or "").strip()
            if nm:
                norm_nm = catalog.normalize_unit_key(nm)
                mapping[norm_nm] = skey
                if not norm_nm.endswith("s"):
                    mapping[norm_nm + "s"] = skey
                elif len(norm_nm) > 2:
                    mapping[norm_nm[:-1]] = skey
    mapping.update(
        {
            "small transports": "s1",
            "kleiner transporter": "s1",
            "large transports": "s2",
            "großer transporter": "s2",
            "grosser transporter": "s2",
            "transmitters": "s3",
            "small fighters": "s4",
            "kleiner jäger": "s4",
            "kleiner jaeger": "s4",
            "warships": "s5",
            "kriegsschiff": "s5",
            "kriegsschiffe": "s5",
            "starcruisers": "s6",
            "sternkreuzer": "s6",
            "phoenixes": "s7",
            "phönix": "s7",
            "phonix": "s7",
            "nixe": "s7",
            "colony ships": "s8",
            "kolonieschiff": "s8",
            "kolonieschiffe": "s8",
            "solar cell": "s9",
            "solarzelle": "s9",
            "solarzellen": "s9",
            "spy probes": "s10",
            "spionagesonde": "s10",
            "spionagesonden": "s10",
            "recyclers": "s11",
            "battleships": "s13",
            "schlachtschiff": "s13",
            "schlachtschiffe": "s13",
            "stealth bombers": "s14",
            "tarnkappenbomber": "s14",
            "destroyers": "s15",
            "zerstörer": "s15",
            "zerstoerer": "s15",
            "imperial starbases": "s16",
            "imperiale sternenbasis": "s16",
        }
    )
    return mapping


_INFLIGHT_SHIP_ALIAS_TO_KEY: dict[str, str] = _build_inflight_ship_alias_map()
_INFLIGHT_LEG_PREFIX_RE = re.compile(r"^\s*(?:outbound|returning|in\s+flight|flight)\b\s*(?:[·•:\-|]\s*)?", re.I)
_INFLIGHT_PART_SPLIT_RE = re.compile(r"(?:(?<!\d),|,(?!\d{3}(?!\d))|[;|\n])+")
_INFLIGHT_SHIP_NUM_FIRST_RE = re.compile(r"^\s*([0-9][0-9.,']*)(?:\s*[xX×]\s*|\s+)([A-Za-zÀ-ÿ0-9_\-\s]{1,48})\s*$")
_INFLIGHT_SHIP_NAME_FIRST_RE = re.compile(r"^\s*([A-Za-zÀ-ÿ0-9_\-\s]{1,48}?)\s*[:=xX×]\s*([0-9][0-9.,']*)\s*$")
_INFLIGHT_DETAIL_TRUNCATION_LEN = 138


def _resolve_inflight_ship_key(raw_name: str) -> str | None:
    norm = catalog.normalize_unit_key(str(raw_name or ""))
    if not norm:
        return None
    for cand in (
        norm,
        norm[:-2] if norm.endswith("es") and len(norm) > 3 else "",
        norm[:-1] if norm.endswith("s") and len(norm) > 2 else "",
    ):
        if cand and (skey := _INFLIGHT_SHIP_ALIAS_TO_KEY.get(cand)) is not None:
            return skey
    unit = catalog.resolve_unit("ships", norm)
    if isinstance(unit, Mapping):
        return _INFLIGHT_SHIP_KEY_BY_SLUG.get(str(unit.get("slug") or ""))
    return None


def _parse_inflight_ships_detail(detail: str) -> tuple[dict[str, int], bool]:
    txt = _INFLIGHT_LEG_PREFIX_RE.sub("", str(detail or "").strip()).strip()
    if not txt:
        return {}, False
    ships: dict[str, int] = {}
    had_unparsed = False
    for seg in _INFLIGHT_PART_SPLIT_RE.split(txt):
        part = " ".join(seg.strip().split())
        if not part:
            continue
        qty, raw_name = 0, ""
        if m_nf := _INFLIGHT_SHIP_NUM_FIRST_RE.match(part):
            qty, raw_name = parse_game_int(m_nf.group(1)), m_nf.group(2)
        elif m_lf := _INFLIGHT_SHIP_NAME_FIRST_RE.match(part):
            raw_name, qty = m_lf.group(1), parse_game_int(m_lf.group(2))
        skey = _resolve_inflight_ship_key(raw_name) if qty > 0 and raw_name else None
        if skey is None:
            had_unparsed = True
            continue
        ships[skey] = ships.get(skey, 0) + qty
    return ships, had_unparsed


def _index_planet_coords(state: object) -> tuple[dict[int, dict[str, int]], dict[int, bool], dict[str, int]]:
    by_pid: dict[int, dict[str, int]] = {}
    complete_by_pid: dict[int, bool] = {}
    pid_by_coords: dict[str, int] = {}
    non_normal: set[str] = set()
    for p in getattr(state, "planets", ()) or ():
        pid = coerce_int(getattr(p, "planet_id", 0), 0)
        if pid <= 0:
            continue
        by_pid[pid] = {}
        complete_by_pid[pid] = True
        norm_c = extract_coords_from_text(safe_state_str(getattr(p, "coords", "")))
        if norm_c:
            is_norm = normalize_planet_type(p) == "normal"
            if norm_c not in pid_by_coords or (is_norm and norm_c in non_normal):
                pid_by_coords[norm_c] = pid
                non_normal.discard(norm_c) if is_norm else non_normal.add(norm_c)
    return by_pid, complete_by_pid, pid_by_coords


def _resolve_target_pid(
    q: object, name_s: str, name_l: str, detail_l: str, orig_coords: str, pid_by_coords: Mapping[str, int]
) -> int | None:
    is_one_way_station = (
        "station" in name_l
        and "↩" not in name_s
        and "return" not in detail_l
        and "rück" not in detail_l
        and "rueck" not in detail_l
    )
    if is_one_way_station:
        dest_part = (
            name_s.split("→", 1)[1] if "→" in name_s else (name_s.split("->", 1)[1] if "->" in name_s else name_s)
        )
        dest_coords = extract_coords_from_text(dest_part) or extract_coords_from_text(name_s)
        if dest_coords and dest_coords in pid_by_coords:
            return pid_by_coords[dest_coords]
    return pid_by_coords.get(orig_coords) if orig_coords else None


def count_in_flight_ships_by_planet(state: object) -> dict[str, object]:
    """Count friendly in-flight ships attributed per owned planet and empire-wide."""
    if state is None:
        return {"by_planet_id": {}, "detail_complete_by_planet_id": {}, "empire_in_flight": {}}
    by_pid, complete_by_pid, pid_by_coords = _index_planet_coords(state)
    empire_in_flight: dict[str, int] = {}
    for q in getattr(state, "active_queues", ()) or ():
        if getattr(q, "category", "") != "fleet" or bool(getattr(q, "hostile", False)):
            continue
        name_s = safe_state_str(getattr(q, "name", "") or "")
        detail_raw = safe_state_str(getattr(q, "detail", "") or "")
        name_l, detail_l = name_s.lower(), detail_raw.lower()
        if detail_l.strip().startswith(("shipyard", "weapons factory")):
            continue
        comb = f"{name_l} {detail_l}"
        if (
            "hostile" in comb
            or "enemy" in comb
            or "feind" in comb
            or name_l.startswith("incoming")
            or detail_l.startswith("incoming")
        ):
            continue
        orig_coords = extract_coords_from_text(safe_state_str(getattr(q, "origin_coords", "") or ""))
        target_pid = _resolve_target_pid(q, name_s, name_l, detail_l, orig_coords, pid_by_coords)
        parsed_ships, had_unparsed = _parse_inflight_ships_detail(detail_raw)
        is_incomplete = (
            not parsed_ships
            or had_unparsed
            or len(detail_raw) >= _INFLIGHT_DETAIL_TRUNCATION_LEN
            or detail_raw.rstrip().endswith(("...", "…"))
        )
        for skey, qty in parsed_ships.items():
            empire_in_flight[skey] = empire_in_flight.get(skey, 0) + qty
        if target_pid is not None:
            p_counts = by_pid.setdefault(target_pid, {})
            for skey, qty in parsed_ships.items():
                p_counts[skey] = p_counts.get(skey, 0) + qty
            if is_incomplete:
                complete_by_pid[target_pid] = False
        elif not orig_coords:
            for pid in complete_by_pid:
                complete_by_pid[pid] = False
    return {
        "by_planet_id": by_pid,
        "detail_complete_by_planet_id": complete_by_pid,
        "empire_in_flight": empire_in_flight,
    }
