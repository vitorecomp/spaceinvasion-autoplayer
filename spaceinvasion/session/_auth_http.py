"""Internal HTTP authentication request/response helpers for `spaceinvasion.session.auth`."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Optional
from urllib.parse import urljoin

from bs4 import BeautifulSoup
import requests

from spaceinvasion.session._auth_models import (
    LOGIN_ERROR_MESSAGES,
    AntiBotChallengeError,
    Credentials,
    LoginRejectedError,
    LoginResult,
    LoginTokens,
    SpaceInvasionAuthError,
    TwoFactorRequiredError,
)
from spaceinvasion.session._auth_parsing import (
    detect_antibot_challenge,
    extract_universes,
    parse_login_tokens,
)
from spaceinvasion.session.http_pools import close_http_response as _close_http_response


def assert_no_antibot(response: requests.Response) -> None:
    """Raise `AntiBotChallengeError` if response contains a CAPTCHA/anti-bot gate."""
    issue = detect_antibot_challenge(response.status_code, response.text)
    if issue:
        raise AntiBotChallengeError(f"{issue}. Stopping automation; please complete the check manually in a browser.")


def submit_login_post(
    session: requests.Session,
    base_url: str,
    landing_url: str,
    tokens: LoginTokens,
    creds: Credentials,
    two_factor_code: Optional[str],
    timeout: float,
) -> dict[str, object]:
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
        assert_no_antibot(response)
        response.raise_for_status()

        try:
            parsed: object = response.json()
        except ValueError as exc:
            snippet = " ".join(response.text.split())[:200]
            raise SpaceInvasionAuthError(f"Expected JSON login response from /index.php, got: {snippet}") from exc

        if not isinstance(parsed, dict) or not isinstance(parsed.get("result"), dict):
            desc = list(parsed) if isinstance(parsed, dict) else type(parsed).__name__
            raise SpaceInvasionAuthError(f"Unexpected JSON structure from /index.php: {desc}")
        result_map = parsed["result"]
        return {str(k): v for k, v in result_map.items()}
    finally:
        _close_http_response(response)


def fetch_landing_and_bootstrap(
    sess: requests.Session,
    normalized_base: str,
    landing_url: str,
    timeout: float,
) -> LoginTokens:
    landing_resp = sess.get(landing_url, timeout=timeout)
    try:
        assert_no_antibot(landing_resp)
        landing_resp.raise_for_status()
        tokens = parse_login_tokens(landing_resp.text)
    finally:
        _close_http_response(landing_resp)

    tpl_resp = sess.get(
        f"{normalized_base}/bitmeup.php?get=tpl",
        headers={"Referer": landing_url},
        timeout=timeout,
    )
    try:
        assert_no_antibot(tpl_resp)
    finally:
        _close_http_response(tpl_resp)
    return tokens


def authenticate_with_optional_2fa(
    sess: requests.Session,
    normalized_base: str,
    landing_url: str,
    tokens: LoginTokens,
    creds: Credentials,
    timeout: float,
    two_factor_provider: Optional[Callable[[], str]],
) -> str:
    code = creds.two_factor_code
    result_obj = submit_login_post(sess, normalized_base, landing_url, tokens, creds, code, timeout)

    if result_obj.get("result") == "2fa":
        if not code and two_factor_provider is not None:
            code = two_factor_provider().strip()
        if not code:
            raise TwoFactorRequiredError(
                "Account requires a 2FA code. Pass --2fa <code> or set SPACEINVASION_2FA_CODE."
            )
        result_obj = submit_login_post(sess, normalized_base, landing_url, tokens, creds, code, timeout)

    status = result_obj.get("result")
    if status != "ok":
        err_key = str(result_obj.get("error") or "unknown")
        friendly = LOGIN_ERROR_MESSAGES.get(err_key, f"Login failed ({err_key}).")
        raise LoginRejectedError(f"{friendly} [code: {err_key}]")

    redirect_url = result_obj.get("login")
    if not redirect_url or not isinstance(redirect_url, str):
        raise SpaceInvasionAuthError("Login succeeded but no redirect URL was returned.")
    return urljoin(f"{normalized_base}/", redirect_url)


def fetch_portal_and_save_result(
    sess: requests.Session,
    abs_redirect: str,
    landing_url: str,
    creds: Credentials,
    session_dir: Path,
    timeout: float,
    save_cookies_fn: Callable[[requests.Session, Path, Optional[Mapping[str, str]]], None],
) -> LoginResult:
    portal_resp = sess.get(abs_redirect, allow_redirects=True, timeout=timeout)
    try:
        assert_no_antibot(portal_resp)
        portal_resp.raise_for_status()

        soup = BeautifulSoup(portal_resp.text, "html.parser")
        page_title = soup.title.get_text(strip=True) if soup.title else ""
        universes = extract_universes(portal_resp.text, portal_resp.url)

        cookie_path = session_dir / f"{creds.account_name}.json"
        save_cookies_fn(
            sess,
            cookie_path,
            {
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
