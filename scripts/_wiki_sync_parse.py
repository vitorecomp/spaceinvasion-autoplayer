"""HTML/table parsing and HTTP fetch helpers for `scripts/sync_wiki_data.py`."""

from __future__ import annotations

import json
import math
from pathlib import Path
import re
import sys
from typing import Any
from urllib.parse import urljoin

from bs4 import BeautifulSoup, Tag
import requests

_SCRIPTS_DIR = str(Path(__file__).resolve().parent)
if _SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, _SCRIPTS_DIR)

from _wiki_sync_constants import (  # noqa: E402
    _COST_HEADER_TO_KEY,
    _DT_STAT_TO_KEY,
    _RES_CLASS_TO_KEY,
    _SUFFIX_MULTIPLIERS,
)
from _wiki_sync_constants import (  # noqa: E402
    BUILDING_ID_BY_SLUG as BUILDING_ID_BY_SLUG,
)
from _wiki_sync_constants import (  # noqa: E402
    MERCHANT_URL as MERCHANT_URL,
)
from _wiki_sync_constants import (  # noqa: E402
    RESEARCH_ID_BY_SLUG as RESEARCH_ID_BY_SLUG,
)
from _wiki_sync_constants import (  # noqa: E402
    WIKI_INDEX_URL as WIKI_INDEX_URL,
)


def _tag_classes(tag: Tag) -> list[str]:
    raw: Any = tag.get("class")
    if isinstance(raw, list):
        return [str(item) for item in raw]
    if isinstance(raw, str):
        return [raw]
    return []


def _parse_int(raw: str) -> int:
    text = raw.strip().lstrip("+")
    neg = text.startswith("-")
    m = re.match(r"^-?\s*([\d,]+(?:\.\d+)?)\s*(qi|qa|t|b|m|k)?\b", text, re.IGNORECASE | re.ASCII)
    if m and m.group(2):
        try:
            num = float(m.group(1).replace(",", ""))
        except (ValueError, OverflowError):
            return 0
        if not math.isfinite(num):
            return 0
        prod = num * _SUFFIX_MULTIPLIERS[m.group(2).lower()]
        if not math.isfinite(prod):
            return 0
        val = min(int(round(prod)), 10**200)
        return -val if neg else val
    digits = re.sub(r"[^\d]", "", text, flags=re.ASCII)
    if not digits:
        return 0
    trimmed = digits.lstrip("0") or "0"
    val = 10**200 if len(trimmed) > 4000 else min(int(trimmed), 10**200)
    return -val if neg else val


def _parse_seconds(raw: str) -> float:
    cleaned = raw.strip().replace(",", "")
    m = re.search(r"[\d]+(?:\.\d+)?", cleaned, re.ASCII)
    if not m:
        return 0.0
    try:
        val = float(m.group(0))
    except (ValueError, OverflowError):
        return 0.0
    return round(val, 4) if math.isfinite(val) else 0.0


def _parse_float_pct(raw: str) -> float:
    m = re.search(r"\d+(?:\.\d+)?", raw.replace(",", "."), re.ASCII)
    if not m:
        return 0.0
    try:
        val = float(m.group(0))
    except (ValueError, OverflowError):
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
        classes_s = _tag_classes(sib)
        ul = sib if sib.name == "ul" and "w-costs" in classes_s else sib.find("ul", class_="w-costs")
        if not isinstance(ul, Tag):
            continue
        for li in ul.find_all("li"):
            if not isinstance(li, Tag):
                continue
            classes = _tag_classes(li)
            res_key = next((_RES_CLASS_TO_KEY[c] for c in classes if c in _RES_CLASS_TO_KEY), None)
            if not res_key:
                continue
            b = li.find("b")
            if isinstance(b, Tag) and b.get("data-n"):
                costs[res_key] = _parse_int(str(b["data-n"]))
            elif isinstance(b, Tag):
                costs[res_key] = _parse_int(b.get_text())
    return costs


def _parse_deps(siblings: list[Tag]) -> list[dict[str, Any]]:
    deps: list[dict[str, Any]] = []
    for sib in siblings:
        classes_s = _tag_classes(sib)
        ul = sib if sib.name == "ul" and "w-deps" in classes_s else sib.find("ul", class_="w-deps")
        if not isinstance(ul, Tag):
            continue
        for li in ul.find_all("li"):
            if not isinstance(li, Tag):
                continue
            a = li.find("a", href=True)
            if not isinstance(a, Tag):
                continue
            href = str(a["href"])
            parts = [p for p in href.strip("/").split("/") if p]
            category = parts[-2] if len(parts) >= 2 else ""
            slug = parts[-1] if parts else ""
            lvl_span = li.find("span", class_="w-lvl")
            lvl = _parse_int(lvl_span.get_text()) if isinstance(lvl_span, Tag) else 1
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
        if isinstance(p, Tag) and p.get("data-t"):
            base_s = _parse_seconds(str(p["data-t"]))
            if p.get("data-f"):
                factors["formula"] = str(p["data-f"])
        for sel in sib.find_all("select"):
            if isinstance(sel, Tag) and (ctrl := sel.get("data-w-ctrl")):
                factors["control"] = str(ctrl)
    return base_s, factors


def _parse_prod_temps(td: Tag) -> dict[int, int]:
    raw_attr = td.get("data-temps")
    if not raw_attr:
        return {}
    try:
        raw_temps = json.loads(str(raw_attr))
    except (ValueError, TypeError):
        return {}
    if not isinstance(raw_temps, dict):
        return {}
    return {_parse_int(str(k)): _parse_int(str(v)) for k, v in raw_temps.items()}


