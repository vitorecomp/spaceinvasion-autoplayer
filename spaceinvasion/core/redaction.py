"""Canonical secret redaction for logs, errors, API payloads, configs, and DB DSNs."""

from __future__ import annotations

from collections.abc import Mapping
import functools
import itertools
import os
import re
from typing import TypeVar, overload
from urllib.parse import parse_qs, urlparse, urlunparse

_KT = TypeVar("_KT")

SECRET_DICT_KEYS: frozenset[str] = frozenset("sid password passwd cookie api_key secret authorization".split())
_SECRET_STEMS: tuple[str, ...] = tuple("password passwd cookie api_key api-key secret authorization sid".split())
_BENIGN_PREFIXES = frozenset("has no non without is allow require enable disable need check use missing empty".split())
_FAST_MARKERS: tuple[str, ...] = tuple("sid pass cookie api_key api-key secret auth bearer".split())
_CONFUSABLES: tuple[str, ...] = ("\u0130", "\u0131", "\u017f", "\u212a")
_SAFE_ERRS = (TypeError, ValueError, OSError, RuntimeError, AttributeError, LookupError, OverflowError, RecursionError)
_MAX_KEY_NODES, _MAX_KEY_CHARS, _MAX_TREE_NODES, _MAX_TREE_DEPTH = 256, 4096, 32_768, 64


def _clip(text: str, limit: int = _MAX_KEY_CHARS) -> str:
    return text if len(text) <= limit else text[:limit] + "...[truncated]"


def is_secret_dict_key(key: object) -> bool:
    """Return True if ``key`` names a secret field (handling Unicode confusables and benign prefixes)."""
    if not isinstance(key, str) or not (low := key.strip().lower()):
        return False
    if not low.isascii():
        low = low.replace("i\u0307", "i").replace("\u0131", "i").replace("\u017f", "s")
    if low == "api-key" or low in SECRET_DICT_KEYS:
        return True
    for stem in _SECRET_STEMS:
        if not low.endswith(stem):
            continue
        prefix = low[: len(low) - len(stem)]
        if not prefix or prefix[-1] not in ("_", "-"):
            return not prefix
        parts = [p for p in prefix.strip("_-").replace("-", "_").split("_") if p]
        return (not parts) or (all(p.isascii() and p.isalnum() for p in parts) and parts[0] not in _BENIGN_PREFIXES)
    return False


_PRE = r"[_-]*(?:[a-z0-9]+[_-]+)*"
_K_ALL = rf"(?P<key>{_PRE}(?:sid|password|passwd|cookie|api_key|api-key|secret|authorization))"
_K_KV = rf"(?P<key>{_PRE}(?:password|passwd|cookie|api_key|api-key|secret))"
_K_AUTH, _SCHEME = rf"(?P<key>{_PRE}authorization)", r"(?:bearer|basic|token|digest)"
_KV_SEP, _SCH_SEP = r"(?:[ \t]*[:=][ \t]*|%(?:25){0,4}3[ad])", r"(?:[ \t]+|%(?:25){0,4}20|\+)"
_SAFE_CH, _STOP = r"[^\s&,;\"'`<>#)}\]\[]", r"%(?:25){0,4}(?:26|23|3[bce]|22|27|5[bd])"
_UNQ = rf"(?:(?!%(?:25){{0,4}}(?:26|23|20|09|0[ad]|22|27|3[bce]|5[bd])){_SAFE_CH})+"


