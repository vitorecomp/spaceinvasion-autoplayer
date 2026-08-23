"""Authentication and session management for SpaceInvasion Classic (BitMeUp)."""

from __future__ import annotations

from dataclasses import dataclass, field
import json
import math
import os
from pathlib import Path
import re
from typing import Any, Callable, Mapping, Optional
from urllib.parse import urljoin

from bs4 import BeautifulSoup
from dotenv import dotenv_values
import requests

from spaceinvasion import game_data

BASE_URL = "https://spaceinvasion.bitmeup.com"
DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)

LOGIN_ERROR_MESSAGES: dict[str, str] = {
    "b.external.login.error.1": "Invalid email or password.",
    "b.external.login.error.2": "Too many login attempts; please wait before retrying.",
    "b.external.login.error.3": "This account is currently locked.",
    "b.external.login.error.4": "This account has not been activated yet.",
    "b.external.login.error.5": "Proxy usage is not allowed by BitMeUp.",
    "b.external.login.error.6": "The 2-factor authentication code is incorrect.",
}

_ANTIBOT_PATTERNS = (
    re.compile(r"cf-turnstile", re.IGNORECASE),
    re.compile(r"challenges\.cloudflare\.com", re.IGNORECASE),
    re.compile(r"cf-challenge", re.IGNORECASE),
    re.compile(r"g-recaptcha", re.IGNORECASE),
    re.compile(r"h-captcha", re.IGNORECASE),
    re.compile(r"<title>\s*Just a moment", re.IGNORECASE),
    re.compile(r"Attention Required!\s*\|\s*Cloudflare", re.IGNORECASE),
)

_PORTAL_FIELDS_RE = re.compile(
    r"bitmeup\.initPortalClient\(\s*\d+\s*,\s*(['\"])([^'\"]+)\1\.split\(\s*['\"]\|['\"]\s*\)",
    re.ASCII,
)
_SID_RE = re.compile(r"bitmeup\.sid\s*=\s*(['\"])(?:sid=)?([^'\"]+)\1")


class SpaceInvasionAuthError(Exception):
    """Base error raised during authentication or session setup."""


class ConfigurationError(SpaceInvasionAuthError):
    """Raised when required credentials or configuration are missing."""


class AntiBotChallengeError(SpaceInvasionAuthError):
    """Raised when a CAPTCHA or anti-bot gate blocks automated access."""


class TwoFactorRequiredError(SpaceInvasionAuthError):
    """Raised when 2FA is required and no valid code was supplied."""


class LoginRejectedError(SpaceInvasionAuthError):
    """Raised when BitMeUp rejects credentials or returns an auth error."""


@dataclass(frozen=True)
class Credentials:
    """BitMeUp login credentials and optional target preferences."""

    email: str
    password: str
    account_name: str = "default"
    locale: str = "en"
    stay_logged_in: bool = True
    universe: Optional[str] = None
    two_factor_code: Optional[str] = None


@dataclass(frozen=True)
class LoginTokens:
    """Per-session dynamic field tokens and session identifier from landing HTML."""

    submit_field: str
    email_field: str
    password_field: str
    sid: str


@dataclass(frozen=True)
class UniverseOption:
    """Discovered universe entry point from the post-login portal page."""

    label: str
    url: str
    universe_id: Optional[int] = None
    is_si2: bool = False
    speed: Optional[float] = None
    flight_speed: Optional[float] = None
    has_account: bool = False


@dataclass(frozen=True)
class LoginResult:
    """Summary of a completed login attempt (free of secrets)."""

    account_name: str
    email: str
    landing_url: str
    final_url: str
    page_title: str
    cookie_path: Path
    cookie_names: tuple[str, ...]
    universes: tuple[UniverseOption, ...] = field(default_factory=tuple)


def _normalize_account_tag(account: Optional[str]) -> str:
    """Normalize an account profile name to safe uppercase env-var prefix."""
    if not account or account.strip().lower() == "default":
        return "default"
    cleaned = re.sub(r"[^A-Za-z0-9]+", "_", account.strip()).strip("_").upper()
    if not cleaned:
        raise ConfigurationError("Account name must contain alphanumeric characters.")
    return cleaned