def _parse_level_row(headers: list[str], tds: list[Tag]) -> dict[str, Any]:
    cost: dict[str, int] = {"pig_iron": 0, "metal": 0, "kryptonite": 0, "spice": 0}
    entry: dict[str, Any] = {"cost": cost}
    for hdr, td in zip(headers[1:], tds, strict=True):
        if hdr in _COST_HEADER_TO_KEY:
            cost[_COST_HEADER_TO_KEY[hdr]] = _parse_int(td.get_text())
        elif hdr in ("Build time", "Research time"):
            if td.get("data-t") is not None:
                entry["base_time_s"] = _parse_seconds(str(td["data-t"]))
        elif hdr.endswith("/ hour") and "used" not in hdr:
            raw_v = td.get("data-v")
            entry["prod"] = _parse_int(str(raw_v)) if raw_v is not None else _parse_int(td.get_text())
            if prod_by_temp := _parse_prod_temps(td):
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
    return entry


def _parse_level_table(table: Tag) -> tuple[dict[int, dict[str, Any]], dict[int, int]]:
    thead = table.find("thead")
    if not isinstance(thead, Tag):
        return {}, {}
    headers = [th.get_text(" ", strip=True) for th in thead.find_all("th")]
    tbody = table.find("tbody") or table
    if not isinstance(tbody, Tag):
        return {}, {}
    if headers == ["Planet temperature", "Energy per solar cell"]:
        temp_map: dict[int, int] = {}
        for tr in tbody.find_all("tr"):
            if not isinstance(tr, Tag):
                continue
            cells = tr.find_all(["th", "td"])
            if len(cells) >= 2:
                temp_map[_parse_int(cells[0].get_text())] = _parse_int(cells[1].get_text())
        return {}, temp_map
    if not headers or headers[0] != "Level":
        return {}, {}

    levels: dict[int, dict[str, Any]] = {}
    for tr in tbody.find_all("tr"):
        if not isinstance(tr, Tag):
            continue
        th = tr.find("th")
        tds = [td for td in tr.find_all("td") if isinstance(td, Tag)]
        if not isinstance(th, Tag) or len(tds) + 1 != len(headers):
            continue
        lvl = _parse_int(th.get_text())
        if lvl > 0:
            levels[lvl] = _parse_level_row(headers, tds)
    return levels, {}


def _parse_stats_dl(soup: BeautifulSoup) -> dict[str, Any]:
    stats: dict[str, Any] = {}
    for dt in soup.find_all("dt"):
        if not isinstance(dt, Tag):
            continue
        key = _DT_STAT_TO_KEY.get(dt.get_text(" ", strip=True))
        if not key:
            continue
        dd = dt.find_next_sibling("dd")
        if not isinstance(dd, Tag):
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
    except (ValueError, OverflowError):
        return defaults
    return rates if all(math.isfinite(v) and v > 0.0 for v in rates.values()) else defaults


def _discover_unit_urls(session: requests.Session) -> list[tuple[str, str, str]]:
    resp = session.get(WIKI_INDEX_URL, timeout=20)
    resp.raise_for_status()
    soup = BeautifulSoup(resp.text, "html.parser")
    seen: set[tuple[str, str]] = set()
    units: list[tuple[str, str, str]] = []
    for a in soup.find_all("a", href=True):
        if not isinstance(a, Tag):
            continue
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


def _parse_unit_page(category: str, slug: str, url: str, html: str) -> dict[str, Any] | None:
    soup = BeautifulSoup(html, "html.parser")
    if not any("fact sheet" in h2.get_text(" ", strip=True).lower() for h2 in soup.find_all("h2")):
        return None
    name = _clean_title(soup, slug)
    badge_texts = [p.get_text(" ", strip=True).lower() for p in soup.find_all("p", class_="w-badge")]
    asteroid_ok = any("asteroids" in t for t in badge_texts)
    cost_siblings = _h3_siblings(soup, "Cost of level 1") or _h3_siblings(soup, "Costs")
    levels: dict[int, dict[str, Any]] = {}
    solar_temp_map: dict[int, int] = {}
    for tbl in soup.find_all("table"):
        if not isinstance(tbl, Tag):
            continue
        parsed_levels, parsed_temp = _parse_level_table(tbl)
        if parsed_levels:
            levels = parsed_levels
        if parsed_temp:
            solar_temp_map = parsed_temp

    common: dict[str, Any] = {
        "slug": slug,
        "name": name,
        "url": url,
        "base_cost": _parse_costs(cost_siblings),
        "requirements": _parse_deps(_h3_siblings(soup, "Requirements")),
        "required_for": _parse_deps(_h3_siblings(soup, "Required for")),
    }
    if category == "buildings":
        common["id"] = BUILDING_ID_BY_SLUG.get(slug)
        common["asteroid_ok"] = asteroid_ok
        common["max_table_level"] = max(levels) if levels else 0
        common["levels"] = levels
    elif category == "research":
        common["id"] = RESEARCH_ID_BY_SLUG.get(slug)
        common["max_table_level"] = max(levels) if levels else 0
        common["levels"] = levels
    else:
        base_s, factors = _parse_unit_build_time(_h3_siblings(soup, "Build time per unit"))
        common["base_build_time_s"] = base_s
        common["build_time_factors"] = factors
        common["stats"] = _parse_stats_dl(soup)
        if category == "ships" and solar_temp_map:
            common["solar_cell_energy_by_temp"] = solar_temp_map
    return common
