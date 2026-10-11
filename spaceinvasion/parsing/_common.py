"""Shared internal HTML, timer, and coordinate-filter helpers for ``spaceinvasion.parsing``."""

from __future__ import annotations

from collections.abc import Mapping
import math
import os
import re

from bs4 import BeautifulSoup, Tag

from spaceinvasion.core.coercion import fmt_compact_int, safe_key

_SAFE_ERRORS = (TypeError, ValueError, OSError, RuntimeError, AttributeError, OverflowError)

HMS_TIMER_RE: re.Pattern[str] = re.compile(
    r"\b(?:(\d{1,3}):)?(\d{1,2}):(\d{2}):(\d{2})\b|\b(\d{1,3}):(\d{2})\b",
    re.ASCII,
)
_QUEUE_LEADING_JUNK_RE = re.compile(r"^(?:(?:cancel|abort|abbrechen|annuler)\s+)+", re.IGNORECASE | re.ASCII)
_QUEUE_TRAILING_JUNK_RE = re.compile(r"(?:\s+(?:cancel|abort|abbrechen|annuler))+$", re.IGNORECASE | re.ASCII)

GALAXY_COORDS_TRIPLE_RE = re.compile(r"\b(\d{1,2}):(\d{1,3}):(\d{1,2})\b", re.ASCII)
SPY_BRACKETED_COORDS_RE = re.compile(r"\[\s*(\d{1,2}):(\d{1,3}):(\d{1,2})\s*\]", re.ASCII)
COORD_CONTEXT_PREFIX_RE = re.compile(
    r"(?:^|[\s(\[\]|,/;-])"
    r"(?:"
    r"targets?|ziels?|zielplanet(?:en)?"
    r"|planets?|planeten|asteroids?|monde?|moons?"
    r"|coords?|coordinates?|koord(?:inaten)?"
    r"|galax(?:y|ie)|solar\s*system|system|sonnensystem"
    r"|pos(?:ition)?"
    r"|location|ort|origin|destination|herkunft"
    r"|(?:espionage|spy)\s+report\s+(?:on|for)|spying\s+(?:on|for)"
    r"|spionagebericht\s+(?:auf|f(?:[üu]|ue)r|(?:[üu]|ue)ber|von)"
    r"|(?:attack|raid|probe|spy|spied|angriff|spionage)\s+(?:on|auf)"
    r"|besuchen"
    r")\s*(?:[:=\-]\s*)?\[?\s*$",
    re.IGNORECASE,
)
GALAXY_PAIR_NOISE_PREFIX_RE = re.compile(
    r"(?:server\s*time|report\s*time|date|datum|timestamp|sent|gesendet|time|clock|uhr|zeit|timer|countdown|eta|updated|stand|fields?|felder|arrival|ankunft)\s*:?\s*[\(\[]?\s*$",
    re.IGNORECASE,
)
GALAXY_TRIPLE_NOISE_SUFFIX_RE = re.compile(
    r"^\s*\)?\s*(?:utc|gmt|cet|cest|mez|mesz|uhr|zeit|am|pm)\b",
    re.IGNORECASE,
)
_GALAXY_PAREN_CLOCK_NOISE_RE = re.compile(
    r"\b(?:server|time|report|date|datum|timestamp|sent|gesendet|clock|uhr|zeit|timer|countdown|eta|updated|stand|utc|gmt|cet|cest|mez|mesz|am|pm)\b",
    re.IGNORECASE,
)


def collapse_ws(text: str) -> str:
    return " ".join(text.split())


def tag_text(node: Tag | BeautifulSoup) -> str:
    return collapse_ws(node.get_text(" ", strip=True))


def attr_str(tag: Tag, attr: str) -> str:
    raw = tag.get(attr)
    if raw is None:
        return ""
    if isinstance(raw, list):
        return " ".join(str(item) for item in raw)
    return str(raw)


def attr_opt_str(tag: Tag, attr: str) -> str | None:
    raw = tag.get(attr)
    if raw is None:
        return None
    if isinstance(raw, list):
        return " ".join(str(item) for item in raw)
    return str(raw)