def load_credentials(
    env_path: Path | str = Path(".env"),
    account: Optional[str] = None,
    environ: Optional[Mapping[str, str]] = None,
) -> Credentials:
    """Load credentials from a `.env` file (with process env overrides).

    Primary profile reads ``SPACEINVASION_EMAIL`` and ``SPACEINVASION_PASSWORD``.
    Named profiles (``--account ALT``) read ``SPACEINVASION_ALT_EMAIL`` and
    ``SPACEINVASION_ALT_PASSWORD``.
    """
    resolved_env = Path(env_path)
    merged: dict[str, str] = {}
    if resolved_env.exists():
        raw_values = dotenv_values(resolved_env)
        merged.update({k: v for k, v in raw_values.items() if v is not None})
    source_env = os.environ if environ is None else environ
    merged.update({k: v for k, v in source_env.items() if v is not None})

    tag = _normalize_account_tag(account)
    prefix = "SPACEINVASION_" if tag == "default" else f"SPACEINVASION_{tag}_"

    email = merged.get(f"{prefix}EMAIL", "").strip()
    password = merged.get(f"{prefix}PASSWORD", "")
    if not email or not password:
        hint = (
            f"Missing {prefix}EMAIL or {prefix}PASSWORD. "
            f"Set them in {resolved_env} (see .env.example)."
        )
        raise ConfigurationError(hint)

    locale = (
        merged.get(f"{prefix}LOCALE")
        or merged.get("SPACEINVASION_LOCALE")
        or "en"
    ).strip()
    stay_raw = (
        merged.get(f"{prefix}STAY_LOGGED_IN")
        or merged.get("SPACEINVASION_STAY_LOGGED_IN")
        or "1"
    ).strip().lower()
    stay_logged_in = stay_raw not in {"0", "false", "no", "off"}
    universe = (merged.get(f"{prefix}UNIVERSE") or "").strip() or None
    two_factor = (merged.get(f"{prefix}2FA_CODE") or "").strip() or None

    return Credentials(
        email=email,
        password=password,
        account_name=tag.lower(),
        locale=locale,
        stay_logged_in=stay_logged_in,
        universe=universe,
        two_factor_code=two_factor,
    )


def detect_antibot_challenge(status_code: int, html: str) -> Optional[str]:
    """Return a diagnostic label if the response presents a CAPTCHA/anti-bot gate."""
    for pattern in _ANTIBOT_PATTERNS:
        if pattern.search(html):
            return f"Anti-bot or CAPTCHA challenge detected ({pattern.pattern})"
    if status_code in (403, 503) and "cloudflare" in html.lower():
        return f"Blocked by Cloudflare gate (HTTP {status_code})"
    return None


def parse_login_tokens(html: str) -> LoginTokens:
    """Extract dynamic login field names and `sid` from the landing page HTML."""
    fields_match = _PORTAL_FIELDS_RE.search(html)
    if not fields_match:
        raise SpaceInvasionAuthError(
            "Could not find bitmeup.initPortalClient login field tokens on landing page."
        )
    fields = fields_match.group(2).split("|")
    if len(fields) != 3 or any(not part for part in fields):
        raise SpaceInvasionAuthError(
            f"Unexpected login token tuple length ({len(fields)}); expected 3 fields."
        )

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


def _safe_portal_speed(raw: object) -> Optional[float]:
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
    try:
        val = float(raw)  # type: ignore[arg-type]
    except (TypeError, ValueError, OverflowError):
        return 1.0
    return val if math.isfinite(val) else 1.0


def _safe_portal_label(raw_name: object) -> str:
    if isinstance(raw_name, int) and not isinstance(raw_name, bool):
        if abs(raw_name).bit_length() >= 12000:
            sign = "-" if raw_name < 0 else ""
            digits = int(abs(raw_name).bit_length() * 0.30103)
            return f"{sign}~1e{digits}"
        return str(raw_name)
    return game_data.safe_key(raw_name)


