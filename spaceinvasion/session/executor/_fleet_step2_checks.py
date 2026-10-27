"""Step 2 (`internalSendFleet`) response validation, mission-block parsing, and success result builder."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import replace
import time

from bs4 import BeautifulSoup

from spaceinvasion.core import catalog
from spaceinvasion.core.coords import normalize_coords
from spaceinvasion.core.model import PlannedAction
from spaceinvasion.core.redaction import redact_sensitive
from spaceinvasion.parsing.fleet_page import parse_send_fleet_mission_block
from spaceinvasion.parsing.models import GameState, QueueJob, Resources
from spaceinvasion.session.client._protocols import WebElementLike
from spaceinvasion.session.executor._fleet_errors import (
    extract_fleet_server_error,
    extract_mission_forbidden_reason,
    is_unattackable_target_html,
    log_fleet_diag,
)
from spaceinvasion.session.executor._fleet_radio import collect_available_mission_radio_values
from spaceinvasion.session.executor._types import ExecutionResult

_MISSION_LABEL_MAP: dict[str, str] = {
    "SPY": "Spy",
    "ESCAPE": "Escape",
    "RECYCLE": "Recycle",
    "COLONIZE": "Colonize",
    "TRANSPORT": "Transport",
    "STATION": "Station",
    "ATTACK": "Attack",
}

_MISSION_VERB_MAP: dict[str, str] = {
    "SPY": "spy probe",
    "ESCAPE": "escape flight",
    "RECYCLE": "recycler harvest",
    "COLONIZE": "colonization fleet",
    "TRANSPORT": "inter-planet transport",
    "STATION": "one-way fleet stationing",
}


def _mission_block_result(action: PlannedAction, mission_kind: str, blk: tuple[str, float, bool]) -> ExecutionResult:
    blk_reason, blk_cd, blk_bash = blk
    return ExecutionResult(
        executed=False,
        dry_run=False,
        action=action,
        message=f"Aborted: {blk_reason}.",
        target_cooldown_seconds=blk_cd,
        target_block_reason=blk_reason,
        target_is_bash_block=blk_bash,
        clear_target_debris=(mission_kind == "RECYCLE"),
        validated=False,
        validation_error=blk_reason,
    )


def check_step1_response_blocks(
    send_html: str,
    send_soup: BeautifulSoup,
    *,
    action: PlannedAction,
    mission_kind: str,
    tname_s: str,
    ships_to_send: Mapping[str, int],
) -> ExecutionResult | None:
    """Inspect Step 1 -> Step 2 response HTML for mission blocks or server rejections."""
    step1_block = parse_send_fleet_mission_block(send_html, mission_kind)
    has_any_step2_radio = send_soup.select_one('input[type="radio"]') is not None
    if step1_block is not None and (
        not has_any_step2_radio
        or "(forbidden)" in step1_block[0].lower()
        or "(verboten)" in step1_block[0].lower()
        or mission_kind == "RECYCLE"
    ):
        return _mission_block_result(action, mission_kind, step1_block)

    step1_err = extract_fleet_server_error(send_html, mission_kind)
    is_step1_prot_err = bool(step1_err and is_unattackable_target_html(step1_err))
    hostile_prot_blocks_step2 = bool(
        is_step1_prot_err
        and mission_kind in ("ATTACK", "SPY")
        and not (mission_kind == "SPY" and "bash" in str(step1_err or "").lower())
    )
    if step1_err and (not has_any_step2_radio or hostile_prot_blocks_step2):
        log_fleet_diag(
            "step1_rejected",
            {
                "mission_kind": mission_kind,
                "target": tname_s,
                "ships": dict(ships_to_send),
                "server_err": step1_err,
                "page_text": " ".join(send_soup.get_text(" ", strip=True).split())[:800],
            },
        )
        return ExecutionResult(
            executed=False,
            dry_run=False,
            action=action,
            message=f"Aborted: fleet step 1 rejected by server ({step1_err}).",
            target_cooldown_seconds=21600.0 if is_step1_prot_err else None,
            target_block_reason=step1_err if is_step1_prot_err else None,
            target_is_bash_block=("bash" in step1_err.lower()),
            validated=False,
            validation_error=step1_err,
        )
    return None


def build_missing_radio_result(
    send_html: str,
    send_soup: BeautifulSoup,
    radios: Sequence[WebElementLike],
    *,
    action: PlannedAction,
    mission_kind: str,
    tname_s: str,
    form_css: str,
) -> ExecutionResult:
    """Build the diagnostic & cooldown `ExecutionResult` when `mission_kind`'s radio is absent on Step 2."""
    mission_block = parse_send_fleet_mission_block(send_html, mission_kind)
    if mission_block is not None:
        return _mission_block_result(action, mission_kind, mission_block)
    has_step2_controls = bool(
        radios
        or send_soup.select_one(
            'form#fleet, form[action*="internalSendFleetFinish"], input[name="todo"], input[type="radio"]'
        )
        is not None
        or is_unattackable_target_html(send_html)
    )
    log_fleet_diag(
        "mission_radio_missing",
        {
            "mission_kind": mission_kind,
            "target": tname_s,
            "form_css": form_css,
            "radios_html": [str(redact_sensitive(str(r)))[:300] for r in send_soup.select('input[type="radio"]')],
            "forms_html": [str(redact_sensitive(str(f)))[:1200] for f in send_soup.find_all("form")],
            "page_text": " ".join(send_soup.get_text(" ", strip=True).split())[:1000],
        },
    )
    if mission_kind in ("ATTACK", "SPY") and has_step2_controls:
        inline_forb = extract_mission_forbidden_reason(send_html, mission_kind)
        avail_vals = collect_available_mission_radio_values(send_soup)
        avail_hint = f"available radios: {', '.join(avail_vals[:6])}" if avail_vals else "no mission radios rendered"
        uninhab_hint = (
            " — target slot appears uninhabited/despawned"
            if any(v.upper() in ("KOLO", "COLONIZE", "KOLONISIEREN") for v in avail_vals)
            or (avail_vals and all(v.upper() == "SAVE" for v in avail_vals))
            else ""
        )
        verb_unavail = "attacked" if mission_kind == "ATTACK" else "spied"
        blk_msg = inline_forb or (
            f"Target cannot be {verb_unavail} on internalSendFleet "
            f"({mission_kind} mission unavailable; {avail_hint}{uninhab_hint})"
        )
        return ExecutionResult(
            executed=False,
            dry_run=False,
            action=action,
            message=f"Aborted: {inline_forb}." if inline_forb else f"Aborted: {blk_msg}.",
            target_cooldown_seconds=6 * 3600.0,
            target_block_reason=blk_msg,
            target_is_bash_block=("bash" in blk_msg.lower()),
            validated=False,
            validation_error=blk_msg,
        )
    if mission_kind == "RECYCLE" and radios:
        avail_vals_r = collect_available_mission_radio_values(send_soup)
        if avail_vals_r:
            rec_blk_msg = (
                f"Recycle mission unavailable on internalSendFleet (available radios: {', '.join(avail_vals_r[:6])})"
            )
            return ExecutionResult(
                executed=False,
                dry_run=False,
                action=action,
                message=f"Aborted: {rec_blk_msg}.",
                target_cooldown_seconds=1800.0,
                target_block_reason=rec_blk_msg,
                clear_target_debris=True,
                validated=False,
                validation_error=rec_blk_msg,
            )
    return ExecutionResult(
        executed=False,
        dry_run=False,
        action=action,
        message=(
            f"Aborted: {mission_kind.title()} mission radio button not confirmed on internalSendFleet "
            f"(page bytes={len(send_html)})."
        ),
    )


