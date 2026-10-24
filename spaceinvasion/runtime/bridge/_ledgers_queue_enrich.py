"""In-flight fleet queue cargo enrichment (`expected_cargo`, `delivered_cargo`, `delivered`)."""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import replace

from spaceinvasion.combat.cargo import fleet_cargo_capacity
from spaceinvasion.core.coords import normalize_coords
from spaceinvasion.core.model import GameState, QueueJob, Resources, is_in_flight_fleet_job
from spaceinvasion.db.storage import GameDatabase
from spaceinvasion.runtime.bridge._context import BridgeContext
from spaceinvasion.runtime.bridge._helpers import safe_api_int
from spaceinvasion.runtime.bridge._helpers_queue import (
    cap_resources_by_capacity,
    extract_coords_from_text,
    parse_ships_from_queue_detail,
)
from spaceinvasion.runtime.bridge._ledgers_transport import (
    is_returning_fleet,
    is_transport_name,
    match_recent_transport_dispatch,
)
from spaceinvasion.runtime.bridge._ledgers_transport_util import observe_transport_utilization


def _reserve_outbound_dispatch_indices(ctx: BridgeContext, queues: Sequence[QueueJob]) -> set[int]:
    used_idxs: set[int] = set()
    if not ctx.recent_transport_dispatches:
        return used_idxs
    for q0 in queues:
        if not is_in_flight_fleet_job(q0):
            continue
        n0 = str(q0.name or "")
        d0 = str(q0.detail or "")
        if "transport" not in n0.lower() or is_returning_fleet(n0, d0.lower()):
            continue
        c0 = q0.cargo
        if not (isinstance(c0, Resources) and not c0.is_zero()):
            continue
        o0 = normalize_coords(str(q0.origin_coords or ""))
        t0 = extract_coords_from_text(n0)
        if not t0:
            continue
        c0_norm = GameDatabase._normalize_cargo_dict(c0)
        for idx in range(len(ctx.recent_transport_dispatches) - 1, -1, -1):
            if idx in used_idxs:
                continue
            ent = ctx.recent_transport_dispatches[idx]
            if ent.get("dest_coords") != t0:
                continue
            if o0 and ent.get("origin_coords") and ent.get("origin_coords") != o0:
                continue
            if GameDatabase._normalize_cargo_dict(ent.get("cargo")) == c0_norm:
                used_idxs.add(idx)
                break
    return used_idxs


def _compute_expected_attack_cargo(
    trow: Mapping[str, object] | None,
    det_s: str,
    miner_lvl: int,
) -> Resources | None:
    if not isinstance(trow, Mapping):
        return None
    raw_res = trow.get("resources")
    res_src: Mapping[str, object] = raw_res if isinstance(raw_res, Mapping) else trow
    plunder = Resources(
        pig_iron=max(0, safe_api_int(res_src.get("pig_iron", 0), 0)) // 2,
        metal=max(0, safe_api_int(res_src.get("metal", 0), 0)) // 2,
        kryptonite=max(0, safe_api_int(res_src.get("kryptonite", 0), 0)) // 2,
        spice=max(0, safe_api_int(res_src.get("spice", 0), 0)) // 2,
    )
    if plunder.is_zero():
        return None
    ships_map = parse_ships_from_queue_detail(det_s)
    cap = fleet_cargo_capacity(ships_map, miner_spec_level=miner_lvl) if ships_map else None
    capped = cap_resources_by_capacity(plunder, cap, mode="value")
    return None if capped.is_zero() else capped


def _compute_expected_recycle_cargo(
    ctx: BridgeContext,
    raid_by_coords: Mapping[str, Mapping[str, object]],
    dest_coords: str,
    det_s: str,
    miner_lvl: int,
) -> Resources | None:
    deb_map = ctx.galaxy_debris_amounts.get(dest_coords)
    dp = dm = dk = ds = 0
    if isinstance(deb_map, Mapping):
        dp = max(0, safe_api_int(deb_map.get("pig_iron", 0), 0))
        dm = max(0, safe_api_int(deb_map.get("metal", 0), 0))
        dk = max(0, safe_api_int(deb_map.get("kryptonite", 0), 0))
        ds = max(0, safe_api_int(deb_map.get("spice", 0), 0))
    if dp + dm + dk + ds <= 0:
        trow = raid_by_coords.get(dest_coords)
        if isinstance(trow, Mapping):
            raw_deb = trow.get("debris")
            if isinstance(raw_deb, Mapping):
                dp = max(0, safe_api_int(raw_deb.get("pig_iron", 0), 0))
                dm = max(0, safe_api_int(raw_deb.get("metal", 0), 0))
                dk = max(0, safe_api_int(raw_deb.get("kryptonite", 0), 0))
                ds = max(0, safe_api_int(raw_deb.get("spice", 0), 0))
            else:
                dp = max(0, safe_api_int(trow.get("debris_pig_iron", 0), 0))
                dm = max(0, safe_api_int(trow.get("debris_metal", 0), 0))
                dk = max(0, safe_api_int(trow.get("debris_kryptonite", 0), 0))
                ds = max(0, safe_api_int(trow.get("debris_spice", 0), 0))
    debris_res = Resources(pig_iron=dp, metal=dm, kryptonite=dk, spice=ds)
    if debris_res.is_zero():
        return None
    ships_map = parse_ships_from_queue_detail(det_s)
    if ships_map.get("s11", 0) > 0:
        cap: int | None = fleet_cargo_capacity({"s11": ships_map["s11"]}, miner_spec_level=miner_lvl)
    elif ships_map:
        cap = fleet_cargo_capacity(ships_map, miner_spec_level=miner_lvl)
    else:
        cap = None
    capped = cap_resources_by_capacity(debris_res, cap, mode="proportional")
    return None if capped.is_zero() else capped


