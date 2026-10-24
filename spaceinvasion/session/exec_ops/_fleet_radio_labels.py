"""BS4 label/row text extraction helpers for `internalSendFleet` mission radio buttons."""

from __future__ import annotations

import re

from bs4 import BeautifulSoup
from bs4.element import NavigableString, Tag

_STRUCTURAL_RADIO_STOP_TAGS: tuple[str, ...] = (
    "br",
    "td",
    "th",
    "div",
    "tr",
    "p",
    "li",
    "hr",
    "table",
    "tbody",
    "thead",
    "tfoot",
    "fieldset",
    "section",
    "form",
    "legend",
    "caption",
    "ul",
    "ol",
    "dl",
    "dt",
    "dd",
    "h1",
    "h2",
    "h3",
    "h4",
    "h5",
    "h6",
)
_NON_LABEL_CONTAINER_TAGS: tuple[str, ...] = (
    "tr",
    "form",
    "table",
    "tbody",
    "thead",
    "tfoot",
    "fieldset",
    "section",
    "ul",
    "ol",
    "dl",
    "[document]",
)
_WRAPPER_WALK_STOP_TAGS: tuple[str, ...] = (
    "br",
    "td",
    "th",
    "tr",
    "p",
    "li",
    "hr",
    "table",
    "tbody",
    "thead",
    "tfoot",
    "fieldset",
    "section",
    "form",
    "legend",
    "caption",
    "ul",
    "ol",
    "dl",
    "dt",
    "dd",
    "h1",
    "h2",
    "h3",
    "h4",
    "h5",
    "h6",
    "[document]",
)
_FORM_CONTROL_TAGS: tuple[str, ...] = ("input", "select", "textarea", "button")
_TR_HEADER_LABEL_RE = re.compile(
    r"^(?:mission|auftrag|speed|geschwindigkeit|route|aktion|action|options?|select|choose)\b",
    re.IGNORECASE,
)
_TR_OPTION_TOKEN_RE = re.compile(
    r"(?:(?<!\d)\d{1,3}\s*%(?!\w)"
    r"|\b(?:attack|angriff|angreif(?:en)?|raid|spy(?:ing)?|spio(?:nage|nier(?:en)?)?|espionage"
    r"|transport(?:ier(?:en)?)?|station(?:ing|ieren)?|deploy|colonize|kolonisier(?:en)?"
    r"|recycl(?:e|ing)|abbau(?:en)?|harvest|escape(?:\s+flight)?|flucht|expedition"
    r"|explore|erkund(?:ung|en)?|hold|halt(?:en)?|escort|defend|direct|direkt|detour|umweg)\b)",
    re.IGNORECASE | re.ASCII,
)
_TR_UTILITY_CELL_RE = re.compile(
    r"^(?:help|hilfe|info|hint|hinweis|note|send|submit|absenden|ok|cancel|abort|abbrechen|back|zur[uü]ck|close|schlie[sß]en)\b|^\?$",
    re.IGNORECASE,
)
_COLON_LABEL_RE = re.compile(r"[A-Za-z]\s*:")


def _is_visible_form_control(tag: Tag) -> bool:
    if tag.name in ("select", "textarea", "button"):
        return True
    if tag.name == "input":
        return str(tag.get("type") or "").strip().lower() != "hidden"
    return False


def _has_visible_form_control(tag: Tag) -> bool:
    if _is_visible_form_control(tag):
        return True
    return any(_is_visible_form_control(c) for c in tag.find_all(_FORM_CONTROL_TAGS))


def _has_other_visible_form_control(container: Tag, target_radio: Tag) -> bool:
    return any(
        ctrl is not target_radio and _is_visible_form_control(ctrl) for ctrl in container.find_all(_FORM_CONTROL_TAGS)
    )


def _collect_adjacent_radio_text(node: Tag, *, reverse: bool = False, target_id: str = "") -> str:
    parts: list[str] = []
    siblings = node.previous_siblings if reverse else node.next_siblings
    for sib in siblings:
        if isinstance(sib, NavigableString):
            txt = " ".join(str(sib).split())
            if txt:
                parts.append(txt)
        elif isinstance(sib, Tag):
            if sib.name in _STRUCTURAL_RADIO_STOP_TAGS:
                break
            if sib.name == "input" and str(sib.get("type") or "").strip().lower() == "hidden":
                continue
            if sib.name == "label":
                lbl_for = str(sib.get("for") or "").strip().lower()
                if lbl_for and lbl_for != target_id:
                    if reverse:
                        parts.clear()
                    break
            if _has_visible_form_control(sib):
                if reverse:
                    parts.clear()
                break
            txt = " ".join(sib.get_text(" ", strip=True).split())
            if txt:
                parts.append(txt)
    if reverse:
        parts.reverse()
    return " ".join(parts)


