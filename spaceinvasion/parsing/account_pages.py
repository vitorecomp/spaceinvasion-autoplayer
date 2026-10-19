"""Parsers for ``internalTransmitter``, Daily Login Bonus, and Universe Research Records."""

from __future__ import annotations

from collections.abc import Mapping
import json
import re

from bs4 import BeautifulSoup, Tag

from spaceinvasion.core.coercion import cap_json_int, coerce_int
from spaceinvasion.parsing._common import attr_opt_str, attr_str, safe_text, tag_text
from spaceinvasion.parsing.models import JsonObject

_IDT_FREE_SPINS_JS_RE = re.compile(r"\b_freeSpins\s*=\s*(\d+)", re.ASCII)
_IDT_CHARGES_TEXT_RE = re.compile(r"\b(?:charges|ladungen|free\s*spins?|freidrehs?)\s*:\s*(\d+)", re.IGNORECASE)
_IDT_AUTO_ADD_SPIN_RE = re.compile(r"\bautoAddSpin\s*:\s*true\b", re.IGNORECASE)
_IDT_FREE_LABEL_TOKENS: tuple[str, ...] = ("free", "gratis", "kostenlos", "frei")

_DAILY_BONUS_HIDE_RE = re.compile(r"hideloginbonus\s*=\s*1", re.IGNORECASE)
_DAILY_BONUS_TRIGGER_RE = re.compile(r"senddailylogin\s*\(|getloginbonus\s*=\s*1", re.IGNORECASE)
_DAILY_BONUS_DAY_RE = re.compile(
    r"\b(\d{1,5})\s*(?:st|nd|rd|th|\.)?\s+(?:consecutive|aufeinanderfolgende\w*)\s+login\b",
    re.IGNORECASE,
)
_DAILY_BONUS_HIDDEN_STYLE_RE = re.compile(r"(?:^|;)\s*(?:display\s*:\s*none|visibility\s*:\s*hidden)\b", re.IGNORECASE)

_UNIVERSE_RESEARCH_ALIASES: dict[str, str] = {
    "mining technology": "r1",
    "bergbautechnik": "r1",
    "energy technology": "r2",
    "energietechnik": "r2",
    "engine research": "r3",
    "triebwerksforschung": "r3",
    "rocket engine": "r4",
    "raketentriebwerk": "r4",
    "diffusion engine": "r5",
    "diffusionstriebwerk": "r5",
    "warp engine": "r6",
    "warptriebwerk": "r6",
    "spy technology": "r7",
    "spionagetechnik": "r7",
    "weapons technology": "r8",
    "waffentechnik": "r8",
    "shield technology": "r9",
    "schildtechnik": "r9",
    "defense systems": "r10",
    "verteidigungssysteme": "r10",
    "targeting systems": "r11",
    "zielsysteme": "r11",
    "particle analyzer": "r12",
    "partikelanalysator": "r12",
    "graviton technology": "r13",
    "gravitontechnik": "r13",
    "teleportation": "r14",
    "black hole research": "r15",
    "schwarzes-loch-forschung": "r15",
    "schwarzes loch forschung": "r15",
    "urplasma-extraction": "urplasma-extraction",
    "urplasma extraction": "urplasma-extraction",
    "urplasma-extraktion": "urplasma-extraction",
    "urplasma extraktion": "urplasma-extraction",
}


def _empty_transmitter_result() -> JsonObject:
    return {
        "has_form": False,
        "free_spins": 0,
        "charges": 0,
        "idt_src": 1,
        "play_count": 1,
        "auto_add_spin": False,
        "has_urplasma_cost": False,
        "button_label": "",
        "free_src_href": None,
        "needs_src_switch_to_free": False,
        "can_activate_free": False,
        "latest_reward": "",
        "latest_date": "",
    }


