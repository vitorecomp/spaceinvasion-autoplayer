"""Parsers for ``internalPlanetsOverview``, ``internalHome``, ``internalMenu``, and ticker rates."""

from __future__ import annotations

from collections.abc import Mapping
import json
import re
import time

from bs4 import BeautifulSoup, Tag

from backend.core.coercion import (
    coerce_bounded_int,
    coerce_finite_float,
    coerce_int,
    coerce_optional_finite_float,
    parse_game_int,
    safe_key,
    safe_sequence,
    sanitize_json_tree,
)
from backend.core.coords import normalize_coord_triple
from backend.core.model import (
    BUILDING_ID_BY_NAME,
    EnergyBalance,
    FleetAlert,
    GameState,
    PlanetState,
    QueueJob,
    Resources,
    normalize_planet_type,
)
from backend.parsing._common import (
    attr_opt_str,
    clean_queue_item_name,
    format_hms_seconds,
    parse_hms_seconds,
    safe_text,
    tag_text,
)
from backend.parsing._fadata import parse_home_fadata_fleets

_MENU_POINTS_RANK_RE = re.compile(
    r"\b(?:Points|Punkte|Pontos)\s*[:\-]?\s*([\d.]+)(?:\s*(?:#|Rank|Platz|Pos)\s*[:\-]?\s*([\d.]+))?",
    re.IGNORECASE | re.ASCII,
)
_UNREAD_BANNER_RE = re.compile(
    r"(?:You\s+have|Du\s+hast)\s+(\d+)\s+(?:unread\s+Messages?|ungelesene\s+Nachrichten?)",
    re.IGNORECASE,
)
_TICKER_INPUT_MAP: tuple[tuple[str, str], ...] = (
    ("roheisen", "pig_iron"),
    ("metall", "metal"),
    ("kryptonit", "kryptonite"),
    ("spice", "spice"),
)


def _extract_levels_and_names(mapping: object) -> tuple[dict[str, int], dict[str, str]]:
    levels: dict[str, int] = {}
    names: dict[str, str] = {}
    if not isinstance(mapping, Mapping):
        return levels, names
    for key, val in mapping.items():
        if isinstance(val, Mapping):
            k_str = safe_key(key)
            levels[k_str] = max(0, coerce_int(val.get("level", 0), 0))
            if val.get("name"):
                names[k_str] = safe_text(val["name"])
    return levels, names


def parse_planets_overview_json(json_text: str) -> GameState:
    """Parse ``#json-container`` from ``internalPlanetsOverview`` into a ``GameState``."""
    raw_payload: object = json.loads(json_text)
    sanitized = sanitize_json_tree(raw_payload, max_int=10**4000, nonfinite_float=0.0)
    payload: Mapping[object, object] = sanitized if isinstance(sanitized, Mapping) else {}
    info_raw = payload.get("info")
    info: Mapping[object, object] = info_raw if isinstance(info_raw, Mapping) else {}
    user_raw = info.get("user")
    user: Mapping[object, object] = user_raw if isinstance(user_raw, Mapping) else {}
    uni_raw = info.get("universe")
    uni: Mapping[object, object] = uni_raw if isinstance(uni_raw, Mapping) else {}

    planets: list[PlanetState] = []
    for raw_p in safe_sequence(payload.get("planets")):
        if not isinstance(raw_p, Mapping):
            continue
        b_levels, b_names = _extract_levels_and_names(raw_p.get("buildings"))
        s_levels, _ = _extract_levels_and_names(raw_p.get("ships"))
        d_levels, _ = _extract_levels_and_names(raw_p.get("defense"))
        energy_raw = raw_p.get("energy")
        energy_map: Mapping[object, object] = energy_raw if isinstance(energy_raw, Mapping) else {}
        planets.append(
            PlanetState(
                planet_id=max(0, coerce_int(raw_p.get("id", 0), 0)),
                name=safe_text(raw_p.get("name", "")),
                coords=safe_text(raw_p.get("coords", "")),
                planet_type=normalize_planet_type({"type": raw_p.get("type")}),
                temperature=coerce_int(raw_p.get("temperature", 0), 0),
                fields_used=sum(b_levels.values()),
                fields_max=max(0, coerce_int(raw_p.get("fields", 0), 0)),
                resources=Resources.from_list(raw_p.get("resources", [])),
                storage=Resources.from_list(raw_p.get("storage", [])),
                energy=EnergyBalance(
                    produced=max(0, coerce_int(energy_map.get("produced", 0), 0)),
                    used=max(0, coerce_int(energy_map.get("used", 0), 0)),
                ),
                buildings=b_levels,
                building_names=b_names,
                ships=s_levels,
                defense=d_levels,
            )
        )

    r_levels, r_names = _extract_levels_and_names(payload.get("researches"))
    specs_raw = payload.get("specs")
    specs = (
        {safe_key(k): max(0, coerce_int(v, 0)) for k, v in specs_raw.items()} if isinstance(specs_raw, Mapping) else {}
    )
    return GameState(
        universe_id=max(0, coerce_int(uni.get("id", 0), 0)),
        user_id=max(0, coerce_int(user.get("userID", 0), 0)),
        nickname=safe_text(user.get("nickname", "")),
        timestamp=coerce_finite_float(info.get("ts", 0.0), 0.0, overflow_cap=None),
        planets=tuple(planets),
        active_planet_id=planets[0].planet_id if planets else None,
        researches=r_levels,
        research_names=r_names,
        specs=specs,
    )