def check_confirm_response_blocks(
    confirm_html: str,
    *,
    action: PlannedAction,
    mission_kind: str,
    tname_s: str,
    ships_to_send: Mapping[str, int],
) -> ExecutionResult | None:
    """Validate post-LAUNCH confirmation HTML for server rejections or stuck Step 2 forms."""
    confirm_block = parse_send_fleet_mission_block(confirm_html, mission_kind)
    if confirm_block is not None:
        return _mission_block_result(action, mission_kind, confirm_block)
    confirm_soup = BeautifulSoup(confirm_html or "", "html.parser")
    remained_on_step2 = (
        confirm_soup.select_one('form[action*="internalSendFleetFinish"] input[type="radio"][name="todo"]') is not None
    )
    server_err = extract_fleet_server_error(confirm_html, mission_kind)
    if server_err and not (
        remained_on_step2
        and (
            (mission_kind not in ("ATTACK", "SPY") and is_unattackable_target_html(server_err))
            or (mission_kind == "SPY" and "bash" in server_err.lower())
        )
    ):
        is_prot_err2 = is_unattackable_target_html(server_err)
        log_fleet_diag(
            "step2_rejected",
            {
                "mission_kind": mission_kind,
                "target": tname_s,
                "ships": dict(ships_to_send),
                "server_err": server_err,
            },
        )
        return ExecutionResult(
            executed=False,
            dry_run=False,
            action=action,
            message=f"Aborted: fleet launch rejected by server ({server_err}).",
            target_cooldown_seconds=21600.0 if is_prot_err2 else None,
            target_block_reason=server_err if is_prot_err2 else None,
            target_is_bash_block=("bash" in server_err.lower()),
            validated=False,
            validation_error=server_err,
        )
    if remained_on_step2:
        return ExecutionResult(
            executed=False,
            dry_run=False,
            action=action,
            message=f"Aborted: {mission_kind.title()} fleet remained on internalSendFleet after clicking LAUNCH.",
            validated=False,
            validation_error="Fleet remained on internalSendFleet after clicking LAUNCH.",
        )
    return None


