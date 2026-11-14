#!/usr/bin/env python3
"""CLI script to log in to SpaceInvasion Classic using `.env` credentials.

Defaults to driving a real Chrome browser via Selenium (`--mode browser`) with a
persistent Chrome profile so interactions look and behave like a normal player.
Use `--mode http` for lightweight headless HTTP authentication.
"""

from __future__ import annotations

import argparse
from collections.abc import Mapping
from dataclasses import asdict, replace
import json
import math
import os
from pathlib import Path
import sys
from typing import Any

_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from spaceinvasion.runtime.cli_support import maybe_reexec_venv, safe_cli_str  # noqa: E402

maybe_reexec_venv(_REPO_ROOT)

from spaceinvasion.core import catalog as game_data  # noqa: E402
from spaceinvasion.core.redaction import redact_sensitive  # noqa: E402
from spaceinvasion.session.auth import (  # noqa: E402
    DEFAULT_SESSIONS_DIR,
    Credentials,
    SpaceInvasionAuthError,
    UniverseOption,
    load_credentials,
    login,
    verify_saved_session,
)
from spaceinvasion.session.browser import (  # noqa: E402
    DEFAULT_DEBUG_HOST,
    DEFAULT_DEBUG_PORT,
    DEFAULT_PROFILE_ROOT,
    BrowserConfig,
    BrowserLoginResult,
    login_with_browser,
)

_SAFE_COERCE_ERRORS: tuple[type[BaseException], ...] = (
    OSError,
    TypeError,
    ValueError,
    RuntimeError,
    AttributeError,
    OverflowError,
    LookupError,
)


def _emit(line: str, *, err: bool = False) -> None:
    (sys.stderr if err else sys.stdout).write(f"{line}\n")


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Authenticate with SpaceInvasion Classic (BitMeUp) using .env credentials."
    )
    parser.add_argument(
        "--mode", choices=("browser", "http"), default="browser", help="Login transport: 'browser' or 'http'."
    )
    parser.add_argument("--env-file", type=Path, default=Path(".env"), help="Path to the .env file (default: .env).")
    parser.add_argument("--account", default="default", help="Named account profile (e.g. ALT).")
    parser.add_argument(
        "--session-dir",
        type=Path,
        default=DEFAULT_SESSIONS_DIR,
        help=f"Session cookie directory (default: {DEFAULT_SESSIONS_DIR}).",
    )
    parser.add_argument(
        "--profile-dir",
        type=Path,
        default=None,
        help=f"Chrome user-data dir (default: {DEFAULT_PROFILE_ROOT}/<account>).",
    )
    parser.add_argument("--headless", action="store_true", help="Run Chrome in headless mode (--mode browser only).")
    parser.add_argument("--keep-open", action="store_true", help="Keep Chrome open after login until Enter.")
    parser.add_argument(
        "--debug-port",
        nargs="?",
        const=DEFAULT_DEBUG_PORT,
        type=int,
        default=None,
        help=f"DevTools debug port (default: {DEFAULT_DEBUG_PORT}).",
    )
    parser.add_argument("--attach", action="store_true", help="Attach to an already-running Chrome debug session.")
    parser.add_argument("--detach", action="store_true", help="Leave Chrome running on the debug port after exit.")
    parser.add_argument("--universe", default=None, help="Optional universe name or ID to open after login.")
    parser.add_argument("--portal-only", action="store_true", help="Stay on the BitMeUp portal after login.")
    parser.add_argument("--allow-join", action="store_true", help="Allow entering a universe marked [JOIN].")
    parser.add_argument("--2fa", dest="two_factor_code", default=None, help="Optional 6-digit 2FA code.")
    parser.add_argument("--verify-session", action="store_true", help="Check whether saved session is authenticated.")
    parser.add_argument("--json", action="store_true", help="Print machine-readable JSON output (redacted).")
    return parser


def _prompt_2fa() -> str:
    if not sys.stdin.isatty():
        return ""
    return input("Enter 6-digit 2FA code: ").strip()


def _wait_for_manual_gate(message: str) -> None:
    if not sys.stdin.isatty():
        return
    _emit(f"\n[ACTION REQUIRED] {message}", err=True)
    input("Press Enter here after completing it in the browser...")