def _parse_home_queue_box(
    soup: BeautifulSoup, box_id: str, cat: str, *, ref_now: float, norm_origin: str
) -> QueueJob | None:
    el = soup.find(id=box_id)
    if not isinstance(el, Tag):
        return None
    content = tag_text(el)
    if not content:
        return None
    q_bid: int | None = None
    q_lvl: int | None = None
    m_lvl = re.search(r"([A-Za-z][A-Za-z ]+?)\s*\(\s*Level\s+(\d+)\s*\)", content, re.ASCII)
    if cat == "building" and m_lvl:
        q_bid = BUILDING_ID_BY_NAME.get(clean_queue_item_name(m_lvl.group(1)).lower())
        parsed_lvl = coerce_bounded_int(m_lvl.group(2), 0)
        q_lvl = parsed_lvl if parsed_lvl > 0 else None
    elif cat == "research" and m_lvl:
        parsed_rlvl = coerce_bounded_int(m_lvl.group(2), 0)
        q_lvl = parsed_rlvl if parsed_rlvl > 0 else None

    left_el = soup.find(id=f"{box_id}-left")
    rem_s: float | None = None
    fin_ts: float | None = None
    rem_txt = ""
    pct_str = ""
    if isinstance(left_el, Tag):
        raw_left = coerce_optional_finite_float(left_el.get("data-left"))
        raw_start_ms = coerce_optional_finite_float(left_el.get("data-start-ts"))
        raw_total = coerce_optional_finite_float(left_el.get("data-total"))
        if raw_left is not None and raw_left >= 0.0:
            rem_s = float(raw_left)
            base_epoch = (raw_start_ms / 1000.0) if (raw_start_ms is not None and raw_start_ms > 1e11) else ref_now
            fin_ts = round(base_epoch + rem_s, 3)
            rem_txt = format_hms_seconds(rem_s)
            if raw_total is not None and raw_total > 0.0:
                pct = max(0, min(100, round((1.0 - min(1.0, rem_s / raw_total)) * 100)))
                pct_str = f"{pct}%"

    detail_prefix = {"fleet": "Shipyard", "building": "In construction", "research": "Researching"}[cat]
    detail_default = {"fleet": "Weapons factory", "building": "In construction", "research": "Researching"}[cat]
    return QueueJob(
        category=cat,
        name=content,
        building_id=q_bid,
        target_level=q_lvl,
        remaining_text=rem_txt,
        remaining_seconds=rem_s,
        finish_ts=fin_ts,
        queue_slot=1,
        detail=f"{detail_prefix} ({pct_str})" if pct_str else detail_default,
        origin_coords=(norm_origin if cat == "building" else ""),
    )


