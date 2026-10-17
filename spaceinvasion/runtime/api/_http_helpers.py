"""HTTP request parsing, static asset resolution, and error-envelope helpers for `http_server`."""

from __future__ import annotations

from collections.abc import Mapping
import errno
from http import HTTPStatus
import json
import logging
import math
from pathlib import Path
from typing import Protocol

from spaceinvasion.runtime.api.validation import ApiRequestError
from spaceinvasion.runtime.bridge._helpers import (
    BRIDGE_RUNTIME_ERRORS,
    MAX_REQUEST_BYTES,
    redact_api_payload,
    reject_nonfinite_json,
    safe_limiter_wait,
)
from spaceinvasion.session.auth import AntiBotChallengeError
from spaceinvasion.session.client import RateLimitError

_LOGGER = logging.getLogger(__name__)

ASSET_CONTENT_TYPES: dict[str, str] = {
    ".js": "application/javascript; charset=utf-8",
    ".mjs": "application/javascript; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".svg": "image/svg+xml",
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".webp": "image/webp",
    ".ico": "image/x-icon",
    ".woff2": "font/woff2",
    ".woff": "font/woff",
    ".ttf": "font/ttf",
    ".json": "application/json; charset=utf-8",
    ".map": "application/json; charset=utf-8",
}

CLIENT_DISCONNECT_ERRNOS: frozenset[int] = frozenset(
    {
        errno.EPIPE,
        errno.ECONNRESET,
        errno.ECONNABORTED,
        errno.ESHUTDOWN,
    }
)


class HttpApiBridge(Protocol):
    """Explicit typed bridge interface required by the HTTP server."""

    @property
    def log_dir(self) -> Path | None: ...

    def health(self) -> dict[str, object]: ...

    def get_bot_control(self) -> dict[str, object]: ...

    def dashboard(self, *, refresh: bool = False) -> dict[str, object]: ...

    def dashboard_delta(
        self,
        *,
        client_section_versions: Mapping[str, str] | None = None,
        since_minute_bucket: int | None = None,
        since_loot_hour: int | None = None,
    ) -> dict[str, object]: ...

    def get_state(self, *, include_buildings: bool = True) -> dict[str, object]: ...

    def handle_command(self, payload: object) -> dict[str, object]: ...

    def rate_limit_remaining(self) -> float: ...

    def record_rate_limit(self, retry_after: float | None = None) -> float: ...

    def record_antibot_pause(self, exc: BaseException) -> None: ...


class RequestTooLargeError(Exception):
    """Raised when an HTTP POST request exceeds `MAX_REQUEST_BYTES`."""


def is_client_disconnect_error(exc: BaseException | None) -> bool:
    """Return True for benign peer-disconnect socket errors (e.g. `BrokenPipeError`)."""
    if exc is None:
        return False
    if isinstance(exc, (BrokenPipeError, ConnectionResetError, ConnectionAbortedError)):
        return True
    return isinstance(exc, OSError) and exc.errno in CLIENT_DISCONNECT_ERRNOS


def build_rate_limit_response(
    bridge: HttpApiBridge,
    exc: RateLimitError,
) -> tuple[dict[str, object], dict[str, str]]:
    """Build a 429 JSON error payload and `Retry-After` header for a `RateLimitError`."""
    exc_retry = safe_limiter_wait(exc.retry_after, 5.0, min_val=1.0)
    if exc.server_throttle:
        rem_now = safe_limiter_wait(bridge.rate_limit_remaining(), 0.0, min_val=0.0)
        if rem_now <= 0.0 or exc_retry > rem_now + 1.0:
            bridge.record_rate_limit(exc_retry)
    rem_final = bridge.rate_limit_remaining()
    rem = safe_limiter_wait(rem_final, exc_retry, min_val=0.0) if rem_final > 0.0 else exc_retry
    err_txt = str(redact_api_payload(exc))
    if "retry after" not in err_txt.lower():
        err_txt = f"{err_txt} (retry after {rem:.1f}s)"
    body: dict[str, object] = {
        "ok": False,
        "rate_limited": True,
        "retry_after": round(rem, 1),
        "error": redact_api_payload(err_txt),
    }
    headers = {"Retry-After": str(max(1, int(math.ceil(rem))))}
    return body, headers


