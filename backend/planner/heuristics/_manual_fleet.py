"""Manual queue fleet-mission item builders (`SPY`, `TRANSPORT`, `STATION`, `ATTACK`, `RECYCLE`, `COLONIZE`)."""

from __future__ import annotations

from collections.abc import Mapping

from backend.combat.flight import format_station_ships_spec, parse_station_ships_spec
from backend.core import catalog
from backend.core.model import Resources
from backend.planner.heuristics._common import (
    _norm_planet_type,
    _normalize_manual_coords,
    _safe_strategy_int,
    _safe_strategy_str,
)
from backend.planner.heuristics._manual_builders import _ManualBuildResult, _ManualItemContext
from backend.planner.heuristics._types import PlannedAction


def _build_manual_transport_fields(
    ctx: _ManualItemContext,
    item: Mapping[str, object],
    tc: str,
    ships_d: Mapping[str, int],
) -> tuple[int | None, str, int | None, Resources, str, bool]:
    dest_own = ctx.planets_by_coords.get(tc)
    has_trans = sum(max(0, _safe_strategy_int(ships_d.get(k, 0), 0)) for k in ("s1", "s2", "s3")) >= 1
    raw_res = item.get("resources")
    raw_res_q: Mapping[object, object] = raw_res if isinstance(raw_res, Mapping) else {}
    cargo_bundle = Resources(
        pig_iron=max(0, _safe_strategy_int(raw_res_q.get("pig_iron", 0), 0)),
        metal=max(0, _safe_strategy_int(raw_res_q.get("metal", 0), 0)),
        kryptonite=max(0, _safe_strategy_int(raw_res_q.get("kryptonite", 0), 0)),
        spice=max(0, _safe_strategy_int(raw_res_q.get("spice", 0), 0)),
    )
    tgt_id_out = dest_own.planet_id if dest_own is not None else None
    own_ok = dest_own is not None and dest_own.planet_id != ctx.planet.planet_id
    ready = bool(own_ok and has_trans and not cargo_bundle.is_zero())
    t_lbl = f"Transport -> {tc}" + (f" ({dest_own.name})" if dest_own is not None and dest_own.name else "")
    rsn = f"{ctx.base_tag}: Transport to own colony {tc}{ctx.note_suffix}"
    if not own_ok:
        rsn += " (destination must be another owned planet)"
    elif not has_trans:
        rsn += " (needs transport ship s1/s2/s3)"
    elif cargo_bundle.is_zero():
        rsn += " (specify cargo resources)"
    return tgt_id_out, t_lbl, None, cargo_bundle, rsn, ready


def _build_manual_station_fields(
    ctx: _ManualItemContext,
    item: Mapping[str, object],
    tc: str,
    ships_d: Mapping[str, int],
    qty_opt: int | None,
) -> tuple[int | None, str, int | None, Resources, str, bool]:
    dest_own = ctx.planets_by_coords.get(tc)
    raw_ships = item.get("ships")
    raw_ships_q: Mapping[object, object] = raw_ships if isinstance(raw_ships, Mapping) else {}
    parsed_ships_q = {
        catalog.safe_key(k): max(0, _safe_strategy_int(v, 0))
        for k, v in raw_ships_q.items()
        if catalog.safe_key(k) != "s9" and _safe_strategy_int(v, 0) > 0
    }
    if not parsed_ships_q:
        parsed_ships_q = parse_station_ships_spec(f"{_safe_strategy_str(item.get('target_name'))} {ctx.note_s}")
    avail_bundle = {
        k: min(cnt, max(0, _safe_strategy_int(ships_d.get(k, 0), 0)))
        for k, cnt in parsed_ships_q.items()
        if min(cnt, max(0, _safe_strategy_int(ships_d.get(k, 0), 0))) > 0
    }
    spec_str = format_station_ships_spec(avail_bundle or parsed_ships_q)
    qty = sum(avail_bundle.values()) if avail_bundle else (qty_opt or None)
    tgt_id_out = dest_own.planet_id if dest_own is not None else None
    own_ok = (
        dest_own is not None and dest_own.planet_id != ctx.planet.planet_id and _norm_planet_type(dest_own) != "raid"
    )
    ready = bool(own_ok and bool(avail_bundle))
    t_lbl = (
        f"Station -> {tc}"
        + (f" ({dest_own.name})" if dest_own is not None and dest_own.name else "")
        + (f" [{spec_str}]" if spec_str else "")
    )
    rsn = f"{ctx.base_tag}: One-way station ships to own colony {tc}{ctx.note_suffix}"
    if not own_ok:
        rsn += " (destination must be another owned non-raid planet)"
    elif not avail_bundle:
        rsn += " (specify stationed ships to deploy)"
    return tgt_id_out, t_lbl, qty, Resources(), rsn, ready


