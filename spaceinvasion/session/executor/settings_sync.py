"""Safe owned-planet rename synchronization via `internalSettings` (never touches delete controls)."""

from __future__ import annotations

from collections.abc import Mapping

from bs4 import BeautifulSoup, Tag

from spaceinvasion.core import catalog
from spaceinvasion.parsing.models import GameState, PlanetState
from spaceinvasion.session.executor._dom import mark_frame_nav
from spaceinvasion.session.executor._planet_state import (
    navigate_settings_with_fallback,
    pace_client_action,
    read_planets_overview_safe,
    sleep_action_delay,
)
from spaceinvasion.session.executor._protocols import BrowserClientLike, ExecutorContext
from spaceinvasion.session.executor._types import safe_str
from spaceinvasion.session.executor.planet_select import PlanetSelector

_RENAME_SUBMIT_JS = """
const updates = arguments[0] || {};
let targetForm = null;
const applied = {};
for (const [fieldName, newVal] of Object.entries(updates)) {
  const inp = document.querySelector(`input[name="${CSS.escape ? CSS.escape(fieldName) : fieldName}"]`);
  if (!inp) continue;
  const tagType = String(inp.getAttribute('type') || 'text').toLowerCase();
  if (tagType !== 'text' && tagType !== '') continue;
  const maxLen = parseInt(inp.getAttribute('maxlength') || '0', 10);
  const finalVal = (maxLen > 0) ? String(newVal).slice(0, maxLen) : String(newVal);
  inp.value = finalVal;
  inp.setAttribute('value', finalVal);
  inp.dispatchEvent(new Event('input', { bubbles: true }));
  inp.dispatchEvent(new Event('change', { bubbles: true }));
  applied[fieldName] = finalVal;
  if (!targetForm && inp.form) {
    targetForm = inp.form;
  } else if (!targetForm) {
    targetForm = inp.closest('form');
  }
}
if (!targetForm) {
  return { ok: false, error: 'form_not_found', applied };
}
const submitBtn = targetForm.querySelector(
  'input[type="submit"]:not([onclick*="Delete"]):not([onclick*="delete"]), button[type="submit"]:not([onclick*="Delete"]):not([onclick*="delete"])'
);
if (submitBtn) {
  submitBtn.click();
  return { ok: true, method: 'submit_button', applied };
}
HTMLFormElement.prototype.submit.call(targetForm);
return { ok: true, method: 'form_submit', applied };
"""


def _sanitize_requested_names(
    state: GameState,
    target_names: Mapping[int, str],
) -> tuple[dict[int, PlanetState], dict[int, str]]:
    planets_by_id = {int(p.planet_id): p for p in state.planets if int(p.planet_id) > 0}
    requested: dict[int, str] = {}
    for raw_pid, raw_name in target_names.items():
        pid = catalog.safe_int(raw_pid, 0)
        if pid <= 0 or pid not in planets_by_id:
            continue
        cleaned = " ".join(
            "".join(" " if ord(ch) < 32 or ord(ch) == 127 else ch for ch in str(raw_name or "")).split()
        ).strip()
        if cleaned:
            requested[pid] = cleaned[:48]
    return planets_by_id, requested


def _partition_planet_renames(
    soup: BeautifulSoup,
    planets_by_id: Mapping[int, PlanetState],
    requested: Mapping[int, str],
) -> tuple[dict[int, str], dict[int, str], list[dict[str, object]]]:
    available_inputs: dict[int, str] = {}
    maxlengths: dict[int, int] = {}
    for pid in requested:
        inp = soup.find("input", attrs={"name": f"planet_{pid}"})
        if isinstance(inp, Tag):
            available_inputs[pid] = str(inp.get("value") or "").strip()
            ml = catalog.safe_int(inp.get("maxlength"), 0)
            if ml > 0:
                maxlengths[pid] = ml

    to_change: dict[int, str] = {}
    skipped: list[dict[str, object]] = []
    for pid, desired in requested.items():
        p_obj = planets_by_id[pid]
        coords_s = safe_str(p_obj.coords)
        if pid not in available_inputs:
            skipped.append(
                {
                    "planet_id": pid,
                    "coords": coords_s,
                    "requested_name": desired,
                    "reason": f"input planet_{pid} not found on internalSettings",
                }
            )
            continue
        eff_desired = desired[: maxlengths[pid]].strip() if pid in maxlengths else desired
        cur_dom_val = available_inputs[pid]
        if cur_dom_val == eff_desired and str(p_obj.name or "").strip() == eff_desired:
            skipped.append(
                {
                    "planet_id": pid,
                    "coords": coords_s,
                    "requested_name": eff_desired,
                    "game_name": cur_dom_val,
                    "reason": "already_synced",
                }
            )
            continue
        to_change[pid] = eff_desired
    return available_inputs, to_change, skipped