def attr_tokens(tag: Tag, attr: str) -> list[str]:
    raw = tag.get(attr)
    if raw is None:
        return []
    if isinstance(raw, list):
        return [str(item) for item in raw if item]
    return [tok for tok in str(raw).split() if tok]


def safe_text(value: object, default: str = "") -> str:
    if value is None:
        return default
    if isinstance(value, os.PathLike):
        try:
            value = os.fspath(value)
        except _SAFE_ERRORS:
            return safe_key(value)
        if value is None:
            return default
    if isinstance(value, str):
        return value
    if isinstance(value, int) and not isinstance(value, bool):
        return fmt_compact_int(value) if abs(value).bit_length() >= 12_000 else str(value)
    if isinstance(value, (bytes, bytearray)):
        return bytes(value).decode("utf-8", errors="replace")
    return safe_key(value)


def clean_queue_item_name(raw: str) -> str:
    cleaned = _QUEUE_TRAILING_JUNK_RE.sub("", _QUEUE_LEADING_JUNK_RE.sub("", collapse_ws(raw)))
    return cleaned.strip()


def parse_hms_seconds(text: object) -> float | None:
    txt = safe_text(text)
    if not txt:
        return None
    match = HMS_TIMER_RE.search(txt)
    if not match:
        return None
    if match.group(4) is not None:
        days = int(match.group(1)) if match.group(1) else 0
        total = float(days * 86400 + int(match.group(2)) * 3600 + int(match.group(3)) * 60 + int(match.group(4)))
        return total if total >= 0.0 else None
    if match.group(5) is not None and match.group(6) is not None:
        total = float(int(match.group(5)) * 60 + int(match.group(6)))
        return total if total >= 0.0 else None
    return None


def format_hms_seconds(seconds: float | None) -> str:
    if seconds is None or not math.isfinite(seconds):
        return ""
    total_s = max(0, int(round(seconds)))
    if total_s >= 86400:
        days, rem = divmod(total_s, 86400)
        hours, rem = divmod(rem, 3600)
        mins, secs = divmod(rem, 60)
        return f"{days}d {hours:02d}:{mins:02d}:{secs:02d}"
    hours, rem = divmod(total_s, 3600)
    mins, secs = divmod(rem, 60)
    return f"{hours:02d}:{mins:02d}:{secs:02d}"


def _valid_bounded_coord(val: object, *, upper: int, max_digits: int) -> int | None:
    if isinstance(val, os.PathLike):
        try:
            val = os.fspath(val)
        except _SAFE_ERRORS:
            return None
    if val is None or isinstance(val, (bool, Mapping, list, tuple, set, frozenset)):
        return None
    if isinstance(val, int):
        return val if 1 <= val <= upper else None
    if isinstance(val, (bytes, bytearray)):
        val = bytes(val).decode("utf-8", errors="replace")
    try:
        text = str(val).strip()
    except _SAFE_ERRORS:
        return None
    if not text or not (text.isascii() and text.isdigit()):
        return None
    trimmed = text.lstrip("0") or "0"
    if len(trimmed) > max_digits:
        return None
    num = int(trimmed)
    return num if 1 <= num <= upper else None


def valid_galaxy(val: object) -> int | None:
    return _valid_bounded_coord(val, upper=99, max_digits=3)


def valid_system(val: object) -> int | None:
    return _valid_bounded_coord(val, upper=999, max_digits=4)


def is_clock_0m_triple(g_str: str, s_str: str, p_str: str) -> bool:
    return (
        len(g_str) in (1, 2) and len(s_str) == 2 and s_str.startswith("0") and len(p_str) == 2 and 0 <= int(g_str) <= 23
    )


def is_clock_shaped_triple(g_str: str, s_str: str, p_str: str) -> bool:
    return len(g_str) == 2 and len(s_str) == 2 and len(p_str) == 2 and 0 <= int(g_str) <= 23 and 0 <= int(s_str) <= 59


