"""Typed input payloads, deduplication, and DTO mappers for `RaidsRepository`."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from backend.db.dto import BashBlockRecord, BattleReportRecord, RaidHistoryRecord
from backend.db.models import BashBlock, BattleReport, RaidHistory
from backend.db.repos._helpers import (
    clamp_int64,
    is_win_outcome,
    normalize_coords,
    normalize_loot_dict,
    round_ts,
    sanitize_text,
    spice_equivalent_from_counts,
)


@dataclass(frozen=True)
class BattleReportInput:
    """Typed input payload for bulk `battle_reports` upserts."""

    msg_uid: str
    target_coords: str = ""
    server_msg_id: str = ""
    battle_id: str = ""
    msg_date_text: str = ""
    attacker: str = ""
    defender: str = ""
    outcome: str = "win"
    rounds: int = 0
    pig_iron: int = 0
    metal: int = 0
    kryptonite: int = 0
    spice: int = 0
    loot_spice_eq: float | None = None
    attacker_lost_spice: int = 0
    defender_lost_spice: int = 0
    debris_pig_iron: int = 0
    debris_metal: int = 0
    debris_spice_eq: float | None = None
    raw_text: str = ""
    ts: float | None = None


def raid_to_dto(row: RaidHistory) -> RaidHistoryRecord:
    """Convert a `RaidHistory` ORM row into a frozen `RaidHistoryRecord` DTO."""
    loot = normalize_loot_dict(row.loot_json)
    loot_sp = round(spice_equivalent_from_counts(loot["pig_iron"], loot["metal"], loot["kryptonite"], loot["spice"]), 3)
    return RaidHistoryRecord(
        id=int(row.id),
        target_coords=normalize_coords(row.target_coords),
        attacked_ts=float(row.attacked_ts),
        is_exempt=bool(row.is_exempt),
        outcome=row.outcome,
        loot_json=row.loot_json,
        loot_spice_eq=loot_sp,
        won=is_win_outcome(row.outcome, loot_sp),
    )


def block_to_dto(row: BashBlock) -> BashBlockRecord:
    """Convert a `BashBlock` ORM row into a frozen `BashBlockRecord` DTO."""
    return BashBlockRecord(
        id=int(row.id),
        target_coords=normalize_coords(row.target_coords),
        blocked_ts=float(row.blocked_ts),
        blocked_until_ts=float(row.blocked_until_ts),
        cooldown_seconds=float(row.cooldown_seconds),
        is_bash=bool(row.is_bash),
        reason=row.reason,
    )


def battle_to_dto(row: BattleReport) -> BattleReportRecord:
    """Convert a `BattleReport` ORM row into a frozen `BattleReportRecord` DTO."""
    return BattleReportRecord(
        id=int(row.id),
        ts=float(row.ts),
        msg_uid=row.msg_uid,
        server_msg_id=row.server_msg_id,
        battle_id=row.battle_id,
        msg_date_text=row.msg_date_text,
        target_coords=normalize_coords(row.target_coords),
        attacker=row.attacker,
        defender=row.defender,
        outcome=row.outcome,
        rounds=int(row.rounds),
        pig_iron=int(row.pig_iron),
        metal=int(row.metal),
        kryptonite=int(row.kryptonite),
        spice=int(row.spice),
        loot_spice_eq=float(row.loot_spice_eq),
        attacker_lost_spice=int(row.attacker_lost_spice),
        defender_lost_spice=int(row.defender_lost_spice),
        debris_pig_iron=int(row.debris_pig_iron),
        debris_metal=int(row.debris_metal),
        debris_spice_eq=float(row.debris_spice_eq),
        raw_text=row.raw_text,
    )


def merge_battle_rows(reports: Sequence[BattleReportInput], default_ts: float) -> list[dict[str, object]]:
    """Normalize and deduplicate battle reports by `msg_uid` before bulk upsert."""
    by_uid: dict[str, dict[str, object]] = {}
    for rep in reports:
        uid = sanitize_text(rep.msg_uid, 120).strip()
        if not uid:
            continue
        pig = max(0, clamp_int64(int(rep.pig_iron)))
        met = max(0, clamp_int64(int(rep.metal)))
        kry = max(0, clamp_int64(int(rep.kryptonite)))
        spi = max(0, clamp_int64(int(rep.spice)))
        dpig = max(0, clamp_int64(int(rep.debris_pig_iron)))
        dmet = max(0, clamp_int64(int(rep.debris_metal)))
        lseq = (
            float(rep.loot_spice_eq)
            if rep.loot_spice_eq is not None
            else round(spice_equivalent_from_counts(pig, met, kry, spi), 2)
        )
        dseq = (
            float(rep.debris_spice_eq)
            if rep.debris_spice_eq is not None
            else round(spice_equivalent_from_counts(dpig, dmet, 0, 0), 2)
        )
        cand: dict[str, object] = {
            "ts": round_ts(rep.ts) if rep.ts is not None else default_ts,
            "msg_uid": uid,
            "server_msg_id": sanitize_text(rep.server_msg_id, 64),
            "battle_id": sanitize_text(rep.battle_id, 64),
            "msg_date_text": sanitize_text(rep.msg_date_text, 64),
            "target_coords": normalize_coords(rep.target_coords) if rep.target_coords.strip() else "",
            "attacker": sanitize_text(rep.attacker, 120),
            "defender": sanitize_text(rep.defender, 120),
            "outcome": sanitize_text(rep.outcome or "win", 32),
            "rounds": max(0, clamp_int64(int(rep.rounds))),
            "pig_iron": pig,
            "metal": met,
            "kryptonite": kry,
            "spice": spi,
            "loot_spice_eq": lseq,
            "attacker_lost_spice": max(0, clamp_int64(int(rep.attacker_lost_spice))),
            "defender_lost_spice": max(0, clamp_int64(int(rep.defender_lost_spice))),
            "debris_pig_iron": dpig,
            "debris_metal": dmet,
            "debris_spice_eq": dseq,
            "raw_text": sanitize_text(rep.raw_text, 8000),
        }
        prev = by_uid.get(uid)
        if prev is None:
            by_uid[uid] = cand
            continue
        for key in ("battle_id", "attacker", "defender"):
            if str(cand[key]):
                prev[key] = cand[key]
        prev["outcome"] = cand["outcome"]
        for num_k in (
            "rounds",
            "pig_iron",
            "metal",
            "kryptonite",
            "spice",
            "loot_spice_eq",
            "attacker_lost_spice",
            "defender_lost_spice",
            "debris_pig_iron",
            "debris_metal",
            "debris_spice_eq",
        ):
            pv, cv = prev[num_k], cand[num_k]
            if isinstance(pv, (int, float)) and isinstance(cv, (int, float)) and cv > pv:
                prev[num_k] = cv
        if len(str(cand["raw_text"])) > len(str(prev["raw_text"])):
            prev["raw_text"] = cand["raw_text"]
    return list(by_uid.values())


__all__ = ["BattleReportInput", "battle_to_dto", "block_to_dto", "merge_battle_rows", "raid_to_dto"]
