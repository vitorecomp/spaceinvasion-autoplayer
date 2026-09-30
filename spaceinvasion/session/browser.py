"""Selenium Chrome browser automation for SpaceInvasion Classic."""

from __future__ import annotations

from dataclasses import dataclass, field
import ipaddress
import math
import os
from pathlib import Path
import random
import shutil
import socket
import subprocess
import time
from typing import Callable, Optional
import urllib.request

from bs4 import BeautifulSoup

from spaceinvasion.core import game_data
from spaceinvasion.session.auth import (
    BASE_URL,
    DEFAULT_SESSIONS_DIR,
    AntiBotChallengeError,
    ConfigurationError,
    Credentials,
    LoginRejectedError,
    SpaceInvasionAuthError,
    TwoFactorRequiredError,
    UniverseOption,
    build_http_session,
    detect_antibot_challenge,
    extract_universes,
    save_session_cookies,
    universes_from_portal_data,
)

DEFAULT_DEBUG_HOST = os.environ.get("SPACEINVASION_CHROME_HOST", "127.0.0.1").strip() or "127.0.0.1"
DEFAULT_DEBUG_PORT = int(os.environ.get("SPACEINVASION_CHROME_PORT", "9222"))
DEFAULT_PROFILE_ROOT = Path(os.environ.get("SPACEINVASION_PROFILE_DIR", "data/browser_profile"))

try:
    from selenium import webdriver
    from selenium.common.exceptions import NoSuchElementException, WebDriverException
    from selenium.webdriver.chrome.options import Options as ChromeOptions
    from selenium.webdriver.chrome.service import Service as ChromeService
    from selenium.webdriver.common.by import By
    from selenium.webdriver.common.keys import Keys

    _SELENIUM_AVAILABLE = True
except ImportError:  # pragma: no cover - guarded at runtime
    webdriver = None  # type: ignore[assignment]
    ChromeOptions = None  # type: ignore[assignment,misc]
    ChromeService = None  # type: ignore[assignment,misc]
    By = None  # type: ignore[assignment]
    Keys = None  # type: ignore[assignment]
    NoSuchElementException = Exception  # type: ignore[assignment,misc]
    WebDriverException = Exception  # type: ignore[assignment,misc]
    _SELENIUM_AVAILABLE = False


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
        raise ConfigurationError(
            "Selenium is not installed in this Python interpreter. "
            "Run with `.venv/bin/python3` or install `requirements.txt`."
        )


def find_chrome_binary(explicit: Optional[str] = None) -> Optional[str]:
    """Locate Google Chrome / Chromium binary path if present on the system."""
    if explicit:
        return explicit
    for candidate in ("google-chrome", "chromium", "chromium-browser"):
        resolved = shutil.which(candidate)
        if resolved:
            return resolved
    return None


def _prepare_profile_dir(profile_dir: Path) -> None:
    profile_dir.mkdir(parents=True, exist_ok=True)
    os.chmod(profile_dir, 0o700)
    if profile_dir.parent.name in (".browser_profile", "browser_profile"):
        os.chmod(profile_dir.parent, 0o700)


def _resolve_cdp_host(host: str) -> str:
    """Resolve non-IP container hostnames (e.g. `browser`) to IPv4 so Chrome CDP accepts the `Host:` header."""
    raw = str(host or DEFAULT_DEBUG_HOST).strip() or "127.0.0.1"
    if raw in ("127.0.0.1", "localhost", "::1", "0.0.0.0"):
        return raw
    try:
        ipaddress.ip_address(raw)
        return raw
    except ValueError:
        pass
    try:
        return socket.gethostbyname(raw)
    except OSError:
        return raw


def _build_chrome_service():
    if ChromeService is None:
        return None
    driver_bin = shutil.which("chromedriver")
    if driver_bin:
        return ChromeService(executable_path=driver_bin)
    return ChromeService()


