"""Galaxy system page parser supporting all 4 DOM dialects plus ``#json-element`` enrichment."""

from __future__ import annotations

import re

from bs4 import BeautifulSoup, Tag

from backend.core.coercion import coerce_int
from backend.parsing._common import attr_str, attr_tokens, tag_text
from backend.parsing._galaxy_status import classify_slot_status, extract_slot_attr_blobs
from backend.parsing.galaxy_coords import (
    ARROW_NAV_MAX_DISTANCE,
    extract_galaxy_coords_from_html,
    extract_galaxy_system_coords,
    extract_galaxy_universe_bounds,
)
from backend.parsing.galaxy_json import merge_json_pos_meta, parse_json_element_pos_meta
from backend.parsing.models import GalaxySlot

_GALAXY_POS_CELL_RE = re.compile(r"^\s*(?:#\s*)?\[?\s*0?(\d{1,2})\s*\]?\.?\s*$", re.ASCII)
_GALAXY_NON_NAME_TOKEN_RE = re.compile(
    r"^(?:debris|rubble|trümmer|truemmer|recycle|spy|spio|espionage|probe|attack|angriff|colonize|message|msg|send\s+message|asteroid|\[a\]|a|-|—)$",
    re.IGNORECASE,
)
_GALAXY_DEBRIS_NUMBER_RE = re.compile(
    r"^[\d.,\s]+(?:k|m)?\s*(?:debris|tf|metal|pig\s*iron)?$", re.IGNORECASE | re.ASCII
)


def _extract_action_href_from_el(el: Tag) -> str:
    href = attr_str(el, "href").strip()
    onclick = attr_str(el, "onclick").strip()
    if href and href not in ("#", "javascript:void(0)", "javascript:void(0);", "javascript:;"):
        return href
    if onclick:
        m_url = re.search(r"['\"]([^'\"]*indexInternal\.es[^'\"]*)['\"]", onclick, re.IGNORECASE)
        return m_url.group(1) if m_url else onclick
    return href


def _extract_slot_links(container: Tag) -> tuple[str | None, str | None]:
    spy_href: str | None = None
    attack_href: str | None = None
    for el in container.find_all(["a", "button", "span", "img"]):
        if not isinstance(el, Tag) or (el.name not in ("a", "button") and not el.get("onclick")):
            continue
        img_alts = " ".join(
            attr_str(img, "alt") or attr_str(img, "title") or attr_str(img, "src")
            for img in el.find_all("img")
            if isinstance(img, Tag)
        )
        blob = (
            f"{attr_str(el, 'href')} {attr_str(el, 'onclick')} {attr_str(el, 'title')} "
            f"{el.get_text(' ', strip=True)} {img_alts}"
        ).lower()
        if spy_href is None and any(
            tok in blob for tok in ("spy", "spio", "espionage", "probe", "s10", "mission=5", "typ=5")
        ):
            spy_href = _extract_action_href_from_el(el) or None
        if attack_href is None and any(tok in blob for tok in ("attack", "angriff", "mission=1", "typ=1")):
            attack_href = _extract_action_href_from_el(el) or None
    return spy_href, attack_href


def _is_non_name_cell(cell: Tag, cell_text: str) -> bool:
    if not cell_text:
        return True
    cls = " ".join(attr_tokens(cell, "class")).lower()
    if any(tok in cls for tok in ("debris", "tf", "action", "actions", "icon", "img", "thumb", "asteroid", "moon")):
        return True
    low = cell_text.strip().lower()
    if _GALAXY_NON_NAME_TOKEN_RE.match(low) or _GALAXY_DEBRIS_NUMBER_RE.match(low):
        return True
    links = [el for el in cell.find_all(["a", "button"]) if isinstance(el, Tag)]
    if links:
        link_texts = [a.get_text(" ", strip=True) for a in links if a.get_text(" ", strip=True)]
        if link_texts and all(_GALAXY_NON_NAME_TOKEN_RE.match(lt.lower()) for lt in link_texts):
            remainder = cell_text
            for lt in link_texts:
                remainder = remainder.replace(lt, " ")
            if not re.sub(r"[\s|/,;\-\[\]()]+", "", remainder):
                return True
    return False


def _match_slot_pos(raw: object) -> int | None:
    if raw is None:
        return None
    match = _GALAXY_POS_CELL_RE.match(str(raw))
    if not match:
        return None
    cand = int(match.group(1))
    return cand if 1 <= cand <= 16 else None


