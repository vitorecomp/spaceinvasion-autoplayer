#!/usr/bin/env python3
"""Supervise both hot-reloadable components (`game_api.py` + `commander.py`) with Chrome kept open."""

from __future__ import annotations

import argparse
import math
import os
from pathlib import Path
import signal
import subprocess
import sys
import threading
import time
from typing import Optional
import urllib.request


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
from spaceinvasion.runtime.api_server import ensure_debug_browser_ready  # noqa: E402
from spaceinvasion.session.auth import SpaceInvasionAuthError  # noqa: E402
from spaceinvasion.session.browser import DEFAULT_DEBUG_PORT  # noqa: E402
from spaceinvasion.core.logging_utils import redact_sensitive  # noqa: E402


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
    parser.add_argument(
        "--interval",
        type=float,
        default=None,
        help="Min commander batch interval in seconds (0 or omitted = use config.action_batch_interval_seconds).",
    )
    parser.add_argument(
        "--max-actions",
        type=int,
        default=10,
        help="Max accumulated actions sent per commander batch (default: 10).",
    )
    parser.add_argument("--env-file", type=Path, default=Path(".env"), help="Path to .env file.")
    parser.add_argument("--account", default="default", help="Account profile in .env.")
    return parser


def _wait_api_health(url: str, timeout: float = 25.0, proc: Optional[subprocess.Popen] = None) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if proc is not None and proc.poll() is not None:
            return False
        remaining = max(0.2, deadline - time.monotonic())
        req_timeout = min(3.0, remaining)
        try:
            with urllib.request.urlopen(f"{url}/health", timeout=req_timeout) as resp:
                if resp.status == 200:
                    return True
        except Exception:
            if proc is not None and proc.poll() is not None:
                return False
            time.sleep(0.25)
    return False


def _stop_proc(proc: subprocess.Popen) -> None:
    if proc.poll() is not None:
        return
    try:
        proc.terminate()
        proc.wait(timeout=5.0)
    except subprocess.TimeoutExpired:
        proc.kill()
        try:
            proc.wait(timeout=5.0)
        except (subprocess.TimeoutExpired, OSError):
            pass
    except OSError:
        if proc.poll() is None:
            try:
                proc.wait(timeout=0.5)
            except (subprocess.TimeoutExpired, OSError):
                pass


def _safe_interval(raw: object, default: float = 20.0) -> float:
    if raw is None or isinstance(raw, (bool, dict, list, tuple, set, frozenset)):
        return default
    if isinstance(raw, int):
        if raw.bit_length() > 128:
            return 86400.0 if raw > 0 else default
        iv = float(raw)
    else:
        if isinstance(raw, (bytes, bytearray)):
            raw = bytes(raw).decode("utf-8", errors="replace")
        if isinstance(raw, str):
            txt = raw.strip()
            if not txt or not txt.isascii():
                return default
            raw = txt
        try:
            iv = float(raw)  # type: ignore[arg-type]
        except (TypeError, ValueError, OverflowError):
            return default
    if not math.isfinite(iv) or iv <= 0.0:
        return default
    return max(0.01, min(86400.0, iv))


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

    try:
        ensure_debug_browser_ready(
            debug_port=args.debug_port,
            env_file=args.env_file,
            account=args.account,
        )
    except SpaceInvasionAuthError as exc:
        print(f"[STACK] Browser startup error: {redact_sensitive(_safe_err_text(exc))}", file=sys.stderr)
        return 1

    base_dir = Path(__file__).resolve().parent
    api_url = f"http://127.0.0.1:{args.api_port}"
    api_cmd = [
        sys.executable,
        str(base_dir / "game_api.py"),
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
    raw_iv = args.interval
    if raw_iv is None or (
        isinstance(raw_iv, (int, float))
        and not isinstance(raw_iv, bool)
        and math.isfinite(raw_iv)
        and raw_iv <= 0.0
    ):
        safe_interval = 0.0
    else:
        safe_interval = _safe_interval(raw_iv, 20.0)
    cmd_cmd = [
        sys.executable,
        str(base_dir / "commander.py"),
        "--api-url",
        api_url,
        "--loop",
        "--interval",
        str(safe_interval),
        "--max-actions",
        str(args.max_actions),
        "--reload",
    ]
    if args.live:
        cmd_cmd.append("--live")

    is_main = threading.current_thread() is threading.main_thread()
    prev_sigterm = signal.getsignal(signal.SIGTERM) if is_main else None

    def _sigterm(_s, _f) -> None:
        raise KeyboardInterrupt

    if is_main:
        signal.signal(signal.SIGTERM, _sigterm)

    api_proc: Optional[subprocess.Popen] = None
    cmd_proc: Optional[subprocess.Popen] = None
    try:
        api_proc = subprocess.Popen(api_cmd)
        if not _wait_api_health(api_url, proc=api_proc):
            print("[STACK] Game API failed to start.", file=sys.stderr)
            return 1

        cmd_proc = subprocess.Popen(cmd_cmd)
        print(
            f"[STACK] Running: Chrome @ 127.0.0.1:{args.debug_port} | "
            f"API @ {api_url} (hot-reload) | Commander (hot-reload)"
        )

        while True:
            time.sleep(1.0)
            if api_proc.poll() is not None or cmd_proc.poll() is not None:
                break
        rc_api = api_proc.poll()
        rc_cmd = cmd_proc.poll()
        if (rc_api is not None and rc_api != 0) or (rc_cmd is not None and rc_cmd != 0):
            return 1
        return 0
    except KeyboardInterrupt:
        print("\n[STACK] Stopping API & Commander (Chrome stays open on debug port).")
        return 0
    finally:
        if cmd_proc is not None:
            _stop_proc(cmd_proc)
        if api_proc is not None:
            _stop_proc(api_proc)
        if is_main and prev_sigterm is not None:
            signal.signal(signal.SIGTERM, prev_sigterm)


if __name__ == "__main__":
    raise SystemExit(main())