def parse_home_metadata(
    html: str,
    *,
    now_ts: float | None = None,
    origin_coords: str = "",
) -> tuple[int | None, float | None, bool, tuple[QueueJob, ...], tuple[FleetAlert, ...]]:
    """Extract active planet ID, speedup factor, newbie protection, queues, and fleet alerts."""
    norm_origin = normalize_coord_triple(origin_coords, strict_bounds=False) if origin_coords else ""
    soup = BeautifulSoup(html, "html.parser")
    text = tag_text(soup)
    ref_now = coerce_finite_float(now_ts, time.time(), overflow_cap=None) if now_ts is not None else time.time()

    active_planet_id: int | None = None
    m_pid = re.search(r"planetID\s*:\s*(\d+)", html, re.ASCII)
    if m_pid:
        pid_val = coerce_bounded_int(m_pid.group(1), -1)
        active_planet_id = pid_val if pid_val >= 0 else None

    speedup: float | None = None
    m_spd = re.search(r"active factor:\s*x([0-9.]+)", text, re.IGNORECASE | re.ASCII)
    if m_spd:
        spd_val = coerce_optional_finite_float(m_spd.group(1))
        speedup = spd_val if (spd_val is not None and spd_val > 0.0) else None

    newbie = "no attacks can be launched on you" in text.lower()
    queues: list[QueueJob] = []
    for box_id, cat in (("sp-own-building", "building"), ("sp-own-research", "research"), ("sp-own-fleet", "fleet")):
        job = _parse_home_queue_box(soup, box_id, cat, ref_now=ref_now, norm_origin=norm_origin)
        if job is not None:
            queues.append(job)

    fadata_fleets = parse_home_fadata_fleets(html, ref_now=ref_now)
    if fadata_fleets:
        queues.extend(fadata_fleets)
    elif not any(q.category == "fleet" for q in queues):
        recall_nodes = soup.select(
            'form[action*="recallFleet"], a[href*="recallFleet"], input[name*="recallFleet"], [onclick*="recallFleet"]'
        )
        for rnode in recall_nodes:
            row = rnode.find_parent("tr") or rnode.find_parent("div") or rnode
            summary_txt = tag_text(row)
            rem_s_rc = parse_hms_seconds(summary_txt)
            queues.append(
                QueueJob(
                    category="fleet",
                    name=(summary_txt[:200] if summary_txt else "Active/returning fleet"),
                    remaining_text=format_hms_seconds(rem_s_rc) if rem_s_rc is not None else "",
                    remaining_seconds=rem_s_rc,
                    finish_ts=round(ref_now + rem_s_rc, 3) if rem_s_rc is not None else None,
                    queue_slot=1,
                    detail="In flight",
                )
            )
            break

    alerts: list[FleetAlert] = []
    for tag in soup.select('[class*="attack"], [id*="attack"]'):
        msg = tag_text(tag)
        if (
            msg
            and "no attacks can be launched on you" not in msg.lower()
            and "beware of the attacks" not in msg.lower()
        ):
            alerts.append(FleetAlert(mission="ATTACK", hostile=True, summary=msg[:200]))
    return active_planet_id, speedup, newbie, tuple(queues), tuple(alerts)


def parse_menu_status(html: str) -> tuple[dict[str, int], int | None, int | None]:
    """Extract unread message counts, server points, and rank from ``F_Navi`` (``internalMenu``)."""
    soup = BeautifulSoup(html, "html.parser")
    counts: dict[str, int] = {}
    for anchor in soup.find_all("a", href=True):
        if not isinstance(anchor, Tag):
            continue
        href = attr_opt_str(anchor, "href") or ""
        if "action=internalMessages" not in href:
            continue
        m_type = re.search(r"type=([a-z]+)", href)
        key = m_type.group(1) if m_type else "total"
        m_num = re.search(r"(\d+)", tag_text(anchor), re.ASCII)
        counts[key] = coerce_bounded_int(m_num.group(1), 0) if m_num else 0

    points: int | None = None
    rank: int | None = None
    m_stat = _MENU_POINTS_RANK_RE.search(tag_text(soup))
    if m_stat:
        points = parse_game_int(m_stat.group(1))
        if m_stat.group(2):
            rank = parse_game_int(m_stat.group(2))
    return counts, points, rank


def parse_menu_messages(html: str) -> dict[str, int]:
    """Extract unread message counts per category from ``F_Navi`` (``internalMenu``)."""
    counts, _, _ = parse_menu_status(html)
    return counts


def parse_unread_banner_count(html: str) -> int:
    """Extract unread message count from ``F_Hauptframe`` top banner."""
    if not html:
        return 0
    match = _UNREAD_BANNER_RE.search(html)
    return coerce_bounded_int(match.group(1), 0) if match else 0


def parse_ticker_rates(html: str) -> dict[str, float]:
    """Extract live per-second production rates from hidden ``<form id="ress">`` ticker inputs."""
    if not isinstance(html, str) or "metall" not in html:
        return {}
    soup = BeautifulSoup(html, "html.parser")
    ress_form = soup.find("form", id="ress")
    if not isinstance(ress_form, Tag):
        return {}
    rates: dict[str, float] = {}
    for input_id, key in _TICKER_INPUT_MAP:
        inp = ress_form.find("input", id=input_id)
        if not isinstance(inp, Tag):
            continue
        val = coerce_optional_finite_float(inp.get("value"))
        if val is not None and val >= 0.0:
            rates[key] = val
    return rates
