"""Pure HTML/text error, protection-banner, and form-selector helpers for fleet dispatch."""

from __future__ import annotations

from collections.abc import Mapping
import logging
import re

from bs4 import BeautifulSoup

from backend.core import catalog
from backend.core.redaction import redact_sensitive
from backend.parsing.fleet_page import parse_send_fleet_mission_block

_LOGGER = logging.getLogger(__name__)

_PROTECTION_AND_AZ_KEYWORDS: tuple[str, ...] = (
    "attackzone",
    "attack zone",
    "angriffszone",
    "vacation",
    "urlaub",
    "urlaubsmodus",
    "newbie",
    "noob",
    "bash",
    "can't be attacked",
    "cannot be attacked",
    "can not be attacked",
    "can't be spied",
    "cannot be spied",
    "nicht angegriffen",
    "nicht ausspioniert",
    "protection",
)

_SERVER_ERROR_SELECTORS: tuple[str, ...] = (
    ".error",
    ".errormessage",
    ".fehler",
    ".alert-danger",
    'font[color="red"]',
    '[style*="color:red"]',
    '[style*="color: red"]',
    '[style*="color:#ff0000"]',
    '[style*="color: #ff0000"]',
)

_STRICT_ERROR_SELECTORS: frozenset[str] = frozenset((".error", ".errormessage", ".fehler", ".alert-danger"))

_SERVER_ERROR_ELEMENT_KEYWORDS: tuple[str, ...] = (
    "can't",
    "cannot",
    "can not",
    "error",
    "forbidden",
    "verboten",
    "kann nicht",
    "not enough",
    "protection",
    "vacation",
    "urlaubsmodus",
    "attackzone",
    "attack zone",
    "angriffszone",
    "bash",
)

_SERVER_ERROR_PHRASES: tuple[str, ...] = (
    "this user can't be attacked!",
    "this user can't be attacked",
    "this user cannot be attacked",
    "this player can't be attacked",
    "this player cannot be attacked",
    "can't be attacked",
    "cannot be attacked",
    "can not be attacked",
    "dieser spieler kann nicht angegriffen werden",
    "kann nicht angegriffen werden",
    "not enough spice",
    "not enough deuterium",
    "not enough ships",
    "no free fleet slot",
    "too many fleets",
    "newbie protection",
    "noob protection",
    "vacation mode",
    "urlaubsmodus",
    "out of your attackzone range",
    "out of your attack zone range",
    "outside your attack zone",
    "outside attack zone",
    "attackzone",
    "angriffszone",
    "bash protection",
    "cannot attack",
    "can't attack",
    "cannot spy",
    "can't spy",
)

_FLEET_ERR_TOKEN_RE = re.compile(r"\bfleetErr\.[A-Za-z0-9_]+")
_SAFE_DOM_ID_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_-]*")
_SEND_FLEET_MISSION_TOKENS: tuple[str, ...] = (
    "attack",
    "spying",
    "espionage",
    "spionage",
    "mission",
    "transport",
)


def log_fleet_diag(event: str, details: Mapping[str, object]) -> None:
    """Emit a redacted structured diagnostic warning for rejected or incomplete fleet pages."""
    _LOGGER.warning("fleet_dispatch.%s: %s", event, redact_sensitive(dict(details)))


def is_unattackable_target_html(html: str) -> bool:
    """Return True when `html` or text contains AZ, vacation, newbie, bash, or target-protection notices."""
    if not html:
        return False
    low = str(html).lower()
    return any(kw in low for kw in _PROTECTION_AND_AZ_KEYWORDS)


def extract_fleet_server_error(html: str, mission_kind: str = "") -> str | None:
    """Extract a server-side fleet error code, alert banner, or known rejection phrase from `html`."""
    _ = mission_kind
    if not html:
        return None
    m_err = _FLEET_ERR_TOKEN_RE.search(html)
    if m_err:
        return m_err.group(0)
    soup = BeautifulSoup(html, "html.parser")
    for sel in _SERVER_ERROR_SELECTORS:
        el = soup.select_one(sel)
        if el is None:
            continue
        txt = " ".join(el.get_text(" ", strip=True).split())
        if not txt or len(txt) > 200:
            continue
        low_el = txt.lower()
        if sel in _STRICT_ERROR_SELECTORS or any(kw in low_el for kw in _SERVER_ERROR_ELEMENT_KEYWORDS):
            return txt[:160]
    low = " ".join(soup.get_text(" ", strip=True).split()).lower()
    for phrase in _SERVER_ERROR_PHRASES:
        if phrase in low:
            return phrase
    return None


def extract_mission_forbidden_reason(html: str, mission_kind: str = "ATTACK") -> str | None:
    """Extract a mission-specific `(forbidden)` banner or hostile target-protection error from `html`."""
    if not html:
        return None
    mk = catalog.safe_key(mission_kind).strip().upper()
    parsed_blk = parse_send_fleet_mission_block(html, mk)
    if parsed_blk is not None:
        return parsed_blk[0]
    srv_err = extract_fleet_server_error(html, mk)
    if mk not in ("ATTACK", "SPY"):
        if srv_err and not is_unattackable_target_html(srv_err):
            return srv_err
        return None
    if srv_err and is_unattackable_target_html(srv_err):
        return srv_err
    soup = BeautifulSoup(html, "html.parser")
    for raw_ln in soup.get_text("\n", strip=True).splitlines():
        ln = " ".join(raw_ln.split()).strip()
        if not ln:
            continue
        low_ln = ln.lower()
        if ("(forbidden)" in low_ln or "(verboten)" in low_ln) and "transport" not in low_ln:
            return ln[:240]
        if is_unattackable_target_html(low_ln) and len(ln) <= 240:
            return ln
    return srv_err


def pick_send_fleet_form_css(send_soup: BeautifulSoup) -> str:
    """Choose the tightest CSS selector for the Step 2 mission form on `internalSendFleet`."""
    if send_soup.select_one('form[action*="internalSendFleetFinish"]'):
        return 'form[action*="internalSendFleetFinish"]'
    send_forms = send_soup.select('form[action*="internalSendFleet"]')
    if send_forms and any(f.select('input[type="radio"]') for f in send_forms):
        if len(send_forms) == 1:
            return 'form[action*="internalSendFleet"]'
        for f in send_forms:
            if f.select('input[type="radio"]'):
                fid = str(f.get("id") or "").strip()
                if fid and _SAFE_DOM_ID_RE.fullmatch(fid):
                    return f"form#{fid}"
    if send_soup.select_one('form[name="fleet"]') and send_soup.select_one('form[name="fleet"] input[type="radio"]'):
        return 'form[name="fleet"]'
    if send_forms:
        return 'form[action*="internalSendFleet"]'
    if send_soup.select_one('form[name="fleet"]'):
        return 'form[name="fleet"]'
    for form_el in send_soup.find_all("form"):
        txt = form_el.get_text(" ", strip=True).lower()
        if form_el.select('input[type="radio"]') and any(tok in txt for tok in _SEND_FLEET_MISSION_TOKENS):
            fid = str(form_el.get("id") or "").strip()
            if fid and _SAFE_DOM_ID_RE.fullmatch(fid):
                return f"form#{fid}"
            faction = str(form_el.get("action") or "").strip()
            if faction:
                return f'form[action="{faction}"]'
    return "form"
