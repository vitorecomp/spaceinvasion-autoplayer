"""Shared CLI bootstrap, process control, HTTP pool cleanup, and token-redaction helpers."""

from __future__ import annotations

from collections.abc import Callable, Mapping
import inspect
import os
from pathlib import Path
import re
import subprocess
import sys
from typing import Any, Optional

from spaceinvasion.core import game_data

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

_SECRET_DICT_KEYS: frozenset[str] = frozenset(
    {"sid", "password", "passwd", "cookie", "api_key", "secret", "authorization"}
)
_SECRET_DICT_STEMS: tuple[str, ...] = (
    "password",
    "passwd",
    "cookie",
    "api_key",
    "api-key",
    "secret",
    "authorization",
    "sid",
)
_BENIGN_KEY_PREFIX_TOKENS: frozenset[str] = frozenset(
    {
        "has",
        "no",
        "non",
        "without",
        "is",
        "allow",
        "require",
        "enable",
        "disable",
        "need",
        "check",
        "use",
        "missing",
        "empty",
    }
)
_SECRET_FAST_MARKERS: tuple[str, ...] = (
    "sid",
    "pass",
    "cookie",
    "api_key",
    "api-key",
    "secret",
    "auth",
    "bearer",
)


def is_secret_dict_key(key: str) -> bool:
    """Return True when a key or parameter name represents a secret credential or token."""
    if not isinstance(key, str):
        return False
    low = key.strip().lower()
    if not low:
        return False
    if not low.isascii():
        low = low.replace("i\u0307", "i").replace("\u0131", "i").replace("\u017f", "s")
    if low == "api-key" or low in _SECRET_DICT_KEYS:
        return True
    for stem in _SECRET_DICT_STEMS:
        if not low.endswith(stem):
            continue
        prefix = low[: len(low) - len(stem)]
        if not prefix:
            return True
        if prefix[-1] not in ("_", "-"):
            return False
        stripped = prefix.strip("_-")
        if not stripped:
            return True
        parts = [p for p in stripped.replace("-", "_").split("_") if p]
        if not parts:
            return True
        if not all(p.isascii() and p.isalnum() for p in parts):
            return False
        return parts[0] not in _BENIGN_KEY_PREFIX_TOKENS
    return False


_COMP_PREFIX_RE = r"[_-]*(?:[a-z0-9]+[_-]+)*"
_COMP_KEY_RE = (
    rf"(?P<key>{_COMP_PREFIX_RE}(?:sid|password|passwd|cookie|api_key|api-key|secret|authorization))"
)
_COMP_KV_KEY_RE = (
    rf"(?P<key>{_COMP_PREFIX_RE}(?:password|passwd|cookie|api_key|api-key|secret))"
)
_COMP_AUTH_KEY_RE = rf"(?P<key>{_COMP_PREFIX_RE}authorization)"
_AUTH_SCHEME_RE = r"(?:bearer|basic|token|digest)"
_KV_SEP_RE = r"(?:[ \t]*[:=][ \t]*|%(?:25){0,4}3[ad])"
_SCHEME_SEP_RE = r"(?:[ \t]+|%(?:25){0,4}20|\+)"
_TOK_SAFE_CHAR = r"[^\s&,;\"'`<>#)}\]\[]"
_UNQUOTED_TOK_RE = rf"(?:(?!%(?:25){{0,4}}(?:26|23|20|09|0[ad]|22|27|3[bce]|5[bd])){_TOK_SAFE_CHAR})+"


def _quoted_val_pattern(p: str) -> str:
    return (
        rf"(?P<{p}_vq>[\"'])(?:"
        rf"(?P<{p}_qval>(?:(?!(?P={p}_vq))(?:\\[^\r\n]|[^\\\r\n]))+)(?P<{p}_cq>(?P={p}_vq))|"
        rf"(?!(?P={p}_vq)|\\?[\"'])(?P<{p}_uqval>{_UNQUOTED_TOK_RE})"
        rf")|"
        rf"(?P<{p}_evq2>\\\\(?P<{p}_ec2>[\"']))(?:"
        rf"(?P<{p}_eqval2>(?:[^\\\r\n]|\\\\(?!(?P={p}_ec2))|\\(?!(?P={p}_ec2)|\\)|\\\\\\(?P={p}_ec2))+)"
        rf"(?P<{p}_ecq2>(?P={p}_evq2))|"
        rf"(?!(?P={p}_evq2)|\\?[\"'])(?P<{p}_euqval2>{_UNQUOTED_TOK_RE})"
        rf")|"
        rf"(?P<{p}_evq>\\(?P<{p}_ec>[\"']))(?:"
        rf"(?P<{p}_eqval>(?:[^\\\r\n]|\\\\\\\\|\\\\\\(?P={p}_ec)|\\\\(?!(?P={p}_ec))[^\\\r\n]|\\(?!(?P={p}_ec))[^\\\r\n])+)"
        rf"(?P<{p}_ecq>(?P={p}_evq))|"
        rf"(?!(?P={p}_evq)|\\?[\"'])(?P<{p}_euqval>{_UNQUOTED_TOK_RE})"
        rf")"
    )