def _qpat(p: str) -> str:
    return (
        rf"(?P<{p}_vq>[\"'])(?:(?P<{p}_qval>(?:(?!(?P={p}_vq))(?:\\[^\r\n]|[^\\\r\n]))+)(?P<{p}_cq>(?P={p}_vq))|"
        rf"(?!(?P={p}_vq)|\\?[\"'])(?P<{p}_uqval>{_UNQ}))|"
        rf"(?P<{p}_evq2>\\\\(?P<{p}_ec2>[\"']))(?:(?P<{p}_eqval2>(?:[^\\\r\n]|\\\\(?!(?P={p}_ec2))|"
        rf"\\(?!(?P={p}_ec2)|\\)|\\\\\\(?P={p}_ec2))+)(?P<{p}_ecq2>(?P={p}_evq2))|"
        rf"(?!(?P={p}_evq2)|\\?[\"'])(?P<{p}_euqval2>{_UNQ}))|"
        rf"(?P<{p}_evq>\\(?P<{p}_ec>[\"']))(?:(?P<{p}_eqval>(?:[^\\\r\n]|\\\\\\\\|\\\\\\(?P={p}_ec)|"
        rf"\\\\(?!(?P={p}_ec))[^\\\r\n]|\\(?!(?P={p}_ec))[^\\\r\n])+)(?P<{p}_ecq>(?P={p}_evq))|"
        rf"(?!(?P={p}_evq)|\\?[\"'])(?P<{p}_euqval>{_UNQ}))"
    )


def _dpat(p: str) -> str:
    return (
        f"{_qpat(p)}|"
        rf"(?P<{p}_bk><)(?:(?P<{p}_bkval>[ \t]*[^\s<>\r\n][^<>\r\n]*)(?P<{p}_bkc>>)|(?P<{p}_ubkval>{_UNQ}))|"
        rf"(?P<{p}_sb>\[)(?:(?P<{p}_sbval>[ \t]*[^\s\[\]\r\n][^\[\]\r\n]*)(?P<{p}_sbc>\])|(?P<{p}_usbval>{_UNQ}))|"
        rf"(?P<{p}_pq>%(?:25){{0,4}}(?:22|27))(?:(?P<{p}_pqval>(?:(?!(?P={p}_pq)|{_STOP}){_SAFE_CH})+)(?P<{p}_pqc>(?P={p}_pq))|(?P<{p}_upqval>{_UNQ}))|"
        rf"(?P<{p}_pb>%(?:25){{0,4}}3c)(?:(?P<{p}_pbval>(?:(?!{_STOP}){_SAFE_CH})+)(?P<{p}_pbc>%(?:25){{0,4}}3e)|(?P<{p}_upbval>{_UNQ}))|"
        rf"(?P<{p}_psb>%(?:25){{0,4}}5b)(?:(?P<{p}_psbval>(?:(?!{_STOP}){_SAFE_CH})+)(?P<{p}_psbc>%(?:25){{0,4}}5d)|(?P<{p}_upsbval>{_UNQ}))|"
        rf"(?!(?:\\?[\"']|<|\[|%(?:25){{0,4}}(?:22|27|3c|5b)))(?P<{p}_uval>{_UNQ})"
    )


def _fmt_val(match: re.Match[str], p: str, *, quoted_only: bool = False) -> str:
    rules: tuple[tuple[str, str, str | None], ...] = (
        (f"{p}_vq", f"{p}_cq", None),
        (f"{p}_evq2", f"{p}_ecq2", None),
        (f"{p}_evq", f"{p}_ecq", None),
    )
    if not quoted_only:
        rules += (
            (f"{p}_bk", f"{p}_bkc", ">"),
            (f"{p}_sb", f"{p}_sbc", "]"),
            (f"{p}_pq", f"{p}_pqc", None),
            (f"{p}_pb", f"{p}_pbc", None),
            (f"{p}_psb", f"{p}_psbc", None),
        )
    for ok, ck, close_ch in rules:
        if (tok := match.group(ok)) is not None:
            ctok = close_ch if close_ch is not None else tok
            return f"{tok}REDACTED{ctok if match.group(ck) is not None else ''}"
    return "" if quoted_only else "REDACTED"


