"""Parser for ``var faData`` active own/returning fleet flights on ``internalHome``."""

from __future__ import annotations

from dataclasses import dataclass
import re

from bs4 import BeautifulSoup

from spaceinvasion.core.coercion import coerce_bounded_int, parse_game_int
from spaceinvasion.core.coords import normalize_coord_triple
from spaceinvasion.core.model import QueueJob, Resources
from spaceinvasion.parsing._common import collapse_ws, format_hms_seconds, tag_text

_FADATA_BLOCK_RE = re.compile(r"\bvar\s+faData\s*=\s*(\[.*?\])\s*;", re.DOTALL)
_FADATA_MISSION_LABELS: dict[str, str] = {
    "ATTACK": "Attack",
    "RECYCLE": "Recycle",
    "SPIONAGE": "Spy",
    "SAVE": "Escape flight",
    "TRANSPORT": "Transport",
    "STATION": "Station",
    "COLONIZE": "Colonize",
    "EVENT": "Event",
    "PARTICLE": "Particle",
}
_FADATA_CARGO_EMPTY_TOKENS: frozenset[str] = frozenset({"", "none", "keine", "ninguno", "aucun", "nenhum", "-", "—"})
_FADATA_CARGO_RES_ALIASES: tuple[tuple[str, str], ...] = (
    ("pig iron", "pig_iron"),
    ("pig_iron", "pig_iron"),
    ("roheisen", "pig_iron"),
    ("hierro", "pig_iron"),
    ("ferro", "pig_iron"),
    ("fonte", "pig_iron"),
    ("pig", "pig_iron"),
    ("kryptonite", "kryptonite"),
    ("kryptonit", "kryptonite"),
    ("kriptonita", "kryptonite"),
    ("krypton", "kryptonite"),
    ("kryp", "kryptonite"),
    ("kry", "kryptonite"),
    ("metall", "metal"),
    ("métal", "metal"),
    ("metal", "metal"),
    ("met", "metal"),
    ("gewürz", "spice"),
    ("gewuerz", "spice"),
    ("especia", "spice"),
    ("épice", "spice"),
    ("spice", "spice"),
    ("spi", "spice"),
)
_FADATA_CARGO_NUM_FIRST_RE = re.compile(r"^\s*([0-9][0-9.,'\s]*)\s*(?:x\s*)?([A-Za-zÀ-ÿ_][A-Za-zÀ-ÿ_\s-]{1,24})\s*$")
_FADATA_CARGO_LABEL_FIRST_RE = re.compile(r"^\s*([A-Za-zÀ-ÿ_][A-Za-zÀ-ÿ_\s-]{1,24}?)\s*[:=]?\s*([0-9][0-9.,'\s]*)\s*$")
_FADATA_CARGO_INLINE_RE = re.compile(
    r"([0-9][0-9.,']*)\s*(pig\s+iron|roheisen|hierro|ferro|fonte|metall|m[eé]tal|metal|kryptonite|kryptonit|kriptonita|krypton|spice|gew[uü]rz|especia|[eé]pice)",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class _FaDataEntry:
    row_id: int
    rem_s: float
    raw_act: str
    mission_lbl: str
    is_ret: bool
    origin_coords: str
    dest_str: str
    ships_detail: str
    cargo: Resources | None


def _match_fadata_cargo_resource(label: str) -> str | None:
    norm = collapse_ws(label.strip().lower())
    if not norm:
        return None
    for alias, field_name in _FADATA_CARGO_RES_ALIASES:
        if norm == alias or norm.startswith(alias + " ") or norm.endswith(" " + alias):
            return field_name
    return None


def _parse_fadata_cargo(tt_cargo_html: str) -> Resources | None:
    if not tt_cargo_html:
        return None
    totals = {"pig_iron": 0, "metal": 0, "kryptonite": 0, "spice": 0}
    found_any = False
    for chunk in re.split(r"(?:<br\s*/?>|</tr>|</div>|\n|\||;)", tt_cargo_html, flags=re.IGNORECASE):
        txt = tag_text(BeautifulSoup(chunk, "html.parser"))
        if not txt or txt.lower() in _FADATA_CARGO_EMPTY_TOKENS:
            continue
        matched_single = False
        for pat, num_idx, lbl_idx in ((_FADATA_CARGO_NUM_FIRST_RE, 1, 2), (_FADATA_CARGO_LABEL_FIRST_RE, 2, 1)):
            m = pat.match(txt)
            if m:
                field_name = _match_fadata_cargo_resource(m.group(lbl_idx))
                if field_name:
                    amt = parse_game_int(m.group(num_idx))
                    if amt > 0:
                        totals[field_name] += amt
                        found_any = True
                    matched_single = True
                    break
        if matched_single:
            continue
        for m_pair in _FADATA_CARGO_INLINE_RE.finditer(txt):
            field_name = _match_fadata_cargo_resource(m_pair.group(2))
            if field_name:
                amt = parse_game_int(m_pair.group(1))
                if amt > 0:
                    totals[field_name] += amt
                    found_any = True
    if not found_any:
        return None
    return Resources(
        pig_iron=totals["pig_iron"],
        metal=totals["metal"],
        kryptonite=totals["kryptonite"],
        spice=totals["spice"],
    )


def _unescape_js_string(raw: str) -> str:
    return raw.replace(r"\/", "/").replace(r"\"", '"').replace(r"\'", "'").replace(r"\n", "\n").replace(r"\t", " ")


def _extract_fadata_entries(html: str) -> list[_FaDataEntry]:
    match = _FADATA_BLOCK_RE.search(html or "")
    if not match:
        return []
    entries: list[_FaDataEntry] = []
    for sec in re.split(r'\{\s*"title"\s*:\s*', match.group(1))[1:]:
        m_title = re.match(r'"([^"]+)"', sec)
        sec_title = (m_title.group(1) if m_title else "").strip().lower()
        if not any(tok in sec_title for tok in ("own fleet", "returning", "eigene", "rückkehr", "rueckkehr")):
            continue
        is_ret_sec = any(tok in sec_title for tok in ("returning", "rückkehr", "rueckkehr"))
        for row_m in re.finditer(r'\{\s*"id"\s*:\s*(\d+)(.*?)\}', sec, re.DOTALL):
            body = row_m.group(2)
            m_t = re.search(r'"t"\s*:\s*(\d+)', body, re.ASCII)
            if not m_t:
                continue
            rem_s = float(coerce_bounded_int(m_t.group(1), 0))
            m_act = re.search(r'"a"\s*:\s*[\'"]([A-Za-z_]+)[\'"]', body, re.ASCII)
            raw_act = (m_act.group(1) if m_act else "FLEET").upper()
            is_ret = is_ret_sec or bool(re.search(r'"return"\s*:\s*true\b', body, re.IGNORECASE))
            m_start = re.search(r'"start"\s*:\s*"([^"]*)"(?:\s*\+\s*"([^"]*)")?', body)
            start_str = ((m_start.group(1) or "") + (m_start.group(2) or "")).strip() if m_start else ""
            m_dest = re.search(r'"dest"\s*:\s*"([^"]*)"(?:\s*\+\s*"([^"]*)")?', body)
            dest_str = ((m_dest.group(1) or "") + (m_dest.group(2) or "")).strip() if m_dest else ""
            ships_detail = ""
            cargo_res: Resources | None = None
            m_tt = re.search(r'"tt"\s*:\s*"((?:[^"\\]|\\.)*)"', body, re.DOTALL)
            if m_tt:
                tt_sections = re.split(
                    r"<hr\s*/?>", _unescape_js_string(m_tt.group(1)), maxsplit=1, flags=re.IGNORECASE
                )
                tt_parts = [
                    tag_text(BeautifulSoup(part, "html.parser"))
                    for part in re.split(r"<br\s*/?>", tt_sections[0], flags=re.IGNORECASE)
                ]
                ships_detail = ", ".join(part for part in tt_parts if part)[:140]
                if len(tt_sections) > 1:
                    parsed_cargo = _parse_fadata_cargo(tt_sections[1])
                    if parsed_cargo is not None:
                        cargo_res = parsed_cargo
                    elif is_ret and raw_act in ("ATTACK", "RECYCLE"):
                        cargo_res = Resources(0, 0, 0, 0)
            entries.append(
                _FaDataEntry(
                    row_id=coerce_bounded_int(row_m.group(1), 0),
                    rem_s=rem_s,
                    raw_act=raw_act,
                    mission_lbl=_FADATA_MISSION_LABELS.get(raw_act, raw_act.title()),
                    is_ret=is_ret,
                    origin_coords=normalize_coord_triple(start_str, strict_bounds=False),
                    dest_str=dest_str,
                    ships_detail=ships_detail,
                    cargo=cargo_res,
                )
            )
    return entries


def _dedup_and_pair_fadata(raw_entries: list[_FaDataEntry]) -> list[_FaDataEntry]:
    by_id: dict[int, _FaDataEntry] = {}
    dedup_entries: list[_FaDataEntry] = []
    for ent in raw_entries:
        if ent.row_id > 0:
            prev = by_id.get(ent.row_id)
            if prev is None or (ent.rem_s, 1 if ent.is_ret else 0) < (prev.rem_s, 1 if prev.is_ret else 0):
                by_id[ent.row_id] = ent
        else:
            dedup_entries.append(ent)
    if by_id:
        dedup_entries = list(by_id.values()) + dedup_entries

    grouped: dict[tuple[str, str, str, str], list[int]] = {}
    for idx, ent in enumerate(dedup_entries):
        sig = (ent.raw_act, ent.origin_coords, collapse_ws(ent.dest_str.lower()), collapse_ws(ent.ships_detail.lower()))
        grouped.setdefault(sig, []).append(idx)

    skip: set[int] = set()
    for idxs in grouped.values():
        out_idxs = sorted(
            (i for i in idxs if not dedup_entries[i].is_ret), key=lambda i: dedup_entries[i].rem_s, reverse=True
        )
        ret_idxs = sorted(
            (i for i in idxs if dedup_entries[i].is_ret), key=lambda i: dedup_entries[i].rem_s, reverse=True
        )
        used_ret: set[int] = set()
        for oi in out_idxs:
            for ri in ret_idxs:
                if ri not in used_ret and dedup_entries[ri].rem_s > dedup_entries[oi].rem_s:
                    used_ret.add(ri)
                    skip.add(ri)
                    break
    return [ent for idx, ent in enumerate(dedup_entries) if idx not in skip]


def parse_home_fadata_fleets(html: str, *, ref_now: float) -> list[QueueJob]:
    """Parse active own/returning fleet flights and exact countdowns from ``var faData``."""
    jobs: list[QueueJob] = []
    for ent in _dedup_and_pair_fadata(_extract_fadata_entries(html)):
        expected_cargo: Resources | None = None
        delivered_flag = False
        if ent.raw_act == "TRANSPORT":
            if ent.is_ret:
                delivered_flag = True
            elif ent.cargo is not None and not ent.cargo.is_zero():
                expected_cargo = ent.cargo
        arrow = "↩" if ent.is_ret else "→"
        label = f"{ent.mission_lbl} {arrow} {ent.dest_str}".strip() if ent.dest_str else ent.mission_lbl
        leg_tag = "Returning" if ent.is_ret else "Outbound"
        detail_full = f"{leg_tag} · {ent.ships_detail}" if ent.ships_detail else leg_tag
        jobs.append(
            QueueJob(
                category="fleet",
                name=label[:200],
                remaining_text=format_hms_seconds(ent.rem_s),
                remaining_seconds=ent.rem_s,
                finish_ts=round(ref_now + ent.rem_s, 3),
                queue_slot=1,
                detail=detail_full[:160],
                origin_coords=ent.origin_coords,
                cargo=ent.cargo,
                expected_cargo=expected_cargo,
                delivered_cargo=None,
                delivered=delivered_flag,
            )
        )
    jobs.sort(key=lambda job: job.finish_ts if job.finish_ts is not None else float("inf"))
    return jobs