def universes_from_portal_data(
    sid: str,
    instances: list[Mapping[str, object]],
    base_url: str,
) -> tuple[UniverseOption, ...]:
    """Build `UniverseOption` entries from runtime or parsed `SI_TPL.startPortal` data."""
    if instances is None or isinstance(instances, (str, bytes, bytearray, Mapping)):
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
        raw_id = item.get("id")
        raw_name = item.get("name")
        if raw_id is None or isinstance(raw_id, (bool, Mapping, list, tuple, set, frozenset)) or not raw_name:
            continue
        if isinstance(raw_id, float) and not math.isfinite(raw_id):
            continue
        if isinstance(raw_id, (bytes, bytearray)):
            raw_id = bytes(raw_id).decode("utf-8", errors="replace")
        if isinstance(raw_id, str):
            txt_id = raw_id.strip()
            if not txt_id or not txt_id.isascii():
                continue
            raw_id = txt_id
        try:
            uid = int(raw_id)  # type: ignore[arg-type]
        except (TypeError, ValueError, OverflowError):
            continue
        if uid < 0 or uid.bit_length() > 1000:
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
        raw_spd = item.get("speed")
        raw_fspd = item.get("flightSpeed")
        options.append(
            UniverseOption(
                label=_safe_portal_label(raw_name),
                url=launch_url,
                universe_id=uid,
                is_si2=bool(item.get("si2")),
                speed=_safe_portal_speed(raw_spd),
                flight_speed=_safe_portal_speed(raw_fspd),
                has_account=has_acc,
            )
        )
    return tuple(options)


def extract_universes(html: str, base_url: str) -> tuple[UniverseOption, ...]:
    """Extract SpaceInvasion universes from `SI_TPL.startPortal` or fallback links."""
    portal_match = re.search(r"SI_TPL\.startPortal\s*=\s*\{", html)
    if portal_match:
        block = _extract_balanced_block(html, portal_match.end() - 1, "{", "}") or ""
        sid_m = re.search(r"\bSID\s*:\s*(['\"])(?:sid=)?([^'\"]+)\1", block)
        inst_key = re.search(r"\binstances\s*:\s*\[", block)
        if inst_key:
            inst_body = _extract_balanced_block(block, inst_key.end() - 1, "[", "]") or ""
            sid = sid_m.group(2) if sid_m else ""
            raw_items = _split_js_object_array(inst_body)
            portal_options: list[UniverseOption] = []
            for item in raw_items:
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
                name = name_m.group(2)
                si2_m = re.search(r"\bsi2\s*:\s*(true|false)", item)
                spd_m = re.search(r"\bspeed\s*:\s*([0-9.]+)", item)
                fspd_m = re.search(r"\bflightSpeed\s*:\s*([0-9.]+)", item)
                acc_obj_m = re.search(r"\bacc\s*:\s*\{", item)
                acc_str_m = re.search(r"\bacc\s*:\s*(['\"])(.*?)\1", item)
                acc_bool_m = re.search(r"\bacc\s*:\s*(true)\b", item)
                has_acc = bool(
                    acc_obj_m or acc_bool_m or (acc_str_m and acc_str_m.group(2).strip())
                )
                sid_q = f"&sid={sid}" if sid else ""
                launch_url = urljoin(base_url, f"?action=externalLogin&instance={uid}{sid_q}")
                portal_options.append(
                    UniverseOption(
                        label=name,
                        url=launch_url,
                        universe_id=uid,
                        is_si2=bool(si2_m and si2_m.group(1) == "true"),
                        speed=_safe_portal_speed(spd_m.group(1)) if spd_m else None,
                        flight_speed=_safe_portal_speed(fspd_m.group(1)) if fspd_m else None,
                        has_account=has_acc,
                    )
                )
            if portal_options:
                return tuple(portal_options)

    soup = BeautifulSoup(html, "html.parser")
    seen: set[tuple[str, str]] = set()
    options: list[UniverseOption] = []

    for anchor in soup.find_all("a", href=True):
        href = anchor["href"].strip()
        if not href or href.startswith("#") or href.lower().startswith("javascript:"):
            continue
        lower_href = href.lower()
        text = " ".join(anchor.get_text(" ", strip=True).split())
        if any(
            marker in lower_href
            for marker in ("uni", "universe", "world", "play", "login", "game")
        ):
            abs_url = urljoin(base_url, href)
            label = text or abs_url
            key = (label, abs_url)
            if key not in seen:
                seen.add(key)
                options.append(UniverseOption(label=label, url=abs_url))

    return tuple(options)


