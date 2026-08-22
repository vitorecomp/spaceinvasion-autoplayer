"""Selenium Chrome browser automation for SpaceInvasion Classic."""

from __future__ import annotations

from dataclasses import dataclass, field
import os
from pathlib import Path
import random
import shutil
import subprocess
import time
from typing import Callable, Optional
import urllib.request

from bs4 import BeautifulSoup

from spaceinvasion.auth import (
    BASE_URL,
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

DEFAULT_DEBUG_PORT = 9222

try:
    from selenium import webdriver
    from selenium.common.exceptions import NoSuchElementException, WebDriverException
    from selenium.webdriver.chrome.options import Options as ChromeOptions
    from selenium.webdriver.common.by import By
    from selenium.webdriver.common.keys import Keys

    _SELENIUM_AVAILABLE = True
except ImportError:  # pragma: no cover - guarded at runtime
    webdriver = None  # type: ignore[assignment]
    ChromeOptions = None  # type: ignore[assignment,misc]
    By = None  # type: ignore[assignment]
    Keys = None  # type: ignore[assignment]
    NoSuchElementException = Exception  # type: ignore[assignment,misc]
    WebDriverException = Exception  # type: ignore[assignment,misc]
    _SELENIUM_AVAILABLE = False


@dataclass(frozen=True)
class BrowserConfig:
    """Configuration for Selenium Chrome browser sessions."""

    headless: bool = False
    profile_dir: Path = Path(".browser_profile/default")
    window_size: tuple[int, int] = (1440, 900)
    timeout: float = 25.0
    chrome_binary: Optional[str] = None
    keep_open: bool = False
    typing_delay_range: tuple[float, float] = (0.03, 0.09)
    debug_port: Optional[int] = None
    debug_host: str = "127.0.0.1"
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
    if profile_dir.parent.name == ".browser_profile":
        os.chmod(profile_dir.parent, 0o700)


def is_debug_port_open(
    port: int,
    host: str = "127.0.0.1",
    timeout: float = 0.8,
) -> bool:
    """Return True if a Chrome DevTools debugging endpoint responds on `host:port`."""
    try:
        with urllib.request.urlopen(f"http://{host}:{port}/json/version", timeout=timeout) as resp:
            return resp.status == 200
    except Exception:
        return False


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

    deadline = time.monotonic() + max(config.timeout, 10.0)
    while time.monotonic() < deadline:
        if proc.poll() is not None:
            raise SpaceInvasionAuthError(
                f"Chrome exited early with code {proc.returncode} while opening debug port {port}."
            )
        if is_debug_port_open(port, config.debug_host):
            return proc
        time.sleep(0.2)

    proc.terminate()
    raise SpaceInvasionAuthError(
        f"Timed out waiting for Chrome debug port {config.debug_host}:{port}."
    )


def attach_chrome_driver(
    port: int = DEFAULT_DEBUG_PORT,
    timeout: float = 25.0,
    host: str = "127.0.0.1",
):
    """Attach Selenium WebDriver to a Chrome instance listening on `host:port`."""
    _require_selenium()
    options = ChromeOptions()
    options.debugger_address = f"{host}:{port}"
    try:
        driver = webdriver.Chrome(options=options)
    except WebDriverException as exc:
        raise SpaceInvasionAuthError(
            f"Failed to attach Selenium to Chrome at {host}:{port}: {exc}"
        ) from exc
    driver.set_page_load_timeout(max(timeout, 15.0))
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

    try:
        driver = webdriver.Chrome(options=options)
    except WebDriverException as exc:
        raise SpaceInvasionAuthError(f"Failed to start Chrome via Selenium: {exc}") from exc

    driver.set_page_load_timeout(max(config.timeout, 15.0))
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
        except Exception:
            proc.terminate()
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
        service = getattr(driver, "service", None)
        if service is not None:
            try:
                service.stop()
            except Exception:
                pass
        return

    try:
        driver.quit()
    except Exception:
        pass
    if launched_proc is not None and launched_proc.poll() is None:
        try:
            launched_proc.terminate()
            launched_proc.wait(timeout=5)
        except Exception:
            pass


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
    sess = build_http_session()
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
        if uni.universe_id is not None and str(uni.universe_id) == needle:
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
    deadline = time.monotonic() + config.timeout
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
    deadline = time.monotonic() + config.timeout
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
                deadline = time.monotonic() + config.timeout

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
        cur = (getattr(driver, "current_url", "") or "").lower()
        is_local_ui = "127.0.0.1" in cur or "localhost" in cur
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
    deadline = time.monotonic() + max(0.0, wait_seconds)
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
        if isinstance(js_data, dict) and js_data.get("instances"):
            parsed = universes_from_portal_data(
                str(js_data.get("sid") or ""),
                list(js_data.get("instances") or []),
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
    deadline = time.monotonic() + min(config.timeout, 15.0)
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
    session_dir: Path = Path(".sessions"),
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
        profile_dir=Path(".browser_profile") / creds.account_name
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
