"""Anti-bot, rate-limit, and game-shell session verification collaborator (`PageGuardChecker`)."""

from __future__ import annotations

from collections.abc import Callable
import logging

from selenium.common.exceptions import NoSuchElementException

from spaceinvasion.session.auth import AntiBotChallengeError, SpaceInvasionAuthError, detect_antibot_challenge
from spaceinvasion.session.client._guards_diag import (
    build_rate_limit_error,
    capture_dom_diag,
    dom_recovered_before_record,
    is_antibot_visible_in_live_dom,
    probe_game_shell,
    try_switch_popup_tab,
)
from spaceinvasion.session.client._protocols import (
    SELENIUM_NAV_ERRORS,
    call_with_optional_priority,
    client_find_and_switch_game_tab,
    client_monotonic,
    is_real_selenium_driver,
    safe_positive_timeout,
)
from spaceinvasion.session.client._state import ClientSessionState
from spaceinvasion.session.limiter import PageViewLimiter, RateLimitError, is_server_rate_limit_html

_LOGGER = logging.getLogger(__name__)


class PageGuardChecker:
    """Enforces anti-bot/CAPTCHA gates, rate-limit cooldowns, and active game-shell verification."""

    def __init__(self, state: ClientSessionState) -> None:
        self._state = state

    def capture_dom_diag(self, html: str = "") -> dict[str, object]:
        return capture_dom_diag(self._state, html)

    def is_antibot_visible_in_live_dom_impl(self) -> bool | None:
        return is_antibot_visible_in_live_dom(self._state)

    def is_antibot_visible_in_live_dom(self) -> bool | None:
        if self._state.hooks is not None:
            return self._state.hooks._is_antibot_visible_in_live_dom()
        return self.is_antibot_visible_in_live_dom_impl()

    def check_portal_redirect(self, html: str, *, context_msg: str) -> None:
        html_low = html.lower()
        if "bitmeup-login" in html_low or "sip-tab-universe" in html_low:
            raise SpaceInvasionAuthError(context_msg)

    def check_page_guards(self, html: str) -> None:
        if self._state.hooks is not None:
            self._state.hooks._check_page_guards(html)
            return
        self.check_page_guards_impl(html)

    def check_page_guards_impl(self, html: str) -> None:
        issue = detect_antibot_challenge(200, html)
        if issue and self.is_antibot_visible_in_live_dom() is False:
            issue = None
        if issue:
            err_ab = AntiBotChallengeError(f"{issue}. Stopping automation; handle verification in Chrome.")
            if self._state.detach_on_close and is_real_selenium_driver(self._state.driver):
                _LOGGER.warning("antibot_challenge error=%s diag=%s", err_ab, self.capture_dom_diag(html))
            raise err_ab
        if is_server_rate_limit_html(html):
            cooldown = self._state.page_limiter.record_rate_limit()
            err = build_rate_limit_error(
                self._state.page_limiter,
                f"SpaceInvasion per-minute page-view throttle hit; cooldown active "
                f"({cooldown:.1f}s remaining; retry after {cooldown:.1f}s).",
                retry_after=cooldown,
                server_throttle=True,
            )
            if self._state.detach_on_close and is_real_selenium_driver(self._state.driver):
                _LOGGER.warning("rate_limit_guard_triggered error=%s diag=%s", err, self.capture_dom_diag(html))
            raise err

    def _supports_coordinated_recovery(self) -> bool:
        limiter = self._state.page_limiter
        return isinstance(limiter, PageViewLimiter) and not type(limiter).__module__.startswith("unittest.mock")

    def _wait_pre_flight_recovery(self, deadline: float, coordinated: bool) -> int:
        if not coordinated:
            return 0
        limiter = self._state.page_limiter
        pre_status = limiter.wait_if_throttle_recovery_in_flight(wait_timeout=max(0.0, deadline - client_monotonic()))
        if pre_status == "cooldown":
            cd_now = max(1.0, limiter.rate_limit_remaining())
            raise build_rate_limit_error(
                limiter,
                f"SpaceInvasion rate-limit cooldown active ({cd_now:.1f}s remaining; retry after {cd_now:.1f}s).",
                retry_after=cd_now,
            )
        if pre_status == "timeout":
            raise RateLimitError(
                "Timed out waiting for concurrent F_planets throttle recovery; retry shortly.",
                retry_after=1.0,
                server_throttle=False,
            )
        return limiter.recovery_generation()

    def _check_top_shell_antibot(self, top_shell_html: str, missing_exc: NoSuchElementException | None) -> None:
        if not top_shell_html:
            return
        top_issue = detect_antibot_challenge(200, top_shell_html)
        if top_issue and missing_exc is None:
            with self._state.driver_lock:
                self._state.driver.switch_to.default_content()
                if self.is_antibot_visible_in_live_dom() is False:
                    top_issue = None
        if top_issue:
            err_top_ab = AntiBotChallengeError(f"{top_issue}. Stopping automation; handle verification in Chrome.")
            if self._state.detach_on_close and is_real_selenium_driver(self._state.driver):
                _LOGGER.warning("antibot_challenge error=%s diag=%s", err_top_ab, self.capture_dom_diag(top_shell_html))
            raise err_top_ab

    def _run_coordinated_leader_recovery(
        self,
        token: int,
        priority: str,
        refresh_planets_fn: Callable[..., object],
    ) -> tuple[bool, str]:
        recovered = False
        rearm = True
        try:
            call_with_optional_priority(refresh_planets_fn, refresh=True, priority=priority)
            with self._state.driver_lock:
                self._state.driver.switch_to.default_content()
            recovered = True
            rearm = False
            return True, ""
        except RateLimitError:
            rearm = True
            raise
        except (AntiBotChallengeError, SpaceInvasionAuthError):
            rearm = False
            raise
        except NoSuchElementException:
            rearm = False
            with self._state.driver_lock:
                try:
                    return False, str(self._state.driver.page_source or "")
                except SELENIUM_NAV_ERRORS:
                    return False, ""
        finally:
            self._state.page_limiter.finish_throttle_recovery(token, recovered=recovered, rearm=rearm)

    def _record_if_generation_or_continue(
        self,
        gen_before: int,
        dom_epoch_seen: int,
        missing_exc: NoSuchElementException,
    ) -> bool:
        """Return True to retry loop; otherwise raise `RateLimitError`."""
        if dom_recovered_before_record(self._state, dom_epoch_seen):
            return True
        recorded, cooldown = self._state.page_limiter.record_rate_limit_if_generation(gen_before)
        if not recorded and cooldown <= 0.0:
            return True
        cd_val = max(1.0, cooldown)
        raise build_rate_limit_error(
            self._state.page_limiter,
            f"SpaceInvasion per-minute page-view throttle hit; cooldown active "
            f"({cd_val:.1f}s remaining; retry after {cd_val:.1f}s).",
            retry_after=cd_val,
        ) from missing_exc

    def _handle_rate_limited_frame(
        self,
        *,
        in_planets_frame: bool,
        coordinated: bool,
        gen_before: int,
        dom_epoch_seen: int,
        deadline: float,
        priority: str,
        missing_exc: NoSuchElementException,
        refresh_planets_fn: Callable[..., object],
    ) -> tuple[str, str]:
        """Handle rate-limited `frame_html`; returns `(outcome, updated_frame_html)`."""
        limiter = self._state.page_limiter
        if coordinated:
            with self._state.driver_lock:
                cur_dom_epoch = self._state.dom_healthy_epoch
            if cur_dom_epoch != dom_epoch_seen:
                return "continue", ""
        cd_rem = limiter.rate_limit_remaining()
        if cd_rem > 0.0:
            raise build_rate_limit_error(
                limiter,
                f"SpaceInvasion rate-limit cooldown active ({cd_rem:.1f}s remaining; retry after {cd_rem:.1f}s).",
                retry_after=cd_rem,
            ) from missing_exc
        if in_planets_frame and coordinated:
            status, tok = limiter.begin_or_wait_throttle_recovery(wait_timeout=max(0.0, deadline - client_monotonic()))
            if status == "cooldown":
                cd_now = max(1.0, limiter.rate_limit_remaining())
                raise build_rate_limit_error(
                    limiter,
                    f"SpaceInvasion rate-limit cooldown active ({cd_now:.1f}s remaining; retry after {cd_now:.1f}s).",
                    retry_after=cd_now,
                ) from missing_exc
            if status == "waited":
                return "continue", ""
            if status == "timeout":
                raise RateLimitError(
                    "Timed out waiting for concurrent F_planets throttle recovery; retry shortly.",
                    retry_after=1.0,
                    server_throttle=False,
                ) from missing_exc
            if status == "leader":
                ok, updated_html = self._run_coordinated_leader_recovery(tok, priority, refresh_planets_fn)
                return ("return", "") if ok else ("fallthrough", updated_html)
            if status == "none" and (
                tok != gen_before or self._record_if_generation_or_continue(gen_before, dom_epoch_seen, missing_exc)
            ):
                return "continue", ""
        elif in_planets_frame and limiter.consume_throttle_recovery():
            try:
                call_with_optional_priority(refresh_planets_fn, refresh=True, priority=priority)
                with self._state.driver_lock:
                    self._state.driver.switch_to.default_content()
                return "return", ""
            except RateLimitError:
                limiter.rearm_throttle_recovery()
                raise
            except NoSuchElementException:
                with self._state.driver_lock:
                    try:
                        return "fallthrough", str(self._state.driver.page_source or "")
                    except SELENIUM_NAV_ERRORS:
                        return "fallthrough", ""
        elif coordinated and self._record_if_generation_or_continue(gen_before, dom_epoch_seen, missing_exc):
            return "continue", ""
        return "fallthrough", ""

    def _recover_or_raise_missing_shell(self, missing_exc: NoSuchElementException) -> None:
        top_html = ""
        cur_url = ""
        with self._state.driver_lock:
            try:
                self._state.driver.switch_to.default_content()
            except SELENIUM_NAV_ERRORS as exc:
                _LOGGER.debug("default_content failed during tab recovery: %s", exc)
            tab_recovered = client_find_and_switch_game_tab(self._state.driver)
            if tab_recovered:
                self._state.dom_healthy_epoch += 1
            else:
                try:
                    top_html = str(self._state.driver.page_source or "")
                except SELENIUM_NAV_ERRORS:
                    top_html = ""
                cur_url = str(self._state.driver.current_url or "")
        if tab_recovered:
            self._state.page_limiter.clear_throttle_recovery()
            return
        if top_html:
            self.check_page_guards(top_html)
        raise SpaceInvasionAuthError(
            f"Browser is not inside SpaceInvasion game shell (current URL: {cur_url}). "
            "Run `python3 login.py --attach` to enter the configured universe first."
        ) from missing_exc

    def ensure_in_game(
        self,
        *,
        priority: str = "normal",
        refresh_planets_fn: Callable[..., object],
    ) -> None:
        """Verify the attached browser has an active `indexGame.es` tab with game frames."""
        wait_budget = safe_positive_timeout(self._state.timeout, 20.0, min_val=0.05, max_val=300.0)
        deadline = client_monotonic() + wait_budget
        renderer_recoveries = 0

        for _attempt in range(64):
            coordinated = self._supports_coordinated_recovery()
            gen_before = self._wait_pre_flight_recovery(deadline, coordinated)
            (
                in_planets_frame,
                frame_html,
                top_shell_html,
                missing_exc,
                dom_epoch_seen,
                renderer_timed_out,
                renderer_recoveries,
            ) = probe_game_shell(self._state, renderer_recoveries)
            if renderer_timed_out:
                if client_monotonic() >= deadline:
                    deadline = client_monotonic() + min(5.0, wait_budget)
                continue

            self._check_top_shell_antibot(top_shell_html, missing_exc)
            if missing_exc is None:
                self._state.page_limiter.clear_throttle_recovery()
                return
            if not in_planets_frame and try_switch_popup_tab(self._state):
                self._state.page_limiter.clear_throttle_recovery()
                return

            if frame_html:
                issue = detect_antibot_challenge(200, frame_html)
                if issue:
                    raise AntiBotChallengeError(
                        f"{issue}. Stopping automation; handle verification in Chrome."
                    ) from missing_exc
                if is_server_rate_limit_html(frame_html):
                    outcome, updated_html = self._handle_rate_limited_frame(
                        in_planets_frame=in_planets_frame,
                        coordinated=coordinated,
                        gen_before=gen_before,
                        dom_epoch_seen=dom_epoch_seen,
                        deadline=deadline,
                        priority=priority,
                        missing_exc=missing_exc,
                        refresh_planets_fn=refresh_planets_fn,
                    )
                    if outcome == "return":
                        return
                    if outcome == "continue":
                        continue
                    if updated_html:
                        frame_html = updated_html
                if frame_html:
                    self.check_page_guards(frame_html)

            self._recover_or_raise_missing_shell(missing_exc)
            return

        raise RateLimitError(
            "Timed out waiting for concurrent F_planets throttle recovery; retry shortly.",
            retry_after=1.0,
            server_throttle=False,
        )
