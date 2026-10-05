#!/usr/bin/env python3
"""Persistent SpaceInvasion Game API server (`http://127.0.0.1:8787`).

Keeps Chrome open on `--debug-port` (default `9222`), turns the live browser
session into a thread-safe JSON API (`/health`, `/state`, `/command`), and
supports `--reload` so API/parser edits restart the server without closing Chrome.
"""

from __future__ import annotations

import argparse
import os
from pathlib import Path
import signal
import sys
import threading


_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from spaceinvasion.core.cli_support import maybe_reexec_venv, safe_cli_str  # noqa: E402

maybe_reexec_venv(_REPO_ROOT, skip=os.environ.get("SPACEINVASION_NO_REEXEC") == "1")

from spaceinvasion.db.backend import is_container_runtime  # noqa: E402
from spaceinvasion.session.auth import AntiBotChallengeError, SpaceInvasionAuthError  # noqa: E402
from spaceinvasion.session.browser import (  # noqa: E402
    DEFAULT_DEBUG_HOST,
    DEFAULT_DEBUG_PORT,
    is_debug_port_open,
)
from spaceinvasion.runtime.api_server import (  # noqa: E402
    GameApiBridge,
    create_api_server,
    ensure_debug_browser_ready,
)
from spaceinvasion.core.logging_utils import redact_sensitive  # noqa: E402
from spaceinvasion.runtime.reloader import (  # noqa: E402
    current_python_cmd,
    discover_watch_paths,
    should_spawn_reloader,
    watch_and_run,
)

WATCH_PATHS = discover_watch_paths(_REPO_ROOT, entrypoints=(Path(__file__).resolve(),))


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Expose live SpaceInvasion Classic browser session as a local HTTP JSON API."
    )
    parser.add_argument("--host", default="127.0.0.1", help="Bind host (default: 127.0.0.1).")
    parser.add_argument("--port", type=int, default=8787, help="API HTTP port (default: 8787).")
    parser.add_argument(
        "--debug-host",
        default=DEFAULT_DEBUG_HOST,
        help=f"Chrome DevTools debug host (default: {DEFAULT_DEBUG_HOST}).",
    )
    parser.add_argument(
        "--debug-port",
        type=int,
        default=DEFAULT_DEBUG_PORT,
        help=f"Chrome debug port (default: {DEFAULT_DEBUG_PORT}).",
    )
    parser.add_argument("--env-file", type=Path, default=Path(".env"), help="Path to .env file.")
    parser.add_argument(
        "--config",
        type=Path,
        default=None,
        help="Optional strategy config path (.db SQLite or .json; defaults to active DB).",
    )
    parser.add_argument(
        "--db-backend",
        choices=("auto", "postgres", "sqlite"),
        default=None,
        help="Database backend to use ('postgres' via Podman, 'sqlite', or 'auto').",
    )
    parser.add_argument(
        "--db-url",
        default=None,
        help="Explicit PostgreSQL DSN (e.g. postgresql://user:pass@127.0.0.1:5433/spaceinvasion).",
    )
    parser.add_argument("--account", default="default", help="Account profile in .env.")
    parser.add_argument(
        "--max-per-hour",
        type=int,
        default=60,
        help="Hourly live action cap enforced by API executor (default: 60).",
    )
    parser.add_argument(
        "--log-dir",
        type=Path,
        default=Path("logs"),
        help="Directory for API command logs (default: logs).",
    )
    parser.add_argument(
        "--reload",
        action="store_true",
        help="Hot-reload the API server whenever backend Python files change (Chrome stays open).",
    )
    return parser


def _safe_err_text(exc: BaseException) -> str:
    return safe_cli_str(exc, redact_fn=redact_sensitive)


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)

    if should_spawn_reloader(args.reload):
        raw_argv = sys.argv[1:] if argv is None else argv
        return watch_and_run(
            label="API",
            cmd=current_python_cmd(Path(__file__).resolve(), raw_argv),
            watch_paths=(*WATCH_PATHS, _REPO_ROOT / "spaceinvasion"),
        )

    startup_auth_exc: BaseException | None = None
    try:
        if args.debug_host == "127.0.0.1":
            ensure_debug_browser_ready(
                debug_port=args.debug_port,
                env_file=args.env_file,
                account=args.account,
            )
        else:
            ensure_debug_browser_ready(
                debug_port=args.debug_port,
                debug_host=args.debug_host,
                env_file=args.env_file,
                account=args.account,
            )
    except Exception as exc:
        if not isinstance(exc, SpaceInvasionAuthError) and not is_container_runtime():
            raise
        if is_container_runtime() and is_debug_port_open(args.debug_port, args.debug_host):
            startup_auth_exc = exc
            print(
                f"[API] Browser requires operator login/CAPTCHA in noVNC (:7900) "
                f"({redact_sensitive(_safe_err_text(exc))}); starting API in paused state...",
                file=sys.stderr,
                flush=True,
            )
        else:
            print(
                f"[API] Browser startup error: {redact_sensitive(_safe_err_text(exc))}",
                file=sys.stderr,
                flush=True,
            )
            return 1

    bridge = GameApiBridge(
        debug_port=args.debug_port,
        debug_host=args.debug_host,
        env_file=args.env_file,
        account=args.account,
        config_path=args.config,
        max_actions_per_hour=args.max_per_hour,
        log_dir=args.log_dir,
        db_backend=args.db_backend,
        db_url=args.db_url,
    )
    if startup_auth_exc is not None:
        if isinstance(startup_auth_exc, AntiBotChallengeError):
            rec_fn = getattr(bridge, "_record_antibot_pause", None)
            if callable(rec_fn):
                rec_fn(startup_auth_exc)
        else:
            bridge.handle_command({"command": "bot_control", "paused": True})
    server = None
    is_main = threading.current_thread() is threading.main_thread()
    prev_sigterm = signal.getsignal(signal.SIGTERM) if is_main else None

    def _shutdown_handler(_signum, _frame) -> None:
        raise KeyboardInterrupt

    try:
        server = create_api_server(bridge, host=args.host, port=args.port)
        bridge.start_galaxy_sweep_worker()
        if is_main:
            signal.signal(signal.SIGTERM, _shutdown_handler)
        print(
            f"[API] Listening on http://{args.host}:{args.port} "
            f"(Control UI: http://{args.host}:{args.port}/ | Chrome debug: {args.debug_host}:{args.debug_port})",
            flush=True,
        )
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n[API] Shutting down (Chrome remains open on debug port).", flush=True)
    finally:
        bridge.stop_galaxy_sweep_worker()
        if server is not None:
            server.server_close()
        bridge.close()
        if is_main and prev_sigterm is not None:
            signal.signal(signal.SIGTERM, prev_sigterm)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
