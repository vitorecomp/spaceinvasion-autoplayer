"""Kahn DAG topological layering, config diffing, lane assembly, and cache signature freezing."""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import asdict, fields
import math

from spaceinvasion.core.catalog import cap_json_int, safe_finite_float, safe_int, safe_key, sanitize_json_tree
from spaceinvasion.core.config import StrategyConfig, strategy_config_from_dict
from spaceinvasion.core.model import PlanetState, normalize_planet_type
from spaceinvasion.core.settings import normalize_planner_mode

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
    "execution_order",
)
EDGE_RANK: dict[str, int] = {etype: idx for idx, etype in enumerate(EDGE_TYPE_PRIORITY)}


def _c_int(value: object, default: int = 0) -> int:
    return cap_json_int(safe_int(value, default))


def _c_float(value: object, default: float = 0.0) -> float:
    val = safe_finite_float(value, default)
    return round(val, 4) if math.isfinite(val) else default


def _c_str(value: object, default: str = "") -> str:
    if value is None:
        return default
    return value if isinstance(value, str) else safe_key(value)


def serialize_config(
    cfg: StrategyConfig,
    *,
    sanitize: bool = True,
    already_normalized: bool = False,
) -> dict[str, object]:
    norm_cfg = cfg if already_normalized else strategy_config_from_dict(asdict(cfg))
    raw: dict[str, object] = dict(asdict(norm_cfg))
    raw["planner_mode"] = normalize_planner_mode(raw.get("planner_mode", "priority"), strict=False)
    raw["shipyard_targets"] = {_c_str(k): max(0, _c_int(v, 0)) for k, v in (norm_cfg.shipyard_targets or {}).items()}
    raw["fleet_research_targets"] = {
        _c_str(k): max(0, min(100, _c_int(v, 0)))
        for k, v in (norm_cfg.fleet_research_targets or {}).items()
        if _c_int(v, 0) > 0
    }
    if sanitize:
        sanitized: dict[str, object] = sanitize_json_tree(raw, nonfinite_float=0.0)
        return sanitized
    return raw


def diff_strategy_configs(active: StrategyConfig, effective: StrategyConfig) -> list[str]:
    changed: list[str] = []
    for fld in fields(StrategyConfig):
        fname = fld.name
        a_val = getattr(active, fname)
        e_val = getattr(effective, fname)
        if fname == "planner_mode":
            if normalize_planner_mode(a_val, strict=False) != normalize_planner_mode(e_val, strict=False):
                changed.append(fname)
        elif fname in ("shipyard_targets", "fleet_research_targets"):
            a_map = {str(k): int(v) for k, v in (a_val or {}).items()}
            e_map = {str(k): int(v) for k, v in (e_val or {}).items()}
            if a_map != e_map:
                changed.append(fname)
        elif isinstance(a_val, float) and isinstance(e_val, float):
            if abs(_c_float(a_val) - _c_float(e_val)) > 1e-9:
                changed.append(fname)
        elif a_val != e_val:
            changed.append(fname)
    changed.sort()
    return changed


def merge_strategy_config_overrides(
    base: StrategyConfig,
    overrides: Mapping[str, object] | None = None,
    *,
    planner_mode_override: str | None = None,
) -> tuple[StrategyConfig, list[str]]:
    """Return `(effective_config, changed_keys)` without mutating `base` or touching disk."""
    base_cfg = base if isinstance(base, StrategyConfig) else StrategyConfig()
    merged: dict[str, object] = dict(asdict(base_cfg))
    merged["shipyard_targets"] = dict(base_cfg.shipyard_targets or {})
    merged["fleet_research_targets"] = dict(base_cfg.fleet_research_targets or {})

    if isinstance(overrides, Mapping):
        for k, v in overrides.items():
            if k == "shipyard_targets" and isinstance(v, Mapping):
                merged["shipyard_targets"] = {**dict(base_cfg.shipyard_targets or {}), **dict(v)}
            elif k == "fleet_research_targets" and isinstance(v, Mapping):
                merged["fleet_research_targets"] = {**dict(base_cfg.fleet_research_targets or {}), **dict(v)}
            else:
                merged[str(k)] = v

    if planner_mode_override is not None:
        merged["planner_mode"] = normalize_planner_mode(planner_mode_override, strict=False)

    effective_cfg = strategy_config_from_dict(merged)
    normalized_base = strategy_config_from_dict(asdict(base_cfg))
    return effective_cfg, diff_strategy_configs(normalized_base, effective_cfg)


def has_path(adj: Mapping[int, Sequence[int]], start: int, goal: int) -> bool:
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


