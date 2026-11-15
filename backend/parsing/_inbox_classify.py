"""Folder-link extraction and category-flag classification for ``backend.parsing.inbox``."""

from __future__ import annotations

from collections.abc import Collection
import re

from bs4 import BeautifulSoup, Tag

from backend.core.coercion import parse_game_int
from backend.core.redaction import redact_text
from backend.parsing._common import attr_str, tag_text

_RECYCLE_EXPLICIT_TOKENS = (
    "recycling report",
    "trümmerfeldbericht",
    "truemmerfeldbericht",
    "space rubble",
    "trümmerfeld abgebaut",
    "couldn't find any more resources",
    "could not find any more resources",
    "pieces of rubble",
)
_NON_RECYCLE_NOTICE_TOKENS = (
    "was re-stationed",
    "re-stationed",
    "restationed",
    "has been stationed",
    "wurde stationiert",
    "umstationiert",
    "stationiert",
    "it delivers",
    "has delivered",
    "delivered resources",
    "fleet return",
    "flottenrückkehr",
    "flottenrueckkehr",
    "returning from duty",
    "were unloaded",
    "wurden ausgeladen",
    "espionage report",
    "spionagebericht",
    "spy report",
)
_COUNTER_SPY_TOKENS = (
    "foreign fleet",
    "fremde flotte",
    "counter-espionage",
    "spionageabwehr",
    "spied on",
    "ausspioniert",
)
_FLEET_RETURN_TOKENS = (
    "fleet return",
    "flottenrückkehr",
    "flottenrueckkehr",
    "returning from duty",
    "were unloaded",
    "wurden ausgeladen",
)
_FLEET_STATION_TOKENS = (
    "was re-stationed",
    "re-stationed",
    "restationed",
    "has been stationed",
    "wurde stationiert",
    "umstationiert",
    "stationiert",
    "it delivers",
    "has delivered",
)
_BATTLE_TOKENS = (
    "battle report",
    "combat report",
    "kampfbericht",
    "attacker",
    "angreifer",
    "plundered",
    "looted",
    "erbeutet",
    "debris field",
    "trümmerfeld",
    "truemmerfeld",
)
_RECYCLE_TOKENS = (
    *_RECYCLE_EXPLICIT_TOKENS,
    "harvested",
    "recycled",
    "processed",
    "abbau",
    "verwertet",
)
_SPY_HEADER_TOKENS = ("espionage report", "spionagebericht", "spy report")
_SPY_MARKERS = (*_SPY_HEADER_TOKENS, "uninhabited", "unbewohnt")
_RESOURCE_WORDS = ("pig iron", "roheisen", "metal", "metall", "kryptonite", "kryptonit", "spice")


def _parse_folder_counts(txt: str, primary_txt: str = "") -> tuple[int, int]:
    m_slash = re.search(r"(\d[\d.]*)\s*/\s*(\d[\d.]*)", primary_txt or txt, re.ASCII)
    if not m_slash and primary_txt:
        m_slash = re.search(r"(\d[\d.]*)\s*/\s*(\d[\d.]*)", txt, re.ASCII)
    if m_slash:
        return parse_game_int(m_slash.group(1)), parse_game_int(m_slash.group(2))
    m_par = re.search(r"\(\s*(\d[\d.]*)\s*\)", primary_txt or txt, re.ASCII)
    if m_par:
        unread = parse_game_int(m_par.group(1))
        return unread, unread
    nums = [parse_game_int(m.group(1)) for m in re.finditer(r"\b(\d[\d.]*)\b", txt, re.ASCII)]
    if len(nums) >= 2:
        return nums[0], nums[1]
    if len(nums) == 1:
        return nums[0], nums[0]
    return 0, 0


