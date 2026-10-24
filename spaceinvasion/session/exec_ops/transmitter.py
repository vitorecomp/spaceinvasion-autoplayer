"""Daily Login Bonus and zero-Urplasma Interdimensional Transmitter execution collaborator."""

from __future__ import annotations

from collections.abc import Callable, Mapping
import contextlib

from spaceinvasion.core.coercion import coerce_int
from spaceinvasion.core.model import PlannedAction
from spaceinvasion.parsing.account_pages import parse_daily_login_bonus, parse_transmitter_page
from spaceinvasion.parsing.models import GameState
from spaceinvasion.session.browser_client._protocols import SELENIUM_NAV_ERRORS
from spaceinvasion.session.exec_ops._planet_state import (
    ActionInput,
    as_planned_action,
    extract_selected_planet_id,
    read_planets_overview_safe,
)
from spaceinvasion.session.exec_ops._protocols import BrowserClientLike, ExecutorContext
from spaceinvasion.session.exec_ops._transmitter_bonus import collect_daily_login_bonus_impl
from spaceinvasion.session.exec_ops._transmitter_spin import (
    check_transmitter_live_dom_guard,
    click_free_transmitter_submit,
    navigate_transmitter_on_homeworld,
    resolve_homeworld_target,
    switch_transmitter_to_free_src,
)
from spaceinvasion.session.exec_ops._types import ExecutionResult, call_with_interactive_priority, safe_str
from spaceinvasion.session.exec_ops.planet_select import PlanetSelector


def _tm_abort(action: PlannedAction, err: str) -> ExecutionResult:
    return ExecutionResult(
        executed=False,
        dry_run=False,
        action=action,
        message=f"Aborted: {err}",
        validated=False,
        validation_error=err,
    )


def _check_pre_spin_guards(
    info: Mapping[str, object],
    effective_action: PlannedAction,
    *,
    daily_bonus_seen: bool,
    daily_bonus_collected: bool,
) -> ExecutionResult | None:
    free_before = coerce_int(info.get("free_spins"), 0)
    if free_before <= 0:
        if daily_bonus_seen and not daily_bonus_collected:
            return _tm_abort(
                effective_action,
                "Daily login bonus popup on Command Central could not be collected "
                "before checking Interdimensional Transmitter.",
            )
        bonus_note = " (daily login bonus collected on Command Central)" if daily_bonus_collected else ""
        return ExecutionResult(
            executed=False,
            dry_run=False,
            action=effective_action,
            message=(
                f"Skipped: 0 free Interdimensional Transmitter charges available today{bonus_note} "
                "(Urplasma spin blocked)."
            ),
            validated=True,
        )
    if not info.get("can_activate_free") or info.get("has_urplasma_cost"):
        return _tm_abort(
            effective_action,
            "Interdimensional Transmitter spin is not free "
            f"(idt_src={info.get('idt_src')}, button={info.get('button_label')!r}); blocked to protect Urplasma.",
        )
    return None


