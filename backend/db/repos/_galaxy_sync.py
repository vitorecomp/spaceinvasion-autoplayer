"""Batched galaxy slot upsert, history, and `spy_targets` / debris sync helpers."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from sqlalchemy import case, func, or_, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from backend.db.models import GalaxySlot, GalaxySlotHistory, SpyTarget
from backend.db.repos._helpers import clamp_int64, normalize_coords, parse_coords_gsp, sanitize_text, to_int
from backend.db.repos._intel_helpers import coalesce_legacy_spy_target_coords
from backend.db.repos.settings import SettingsRepository


@dataclass(frozen=True)
class GalaxySlotInput:
    """Typed input row for batched galaxy slot and system scan ingestion."""

    coords: str
    position: int = 0
    planet_name: str = ""
    owner: str = ""
    status_color: str = "free"
    is_spacia: bool = False
    has_debris: bool = False
    has_asteroid: bool = False
    uninhabited_fields: int | None = None
    colonize_min_fields: int | None = None
    colonize_max_fields: int | None = None
    temperature: int | None = None
    debris_pig_iron: int = 0
    debris_metal: int = 0
    debris_kryptonite: int = 0
    debris_spice: int = 0


def _prepare_slot_rows(
    slots: Sequence[GalaxySlotInput],
    now_ts: float,
) -> tuple[list[dict[str, object]], list[dict[str, object]]]:
    slot_by_coords: dict[str, dict[str, object]] = {}
    hist_rows: list[dict[str, object]] = []
    for sl in slots:
        nc = normalize_coords(sl.coords)
        if not nc:
            continue
        gsp = parse_coords_gsp(nc)
        gal_i = gsp[0] if gsp is not None else 0
        sys_i = gsp[1] if gsp is not None else 0
        pos_i = gsp[2] if gsp is not None else clamp_int64(int(sl.position))
        scolor = sanitize_text(sl.status_color or "free")
        is_sp = 1 if sl.is_spacia else 0
        is_in = 1 if (scolor == "yellow" or is_sp == 1) else 0
        has_deb = 1 if sl.has_debris else 0
        has_ast = 1 if sl.has_asteroid else 0
        cmin = (
            clamp_int64(int(sl.colonize_min_fields))
            if (scolor == "free" and sl.colonize_min_fields is not None)
            else None
        )
        cmax = (
            clamp_int64(int(sl.colonize_max_fields))
            if (scolor == "free" and sl.colonize_max_fields is not None)
            else None
        )
        temp = clamp_int64(int(sl.temperature)) if sl.temperature is not None else None
        pname = sanitize_text(sl.planet_name)
        owner = sanitize_text(sl.owner)
        row_common: dict[str, object] = {
            "coords": nc,
            "galaxy": gal_i,
            "system": sys_i,
            "position": pos_i,
            "planet_name": pname,
            "owner": owner,
            "status_color": scolor,
            "is_inactive": is_in,
            "is_spacia": is_sp,
            "has_debris": has_deb,
            "colonize_min_fields": cmin,
            "colonize_max_fields": cmax,
            "temperature": temp,
        }
        slot_by_coords[nc] = {**row_common, "last_scanned_ts": now_ts}
        hist_rows.append({**row_common, "ts": now_ts, "has_asteroid": has_ast})
    return list(slot_by_coords.values()), hist_rows


def batch_upsert_slots_and_history(
    session: Session,
    slots: Sequence[GalaxySlotInput],
    now_ts: float,
    *,
    append_slot_history: bool,
    only_changed_slot_history: bool,
) -> tuple[list[str], set[tuple[int, int]]]:
    """Batch-upsert `galaxy_slots` and append `galaxy_slot_history` in <= 3 SQL statements."""
    slot_rows, hist_rows = _prepare_slot_rows(slots, now_ts)
    if not slot_rows:
        return [], set()

    coords_list = [str(r["coords"]) for r in slot_rows]
    if append_slot_history and only_changed_slot_history:
        existing_rows = session.execute(
            select(
                GalaxySlot.coords,
                GalaxySlot.planet_name,
                GalaxySlot.owner,
                GalaxySlot.status_color,
                GalaxySlot.is_spacia,
                GalaxySlot.has_debris,
            ).where(GalaxySlot.coords.in_(coords_list))
        ).all()
        existing_sig = {str(r[0]): (str(r[1]), str(r[2]), str(r[3]), int(r[4]), int(r[5])) for r in existing_rows}
        hist_rows = [
            hr
            for hr in hist_rows
            if existing_sig.get(str(hr["coords"]))
            != (
                str(hr["planet_name"]),
                str(hr["owner"]),
                str(hr["status_color"]),
                to_int(hr["is_spacia"]),
                to_int(hr["has_debris"]),
            )
        ]

    stmt = pg_insert(GalaxySlot).values(slot_rows)
    session.execute(
        stmt.on_conflict_do_update(
            index_elements=[GalaxySlot.coords],
            set_={
                "galaxy": stmt.excluded.galaxy,
                "system": stmt.excluded.system,
                "position": stmt.excluded.position,
                "planet_name": stmt.excluded.planet_name,
                "owner": stmt.excluded.owner,
                "status_color": stmt.excluded.status_color,
                "is_inactive": stmt.excluded.is_inactive,
                "is_spacia": stmt.excluded.is_spacia,
                "has_debris": stmt.excluded.has_debris,
                "last_scanned_ts": stmt.excluded.last_scanned_ts,
                "colonize_min_fields": case(
                    (stmt.excluded.status_color != "free", None),
                    else_=func.coalesce(stmt.excluded.colonize_min_fields, GalaxySlot.colonize_min_fields),
                ),
                "colonize_max_fields": case(
                    (stmt.excluded.status_color != "free", None),
                    else_=func.coalesce(stmt.excluded.colonize_max_fields, GalaxySlot.colonize_max_fields),
                ),
                "temperature": func.coalesce(stmt.excluded.temperature, GalaxySlot.temperature),
            },
        )
    )
    if append_slot_history and hist_rows:
        session.execute(pg_insert(GalaxySlotHistory).values(hist_rows))

    sectors: set[tuple[int, int]] = set()
    for r in slot_rows:
        g_val = to_int(r["galaxy"], 0)
        s_val = to_int(r["system"], 0)
        if 1 <= g_val <= 14 and 1 <= s_val <= 400:
            sectors.add((g_val, s_val))
    return coords_list, sectors


def _upsert_probed_free_targets(session: Session, rows: list[dict[str, object]]) -> None:
    p_stmt = pg_insert(SpyTarget).values(rows)
    cant_atk = or_(
        func.lower(SpyTarget.block_reason).like("%can't be attacked%"),
        func.lower(SpyTarget.block_reason).like("%cannot be attacked%"),
    )
    session.execute(
        p_stmt.on_conflict_do_update(
            index_elements=[SpyTarget.coords],
            set_={
                "owner": "",
                "planet_name": "",
                "status_color": "free",
                "is_inactive": 0,
                "is_spacia": 0,
                "has_debris": 0,
                "pig_iron": 0,
                "metal": 0,
                "kryptonite": 0,
                "spice": 0,
                "fleet_json": "{}",
                "defense_json": "{}",
                "dug_in": 0,
                "last_scanned_ts": p_stmt.excluded.last_scanned_ts,
                "uninhabited_fields": func.coalesce(p_stmt.excluded.uninhabited_fields, SpyTarget.uninhabited_fields),
                "colonize_min_fields": func.coalesce(
                    p_stmt.excluded.colonize_min_fields, SpyTarget.colonize_min_fields
                ),
                "colonize_max_fields": func.coalesce(
                    p_stmt.excluded.colonize_max_fields, SpyTarget.colonize_max_fields
                ),
                "temperature": func.coalesce(p_stmt.excluded.temperature, SpyTarget.temperature),
                "blocked_until_ts": case((cant_atk, 0.0), else_=SpyTarget.blocked_until_ts),
                "block_reason": case((cant_atk, ""), else_=SpyTarget.block_reason),
            },
            where=SpyTarget.is_own_planet == 0,
        )
    )


def _upsert_occupied_targets(session: Session, rows: list[dict[str, object]]) -> None:
    o_stmt = pg_insert(SpyTarget).values(rows)
    lost_spacia = (SpyTarget.is_spacia == 1) & (o_stmt.excluded.is_spacia == 0)
    reset_res = (o_stmt.excluded.status_color == "free") | lost_spacia
    session.execute(
        o_stmt.on_conflict_do_update(
            index_elements=[SpyTarget.coords],
            set_={
                "merged_into": "",
                "owner": o_stmt.excluded.owner,
                "planet_name": o_stmt.excluded.planet_name,
                "status_color": o_stmt.excluded.status_color,
                "is_inactive": o_stmt.excluded.is_inactive,
                "is_spacia": o_stmt.excluded.is_spacia,
                "has_debris": o_stmt.excluded.has_debris,
                "pig_iron": case((reset_res, 0), else_=SpyTarget.pig_iron),
                "metal": case((reset_res, 0), else_=SpyTarget.metal),
                "kryptonite": case((reset_res, 0), else_=SpyTarget.kryptonite),
                "spice": case((reset_res, 0), else_=SpyTarget.spice),
                "fleet_json": case((reset_res, o_stmt.excluded.fleet_json), else_=SpyTarget.fleet_json),
                "defense_json": case((reset_res, o_stmt.excluded.defense_json), else_=SpyTarget.defense_json),
                "dug_in": case((reset_res, 0), else_=SpyTarget.dug_in),
                "last_spied_ts": case((lost_spacia, 0.0), else_=SpyTarget.last_spied_ts),
                "last_scanned_ts": o_stmt.excluded.last_scanned_ts,
            },
        )
    )


def _reset_free_targets(session: Session, coords: Sequence[str], now_ts: float) -> None:
    session.execute(
        update(SpyTarget)
        .where(SpyTarget.coords.in_(coords), SpyTarget.is_own_planet == 0)
        .values(
            owner="",
            planet_name="",
            status_color="free",
            is_inactive=0,
            is_spacia=0,
            has_debris=0,
            pig_iron=0,
            metal=0,
            kryptonite=0,
            spice=0,
            fleet_json="{}",
            defense_json="{}",
            dug_in=0,
            last_scanned_ts=now_ts,
        )
    )


def _sync_debris_meta(
    session: Session,
    debris_updates: dict[str, list[object] | None],
    now_ts: float,
) -> None:
    settings = SettingsRepository(session)
    raw_map = settings.get_meta_json("galaxy_debris_amounts")
    deb_map: dict[str, object] = dict(raw_map) if isinstance(raw_map, dict) else {}
    changed = False
    for dc, dval in debris_updates.items():
        if dval is None:
            if dc in deb_map:
                deb_map.pop(dc, None)
                changed = True
        elif deb_map.get(dc) != dval:
            deb_map[dc] = dval
            changed = True
    if changed:
        settings.upsert_meta_json("galaxy_debris_amounts", deb_map, ts=now_ts)


def sync_slots_to_spy_targets_and_debris(
    session: Session,
    slots: Sequence[GalaxySlotInput],
    now_ts: float,
) -> int:
    """Batch-sync scanned slots into `spy_targets` and `meta['galaxy_debris_amounts']`."""
    probed_free: dict[str, dict[str, object]] = {}
    reset_free_coords: list[str] = []
    occupied_rows: dict[str, dict[str, object]] = {}
    debris_updates: dict[str, list[object] | None] = {}
    saved_occupied = 0

    for sl in slots:
        nc = normalize_coords(sl.coords)
        if not nc:
            continue
        has_deb = bool(sl.has_debris)
        if has_deb:
            dp = max(0, clamp_int64(int(sl.debris_pig_iron)))
            dm = max(0, clamp_int64(int(sl.debris_metal)))
            dk = max(0, clamp_int64(int(sl.debris_kryptonite)))
            ds = max(0, clamp_int64(int(sl.debris_spice)))
            if dp + dm + dk + ds > 0:
                debris_updates[nc] = [dp, dm, dk, ds, now_ts]
        else:
            debris_updates[nc] = None

        scolor = sanitize_text(sl.status_color or "free")
        if scolor == "free" and not has_deb:
            if sl.uninhabited_fields is not None or sl.colonize_max_fields is not None:
                probed_free[nc] = {
                    "coords": nc,
                    "owner": "",
                    "planet_name": "",
                    "status_color": "free",
                    "is_inactive": 0,
                    "is_spacia": 0,
                    "has_debris": 0,
                    "last_scanned_ts": now_ts,
                    "uninhabited_fields": clamp_int64(int(sl.uninhabited_fields))
                    if sl.uninhabited_fields is not None
                    else None,
                    "colonize_min_fields": clamp_int64(int(sl.colonize_min_fields))
                    if sl.colonize_min_fields is not None
                    else None,
                    "colonize_max_fields": clamp_int64(int(sl.colonize_max_fields))
                    if sl.colonize_max_fields is not None
                    else None,
                    "temperature": clamp_int64(int(sl.temperature)) if sl.temperature is not None else None,
                    "merged_into": "",
                }
            else:
                reset_free_coords.append(nc)
            continue

        occupied_rows[nc] = {
            "coords": nc,
            "owner": sanitize_text(sl.owner),
            "planet_name": sanitize_text(sl.planet_name),
            "status_color": scolor,
            "is_inactive": 1 if (scolor == "yellow" or sl.is_spacia) else 0,
            "is_spacia": 1 if sl.is_spacia else 0,
            "has_debris": 1 if has_deb else 0,
            "fleet_json": "{}",
            "defense_json": "{}",
            "last_scanned_ts": now_ts,
            "merged_into": "",
        }
        saved_occupied += 1

    all_sync_coords = list(probed_free.keys()) + reset_free_coords + list(occupied_rows.keys())
    if all_sync_coords:
        coalesce_legacy_spy_target_coords(session, all_sync_coords)
    if probed_free:
        _upsert_probed_free_targets(session, list(probed_free.values()))
    if reset_free_coords:
        _reset_free_targets(session, reset_free_coords, now_ts)
    if occupied_rows:
        _upsert_occupied_targets(session, list(occupied_rows.values()))
    if debris_updates:
        _sync_debris_meta(session, debris_updates, now_ts)
    return saved_occupied


__all__ = ["GalaxySlotInput", "batch_upsert_slots_and_history", "sync_slots_to_spy_targets_and_debris"]