def build_dispatch_success_result(
    *,
    action: PlannedAction,
    updated_state: GameState | None,
    ships_to_send: Mapping[str, int],
    mission_kind: str,
    t_gal: int,
    t_sys: int,
    t_pos: int,
    coords_s: str,
    cargo_resources: Resources | None,
    slot_status: tuple[int, int] | None,
) -> ExecutionResult:
    """Attach a synthetic outbound `QueueJob` and return the final fleet-dispatch `ExecutionResult`."""
    ship_desc = ", ".join(f"{catalog.fmt_compact_int(qty)}x {k}" for k, qty in ships_to_send.items())
    if updated_state is not None:
        norm_orig_c = normalize_coords(coords_s)
        norm_dest_c = f"{t_gal:02d}:{t_sys:03d}:{t_pos:02d}"
        m_lbl = _MISSION_LABEL_MAP.get(mission_kind, mission_kind.title())
        synth_job = QueueJob(
            category="fleet",
            name=f"{m_lbl} → [{norm_dest_c}]",
            remaining_text="in transit",
            remaining_seconds=1800.0,
            finish_ts=round(time.time() + 1800.0, 3),
            queue_slot=1,
            detail=f"Outbound · {ship_desc}"[:160],
            origin_coords=norm_orig_c,
            cargo=cargo_resources if mission_kind == "TRANSPORT" else None,
            expected_cargo=cargo_resources if mission_kind == "TRANSPORT" else None,
        )
        updated_state = replace(
            updated_state,
            active_queues=tuple(list(updated_state.active_queues or ()) + [synth_job]),
        )
    verb = _MISSION_VERB_MAP.get(mission_kind, "attack")
    obs_avail_ok = slot_status[0] if slot_status is not None else None
    obs_filled_ok = min(slot_status[0], slot_status[1] + 1) if slot_status is not None else None
    return ExecutionResult(
        executed=True,
        dry_run=False,
        action=action,
        message=f"Dispatched {verb} ({ship_desc}) to [{t_gal:02d}:{t_sys:03d}:{t_pos:02d}] from [{coords_s}].",
        updated_state=updated_state,
        clear_target_debris=(mission_kind == "RECYCLE"),
        observed_fleet_slots_available=obs_avail_ok,
        observed_fleet_slots_filled=obs_filled_ok,
    )
