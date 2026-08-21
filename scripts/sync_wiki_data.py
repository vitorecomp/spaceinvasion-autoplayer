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
from pathlib import Path
import pprint
import re
from typing import Any
from urllib.parse import urljoin

from bs4 import BeautifulSoup, Tag
import requests

WIKI_INDEX_URL = "https://spaceinvasion.bitmeup.com/wiki/en/"
MERCHANT_URL = "https://spaceinvasion.bitmeup.com/wiki/en/economy/merchant"
OUTPUT_PATH = Path(__file__).resolve().parent.parent / "spaceinvasion" / "game_data.py"

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


def _parse_int(raw: str) -> int:
    text = raw.strip().lstrip("+")
    neg = text.startswith("-")
    digits = re.sub(r"[^\d]", "", text)
    if not digits:
        return 0
    val = int(digits)
    return -val if neg else val


def _parse_float_pct(raw: str) -> float:
    m = re.search(r"[\d.]+", raw.replace(",", "."))
    return float(m.group(0)) if m else 0.0


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


def _parse_unit_build_time(siblings: list[Tag]) -> tuple[int, dict[str, str]]:
    base_s = 0
    factors: dict[str, str] = {}
    for sib in siblings:
        p = sib.find("p", class_="w-big") if sib.name != "p" else sib
        if p and p.get("data-t"):
            base_s = _parse_int(str(p["data-t"]))
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
                    entry["base_time_s"] = _parse_int(str(td["data-t"]))
            elif hdr.endswith("/ hour") and "used" not in hdr:
                raw_v = td.get("data-v")
                entry["prod"] = _parse_int(str(raw_v)) if raw_v is not None else _parse_int(td.get_text())
                if td.get("data-temps"):
                    raw_temps = json.loads(str(td["data-temps"]))
                    entry["prod_by_temp"] = {int(k): int(v) for k, v in raw_temps.items()}
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
    )
    if not m:
        return defaults
    return {
        "pig_iron": float(m.group(1)),
        "metal": float(m.group(2)),
        "kryptonite": float(m.group(3)),
        "spice": float(m.group(4)),
    }


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
    session = requests.Session()
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
            if _cat == "buildings" and str(_uid).isdigit():
                _UNIT_ALIAS_INDEX[f"b{_uid}"] = (_cat, _slug)


def resolve_unit(category: Optional[str], identifier: str | int) -> Optional[dict[str, Any]]:
    """Look up a unit dict by slug, numeric building ID, `'b1'`/`'r1'` key, or display name."""
    if isinstance(identifier, int) or (isinstance(identifier, str) and identifier.isdigit()):
        b_info = BUILDING_BY_ID.get(int(identifier))
        if b_info is not None and category in (None, "buildings"):
            return b_info
    key = str(identifier).strip()
    hit = _UNIT_ALIAS_INDEX.get(key) or _UNIT_ALIAS_INDEX.get(normalize_unit_key(key))
    if not hit:
        return None
    found_cat, slug = hit
    if category is not None and found_cat != category:
        return None
    return {"buildings": BUILDINGS, "research": RESEARCHES, "ships": SHIPS, "defense": DEFENSES}[found_cat].get(slug)


def _cost_dict(cost: Any) -> dict[str, int]:
    if isinstance(cost, Mapping):
        return {k: int(cost.get(k, 0)) for k in ("pig_iron", "metal", "kryptonite", "spice")}
    return {
        "pig_iron": int(getattr(cost, "pig_iron", 0)),
        "metal": int(getattr(cost, "metal", 0)),
        "kryptonite": int(getattr(cost, "kryptonite", 0)),
        "spice": int(getattr(cost, "spice", 0)),
    }


def spice_equivalent(cost_or_res: Any) -> float:
    """Convert any resource bundle to Spice equivalents at fixed Merchant parity."""
    vals = _cost_dict(cost_or_res)
    return (
        vals["pig_iron"] / MERCHANT_RATES_IN_SPICE["pig_iron"]
        + vals["metal"] / MERCHANT_RATES_IN_SPICE["metal"]
        + vals["kryptonite"] / MERCHANT_RATES_IN_SPICE["kryptonite"]
        + vals["spice"] / MERCHANT_RATES_IN_SPICE["spice"]
    )