def build_antibot_response(bridge: HttpApiBridge, exc: AntiBotChallengeError) -> dict[str, object]:
    """Record CAPTCHA pause on `bridge` and return a 503 anti-bot JSON error body."""
    try:
        bridge.record_antibot_pause(exc)
    except BRIDGE_RUNTIME_ERRORS:
        _LOGGER.warning("Failed to record anti-bot pause from HTTP boundary", exc_info=True)
    err_txt = str(redact_api_payload(exc)).strip()
    if not any(tok in err_txt.lower() for tok in ("captcha", "anti-bot", "antibot", "turnstile", "sicherheitspr")):
        err_txt = f"CAPTCHA / anti-bot challenge: {err_txt}" if err_txt else "CAPTCHA / anti-bot challenge detected."
    return {"ok": False, "captcha_detected": True, "paused": True, "error": redact_api_payload(err_txt)}


def parse_dashboard_delta_query(
    qs: Mapping[str, list[str]],
) -> tuple[dict[str, str] | None, int | None, int | None] | None:
    """Parse Phase 4.3 delta query parameters if present; return None for full legacy dashboard."""
    raw_ver_list = qs.get("versions") or qs.get("section_versions")
    raw_min_list = qs.get("since_minute_bucket")
    raw_loot_list = qs.get("since_loot_hour")
    if not raw_ver_list and not raw_min_list and not raw_loot_list:
        return None
    client_versions: dict[str, str] | None = None
    if raw_ver_list and raw_ver_list[0].strip():
        parsed = json.loads(raw_ver_list[0])
        if not isinstance(parsed, Mapping):
            raise ApiRequestError("Query parameter 'versions' must be a JSON object.")
        client_versions = {str(k): str(v) for k, v in parsed.items()}
    since_min = int(raw_min_list[0].strip()) if (raw_min_list and raw_min_list[0].strip()) else None
    since_loot = int(raw_loot_list[0].strip()) if (raw_loot_list and raw_loot_list[0].strip()) else None
    return client_versions, since_min, since_loot


def read_post_body_json(rfile: object, content_length_hdr: str | None) -> object:
    """Read and parse a bounded JSON POST request body, rejecting NaN/Infinity constants."""
    if content_length_hdr is None or not str(content_length_hdr).strip():
        length = 0
    else:
        raw_len = str(content_length_hdr).strip()
        if not (raw_len.isascii() and raw_len.isdigit()) or len(raw_len) > 18:
            raise ApiRequestError("Invalid Content-Length header")
        length = int(raw_len)
    if length > MAX_REQUEST_BYTES:
        raise RequestTooLargeError(f"Request body too large (max {MAX_REQUEST_BYTES} bytes)")
    if length > 0:
        read_fn = rfile.read  # type: ignore[attr-defined]
        raw_bytes: bytes = read_fn(length)
    else:
        raw_bytes = b"{}"
    return json.loads(raw_bytes.decode("utf-8"), parse_constant=reject_nonfinite_json)


def resolve_asset_file(ui_dir: Path, path: str) -> tuple[Path | None, int, str]:
    """Validate and resolve a `/assets/<rel>` request against `ui_dir / 'assets'`."""
    rel_asset = path[len("/assets/") :] if path.startswith("/assets/") else ""
    if (
        not rel_asset
        or "\x00" in rel_asset
        or "\\" in rel_asset
        or rel_asset.startswith("/")
        or any(seg in ("", ".", "..") for seg in rel_asset.split("/"))
    ):
        return None, HTTPStatus.BAD_REQUEST, "Invalid asset path."
    assets_root = (ui_dir / "assets").resolve()
    cand_path = (assets_root / rel_asset).resolve()
    try:
        cand_path.relative_to(assets_root)
    except ValueError:
        return None, HTTPStatus.BAD_REQUEST, "Invalid asset path."
    if not cand_path.is_file():
        return None, HTTPStatus.NOT_FOUND, str(redact_api_payload(f"Missing UI asset {rel_asset}"))
    return cand_path, HTTPStatus.OK, ASSET_CONTENT_TYPES.get(cand_path.suffix.lower(), "application/octet-stream")
