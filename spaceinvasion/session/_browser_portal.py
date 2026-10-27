"""Browser login form submission, 2FA, and universe-entry orchestration helpers."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
import time
from typing import Any, Optional

from bs4 import BeautifulSoup

from spaceinvasion.session._browser_driver import (
    By,
    Keys,
    _finite_bounded_timeout,
)
from spaceinvasion.session._browser_tabs import (
    _element_if_visible,
    _is_portal_authenticated,
    _select_spaceinvasion_tab,
    _type_naturally,
    is_in_game_tab,
)
from spaceinvasion.session._browser_universe import (
    BrowserConfigLike,
    _click_portal_universe_dom,
    _extract_portal_universes,
    _handle_antibot_or_raise,
    _resolve_universe_choice,
    _wait_for_universe_entry,
)
from spaceinvasion.session.auth import (
    ConfigurationError,
    Credentials,
    LoginRejectedError,
    SpaceInvasionAuthError,
    TwoFactorRequiredError,
    UniverseOption,
)


def _submit_two_factor_if_requested(
    driver: Any,
    creds: Credentials,
    two_factor_provider: Optional[Callable[[], str]],
    config: BrowserConfigLike,
) -> bool:
    """Fill the 2FA modal input when visible; returns True if a code was submitted."""
    tfa_input = _element_if_visible(driver, By.ID, "2fa-bitmeup-code")
    if tfa_input is None:
        return False

    code = creds.two_factor_code
    if not code and two_factor_provider is not None:
        code = two_factor_provider().strip()
    if not code:
        raise TwoFactorRequiredError("Account requires a 2FA code. Pass --2fa <code> or set SPACEINVASION_2FA_CODE.")

    _type_naturally(tfa_input, code, config.typing_delay_range)
    tfa_input.send_keys(Keys.RETURN)
    return True


def _wait_for_landing_ready(
    driver: Any,
    config: BrowserConfigLike,
    wait_for_manual_gate: Optional[Callable[[str], None]],
) -> bool:
    """Wait for either authenticated portal or rendered `#bitmeup.login.email` input."""
    deadline = time.monotonic() + _finite_bounded_timeout(config.timeout, default=25.0, min_val=0.05, max_val=300.0)
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

    raise SpaceInvasionAuthError("Timed out waiting for SpaceInvasion login form or portal to render in Chrome.")


def _wait_for_login_outcome(
    driver: Any,
    creds: Credentials,
    config: BrowserConfigLike,
    two_factor_provider: Optional[Callable[[], str]],
    wait_for_manual_gate: Optional[Callable[[str], None]],
) -> None:
    """Wait for login completion, handling optional 2FA or server error messages."""
    tfa_submitted = False
    wait_secs = _finite_bounded_timeout(config.timeout, default=25.0, min_val=0.05, max_val=300.0)
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

        if not tfa_submitted and _submit_two_factor_if_requested(driver, creds, two_factor_provider, config):
            tfa_submitted = True
            deadline = time.monotonic() + wait_secs

        time.sleep(0.25)

    raise SpaceInvasionAuthError("Timed out waiting for BitMeUp login to complete in Chrome.")


def _submit_browser_login_form(
    driver: Any,
    creds: Credentials,
    cfg: BrowserConfigLike,
    two_factor_provider: Optional[Callable[[], str]],
    wait_for_manual_gate: Optional[Callable[[str], None]],
) -> None:
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

    _wait_for_login_outcome(driver, creds, cfg, two_factor_provider, wait_for_manual_gate)


def _resolve_and_enter_universe(
    driver: Any,
    creds: Credentials,
    cfg: BrowserConfigLike,
    session_dir: Path,
    universes: tuple[UniverseOption, ...],
    *,
    in_game_already: bool,
    enter_universe: Optional[str],
    allow_join: bool,
    require_in_game: bool,
    wait_for_manual_gate: Optional[Callable[[str], None]],
    sync_cookies_fn: Callable[[Any, Path, Credentials, str], tuple[Path, tuple[str, ...]]],
    cookie_path: Path,
    cookie_names: tuple[str, ...],
) -> tuple[Optional[str], Path, tuple[str, ...]]:
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
            return None, cookie_path, cookie_names
        matched = _resolve_universe_choice(universes, target_uni)
        if matched is None:
            raise ConfigurationError(f"Configured universe '{target_uni}' not found on portal.")
        if not matched.has_account and not allow_join:
            raise ConfigurationError(
                f"Universe '{matched.label}' has no active account yet ([JOIN]). "
                "Pass --allow-join if you intend to create a planet there, or --portal-only to stay on the portal."
            )
        handles_before = list(getattr(driver, "window_handles", None) or [])
        driver.get(matched.url)
        entered_ok = _wait_for_universe_entry(driver, cfg, wait_for_manual_gate, previous_handles=handles_before)
        if not entered_ok and _is_portal_authenticated(driver) and _click_portal_universe_dom(driver, matched):
            entered_ok = _wait_for_universe_entry(driver, cfg, wait_for_manual_gate, previous_handles=handles_before)
        if require_in_game and not entered_ok:
            raise SpaceInvasionAuthError(
                f"Could not enter universe '{matched.label}' (still at {driver.current_url}). "
                "Check Chrome for CAPTCHA, cookie prompt, or portal Play button."
            )
        cookie_path, cookie_names = sync_cookies_fn(driver, session_dir, creds, driver.current_url)
        return matched.label, cookie_path, cookie_names

    if require_in_game and not in_game_already and not is_in_game_tab(driver):
        raise SpaceInvasionAuthError(
            f"Browser is still outside SpaceInvasion game shell ({driver.current_url}). "
            "Set SPACEINVASION_UNIVERSE (e.g. RETRO) in .env or enter the universe in Chrome."
        )
    return None, cookie_path, cookie_names


def _execute_browser_login_flow(
    driver: Any,
    creds: Credentials,
    cfg: BrowserConfigLike,
    *,
    session_dir: Path,
    landing_url: str,
    was_already_open: bool,
    two_factor_provider: Optional[Callable[[], str]],
    wait_for_manual_gate: Optional[Callable[[str], None]],
    enter_universe: Optional[str],
    allow_join: bool,
    require_in_game: bool,
    sync_cookies_fn: Callable[[Any, Path, Credentials, str], tuple[Path, tuple[str, ...]]],
) -> tuple[bool, tuple[UniverseOption, ...], Optional[str], Path, tuple[str, ...], str]:
    if was_already_open:
        _select_spaceinvasion_tab(driver, allow_new_tab=True)

    current_url = (getattr(driver, "current_url", "") or "").lower()
    in_game_now = is_in_game_tab(driver)
    is_portal_now = (
        "indexgame.es" not in current_url and "indexinternal.es" not in current_url and _is_portal_authenticated(driver)
    )
    reused_live_page = (
        was_already_open and "spaceinvasion.bitmeup.com" in current_url and (in_game_now or is_portal_now)
    )
    if not reused_live_page:
        driver.get(landing_url)
        already_logged_in = _wait_for_landing_ready(driver, cfg, wait_for_manual_gate)
    else:
        already_logged_in = True

    if not already_logged_in:
        _submit_browser_login_form(driver, creds, cfg, two_factor_provider, wait_for_manual_gate)

    in_game_already = is_in_game_tab(driver)
    universes = () if in_game_already else _extract_portal_universes(driver)
    cookie_path, cookie_names = sync_cookies_fn(driver, session_dir, creds, driver.current_url)
    entered_universe, cookie_path, cookie_names = _resolve_and_enter_universe(
        driver,
        creds,
        cfg,
        session_dir,
        universes,
        in_game_already=in_game_already,
        enter_universe=enter_universe,
        allow_join=allow_join,
        require_in_game=require_in_game,
        wait_for_manual_gate=wait_for_manual_gate,
        sync_cookies_fn=sync_cookies_fn,
        cookie_path=cookie_path,
        cookie_names=cookie_names,
    )
    soup = BeautifulSoup(driver.page_source or "", "html.parser")
    page_title = soup.title.get_text(strip=True) if soup.title else (driver.title or "")
    return already_logged_in, universes, entered_universe, cookie_path, cookie_names, page_title


__all__ = [
    "BrowserConfigLike",
    "_click_portal_universe_dom",
    "_execute_browser_login_flow",
    "_extract_portal_universes",
    "_handle_antibot_or_raise",
    "_resolve_and_enter_universe",
    "_resolve_universe_choice",
    "_submit_browser_login_form",
    "_submit_two_factor_if_requested",
    "_wait_for_landing_ready",
    "_wait_for_login_outcome",
    "_wait_for_universe_entry",
]
