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
from spaceinvasion.combat.fleet import normalize_coords
from spaceinvasion.combat.ranking import attach_action_etas
from spaceinvasion.core.state import (
    GameState,
    PlanetState,
    count_in_flight_spy_fleets_for_planet,
    is_in_flight_fleet_job,
    normalize_planet_type,
)
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
    raw["fleet_research_targets"] = {
        _clean_str(k): max(0, min(100, _clean_int(v, 0)))
        for k, v in (getattr(norm_cfg, "fleet_research_targets", None) or {}).items()
        if _clean_int(v, 0) > 0
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
        elif fname in ("shipyard_targets", "fleet_research_targets"):
            a_map = {str(k): int(v) for k, v in (a_val or {}).items()}
            e_map = {str(k): int(v) for k, v in (e_val or {}).items()}
            if a_map != e_map:
                changed.append(fname)
        elif fname in (
            "storage_warn_ratio",
            "blast_pig_iron_max_share",
            "combat_safety_margin",
            "raid_min_loot_spice_eq",
            "attack_min_res_per_hour",
            "recycle_min_res_per_hour",
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
    merged["fleet_research_targets"] = dict(getattr(base_cfg, "fleet_research_targets", None) or {})

    if isinstance(overrides, Mapping):
        for k, v in overrides.items():
            if k == "shipyard_targets" and isinstance(v, Mapping):
                merged["shipyard_targets"] = {
                    **dict(base_cfg.shipyard_targets or {}),
                    **dict(v),
                }
            elif k == "fleet_research_targets" and isinstance(v, Mapping):
                merged["fleet_research_targets"] = {
                    **dict(getattr(base_cfg, "fleet_research_targets", None) or {}),
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
    if kind in ("SCAN_GALAXY", "SPY", "TRANSPORT", "STATION", "RECYCLE", "COLONIZE", "ABANDON_COLONY"):
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
    if kind in ("ESCAPE_FLIGHT", "SCAN_GALAXY", "SPY", "TRANSPORT", "STATION", "ATTACK", "RECYCLE", "COLONIZE", "ABANDON_COLONY"):
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
            elif kind in ("ATTACK", "SPY", "TRANSPORT", "STATION", "SCAN_GALAXY", "RECYCLE", "COLONIZE", "CLAIM_TUTORIAL", "ACTIVATE_TRANSMITTER") and so_kind == kind:
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
            "fleet_research_targets": dict(act_cfg_dict.get("fleet_research_targets") or {}),
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


# ---------------------------------------------------------------------------
# Tab 3 (AI-Planner) & Tab 4 (Fleet / Attack Planner) DAG Builders
# ---------------------------------------------------------------------------

_COORDS_FULL_RE = re.compile(r"(\d{1,2}):(\d{1,3}):(\d{1,2})", re.ASCII)

_FLEET_PLANNER_KINDS: frozenset[str] = frozenset(
    {
        "ALERT_HOSTILE",
        "ESCAPE_FLIGHT",
        "ATTACK",
        "RECYCLE",
        "SPY",
        "SCAN_GALAXY",
        "TRANSPORT",
        "STATION",
        "COLONIZE",
        "ABANDON_COLONY",
        "BUILD_SHIP",
        "ACTIVATE_TRANSMITTER",
    }
)

_FLEET_DISPATCH_KINDS: frozenset[str] = frozenset(
    {
        "ESCAPE_FLIGHT",
        "ATTACK",
        "RECYCLE",
        "SPY",
        "TRANSPORT",
        "STATION",
        "COLONIZE",
    }
)


def _is_fleet_planner_action(act: Any) -> bool:
    """Include tactical/shipyard actions plus fleet/ship-unlock `RESEARCH` actions (excluding pure economy `r1`/`r2`)."""
    kind = _clean_str(getattr(act, "kind", "")).strip().upper()
    if kind in _FLEET_PLANNER_KINDS:
        return True
    if kind == "RESEARCH":
        rkey = _resolve_research_key(
            getattr(act, "target_id", None),
            _clean_str(getattr(act, "target_name", "")),
        )
        reason_l = _clean_str(getattr(act, "reason", "")).lower()
        if "fleet research" in reason_l:
            return True
        if rkey is not None and rkey not in ("r1", "r2"):
            return True
    return False


def _research_direct_deps(rkey: Optional[str]) -> set[str]:
    if not rkey or rkey not in game_data.RESEARCH_BY_ID:
        return set()
    deps: set[str] = set()
    for req in game_data.RESEARCH_BY_ID[rkey].get("requirements") or ():
        if not isinstance(req, Mapping):
            continue
        if str(req.get("category") or "").strip().lower() != "research":
            continue
        r_info = game_data.resolve_unit("research", str(req.get("slug") or req.get("name") or ""))
        dep_id = str(r_info.get("id") or "") if isinstance(r_info, Mapping) else ""
        if dep_id in game_data.RESEARCH_BY_ID:
            deps.add(dep_id)
    return deps


def _ship_all_research_deps(ship_slug: Optional[str]) -> set[str]:
    if not ship_slug:
        return set()
    ship_info = game_data.SHIPS.get(ship_slug) or game_data.resolve_unit("ships", ship_slug)
    if not isinstance(ship_info, Mapping):
        return set()
    deps: set[str] = set()
    stack: list[str] = []
    for req in ship_info.get("requirements") or ():
        if not isinstance(req, Mapping):
            continue
        if str(req.get("category") or "").strip().lower() != "research":
            continue
        r_info = game_data.resolve_unit("research", str(req.get("slug") or req.get("name") or ""))
        r_id = str(r_info.get("id") or "") if isinstance(r_info, Mapping) else ""
        if r_id in game_data.RESEARCH_BY_ID and r_id not in deps:
            deps.add(r_id)
            stack.append(r_id)
    while stack:
        cur = stack.pop()
        for dep_id in _research_direct_deps(cur):
            if dep_id not in deps:
                deps.add(dep_id)
                stack.append(dep_id)
    return deps


def _extract_first_coords(text: str) -> str:
    if not text:
        return ""
    m = _COORDS_FULL_RE.search(text)
    if not m:
        return ""
    return normalize_coords(f"{int(m.group(1))}:{int(m.group(2))}:{int(m.group(3))}")


def _kahn_longest_layers(
    n_count: int,
    edge_acc: Mapping[tuple[int, int], Mapping[str, str]],
    node_ids: Sequence[str],
) -> tuple[list[int], list[dict[str, Any]], dict[int, list[int]], dict[int, list[int]], dict[str, int]]:
    incoming: dict[int, list[int]] = {i: [] for i in range(n_count)}
    outgoing: dict[int, list[int]] = {i: [] for i in range(n_count)}
    edges_out: list[dict[str, Any]] = []
    by_edge_type: dict[str, int] = {}

    for u_idx, v_idx in sorted(edge_acc.keys()):
        type_map = edge_acc[(u_idx, v_idx)]
        sorted_types = sorted(type_map.keys(), key=lambda t: _EDGE_RANK.get(t, 99))
        primary_type = sorted_types[0]
        label = type_map[primary_type]
        incoming[v_idx].append(u_idx)
        outgoing[u_idx].append(v_idx)
        by_edge_type[primary_type] = by_edge_type.get(primary_type, 0) + 1
        src_id = node_ids[u_idx]
        dst_id = node_ids[v_idx]
        edges_out.append(
            {
                "id": f"edge_{src_id}_{dst_id}",
                "source": src_id,
                "target": dst_id,
                "primary_type": primary_type,
                "types": sorted_types,
                "label": label,
            }
        )

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

    return layers, edges_out, incoming, outgoing, by_edge_type


def _parse_week_filter(raw: Any) -> Optional[int]:
    if raw is None or isinstance(raw, bool):
        return None
    if isinstance(raw, int):
        return raw if raw >= 1 else None
    s = _clean_str(raw).strip().lower()
    if not s or s in ("all", "next", "window", "*"):
        return None
    if s.startswith("w") and s[1:].isascii() and s[1:].isdigit():
        val = int(s[1:])
        return val if val >= 1 else None
    parsed = _clean_optional_int(s)
    return parsed if (parsed is not None and parsed >= 1) else None


def _dominant_cost_resource(cost_d: Mapping[str, int], missing_d: Mapping[str, int]) -> Optional[str]:
    best_rk: Optional[str] = None
    best_val = 0
    for rk in _RESOURCE_KEYS:
        mv = max(0, _clean_int(missing_d.get(rk, 0), 0))
        if mv > best_val:
            best_val = mv
            best_rk = rk
    if best_rk is not None:
        return best_rk
    for rk in _RESOURCE_KEYS:
        cv = max(0, _clean_int(cost_d.get(rk, 0), 0))
        if cv > best_val:
            best_val = cv
            best_rk = rk
    return best_rk


def build_ai_planner_graph(
    state: Optional[GameState],
    plan_result: Optional[Mapping[str, Any]],
    *,
    window_size: int = 40,
    week_filter: Optional[int] = None,
    planet_filter: Optional[int | str] = None,
) -> dict[str, Any]:
    """Construct a windowed, guaranteed-acyclic multi-lane Kahn DAG for Tab 3 (AI-Planner)."""
    eff_window = max(1, min(250, _clean_int(window_size, 40)))
    clean_week = _parse_week_filter(week_filter)

    raw_pfilter = _clean_str(planet_filter).strip() if planet_filter is not None else ""
    pfilter_global_only = raw_pfilter.lower() in ("global", "research", "0")
    pfilter_pid = _clean_optional_int(planet_filter) if not pfilter_global_only else None
    if pfilter_pid is not None and pfilter_pid <= 0:
        pfilter_pid = None
    pfilter_coords = normalize_coords(raw_pfilter) if (raw_pfilter and ":" in raw_pfilter) else ""
    pfilter_name_low = (
        raw_pfilter.lower()
        if (raw_pfilter and not pfilter_global_only and pfilter_pid is None and not pfilter_coords)
        else ""
    )

    planets_by_id: dict[int, PlanetState] = {}
    planets_by_coords: dict[str, PlanetState] = {}
    planets_by_name: dict[str, PlanetState] = {}
    ordered_planets: list[PlanetState] = []
    if state is not None:
        for p in getattr(state, "planets", ()) or ():
            pid = _clean_int(getattr(p, "planet_id", 0), 0)
            p_coords = normalize_coords(_clean_str(getattr(p, "coords", "")))
            p_name = _clean_str(getattr(p, "name", ""))
            planets_by_id[pid] = p
            if p_coords:
                planets_by_coords[p_coords] = p
            if p_name:
                planets_by_name[p_name.lower()] = p
            ordered_planets.append(p)

    researches_map: dict[str, int] = {}
    if state is not None and isinstance(getattr(state, "researches", None), Mapping):
        for rk, rv in state.researches.items():
            researches_map[_clean_str(rk)] = max(0, _clean_int(rv, 0))

    raw_steps_seq: list[Mapping[str, Any]] = []
    if isinstance(plan_result, Mapping):
        cand_steps = plan_result.get("steps")
        if isinstance(cand_steps, Sequence) and not isinstance(cand_steps, (str, bytes)):
            raw_steps_seq = [s for s in cand_steps if isinstance(s, Mapping)]

    # Normalize & classify every plan step (including completed status against live state)
    all_steps: list[dict[str, Any]] = []
    available_weeks_set: set[int] = set()
    completed_total = 0

    for raw_pos, step in enumerate(raw_steps_seq):
        seq_num = _clean_int(step.get("seq"), raw_pos + 1)
        kind = _clean_str(step.get("kind"), "BUILD").strip().upper() or "BUILD"
        if kind == "DEMOLISH_BUILDING":
            kind = "DEMOLISH"
        elif kind not in ("BUILD", "DEMOLISH", "RESEARCH", "COLONIZE_PREVIEW"):
            kind = "BUILD"

        raw_start = step.get("start_sec")
        if raw_start is None and step.get("start_hours") is not None:
            raw_start = _clean_float(step.get("start_hours"), 0.0) * 3600.0
        start_sec = max(0.0, _clean_float(raw_start, 0.0))

        raw_dur = step.get("duration_sec")
        if raw_dur is None and step.get("duration_hours") is not None:
            raw_dur = _clean_float(step.get("duration_hours"), 0.0) * 3600.0
        dur_sec = max(0.0, _clean_float(raw_dur, 0.0))

        raw_fin = step.get("finish_sec")
        if raw_fin is None and step.get("finish_hours") is not None:
            raw_fin = _clean_float(step.get("finish_hours"), 0.0) * 3600.0
        finish_sec = max(start_sec + dur_sec, _clean_float(raw_fin, start_sec + dur_sec))

        week_raw = _clean_optional_int(step.get("week"))
        week_num = week_raw if (week_raw is not None and week_raw >= 1) else (int(start_sec // (7.0 * 86400.0)) + 1)
        available_weeks_set.add(week_num)

        s_pid = _clean_int(step.get("planet_id"), 0)
        s_coords_raw = _clean_str(step.get("planet_coords") or step.get("coords"))
        s_coords = normalize_coords(s_coords_raw) if s_coords_raw else ""
        s_pname = _clean_str(step.get("planet_name"))
        s_slot = _clean_optional_int(step.get("planet_slot", step.get("slot")))

        p_obj: Optional[PlanetState] = None
        if s_pid > 0:
            p_obj = planets_by_id.get(s_pid)
        if p_obj is None and s_coords:
            p_obj = planets_by_coords.get(s_coords)
        if p_obj is None and s_pname:
            p_obj = planets_by_name.get(s_pname.lower())
        if p_obj is not None:
            if s_pid <= 0:
                s_pid = _clean_int(p_obj.planet_id, 0)
            if not s_coords:
                s_coords = normalize_coords(_clean_str(p_obj.coords))
            if not s_pname:
                s_pname = _clean_str(p_obj.name)

        entity_key = _clean_str(step.get("entity_key") or step.get("target_id") or "").strip().lower()
        entity_name = _clean_str(step.get("entity_name") or step.get("target_name") or "").strip()
        target_level = _clean_optional_int(step.get("target_level"))

        bid: Optional[int] = None
        rkey: Optional[str] = None
        if kind in ("BUILD", "DEMOLISH"):
            bid = _resolve_building_id(entity_key, entity_name)
            if bid is not None:
                if not entity_key:
                    entity_key = f"b{bid}"
                if not entity_name:
                    b_info = game_data.BUILDING_BY_ID.get(bid)
                    entity_name = _clean_str(b_info.get("name") if b_info else f"Building {bid}")
        elif kind == "RESEARCH":
            rkey = _resolve_research_key(entity_key, entity_name)
            if rkey is not None:
                if not entity_key:
                    entity_key = rkey
                if not entity_name:
                    r_info = game_data.RESEARCH_BY_ID.get(rkey)
                    entity_name = _clean_str(r_info.get("name") if r_info else rkey.upper())
        else:
            if not entity_name:
                entity_name = f"Colony Preview ({s_pname or s_coords or f'Slot {s_slot or 0}'})"

        is_completed = False
        if kind == "BUILD" and p_obj is not None and bid is not None and target_level is not None and target_level > 0:
            is_completed = p_obj.building_level(bid) >= target_level
        elif kind == "DEMOLISH" and p_obj is not None and bid is not None and target_level is not None and target_level >= 0:
            is_completed = p_obj.building_level(bid) <= target_level
        elif kind == "RESEARCH" and rkey is not None and target_level is not None and target_level > 0:
            is_completed = researches_map.get(rkey, 0) >= target_level

        if is_completed:
            completed_total += 1

        cost_d = _res_to_dict(step.get("cost"))
        missing_d = {rk: 0 for rk in _RESOURCE_KEYS}
        if not is_completed and p_obj is not None:
            p_res = _res_to_dict(getattr(p_obj, "resources", None))
            for rk in _RESOURCE_KEYS:
                missing_d[rk] = max(0, cost_d[rk] - p_res[rk])
        elif not is_completed and kind == "RESEARCH" and state is not None and ordered_planets:
            # Check active planet or best planet with Research lab for display deficit
            lab_p = planets_by_id.get(_clean_int(getattr(state, "active_planet_id", 0), 0)) or ordered_planets[0]
            p_res = _res_to_dict(getattr(lab_p, "resources", None))
            for rk in _RESOURCE_KEYS:
                missing_d[rk] = max(0, cost_d[rk] - p_res[rk])

        if kind == "RESEARCH":
            lane_id = "lane:global"
        elif s_pid > 0:
            lane_id = f"lane:planet:{s_pid}"
        elif s_coords:
            lane_id = f"lane:coords:{s_coords}"
        elif s_slot is not None and s_slot > 0:
            lane_id = f"lane:slot:{s_slot}"
        else:
            lane_id = "lane:global"

        raw_tr_sources = step.get("transport_sources")
        tr_sources = (
            [dict(src) for src in raw_tr_sources if isinstance(src, Mapping)]
            if isinstance(raw_tr_sources, Sequence)
            and not isinstance(raw_tr_sources, (str, bytes))
            else []
        )
        all_steps.append(
            {
                "seq": seq_num,
                "kind": kind,
                "week": week_num,
                "start_sec": start_sec,
                "duration_sec": dur_sec,
                "finish_sec": finish_sec,
                "planet_id": s_pid,
                "planet_coords": s_coords,
                "planet_name": s_pname or (f"Slot {s_slot}" if s_slot else ("Empire" if kind == "RESEARCH" else f"Colony {s_pid}")),
                "planet_slot": s_slot,
                "planet_obj": p_obj,
                "entity_key": entity_key,
                "entity_name": entity_name or kind,
                "target_level": target_level,
                "bid": bid,
                "rkey": rkey,
                "cost": cost_d,
                "missing": missing_d,
                "completed": is_completed,
                "lane_id": lane_id,
                "needs_transport": bool(step.get("needs_transport", False)),
                "transport_request_at_sec": _clean_optional_float(step.get("transport_request_at_sec")),
                "transport_request_text": _clean_str(step.get("transport_request_text")),
                "transport_lead_sec": _clean_float(step.get("transport_lead_sec"), 0.0),
                "transport_travel_sec": _clean_float(step.get("transport_travel_sec"), 0.0),
                "transport_dispatch_delay_sec": _clean_float(step.get("transport_dispatch_delay_sec"), 0.0),
                "transport_sources": tr_sources,
            }
        )

    total_plan_steps = len(all_steps)
    remaining_total = total_plan_steps - completed_total

    def _matches_planet_filter(st: Mapping[str, Any]) -> bool:
        if not raw_pfilter:
            return True
        if pfilter_global_only:
            return st["lane_id"] == "lane:global"
        if st["kind"] == "RESEARCH":
            return True
        if pfilter_pid is not None and st["planet_id"] == pfilter_pid:
            return True
        if pfilter_coords and st["planet_coords"] == pfilter_coords:
            return True
        if pfilter_name_low and st["planet_name"].lower() == pfilter_name_low:
            return True
        return False

    filtered_steps: list[dict[str, Any]] = []
    for st in all_steps:
        if clean_week is not None:
            if st["week"] != clean_week:
                continue
        else:
            if st["completed"]:
                continue
        if not _matches_planet_filter(st):
            continue
        filtered_steps.append(st)

    windowed_steps = filtered_steps[:eff_window]
    n_count = len(windowed_steps)

    # Track earliest uncompleted step per queue slot to classify "ready" vs "queued"
    seen_uncompleted_slot: set[str] = set()
    nodes_meta: list[dict[str, Any]] = []
    lane_counts: dict[str, int] = {"lane:global": 0}
    for p in ordered_planets:
        pid = _clean_int(p.planet_id, 0)
        if not raw_pfilter or (
            not pfilter_global_only
            and (
                (pfilter_pid is not None and pid == pfilter_pid)
                or (pfilter_coords and normalize_coords(_clean_str(p.coords)) == pfilter_coords)
                or (pfilter_name_low and _clean_str(p.name).lower() == pfilter_name_low)
            )
        ):
            lane_counts[f"lane:planet:{pid}"] = 0

    used_node_ids: set[str] = set()
    for idx, st in enumerate(windowed_steps):
        cand_id = f"ai_{st['seq']}"
        if cand_id in used_node_ids:
            cand_id = f"ai_{st['seq']}_{idx}"
        used_node_ids.add(cand_id)

        kind = st["kind"]
        bid = st["bid"]
        rkey = st["rkey"]
        tlvl = st["target_level"]
        lane_id = st["lane_id"]
        lane_counts[lane_id] = lane_counts.get(lane_id, 0) + 1

        if kind == "RESEARCH":
            slot_key = "global:research"
        elif kind in ("BUILD", "DEMOLISH"):
            slot_key = f"{lane_id}:build"
        else:
            slot_key = f"{lane_id}:colonize"

        is_first_in_slot = False
        if not st["completed"] and slot_key not in seen_uncompleted_slot:
            is_first_in_slot = True
            seen_uncompleted_slot.add(slot_key)

        has_missing = any(v > 0 for v in st["missing"].values())
        p_full_now = bool(
            st["planet_obj"] is not None
            and st["planet_obj"].fields_max > 0
            and st["planet_obj"].fields_used >= st["planet_obj"].fields_max
        )
        if st["completed"]:
            status = "completed"
            exec_now = False
            prio = "P3"
        elif kind == "COLONIZE_PREVIEW":
            status = "blocked_gate"
            exec_now = False
            prio = "P2"
        elif kind == "DEMOLISH":
            if is_first_in_slot and p_full_now:
                status = "ready"
                exec_now = True
                prio = "P1"
            else:
                status = "queued"
                exec_now = False
                prio = "P2"
        elif is_first_in_slot and not has_missing:
            status = "ready"
            exec_now = True
            prio = "P1"
        elif is_first_in_slot and has_missing:
            status = "waiting_resources"
            exec_now = False
            prio = "P1"
        else:
            status = "queued"
            exec_now = False
            prio = "P2"

        unit_reqs: list[dict[str, Any]] = []
        if kind == "BUILD" and bid is not None:
            u_info = game_data.BUILDING_BY_ID.get(bid)
            if u_info:
                unit_reqs = list(u_info.get("requirements", ()))
        elif kind == "RESEARCH" and rkey is not None:
            u_info = game_data.RESEARCH_BY_ID.get(rkey)
            if u_info:
                unit_reqs = list(u_info.get("requirements", ()))

        category = _classify_node_category(
            "DEMOLISH_BUILDING" if kind == "DEMOLISH" else (kind if kind != "COLONIZE_PREVIEW" else "COLONIZE"),
            bid,
            None,
        )
        wait_eta = "Done" if st["completed"] else _format_eta_short(st["start_sec"])
        dur_txt = _format_eta_short(st["duration_sec"]) if st["duration_sec"] > 0.5 else "Instant"
        tot_eta = "Done" if st["completed"] else _format_eta_short(st["finish_sec"])
        bneck = _dominant_cost_resource(st["cost"], st["missing"]) if has_missing else None
        ship_suffix = ""
        if not st["completed"] and st["needs_transport"]:
            req_txt = st["transport_request_text"] or (
                _format_eta_short(st["transport_request_at_sec"])
                if st["transport_request_at_sec"] is not None
                else ""
            )
            ship_suffix = f" · Ship at {req_txt}" if req_txt else " · Transport needed"
        verb_lbl = "Demolish" if kind == "DEMOLISH" else "Build"
        reason_txt = (
            f"Week {st['week']} · Step #{st['seq']} · Start {wait_eta} · {verb_lbl} {dur_txt}{ship_suffix}"
            if not st["completed"]
            else f"Week {st['week']} · Step #{st['seq']} · Completed in live state"
        )

        nodes_meta.append(
            {
                "idx": idx,
                "id": cand_id,
                "step_seq": st["seq"],
                "week": st["week"],
                "order_index": idx,
                "lane_id": lane_id,
                "slot_key": slot_key,
                "priority": prio,
                "kind": kind,
                "category": category,
                "status": status,
                "planet_id": st["planet_id"],
                "planet_coords": st["planet_coords"],
                "planet_name": st["planet_name"],
                "planet_obj": st["planet_obj"],
                "target_id": bid if bid is not None else (int(rkey[1:]) if (rkey and rkey[1:].isdigit()) else None),
                "target_name": st["entity_name"],
                "target_level": tlvl,
                "entity_key": st["entity_key"],
                "reason": reason_txt,
                "executable_now": exec_now,
                "completed": st["completed"],
                "cost": st["cost"],
                "missing": st["missing"],
                "wait_seconds": st["start_sec"],
                "wait_eta": wait_eta,
                "duration_seconds": st["duration_sec"],
                "duration_text": dur_txt,
                "total_seconds": st["finish_sec"],
                "total_eta": tot_eta,
                "eta_bottleneck": bneck,
                "needs_transport": st["needs_transport"],
                "transport_request_at_sec": st["transport_request_at_sec"],
                "transport_request_text": st["transport_request_text"],
                "transport_lead_sec": st["transport_lead_sec"],
                "transport_travel_sec": st["transport_travel_sec"],
                "transport_dispatch_delay_sec": st["transport_dispatch_delay_sec"],
                "transport_sources": st["transport_sources"],
                "bid": bid,
                "rkey": rkey,
                "unit_reqs": unit_reqs,
            }
        )

    # Edge construction (strictly forward index u_idx < v_idx -> guaranteed DAG)
    edge_acc: dict[tuple[int, int], dict[str, str]] = {}
    adj: dict[int, list[int]] = {i: [] for i in range(n_count)}

    def _add_ai_edge(u_idx: int, v_idx: int, etype: str, label: str) -> None:
        if u_idx == v_idx or not (0 <= u_idx < n_count) or not (0 <= v_idx < n_count) or u_idx > v_idx:
            return
        pair = (u_idx, v_idx)
        if pair not in edge_acc:
            if _has_path(adj, v_idx, u_idx):
                return
            edge_acc[pair] = {}
            adj[u_idx].append(v_idx)
        if etype not in edge_acc[pair]:
            edge_acc[pair][etype] = label

    for v_pos in range(1, n_count):
        v = nodes_meta[v_pos]
        v_kind = v["kind"]
        v_lane = v["lane_id"]
        v_bid = v["bid"]
        v_rkey = v["rkey"]
        v_tlvl = v["target_level"]
        v_pobj = v["planet_obj"]

        # 1) Level chains (closest earlier upgrade of exact same building on same lane or same research)
        if v_kind == "BUILD" and v_bid is not None:
            for u_pos in range(v_pos - 1, -1, -1):
                u = nodes_meta[u_pos]
                if u["kind"] == "BUILD" and u["lane_id"] == v_lane and u["bid"] == v_bid:
                    u_tlvl = u["target_level"]
                    if u_tlvl is None or v_tlvl is None or v_tlvl > u_tlvl:
                        lbl = (
                            f"{v['target_name']} L{u_tlvl} -> L{v_tlvl}"
                            if (u_tlvl is not None and v_tlvl is not None)
                            else f"Sequential {v['target_name']} upgrade"
                        )
                        _add_ai_edge(u_pos, v_pos, "level_chain", lbl)
                    break
        elif v_kind == "DEMOLISH" and v_bid is not None:
            for u_pos in range(v_pos - 1, -1, -1):
                u = nodes_meta[u_pos]
                if u["kind"] == "DEMOLISH" and u["lane_id"] == v_lane and u["bid"] == v_bid:
                    u_tlvl = u["target_level"]
                    if u_tlvl is None or v_tlvl is None or v_tlvl < u_tlvl:
                        lbl = (
                            f"Demolish {v['target_name']} L{u_tlvl} -> L{v_tlvl}"
                            if (u_tlvl is not None and v_tlvl is not None)
                            else f"Sequential {v['target_name']} demolition"
                        )
                        _add_ai_edge(u_pos, v_pos, "level_chain", lbl)
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
                        _add_ai_edge(u_pos, v_pos, "level_chain", lbl)
                    break

        # 1b) Field-freeing edge: most recent DEMOLISH on the same planet lane unlocks the next BUILD
        if v_kind == "BUILD" and v_lane.startswith("lane:planet:"):
            for u_pos in range(v_pos - 1, -1, -1):
                u = nodes_meta[u_pos]
                if u["lane_id"] != v_lane:
                    continue
                if u["kind"] == "DEMOLISH":
                    _add_ai_edge(
                        u_pos,
                        v_pos,
                        "prerequisite",
                        f"Demolish {u['target_name']} frees 1 field for {v['target_name']}",
                    )
                    break
                if u["kind"] == "BUILD":
                    break

        # 2) Wiki game_data tech-tree prerequisites
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
                    if u["kind"] == "BUILD" and u["bid"] == req_bid and (
                        v_kind == "RESEARCH" or u["lane_id"] == v_lane
                    ):
                        u_lvl = u["target_level"]
                        if cur_b_lvl < req_lvl or (u_lvl is not None and u_lvl <= req_lvl):
                            _add_ai_edge(
                                u_pos,
                                v_pos,
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
                            _add_ai_edge(
                                u_pos,
                                v_pos,
                                "prerequisite",
                                f"{u['target_name']} L{u_lvl or req_lvl} unlocks {v['target_name']}",
                            )
                            break

        # 3) Energy support: most recent power producer (b5/b6 on same lane or r2 globally) -> energy-consuming BUILD
        if v_kind == "BUILD" and v_bid is not None:
            eff_tlvl = v_tlvl if (v_tlvl is not None and v_tlvl > 0) else 1
            v_energy_delta = game_data.building_energy_delta(v_bid, eff_tlvl)
            if v_energy_delta < 0:
                for u_pos in range(v_pos - 1, -1, -1):
                    u = nodes_meta[u_pos]
                    is_pwr = (
                        (u["kind"] == "BUILD" and u["lane_id"] == v_lane and u["bid"] in (5, 6))
                        or (u["kind"] == "RESEARCH" and u["rkey"] == "r2")
                    )
                    if is_pwr:
                        lvl_s = f" L{v_tlvl}" if v_tlvl is not None else ""
                        _add_ai_edge(
                            u_pos,
                            v_pos,
                            "energy_support",
                            f"Supplies energy for {v['target_name']}{lvl_s}",
                        )
                        break

        # 4) Resource feed: 2:1 Pig iron (b1 -> b2) and dominant cost resource mine / r1 Mining tech (never Metal!)
        if v_kind == "BUILD" and v_bid == 2:
            for u_pos in range(v_pos - 1, -1, -1):
                u = nodes_meta[u_pos]
                if u["kind"] == "BUILD" and u["lane_id"] == v_lane and u["bid"] == 1:
                    _add_ai_edge(
                        u_pos,
                        v_pos,
                        "resource_feed",
                        "Supplies 2:1 Pig iron feedstock for Blast furnace",
                    )
                    break
        dom_res = _dominant_cost_resource(v["cost"], v["missing"])
        if dom_res is not None:
            for u_pos in range(v_pos - 1, -1, -1):
                u = nodes_meta[u_pos]
                if u["kind"] == "BUILD" and u["lane_id"] == v_lane and u["bid"] in _MINE_RESOURCE_BY_BID:
                    if _MINE_RESOURCE_BY_BID[u["bid"]] == dom_res and not (u["bid"] == v_bid):
                        _add_ai_edge(
                            u_pos,
                            v_pos,
                            "resource_feed",
                            f"Boosts {_RESOURCE_LABELS[dom_res]} output for {v['target_name']}",
                        )
                        break
                elif u["kind"] == "RESEARCH" and u["rkey"] == "r1" and dom_res in ("pig_iron", "kryptonite", "spice"):
                    _add_ai_edge(
                        u_pos,
                        v_pos,
                        "resource_feed",
                        f"Mining technology (+2%/lvl) boosts {_RESOURCE_LABELS[dom_res]} for {v['target_name']}",
                    )
                    break

    # 5) Queue serialization per exclusive slot (global research + per-planet build queue)
    last_by_slot: dict[str, int] = {}
    for m in nodes_meta:
        skey = m["slot_key"]
        cur_idx = m["idx"]
        prev_idx = last_by_slot.get(skey)
        if prev_idx is not None and prev_idx < cur_idx:
            if skey == "global:research":
                q_lbl = "Single global research lab slot"
            else:
                q_lbl = f"Colony build queue ({m['planet_coords'] or m['planet_name']})"
            _add_ai_edge(prev_idx, cur_idx, "queue_order", q_lbl)
        last_by_slot[skey] = cur_idx

    node_ids = [m["id"] for m in nodes_meta]
    layers, edges_out, incoming, outgoing, by_edge_type = _kahn_longest_layers(
        n_count, edge_acc, node_ids
    )

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
            if lid not in lane_counts:
                continue
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
                pcoords = m["planet_coords"]
                pname = m["planet_name"]
                lanes_out.append(
                    {
                        "id": lid,
                        "scope": "planet",
                        "label": f"{pname} [{pcoords}]" if pcoords else pname,
                        "planet_id": m["planet_id"],
                        "coords": pcoords,
                        "planet_type": "preview" if m["kind"] == "COLONIZE_PREVIEW" else "normal",
                        "energy": 0,
                        "fields_used": 0,
                        "fields_max": 0,
                        "node_count": lane_counts.get(lid, 0),
                    }
                )
                emitted_lane_ids.add(lid)

    # Assemble nodes & summary
    nodes_out: list[dict[str, Any]] = []
    by_priority: dict[str, int] = {"P0": 0, "P1": 0, "P2": 0, "P3": 0}
    ready_count = 0
    waiting_count = 0
    blocked_count = 0
    queued_count = 0
    completed_in_window = 0
    max_finish_s = 0.0
    min_start_s: Optional[float] = None
    exec_counter = 0

    for m in nodes_meta:
        idx = m["idx"]
        st = m["status"]
        if st == "ready":
            ready_count += 1
        elif st == "waiting_resources":
            waiting_count += 1
        elif st == "blocked_gate":
            blocked_count += 1
        elif st == "completed":
            completed_in_window += 1
        else:
            queued_count += 1

        by_priority[m["priority"]] = by_priority.get(m["priority"], 0) + 1
        if m["total_seconds"] > max_finish_s:
            max_finish_s = float(m["total_seconds"])
        if min_start_s is None or m["wait_seconds"] < min_start_s:
            min_start_s = float(m["wait_seconds"])

        exec_rank: Optional[int] = None
        if m["executable_now"]:
            exec_rank = exec_counter
            exec_counter += 1

        nodes_out.append(
            {
                "id": m["id"],
                "step_seq": m["step_seq"],
                "week": m["week"],
                "order_index": m["order_index"],
                "exec_rank": exec_rank,
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
                "entity_key": m["entity_key"],
                "reason": m["reason"],
                "executable_now": m["executable_now"],
                "completed": m["completed"],
                "cost": m["cost"],
                "missing": m["missing"],
                "wait_seconds": m["wait_seconds"],
                "wait_eta": m["wait_eta"],
                "duration_seconds": m["duration_seconds"],
                "duration_text": m["duration_text"],
                "total_seconds": m["total_seconds"],
                "total_eta": m["total_eta"],
                "eta_bottleneck": m["eta_bottleneck"],
                "needs_transport": m["needs_transport"],
                "transport_request_at_sec": m["transport_request_at_sec"],
                "transport_request_text": m["transport_request_text"],
                "transport_lead_sec": m["transport_lead_sec"],
                "transport_travel_sec": m["transport_travel_sec"],
                "transport_dispatch_delay_sec": m["transport_dispatch_delay_sec"],
                "transport_sources": m["transport_sources"],
                "in_degree": len(incoming[idx]),
                "out_degree": len(outgoing[idx]),
            }
        )

    window_span_s = max(0.0, max_finish_s - (min_start_s or 0.0)) if n_count > 0 else 0.0
    payload = {
        "planner_mode": "ai_planner",
        "lanes": lanes_out,
        "nodes": nodes_out,
        "edges": edges_out,
        "summary": {
            "total_nodes": n_count,
            "total_edges": len(edges_out),
            "total_plan_steps": total_plan_steps,
            "completed_steps": completed_total,
            "remaining_steps": remaining_total,
            "filtered_steps": len(filtered_steps),
            "window_size": eff_window,
            "week_filter": clean_week,
            "planet_filter": raw_pfilter or None,
            "available_weeks": sorted(available_weeks_set),
            "ready_count": ready_count,
            "waiting_count": waiting_count,
            "blocked_count": blocked_count,
            "queued_count": queued_count,
            "completed_in_window": completed_in_window,
            "lane_count": len(lanes_out),
            "active_lane_count": sum(1 for ln in lanes_out if ln["node_count"] > 0),
            "max_layer": max(layers, default=0),
            "critical_path_seconds": _clean_float(max_finish_s, 0.0),
            "critical_path_eta": _format_eta_short(max_finish_s),
            "window_span_seconds": _clean_float(window_span_s, 0.0),
            "window_span_eta": _format_eta_short(window_span_s),
            "by_priority": by_priority,
            "by_edge_type": by_edge_type,
        },
    }
    return game_data.sanitize_json_tree(payload, nonfinite_float=0.0)


def _classify_inflight_mission(name: str, detail: str) -> tuple[str, str]:
    txt = f"{name} {detail}".lower()
    if any(tok in txt for tok in ("escape", "flucht", "save")):
        return "ESCAPE_FLIGHT", "survival"
    if any(tok in txt for tok in ("attack", "angriff", "raid")):
        return "ATTACK", "combat"
    if any(tok in txt for tok in ("recycl", "trümmer", "truemmer")):
        return "RECYCLE", "recon"
    if any(tok in txt for tok in ("spy", "spio", "probe")):
        return "SPY", "recon"
    if any(tok in txt for tok in ("coloniz", "koloni", "siedel")):
        return "COLONIZE", "recon"
    if any(tok in txt for tok in ("station", "deploy")):
        return "STATION", "recon"
    if any(tok in txt for tok in ("transport", "liefer")):
        return "TRANSPORT", "recon"
    return "IN_FLIGHT", "recon"


def build_fleet_planner_graph(
    state: Optional[GameState],
    planned_actions: Sequence[PlannedAction],
    config: StrategyConfig,
    *,
    attack_telemetry: Optional[Mapping[str, Any]] = None,
    spare_budget: Optional[Mapping[str, Any]] = None,
) -> dict[str, Any]:
    """Construct a guaranteed-acyclic multi-lane Fleet & Attack Planner DAG with per-planet `1 + b10` slot budgets."""
    _ = config if isinstance(config, StrategyConfig) else StrategyConfig()

    planets_by_id: dict[int, PlanetState] = {}
    planets_by_coords: dict[str, PlanetState] = {}
    ordered_planets: list[PlanetState] = []
    if state is not None:
        for p in getattr(state, "planets", ()) or ():
            pid = _clean_int(getattr(p, "planet_id", 0), 0)
            p_coords = normalize_coords(_clean_str(getattr(p, "coords", "")))
            planets_by_id[pid] = p
            if p_coords:
                planets_by_coords[p_coords] = p
            ordered_planets.append(p)

    fallback_pid: int = 0
    if state is not None:
        act_pid = _clean_int(getattr(state, "active_planet_id", 0), 0)
        if act_pid in planets_by_id:
            fallback_pid = act_pid
        elif ordered_planets:
            fallback_pid = _clean_int(ordered_planets[0].planet_id, 0)

    # 1. Assign in-flight fleet QueueJobs to launching planets
    inflight_by_pid: dict[int, list[Any]] = {pid: [] for pid in planets_by_id}
    inflight_unassigned: list[Any] = []
    if state is not None:
        for q in getattr(state, "active_queues", ()) or ():
            if not is_in_flight_fleet_job(q):
                continue
            q_orig = normalize_coords(_clean_str(getattr(q, "origin_coords", "")))
            matched_p = planets_by_coords.get(q_orig) if q_orig else None
            if matched_p is not None:
                inflight_by_pid[_clean_int(matched_p.planet_id, 0)].append(q)
            elif fallback_pid > 0:
                inflight_by_pid.setdefault(fallback_pid, []).append(q)
            else:
                inflight_unassigned.append(q)

    # Sort each planet's in-flight jobs by remaining_seconds ascending (earliest return first)
    def _rem_key(job: Any) -> float:
        rs = _clean_optional_float(getattr(job, "remaining_seconds", None))
        return rs if rs is not None else 1e12

    for pid in inflight_by_pid:
        inflight_by_pid[pid].sort(key=_rem_key)
    inflight_unassigned.sort(key=_rem_key)

    # 2. Filter planned_actions to fleet/attack/shipyard/fleet-research/transmitter kinds and enrich ETAs if needed
    fleet_actions: list[PlannedAction] = [
        a
        for a in (planned_actions or ())
        if _is_fleet_planner_action(a)
    ]
    if state is not None and fleet_actions:
        needs_eta = any(
            any(v > 0 for v in _res_to_dict(getattr(a, "missing", None)).values())
            and getattr(a, "wait_seconds", 0.0) == 0.0
            and getattr(a, "wait_eta", "Ready") == "Ready"
            for a in fleet_actions
        )
        if needs_eta:
            fleet_actions = attach_action_etas(state, fleet_actions)

    # Build per-planet slot_budgets (`1 + b10`)
    planned_fleet_by_pid: dict[int, int] = {}
    planned_shipyard_by_pid: dict[int, int] = {}
    for act in fleet_actions:
        k = _clean_str(getattr(act, "kind", "")).strip().upper()
        pid = _clean_int(getattr(act, "planet_id", 0), 0)
        if k in _FLEET_DISPATCH_KINDS:
            planned_fleet_by_pid[pid] = planned_fleet_by_pid.get(pid, 0) + 1
        elif k == "BUILD_SHIP":
            planned_shipyard_by_pid[pid] = planned_shipyard_by_pid.get(pid, 0) + 1

    slot_budgets_out: list[dict[str, Any]] = []
    slot_budget_by_pid: dict[int, dict[str, Any]] = {}
    total_slots_used = 0
    total_slots_max = 0
    res_spy_slots_cfg = max(0, _clean_int(getattr(config, "reserved_spy_slots", 1), 1))
    spy_res_only_cfg = bool(getattr(config, "spy_reserved_slots_only", False))
    for p in ordered_planets:
        pid = _clean_int(p.planet_id, 0)
        b10_lvl = max(0, p.building_level(10))
        max_slots = 1 + b10_lvl
        used_slots = len(inflight_by_pid.get(pid, ()))
        spy_used_slots = count_in_flight_spy_fleets_for_planet(state, p) if state is not None else 0
        avail_slots = max(0, max_slots - used_slots)
        total_slots_used += used_slots
        total_slots_max += max_slots
        ships_clean = {
            _clean_str(sk): max(0, _clean_int(sv, 0))
            for sk, sv in (getattr(p, "ships", None) or {}).items()
            if _clean_int(sv, 0) > 0
        }
        solar_cells = ships_clean.get("s9", 0)
        total_docked = sum(v for k, v in ships_clean.items() if k != "s9")
        entry = {
            "planet_id": pid,
            "planet_name": _clean_str(p.name) or f"Colony {pid}",
            "coords": normalize_coords(_clean_str(p.coords)) or _clean_str(p.coords),
            "planet_type": normalize_planet_type(p),
            "b10_level": b10_lvl,
            "used_slots": used_slots,
            "spy_used_slots": spy_used_slots,
            "reserved_spy_slots": min(max_slots, res_spy_slots_cfg),
            "spy_reserved_slots_only": spy_res_only_cfg,
            "max_slots": max_slots,
            "available_slots": avail_slots,
            "in_flight_count": used_slots,
            "planned_fleet_count": planned_fleet_by_pid.get(pid, 0),
            "planned_shipyard_count": planned_shipyard_by_pid.get(pid, 0),
            "docked_ships": ships_clean,
            "total_docked_ships": total_docked,
            "solar_cells": solar_cells,
        }
        slot_budgets_out.append(entry)
        slot_budget_by_pid[pid] = entry

    # 3. Assemble nodes_meta: in-flight nodes first, then planned fleet/shipyard nodes
    nodes_meta: list[dict[str, Any]] = []
    lane_counts: dict[str, int] = {"lane:global": 0}
    for p in ordered_planets:
        lane_counts[f"lane:planet:{_clean_int(p.planet_id, 0)}"] = 0

    inflight_indices_by_pid: dict[int, list[int]] = {}
    inflight_seq = 0
    for pid in [p.planet_id for p in ordered_planets] + [0]:
        jobs = inflight_by_pid.get(pid, []) if pid > 0 else inflight_unassigned
        p_obj = planets_by_id.get(pid)
        p_coords = normalize_coords(_clean_str(p_obj.coords)) if p_obj is not None else ""
        p_name = _clean_str(p_obj.name) if p_obj is not None else "Empire"
        lid = f"lane:planet:{pid}" if pid > 0 else "lane:global"
        for q in jobs:
            idx = len(nodes_meta)
            q_name = _clean_str(getattr(q, "name", "")) or "Active Fleet"
            q_detail = _clean_str(getattr(q, "detail", ""))
            mission_kind, cat = _classify_inflight_mission(q_name, q_detail)
            rem_s = max(0.0, _clean_float(getattr(q, "remaining_seconds", 0.0), 0.0))
            rem_txt = _clean_str(getattr(q, "remaining_text", "")) or _format_eta_short(rem_s)
            lane_counts[lid] = lane_counts.get(lid, 0) + 1
            inflight_indices_by_pid.setdefault(pid, []).append(idx)
            nodes_meta.append(
                {
                    "idx": idx,
                    "id": f"inflight_{inflight_seq}",
                    "order_index": idx,
                    "exec_rank": None,
                    "lane_id": lid,
                    "priority": "P0" if mission_kind == "ESCAPE_FLIGHT" else "P1",
                    "kind": "IN_FLIGHT",
                    "mission_kind": mission_kind,
                    "category": cat,
                    "status": "in_flight",
                    "in_flight": True,
                    "planet_id": pid,
                    "planet_coords": p_coords,
                    "planet_name": p_name,
                    "target_id": None,
                    "target_name": q_name,
                    "target_level": None,
                    "reason": q_detail or f"In-flight {mission_kind.lower().replace('_', ' ')} occupying Fleet base slot",
                    "executable_now": False,
                    "cost": {rk: 0 for rk in _RESOURCE_KEYS},
                    "missing": {rk: 0 for rk in _RESOURCE_KEYS},
                    "wait_seconds": 0.0,
                    "wait_eta": "In Flight",
                    "duration_seconds": rem_s,
                    "duration_text": rem_txt,
                    "total_seconds": rem_s,
                    "total_eta": rem_txt,
                    "eta_bottleneck": None,
                    "sid": None,
                    "slot_key": None,
                    "sys_key": _extract_system_key(q_name) or _extract_system_key(q_detail),
                    "target_coords": _extract_first_coords(q_name) or _extract_first_coords(q_detail),
                }
            )
            inflight_seq += 1

    exec_counter = 0
    for f_pos, act in enumerate(fleet_actions):
        idx = len(nodes_meta)
        kind = _clean_str(getattr(act, "kind", "")).strip().upper() or "ATTACK"
        prio = _clean_str(getattr(act, "priority", "P2")).strip().upper()
        if prio not in ("P0", "P1", "P2", "P3"):
            prio = "P2"
        pid = _clean_int(getattr(act, "planet_id", 0), 0)
        p_obj = planets_by_id.get(pid)
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

        cost_d = _res_to_dict(getattr(act, "cost", None))
        missing_d = _res_to_dict(getattr(act, "missing", None))
        sid, ship_slug = (
            _resolve_ship_id_and_slug(raw_tid, t_name)
            if kind in ("BUILD_SHIP", "SPY", "RECYCLE", "COLONIZE")
            else (None, None)
        )
        rkey = _resolve_research_key(raw_tid, t_name) if kind == "RESEARCH" else None
        lane_id = (
            "lane:global"
            if (kind in ("ACTIVATE_TRANSMITTER", "RESEARCH") or pid <= 0)
            else f"lane:planet:{pid}"
        )
        lane_counts[lane_id] = lane_counts.get(lane_id, 0) + 1

        category = _classify_node_category(kind, None, sid)
        if kind == "ACTIVATE_TRANSMITTER":
            category = "recon"
        status = _classify_node_status(kind, exec_now, missing_d, reason)

        wait_s = _clean_optional_float(getattr(act, "wait_seconds", 0.0))
        dur_s = _clean_float(getattr(act, "duration_seconds", 0.0), 0.0)
        tot_s = _clean_optional_float(getattr(act, "total_seconds", 0.0))
        wait_eta = _clean_str(getattr(act, "wait_eta", "Ready"), "Ready")
        dur_txt = _clean_str(getattr(act, "duration_text", "Instant"), "Instant")
        tot_eta = _clean_str(getattr(act, "total_eta", "Ready"), "Ready")
        bottleneck_raw = getattr(act, "eta_bottleneck", None)
        bottleneck = _clean_str(bottleneck_raw) if bottleneck_raw else None

        nodes_meta.append(
            {
                "idx": idx,
                "id": f"fleet_{f_pos}",
                "order_index": idx,
                "exec_rank": exec_rank,
                "lane_id": lane_id,
                "priority": prio,
                "kind": kind,
                "mission_kind": kind,
                "category": category,
                "status": status,
                "in_flight": False,
                "planet_id": pid,
                "planet_coords": p_coords,
                "planet_name": p_name,
                "target_id": t_id,
                "target_name": t_name,
                "target_level": t_lvl,
                "reason": reason,
                "executable_now": exec_now,
                "cost": cost_d,
                "missing": missing_d,
                "wait_seconds": wait_s,
                "wait_eta": wait_eta,
                "duration_seconds": dur_s,
                "duration_text": dur_txt,
                "total_seconds": tot_s,
                "total_eta": tot_eta,
                "eta_bottleneck": bottleneck,
                "sid": sid,
                "ship_slug": ship_slug,
                "rkey": rkey,
                "slot_key": _slot_key_for_action(kind, pid),
                "sys_key": _extract_system_key(t_name) or _extract_system_key(reason),
                "target_coords": _extract_first_coords(t_name) or _extract_first_coords(reason),
            }
        )

    # 4. Build guaranteed-acyclic Fleet Planner DAG edges
    n_count = len(nodes_meta)
    edge_acc: dict[tuple[int, int], dict[str, str]] = {}
    adj: dict[int, list[int]] = {i: [] for i in range(n_count)}

    def _add_fl_edge(
        u_idx: int,
        v_idx: int,
        etype: str,
        label: str,
        *,
        allow_reverse_index: bool = False,
    ) -> None:
        if u_idx == v_idx or not (0 <= u_idx < n_count) or not (0 <= v_idx < n_count):
            return
        if not allow_reverse_index and u_idx > v_idx:
            return
        pair = (u_idx, v_idx)
        if pair not in edge_acc:
            if _has_path(adj, v_idx, u_idx):
                return
            edge_acc[pair] = {}
            adj[u_idx].append(v_idx)
        if etype not in edge_acc[pair]:
            edge_acc[pair][etype] = label

    # (a) P0 hostile alert -> escape flight, and P0 escape -> first non-P0 planned action per planet
    p0_escape_by_planet: dict[int, int] = {}
    for u in nodes_meta:
        if u["kind"] == "ALERT_HOSTILE":
            for v in nodes_meta:
                if v["kind"] == "ESCAPE_FLIGHT" and not v["in_flight"] and u["planet_id"] in (0, v["planet_id"]):
                    _add_fl_edge(
                        u["idx"],
                        v["idx"],
                        "priority_gate",
                        f"Hostile alert triggers escape flight on {v['planet_coords'] or v['planet_name']}",
                        allow_reverse_index=True,
                    )
        if u["priority"] == "P0" and u["kind"] == "ESCAPE_FLIGHT" and not u["in_flight"]:
            p0_escape_by_planet.setdefault(u["planet_id"], u["idx"])

    gated_p0: set[int] = set()
    for v in nodes_meta:
        if v["in_flight"] or v["priority"] == "P0":
            continue
        v_pid = v["planet_id"]
        if v_pid in gated_p0:
            continue
        gate_u = p0_escape_by_planet.get(v_pid)
        if gate_u is not None and gate_u < v["idx"]:
            _add_fl_edge(
                gate_u,
                v["idx"],
                "priority_gate",
                f"P0 escape flight precedes {v['target_name'] or v['kind']}",
            )
            gated_p0.add(v_pid)

    # (b) Full fleet slot dependency: earliest-returning in-flight job -> first planned dispatch when slots are full
    for pid, sb in slot_budget_by_pid.items():
        if sb["used_slots"] >= sb["max_slots"] and inflight_indices_by_pid.get(pid):
            earliest_inf = inflight_indices_by_pid[pid][0]
            first_dispatch = next(
                (
                    v
                    for v in nodes_meta
                    if not v["in_flight"]
                    and v["planet_id"] == pid
                    and v["kind"] in _FLEET_DISPATCH_KINDS
                    and v["kind"] != "ESCAPE_FLIGHT"
                ),
                None,
            )
            if first_dispatch is not None:
                _add_fl_edge(
                    earliest_inf,
                    first_dispatch["idx"],
                    "queue_order",
                    f"Awaits free Fleet base (b10 L{sb['b10_level']}) slot on {sb['coords'] or sb['planet_name']}",
                )

    # (c) Pairwise fleet/shipyard prerequisites
    for v_pos in range(n_count):
        v = nodes_meta[v_pos]
        if v["in_flight"]:
            continue
        v_kind = v["kind"]
        v_pid = v["planet_id"]
        for u_pos in range(v_pos):
            u = nodes_meta[u_pos]
            if u["in_flight"]:
                continue
            u_kind = u["kind"]
            u_pid = u["planet_id"]
            u_sid = u["sid"]

            if u_kind == "RESEARCH" and u.get("rkey"):
                if v_kind == "RESEARCH" and v.get("rkey") and u["rkey"] in _research_direct_deps(v["rkey"]):
                    _add_fl_edge(
                        u_pos,
                        v_pos,
                        "prerequisite",
                        f"Prerequisite {u['target_name']} for {v['target_name']}",
                    )
                elif v_kind == "BUILD_SHIP" and v.get("ship_slug") and u["rkey"] in _ship_all_research_deps(v["ship_slug"]):
                    _add_fl_edge(
                        u_pos,
                        v_pos,
                        "prerequisite",
                        f"Unlocks {v['target_name']} ({u['target_name']})",
                    )

            if u_kind == "BUILD_SHIP" and u_pid in (0, v_pid):
                if u_sid == 10 and v_kind == "SPY":
                    _add_fl_edge(u_pos, v_pos, "prerequisite", "Builds Spy probe (s10) for espionage")
                elif u_sid == 11 and v_kind == "RECYCLE":
                    _add_fl_edge(u_pos, v_pos, "prerequisite", "Builds Recycler (s11) for debris harvesting")
                elif u_sid == 8 and v_kind == "COLONIZE":
                    _add_fl_edge(u_pos, v_pos, "prerequisite", "Builds Colony ship (s8) for colonization")
                elif u_sid in (1, 2) and v_kind in ("TRANSPORT", "STATION"):
                    _add_fl_edge(u_pos, v_pos, "prerequisite", f"Builds {u['target_name']} for logistics")
                elif u_sid not in (None, 8, 9, 10, 11) and v_kind in ("ATTACK", "ESCAPE_FLIGHT") and u_pid == v_pid:
                    _add_fl_edge(u_pos, v_pos, "prerequisite", f"Supplies {u['target_name']} for {v_kind.lower()}")

            if u_kind == "SCAN_GALAXY" and v_kind in ("SPY", "ATTACK", "RECYCLE", "COLONIZE"):
                if (u["sys_key"] and v["sys_key"] and u["sys_key"] == v["sys_key"]) or (
                    (not u["sys_key"] or not v["sys_key"]) and u_pid == v_pid
                ):
                    _add_fl_edge(u_pos, v_pos, "prerequisite", f"Galaxy scan scouts system for {v['target_name'] or v_kind}")

            if u_kind == "SPY" and v_kind in ("ATTACK", "COLONIZE"):
                same_target = bool(u["target_coords"] and v["target_coords"] and u["target_coords"] == v["target_coords"])
                same_sys = bool(u["sys_key"] and v["sys_key"] and u["sys_key"] == v["sys_key"] and v_kind == "COLONIZE")
                if same_target or same_sys:
                    _add_fl_edge(u_pos, v_pos, "prerequisite", f"Spy intel precedes {v['target_name'] or v_kind}")

            if u_kind == "STATION" and v_kind in ("ATTACK", "RECYCLE"):
                dest_c = u["target_coords"]
                v_c = normalize_coords(v["planet_coords"]) if v["planet_coords"] else ""
                if dest_c and v_c and dest_c == v_c:
                    _add_fl_edge(u_pos, v_pos, "prerequisite", f"Stations ships at {dest_c} for {v_kind.lower()}")

    # (d) Slot queue serialization & intra-lane executable_now dispatch order
    last_by_slot: dict[str, int] = {}
    last_ready_by_lane: dict[str, tuple[int, int]] = {}
    for m in nodes_meta:
        if m["in_flight"]:
            continue
        cur_idx = m["idx"]
        skey = m["slot_key"]
        if skey:
            prev_idx = last_by_slot.get(skey)
            if prev_idx is not None and prev_idx < cur_idx:
                q_lbl = (
                    "Global Research lab queue"
                    if skey == "global:research"
                    else (
                        f"Weapons factory queue ({m['planet_coords'] or m['planet_name']})"
                        if skey.endswith(":shipyard")
                        else f"Colony fleet dispatch order ({m['planet_coords'] or m['planet_name']})"
                    )
                )
                _add_fl_edge(prev_idx, cur_idx, "queue_order", q_lbl)
            last_by_slot[skey] = cur_idx
        if m["executable_now"] and m["exec_rank"] is not None:
            lid = m["lane_id"]
            cur_rank = int(m["exec_rank"])
            prev_ready = last_ready_by_lane.get(lid)
            if prev_ready is not None and prev_ready[0] < cur_idx:
                _add_fl_edge(
                    prev_ready[0],
                    cur_idx,
                    "execution_order",
                    f"Dispatch sequence #{prev_ready[1] + 1} -> #{cur_rank + 1}",
                )
            last_ready_by_lane[lid] = (cur_idx, cur_rank)

    node_ids = [m["id"] for m in nodes_meta]
    layers, edges_out, incoming, outgoing, by_edge_type = _kahn_longest_layers(
        n_count, edge_acc, node_ids
    )

    # Assemble lanes with fleet_slots_used / fleet_slots_max
    lanes_out: list[dict[str, Any]] = []
    if ordered_planets or nodes_meta:
        lanes_out.append(
            {
                "id": "lane:global",
                "scope": "global",
                "label": "Empire Tactical, Research & Transmitter",
                "planet_id": 0,
                "coords": "",
                "planet_type": "global",
                "energy": 0,
                "fields_used": 0,
                "fields_max": 0,
                "b10_level": 0,
                "fleet_slots_used": total_slots_used,
                "fleet_slots_max": total_slots_max,
                "node_count": lane_counts.get("lane:global", 0),
            }
        )
        emitted_lane_ids: set[str] = {"lane:global"}
        for p in ordered_planets:
            pid = _clean_int(p.planet_id, 0)
            lid = f"lane:planet:{pid}"
            sb = slot_budget_by_pid.get(pid, {})
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
                    "b10_level": sb.get("b10_level", 0),
                    "fleet_slots_used": sb.get("used_slots", 0),
                    "fleet_slots_max": sb.get("max_slots", 1),
                    "node_count": lane_counts.get(lid, 0),
                }
            )
            emitted_lane_ids.add(lid)
        for m in nodes_meta:
            lid = m["lane_id"]
            if lid not in emitted_lane_ids:
                pcoords = m["planet_coords"]
                pname = m["planet_name"]
                lanes_out.append(
                    {
                        "id": lid,
                        "scope": "planet",
                        "label": f"{pname} [{pcoords}]" if pcoords else pname,
                        "planet_id": m["planet_id"],
                        "coords": pcoords,
                        "planet_type": "normal",
                        "energy": 0,
                        "fields_used": 0,
                        "fields_max": 0,
                        "b10_level": 0,
                        "fleet_slots_used": 0,
                        "fleet_slots_max": 1,
                        "node_count": lane_counts.get(lid, 0),
                    }
                )
                emitted_lane_ids.add(lid)

    # Assemble nodes & summary
    nodes_out: list[dict[str, Any]] = []
    by_priority: dict[str, int] = {"P0": 0, "P1": 0, "P2": 0, "P3": 0}
    by_kind: dict[str, int] = {}
    ready_count = 0
    waiting_count = 0
    blocked_count = 0
    queued_count = 0
    in_flight_count = 0
    max_total_s = 0.0

    for m in nodes_meta:
        idx = m["idx"]
        st = m["status"]
        if st == "in_flight":
            in_flight_count += 1
        elif st == "ready":
            ready_count += 1
        elif st == "waiting_resources":
            waiting_count += 1
        elif st == "blocked_gate":
            blocked_count += 1
        else:
            queued_count += 1

        by_priority[m["priority"]] = by_priority.get(m["priority"], 0) + 1
        by_kind[m["kind"]] = by_kind.get(m["kind"], 0) + 1
        tot_s = m["total_seconds"]
        if isinstance(tot_s, (int, float)) and math.isfinite(tot_s) and tot_s > max_total_s:
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
                "mission_kind": m["mission_kind"],
                "category": m["category"],
                "status": st,
                "in_flight": m["in_flight"],
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
                "eta_bottleneck": m["eta_bottleneck"],
                "in_degree": len(incoming[idx]),
                "out_degree": len(outgoing[idx]),
            }
        )

    clean_telemetry = dict(attack_telemetry) if isinstance(attack_telemetry, Mapping) else {}
    clean_spare = dict(spare_budget) if isinstance(spare_budget, Mapping) else {}

    payload = {
        "planner_mode": "fleet_planner",
        "lanes": lanes_out,
        "nodes": nodes_out,
        "edges": edges_out,
        "summary": {
            "total_nodes": n_count,
            "total_edges": len(edges_out),
            "in_flight_count": in_flight_count,
            "ready_count": ready_count,
            "waiting_count": waiting_count,
            "blocked_count": blocked_count,
            "queued_count": queued_count,
            "lane_count": len(lanes_out),
            "active_lane_count": sum(1 for ln in lanes_out if ln["node_count"] > 0),
            "max_layer": max(layers, default=0),
            "critical_path_seconds": _clean_float(max_total_s, 0.0),
            "critical_path_eta": _format_eta_short(max_total_s),
            "total_slots_used": total_slots_used,
            "total_slots_max": total_slots_max,
            "total_slots_available": max(0, total_slots_max - total_slots_used),
            "planned_fleet_actions": sum(
                1
                for a in fleet_actions
                if _clean_str(getattr(a, "kind", "")).upper() not in ("BUILD_SHIP", "RESEARCH")
            ),
            "planned_shipyard_actions": sum(1 for a in fleet_actions if _clean_str(getattr(a, "kind", "")).upper() == "BUILD_SHIP"),
            "planned_research_actions": sum(1 for a in fleet_actions if _clean_str(getattr(a, "kind", "")).upper() == "RESEARCH"),
            "by_priority": by_priority,
            "by_kind": by_kind,
            "by_edge_type": by_edge_type,
        },
        "slot_budgets": slot_budgets_out,
        "attack_telemetry": clean_telemetry,
        "spare_budget": clean_spare,
    }
    return game_data.sanitize_json_tree(payload, nonfinite_float=0.0)
