"""Typed inputs, deduplication, and PostgreSQL upsert helpers for `MessagesRepository`."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
import re

from sqlalchemy import case, func
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from spaceinvasion.db.dto import GameMessageRecord, RecycleReportRecord
from spaceinvasion.db.models import GameMessage, RecycleReport
from spaceinvasion.db.repos._helpers import (
    as_str_dict,
    clamp_int64,
    dumps_json,
    normalize_coords,
    round_ts,
    sanitize_text,
    spice_equivalent_from_counts,
    to_float,
    to_int,
)
from spaceinvasion.db.repos.raids import BattleReportInput

FRACTION_ONLY_RE = re.compile(r"^\d[\d.]*\s*/\s*\d[\d.]*$")


@dataclass(frozen=True)
class GameMessageInput:
    """Typed input payload for append-only `game_messages` ingestion."""

    msg_uid: str
    server_msg_id: str = ""
    folder_type: str = ""
    category: str = "other"
    msg_date_text: str = ""
    sender: str = ""
    subject: str = ""
    coords: str = ""
    body_text: str = ""
    raw_html: str = ""
    parsed_data: Mapping[str, object] = field(default_factory=dict)


@dataclass(frozen=True)
class RecycleReportInput:
    """Typed input payload for bulk `recycle_reports` upserts."""

    msg_uid: str
    coords: str = ""
    server_msg_id: str = ""
    msg_date_text: str = ""
    recycler_count: int = 0
    cargo_capacity: int = 0
    debris_avail_pig: int = 0
    debris_avail_metal: int = 0
    harvested_pig_iron: int = 0
    harvested_metal: int = 0
    harvested_kryptonite: int = 0
    harvested_spice: int = 0
    harvested_spice_eq: float | None = None
    raw_text: str = ""
    ts: float | None = None


def msg_to_dto(row: GameMessage) -> GameMessageRecord:
    """Convert a `GameMessage` ORM row into a frozen `GameMessageRecord` DTO."""
    return GameMessageRecord(
        id=to_int(row.id, 0),
        ingested_ts=to_float(row.ingested_ts, 0.0),
        msg_uid=row.msg_uid,
        server_msg_id=row.server_msg_id,
        folder_type=row.folder_type,
        category=row.category,
        msg_date_text=row.msg_date_text,
        sender=row.sender,
        subject=row.subject,
        coords=row.coords,
        body_text=row.body_text,
        raw_html=row.raw_html,
        parsed_json=row.parsed_json,
        deleted_in_game=bool(row.deleted_in_game),
        deleted_ts=to_float(row.deleted_ts, 0.0) if row.deleted_ts is not None else None,
    )


def recycle_to_dto(row: RecycleReport) -> RecycleReportRecord:
    """Convert a `RecycleReport` ORM row into a frozen `RecycleReportRecord` DTO."""
    return RecycleReportRecord(
        id=to_int(row.id, 0),
        ts=to_float(row.ts, 0.0),
        msg_uid=row.msg_uid,
        server_msg_id=row.server_msg_id,
        msg_date_text=row.msg_date_text,
        coords=normalize_coords(row.coords) if row.coords else "",
        recycler_count=to_int(row.recycler_count, 0),
        cargo_capacity=to_int(row.cargo_capacity, 0),
        debris_avail_pig=to_int(row.debris_avail_pig, 0),
        debris_avail_metal=to_int(row.debris_avail_metal, 0),
        harvested_pig_iron=to_int(row.harvested_pig_iron, 0),
        harvested_metal=to_int(row.harvested_metal, 0),
        harvested_kryptonite=to_int(row.harvested_kryptonite, 0),
        harvested_spice=to_int(row.harvested_spice, 0),
        harvested_spice_eq=to_float(row.harvested_spice_eq, 0.0),
        raw_text=row.raw_text,
    )


def _build_battle_from_msg(
    uid: str,
    srv_id: str,
    dt_s: str,
    norm_c: str,
    body_s: str,
    pdata: dict[str, object],
    now_ts: float,
) -> BattleReportInput:
    loot_raw = pdata.get("loot") if isinstance(pdata.get("loot"), Mapping) else pdata.get("resources_mentioned")
    loot_obj = as_str_dict(loot_raw)
    deb_obj = as_str_dict(pdata.get("debris"))
    lseq_raw = pdata.get("loot_spice_eq")
    dseq_raw = pdata.get("debris_spice_eq")
    return BattleReportInput(
        msg_uid=uid,
        target_coords=norm_c,
        server_msg_id=srv_id,
        battle_id=sanitize_text(pdata.get("battle_id") or "", 64),
        msg_date_text=dt_s,
        attacker=sanitize_text(pdata.get("attacker") or "", 120),
        defender=sanitize_text(pdata.get("defender") or "", 120),
        outcome=sanitize_text(pdata.get("outcome") or "win", 32),
        rounds=max(0, clamp_int64(to_int(pdata.get("rounds"), 0))),
        pig_iron=max(0, clamp_int64(to_int(loot_obj.get("pig_iron"), 0))),
        metal=max(0, clamp_int64(to_int(loot_obj.get("metal"), 0))),
        kryptonite=max(0, clamp_int64(to_int(loot_obj.get("kryptonite"), 0))),
        spice=max(0, clamp_int64(to_int(loot_obj.get("spice"), 0))),
        loot_spice_eq=to_float(lseq_raw) if lseq_raw is not None else None,
        attacker_lost_spice=max(0, clamp_int64(to_int(pdata.get("attacker_lost_spice"), 0))),
        defender_lost_spice=max(0, clamp_int64(to_int(pdata.get("defender_lost_spice"), 0))),
        debris_pig_iron=max(0, clamp_int64(to_int(deb_obj.get("pig_iron"), 0))),
        debris_metal=max(0, clamp_int64(to_int(deb_obj.get("metal"), 0))),
        debris_spice_eq=to_float(dseq_raw) if dseq_raw is not None else None,
        raw_text=body_s[:8000],
        ts=now_ts,
    )


def _build_recycle_from_msg(
    uid: str,
    srv_id: str,
    dt_s: str,
    norm_c: str,
    body_s: str,
    pdata: dict[str, object],
    now_ts: float,
) -> RecycleReportInput:
    harv = as_str_dict(pdata.get("harvested"))
    avail = as_str_dict(pdata.get("debris_available"))
    hseq_raw = pdata.get("harvested_spice_eq")
    return RecycleReportInput(
        msg_uid=uid,
        coords=norm_c,
        server_msg_id=srv_id,
        msg_date_text=dt_s,
        recycler_count=max(0, clamp_int64(to_int(pdata.get("recycler_count"), 0))),
        cargo_capacity=max(0, clamp_int64(to_int(pdata.get("cargo_capacity"), 0))),
        debris_avail_pig=max(0, clamp_int64(to_int(avail.get("pig_iron"), 0))),
        debris_avail_metal=max(0, clamp_int64(to_int(avail.get("metal"), 0))),
        harvested_pig_iron=max(0, clamp_int64(to_int(harv.get("pig_iron"), 0))),
        harvested_metal=max(0, clamp_int64(to_int(harv.get("metal"), 0))),
        harvested_kryptonite=max(0, clamp_int64(to_int(harv.get("kryptonite"), 0))),
        harvested_spice=max(0, clamp_int64(to_int(harv.get("spice"), 0))),
        harvested_spice_eq=to_float(hseq_raw) if hseq_raw is not None else None,
        raw_text=body_s[:8000],
        ts=now_ts,
    )


def prepare_message_batch(
    messages: Sequence[GameMessageInput],
    now_ts: float,
) -> tuple[
    dict[str, dict[str, object]],
    list[str],
    list[tuple[str, int]],
    list[BattleReportInput],
    list[RecycleReportInput],
]:
    """Filter, normalize, and deduplicate an inbox batch while extracting combat reports."""
    dedup_msgs: dict[str, dict[str, object]] = {}
    ordered_uids: list[str] = []
    ordered_lengths: list[tuple[str, int]] = []
    battle_inputs: list[BattleReportInput] = []
    recycle_inputs: list[RecycleReportInput] = []

    for msg in messages:
        uid = sanitize_text(msg.msg_uid, 120).strip()
        if not uid or uid.lower().startswith("id:msg-"):
            continue
        body_s = sanitize_text(msg.body_text, 16000)
        subj_s = sanitize_text(msg.subject, 300)
        if (not body_s and not subj_s) or FRACTION_ONLY_RE.fullmatch(body_s.strip()):
            continue
        norm_c = normalize_coords(msg.coords) if msg.coords.strip() else ""
        cat_s = sanitize_text(msg.category or "other", 64)
        srv_id = sanitize_text(msg.server_msg_id, 64)
        dt_s = sanitize_text(msg.msg_date_text, 64)
        pdata = as_str_dict(msg.parsed_data)
        pjson = dumps_json(pdata)
        cand: dict[str, object] = {
            "ingested_ts": now_ts,
            "msg_uid": uid,
            "server_msg_id": srv_id,
            "folder_type": sanitize_text(msg.folder_type, 64),
            "category": cat_s,
            "msg_date_text": dt_s,
            "sender": sanitize_text(msg.sender, 160),
            "subject": subj_s,
            "coords": norm_c,
            "body_text": body_s,
            "raw_html": sanitize_text(msg.raw_html, 32000),
            "parsed_json": pjson,
        }
        ordered_uids.append(uid)
        ordered_lengths.append((uid, len(body_s)))
        prev = dedup_msgs.get(uid)
        if prev is None:
            dedup_msgs[uid] = cand
        else:
            if not str(prev["server_msg_id"]) and srv_id:
                prev["server_msg_id"] = srv_id
            if (prev["category"] == "other" and cat_s != "other") or (
                prev["category"] == "recycle_report" and cat_s in ("fleet_transport", "fleet_return")
            ):
                prev["category"] = cat_s
            if not str(prev["coords"]) and norm_c:
                prev["coords"] = norm_c
            if len(body_s) >= len(str(prev["body_text"])) and pjson != "{}":
                prev["parsed_json"] = pjson
            if len(body_s) > len(str(prev["body_text"])):
                prev["body_text"] = body_s
            if len(str(cand["raw_html"])) > len(str(prev["raw_html"])):
                prev["raw_html"] = cand["raw_html"]
        if cat_s == "battle_report":
            battle_inputs.append(_build_battle_from_msg(uid, srv_id, dt_s, norm_c, body_s, pdata, now_ts))
        elif cat_s == "recycle_report":
            recycle_inputs.append(_build_recycle_from_msg(uid, srv_id, dt_s, norm_c, body_s, pdata, now_ts))
    return dedup_msgs, ordered_uids, ordered_lengths, battle_inputs, recycle_inputs


def upsert_message_rows(session: Session, rows: list[dict[str, object]]) -> None:
    """Execute 1 batched PostgreSQL `ON CONFLICT(msg_uid) DO UPDATE` into `game_messages`."""
    stmt = pg_insert(GameMessage).values(rows)
    session.execute(
        stmt.on_conflict_do_update(
            index_elements=[GameMessage.msg_uid],
            set_={
                "server_msg_id": case(
                    (GameMessage.server_msg_id != "", GameMessage.server_msg_id),
                    else_=stmt.excluded.server_msg_id,
                ),
                "category": case(
                    ((GameMessage.category == "other") & (stmt.excluded.category != "other"), stmt.excluded.category),
                    (
                        (GameMessage.category == "recycle_report")
                        & stmt.excluded.category.in_(("fleet_transport", "fleet_return")),
                        stmt.excluded.category,
                    ),
                    else_=GameMessage.category,
                ),
                "coords": case((GameMessage.coords != "", GameMessage.coords), else_=stmt.excluded.coords),
                "body_text": case(
                    (
                        func.length(stmt.excluded.body_text) > func.length(GameMessage.body_text),
                        stmt.excluded.body_text,
                    ),
                    else_=GameMessage.body_text,
                ),
                "raw_html": case(
                    (func.length(stmt.excluded.raw_html) > func.length(GameMessage.raw_html), stmt.excluded.raw_html),
                    else_=GameMessage.raw_html,
                ),
                "parsed_json": case(
                    (
                        (func.length(stmt.excluded.body_text) >= func.length(GameMessage.body_text))
                        & (stmt.excluded.parsed_json != "{}"),
                        stmt.excluded.parsed_json,
                    ),
                    else_=GameMessage.parsed_json,
                ),
            },
        )
    )


def merge_recycle_rows(reports: Sequence[RecycleReportInput], default_ts: float) -> list[dict[str, object]]:
    """Normalize and deduplicate recycle report inputs by `msg_uid` before bulk upsert."""
    by_uid: dict[str, dict[str, object]] = {}
    for rep in reports:
        uid = sanitize_text(rep.msg_uid, 120).strip()
        if not uid:
            continue
        hpig = max(0, clamp_int64(int(rep.harvested_pig_iron)))
        hmet = max(0, clamp_int64(int(rep.harvested_metal)))
        hkry = max(0, clamp_int64(int(rep.harvested_kryptonite)))
        hspi = max(0, clamp_int64(int(rep.harvested_spice)))
        hseq = (
            float(rep.harvested_spice_eq)
            if rep.harvested_spice_eq is not None
            else round(spice_equivalent_from_counts(hpig, hmet, hkry, hspi), 2)
        )
        cand: dict[str, object] = {
            "ts": round_ts(rep.ts) if rep.ts is not None else default_ts,
            "msg_uid": uid,
            "server_msg_id": sanitize_text(rep.server_msg_id, 64),
            "msg_date_text": sanitize_text(rep.msg_date_text, 64),
            "coords": normalize_coords(rep.coords) if rep.coords.strip() else "",
            "recycler_count": max(0, clamp_int64(int(rep.recycler_count))),
            "cargo_capacity": max(0, clamp_int64(int(rep.cargo_capacity))),
            "debris_avail_pig": max(0, clamp_int64(int(rep.debris_avail_pig))),
            "debris_avail_metal": max(0, clamp_int64(int(rep.debris_avail_metal))),
            "harvested_pig_iron": hpig,
            "harvested_metal": hmet,
            "harvested_kryptonite": hkry,
            "harvested_spice": hspi,
            "harvested_spice_eq": hseq,
            "raw_text": sanitize_text(rep.raw_text, 8000),
        }
        prev = by_uid.get(uid)
        if prev is None:
            by_uid[uid] = cand
            continue
        if str(cand["coords"]):
            prev["coords"] = cand["coords"]
        for num_k in (
            "recycler_count",
            "cargo_capacity",
            "debris_avail_pig",
            "debris_avail_metal",
            "harvested_pig_iron",
            "harvested_metal",
            "harvested_kryptonite",
            "harvested_spice",
            "harvested_spice_eq",
        ):
            pv, cv = prev[num_k], cand[num_k]
            if isinstance(pv, (int, float)) and isinstance(cv, (int, float)) and cv > pv:
                prev[num_k] = cv
        if len(str(cand["raw_text"])) > len(str(prev["raw_text"])):
            prev["raw_text"] = cand["raw_text"]
    return list(by_uid.values())


__all__ = [
    "FRACTION_ONLY_RE",
    "GameMessageInput",
    "RecycleReportInput",
    "merge_recycle_rows",
    "msg_to_dto",
    "prepare_message_batch",
    "recycle_to_dto",
    "upsert_message_rows",
]
