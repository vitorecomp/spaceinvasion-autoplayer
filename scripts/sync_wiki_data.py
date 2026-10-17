#!/usr/bin/env python3
"""Scrape official SpaceInvasion Classic Wiki data into `spaceinvasion/game_data.py`.

Fetches all unit pages (`/wiki/en/{buildings,research,ships,defense}/...`) plus
economy/resource constants from https://spaceinvasion.bitmeup.com/wiki/en/ and
generates the offline typed catalog `spaceinvasion/game_data.py`.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import math
from pathlib import Path
import pprint
import re
from typing import Any
from urllib.parse import urljoin

from bs4 import BeautifulSoup, Tag
import requests

WIKI_INDEX_URL = "https://spaceinvasion.bitmeup.com/wiki/en/"
MERCHANT_URL = "https://spaceinvasion.bitmeup.com/wiki/en/economy/merchant"
OUTPUT_TABLES_DIR = Path(__file__).resolve().parent.parent / "spaceinvasion" / "core" / "catalog" / "tables"

BUILDING_ID_BY_SLUG: dict[str, int] = {
    "pig-iron-mine": 1,
    "blast-furnace": 2,
    "kryptonite-mine": 3,
    "spice-mine": 4,
    "fusion-plant": 5,
    "thermosolar-plant": 6,
    "development-center": 7,
    "research-lab": 8,
    "weapons-factory": 9,
    "fleet-base": 10,
    "pig-iron-warehouse": 11,
    "metal-warehouse": 12,
    "kryptonite-warehouse": 13,
    "spice-warehouse": 14,
    "particle-cannon": 15,
    "particle-shield": 16,
    "microsystem-accelerator": 17,
    "teleporter": 18,
    "galaxy-scanner": 19,
    "tunnel-system": 20,
    "quantum-array": 21,
}

# RETRO universe live `research_names` mapping (`r1`..`r15`)
RESEARCH_ID_BY_SLUG: dict[str, str] = {
    "mining-technology": "r1",
    "energy-technology": "r2",
    "engine-research": "r3",
    "rocket-engine": "r4",
    "diffusion-engine": "r5",
    "warp-engine": "r6",
    "spy-technology": "r7",
    "weapons-technology": "r8",
    "shield-technology": "r9",
    "defense-systems": "r10",
    "targeting-systems": "r11",
    "particle-analyzer": "r12",
    "graviton-technology": "r13",
    "teleportation": "r14",
    "black-hole-research": "r15",
}

_RES_CLASS_TO_KEY = {
    "w-res1": "pig_iron",
    "w-res2": "metal",
    "w-res3": "kryptonite",
    "w-res4": "spice",
}

_COST_HEADER_TO_KEY = {
    "Pig iron": "pig_iron",
    "Metal": "metal",
    "Kryptonite": "kryptonite",
    "Spice": "spice",
}

_DT_STAT_TO_KEY = {
    "Hull (structure)": "hull",
    "Shield": "shield",
    "Attack": "attack",
    "Speed": "speed",
    "Cargo capacity": "cargo",
    "Fuel": "fuel",
    "Engine": "engine",
}


_SUFFIX_MULTIPLIERS = {
    "k": 1_000,
    "m": 1_000_000,
    "b": 1_000_000_000,
    "t": 1_000_000_000_000,
    "qa": 1_000_000_000_000_000,
    "qi": 1_000_000_000_000_000_000,
}


def _parse_int(raw: str) -> int:
    text = raw.strip().lstrip("+")
    neg = text.startswith("-")
    m = re.match(r"^-?\s*([\d,]+(?:\.\d+)?)\s*(qi|qa|t|b|m|k)?\b", text, re.IGNORECASE | re.ASCII)
    if m and m.group(2):
        try:
            num = float(m.group(1).replace(",", ""))
        except Exception:
            return 0
        if not math.isfinite(num):
            return 0
        prod = num * _SUFFIX_MULTIPLIERS[m.group(2).lower()]
        if not math.isfinite(prod):
            return 0
        val = int(round(prod))
        if val > 10**200:
            val = 10**200
        return -val if neg else val
    digits = re.sub(r"[^\d]", "", text, flags=re.ASCII)
    if not digits:
        return 0
    trimmed = digits.lstrip("0") or "0"
    if len(trimmed) > 4000:
        val = 10**200
    else:
        val = int(trimmed)
        if val > 10**200:
            val = 10**200
    return -val if neg else val


def _parse_seconds(raw: str) -> float:
    cleaned = raw.strip().replace(",", "")
    m = re.search(r"[\d]+(?:\.\d+)?", cleaned, re.ASCII)
    if not m:
        return 0.0
    try:
        val = float(m.group(0))
    except Exception:
        return 0.0
    return round(val, 4) if math.isfinite(val) else 0.0


def _parse_float_pct(raw: str) -> float:
    m = re.search(r"\d+(?:\.\d+)?", raw.replace(",", "."), re.ASCII)
    if not m:
        return 0.0
    try:
        val = float(m.group(0))
    except Exception:
        return 0.0
    return val if math.isfinite(val) else 0.0


def _clean_title(soup: BeautifulSoup, fallback_slug: str) -> str:
    h1 = soup.find("h1")
    if h1:
        text = h1.get_text(" ", strip=True)
        if text:
            return text
    if soup.title:
        title = soup.title.get_text(" ", strip=True)
        return title.split("|", 1)[0].strip() or fallback_slug
    return fallback_slug.replace("-", " ").title()


def _h3_siblings(soup: BeautifulSoup, heading_text: str) -> list[Tag]:
    for h3 in soup.find_all("h3"):
        if h3.get_text(" ", strip=True).lower() == heading_text.lower():
            out: list[Tag] = []
            for sib in h3.find_next_siblings():
                if not isinstance(sib, Tag):
                    continue
                if sib.name in ("h2", "h3"):
                    break
                out.append(sib)
            return out
    return []


def _parse_costs(siblings: list[Tag]) -> dict[str, int]:
    costs: dict[str, int] = {"pig_iron": 0, "metal": 0, "kryptonite": 0, "spice": 0}
    for sib in siblings:
        ul = sib if sib.name == "ul" and "w-costs" in (sib.get("class") or []) else sib.find("ul", class_="w-costs")
        if not ul:
            continue
        for li in ul.find_all("li"):
            classes = li.get("class") or []
            res_key = next((_RES_CLASS_TO_KEY[c] for c in classes if c in _RES_CLASS_TO_KEY), None)
            if not res_key:
                continue
            b = li.find("b")
            if b and b.get("data-n"):
                costs[res_key] = _parse_int(str(b["data-n"]))
            elif b:
                costs[res_key] = _parse_int(b.get_text())
    return costs


def _parse_deps(siblings: list[Tag]) -> list[dict[str, Any]]:
    deps: list[dict[str, Any]] = []
    for sib in siblings:
        ul = sib if sib.name == "ul" and "w-deps" in (sib.get("class") or []) else sib.find("ul", class_="w-deps")
        if not ul:
            continue
        for li in ul.find_all("li"):
            a = li.find("a", href=True)
            if not a:
                continue
            href = str(a["href"])
            parts = [p for p in href.strip("/").split("/") if p]
            category = parts[-2] if len(parts) >= 2 else ""
            slug = parts[-1] if parts else ""
            lvl_span = li.find("span", class_="w-lvl")
            lvl = _parse_int(lvl_span.get_text()) if lvl_span else 1
            deps.append(
                {
                    "category": category,
                    "slug": slug,
                    "name": a.get_text(" ", strip=True),
                    "level": max(1, lvl),
                }
            )
    return deps


def _parse_unit_build_time(siblings: list[Tag]) -> tuple[float, dict[str, str]]:
    base_s = 0.0
    factors: dict[str, str] = {}
    for sib in siblings:
        p = sib.find("p", class_="w-big") if sib.name != "p" else sib
        if p and p.get("data-t"):
            base_s = _parse_seconds(str(p["data-t"]))
            if p.get("data-f"):
                factors["formula"] = str(p["data-f"])
        for sel in sib.find_all("select"):
            ctrl = sel.get("data-w-ctrl")
            if ctrl:
                factors["control"] = str(ctrl)
    return base_s, factors


def _parse_level_table(table: Tag) -> tuple[dict[int, dict[str, Any]], dict[int, int]]:
    thead = table.find("thead")
    if not thead:
        return {}, {}
    headers = [th.get_text(" ", strip=True) for th in thead.find_all("th")]
    if headers == ["Planet temperature", "Energy per solar cell"]:
        temp_map: dict[int, int] = {}
        tbody = table.find("tbody") or table
        for tr in tbody.find_all("tr"):
            cells = tr.find_all(["th", "td"])
            if len(cells) >= 2:
                temp_map[_parse_int(cells[0].get_text())] = _parse_int(cells[1].get_text())
        return {}, temp_map

    if not headers or headers[0] != "Level":
        return {}, {}

    levels: dict[int, dict[str, Any]] = {}
    tbody = table.find("tbody") or table
    for tr in tbody.find_all("tr"):
        th = tr.find("th")
        tds = tr.find_all("td")
        if not th or len(tds) + 1 != len(headers):
            continue
        lvl = _parse_int(th.get_text())
        if lvl <= 0:
            continue
        cost: dict[str, int] = {"pig_iron": 0, "metal": 0, "kryptonite": 0, "spice": 0}
        entry: dict[str, Any] = {"cost": cost}
        for hdr, td in zip(headers[1:], tds):
            if hdr in _COST_HEADER_TO_KEY:
                cost[_COST_HEADER_TO_KEY[hdr]] = _parse_int(td.get_text())
            elif hdr in ("Build time", "Research time"):
                if td.get("data-t") is not None:
                    entry["base_time_s"] = _parse_seconds(str(td["data-t"]))
            elif hdr.endswith("/ hour") and "used" not in hdr:
                raw_v = td.get("data-v")
                entry["prod"] = _parse_int(str(raw_v)) if raw_v is not None else _parse_int(td.get_text())
                if td.get("data-temps"):
                    try:
                        raw_temps = json.loads(str(td["data-temps"]))
                    except Exception:
                        raw_temps = None
                    if isinstance(raw_temps, dict):
                        prod_by_temp: dict[int, int] = {}
                        for k, v in raw_temps.items():
                            try:
                                k_i = _parse_int(str(k))
                                v_i = _parse_int(str(v))
                            except Exception:
                                continue
                            prod_by_temp[k_i] = v_i
                        if prod_by_temp:
                            entry["prod_by_temp"] = prod_by_temp
            elif hdr == "Pig iron used / hour":
                raw_v = td.get("data-v")
                entry["pig_iron_used"] = _parse_int(str(raw_v)) if raw_v is not None else _parse_int(td.get_text())
            elif hdr == "Spice used / hour":
                raw_v = td.get("data-v")
                entry["spice_used"] = _parse_int(str(raw_v)) if raw_v is not None else _parse_int(td.get_text())
            elif hdr in ("Energy", "Energy needed"):
                raw_v = td.get("data-v")
                entry["energy"] = _parse_int(str(raw_v)) if raw_v is not None else _parse_int(td.get_text())
            elif hdr == "Build time of other buildings":
                entry["building_time_pct"] = _parse_float_pct(td.get_text())
            elif hdr == "Build time of ships & defense":
                entry["shipyard_time_pct"] = _parse_float_pct(td.get_text())
            elif hdr == "Storage capacity":
                entry["storage_capacity"] = _parse_int(td.get_text())
        levels[lvl] = entry
    return levels, {}


def _parse_stats_dl(soup: BeautifulSoup) -> dict[str, Any]:
    stats: dict[str, Any] = {}
    for dt in soup.find_all("dt"):
        key = _DT_STAT_TO_KEY.get(dt.get_text(" ", strip=True))
        if not key:
            continue
        dd = dt.find_next_sibling("dd")
        if not dd:
            continue
        txt = dd.get_text(" ", strip=True)
        stats[key] = txt if key == "engine" else _parse_int(txt)
    return stats


def _fetch_merchant_rates(session: requests.Session) -> dict[str, float]:
    defaults = {"pig_iron": 6.0, "metal": 2.5, "kryptonite": 3.0, "spice": 1.0}
    resp = session.get(MERCHANT_URL, timeout=20)
    if not resp.ok:
        return defaults
    text = BeautifulSoup(resp.text, "html.parser").get_text(" ", strip=True)
    m = re.search(
        r"Pig iron\s*([\d.]+)\s*:\s*Metal\s*([\d.]+)\s*:\s*Kryptonite\s*([\d.]+)\s*:\s*Spice\s*([\d.]+)",
        text,
        re.ASCII,
    )
    if not m:
        return defaults
    try:
        rates = {
            "pig_iron": float(m.group(1)),
            "metal": float(m.group(2)),
            "kryptonite": float(m.group(3)),
            "spice": float(m.group(4)),
        }
    except Exception:
        return defaults
    if not all(math.isfinite(v) and v > 0.0 for v in rates.values()):
        return defaults
    return rates


def _discover_unit_urls(session: requests.Session) -> list[tuple[str, str, str]]:
    resp = session.get(WIKI_INDEX_URL, timeout=20)
    resp.raise_for_status()
    soup = BeautifulSoup(resp.text, "html.parser")
    seen: set[tuple[str, str]] = set()
    units: list[tuple[str, str, str]] = []
    for a in soup.find_all("a", href=True):
        href = str(a["href"])
        m = re.search(r"/wiki/en/(buildings|research|ships|defense)/([a-z0-9-]+)$", href)
        if not m:
            continue
        cat, slug = m.group(1), m.group(2)
        if (cat, slug) in seen:
            continue
        seen.add((cat, slug))
        units.append((cat, slug, urljoin("https://spaceinvasion.bitmeup.com", href)))
    return sorted(units)


def scrape_wiki() -> dict[str, Any]:
    with requests.Session() as session:
        session.headers["User-Agent"] = "spaceinvasion-autoplayer-wiki-sync/1.0"

        merchant_rates = _fetch_merchant_rates(session)
        unit_urls = _discover_unit_urls(session)

        buildings: dict[str, dict[str, Any]] = {}
        researches: dict[str, dict[str, Any]] = {}
        ships: dict[str, dict[str, Any]] = {}
        defenses: dict[str, dict[str, Any]] = {}

        for category, slug, url in unit_urls:
            resp = session.get(url, timeout=20)
            resp.raise_for_status()
            soup = BeautifulSoup(resp.text, "html.parser")
            if not any("fact sheet" in h2.get_text(" ", strip=True).lower() for h2 in soup.find_all("h2")):
                continue

            name = _clean_title(soup, slug)
            badge_texts = [p.get_text(" ", strip=True).lower() for p in soup.find_all("p", class_="w-badge")]
            asteroid_ok = any("asteroids" in t for t in badge_texts)

            cost_siblings = _h3_siblings(soup, "Cost of level 1") or _h3_siblings(soup, "Costs")
            base_cost = _parse_costs(cost_siblings)
            requirements = _parse_deps(_h3_siblings(soup, "Requirements"))
            required_for = _parse_deps(_h3_siblings(soup, "Required for"))

            levels: dict[int, dict[str, Any]] = {}
            solar_temp_map: dict[int, int] = {}
            for tbl in soup.find_all("table"):
                parsed_levels, parsed_temp = _parse_level_table(tbl)
                if parsed_levels:
                    levels = parsed_levels
                if parsed_temp:
                    solar_temp_map = parsed_temp

            common: dict[str, Any] = {
                "slug": slug,
                "name": name,
                "url": url,
                "base_cost": base_cost,
                "requirements": requirements,
                "required_for": required_for,
            }

            if category == "buildings":
                common["id"] = BUILDING_ID_BY_SLUG.get(slug)
                common["asteroid_ok"] = asteroid_ok
                common["max_table_level"] = max(levels) if levels else 0
                common["levels"] = levels
                buildings[slug] = common
            elif category == "research":
                common["id"] = RESEARCH_ID_BY_SLUG.get(slug)
                common["max_table_level"] = max(levels) if levels else 0
                common["levels"] = levels
                researches[slug] = common
            elif category == "ships":
                base_s, factors = _parse_unit_build_time(_h3_siblings(soup, "Build time per unit"))
                common["base_build_time_s"] = base_s
                common["build_time_factors"] = factors
                common["stats"] = _parse_stats_dl(soup)
                if solar_temp_map:
                    common["solar_cell_energy_by_temp"] = solar_temp_map
                ships[slug] = common
            elif category == "defense":
                base_s, factors = _parse_unit_build_time(_h3_siblings(soup, "Build time per unit"))
                common["base_build_time_s"] = base_s
                common["build_time_factors"] = factors
                common["stats"] = _parse_stats_dl(soup)
                defenses[slug] = common

        return {
            "synced_at_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "source": WIKI_INDEX_URL,
            "merchant_rates_in_spice": merchant_rates,
            "buildings": buildings,
            "researches": researches,
            "ships": ships,
            "defenses": defenses,
        }


_HELPER_CODE = '''

BUILDING_BY_ID: dict[int, dict[str, Any]] = {
    int(info["id"]): info for info in BUILDINGS.values() if info.get("id") is not None
}
BUILDING_LEVELS: dict[str, dict[int, dict[str, Any]]] = {
    slug: info["levels"] for slug, info in BUILDINGS.items()
}
RESEARCH_BY_ID: dict[str, dict[str, Any]] = {
    str(info["id"]): info for info in RESEARCHES.values() if info.get("id") is not None
}
RESEARCH_LEVELS: dict[str, dict[int, dict[str, Any]]] = {
    slug: info["levels"] for slug, info in RESEARCHES.items()
}


def normalize_unit_key(value: str) -> str:
    """Normalize names/slugs across US and UK spellings (defense/defence, analyzer/analyser)."""
    norm = value.strip().lower().replace("_", " ").replace("-", " ")
    norm = norm.replace("defence", "defense").replace("analyser", "analyzer")
    return " ".join(norm.split())


_UNIT_ALIAS_INDEX: dict[str, tuple[str, str]] = {}
for _cat, _catalog in (
    ("buildings", BUILDINGS),
    ("research", RESEARCHES),
    ("ships", SHIPS),
    ("defense", DEFENSES),
):
    for _slug, _info in _catalog.items():
        _UNIT_ALIAS_INDEX[_slug] = (_cat, _slug)
        _UNIT_ALIAS_INDEX[normalize_unit_key(_slug)] = (_cat, _slug)
        _UNIT_ALIAS_INDEX[normalize_unit_key(str(_info["name"]))] = (_cat, _slug)
        if _info.get("id") is not None:
            _uid = str(_info["id"])
            _UNIT_ALIAS_INDEX[_uid] = (_cat, _slug)
            if _cat == "buildings" and _uid.isascii() and _uid.isdigit():
                _UNIT_ALIAS_INDEX[f"b{_uid}"] = (_cat, _slug)


def resolve_unit(category: Optional[str], identifier: str | int) -> Optional[dict[str, Any]]:
    """Look up a unit dict by slug, numeric building ID, `'b1'`/`'r1'` key, or display name."""
    if identifier is None or isinstance(identifier, bool):
        return None
    if isinstance(identifier, os.PathLike):
        try:
            identifier = os.fspath(identifier)
        except Exception:
            return None
        if identifier is None or isinstance(identifier, bool):
            return None
    if isinstance(identifier, int):
        b_info = BUILDING_BY_ID.get(identifier)
        if b_info is not None and category in (None, "buildings"):
            return b_info
        return None
    if isinstance(identifier, (bytes, bytearray)):
        key = bytes(identifier).decode("utf-8", errors="replace").strip()
    elif isinstance(identifier, str):
        key = identifier.strip()
    else:
        try:
            key = str(identifier).strip()
        except Exception:
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
    return {"buildings": BUILDINGS, "research": RESEARCHES, "ships": SHIPS, "defense": DEFENSES}[found_cat].get(slug)


MAX_JSON_SAFE_INT = 10**200
_MAX_FORMAT_INT_BITS = 12_000
_LOG10_2 = 0.3010299956639812


def safe_int(value: Any, default: int = 0) -> int:
    if value is None or isinstance(value, (bool, Mapping, list, tuple, set, frozenset)):
        return default
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value) if math.isfinite(value) else default
    if isinstance(value, (bytes, bytearray)):
        value = bytes(value).decode("utf-8", errors="replace")
    if isinstance(value, str):
        txt = value.strip()
        if (
            not txt
            or not txt.isascii()
            or txt.lower()
            in (
                "null",
                "none",
                "~",
                "nan",
                "inf",
                "+inf",
                "-inf",
                "infinity",
                "+infinity",
                "-infinity",
            )
        ):
            return default
        value = txt
    try:
        return int(value)
    except Exception:
        try:
            f = float(value)
            return int(f) if math.isfinite(f) else default
        except Exception:
            return default


def safe_optional_int(value: Any, default: Optional[int] = None) -> Optional[int]:
    if value is None or isinstance(value, (bool, Mapping, list, tuple, set, frozenset)):
        return default
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value) if math.isfinite(value) else default
    if isinstance(value, (bytes, bytearray)):
        value = bytes(value).decode("utf-8", errors="replace")
    if isinstance(value, str):
        txt = value.strip()
        if (
            not txt
            or not txt.isascii()
            or txt.lower()
            in (
                "null",
                "none",
                "~",
                "nan",
                "inf",
                "+inf",
                "-inf",
                "infinity",
                "+infinity",
                "-infinity",
            )
        ):
            return default
        value = txt
    try:
        return int(value)
    except Exception:
        try:
            f = float(value)
            return int(f) if math.isfinite(f) else default
        except Exception:
            return default


def safe_finite_float(value: Any, default: float = 0.0) -> float:
    if value is None or isinstance(value, (bool, Mapping, list, tuple, set, frozenset)):
        return default
    if isinstance(value, (bytes, bytearray)):
        value = bytes(value).decode("utf-8", errors="replace")
    if isinstance(value, str):
        txt = value.strip()
        if not txt or not txt.isascii() or txt.lower() in ("null", "none", "~"):
            return default
        value = txt
    try:
        f = float(value)
    except OverflowError:
        if isinstance(value, int):
            return 1e300 if value > 0 else -1e300
        return default
    except Exception:
        return default
    return f if math.isfinite(f) else default


def cap_json_int(value: Any, *, limit: int = MAX_JSON_SAFE_INT, default: int = 0) -> int:
    if isinstance(value, int) and not isinstance(value, bool):
        v = value
    else:
        v = safe_int(value, default)
    if v > limit:
        return limit
    if v < -limit:
        return -limit
    return v


_MAX_KEY_NODES = 256
_MAX_KEY_CHARS = 4096
_MAX_TREE_NODES = 32_768
_MAX_TREE_DEPTH = 64


def _clip_str(text: str, limit: int = _MAX_KEY_CHARS) -> str:
    if len(text) <= limit:
        return text
    return text[:limit] + "...[truncated]"


def _safe_json_key(
    k: Any,
    max_int: int,
    *,
    _depth: int = 0,
    _seen: Optional[set[int]] = None,
    _budget: Optional[list[int]] = None,
) -> str:
    if isinstance(k, str):
        return k
    if isinstance(k, int) and not isinstance(k, bool):
        return str(cap_json_int(k, limit=max_int))
    if isinstance(k, (bytes, bytearray)):
        return bytes(k).decode("utf-8", errors="replace")
    seen = _seen if _seen is not None else set()
    budget = _budget if _budget is not None else [_MAX_KEY_NODES]
    if isinstance(k, os.PathLike):
        vid = id(k)
        if vid in seen or _depth >= 16 or budget[0] <= 0:
            return f"<{type(k).__name__}>"
        budget[0] -= 1
        seen.add(vid)
        try:
            try:
                fsp = os.fspath(k)
            except Exception:
                return f"<{type(k).__name__}>"
            return _safe_json_key(fsp, max_int, _depth=_depth + 1, _seen=seen, _budget=budget)
        finally:
            seen.discard(vid)
    if isinstance(k, BaseException):
        str_fn = getattr(type(k), "__str__", BaseException.__str__)
        if str_fn not in (BaseException.__str__, KeyError.__str__):
            try:
                return _clip_str(str(k), 16_384)
            except Exception:
                pass
        args = getattr(k, "args", ())
        if not isinstance(args, (tuple, list)) or not args:
            return "" if str_fn is BaseException.__str__ else type(k).__name__
        vid = id(k)
        if vid in seen or _depth >= 16 or budget[0] <= 0:
            return f"<{type(k).__name__}>"
        budget[0] -= 1
        seen.add(vid)
        try:
            if len(args) == 1:
                a0 = args[0]
                if isinstance(a0, str):
                    res = repr(a0) if isinstance(k, KeyError) else a0
                    return _clip_str(res, 16_384)
                return _clip_str(
                    _safe_json_key(a0, max_int, _depth=_depth + 1, _seen=seen, _budget=budget),
                    _MAX_KEY_CHARS,
                )
            parts: list[str] = []
            for item in args[:8]:
                if budget[0] <= 0:
                    parts.append("...")
                    break
                if isinstance(item, str):
                    parts.append(repr(item))
                else:
                    parts.append(
                        _safe_json_key(
                            item,
                            max_int,
                            _depth=_depth + 1,
                            _seen=seen,
                            _budget=budget,
                        )
                    )
            if len(args) > 8:
                parts.append(f"...+{len(args) - 8}")
            return _clip_str("(" + ", ".join(parts) + ")", _MAX_KEY_CHARS)
        finally:
            seen.discard(vid)
    if isinstance(k, (tuple, list, set, frozenset, Mapping)):
        vid = id(k)
        if vid in seen:
            if isinstance(k, tuple):
                return "(...)"
            if isinstance(k, list):
                return "[...]"
            if isinstance(k, frozenset):
                return "frozenset({...})"
            return "{...}"
        if _depth >= 16 or budget[0] <= 0:
            return "..."
        budget[0] -= 1
        seen.add(vid)
        try:
            if isinstance(k, tuple):
                parts = []
                for idx, item in enumerate(k):
                    if idx >= 64 or budget[0] <= 0:
                        parts.append("...")
                        break
                    parts.append(
                        _safe_json_key(item, max_int, _depth=_depth + 1, _seen=seen, _budget=budget)
                    )
                inner = ", ".join(parts)
                return _clip_str(f"({inner},)" if len(k) == 1 else f"({inner})", _MAX_KEY_CHARS)
            if isinstance(k, list):
                parts = []
                for idx, item in enumerate(k):
                    if idx >= 64 or budget[0] <= 0:
                        parts.append("...")
                        break
                    parts.append(
                        _safe_json_key(item, max_int, _depth=_depth + 1, _seen=seen, _budget=budget)
                    )
                return _clip_str("[" + ", ".join(parts) + "]", _MAX_KEY_CHARS)
            if isinstance(k, (set, frozenset)):
                parts = []
                for idx, item in enumerate(k):
                    if idx >= 64 or budget[0] <= 0:
                        parts.append("...")
                        break
                    parts.append(
                        _safe_json_key(item, max_int, _depth=_depth + 1, _seen=seen, _budget=budget)
                    )
                inner = ", ".join(sorted(parts))
                if isinstance(k, frozenset):
                    return _clip_str(f"frozenset({{{inner}}})", _MAX_KEY_CHARS)
                return _clip_str(f"{{{inner}}}" if parts else "set()", _MAX_KEY_CHARS)
            # Mapping
            parts = []
            for idx, (mk, mv) in enumerate(k.items()):
                if idx >= 32 or budget[0] <= 0:
                    parts.append("...")
                    break
                ks = _safe_json_key(mk, max_int, _depth=_depth + 1, _seen=seen, _budget=budget)
                vs = _safe_json_key(mv, max_int, _depth=_depth + 1, _seen=seen, _budget=budget)
                parts.append(f"{ks!r}: {vs!r}")
            return _clip_str("{" + ", ".join(parts) + "}", _MAX_KEY_CHARS)
        finally:
            seen.discard(vid)
    try:
        return str(k)
    except Exception:
        return type(k).__name__


def safe_key(key: Any, *, max_int: int = MAX_JSON_SAFE_INT) -> str:
    """Convert arbitrary dict keys or identifiers to string safely (resisting >4300-digit ints/tuples)."""
    return _safe_json_key(key, max_int)


def sanitize_json_tree(
    obj: Any,
    *,
    max_int: int = MAX_JSON_SAFE_INT,
    nonfinite_float: Any = 0.0,
    _depth: int = 0,
    _seen: Optional[set[int]] = None,
    _budget: Optional[list[int]] = None,
) -> Any:
    seen = _seen if _seen is not None else set()
    budget = _budget if _budget is not None else [_MAX_TREE_NODES]

    def _walk(val: Any, d: int) -> Any:
        tv = type(val)
        if val is None or tv is bool or tv is str:
            return val
        if tv is int:
            return val if -max_int <= val <= max_int else cap_json_int(val, limit=max_int)
        if tv is float:
            return val if math.isfinite(val) else nonfinite_float
        is_dict = tv is dict
        is_seq = tv is list or tv is tuple
        if is_dict or is_seq or isinstance(val, (Mapping, list, tuple, set, frozenset, os.PathLike, bytes, bytearray, int, float)):
            if not (is_dict or is_seq) and isinstance(val, (bool, str)):
                return str(val)
            if not (is_dict or is_seq) and isinstance(val, int):
                return cap_json_int(int(val), limit=max_int)
            if not (is_dict or is_seq) and isinstance(val, float):
                fval = float(val)
                return fval if math.isfinite(fval) else nonfinite_float
            if not (is_dict or is_seq) and isinstance(val, (bytes, bytearray)):
                return bytes(val).decode("utf-8", errors="replace")
            vid = id(val)
            if vid in seen:
                return "[Cycle]"
            if d >= _MAX_TREE_DEPTH or budget[0] <= 0:
                return "[Truncated]"
            budget[0] -= 1
            seen.add(vid)
            try:
                if is_dict or isinstance(val, Mapping):
                    out: dict[str, Any] = {}
                    for k, v in val.items():
                        if budget[0] <= 0:
                            out["[Truncated]"] = "[Truncated]"
                            break
                        sk = k if type(k) is str else _safe_json_key(k, max_int)
                        out[sk] = _walk(v, d + 1)
                    return out
                if is_seq or isinstance(val, (list, tuple, set, frozenset)):
                    out_list: list[Any] = []
                    for v in val:
                        if budget[0] <= 0:
                            out_list.append("[Truncated]")
                            break
                        out_list.append(_walk(v, d + 1))
                    return out_list
                try:
                    fsp = os.fspath(val)
                except Exception:
                    return safe_key(val, max_int=max_int)
                if isinstance(fsp, (bytes, bytearray)):
                    return bytes(fsp).decode("utf-8", errors="replace")
                if isinstance(fsp, str):
                    return fsp
                if isinstance(fsp, os.PathLike):
                    return safe_key(fsp, max_int=max_int)
                return _walk(fsp, d + 1)
            finally:
                seen.discard(vid)
        return safe_key(val, max_int=max_int)

    return _walk(obj, _depth)


def _to_mant_exp2(val: int | float) -> tuple[float, int]:
    if isinstance(val, int):
        if val == 0:
            return 0.0, 0
        sign = -1.0 if val < 0 else 1.0
        aval = -val if val < 0 else val
        shift = max(0, aval.bit_length() - 53)
        return sign * float(aval >> shift), shift
    return math.frexp(float(val))


def safe_big_ratio(
    num: Any,
    den: Any,
    *,
    cap: float = 1e200,
    allow_negative: bool = False,
) -> float:
    if isinstance(num, bool) or isinstance(den, bool):
        return 0.0
    if not isinstance(num, (int, float)):
        num = safe_finite_float(num, 0.0)
    if not isinstance(den, (int, float)):
        den = safe_finite_float(den, 0.0)
    if isinstance(den, float) and (not math.isfinite(den) or den <= 0.0):
        return 0.0
    if isinstance(den, int) and den <= 0:
        return 0.0
    if isinstance(num, float) and not math.isfinite(num):
        return 0.0
    if not allow_negative and num <= 0:
        return 0.0
    if num == 0:
        return 0.0

    low_cap = -cap if allow_negative else 0.0
    if (
        isinstance(num, int)
        and isinstance(den, int)
        and abs(num).bit_length() <= 1020
        and den.bit_length() <= 1020
    ):
        val = num / den
        return max(low_cap, min(cap, val)) if math.isfinite(val) else 0.0

    if isinstance(num, float) and isinstance(den, float):
        try:
            val = num / den
            if math.isfinite(val):
                return max(low_cap, min(cap, val))
        except OverflowError:
            pass

    m_n, e_n = _to_mant_exp2(num)
    m_d, e_d = _to_mant_exp2(den)
    if m_d == 0.0 or m_n == 0.0:
        return 0.0
    m_q, r_e = math.frexp(m_n / m_d)
    total_e = r_e + e_n - e_d
    if total_e > 900:
        return -cap if (allow_negative and m_q < 0.0) else cap
    if total_e < -1074:
        return 0.0
    val = math.ldexp(m_q, total_e)
    return max(low_cap, min(cap, val)) if math.isfinite(val) else 0.0


def safe_scale_round_int(
    raw: Any,
    scale: float,
    *,
    default: int = 0,
    max_result: Optional[int] = None,
) -> int:
    raw_i = safe_int(raw, default)
    if raw_i <= 0 or isinstance(scale, bool) or not isinstance(scale, (int, float)):
        return 0
    scale_f = float(scale)
    if not math.isfinite(scale_f) or scale_f <= 0.0:
        return 0
    if raw_i.bit_length() <= 1000:
        prod = raw_i * scale_f
        if math.isfinite(prod):
            res = max(0, int(round(prod)))
            return min(res, max(0, int(max_result))) if max_result is not None else res
    mant, exp = math.frexp(scale_f)
    if max_result is not None:
        max_r = max(0, int(max_result))
        if raw_i.bit_length() + exp - 2 > max_r.bit_length():
            return max_r
    mant_i = int(round(mant * (1 << 53)))
    shift = max(0, raw_i.bit_length() - 256)
    num = (raw_i >> shift) * mant_i
    net = shift + exp - 53
    if net >= 0:
        res = num << net
    else:
        rshift = -net
        if rshift > num.bit_length() + 1:
            return 0
        half = 1 << (rshift - 1)
        res = (num + half) >> rshift
    res = max(0, res)
    if max_result is not None:
        return min(res, max(0, int(max_result)))
    return res


def fmt_compact_int(
    value: Any,
    *,
    signed: bool = False,
    grouped: bool = False,
    default: int = 0,
) -> str:
    v = safe_int(value, default)
    av = abs(v)
    if av.bit_length() >= _MAX_FORMAT_INT_BITS:
        sign = "-" if v < 0 else ("+" if signed and v > 0 else "")
        exp = int(av.bit_length() * _LOG10_2)
        return f"{sign}~1e{exp}"
    if signed and grouped:
        return f"{v:+,}"
    if signed:
        return f"{v:+d}"
    if grouped:
        return f"{v:,}"
    return str(v)


def _safe_cost_int(value: Any) -> int:
    return safe_int(value, 0)


def _safe_cost_float(value: Any) -> float:
    return safe_finite_float(value, 0.0)


def _cost_dict(cost: Any) -> dict[str, int]:
    if cost is None:
        return {"pig_iron": 0, "metal": 0, "kryptonite": 0, "spice": 0}
    if isinstance(cost, Mapping):
        return {k: _safe_cost_int(cost.get(k, 0)) for k in ("pig_iron", "metal", "kryptonite", "spice")}
    return {
        "pig_iron": _safe_cost_int(getattr(cost, "pig_iron", 0)),
        "metal": _safe_cost_int(getattr(cost, "metal", 0)),
        "kryptonite": _safe_cost_int(getattr(cost, "kryptonite", 0)),
        "spice": _safe_cost_int(getattr(cost, "spice", 0)),
    }


def _cost_float_dict(cost: Any) -> dict[str, float]:
    if cost is None:
        return {"pig_iron": 0.0, "metal": 0.0, "kryptonite": 0.0, "spice": 0.0}
    if isinstance(cost, Mapping):
        return {k: _safe_cost_float(cost.get(k, 0.0)) for k in ("pig_iron", "metal", "kryptonite", "spice")}
    return {
        "pig_iron": _safe_cost_float(getattr(cost, "pig_iron", 0.0)),
        "metal": _safe_cost_float(getattr(cost, "metal", 0.0)),
        "kryptonite": _safe_cost_float(getattr(cost, "kryptonite", 0.0)),
        "spice": _safe_cost_float(getattr(cost, "spice", 0.0)),
    }


def spice_equivalent(cost_or_res: Any) -> float:
    """Convert any resource bundle to Spice equivalents at fixed Merchant parity."""
    vals = _cost_float_dict(cost_or_res)
    total = (
        vals["pig_iron"] / MERCHANT_RATES_IN_SPICE["pig_iron"]
        + vals["metal"] / MERCHANT_RATES_IN_SPICE["metal"]
        + vals["kryptonite"] / MERCHANT_RATES_IN_SPICE["kryptonite"]
        + vals["spice"] / MERCHANT_RATES_IN_SPICE["spice"]
    )
    return total if math.isfinite(total) else 0.0


def building_level_stats(building_id_or_slug: int | str, level: int) -> dict[str, Any]:
    """Return wiki table row for `(building, level)`, or `{}` if `level <= 0` or out of range."""
    lvl = safe_int(level, 0)
    if lvl <= 0 or lvl > 10_000:
        return {}
    info = resolve_unit("buildings", building_id_or_slug)
    if not info:
        return {}
    return info.get("levels", {}).get(lvl, {})


def building_cost(building_id_or_slug: int | str, target_level: int) -> dict[str, int]:
    """Return wiki resource cost dict for upgrading `building` to `target_level`."""
    row = building_level_stats(building_id_or_slug, target_level)
    cost = row.get("cost")
    if isinstance(cost, dict):
        return dict(cost)
    return {"pig_iron": 0, "metal": 0, "kryptonite": 0, "spice": 0}


def interpolate_temp_value(temp_map: Mapping[int, int], temperature: int | float) -> float:
    """Linearly interpolate a wiki temperature table for an arbitrary planet temperature."""
    if not temp_map or not isinstance(temp_map, Mapping):
        return 0.0
    temp = safe_finite_float(temperature, 0.0)
    pts_raw: list[tuple[int, float]] = []
    for k, v in temp_map.items():
        ki = safe_optional_int(k, None)
        if ki is None:
            continue
        pts_raw.append((cap_json_int(ki, limit=10**12), safe_finite_float(v, 0.0)))
    if not pts_raw:
        return 0.0
    pts = sorted(pts_raw)
    if temp <= pts[0][0]:
        return pts[0][1]
    if temp >= pts[-1][0]:
        return pts[-1][1]
    for (t0, v0), (t1, v1) in zip(pts, pts[1:]):
        if t0 <= temp <= t1:
            if t1 == t0:
                return v0
            ratio = (temp - t0) / (t1 - t0)
            return v0 + ratio * (v1 - v0)
    return pts[-1][1]


def mine_production_per_hour(
    building_id: int,
    level: int,
    *,
    temperature: int = 0,
    mining_tech_level: int = 0,
) -> float:
    """Return hourly base output for mines (`b1..b4`), including temp curve & Mining tech (+2%/lvl).

    Blast furnace (`b2`) converts Pig iron to Metal and is not boosted by Mining technology.
    """
    lvl = safe_int(level, 0)
    bid = safe_int(building_id, 0)
    if lvl <= 0:
        return 0.0
    row = building_level_stats(bid, lvl)
    if not row:
        return 0.0
    if "prod_by_temp" in row and row["prod_by_temp"]:
        base = interpolate_temp_value(row["prod_by_temp"], temperature)
    else:
        base = float(row.get("prod", 0))
    safe_tech = max(0, min(100_000, _safe_cost_int(mining_tech_level)))
    if bid in (1, 3, 4) and safe_tech > 0:
        base *= 1.0 + MINING_TECH_BONUS_PER_LEVEL * safe_tech
    return base


def blast_furnace_pig_iron_used(level: int) -> float:
    """Return hourly Pig iron consumed by Blast furnace (`b2`) at `level`."""
    lvl = safe_int(level, 0)
    if lvl <= 0:
        return 0.0
    return float(building_level_stats(2, lvl).get("pig_iron_used", 0))


def mine_energy_needed(building_id_or_slug: int | str, level: int) -> int:
    """Return positive energy consumed by `building` at `level` (0 for power plants)."""
    lvl = safe_int(level, 0)
    if lvl <= 0:
        return 0
    val = int(building_level_stats(building_id_or_slug, lvl).get("energy", 0))
    return -val if val < 0 else 0


def building_energy_delta(building_id_or_slug: int | str, target_level: int) -> int:
    """Signed net energy change when upgrading `building` from `target_level - 1` to `target_level`."""
    tgt = safe_int(target_level, 0)
    if tgt <= 0:
        return 0
    cur_energy = int(building_level_stats(building_id_or_slug, tgt - 1).get("energy", 0))
    nxt_energy = int(building_level_stats(building_id_or_slug, tgt).get("energy", 0))
    return nxt_energy - cur_energy


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
        raw = b_map.get(f"b{bid}", b_map.get(str(bid), b_map.get(req_slug, 0)))
        return max(0, _safe_cost_int(raw))
    if req_cat == "research":
        info = RESEARCHES.get(req_slug)
        if info and info.get("id"):
            rid = str(info["id"])
            if rid in r_map:
                return max(0, _safe_cost_int(r_map[rid]))
        for k, v in r_map.items():
            if not isinstance(k, str):
                continue
            if normalize_unit_key(k) == normalize_unit_key(req_slug):
                return max(0, _safe_cost_int(v))
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
'''


def render_module(payload: dict[str, Any]) -> str:
    header = '''"""Offline SpaceInvasion Classic Wiki catalog and economy/tech-tree helpers.

