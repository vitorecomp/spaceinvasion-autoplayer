"""Authentication and session management for SpaceInvasion Classic (BitMeUp)."""

from __future__ import annotations

from collections.abc import Callable, Mapping
import json
import os
from pathlib import Path
from typing import Optional

from bs4 import BeautifulSoup
from dotenv import dotenv_values
import requests

from spaceinvasion.core import catalog as game_data
from spaceinvasion.session._auth_http import (
    assert_no_antibot as _assert_no_antibot,
)
from spaceinvasion.session._auth_http import (
    authenticate_with_optional_2fa as _authenticate_with_optional_2fa,
)
from spaceinvasion.session._auth_http import (
    fetch_landing_and_bootstrap as _fetch_landing_and_bootstrap,
)
from spaceinvasion.session._auth_http import (
    fetch_portal_and_save_result as _fetch_portal_and_save_result,
)
from spaceinvasion.session._auth_models import (
    LOGIN_ERROR_MESSAGES,
    AntiBotChallengeError,
    ConfigurationError,
    Credentials,
    LoginRejectedError,
    LoginResult,
    LoginTokens,
    SpaceInvasionAuthError,
    TwoFactorRequiredError,
    UniverseOption,
    _normalize_account_tag,
    _safe_cookie_expires,
)
from spaceinvasion.session._auth_parsing import (
    _extract_balanced_block,
    _safe_portal_label,
    _safe_portal_speed,
    _split_js_object_array,
    detect_antibot_challenge,
    extract_universes,
    parse_login_tokens,
    universes_from_portal_data,
)
from spaceinvasion.session.http_pools import (
    close_http_response as _close_http_response,
)
from spaceinvasion.session.http_pools import (
    close_http_session as _close_http_session,
)
from spaceinvasion.session.http_pools import (
    configure_http_session_pools as _configure_http_session_pools,
)

BASE_URL = "https://spaceinvasion.bitmeup.com"
DEFAULT_SESSIONS_DIR = Path(os.environ.get("SPACEINVASION_SESSIONS_DIR", "data/sessions"))
DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)


def load_credentials(
    env_path: Path | str = Path(".env"),
    account: Optional[str] = None,
    environ: Optional[Mapping[str, str]] = None,
) -> Credentials:
    """Load credentials from a `.env` file (with process env overrides)."""
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
        hint = f"Missing {prefix}EMAIL or {prefix}PASSWORD. Set them in {resolved_env} (see .env.example)."
        raise ConfigurationError(hint)

    locale = (merged.get(f"{prefix}LOCALE") or merged.get("SPACEINVASION_LOCALE") or "en").strip()
    stay_raw = (
        (merged.get(f"{prefix}STAY_LOGGED_IN") or merged.get("SPACEINVASION_STAY_LOGGED_IN") or "1").strip().lower()
    )
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
        raise ConfigurationError(f"Failed to read session cookie file {cookie_path}: {type(exc).__name__}") from exc
    if not isinstance(raw, Mapping):
        raise ConfigurationError(f"Invalid session cookie format in {cookie_path}")
    sess = session or build_http_session()
    succeeded = False
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
        metadata = {str(k): str(v) for k, v in raw_meta.items()} if isinstance(raw_meta, Mapping) else {}
        succeeded = True
        return sess, metadata
    finally:
        if not succeeded and session is None:
            _close_http_session(sess)


def build_http_session() -> requests.Session:
    """Create a `requests.Session` configured with browser-like headers."""
    session = requests.Session()
    _configure_http_session_pools(session)
    session.headers.update(
        {
            "User-Agent": DEFAULT_USER_AGENT,
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,application/json;q=0.8,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.9",
        }
    )
    return session


def login(
    creds: Credentials,
    session_dir: Path = DEFAULT_SESSIONS_DIR,
    base_url: str = BASE_URL,
    timeout: float = 20.0,
    two_factor_provider: Optional[Callable[[], str]] = None,
    session: Optional[requests.Session] = None,
) -> LoginResult:
    """Authenticate against SpaceInvasion / BitMeUp and save session cookies."""
    normalized_base = base_url.rstrip("/")
    landing_url = f"{normalized_base}/?locale={creds.locale}"
    owns_session = session is None
    sess = session if session is not None else build_http_session()

    try:
        tokens = _fetch_landing_and_bootstrap(sess, normalized_base, landing_url, timeout)
        abs_redirect = _authenticate_with_optional_2fa(
            sess, normalized_base, landing_url, tokens, creds, timeout, two_factor_provider
        )
        return _fetch_portal_and_save_result(
            sess, abs_redirect, landing_url, creds, session_dir, timeout, save_session_cookies
        )
    finally:
        if owns_session:
            _close_http_session(sess)


def verify_saved_session(
    account: Optional[str] = None,
    session_dir: Path = DEFAULT_SESSIONS_DIR,
    base_url: str = BASE_URL,
    timeout: float = 20.0,
) -> tuple[bool, str, str]:
    """Check whether a saved cookie file still yields an authenticated session."""
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


__all__ = [
    "AntiBotChallengeError",
    "BASE_URL",
    "ConfigurationError",
    "Credentials",
    "DEFAULT_SESSIONS_DIR",
    "DEFAULT_USER_AGENT",
    "LOGIN_ERROR_MESSAGES",
    "LoginRejectedError",
    "LoginResult",
    "LoginTokens",
    "SpaceInvasionAuthError",
    "TwoFactorRequiredError",
    "UniverseOption",
    "_extract_balanced_block",
    "_normalize_account_tag",
    "_safe_cookie_expires",
    "_safe_portal_label",
    "_safe_portal_speed",
    "_split_js_object_array",
    "build_http_session",
    "detect_antibot_challenge",
    "extract_universes",
    "load_credentials",
    "load_session_cookies",
    "login",
    "parse_login_tokens",
    "save_session_cookies",
    "universes_from_portal_data",
    "verify_saved_session",
]
