"""Fleet queue detail parsing, cargo capacity capping, and building target hint helpers."""

from __future__ import annotations

from collections.abc import Mapping
import re

from spaceinvasion.combat.fleet import SHIP_SLUG_BY_KEY, normalize_coords
from spaceinvasion.core import game_data
from spaceinvasion.core.state import BUILDING_ID_BY_NAME, Resources, parse_si_int
from spaceinvasion.runtime.bridge._helpers import safe_api_int

_COORDS_INLINE_RE = re.compile(r"(\d{1,2}:\d{1,3}:\d{1,2})", re.ASCII)
_SHIP_NUM_FIRST_RE = re.compile(r"^\s*([0-9][0-9.,']*)\s*(?:x\s*)?([A-Za-zÀ-ÿ0-9_\-\s]{1,40})\s*$")
_SHIP_NAME_FIRST_RE = re.compile(r"^\s*([A-Za-zÀ-ÿ0-9_\-\s]{1,40}?)\s*[:=x]\s*([0-9][0-9.,']*)\s*$")
_BLDG_LVL_HINT_RE = re.compile(
    r"(?:early\s+pre-ship\s*#\d+\s*:\s*)?([A-Za-z][A-Za-z\s\-]{1,48}?)\s*(?:->\s*)?(?:L|level\s*)(\d{1,3})\b",
    re.IGNORECASE,
)


def _build_ship_name_to_key_map() -> dict[str, str]:
    mapping: dict[str, str] = {}
    for skey, slug in SHIP_SLUG_BY_KEY.items():
        mapping[skey.lower()] = skey
        mapping[slug.lower()] = skey
        mapping[slug.lower().replace("-", " ")] = skey
        info = game_data.resolve_unit("ships", slug)
        if isinstance(info, Mapping):
            nm = str(info.get("name") or "").strip().lower()
            if nm:
                mapping[nm] = skey
                if not nm.endswith("s"):
                    mapping[nm + "s"] = skey
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


_SHIP_NAME_TO_KEY: dict[str, str] = _build_ship_name_to_key_map()


def extract_coords_from_text(text: object) -> str:
    """Extract and normalize the first `G:S:P` coordinate pair in text, or empty string."""
    if not text:
        return ""
    match = _COORDS_INLINE_RE.search(str(text))
    return str(normalize_coords(match.group(1))) if match else ""


def parse_ships_from_queue_detail(detail: str) -> dict[str, int]:
    """Parse ship counts from `QueueJob.detail` (e.g. `'Outbound · 12 Recycler, 4 Large transport'`)."""
    txt = str(detail or "").strip()
    if not txt:
        return {}
    if "·" in txt:
        head, tail = txt.split("·", 1)
        if head.strip().lower() in ("outbound", "returning", "in flight", "flight"):
            txt = tail.strip()
    if not txt:
        return {}
    ships: dict[str, int] = {}
    for seg in re.split(r"[,;|\n]+", txt):
        part = " ".join(seg.strip().split())
        if not part:
            continue
        m_nf = _SHIP_NUM_FIRST_RE.match(part)
        qty = 0
        raw_name = ""
        if m_nf:
            qty = parse_si_int(m_nf.group(1))
            raw_name = m_nf.group(2)
        else:
            m_lf = _SHIP_NAME_FIRST_RE.match(part)
            if m_lf:
                raw_name = m_lf.group(1)
                qty = parse_si_int(m_lf.group(2))
        if qty <= 0 or not raw_name:
            continue
        norm_name = " ".join(raw_name.strip().lower().split())
        skey = _SHIP_NAME_TO_KEY.get(norm_name)
        if skey is None and norm_name.endswith("s"):
            skey = _SHIP_NAME_TO_KEY.get(norm_name[:-1])
        if skey is not None:
            ships[skey] = ships.get(skey, 0) + qty
    return ships


def cap_resources_by_capacity(raw: Resources, capacity: int | None, *, mode: str = "value") -> Resources:
    """Cap expected loot or debris harvest to fleet cargo capacity."""
    if capacity is None or capacity <= 0:
        return raw
    total = raw.total()
    if total <= 0 or total <= capacity:
        return raw
    if mode == "proportional":
        scale = float(capacity) / float(total)
        raw_map: dict[str, int] = {
            "pig_iron": raw.pig_iron,
            "metal": raw.metal,
            "kryptonite": raw.kryptonite,
            "spice": raw.spice,
        }
        vals = {rk: min(rv, int(rv * scale)) for rk, rv in raw_map.items()}
        rem = capacity - sum(vals.values())
        if rem > 0:
            for rk in ("metal", "pig_iron", "kryptonite", "spice"):
                if rem <= 0:
                    break
                headroom = max(0, raw_map[rk] - vals[rk])
                if headroom > 0:
                    add = min(headroom, rem)
                    vals[rk] += add
                    rem -= add
        return Resources(
            pig_iron=vals["pig_iron"],
            metal=vals["metal"],
            kryptonite=vals["kryptonite"],
            spice=vals["spice"],
        )
    rem = capacity
    spi = min(raw.spice, rem)
    rem -= spi
    met = min(raw.metal, rem)
    rem -= met
    kry = min(raw.kryptonite, rem)
    rem -= kry
    pig = min(raw.pig_iron, rem)
    return Resources(pig_iron=pig, metal=met, kryptonite=kry, spice=spi)


def parse_building_target_hint(label: str) -> tuple[str, int | None, int | None]:
    """Extract `(target_building, target_level, building_id)` from a human label or transport reason."""
    txt = " ".join(str(label or "").strip().split())
    if not txt:
        return "", None, None
    match = _BLDG_LVL_HINT_RE.search(txt)
    if match:
        name = " ".join(match.group(1).strip().split())
        lvl = safe_api_int(match.group(2), 0)
        bid = BUILDING_ID_BY_NAME.get(name.lower())
        return name, (lvl if lvl > 0 else None), bid
    low = txt.lower()
    for bname_low, bid in BUILDING_ID_BY_NAME.items():
        if bname_low in low:
            info = game_data.BUILDING_BY_ID.get(bid)
            canon = str(info.get("name") or bname_low.title()) if isinstance(info, Mapping) else bname_low.title()
            return canon, None, bid
    return txt[:64], None, None
