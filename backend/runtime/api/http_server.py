"""Phase 4 modular local HTTP JSON API and static Web Operator Console server."""

from __future__ import annotations

from collections.abc import Mapping
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import logging
from pathlib import Path
import sys
import time
from urllib.parse import parse_qs, urlparse

from backend.core.redaction import format_clean_error
from backend.runtime.api._http_helpers import (
    HttpApiBridge,
    RequestTooLargeError,
    build_antibot_response,
    build_rate_limit_response,
    is_client_disconnect_error,
    parse_dashboard_delta_query,
    read_post_body_json,
    resolve_asset_file,
)
from backend.runtime.api.validation import ApiRequestError
from backend.runtime.bridge._helpers import (
    BRIDGE_RUNTIME_ERRORS,
    SERVER_BOOT_ID,
    UI_ASSETS,
    UI_DIR,
    GalaxyScanInProgressError,
    redact_api_payload,
    resolve_ui_dir,
    sanitize_finite_json,
    sanitize_json_dict,
)
from backend.runtime.bridge._helpers_sig import compute_ui_assets_state
from backend.runtime.logs import append_api_log
from backend.runtime.postgres_ctl import is_container_runtime
from backend.session.auth import AntiBotChallengeError, SpaceInvasionAuthError
from backend.session.client import RateLimitError

_LOGGER = logging.getLogger(__name__)


class _ReusableThreadingHTTPServer(ThreadingHTTPServer):
    allow_reuse_address = True
    daemon_threads = True

    def handle_error(self, request: object, client_address: object) -> None:
        exc = sys.exc_info()[1]
        if is_client_disconnect_error(exc):
            return
        super().handle_error(request, client_address)  # type: ignore[arg-type]