def _is_tr_header_cell(cell_tag: Tag, txt: str) -> bool:
    if cell_tag.name == "th" or txt.endswith(":"):
        return True
    if _TR_OPTION_TOKEN_RE.search(txt):
        return False
    return bool(_TR_HEADER_LABEL_RE.match(txt))


def _is_tr_option_label(cell_tag: Tag, txt: str) -> bool:
    if not txt or _is_tr_header_cell(cell_tag, txt):
        return False
    if _COLON_LABEL_RE.search(txt) or len(txt.split()) > 5:
        return False
    return bool(_TR_OPTION_TOKEN_RE.search(txt))


def _cell_labels_foreign_id(cell_tag: Tag, target_radio_id: str) -> bool:
    lbls = [lbl for lbl in cell_tag.find_all("label") if str(lbl.get("for") or "").strip()]
    if not lbls:
        return False
    for_ids = {str(lbl.get("for") or "").strip().lower() for lbl in lbls}
    if target_radio_id and target_radio_id in for_ids:
        return False
    lbl_txt = " ".join(" ".join(lbl.get_text(" ", strip=True).split()) for lbl in lbls).strip()
    full_txt = " ".join(cell_tag.get_text(" ", strip=True).split())
    return bool(lbl_txt) and lbl_txt == full_txt


def _is_pure_link_cell(cell_tag: Tag, txt: str) -> bool:
    links = cell_tag.find_all("a")
    if not links:
        return False
    lnk_txt = " ".join(" ".join(a.get_text(" ", strip=True).split()) for a in links).strip()
    return bool(lnk_txt) and lnk_txt == txt


def _extract_multi_radio_tr_cell_label(tr: Tag, cell: Tag) -> str:
    cells = tr.find_all(["td", "th"], recursive=False)
    if cell not in cells and cell.parent is not None:
        cells = cell.parent.find_all(["td", "th"], recursive=False)
    if cell not in cells:
        nxt_td = cell.find_next_sibling(["td", "th"])
        if (
            isinstance(nxt_td, Tag)
            and not nxt_td.select('input[type="radio"]')
            and not _has_visible_form_control(nxt_td)
        ):
            return " ".join(nxt_td.get_text(" ", strip=True).split())
        return ""

    cell_idx = cells.index(cell)
    has_radio = [bool(c.select('input[type="radio"]')) for c in cells]
    cell_txts = [" ".join(c.get_text(" ", strip=True).split()) for c in cells]
    is_label = [
        (not has_radio[i]) and (not _has_visible_form_control(cells[i])) and bool(cell_txts[i])
        for i in range(len(cells))
    ]
    radio_ids: dict[int, str] = {}
    needs_sib: list[int] = []
    for i, c in enumerate(cells):
        radios_in_c = c.select('input[type="radio"]')
        if len(radios_in_c) == 1 and not cell_txts[i]:
            needs_sib.append(i)
            radio_ids[i] = str(radios_in_c[0].get("id") or "").strip().lower()

    def _can_pair(r_idx: int, lbl_idx: int) -> bool:
        if not (0 <= lbl_idx < len(cells)) or not is_label[lbl_idx]:
            return False
        return not _cell_labels_foreign_id(cells[lbl_idx], radio_ids.get(r_idx, ""))

    after_hits = sum(1 for i in needs_sib if _can_pair(i, i + 1))
    before_hits = sum(1 for i in needs_sib if _can_pair(i, i - 1))
    direction = "before" if before_hits > after_hits else "after"
    if before_hits == after_hits and before_hits > 0 and needs_sib:

        def _dir_score(offset: int) -> tuple[int, int, int, int, int]:
            paired = [i + offset for i in needs_sib if _can_pair(i, i + offset)]
            return (
                sum(1 for j in paired if _is_tr_option_label(cells[j], cell_txts[j])),
                -sum(1 for j in paired if _is_tr_header_cell(cells[j], cell_txts[j])),
                -sum(1 for j in paired if _is_pure_link_cell(cells[j], cell_txts[j])),
                -sum(1 for j in paired if cells[j].find("a") is not None),
                -sum(1 for j in paired if _TR_UTILITY_CELL_RE.match(cell_txts[j])),
            )

        direction = "before" if _dir_score(-1) > _dir_score(1) else "after"

    target_idx = cell_idx - 1 if direction == "before" else cell_idx + 1
    return cell_txts[target_idx] if _can_pair(cell_idx, target_idx) else ""


