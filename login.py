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


def _maybe_reexec_venv() -> None:
    """Re-exec with repo `.venv/bin/python3` when invoked via bare system python."""
    if os.environ.get("SPACEINVASION_NO_REEXEC") == "1":
        return
    if sys.prefix != sys.base_prefix:
        return
    venv_python = Path(__file__).resolve().parent / ".venv" / "bin" / "python3"
    if venv_python.exists() and os.path.abspath(sys.executable) != os.path.abspath(venv_python):
        os.execv(str(venv_python), [str(venv_python), *sys.argv])


_maybe_reexec_venv()

from spaceinvasion import game_data  # noqa: E402
from spaceinvasion.auth import (  # noqa: E402
    SpaceInvasionAuthError,
    UniverseOption,
    load_credentials,
    login,
    verify_saved_session,
)
from spaceinvasion.browser import (  # noqa: E402
    DEFAULT_DEBUG_PORT,
    BrowserConfig,
    login_with_browser,
)
from spaceinvasion.logging_utils import redact_sensitive  # noqa: E402


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Authenticate with SpaceInvasion Classic (BitMeUp) using .env credentials."
    )
    parser.add_argument(
        "--mode",
        choices=("browser", "http"),
        default="browser",
        help="Login transport: 'browser' (Selenium Chrome, default) or 'http' (requests).",
    )
    parser.add_argument(
        "--env-file",
        type=Path,
        default=Path(".env"),
        help="Path to the .env file containing credentials (default: .env).",
    )
    parser.add_argument(
        "--account",
        default="default",
        help="Optional named account profile (e.g. ALT -> SPACEINVASION_ALT_EMAIL).",
    )
    parser.add_argument(
        "--session-dir",
        type=Path,
        default=Path(".sessions"),
        help="Directory to store saved session cookies (default: .sessions).",
    )
    parser.add_argument(
        "--profile-dir",
        type=Path,
        default=None,
        help="Persistent Chrome user-data directory (default: .browser_profile/<account>).",
    )
    parser.add_argument(
        "--headless",
        action="store_true",
        help="Run Chrome in headless mode (only applies to --mode browser).",
    )
    parser.add_argument(
        "--keep-open",
        action="store_true",
        help="Keep the Chrome window open after login until Enter is pressed.",
    )
    parser.add_argument(
        "--debug-port",
        nargs="?",
        const=DEFAULT_DEBUG_PORT,
        type=int,
        default=None,
        help=f"Launch/reuse Chrome with localhost DevTools debug port (default when flag omitted value: {DEFAULT_DEBUG_PORT}).",
    )
    parser.add_argument(
        "--attach",
        action="store_true",
        help="Attach to an already-running Chrome debug session instead of launching a new browser.",
    )
    parser.add_argument(
        "--detach",
        action="store_true",
        help="Leave Chrome running on the debug port after login.py exits so future commands can attach.",
    )
    parser.add_argument(
        "--universe",
        default=None,
        help="Optional universe name or ID to open after login.",
    )
    parser.add_argument(
        "--portal-only",
        action="store_true",
        help="Stay on the BitMeUp portal after login without entering SPACEINVASION_UNIVERSE.",
    )
    parser.add_argument(
        "--allow-join",
        action="store_true",
        help="Allow entering a universe marked [JOIN] (creates a new planet there).",
    )
    parser.add_argument(
        "--2fa",
        dest="two_factor_code",
        default=None,
        help="Optional 6-digit 2FA code (overrides SPACEINVASION_2FA_CODE).",
    )
    parser.add_argument(
        "--verify-session",
        action="store_true",
        help="Check whether the saved session cookie file is still authenticated.",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Print machine-readable JSON output (never includes passwords or cookies).",
    )
    return parser


def _prompt_2fa() -> str:
    if not sys.stdin.isatty():
        return ""
    return input("Enter 6-digit 2FA code: ").strip()