def _enrich_single_fleet_job(
    ctx: BridgeContext,
    q: QueueJob,
    *,
    raid_by_coords: Mapping[str, Mapping[str, object]],
    miner_lvl: int,
    used_dispatch_idxs: set[int],
) -> tuple[QueueJob, bool]:
    name_s = str(q.name or "")
    det_s = str(q.detail or "")
    name_l = name_s.lower()
    is_ret = is_returning_fleet(name_s, det_s.lower())
    is_attack = name_l.startswith("attack") or "attack →" in name_l or "attack ↩" in name_l or "attack ->" in name_l
    is_recycle = any(tok in name_l for tok in ("recycl", "rubble", "debris", "harvest", "abbau", "trümmer", "truemmer"))
    is_transport = is_transport_name(name_l, include_return=True)
    orig_coords = normalize_coords(str(q.origin_coords or ""))
    dest_coords = extract_coords_from_text(name_s)
    ships_part = det_s.split("·", 1)[1].strip() if "·" in det_s else det_s

    exp_cargo = q.expected_cargo
    del_cargo = q.delivered_cargo
    delivered = bool(q.delivered)

    if is_attack and not is_ret and exp_cargo is None and dest_coords:
        exp_cargo = _compute_expected_attack_cargo(raid_by_coords.get(dest_coords), det_s, miner_lvl)
    elif is_recycle and not is_ret and exp_cargo is None and dest_coords:
        exp_cargo = _compute_expected_recycle_cargo(ctx, raid_by_coords, dest_coords, det_s, miner_lvl)
    elif is_transport and not is_ret:
        if exp_cargo is None or exp_cargo.is_zero():
            if q.cargo is not None and not q.cargo.is_zero():
                exp_cargo = q.cargo
            else:
                matched = match_recent_transport_dispatch(
                    ctx, orig_coords, dest_coords, ships_detail=ships_part, used_indices=used_dispatch_idxs
                )
                if matched is not None:
                    used_dispatch_idxs.add(matched[0])
                    exp_cargo = matched[1]
    elif is_transport and is_ret:
        delivered = True
        if del_cargo is None:
            matched = match_recent_transport_dispatch(
                ctx, orig_coords, dest_coords, ships_detail=ships_part, used_indices=used_dispatch_idxs
            )
            if matched is not None:
                used_dispatch_idxs.add(matched[0])
                del_cargo = matched[1]

    if exp_cargo != q.expected_cargo or del_cargo != q.delivered_cargo or delivered != bool(q.delivered):
        return replace(q, expected_cargo=exp_cargo, delivered_cargo=del_cargo, delivered=delivered), True
    return q, False


def enrich_active_queues(
    ctx: BridgeContext,
    state: GameState,
    *,
    on_persist_runtime_state: Callable[[], None] | None = None,
) -> GameState:
    """Enrich ongoing fleet `QueueJob` entries with expected/delivered cargo and observe transport utilization."""
    queues = state.active_queues
    if not queues or not any(is_in_flight_fleet_job(q) for q in queues):
        return state
    raid_by_coords: dict[str, Mapping[str, object]] = {}
    for src_rows in (ctx.raid_targets, state.raid_targets):
        for row in src_rows:
            if isinstance(row, Mapping):
                nc = normalize_coords(str(row.get("coords") or row.get("target_coords") or ""))
                if nc:
                    raid_by_coords[nc] = row
    miner_lvl = safe_api_int(state.specs.get("miner", 0), 0)
    used_dispatch_idxs = _reserve_outbound_dispatch_indices(ctx, queues)
    changed = False
    enriched_jobs: list[QueueJob] = []
    for q in queues:
        if not is_in_flight_fleet_job(q):
            enriched_jobs.append(q)
            continue
        upd_q, did = _enrich_single_fleet_job(
            ctx, q, raid_by_coords=raid_by_coords, miner_lvl=miner_lvl, used_dispatch_idxs=used_dispatch_idxs
        )
        changed = changed or did
        enriched_jobs.append(upd_q)
    out_state = replace(state, active_queues=tuple(enriched_jobs)) if changed else state
    if observe_transport_utilization(ctx, out_state) and ctx.db is not None and on_persist_runtime_state is not None:
        on_persist_runtime_state()
    return out_state
