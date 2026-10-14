"""CLI virtualenv bootstrap, child-process termination, and redacted text formatting helpers."""

from __future__ import annotations

from collections.abc import Callable, Mapping
import os
from pathlib import Path
import subprocess
import sys

from spaceinvasion.core.coercion import fmt_compact_int, safe_key
from spaceinvasion.core.redaction import redact_sensitive

_SAFE_TEXT_EXCS = (
    OSError,
    ValueError,
    TypeError,
    AttributeError,
    LookupError,
    RuntimeError,
    OverflowError,
    UnicodeError,
    ArithmeticError,
)


def maybe_reexec_venv(repo_root: Path, *, skip: bool = False) -> None:
    """Re-exec the current CLI script with `<repo_root>/.venv/bin/python3` when needed."""
    if skip or sys.prefix != sys.base_prefix:
        return
    venv_python = repo_root / ".venv" / "bin" / "python3"
    if venv_python.exists() and os.path.abspath(sys.executable) != os.path.abspath(venv_python):
        os.execv(str(venv_python), [str(venv_python), *sys.argv])


def terminate_child(proc: subprocess.Popen[bytes] | subprocess.Popen[str]) -> None:
    """Terminate a child process gracefully, escalating to SIGKILL on timeout."""
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


def _format_exc_text(
    exc: BaseException,
    default: str,
    *,
    depth: int,
    seen: set[int],
    budget: list[int],
    redact_fn: Callable[[object], object],
) -> str:
    str_fn = getattr(type(exc), "__str__", BaseException.__str__)
    if str_fn not in (BaseException.__str__, KeyError.__str__):
        try:
            return str(exc)
        except _SAFE_TEXT_EXCS:
            pass
    args = getattr(exc, "args", ())
    if not isinstance(args, (tuple, list)) or not args:
        return "" if str_fn is BaseException.__str__ else type(exc).__name__
    vid = id(exc)
    if vid in seen or depth >= 16 or budget[0] <= 0:
        return f"<{type(exc).__name__}>"
    budget[0] -= 1
    seen.add(vid)
    try:
        if len(args) == 1:
            a0 = args[0]
            if isinstance(a0, str):
                return repr(a0) if isinstance(exc, KeyError) else a0
            return safe_cli_str(
                a0,
                f"<{type(a0).__name__}>",
                max_len=None,
                redact_fn=redact_fn,
                _depth=depth + 1,
                _seen=seen,
                _budget=budget,
            )
        parts: list[str] = []
        for a in args[:8]:
            if budget[0] <= 0:
                parts.append("...")
                break
            if isinstance(a, str):
                parts.append(repr(a))
            else:
                parts.append(
                    safe_cli_str(
                        a,
                        f"<{type(a).__name__}>",
                        max_len=None,
                        redact_fn=redact_fn,
                        _depth=depth + 1,
                        _seen=seen,
                        _budget=budget,
                    )
                )
        if len(args) > 8:
            parts.append(f"...+{len(args) - 8}")
        return "(" + ", ".join(parts) + ")"
    finally:
        seen.discard(vid)


def safe_cli_str(
    val: object,
    default: str = "",
    *,
    max_len: int | None = None,
    root_cap: int | None = None,
    redact_fn: Callable[[object], object] | None = None,
    _depth: int = 0,
    _seen: set[int] | None = None,
    _budget: list[int] | None = None,
) -> str:
    """Format an arbitrary value or nested exception safely with redaction and cycle guards."""
    if val is None:
        return default
    if isinstance(val, os.PathLike):
        try:
            val = os.fspath(val)
        except _SAFE_TEXT_EXCS:
            return default
        if val is None or isinstance(val, os.PathLike):
            return default
    redacter = redact_fn if redact_fn is not None else redact_sensitive
    if isinstance(val, str):
        txt = val
    elif isinstance(val, (bytes, bytearray)):
        txt = bytes(val).decode("utf-8", errors="replace")
    elif isinstance(val, int) and not isinstance(val, bool):
        txt = fmt_compact_int(val) if abs(val).bit_length() >= 12_000 else str(val)
    elif isinstance(val, BaseException):
        txt = _format_exc_text(
            val,
            default,
            depth=_depth,
            seen=_seen if _seen is not None else set(),
            budget=_budget if _budget is not None else [256],
            redact_fn=redacter,
        )
    elif isinstance(val, Mapping):
        try:
            masked_val = redacter(val)
            txt = safe_key(masked_val)
        except _SAFE_TEXT_EXCS:
            return default
    else:
        try:
            txt = safe_key(val)
        except _SAFE_TEXT_EXCS:
            return default
    txt = str(redacter(txt))
    if max_len is not None and max_len >= 0 and len(txt) > max_len:
        return txt[:max_len]
    if root_cap is not None and _depth == 0 and len(txt) > root_cap:
        return txt[:root_cap]
    return txt