def _wait_for_manual_gate(message: str) -> None:
    if not sys.stdin.isatty():
        return
    print(f"\n[ACTION REQUIRED] {message}", file=sys.stderr)
    input("Press Enter here after completing it in the browser...")


def _safe_cli_str(
    val: object,
    default: str = "",
    *,
    max_len: int = 1024,
    _depth: int = 0,
    _seen: set[int] | None = None,
    _budget: list[int] | None = None,
) -> str:
    if val is None:
        return default
    if isinstance(val, os.PathLike):
        try:
            val = os.fspath(val)
        except Exception:
            return default
        if val is None:
            return default
        if isinstance(val, os.PathLike):
            return default
    if isinstance(val, (bytes, bytearray)):
        txt = bytes(val).decode("utf-8", errors="replace")
    elif isinstance(val, BaseException):
        str_fn = getattr(type(val), "__str__", BaseException.__str__)
        used_custom_str = False
        txt = ""
        if str_fn not in (BaseException.__str__, KeyError.__str__):
            try:
                txt = str(val)[:16_384]
                used_custom_str = True
            except Exception:
                txt = ""
        if not used_custom_str:
            args = getattr(val, "args", ())
            if not isinstance(args, (tuple, list)) or not args:
                txt = "" if str_fn is BaseException.__str__ else type(val).__name__
            else:
                seen = _seen if _seen is not None else set()
                budget = _budget if _budget is not None else [256]
                vid = id(val)
                if vid in seen or _depth >= 16 or budget[0] <= 0:
                    txt = f"<{type(val).__name__}>"
                else:
                    budget[0] -= 1
                    seen.add(vid)
                    try:
                        if len(args) == 1:
                            a0 = args[0]
                            if isinstance(a0, str):
                                txt = (repr(a0) if isinstance(val, KeyError) else a0)[:16_384]
                            else:
                                txt = _safe_cli_str(
                                    a0,
                                    f"<{type(a0).__name__}>",
                                    max_len=4096,
                                    _depth=_depth + 1,
                                    _seen=seen,
                                    _budget=budget,
                                )
                        else:
                            parts = []
                            for a in args[:8]:
                                if budget[0] <= 0:
                                    parts.append("...")
                                    break
                                if isinstance(a, str):
                                    parts.append(repr(a))
                                else:
                                    parts.append(
                                        _safe_cli_str(
                                            a,
                                            f"<{type(a).__name__}>",
                                            max_len=4096,
                                            _depth=_depth + 1,
                                            _seen=seen,
                                            _budget=budget,
                                        )
                                    )
                            if len(args) > 8:
                                parts.append(f"...+{len(args) - 8}")
                            txt = ("(" + ", ".join(parts) + ")")[:4096]
                    finally:
                        seen.discard(vid)
    elif isinstance(val, int) and not isinstance(val, bool):
        txt = game_data.fmt_compact_int(val) if abs(val).bit_length() >= 12_000 else str(val)
    else:
        try:
            txt = game_data.safe_key(val)
        except Exception:
            return default
    txt = str(redact_sensitive(txt))
    return txt[:max_len] if len(txt) > max_len else txt


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
        if -game_data.MAX_JSON_SAFE_INT <= key <= game_data.MAX_JSON_SAFE_INT:
            return key
        return _safe_cli_str(key, default="key", max_len=256)
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
                fsp = os.fspath(key)
            except Exception:
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
            if isinstance(key, frozenset):
                return "frozenset({...})"
            return "{...}"
        if _depth >= 16 or budget[0] <= 0:
            return "..."
        budget[0] -= 1
        seen.add(vid)
        try:
            if isinstance(key, tuple):
                parts: list[str] = []
                for idx, el in enumerate(key):
                    if idx >= 64 or budget[0] <= 0:
                        parts.append("...")
                        break
                    parts.append(str(_coerce_cli_json_key(el, _depth=_depth + 1, _seen=seen, _budget=budget)))
                inner = ", ".join(parts)
                return (f"({inner},)" if len(key) == 1 else f"({inner})")[:4096]
            if isinstance(key, frozenset):
                parts = []
                for idx, el in enumerate(key):
                    if idx >= 64 or budget[0] <= 0:
                        parts.append("...")
                        break
                    parts.append(str(_coerce_cli_json_key(el, _depth=_depth + 1, _seen=seen, _budget=budget)))
                inner = ", ".join(sorted(parts))
                return f"frozenset({{{inner}}})"[:4096]
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
        if -game_data.MAX_JSON_SAFE_INT <= obj <= game_data.MAX_JSON_SAFE_INT:
            return obj
        return _safe_cli_str(obj, default="0", max_len=256)
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
                    raw = os.fspath(obj)
                except Exception:
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
                out_l.append(
                    _coerce_cli_json_scalars(v, _depth=_depth + 1, _seen=seen, _budget=budget)
                )
            return out_l
        finally:
            seen.discard(vid)
    return _safe_cli_str(obj, default="<unprintable>", max_len=1024)


