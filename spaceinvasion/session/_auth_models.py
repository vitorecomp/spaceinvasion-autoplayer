"""Authentication exception hierarchy, dataclasses, and scalar normalization helpers."""

from __future__ import annotations

from dataclasses import dataclass, field
import math
from pathlib import Path
import re
from typing import Optional

LOGIN_ERROR_MESSAGES: dict[str, str] = {
    "b.external.login.error.1": "Invalid email or password.",
    "b.external.login.error.2": "Too many login attempts; please wait before retrying.",
    "b.external.login.error.3": "This account is currently locked.",
    "b.external.login.error.4": "This account has not been activated yet.",
    "b.external.login.error.5": "Proxy usage is not allowed by BitMeUp.",
    "b.external.login.error.6": "The 2-factor authentication code is incorrect.",
}


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


def _safe_cookie_expires(value: object) -> Optional[int | float]:
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, int):
        return None if value.bit_length() > 128 else value
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    return None


__all__ = [
    "AntiBotChallengeError",
    "ConfigurationError",
    "Credentials",
    "LOGIN_ERROR_MESSAGES",
    "LoginRejectedError",
    "LoginResult",
    "LoginTokens",
    "SpaceInvasionAuthError",
    "TwoFactorRequiredError",
    "UniverseOption",
    "_normalize_account_tag",
    "_safe_cookie_expires",
]
