"""Espionage report parsers (text, HTML, embedded JSON, and multi-report inbox folders)."""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
import re

from bs4 import BeautifulSoup, Tag

from backend.core.coercion import parse_game_int
from backend.core.coords import normalize_coord_triple
from backend.core.model import Resources
from backend.core.redaction import redact_text
from backend.parsing._common import extract_spy_coords, extract_spy_link_coords, tag_text
from backend.parsing._msg_dom import (
    MSG_DATE_RE,
    extract_element_msg_id,
    extract_inner_message_body,
    group_table_message_blocks,
    is_folder_counter_node,
    strip_own_planet_header_dom,
)
from backend.parsing._spy_aliases import (
    EXPLICIT_SPY_REPORT_TOKENS,
    apply_embedded_spy_json,
    extract_embedded_spy_json,
    is_non_spy_notice_text,
    parse_embedded_unit_val,
    parse_text_units_and_tech,
)
from backend.parsing.models import SpyReport

_SPY_KEYWORDS: tuple[str, ...] = (
    "pig iron",
    "roheisen",
    "metal",
    "metall",
    "kryptonite",
    "kryptonit",
    "spice",
    "fields",
    "felder",
    "espionage",
    "spionage",
)


def _extract_spy_owner(text: str, low_text: str, embedded_json: Mapping[str, object] | None) -> tuple[bool, str]:
    is_spacia = "spacia" in low_text
    owner = "Spacia" if is_spacia else ""
    if not owner and embedded_json is not None:
        pl_j = embedded_json.get("planet")
        pl_map: Mapping[object, object] = pl_j if isinstance(pl_j, Mapping) else {}
        for cand_own in (embedded_json.get("player"), pl_map.get("owner"), pl_map.get("player")):
            if isinstance(cand_own, Mapping) and cand_own.get("name"):
                owner = str(cand_own["name"]).strip()[:80]
                break
            if isinstance(cand_own, str) and cand_own.strip():
                owner = cand_own.strip()[:80]
                break
    if not owner:
        m_own = re.search(
            r"(?:Player|Owner|Spieler)\s*:\s*(.+?)(?=\s+(?:Resources|Rohstoffe|Pig\s+iron|Roheisen|Metal|Metall|Kryptonite|Kryptonit|Spice|Fleet|Flotte|Defense|Verteidigung|Buildings|Gebäude|Gebaeude|Research|Forschung|Approx|Dug|Eingegraben|[0-9]{1,2}:[0-9]{1,3}:[0-9]{1,2}|$)|$)",
            text,
            re.IGNORECASE,
        )
        if m_own:
            owner = m_own.group(1).strip(" |:-()'\"")
    return is_spacia, owner


def _parse_spy_fields(text: str, embedded_json: Mapping[str, object] | None) -> int | None:
    m_fld = re.search(
        r"(?:approx\.?|estimated|expected|ca\.?)?\s*([0-9]{2,3})\s*(?:building\s*|bau)?(?:fields|felder)\b",
        text,
        re.IGNORECASE,
    )
    if m_fld:
        return int(m_fld.group(1))
    m_fld_alt = re.search(
        r"(?:building\s*fields|baufelder|planet\s*size|fields|felder)\s*(?::|=)\s*(?:[0-9]{1,3}\s*/\s*)?([0-9]{2,3})\b",
        text,
        re.IGNORECASE,
    )
    if m_fld_alt:
        return int(m_fld_alt.group(1))
    pl_j = embedded_json.get("planet") if embedded_json is not None else None
    if isinstance(pl_j, Mapping):
        for fk in ("maxFields", "max_fields", "fields"):
            val = pl_j.get(fk)
            if val is not None and not isinstance(val, Mapping):
                fv = parse_embedded_unit_val(val)
                if 30 <= fv <= 600:
                    return fv
    return None


