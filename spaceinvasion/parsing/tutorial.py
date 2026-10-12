"""Parser for the SpaceInvasion Classic tutorial banner on ``internalHome`` / ``internalBuildings``."""

from __future__ import annotations

from collections.abc import Mapping
import re

from bs4 import BeautifulSoup, Tag

from spaceinvasion.core import game_data
from spaceinvasion.core.coercion import coerce_bounded_int
from spaceinvasion.core.state import BUILDING_ID_BY_NAME, TutorialStatus, TutorialTask
from spaceinvasion.parsing._common import collapse_ws, tag_text
from spaceinvasion.parsing.models import SHIP_SLUG_BY_KEY


def _parse_tut_counter(raw_cnt: str) -> int:
    txt = raw_cnt.strip().replace(".", "")
    return coerce_bounded_int(txt, 0)


def _unit_singular_plural_variants(raw_name: str) -> list[str]:
    clean = collapse_ws(raw_name).strip()
    if not clean:
        return []
    variants: list[str] = [clean]
    low = clean.lower()
    if low.endswith("ies") and len(clean) > 3:
        variants.append(f"{clean[:-3]}y")
    if low.endswith("s") and len(clean) > 2:
        variants.append(clean[:-1])
    if low.endswith("es") and len(clean) > 3:
        variants.append(clean[:-2])
    if not low.endswith("s"):
        variants.append(f"{clean}s")
    dedup: list[str] = []
    for cand in variants:
        if cand and cand not in dedup:
            dedup.append(cand)
    return dedup


def _resolve_catalog_unit(category: str, raw_name: str) -> Mapping[str, object] | None:
    for cand in _unit_singular_plural_variants(raw_name):
        unit = game_data.resolve_unit(category, cand)
        if isinstance(unit, Mapping):
            return unit
    return None


def _resolve_ship_id(slug_or_name: str) -> int | None:
    unit = _resolve_catalog_unit("ships", slug_or_name)
    if unit is None:
        return None
    slug = str(unit.get("slug") or "")
    for skey, sslug in SHIP_SLUG_BY_KEY.items():
        if sslug == slug and skey.startswith("s") and skey[1:].isascii() and skey[1:].isdigit():
            return int(skey[1:])
    return None


def _resolve_building_name_and_id(raw_name: str) -> tuple[str | None, int | None]:
    for cand in _unit_singular_plural_variants(raw_name):
        bid = BUILDING_ID_BY_NAME.get(cand.lower())
        if bid is not None:
            binfo = game_data.BUILDING_BY_ID.get(bid)
            bname = str(binfo.get("name") or cand) if isinstance(binfo, Mapping) else cand
            return bname, bid
        unit = game_data.resolve_unit("buildings", cand)
        if isinstance(unit, Mapping) and unit.get("id") is not None:
            return str(unit.get("name") or cand), int(unit["id"])
    return None, None


def _classify_task(label: str, cur: int, tgt: int) -> TutorialTask:
    desc = f"{label} {cur} / {tgt}"
    inc_m = re.match(r"Increase your\s+([A-Za-z -]+?)\s+level$", label, re.IGNORECASE)
    if inc_m:
        bname = collapse_ws(inc_m.group(1))
        canon_bname, bid = _resolve_building_name_and_id(bname)
        return TutorialTask(
            description=desc,
            building_name=canon_bname or bname,
            building_id=bid,
            current=cur,
            target=tgt,
            category="building",
        )
    upg_m = re.match(r"(?:Upgrade|Research)\s+(?:your\s+)?([A-Za-z -]+?)(?:\s+level)?$", label, re.IGNORECASE)
    if upg_m:
        rname = collapse_ws(upg_m.group(1))
        unit = _resolve_catalog_unit("research", rname)
        if unit is not None or label.lower().startswith("upgrade your "):
            rid = str(unit["id"]) if unit is not None and unit.get("id") else None
            return TutorialTask(
                description=desc,
                building_name=str(unit["name"]) if unit is not None and unit.get("name") else rname,
                building_id=None,
                current=cur,
                target=tgt,
                category="research",
                research_id=rid,
            )
    bld_m = re.match(
        r"(?:Build|Construct|Produce)\s+(?:(?:an?|\d+)\s+)?([A-Za-z][A-Za-z -]*?)(?:\s+level)?$",
        label,
        re.IGNORECASE,
    )
    if bld_m:
        uname = collapse_ws(bld_m.group(1))
        ship_unit = _resolve_catalog_unit("ships", uname)
        if ship_unit is not None:
            return TutorialTask(
                description=desc,
                building_name=str(ship_unit["name"]),
                building_id=_resolve_ship_id(str(ship_unit["slug"])),
                current=cur,
                target=tgt,
                category="ship",
            )
        canon_bname, bid = _resolve_building_name_and_id(uname)
        if bid is not None:
            return TutorialTask(
                description=desc,
                building_name=canon_bname or uname,
                building_id=bid,
                current=cur,
                target=tgt,
                category="building",
            )
    return TutorialTask(
        description=desc,
        building_name=label,
        building_id=None,
        current=cur,
        target=tgt,
        category="milestone",
    )


