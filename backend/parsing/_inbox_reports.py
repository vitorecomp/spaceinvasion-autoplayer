"""Battle-report and recycle-report payload parsers shared by ``backend.parsing.inbox``."""

from __future__ import annotations

from collections.abc import Collection, Mapping
import json
import re

from bs4 import Tag

from backend.core.coercion import coerce_int, parse_game_int
from backend.core.coords import normalize_coord_triple
from backend.parsing._common import SPY_BRACKETED_COORDS_RE, attr_str
from backend.parsing._spy_aliases import parse_embedded_unit_val
from backend.parsing.models import JsonObject, SpyReport


def extract_resource_amount(segment: str, label_re: str) -> int:
    """Extract a resource quantity matching either ``Label: N`` or ``N Label``."""
    m1 = re.search(rf"{label_re}\s*:?\s*([\d.]+)", segment, re.IGNORECASE | re.ASCII)
    if m1:
        return parse_game_int(m1.group(1))
    m2 = re.search(rf"([\d.]+)\s*(?:units?\s+of\s+)?{label_re}\b", segment, re.IGNORECASE | re.ASCII)
    return parse_game_int(m2.group(1)) if m2 else 0


def extract_resource_quad(segment: str) -> dict[str, int]:
    """Extract all 4 SpaceInvasion resources from a text segment."""
    return {
        "pig_iron": extract_resource_amount(segment, r"(?:Pig\s+iron|Roheisen)"),
        "metal": extract_resource_amount(segment, r"(?:Metal|Metall)"),
        "kryptonite": extract_resource_amount(segment, r"(?:Kryptonite|Kryptonit)"),
        "spice": extract_resource_amount(segment, r"Spice"),
    }


def _spice_eq_from_loot(loot: Mapping[str, int]) -> float:
    return round(
        loot["pig_iron"] / 6.0 + loot["metal"] / 2.5 + loot["kryptonite"] / 3.0 + float(loot["spice"]),
        2,
    )


def _extract_battle_id(block_html: str, body_for_parse: str, bsum_el: Tag | None) -> str:
    battle_id = attr_str(bsum_el, "data-bsum").strip() if bsum_el is not None else ""
    if not battle_id:
        m_bid = re.search(r"showBattleReport\s*\(\s*['\"]?(\d+)['\"]?", block_html, re.IGNORECASE | re.ASCII)
        if m_bid:
            battle_id = m_bid.group(1)
    if not battle_id:
        m_bid_txt = re.search(r"Battle\s+report\s*#(\d+)", body_for_parse, re.IGNORECASE | re.ASCII)
        if m_bid_txt:
            battle_id = m_bid_txt.group(1)
    return battle_id


def _parse_battle_text_fields(
    body_for_parse: str, low: str
) -> tuple[int, str, str, str, int, int, dict[str, int], float, int, int]:
    m_ptail = re.search(r"(?:Plundered|Erbeutet)\s+(.+)$", body_for_parse, re.IGNORECASE)
    loot_dict = extract_resource_quad(m_ptail.group(1) if m_ptail else body_for_parse)
    debris_pig = 0
    debris_met = 0
    m_deb = re.search(
        r"(?:debris|rubble|trümmer|truemmer)[^.]{0,120}?(?:Pig\s+iron|Roheisen)\s*:?\s*([0-9][0-9.]*)[^.]{0,60}?(?:Metal|Metall)\s*:?\s*([0-9][0-9.]*)",
        body_for_parse,
        re.IGNORECASE,
    )
    if m_deb:
        debris_pig, debris_met = parse_game_int(m_deb.group(1)), parse_game_int(m_deb.group(2))

    attacker_name, defender_name = "", ""
    attacker_lost, defender_lost = 0, 0
    m_att = re.search(
        r"\bAttacker\s*:\s*([^|(]+?)\s*\(\s*Lost\s*:\s*([0-9][0-9.]*)\s*\)", body_for_parse, re.IGNORECASE
    )
    if m_att:
        attacker_name, attacker_lost = m_att.group(1).strip()[:120], parse_game_int(m_att.group(2))
    else:
        m_lost = re.search(r"\bLost\s*:?\s*([0-9][0-9.]*)", body_for_parse, re.IGNORECASE | re.ASCII)
        if m_lost:
            attacker_lost = parse_game_int(m_lost.group(1))
    m_def = re.search(
        r"\bDefender\s*:\s*([^|(]+?)\s*\(\s*Lost\s*:\s*([0-9][0-9.]*)\s*\)", body_for_parse, re.IGNORECASE
    )
    if m_def:
        defender_name, defender_lost = m_def.group(1).strip()[:120], parse_game_int(m_def.group(2))
    else:
        m_dest = re.search(r"\bDestroyed\s*:?\s*([0-9][0-9.]*)", body_for_parse, re.IGNORECASE | re.ASCII)
        if m_dest:
            defender_lost = parse_game_int(m_dest.group(1))

    m_rnd = re.search(r"\bRounds\s*:?\s*([0-9]{1,3})\b", body_for_parse, re.IGNORECASE | re.ASCII)
    rounds = int(m_rnd.group(1)) if m_rnd else 0
    outcome = "win"
    m_win = re.search(r"\bWinner\s*:\s*([012])\b", body_for_parse, re.IGNORECASE | re.ASCII)
    if m_win:
        wc = int(m_win.group(1))
        outcome = "win" if wc == 1 else ("loss" if wc == 2 else "draw")
    elif any(
        tok in low for tok in ("defender has won", "verteidiger hat die schlacht gewonnen", "fleet was destroyed")
    ):
        outcome = "loss"
    elif any(tok in low for tok in ("ended in a draw", "endet unentschieden", "draw")):
        outcome = "draw"

    loot_spice_eq = _spice_eq_from_loot(loot_dict)
    if loot_spice_eq <= 0.0:
        m_loot_tot = re.search(r"\bLooted\s*:?\s*([0-9][0-9.]*)", body_for_parse, re.IGNORECASE | re.ASCII)
        if m_loot_tot:
            loot_spice_eq = float(parse_game_int(m_loot_tot.group(1)))
    return (
        rounds,
        outcome,
        attacker_name,
        defender_name,
        attacker_lost,
        defender_lost,
        loot_dict,
        loot_spice_eq,
        debris_pig,
        debris_met,
    )