def _build_synced_rows(
    planets_by_id: Mapping[int, PlanetState],
    final_state: GameState,
    after_soup: BeautifulSoup,
    available_inputs: Mapping[int, str],
    to_change: Mapping[int, str],
) -> list[dict[str, object]]:
    final_by_id = {int(p.planet_id): p for p in final_state.planets if int(p.planet_id) > 0}
    synced_rows: list[dict[str, object]] = []
    for pid, eff_desired in to_change.items():
        old_p = planets_by_id[pid]
        new_p = final_by_id.get(pid, old_p)
        after_inp = after_soup.find("input", attrs={"name": f"planet_{pid}"})
        dom_after = str(after_inp.get("value") or "").strip() if isinstance(after_inp, Tag) else ""
        observed_game_name = str(new_p.name or "").strip() or dom_after or eff_desired
        synced_rows.append(
            {
                "planet_id": pid,
                "coords": safe_str(new_p.coords or old_p.coords),
                "old_game_name": available_inputs.get(pid) or str(old_p.name or ""),
                "requested_name": eff_desired,
                "game_name": observed_game_name,
                "verified": bool(observed_game_name == eff_desired or dom_after == eff_desired),
            }
        )
    return synced_rows


class SettingsSyncOps:
    """Explicit collaborator owning safe owned-planet rename sync on `internalSettings`."""

    def __init__(self, ctx: ExecutorContext, selector: PlanetSelector) -> None:
        self._ctx = ctx
        self._selector = selector

    def sync_planet_names_in_game(
        self,
        client: BrowserClientLike,
        state: GameState,
        target_names: Mapping[int, str],
    ) -> dict[str, object]:
        """Update owned planet names in SpaceInvasion Classic via `action=internalSettings`.

        SAFETY INVARIANT: Strictly edits `input[name="planet_<pid>"]` text inputs and submits
        their enclosing settings `<form>`. Never clicks or touches `confirmPlanetDeletion`,
        `deletePlanet`, or `#delete-confirm`.
        """
        planets_by_id, requested = _sanitize_requested_names(state, target_names)
        if not requested:
            return {
                "ok": True,
                "synced_count": 0,
                "synced": [],
                "skipped": [],
                "updated_state": state,
                "message": "No owned planet names to sync.",
            }

        settings_html = navigate_settings_with_fallback(client)
        soup = BeautifulSoup(settings_html, "html.parser")
        available_inputs, to_change, skipped = _partition_planet_renames(soup, planets_by_id, requested)
        if not to_change:
            updated_ov = read_planets_overview_safe(client)
            return {
                "ok": True,
                "synced_count": 0,
                "synced": [],
                "skipped": skipped,
                "updated_state": updated_ov if isinstance(updated_ov, GameState) else state,
                "message": (
                    "All custom planet names already match in-game names."
                    if any(s.get("reason") == "already_synced" for s in skipped)
                    else "No matching planet inputs found on internalSettings."
                ),
            }

        client._switch_frame("F_Hauptframe")
        pace_client_action(self._ctx, client, priority="interactive")
        nav_token = mark_frame_nav(client)
        sleep_action_delay(self._ctx)

        js_payload = {f"planet_{pid}": val for pid, val in to_change.items()}
        submit_result = client.driver.execute_script(_RENAME_SUBMIT_JS, js_payload)
        if isinstance(submit_result, Mapping) and not bool(submit_result.get("ok", True)):
            err_code = str(submit_result.get("error") or "submit_failed")
            return {
                "ok": False,
                "synced_count": 0,
                "synced": [],
                "skipped": skipped,
                "updated_state": state,
                "message": f"Failed to submit internalSettings rename form ({err_code}).",
            }

        after_html = client._wait_frame_ready("F_Hauptframe", expected_action="internalSettings", nav_token=nav_token)
        self._ctx.executed_timestamps.append(self._ctx.clock())
        after_soup = BeautifulSoup(after_html if isinstance(after_html, str) else "", "html.parser")
        updated_ov = read_planets_overview_safe(client)
        final_state = updated_ov if isinstance(updated_ov, GameState) else state
        synced_rows = _build_synced_rows(planets_by_id, final_state, after_soup, available_inputs, to_change)
        return {
            "ok": True,
            "synced_count": len(synced_rows),
            "synced": synced_rows,
            "skipped": skipped,
            "updated_state": final_state,
            "message": (
                f"Synced {len(synced_rows)} planet name{'s' if len(synced_rows) != 1 else ''} "
                "in-game via internalSettings."
            ),
        }
