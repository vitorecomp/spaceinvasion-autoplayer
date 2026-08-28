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


def _maybe_reexec_venv() -> None:
    if os.environ.get("SPACEINVASION_NO_REEXEC") == "1":
        return
    if sys.prefix != sys.base_prefix:
        return
    venv_python = _REPO_ROOT / ".venv" / "bin" / "python3"
    if venv_python.exists() and os.path.abspath(sys.executable) != os.path.abspath(venv_python):
        os.execv(str(venv_python), [str(venv_python), *sys.argv])


_maybe_reexec_venv()

from spaceinvasion.core import game_data # noqa: E402
from spaceinvasion.session.auth import SpaceInvasionAuthError  # noqa: E402
from spaceinvasion.session.browser import DEFAULT_DEBUG_PORT  # noqa: E402
from spaceinvasion.runtime.api_server import (  # noqa: E402
    GameApiBridge,
    create_api_server,
    ensure_debug_browser_ready,
)
from spaceinvasion.core.logging_utils import redact_sensitive  # noqa: E402
from spaceinvasion.runtime.reloader import (  # noqa: E402
    current_python_cmd,
    should_spawn_reloader,
    watch_and_run,
)

WATCH_PATHS = (
    Path(__file__).resolve(),
    Path("config.yaml"),
    Path("spaceinvasion/planner/action_graph.py"),
    Path("spaceinvasion/session/auth.py"),
    Path("spaceinvasion/session/browser.py"),
    Path("spaceinvasion/core/config.py"),
    Path("spaceinvasion/combat/fleet.py"),
    Path("spaceinvasion/core/game_data.py"),
    Path("spaceinvasion/db/migrations.py"),
    Path("spaceinvasion/combat/ranking.py"),
    Path("spaceinvasion/core/state.py"),
    Path("spaceinvasion/db/storage.py"),
    Path("spaceinvasion/planner/strategy.py"),
    Path("spaceinvasion/planner/optimizer.py"),
    Path("spaceinvasion/session/client.py"),
    Path("spaceinvasion/session/executor.py"),
    Path("spaceinvasion/runtime/api_server.py"),
    Path("spaceinvasion/core/logging_utils.py"),
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


def _safe_err_text(
    exc: BaseException,
    *,
    _depth: int = 0,
    _seen: set[int] | None = None,
    _budget: list[int] | None = None,
) -> str:
    if not isinstance(exc, BaseException):
        return str(redact_sensitive(game_data.safe_key(exc)))
    seen = _seen if _seen is not None else set()
    budget = _budget if _budget is not None else [256]
    str_fn = getattr(type(exc), "__str__", BaseException.__str__)
    used_custom_str = False
    txt = ""
    if str_fn not in (BaseException.__str__, KeyError.__str__):
        try:
            txt = str(exc)[:16_384]
            used_custom_str = True
        except Exception:
            txt = ""
    if not used_custom_str:
        args = getattr(exc, "args", ())
        if not isinstance(args, (tuple, list)) or not args:
            txt = "" if str_fn is BaseException.__str__ else type(exc).__name__
        else:
            vid = id(exc)
            if vid in seen or _depth >= 16 or budget[0] <= 0:
                txt = f"<{type(exc).__name__}>"
            else:
                budget[0] -= 1
                seen.add(vid)
                try:
                    if len(args) == 1:
                        a0 = args[0]
                        if isinstance(a0, str):
                            txt = (repr(a0) if isinstance(exc, KeyError) else a0)[:16_384]
                        elif isinstance(a0, BaseException):
                            txt = _safe_err_text(a0, _depth=_depth + 1, _seen=seen, _budget=budget)
                        else:
                            txt = game_data.safe_key(a0)
                    else:
                        parts = []
                        for a in args[:8]:
                            if budget[0] <= 0:
                                parts.append("...")
                                break
                            if isinstance(a, str):
                                parts.append(repr(a))
                            elif isinstance(a, BaseException):
                                parts.append(_safe_err_text(a, _depth=_depth + 1, _seen=seen, _budget=budget))
                            else:
                                parts.append(game_data.safe_key(a))
                        if len(args) > 8:
                            parts.append(f"...+{len(args) - 8}")
                        txt = ("(" + ", ".join(parts) + ")")[:4096]
                finally:
                    seen.discard(vid)
    return str(redact_sensitive(txt))


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
        print(f"[API] Browser startup error: {redact_sensitive(_safe_err_text(exc))}", file=sys.stderr)
        return 1

    bridge = GameApiBridge(
        debug_port=args.debug_port,
        env_file=args.env_file,
        account=args.account,
        config_path=args.config,
        max_actions_per_hour=args.max_per_hour,
        log_dir=args.log_dir,
    )
    server = None
    is_main = threading.current_thread() is threading.main_thread()
    prev_sigterm = signal.getsignal(signal.SIGTERM) if is_main else None

    def _shutdown_handler(_signum, _frame) -> None:
        raise KeyboardInterrupt

    try:
        server = create_api_server(bridge, host=args.host, port=args.port)
        if is_main:
            signal.signal(signal.SIGTERM, _shutdown_handler)
        print(
            f"[API] Listening on http://{args.host}:{args.port} "
            f"(Control UI: http://{args.host}:{args.port}/ | Chrome debug port: 127.0.0.1:{args.debug_port})"
        )
        sys.stdout.flush()
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n[API] Shutting down (Chrome remains open on debug port).")
    finally:
        if server is not None:
            server.server_close()
        db = getattr(bridge, "_db", None)
        if db is not None:
            close_db = getattr(db, "close", None)
            if callable(close_db):
                close_db()
        if is_main and prev_sigterm is not None:
            signal.signal(signal.SIGTERM, prev_sigterm)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