def _parse_transmitter_history(soup: BeautifulSoup) -> tuple[str, str]:
    for tbl in soup.find_all("table"):
        if not isinstance(tbl, Tag):
            continue
        rows = [tr for tr in tbl.find_all("tr") if isinstance(tr, Tag)]
        if len(rows) < 2:
            continue
        hdr_txt = tag_text(rows[0]).lower()
        if not any(tok in hdr_txt for tok in ("winning", "gewinn", "free activation", "freidreh")):
            continue
        for tr in rows[1:]:
            cells = [tag_text(td) for td in tr.find_all("td") if isinstance(td, Tag)]
            if len(cells) >= 2 and cells[0] and cells[0] != "---" and cells[1] and cells[1] != "---":
                return cells[0][:64], cells[1][:160]
    return "", ""


def parse_transmitter_page(html: object) -> JsonObject:
    """Parse ``action=internalTransmitter`` HTML with strict zero-Urplasma free-spin safety checks."""
    raw_html = safe_text(html) if html is not None else ""
    if not raw_html:
        return _empty_transmitter_result()

    soup = BeautifulSoup(raw_html, "html.parser")
    tm_form = soup.find(id="tm.form")
    tm_submit = soup.find(id="tm.submit")
    form_tag = tm_form if isinstance(tm_form, Tag) else None
    submit_tag = tm_submit if isinstance(tm_submit, Tag) else None

    m_js_free = _IDT_FREE_SPINS_JS_RE.search(raw_html)
    js_free_spins = cap_json_int(int(m_js_free.group(1))) if m_js_free else None
    m_chg = _IDT_CHARGES_TEXT_RE.search(tag_text(soup))
    charges = cap_json_int(int(m_chg.group(1))) if m_chg else 0
    free_spins = max(0, js_free_spins if js_free_spins is not None else charges)
    auto_add_spin = bool(_IDT_AUTO_ADD_SPIN_RE.search(raw_html))

    idt_src = 1
    play_count = 1
    if form_tag is not None:
        src_inp = form_tag.find("input", attrs={"name": "idtSrc"})
        if isinstance(src_inp, Tag) and src_inp.get("value") is not None:
            idt_src = coerce_int(src_inp.get("value"), 1)
        play_inp = form_tag.find(id="tm.play") or form_tag.find("input", attrs={"name": "play"})
        if isinstance(play_inp, Tag) and play_inp.get("value") is not None:
            play_count = coerce_int(play_inp.get("value"), 1)

    free_src_href: str | None = None
    for anchor in soup.select("a.idt-srcpick__opt[href], a[href*='idtSrc=1']"):
        href_v = attr_str(anchor, "href").strip()
        if "idtSrc=1" in href_v:
            free_src_href = href_v
            break

    btn_text = tag_text(submit_tag) if submit_tag is not None else ""
    has_urplasma_icon = (
        submit_tag is not None
        and submit_tag.select_one(".urplasma-icon-30, .urplasma-base, [class*='urplasma']") is not None
    )
    btn_low = btn_text.lower()
    has_free_label = any(tok in btn_low for tok in _IDT_FREE_LABEL_TOKENS)
    has_urplasma_cost = (
        has_urplasma_icon
        or "urplasma" in btn_low
        or "activate for" in btn_low
        or "aktivieren für" in btn_low
        or "aktivieren fuer" in btn_low
        or idt_src == 3
    )
    latest_date, latest_reward = _parse_transmitter_history(soup)
    return {
        "has_form": form_tag is not None,
        "free_spins": free_spins,
        "charges": max(0, charges),
        "idt_src": idt_src,
        "play_count": play_count,
        "auto_add_spin": auto_add_spin,
        "has_urplasma_cost": has_urplasma_cost,
        "button_label": btn_text[:160],
        "free_src_href": free_src_href,
        "needs_src_switch_to_free": bool(
            form_tag is not None and free_spins >= 1 and idt_src != 1 and free_src_href is not None
        ),
        "can_activate_free": bool(
            form_tag is not None
            and submit_tag is not None
            and free_spins >= 1
            and idt_src == 1
            and play_count == 1
            and not auto_add_spin
            and not has_urplasma_cost
            and has_free_label
        ),
        "latest_reward": latest_reward,
        "latest_date": latest_date,
    }