def _safe_cookie_expires(value: object) -> Optional[int | float]:
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, int):
        return None if value.bit_length() > 128 else value
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    return None


def save_session_cookies(
    session: requests.Session,
    cookie_path: Path,
    metadata: Optional[Mapping[str, str]] = None,
) -> None:
    """Persist session cookies with owner-only file permissions (`0o600`)."""
    cookie_path.parent.mkdir(parents=True, exist_ok=True)
    os.chmod(cookie_path.parent, 0o700)

    serialized_cookies = [
        {
            "name": c.name,
            "value": c.value,
            "domain": c.domain,
            "path": c.path,
            "secure": bool(c.secure),
            "expires": _safe_cookie_expires(c.expires),
        }
        for c in session.cookies
    ]
    payload = {
        "metadata": {game_data.safe_key(k): game_data.safe_key(v) for k, v in (metadata or {}).items()},
        "cookies": serialized_cookies,
    }
    cookie_path.write_text(json.dumps(payload, indent=2, allow_nan=False), encoding="utf-8")
    os.chmod(cookie_path, 0o600)


def load_session_cookies(
    cookie_path: Path,
    session: Optional[requests.Session] = None,
) -> tuple[requests.Session, dict[str, str]]:
    """Restore a `requests.Session` and metadata from a saved cookie file."""
    if not cookie_path.exists():
        raise ConfigurationError(f"Saved session file not found: {cookie_path}")

    try:
        raw = json.loads(cookie_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, ValueError) as exc:
        raise ConfigurationError(
            f"Failed to read session cookie file {cookie_path}: {type(exc).__name__}"
        ) from exc
    if not isinstance(raw, Mapping):
        raise ConfigurationError(f"Invalid session cookie format in {cookie_path}")
    sess = session or build_http_session()
    try:
        raw_cookies = raw.get("cookies", [])
        if isinstance(raw_cookies, list):
            for item in raw_cookies:
                if not isinstance(item, Mapping):
                    continue
                name = item.get("name")
                value = item.get("value")
                if not name or value is None:
                    continue
                sess.cookies.set(
                    name=str(name),
                    value=str(value),
                    domain=str(item.get("domain") or ""),
                    path=str(item.get("path") or "/"),
                    secure=bool(item.get("secure", False)),
                    expires=_safe_cookie_expires(item.get("expires")),
                )
        raw_meta = raw.get("metadata", {})
        metadata = (
            {str(k): str(v) for k, v in raw_meta.items()}
            if isinstance(raw_meta, Mapping)
            else {}
        )
        return sess, metadata
    except Exception:
        if session is None:
            _close_http_session(sess)
        raise


def _enable_pool_dispose(pm: Any) -> None:
    if pm is None or isinstance(pm, type):
        return
    pools = getattr(pm, "pools", None)
    if pools is None:
        return
    try:
        dispose = getattr(pools, "dispose_func", ...)
    except Exception:
        return
    if dispose is None:
        try:
            pools.dispose_func = lambda p: p.close()
        except Exception:
            pass


def _configure_http_session_pools(sess: Any) -> None:
    adapters = getattr(sess, "adapters", None)
    if not isinstance(adapters, Mapping):
        return
    for adapter in list(adapters.values()):
        _enable_pool_dispose(getattr(adapter, "poolmanager", None))
        proxy_mgr = getattr(adapter, "proxy_manager", None)
        if isinstance(proxy_mgr, Mapping):
            for pm in list(proxy_mgr.values()):
                _enable_pool_dispose(pm)


