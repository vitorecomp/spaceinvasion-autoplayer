"""Inbox message multi-dialect parser for ``internalMessages``."""

from __future__ import annotations

from collections.abc import Collection, Iterable
import hashlib
import re

from bs4 import BeautifulSoup, Tag

from backend.core.coords import normalize_coord_triple
from backend.core.redaction import redact_text
from backend.parsing._common import (
    SPY_BRACKETED_COORDS_RE,
    attr_str,
    extract_spy_coords,
    extract_spy_link_coords,
    tag_text,
)
from backend.parsing._inbox_classify import (
    detect_inbox_flags,
    extract_message_folder_links,
    is_non_recycle_fleet_notice_text,
)
from backend.parsing._inbox_reports import (
    extract_resource_quad,
    parse_battle_report_data,
    parse_recycle_report_data,
    spy_report_to_parsed_data,
)
from backend.parsing._msg_dom import (
    MSG_DATE_RE,
    extract_element_msg_id,
    group_table_message_blocks,
    is_folder_counter_node,
    strip_own_planet_header_dom,
)
from backend.parsing.models import InboxMessage, JsonObject, SpyReport
from backend.parsing.spy import parse_spy_messages_html, parse_spy_report_html

_INBOX_INNER_BODY_RE = re.compile(
    r"(?:Message\s+from|Nachricht\s+von)\s+[^:]{1,60}:\s*(.+?)(?=\s*['\"]?\s*(?:Fleet\s+reports|Battle\s+reports|Espionage|System|User|Alliance)\s*[\u2605*]|\s*[\u2605*]\s*Favourites|\s*Received\s+on:\s*From:|$)",
    re.IGNORECASE,
)


def _extract_sender_and_subject(soup: BeautifulSoup) -> tuple[str, str]:
    sender, subject = "", ""
    first_tr = soup.find("tr")
    if isinstance(first_tr, Tag):
        cells: list[str] = []
        for td in first_tr.find_all(["td", "th"], recursive=False):
            if not isinstance(td, Tag):
                continue
            td_clone = BeautifulSoup(str(td), "html.parser")
            for exp_div in td_clone.select(".si-expanded-body"):
                exp_div.decompose()
            cells.append(tag_text(td_clone).strip())
        non_empty = [c for c in cells if c and not MSG_DATE_RE.fullmatch(c)]
        if len(non_empty) >= 2:
            sender, subject = non_empty[0][:120], non_empty[1][:200]
        elif len(non_empty) == 1:
            subject = non_empty[0][:200]
    if not subject:
        hdr_el = soup.select_one(".subject, .msg-subject, .title, .msg-title, b, strong, th")
        if hdr_el is not None:
            subject = tag_text(hdr_el)[:200]
    return sender, subject