def _finite_bounded_timeout(
    raw: object,
    *,
    default: float,
    min_val: float = 0.05,
    max_val: float = 300.0,
) -> float:
    if raw is None or isinstance(raw, (bool, dict, list, tuple, set, frozenset)):
        return default
    if isinstance(raw, (bytes, bytearray)):
        raw = bytes(raw).decode("utf-8", errors="replace")
    if isinstance(raw, str):
        s = raw.strip()
        if not s or not s.isascii():
            return default
        raw = s
    try:
        val = float(raw)  # type: ignore[arg-type]
    except Exception:
        return default
    if not math.isfinite(val):
        return default
    return max(min_val, min(max_val, val))


def is_debug_port_open(
    port: int,
    host: str = DEFAULT_DEBUG_HOST,
    timeout: float = 0.8,
) -> bool:
    """Return True if a Chrome DevTools debugging endpoint responds on `host:port`."""
    if isinstance(port, bool) or not isinstance(port, int) or port <= 0 or port > 65535:
        return False
    safe_timeout = _finite_bounded_timeout(timeout, default=0.8, min_val=0.05, max_val=60.0)
    resolved_host = _resolve_cdp_host(host)
    try:
        with urllib.request.urlopen(f"http://{resolved_host}:{port}/json/version", timeout=safe_timeout) as resp:
            return resp.status == 200
    except Exception:
        return False


def _terminate_or_kill_process(proc: Optional[subprocess.Popen]) -> None:
    """Terminate a child process and escalate to SIGKILL + wait so no orphan/ResourceWarning leaks."""
    if proc is None or proc.poll() is not None:
        return
    try:
        proc.terminate()
        proc.wait(timeout=5.0)
    except subprocess.TimeoutExpired:
        try:
            proc.kill()
        except OSError:
            pass
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


def launch_chrome_with_debug_port(config: BrowserConfig, port: int) -> subprocess.Popen:
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

    deadline = time.monotonic() + _finite_bounded_timeout(
        config.timeout, default=25.0, min_val=10.0, max_val=300.0
    )
    try:
        while time.monotonic() < deadline:
            if proc.poll() is not None:
                raise SpaceInvasionAuthError(
                    f"Chrome exited early with code {proc.returncode} while opening debug port {port}."
                )
            if is_debug_port_open(port, config.debug_host):
                return proc
            time.sleep(0.2)
    except BaseException:
        _terminate_or_kill_process(proc)
        raise

    _terminate_or_kill_process(proc)
    raise SpaceInvasionAuthError(
        f"Timed out waiting for Chrome debug port {config.debug_host}:{port}."
    )


def _safe_browser_err(exc: BaseException) -> str:
    from spaceinvasion.core.logging_utils import redact_sensitive

    res = redact_sensitive(exc)
    if isinstance(res, str):
        return res
    return f"<{type(exc).__name__}>"


def _configure_driver_timeouts(driver, timeout: float) -> None:
    """Apply finite page-load, script, and WebDriver HTTP command timeouts."""
    bounded = _finite_bounded_timeout(timeout, default=25.0, min_val=15.0, max_val=300.0)
    driver.set_page_load_timeout(bounded)
    set_script = getattr(driver, "set_script_timeout", None)
    if callable(set_script):
        try:
            set_script(bounded)
        except Exception:
            pass
    executor = getattr(driver, "command_executor", None)
    if executor is not None and "unittest.mock" not in type(executor).__module__:
        cmd_timeout = max(20.0, min(60.0, bounded + 10.0))
        client_cfg = getattr(executor, "_client_config", None)
        if client_cfg is not None and hasattr(client_cfg, "timeout"):
            try:
                client_cfg.timeout = cmd_timeout
                old_conn = getattr(executor, "_conn", None)
                get_mgr = getattr(executor, "_get_connection_manager", None)
                if callable(get_mgr):
                    if old_conn is not None and callable(getattr(old_conn, "clear", None)):
                        try:
                            old_conn.clear()
                        except Exception:
                            pass
                    executor._conn = get_mgr()
            except Exception:
                pass


