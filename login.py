#!/usr/bin/env python3
"""CLI script to log in to SpaceInvasion Classic using `.env` credentials.

Defaults to driving a real Chrome browser via Selenium (`--mode browser`) with a
persistent Chrome profile so interactions look and behave like a normal player.
Use `--mode http` for lightweight headless HTTP authentication.
"""

from __future__ import annotations

import argparse
from dataclasses import asdict, replace
import json
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


def _print_universes(universes: tuple[UniverseOption, ...]) -> None:
    if not universes:
        return
    print("Discovered universes:")
    for uni in universes[:15]:
        if uni.universe_id is not None:
            game = "SI2" if uni.is_si2 else "SI Classic"
            status = "PLAY" if uni.has_account else "JOIN"
            speed = (
                f"{uni.speed:g}x/{uni.flight_speed:g}x"
                if uni.speed and uni.flight_speed
                else "?"
            )
            print(f"  - [{status}] {uni.label} (id={uni.universe_id}, {game}, {speed})")
        else:
            print(f"  - {uni.label}: {uni.url}")


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
                print(json.dumps(payload, indent=2))
            else:
                state = "ACTIVE" if authenticated else "EXPIRED / LOGGED OUT"
                print(f"Session ({args.account}): {state}")
                print(f"URL:   {url}")
                print(f"Title: {title}")
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
                serializable["cookie_path"] = str(result.cookie_path)
                print(json.dumps(serializable, indent=2))
            else:
                print(f"Login successful for {result.email} (profile: {result.account_name})")
                print(f"Final URL:  {result.final_url}")
                print(f"Page Title: {result.page_title}")
                print(f"Cookies:    {', '.join(result.cookie_names)} -> {result.cookie_path}")
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
                print(f"\nBrowser is open at {res.final_url}")
                if res.debug_address:
                    print(f"Attachable debug port: {res.debug_address}")
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
            serializable["cookie_path"] = str(result.cookie_path)
            serializable["profile_dir"] = str(result.profile_dir)
            print(json.dumps(serializable, indent=2))
        else:
            reused = " (reused existing browser session)" if result.already_logged_in else ""
            print(
                f"Browser login successful for {result.email} "
                f"(profile: {result.account_name}){reused}"
            )
            print(f"Final URL:   {result.final_url}")
            print(f"Page Title:  {result.page_title}")
            print(f"Chrome Dir:  {result.profile_dir}")
            if result.debug_address:
                print(f"Debug Port:  {result.debug_address}")
            print(f"Cookies:     {', '.join(result.cookie_names)} -> {result.cookie_path}")
            if result.entered_universe:
                print(f"Universe:    {result.entered_universe}")
            _print_universes(result.universes)
        return 0

    except SpaceInvasionAuthError as exc:
        print(f"Auth error: {exc}", file=sys.stderr)
        return 1
    except Exception as exc:  # pragma: no cover - defensive fallback
        print(f"Unexpected error during login: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