class _BaseApiRequestHandler(BaseHTTPRequestHandler):
    """Module-level HTTP handler bound to a concrete `bridge` and `ui_dir` via subclass attributes."""

    bridge: HttpApiBridge
    ui_dir: Path

    def _send_bytes(
        self,
        status: int,
        raw: bytes,
        *,
        content_type: str | None = None,
        extra_headers: Mapping[str, str] | None = None,
    ) -> None:
        try:
            self.send_response(status)
            if content_type is not None:
                self.send_header("Content-Type", content_type)
            self.send_header("Cache-Control", "no-store")
            if extra_headers:
                for hk, hv in extra_headers.items():
                    self.send_header(str(hk), str(hv))
            if raw or content_type is not None:
                self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            if raw:
                self.wfile.write(raw)
        except OSError as exc:
            if is_client_disconnect_error(exc):
                self.close_connection = True
                return
            raise

    def _send_json(
        self,
        status: int,
        body: Mapping[str, object],
        *,
        extra_headers: Mapping[str, str] | None = None,
    ) -> None:
        clean_body = sanitize_finite_json(dict(body))
        raw = json.dumps(clean_body, allow_nan=False).encode("utf-8")
        self._send_bytes(status, raw, content_type="application/json; charset=utf-8", extra_headers=extra_headers)

    def _send_static(self, filename: str, content_type: str) -> None:
        asset_path = self.ui_dir / filename
        if not asset_path.exists():
            self._send_json(HTTPStatus.NOT_FOUND, {"ok": False, "error": f"Missing UI asset {filename}"})
            return
        self._send_bytes(HTTPStatus.OK, asset_path.read_bytes(), content_type=content_type)

    def log_message(self, format: str, *args: object) -> None:
        return

    def _dispatch_get(self, path: str, query: str) -> None:
        if path in UI_ASSETS:
            filename, ctype = UI_ASSETS[path]
            self._send_static(filename, ctype)
            return
        if path == "/assets" or path.startswith("/assets/"):
            cand, status, msg_or_ctype = resolve_asset_file(self.ui_dir, path)
            if cand is None:
                self._send_json(status, {"ok": False, "error": msg_or_ctype})
            else:
                self._send_bytes(HTTPStatus.OK, cand.read_bytes(), content_type=msg_or_ctype)
            return
        if path == "/favicon.ico":
            if (self.ui_dir / "favicon.ico").is_file():
                self._send_static("favicon.ico", "image/x-icon")
            elif (self.ui_dir / "favicon.svg").is_file():
                self._send_static("favicon.svg", "image/svg+xml")
            else:
                self._send_bytes(HTTPStatus.NO_CONTENT, b"")
            return
        if path == "/ui-version":
            self._send_json(
                HTTPStatus.OK,
                {
                    "ok": True,
                    "server_boot_id": SERVER_BOOT_ID,
                    "ts": round(time.time(), 3),
                    **compute_ui_assets_state(self.ui_dir),
                },
            )
            return
        if path == "/health":
            self._send_json(HTTPStatus.OK, self.bridge.health())
            return
        if path == "/bot_status":
            self._send_json(HTTPStatus.OK, self.bridge.get_bot_control())
            return
        if path == "/dashboard":
            qs = parse_qs(query)
            ref = qs.get("refresh", ["0"])[0].lower() in ("1", "true", "yes")
            delta_args = parse_dashboard_delta_query(qs) if not ref else None
            ui_assets = compute_ui_assets_state(self.ui_dir)
            if delta_args is not None:
                c_ver, s_min, s_loot = delta_args
                dash_body = self.bridge.dashboard_delta(
                    client_section_versions=c_ver, since_minute_bucket=s_min, since_loot_hour=s_loot
                )
            else:
                dash_body = self.bridge.dashboard(refresh=ref)
            self._send_json(HTTPStatus.OK, {**dash_body, **ui_assets})
            return
        if path == "/state":
            qs = parse_qs(query)
            inc = qs.get("include_buildings", ["1"])[0].lower() not in ("0", "false", "no")
            self._send_json(HTTPStatus.OK, self.bridge.get_state(include_buildings=inc))
            return
        self._send_json(HTTPStatus.NOT_FOUND, {"ok": False, "error": redact_api_payload(f"Unknown route {path}")})

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        try:
            self._dispatch_get(parsed.path, parsed.query)
        except (ValueError, ApiRequestError) as exc:
            self._send_json(HTTPStatus.BAD_REQUEST, {"ok": False, "error": redact_api_payload(exc)})
        except GalaxyScanInProgressError as exc:
            self._send_json(
                HTTPStatus.CONFLICT,
                {
                    "ok": False,
                    "sweep_in_progress": True,
                    "sweep_blocked": True,
                    "error": format_clean_error(redact_api_payload(exc), max_chars=1024),
                },
            )
        except RateLimitError as exc:
            body, headers = build_rate_limit_response(self.bridge, exc)
            self._send_json(HTTPStatus.TOO_MANY_REQUESTS, body, extra_headers=headers)
        except AntiBotChallengeError as exc:
            self._send_json(HTTPStatus.SERVICE_UNAVAILABLE, build_antibot_response(self.bridge, exc))
        except SpaceInvasionAuthError as exc:
            self._send_json(
                HTTPStatus.SERVICE_UNAVAILABLE,
                {"ok": False, "error": format_clean_error(redact_api_payload(exc), max_chars=1024)},
            )
        except OSError as exc:
            if is_client_disconnect_error(exc):
                self.close_connection = True
                return
            self._send_json(
                HTTPStatus.INTERNAL_SERVER_ERROR,
                {"ok": False, "error": format_clean_error(redact_api_payload(exc), max_chars=1024)},
            )
        except BRIDGE_RUNTIME_ERRORS as exc:
            _LOGGER.exception("Unhandled GET error on %s", parsed.path)
            self._send_json(
                HTTPStatus.INTERNAL_SERVER_ERROR,
                {"ok": False, "error": format_clean_error(redact_api_payload(exc), max_chars=1024)},
            )

    def _log_post_command(self, path: str, status: int, payload: object, body: Mapping[str, object]) -> None:
        log_dir = self.bridge.log_dir
        if not (isinstance(log_dir, (str, Path)) or is_container_runtime()):
            return
        log_payload: dict[str, object] | None = (
            sanitize_json_dict({str(k): v for k, v in payload.items()})
            if isinstance(payload, Mapping)
            else (sanitize_json_dict({"raw_payload": payload}) if payload is not None else None)
        )
        try:
            append_api_log(
                Path(log_dir) if isinstance(log_dir, (str, Path)) else None,
                method="POST",
                path=path,
                status=int(status),
                payload=log_payload,
                response=sanitize_json_dict(body),
            )
        except BRIDGE_RUNTIME_ERRORS:
            _LOGGER.debug("Failed to append API log entry", exc_info=True)

    def do_POST(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path != "/command":
            self._send_json(
                HTTPStatus.NOT_FOUND, {"ok": False, "error": redact_api_payload(f"Unknown route {parsed.path}")}
            )
            return
        payload: object = None
        status_code = HTTPStatus.OK
        body: dict[str, object] = {}
        extra_headers: dict[str, str] | None = None
        try:
            payload = read_post_body_json(self.rfile, self.headers.get("Content-Length"))
            body = self.bridge.handle_command(payload)
        except RequestTooLargeError as exc:
            status_code = HTTPStatus.REQUEST_ENTITY_TOO_LARGE
            body = {"ok": False, "error": redact_api_payload(exc)}
        except GalaxyScanInProgressError as exc:
            status_code = HTTPStatus.CONFLICT
            body = {
                "ok": False,
                "sweep_in_progress": True,
                "sweep_blocked": True,
                "error": format_clean_error(redact_api_payload(exc), max_chars=1024),
            }
        except (ValueError, ApiRequestError) as exc:
            status_code = HTTPStatus.BAD_REQUEST
            body = {"ok": False, "error": redact_api_payload(exc)}
        except RateLimitError as exc:
            body, extra_headers = build_rate_limit_response(self.bridge, exc)
            status_code = HTTPStatus.TOO_MANY_REQUESTS
        except AntiBotChallengeError as exc:
            status_code = HTTPStatus.SERVICE_UNAVAILABLE
            body = build_antibot_response(self.bridge, exc)
        except SpaceInvasionAuthError as exc:
            status_code = HTTPStatus.SERVICE_UNAVAILABLE
            body = {"ok": False, "error": format_clean_error(redact_api_payload(exc), max_chars=1024)}
        except OSError as exc:
            if is_client_disconnect_error(exc):
                self.close_connection = True
                return
            status_code = HTTPStatus.INTERNAL_SERVER_ERROR
            body = {"ok": False, "error": format_clean_error(redact_api_payload(exc), max_chars=1024)}
        except BRIDGE_RUNTIME_ERRORS as exc:
            _LOGGER.exception("Unhandled POST /command error")
            status_code = HTTPStatus.INTERNAL_SERVER_ERROR
            body = {"ok": False, "error": format_clean_error(redact_api_payload(exc), max_chars=1024)}

        self._log_post_command(parsed.path, int(status_code), payload, body)
        self._send_json(status_code, body, extra_headers=extra_headers)


def create_modular_api_server(
    bridge: HttpApiBridge,
    *,
    host: str = "127.0.0.1",
    port: int = 8787,
    ui_dir: Path | None = None,
) -> ThreadingHTTPServer:
    """Create a local `ThreadingHTTPServer` bound to `host:port` for `bridge` with optional `ui_dir`."""
    effective_ui_dir = resolve_ui_dir(ui_dir, default_dir=UI_DIR)

    class _BoundApiHandler(_BaseApiRequestHandler):
        pass

    _BoundApiHandler.bridge = bridge
    _BoundApiHandler.ui_dir = effective_ui_dir
    return _ReusableThreadingHTTPServer((host, port), _BoundApiHandler)


create_api_server = create_modular_api_server
