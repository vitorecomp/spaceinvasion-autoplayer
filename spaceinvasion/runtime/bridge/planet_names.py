"""Custom planet display-name persistence and overlay collaborator."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import asdict, replace
import re

from spaceinvasion.core.coords import normalize_coords
from spaceinvasion.core.model import GameState, PlanetState, QueueJob, is_in_flight_fleet_job
from spaceinvasion.runtime.bridge._context import BridgeContext
from spaceinvasion.runtime.bridge._db_gateway import RuntimeDatabaseGateway
from spaceinvasion.runtime.bridge._helpers import safe_api_int
from spaceinvasion.runtime.bridge._helpers_queue import extract_coords_from_text


def _patch_co_planet_row(
    row: object,
    raw_by_pid: Mapping[int, str],
    pid_by_coords: Mapping[str, int],
    custom_names: Mapping[str, str],
) -> object:
    if not isinstance(row, Mapping):
        return row
    out = dict(row)
    pid = safe_api_int(out.get("planet_id"), 0)
    if pid <= 0:
        nc = normalize_coords(str(out.get("coords") or ""))
        if nc and nc in pid_by_coords:
            pid = pid_by_coords[nc]
    raw_game = str((raw_by_pid.get(pid) or "") or out.get("game_name") or out.get("name") or "")
    custom = custom_names.get(str(pid)) if pid > 0 else None
    out["game_name"] = raw_game
    out["custom_name"] = custom if custom else None
    if custom:
        out["name"] = custom
    elif pid > 0 and raw_by_pid.get(pid):
        out["name"] = raw_by_pid[pid]
    return out


class PlanetNameService:
    """Manages custom planet display names while preserving underlying game names."""

    def __init__(self, ctx: BridgeContext) -> None:
        self._ctx = ctx

    def get_custom_planet_names_locked(self) -> dict[str, str]:
        """Return a shallow copy of custom planet display names keyed by string planet ID."""
        return dict(self._ctx.custom_planet_names)

    def patch_last_dashboard_custom_names_locked(self) -> None:
        """Update cached dashboard custom planet names in place."""
        prev = self._ctx.last_dashboard_payload
        if isinstance(prev, Mapping):
            patched = dict(prev)
            patched["custom_planet_names"] = dict(self._ctx.custom_planet_names)
            self._ctx.last_dashboard_payload = patched

    def set_custom_planet_name_locked(self, planet_id: int, raw_name: object) -> dict[str, str]:
        """Set or clear a single custom planet name, persisting to DB when present."""
        pid = safe_api_int(planet_id, 0)
        clean_name = RuntimeDatabaseGateway.sanitize_custom_planet_name(raw_name)
        db = self._ctx.db
        if db is not None:
            updated = db.set_custom_planet_name(pid, clean_name)
            self._ctx.custom_planet_names = dict(updated)
        elif pid > 0:
            if clean_name:
                self._ctx.custom_planet_names[str(pid)] = clean_name
            else:
                self._ctx.custom_planet_names.pop(str(pid), None)
        self._ctx.invalidate_dashboard_cache()
        self.patch_last_dashboard_custom_names_locked()
        return dict(self._ctx.custom_planet_names)

    def set_custom_planet_names_batch_locked(self, updates: Mapping[int, object]) -> dict[str, str]:
        """Set or clear multiple custom planet names atomically."""
        db = self._ctx.db
        if db is not None:
            payload: dict[object, object] = {k: v for k, v in updates.items()}
            updated = db.set_custom_planet_names(payload)
            self._ctx.custom_planet_names = dict(updated)
        else:
            for raw_pid, raw_name in updates.items():
                pid = safe_api_int(raw_pid, 0)
                if pid <= 0:
                    continue
                clean_name = RuntimeDatabaseGateway.sanitize_custom_planet_name(raw_name)
                if clean_name:
                    self._ctx.custom_planet_names[str(pid)] = clean_name
                else:
                    self._ctx.custom_planet_names.pop(str(pid), None)
        self._ctx.invalidate_dashboard_cache()
        self.patch_last_dashboard_custom_names_locked()
        return dict(self._ctx.custom_planet_names)

    def apply_custom_planet_names_to_state(
        self,
        state: GameState | None,
        custom_names: Mapping[str, str] | None = None,
    ) -> GameState | None:
        """Overlay custom names onto a `GameState`'s planets and in-flight queue labels."""
        if state is None:
            return None
        names_map = custom_names if custom_names is not None else self._ctx.custom_planet_names
        if not names_map:
            return state
        changed = False
        new_planets: list[PlanetState] = []
        owned_custom_by_coords: dict[str, tuple[str, str]] = {}
        for planet in state.planets:
            custom = names_map.get(str(planet.planet_id))
            if custom:
                nc = normalize_coords(planet.coords)
                if nc:
                    owned_custom_by_coords[nc] = (str(planet.name or ""), custom)
            if custom and custom != planet.name:
                changed = True
                new_planets.append(replace(planet, name=custom))
            else:
                new_planets.append(planet)

        new_queues: tuple[QueueJob, ...] | None = None
        if owned_custom_by_coords and state.active_queues:
            patched_q, q_changed = _patch_queue_custom_names(state.active_queues, owned_custom_by_coords)
            if q_changed:
                new_queues = patched_q
                changed = True

        if not changed:
            return state
        if new_queues is not None:
            return replace(state, planets=tuple(new_planets), active_queues=new_queues)
        return replace(state, planets=tuple(new_planets))

    def serialize_state_with_custom_names(
        self,
        raw_state: GameState,
        display_state: GameState | None = None,
        custom_names: Mapping[str, str] | None = None,
    ) -> dict[str, object]:
        """Serialize `GameState` preserving `game_name` alongside `custom_name` and display `name`."""
        names_map = custom_names if custom_names is not None else self._ctx.custom_planet_names
        eff_state = (
            display_state
            if display_state is not None
            else (self.apply_custom_planet_names_to_state(raw_state, names_map) or raw_state)
        )
        raw_by_pid = {int(p.planet_id): str(p.name or "") for p in (raw_state.planets or ())}
        out: dict[str, object] = asdict(eff_state)
        planets_list = out.get("planets")
        if isinstance(planets_list, (list, tuple)):
            planets_out: list[object] = []
            for p_dict in planets_list:
                if not isinstance(p_dict, dict):
                    planets_out.append(p_dict)
                    continue
                pid = safe_api_int(p_dict.get("planet_id"), 0)
                raw_name = raw_by_pid.get(pid, str(p_dict.get("name") or ""))
                custom = names_map.get(str(pid)) if pid > 0 else None
                p_dict["game_name"] = raw_name
                p_dict["custom_name"] = custom if custom else None
                if custom:
                    p_dict["name"] = custom
                planets_out.append(p_dict)
            out["planets"] = planets_out
        return out

    def overlay_custom_names_on_config_optimizer(
        self,
        active_co: dict[str, object] | None,
        snap_state: GameState | None,
        custom_names: Mapping[str, str],
    ) -> dict[str, object] | None:
        """Overlay custom planet names onto a Config Optimizer result dict."""
        if not isinstance(active_co, Mapping):
            return active_co
        out = dict(active_co)
        res = out.get("result")
        if not isinstance(res, Mapping):
            return out
        raw_by_pid, pid_by_coords = _build_planet_name_maps(snap_state)
        res_copy = dict(res)
        patched_planets: list[object] | None = None
        raw_planets = res_copy.get("planets")
        if isinstance(raw_planets, Sequence) and not isinstance(raw_planets, (str, bytes)):
            patched_planets = [_patch_co_planet_row(r, raw_by_pid, pid_by_coords, custom_names) for r in raw_planets]
            res_copy["planets"] = patched_planets
        raw_inputs = res_copy.get("inputs")
        if isinstance(raw_inputs, Mapping):
            inp_copy = dict(raw_inputs)
            inp_planets = inp_copy.get("planets")
            if isinstance(inp_planets, Sequence) and not isinstance(inp_planets, (str, bytes)):
                inp_copy["planets"] = [
                    _patch_co_planet_row(r, raw_by_pid, pid_by_coords, custom_names) for r in inp_planets
                ]
            res_copy["inputs"] = inp_copy
        if patched_planets is not None:
            _patch_co_summary_names(res_copy, patched_planets)
        out["result"] = res_copy
        return out

    def overlay_custom_names_on_ai_items(
        self,
        items: Sequence[Mapping[str, object]],
        snap_state: GameState | None,
        custom_names: Mapping[str, str],
    ) -> list[dict[str, object]]:
        """Overlay custom planet names onto AI Planner timeline items."""
        raw_by_pid, pid_by_coords = _build_planet_name_maps(snap_state)
        out: list[dict[str, object]] = []
        for item in items:
            row = dict(item)
            pid = safe_api_int(row.get("planet_id"), 0)
            if pid <= 0:
                nc = normalize_coords(str(row.get("planet_coords") or ""))
                if nc and nc in pid_by_coords:
                    pid = pid_by_coords[nc]
            if pid > 0:
                custom = custom_names.get(str(pid))
                if custom:
                    row["planet_name"] = custom
                elif pid in raw_by_pid:
                    row["planet_name"] = raw_by_pid[pid]
            out.append(row)
        return out