_JSON_KV_RE = re.compile(rf"(?P<prefix>(?P<kq>\\{{0,2}}[\"']){_K_ALL}(?P=kq)[ \t]*:[ \t]*)(?:{_qpat('j')})", re.I)
_URLENC_JSON_RE = re.compile(
    rf"(?P<prefix>(?P<kq>%(?:25){{0,4}}(?:22|27)){_K_ALL}(?P=kq)(?:[ \t]|%(?:25){{0,4}}20|\+)*(?::|%(?:25){{0,4}}3a)"
    rf"(?:[ \t]|%(?:25){{0,4}}20|\+)*)(?:{_qpat('uj')}|(?P<uj_pq>%(?:25){{0,4}}(?:22|27))"
    rf"(?:(?P<uj_pqval>(?:(?!(?P=uj_pq)|{_STOP}){_SAFE_CH})+)(?P<uj_pqc>(?P=uj_pq))|(?P<uj_upqval>{_UNQ})))",
    re.I,
)
_QUOTED_KV_RE = re.compile(rf"(?P<prefix>(?<![\w-]){_K_ALL}{_KV_SEP}(?:{_SCHEME}{_SCH_SEP})?)(?:{_qpat('qk')})", re.I)
_AUTH_HDR_RE = re.compile(
    rf"(?<![\w-])(?P<hdr>{_K_AUTH}{_KV_SEP})(?:(?P<scheme>{_SCHEME}{_SCH_SEP})(?:{_dpat('s')})|"
    rf"(?!{_SCHEME}(?:{_SCH_SEP}|$|(?=[&,;\"'`<>#)}}\[%]|\])))(?:{_dpat('r')}))",
    re.I,
)
_BEARER_RE = re.compile(
    rf"(?P<prefix>(?:(?<![A-Za-z0-9_])|%(?:25){{0,4}}(?:20|22|27|3[acd]|2c|5b|7b))bearer(?:[ \t]+|%(?:25){{0,4}}20))(?:{_dpat('b')})",
    re.I,
)
_SID_RE = re.compile(
    rf"(?P<bprefix>(?<![\w-])|%(?:25){{0,4}}(?:3[fb]|26|3b|20|09|22|27))(?P<key>{_PRE}sid)(?P<sep>=|%(?:25){{0,4}}3d)(?:{_dpat('sid')})",
    re.I,
)
_KV_RE = re.compile(rf"(?<![\w-]){_K_KV}(?P<sep>{_KV_SEP})(?:{_dpat('kv')})", re.I)
_DSN_RE = re.compile(r"(postgres(?:ql)?(?:\+[a-z0-9_]+)?://[^:/@\s\"'<>]*:)[^@\s\"'<>]+(@)", re.I)
_HTML_INP_RE = re.compile(r"<input\b[^>]*>", re.I)
_HTML_NAME_RE = re.compile(r"""(?<![\w-])name\s*=\s*(?:"(?P<dq>[^"]*)"|'(?P<sq>[^']*)'|(?P<uq>[^\s"'=<>`/]+))""", re.I)
_HTML_VAL_RE = re.compile(
    r"""(?P<prefix>(?<![\w-])value\s*=\s*)(?:"(?P<dq>[^"]+)"|'(?P<sq>[^']+)'|(?P<uq>[^\s"'=<>`]+?)(?=(?:\s|/>|>|$)))""",
    re.I,
)
_WD_STACK_RE = re.compile(r"(?:\r?\n|\s)+Stacktrace:\s*(?:\r?\n\s*#\d+\s+[^\r\n]*)*.*$", re.DOTALL)
_WD_INFO_RE = re.compile(r"(?:\r?\n|\s)+\((?:Session|Driver|Build) info:[^\r\n)]*\)", re.I)


def _sub_html_input(match: re.Match[str]) -> str:
    if (m_name := _HTML_NAME_RE.search(tag := match.group(0))) is None:
        return tag
    dq = m_name.group("dq")
    if not is_secret_dict_key(dq if dq is not None else (m_name.group("sq") or m_name.group("uq") or "")):
        return tag

    def _val(vm: re.Match[str]) -> str:
        pre, dq, sq = vm.group("prefix"), vm.group("dq"), vm.group("sq")
        if dq is not None or sq is not None:
            return f'{pre}"REDACTED"' if dq is not None else f"{pre}'REDACTED'"
        return vm.group(0) if (vm.group("uq") or "") in ('""', "''") else f"{pre}REDACTED"

    return _HTML_VAL_RE.sub(_val, tag)


