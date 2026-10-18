"""Pure stdlib parser for SpaceInvasion `sendFleet` forbidden/bash/protection banners."""

from __future__ import annotations

from html.parser import HTMLParser
import re

from spaceinvasion.core.catalog import safe_key

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
        r"\b(?:(?:Recycl(?:e|ing)|Harvest|Rubble(?:\s+field)?|Debris(?:\s+field)?)\s*\(\s*forbidden\s*\)"
        r"|(?:Abbau|Recycl(?:e|ing)|Tr[üu]mmerfeld)\s*\(\s*verboten\s*\))\s*:\s*([^<\r\n]+)",
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

_BASH_RESET_TIMER_RE = re.compile(
    r"(?:Reset\s+in|Zur[üu]cksetzen\s+in)\s+(\d{1,3}):(\d{2}):(\d{2})",
    re.IGNORECASE,
)

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


class _TextLinesParser(HTMLParser):
    """Stdlib HTML-to-lines extractor matching BeautifulSoup `get_text('\\n', strip=True)`."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._skip_depth = 0
        self._chunks: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.lower() in ("script", "style"):
            self._skip_depth += 1
        elif self._skip_depth == 0:
            self._chunks.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() in ("script", "style"):
            self._skip_depth = max(0, self._skip_depth - 1)
        elif self._skip_depth == 0:
            self._chunks.append("\n")

    def handle_data(self, data: str) -> None:
        if self._skip_depth == 0 and data:
            self._chunks.append(data)

    def joined_lines(self) -> str:
        raw_text = "".join(self._chunks)
        lines = [" ".join(ln.split()).strip() for ln in raw_text.splitlines() if ln and ln.strip()]
        return "\n".join(lines)


def parse_send_fleet_mission_block(html: str, mission_kind: str) -> tuple[str, float, bool] | None:
    """Parse mission-specific `(forbidden)`, player-protection, or bash-protection banners.

    Returns `(reason, cooldown_seconds, is_bash_block)` or `None` when not forbidden.
    """
    if not isinstance(html, str) or not html.strip():
        return None
    mk = safe_key(mission_kind).strip().upper()
    pat = _MISSION_FORBIDDEN_PATTERNS.get(mk)
    if pat is None:
        return None

    parser = _TextLinesParser()
    parser.feed(html)
    parser.close()
    joined_lines = parser.joined_lines()

    m_forb = pat.search(joined_lines)
    if m_forb is None and mk in ("ATTACK", "SPY"):
        m_prot = _PLAYER_PROTECTION_BLOCK_RE.search(joined_lines)
        if m_prot is not None:
            prot_msg = " ".join(m_prot.group(0).split()).strip()
            return (prot_msg[:240], 21600.0, False)
    if m_forb is None and mk == "RECYCLE":
        low_all = joined_lines.lower()
        for phrase in ("no rubble field", "no debris field", "kein trümmerfeld", "kein truemmerfeld"):
            if phrase in low_all:
                return (f"Recycle (forbidden): {phrase}", 1800.0, False)
    if m_forb is None:
        return None

    full_match = " ".join(m_forb.group(0).split()).strip()
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