def _dispatch_inbox_category(
    *,
    block_html: str,
    body_for_parse: str,
    low: str,
    subj_low: str,
    subject: str,
    f_low: str,
    norm_coords: str,
    exclude_coords: Collection[str],
    body_complete: bool,
    bsum_el: Tag | None,
    battle_json_el: Tag | None,
) -> tuple[str, str, JsonObject, SpyReport | None]:
    is_spy, is_counter_spy, is_battle, is_recycle, is_fleet_station, is_fleet_return = detect_inbox_flags(
        low=low,
        subj_low=subj_low,
        body_for_parse=body_for_parse,
        subject=subject,
        f_low=f_low,
        norm_coords=norm_coords,
        exclude_coords=exclude_coords,
        has_battle_attr=battle_json_el is not None or bsum_el is not None,
    )
    if is_spy:
        rep = parse_spy_report_html(block_html, target_coords=norm_coords or "")
        rep_norm = normalize_coord_triple(rep.target_coords)
        if rep_norm and rep_norm not in exclude_coords:
            has_payload = bool(
                body_complete
                or rep.uninhabited_fields is not None
                or rep.resources.total() > 0
                or rep.buildings
                or rep.fleet
                or rep.defense
                or rep.researches
            )
            return "spy_report", rep_norm, spy_report_to_parsed_data(rep, rep_norm), (rep if has_payload else None)
        return "other", norm_coords, {}, None
    if is_counter_spy:
        return "counter_spy", norm_coords, {"coords": norm_coords}, None
    if is_battle:
        return (
            "battle_report",
            norm_coords,
            parse_battle_report_data(block_html, body_for_parse, low, bsum_el, battle_json_el, norm_coords),
            None,
        )
    if is_recycle:
        upd_coords, rec_data = parse_recycle_report_data(subject, body_for_parse, norm_coords, exclude_coords)
        return "recycle_report", upd_coords, rec_data, None
    if any(tok in low or tok in subj_low for tok in ("coloniz", "kolonisier")):
        return "colonize_report", norm_coords, {"coords": norm_coords}, None
    if is_fleet_station or (
        not is_fleet_return
        and any(tok in low or tok in subj_low for tok in ("transport", "delivered", "geliefert", "liefert"))
    ):
        return (
            "fleet_transport",
            norm_coords,
            {"coords": norm_coords, "resources": {k: v for k, v in extract_resource_quad(body_for_parse).items()}},
            None,
        )
    if (
        is_fleet_return
        or f_low == "fleet"
        or any(
            tok in low or tok in subj_low
            for tok in ("fleet", "flotte", "returned", "zurückgekehrt", "escape flight", "tarnflug")
        )
    ):
        return (
            "fleet_return",
            norm_coords,
            {"coords": norm_coords, "unloaded": {k: v for k, v in extract_resource_quad(body_for_parse).items()}},
            None,
        )
    if f_low in ("normal", "alliance"):
        return "player", norm_coords, {}, None
    if f_low == "admin":
        return "system", norm_coords, {}, None
    return "other", norm_coords, {}, None


def _classify_and_parse_inbox_block(
    block_html: str,
    *,
    folder_type: str = "",
    exclude_coords: Collection[str] = frozenset(),
) -> InboxMessage | None:
    soup = BeautifulSoup(block_html or "", "html.parser")
    text = tag_text(soup).strip()
    if not text or len(text) < 4 or re.fullmatch(r"\d[\d.]*\s*/\s*\d[\d.]*", text):
        return None
    if text.lower() in ("no messages", "keine nachrichten", "delete marked", "markierte löschen", "proceed"):
        return None

    has_expanded = (
        soup.select_one('[data-si-expanded="1"]') is not None or soup.select_one(".si-expanded-body") is not None
    )
    needs_expand = bool(
        soup.select_one(
            '[onclick*="showSpionageReport"], [onclick*="showBattleReport"], [data-bsum], a[href*="action=internalMessages"][href*="read="]'
        )
    )
    body_complete = has_expanded or not needs_expand
    server_msg_id = extract_element_msg_id(soup)
    m_dt = MSG_DATE_RE.search(text)
    msg_date_text = m_dt.group(1).strip() if m_dt else ""
    sender, subject = _extract_sender_and_subject(soup)
    m_inner = _INBOX_INNER_BODY_RE.search(text)
    inner_body = m_inner.group(1).strip() if m_inner else ""
    body_for_parse = inner_body if inner_body else text

    coords_raw = extract_spy_coords(body_for_parse) or extract_spy_coords(text) or extract_spy_link_coords(soup)
    norm_coords = normalize_coord_triple(coords_raw)
    if norm_coords and norm_coords in exclude_coords:
        for m_br in SPY_BRACKETED_COORDS_RE.finditer(body_for_parse):
            cand_n = normalize_coord_triple(f"{m_br.group(1)}:{m_br.group(2)}:{m_br.group(3)}")
            if cand_n and cand_n not in exclude_coords:
                norm_coords = cand_n
                break

    f_low = (folder_type or "").strip().lower()
    category, norm_coords, parsed_data, spy_rep = _dispatch_inbox_category(
        block_html=block_html,
        body_for_parse=body_for_parse,
        low=body_for_parse.lower(),
        subj_low=subject.lower(),
        subject=subject,
        f_low=f_low,
        norm_coords=norm_coords,
        exclude_coords=exclude_coords,
        body_complete=body_complete,
        bsum_el=soup.select_one("[data-bsum]"),
        battle_json_el=soup.select_one("[data-battle-json]"),
    )

    clean_text = redact_text(inner_body if inner_body else text)[:8000]
    clean_html = redact_text(block_html)[:16000]
    clean_sender = redact_text(sender)[:120]
    clean_subject = redact_text(subject)[:240]
    msg_uid = (
        f"id:{server_msg_id}"
        if server_msg_id
        else "sha:"
        + hashlib.sha256(
            f"{f_low}|{category}|{msg_date_text}|{clean_sender}|{clean_subject}|{norm_coords}|{clean_text}".encode(
                "utf-8"
            )
        ).hexdigest()[:24]
    )
    return InboxMessage(
        msg_uid=msg_uid,
        server_msg_id=server_msg_id,
        folder_type=f_low or "inbox",
        category=category,
        msg_date_text=msg_date_text,
        sender=clean_sender,
        subject=clean_subject,
        coords=norm_coords,
        body_text=clean_text,
        raw_html=clean_html,
        parsed_data=parsed_data,
        spy_report=spy_rep,
        body_complete=body_complete,
    )


