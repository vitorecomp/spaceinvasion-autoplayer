"""HTTP client used by `commander.py` and external tools to talk to `game_api.py`."""

from __future__ import annotations

from typing import Any
import requests

from spaceinvasion.state import GameState, game_state_from_dict


class GameApiError(RuntimeError):
    """Raised when the local SpaceInvasion Game API returns an error or is unreachable."""


class GameApiClient:
    """Thin HTTP client for the SpaceInvasion local Game API (`127.0.0.1:8787`)."""

    def __init__(self, base_url: str = "http://127.0.0.1:8787", timeout: float = 30.0) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self._session = requests.Session()

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
            data = resp.json()
        except ValueError as exc:
            raise GameApiError(f"Non-JSON response ({resp.status_code}): {resp.text[:200]}") from exc
        if resp.status_code >= 400 or (isinstance(data, dict) and data.get("ok") is False and "error" in data):
            raise GameApiError(str(data.get("error") or f"HTTP {resp.status_code}"))
        return data
