"""Startup maintenance for `RuntimeDatabaseGateway`: spy-target repair & inbox report backfill."""

from __future__ import annotations

from collections.abc import Mapping
import re
import time

from spaceinvasion.core.coords import normalize_coords
from spaceinvasion.db.queries import (
    GameMessageBackfillRow,
    GameMessageBackfillUpdate,
    SpyBackfillUpdate,
    apply_game_message_backfill,
    format_backfill_json,
    load_game_messages_for_backfill,
    repair_spy_targets_sql,
)
from spaceinvasion.db.repos.messages import RecycleReportInput
from spaceinvasion.db.repos.raids import BattleReportInput
from spaceinvasion.db.store import DatabaseStore
from spaceinvasion.parsing.inbox import _classify_and_parse_inbox_block, is_non_recycle_fleet_notice_text
from spaceinvasion.parsing.models import InboxMessage
from spaceinvasion.parsing.spy import is_non_spy_notice_text
from spaceinvasion.runtime.bridge._db_gateway_common import _clean_str, _safe_float, _safe_int

_FRACTION_RE = re.compile(r"^\d[\d.]*\s*/\s*\d[\d.]*$")


def _extract_backfill_battle(
    *,
    uid: str,
    srv_id: str,
    dt_s: str,
    norm_c: str,
    clean_btxt: str,
    pdata: Mapping[str, object],
    row_ts: float,
) -> BattleReportInput:
    loot_raw = pdata.get("loot")
    loot_obj: Mapping[str, object] = loot_raw if isinstance(loot_raw, Mapping) else {}
    deb_raw = pdata.get("debris")
    deb_obj: Mapping[str, object] = deb_raw if isinstance(deb_raw, Mapping) else {}
    lseq = pdata.get("loot_spice_eq")
    dseq = pdata.get("debris_spice_eq")
    return BattleReportInput(
        msg_uid=uid,
        target_coords=norm_c,
        server_msg_id=srv_id,
        battle_id=_clean_str(pdata.get("battle_id") or "", 64),
        msg_date_text=dt_s,
        attacker=_clean_str(pdata.get("attacker") or "", 120),
        defender=_clean_str(pdata.get("defender") or "", 120),
        outcome=_clean_str(pdata.get("outcome") or "win", 32),
        rounds=max(0, _safe_int(pdata.get("rounds"), 0)),
        pig_iron=max(0, _safe_int(loot_obj.get("pig_iron"), 0)),
        metal=max(0, _safe_int(loot_obj.get("metal"), 0)),
        kryptonite=max(0, _safe_int(loot_obj.get("kryptonite"), 0)),
        spice=max(0, _safe_int(loot_obj.get("spice"), 0)),
        loot_spice_eq=_safe_float(lseq, 0.0) if lseq is not None else None,
        attacker_lost_spice=max(0, _safe_int(pdata.get("attacker_lost_spice"), 0)),
        defender_lost_spice=max(0, _safe_int(pdata.get("defender_lost_spice"), 0)),
        debris_pig_iron=max(0, _safe_int(deb_obj.get("pig_iron"), 0)),
        debris_metal=max(0, _safe_int(deb_obj.get("metal"), 0)),
        debris_spice_eq=_safe_float(dseq, 0.0) if dseq is not None else None,
        raw_text=clean_btxt,
        ts=row_ts,
    )


def _extract_backfill_recycle(
    *,
    uid: str,
    srv_id: str,
    dt_s: str,
    norm_c: str,
    clean_btxt: str,
    pdata: Mapping[str, object],
    row_ts: float,
) -> RecycleReportInput:
    harv_raw = pdata.get("harvested")
    harv_obj: Mapping[str, object] = harv_raw if isinstance(harv_raw, Mapping) else {}
    avail_raw = pdata.get("debris_available")
    avail_obj: Mapping[str, object] = avail_raw if isinstance(avail_raw, Mapping) else {}
    hseq = pdata.get("harvested_spice_eq")
    return RecycleReportInput(
        msg_uid=uid,
        coords=norm_c,
        server_msg_id=srv_id,
        msg_date_text=dt_s,
        recycler_count=max(0, _safe_int(pdata.get("recycler_count"), 0)),
        cargo_capacity=max(0, _safe_int(pdata.get("cargo_capacity"), 0)),
        debris_avail_pig=max(0, _safe_int(avail_obj.get("pig_iron"), 0)),
        debris_avail_metal=max(0, _safe_int(avail_obj.get("metal"), 0)),
        harvested_pig_iron=max(0, _safe_int(harv_obj.get("pig_iron"), 0)),
        harvested_metal=max(0, _safe_int(harv_obj.get("metal"), 0)),
        harvested_kryptonite=max(0, _safe_int(harv_obj.get("kryptonite"), 0)),
        harvested_spice=max(0, _safe_int(harv_obj.get("spice"), 0)),
        harvested_spice_eq=_safe_float(hseq, 0.0) if hseq is not None else None,
        raw_text=clean_btxt,
        ts=row_ts,
    )