def _delim_val_pattern(p: str) -> str:
    return (
        f"{_quoted_val_pattern(p)}|"
        rf"(?P<{p}_bk><)(?:"
        rf"(?P<{p}_bkval>[ \t]*[^\s<>\r\n][^<>\r\n]*)(?P<{p}_bkc>>)|"
        rf"(?P<{p}_ubkval>{_UNQUOTED_TOK_RE})"
        rf")|"
        rf"(?P<{p}_sb>\[)(?:"
        rf"(?P<{p}_sbval>[ \t]*[^\s\[\]\r\n][^\[\]\r\n]*)(?P<{p}_sbc>\])|"
        rf"(?P<{p}_usbval>{_UNQUOTED_TOK_RE})"
        rf")|"
        rf"(?P<{p}_pq>%(?:25){{0,4}}(?:22|27))(?:"
        rf"(?P<{p}_pqval>(?:(?!(?P={p}_pq)|%(?:25){{0,4}}(?:26|23|3[bce]|22|27|5[bd])){_TOK_SAFE_CHAR})+)"
        rf"(?P<{p}_pqc>(?P={p}_pq))|"
        rf"(?P<{p}_upqval>{_UNQUOTED_TOK_RE})"
        rf")|"
        rf"(?P<{p}_pb>%(?:25){{0,4}}3c)(?:"
        rf"(?P<{p}_pbval>(?:(?!%(?:25){{0,4}}(?:26|23|3[bce]|22|27|5[bd])){_TOK_SAFE_CHAR})+)"
        rf"(?P<{p}_pbc>%(?:25){{0,4}}3e)|"
        rf"(?P<{p}_upbval>{_UNQUOTED_TOK_RE})"
        rf")|"
        rf"(?P<{p}_psb>%(?:25){{0,4}}5b)(?:"
        rf"(?P<{p}_psbval>(?:(?!%(?:25){{0,4}}(?:26|23|3[bce]|22|27|5[bd])){_TOK_SAFE_CHAR})+)"
        rf"(?P<{p}_psbc>%(?:25){{0,4}}5d)|"
        rf"(?P<{p}_upsbval>{_UNQUOTED_TOK_RE})"
        rf")|"
        rf"(?!(?:\\?[\"']|<|\[|%(?:25){{0,4}}(?:22|27|3c|5b)))(?P<{p}_uval>{_UNQUOTED_TOK_RE})"
    )


def _format_quoted_val(match: re.Match[str], p: str) -> str:
    for grp, close_grp in (
        (f"{p}_vq", f"{p}_cq"),
        (f"{p}_evq2", f"{p}_ecq2"),
        (f"{p}_evq", f"{p}_ecq"),
    ):
        tok = match.group(grp)
        if tok is not None:
            return f"{tok}REDACTED{tok}" if match.group(close_grp) is not None else f"{tok}REDACTED"
    return ""


def _format_delim_val(match: re.Match[str], p: str) -> str:
    q = _format_quoted_val(match, p)
    if q:
        return q
    if match.group(f"{p}_bk") is not None:
        return "<REDACTED>" if match.group(f"{p}_bkc") is not None else "<REDACTED"
    if match.group(f"{p}_sb") is not None:
        return "[REDACTED]" if match.group(f"{p}_sbc") is not None else "[REDACTED"
    pq = match.group(f"{p}_pq")
    if pq is not None:
        return f"{pq}REDACTED{pq}" if match.group(f"{p}_pqc") is not None else f"{pq}REDACTED"
    pb = match.group(f"{p}_pb")
    if pb is not None:
        pbc = match.group(f"{p}_pbc")
        return f"{pb}REDACTED{pbc}" if pbc is not None else f"{pb}REDACTED"
    psb = match.group(f"{p}_psb")
    if psb is not None:
        psbc = match.group(f"{p}_psbc")
        return f"{psb}REDACTED{psbc}" if psbc is not None else f"{psb}REDACTED"
    return "REDACTED"