def _close_http_session(sess: Any) -> None:
    if sess is None:
        return
    _configure_http_session_pools(sess)
    close_fn = getattr(sess, "close", None)
    if callable(close_fn):
        close_fn()


def _close_http_response(resp: Any) -> None:
    if resp is None:
        return
    close_fn = getattr(resp, "close", None)
    if callable(close_fn):
        close_fn()


def build_http_session() -> requests.Session:
    """Create a `requests.Session` configured with browser-like headers."""
    session = requests.Session()
    _configure_http_session_pools(session)
    session.headers.update(
        {
            "User-Agent": DEFAULT_USER_AGENT,
            "Accept": (
                "text/html,application/xhtml+xml,application/xml;q=0.9,"
                "application/json;q=0.8,*/*;q=0.8"
            ),
            "Accept-Language": "en-US,en;q=0.9",
        }
    )
    return session


def _assert_no_antibot(response: requests.Response) -> None:
    """Raise `AntiBotChallengeError` if response contains a CAPTCHA/anti-bot gate."""
    issue = detect_antibot_challenge(response.status_code, response.text)
    if issue:
        raise AntiBotChallengeError(
            f"{issue}. Stopping automation; please complete the check manually in a browser."
        )


def _submit_login_post(
    session: requests.Session,
    base_url: str,
    landing_url: str,
    tokens: LoginTokens,
    creds: Credentials,
    two_factor_code: Optional[str],
    timeout: float,
) -> dict:
    """Submit credentials to `/index.php` and return the parsed JSON response."""
    payload = {
        tokens.submit_field: "1",
        tokens.email_field: creds.email,
        tokens.password_field: creds.password,
        "l": "1" if creds.stay_logged_in else "0",
        "sid": tokens.sid,
    }
    if two_factor_code:
        payload["2fa"] = two_factor_code.strip()

    response = session.post(
        f"{base_url}/index.php",
        data=payload,
        headers={
            "Origin": base_url,
            "Referer": landing_url,
            "Accept": "application/json, text/javascript, */*; q=0.01",
            "X-Requested-With": "XMLHttpRequest",
        },
        timeout=timeout,
    )
    try:
        _assert_no_antibot(response)
        response.raise_for_status()

        try:
            parsed = response.json()
        except ValueError as exc:
            snippet = " ".join(response.text.split())[:200]
            raise SpaceInvasionAuthError(
                f"Expected JSON login response from /index.php, got: {snippet}"
            ) from exc

        if not isinstance(parsed, dict) or not isinstance(parsed.get("result"), dict):
            raise SpaceInvasionAuthError(
                f"Unexpected JSON structure from /index.php: {list(parsed) if isinstance(parsed, dict) else type(parsed).__name__}"
            )
        return parsed["result"]
    finally:
        _close_http_response(response)