def _sub_secret(match: re.Match[str], p: str, mode: str) -> str:
    if mode == "bearer":
        return f"{match.group('prefix')}{_fmt_val(match, p)}"
    if not is_secret_dict_key(match.group("key")):
        return match.group(0)
    if mode == "quoted":
        return f"{match.group('prefix')}{_fmt_val(match, p, quoted_only=True)}"
    if mode == "urlenc":
        if quoted := _fmt_val(match, p, quoted_only=True):
            return f"{match.group('prefix')}{quoted}"
        pq = match.group("uj_pq")
        return f"{match.group('prefix')}{pq}REDACTED{pq if match.group('uj_pqc') is not None else ''}"
    if mode == "auth":
        hdr, scheme = match.group("hdr"), match.group("scheme")
        return f"{hdr}{_fmt_val(match, 'r')}" if scheme is None else f"{hdr}{scheme}{_fmt_val(match, 's')}"
    head = f"{match.group('bprefix') if mode == 'sid' else ''}{match.group('key')}{match.group('sep')}"
    return f"{head}{_fmt_val(match, p)}"


def _sub_sid_kv(match: re.Match[str]) -> str:
    return _sub_secret(match, "sid", "sid")


_TEXT_RULES = (
    (_JSON_KV_RE, "j", "quoted"),
    (_URLENC_JSON_RE, "uj", "urlenc"),
    (_QUOTED_KV_RE, "qk", "quoted"),
    (_AUTH_HDR_RE, "s", "auth"),
    (_BEARER_RE, "b", "bearer"),
    (_SID_RE, "sid", "sid"),
    (_KV_RE, "kv", "kv"),
)


def redact_text(text: str) -> str:
    """Scrub session tokens, auth headers, secret KVs, HTML secret inputs, and DSN passwords."""
    if not text or len(text) < 5:
        return text
    low = text.lower()
    has_dsn = "://" in text and "@" in text and "postgres" in low
    if not has_dsn and not any(m in low for m in _FAST_MARKERS):
        if text.isascii() or not any(c in text for c in _CONFUSABLES):
            return text
    out = _DSN_RE.sub(r"\1***\2", text) if has_dsn else text
    if "<input" in low:
        out = _HTML_INP_RE.sub(_sub_html_input, out)
    for regex, p, mode in _TEXT_RULES:
        out = regex.sub(functools.partial(_sub_secret, p=p, mode=mode), out)
    return out


@functools.lru_cache(maxsize=512)
def is_clean_api_key(key: str) -> bool:
    """Fast cached check for short ASCII dict keys containing no secret tokens."""
    return bool(key) and len(key) <= 64 and key.isascii() and redact_text(key) == key and not is_secret_dict_key(key)


def redact_db_url(url_or_path: object) -> str:
    """Mask password credentials in PostgreSQL DSNs while preserving host/socket/query structure."""
    txt = str(url_or_path or "").strip()
    if not txt.lower().startswith(("postgresql://", "postgres://", "postgresql+")):
        return redact_text(txt)
    try:
        parsed = urlparse(txt)
        is_unix = any(str(h).strip().startswith("/") for h in parse_qs(parsed.query).get("host", ()))
        q = redact_text(parsed.query)
        if parsed.password or q != parsed.query:
            netloc = parsed.netloc
            if parsed.password:
                user = f"{parsed.username}:***" if parsed.username else "***"
                host = parsed.hostname if parsed.hostname is not None else ("" if is_unix else "127.0.0.1")
                netloc = f"{user}@{host}:{parsed.port}" if parsed.port else f"{user}@{host}"
            return urlunparse((parsed.scheme, netloc, parsed.path, parsed.params, q, parsed.fragment))
        return txt
    except ValueError:
        return _DSN_RE.sub(r"\1***\2", txt)