_SECRET_JSON_KV_RE = re.compile(
    rf"(?P<prefix>(?P<kq>\\{{0,2}}[\"']){_COMP_KEY_RE}(?P=kq)[ \t]*:[ \t]*)(?:{_quoted_val_pattern('j')})",
    re.IGNORECASE,
)
_SECRET_URLENC_JSON_KV_RE = re.compile(
    rf"(?P<prefix>(?P<kq>%(?:25){{0,4}}(?:22|27)){_COMP_KEY_RE}(?P=kq)(?:[ \t]|%(?:25){{0,4}}20|\+)*(?::|%(?:25){{0,4}}3a)(?:[ \t]|%(?:25){{0,4}}20|\+)*)(?:"
    rf"{_quoted_val_pattern('uj')}|"
    rf"(?P<uj_pq>%(?:25){{0,4}}(?:22|27))(?:"
    rf"(?P<uj_pqval>(?:(?!(?P=uj_pq)|%(?:25){{0,4}}(?:26|23|3[bce]|22|27|5[bd])){_TOK_SAFE_CHAR})+)(?P<uj_pqc>(?P=uj_pq))|"
    rf"(?P<uj_upqval>{_UNQUOTED_TOK_RE})"
    rf")"
    rf")",
    re.IGNORECASE,
)
_SECRET_QUOTED_KV_RE = re.compile(
    rf"(?P<prefix>(?<![\w-]){_COMP_KEY_RE}{_KV_SEP_RE}(?:{_AUTH_SCHEME_RE}{_SCHEME_SEP_RE})?)(?:{_quoted_val_pattern('qk')})",
    re.IGNORECASE,
)
_AUTH_HEADER_RE = re.compile(
    rf"(?<![\w-])(?P<hdr>{_COMP_AUTH_KEY_RE}{_KV_SEP_RE})"
    rf"(?:"
    rf"(?P<scheme>{_AUTH_SCHEME_RE}{_SCHEME_SEP_RE})(?:{_delim_val_pattern('s')})"
    rf"|"
    rf"(?!{_AUTH_SCHEME_RE}(?:{_SCHEME_SEP_RE}|$|(?=[&,;\"'`<>#)}}\[%]|\])))(?:{_delim_val_pattern('r')})"
    rf")",
    re.IGNORECASE,
)
_BEARER_RE = re.compile(
    rf"(?P<prefix>(?:(?<![A-Za-z0-9_])|%(?:25){{0,4}}(?:20|22|27|3[acd]|2c|5b|7b))bearer(?:[ \t]+|%(?:25){{0,4}}20))(?:{_delim_val_pattern('b')})",
    re.IGNORECASE,
)
_SID_RE = re.compile(
    rf"(?P<bprefix>(?<![\w-])|%(?:25){{0,4}}(?:3[fb]|26|3b|20|09|22|27))(?P<key>{_COMP_PREFIX_RE}sid)(?P<sep>=|%(?:25){{0,4}}3d)(?:{_delim_val_pattern('sid')})",
    re.IGNORECASE,
)
_SECRET_KV_RE = re.compile(
    rf"(?<![\w-]){_COMP_KV_KEY_RE}(?P<sep>{_KV_SEP_RE})(?:{_delim_val_pattern('kv')})",
    re.IGNORECASE,
)
_HTML_INPUT_TAG_RE = re.compile(r"<input\b[^>]*>", re.IGNORECASE)
_HTML_INPUT_NAME_RE = re.compile(
    r"""(?<![\w-])name\s*=\s*(?:"(?P<dq>[^"]*)"|'(?P<sq>[^']*)'|(?P<uq>[^\s"'=<>`/]+))""",
    re.IGNORECASE,
)
_HTML_INPUT_VALUE_RE = re.compile(
    r"""(?P<prefix>(?<![\w-])value\s*=\s*)(?:"(?P<dq>[^"]+)"|'(?P<sq>[^']+)'|(?P<uq>[^\s"'=<>`]+?)(?=(?:\s|/>|>|$)))""",
    re.IGNORECASE,
)