def login(
    creds: Credentials,
    session_dir: Path = Path(".sessions"),
    base_url: str = BASE_URL,
    timeout: float = 20.0,
    two_factor_provider: Optional[Callable[[], str]] = None,
    session: Optional[requests.Session] = None,
) -> LoginResult:
    """Authenticate against SpaceInvasion / BitMeUp and save session cookies.

    Args:
        creds: Loaded credentials for the target account.
        session_dir: Directory where `<account>.json` cookies will be saved.
        base_url: Portal base URL (defaults to `https://spaceinvasion.bitmeup.com`).
        timeout: HTTP timeout in seconds per request.
        two_factor_provider: Optional callback invoked if server requests 2FA
            and `creds.two_factor_code` was not already provided.
        session: Optional `requests.Session` (useful for tests or reuse).

    Returns:
        `LoginResult` with final URL, page title, saved cookie path, and
        discovered universe options.
    """
    normalized_base = base_url.rstrip("/")
    landing_url = f"{normalized_base}/?locale={creds.locale}"
    owns_session = session is None
    sess = session if session is not None else build_http_session()

    try:
        landing_resp = sess.get(landing_url, timeout=timeout)
        try:
            _assert_no_antibot(landing_resp)
            landing_resp.raise_for_status()
            tokens = parse_login_tokens(landing_resp.text)
        finally:
            _close_http_response(landing_resp)

        # Mirror bitmeup.initPortalClient template bootstrap so session state matches browser.
        tpl_resp = sess.get(
            f"{normalized_base}/bitmeup.php?get=tpl",
            headers={"Referer": landing_url},
            timeout=timeout,
        )
        try:
            _assert_no_antibot(tpl_resp)
        finally:
            _close_http_response(tpl_resp)

        code = creds.two_factor_code
        result_obj = _submit_login_post(
            sess, normalized_base, landing_url, tokens, creds, code, timeout
        )

        if result_obj.get("result") == "2fa":
            if not code and two_factor_provider is not None:
                code = two_factor_provider().strip()
            if not code:
                raise TwoFactorRequiredError(
                    "Account requires a 2FA code. Pass --2fa <code> or set SPACEINVASION_2FA_CODE."
                )
            result_obj = _submit_login_post(
                sess, normalized_base, landing_url, tokens, creds, code, timeout
            )

        status = result_obj.get("result")
        if status != "ok":
            err_key = str(result_obj.get("error") or "unknown")
            friendly = LOGIN_ERROR_MESSAGES.get(err_key, f"Login failed ({err_key}).")
            raise LoginRejectedError(f"{friendly} [code: {err_key}]")

        redirect_url = result_obj.get("login")
        if not redirect_url or not isinstance(redirect_url, str):
            raise SpaceInvasionAuthError("Login succeeded but no redirect URL was returned.")

        abs_redirect = urljoin(f"{normalized_base}/", redirect_url)
        portal_resp = sess.get(abs_redirect, allow_redirects=True, timeout=timeout)
        try:
            _assert_no_antibot(portal_resp)
            portal_resp.raise_for_status()

            soup = BeautifulSoup(portal_resp.text, "html.parser")
            page_title = soup.title.get_text(strip=True) if soup.title else ""
            universes = extract_universes(portal_resp.text, portal_resp.url)

            cookie_path = session_dir / f"{creds.account_name}.json"
            save_session_cookies(
                sess,
                cookie_path,
                metadata={
                    "account": creds.account_name,
                    "email": creds.email,
                    "last_url": portal_resp.url,
                    "locale": creds.locale,
                },
            )

            return LoginResult(
                account_name=creds.account_name,
                email=creds.email,
                landing_url=landing_url,
                final_url=portal_resp.url,
                page_title=page_title,
                cookie_path=cookie_path,
                cookie_names=tuple(sorted({c.name for c in sess.cookies})),
                universes=universes,
            )
        finally:
            _close_http_response(portal_resp)
    finally:
        if owns_session:
            _close_http_session(sess)


def verify_saved_session(
    account: Optional[str] = None,
    session_dir: Path = Path(".sessions"),
    base_url: str = BASE_URL,
    timeout: float = 20.0,
) -> tuple[bool, str, str]:
    """Check whether a saved cookie file still yields an authenticated session.

    Returns `(is_authenticated, checked_url, page_title)`.
    """
    tag = _normalize_account_tag(account).lower()
    cookie_path = session_dir / f"{tag}.json"
    sess, metadata = load_session_cookies(cookie_path)
    try:
        check_url = metadata.get("last_url") or f"{base_url.rstrip('/')}/?locale={metadata.get('locale', 'en')}"

        resp = sess.get(check_url, allow_redirects=True, timeout=timeout)
        try:
            _assert_no_antibot(resp)
            resp.raise_for_status()

            soup = BeautifulSoup(resp.text, "html.parser")
            title = soup.title.get_text(strip=True) if soup.title else ""
            has_login_box = soup.find(id="bitmeup-login") is not None
            has_init_client = "bitmeup.initPortalClient(" in resp.text and "bitmeup-login" in resp.text
            is_authenticated = not (has_login_box or has_init_client)
            return is_authenticated, resp.url, title
        finally:
            _close_http_response(resp)
    finally:
        _close_http_session(sess)
