"""Message DOM stripping, folder-counter filtering, message-ID extraction, and row grouping."""

from __future__ import annotations

import re

from bs4 import BeautifulSoup, Tag

from backend.parsing._common import attr_str, attr_tokens, extract_spy_coords, tag_text

MSG_DATE_RE = re.compile(
    r"\b(\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2}(?::\d{2})?|\d{1,2}[./]\d{1,2}[./]\d{2,4}\s*,?\s*\d{2}:\d{2}(?::\d{2})?)\b",
    re.ASCII,
)
_MSG_ID_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"^check[_-](\d{1,20})$", re.IGNORECASE | re.ASCII),
    re.compile(r"(?:del|delete|msg|message|id)\s*\[\s*(\d{1,20})\s*\]", re.IGNORECASE | re.ASCII),
    re.compile(
        r"(?:msg[_-]?id|message[_-]?id|del[_-]?id|del|delete|read|reportID|battle|id)\s*=\s*(\d{1,20})\b",
        re.IGNORECASE | re.ASCII,
    ),
    re.compile(
        r"\b(?:deleteMessage|delMsg|showMessage|openMessage|toggleMessage|readMessage|showSpionageReport|showBattleReport)\s*\(\s*['\"]?(\d{1,20})['\"]?",
        re.IGNORECASE | re.ASCII,
    ),
    re.compile(r"^(?:msg|message|row|tr[_-]?msg)[_-]?(\d{1,20})$", re.IGNORECASE | re.ASCII),
)
FOLDER_COUNTER_IDS: frozenset[str] = frozenset(
    "msg-normal msg-alliance msg-spio msg-battle msg-fleet msg-admin msg-outgoing msg-system msg-economy".split()
)
INNER_MESSAGE_BODY_RE = re.compile(
    r"(?:Message\s+from|Nachricht\s+von)\s+[^:]{1,60}:\s*(.+?)(?=\s*['\"]?\s*(?:Fleet\s+reports|Battle\s+reports|Espionage|System|User|Alliance)\s*[\u2605*]|\s*[\u2605*]\s*Favourites|\s*Received\s+on:\s*From:|\s*Empfangen\s+am:|$)",
    re.IGNORECASE,
)


def is_folder_counter_node(el: Tag | BeautifulSoup | None) -> bool:
    if not isinstance(el, Tag):
        return False
    eid = attr_str(el, "id").strip().lower()
    if eid in FOLDER_COUNTER_IDS:
        return True
    cls_tokens = [tok.lower() for tok in attr_tokens(el, "class")]
    return "messagebox" in cls_tokens or (eid.startswith("msg-") and "info" in cls_tokens)


def is_select_all_checkbox(inp: Tag) -> bool:
    nm = attr_str(inp, "name").strip().lower()
    cid = attr_str(inp, "id").strip().lower()
    if nm == "check" or cid == "check":
        return True
    for attr in ("name", "id", "class", "onclick", "onchange", "title"):
        txt = attr_str(inp, attr).lower()
        if any(
            tok in txt for tok in ("allmessages", "checkall", "selectall", "markall", "toggleall", "all_msg", "setall")
        ):
            return True
    return False


def extract_element_msg_id(el: Tag | BeautifulSoup | None) -> str:
    if el is None or is_folder_counter_node(el):
        return ""
    if isinstance(el, Tag):
        for attr in ("data-msg-id", "data-message-id", "data-id", "data-mfav", "id"):
            val = attr_str(el, attr).strip()
            if not val or (attr == "id" and val.lower() in FOLDER_COUNTER_IDS):
                continue
            if attr != "id" and val.isascii() and val.isdigit():
                return val
            for pat in _MSG_ID_PATTERNS:
                m = pat.search(val)
                if m:
                    return m.group(1)
    for cb in el.find_all("input", attrs={"type": re.compile(r"^checkbox$", re.IGNORECASE)}):
        if not isinstance(cb, Tag) or is_select_all_checkbox(cb):
            continue
        for blob in (attr_str(cb, "name").strip(), attr_str(cb, "id").strip()):
            for pat in _MSG_ID_PATTERNS:
                m = pat.search(blob)
                if m:
                    return m.group(1)
        val = attr_str(cb, "value").strip()
        if val.isascii() and val.isdigit() and val not in ("0", "1"):
            return val
    for tag in el.find_all(["span", "a", "button", "input", "tr", "div", "td"]):
        if not isinstance(tag, Tag) or is_folder_counter_node(tag):
            continue
        for attr in ("data-mfav", "data-msg-id", "data-id", "href", "onclick", "id", "name", "data-bsum"):
            val = attr_str(tag, attr).strip()
            if not val:
                continue
            if attr in ("data-mfav", "data-msg-id", "data-id", "data-bsum") and val.isascii() and val.isdigit():
                return val
            for pat in _MSG_ID_PATTERNS:
                m = pat.search(val)
                if m:
                    return m.group(1)
    return ""


