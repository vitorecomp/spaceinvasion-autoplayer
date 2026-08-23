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


def _maybe_reexec_venv() -> None:
    if os.environ.get("SPACEINVASION_NO_REEXEC") == "1":
        return
    if sys.prefix != sys.base_prefix:
        return
    venv_python = Path(__file__).resolve().parent / ".venv" / "bin" / "python3"
    if venv_python.exists() and os.path.abspath(sys.executable) != os.path.abspath(venv_python):
        os.execv(str(venv_python), [str(venv_python), *sys.argv])


_maybe_reexec_venv()

from spaceinvasion.auth import SpaceInvasionAuthError  # noqa: E402
from spaceinvasion.browser import DEFAULT_DEBUG_PORT  # noqa: E402
from spaceinvasion.api_server import (  # noqa: E402
    GameApiBridge,
    create_api_server,
    ensure_debug_browser_ready,
)
from spaceinvasion.reloader import (  # noqa: E402
    current_python_cmd,
    should_spawn_reloader,
    watch_and_run,
)

WATCH_PATHS = (
    Path("game_api.py"),
    Path("config.yaml"),
    Path("spaceinvasion/auth.py"),
    Path("spaceinvasion/browser.py"),
    Path("spaceinvasion/config.py"),
    Path("spaceinvasion/fleet.py"),
    Path("spaceinvasion/game_data.py"),
    Path("spaceinvasion/ranking.py"),
    Path("spaceinvasion/state.py"),
    Path("spaceinvasion/storage.py"),
    Path("spaceinvasion/strategy.py"),
    Path("spaceinvasion/client.py"),
    Path("spaceinvasion/executor.py"),
    Path("spaceinvasion/api_server.py"),
    Path("spaceinvasion/logging_utils.py"),
)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Expose live SpaceInvasion Classic browser session as a local HTTP JSON API."
    )
    parser.add_argument("--host", default="127.0.0.1", help="Bind host (default: 127.0.0.1).")
    parser.add_argument("--port", type=int, default=8787, help="API HTTP port (default: 8787).")
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
        default=Path("config.yaml"),
        help="Optional non-secret strategy config file (default: config.yaml).",
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


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)

    if should_spawn_reloader(args.reload):
        raw_argv = sys.argv[1:] if argv is None else argv
        return watch_and_run(
            label="API",
            cmd=current_python_cmd(Path(__file__).resolve(), raw_argv),
            watch_paths=WATCH_PATHS,
        )

    try:
        ensure_debug_browser_ready(
            debug_port=args.debug_port,
            env_file=args.env_file,
            account=args.account,
        )
    except SpaceInvasionAuthError as exc:
        print(f"[API] Browser startup error: {exc}", file=sys.stderr)
        return 1

    bridge = GameApiBridge(
        debug_port=args.debug_port,
        env_file=args.env_file,
        account=args.account,
        config_path=args.config,
        max_actions_per_hour=args.max_per_hour,
        log_dir=args.log_dir,
    )
    server = create_api_server(bridge, host=args.host, port=args.port)

    def _shutdown_handler(_signum, _frame) -> None:
        raise KeyboardInterrupt

    signal.signal(signal.SIGTERM, _shutdown_handler)
    print(
        f"[API] Listening on http://{args.host}:{args.port} "
        f"(Control UI: http://{args.host}:{args.port}/ | Chrome debug port: 127.0.0.1:{args.debug_port})"
    )
    sys.stdout.flush()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n[API] Shutting down (Chrome remains open on debug port).")
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
