"""DOM mission-radio candidate filtering and matching for `internalSendFleet`."""

from __future__ import annotations

from collections.abc import Sequence

from bs4 import BeautifulSoup

from backend.session.client._protocols import WebElementLike
from backend.session.executor._fleet_radio_labels import collect_radio_labels

_SPEED_OR_ROUTE_NAME_TOKENS: tuple[str, ...] = ("procent", "percent", "geschwindigkeit", "speed", "route")

_MISSION_WANT_TOKENS: dict[str, tuple[str, ...]] = {
    "ATTACK": ("attack", "angriff", "angreif"),
    "ESCAPE": ("escape", "flucht", "save"),
    "RECYCLE": (
        "recycle",
        "recycl",
        "rubble",
        "debris",
        "trümmer",
        "truemmer",
        "abbau",
        "harvest",
        "sammeln",
        "tf",
    ),
    "COLONIZE": ("colonize", "colonisation", "colonization", "kolonisier", "colony", "siedel"),
    "TRANSPORT": ("transport", "transportier", "liefer"),
    "STATION": ("station", "stationier", "deploy", "verleg"),
}
_DEFAULT_SPY_TOKENS: tuple[str, ...] = ("spy", "spio", "spionier", "espionage")
_OTHER_ATTACK_MISSIONS: tuple[str, ...] = (
    "transport",
    "transportier",
    "station",
    "stationier",
    "spy",
    "spio",
    "spionier",
    "espionage",
    "colonize",
    "kolonisier",
    "recycle",
    "recycl",
    "harvest",
    "sammeln",
    "sammel",
    "abbau",
    "escape",
    "flucht",
    "halt",
    "hold",
    "defend",
    "verteidig",
    "escort",
    "eskort",
    "deploy",
    "expedition",
    "explore",
    "erkund",
)


def _is_speed_or_route(r_name: str, lbl: str) -> bool:
    return "%" in lbl or any(tok in r_name for tok in _SPEED_OR_ROUTE_NAME_TOKENS)


def _build_radio_candidates(
    radios: Sequence[WebElementLike],
    radio_labels: Sequence[tuple[str, str, str, str, bool]],
) -> list[tuple[WebElementLike, str, str, str, str]]:
    candidates: list[tuple[WebElementLike, str, str, str, str]] = []
    for idx, r in enumerate(radios):
        r_val = str(r.get_attribute("value") or "").strip().lower()
        r_id = str(r.get_attribute("id") or "").strip().lower()
        r_name = str(r.get_attribute("name") or "").strip().lower()
        raw_dis: object = r.get_attribute("disabled")
        is_dis = (isinstance(raw_dis, str) and raw_dis.strip().lower() in ("true", "disabled", "1")) or raw_dis is True
        label_txt = ""
        matched_label = False
        if len(radios) == len(radio_labels) and idx < len(radio_labels):
            b_val, b_id, b_name, b_lbl, b_dis = radio_labels[idx]
            if (
                (not r_id or not b_id or r_id == b_id)
                and (not r_name or not b_name or r_name == b_name)
                and (not r_val or not b_val or r_val == b_val)
            ):
                label_txt = b_lbl
                is_dis = is_dis or b_dis
                matched_label = True
        if not matched_label:
            for b_val, b_id, b_name, b_lbl, b_dis in radio_labels:
                if r_id and b_id and r_id != b_id:
                    continue
                if (r_id and r_id == b_id) or (
                    r_val and r_val == b_val and (not r_name or not b_name or r_name == b_name)
                ):
                    label_txt = b_lbl
                    is_dis = is_dis or b_dis
                    matched_label = True
                    break
        if not matched_label and idx < len(radio_labels):
            label_txt = radio_labels[idx][3]
            is_dis = is_dis or radio_labels[idx][4]
        if is_dis or _is_speed_or_route(r_name, label_txt):
            continue
        candidates.append((r, r_val, r_id, r_name, label_txt))
    return candidates


def match_mission_radio(
    send_soup: BeautifulSoup,
    form_css: str,
    radios: Sequence[WebElementLike],
    mission_kind: str,
) -> WebElementLike | None:
    """Select the DOM radio input matching `mission_kind` without colliding with speed/route radios."""
    radio_labels = collect_radio_labels(send_soup, form_css)
    want_tokens = _MISSION_WANT_TOKENS.get(mission_kind, _DEFAULT_SPY_TOKENS)
    candidates = _build_radio_candidates(radios, radio_labels)

    for r, r_val, r_id, _r_name, label_txt in candidates:
        if any(tok in r_val or tok in r_id or tok in label_txt for tok in want_tokens):
            return r

    if mission_kind == "ATTACK":
        for r, r_val, r_id, r_name, label_txt in candidates:
            if (r_val == "1" or ("mission" in r_name and r_val == "1")) and not any(
                m in label_txt or m in r_id for m in _OTHER_ATTACK_MISSIONS
            ):
                return r
    return None


def collect_available_mission_radio_values(send_soup: BeautifulSoup) -> list[str]:
    """Return distinct non-speed/non-route radio values present on `internalSendFleet`."""
    avail_vals: list[str] = []
    for r_el in send_soup.select('input[type="radio"]'):
        r_nm = str(r_el.get("name") or "").strip().lower()
        r_vl = str(r_el.get("value") or "").strip()
        if r_vl and not any(tok in r_nm for tok in _SPEED_OR_ROUTE_NAME_TOKENS) and r_vl not in avail_vals:
            avail_vals.append(r_vl)
    return avail_vals