def _build_manual_fleet_item(
    ctx: _ManualItemContext,
    kind: str,
    item: Mapping[str, object],
) -> _ManualBuildResult:
    tc = _normalize_manual_coords(item.get("target_coords") or item.get("target_name"))
    if not tc:
        return _ManualBuildResult()
    ships_d = ctx.planet.ships or {}
    qty_opt = catalog.safe_optional_int(item.get("target_level"), None)
    cargo_bundle = Resources()
    tgt_id_out: int | None = None

    if kind == "SPY":
        qty: int | None = max(1, qty_opt or 1)
        ready = _safe_strategy_int(ships_d.get("s10", 0), 0) >= 1
        t_lbl = f"{tc} (Spy)"
        rsn = f"{ctx.base_tag}: Dispatch {qty}x Spy probe to {tc}{ctx.note_suffix}" + (
            "" if ready else " (needs Spy probe s10)"
        )
    elif kind == "TRANSPORT":
        tgt_id_out, t_lbl, qty, cargo_bundle, rsn, ready = _build_manual_transport_fields(ctx, item, tc, ships_d)
    elif kind == "STATION":
        tgt_id_out, t_lbl, qty, cargo_bundle, rsn, ready = _build_manual_station_fields(ctx, item, tc, ships_d, qty_opt)
    elif kind == "COLONIZE":
        qty = 1
        ready = _safe_strategy_int(ships_d.get("s8", 0), 0) >= 1
        t_lbl = f"{tc} (Colonize)"
        rsn = f"{ctx.base_tag}: Dispatch Colony ship (s8) to {tc}{ctx.note_suffix}" + (
            "" if ready else " (needs Colony ship s8)"
        )
    elif kind == "RECYCLE":
        owned_rec = _safe_strategy_int(ships_d.get("s11", 0), 0) + _safe_strategy_int(ships_d.get("s111", 0), 0)
        qty = max(1, qty_opt or max(1, owned_rec))
        ready = owned_rec >= 1
        t_lbl = f"{tc} (Debris)"
        rsn = f"{ctx.base_tag}: Dispatch {qty}x Recycler to {tc}{ctx.note_suffix}" + (
            "" if ready else " (needs Recycler s11)"
        )
    else:
        qty = None
        combat_ships = sum(
            max(0, _safe_strategy_int(v, 0)) for k, v in ships_d.items() if k not in ("s8", "s9", "s10", "s11", "s111")
        )
        ready = combat_ships >= 1
        t_lbl = _safe_strategy_str(item.get("target_name")).strip() or tc
        if tc not in t_lbl:
            t_lbl = f"{tc} ({t_lbl})"
        rsn = f"{ctx.base_tag}: Raid target {tc}{ctx.note_suffix}" + (
            "" if ready else " (no stationed raid/transport ships)"
        )

    return _ManualBuildResult(
        action=PlannedAction(
            priority=ctx.prio,
            kind=kind,
            planet_id=ctx.planet.planet_id,
            planet_coords=ctx.planet.coords,
            target_id=tgt_id_out,
            target_name=t_lbl,
            target_level=qty,
            cost=cargo_bundle,
            missing=Resources(),
            reason=rsn,
            executable_now=ready,
            manual_queue_id=ctx.qid,
            manual_sort_rank=ctx.srank,
        )
    )