def parse_spy_report_html(html: str, *, target_coords: str = "") -> SpyReport:
    """Parse an espionage or uninhabited-ecology report snippet into ``SpyReport``."""
    soup = BeautifulSoup(html, "html.parser")
    server_msg_id = extract_element_msg_id(soup)
    strip_own_planet_header_dom(soup)
    full_text = tag_text(soup)
    embedded_json = extract_embedded_spy_json(full_text)
    text = full_text if embedded_json is not None else extract_inner_message_body(full_text)
    clean_raw_text = redact_text(text)[:4000]

    planet_json = embedded_json.get("planet") if embedded_json is not None else None
    planet_map: Mapping[object, object] | None = planet_json if isinstance(planet_json, Mapping) else None

    coords = target_coords
    if planet_map is not None:
        emb_c = normalize_coord_triple(str(planet_map.get("coords") or ""))
        if emb_c:
            coords = emb_c
    if not coords:
        coords = extract_spy_coords(text) or extract_spy_link_coords(soup)

    m_dt = MSG_DATE_RE.search(text)
    msg_date_text = m_dt.group(1).strip() if m_dt else ""
    planet_name = str(planet_map.get("name") or "").strip()[:60] if planet_map is not None else ""
    if not planet_name:
        m_pl = re.search(
            r"(?:on|auf|for|von|planet)\s+([A-Za-z0-9_.\- ]{1,40}?)\s*\[\s*\d{1,2}:\d{1,3}:\d{1,2}\s*\]",
            text,
            re.IGNORECASE | re.ASCII,
        )
        if m_pl:
            cand_pl = m_pl.group(1).strip(" -:|")
            if cand_pl.lower() not in ("espionage report", "spionagebericht", "report", "planet"):
                planet_name = cand_pl

    def _find_res(label: str) -> int:
        m = re.search(rf"{label}\s*:?\s*([\d.]+)", text, re.IGNORECASE | re.ASCII)
        return parse_game_int(m.group(1)) if m else 0

    res = Resources(
        pig_iron=_find_res(r"(?:Pig\s+iron|Roheisen)"),
        metal=_find_res(r"(?:Metal|Metall)"),
        kryptonite=_find_res(r"(?:Kryptonite|Kryptonit)"),
        spice=_find_res(r"Spice"),
    )
    if planet_map is not None:
        emb_res = planet_map.get("resources")
        if isinstance(emb_res, Sequence) and not isinstance(emb_res, (str, bytes)) and len(emb_res) >= 4:
            res = Resources(
                pig_iron=parse_embedded_unit_val(emb_res[0]),
                metal=parse_embedded_unit_val(emb_res[1]),
                kryptonite=parse_embedded_unit_val(emb_res[2]),
                spice=parse_embedded_unit_val(emb_res[3]),
            )

    low_text = text.lower()
    dug_in: bool | None = None
    if "dug in" in low_text or "dug-in" in low_text:
        dug_in = "not dug in" not in low_text
    elif "eingegraben" in low_text:
        dug_in = "nicht eingegraben" not in low_text

    fleet, defense, buildings, researches = parse_text_units_and_tech(text)
    if embedded_json is not None:
        apply_embedded_spy_json(embedded_json, fleet=fleet, defense=defense, buildings=buildings, researches=researches)
    is_spacia, owner = _extract_spy_owner(text, low_text, embedded_json)
    uninhabited_fields = _parse_spy_fields(text, embedded_json)
    if (
        uninhabited_fields is not None
        and (owner or is_spacia or buildings)
        and not any(tok in low_text for tok in ("uninhabited", "unbewohnt", "free planet", "empty planet"))
    ):
        uninhabited_fields = None

    return SpyReport(
        target_coords=coords,
        owner=owner,
        resources=res,
        fleet=fleet,
        defense=defense,
        dug_in=dug_in,
        uninhabited_fields=uninhabited_fields,
        is_spacia=is_spacia,
        buildings=buildings,
        researches=researches,
        planet_name=planet_name,
        server_msg_id=server_msg_id,
        msg_date_text=msg_date_text,
        raw_text=clean_raw_text,
    )


