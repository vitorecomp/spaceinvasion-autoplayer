"""Localized unit/tech alias tables and embedded-JSON helpers for espionage report parsing."""

from __future__ import annotations

from collections.abc import Mapping
import json
import math
import re

from spaceinvasion.core import catalog as game_data
from spaceinvasion.core.coercion import coerce_int, parse_game_int
from spaceinvasion.parsing.models import DEFENSE_SLUG_BY_KEY, SHIP_SLUG_BY_KEY

_GERMAN_SHIP_ALIASES: dict[str, tuple[str, ...]] = {
    "s1": ("Kleiner Transporter",),
    "s2": ("Großer Transporter", "Grosser Transporter"),
    "s3": ("Transmitter",),
    "s4": ("Kleiner Jäger", "Kleiner Jaeger"),
    "s5": ("Kriegsschiff",),
    "s6": ("Sternenkreuzer",),
    "s7": ("Phönix", "Phoenix"),
    "s8": ("Kolonieschiff",),
    "s9": ("Solarzellen", "Solarzelle"),
    "s10": ("Spionagesonde",),
    "s11": ("Recycler",),
    "s13": ("Schlachtschiff",),
    "s14": ("Tarnkappenbomber",),
    "s15": ("Zerstörer", "Zerstoerer"),
    "s16": ("Imperiale Sternenbasis",),
}
_GERMAN_DEFENSE_ALIASES: dict[str, tuple[str, ...]] = {
    "d1": ("Mörser", "Moerser"),
    "d2": ("Lasergeschützbatterie", "Lasergeschuetzbatterie"),
    "d3": ("Ionenkanone",),
    "d4": ("Positronenfeuerzeug", "Positronengeschütz", "Positronengeschuetz"),
    "d5": ("Turboflak",),
    "d6": ("Intervallkanone",),
    "d7": ("Solarkanone",),
    "d8": ("Leichte Gravitonkanone",),
    "d9": ("Schwere Gravitonkanone",),
    "d10": (" Kleine Schildkuppel", "Kleine Schildkuppel"),
    "d11": ("Große Schildkuppel", "Grosse Schildkuppel"),
}
_GERMAN_BUILDING_ALIASES: dict[str, tuple[str, ...]] = {
    "b1": ("Roheisenmine",),
    "b2": ("Hochofen",),
    "b3": ("Kryptonitmine",),
    "b4": ("Spicemine",),
    "b5": ("Fusionskraftwerk",),
    "b6": ("Thermosolarkraftwerk",),
    "b7": ("Entwicklungszentrum",),
    "b8": ("Forschungslabor",),
    "b9": ("Waffenfabrik",),
    "b10": ("Flottenstützpunkt", "Flottenstuetzpunkt"),
    "b11": ("Schutzschild", "Schildgenerator"),
    "b12": ("Roheisenlager",),
    "b13": ("Metalllager",),
    "b14": ("Kryptonitlager",),
    "b15": ("Spicelager",),
    "b16": ("Mikrosystembeschleuniger",),
    "b17": ("Teleporter",),
    "b18": ("Galaxiescanner",),
}
_GERMAN_RESEARCH_ALIASES: dict[str, tuple[str, ...]] = {
    "r1": ("Bergbautechnik",),
    "r2": ("Energietechnik",),
    "r3": ("Raketentriebwerk", "Verbrennungstriebwerk"),
    "r4": ("Diffusionstriebwerk",),
    "r5": ("Spionagetechnik",),
    "r6": ("Warpantrieb",),
    "r7": ("Waffentechnik",),
    "r8": ("Schildtechnik",),
    "r9": ("Zielsysteme",),
    "r10": ("Schiffskonstruktion", "Raumschiffpanzerung"),
    "r11": ("Gravitonforschung", "Gravitontechnik"),
    "r12": ("Teleportation",),
    "r15": ("Schwarzes-Loch-Forschung",),
}

EXPLICIT_SPY_REPORT_TOKENS: tuple[str, ...] = (
    "espionage report",
    "spionagebericht",
    "spy report",
    '"spy-report"',
    "'spy-report'",
)
_NON_SPY_NOTICE_TOKENS: tuple[str, ...] = (
    "fleet return",
    "flottenrückkehr",
    "flottenrueckkehr",
    "returning from duty",
    "is returning from",
    "kehrt von",
    "zurückgekehrt",
    "zurueckgekehrt",
    "were unloaded",
    "wurden ausgeladen",
    "the assignment was to:",
    "der auftrag war:",
    "to its home planet",
    "zu seinem heimatplaneten",
    "colonization report",
    "kolonisierungsbericht",
    "planet was colonized",
    "planet wurde kolonisiert",
    "colonization failed",
    "kolonisierung fehlgeschlagen",
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
    "recycling report",
    "space rubble",
    "harvested",
    "recycled",
    "trümmerfeld abgebaut",
    "delivered",
    "geliefert",
)


def parse_embedded_unit_val(entry: object) -> int:
    """Parse nested numeric/dict unit levels or counts from embedded spy/battle JSON."""
    if isinstance(entry, Mapping):
        for key in ("level", "count", "amount", "v", "val"):
            if entry.get(key) is not None:
                return parse_embedded_unit_val(entry.get(key))
        return 0
    if isinstance(entry, bool) or entry is None:
        return 0
    if isinstance(entry, int):
        return max(0, entry)
    if isinstance(entry, float):
        return max(0, int(round(entry))) if math.isfinite(entry) else 0
    text = str(entry).strip()
    if re.fullmatch(r"\d{1,3}(?:\.\d{3})+|\d+", text, re.ASCII):
        return parse_game_int(text)
    return max(0, coerce_int(text, 0))


