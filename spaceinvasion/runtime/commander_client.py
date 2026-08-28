"""HTTP client used by `commander.py` and external tools to talk to `game_api.py`."""

from __future__ import annotations

from collections.abc import Mapping
import math
import os
from typing import Any, Optional
import requests

from spaceinvasion.core import game_data
from spaceinvasion.core.logging_utils import redact_sensitive
from spaceinvasion.core.state import GameState, game_state_from_dict


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
    _depth: int = 0,
    _seen: Optional[set[int]] = None,
    _budget: Optional[list[int]] = None,
) -> str:
    if val is None:
        return default
    if isinstance(val, os.PathLike):
        try:
            val = os.fspath(val)
        except Exception:
            return default
        if val is None or isinstance(val, os.PathLike):
            return default
    if isinstance(val, str):
        txt = val
    elif isinstance(val, (bytes, bytearray)):
        txt = bytes(val).decode("utf-8", errors="replace")
    elif isinstance(val, int) and not isinstance(val, bool):
        txt = game_data.fmt_compact_int(val) if abs(val).bit_length() >= 12_000 else str(val)
    elif isinstance(val, BaseException):
        str_fn = getattr(type(val), "__str__", BaseException.__str__)
        used_custom_str = False
        txt = ""
        if str_fn not in (BaseException.__str__, KeyError.__str__):
            try:
                txt = str(val)
                used_custom_str = True
            except Exception:
                txt = ""
        if not used_custom_str:
            args = getattr(val, "args", ())
            if not isinstance(args, (tuple, list)) or not args:
                txt = "" if str_fn is BaseException.__str__ else type(val).__name__
            else:
                seen = _seen if _seen is not None else set()
                budget = _budget if _budget is not None else [256]
                vid = id(val)
                if vid in seen or _depth >= 16 or budget[0] <= 0:
                    txt = f"<{type(val).__name__}>"
                else:
                    budget[0] -= 1
                    seen.add(vid)
                    try:
                        if len(args) == 1:
                            a0 = args[0]
                            if isinstance(a0, str):
                                txt = repr(a0) if isinstance(val, KeyError) else a0
                            else:
                                txt = _safe_client_text(
                                    a0,
                                    f"<{type(a0).__name__}>",
                                    max_len=None,
                                    _depth=_depth + 1,
                                    _seen=seen,
                                    _budget=budget,
                                )
                        else:
                            parts: list[str] = []
                            for a in args[:8]:
                                if budget[0] <= 0:
                                    parts.append("...")
                                    break
                                if isinstance(a, str):
                                    parts.append(repr(a))
                                else:
                                    parts.append(
                                        _safe_client_text(
                                            a,
                                            f"<{type(a).__name__}>",
                                            max_len=None,
                                            _depth=_depth + 1,
                                            _seen=seen,
                                            _budget=budget,
                                        )
                                    )
                            if len(args) > 8:
                                parts.append(f"...+{len(args) - 8}")
                            txt = "(" + ", ".join(parts) + ")"
                    finally:
                        seen.discard(vid)
    elif isinstance(val, Mapping):
        try:
            txt = game_data.safe_key(redact_sensitive(val))
        except Exception:
            return default
    elif isinstance(val, (tuple, list, set, frozenset)):
        try:
            txt = game_data.safe_key(val)
        except Exception:
            return default
    else:
        try:
            txt = str(val)
        except Exception:
            return default
    txt = str(redact_sensitive(txt))
    if max_len is not None and max_len >= 0 and len(txt) > max_len:
        return txt[:max_len]
    return txt


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
                raise GameApiError(
                    f"Non-JSON response ({resp.status_code}): {body_preview}"
                ) from exc
            if not isinstance(data, dict):
                raise GameApiError(
                    f"Expected JSON object response (HTTP {resp.status_code}, got {type(data).__name__})"
                )
            if resp.status_code >= 400 or (data.get("ok") is False and "error" in data):
                raise GameApiError(_safe_client_text(data.get("error"), f"HTTP {resp.status_code}") or f"HTTP {resp.status_code}")
            return data
        finally:
            _close_http_response(resp)