def _tag_or_ancestor_hidden(tag: Tag) -> bool:
    cur: Tag | None = tag
    while cur is not None and getattr(cur, "name", None):
        if cur.has_attr("hidden"):
            return True
        style = attr_str(cur, "style")
        if style and _DAILY_BONUS_HIDDEN_STYLE_RE.search(style):
            return True
        parent = cur.parent
        cur = parent if isinstance(parent, Tag) else None
    return False


def parse_daily_login_bonus(html: object) -> JsonObject:
    """Parse the Daily Login Bonus popup shown on Command Central (``action=internalHome``)."""
    raw_html = safe_text(html) if html is not None else ""
    if not raw_html:
        return {
            "present": False,
            "button_label": "",
            "has_send_daily_login": False,
            "tpid_options": [],
            "default_tpid": None,
            "consecutive_day": None,
            "grants_free_transmitter": False,
        }

    soup = BeautifulSoup(raw_html, "html.parser")
    trigger_el: Tag | None = None
    for el in soup.find_all(True):
        if not isinstance(el, Tag):
            continue
        onclick = attr_str(el, "onclick")
        href = attr_str(el, "href")
        if _DAILY_BONUS_HIDE_RE.search(onclick) or _DAILY_BONUS_HIDE_RE.search(href):
            continue
        if (
            _DAILY_BONUS_TRIGGER_RE.search(onclick) or _DAILY_BONUS_TRIGGER_RE.search(href)
        ) and not _tag_or_ancestor_hidden(el):
            trigger_el = el
            break

    tpid_options: list[int] = []
    default_tpid: int | None = None
    tpid_select = soup.find("select", attrs={"id": "tpid"}) or soup.find("select", attrs={"name": "tpid"})
    if isinstance(tpid_select, Tag):
        for opt in tpid_select.find_all("option"):
            if not isinstance(opt, Tag):
                continue
            val_s = (attr_opt_str(opt, "value") or "").strip()
            if val_s.isascii() and val_s.isdigit() and len(val_s) <= 18:
                pid_i = cap_json_int(int(val_s))
                if pid_i > 0:
                    if pid_i not in tpid_options:
                        tpid_options.append(pid_i)
                    if opt.has_attr("selected") and default_tpid is None:
                        default_tpid = pid_i
        if default_tpid is None and tpid_options:
            default_tpid = tpid_options[0]

    has_send_fn = bool(
        re.search(r"\bsendDailyLogin\s*=", raw_html, re.IGNORECASE)
        or (trigger_el is not None and "senddailylogin" in attr_str(trigger_el, "onclick").lower())
    )
    present = trigger_el is not None
    body_text = tag_text(soup)
    m_day = _DAILY_BONUS_DAY_RE.search(body_text)
    consecutive_day = cap_json_int(int(m_day.group(1))) if m_day else None
    low_body = body_text.lower()
    low_html = raw_html.lower()
    grants_free_transmitter = present and (
        "transmitter" in low_body or "freidreh" in low_body or "payment/transmitter.png" in low_html
    )
    return {
        "present": present,
        "button_label": tag_text(trigger_el)[:120] if trigger_el is not None else "",
        "has_send_daily_login": has_send_fn,
        "tpid_options": list(tpid_options),
        "default_tpid": default_tpid,
        "consecutive_day": consecutive_day,
        "grants_free_transmitter": grants_free_transmitter,
    }


def _record_research_max(out: dict[str, JsonObject], rkey: str, lvl: int, holder: str = "", coords: str = "") -> None:
    if lvl <= 0 or lvl > 500:
        return
    prev = out.get(rkey)
    prev_lvl = prev.get("level") if prev is not None else None
    if prev is None or (isinstance(prev_lvl, int) and lvl > prev_lvl):
        out[rkey] = {"level": int(lvl), "holder": holder.strip()[:80], "coords": coords.strip()[:24]}