def is_parenthesized_noise_triple(text: str, m_start: int, m_end: int) -> bool:
    before_ch = text[m_start - 1] if m_start > 0 else ""
    after_ch = text[m_end] if m_end < len(text) else ""
    if before_ch == "(" and after_ch == ")":
        return True
    open_idx = -1
    for idx in range(m_start - 1, -1, -1):
        ch = text[idx]
        if ch in (")", "[", "]"):
            break
        if ch == "(":
            open_idx = idx
            break
    if open_idx == -1:
        return False
    close_idx = -1
    for idx in range(m_end, len(text)):
        ch = text[idx]
        if ch in ("(", "[", "]"):
            break
        if ch == ")":
            close_idx = idx
            break
    if close_idx == -1:
        return False
    if _GALAXY_PAREN_CLOCK_NOISE_RE.search(text[open_idx + 1 : close_idx]):
        return True
    left_in = text[open_idx + 1 : m_start].strip()
    right_in = text[m_end:close_idx].strip()
    return bool(
        (not left_in and right_in.startswith(("-", "—", "|", "@")))
        or (not right_in and left_in.endswith(("-", "—", "|", "@", ":")))
    )


def _match_is_noisy_coord(
    text: str, start: int, end: int, g_str: str, s_str: str, p_str: str
) -> tuple[bool, bool, bool]:
    if (
        is_parenthesized_noise_triple(text, start, end)
        or GALAXY_PAIR_NOISE_PREFIX_RE.search(text[:start])
        or GALAXY_TRIPLE_NOISE_SUFFIX_RE.match(text[end:])
    ):
        return True, False, False
    is_clock = is_clock_shaped_triple(g_str, s_str, p_str) or is_clock_0m_triple(g_str, s_str, p_str)
    has_ctx = bool(COORD_CONTEXT_PREFIX_RE.search(text[:start]))
    if is_clock and not has_ctx:
        return True, is_clock, has_ctx
    return False, is_clock, has_ctx


def extract_spy_coords(text: str) -> str:
    candidates: list[tuple[int, int, int, str]] = []
    bracketed_spans: list[tuple[int, int]] = []
    for match in SPY_BRACKETED_COORDS_RE.finditer(text):
        bracketed_spans.append((match.start(), match.end()))
        g_s, s_s, p_s = match.group(1), match.group(2), match.group(3)
        if valid_galaxy(g_s) is None or valid_system(s_s) is None or not (1 <= int(p_s) <= 16):
            continue
        noisy, is_clock, has_ctx = _match_is_noisy_coord(text, match.start(), match.end(), g_s, s_s, p_s)
        if not noisy:
            candidates.append((0 if has_ctx else 1, 1 if is_clock else 0, 0, f"{g_s}:{s_s}:{p_s}"))
    for match in GALAXY_COORDS_TRIPLE_RE.finditer(text):
        start, end = match.start(), match.end()
        if any(bs <= start and end <= be for bs, be in bracketed_spans):
            continue
        g_s, s_s, p_s = match.group(1), match.group(2), match.group(3)
        if valid_galaxy(g_s) is None or valid_system(s_s) is None or not (1 <= int(p_s) <= 16):
            continue
        noisy, is_clock, has_ctx = _match_is_noisy_coord(text, start, end, g_s, s_s, p_s)
        if not noisy:
            candidates.append((0 if has_ctx else 1, 1 if is_clock else 0, 1, match.group(0)))
    if not candidates:
        return ""
    candidates.sort(key=lambda item: (item[0], item[1], item[2]))
    return candidates[0][3]


def extract_spy_link_coords(soup: Tag | BeautifulSoup | None) -> str:
    if soup is None:
        return ""
    for anchor in soup.find_all("a"):
        if not isinstance(anchor, Tag):
            continue
        meta = f"{attr_str(anchor, 'href')} {attr_str(anchor, 'onclick')}".lower()
        if not any(
            tok in meta
            for tok in ("internalgalaxy", "internalfleet", "p_galaxy", "sendspy", "gal=", "galaxy=", "sun=", "system=")
        ):
            continue
        a_txt = tag_text(anchor)
        if not a_txt:
            continue
        for match in GALAXY_COORDS_TRIPLE_RE.finditer(a_txt):
            g_s, s_s, p_s = match.group(1), match.group(2), match.group(3)
            if valid_galaxy(g_s) is None or valid_system(s_s) is None or not (1 <= int(p_s) <= 16):
                continue
            noisy, _, _ = _match_is_noisy_coord(a_txt, match.start(), match.end(), g_s, s_s, p_s)
            if not noisy:
                return match.group(0)
    return ""
