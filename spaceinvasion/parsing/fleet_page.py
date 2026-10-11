"""Parsers for ``internalFleet`` / ``internalSendFleet`` slot status, mission blocks, and station specs."""

from __future__ import annotations

from collections.abc import Mapping
import re

from bs4 import BeautifulSoup

from spaceinvasion.core.coercion import coerce_int, fmt_compact_int, safe_key
from spaceinvasion.core.coords import normalize_coords
from spaceinvasion.parsing._common import collapse_ws, tag_text
from spaceinvasion.parsing.models import SHIP_SLUG_BY_KEY

_MISSION_FORBIDDEN_PATTERNS: dict[str, re.Pattern[str]] = {
    "ATTACK": re.compile(
        r"\b(?:Attack\s*\(\s*forbidden\s*\)|Angriff\s*\(\s*verboten\s*\))\s*:\s*([^<\r\n]+)",
        re.IGNORECASE,
    ),
    "SPY": re.compile(
        r"\b(?:(?:Spying|Espionage|Spy)\s*\(\s*forbidden\s*\)|Spionage\s*\(\s*verboten\s*\))\s*:\s*([^<\r\n]+)",
        re.IGNORECASE,
    ),
    "RECYCLE": re.compile(
        r"\b(?:(?:Recycl(?:e|ing)|Harvest|Rubble(?:\s+field)?|Debris(?:\s+field)?)\s*\(\s*forbidden\s*\)|(?:Abbau|Recycl(?:e|ing)|Tr[üu]mmerfeld)\s*\(\s*verboten\s*\))\s*:\s*([^<\r\n]+)",
        re.IGNORECASE,
    ),
    "ESCAPE": re.compile(
        r"\b(?:(?:Escape(?:\s+flight)?|Save)\s*\(\s*forbidden\s*\)|Flucht(?:flug)?\s*\(\s*verboten\s*\))\s*:\s*([^<\r\n]+)",
        re.IGNORECASE,
    ),
    "COLONIZE": re.compile(
        r"\b(?:(?:Colonize|Colonisation|Colonization)\s*\(\s*forbidden\s*\)|Kolonisierung\s*\(\s*verboten\s*\))\s*:\s*([^<\r\n]+)",
        re.IGNORECASE,
    ),
    "STATION": re.compile(
        r"\b(?:(?:Station(?:ing)?|Deploy(?:ment)?)\s*\(\s*forbidden\s*\)|Stationier(?:en|ung)\s*\(\s*verboten\s*\))\s*:\s*([^<\r\n]+)",
        re.IGNORECASE,
    ),
    "TRANSPORT": re.compile(
        r"\b(?:Transport\s*\(\s*forbidden\s*\)|Transport(?:ieren)?\s*\(\s*verboten\s*\))\s*:\s*([^<\r\n]+)",
        re.IGNORECASE,
    ),
}
_STATION_SHIP_TOKEN_RE = re.compile(r"\b(\d{1,9})\s*x\s*(s\d{1,3})\b", re.IGNORECASE | re.ASCII)
_BASH_RESET_TIMER_RE = re.compile(r"(?:Reset\s+in|Zur[üu]cksetzen\s+in)\s+(\d{1,3}):(\d{2}):(\d{2})", re.IGNORECASE)
_PLAYER_PROTECTION_BLOCK_RE = re.compile(
    r"(?:"
    r"this\s+(?:user|player)\s+can(?:'t|\s*not)\s+be\s+(?:attacked|spied)"
    r"|(?:user|player)\s+can(?:'t|\s*not)\s+be\s+(?:attacked|spied)"
    r"|dieser\s+spieler\s+kann\s+nicht\s+(?:angegriffen|ausspioniert)\s+werden"
    r"|spieler\s+kann\s+nicht\s+(?:angegriffen|ausspioniert)\s+werden"
    r"|target\s+is\s+in\s+(?:vacation\s+mode|urlaubsmodus|newbie\s+protection|noob\s+protection)"
    r"|player\s+is\s+in\s+(?:vacation\s+mode|urlaubsmodus|newbie\s+protection|noob\s+protection)"
    r"|(?:out\s+of|outside)\s+(?:your\s+)?attack\s*zone(?:\s+range)?"
    r"|au[ßs]erhalb\s+(?:deiner\s+|ihrer\s+)?angriffszone"
    r")!?",
    re.IGNORECASE,
)
_FLEET_SLOT_STATUS_RE = re.compile(
    r"(?:Fleet\s+bases|Flottenst(?:ü|ue)tzpunkte)\s*\(\s*"
    r"(?:available|verf(?:ü|ue)gbar)\s*:\s*(\d+)\s*,\s*"
    r"(?:filled|besetzt|belegt)\s*:\s*(\d+)\s*\)",
    re.IGNORECASE,
)
_PROTECTION_COOLDOWN_TOKENS: tuple[str, ...] = (
    "vacation",
    "urlaub",
    "urlaubsmodus",
    "newbie",
    "noob",
    "spectral",
    "attackzone",
    "attack zone",
    "angriffszone",
    "zone",
    "strong",
    "weak",
    "can't be attacked",
    "cannot be attacked",
)