def _safe_cli_str(val: object, default: str = "", *, max_len: int = 1024) -> str:
    return safe_cli_str(val, default, max_len=max_len, redact_fn=redact_sensitive)


def _coerce_cli_json_key(
    key: object,
    *,
    _depth: int = 0,
    _seen: set[int] | None = None,
    _budget: list[int] | None = None,
) -> object:
    if key is None or isinstance(key, (bool, str)):
        return key
    if isinstance(key, int):
        return (
            key
            if -game_data.MAX_JSON_SAFE_INT <= key <= game_data.MAX_JSON_SAFE_INT
            else _safe_cli_str(key, default="key", max_len=256)
        )
    if isinstance(key, float):
        return key if math.isfinite(key) else "null"
    if isinstance(key, (bytes, bytearray)):
        return bytes(key).decode("utf-8", errors="replace")
    if isinstance(key, BaseException):
        return _safe_cli_str(key, default="<unprintable>", max_len=512)
    seen = _seen if _seen is not None else set()
    budget = _budget if _budget is not None else [256]
    if isinstance(key, os.PathLike):
        vid = id(key)
        if vid in seen or _depth >= 16 or budget[0] <= 0:
            return "<unprintable>"
        budget[0] -= 1
        seen.add(vid)
        try:
            try:
                fsp: object = os.fspath(key)
            except _SAFE_COERCE_ERRORS:
                return "<unprintable>"
            return _coerce_cli_json_key(fsp, _depth=_depth + 1, _seen=seen, _budget=budget)
        finally:
            seen.discard(vid)
    if isinstance(key, (tuple, frozenset, list, set, Mapping)):
        vid = id(key)
        if vid in seen:
            if isinstance(key, tuple):
                return "(...)"
            if isinstance(key, list):
                return "[...]"
            return "frozenset({...})" if isinstance(key, frozenset) else "{...}"
        if _depth >= 16 or budget[0] <= 0:
            return "..."
        budget[0] -= 1
        seen.add(vid)
        try:
            if isinstance(key, (tuple, frozenset)):
                parts: list[str] = []
                for idx, el in enumerate(key):
                    if idx >= 64 or budget[0] <= 0:
                        parts.append("...")
                        break
                    parts.append(str(_coerce_cli_json_key(el, _depth=_depth + 1, _seen=seen, _budget=budget)))
                if isinstance(key, tuple):
                    inner = ", ".join(parts)
                    return (f"({inner},)" if len(key) == 1 else f"({inner})")[:4096]
                return f"frozenset({{{', '.join(sorted(parts))}}})"[:4096]
            return game_data.safe_key(key)
        finally:
            seen.discard(vid)
    return _safe_cli_str(key, default="<unprintable>", max_len=512)


def _coerce_cli_json_scalars(
    obj: object,
    *,
    _depth: int = 0,
    _seen: set[int] | None = None,
    _budget: list[int] | None = None,
) -> object:
    if obj is None or isinstance(obj, (bool, str)):
        return obj
    if isinstance(obj, int):
        return (
            obj
            if -game_data.MAX_JSON_SAFE_INT <= obj <= game_data.MAX_JSON_SAFE_INT
            else _safe_cli_str(obj, default="0", max_len=256)
        )
    if isinstance(obj, float):
        return obj if math.isfinite(obj) else None
    if isinstance(obj, (bytes, bytearray)):
        return bytes(obj).decode("utf-8", errors="replace")
    if isinstance(obj, BaseException):
        return _safe_cli_str(obj, default="<unprintable>", max_len=1024)
    if isinstance(obj, (Mapping, list, tuple, set, frozenset, os.PathLike)):
        seen = _seen if _seen is not None else set()
        budget = _budget if _budget is not None else [32_768]
        vid = id(obj)
        if vid in seen:
            return "<unprintable>" if isinstance(obj, os.PathLike) else "[Cycle]"
        if _depth >= 64 or budget[0] <= 0:
            return "<unprintable>" if isinstance(obj, os.PathLike) else "[Truncated]"
        budget[0] -= 1
        seen.add(vid)
        try:
            if isinstance(obj, os.PathLike):
                try:
                    raw: object = os.fspath(obj)
                except _SAFE_COERCE_ERRORS:
                    return "<unprintable>"
                if isinstance(raw, (bytes, bytearray)):
                    return bytes(raw).decode("utf-8", errors="replace")
                return raw if isinstance(raw, str) else "<unprintable>"
            if isinstance(obj, Mapping):
                out: dict[str, object] = {}
                for k, v in obj.items():
                    if budget[0] <= 0:
                        out["[Truncated]"] = "[Truncated]"
                        break
                    out[str(_coerce_cli_json_key(k))] = _coerce_cli_json_scalars(
                        v, _depth=_depth + 1, _seen=seen, _budget=budget
                    )
                return out
            out_l: list[object] = []
            for v in obj:
                if budget[0] <= 0:
                    out_l.append("[Truncated]")
                    break
                out_l.append(_coerce_cli_json_scalars(v, _depth=_depth + 1, _seen=seen, _budget=budget))
            return out_l
        finally:
            seen.discard(vid)
    return _safe_cli_str(obj, default="<unprintable>", max_len=1024)