def _build_planet_name_maps(snap_state: GameState | None) -> tuple[dict[int, str], dict[str, int]]:
    raw_by_pid: dict[int, str] = {}
    pid_by_coords: dict[str, int] = {}
    if snap_state is not None:
        for planet in snap_state.planets:
            raw_by_pid[int(planet.planet_id)] = str(planet.name or "")
            nc = normalize_coords(planet.coords)
            if nc:
                pid_by_coords[nc] = int(planet.planet_id)
    return raw_by_pid, pid_by_coords


def _patch_queue_custom_names(
    queues: Sequence[QueueJob],
    owned_custom_by_coords: Mapping[str, tuple[str, str]],
) -> tuple[tuple[QueueJob, ...], bool]:
    q_changed = False
    patched_q: list[QueueJob] = []
    for job in queues:
        q_name = str(job.name or "")
        if q_name and is_in_flight_fleet_job(job):
            dest_nc = extract_coords_from_text(q_name)
            if dest_nc and dest_nc in owned_custom_by_coords:
                raw_nm, cust_nm = owned_custom_by_coords[dest_nc]
                alts = [re.escape(cust_nm), "Colony"]
                if raw_nm and raw_nm.lower() != "colony":
                    alts.append(re.escape(raw_nm))
                pat = r"(\[\s*\d{1,2}:\d{1,3}:\d{1,2}\s*\]\s*)(?:" + "|".join(alts) + r")\s*$"

                def _replace_match(match: re.Match[str], cn: str = cust_nm) -> str:
                    return f"{match.group(1)}{cn}"

                updated_q_name = re.sub(pat, _replace_match, q_name, flags=re.IGNORECASE)
                if updated_q_name != q_name:
                    job = replace(job, name=updated_q_name)
                    q_changed = True
        patched_q.append(job)
    return tuple(patched_q), q_changed


def _patch_co_summary_names(res_copy: dict[str, object], patched_planets: Sequence[object]) -> None:
    res_copy["planet_names"] = {
        str(safe_api_int(pr.get("planet_id"), 0)): str(pr.get("name") or "")
        for pr in patched_planets
        if isinstance(pr, Mapping) and safe_api_int(pr.get("planet_id"), 0) > 0
    }
    raw_summary = res_copy.get("summary")
    if not isinstance(raw_summary, Mapping):
        return
    central_row = next(
        (pr for pr in patched_planets if isinstance(pr, Mapping) and bool(pr.get("is_central_planet", False))),
        None,
    )
    if central_row is None:
        return
    sum_copy = dict(raw_summary)
    c_name = str(central_row.get("name") or "")
    c_coords = str(central_row.get("coords") or "")
    c_slot = safe_api_int(central_row.get("slot_index", central_row.get("slot")), 1)
    sum_copy["central_planet_name"] = c_name
    if c_coords:
        sum_copy["central_planet"] = (
            f"{c_name} [{c_coords}] (slot {c_slot})" if c_name and c_name != c_coords else f"{c_coords} (slot {c_slot})"
        )
    res_copy["summary"] = sum_copy