def _sub_html_input_value(match: re.Match[str]) -> str:
    prefix = match.group("prefix")
    if match.group("dq") is not None:
        return f'{prefix}"REDACTED"'
    if match.group("sq") is not None:
        return f"{prefix}'REDACTED'"
    uq = match.group("uq") or ""
    if uq in ('""', "''"):
        return match.group(0)
    return f"{prefix}REDACTED"


def _sub_html_secret_input(match: re.Match[str]) -> str:
    tag = match.group(0)
    m_name = _HTML_INPUT_NAME_RE.search(tag)
    if m_name is None:
        return tag
    raw_key = m_name.group("dq")
    if raw_key is None:
        raw_key = m_name.group("sq")
    if raw_key is None:
        raw_key = m_name.group("uq") or ""
    if not is_secret_dict_key(raw_key):
        return tag
    return _HTML_INPUT_VALUE_RE.sub(_sub_html_input_value, tag)


def _sub_secret_json_kv(match: re.Match[str]) -> str:
    if not is_secret_dict_key(match.group("key")):
        return match.group(0)
    return f"{match.group('prefix')}{_format_quoted_val(match, 'j')}"


def _sub_secret_urlenc_json_kv(match: re.Match[str]) -> str:
    if not is_secret_dict_key(match.group("key")):
        return match.group(0)
    q = _format_quoted_val(match, "uj")
    if q:
        return f"{match.group('prefix')}{q}"
    pq = match.group("uj_pq")
    if match.group("uj_pqc") is not None:
        return f"{match.group('prefix')}{pq}REDACTED{pq}"
    return f"{match.group('prefix')}{pq}REDACTED"


def _sub_secret_quoted(match: re.Match[str]) -> str:
    if not is_secret_dict_key(match.group("key")):
        return match.group(0)
    return f"{match.group('prefix')}{_format_quoted_val(match, 'qk')}"


def _sub_auth_header(match: re.Match[str]) -> str:
    if not is_secret_dict_key(match.group("key")):
        return match.group(0)
    hdr = match.group("hdr")
    scheme = match.group("scheme")
    if scheme is None:
        return f"{hdr}{_format_delim_val(match, 'r')}"
    return f"{hdr}{scheme}{_format_delim_val(match, 's')}"


def _sub_bearer(match: re.Match[str]) -> str:
    return f"{match.group('prefix')}{_format_delim_val(match, 'b')}"


def _sub_sid_kv(match: re.Match[str]) -> str:
    if not is_secret_dict_key(match.group("key")):
        return match.group(0)
    return f"{match.group('bprefix')}{match.group('key')}{match.group('sep')}{_format_delim_val(match, 'sid')}"


def _sub_secret_kv(match: re.Match[str]) -> str:
    if not is_secret_dict_key(match.group("key")):
        return match.group(0)
    return f"{match.group('key')}{match.group('sep')}{_format_delim_val(match, 'kv')}"


def redact_secret_tokens(text: str, *, include_html_inputs: bool = True) -> str:
    """Redact credentials, sid tokens, auth headers, and secret KV pairs from text."""
    if not text or len(text) < 5:
        return text
    low = text.lower()
    if not any(marker in low for marker in _SECRET_FAST_MARKERS) and (
        text.isascii()
        or ("\u0130" not in text and "\u0131" not in text and "\u017f" not in text and "\u212a" not in text)
    ):
        return text
    masked = (
        _HTML_INPUT_TAG_RE.sub(_sub_html_secret_input, text)
        if include_html_inputs and "<input" in low
        else text
    )
    masked = _SECRET_JSON_KV_RE.sub(_sub_secret_json_kv, masked)
    masked = _SECRET_URLENC_JSON_KV_RE.sub(_sub_secret_urlenc_json_kv, masked)
    masked = _SECRET_QUOTED_KV_RE.sub(_sub_secret_quoted, masked)
    masked = _AUTH_HEADER_RE.sub(_sub_auth_header, masked)
    masked = _BEARER_RE.sub(_sub_bearer, masked)
    masked = _SID_RE.sub(_sub_sid_kv, masked)
    return _SECRET_KV_RE.sub(_sub_secret_kv, masked)


