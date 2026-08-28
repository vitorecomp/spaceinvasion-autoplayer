"""Lightweight subprocess hot-reloader for the API server and Commander."""

from __future__ import annotations

import os
from pathlib import Path
import signal
import subprocess
import sys
import threading
import time
from typing import Optional, Sequence

RELOAD_CHILD_ENV = "SPACEINVASION_RELOAD_CHILD"


def snapshot_mtimes(paths: Sequence[Path]) -> dict[str, int]:
    """Capture `{filepath: mtime_ns}` for all watched Python files/directories."""
    mtimes: dict[str, int] = {}
    for target in paths:
        if not target.exists():
            continue
        if target.is_file():
            try:
                mtimes[str(target.resolve())] = target.stat().st_mtime_ns
            except OSError:
                pass
        elif target.is_dir():
            for child in sorted(target.rglob("*.py")):
                try:
                    mtimes[str(child.resolve())] = child.stat().st_mtime_ns
                except OSError:
                    pass
    return mtimes


def _terminate_child(proc: subprocess.Popen) -> None:
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


def watch_and_run(
    *,
    label: str,
    cmd: Sequence[str],
    watch_paths: Sequence[Path],
    poll_interval: float = 0.6,
    debounce_seconds: float = 0.25,
) -> int:
    """Run `cmd` as a supervised child process, restarting on watched file edits."""
    env = os.environ.copy()
    env[RELOAD_CHILD_ENV] = "1"

    is_main = threading.current_thread() is threading.main_thread()
    prev_sigterm = signal.getsignal(signal.SIGTERM) if is_main else None

    def _on_sigterm(_signum, _frame) -> None:
        raise KeyboardInterrupt

    if is_main:
        signal.signal(signal.SIGTERM, _on_sigterm)

    proc: Optional[subprocess.Popen] = None
    try:
        mtimes = snapshot_mtimes(watch_paths)
        proc = subprocess.Popen(list(cmd), env=env)
        print(f"[{label}] hot-reloader started (pid={proc.pid}, watching {len(mtimes)} files)", flush=True)
        while True:
            time.sleep(poll_interval)
            rc = proc.poll()
            current = snapshot_mtimes(watch_paths)

            if current != mtimes:
                # Brief debounce so multi-file saves settle before respawn
                time.sleep(debounce_seconds)
                mtimes = snapshot_mtimes(watch_paths)
                print(f"[{label}] code change detected -> hot-reloading...", flush=True)
                _terminate_child(proc)
                proc = subprocess.Popen(list(cmd), env=env)
                print(f"[{label}] restarted (pid={proc.pid})", flush=True)
                continue

            if rc is not None:
                if rc != 0:
                    try:
                        from spaceinvasion.core.logging_utils import append_crash_debug_log

                        append_crash_debug_log(
                            component=f"reloader.{label}",
                            event="child_exited_nonzero",
                            details={"returncode": rc, "cmd": list(cmd)},
                        )
                    except Exception:
                        pass
                return rc
    except KeyboardInterrupt:
        if proc is not None:
            _terminate_child(proc)
        return 0
    finally:
        if proc is not None and proc.poll() is None:
            _terminate_child(proc)
        if is_main and prev_sigterm is not None:
            signal.signal(signal.SIGTERM, prev_sigterm)


def should_spawn_reloader(reload_flag: bool) -> bool:
    """Return True when `--reload` was requested and we are not already the child."""
    return reload_flag and os.environ.get(RELOAD_CHILD_ENV) != "1"


def strip_reload_args(argv: Sequence[str]) -> list[str]:
    """Return `argv` with `--reload` removed for child invocation."""
    return [arg for arg in argv if arg != "--reload"]


def current_python_cmd(script_path: str | Path, argv: Sequence[str]) -> list[str]:
    """Build `[sys.executable, script_path, ...]` without `--reload`."""
    return [sys.executable, str(script_path), *strip_reload_args(argv)]