def _merge_battle_json(
    battle_json_el: Tag,
    fields: tuple[int, str, str, str, int, int, dict[str, int], float, int, int],
) -> tuple[int, str, str, str, int, int, dict[str, int], float, int, int]:
    (
        rounds,
        outcome,
        attacker_name,
        defender_name,
        attacker_lost,
        defender_lost,
        loot_dict,
        loot_spice_eq,
        debris_pig,
        debris_met,
    ) = fields
    try:
        bj: object = json.loads(attr_str(battle_json_el, "data-battle-json") or "{}")
    except (ValueError, TypeError):
        bj = {}
    if not isinstance(bj, dict) or not bj.get("ok"):
        return fields
    rounds = coerce_int(bj.get("rounds"), rounds)
    win_code = coerce_int(bj.get("winner"), 1)
    outcome = "win" if win_code == 1 else ("loss" if win_code == 2 else "draw")
    att_raw, def_raw = bj.get("attacker"), bj.get("defender")
    att_obj: Mapping[object, object] = att_raw if isinstance(att_raw, Mapping) else {}
    def_obj: Mapping[object, object] = def_raw if isinstance(def_raw, Mapping) else {}
    attacker_name = str(att_obj.get("name") or attacker_name).strip()[:120]
    defender_name = str(def_obj.get("name") or defender_name).strip()[:120]
    if att_obj.get("lost") is not None:
        attacker_lost = parse_embedded_unit_val(att_obj.get("lost"))
    if def_obj.get("lost") is not None:
        defender_lost = parse_embedded_unit_val(def_obj.get("lost"))
    stolen_arr = bj.get("stolen")
    if isinstance(stolen_arr, list):
        bj_loot = {"pig_iron": 0, "metal": 0, "kryptonite": 0, "spice": 0}
        rmap = {1: "pig_iron", 2: "metal", 3: "kryptonite", 4: "spice"}
        for item in stolen_arr:
            if isinstance(item, Mapping):
                rk = rmap.get(coerce_int(item.get("r"), 0))
                if rk:
                    bj_loot[rk] = max(0, parse_embedded_unit_val(item.get("v")))
        loot_dict = bj_loot
        loot_spice_eq = _spice_eq_from_loot(loot_dict)
    if bj.get("stolenSpice") is not None and loot_spice_eq <= 0.0:
        loot_spice_eq = float(max(0, parse_embedded_unit_val(bj.get("stolenSpice"))))
    rubble_arr = bj.get("rubble")
    if isinstance(rubble_arr, list):
        debris_pig, debris_met = 0, 0
        for item in rubble_arr:
            if isinstance(item, Mapping):
                rc = coerce_int(item.get("r"), 0)
                if rc == 1:
                    debris_pig = max(0, parse_embedded_unit_val(item.get("v")))
                elif rc == 2:
                    debris_met = max(0, parse_embedded_unit_val(item.get("v")))
    return (
        rounds,
        outcome,
        attacker_name,
        defender_name,
        attacker_lost,
        defender_lost,
        loot_dict,
        loot_spice_eq,
        debris_pig,
        debris_met,
    )