def parse_station_ships_spec(text: str) -> dict[str, int]:
    """Extract ``{ship_key: qty}`` pairs from a formatted ``STATION`` label."""
    out: dict[str, int] = {}
    for match in _STATION_SHIP_TOKEN_RE.finditer(str(text or "")):
        qty = max(0, coerce_int(match.group(1), 0))
        skey = match.group(2).lower()
        if qty > 0 and skey in SHIP_SLUG_BY_KEY and skey != "s9":
            out[skey] = out.get(skey, 0) + qty
    return out


def format_station_ships_spec(ships: Mapping[str, int]) -> str:
    """Format ``{ship_key: qty}`` into canonical ``Nx s11, Mx s4`` order."""
    parts = [
        f"{fmt_compact_int(int(qty))}x {skey}"
        for skey, qty in sorted(
            ships.items(),
            key=lambda kv: (int(kv[0][1:]) if kv[0].startswith("s") and kv[0][1:].isdigit() else 999, kv[0]),
        )
        if int(qty) > 0 and skey in SHIP_SLUG_BY_KEY and skey != "s9"
    ]
    return ", ".join(parts)


def parse_send_fleet_mission_block(
    html: str,
    mission_kind: str,
) -> tuple[str, float, bool] | None:
    """Parse mission-specific ``(forbidden)``, player-protection, or bash-protection banners."""
    if not isinstance(html, str) or not html.strip():
        return None
    mk = safe_key(mission_kind).strip().upper()
    pat = _MISSION_FORBIDDEN_PATTERNS.get(mk)
    if pat is None:
        return None

    soup = BeautifulSoup(html, "html.parser")
    lines = [
        collapse_ws(raw_ln).strip()
        for raw_ln in soup.get_text("\n", strip=True).splitlines()
        if raw_ln and raw_ln.strip()
    ]
    joined_lines = "\n".join(lines)
    m_forb = pat.search(joined_lines)
    if m_forb is None and mk in ("ATTACK", "SPY"):
        m_prot = _PLAYER_PROTECTION_BLOCK_RE.search(joined_lines)
        if m_prot is not None:
            return (collapse_ws(m_prot.group(0)).strip()[:240], 21600.0, False)
    if m_forb is None and mk == "RECYCLE":
        low_all = joined_lines.lower()
        for phrase in ("no rubble field", "no debris field", "kein trümmerfeld", "kein truemmerfeld"):
            if phrase in low_all:
                return (f"Recycle (forbidden): {phrase}", 1800.0, False)
    if m_forb is None:
        return None

    full_match = collapse_ws(m_forb.group(0)).strip()
    m_reset = _BASH_RESET_TIMER_RE.search(joined_lines)
    reset_seconds: float | None = None
    reset_label = ""
    if m_reset is not None:
        hh, mm, ss = int(m_reset.group(1)), int(m_reset.group(2)), int(m_reset.group(3))
        reset_seconds = float(hh * 3600 + mm * 60 + ss) + 30.0
        reset_label = f"{hh:02d}:{mm:02d}:{ss:02d}"

    low_match = full_match.lower()
    low_page = joined_lines.lower()
    is_bash = mk == "ATTACK" and (
        "bash" in low_match
        or "too often" in low_match
        or "zu oft" in low_match
        or ("attack-bash-regulations" in low_page and m_reset is not None)
    )
    if reset_seconds is not None and reset_seconds > 0.0:
        cooldown_s = min(7 * 86400.0, max(60.0, reset_seconds))
    elif is_bash:
        cooldown_s = 86400.0
    elif any(tok in low_match for tok in _PROTECTION_COOLDOWN_TOKENS):
        cooldown_s = 21600.0
    elif mk == "RECYCLE":
        cooldown_s = 1800.0
    else:
        cooldown_s = 3600.0

    reason_out = f"{full_match} (Reset in {reset_label})" if reset_label else full_match
    return (reason_out[:240], cooldown_s, is_bash)


def parse_internal_fleet_slot_status(html: str) -> tuple[int, int] | None:
    """Parse ``Fleet bases (available: N, filled: M)`` from ``internalFleet`` HTML."""
    if not html:
        return None
    soup = BeautifulSoup(html, "html.parser")
    match = _FLEET_SLOT_STATUS_RE.search(tag_text(soup))
    if not match:
        return None
    avail = coerce_int(match.group(1), -1)
    filled = coerce_int(match.group(2), -1)
    return (avail, filled) if (avail >= 0 and filled >= 0) else None


def extract_internal_fleet_planet_coords(html: str) -> str:
    """Extract active planet coordinates (``GG:SSS:PP``) from ``internalFleet`` header/filter."""
    if not html:
        return ""
    soup = BeautifulSoup(html, "html.parser")
    p_el = soup.select_one("#planet-coords")
    if p_el is not None:
        m_pc = re.search(r"(\d{1,2}:\d{1,3}:\d{1,2})", p_el.get_text(" ", strip=True), re.ASCII)
        if m_pc:
            return normalize_coords(m_pc.group(1))
    m_only = re.search(
        r"(?:Only\s+flights\s+from\s+here|Nur\s+Fl(?:ü|ue)ge\s+von\s+hier)\s*:\s*[^\[\n]{0,80}\[\s*(\d{1,2}:\d{1,3}:\d{1,2})\s*\]",
        tag_text(soup),
        re.IGNORECASE,
    )
    return normalize_coords(m_only.group(1)) if m_only else ""