def _build_dom_slot(container: Tag, *, gal: int, sys_num: int, pos: int, planet_name: str, owner: str) -> GalaxySlot:
    row_html = str(container).lower()
    row_text = tag_text(container)
    spy_href, attack_href = _extract_slot_links(container)
    is_spacia = "spacia" in row_text.lower() or "spacia" in row_html
    if is_spacia and planet_name and not owner:
        owner = "Spacia"
    style_attrs, semantic_attrs = extract_slot_attr_blobs(container)
    return GalaxySlot(
        coords=f"{gal:02d}:{sys_num:03d}:{pos:02d}",
        position=pos,
        name=planet_name,
        owner=owner,
        status_color=classify_slot_status(
            occupied=bool(planet_name or owner or spy_href or attack_href),
            is_spacia=is_spacia,
            row_html=row_html,
            row_text=row_text,
            style_attrs=style_attrs,
            semantic_attrs=semantic_attrs,
        ),
        has_debris=any(tok in row_html for tok in ("debris", "trümmer", "truemmer", "rubble", "recycle")),
        is_spacia=is_spacia,
        spy_href=spy_href,
        has_asteroid=bool(
            re.search(r"\[\s*a\s*\]|\basteroid\b", row_text, re.IGNORECASE)
            or container.select(".asteroid, [class*='asteroid']")
        ),
        attack_href=attack_href,
    )


def _parse_table_slots(soup: BeautifulSoup, gal: int, sys_num: int) -> list[GalaxySlot]:
    slots: list[GalaxySlot] = []
    seen_pos: set[int] = set()
    for tr in soup.find_all("tr"):
        if not isinstance(tr, Tag):
            continue
        tds = [td for td in tr.find_all("td", recursive=False) if isinstance(td, Tag)]
        if len(tds) < 2:
            continue
        pos = _match_slot_pos(tr.get("data-pos") or tr.get("data-pla"))
        pos_idx = -1
        if pos is None:
            for idx in range(min(2, len(tds))):
                pos_txt = tds[idx].get_text(" ", strip=True)
                cand = _match_slot_pos(pos_txt)
                if cand is not None:
                    pos, pos_idx = cand, idx
                    break
                if idx == 0 and pos_txt:
                    break
        if pos is None or pos in seen_pos:
            continue
        seen_pos.add(pos)
        data_cells = tds[pos_idx + 1 :] if pos_idx >= 0 else tds
        texts = [txt for cell in data_cells if not _is_non_name_cell(cell, txt := tag_text(cell))]
        slots.append(
            _build_dom_slot(
                tr,
                gal=gal,
                sys_num=sys_num,
                pos=pos,
                planet_name=texts[0] if texts else "",
                owner=texts[1] if len(texts) >= 2 else "",
            )
        )
    return slots


def _parse_modern_card_slots(soup: BeautifulSoup, gal: int, sys_num: int) -> list[GalaxySlot]:
    slots: list[GalaxySlot] = []
    seen_pos: set[int] = set()
    for card in soup.select("[data-pos], [data-pla], .galaxy-row, .galaxy-slot, .galaxy_row, li.slot"):
        if "planet-hit" in attr_tokens(card, "class"):
            continue
        pos = _match_slot_pos(card.get("data-pos") or card.get("data-pla"))
        if pos is None:
            pos_el = card.select_one(".pos, .position, .slot-pos, .gal-pos")
            if pos_el is not None:
                pos = _match_slot_pos(pos_el.get_text(" ", strip=True))
        if pos is None or pos in seen_pos:
            continue
        seen_pos.add(pos)
        p_el = card.select_one(".planet, .planet-name, .planet_name, .slot-planet")
        o_el = card.select_one(".owner, .player, .player-name, .player_name, .slot-owner")
        planet_name = tag_text(p_el) if p_el is not None else ""
        owner = tag_text(o_el) if o_el is not None else ""
        if not planet_name and not owner:
            clean_parts = [
                part
                for ch in card.find_all(["span", "div"], recursive=False)
                if isinstance(ch, Tag)
                and (part := tag_text(ch))
                and not _GALAXY_POS_CELL_RE.match(part)
                and not _GALAXY_NON_NAME_TOKEN_RE.match(part.lower())
            ]
            planet_name = clean_parts[0] if clean_parts else ""
            owner = clean_parts[1] if len(clean_parts) >= 2 else ""
        slots.append(_build_dom_slot(card, gal=gal, sys_num=sys_num, pos=pos, planet_name=planet_name, owner=owner))
    return slots