def parse_battle_report_data(
    block_html: str,
    body_for_parse: str,
    low: str,
    bsum_el: Tag | None,
    battle_json_el: Tag | None,
    norm_coords: str,
) -> JsonObject:
    """Parse structured battle-report fields from text and optional ``data-battle-json``."""
    battle_id = _extract_battle_id(block_html, body_for_parse, bsum_el)
    parsed = _parse_battle_text_fields(body_for_parse, low)
    if battle_json_el is not None:
        parsed = _merge_battle_json(battle_json_el, parsed)
    (
        rounds,
        outcome,
        attacker_name,
        defender_name,
        attacker_lost,
        defender_lost,
        loot_dict,
        loot_spice_eq,
        debris_pig,
        debris_met,
    ) = parsed
    if not defender_name and "spacia" in low:
        defender_name = "Spacia"
    return {
        "battle_id": battle_id,
        "coords": norm_coords,
        "attacker": attacker_name,
        "defender": defender_name,
        "outcome": outcome,
        "rounds": rounds,
        "loot": {k: v for k, v in loot_dict.items()},
        "loot_spice_eq": loot_spice_eq,
        "resources_mentioned": {k: v for k, v in loot_dict.items()},
        "attacker_lost_spice": attacker_lost,
        "defender_lost_spice": defender_lost,
        "debris": {"pig_iron": debris_pig, "metal": debris_met},
        "debris_spice_eq": round(debris_pig / 6.0 + debris_met / 2.5, 2),
        "has_debris": (debris_pig + debris_met) > 0,
    }


def parse_recycle_report_data(
    subject: str,
    body_for_parse: str,
    norm_coords: str,
    exclude_coords: Collection[str],
) -> tuple[str, JsonObject]:
    """Parse structured recycling-report payload and updated target coordinates."""
    m_subj_c = SPY_BRACKETED_COORDS_RE.search(subject)
    if m_subj_c:
        cand_sc = normalize_coord_triple(f"{m_subj_c.group(1)}:{m_subj_c.group(2)}:{m_subj_c.group(3)}")
        if cand_sc and cand_sc not in exclude_coords:
            norm_coords = cand_sc
    else:
        m_goal_c = re.search(
            r"(?:goal|ziel|at|bei)\s+[^\[\]]{0,60}\[\s*(\d{1,2})\s*:\s*(\d{1,3})\s*:\s*(\d{1,2})\s*\]",
            body_for_parse,
            re.IGNORECASE,
        )
        if m_goal_c:
            cand_gc = normalize_coord_triple(f"{m_goal_c.group(1)}:{m_goal_c.group(2)}:{m_goal_c.group(3)}")
            if cand_gc and cand_gc not in exclude_coords:
                norm_coords = cand_gc

    m_rc = re.search(r"\b([0-9][0-9.]*)\s*Recycler\b", body_for_parse, re.IGNORECASE | re.ASCII)
    rec_count = parse_game_int(m_rc.group(1)) if m_rc else 0
    m_cap = re.search(r"(?:capacity|ladekapazit[aä]t|storage)[^0-9]{0,30}([0-9][0-9.]*)", body_for_parse, re.IGNORECASE)
    cargo_cap = parse_game_int(m_cap.group(1)) if m_cap else 0

    m_htail = re.search(
        r"(?:harvested|collected|recycled|gathered|processed|abgebaut|gesammelt|verwertet)(.+)$",
        body_for_parse,
        re.IGNORECASE,
    )
    harv_tail = m_htail.group(1) if m_htail else body_for_parse
    harv = extract_resource_quad(harv_tail)
    if sum(harv.values()) == 0 and harv_tail != body_for_parse:
        harv = extract_resource_quad(body_for_parse)

    avail_pig, avail_met = harv["pig_iron"], harv["metal"]
    if m_htail:
        head_seg = body_for_parse[: m_htail.start()]
        ap = extract_resource_amount(head_seg, r"(?:Pig\s+iron|Roheisen)")
        am = extract_resource_amount(head_seg, r"(?:Metal|Metall)")
        if ap > 0 or am > 0:
            avail_pig, avail_met = ap, am

    return norm_coords, {
        "coords": norm_coords,
        "recycler_count": rec_count,
        "cargo_capacity": cargo_cap,
        "debris_available": {"pig_iron": avail_pig, "metal": avail_met},
        "harvested": {k: v for k, v in harv.items()},
        "harvested_spice_eq": _spice_eq_from_loot(harv),
    }


def spy_report_to_parsed_data(rep: SpyReport, rep_norm: str) -> JsonObject:
    """Convert a parsed ``SpyReport`` into the canonical inbox ``parsed_data`` mapping."""
    return {
        "target_coords": rep_norm,
        "owner": rep.owner,
        "planet_name": rep.planet_name,
        "is_spacia": rep.is_spacia,
        "resources": {
            "pig_iron": rep.resources.pig_iron,
            "metal": rep.resources.metal,
            "kryptonite": rep.resources.kryptonite,
            "spice": rep.resources.spice,
        },
        "buildings": {k: v for k, v in rep.buildings.items()},
        "fleet": {k: v for k, v in rep.fleet.items()},
        "defense": {k: v for k, v in rep.defense.items()},
        "researches": {k: v for k, v in rep.researches.items()},
        "dug_in": rep.dug_in,
        "uninhabited_fields": rep.uninhabited_fields,
    }