def building_level_stats(building_id_or_slug: int | str, level: int) -> dict[str, Any]:
    """Return wiki table row for `(building, level)`, or `{}` if `level <= 0` or out of range."""
    if level <= 0:
        return {}
    info = resolve_unit("buildings", building_id_or_slug)
    if not info:
        return {}
    return info.get("levels", {}).get(int(level), {})


def building_cost(building_id_or_slug: int | str, target_level: int) -> dict[str, int]:
    """Return wiki resource cost dict for upgrading `building` to `target_level`."""
    row = building_level_stats(building_id_or_slug, target_level)
    cost = row.get("cost")
    if isinstance(cost, dict):
        return dict(cost)
    return {"pig_iron": 0, "metal": 0, "kryptonite": 0, "spice": 0}


def interpolate_temp_value(temp_map: Mapping[int, int], temperature: int) -> float:
    """Linearly interpolate a wiki temperature table for an arbitrary planet temperature."""
    if not temp_map:
        return 0.0
    pts = sorted((int(k), float(v)) for k, v in temp_map.items())
    if temperature <= pts[0][0]:
        return pts[0][1]
    if temperature >= pts[-1][0]:
        return pts[-1][1]
    for (t0, v0), (t1, v1) in zip(pts, pts[1:]):
        if t0 <= temperature <= t1:
            if t1 == t0:
                return v0
            ratio = (temperature - t0) / (t1 - t0)
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
    if level <= 0:
        return 0.0
    row = building_level_stats(building_id, level)
    if not row:
        return 0.0
    if "prod_by_temp" in row and row["prod_by_temp"]:
        base = interpolate_temp_value(row["prod_by_temp"], temperature)
    else:
        base = float(row.get("prod", 0))
    if building_id in (1, 3, 4) and mining_tech_level > 0:
        base *= 1.0 + MINING_TECH_BONUS_PER_LEVEL * mining_tech_level
    return base


def blast_furnace_pig_iron_used(level: int) -> float:
    """Return hourly Pig iron consumed by Blast furnace (`b2`) at `level`."""
    if level <= 0:
        return 0.0
    return float(building_level_stats(2, level).get("pig_iron_used", 0))


def mine_energy_needed(building_id_or_slug: int | str, level: int) -> int:
    """Return positive energy consumed by `building` at `level` (0 for power plants)."""
    if level <= 0:
        return 0
    val = int(building_level_stats(building_id_or_slug, level).get("energy", 0))
    return -val if val < 0 else 0


def building_energy_delta(building_id_or_slug: int | str, target_level: int) -> int:
    """Signed net energy change when upgrading `building` from `target_level - 1` to `target_level`."""
    if target_level <= 0:
        return 0
    cur_energy = int(building_level_stats(building_id_or_slug, target_level - 1).get("energy", 0))
    nxt_energy = int(building_level_stats(building_id_or_slug, target_level).get("energy", 0))
    return nxt_energy - cur_energy


def _current_level_for_req(
    req_cat: str,
    req_slug: str,
    buildings: Mapping[str, int],
    researches: Mapping[str, int],
) -> int:
    if req_cat == "buildings":
        info = BUILDINGS.get(req_slug)
        if not info or info.get("id") is None:
            return 0
        bid = int(info["id"])
        return int(buildings.get(f"b{bid}", buildings.get(str(bid), buildings.get(req_slug, 0))))
    if req_cat == "research":
        info = RESEARCHES.get(req_slug)
        if info and info.get("id"):
            rid = str(info["id"])
            if rid in researches:
                return int(researches[rid])
        for k, v in researches.items():
            if normalize_unit_key(str(k)) == normalize_unit_key(req_slug):
                return int(v)
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


def main() -> int:
    parser = argparse.ArgumentParser(description="Sync SpaceInvasion Classic Wiki data into spaceinvasion/game_data.py")
    parser.add_argument("--output", type=Path, default=OUTPUT_PATH)
    args = parser.parse_args()

    payload = scrape_wiki()
    module_src = render_module(payload)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(module_src, encoding="utf-8")
    print(
        f"Wrote {args.output} ({len(module_src)} bytes): "
        f"{len(payload['buildings'])} buildings, "
        f"{len(payload['researches'])} researches, "
        f"{len(payload['ships'])} ships, "
        f"{len(payload['defenses'])} defenses"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