def _parse_2d_map_slots(soup: BeautifulSoup, gal: int, sys_num: int) -> list[GalaxySlot]:
    slots: list[GalaxySlot] = []
    seen_pos: set[int] = set()
    for hit in soup.select("div.planet-hit"):
        pos: int | None = None
        for div_child in reversed([d for d in hit.find_all("div") if isinstance(d, Tag)]):
            if div_child.find("div") is not None:
                continue
            pos_txt = div_child.get_text(" ", strip=True)
            if pos_txt.isascii() and pos_txt.isdigit() and 1 <= int(pos_txt) <= 16:
                pos = int(pos_txt)
                break
        if pos is None or pos in seen_pos:
            continue
        seen_pos.add(pos)
        spacia_el = hit.select_one(".spacia-planet")
        b_el = hit.find("b")
        name_el = spacia_el if spacia_el is not None else (b_el if isinstance(b_el, Tag) else None)
        occupied = name_el is not None
        planet_name = tag_text(name_el) if name_el is not None else ""
        owner = ""
        span_txt = ""
        if occupied and name_el is not None:
            label_span = name_el.find_parent("span")
            span_txt = tag_text(label_span if isinstance(label_span, Tag) else hit)
            rem_txt = (
                span_txt[len(planet_name) :].strip() if (planet_name and span_txt.startswith(planet_name)) else span_txt
            )
            for m_own in re.finditer(r"\(([^()]+)\)", rem_txt):
                cand_own = m_own.group(1).strip()
                if cand_own and not re.fullmatch(r"i+|[vuns]|go", cand_own, re.IGNORECASE):
                    owner = cand_own
                    break
        is_spacia = spacia_el is not None or owner.lower() == "spacia"
        if is_spacia and planet_name and not owner:
            owner = "Spacia"
        spy_href, attack_href = _extract_slot_links(hit)
        rubble_el = hit.select_one(".rubble-orbit, [name='rubblefield']")
        has_debris = bool(
            rubble_el is not None
            and not rubble_el.has_attr("hidden")
            and not re.search(
                r"display\s*:\s*none|visibility\s*:\s*hidden", attr_str(rubble_el, "style"), re.IGNORECASE
            )
        )
        style_attrs, semantic_attrs = extract_slot_attr_blobs(hit)
        if occupied and re.search(
            r"opacity\s*:\s*0\.6(?![0-9])|0px\s+0px\s+2px\s+#fff(?:fff)?(?![0-9a-f])", style_attrs, re.IGNORECASE
        ):
            status_color = "white"
        else:
            status_color = classify_slot_status(
                occupied=occupied,
                is_spacia=is_spacia,
                row_html=str(hit).lower(),
                row_text=span_txt if occupied else "",
                style_attrs=style_attrs,
                semantic_attrs=semantic_attrs,
            )
        slots.append(
            GalaxySlot(
                coords=f"{gal:02d}:{sys_num:03d}:{pos:02d}",
                position=pos,
                name=planet_name,
                owner=owner,
                status_color=status_color,
                has_debris=has_debris,
                is_spacia=is_spacia,
                spy_href=spy_href,
                has_asteroid=hit.select_one("[name='astro'], [id^='astro-']") is not None,
                attack_href=attack_href,
            )
        )
    return slots


def parse_galaxy_system_html(
    html: str,
    *,
    galaxy: int | None = None,
    system: int | None = None,
    url: str = "",
) -> tuple[GalaxySlot, ...]:
    """Parse galaxy system rows for status colors, Spacia flags, empty slots, and debris."""
    soup = BeautifulSoup(html or "", "html.parser")
    if galaxy is None or system is None:
        det_gal, det_sys = extract_galaxy_system_coords(html, url=url, default=(1, 1))
        gal = det_gal if galaxy is None else coerce_int(galaxy, det_gal)
        sys_num = det_sys if system is None else coerce_int(system, det_sys)
        gal = det_gal if abs(gal) > 999_999 else gal
        sys_num = det_sys if abs(sys_num) > 999_999 else sys_num
    else:
        gal = coerce_int(galaxy, 1)
        sys_num = coerce_int(system, 1)
        gal = 1 if abs(gal) > 999_999 else gal
        sys_num = 1 if abs(sys_num) > 999_999 else sys_num

    slots = _parse_table_slots(soup, gal, sys_num)
    if not slots:
        slots = _parse_modern_card_slots(soup, gal, sys_num)
    if not slots:
        slots = _parse_2d_map_slots(soup, gal, sys_num)
    return tuple(merge_json_pos_meta(slots, parse_json_element_pos_meta(soup), gal, sys_num))


__all__ = [
    "ARROW_NAV_MAX_DISTANCE",
    "extract_galaxy_coords_from_html",
    "extract_galaxy_system_coords",
    "extract_galaxy_universe_bounds",
    "parse_galaxy_system_html",
]