def _safe_cli_json(payload: object) -> str:
    pre_redacted = redact_sensitive(payload)
    coerced = _coerce_cli_json_scalars(pre_redacted)
    sanitized = game_data.sanitize_json_tree(coerced, max_int=game_data.MAX_JSON_SAFE_INT, nonfinite_float=None)
    return json.dumps(redact_sensitive(sanitized), indent=2, allow_nan=False)


def _safe_finite_float(val: object) -> float | None:
    if val is None or isinstance(val, bool) or not isinstance(val, (int, float)):
        return None
    try:
        f = float(val)
    except (TypeError, ValueError, OverflowError):
        return None
    return f if math.isfinite(f) else None


def _print_universes(universes: tuple[UniverseOption, ...]) -> None:
    if not universes:
        return
    _emit("Discovered universes:")
    for uni in universes[:15]:
        label = redact_sensitive(_safe_cli_str(uni.label))
        if uni.universe_id is not None:
            game = "SI2" if uni.is_si2 else "SI Classic"
            status = "PLAY" if uni.has_account else "JOIN"
            sp_f, fl_f = _safe_finite_float(uni.speed), _safe_finite_float(uni.flight_speed)
            speed = f"{sp_f:g}x/{fl_f:g}x" if (sp_f is not None and fl_f is not None and sp_f > 0 and fl_f > 0) else "?"
            uid_txt = (
                game_data.fmt_compact_int(uni.universe_id)
                if isinstance(uni.universe_id, int) and not isinstance(uni.universe_id, bool)
                else redact_sensitive(_safe_cli_str(uni.universe_id))
            )
            _emit(f"  - [{status}] {label} (id={uid_txt}, {game}, {speed})")
        else:
            _emit(f"  - {label}: {redact_sensitive(_safe_cli_str(uni.url))}")


def _run_verify_session(args: argparse.Namespace) -> int:
    authenticated, url, title = verify_saved_session(account=args.account, session_dir=args.session_dir)
    if args.json:
        _emit(_safe_cli_json({"authenticated": authenticated, "account": args.account, "url": url, "title": title}))
    else:
        state = "ACTIVE" if authenticated else "EXPIRED / LOGGED OUT"
        _emit(f"Session ({redact_sensitive(_safe_cli_str(args.account))}): {state}")
        _emit(f"URL:   {redact_sensitive(_safe_cli_str(url))}")
        _emit(f"Title: {redact_sensitive(_safe_cli_str(title))}")
    return 0 if authenticated else 1


def _run_http_login(args: argparse.Namespace, creds: Credentials) -> int:
    result = login(creds=creds, session_dir=args.session_dir, two_factor_provider=_prompt_2fa)
    if args.json:
        serializable = asdict(result)
        serializable["cookie_path"] = redact_sensitive(_safe_cli_str(result.cookie_path))
        _emit(_safe_cli_json(serializable))
        return 0
    cookies_txt = ", ".join(str(redact_sensitive(_safe_cli_str(name))) for name in (result.cookie_names or ()))
    _emit(
        f"Login successful for {redact_sensitive(_safe_cli_str(result.email))} (profile: {redact_sensitive(_safe_cli_str(result.account_name))})"
    )
    _emit(f"Final URL:  {redact_sensitive(_safe_cli_str(result.final_url))}")
    _emit(f"Page Title: {redact_sensitive(_safe_cli_str(result.page_title))}")
    _emit(f"Cookies:    {cookies_txt} -> {redact_sensitive(_safe_cli_str(result.cookie_path))}")
    _print_universes(result.universes)
    return 0