def kahn_longest_layers(
    n_count: int,
    edge_acc: Mapping[tuple[int, int], Mapping[str, str]],
    node_ids: Sequence[str],
) -> tuple[list[int], list[dict[str, object]], dict[int, list[int]], dict[int, list[int]], dict[str, int]]:
    """Compute longest-path topological layers and sorted edge records via Kahn's algorithm."""
    incoming: dict[int, list[int]] = {i: [] for i in range(n_count)}
    outgoing: dict[int, list[int]] = {i: [] for i in range(n_count)}
    edges_out: list[dict[str, object]] = []
    by_edge_type: dict[str, int] = {}

    for u_idx, v_idx in sorted(edge_acc.keys()):
        type_map = edge_acc[(u_idx, v_idx)]
        sorted_types = sorted(type_map.keys(), key=lambda t: EDGE_RANK.get(t, 99))
        primary_type = sorted_types[0]
        incoming[v_idx].append(u_idx)
        outgoing[u_idx].append(v_idx)
        by_edge_type[primary_type] = by_edge_type.get(primary_type, 0) + 1
        src_id, dst_id = node_ids[u_idx], node_ids[v_idx]
        edges_out.append(
            {
                "id": f"edge_{src_id}_{dst_id}",
                "source": src_id,
                "target": dst_id,
                "primary_type": primary_type,
                "types": sorted_types,
                "label": type_map[primary_type],
            }
        )

    layers = [0] * n_count
    in_deg = [len(incoming[i]) for i in range(n_count)]
    ready_q = [i for i in range(n_count) if in_deg[i] == 0]
    head = 0
    while head < len(ready_q):
        u_node = ready_q[head]
        head += 1
        base_layer = layers[u_node]
        for v_node in outgoing[u_node]:
            if base_layer + 1 > layers[v_node]:
                layers[v_node] = base_layer + 1
            in_deg[v_node] -= 1
            if in_deg[v_node] == 0:
                ready_q.append(v_node)
    return layers, edges_out, incoming, outgoing, by_edge_type


def assemble_lanes(
    ordered_planets: Sequence[PlanetState],
    nodes_meta: Sequence[Mapping[str, object]],
    lane_counts: Mapping[str, int],
    *,
    global_label: str = "Empire & Global Research",
    require_in_lane_counts: bool = False,
    global_extra: Mapping[str, object] | None = None,
    planet_extra_fn: Callable[[int], Mapping[str, object]] | None = None,
    fallback_extra: Mapping[str, object] | None = None,
    fallback_type_fn: Callable[[Mapping[str, object]], str] | None = None,
) -> list[dict[str, object]]:
    """Build deterministic global + per-planet + fallback swimlanes shared by all DAG tabs."""
    if not ordered_planets and not nodes_meta:
        return []
    global_lane: dict[str, object] = {
        "id": "lane:global",
        "scope": "global",
        "label": global_label,
        "planet_id": 0,
        "coords": "",
        "planet_type": "global",
        "energy": 0,
        "fields_used": 0,
        "fields_max": 0,
        **(dict(global_extra) if global_extra else {}),
        "node_count": lane_counts.get("lane:global", 0),
    }
    lanes_out: list[dict[str, object]] = [global_lane]
    emitted: set[str] = {"lane:global"}
    for p in ordered_planets:
        pid = _c_int(p.planet_id, 0)
        lid = f"lane:planet:{pid}"
        if require_in_lane_counts and lid not in lane_counts:
            continue
        pcoords = _c_str(p.coords)
        pname = _c_str(p.name) or f"Colony {pid}"
        lane: dict[str, object] = {
            "id": lid,
            "scope": "planet",
            "label": f"{pname} [{pcoords}]" if pcoords else pname,
            "planet_id": pid,
            "coords": pcoords,
            "planet_type": normalize_planet_type(p),
            "energy": _c_int(p.energy.net, 0),
            "fields_used": _c_int(p.fields_used, 0),
            "fields_max": _c_int(p.fields_max, 0),
            **(dict(planet_extra_fn(pid)) if planet_extra_fn else {}),
            "node_count": lane_counts.get(lid, 0),
        }
        lanes_out.append(lane)
        emitted.add(lid)
    for m in nodes_meta:
        lid = str(m["lane_id"])
        if lid not in emitted:
            pcoords, pname = str(m["planet_coords"]), str(m["planet_name"])
            fb_lane: dict[str, object] = {
                "id": lid,
                "scope": "planet",
                "label": f"{pname} [{pcoords}]" if pcoords else pname,
                "planet_id": m["planet_id"],
                "coords": pcoords,
                "planet_type": fallback_type_fn(m) if fallback_type_fn else "normal",
                "energy": 0,
                "fields_used": 0,
                "fields_max": 0,
                **(dict(fallback_extra) if fallback_extra else {}),
                "node_count": lane_counts.get(lid, 0),
            }
            lanes_out.append(fb_lane)
            emitted.add(lid)
    return lanes_out


def freeze_sig_tree(val: object, _depth: int = 0, _seen: set[int] | None = None) -> object:
    """Convert nested mappings/sequences/sets/floats into a cycle-safe hashable cache signature."""
    tv = type(val)
    if val is None or tv is int or tv is str or tv is bytes:
        return val
    if tv is bool:
        return ("__bool__", val)
    if isinstance(val, float):
        if math.isnan(val):
            return ("__nan__",)
        if math.isinf(val):
            return ("__inf__", 1 if val > 0 else -1)
        return float(val)
    if _depth >= 32:
        return repr(val)
    if isinstance(val, (Mapping, list, tuple, set, frozenset)):
        seen = _seen if _seen is not None else set()
        vid = id(val)
        if vid in seen:
            return ("__cycle__",)
        seen.add(vid)
        try:
            if isinstance(val, Mapping):
                items = [
                    (
                        k if type(k) is str else f"{type(k).__name__}:{k}",
                        freeze_sig_tree(v, _depth + 1, seen),
                    )
                    for k, v in val.items()
                ]
                items.sort(key=lambda kv: kv[0])
                return tuple(items)
            if isinstance(val, (list, tuple)):
                return tuple(freeze_sig_tree(x, _depth + 1, seen) for x in val)
            return ("__set__", tuple(freeze_sig_tree(x, _depth + 1, seen) for x in sorted(val, key=repr)))
        finally:
            seen.discard(vid)
    return val


_freeze_sig_tree = freeze_sig_tree
