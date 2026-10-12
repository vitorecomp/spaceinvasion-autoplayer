"""Parsers for construction queues (``internalBuildings``) and research queue timers (``internalResearch``)."""

from __future__ import annotations

import re
import time

from bs4 import BeautifulSoup, Tag

from spaceinvasion.core.coercion import coerce_bounded_int, coerce_finite_float
from spaceinvasion.core.coords import normalize_coord_triple
from spaceinvasion.core.state import BUILDING_ID_BY_NAME, QueueJob
from spaceinvasion.parsing._common import (
    HMS_TIMER_RE,
    clean_queue_item_name,
    format_hms_seconds,
    parse_hms_seconds,
    tag_text,
)

_DATE_STAMP_RE = re.compile(r"\b\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2}:\d{2}\b")
_LEVEL_ENTRY_RE = re.compile(r"([A-Za-z][A-Za-z ]+?)\s*\(\s*Level\s+(\d+)\s*\)", re.ASCII)


def _parse_queue_row(
    row_text: str,
    *,
    slot_num: int,
    top_timer_txt: str,
    top_pct_txt: str,
    cumulative_rem: float | None,
    ref_now: float,
    bname: str,
    lvl: int,
    norm_origin: str,
) -> tuple[QueueJob, float | None]:
    rem_s: float | None = None
    rem_txt = ""
    fin_ts: float | None = None
    if slot_num == 1:
        cand_timer = top_timer_txt
        if not cand_timer:
            m_hms = HMS_TIMER_RE.search(_DATE_STAMP_RE.sub("", row_text))
            if m_hms:
                cand_timer = m_hms.group(0)
        parsed_s = parse_hms_seconds(cand_timer)
        if parsed_s is not None:
            rem_s = parsed_s
            cumulative_rem = parsed_s
            rem_txt = format_hms_seconds(parsed_s)
            fin_ts = round(ref_now + parsed_s, 3)
        detail_txt = f"In construction ({top_pct_txt})" if top_pct_txt else "In construction"
    else:
        dur_s = parse_hms_seconds(_DATE_STAMP_RE.sub("", row_text))
        if dur_s is not None:
            if cumulative_rem is not None:
                cumulative_rem += dur_s
                rem_s = cumulative_rem
                rem_txt = format_hms_seconds(rem_s)
                fin_ts = round(ref_now + rem_s, 3)
            else:
                rem_s = dur_s
                rem_txt = format_hms_seconds(dur_s)
        detail_txt = f"In-game slot #{slot_num}"
    job = QueueJob(
        category="building",
        name=bname,
        building_id=BUILDING_ID_BY_NAME.get(bname.lower()),
        target_level=lvl,
        remaining_text=rem_txt,
        remaining_seconds=rem_s,
        finish_ts=fin_ts,
        queue_slot=slot_num,
        detail=detail_txt,
        origin_coords=norm_origin,
    )
    return job, cumulative_rem


def parse_building_queue(
    html: str,
    *,
    now_ts: float | None = None,
    origin_coords: str = "",
) -> tuple[QueueJob, ...]:
    """Parse active and queued jobs from ``Construction queue``."""
    norm_origin = normalize_coord_triple(origin_coords, strict_bounds=False) if origin_coords else ""
    soup = BeautifulSoup(html, "html.parser")
    queue_table = next(
        (tbl for tbl in soup.find_all("table") if isinstance(tbl, Tag) and "Construction queue" in tag_text(tbl)),
        None,
    )
    if queue_table is None:
        return ()

    ref_now = coerce_finite_float(now_ts, time.time(), overflow_cap=None) if now_ts is not None else time.time()
    jobs: list[QueueJob] = []
    seen: set[tuple[str, int]] = set()
    cumulative_rem: float | None = None

    top_timer_el = queue_table.find(id="sicntbar-1-1")
    top_pct_el = queue_table.find(id="sicntbar-1-2")
    top_timer_txt = tag_text(top_timer_el) if isinstance(top_timer_el, Tag) else ""
    top_pct_txt = tag_text(top_pct_el) if isinstance(top_pct_el, Tag) else ""

    for tr in queue_table.find_all("tr"):
        if not isinstance(tr, Tag) or tr.find_parent("table") is not queue_table:
            continue
        row_text = tag_text(tr)
        if "Construction queue" in row_text:
            continue
        match = _LEVEL_ENTRY_RE.search(row_text)
        if not match:
            continue
        bname = clean_queue_item_name(match.group(1))
        if not bname:
            continue
        lvl = coerce_bounded_int(match.group(2), 0)
        key = (bname.lower(), lvl)
        if key in seen:
            continue
        seen.add(key)
        job, cumulative_rem = _parse_queue_row(
            row_text,
            slot_num=len(jobs) + 1,
            top_timer_txt=top_timer_txt,
            top_pct_txt=top_pct_txt,
            cumulative_rem=cumulative_rem,
            ref_now=ref_now,
            bname=bname,
            lvl=lvl,
            norm_origin=norm_origin,
        )
        jobs.append(job)

    if jobs:
        return tuple(jobs)
    for match in _LEVEL_ENTRY_RE.finditer(queue_table.get_text(" ", strip=True)):
        bname = clean_queue_item_name(match.group(1))
        if not bname:
            continue
        lvl = coerce_bounded_int(match.group(2), 0)
        key = (bname.lower(), lvl)
        if key in seen:
            continue
        seen.add(key)
        jobs.append(
            QueueJob(
                category="building",
                name=bname,
                building_id=BUILDING_ID_BY_NAME.get(bname.lower()),
                target_level=lvl,
                queue_slot=len(jobs) + 1,
                origin_coords=norm_origin,
            )
        )
    return tuple(jobs)


def parse_research_queue_timer(
    html: str,
    *,
    now_ts: float | None = None,
) -> tuple[str, float | None, float | None, str]:
    """Extract ``(remaining_text, remaining_seconds, finish_ts, detail)`` from ``internalResearch``."""
    if not html:
        return ("", None, None, "")
    soup = BeautifulSoup(html, "html.parser")
    t_el = soup.find(id="sicntbar-1-1")
    p_el = soup.find(id="sicntbar-1-2")
    t_txt = tag_text(t_el) if isinstance(t_el, Tag) else ""
    p_txt = tag_text(p_el) if isinstance(p_el, Tag) else ""
    rem_s = parse_hms_seconds(t_txt)
    detail = f"Researching ({p_txt})" if p_txt else "Researching"
    if rem_s is None:
        return ("", None, None, detail)
    ref_now = coerce_finite_float(now_ts, time.time(), overflow_cap=None) if now_ts is not None else time.time()
    return (format_hms_seconds(rem_s), rem_s, round(ref_now + rem_s, 3), detail)