def _run_browser_login(args: argparse.Namespace, creds: Credentials) -> int:
    debug_port = DEFAULT_DEBUG_PORT if (args.attach or args.detach) and args.debug_port is None else args.debug_port
    browser_cfg = BrowserConfig(
        headless=args.headless,
        profile_dir=args.profile_dir or (DEFAULT_PROFILE_ROOT / creds.account_name),
        keep_open=args.keep_open,
        debug_port=debug_port,
        debug_host=DEFAULT_DEBUG_HOST,
        attach=args.attach,
        detach=args.detach,
    )

    def _hold_if_requested(_driver: Any, res: BrowserLoginResult) -> None:
        if args.keep_open and not args.headless and not args.detach:
            _emit(f"\nBrowser is open at {redact_sensitive(_safe_cli_str(res.final_url))}")
            if res.debug_address:
                _emit(f"Attachable debug port: {redact_sensitive(_safe_cli_str(res.debug_address))}")
            input("Press Enter to close Chrome and exit...")

    result = login_with_browser(
        creds=creds,
        config=browser_cfg,
        session_dir=args.session_dir,
        two_factor_provider=_prompt_2fa,
        wait_for_manual_gate=_wait_for_manual_gate if sys.stdin.isatty() else None,
        enter_universe="" if args.portal_only else args.universe,
        allow_join=args.allow_join,
        on_session_ready=_hold_if_requested,
    )
    if args.json:
        serializable = asdict(result)
        serializable["cookie_path"] = redact_sensitive(_safe_cli_str(result.cookie_path))
        serializable["profile_dir"] = redact_sensitive(_safe_cli_str(result.profile_dir))
        _emit(_safe_cli_json(serializable))
        return 0
    reused = " (reused existing browser session)" if result.already_logged_in else ""
    cookies_txt = ", ".join(str(redact_sensitive(_safe_cli_str(name))) for name in (result.cookie_names or ()))
    _emit(
        f"Browser login successful for {redact_sensitive(_safe_cli_str(result.email))} (profile: {redact_sensitive(_safe_cli_str(result.account_name))}){reused}"
    )
    _emit(f"Final URL:   {redact_sensitive(_safe_cli_str(result.final_url))}")
    _emit(f"Page Title:  {redact_sensitive(_safe_cli_str(result.page_title))}")
    _emit(f"Chrome Dir:  {redact_sensitive(_safe_cli_str(result.profile_dir))}")
    if result.debug_address:
        _emit(f"Debug Port:  {redact_sensitive(_safe_cli_str(result.debug_address))}")
    _emit(f"Cookies:     {cookies_txt} -> {redact_sensitive(_safe_cli_str(result.cookie_path))}")
    if result.entered_universe:
        _emit(f"Universe:    {redact_sensitive(_safe_cli_str(result.entered_universe))}")
    _print_universes(result.universes)
    return 0


def _emit_cli_error(args: argparse.Namespace, prefix: str, exc: BaseException) -> int:
    err_txt = redact_sensitive(_safe_cli_str(exc))
    if args.json:
        _emit(_safe_cli_json({"ok": False, "error": err_txt}))
    else:
        _emit(f"{prefix}: {err_txt}", err=True)
    return 1


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    try:
        if args.verify_session:
            return _run_verify_session(args)
        creds = load_credentials(env_path=args.env_file, account=args.account)
        if args.two_factor_code:
            creds = replace(creds, two_factor_code=args.two_factor_code)
        return _run_http_login(args, creds) if args.mode == "http" else _run_browser_login(args, creds)
    except SpaceInvasionAuthError as exc:
        return _emit_cli_error(args, "Auth error", exc)
    except (OSError, RuntimeError, ValueError, TypeError, KeyError, LookupError) as exc:
        return _emit_cli_error(args, "Unexpected error during login", exc)


if __name__ == "__main__":
    raise SystemExit(main())