def _safe_text(val: object, default: str, depth: int, seen: set[int], budget: list[int]) -> str:
    if val is None or isinstance(val, str):
        return default if val is None else val
    if isinstance(val, (bytes, bytearray)):
        return bytes(val).decode("utf-8", errors="replace")
    if isinstance(val, int) and not isinstance(val, bool):
        bits = abs(val).bit_length()
        return f"{'-' if val < 0 else ''}~1e{int(bits * 0.3010299956639812)}" if bits >= 12_000 else str(val)
    if isinstance(val, os.PathLike):
        try:
            raw: object = os.fspath(val)
        except _SAFE_ERRS:
            return default
        return default if isinstance(raw, os.PathLike) else _safe_text(raw, default, depth + 1, seen, budget)
    if isinstance(val, (BaseException, tuple, list, set, frozenset, Mapping)):
        vid, d = id(val), depth + 1
        if vid in seen or depth >= 16 or budget[0] <= 0:
            if isinstance(val, BaseException):
                return f"<{type(val).__name__}>"
            return "(...)" if isinstance(val, tuple) else ("{...}" if isinstance(val, Mapping) else "[...]")
        budget[0] -= 1
        seen.add(vid)
        try:
            if isinstance(val, BaseException):
                str_fn = getattr(type(val), "__str__", BaseException.__str__)
                if str_fn not in (BaseException.__str__, KeyError.__str__):
                    try:
                        return _clip(str(val), 16_384)
                    except _SAFE_ERRS:
                        pass
                args = getattr(val, "args", ())
                if not isinstance(args, (tuple, list)) or not args:
                    return "" if str_fn is BaseException.__str__ else type(val).__name__
                if len(args) == 1:
                    a0 = args[0]
                    return (
                        _clip(repr(a0) if isinstance(val, KeyError) else a0, 16_384)
                        if isinstance(a0, str)
                        else _clip(_safe_text(a0, f"<{type(a0).__name__}>", d, seen, budget))
                    )
                parts = [
                    repr(a) if isinstance(a, str) else _safe_text(a, f"<{type(a).__name__}>", d, seen, budget)
                    for a in args[:8]
                    if budget[0] > 0
                ] + ([f"...+{len(args) - 8}"] if len(args) > 8 else [])
                return _clip(f"({', '.join(parts)})")
            if isinstance(val, Mapping):
                pairs = [
                    f"{_safe_text(k, '<key>', d, seen, budget)!r}: {_safe_text(v, '<val>', d, seen, budget)!r}"
                    for k, v in itertools.islice(val.items(), 32)
                    if budget[0] > 0
                ]
                return _clip("{" + ", ".join(pairs) + "}")
            items = [
                _safe_text(el, f"<{type(el).__name__}>", d, seen, budget)
                for el in itertools.islice(val, 64)
                if budget[0] > 0
            ]
            inner = ", ".join(items)
            return _clip((f"({inner},)" if len(val) == 1 else f"({inner})") if isinstance(val, tuple) else f"[{inner}]")
        finally:
            seen.discard(vid)
    try:
        return str(val)
    except _SAFE_ERRS:
        return f"<{type(val).__name__}>"


def _redact_key(key: object, api: bool, depth: int, seen: set[int], budget: list[int]) -> object:
    if key is None or isinstance(key, (bool, int, float)):
        return key
    if isinstance(key, (str, bytes, bytearray)):
        return redact_text(key if isinstance(key, str) else bytes(key).decode("utf-8", errors="replace"))
    err_tok = "<unprintable>" if api else f"<{type(key).__name__}>"
    if isinstance(key, (tuple, frozenset)):
        vid = id(key)
        if vid in seen or depth >= 16 or budget[0] <= 0:
            return "(...)" if isinstance(key, tuple) else "frozenset({...})"
        budget[0] -= 1
        seen.add(vid)
        try:
            items: list[object] = []
            for idx, el in enumerate(key):
                if idx >= 64 or budget[0] <= 0:
                    items.append("...")
                    break
                items.append(_redact_key(el, api, depth + 1, seen, budget))
            return tuple(items) if isinstance(key, tuple) else frozenset(items)
        except TypeError:
            pass
        finally:
            seen.discard(vid)
    out = redact_text(_safe_text(key, err_tok, depth, seen, budget))
    return out[:512] if api and isinstance(key, BaseException) and len(out) > 512 else out


