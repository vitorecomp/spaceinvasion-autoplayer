"""Anti-bot detection, landing token parsing, and BitMeUp portal universe parsers."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
import math
import re
from typing import Optional
from urllib.parse import urljoin

from bs4 import BeautifulSoup, Tag

from backend.core import catalog as game_data
from backend.session._auth_models import (
    LoginTokens,
    SpaceInvasionAuthError,
    UniverseOption,
)

_CLOUDFLARE_INTERSTITIAL_PATTERNS = (
    re.compile(r"<title>\s*Just a moment", re.IGNORECASE),
    re.compile(r"Attention Required!\s*\|\s*Cloudflare", re.IGNORECASE),
)

_VISIBLE_CHALLENGE_TEXT_PATTERNS = (
    re.compile(r"Sicherheitspr(?:ü|ue)fung", re.IGNORECASE),
    re.compile(r"best(?:ä|ae)tigen,\s*dass\s+du\s+kein\s+Bot\s+bist", re.IGNORECASE),
    re.compile(r"confirm\s+that\s+you\s+are\s+not\s+a\s+bot", re.IGNORECASE),
)

_HIDDEN_INLINE_STYLE_RE = re.compile(
    r"(?:^|;)\s*(?:display\s*:\s*none|visibility\s*:\s*hidden)\b",
    re.IGNORECASE,
)

_ANTIBOT_FAST_MARKERS = (
    "turnstile",
    "cloudflare",
    "cf-challenge",
    "recaptcha",
    "h-captcha",
    "si-captcha-gate",
    "sicherheitspr",
    "kein bot",
    "not a bot",
)

_PORTAL_FIELDS_RE = re.compile(
    r"bitmeup\.initPortalClient\(\s*\d+\s*,\s*(['\"])([^'\"]+)\1\.split\(\s*['\"]\|['\"]\s*\)",
    re.ASCII,
)
_SID_RE = re.compile(r"bitmeup\.sid\s*=\s*(['\"])(?:sid=)?([^'\"]+)\1")


def detect_antibot_challenge(status_code: int, html: str) -> Optional[str]:
    """Return a diagnostic label if the response presents a visible CAPTCHA/anti-bot gate."""
    if not isinstance(html, str) or not html:
        return None
    for pattern in _CLOUDFLARE_INTERSTITIAL_PATTERNS:
        if pattern.search(html):
            return f"Anti-bot or CAPTCHA challenge detected ({pattern.pattern})"
    low = html.lower()
    if status_code in (403, 503) and "cloudflare" in low:
        return f"Blocked by Cloudflare gate (HTTP {status_code})"
    if not any(marker in low for marker in _ANTIBOT_FAST_MARKERS):
        return None

    soup = BeautifulSoup(html, "html.parser")
    for non_vis in soup.find_all(["script", "style", "noscript", "template", "link"]):
        non_vis.decompose()

    to_decompose: list[Tag] = []
    for el in soup.find_all(True):
        if not isinstance(el, Tag) or el.attrs is None:
            continue
        if el.has_attr("hidden"):
            to_decompose.append(el)
            continue
        aria_h = str(el.get("aria-hidden") or "").strip().lower()
        if aria_h == "true":
            to_decompose.append(el)
            continue
        style_v = str(el.get("style") or "")
        if style_v and _HIDDEN_INLINE_STYLE_RE.search(style_v):
            to_decompose.append(el)
    for el in to_decompose:
        el.decompose()

    for selector, label in (
        ("#si-captcha-gate", "si-captcha-gate"),
        (".cf-turnstile, [class*='cf-turnstile']", "cf-turnstile"),
        (".cf-challenge, [id*='cf-challenge'], [class*='cf-challenge']", "cf-challenge"),
        (".g-recaptcha, [class*='g-recaptcha']", "g-recaptcha"),
        (".h-captcha, [class*='h-captcha']", "h-captcha"),
        ("iframe[src*='challenges.cloudflare.com']", "challenges.cloudflare.com"),
    ):
        if soup.select_one(selector) is not None:
            return f"Anti-bot or CAPTCHA challenge detected ({label})"

    visible_text = " ".join(soup.get_text(" ", strip=True).split())
    if visible_text:
        for pattern in _VISIBLE_CHALLENGE_TEXT_PATTERNS:
            if pattern.search(visible_text):
                return f"Anti-bot or CAPTCHA challenge detected ({pattern.pattern})"
    return None


def parse_login_tokens(html: str) -> LoginTokens:
    """Extract dynamic login field names and `sid` from the landing page HTML."""
    fields_match = _PORTAL_FIELDS_RE.search(html)
    if not fields_match:
        raise SpaceInvasionAuthError("Could not find bitmeup.initPortalClient login field tokens on landing page.")
    fields = fields_match.group(2).split("|")
    if len(fields) != 3 or any(not part for part in fields):
        raise SpaceInvasionAuthError(f"Unexpected login token tuple length ({len(fields)}); expected 3 fields.")

    sid_match = _SID_RE.search(html)
    if not sid_match or not sid_match.group(2):
        raise SpaceInvasionAuthError("Could not find bitmeup.sid on landing page.")

    return LoginTokens(
        submit_field=fields[0],
        email_field=fields[1],
        password_field=fields[2],
        sid=sid_match.group(2),
    )


def _split_js_object_array(array_body: str) -> list[str]:
    """Split top-level `{...}` items inside a JS array literal, respecting strings."""
    items: list[str] = []
    depth = 0
    quote: Optional[str] = None
    escaped = False
    start: Optional[int] = None

    for idx, ch in enumerate(array_body):
        if quote is not None:
            if escaped:
                escaped = False
            elif ch == "\\":
                escaped = True
            elif ch == quote:
                quote = None
            continue
        if ch in ("'", '"'):
            quote = ch
        elif ch == "{":
            if depth == 0:
                start = idx
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0 and start is not None:
                items.append(array_body[start : idx + 1])
                start = None

    return items


def _extract_balanced_block(text: str, start_idx: int, open_ch: str, close_ch: str) -> Optional[str]:
    """Return content inside balanced `open_ch`/`close_ch` delimiters starting at `start_idx`."""
    if start_idx < 0 or start_idx >= len(text) or text[start_idx] != open_ch:
        return None
    depth = 0
    quote: Optional[str] = None
    escaped = False
    for idx in range(start_idx, len(text)):
        ch = text[idx]
        if quote is not None:
            if escaped:
                escaped = False
            elif ch == "\\":
                escaped = True
            elif ch == quote:
                quote = None
            continue
        if ch in ("'", '"'):
            quote = ch
        elif ch == open_ch:
            depth += 1
        elif ch == close_ch:
            depth -= 1
            if depth == 0:
                return text[start_idx + 1 : idx]
    return None


def safe_portal_speed(raw: object) -> Optional[float]:
    if raw is None:
        return None
    if isinstance(raw, (bool, Mapping, list, tuple, set, frozenset)):
        return 1.0
    if isinstance(raw, (bytes, bytearray)):
        raw = bytes(raw).decode("utf-8", errors="replace")
    if isinstance(raw, str):
        txt = raw.strip()
        if not txt or not txt.isascii():
            return 1.0
        raw = txt
    if not isinstance(raw, (int, float, str)):
        return 1.0
    try:
        val = float(raw)
    except (TypeError, ValueError, OverflowError):
        return 1.0
    return val if math.isfinite(val) else 1.0


def safe_portal_label(raw_name: object) -> str:
    if isinstance(raw_name, int) and not isinstance(raw_name, bool):
        if abs(raw_name).bit_length() >= 12000:
            sign = "-" if raw_name < 0 else ""
            digits = int(abs(raw_name).bit_length() * 0.30103)
            return f"{sign}~1e{digits}"
        return str(raw_name)
    return game_data.safe_key(raw_name)


_safe_portal_speed = safe_portal_speed
_safe_portal_label = safe_portal_label


def _coerce_portal_uid(raw_id: object) -> int | None:
    if raw_id is None or isinstance(raw_id, (bool, Mapping, list, tuple, set, frozenset)):
        return None
    if isinstance(raw_id, float) and not math.isfinite(raw_id):
        return None
    val: object = bytes(raw_id).decode("utf-8", errors="replace") if isinstance(raw_id, (bytes, bytearray)) else raw_id
    if isinstance(val, str):
        txt_id = val.strip()
        if not txt_id or not txt_id.isascii():
            return None
        val = txt_id
    if not isinstance(val, (int, float, str)):
        return None
    try:
        uid = int(val)
    except (TypeError, ValueError, OverflowError):
        return None
    if uid < 0 or uid.bit_length() > 1000:
        return None
    return uid


def universes_from_portal_data(
    sid: str,
    instances: Sequence[Mapping[str, object]],
    base_url: str,
) -> tuple[UniverseOption, ...]:
    """Build `UniverseOption` entries from runtime or parsed `SI_TPL.startPortal` data."""
    raw_inst: object = instances
    if raw_inst is None or isinstance(raw_inst, (str, bytes, bytearray, Mapping)):
        return ()
    try:
        iter_items = iter(instances)
    except TypeError:
        return ()

    clean_sid = game_data.safe_key(sid).strip() if sid is not None else ""
    if clean_sid.startswith("sid="):
        clean_sid = clean_sid[4:]

    options: list[UniverseOption] = []
    for item in iter_items:
        if not isinstance(item, Mapping):
            continue
        raw_name = item.get("name")
        if not raw_name:
            continue
        uid = _coerce_portal_uid(item.get("id"))
        if uid is None:
            continue
        acc_val = item.get("acc")
        if isinstance(acc_val, Mapping):
            has_acc = bool(acc_val)
        elif isinstance(acc_val, str):
            has_acc = bool(acc_val.strip())
        else:
            has_acc = bool(acc_val)
        sid_q = f"&sid={clean_sid}" if clean_sid else ""
        launch_url = urljoin(base_url, f"?action=externalLogin&instance={uid}{sid_q}")
        options.append(
            UniverseOption(
                label=safe_portal_label(raw_name),
                url=launch_url,
                universe_id=uid,
                is_si2=bool(item.get("si2")),
                speed=safe_portal_speed(item.get("speed")),
                flight_speed=safe_portal_speed(item.get("flightSpeed")),
                has_account=has_acc,
            )
        )
    return tuple(options)


def _extract_portal_js_universes(html: str, base_url: str) -> tuple[UniverseOption, ...]:
    portal_match = re.search(r"SI_TPL\.startPortal\s*=\s*\{", html)
    if not portal_match:
        return ()
    block = _extract_balanced_block(html, portal_match.end() - 1, "{", "}") or ""
    sid_m = re.search(r"\bSID\s*:\s*(['\"])(?:sid=)?([^'\"]+)\1", block)
    inst_key = re.search(r"\binstances\s*:\s*\[", block)
    if not inst_key:
        return ()
    inst_body = _extract_balanced_block(block, inst_key.end() - 1, "[", "]") or ""
    sid = sid_m.group(2) if sid_m else ""
    portal_options: list[UniverseOption] = []
    for item in _split_js_object_array(inst_body):
        id_m = re.search(r"\bid\s*:\s*(\d+)", item, re.ASCII)
        name_m = re.search(r"\bname\s*:\s*(['\"])([^'\"]+)\1", item)
        if not id_m or not name_m:
            continue
        try:
            uid = int(id_m.group(1))
        except (ValueError, OverflowError):
            continue
        if uid < 0 or uid.bit_length() > 1000:
            continue
        si2_m = re.search(r"\bsi2\s*:\s*(true|false)", item)
        spd_m = re.search(r"\bspeed\s*:\s*([0-9.]+)", item)
        fspd_m = re.search(r"\bflightSpeed\s*:\s*([0-9.]+)", item)
        acc_obj_m = re.search(r"\bacc\s*:\s*\{", item)
        acc_str_m = re.search(r"\bacc\s*:\s*(['\"])(.*?)\1", item)
        acc_bool_m = re.search(r"\bacc\s*:\s*(true)\b", item)
        has_acc = bool(acc_obj_m or acc_bool_m or (acc_str_m and acc_str_m.group(2).strip()))
        sid_q = f"&sid={sid}" if sid else ""
        portal_options.append(
            UniverseOption(
                label=name_m.group(2),
                url=urljoin(base_url, f"?action=externalLogin&instance={uid}{sid_q}"),
                universe_id=uid,
                is_si2=bool(si2_m and si2_m.group(1) == "true"),
                speed=safe_portal_speed(spd_m.group(1)) if spd_m else None,
                flight_speed=safe_portal_speed(fspd_m.group(1)) if fspd_m else None,
                has_account=has_acc,
            )
        )
    return tuple(portal_options)


def extract_universes(html: str, base_url: str) -> tuple[UniverseOption, ...]:
    """Extract SpaceInvasion universes from `SI_TPL.startPortal` or fallback links."""
    portal_options = _extract_portal_js_universes(html, base_url)
    if portal_options:
        return portal_options

    soup = BeautifulSoup(html, "html.parser")
    seen: set[tuple[str, str]] = set()
    options: list[UniverseOption] = []

    for anchor in soup.find_all("a", href=True):
        if not isinstance(anchor, Tag):
            continue
        href = str(anchor.get("href") or "").strip()
        if not href or href.startswith("#") or href.lower().startswith("javascript:"):
            continue
        lower_href = href.lower()
        text = " ".join(anchor.get_text(" ", strip=True).split())
        if any(marker in lower_href for marker in ("uni", "universe", "world", "play", "login", "game")):
            abs_url = urljoin(base_url, href)
            label = text or abs_url
            key = (label, abs_url)
            if key not in seen:
                seen.add(key)
                options.append(UniverseOption(label=label, url=abs_url))

    return tuple(options)


__all__ = [
    "_extract_balanced_block",
    "_safe_portal_label",
    "_safe_portal_speed",
    "_split_js_object_array",
    "detect_antibot_challenge",
    "extract_universes",
    "parse_login_tokens",
    "safe_portal_label",
    "safe_portal_speed",
    "universes_from_portal_data",
]