def extract_message_folder_links(html: str) -> list[tuple[str, str, int, int]]:
    """Extract ``(folder_type, href, unread_count, total_count)`` links from ``internalMessages`` or ``F_Navi``."""
    soup = BeautifulSoup(html or "", "html.parser")
    found: dict[str, tuple[str, str, int, int]] = {}

    for anchor in soup.find_all("a", href=True):
        if not isinstance(anchor, Tag):
            continue
        href = attr_str(anchor, "href").strip()
        if "action=internalmessages" not in href.lower():
            continue
        m_type = re.search(r"[?&](?:type|cat|category)=([a-zA-Z0-9_-]+)", href)
        if not m_type:
            continue
        ftype = m_type.group(1).lower()
        row = anchor.find_parent("tr") or anchor.find_parent("li") or anchor
        unread, total = _parse_folder_counts(tag_text(row), tag_text(anchor))
        prev = found.get(ftype)
        if prev is None or (unread, total) > (prev[2], prev[3]):
            found[ftype] = (ftype, redact_text(href), unread, total)

    for box in soup.select("[onclick]"):
        oc = attr_str(box, "onclick").strip()
        if "action=internalmessages" not in oc.lower():
            continue
        m_url = re.search(r"['\"]([^'\"]*action=internalMessages[^'\"]*)['\"]", oc, re.IGNORECASE)
        href = m_url.group(1).strip() if m_url else oc
        m_type = re.search(r"[?&](?:type|cat|category)=([a-zA-Z0-9_-]+)", href, re.IGNORECASE)
        if not m_type:
            continue
        ftype = m_type.group(1).lower()
        info_el = box.find(id=f"msg-{ftype}") or box.select_one(".info")
        info_txt = tag_text(info_el) if isinstance(info_el, Tag) else ""
        unread, total = _parse_folder_counts(tag_text(box), info_txt)
        prev = found.get(ftype)
        if prev is None or (unread, total) > (prev[2], prev[3]):
            found[ftype] = (ftype, redact_text(href), unread, total)

    for ftype in ("normal", "alliance", "spio", "battle", "fleet", "admin"):
        if ftype in found:
            continue
        cnt_el = soup.find(id=f"msg-{ftype}")
        if isinstance(cnt_el, Tag):
            cnt_txt = tag_text(cnt_el)
            unread, total = _parse_folder_counts(cnt_txt, cnt_txt)
            found[ftype] = (ftype, f"indexInternal.es?action=internalMessages&type={ftype}", unread, total)
    return list(found.values())


def is_non_recycle_fleet_notice_text(text: str, subject: str = "") -> bool:
    """Return True for stationing/transport/return/spy notices mentioning 'Recycler'."""
    combined = f"{(subject or '').strip().lower()} {(text or '').strip().lower()}".strip()
    if not combined or any(tok in combined for tok in _RECYCLE_EXPLICIT_TOKENS):
        return False
    if any(tok in combined for tok in _NON_RECYCLE_NOTICE_TOKENS):
        return True
    subj_low = (subject or "").strip().lower()
    return bool(re.search(r"\]\s*station\b", combined) or re.search(r"\bstation\s*$", subj_low))


def detect_inbox_flags(
    *,
    low: str,
    subj_low: str,
    body_for_parse: str,
    subject: str,
    f_low: str,
    norm_coords: str,
    exclude_coords: Collection[str],
    has_battle_attr: bool,
) -> tuple[bool, bool, bool, bool, bool, bool]:
    """Return ``(is_spy, is_counter_spy, is_battle, is_recycle, is_fleet_station, is_fleet_return)``."""
    has_spy_hdr = any(tok in low or tok in subj_low for tok in _SPY_HEADER_TOKENS)
    is_counter_spy = any(tok in low or tok in subj_low for tok in _COUNTER_SPY_TOKENS) and not has_spy_hdr
    is_fleet_return = (
        any(tok in subj_low or tok in low for tok in _FLEET_RETURN_TOKENS)
        and "recycling report" not in subj_low
        and "trümmerfeldbericht" not in subj_low
    )
    is_fleet_station = (
        not is_fleet_return
        and not any(tok in subj_low or tok in low for tok in _RECYCLE_EXPLICIT_TOKENS[:4])
        and (
            any(tok in subj_low or tok in low for tok in _FLEET_STATION_TOKENS)
            or bool(re.search(r"\]\s*station\b", f"{subj_low} {low}"))
            or bool(re.search(r"\bstation\s*$", subj_low))
        )
    )
    is_battle = f_low == "battle" or has_battle_attr or any(tok in low or tok in subj_low for tok in _BATTLE_TOKENS)
    is_recycle = (
        not is_battle
        and not is_fleet_return
        and not is_fleet_station
        and not is_non_recycle_fleet_notice_text(body_for_parse, subject)
        and any(tok in low or tok in subj_low for tok in _RECYCLE_TOKENS)
        and not has_spy_hdr
    )
    is_spy = (
        not is_counter_spy
        and not is_battle
        and not is_recycle
        and not is_fleet_return
        and not is_fleet_station
        and (
            f_low == "spio"
            or any(tok in low or tok in subj_low for tok in _SPY_MARKERS)
            or (
                bool(norm_coords)
                and norm_coords not in exclude_coords
                and sum(1 for rk in _RESOURCE_WORDS if rk in low) >= 2
            )
            or bool(re.search(r"\b[0-9]{2,3}\s*(?:building\s*|bau)?(?:fields|felder)\b", low))
        )
    )
    return is_spy, is_counter_spy, is_battle, is_recycle, is_fleet_station, is_fleet_return
