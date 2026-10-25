"""Transport dispatch ledger and recent-dispatch matching helpers."""

from __future__ import annotations

from collections.abc import Mapping
import time

from spaceinvasion.core.coords import normalize_coords
from spaceinvasion.core.model import GameState, Resources, is_in_flight_fleet_job
from spaceinvasion.runtime.bridge._context import BridgeContext
from spaceinvasion.runtime.bridge._db_gateway_telemetry import _normalize_cargo_dict
from spaceinvasion.runtime.bridge._helpers import bounded_api_str, safe_api_float, safe_api_int
from spaceinvasion.runtime.bridge._helpers_queue import extract_coords_from_text


def is_returning_fleet(name_s: str, det_l: str) -> bool:
    """Return True when a fleet job label/detail indicates the returning leg."""
    return ("returning" in det_l) or ("↩" in name_s) or ("rück" in det_l) or ("rueck" in det_l)


def is_transport_name(name_l: str, *, include_return: bool = True) -> bool:
    """Return True when a fleet job label identifies a transport mission."""
    if name_l.startswith("transport") or "transport →" in name_l or "transport ->" in name_l:
        return True
    return include_return and "transport ↩" in name_l


def record_transport_dispatch(
    ctx: BridgeContext,
    *,
    origin_coords: str,
    dest_coords: str,
    cargo: object,
    ships: object = "",
    source: str = "dispatch",
    ts: float | None = None,
) -> None:
    """Record an outbound transport dispatch (with fast dedupe) to DB or in-memory ring."""
    orig = normalize_coords(origin_coords)
    dest = normalize_coords(dest_coords)
    if not orig or not dest:
        return
    cargo_d = _normalize_cargo_dict(cargo)
    if sum(cargo_d.values()) <= 0:
        return
    now_ts = round(safe_api_float(ts, time.time()) if ts is not None else time.time(), 3)
    ships_s = bounded_api_str(ships, default="")[:140] if ships else ""
    src_s = str(source or "dispatch").strip().lower()[:32] or "dispatch"

    dedupe_window = 7200.0 if src_s == "observed_outbound" else 5.0
    for prev in reversed(ctx.recent_transport_dispatches):
        if (now_ts - safe_api_float(prev.get("ts"), 0.0)) > dedupe_window:
            break
        if (
            prev.get("origin_coords") == orig
            and prev.get("dest_coords") == dest
            and prev.get("cargo") == cargo_d
            and (src_s == "observed_outbound" or prev.get("source") == src_s)
        ):
            if ships_s and not prev.get("ships"):
                prev["ships"] = ships_s
                if ctx.db is not None:
                    ctx.recent_transport_dispatches = [
                        dict(r)
                        for r in ctx.db.record_transport_dispatch(
                            origin_coords=orig,
                            dest_coords=dest,
                            cargo=cargo_d,
                            ships=ships_s,
                            source=src_s,
                            ts=now_ts,
                        )
                    ]
            return

    if ctx.db is not None:
        ctx.recent_transport_dispatches = [
            dict(r)
            for r in ctx.db.record_transport_dispatch(
                origin_coords=orig,
                dest_coords=dest,
                cargo=cargo_d,
                ships=ships_s,
                source=src_s,
                ts=now_ts,
            )
        ]
    else:
        ctx.recent_transport_dispatches.append(
            {
                "origin_coords": orig,
                "dest_coords": dest,
                "cargo": cargo_d,
                "ships": ships_s,
                "ts": now_ts,
                "source": src_s,
            }
        )
        ctx.recent_transport_dispatches = ctx.recent_transport_dispatches[-120:]


def capture_observed_outbound_transports(ctx: BridgeContext, state: GameState) -> None:
    """Remember cargo of live outbound `TRANSPORT` flights so returning legs retain `DELIVERED` values."""
    for q in state.active_queues:
        if not is_in_flight_fleet_job(q):
            continue
        name_s = str(q.name or "")
        det_s = str(q.detail or "")
        if is_returning_fleet(name_s, det_s.lower()) or not is_transport_name(name_s.lower(), include_return=False):
            continue
        cargo_obj = q.cargo or q.expected_cargo
        if not isinstance(cargo_obj, Resources) or cargo_obj.is_zero():
            continue
        orig = normalize_coords(str(q.origin_coords or ""))
        dest = extract_coords_from_text(name_s)
        if not orig or not dest:
            continue
        ships_part = det_s.split("·", 1)[1].strip() if "·" in det_s else ""
        record_transport_dispatch(
            ctx, origin_coords=orig, dest_coords=dest, cargo=cargo_obj, ships=ships_part, source="observed_outbound"
        )


def _entry_to_resources(ent: Mapping[str, object]) -> Resources | None:
    cargo_raw = ent.get("cargo")
    if not isinstance(cargo_raw, Mapping):
        return None
    res = Resources(
        pig_iron=max(0, safe_api_int(cargo_raw.get("pig_iron", 0), 0)),
        metal=max(0, safe_api_int(cargo_raw.get("metal", 0), 0)),
        kryptonite=max(0, safe_api_int(cargo_raw.get("kryptonite", 0), 0)),
        spice=max(0, safe_api_int(cargo_raw.get("spice", 0), 0)),
    )
    return None if res.is_zero() else res


def match_recent_transport_dispatch(
    ctx: BridgeContext,
    origin_coords: str,
    dest_coords: str,
    *,
    ships_detail: str = "",
    used_indices: set[int] | None = None,
    max_age_seconds: float = 86400.0,
) -> tuple[int, Resources] | None:
    """Match an in-flight transport leg against recent dispatch records in priority order."""
    if not ctx.recent_transport_dispatches:
        return None
    orig = normalize_coords(origin_coords)
    dest = normalize_coords(dest_coords)
    if not dest and not orig:
        return None
    now_ts = time.time()
    ships_norm = " ".join(str(ships_detail or "").strip().lower().split())
    used = used_indices if used_indices is not None else set()

    eligible: list[tuple[int, dict[str, object]]] = []
    for idx in range(len(ctx.recent_transport_dispatches) - 1, -1, -1):
        if idx in used:
            continue
        ent = ctx.recent_transport_dispatches[idx]
        ent_ts = safe_api_float(ent.get("ts"), 0.0)
        if ent_ts > 0.0 and (now_ts - ent_ts) > max_age_seconds:
            continue
        eligible.append((idx, ent))
    if not eligible:
        return None

    if orig and dest and ships_norm:
        for idx, ent in eligible:
            if ent.get("origin_coords") == orig and ent.get("dest_coords") == dest:
                es = " ".join(str(ent.get("ships") or "").strip().lower().split())
                if es and (es == ships_norm or es in ships_norm or ships_norm in es):
                    res = _entry_to_resources(ent)
                    if res is not None:
                        return idx, res
    if orig and dest:
        for idx, ent in eligible:
            if ent.get("origin_coords") == orig and ent.get("dest_coords") == dest:
                res = _entry_to_resources(ent)
                if res is not None:
                    return idx, res
        for idx, ent in eligible:
            if ent.get("origin_coords") == dest and ent.get("dest_coords") == orig:
                res = _entry_to_resources(ent)
                if res is not None:
                    return idx, res
    if dest and not orig:
        for idx, ent in eligible:
            if ent.get("dest_coords") == dest:
                res = _entry_to_resources(ent)
                if res is not None:
                    return idx, res
    return None
