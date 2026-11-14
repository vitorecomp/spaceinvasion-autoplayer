"""Selenium Chrome browser automation for SpaceInvasion Classic."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
import os
from pathlib import Path
import shutil
import subprocess
import time
from typing import Any, Optional
import urllib.error
import urllib.request

from spaceinvasion.core.env import get_env
from spaceinvasion.session._browser_driver import (
    _SELENIUM_AVAILABLE,
    By,
    ChromeOptions,
    ChromeService,
    Keys,
    NoSuchElementException,
    WebDriverException,
    _close_driver_executor,
    _configure_driver_timeouts,
    _finite_bounded_timeout,
    _prepare_profile_dir,
    _resolve_cdp_host,
    _safe_browser_err,
    _stop_service_process,
    _terminate_or_kill_process,
    close_or_detach_driver,
    webdriver,
)
from spaceinvasion.session._browser_portal import (
    _click_portal_universe_dom,
    _execute_browser_login_flow,
    _extract_portal_universes,
    _handle_antibot_or_raise,
    _resolve_universe_choice,
    _submit_two_factor_if_requested,
    _wait_for_landing_ready,
    _wait_for_login_outcome,
    _wait_for_universe_entry,
)
from spaceinvasion.session._browser_tabs import (
    _element_if_visible,
    _is_portal_authenticated,
    _select_spaceinvasion_tab,
    _type_naturally,
    find_and_switch_game_tab,
    is_in_game_tab,
)
from spaceinvasion.session.auth import (
    BASE_URL,
    DEFAULT_SESSIONS_DIR,
    ConfigurationError,
    Credentials,
    SpaceInvasionAuthError,
    UniverseOption,
    build_http_session,
    save_session_cookies,
)

DEFAULT_DEBUG_HOST = get_env("SPACEINVASION_CHROME_HOST", "127.0.0.1").strip() or "127.0.0.1"
DEFAULT_DEBUG_PORT = int(get_env("SPACEINVASION_CHROME_PORT", "9222"))
DEFAULT_PROFILE_ROOT = Path(get_env("SPACEINVASION_PROFILE_DIR", "data/browser_profile"))


@dataclass(frozen=True)
class BrowserConfig:
    """Configuration for Selenium Chrome browser sessions."""

    headless: bool = False
    profile_dir: Path = DEFAULT_PROFILE_ROOT / "default"
    window_size: tuple[int, int] = (1440, 900)
    timeout: float = 25.0
    chrome_binary: Optional[str] = None
    keep_open: bool = False
    typing_delay_range: tuple[float, float] = (0.03, 0.09)
    debug_port: Optional[int] = None
    debug_host: str = DEFAULT_DEBUG_HOST
    attach: bool = False
    detach: bool = False


@dataclass(frozen=True)
class BrowserLoginResult:
    """Summary of a Selenium browser login (free of secrets)."""

    account_name: str
    email: str
    final_url: str
    page_title: str
    profile_dir: Path
    cookie_path: Path
    cookie_names: tuple[str, ...]
    already_logged_in: bool = False
    universes: tuple[UniverseOption, ...] = field(default_factory=tuple)
    entered_universe: Optional[str] = None
    debug_address: Optional[str] = None


def _require_selenium() -> None:
    if not _SELENIUM_AVAILABLE:
        raise ConfigurationError("Selenium is not installed. Run with `.venv/bin/python3` or install requirements.")


def find_chrome_binary(explicit: Optional[str] = None) -> Optional[str]:
    """Locate Google Chrome / Chromium binary path if present on the system."""
    if explicit:
        return explicit
    for candidate in ("google-chrome", "chromium", "chromium-browser"):
        if resolved := shutil.which(candidate):
            return resolved
    return None


def _build_chrome_service() -> Any:
    if ChromeService is None:
        return None
    driver_bin = shutil.which("chromedriver")
    return ChromeService(executable_path=driver_bin) if driver_bin else ChromeService()


def is_debug_port_open(port: int, host: str = DEFAULT_DEBUG_HOST, timeout: float = 0.8) -> bool:
    """Return True if a Chrome DevTools debugging endpoint responds on `host:port`."""
    if isinstance(port, bool) or not isinstance(port, int) or port <= 0 or port > 65535:
        return False
    safe_timeout = _finite_bounded_timeout(timeout, default=0.8, min_val=0.05, max_val=60.0)
    resolved_host = _resolve_cdp_host(host, DEFAULT_DEBUG_HOST)
    try:
        with urllib.request.urlopen(f"http://{resolved_host}:{port}/json/version", timeout=safe_timeout) as resp:
            return bool(resp.status == 200)
    except (urllib.error.URLError, OSError, ValueError, TimeoutError, RuntimeError):
        return False


def launch_chrome_with_debug_port(config: BrowserConfig, port: int) -> subprocess.Popen[bytes]:
    """Launch Google Chrome with `--remote-debugging-port` bound to localhost."""
    binary = find_chrome_binary(config.chrome_binary)
    if not binary:
        raise ConfigurationError("Could not find google-chrome or chromium binary on PATH.")

    _prepare_profile_dir(config.profile_dir)
    cmd = [
        binary,
        f"--remote-debugging-port={port}",
        f"--remote-debugging-address={config.debug_host}",
        f"--user-data-dir={os.path.abspath(config.profile_dir)}",
        f"--window-size={config.window_size[0]},{config.window_size[1]}",
        "--no-first-run",
        "--no-default-browser-check",
    ]
    if config.headless:
        cmd.append("--headless=new")
    cmd.append("about:blank")

    proc = subprocess.Popen(
        cmd,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=config.detach,
    )
    deadline = time.monotonic() + _finite_bounded_timeout(config.timeout, default=25.0, min_val=10.0, max_val=300.0)
    ready = False
    try:
        while time.monotonic() < deadline:
            if proc.poll() is not None:
                raise SpaceInvasionAuthError(
                    f"Chrome exited early with code {proc.returncode} while opening debug port {port}."
                )
            if is_debug_port_open(port, config.debug_host):
                ready = True
                return proc
            time.sleep(0.2)
    finally:
        if not ready:
            _terminate_or_kill_process(proc)
    raise SpaceInvasionAuthError(f"Timed out waiting for Chrome debug port {config.debug_host}:{port}.")


def _start_and_configure_driver(options: Any, timeout: float, err_prefix: str) -> Any:
    service = _build_chrome_service()
    driver: Any = None
    driver_ready = False
    try:
        try:
            driver = (
                webdriver.Chrome(options=options, service=service)
                if service is not None
                else webdriver.Chrome(options=options)
            )
        except WebDriverException as exc:
            raise SpaceInvasionAuthError(f"{err_prefix}: {_safe_browser_err(exc)}") from exc
        _configure_driver_timeouts(driver, timeout)
        driver_ready = True
        return driver
    finally:
        if not driver_ready:
            if driver is not None:
                _close_driver_executor(driver)
                _stop_service_process(getattr(driver, "service", None) or service)
            else:
                _stop_service_process(service)


def attach_chrome_driver(port: int = DEFAULT_DEBUG_PORT, timeout: float = 25.0, host: str = DEFAULT_DEBUG_HOST) -> Any:
    """Attach Selenium WebDriver to a Chrome instance listening on `host:port`."""
    _require_selenium()
    resolved_host = _resolve_cdp_host(host, DEFAULT_DEBUG_HOST)
    options = ChromeOptions()
    options.debugger_address = f"{resolved_host}:{port}"
    return _start_and_configure_driver(options, timeout, f"Failed to attach Selenium to Chrome at {host}:{port}")


def create_chrome_driver(config: BrowserConfig) -> Any:
    """Create a persistent Chrome WebDriver configured for normal player browsing."""
    _require_selenium()
    _prepare_profile_dir(config.profile_dir)
    options = ChromeOptions()
    if binary := find_chrome_binary(config.chrome_binary):
        options.binary_location = binary
    options.add_argument(f"--user-data-dir={os.path.abspath(config.profile_dir)}")
    options.add_argument(f"--window-size={config.window_size[0]},{config.window_size[1]}")
    options.add_argument("--no-first-run")
    options.add_argument("--no-default-browser-check")
    if config.headless:
        options.add_argument("--headless=new")
    return _start_and_configure_driver(options, config.timeout, "Failed to start Chrome via Selenium")


def open_browser_session(config: BrowserConfig) -> tuple[object, Optional[subprocess.Popen[bytes]], bool]:
    """Open or attach a Chrome WebDriver according to `config`."""
    if config.attach:
        port = config.debug_port or DEFAULT_DEBUG_PORT
        if not is_debug_port_open(port, config.debug_host):
            raise SpaceInvasionAuthError(
                f"No Chrome debug session is listening on {config.debug_host}:{port}. "
                f"Start one first with `--debug-port {port} --detach` (or `--keep-open`)."
            )
        return attach_chrome_driver(port, config.timeout, config.debug_host), None, True

    if config.debug_port is not None:
        if is_debug_port_open(config.debug_port, config.debug_host):
            return attach_chrome_driver(config.debug_port, config.timeout, config.debug_host), None, True
        proc = launch_chrome_with_debug_port(config, config.debug_port)
        attached = False
        try:
            driver = attach_chrome_driver(config.debug_port, config.timeout, config.debug_host)
            attached = True
            return driver, proc, False
        finally:
            if not attached:
                _terminate_or_kill_process(proc)
    return create_chrome_driver(config), None, False


def _sync_browser_cookies(
    driver: Any, session_dir: Path, creds: Credentials, last_url: str
) -> tuple[Path, tuple[str, ...]]:
    """Persist Selenium browser cookies into `.sessions/<account>.json` (`0o600`)."""
    with build_http_session() as sess:
        for cookie in driver.get_cookies():
            sess.cookies.set(
                name=cookie["name"],
                value=cookie["value"],
                domain=cookie.get("domain") or "",
                path=cookie.get("path") or "/",
                secure=bool(cookie.get("secure", False)),
                expires=cookie.get("expiry"),
            )
        cookie_path = session_dir / f"{creds.account_name}.json"
        save_session_cookies(
            sess,
            cookie_path,
            metadata={
                "account": creds.account_name,
                "email": creds.email,
                "last_url": last_url,
                "locale": creds.locale,
                "source": "selenium",
            },
        )
        return cookie_path, tuple(sorted({c.name for c in sess.cookies}))


def login_with_browser(
    creds: Credentials,
    config: Optional[BrowserConfig] = None,
    session_dir: Path = DEFAULT_SESSIONS_DIR,
    base_url: str = BASE_URL,
    two_factor_provider: Optional[Callable[[], str]] = None,
    wait_for_manual_gate: Optional[Callable[[str], None]] = None,
    enter_universe: Optional[str] = None,
    allow_join: bool = False,
    require_in_game: bool = False,
    driver_factory: Optional[Callable[[BrowserConfig], object]] = None,
    on_session_ready: Optional[Callable[[object, BrowserLoginResult], None]] = None,
) -> BrowserLoginResult:
    """Log in to SpaceInvasion using a real Chrome browser controlled by Selenium."""
    cfg = config or BrowserConfig(profile_dir=DEFAULT_PROFILE_ROOT / creds.account_name)
    landing_url = f"{base_url.rstrip('/')}/?locale={creds.locale}"
    driver: Any
    launched_proc: Optional[subprocess.Popen[bytes]]
    if driver_factory is not None:
        driver, launched_proc, was_already_open = driver_factory(cfg), None, False
    else:
        driver, launched_proc, was_already_open = open_browser_session(cfg)

    effective_port = cfg.debug_port or (DEFAULT_DEBUG_PORT if cfg.attach else None)
    debug_address = f"{cfg.debug_host}:{effective_port}" if effective_port else None
    keep_running = cfg.detach or (was_already_open and not cfg.keep_open)
    try:
        already_logged_in, universes, entered_universe, cookie_path, cookie_names, page_title = (
            _execute_browser_login_flow(
                driver,
                creds,
                cfg,
                session_dir=session_dir,
                landing_url=landing_url,
                was_already_open=was_already_open,
                two_factor_provider=two_factor_provider,
                wait_for_manual_gate=wait_for_manual_gate,
                enter_universe=enter_universe,
                allow_join=allow_join,
                require_in_game=require_in_game,
                sync_cookies_fn=_sync_browser_cookies,
            )
        )
        result = BrowserLoginResult(
            account_name=creds.account_name,
            email=creds.email,
            final_url=driver.current_url,
            page_title=page_title,
            profile_dir=cfg.profile_dir,
            cookie_path=cookie_path,
            cookie_names=cookie_names,
            already_logged_in=already_logged_in,
            universes=universes,
            entered_universe=entered_universe,
            debug_address=debug_address,
        )
        if on_session_ready is not None:
            on_session_ready(driver, result)
        return result
    finally:
        close_or_detach_driver(driver, launched_proc, keep_running=keep_running)


__all__ = [
    "BrowserConfig",
    "BrowserLoginResult",
    "By",
    "ChromeOptions",
    "ChromeService",
    "DEFAULT_DEBUG_HOST",
    "DEFAULT_DEBUG_PORT",
    "DEFAULT_PROFILE_ROOT",
    "Keys",
    "NoSuchElementException",
    "WebDriverException",
    "_click_portal_universe_dom",
    "_close_driver_executor",
    "_configure_driver_timeouts",
    "_element_if_visible",
    "_extract_portal_universes",
    "_finite_bounded_timeout",
    "_handle_antibot_or_raise",
    "_is_portal_authenticated",
    "_prepare_profile_dir",
    "_resolve_cdp_host",
    "_resolve_universe_choice",
    "_safe_browser_err",
    "_select_spaceinvasion_tab",
    "_stop_service_process",
    "_submit_two_factor_if_requested",
    "_sync_browser_cookies",
    "_terminate_or_kill_process",
    "_type_naturally",
    "_wait_for_landing_ready",
    "_wait_for_login_outcome",
    "_wait_for_universe_entry",
    "attach_chrome_driver",
    "close_or_detach_driver",
    "create_chrome_driver",
    "find_and_switch_game_tab",
    "find_chrome_binary",
    "is_debug_port_open",
    "is_in_game_tab",
    "launch_chrome_with_debug_port",
    "login_with_browser",
    "open_browser_session",
    "webdriver",
]