def _close_driver_executor(driver) -> None:
    """Release urllib3 pools on Selenium's command executor so sockets do not linger in CLOSE-WAIT."""
    executor = getattr(driver, "command_executor", None)
    if executor is None or "unittest.mock" in type(executor).__module__:
        return
    close_fn = getattr(executor, "close", None)
    if callable(close_fn):
        try:
            close_fn()
        except Exception:
            pass


def _stop_service_process(service) -> None:
    """Stop a ChromeDriver service and force-reap its child process if it ignores shutdown."""
    if service is None:
        return
    proc = getattr(service, "process", None)
    stop_fn = getattr(service, "stop", None)
    if callable(stop_fn):
        try:
            stop_fn()
        except Exception:
            pass
    if isinstance(proc, subprocess.Popen):
        _terminate_or_kill_process(proc)


def attach_chrome_driver(
    port: int = DEFAULT_DEBUG_PORT,
    timeout: float = 25.0,
    host: str = DEFAULT_DEBUG_HOST,
):
    """Attach Selenium WebDriver to a Chrome instance listening on `host:port`."""
    _require_selenium()
    resolved_host = _resolve_cdp_host(host)
    options = ChromeOptions()
    options.debugger_address = f"{resolved_host}:{port}"
    service = _build_chrome_service()
    try:
        driver = (
            webdriver.Chrome(options=options, service=service)
            if service is not None
            else webdriver.Chrome(options=options)
        )
    except WebDriverException as exc:
        _stop_service_process(service)
        raise SpaceInvasionAuthError(
            f"Failed to attach Selenium to Chrome at {host}:{port}: {_safe_browser_err(exc)}"
        ) from exc
    except BaseException:
        _stop_service_process(service)
        raise
    try:
        _configure_driver_timeouts(driver, timeout)
    except BaseException:
        _close_driver_executor(driver)
        _stop_service_process(getattr(driver, "service", None) or service)
        raise
    return driver


def create_chrome_driver(config: BrowserConfig):
    """Create a persistent Chrome WebDriver configured for normal player browsing."""
    _require_selenium()
    _prepare_profile_dir(config.profile_dir)

    options = ChromeOptions()
    binary = find_chrome_binary(config.chrome_binary)
    if binary:
        options.binary_location = binary

    options.add_argument(f"--user-data-dir={os.path.abspath(config.profile_dir)}")
    options.add_argument(f"--window-size={config.window_size[0]},{config.window_size[1]}")
    options.add_argument("--no-first-run")
    options.add_argument("--no-default-browser-check")
    if config.headless:
        options.add_argument("--headless=new")

    service = _build_chrome_service()
    try:
        driver = (
            webdriver.Chrome(options=options, service=service)
            if service is not None
            else webdriver.Chrome(options=options)
        )
    except WebDriverException as exc:
        _stop_service_process(service)
        raise SpaceInvasionAuthError(f"Failed to start Chrome via Selenium: {_safe_browser_err(exc)}") from exc
    except BaseException:
        _stop_service_process(service)
        raise

    try:
        _configure_driver_timeouts(driver, config.timeout)
    except BaseException:
        _close_driver_executor(driver)
        _stop_service_process(getattr(driver, "service", None) or service)
        raise
    return driver


def open_browser_session(
    config: BrowserConfig,
) -> tuple[object, Optional[subprocess.Popen], bool]:
    """Open or attach a Chrome WebDriver according to `config`.

    Returns `(driver, launched_process, was_already_open)`.
    """
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
            return (
                attach_chrome_driver(config.debug_port, config.timeout, config.debug_host),
                None,
                True,
            )
        proc = launch_chrome_with_debug_port(config, config.debug_port)
        try:
            driver = attach_chrome_driver(config.debug_port, config.timeout, config.debug_host)
        except BaseException:
            _terminate_or_kill_process(proc)
            raise
        return driver, proc, False

    return create_chrome_driver(config), None, False