def _looks_like_spy_block(txt: str, el: Tag | BeautifulSoup | None = None) -> bool:
    inner_txt = extract_inner_message_body(txt)
    low_full = txt.lower()
    low = inner_txt.lower()
    if not any(k in low for k in _SPY_KEYWORDS):
        return False
    has_explicit = any(tok in low or tok in low_full for tok in EXPLICIT_SPY_REPORT_TOKENS)
    if (
        el is not None
        and el.select_one('[onclick*="showSpionageReport"]') is not None
        and el.select_one('[data-si-expanded="1"]') is None
        and el.select_one(".si-expanded-body") is None
        and not any(rk in low for rk in ("pig iron", "roheisen", "kryptonite", "kryptonit", "fields", "felder"))
    ):
        return False
    if (
        any(tok in low for tok in ("foreign fleet", "fremde flotte", "counter-espionage", "spionageabwehr"))
        and not has_explicit
        and not any(rk in low for rk in ("pig iron", "roheisen", "kryptonite", "kryptonit", "fields", "felder"))
    ):
        return False
    if not has_explicit and (is_non_spy_notice_text(inner_txt) or is_non_spy_notice_text(txt)):
        return False
    return bool(
        extract_spy_coords(inner_txt) or extract_spy_coords(txt) or (el is not None and extract_spy_link_coords(el))
    )


def _collect_spy_blocks(soup: BeautifulSoup) -> list[str]:
    blocks: list[str] = []
    containers = [
        el
        for el in soup.select(
            "div.message, div.msg, tr.message, tr.msg, tr[id^='msg'], div[id^='msg'], [data-msg-id], [data-message-id]"
        )
        if not is_folder_counter_node(el)
    ]
    for el in containers:
        combined_html = str(el)
        if el.name == "tr":
            nxt = el.find_next_sibling("tr")
            if isinstance(nxt, Tag) and nxt not in containers and not extract_element_msg_id(nxt):
                combined_html = f"<table>{el}{nxt}</table>"
        comb_soup = BeautifulSoup(combined_html, "html.parser")
        if _looks_like_spy_block(comb_soup.get_text(" ", strip=True), comb_soup):
            blocks.append(combined_html)
    if not blocks:
        for grp_html in group_table_message_blocks(soup):
            grp_soup = BeautifulSoup(grp_html, "html.parser")
            if _looks_like_spy_block(grp_soup.get_text(" ", strip=True), grp_soup):
                blocks.append(grp_html)
    if not blocks:
        candidates = [
            el for el in soup.find_all(["tr", "div", "li"]) if isinstance(el, Tag) and not is_folder_counter_node(el)
        ]
        for el in candidates:
            if not _looks_like_spy_block(el.get_text(" ", strip=True), el):
                continue
            has_child_spy = any(
                isinstance(child, Tag)
                and child is not el
                and _looks_like_spy_block(child.get_text(" ", strip=True), child)
                for child in el.find_all(["tr", "div", "li"])
            )
            if not has_child_spy:
                blocks.append(str(el))
    if not blocks:
        page_txt = soup.get_text(" ", strip=True)
        if any(
            tok in page_txt.lower() for tok in (*EXPLICIT_SPY_REPORT_TOKENS, "uninhabited", "unbewohnt")
        ) and _looks_like_spy_block(page_txt, soup):
            blocks.append(str(soup))
    return blocks


def parse_spy_messages_html(
    html: str,
    *,
    exclude_coords: Iterable[str] = (),
) -> tuple[SpyReport, ...]:
    """Parse multiple espionage reports from ``internalMessages&type=spio`` or main inbox HTML."""
    soup = BeautifulSoup(html or "", "html.parser")
    strip_own_planet_header_dom(soup)
    excl_norm = {nc for c in (exclude_coords or ()) if (nc := normalize_coord_triple(c))}

    reports: list[SpyReport] = []
    seen_keys: set[tuple[str, str]] = set()
    for blk in _collect_spy_blocks(soup):
        rep = parse_spy_report_html(blk)
        if not rep.target_coords or is_non_spy_notice_text(rep.raw_text):
            continue
        norm_c = normalize_coord_triple(rep.target_coords)
        if norm_c and norm_c in excl_norm:
            continue
        dedup_key = (norm_c or rep.target_coords, rep.server_msg_id)
        if dedup_key in seen_keys:
            continue
        seen_keys.add(dedup_key)
        reports.append(rep)
    return tuple(reports)


__all__ = [
    "is_non_spy_notice_text",
    "parse_embedded_unit_val",
    "parse_spy_messages_html",
    "parse_spy_report_html",
]
