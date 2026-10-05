"""Lightweight subprocess hot-reloader for the API server and Commander."""

from __future__ import annotations

import hashlib
import os
from pathlib import Path
import signal
import subprocess
import sys
import threading
import time
from typing import Mapping, Optional, Sequence

from spaceinvasion.core.cli_support import terminate_child as _terminate_child

RELOAD_CHILD_ENV = "SPACEINVASION_RELOAD_CHILD"


def _file_sha256(path_str: str) -> Optional[str]:
    """Return hex SHA-256 digest of `path_str`, or `None` if unreadable."""
    try:
        h = hashlib.sha256()
        with open(path_str, "rb") as fh:
            while True:
                chunk = fh.read(65536)
                if not chunk:
                    break
                h.update(chunk)
        return h.hexdigest()
    except OSError:
        return None


def _snapshot_digests(mtimes: Mapping[str, int]) -> dict[str, str]:
    digests: dict[str, str] = {}
    for path_str in mtimes:
        digest = _file_sha256(path_str)
        if digest is not None:
            digests[path_str] = digest
    return digests


def _detect_content_changes(
    prev_mtimes: Mapping[str, int],
    prev_digests: Mapping[str, str],
    curr_mtimes: Mapping[str, int],
) -> tuple[list[str], dict[str, str]]:
    """Return `(changed_paths, updated_digests)`, ignoring mtime-only touches and transient blips."""
    next_digests = dict(prev_digests)
    changed: list[str] = []

    for path_str, mtime_ns in curr_mtimes.items():
        if path_str not in prev_mtimes:
            digest = _file_sha256(path_str)
            if digest is not None:
                next_digests[path_str] = digest
                changed.append(path_str)
            continue
        if mtime_ns != prev_mtimes[path_str]:
            digest = _file_sha256(path_str)
            if digest is None:
                # Transient read/stat failure during atomic write; keep prior digest.
                continue
            old_digest = prev_digests.get(path_str)
            next_digests[path_str] = digest
            if old_digest is None or digest != old_digest:
                changed.append(path_str)

    for path_str in list(prev_mtimes):
        if path_str not in curr_mtimes:
            # Only treat removal as a change when the path is genuinely absent on disk.
            if not Path(path_str).exists():
                next_digests.pop(path_str, None)
                changed.append(path_str)

    return changed, next_digests


def _is_excluded_watch_path(path: Path, root: Path, exclude_dirs: Sequence[str] = ("ui", "__pycache__")) -> bool:
    """Return True if `path` falls inside an excluded watch directory (e.g. `spaceinvasion/ui/`)."""
    exclude_set = set(exclude_dirs)
    if "__pycache__" in path.parts:
        return True
    try:
        rel_parts = path.resolve().relative_to(root.resolve()).parts
    except ValueError:
        rel_parts = path.parts
    if root.name == "spaceinvasion":
        return any(part in exclude_set or part.startswith(".") for part in rel_parts[:-1])
    for idx in range(len(path.parts) - 1):
        if path.parts[idx] == "spaceinvasion" and path.parts[idx + 1] in exclude_set:
            return True
    return False


def discover_watch_paths(
    repo_root: Path,
    *,
    entrypoints: Sequence[Path] = (),
    packages: Optional[Sequence[str]] = None,
    extra_files: Sequence[Path] = (),
    exclude_dirs: Sequence[str] = ("ui", "__pycache__"),
) -> tuple[Path, ...]:
    """Discover watched Python files under `spaceinvasion/` (or selected `packages`) plus `entrypoints`."""
    root = repo_root.resolve()
    cwd = Path.cwd().resolve()
    use_rel = cwd == root

    def _format_path(candidate: Path) -> Path:
        resolved = candidate.resolve()
        if use_rel and resolved.is_relative_to(cwd):
            return resolved.relative_to(cwd)
        return resolved

    discovered: list[Path] = []
    seen: set[Path] = set()

    def _add_file(candidate: Path) -> None:
        if not candidate.exists():
            return
        formatted = _format_path(candidate)
        if formatted not in seen:
            seen.add(formatted)
            discovered.append(formatted)

    for ep in entrypoints:
        _add_file(ep)

    pkg_root = root / "spaceinvasion"
    if pkg_root.is_dir():
        scan_roots = [pkg_root / pkg for pkg in packages] if packages is not None else [pkg_root]
        for scan_root in scan_roots:
            if not scan_root.is_dir():
                continue
            for child in sorted(scan_root.rglob("*.py")):
                if _is_excluded_watch_path(child, pkg_root, exclude_dirs=exclude_dirs):
                    continue
                _add_file(child)

    for extra in extra_files:
        _add_file(extra)

    return tuple(discovered)


def snapshot_mtimes(paths: Sequence[Path]) -> dict[str, int]:
    """Capture `{filepath: mtime_ns}` for all watched Python files/directories."""
    mtimes: dict[str, int] = {}
    for target in paths:
        if not target.exists():
            continue
        if target.is_file():
            if _is_excluded_watch_path(target, target.parent):
                continue
            try:
                mtimes[str(target.resolve())] = target.stat().st_mtime_ns
            except OSError:
                pass
        elif target.is_dir():
            for child in sorted(target.rglob("*.py")):
                if _is_excluded_watch_path(child, target):
                    continue
                try:
                    mtimes[str(child.resolve())] = child.stat().st_mtime_ns
                except OSError:
                    pass
    return mtimes


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
    env.setdefault("PYTHONUNBUFFERED", "1")

    is_main = threading.current_thread() is threading.main_thread()
    prev_sigterm = signal.getsignal(signal.SIGTERM) if is_main else None

    def _on_sigterm(_signum, _frame) -> None:
        raise KeyboardInterrupt

    if is_main:
        signal.signal(signal.SIGTERM, _on_sigterm)

    proc: Optional[subprocess.Popen] = None
    try:
        mtimes = snapshot_mtimes(watch_paths)
        digests = _snapshot_digests(mtimes)
        proc = subprocess.Popen(list(cmd), env=env)
        print(f"[{label}] hot-reloader started (pid={proc.pid}, watching {len(mtimes)} files)", flush=True)
        while True:
            time.sleep(poll_interval)
            rc = proc.poll()
            current = snapshot_mtimes(watch_paths)

            if current != mtimes:
                # Brief debounce so multi-file saves settle before checking content hashes
                time.sleep(debounce_seconds)
                settled = snapshot_mtimes(watch_paths)
                changed, next_digests = _detect_content_changes(mtimes, digests, settled)
                mtimes = settled
                digests = next_digests
                if changed:
                    names = ", ".join(Path(p).name for p in changed[:4])
                    if len(changed) > 4:
                        names += f", +{len(changed) - 4} more"
                    print(f"[{label}] code change detected ({names}) -> hot-reloading...", flush=True)
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