def _safe_cli_json(payload: object) -> str:
    pre_redacted = redact_sensitive(payload)
    coerced = _coerce_cli_json_scalars(pre_redacted)
    sanitized = game_data.sanitize_json_tree(
        coerced,
        max_int=game_data.MAX_JSON_SAFE_INT,
        nonfinite_float=None,
    )
    clean = redact_sensitive(sanitized)
    return json.dumps(clean, indent=2, allow_nan=False)


def _safe_finite_float(val: object) -> float | None:
    if val is None or isinstance(val, bool) or not isinstance(val, (int, float)):
        return None
    try:
        f = float(val)
    except Exception:
        return None
    return f if math.isfinite(f) else None


def _print_universes(universes: tuple[UniverseOption, ...]) -> None:
    if not universes:
        return
    print("Discovered universes:")
    for uni in universes[:15]:
        label = redact_sensitive(_safe_cli_str(uni.label))
        if uni.universe_id is not None:
            game = "SI2" if uni.is_si2 else "SI Classic"
            status = "PLAY" if uni.has_account else "JOIN"
            sp_f = _safe_finite_float(uni.speed)
            fl_f = _safe_finite_float(uni.flight_speed)
            speed = (
                f"{sp_f:g}x/{fl_f:g}x"
                if (sp_f is not None and fl_f is not None and sp_f > 0 and fl_f > 0)
                else "?"
            )
            uid_txt = (
                game_data.fmt_compact_int(uni.universe_id)
                if isinstance(uni.universe_id, int) and not isinstance(uni.universe_id, bool)
                else redact_sensitive(_safe_cli_str(uni.universe_id))
            )
            print(f"  - [{status}] {label} (id={uid_txt}, {game}, {speed})")
        else:
            print(f"  - {label}: {redact_sensitive(_safe_cli_str(uni.url))}")


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)

    try:
        if args.verify_session:
            authenticated, url, title = verify_saved_session(
                account=args.account,
                session_dir=args.session_dir,
            )
            payload = {
                "authenticated": authenticated,
                "account": args.account,
                "url": url,
                "title": title,
            }
            if args.json:
                print(_safe_cli_json(payload))
            else:
                state = "ACTIVE" if authenticated else "EXPIRED / LOGGED OUT"
                print(f"Session ({redact_sensitive(_safe_cli_str(args.account))}): {state}")
                print(f"URL:   {redact_sensitive(_safe_cli_str(url))}")
                print(f"Title: {redact_sensitive(_safe_cli_str(title))}")
            return 0 if authenticated else 1

        creds = load_credentials(env_path=args.env_file, account=args.account)
        if args.two_factor_code:
            creds = replace(creds, two_factor_code=args.two_factor_code)

        if args.mode == "http":
            result = login(
                creds=creds,
                session_dir=args.session_dir,
                two_factor_provider=_prompt_2fa,
            )
            if args.json:
                serializable = asdict(result)
                serializable["cookie_path"] = redact_sensitive(_safe_cli_str(result.cookie_path))
                print(_safe_cli_json(serializable))
            else:
                cookies_txt = ", ".join(
                    str(redact_sensitive(_safe_cli_str(name)))
                    for name in (result.cookie_names or ())
                )
                print(
                    f"Login successful for {redact_sensitive(_safe_cli_str(result.email))} "
                    f"(profile: {redact_sensitive(_safe_cli_str(result.account_name))})"
                )
                print(f"Final URL:  {redact_sensitive(_safe_cli_str(result.final_url))}")
                print(f"Page Title: {redact_sensitive(_safe_cli_str(result.page_title))}")
                print(
                    f"Cookies:    {cookies_txt} -> {redact_sensitive(_safe_cli_str(result.cookie_path))}"
                )
                _print_universes(result.universes)
            return 0

        debug_port = args.debug_port
        if (args.attach or args.detach) and debug_port is None:
            debug_port = DEFAULT_DEBUG_PORT

        profile_dir = args.profile_dir or (Path(".browser_profile") / creds.account_name)
        browser_cfg = BrowserConfig(
            headless=args.headless,
            profile_dir=profile_dir,
            keep_open=args.keep_open,
            debug_port=debug_port,
            attach=args.attach,
            detach=args.detach,
        )

        def _hold_if_requested(_driver, res) -> None:
            if args.keep_open and not args.headless and not args.detach:
                print(f"\nBrowser is open at {redact_sensitive(_safe_cli_str(res.final_url))}")
                if res.debug_address:
                    print(f"Attachable debug port: {redact_sensitive(_safe_cli_str(res.debug_address))}")
                input("Press Enter to close Chrome and exit...")

        target_universe = "" if args.portal_only else args.universe
        result = login_with_browser(
            creds=creds,
            config=browser_cfg,
            session_dir=args.session_dir,
            two_factor_provider=_prompt_2fa,
            wait_for_manual_gate=_wait_for_manual_gate if sys.stdin.isatty() else None,
            enter_universe=target_universe,
            allow_join=args.allow_join,
            on_session_ready=_hold_if_requested,
        )

        if args.json:
            serializable = asdict(result)
            serializable["cookie_path"] = redact_sensitive(_safe_cli_str(result.cookie_path))
            serializable["profile_dir"] = redact_sensitive(_safe_cli_str(result.profile_dir))
            print(_safe_cli_json(serializable))
        else:
            reused = " (reused existing browser session)" if result.already_logged_in else ""
            cookies_txt = ", ".join(
                str(redact_sensitive(_safe_cli_str(name)))
                for name in (result.cookie_names or ())
            )
            print(
                f"Browser login successful for {redact_sensitive(_safe_cli_str(result.email))} "
                f"(profile: {redact_sensitive(_safe_cli_str(result.account_name))}){reused}"
            )
            print(f"Final URL:   {redact_sensitive(_safe_cli_str(result.final_url))}")
            print(f"Page Title:  {redact_sensitive(_safe_cli_str(result.page_title))}")
            print(f"Chrome Dir:  {redact_sensitive(_safe_cli_str(result.profile_dir))}")
            if result.debug_address:
                print(f"Debug Port:  {redact_sensitive(_safe_cli_str(result.debug_address))}")
            print(
                f"Cookies:     {cookies_txt} -> {redact_sensitive(_safe_cli_str(result.cookie_path))}"
            )
            if result.entered_universe:
                print(f"Universe:    {redact_sensitive(_safe_cli_str(result.entered_universe))}")
            _print_universes(result.universes)
        return 0

    except SpaceInvasionAuthError as exc:
        err_txt = redact_sensitive(_safe_cli_str(exc))
        if args.json:
            print(_safe_cli_json({"ok": False, "error": err_txt}))
        else:
            print(f"Auth error: {err_txt}", file=sys.stderr)
        return 1
    except Exception as exc:  # pragma: no cover - defensive fallback
        err_txt = redact_sensitive(_safe_cli_str(exc))
        if args.json:
            print(_safe_cli_json({"ok": False, "error": err_txt}))
        else:
            print(f"Unexpected error during login: {err_txt}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