def _extract_backfill_spy(reparsed: InboxMessage, *, srv_id: str, norm_c: str) -> SpyBackfillUpdate | None:
    srep = reparsed.spy_report
    if srep is None or not srv_id:
        return None
    pig = max(0, _safe_int(srep.resources.pig_iron, 0))
    met = max(0, _safe_int(srep.resources.metal, 0))
    kry = max(0, _safe_int(srep.resources.kryptonite, 0))
    spi = max(0, _safe_int(srep.resources.spice, 0))
    if (pig + met + kry + spi) <= 0 and not srep.buildings and srep.uninhabited_fields is None:
        return None
    return SpyBackfillUpdate(
        server_msg_id=srv_id,
        coords=norm_c,
        planet_name=_clean_str(srep.planet_name, 80),
        pig_iron=pig,
        metal=met,
        kryptonite=kry,
        spice=spi,
        buildings_json=format_backfill_json(srep.buildings),
        fleet_json=format_backfill_json(srep.fleet),
        defense_json=format_backfill_json(srep.defense),
        researches_json=format_backfill_json(srep.researches),
        uninhabited_fields=srep.uninhabited_fields,
    )


def _reparse_backfill_row(
    row: GameMessageBackfillRow,
    *,
    own_coords: frozenset[str],
    now_ts: float,
) -> tuple[
    GameMessageBackfillUpdate | None,
    BattleReportInput | None,
    RecycleReportInput | None,
    SpyBackfillUpdate | None,
]:
    btxt_orig = row.body_text.strip()
    if _FRACTION_RE.fullmatch(btxt_orig):
        return None, None, None, None
    raw_html = row.raw_html.strip()
    fallback_html = (
        f"<table><tr><td>{row.msg_date_text}</td><td>{row.sender}</td><td>{row.subject}</td></tr>"
        f"<tr><td colspan='3'>{btxt_orig}{raw_html}</td></tr></table>"
    )
    reparsed = (
        _classify_and_parse_inbox_block(raw_html, folder_type=row.folder_type, exclude_coords=own_coords)
        if raw_html
        else None
    )
    if reparsed is None:
        reparsed = _classify_and_parse_inbox_block(
            fallback_html, folder_type=row.folder_type, exclude_coords=own_coords
        )
    if reparsed is None:
        return None, None, None, None

    row_ts = row.ingested_ts if row.ingested_ts > 0.0 else now_ts
    uid = _clean_str(row.msg_uid, 120)
    srv_id = _clean_str(row.server_msg_id or reparsed.server_msg_id, 64)
    dt_s = _clean_str(row.msg_date_text or reparsed.msg_date_text, 64)
    cat_s = _clean_str(reparsed.category or row.category or "other", 64)
    norm_c = normalize_coords(reparsed.coords) if reparsed.coords else normalize_coords(row.coords)
    clean_btxt = _clean_str(reparsed.body_text or btxt_orig, 8000)
    pdata: Mapping[str, object] = reparsed.parsed_data if isinstance(reparsed.parsed_data, Mapping) else {}
    msg_upd = GameMessageBackfillUpdate(
        row_id=row.id,
        category=cat_s,
        coords=norm_c,
        body_text=clean_btxt,
        parsed_json=format_backfill_json(pdata),
    )
    if cat_s == "battle_report":
        b_in = _extract_backfill_battle(
            uid=uid, srv_id=srv_id, dt_s=dt_s, norm_c=norm_c, clean_btxt=clean_btxt, pdata=pdata, row_ts=row_ts
        )
        return msg_upd, b_in, None, None
    if cat_s == "recycle_report" and not is_non_recycle_fleet_notice_text(clean_btxt, row.subject):
        r_in = _extract_backfill_recycle(
            uid=uid, srv_id=srv_id, dt_s=dt_s, norm_c=norm_c, clean_btxt=clean_btxt, pdata=pdata, row_ts=row_ts
        )
        return msg_upd, None, r_in, None
    if cat_s == "spy_report":
        return msg_upd, None, None, _extract_backfill_spy(reparsed, srv_id=srv_id, norm_c=norm_c)
    return msg_upd, None, None, None


def run_gateway_startup_maintenance(store: DatabaseStore, *, ts: float | None = None) -> None:
    """Run idempotent spy-target repair and `game_messages` report backfill on gateway boot."""
    now_ts = round(_safe_float(ts, time.time()) if ts is not None else time.time(), 3)
    with store.session() as session:
        repair_spy_targets_sql(session, is_non_spy_fn=is_non_spy_notice_text, ts=now_ts)
        max_id, msg_rows, own_coords = load_game_messages_for_backfill(session)
        if max_id > 0:
            msg_updates: list[GameMessageBackfillUpdate] = []
            battle_inputs: list[BattleReportInput] = []
            recycle_inputs: list[RecycleReportInput] = []
            spy_updates: list[SpyBackfillUpdate] = []
            for row in msg_rows:
                m_u, b_in, r_in, s_u = _reparse_backfill_row(row, own_coords=own_coords, now_ts=now_ts)
                if m_u is not None:
                    msg_updates.append(m_u)
                if b_in is not None:
                    battle_inputs.append(b_in)
                if r_in is not None:
                    recycle_inputs.append(r_in)
                if s_u is not None:
                    spy_updates.append(s_u)
            apply_game_message_backfill(
                session,
                max_id=max_id,
                msg_updates=msg_updates,
                battle_inputs=battle_inputs,
                recycle_inputs=recycle_inputs,
                spy_updates=spy_updates,
                ts=now_ts,
            )


__all__ = ["run_gateway_startup_maintenance"]