def parse_inbox_messages_html(
    html: str,
    *,
    folder_type: str = "",
    exclude_coords: Iterable[str] = (),
) -> tuple[InboxMessage, ...]:
    """Parse all messages (espionage, battle, recycle, fleet, system, player) from ``internalMessages`` HTML."""
    soup = BeautifulSoup(html or "", "html.parser")
    strip_own_planet_header_dom(soup)
    excl_norm = {nc for c in (exclude_coords or ()) if (nc := normalize_coord_triple(c))}

    raw_blocks: list[str] = []
    containers = [
        el
        for el in soup.select(
            "div.message, div.msg, tr.message, tr.msg, tr[id^='msg'], div[id^='msg'], [data-msg-id], [data-message-id]"
        )
        if not is_folder_counter_node(el)
    ]
    for el in containers:
        if el.name == "tr":
            nxt = el.find_next_sibling("tr")
            if isinstance(nxt, Tag) and nxt not in containers and not extract_element_msg_id(nxt):
                raw_blocks.append(f"<table>{el}{nxt}</table>")
                continue
        raw_blocks.append(str(el))
    if not raw_blocks:
        raw_blocks.extend(group_table_message_blocks(soup))
    if not raw_blocks:
        for rep in parse_spy_messages_html(str(soup), exclude_coords=excl_norm):
            raw_blocks.append(f"<div class='msg'>{rep.raw_text or rep.target_coords}</div>")

    messages: list[InboxMessage] = []
    seen_uids: set[str] = set()
    for blk in raw_blocks:
        msg = _classify_and_parse_inbox_block(blk, folder_type=folder_type, exclude_coords=excl_norm)
        if msg is None or msg.msg_uid in seen_uids:
            continue
        blk_soup = BeautifulSoup(blk, "html.parser")
        folder_anchors = [
            a
            for a in blk_soup.find_all("a", href=True)
            if isinstance(a, Tag)
            and "action=internalmessages" in attr_str(a, "href").lower()
            and re.search(r"[?&](?:type|cat|category)=", attr_str(a, "href"), re.IGNORECASE)
            and "read=" not in attr_str(a, "href").lower()
        ]
        if folder_anchors and not msg.server_msg_id and msg.spy_report is None and len(msg.body_text) < 120:
            continue
        seen_uids.add(msg.msg_uid)
        messages.append(msg)
    return tuple(messages)


__all__ = [
    "extract_message_folder_links",
    "is_non_recycle_fleet_notice_text",
    "parse_inbox_messages_html",
]