def close_or_detach_driver(
    driver,
    launched_proc: Optional[subprocess.Popen] = None,
    *,
    keep_running: bool,
) -> None:
    """Detach from Chrome (leaving it open) or shut down WebDriver and Chrome."""
    if keep_running:
        _close_driver_executor(driver)
        _stop_service_process(getattr(driver, "service", None))
        return

    service = getattr(driver, "service", None)
    service_proc = getattr(service, "process", None)
    try:
        driver.quit()
    except Exception:
        pass
    _close_driver_executor(driver)
    if isinstance(service_proc, subprocess.Popen):
        _terminate_or_kill_process(service_proc)
    _terminate_or_kill_process(launched_proc)


def _type_naturally(element, text: str, delay_range: tuple[float, float]) -> None:
    """Clear an input element and type characters with light human-like cadence."""
    element.click()
    element.clear()
    low, high = delay_range
    for ch in text:
        element.send_keys(ch)
        if high > 0:
            time.sleep(random.uniform(low, high))


def _element_if_visible(driver, by: str, selector: str):
    """Return matching element if present and displayed, else `None`."""
    try:
        el = driver.find_element(by, selector)
        return el if el.is_displayed() else None
    except NoSuchElementException:
        return None


def _is_portal_authenticated(driver) -> bool:
    """Detect whether the BitMeUp portal or in-game view is authenticated."""
    try:
        has_shell_state = bool(
            driver.execute_script(
                "return Boolean(window.SI_TPL && (window.SI_TPL.startPortal || window.SI_TPL.gameTop));"
            )
        )
    except Exception:
        has_shell_state = False

    if has_shell_state:
        return True

    if _element_if_visible(driver, By.ID, "sip-tab-universe") is not None:
        return True
    if _element_if_visible(driver, By.ID, "F_Hauptframe") is not None:
        return True

    try:
        current_url = (driver.current_url or "").lower()
    except Exception:
        current_url = ""
    if "spaceinvasion.bitmeup.com" in current_url and "sid=" in current_url:
        if _element_if_visible(driver, By.ID, "bitmeup-login") is None:
            return True

    return False


