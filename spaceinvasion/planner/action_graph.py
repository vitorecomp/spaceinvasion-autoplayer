"""Pure multi-lane Action Graph DAG builder and StrategyConfig override merger."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import asdict, fields
import math
import re
from typing import Any, Optional

from spaceinvasion.core import game_data
from spaceinvasion.core.config import (
    StrategyConfig,
    normalize_planner_mode,
    strategy_config_from_dict,
)
from spaceinvasion.combat.ranking import attach_action_etas
from spaceinvasion.core.state import GameState, PlanetState, normalize_planet_type
from spaceinvasion.planner.strategy import PlannedAction

_SHIP_SLUG_BY_ID: dict[int, str] = {
    1: "small-transport",
    2: "large-transport",
    3: "transmitter",
    4: "small-fighter",
    5: "warship",
    6: "starcruiser",
    7: "phoenix",
    8: "colony-ship",
    9: "solar-cells",
    10: "spy-probe",
    11: "recycler",
    13: "battleship",
    14: "stealth-bomber",
    15: "destroyer",
    16: "imperial-starbase",
}

_RESOURCE_KEYS: tuple[str, ...] = ("pig_iron", "metal", "kryptonite", "spice")

_MINE_RESOURCE_BY_BID: dict[int, str] = {
    1: "pig_iron",
    2: "metal",
    3: "kryptonite",
    4: "spice",
}

_WAREHOUSE_RESOURCE_BY_BID: dict[int, str] = {
    11: "pig_iron",
    12: "metal",
    13: "kryptonite",
    14: "spice",
}

_RESOURCE_LABELS: dict[str, str] = {
    "pig_iron": "Pig iron",
    "metal": "Metal",
    "kryptonite": "Kryptonite",
    "spice": "Spice",
}

EDGE_TYPE_PRIORITY: tuple[str, ...] = (
    "prerequisite",
    "level_chain",
    "energy_support",
    "throttle_control",
    "storage_cap",
    "priority_gate",
    "resource_feed",
    "loot_feed",
    "queue_order",
    "ga_step_order",
    "execution_order",
)
_EDGE_RANK: dict[str, int] = {etype: idx for idx, etype in enumerate(EDGE_TYPE_PRIORITY)}

_SYS_COORDS_RE = re.compile(r"(\d{1,2}):(\d{1,3})", re.ASCII)


def _clean_int(value: Any, default: int = 0) -> int:
    return game_data.cap_json_int(game_data.safe_int(value, default))


def _clean_optional_int(value: Any) -> Optional[int]:
    parsed = game_data.safe_optional_int(value, None)
    if parsed is None:
        return None
    return game_data.cap_json_int(parsed)


def _clean_float(value: Any, default: float = 0.0) -> float:
    val = game_data.safe_finite_float(value, default)
    return round(val, 4) if math.isfinite(val) else default


def _clean_optional_float(value: Any) -> Optional[float]:
    if value is None or isinstance(value, bool):
        return None
    try:
        val = float(value)
    except Exception:
        return None
    if not math.isfinite(val):
        return None
    return round(val, 4)


def _clean_str(value: Any, default: str = "") -> str:
    if value is None:
        return default
    if isinstance(value, str):
        return value
    return game_data.safe_key(value)


def _res_to_dict(res: Any) -> dict[str, int]:
    if res is None:
        return {rk: 0 for rk in _RESOURCE_KEYS}
    if isinstance(res, Mapping):
        return {rk: max(0, _clean_int(res.get(rk, 0), 0)) for rk in _RESOURCE_KEYS}
    return {rk: max(0, _clean_int(getattr(res, rk, 0), 0)) for rk in _RESOURCE_KEYS}


def _format_eta_short(seconds: Optional[float]) -> str:
    if seconds is None or not math.isfinite(seconds):
        return "—"
    if seconds <= 0.5:
        return "Ready"
    total = int(round(seconds))
    days, rem = divmod(total, 86400)
    hours, rem = divmod(rem, 3600)
    mins, secs = divmod(rem, 60)
    if days > 0:
        return f"{days}d {hours}h"
    if hours > 0:
        return f"{hours}h {mins}m"
    if mins > 0:
        return f"{mins}m {secs}s"
    return f"{secs}s"


def _serialize_config(
    cfg: StrategyConfig,
    *,
    sanitize: bool = True,
    _already_normalized: bool = False,
) -> dict[str, Any]:
    norm_cfg = cfg if _already_normalized else strategy_config_from_dict(asdict(cfg))
    raw = asdict(norm_cfg)
    raw["planner_mode"] = normalize_planner_mode(raw.get("planner_mode", "priority"))
    raw["shipyard_targets"] = {
        _clean_str(k): max(0, _clean_int(v, 0))
        for k, v in (norm_cfg.shipyard_targets or {}).items()
    }
    return game_data.sanitize_json_tree(raw, nonfinite_float=0.0) if sanitize else raw


def _diff_strategy_configs(active: StrategyConfig, effective: StrategyConfig) -> list[str]:
    changed: list[str] = []
    for f in fields(StrategyConfig):
        fname = f.name
        a_val = getattr(active, fname)
        e_val = getattr(effective, fname)
        if fname == "planner_mode":
            if normalize_planner_mode(a_val) != normalize_planner_mode(e_val):
                changed.append(fname)
        elif fname == "shipyard_targets":
            a_map = {str(k): int(v) for k, v in (a_val or {}).items()}
            e_map = {str(k): int(v) for k, v in (e_val or {}).items()}
            if a_map != e_map:
                changed.append(fname)
        elif fname in (
            "storage_warn_ratio",
            "blast_pig_iron_max_share",
            "combat_safety_margin",
            "raid_min_loot_spice_eq",
            "ga_mutation_rate",
            "ga_nn_weight",
        ):
            if abs(_clean_float(a_val) - _clean_float(e_val)) > 1e-9:
                changed.append(fname)
        else:
            if a_val != e_val:
                changed.append(fname)
    changed.sort()
    return changed


def merge_strategy_config_overrides(
    base: StrategyConfig,
    overrides: Optional[Mapping[str, Any]] = None,
    *,
    planner_mode_override: Optional[str] = None,
) -> tuple[StrategyConfig, list[str]]:
    """Return `(effective_config, changed_keys)` without mutating `base` or touching disk."""
    base_cfg = base if isinstance(base, StrategyConfig) else StrategyConfig()
    merged: dict[str, Any] = asdict(base_cfg)
    merged["shipyard_targets"] = dict(base_cfg.shipyard_targets or {})

    if isinstance(overrides, Mapping):
        for k, v in overrides.items():
            if k == "shipyard_targets" and isinstance(v, Mapping):
                merged["shipyard_targets"] = {
                    **dict(base_cfg.shipyard_targets or {}),
                    **dict(v),
                }
            else:
                merged[str(k)] = v

    if planner_mode_override is not None:
        merged["planner_mode"] = normalize_planner_mode(planner_mode_override)

    effective_cfg = strategy_config_from_dict(merged)
    normalized_base = strategy_config_from_dict(asdict(base_cfg))
    changed_keys = _diff_strategy_configs(normalized_base, effective_cfg)
    return effective_cfg, changed_keys


def _resolve_building_id(target_id: Any, target_name: str) -> Optional[int]:
    tid = _clean_optional_int(target_id)
    if tid is not None and tid in game_data.BUILDING_BY_ID:
        return tid
    if isinstance(target_id, str):
        s = target_id.strip().lower()
        sub = s[1:] if s.startswith("b") else s
        if sub.isascii() and sub.isdigit() and len(sub) <= 18:
            bid = int(sub)
            if bid in game_data.BUILDING_BY_ID:
                return bid
    if target_name:
        info = game_data.resolve_unit("buildings", target_name)
        if info and info.get("id") is not None:
            bid = int(info["id"])
            if bid in game_data.BUILDING_BY_ID:
                return bid
    return None


def _resolve_research_key(target_id: Any, target_name: str) -> Optional[str]:
    if isinstance(target_id, str):
        s = target_id.strip().lower()
        if s in game_data.RESEARCH_BY_ID:
            return s
        sub = s[1:] if s.startswith("r") else s
        if sub.isascii() and sub.isdigit() and len(sub) <= 18:
            rk = f"r{int(sub)}"
            if rk in game_data.RESEARCH_BY_ID:
                return rk
    tid = _clean_optional_int(target_id)
    if tid is not None and 1 <= tid <= 100:
        rk = f"r{tid}"
        if rk in game_data.RESEARCH_BY_ID:
            return rk
    if target_name:
        info = game_data.resolve_unit("research", target_name)
        if info and info.get("id"):
            rk = str(info["id"])
            if rk in game_data.RESEARCH_BY_ID:
                return rk
    return None


def _resolve_ship_id_and_slug(target_id: Any, target_name: str) -> tuple[Optional[int], Optional[str]]:
    tid = _clean_optional_int(target_id)
    if tid is not None and tid in _SHIP_SLUG_BY_ID:
        return tid, _SHIP_SLUG_BY_ID[tid]
    if isinstance(target_id, str):
        s = target_id.strip().lower()
        sub = s[1:] if s.startswith("s") else s
        if sub.isascii() and sub.isdigit() and len(sub) <= 18:
            sid = int(sub)
            if sid in _SHIP_SLUG_BY_ID:
                return sid, _SHIP_SLUG_BY_ID[sid]
        if s in game_data.SHIPS:
            for sid, slug in _SHIP_SLUG_BY_ID.items():
                if slug == s:
                    return sid, slug
            return None, s
    if target_name:
        # Strip trailing "(xN)" batch suffix if present
        clean_name = re.sub(r"\s*\(x\d+\)\s*$", "", target_name.strip(), flags=re.IGNORECASE)
        info = game_data.resolve_unit("ships", clean_name)
        if info and info.get("slug"):
            slug = str(info["slug"])
            for sid, s_slug in _SHIP_SLUG_BY_ID.items():
                if s_slug == slug:
                    return sid, slug
            return None, slug
    return None, None


def _extract_system_key(text: str) -> Optional[str]:
    if not text:
        return None
    m = _SYS_COORDS_RE.search(text)
    if not m:
        return None
    return f"{int(m.group(1)):02d}:{int(m.group(2)):03d}"


def _classify_node_category(kind: str, bid: Optional[int], sid: Optional[int]) -> str:
    if kind in ("ALERT_HOSTILE", "ESCAPE_FLIGHT"):
        return "survival"
    if kind == "THROTTLE_BUILDING":
        return "throttle"
    if kind in ("CLAIM_TUTORIAL", "TUTORIAL_TASK"):
        return "tutorial"
    if kind == "RESEARCH":
        return "research"
    if kind in ("SCAN_GALAXY", "SPY", "RECYCLE", "COLONIZE"):
        return "recon"
    if kind == "ATTACK":
        return "combat"
    if kind == "BUILD_SHIP":
        return "energy" if sid == 9 else "shipyard"
    if kind in ("BUILD", "WAIT_RESOURCES", "DEMOLISH_BUILDING"):
        if bid in (5, 6):
            return "energy"
        if bid in (11, 12, 13, 14):
            return "storage"
        return "economy"
    return "economy"


def _classify_node_status(
    kind: str,
    executable_now: bool,
    missing_dict: Mapping[str, int],
    reason: str,
) -> str:
    if executable_now:
        return "ready"
    r_low = reason.lower()
    has_missing = any(v > 0 for v in missing_dict.values())
    if (
        kind == "ALERT_HOSTILE"
        or "[hold:" in r_low
        or "allow_" in r_low
        or "prerequisites unmet" in r_low
        or "required)" in r_low
        or "in transit" in r_low
    ):
        return "blocked_gate"
    if has_missing:
        return "waiting_resources"
    if (
        "queue full" in r_low
        or "future/queued" in r_low
        or "waiting/queued" in r_low
        or "in research" in r_low
        or "busy" in r_low
    ):
        return "queued"
    if kind == "WAIT_RESOURCES":
        return "waiting_resources"
    return "queued"


def _slot_key_for_action(kind: str, planet_id: int) -> Optional[str]:
    if kind == "RESEARCH":
        return "global:research"
    if planet_id <= 0:
        return None
    if kind in ("BUILD", "WAIT_RESOURCES", "DEMOLISH_BUILDING"):
        return f"planet:{planet_id}:build"
    if kind == "BUILD_SHIP":
        return f"planet:{planet_id}:shipyard"
    if kind in ("ESCAPE_FLIGHT", "SCAN_GALAXY", "SPY", "ATTACK", "RECYCLE", "COLONIZE"):
        return f"planet:{planet_id}:fleet"
    return None


def _match_ga_steps(
    nodes_meta: Sequence[dict[str, Any]],
    optimizer_summary: Optional[Mapping[str, Any]],
) -> dict[int, dict[str, Any]]:
    if not isinstance(optimizer_summary, Mapping):
        return {}
    best_sim = optimizer_summary.get("best_simulation")
    if not isinstance(best_sim, Mapping):
        return {}
    raw_outcomes = best_sim.get("step_outcomes")
    if not isinstance(raw_outcomes, Sequence) or isinstance(raw_outcomes, (str, bytes)):
        return {}

    outcomes: list[dict[str, Any]] = [dict(so) for so in raw_outcomes if isinstance(so, Mapping)]
    if not outcomes:
        return {}

    used_indices: set[int] = set()
    matched: dict[int, dict[str, Any]] = {}

    for meta in nodes_meta:
        n_idx = int(meta["idx"])
        kind = str(meta["kind"])
        reason = str(meta["reason"])
        if kind in ("ALERT_HOSTILE", "ESCAPE_FLIGHT") or "[Hold:" in reason:
            continue
        pid = int(meta["planet_id"])
        bid = meta["bid"]
        rkey = meta["rkey"]
        sid = meta["sid"]
        ship_slug = meta["ship_slug"]
        tgt_lvl = meta["target_level"]

        candidates: list[tuple[int, int, dict[str, Any]]] = []
        for pos, so in enumerate(outcomes):
            if pos in used_indices:
                continue
            so_kind = _clean_str(so.get("kind")).upper()
            so_pid = _clean_int(so.get("planet_id"), 0)
            so_tid = so.get("target_id")
            so_tname = _clean_str(so.get("target_name"))
            so_tlvl = _clean_optional_int(so.get("target_level"))
            so_valid = bool(so.get("valid", True))
            valid_rank = 0 if so_valid else 1

            if kind in ("BUILD", "WAIT_RESOURCES") and so_kind == "BUILD":
                so_bid = _resolve_building_id(so_tid, so_tname)
                if so_pid == pid and so_bid is not None and so_bid == bid:
                    lvl_rank = 0 if (tgt_lvl is not None and so_tlvl == tgt_lvl) else 1
                    candidates.append((valid_rank, lvl_rank * 1000 + pos, so))
            elif kind == "RESEARCH" and so_kind == "RESEARCH":
                so_rkey = _resolve_research_key(so_tid, so_tname)
                if so_rkey is not None and so_rkey == rkey:
                    lvl_rank = 0 if (tgt_lvl is not None and so_tlvl == tgt_lvl) else 1
                    candidates.append((valid_rank, lvl_rank * 1000 + pos, so))
            elif kind == "BUILD_SHIP" and so_kind == "BUILD_SHIP":
                so_sid, so_slug = _resolve_ship_id_and_slug(so_tid, so_tname)
                if so_pid == pid and (
                    (sid is not None and so_sid == sid)
                    or (ship_slug is not None and so_slug == ship_slug)
                ):
                    candidates.append((valid_rank, pos, so))
            elif kind == "THROTTLE_BUILDING" and so_kind == "THROTTLE_BUILDING":
                if so_pid == pid:
                    candidates.append((valid_rank, pos, so))
            elif kind == "DEMOLISH_BUILDING" and so_kind == "DEMOLISH_BUILDING":
                so_bid = _resolve_building_id(so_tid, so_tname)
                if so_pid in (0, pid) and (bid is None or so_bid is None or so_bid == bid):
                    lvl_rank = 0 if (tgt_lvl is not None and so_tlvl == tgt_lvl) else 1
                    candidates.append((valid_rank, lvl_rank * 1000 + pos, so))
            elif kind in ("ATTACK", "SPY", "SCAN_GALAXY", "RECYCLE", "COLONIZE", "CLAIM_TUTORIAL", "ACTIVATE_TRANSMITTER") and so_kind == kind:
                if kind in ("CLAIM_TUTORIAL", "ACTIVATE_TRANSMITTER") or so_pid in (0, pid):
                    candidates.append((valid_rank, pos, so))

        if not candidates:
            continue
        candidates.sort(key=lambda item: (item[0], item[1]))
        chosen_so = candidates[0][2]
        chosen_pos = next(i for i, item in enumerate(outcomes) if item is chosen_so)
        used_indices.add(chosen_pos)

        matched[n_idx] = {
            "step_index": _clean_int(chosen_so.get("index"), chosen_pos),
            "valid": bool(chosen_so.get("valid", True)),
            "start_t": _clean_float(chosen_so.get("start_t"), 0.0),
            "finish_t": _clean_float(chosen_so.get("finish_t"), 0.0),
            "wait_s": _clean_float(chosen_so.get("wait_s"), 0.0),
            "duration_s": _clean_float(chosen_so.get("duration_s"), 0.0),
            "executable_at_t0": bool(chosen_so.get("executable_at_t0", False)),
            "raid_loot_spice_eq": _clean_float(chosen_so.get("raid_loot_spice_eq"), 0.0),
        }

    return matched


def _has_path(adj: Mapping[int, Sequence[int]], start: int, goal: int) -> bool:
    if start == goal:
        return True
    stack = [start]
    visited: set[int] = set()
    while stack:
        cur = stack.pop()
        if cur in visited:
            continue
        visited.add(cur)
        for nxt in adj.get(cur, ()):
            if nxt == goal:
                return True
            if nxt not in visited:
                stack.append(nxt)
    return False


def build_action_graph(
    state: Optional[GameState],
    actions: Sequence[PlannedAction],
    *,
    effective_config: StrategyConfig,
    active_config: Optional[StrategyConfig] = None,
    simulated: bool = False,
    optimizer_summary: Optional[Mapping[str, Any]] = None,
    planet_filter: Optional[int] = None,
    sanitize: bool = True,
) -> dict[str, Any]:
    """Construct a deterministic, guaranteed-acyclic multi-lane Action Graph DAG."""
    eff_cfg = effective_config if isinstance(effective_config, StrategyConfig) else StrategyConfig()
    act_cfg = active_config if isinstance(active_config, StrategyConfig) else eff_cfg
    mode = normalize_planner_mode(getattr(eff_cfg, "planner_mode", "priority"))
    norm_act: Optional[StrategyConfig] = None
    norm_eff: Optional[StrategyConfig] = None
    if act_cfg is eff_cfg or act_cfg == eff_cfg:
        changed_keys: list[str] = []
    else:
        norm_act = strategy_config_from_dict(asdict(act_cfg))
        norm_eff = strategy_config_from_dict(asdict(eff_cfg))
        changed_keys = _diff_strategy_configs(norm_act, norm_eff)
    is_dirty = bool(changed_keys)
    clean_pfilter = _clean_optional_int(planet_filter)
    if clean_pfilter is not None and clean_pfilter <= 0:
        clean_pfilter = None

    raw_list: list[PlannedAction] = list(actions or ())
    if clean_pfilter is not None:
        raw_list = [
            a
            for a in raw_list
            if _clean_int(getattr(a, "planet_id", 0), 0) in (0, clean_pfilter)
            or _clean_str(getattr(a, "kind", "")).upper() in ("RESEARCH", "CLAIM_TUTORIAL", "ACTIVATE_TRANSMITTER")
        ]

    # Enrich synthetic unit-test PlannedActions if ETAs have not been attached yet
    if state is not None and raw_list:
        needs_eta = any(
            any(v > 0 for v in _res_to_dict(getattr(a, "missing", None)).values())
            and getattr(a, "wait_seconds", 0.0) == 0.0
            and getattr(a, "wait_eta", "Ready") == "Ready"
            for a in raw_list
        )
        if needs_eta:
            raw_list = attach_action_etas(state, raw_list)

    planets_by_id: dict[int, PlanetState] = {}
    ordered_planets: list[PlanetState] = []
    if state is not None:
        for p in getattr(state, "planets", ()) or ():
            pid = _clean_int(getattr(p, "planet_id", 0), 0)
            if clean_pfilter is not None and pid != clean_pfilter:
                continue
            planets_by_id[pid] = p
            ordered_planets.append(p)

    researches_map: dict[str, int] = {}
    if state is not None and isinstance(getattr(state, "researches", None), Mapping):
        for rk, rv in state.researches.items():
            researches_map[_clean_str(rk)] = max(0, _clean_int(rv, 0))

    warn_ratio = max(0.1, min(0.99, _clean_float(getattr(eff_cfg, "storage_warn_ratio", 0.85), 0.85)))

    nodes_meta: list[dict[str, Any]] = []
    exec_counter = 0
    lane_counts: dict[str, int] = {"lane:global": 0}
    for p in ordered_planets:
        lane_counts[f"lane:planet:{_clean_int(p.planet_id, 0)}"] = 0

    for idx, act in enumerate(raw_list):
        kind = _clean_str(getattr(act, "kind", "")).strip().upper() or "BUILD"
        prio = _clean_str(getattr(act, "priority", "P2")).strip().upper()
        if prio not in ("P0", "P1", "P2", "P3"):
            prio = "P2"
        pid = _clean_int(getattr(act, "planet_id", 0), 0)
        p_obj = planets_by_id.get(pid)
        if p_obj is None and state is not None and pid > 0:
            for sp in getattr(state, "planets", ()) or ():
                if _clean_int(getattr(sp, "planet_id", 0), 0) == pid:
                    p_obj = sp
                    break
        p_coords = _clean_str(getattr(act, "planet_coords", "")) or (
            _clean_str(p_obj.coords) if p_obj is not None else ""
        )
        p_name = _clean_str(p_obj.name) if p_obj is not None else (
            f"Colony {pid}" if pid > 0 else "Empire"
        )
        raw_tid = getattr(act, "target_id", None)
        t_id = _clean_optional_int(raw_tid)
        t_name = _clean_str(getattr(act, "target_name", ""))
        t_lvl = _clean_optional_int(getattr(act, "target_level", None))
        reason = _clean_str(getattr(act, "reason", ""))
        exec_now = bool(getattr(act, "executable_now", False))
        exec_rank: Optional[int] = None
        if exec_now:
            exec_rank = exec_counter
            exec_counter += 1

        raw_cost_obj = getattr(act, "cost", None)
        if raw_cost_obj is None:
            raw_cost_d = {rk: 0 for rk in _RESOURCE_KEYS}
        elif isinstance(raw_cost_obj, Mapping):
            raw_cost_d = {rk: max(0, game_data.safe_int(raw_cost_obj.get(rk, 0), 0)) for rk in _RESOURCE_KEYS}
        else:
            raw_cost_d = {rk: max(0, game_data.safe_int(getattr(raw_cost_obj, rk, 0), 0)) for rk in _RESOURCE_KEYS}
        cost_d = {rk: game_data.cap_json_int(val) for rk, val in raw_cost_d.items()}
        missing_d = _res_to_dict(getattr(act, "missing", None))

        bid = (
            _resolve_building_id(raw_tid, t_name)
            if kind in ("BUILD", "WAIT_RESOURCES", "THROTTLE_BUILDING", "DEMOLISH_BUILDING")
            else None
        )
        rkey = _resolve_research_key(raw_tid, t_name) if kind == "RESEARCH" else None
        sid, ship_slug = (
            _resolve_ship_id_and_slug(raw_tid, t_name)
            if kind in ("BUILD_SHIP", "SPY", "RECYCLE", "COLONIZE")
            else (None, None)
        )

        lane_id = "lane:global" if (kind in ("RESEARCH", "CLAIM_TUTORIAL", "ACTIVATE_TRANSMITTER") or pid <= 0) else f"lane:planet:{pid}"
        lane_counts[lane_id] = lane_counts.get(lane_id, 0) + 1

        category = _classify_node_category(kind, bid, sid)
        status = _classify_node_status(kind, exec_now, missing_d, reason)

        wait_s = _clean_optional_float(getattr(act, "wait_seconds", 0.0))
        dur_s = _clean_float(getattr(act, "duration_seconds", 0.0), 0.0)
        tot_s = _clean_optional_float(getattr(act, "total_seconds", 0.0))
        wait_eta = _clean_str(getattr(act, "wait_eta", "Ready"), "Ready")
        dur_txt = _clean_str(getattr(act, "duration_text", "Instant"), "Instant")
        tot_eta = _clean_str(getattr(act, "total_eta", "Ready"), "Ready")
        bottleneck_raw = getattr(act, "eta_bottleneck", None)
        bottleneck = _clean_str(bottleneck_raw) if bottleneck_raw else None

        unit_reqs: list[dict[str, Any]] = []
        if kind in ("BUILD", "WAIT_RESOURCES") and bid is not None:
            u_info = game_data.BUILDING_BY_ID.get(bid)
            if u_info:
                unit_reqs = list(u_info.get("requirements", ()))
        elif kind == "RESEARCH" and rkey is not None:
            u_info = game_data.RESEARCH_BY_ID.get(rkey)
            if u_info:
                unit_reqs = list(u_info.get("requirements", ()))
        elif kind == "BUILD_SHIP" and ship_slug is not None:
            u_info = game_data.SHIPS.get(ship_slug)
            if u_info:
                unit_reqs = list(u_info.get("requirements", ()))

        nodes_meta.append(
            {
                "idx": idx,
                "id": f"act_{idx}",
                "order_index": idx,
                "exec_rank": exec_rank,
                "lane_id": lane_id,
                "priority": prio,
                "kind": kind,
                "category": category,
                "status": status,
                "planet_id": pid,
                "planet_obj": p_obj,
                "planet_coords": p_coords,
                "planet_name": p_name,
                "target_id": t_id,
                "target_name": t_name,
                "target_level": t_lvl,
                "reason": reason,
                "executable_now": exec_now,
                "cost": cost_d,
                "raw_cost": raw_cost_d,
                "missing": missing_d,
                "wait_seconds": wait_s,
                "wait_eta": wait_eta,
                "duration_seconds": dur_s,
                "duration_text": dur_txt,
                "total_seconds": tot_s,
                "total_eta": tot_eta,
                "eta_bottleneck": bottleneck,
                "bid": bid,
                "rkey": rkey,
                "sid": sid,
                "ship_slug": ship_slug,
                "unit_reqs": unit_reqs,
                "slot_key": _slot_key_for_action(kind, pid),
                "sys_key": _extract_system_key(t_name) or _extract_system_key(reason),
            }
        )

    ga_steps_by_idx = _match_ga_steps(nodes_meta, optimizer_summary) if mode == "ga_nn" else {}

    # Edge accumulator: (u_idx, v_idx) -> dict[edge_type, label]
    edge_Acc: dict[tuple[int, int], dict[str, str]] = {}
    adj: dict[int, list[int]] = {m["idx"]: [] for m in nodes_meta}

    def _add_edge(
        u_idx: int,
        v_idx: int,
        etype: str,
        label: str,
        *,
        allow_reverse_index: bool = False,
    ) -> None:
        if u_idx == v_idx or not (0 <= u_idx < len(nodes_meta)) or not (0 <= v_idx < len(nodes_meta)):
            return
        if not allow_reverse_index and u_idx > v_idx:
            return
        pair = (u_idx, v_idx)
        if pair not in edge_Acc:
            if _has_path(adj, v_idx, u_idx):
                return
            edge_Acc[pair] = {}
            adj[u_idx].append(v_idx)
        if etype not in edge_Acc[pair]:
            edge_Acc[pair][etype] = label

    n_count = len(nodes_meta)

    # 1. Pairwise domain dependencies
    p0_escape_by_planet: dict[int, int] = {}
    p0_alert_by_planet: dict[int, int] = {}
    for m in nodes_meta:
        if m["priority"] == "P0":
            if m["kind"] == "ESCAPE_FLIGHT" and m["planet_id"] not in p0_escape_by_planet:
                p0_escape_by_planet[m["planet_id"]] = m["idx"]
            elif m["kind"] == "ALERT_HOSTILE" and m["planet_id"] not in p0_alert_by_planet:
                p0_alert_by_planet[m["planet_id"]] = m["idx"]

    # Connect ALERT_HOSTILE -> ESCAPE_FLIGHT (even when executable ESCAPE_FLIGHT sorts ahead of advisory ALERT_HOSTILE)
    for u in nodes_meta:
        u_idx = u["idx"]
        if u["kind"] == "ALERT_HOSTILE":
            for v in nodes_meta:
                if v["kind"] == "ESCAPE_FLIGHT" and u["planet_id"] in (0, v["planet_id"]):
                    _add_edge(
                        u_idx,
                        v["idx"],
                        "priority_gate",
                        f"Hostile alert triggers escape flight on {v['planet_coords'] or v['planet_name']}",
                        allow_reverse_index=True,
                    )

    gated_planets_p0: set[int] = set()
    for v in nodes_meta:
        if v["priority"] == "P0":
            continue
        v_pid = v["planet_id"]
        if v_pid in gated_planets_p0:
            continue
        gate_u = p0_escape_by_planet.get(v_pid)
        if gate_u is None:
            gate_u = p0_alert_by_planet.get(v_pid, p0_alert_by_planet.get(0))
        if gate_u is not None and gate_u < v["idx"]:
            _add_edge(
                gate_u,
                v["idx"],
                "priority_gate",
                f"P0 survival gate precedes {v['target_name']}",
            )
            gated_planets_p0.add(v_pid)

    # P1 safety gate -> first P2 action on the same planet
    p1_by_planet: dict[int, list[int]] = {}
    for u in nodes_meta:
        if u["priority"] == "P1" and u["planet_id"] > 0:
            p1_by_planet.setdefault(u["planet_id"], []).append(u["idx"])
    for pid, p1_indices in p1_by_planet.items():
        first_p2 = next(
            (v for v in nodes_meta if v["planet_id"] == pid and v["priority"] == "P2" and v["idx"] > p1_indices[-1]),
            None,
        )
        if first_p2 is not None:
            for u_idx in p1_indices:
                _add_edge(
                    u_idx,
                    first_p2["idx"],
                    "priority_gate",
                    f"P1 safety recovery precedes {first_p2['target_name']}",
                )

    # Level chains, prerequisites, energy support, throttle control, storage cap, resource/loot feeds
    for v_pos in range(1, n_count):
        v = nodes_meta[v_pos]
        v_idx = v["idx"]
        v_kind = v["kind"]
        v_pid = v["planet_id"]
        v_bid = v["bid"]
        v_rkey = v["rkey"]
        v_tlvl = v["target_level"]
        v_pobj = v["planet_obj"]
        v_reason_low = v["reason"].lower()

        # (a) Level chains (closest earlier upgrade of the exact same building/research)
        if v_kind in ("BUILD", "WAIT_RESOURCES") and v_bid is not None and v_pid > 0:
            for u_pos in range(v_pos - 1, -1, -1):
                u = nodes_meta[u_pos]
                if u["kind"] in ("BUILD", "WAIT_RESOURCES") and u["planet_id"] == v_pid and u["bid"] == v_bid:
                    u_tlvl = u["target_level"]
                    if u_tlvl is None or v_tlvl is None or v_tlvl > u_tlvl:
                        lbl = (
                            f"{v['target_name']} L{u_tlvl} -> L{v_tlvl}"
                            if (u_tlvl is not None and v_tlvl is not None)
                            else f"Sequential {v['target_name']} upgrade"
                        )
                        _add_edge(u["idx"], v_idx, "level_chain", lbl)
                    break
        elif v_kind == "DEMOLISH_BUILDING" and v_bid is not None and v_pid > 0:
            for u_pos in range(v_pos - 1, -1, -1):
                u = nodes_meta[u_pos]
                if u["kind"] == "DEMOLISH_BUILDING" and u["planet_id"] == v_pid and u["bid"] == v_bid:
                    u_tlvl = u["target_level"]
                    if u_tlvl is None or v_tlvl is None or v_tlvl < u_tlvl:
                        lbl = (
                            f"{v['target_name']} L{u_tlvl} -> L{v_tlvl}"
                            if (u_tlvl is not None and v_tlvl is not None)
                            else f"Sequential {v['target_name']} teardown"
                        )
                        _add_edge(u["idx"], v_idx, "level_chain", lbl)
                    break
        elif v_kind == "RESEARCH" and v_rkey is not None:
            for u_pos in range(v_pos - 1, -1, -1):
                u = nodes_meta[u_pos]
                if u["kind"] == "RESEARCH" and u["rkey"] == v_rkey:
                    u_tlvl = u["target_level"]
                    if u_tlvl is None or v_tlvl is None or v_tlvl > u_tlvl:
                        lbl = (
                            f"{v['target_name']} L{u_tlvl} -> L{v_tlvl}"
                            if (u_tlvl is not None and v_tlvl is not None)
                            else f"Sequential {v['target_name']} research"
                        )
                        _add_edge(u["idx"], v_idx, "level_chain", lbl)
                    break

        # (b) Wiki game_data tech-tree prerequisites
        for req in v["unit_reqs"]:
            req_cat = str(req.get("category", ""))
            req_slug = str(req.get("slug", ""))
            req_lvl = _clean_int(req.get("level", 1), 1)
            if req_cat == "buildings":
                req_info = game_data.BUILDINGS.get(req_slug)
                req_bid = int(req_info["id"]) if req_info and req_info.get("id") is not None else None
                if req_bid is None:
                    continue
                cur_b_lvl = v_pobj.building_level(req_bid) if v_pobj is not None else 0
                for u_pos in range(v_pos - 1, -1, -1):
                    u = nodes_meta[u_pos]
                    if (
                        u["kind"] in ("BUILD", "WAIT_RESOURCES")
                        and u["planet_id"] == v_pid
                        and u["bid"] == req_bid
                    ):
                        u_lvl = u["target_level"]
                        if cur_b_lvl < req_lvl or (u_lvl is not None and u_lvl <= req_lvl):
                            _add_edge(
                                u["idx"],
                                v_idx,
                                "prerequisite",
                                f"{u['target_name']} L{u_lvl or req_lvl} unlocks {v['target_name']}",
                            )
                            break
            elif req_cat == "research":
                req_info = game_data.RESEARCHES.get(req_slug)
                req_rkey = str(req_info["id"]) if req_info and req_info.get("id") else None
                if req_rkey is None:
                    continue
                cur_r_lvl = researches_map.get(req_rkey, 0)
                for u_pos in range(v_pos - 1, -1, -1):
                    u = nodes_meta[u_pos]
                    if u["kind"] == "RESEARCH" and u["rkey"] == req_rkey:
                        u_lvl = u["target_level"]
                        if cur_r_lvl < req_lvl or (u_lvl is not None and u_lvl <= req_lvl):
                            _add_edge(
                                u["idx"],
                                v_idx,
                                "prerequisite",
                                f"{u['target_name']} L{u_lvl or req_lvl} unlocks {v['target_name']}",
                            )
                            break

        # Operational fleet / tutorial prerequisites + economy dependencies
        v_energy_delta = 0
        if v_kind in ("BUILD", "WAIT_RESOURCES") and v_bid is not None:
            eff_tgt_lvl = v_tlvl if (v_tlvl is not None and v_tlvl > 0) else (
                (v_pobj.building_level(v_bid) + 1) if v_pobj is not None else 1
            )
            v_energy_delta = game_data.building_energy_delta(v_bid, eff_tgt_lvl)

        for u_pos in range(v_pos):
            u = nodes_meta[u_pos]
            u_idx = u["idx"]
            u_kind = u["kind"]
            u_pid = u["planet_id"]
            u_bid = u["bid"]
            u_rkey = u["rkey"]
            u_sid = u["sid"]
            u_tlvl = u["target_level"]
            u_reason_low = u["reason"].lower()

            # Tutorial completion prerequisite
            if v_kind == "CLAIM_TUTORIAL" and "tutorial" in u_reason_low and u_kind != "CLAIM_TUTORIAL":
                _add_edge(u_idx, v_idx, "prerequisite", f"Completes tutorial step before {v['target_name']}")

            # Recon & ship operational prerequisites
            if u_kind == "BUILD_SHIP" and u_sid == 10 and v_kind == "SPY" and u_pid in (0, v_pid):
                _add_edge(u_idx, v_idx, "prerequisite", "Builds Spy probe (s10) for espionage")
            if u_kind == "BUILD_SHIP" and u_sid == 11 and v_kind == "RECYCLE" and u_pid in (0, v_pid):
                _add_edge(u_idx, v_idx, "prerequisite", "Builds Recycler (s11) for debris harvesting")
            if u_kind == "BUILD_SHIP" and u_sid == 8 and v_kind == "COLONIZE" and u_pid in (0, v_pid):
                _add_edge(u_idx, v_idx, "prerequisite", "Builds Colony ship (s8) for colonization")
            if u_kind == "SCAN_GALAXY" and v_kind in ("SPY", "ATTACK", "RECYCLE", "COLONIZE"):
                if (u["sys_key"] and v["sys_key"] and u["sys_key"] == v["sys_key"]) or (
                    (not u["sys_key"] or not v["sys_key"]) and u_pid == v_pid
                ):
                    _add_edge(u_idx, v_idx, "prerequisite", f"Galaxy scan scouts system for {v['target_name']}")
            if u_kind == "SPY" and v_kind == "COLONIZE" and u["sys_key"] and v["sys_key"] and u["sys_key"] == v["sys_key"]:
                _add_edge(u_idx, v_idx, "prerequisite", f"Probes free slot fields before {v['target_name']}")
            if (
                u_kind in ("BUILD", "WAIT_RESOURCES")
                and u_bid == 5
                and v_kind == "DEMOLISH_BUILDING"
                and v_bid == 6
                and u_pid == v_pid
            ):
                _add_edge(u_idx, v_idx, "energy_support", "Supplies Fusion energy buffer before Thermosolar teardown")
            if (
                u_kind == "DEMOLISH_BUILDING"
                and v_kind in ("BUILD", "WAIT_RESOURCES")
                and u_pid == v_pid
                and (
                    (v_pobj is not None and _clean_int(getattr(v_pobj, "fields_remaining", 1), 1) <= 0)
                    or "reclaim" in u_reason_low
                    or "fields" in u_reason_low
                )
            ):
                _add_edge(
                    u_idx,
                    v_idx,
                    "prerequisite",
                    f"Reclaims planetary building field before {v['target_name']}",
                )
            if (
                u_kind == "BUILD_SHIP"
                and u_sid not in (None, 9, 10)
                and v_kind in ("ESCAPE_FLIGHT", "ATTACK")
                and u_pid == v_pid
            ):
                _add_edge(
                    u_idx,
                    v_idx,
                    "prerequisite",
                    f"Supplies {u['target_name']} for {v_kind.replace('_', ' ').lower()}",
                )

            # Energy support: b5 / b6 / s9 / r2 -> energy-consuming building upgrade
            is_power_producer = (
                (u_kind in ("BUILD", "WAIT_RESOURCES") and u_bid in (5, 6) and u_pid == v_pid)
                or (u_kind == "BUILD_SHIP" and u_sid == 9 and u_pid == v_pid)
                or (u_kind == "RESEARCH" and u_rkey == "r2")
            )
            if is_power_producer and v_kind in ("BUILD", "WAIT_RESOURCES") and v_energy_delta < 0:
                p_energy = getattr(v_pobj, "energy", None) if v_pobj is not None else None
                p_net = _clean_int(getattr(p_energy, "net", 0), 0) if p_energy is not None else 0
                if (
                    p_net < 0
                    or (p_net + v_energy_delta) < 0
                    or "power" in u_reason_low
                    or "energy" in u_reason_low
                    or "power" in v_reason_low
                    or "energy" in v_reason_low
                ):
                    lvl_suffix = f" L{v_tlvl}" if v_tlvl is not None else ""
                    _add_edge(
                        u_idx,
                        v_idx,
                        "energy_support",
                        f"Supplies energy for {v['target_name']}{lvl_suffix}",
                    )

            # Blast furnace (b2) 0% / 100% throttle control
            if u_kind == "THROTTLE_BUILDING" and u_pid == v_pid:
                if u_tlvl == 0:
                    if (
                        v["missing"]["pig_iron"] > 0
                        or v["eta_bottleneck"] == "pig_iron"
                        or (v_kind in ("BUILD", "WAIT_RESOURCES") and v_bid in (1, 5) and "pig iron" in u_reason_low)
                    ):
                        _add_edge(
                            u_idx,
                            v_idx,
                            "throttle_control",
                            f"Pauses Blast furnace (0%) to conserve 2:1 Pig iron for {v['target_name']}",
                        )
                elif u_tlvl is not None and u_tlvl > 0:
                    if (
                        v["missing"]["metal"] > 0
                        or v["eta_bottleneck"] == "metal"
                        or (v_kind in ("BUILD", "WAIT_RESOURCES") and v_bid == 2)
                    ):
                        _add_edge(
                            u_idx,
                            v_idx,
                            "throttle_control",
                            f"Resumes Blast furnace ({u_tlvl}%) to smelt Metal for {v['target_name']}",
                        )

            # Warehouse storage cap & overflow protection (b11..b14)
            if u_kind in ("BUILD", "WAIT_RESOURCES") and u_bid in _WAREHOUSE_RESOURCE_BY_BID and u_pid == v_pid:
                wh_res = _WAREHOUSE_RESOURCE_BY_BID[u_bid]
                res_lbl = _RESOURCE_LABELS[wh_res]
                p_sto = getattr(v_pobj, "storage", None) if v_pobj is not None else None
                raw_sto_cap = max(0, game_data.safe_int(getattr(p_sto, wh_res, 0), 0)) if p_sto is not None else 0
                fill_ratio = (
                    _clean_float(v_pobj.storage_fill_ratios().get(wh_res, 0.0), 0.0)
                    if (v_pobj is not None and p_sto is not None)
                    else 0.0
                )
                exceeds_cap = raw_sto_cap > 0 and v["raw_cost"][wh_res] > raw_sto_cap
                named_in_reason = bool(v["target_name"]) and v["target_name"].lower() in u_reason_low
                overflow_unblocks = fill_ratio >= warn_ratio and (
                    (v_kind in ("BUILD", "WAIT_RESOURCES") and _MINE_RESOURCE_BY_BID.get(v_bid or 0) == wh_res)
                    or v["missing"][wh_res] > 0
                )
                if exceeds_cap or named_in_reason or overflow_unblocks:
                    _add_edge(
                        u_idx,
                        v_idx,
                        "storage_cap",
                        f"Expands {res_lbl} warehouse capacity for {v['target_name']}",
                    )

            # Resource feeds (b1..b4 mines, 2:1 Pig iron -> Blast furnace, r1 Mining tech on b1/b3/b4 only)
            if u_kind in ("BUILD", "WAIT_RESOURCES") and u_bid in _MINE_RESOURCE_BY_BID and u_pid == v_pid:
                prod_res = _MINE_RESOURCE_BY_BID[u_bid]
                if u_bid == 1 and v_kind in ("BUILD", "WAIT_RESOURCES") and v_bid == 2:
                    _add_edge(
                        u_idx,
                        v_idx,
                        "resource_feed",
                        "Supplies 2:1 Pig iron feedstock for Blast furnace",
                    )
                elif v["missing"][prod_res] > 0 or v["eta_bottleneck"] == prod_res:
                    _add_edge(
                        u_idx,
                        v_idx,
                        "resource_feed",
                        f"Boosts {_RESOURCE_LABELS[prod_res]} output for {v['target_name']}",
                    )
            elif u_kind == "RESEARCH" and u_rkey == "r1":
                # Mining technology boosts b1, b3, b4 ONLY (never Blast furnace b2 Metal)
                for boosted_rk in ("pig_iron", "kryptonite", "spice"):
                    if v["missing"][boosted_rk] > 0 or v["eta_bottleneck"] == boosted_rk:
                        _add_edge(
                            u_idx,
                            v_idx,
                            "resource_feed",
                            f"Mining technology (+2%/lvl) boosts {_RESOURCE_LABELS[boosted_rk]} for {v['target_name']}",
                        )
                        break

            # Loot / tutorial reward feeds
            if u_kind in ("CLAIM_TUTORIAL", "ATTACK"):
                has_missing = any(val > 0 for val in v["missing"].values())
                if (has_missing or v_kind == "WAIT_RESOURCES") and (
                    u_kind == "CLAIM_TUTORIAL" or u_pid == v_pid
                ):
                    feed_lbl = (
                        f"Tutorial reward funds {v['target_name']}"
                        if u_kind == "CLAIM_TUTORIAL"
                        else f"Raid plunder funds {v['target_name']}"
                    )
                    _add_edge(u_idx, v_idx, "loot_feed", feed_lbl)

    # 2. Exclusive slot queue serialization (planet build / shipyard / fleet & global 1-slot research)
    last_by_slot: dict[str, int] = {}
    for m in nodes_meta:
        skey = m["slot_key"]
        if not skey:
            continue
        cur_idx = m["idx"]
        prev_idx = last_by_slot.get(skey)
        if prev_idx is not None and prev_idx < cur_idx:
            if skey == "global:research":
                q_label = "Single global research lab slot"
            elif skey.endswith(":build"):
                q_label = f"Colony build queue ({m['planet_coords'] or m['planet_name']})"
            elif skey.endswith(":shipyard"):
                q_label = f"Weapons factory queue ({m['planet_coords'] or m['planet_name']})"
            else:
                q_label = f"Colony fleet dispatch order ({m['planet_coords'] or m['planet_name']})"
            _add_edge(prev_idx, cur_idx, "queue_order", q_label)
        last_by_slot[skey] = cur_idx

    # 3. GA horizon rollout step sequence (in ga_nn mode)
    if mode == "ga_nn" and ga_steps_by_idx:
        ga_ordered = sorted(
            ga_steps_by_idx.items(),
            key=lambda item: (int(item[1]["step_index"]), item[0]),
        )
        for (u_idx, u_step), (v_idx, v_step) in zip(ga_ordered, ga_ordered[1:]):
            if u_idx < v_idx:
                _add_edge(
                    u_idx,
                    v_idx,
                    "ga_step_order",
                    f"GA rollout step #{int(u_step['step_index']) + 1} -> #{int(v_step['step_index']) + 1}",
                )

    # 4. Intra-lane dispatch sequence among executable_now actions (preserves multi-planet parallelism!)
    last_ready_by_lane: dict[str, tuple[int, int]] = {}
    for m in nodes_meta:
        if not m["executable_now"] or m["exec_rank"] is None:
            continue
        lid = m["lane_id"]
        cur_idx = m["idx"]
        cur_rank = int(m["exec_rank"])
        prev_entry = last_ready_by_lane.get(lid)
        if prev_entry is not None and prev_entry[0] < cur_idx:
            _add_edge(
                prev_entry[0],
                cur_idx,
                "execution_order",
                f"Dispatch sequence #{prev_entry[1] + 1} -> #{cur_rank + 1}",
            )
        last_ready_by_lane[lid] = (cur_idx, cur_rank)

    # Build final sorted edge list & compute strict topological layers (layer[u] < layer[v])
    incoming: dict[int, list[int]] = {i: [] for i in range(n_count)}
    outgoing: dict[int, list[int]] = {i: [] for i in range(n_count)}
    edges_out: list[dict[str, Any]] = []
    by_edge_type: dict[str, int] = {}

    for (u_idx, v_idx) in sorted(edge_Acc.keys()):
        type_map = edge_Acc[(u_idx, v_idx)]
        sorted_types = sorted(type_map.keys(), key=lambda t: _EDGE_RANK.get(t, 99))
        primary_type = sorted_types[0]
        label = type_map[primary_type]
        incoming[v_idx].append(u_idx)
        outgoing[u_idx].append(v_idx)
        by_edge_type[primary_type] = by_edge_type.get(primary_type, 0) + 1
        edges_out.append(
            {
                "id": f"edge_act_{u_idx}_act_{v_idx}",
                "source": f"act_{u_idx}",
                "target": f"act_{v_idx}",
                "primary_type": primary_type,
                "types": sorted_types,
                "label": label,
            }
        )

    # Compute longest-path topological layers via Kahn's algorithm on the guaranteed DAG
    layers: list[int] = [0] * n_count
    in_deg_work: list[int] = [len(incoming[i]) for i in range(n_count)]
    ready_q: list[int] = [i for i in range(n_count) if in_deg_work[i] == 0]
    q_head = 0
    while q_head < len(ready_q):
        u_node = ready_q[q_head]
        q_head += 1
        base_layer = layers[u_node]
        for v_node in outgoing[u_node]:
            if base_layer + 1 > layers[v_node]:
                layers[v_node] = base_layer + 1
            in_deg_work[v_node] -= 1
            if in_deg_work[v_node] == 0:
                ready_q.append(v_node)

    # Assemble lanes
    lanes_out: list[dict[str, Any]] = []
    if ordered_planets or nodes_meta:
        lanes_out.append(
            {
                "id": "lane:global",
                "scope": "global",
                "label": "Empire & Global Research",
                "planet_id": 0,
                "coords": "",
                "planet_type": "global",
                "energy": 0,
                "fields_used": 0,
                "fields_max": 0,
                "node_count": lane_counts.get("lane:global", 0),
            }
        )
        emitted_lane_ids: set[str] = {"lane:global"}
        for p in ordered_planets:
            pid = _clean_int(p.planet_id, 0)
            lid = f"lane:planet:{pid}"
            pcoords = _clean_str(p.coords)
            pname = _clean_str(p.name) or f"Colony {pid}"
            lanes_out.append(
                {
                    "id": lid,
                    "scope": "planet",
                    "label": f"{pname} [{pcoords}]" if pcoords else pname,
                    "planet_id": pid,
                    "coords": pcoords,
                    "planet_type": normalize_planet_type(p),
                    "energy": _clean_int(getattr(getattr(p, "energy", None), "net", 0), 0),
                    "fields_used": _clean_int(p.fields_used, 0),
                    "fields_max": _clean_int(p.fields_max, 0),
                    "node_count": lane_counts.get(lid, 0),
                }
            )
            emitted_lane_ids.add(lid)
        for m in nodes_meta:
            lid = m["lane_id"]
            if lid not in emitted_lane_ids:
                pid = m["planet_id"]
                pcoords = m["planet_coords"]
                pname = m["planet_name"]
                lanes_out.append(
                    {
                        "id": lid,
                        "scope": "planet",
                        "label": f"{pname} [{pcoords}]" if pcoords else pname,
                        "planet_id": pid,
                        "coords": pcoords,
                        "planet_type": "normal",
                        "energy": 0,
                        "fields_used": 0,
                        "fields_max": 0,
                        "node_count": lane_counts.get(lid, 0),
                    }
                )
                emitted_lane_ids.add(lid)

    # Assemble nodes & summary counters
    nodes_out: list[dict[str, Any]] = []
    by_priority: dict[str, int] = {"P0": 0, "P1": 0, "P2": 0, "P3": 0}
    ready_count = 0
    waiting_count = 0
    blocked_count = 0
    queued_count = 0
    bottleneck_counts: dict[str, int] = {}
    max_total_s = 0.0
    has_unbounded_eta = False

    for m in nodes_meta:
        idx = m["idx"]
        st = m["status"]
        if st == "ready":
            ready_count += 1
        elif st == "waiting_resources":
            waiting_count += 1
        elif st == "blocked_gate":
            blocked_count += 1
        else:
            queued_count += 1

        by_priority[m["priority"]] = by_priority.get(m["priority"], 0) + 1
        bneck = m["eta_bottleneck"]
        if bneck:
            bottleneck_counts[bneck] = bottleneck_counts.get(bneck, 0) + 1

        tot_s = m["total_seconds"]
        if tot_s is None and st == "waiting_resources":
            has_unbounded_eta = True
        elif isinstance(tot_s, (int, float)) and math.isfinite(tot_s) and tot_s > max_total_s:
            max_total_s = float(tot_s)

        nodes_out.append(
            {
                "id": m["id"],
                "order_index": m["order_index"],
                "exec_rank": m["exec_rank"],
                "topological_layer": layers[idx],
                "lane_id": m["lane_id"],
                "priority": m["priority"],
                "kind": m["kind"],
                "category": m["category"],
                "status": st,
                "planet_id": m["planet_id"],
                "planet_coords": m["planet_coords"],
                "planet_name": m["planet_name"],
                "target_id": m["target_id"],
                "target_name": m["target_name"],
                "target_level": m["target_level"],
                "reason": m["reason"],
                "executable_now": m["executable_now"],
                "cost": m["cost"],
                "missing": m["missing"],
                "wait_seconds": m["wait_seconds"],
                "wait_eta": m["wait_eta"],
                "duration_seconds": m["duration_seconds"],
                "duration_text": m["duration_text"],
                "total_seconds": m["total_seconds"],
                "total_eta": m["total_eta"],
                "eta_bottleneck": bneck,
                "ga_step": ga_steps_by_idx.get(idx),
                "in_degree": len(incoming[idx]),
                "out_degree": len(outgoing[idx]),
            }
        )

    if mode == "ga_nn" and isinstance(optimizer_summary, Mapping):
        best_sim = optimizer_summary.get("best_simulation")
        if isinstance(best_sim, Mapping):
            sim_elapsed = _clean_optional_float(best_sim.get("elapsed_seconds"))
            if sim_elapsed is not None and sim_elapsed > max_total_s:
                max_total_s = sim_elapsed

    primary_bottleneck: Optional[str] = None
    if bottleneck_counts:
        primary_bottleneck = max(
            sorted(bottleneck_counts.keys()),
            key=lambda k: bottleneck_counts[k],
        )

    crit_eta = (
        "Blocked (0/h)"
        if (has_unbounded_eta and max_total_s <= 0.0 and waiting_count > 0)
        else _format_eta_short(max_total_s)
    )

    ga_summary_clean: Optional[dict[str, Any]] = None
    if mode == "ga_nn" and isinstance(optimizer_summary, Mapping) and optimizer_summary:
        ga_summary_clean = dict(optimizer_summary)

    if not is_dirty:
        act_cfg_dict = (
            _serialize_config(norm_act, sanitize=False, _already_normalized=True)
            if norm_act is not None
            else _serialize_config(act_cfg, sanitize=False)
        )
        eff_cfg_dict = {
            **act_cfg_dict,
            "shipyard_targets": dict(act_cfg_dict["shipyard_targets"]),
        }
    else:
        assert norm_act is not None and norm_eff is not None
        act_cfg_dict = _serialize_config(norm_act, sanitize=False, _already_normalized=True)
        eff_cfg_dict = _serialize_config(norm_eff, sanitize=False, _already_normalized=True)

    payload = {
        "mode": mode,
        "simulated": bool(simulated),
        "state_available": state is not None,
        "is_dirty": is_dirty,
        "changed_keys": changed_keys,
        "planet_filter": clean_pfilter,
        "active_config": act_cfg_dict,
        "effective_config": eff_cfg_dict,
        "lanes": lanes_out,
        "nodes": nodes_out,
        "edges": edges_out,
        "summary": {
            "total_nodes": n_count,
            "total_edges": len(edges_out),
            "ready_count": ready_count,
            "waiting_count": waiting_count,
            "blocked_count": blocked_count,
            "queued_count": queued_count,
            "lane_count": len(lanes_out),
            "active_lane_count": sum(1 for ln in lanes_out if ln["node_count"] > 0),
            "max_layer": max(layers, default=0),
            "critical_path_seconds": _clean_float(max_total_s, 0.0),
            "critical_path_eta": crit_eta,
            "primary_bottleneck": primary_bottleneck,
            "by_priority": by_priority,
            "by_edge_type": by_edge_type,
        },
        "ga_summary": ga_summary_clean,
    }
    return game_data.sanitize_json_tree(payload, nonfinite_float=0.0) if sanitize else payload