class TransmitterOps:
    """Explicit collaborator owning Daily Login Bonus collection and free Interdimensional Transmitter spins."""

    def __init__(self, ctx: ExecutorContext, selector: PlanetSelector) -> None:
        self._ctx = ctx
        self._selector = selector
        self.last_transmitter_info: dict[str, object] | None = None

    def collect_daily_login_bonus_on_home(
        self,
        client: BrowserClientLike,
        hw_pid: int | None,
        daily_info: Mapping[str, object],
    ) -> tuple[bool, str]:
        """Collect the Command Central (`internalHome`) Daily Login Bonus popup targeting Homeworld `#tpid`."""
        return collect_daily_login_bonus_impl(self._ctx, client, hw_pid, daily_info)

    def _refresh_post_spin_state(
        self,
        client: BrowserClientLike,
        state: GameState,
        effective_action: PlannedAction,
        after_html: str,
    ) -> GameState | None:
        with contextlib.suppress(AttributeError, *SELENIUM_NAV_ERRORS):
            updated_planets = read_planets_overview_safe(client)
            return self._selector.build_updated_state(
                client,
                state,
                effective_action,
                updated_planets,
                page_html=after_html,
            )
        return None

    def _verify_and_finalize_spin(
        self,
        client: BrowserClientLike,
        state: GameState,
        eff_act: PlannedAction,
        info: Mapping[str, object],
        after_html: str,
        *,
        hw_coords: str,
        daily_collected: bool,
        meta_fn: Callable[[Mapping[str, object]], dict[str, object]],
    ) -> ExecutionResult:
        free_before = coerce_int(info.get("free_spins"), 0)
        after_info = parse_transmitter_page(after_html)
        free_after = coerce_int(after_info.get("free_spins"), 0)
        reward_txt = safe_str(after_info.get("latest_reward") or "").strip()
        prev_reward = safe_str(info.get("latest_reward") or "").strip()
        prev_date = safe_str(info.get("latest_date") or "").strip()
        after_date = safe_str(after_info.get("latest_date") or "").strip()
        spin_verified = (
            not after_html
            or free_after < free_before
            or (bool(reward_txt) and (reward_txt != prev_reward or after_date != prev_date))
        )
        if not spin_verified:
            self.last_transmitter_info = meta_fn({**after_info, "activated": False})
            return ExecutionResult(
                executed=False,
                dry_run=False,
                action=eff_act,
                message=(
                    f"Aborted: Interdimensional Transmitter free charges did not decrease "
                    f"(before={free_before}, after={free_after})."
                ),
                validated=False,
                validation_error=f"Free spin count unchanged ({free_before} -> {free_after}).",
            )
        self.last_transmitter_info = meta_fn({**after_info, "activated": True, "before_free_spins": free_before})
        self._ctx.executed_timestamps.append(self._ctx.clock())
        updated_state = self._refresh_post_spin_state(client, state, eff_act, after_html)
        bonus_prefix = "Collected daily login bonus on Command Central; " if daily_collected else ""
        hw_label = f" on Homeworld [{hw_coords}]" if hw_coords else ""
        reward_note = f" — Won: {reward_txt}" if reward_txt else ""
        return ExecutionResult(
            executed=True,
            dry_run=False,
            action=eff_act,
            message=(
                f"{bonus_prefix}Activated free daily Interdimensional Transmitter spin{hw_label} "
                f"(charges {free_before} -> {free_after}){reward_note}."
            ),
            updated_state=updated_state,
            validated=True,
        )

    def execute_activate_transmitter(
        self,
        client: BrowserClientLike,
        state: GameState,
        action: ActionInput,
    ) -> ExecutionResult:
        """Collect daily bonus on Homeworld and activate a free Interdimensional Transmitter charge."""
        act = as_planned_action(action)
        self.last_transmitter_info = None
        call_with_interactive_priority(client.ensure_in_game)

        hw_pid, hw_coords, multi_planet, eff_act = resolve_homeworld_target(state, act)
        home_raw = call_with_interactive_priority(client.navigate_menu, "internalHome")
        home_html = home_raw if isinstance(home_raw, str) else ""
        daily_info = parse_daily_login_bonus(home_html)
        daily_checked = bool(home_html)
        daily_seen = bool(daily_info.get("present"))
        daily_collected = self.collect_daily_login_bonus_on_home(client, hw_pid, daily_info)[0] if daily_seen else False

        def _meta(extra: Mapping[str, object]) -> dict[str, object]:
            return {
                **extra,
                "daily_bonus_checked": daily_checked,
                "daily_bonus_seen": daily_seen,
                "daily_bonus_collected": daily_collected,
                "homeworld_planet_id": hw_pid,
                "homeworld_coords": hw_coords,
            }

        html, nav_err = navigate_transmitter_on_homeworld(
            self._selector, client, state, hw_pid, hw_coords, multi_planet
        )
        if nav_err is not None:
            self.last_transmitter_info = _meta({"has_form": False, "free_spins": None, "activated": False})
            return _tm_abort(eff_act, nav_err)

        html, info = switch_transmitter_to_free_src(
            self._ctx,
            self._selector,
            client,
            html,
            parse_transmitter_page(html),
            hw_pid,
            multi_planet,
        )
        if multi_planet and hw_pid is not None and html:
            obs_tm_pid = extract_selected_planet_id(html, "internalTransmitter")
            if obs_tm_pid is not None and obs_tm_pid != hw_pid:
                self.last_transmitter_info = _meta({**info, "activated": False})
                return _tm_abort(
                    eff_act,
                    f"Active planet mismatch on internalTransmitter (observed={obs_tm_pid}, "
                    f"expected Homeworld={hw_pid} [{hw_coords}]); aborted.",
                )

        self.last_transmitter_info = _meta({**info, "activated": False})
        pre_res = _check_pre_spin_guards(
            info,
            eff_act,
            daily_bonus_seen=daily_seen,
            daily_bonus_collected=daily_collected,
        )
        if pre_res is not None:
            return pre_res

        dom_err = check_transmitter_live_dom_guard(client)
        if dom_err is not None:
            return _tm_abort(eff_act, dom_err)

        clicked, after_html = click_free_transmitter_submit(self._ctx, client)
        if not clicked:
            return ExecutionResult(
                executed=False,
                dry_run=False,
                action=eff_act,
                message="Aborted: could not find free #tm.submit control on internalTransmitter.",
                validated=False,
                validation_error="Missing #tm.submit button on internalTransmitter.",
            )
        return self._verify_and_finalize_spin(
            client,
            state,
            eff_act,
            info,
            after_html,
            hw_coords=hw_coords,
            daily_collected=daily_collected,
            meta_fn=_meta,
        )