def _extract_table_or_wrapper_label(scoped_form: Tag, inp: Tag, rid: str) -> str:
    cell = inp.find_parent(["td", "th"])
    if cell is not None and scoped_form not in cell.parents:
        cell = None
    tr = cell.find_parent("tr") if cell is not None else None
    if tr is not None and scoped_form not in tr.parents:
        tr = None
    if (
        cell is not None
        and tr is not None
        and not cell.find("table")
        and len(cell.select('input[type="radio"]')) == 1
        and not _has_other_visible_form_control(cell, inp)
    ):
        ctxt = " ".join(cell.get_text(" ", strip=True).split())
        if ctxt:
            return ctxt
        if len(tr.select('input[type="radio"]')) == 1 and not _has_other_visible_form_control(tr, inp):
            return " ".join(tr.get_text(" ", strip=True).split())
        return _extract_multi_radio_tr_cell_label(tr, cell)

    wrapper = inp
    while (
        wrapper.parent is not None
        and isinstance(wrapper.parent, Tag)
        and wrapper.parent is not scoped_form
        and wrapper.parent.name not in _WRAPPER_WALK_STOP_TAGS
        and not _has_other_visible_form_control(wrapper.parent, inp)
        and len(wrapper.parent.select('input[type="radio"]')) == 1
        and not " ".join(wrapper.parent.get_text(" ", strip=True).split())
    ):
        wrapper = wrapper.parent
    parent = wrapper.parent
    if (
        parent is not None
        and isinstance(parent, Tag)
        and parent is not scoped_form
        and parent.name not in _NON_LABEL_CONTAINER_TAGS
        and parent.name not in ("td", "th")
        and len(parent.select('input[type="radio"]')) == 1
        and not _has_other_visible_form_control(parent, inp)
    ):
        ptxt = " ".join(parent.get_text(" ", strip=True).split())
        if ptxt:
            return ptxt
        return _collect_adjacent_radio_text(parent, target_id=rid) or _collect_adjacent_radio_text(
            parent, reverse=True, target_id=rid
        )
    return _collect_adjacent_radio_text(wrapper, target_id=rid) or _collect_adjacent_radio_text(
        wrapper, reverse=True, target_id=rid
    )


def collect_radio_labels(send_soup: BeautifulSoup, form_css: str) -> list[tuple[str, str, str, str, bool]]:
    """Collect `(value, id, name, label_text, disabled)` tuples for each radio inside `form_css`."""
    scoped_form = send_soup.select_one(form_css) if form_css else None
    if scoped_form is None:
        return []
    radio_labels: list[tuple[str, str, str, str, bool]] = []
    for inp in scoped_form.select('input[type="radio"]'):
        raw_id = inp.get("id")
        rid = str(raw_id or "").strip().lower()
        rval = str(inp.get("value") or "").strip().lower()
        rname = str(inp.get("name") or "").strip().lower()
        inp_disabled = inp.has_attr("disabled")
        lbl_parts: list[str] = []
        if rid and isinstance(raw_id, str):
            lbl = scoped_form.find("label", attrs={"for": raw_id})
            if (
                lbl is not None
                and len(lbl.select('input[type="radio"]')) <= 1
                and not _has_other_visible_form_control(lbl, inp)
            ):
                ltxt = " ".join(lbl.get_text(" ", strip=True).split())
                if ltxt:
                    lbl_parts.append(ltxt)
        parent_lbl = inp.find_parent("label")
        if (
            parent_lbl is not None
            and len(parent_lbl.select('input[type="radio"]')) <= 1
            and not _has_other_visible_form_control(parent_lbl, inp)
        ):
            ltxt = " ".join(parent_lbl.get_text(" ", strip=True).split())
            if ltxt:
                lbl_parts.append(ltxt)
        if not lbl_parts:
            fallback_txt = _extract_table_or_wrapper_label(scoped_form, inp, rid)
            if fallback_txt:
                lbl_parts.append(fallback_txt)
        radio_labels.append((rval, rid, rname, " ".join(lbl_parts).lower(), inp_disabled))
    return radio_labels