def strip_own_planet_header_dom(soup: BeautifulSoup) -> None:
    """Remove ``F_Hauptframe`` top planet/resource/folder header bars."""
    selectors = (
        "#planet-coords",
        "#planet-list",
        "form#ress",
        "#ress",
        "select[name='planet']",
        "#top-bar",
        ".top-bar",
        "#resource-bar",
        ".resource-bar",
        "#infobox",
        ".messagebox",
        "div.info[id^='msg-']",
        "#roheisen",
        "#metall",
        "#kryptonit",
        "#spice",
    )
    for sel in selectors:
        for node in list(soup.select(sel)):
            if sel in ("#roheisen", "#metall", "#kryptonit", "#spice"):
                parent_tbl = node.find_parent("table") or node.find_parent("div")
                if isinstance(parent_tbl, Tag) and not parent_tbl.select(
                    "input[type='checkbox'], div.message, div.msg, tr.message, tr.msg, a[href*='internalMessages']"
                ):
                    txt_low = parent_tbl.get_text(" ", strip=True).lower()
                    if not any(
                        tok in txt_low
                        for tok in ("espionage", "spionage", "battle", "kampfbericht", "player:", "spieler:")
                    ):
                        parent_tbl.decompose()
                        continue
            node.decompose()
    for node in list(soup.select("[id]")):
        if is_folder_counter_node(node):
            node.decompose()
    for form in list(soup.find_all("form")):
        if isinstance(form, Tag) and "newplanet=" in attr_str(form, "action").lower():
            form.decompose()
    for anchor in list(soup.find_all("a", href=True)):
        if isinstance(anchor, Tag) and re.search(
            r"(?:unread\s+messages?|ungelesene\s+nachrichten?)", tag_text(anchor), re.IGNORECASE
        ):
            anchor.decompose()


def group_table_message_blocks(soup: BeautifulSoup) -> list[str]:
    """Group multi-``<tr>`` SpaceInvasion ``internalMessages`` entries."""
    grouped: list[str] = []
    seen_tr_ids: set[int] = set()
    for tbl in soup.find_all("table"):
        if not isinstance(tbl, Tag):
            continue
        tbody = tbl.find("tbody", recursive=False)
        row_parent = tbody if isinstance(tbody, Tag) else tbl
        direct_rows = [tr for tr in row_parent.find_all("tr", recursive=False) if isinstance(tr, Tag)]
        if not direct_rows:
            continue
        header_indices: list[int] = []
        for idx, tr in enumerate(direct_rows):
            if id(tr) in seen_tr_ids:
                continue
            cbs = [
                cb
                for cb in tr.find_all("input", attrs={"type": re.compile(r"^checkbox$", re.IGNORECASE)})
                if isinstance(cb, Tag) and not is_select_all_checkbox(cb)
            ]
            if len(cbs) == 1:
                header_indices.append(idx)
                continue
            tr_id = attr_str(tr, "id")
            tr_cls = attr_tokens(tr, "class")
            if (
                re.match(r"^(?:msg|message)[_-]?\d+$", tr_id, re.IGNORECASE)
                or any(c in ("message", "msg", "msg-head", "msg-header") for c in tr_cls)
                or tr.get("data-msg-id")
                or tr.get("data-message-id")
            ):
                header_indices.append(idx)
        for pos_i, start_idx in enumerate(header_indices):
            end_idx = header_indices[pos_i + 1] if (pos_i + 1) < len(header_indices) else len(direct_rows)
            chunk: list[str] = []
            for r_idx in range(start_idx, end_idx):
                tr = direct_rows[r_idx]
                if r_idx > start_idx and (
                    tr.find("select") is not None
                    or tr.find("input", attrs={"type": re.compile(r"^(?:submit|button)$", re.IGNORECASE)}) is not None
                ):
                    if not extract_spy_coords(tr.get_text(" ", strip=True)):
                        break
                seen_tr_ids.add(id(tr))
                chunk.append(str(tr))
            if chunk:
                grouped.append("<table>" + "".join(chunk) + "</table>")
    return grouped


def extract_inner_message_body(text: str) -> str:
    """Return the inner ``Message from ...:`` body when full frame chrome surrounds a read message."""
    if not text:
        return ""
    match = INNER_MESSAGE_BODY_RE.search(text)
    return match.group(1).strip() if match else text