def maybe_reexec_venv(repo_root: Path, *, skip: bool = False) -> None:
    """Re-exec the current CLI script with `<repo_root>/.venv/bin/python3` when needed."""
    if skip or sys.prefix != sys.base_prefix:
        return
    venv_python = repo_root / ".venv" / "bin" / "python3"
    if venv_python.exists() and os.path.abspath(sys.executable) != os.path.abspath(venv_python):
        os.execv(str(venv_python), [str(venv_python), *sys.argv])


def terminate_child(proc: subprocess.Popen[Any]) -> None:
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
    redact_fn: Optional[Callable[[Any], Any]],
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
    val: Any,
    default: str = "",
    *,
    max_len: Optional[int] = None,
    root_cap: Optional[int] = None,
    redact_fn: Optional[Callable[[Any], Any]] = None,
    _depth: int = 0,
    _seen: Optional[set[int]] = None,
    _budget: Optional[list[int]] = None,
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
    redacter = redact_fn if redact_fn is not None else redact_secret_tokens
    if isinstance(val, str):
        txt = val
    elif isinstance(val, (bytes, bytearray)):
        txt = bytes(val).decode("utf-8", errors="replace")
    elif isinstance(val, int) and not isinstance(val, bool):
        txt = game_data.fmt_compact_int(val) if abs(val).bit_length() >= 12_000 else str(val)
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
            masked_val = redact_fn(val) if redact_fn is not None else val
            txt = game_data.safe_key(masked_val)
        except _SAFE_TEXT_EXCS:
            return default
    else:
        try:
            txt = game_data.safe_key(val)
        except _SAFE_TEXT_EXCS:
            return default
    txt = str(redacter(txt))
    if max_len is not None and max_len >= 0 and len(txt) > max_len:
        return txt[:max_len]
    if root_cap is not None and _depth == 0 and len(txt) > root_cap:
        return txt[:root_cap]
    return txt


def enable_pool_dispose(pm: Any) -> None:
    """Attach a connection-pool close callback to prevent leaked socket warnings."""
    if pm is None or isinstance(pm, type):
        return
    pools = getattr(pm, "pools", None)
    if pools is None:
        return
    try:
        dispose = getattr(pools, "dispose_func", ...)
    except _SAFE_TEXT_EXCS:
        return
    if dispose is None:
        try:
            pools.dispose_func = lambda p: p.close()
        except _SAFE_TEXT_EXCS:
            pass


def configure_http_session_pools(sess: Any) -> None:
    """Ensure urllib3 pool managers on a requests session dispose sockets on clear."""
    adapters = getattr(sess, "adapters", None)
    if not isinstance(adapters, Mapping):
        return
    for adapter in list(adapters.values()):
        enable_pool_dispose(getattr(adapter, "poolmanager", None))
        proxy_mgr = getattr(adapter, "proxy_manager", None)
        if isinstance(proxy_mgr, Mapping):
            for pm in list(proxy_mgr.values()):
                enable_pool_dispose(pm)


def close_http_session(sess: Any) -> None:
    """Close an HTTP session after ensuring pool disposal hooks are attached."""
    if sess is None:
        return
    configure_http_session_pools(sess)
    close_fn = getattr(sess, "close", None)
    if callable(close_fn):
        close_fn()


def close_http_response(resp: Any) -> None:
    """Close an HTTP response object when present."""
    if resp is None:
        return
    close_fn = getattr(resp, "close", None)
    if callable(close_fn):
        close_fn()


def call_with_optional_priority(
    fn: Callable[..., Any],
    *args: Any,
    priority: str = "normal",
    **kwargs: Any,
) -> Any:
    """Invoke `fn` passing `priority=...` only if its signature (or wrapped mock) accepts it."""
    call_kwargs = dict(kwargs)
    targets: list[Any] = [fn]
    for attr in ("side_effect", "_mock_wraps"):
        wrapped = getattr(fn, attr, None)
        if callable(wrapped) and not isinstance(wrapped, type):
            targets.append(wrapped)
    accepts_priority = True
    for target in targets:
        try:
            sig = inspect.signature(target)
        except (TypeError, ValueError):
            accepts_priority = False
            break
        if "priority" not in sig.parameters and not any(
            p.kind == inspect.Parameter.VAR_KEYWORD for p in sig.parameters.values()
        ):
            accepts_priority = False
            break
    if accepts_priority:
        call_kwargs["priority"] = priority
    return fn(*args, **call_kwargs)
