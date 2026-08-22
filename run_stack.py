#!/usr/bin/env python3
"""Supervise both hot-reloadable components (`game_api.py` + `commander.py`) with Chrome kept open."""

from __future__ import annotations

import argparse
import os
from pathlib import Path
import signal
import subprocess
import sys
import time
import urllib.request


def _maybe_reexec_venv() -> None:
    if os.environ.get("SPACEINVASION_NO_REEXEC") == "1":
        return
    if sys.prefix != sys.base_prefix:
        return
    venv_python = Path(__file__).resolve().parent / ".venv" / "bin" / "python3"
    if venv_python.exists() and os.path.abspath(sys.executable) != os.path.abspath(venv_python):
        os.execv(str(venv_python), [str(venv_python), *sys.argv])


_maybe_reexec_venv()

from spaceinvasion.api_server import ensure_debug_browser_ready  # noqa: E402
from spaceinvasion.browser import DEFAULT_DEBUG_PORT  # noqa: E402


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run persistent Chrome + hot-reloadable Game API + hot-reloadable Commander."
    )
    parser.add_argument("--api-port", type=int, default=8787, help="Game API port (default: 8787).")
    parser.add_argument(
        "--debug-port",
        type=int,
        default=DEFAULT_DEBUG_PORT,
        help=f"Chrome debug port (default: {DEFAULT_DEBUG_PORT}).",
    )
    parser.add_argument("--live", action="store_true", help="Enable live execution in Commander.")
    parser.add_argument("--interval", type=float, default=20.0, help="Commander tick interval (s).")
    parser.add_argument("--max-actions", type=int, default=1, help="Max commander actions per tick.")
    parser.add_argument("--env-file", type=Path, default=Path(".env"), help="Path to .env file.")
    parser.add_argument("--account", default="default", help="Account profile in .env.")
    return parser


def _wait_api_health(url: str, timeout: float = 15.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(f"{url}/health", timeout=1.0) as resp:
                if resp.status == 200:
                    return True
        except Exception:
            time.sleep(0.25)
    return False


def _stop_proc(proc: subprocess.Popen) -> None:
    if proc.poll() is not None:
        return
    proc.terminate()
    try:
        proc.wait(timeout=5.0)
    except subprocess.TimeoutExpired:
        proc.kill()


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)

    ensure_debug_browser_ready(
        debug_port=args.debug_port,
        env_file=args.env_file,
        account=args.account,
    )

    api_url = f"http://127.0.0.1:{args.api_port}"
    api_cmd = [
        sys.executable,
        "game_api.py",
        "--port",
        str(args.api_port),
        "--debug-port",
        str(args.debug_port),
        "--env-file",
        str(args.env_file),
        "--account",
        args.account,
        "--reload",
    ]
    cmd_cmd = [
        sys.executable,
        "commander.py",
        "--api-url",
        api_url,
        "--loop",
        "--interval",
        str(args.interval),
        "--max-actions",
        str(args.max_actions),
        "--reload",
    ]
    if args.live:
        cmd_cmd.append("--live")

    api_proc = subprocess.Popen(api_cmd)
    if not _wait_api_health(api_url):
        _stop_proc(api_proc)
        print("[STACK] Game API failed to start.", file=sys.stderr)
        return 1

    cmd_proc = subprocess.Popen(cmd_cmd)
    print(
        f"[STACK] Running: Chrome @ 127.0.0.1:{args.debug_port} | "
        f"API @ {api_url} (hot-reload) | Commander (hot-reload)"
    )

    def _sigterm(_s, _f) -> None:
        raise KeyboardInterrupt

    signal.signal(signal.SIGTERM, _sigterm)
    try:
        while True:
            time.sleep(1.0)
            if api_proc.poll() is not None or cmd_proc.poll() is not None:
                break
    except KeyboardInterrupt:
        print("\n[STACK] Stopping API & Commander (Chrome stays open on debug port).")
    finally:
        _stop_proc(cmd_proc)
        _stop_proc(api_proc)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