def extract_embedded_spy_json(text: str) -> Mapping[str, object] | None:
    if "spy-report" not in text:
        return None
    marker = text.find("spy-report")
    decoder = json.JSONDecoder()
    idx = text.find("{", max(0, marker - 400))
    while 0 <= idx < marker:
        try:
            obj, _ = decoder.raw_decode(text, idx)
            if isinstance(obj, dict):
                info = obj.get("info")
                if (isinstance(info, Mapping) and str(info.get("type") or "") == "spy-report") or isinstance(
                    obj.get("planet"), Mapping
                ):
                    return obj
        except ValueError:
            pass
        idx = text.find("{", idx + 1)
    return None


def is_non_spy_notice_text(text: str) -> bool:
    """Return True when ``text`` is a fleet-return, battle, recycle, transport, or colonize notice."""
    if not text:
        return False
    low = str(text).lower()
    if any(tok in low for tok in EXPLICIT_SPY_REPORT_TOKENS):
        return False
    return any(tok in low for tok in _NON_SPY_NOTICE_TOKENS)


def parse_text_units_and_tech(
    text: str,
) -> tuple[dict[str, int], dict[str, int], dict[str, int], dict[str, int]]:
    fleet: dict[str, int] = {}
    for skey, slug in SHIP_SLUG_BY_KEY.items():
        info = game_data.SHIPS.get(slug)
        names = ([str(info["name"])] if isinstance(info, Mapping) and info.get("name") else []) + list(
            _GERMAN_SHIP_ALIASES.get(skey, ())
        )
        for nm in names:
            m_u = re.search(rf"\b{re.escape(nm.strip())}\s*:?\s*([0-9][0-9.]*)", text, re.IGNORECASE)
            if m_u:
                fleet[skey] = parse_game_int(m_u.group(1))
                break

    defense: dict[str, int] = {}
    for dkey, slug in DEFENSE_SLUG_BY_KEY.items():
        info = game_data.DEFENSES.get(slug)
        names_d = ([str(info["name"])] if isinstance(info, Mapping) and info.get("name") else []) + list(
            _GERMAN_DEFENSE_ALIASES.get(dkey, ())
        )
        for nm in names_d:
            m_u = re.search(rf"\b{re.escape(nm.strip())}\s*:?\s*([0-9][0-9.]*)", text, re.IGNORECASE)
            if m_u:
                defense[dkey] = parse_game_int(m_u.group(1))
                break

    buildings: dict[str, int] = {}
    for info in game_data.BUILDINGS.values():
        bid = info.get("id") if isinstance(info, Mapping) else None
        if not bid:
            continue
        bkey = f"b{bid}"
        names_b = ([str(info["name"])] if info.get("name") else []) + list(_GERMAN_BUILDING_ALIASES.get(bkey, ()))
        for nm in names_b:
            m_b = re.search(
                rf"\b{re.escape(nm.strip())}\s*(?:\(\s*Level\s*|:)?\s*([0-9]{{1,3}})\b", text, re.IGNORECASE
            )
            if m_b:
                buildings[bkey] = parse_game_int(m_b.group(1))
                break

    researches: dict[str, int] = {}
    for info in game_data.RESEARCHES.values():
        rkey = str(info.get("id") or "") if isinstance(info, Mapping) else ""
        if not rkey:
            continue
        names_r = ([str(info["name"])] if info.get("name") else []) + list(_GERMAN_RESEARCH_ALIASES.get(rkey, ()))
        for nm in names_r:
            m_r = re.search(
                rf"\b{re.escape(nm.strip())}\s*(?:\(\s*Level\s*|:)?\s*([0-9]{{1,3}})\b", text, re.IGNORECASE
            )
            if m_r:
                researches[rkey] = parse_game_int(m_r.group(1))
                break
    return fleet, defense, buildings, researches


def apply_embedded_spy_json(
    embedded_json: Mapping[str, object],
    *,
    fleet: dict[str, int],
    defense: dict[str, int],
    buildings: dict[str, int],
    researches: dict[str, int],
) -> None:
    pl_j = embedded_json.get("planet")
    if not isinstance(pl_j, Mapping):
        return
    emb_b = pl_j.get("buildings")
    if isinstance(emb_b, Mapping):
        for bk, bv in emb_b.items():
            bk_s = str(bk).strip().lower()
            if re.fullmatch(r"b\d{1,2}", bk_s) and (lvl_v := parse_embedded_unit_val(bv)) > 0:
                buildings[bk_s] = lvl_v
    emb_s = pl_j.get("ships") or pl_j.get("fleet")
    if isinstance(emb_s, Mapping):
        for sk, sv in emb_s.items():
            sk_s = str(sk).strip().lower()
            if sk_s in SHIP_SLUG_BY_KEY and (cnt_v := parse_embedded_unit_val(sv)) > 0:
                fleet[sk_s] = cnt_v
    emb_d = pl_j.get("defense") or pl_j.get("defenses")
    if isinstance(emb_d, Mapping):
        for dk, dv in emb_d.items():
            dk_s = str(dk).strip().lower()
            if dk_s in DEFENSE_SLUG_BY_KEY and (cnt_v := parse_embedded_unit_val(dv)) > 0:
                defense[dk_s] = cnt_v
    emb_r = pl_j.get("researches") or embedded_json.get("researches")
    if isinstance(emb_r, Mapping):
        for rk, rv in emb_r.items():
            rk_s = str(rk).strip().lower()
            if re.fullmatch(r"r\d{1,2}", rk_s) and (lvl_v := parse_embedded_unit_val(rv)) > 0:
                researches[rk_s] = lvl_v