def _redact_tree(obj: object, api: bool, depth: int, seen: set[int], budget: list[int]) -> object:
    if obj is None or isinstance(obj, (bool, int, float)):
        return obj
    if isinstance(obj, str):
        return obj if (len(obj) < 5 and obj.isascii()) else redact_text(obj)
    if isinstance(obj, (bytes, bytearray)):
        return redact_text(bytes(obj).decode("utf-8", errors="replace"))
    if isinstance(obj, (BaseException, os.PathLike)):
        err_tok = "<unprintable>" if api else f"<{type(obj).__name__}>"
        out = redact_text(_safe_text(obj, err_tok, 0, set(), [_MAX_KEY_NODES]))
        return out[:2048] if api and isinstance(obj, BaseException) and len(out) > 2048 else out
    if not isinstance(obj, (Mapping, list, tuple, set, frozenset)):
        return obj
    vid = id(obj)
    if vid in seen or depth >= _MAX_TREE_DEPTH or budget[0] <= 0:
        return "[Cycle]" if vid in seen else "[Truncated]"
    budget[0] -= 1
    seen.add(vid)
    try:
        if isinstance(obj, Mapping):
            out_map: dict[object, object] = {}
            for k, v in obj.items():
                if budget[0] <= 0:
                    out_map["[Truncated]"] = "[Truncated]"
                    break
                if type(k) is str and is_clean_api_key(k):
                    out_map[k] = _redact_tree(v, api, depth + 1, seen, budget)
                    continue
                ck: object = redact_text(k) if isinstance(k, str) else _redact_key(k, api, 0, set(), [_MAX_KEY_NODES])
                if isinstance(ck, str) and is_secret_dict_key(ck):
                    empty_b = isinstance(v, (bytes, bytearray)) and not v
                    out_map[ck] = v if (v is None or v == "") else ("" if empty_b else "REDACTED")
                else:
                    out_map[ck] = _redact_tree(v, api, depth + 1, seen, budget)
            return out_map
        out_list: list[object] = []
        for item in obj:
            if budget[0] <= 0:
                out_list.append("[Truncated]")
                break
            out_list.append(_redact_tree(item, api, depth + 1, seen, budget))
        return out_list
    finally:
        seen.discard(vid)


@overload
def redact_sensitive(obj: str) -> str: ...
@overload
def redact_sensitive(obj: Mapping[_KT, object]) -> dict[_KT, object]: ...
@overload
def redact_sensitive(obj: object) -> object: ...
def redact_sensitive(obj: object) -> object:
    """Recursively mask session tokens, credentials, and secret key-value fields."""
    return _redact_tree(obj, False, 0, set(), [_MAX_TREE_NODES])


def redact_api_payload(value: object) -> object:
    """Recursively scrub API response payloads using the cached ASCII-clean key fast path."""
    return _redact_tree(value, True, 0, set(), [_MAX_TREE_NODES])


def strip_webdriver_stacktrace(text: str) -> str:
    """Strip noisy ChromeDriver/Selenium C++ stacktrace and session-info footers."""
    if not isinstance(text, str) or not text:
        return ""
    return re.sub(r"\s*\r?\n\s*", " ", _WD_INFO_RE.sub("", _WD_STACK_RE.sub("", text))).strip()


def format_clean_error(err: object, *, max_chars: int = 512) -> str:
    """Redact sensitive tokens, strip ChromeDriver stacktraces, and format an error cleanly."""
    red, tok = redact_sensitive(err), f"<{type(err).__name__}>"
    txt = red if isinstance(red, str) else ("" if red is None else _safe_text(red, tok, 0, set(), [_MAX_KEY_NODES]))
    clean = strip_webdriver_stacktrace(txt) or (tok if isinstance(err, BaseException) else "")
    return clean[:max_chars].rstrip() + "...[truncated]" if (max_chars > 0 and len(clean) > max_chars) else clean