def _sync_browser_cookies(
    driver,
    session_dir: Path,
    creds: Credentials,
    last_url: str,
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


def _resolve_universe_choice(
    universes: tuple[UniverseOption, ...],
    target: str,
) -> Optional[UniverseOption]:
    """Match a configured universe name or numeric ID case-insensitively."""
    needle = target.strip().lower()
    for uni in universes:
        if uni.universe_id is not None and game_data.fmt_compact_int(uni.universe_id) == needle:
            return uni
        if uni.label.strip().lower() == needle:
            return uni
    for uni in universes:
        if needle in uni.label.strip().lower():
            return uni
    return None


def _handle_antibot_or_raise(
    driver,
    config: BrowserConfig,
    wait_for_manual_gate: Optional[Callable[[str], None]],
) -> None:
    """Stop automation if a CAPTCHA/anti-bot gate appears; let user act in visible UI."""
    html = driver.page_source or ""
    issue = detect_antibot_challenge(200, html)
    if not issue:
        return
    if not config.headless and wait_for_manual_gate is not None:
        wait_for_manual_gate(
            f"{issue}. Complete the verification directly in the Chrome window"
        )
        still_blocked = detect_antibot_challenge(200, driver.page_source or "")
        if not still_blocked:
            return
    raise AntiBotChallengeError(
        f"{issue}. Automation stopped; complete verification manually in a browser."
    )


def _submit_two_factor_if_requested(
    driver,
    creds: Credentials,
    two_factor_provider: Optional[Callable[[], str]],
    config: BrowserConfig,
) -> bool:
    """Fill the 2FA modal input when visible; returns True if a code was submitted."""
    tfa_input = _element_if_visible(driver, By.ID, "2fa-bitmeup-code")
    if tfa_input is None:
        return False

    code = creds.two_factor_code
    if not code and two_factor_provider is not None:
        code = two_factor_provider().strip()
    if not code:
        raise TwoFactorRequiredError(
            "Account requires a 2FA code. Pass --2fa <code> or set SPACEINVASION_2FA_CODE."
        )

    _type_naturally(tfa_input, code, config.typing_delay_range)
    tfa_input.send_keys(Keys.RETURN)
    return True


def _wait_for_landing_ready(
    driver,
    config: BrowserConfig,
    wait_for_manual_gate: Optional[Callable[[str], None]],
) -> bool:
    """Wait for either authenticated portal or rendered `#bitmeup.login.email` input.

    Returns `True` if already authenticated, `False` if login form is ready.
    """
    deadline = time.monotonic() + _finite_bounded_timeout(
        config.timeout, default=25.0, min_val=0.05, max_val=300.0
    )
    while time.monotonic() < deadline:
        _handle_antibot_or_raise(driver, config, wait_for_manual_gate)
        if _is_portal_authenticated(driver):
            return True
        if (
            _element_if_visible(driver, By.ID, "bitmeup.login.email") is not None
            and _element_if_visible(driver, By.ID, "bitmeup.login.password") is not None
        ):
            return False
        time.sleep(0.25)

    raise SpaceInvasionAuthError(
        "Timed out waiting for SpaceInvasion login form or portal to render in Chrome."
    )


def _wait_for_login_outcome(
    driver,
    creds: Credentials,
    config: BrowserConfig,
    two_factor_provider: Optional[Callable[[], str]],
    wait_for_manual_gate: Optional[Callable[[str], None]],
) -> None:
    """Wait for login completion, handling optional 2FA or server error messages."""
    tfa_submitted = False
    wait_secs = _finite_bounded_timeout(
        config.timeout, default=25.0, min_val=0.05, max_val=300.0
    )
    deadline = time.monotonic() + wait_secs
    while time.monotonic() < deadline:
        _handle_antibot_or_raise(driver, config, wait_for_manual_gate)
        if _is_portal_authenticated(driver):
            return

        err_el = _element_if_visible(driver, By.ID, "form.error.login")
        if err_el is not None:
            err_text = " ".join(err_el.text.split())
            if err_text:
                raise LoginRejectedError(err_text)

        if not tfa_submitted:
            if _submit_two_factor_if_requested(driver, creds, two_factor_provider, config):
                tfa_submitted = True
                deadline = time.monotonic() + wait_secs

        time.sleep(0.25)

    raise SpaceInvasionAuthError(
        "Timed out waiting for BitMeUp login to complete in Chrome."
    )


def is_in_game_tab(driver) -> bool:
    """Return True if the currently focused window holds a live SpaceInvasion game session."""
    try:
        driver.switch_to.default_content()
    except Exception:
        pass
    try:
        f_planets = driver.find_element(By.ID, "F_planets")
        driver.find_element(By.ID, "F_Hauptframe")
        driver.find_element(By.ID, "F_Navi")
        try:
            driver.switch_to.frame(f_planets)
            driver.find_element(By.ID, "json-container")
            return True
        except NoSuchElementException:
            return False
        except Exception:
            return False
        finally:
            try:
                driver.switch_to.default_content()
            except Exception:
                pass
    except NoSuchElementException:
        cur = (getattr(driver, "current_url", "") or "").lower()
        if "indexgame.es" in cur and _element_if_visible(driver, By.ID, "F_Hauptframe") is not None:
            return True
        return False
    except Exception:
        return False


def find_and_switch_game_tab(driver) -> bool:
    """Scan open browser tabs and switch WebDriver to an active SpaceInvasion game tab."""
    if is_in_game_tab(driver):
        return True
    handles = list(getattr(driver, "window_handles", None) or [])
    if len(handles) <= 1:
        return False

    ordered: list[str] = []
    other: list[str] = []
    current_handle = getattr(driver, "current_window_handle", None)
    for handle in handles:
        if handle == current_handle:
            continue
        try:
            driver.switch_to.window(handle)
            url = (getattr(driver, "current_url", "") or "").lower()
        except Exception:
            continue
        if "indexgame.es" in url:
            ordered.append(handle)
        elif "spaceinvasion.bitmeup.com" in url:
            other.append(handle)

    for handle in ordered + other:
        try:
            driver.switch_to.window(handle)
            if is_in_game_tab(driver):
                return True
        except Exception:
            continue

    if current_handle in handles:
        try:
            driver.switch_to.window(current_handle)
        except Exception:
            pass
    return False


def _select_spaceinvasion_tab(driver, *, allow_new_tab: bool = True) -> None:
    """Focus any existing SpaceInvasion tab so Control UI (`127.0.0.1`) tabs stay untouched."""
    if find_and_switch_game_tab(driver):
        return

    handles = list(getattr(driver, "window_handles", None) or [])
    current_handle = getattr(driver, "current_window_handle", None)
    portal_candidates: list[str] = []
    login_candidates: list[str] = []

    for handle in handles:
        try:
            driver.switch_to.window(handle)
            url = (getattr(driver, "current_url", "") or "").lower()
        except Exception:
            continue
        if "spaceinvasion.bitmeup.com" in url:
            if _is_portal_authenticated(driver):
                portal_candidates.append(handle)
            else:
                login_candidates.append(handle)

    chosen = (portal_candidates or login_candidates or [None])[0]
    if chosen is not None:
        driver.switch_to.window(chosen)
        return

    if current_handle in handles:
        try:
            driver.switch_to.window(current_handle)
        except Exception:
            pass

    if allow_new_tab:
        try:
            cur = (getattr(driver, "current_url", "") or "").lower()
        except Exception:
            cur = ""
        is_local_ui = "127.0.0.1" in cur or "localhost" in cur or cur.startswith("chrome://")
        if is_local_ui:
            switch_to = getattr(driver, "switch_to", None)
            new_win = getattr(switch_to, "new_window", None)
            if callable(new_win):
                try:
                    new_win("tab")
                except Exception:
                    pass


def _extract_portal_universes(driver, wait_seconds: float = 2.5) -> tuple[UniverseOption, ...]:
    """Read portal universes from live `window.SI_TPL.startPortal` JS or HTML."""
    deadline = time.monotonic() + _finite_bounded_timeout(
        wait_seconds, default=2.5, min_val=0.0, max_val=60.0
    )
    while True:
        try:
            js_data = driver.execute_script(
                "var p = window.SI_TPL && window.SI_TPL.startPortal;"
                "if (!p || !Array.isArray(p.instances) || p.instances.length === 0) return null;"
                "return {sid: String(p.SID || ''), instances: p.instances};"
            )
        except Exception:
            js_data = None

        cur_url = getattr(driver, "current_url", "") or BASE_URL
        raw_instances = js_data.get("instances") if isinstance(js_data, dict) else None
        if isinstance(raw_instances, (list, tuple)) and raw_instances:
            parsed = universes_from_portal_data(
                game_data.safe_key(js_data.get("sid") or ""),
                list(raw_instances),
                cur_url,
            )
            if parsed:
                return parsed

        parsed_html = extract_universes(getattr(driver, "page_source", "") or "", cur_url)
        if parsed_html:
            return parsed_html

        if time.monotonic() >= deadline or not _is_portal_authenticated(driver):
            return ()
        time.sleep(0.2)


def _click_portal_universe_dom(driver, matched: UniverseOption) -> bool:
    """Try clicking the universe card or Play button directly in the portal DOM."""
    label_needle = matched.label.strip().lower()
    uid = matched.universe_id
    selectors: list[str] = []
    if uid is not None:
        selectors.extend(
            [
                f'a[href*="instance={uid}"]',
                f'[data-instance="{uid}"] a',
                f'[data-instance="{uid}"] button',
                f'[data-instance="{uid}"]',
                f'[onclick*="instance={uid}"]',
                f'[onclick*="({uid})"]',
                f'[onclick*="({uid},"]',
            ]
        )
    for sel in selectors:
        try:
            el = driver.find_element(By.CSS_SELECTOR, sel)
            if el is not None:
                try:
                    el.click()
                except Exception:
                    driver.execute_script("arguments[0].click();", el)
                return True
        except Exception:
            continue

    try:
        clicked = bool(
            driver.execute_script(
                """
                var needle = arguments[0];
                var uidStr = arguments[1] !== null ? String(arguments[1]) : null;
                var nodes = Array.from(document.querySelectorAll('a, button, [role="button"], .sip-uni, [class*="instance"], [class*="universe"], tr, li, div'));
                for (var i = 0; i < nodes.length; i++) {
                  var el = nodes[i];
                  var txt = (el.innerText || el.textContent || '').trim().toLowerCase();
                  if (!txt || txt.indexOf(needle) === -1) continue;
                  var rect = el.getBoundingClientRect ? el.getBoundingClientRect() : null;
                  if (rect && (rect.width === 0 || rect.height === 0)) continue;
                  var target = el.querySelector && (
                    el.querySelector('a[href*="externalLogin"], a[href*="instance="], button, a, .play, [class*="play"]')
                  );
                  if (target) {
                    target.click();
                    return true;
                  }
                  if (el.tagName === 'A' || el.tagName === 'BUTTON' || el.getAttribute('onclick') || (uidStr && (el.innerHTML || '').indexOf(uidStr) !== -1)) {
                    el.click();
                    return true;
                  }
                }
                return false;
                """,
                label_needle,
                uid,
            )
        )
        return clicked
    except Exception:
        return False


def _wait_for_universe_entry(
    driver,
    config: BrowserConfig,
    wait_for_manual_gate: Optional[Callable[[str], None]],
    *,
    previous_handles: Optional[list[str]] = None,
) -> bool:
    """Wait until universe navigation settles on `indexGame.es` (across open tabs)."""
    deadline = time.monotonic() + _finite_bounded_timeout(
        config.timeout, default=15.0, min_val=0.05, max_val=15.0
    )
    prev_set = set(previous_handles or [])
    while time.monotonic() < deadline:
        _handle_antibot_or_raise(driver, config, wait_for_manual_gate)
        if find_and_switch_game_tab(driver):
            return True

        handles = list(getattr(driver, "window_handles", None) or [])
        for handle in handles:
            if handle in prev_set:
                continue
            try:
                driver.switch_to.window(handle)
                cur_new = (getattr(driver, "current_url", "") or "").lower()
                if "indexgame.es" in cur_new or "uni" in cur_new:
                    if find_and_switch_game_tab(driver) or "action=externallogin" not in cur_new:
                        return True
            except Exception:
                continue

        cur = (getattr(driver, "current_url", "") or "").lower()
        if "indexgame.es" in cur or "uni" in cur:
            if _element_if_visible(driver, By.ID, "F_Hauptframe") is not None:
                return True
            if "action=externallogin" not in cur:
                return True
        time.sleep(0.2)
    return False


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
    """Log in to SpaceInvasion using a real Chrome browser controlled by Selenium.

    Supports persistent Chrome profiles, localhost debug-port attach/detach,
    2FA prompts, CAPTCHA/anti-bot manual handoff, and cookie syncing.
    """
    cfg = config or BrowserConfig(
        profile_dir=DEFAULT_PROFILE_ROOT / creds.account_name
    )
    normalized_base = base_url.rstrip("/")
    landing_url = f"{normalized_base}/?locale={creds.locale}"

    if driver_factory is not None:
        driver = driver_factory(cfg)
        launched_proc = None
        was_already_open = False
    else:
        driver, launched_proc, was_already_open = open_browser_session(cfg)

    effective_port = cfg.debug_port or (DEFAULT_DEBUG_PORT if cfg.attach else None)
    debug_address = f"{cfg.debug_host}:{effective_port}" if effective_port else None
    keep_running = cfg.detach or (was_already_open and not cfg.keep_open)

    try:
        if was_already_open:
            _select_spaceinvasion_tab(driver, allow_new_tab=True)

        current_url = (getattr(driver, "current_url", "") or "").lower()
        in_game_now = is_in_game_tab(driver)
        is_portal_now = (
            "indexgame.es" not in current_url
            and "indexinternal.es" not in current_url
            and _is_portal_authenticated(driver)
        )
        reused_live_page = (
            was_already_open
            and "spaceinvasion.bitmeup.com" in current_url
            and (in_game_now or is_portal_now)
        )
        if not reused_live_page:
            driver.get(landing_url)
            already_logged_in = _wait_for_landing_ready(driver, cfg, wait_for_manual_gate)
        else:
            already_logged_in = True

        if not already_logged_in:
            email_el = driver.find_element(By.ID, "bitmeup.login.email")
            pass_el = driver.find_element(By.ID, "bitmeup.login.password")
            _type_naturally(email_el, creds.email, cfg.typing_delay_range)
            _type_naturally(pass_el, creds.password, cfg.typing_delay_range)

            stay_el = _element_if_visible(driver, By.ID, "slideLoggedIn")
            if stay_el is not None and stay_el.is_selected() != creds.stay_logged_in:
                stay_el.click()

            submit_btn = _element_if_visible(driver, By.CSS_SELECTOR, ".loginbutton")
            if submit_btn is not None:
                submit_btn.click()
            else:
                pass_el.send_keys(Keys.RETURN)

            _wait_for_login_outcome(
                driver, creds, cfg, two_factor_provider, wait_for_manual_gate
            )

        in_game_already = is_in_game_tab(driver)
        universes = () if in_game_already else _extract_portal_universes(driver)
        cookie_path, cookie_names = _sync_browser_cookies(
            driver, session_dir, creds, driver.current_url
        )
        entered_universe: Optional[str] = None

        target_uni = enter_universe if enter_universe is not None else creds.universe
        if not target_uni and require_in_game and not in_game_already and universes:
            active_unis = [u for u in universes if u.has_account]
            if len(active_unis) == 1:
                target_uni = active_unis[0].label
            elif len(active_unis) > 1:
                labels = ", ".join(u.label for u in active_unis[:6])
                raise ConfigurationError(
                    f"Multiple universes have active accounts ({labels}); set SPACEINVASION_UNIVERSE in .env."
                )

        if target_uni and not (in_game_already and enter_universe is None):
            if not universes:
                if require_in_game:
                    raise SpaceInvasionAuthError(
                        f"Could not locate universe selection entries on portal ({driver.current_url})."
                    )
            else:
                matched = _resolve_universe_choice(universes, target_uni)
                if matched is None:
                    raise ConfigurationError(
                        f"Configured universe '{target_uni}' not found on portal."
                    )
                if not matched.has_account and not allow_join:
                    raise ConfigurationError(
                        f"Universe '{matched.label}' has no active account yet ([JOIN]). "
                        "Pass --allow-join if you intend to create a planet there, "
                        "or --portal-only to stay on the portal."
                    )
                handles_before = list(getattr(driver, "window_handles", None) or [])
                driver.get(matched.url)
                entered_ok = _wait_for_universe_entry(
                    driver, cfg, wait_for_manual_gate, previous_handles=handles_before
                )
                if not entered_ok and _is_portal_authenticated(driver):
                    if _click_portal_universe_dom(driver, matched):
                        entered_ok = _wait_for_universe_entry(
                            driver, cfg, wait_for_manual_gate, previous_handles=handles_before
                        )
                if require_in_game and not entered_ok:
                    raise SpaceInvasionAuthError(
                        f"Could not enter universe '{matched.label}' (still at {driver.current_url}). "
                        "Check Chrome for CAPTCHA, cookie prompt, or portal Play button."
                    )
                entered_universe = matched.label
                cookie_path, cookie_names = _sync_browser_cookies(
                    driver, session_dir, creds, driver.current_url
                )
        elif require_in_game and not in_game_already and not is_in_game_tab(driver):
            raise SpaceInvasionAuthError(
                f"Browser is still outside SpaceInvasion game shell ({driver.current_url}). "
                "Set SPACEINVASION_UNIVERSE (e.g. RETRO) in .env or enter the universe in Chrome."
            )

        soup = BeautifulSoup(driver.page_source or "", "html.parser")
        page_title = soup.title.get_text(strip=True) if soup.title else (driver.title or "")

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