def _extract_regex_tasks(full_text: str) -> list[TutorialTask]:
    tasks: list[TutorialTask] = []
    for pat in (
        r"(Increase your\s+([A-Za-z -]+?)\s+level)\s+(\d+(?:\.\d{3})*)\s*/\s*(\d+(?:\.\d{3})*)",
        r"(Upgrade your\s+([A-Za-z -]+?))\s+(\d+(?:\.\d{3})*)\s*/\s*(\d+(?:\.\d{3})*)",
    ):
        for match in re.finditer(pat, full_text, re.IGNORECASE | re.ASCII):
            tasks.append(
                _classify_task(
                    collapse_ws(match.group(1)),
                    _parse_tut_counter(match.group(3)),
                    _parse_tut_counter(match.group(4)),
                )
            )
    build_pat = r"\b((?:Build|Construct|Produce)\s+(?:(?:an?|\d+)\s+)?([A-Za-z][A-Za-z -]{1,45}?))\s+(\d+(?:\.\d{3})*)\s*/\s*(\d+(?:\.\d{3})*)"
    for match in re.finditer(build_pat, full_text, re.IGNORECASE | re.ASCII):
        uname = collapse_ws(match.group(2))
        if (
            _resolve_catalog_unit("ships", uname) is not None
            or _resolve_building_name_and_id(uname)[1] is not None
            or _resolve_catalog_unit("defenses", uname) is not None
        ):
            tasks.append(
                _classify_task(
                    collapse_ws(match.group(1)),
                    _parse_tut_counter(match.group(3)),
                    _parse_tut_counter(match.group(4)),
                )
            )
    return tasks


def _extract_div_tasks(tut_div: Tag) -> list[TutorialTask]:
    tasks: list[TutorialTask] = []
    for tr in tut_div.find_all("tr"):
        if not isinstance(tr, Tag) or tr.find("tr") is not None:
            continue
        tds = [td for td in tr.find_all("td", recursive=False) if isinstance(td, Tag)]
        if len(tds) < 2:
            continue
        cnt_m = re.match(r"^(\d+(?:\.\d{3})*)\s*/\s*(\d+(?:\.\d{3})*)$", tag_text(tds[-1]), re.ASCII)
        if not cnt_m:
            continue
        label = " ".join(part for part in (tag_text(td) for td in tds[:-1]) if part).strip()
        if label and not re.match(r"^Tutorial\s+\d+", label, re.IGNORECASE | re.ASCII):
            tasks.append(_classify_task(label, _parse_tut_counter(cnt_m.group(1)), _parse_tut_counter(cnt_m.group(2))))

    for raw_line in tut_div.get_text("\n", strip=True).splitlines():
        line = collapse_ws(raw_line).strip()
        if not line:
            continue
        low_line = line.lower()
        if low_line.startswith("tutorial ") or "you receive the following" in low_line:
            continue
        line_m = re.match(
            r"^([A-Za-z][A-Za-z0-9 ()'+-]{2,80}?)\s+(\d+(?:\.\d{3})*)\s*/\s*(\d+(?:\.\d{3})*)$",
            line,
            re.ASCII,
        )
        if line_m:
            lbl = line_m.group(1).strip()
            if lbl and not lbl.lower().startswith("tutorial"):
                tasks.append(
                    _classify_task(lbl, _parse_tut_counter(line_m.group(2)), _parse_tut_counter(line_m.group(3)))
                )
    return tasks


def parse_tutorial_from_home(html: str) -> TutorialStatus | None:
    """Parse tutorial banner step and task lines from ``internalHome`` or ``internalBuildings``."""
    soup = BeautifulSoup(html, "html.parser")
    full_text = tag_text(soup)
    step_m = re.search(r"Tutorial\s+(\d+)\s*/\s*(\d+)", full_text, re.ASCII)
    if not step_m:
        return None
    step = coerce_bounded_int(step_m.group(1), 0)
    total = coerce_bounded_int(step_m.group(2), 0)
    tut_div = soup.find(id="tutorial.text")
    tut_text = tag_text(tut_div) if isinstance(tut_div, Tag) else full_text

    tasks = _extract_regex_tasks(full_text)
    if isinstance(tut_div, Tag):
        tasks.extend(_extract_div_tasks(tut_div))

    deduped: list[TutorialTask] = []
    seen_desc: set[str] = set()
    for task in tasks:
        key = task.description.lower()
        if key not in seen_desc:
            seen_desc.add(key)
            deduped.append(task)

    reward_m = re.search(r"You receive the following resources:\s*([^\n]+?)(?:$|Active|Pig iron mine)", tut_text)
    reward_text = reward_m.group(1).strip() if reward_m else ""
    can_complete = soup.select_one('input[name="tutorialComplete"]') is not None
    return TutorialStatus(
        step=step,
        total_steps=total,
        tasks=tuple(deduped),
        reward_text=reward_text,
        can_complete=can_complete,
    )
