"""HTTP client used by `commander.py` and external tools to talk to `game_api.py`."""

from __future__ import annotations

from collections.abc import Mapping
import math
from typing import Any, Optional

import requests

from spaceinvasion.core.model import GameState, game_state_from_dict
from spaceinvasion.core.redaction import redact_sensitive
from spaceinvasion.runtime.cli_support import safe_cli_str
from spaceinvasion.session.http_pools import (
    close_http_response as _close_http_response,
)
from spaceinvasion.session.http_pools import (
    close_http_session as _close_http_session,
)
from spaceinvasion.session.http_pools import (
    configure_http_session_pools as _configure_http_session_pools,
)


def _safe_api_timeout(raw: Any, default: float = 30.0) -> float:
    if raw is None or isinstance(raw, (bool, Mapping, list, tuple, set, frozenset)):
        return default
    if isinstance(raw, (bytes, bytearray)):
        raw = bytes(raw).decode("utf-8", errors="replace")
    if isinstance(raw, str):
        s = raw.strip()
        if not s or not s.isascii():
            return default
        raw = s
    try:
        val = float(raw)
    except (TypeError, ValueError, OverflowError):
        return default
    if not math.isfinite(val) or val <= 0.0:
        return default
    return max(0.05, min(3600.0, val))


class GameApiError(RuntimeError):
    """Raised when the local SpaceInvasion Game API returns an error or is unreachable."""


def _safe_client_text(
    val: Any,
    default: str = "",
    *,
    max_len: Optional[int] = None,
) -> str:
    return safe_cli_str(val, default, max_len=max_len, redact_fn=redact_sensitive)


class GameApiClient:
    """Thin HTTP client for the SpaceInvasion local Game API (`127.0.0.1:8787`)."""

    def __init__(self, base_url: str = "http://127.0.0.1:8787", timeout: float = 30.0) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout = _safe_api_timeout(timeout, 30.0)
        self._session = requests.Session()
        _configure_http_session_pools(self._session)

    def close(self) -> None:
        _close_http_session(self._session)

    def __enter__(self) -> "GameApiClient":
        return self

    def __exit__(self, *_exc: Any) -> None:
        self.close()

    def health(self) -> dict[str, Any]:
        resp = self._session.get(f"{self.base_url}/health", timeout=self.timeout)
        return self._decode(resp)

    def get_bot_control(self) -> dict[str, Any]:
        resp = self._session.get(f"{self.base_url}/bot_status", timeout=self.timeout)
        return self._decode(resp)

    def get_state(self, *, include_buildings: bool = True) -> GameState:
        flag = "1" if include_buildings else "0"
        resp = self._session.get(
            f"{self.base_url}/state?include_buildings={flag}",
            timeout=self.timeout,
        )
        data = self._decode(resp)
        return game_state_from_dict(data)

    def send_command(self, payload: dict[str, Any]) -> dict[str, Any]:
        resp = self._session.post(
            f"{self.base_url}/command",
            json=payload,
            timeout=self.timeout,
        )
        return self._decode(resp)

    def _decode(self, resp: requests.Response) -> dict[str, Any]:
        try:
            try:
                data = resp.json()
            except ValueError as exc:
                body_preview = _safe_client_text(getattr(resp, "text", ""), "", max_len=200)
                raise GameApiError(f"Non-JSON response ({resp.status_code}): {body_preview}") from exc
            if not isinstance(data, dict):
                raise GameApiError(
                    f"Expected JSON object response (HTTP {resp.status_code}, got {type(data).__name__})"
                )
            if resp.status_code >= 400 or (data.get("ok") is False and "error" in data):
                raise GameApiError(
                    _safe_client_text(data.get("error"), f"HTTP {resp.status_code}") or f"HTTP {resp.status_code}"
                )
            return data
        finally:
            _close_http_response(resp)
