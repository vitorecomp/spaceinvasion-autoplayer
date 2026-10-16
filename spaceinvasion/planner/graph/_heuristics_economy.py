"""Economy, energy, throttle, storage, and loot-feed edge rules for the Tab 1 Heuristic DAG."""

from __future__ import annotations

from collections.abc import Callable, Mapping

from spaceinvasion.core.catalog import safe_int
from spaceinvasion.core.model import PlanetState
from spaceinvasion.planner.graph.common import (
    MINE_RESOURCE_BY_BID,
    RESOURCE_LABELS,
    WAREHOUSE_RESOURCE_BY_BID,
    clean_float,
    clean_int,
)

NodeMeta = dict[str, object]
AddEdgeFn = Callable[..., None]


def _int(val: object, default: int = 0) -> int:
    return val if isinstance(val, int) and not isinstance(val, bool) else clean_int(val, default)


def _opt_int(val: object) -> int | None:
    return val if isinstance(val, int) and not isinstance(val, bool) else None


def _str(val: object) -> str:
    return val if isinstance(val, str) else ""


def _int_map(val: object) -> Mapping[str, int]:
    return val if isinstance(val, Mapping) else {}


def add_economy_edges(
    u: NodeMeta,
    v: NodeMeta,
    *,
    v_energy_delta: int,
    warn_ratio: float,
    add_edge: AddEdgeFn,
) -> None:
    u_idx, v_idx = _int(u["idx"]), _int(v["idx"])
    u_kind, v_kind = _str(u["kind"]), _str(v["kind"])
    u_pid, v_pid = _int(u["planet_id"]), _int(v["planet_id"])
    u_bid, v_bid, u_rkey, u_sid = _opt_int(u["bid"]), _opt_int(v["bid"]), u["rkey"], _opt_int(u["sid"])
    u_tlvl, v_tlvl = _opt_int(u["target_level"]), _opt_int(v["target_level"])
    v_name, u_reason_low, v_reason_low = _str(v["target_name"]), _str(u["reason"]).lower(), _str(v["reason"]).lower()
    v_pobj = v["planet_obj"] if isinstance(v["planet_obj"], PlanetState) else None
    v_missing = _int_map(v["missing"])
    v_raw_cost = _int_map(v["raw_cost"])

    is_power = (
        (u_kind in ("BUILD", "WAIT_RESOURCES") and u_bid in (5, 6) and u_pid == v_pid)
        or (u_kind == "BUILD_SHIP" and u_sid == 9 and u_pid == v_pid)
        or (u_kind == "RESEARCH" and u_rkey == "r2")
    )
    if is_power and v_kind in ("BUILD", "WAIT_RESOURCES") and v_energy_delta < 0:
        p_net = clean_int(v_pobj.energy.net, 0) if v_pobj is not None else 0
        if (
            p_net < 0
            or (p_net + v_energy_delta) < 0
            or any(tok in u_reason_low or tok in v_reason_low for tok in ("power", "energy"))
        ):
            lvl_suffix = f" L{v_tlvl}" if v_tlvl is not None else ""
            add_edge(u_idx, v_idx, "energy_support", f"Supplies energy for {v_name}{lvl_suffix}")

    if u_kind == "THROTTLE_BUILDING" and u_pid == v_pid:
        if u_tlvl == 0 and (
            v_missing.get("pig_iron", 0) > 0
            or v["eta_bottleneck"] == "pig_iron"
            or (v_kind in ("BUILD", "WAIT_RESOURCES") and v_bid in (1, 5) and "pig iron" in u_reason_low)
        ):
            add_edge(
                u_idx, v_idx, "throttle_control", f"Pauses Blast furnace (0%) to conserve 2:1 Pig iron for {v_name}"
            )
        elif (
            u_tlvl is not None
            and u_tlvl > 0
            and (
                v_missing.get("metal", 0) > 0
                or v["eta_bottleneck"] == "metal"
                or (v_kind in ("BUILD", "WAIT_RESOURCES") and v_bid == 2)
            )
        ):
            add_edge(u_idx, v_idx, "throttle_control", f"Resumes Blast furnace ({u_tlvl}%) to smelt Metal for {v_name}")

    if u_kind in ("BUILD", "WAIT_RESOURCES") and u_bid in WAREHOUSE_RESOURCE_BY_BID and u_pid == v_pid:
        wh_res = WAREHOUSE_RESOURCE_BY_BID[u_bid]
        raw_sto_cap = max(0, safe_int(getattr(v_pobj.storage, wh_res, 0), 0)) if v_pobj is not None else 0
        fill_ratio = clean_float(v_pobj.storage_fill_ratios().get(wh_res, 0.0), 0.0) if v_pobj is not None else 0.0
        exceeds_cap = raw_sto_cap > 0 and v_raw_cost.get(wh_res, 0) > raw_sto_cap
        named_in_reason = bool(v_name) and v_name.lower() in u_reason_low
        overflow_unblocks = fill_ratio >= warn_ratio and (
            (v_kind in ("BUILD", "WAIT_RESOURCES") and MINE_RESOURCE_BY_BID.get(v_bid or 0) == wh_res)
            or v_missing.get(wh_res, 0) > 0
        )
        if exceeds_cap or named_in_reason or overflow_unblocks:
            add_edge(u_idx, v_idx, "storage_cap", f"Expands {RESOURCE_LABELS[wh_res]} warehouse capacity for {v_name}")

    if u_kind in ("BUILD", "WAIT_RESOURCES") and u_bid in MINE_RESOURCE_BY_BID and u_pid == v_pid:
        prod_res = MINE_RESOURCE_BY_BID[u_bid]
        if u_bid == 1 and v_kind in ("BUILD", "WAIT_RESOURCES") and v_bid == 2:
            add_edge(u_idx, v_idx, "resource_feed", "Supplies 2:1 Pig iron feedstock for Blast furnace")
        elif v_missing.get(prod_res, 0) > 0 or v["eta_bottleneck"] == prod_res:
            add_edge(u_idx, v_idx, "resource_feed", f"Boosts {RESOURCE_LABELS[prod_res]} output for {v_name}")
    elif u_kind == "RESEARCH" and u_rkey == "r1":
        for boosted_rk in ("pig_iron", "kryptonite", "spice"):
            if v_missing.get(boosted_rk, 0) > 0 or v["eta_bottleneck"] == boosted_rk:
                add_edge(
                    u_idx,
                    v_idx,
                    "resource_feed",
                    f"Mining technology (+2%/lvl) boosts {RESOURCE_LABELS[boosted_rk]} for {v_name}",
                )
                break

    if u_kind in ("CLAIM_TUTORIAL", "ATTACK"):
        if (any(val > 0 for val in v_missing.values()) or v_kind == "WAIT_RESOURCES") and (
            u_kind == "CLAIM_TUTORIAL" or u_pid == v_pid
        ):
            lbl = f"Tutorial reward funds {v_name}" if u_kind == "CLAIM_TUTORIAL" else f"Raid plunder funds {v_name}"
            add_edge(u_idx, v_idx, "loot_feed", lbl)