Auto-generated by `scripts/sync_wiki_data.py` from official wiki pages.
Do not hand-edit data tables; re-run `scripts/sync_wiki_data.py` to refresh.
"""

from __future__ import annotations

from collections.abc import Mapping
import math
import os
from typing import Any, Optional

'''
    body = [
        header,
        f"WIKI_SYNCED_AT_UTC = {payload['synced_at_utc']!r}\n",
        f"WIKI_SOURCE_URL = {payload['source']!r}\n",
        "MINING_TECH_BONUS_PER_LEVEL = 0.02\n",
        f"MERCHANT_RATES_IN_SPICE: dict[str, float] = {pprint.pformat(payload['merchant_rates_in_spice'], width=100, sort_dicts=False)}\n",
        f"\nBUILDINGS: dict[str, dict[str, Any]] = {pprint.pformat(payload['buildings'], width=110, sort_dicts=False)}\n",
        f"\nRESEARCHES: dict[str, dict[str, Any]] = {pprint.pformat(payload['researches'], width=110, sort_dicts=False)}\n",
        f"\nSHIPS: dict[str, dict[str, Any]] = {pprint.pformat(payload['ships'], width=110, sort_dicts=False)}\n",
        f"\nDEFENSES: dict[str, dict[str, Any]] = {pprint.pformat(payload['defenses'], width=110, sort_dicts=False)}\n",
        _HELPER_CODE,
    ]
    return "\n".join(body)


def write_catalog_tables(payload: dict[str, Any], tables_dir: Path = OUTPUT_TABLES_DIR) -> None:
    """Write scraped SpaceInvasion Classic tables into ``spaceinvasion/core/catalog/tables/*.json``."""
    tables_dir.mkdir(parents=True, exist_ok=True)
    for filename, key in (
        ("buildings.json", "buildings"),
        ("research.json", "researches"),
        ("ships.json", "ships"),
        ("defenses.json", "defenses"),
    ):
        (tables_dir / filename).write_text(
            json.dumps(payload[key], indent=2, ensure_ascii=False, allow_nan=False) + "\n", encoding="utf-8"
        )


def main() -> int:
    parser = argparse.ArgumentParser(description="Sync SpaceInvasion Classic Wiki data into catalog tables JSON")
    parser.add_argument("--output-dir", type=Path, default=OUTPUT_TABLES_DIR)
    args = parser.parse_args()
    payload = scrape_wiki()
    write_catalog_tables(payload, args.output_dir)
    print(
        f"Wrote {args.output_dir}: {len(payload['buildings'])} buildings, {len(payload['researches'])} researches, {len(payload['ships'])} ships, {len(payload['defenses'])} defenses"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