def _parse_records_from_json(soup: BeautifulSoup, out: dict[str, JsonObject]) -> None:
    for jid in ("json-element", "json-container"):
        jel = soup.find(id=jid)
        if not isinstance(jel, Tag):
            continue
        jtxt = (jel.string if jel.string is not None else jel.get_text("", strip=True)) or ""
        if not jtxt.strip():
            continue
        try:
            jdata: object = json.loads(jtxt)
        except (ValueError, TypeError):
            jdata = None
        if not isinstance(jdata, Mapping):
            continue
        rec_sec = jdata.get("records") or jdata.get("research_records") or jdata.get("researches")
        if not isinstance(rec_sec, Mapping):
            continue
        for rk, rv in rec_sec.items():
            rk_s = str(rk).strip().lower()
            canon_k = _UNIVERSE_RESEARCH_ALIASES.get(rk_s, rk_s if re.fullmatch(r"r\d{1,2}", rk_s) else "")
            if not canon_k:
                continue
            if isinstance(rv, Mapping):
                lvl_v = coerce_int(rv.get("level", rv.get("max", 0)), 0)
                _record_research_max(
                    out,
                    canon_k,
                    lvl_v,
                    str(rv.get("holder") or rv.get("player") or rv.get("uname") or ""),
                    str(rv.get("coords") or ""),
                )
            elif isinstance(rv, int) and not isinstance(rv, bool):
                _record_research_max(out, canon_k, rv)


def parse_universe_research_records(html: object) -> dict[str, JsonObject]:
    """Parse Universe Records / Top Research Levels HTML into ``{rkey: {level, holder, coords}}``."""
    raw_html = safe_text(html) if html is not None else ""
    if not raw_html or "researchid=" in raw_html.lower():
        return {}
    soup = BeautifulSoup(raw_html, "html.parser")
    out: dict[str, JsonObject] = {}
    _parse_records_from_json(soup, out)

    for tr in soup.find_all("tr"):
        if not isinstance(tr, Tag):
            continue
        cells = [tag_text(c) for c in tr.find_all(["td", "th"], recursive=False) if isinstance(c, Tag)]
        if len(cells) < 2:
            continue
        matched_key = ""
        matched_idx = -1
        for idx, ctxt in enumerate(cells):
            low_c = re.sub(r"\s*\(\s*(?:level|stufe)\s*\d+\s*\)\s*$", "", ctxt.lower()).strip().rstrip(":")
            if low_c in _UNIVERSE_RESEARCH_ALIASES:
                matched_key = _UNIVERSE_RESEARCH_ALIASES[low_c]
                matched_idx = idx
                break
        if not matched_key:
            continue
        lvl_found = 0
        holder_found = ""
        coords_found = ""
        m_inline_lvl = re.search(r"(?:level|stufe)\s*(\d{1,3})\b", cells[matched_idx], re.IGNORECASE | re.ASCII)
        if m_inline_lvl:
            lvl_found = int(m_inline_lvl.group(1))
        for idx, ctxt in enumerate(cells):
            if idx == matched_idx or not ctxt:
                continue
            m_c = re.search(r"(\d{1,2}:\d{1,3}:\d{1,2})", ctxt, re.ASCII)
            if m_c and not coords_found:
                coords_found = m_c.group(1)
            m_l = re.search(r"^(?:level|stufe|lvl\.?)?\s*(\d{1,3})$", ctxt.strip(), re.IGNORECASE | re.ASCII)
            if m_l and lvl_found <= 0:
                lvl_found = int(m_l.group(1))
                continue
            m_l2 = re.search(r"\b(?:level|stufe)\s*(\d{1,3})\b", ctxt, re.IGNORECASE | re.ASCII)
            if m_l2 and lvl_found <= 0:
                lvl_found = int(m_l2.group(1))
                continue
            clean_h = re.sub(r"\[\s*\d{1,2}:\d{1,3}:\d{1,2}\s*\]", "", ctxt).strip()
            if clean_h and clean_h not in ("-", "---", "—") and not clean_h.isdigit() and not holder_found:
                holder_found = clean_h
        if lvl_found > 0:
            _record_research_max(out, matched_key, lvl_found, holder_found, coords_found)
    return out
