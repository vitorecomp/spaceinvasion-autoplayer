"""SQLAlchemy ORM repository for `raid_history`, `battle_reports`, and `bash_blocks`."""

from __future__ import annotations

from collections.abc import Mapping, Sequence

from sqlalchemy import case, func, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from spaceinvasion.db.dto import BashBlockRecord, BattleReportRecord, RaidHistoryRecord
from spaceinvasion.db.models import BashBlock, BattleReport, GalaxySlot, RaidHistory, SpyTarget
from spaceinvasion.db.repos._helpers import (
    affected_rows,
    dumps_json,
    normalize_coords,
    normalize_loot_dict,
    parse_coords_gsp,
    round_ts,
    sanitize_text,
)
from spaceinvasion.db.repos._hot_refresh import refresh_colony_candidates_hot, refresh_galaxy_sectors_hot
from spaceinvasion.db.repos._raid_helpers import (
    BattleReportInput,
    battle_to_dto,
    block_to_dto,
    merge_battle_rows,
    raid_to_dto,
)


class RaidsRepository:
    """Append-only raid, bash-block, and battle-report persistence."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def record_raid(
        self,
        target_coords: str,
        *,
        attacked_ts: float | None = None,
        is_exempt: bool = False,
        outcome: str = "",
        loot: Mapping[str, object] | None = None,
        refresh_hot: bool = True,
    ) -> RaidHistoryRecord:
        """Append a raid row and clear any expired target block in bounded O(1) SQL."""
        ts_val = round_ts(attacked_ts)
        norm_c = normalize_coords(target_coords)
        norm_loot = normalize_loot_dict(loot) if loot else {}
        row = RaidHistory(
            target_coords=norm_c,
            attacked_ts=ts_val,
            is_exempt=1 if is_exempt else 0,
            outcome=sanitize_text(outcome),
            loot_json=dumps_json(norm_loot),
        )
        self._session.add(row)
        self._session.flush()

        res = self._session.execute(
            update(SpyTarget)
            .where(
                SpyTarget.coords == norm_c,
                SpyTarget.blocked_until_ts > 0.0,
                SpyTarget.blocked_until_ts <= ts_val,
            )
            .values(blocked_until_ts=0.0, block_reason="")
        )
        if refresh_hot and affected_rows(res) > 0:
            refresh_colony_candidates_hot(self._session, (norm_c,), ts=ts_val)
        return raid_to_dto(row)

    def record_target_block(
        self,
        target_coords: str,
        *,
        blocked_until_ts: float,
        reason: str = "",
        is_bash: bool = False,
        blocked_ts: float | None = None,
        refresh_hot: bool = True,
    ) -> BashBlockRecord | None:
        """Upsert `spy_targets` block cooldown and append an immutable `bash_blocks` ledger row."""
        norm_c = normalize_coords(target_coords)
        if not norm_c:
            return None
        now_val = round_ts(blocked_ts)
        until_val = round_ts(blocked_until_ts)
        if until_val <= now_val:
            until_val = round(now_val + 60.0, 3)
        cooldown_s = round(max(0.0, until_val - now_val), 3)
        clean_reason = sanitize_text(reason, 400)
        is_spacia_hint = 1 if "spacia" in clean_reason.lower() else 0

        gs_row = self._session.execute(
            select(
                GalaxySlot.owner,
                GalaxySlot.planet_name,
                GalaxySlot.status_color,
                GalaxySlot.is_inactive,
                GalaxySlot.is_spacia,
                GalaxySlot.has_debris,
            )
            .where(GalaxySlot.coords == norm_c)
            .limit(1)
        ).first()
        if gs_row is not None:
            ins_owner = sanitize_text(gs_row[0] or "")
            ins_pname = sanitize_text(gs_row[1] or "")
            ins_color = sanitize_text(gs_row[2] or "free") or "free"
            ins_inactive = 1 if bool(gs_row[3]) else 0
            ins_spacia = 1 if (bool(gs_row[4]) or is_spacia_hint) else 0
            ins_debris = 1 if bool(gs_row[5]) else 0
        elif is_spacia_hint:
            ins_owner, ins_pname, ins_color, ins_inactive, ins_spacia, ins_debris = (
                "Spacia",
                "Spacia",
                "purple",
                1,
                1,
                0,
            )
        else:
            ins_owner, ins_pname, ins_color, ins_inactive, ins_spacia, ins_debris = ("", "", "free", 0, 0, 0)

        st_stmt = pg_insert(SpyTarget).values(
            coords=norm_c,
            owner=ins_owner,
            planet_name=ins_pname,
            status_color=ins_color,
            is_inactive=ins_inactive,
            is_spacia=ins_spacia,
            has_debris=ins_debris,
            last_scanned_ts=now_val,
            blocked_until_ts=until_val,
            block_reason=clean_reason,
            bash_hits=1 if is_bash else 0,
            last_blocked_ts=now_val,
        )
        self._session.execute(
            st_stmt.on_conflict_do_update(
                index_elements=[SpyTarget.coords],
                set_={
                    "blocked_until_ts": func.greatest(SpyTarget.blocked_until_ts, st_stmt.excluded.blocked_until_ts),
                    "block_reason": case(
                        (st_stmt.excluded.block_reason != "", st_stmt.excluded.block_reason),
                        else_=SpyTarget.block_reason,
                    ),
                    "bash_hits": SpyTarget.bash_hits + st_stmt.excluded.bash_hits,
                    "last_blocked_ts": func.greatest(SpyTarget.last_blocked_ts, st_stmt.excluded.last_blocked_ts),
                },
            )
        )
        blk = BashBlock(
            target_coords=norm_c,
            blocked_ts=now_val,
            blocked_until_ts=until_val,
            cooldown_seconds=cooldown_s,
            is_bash=1 if is_bash else 0,
            reason=clean_reason,
        )
        self._session.add(blk)
        self._session.flush()
        if refresh_hot:
            refresh_colony_candidates_hot(self._session, (norm_c,), ts=now_val)
            gsp = parse_coords_gsp(norm_c)
            if gsp is not None and 1 <= gsp[0] <= 14 and 1 <= gsp[1] <= 400:
                refresh_galaxy_sectors_hot(self._session, ((gsp[0], gsp[1]),), ts=now_val)
        return block_to_dto(blk)

    def upsert_battle_reports(
        self,
        reports: Sequence[BattleReportInput],
        *,
        ts: float | None = None,
    ) -> int:
        """Bulk-upsert structured battle reports in 1 PostgreSQL `ON CONFLICT` statement."""
        payload = merge_battle_rows(reports, round_ts(ts))
        if not payload:
            return 0
        stmt = pg_insert(BattleReport).values(payload)
        self._session.execute(
            stmt.on_conflict_do_update(
                index_elements=[BattleReport.msg_uid],
                set_={
                    "battle_id": case(
                        (stmt.excluded.battle_id != "", stmt.excluded.battle_id), else_=BattleReport.battle_id
                    ),
                    "attacker": case(
                        (stmt.excluded.attacker != "", stmt.excluded.attacker), else_=BattleReport.attacker
                    ),
                    "defender": case(
                        (stmt.excluded.defender != "", stmt.excluded.defender), else_=BattleReport.defender
                    ),
                    "outcome": stmt.excluded.outcome,
                    "rounds": func.greatest(BattleReport.rounds, stmt.excluded.rounds),
                    "pig_iron": func.greatest(BattleReport.pig_iron, stmt.excluded.pig_iron),
                    "metal": func.greatest(BattleReport.metal, stmt.excluded.metal),
                    "kryptonite": func.greatest(BattleReport.kryptonite, stmt.excluded.kryptonite),
                    "spice": func.greatest(BattleReport.spice, stmt.excluded.spice),
                    "loot_spice_eq": func.greatest(BattleReport.loot_spice_eq, stmt.excluded.loot_spice_eq),
                    "attacker_lost_spice": func.greatest(
                        BattleReport.attacker_lost_spice, stmt.excluded.attacker_lost_spice
                    ),
                    "defender_lost_spice": func.greatest(
                        BattleReport.defender_lost_spice, stmt.excluded.defender_lost_spice
                    ),
                    "debris_pig_iron": func.greatest(BattleReport.debris_pig_iron, stmt.excluded.debris_pig_iron),
                    "debris_metal": func.greatest(BattleReport.debris_metal, stmt.excluded.debris_metal),
                    "debris_spice_eq": func.greatest(BattleReport.debris_spice_eq, stmt.excluded.debris_spice_eq),
                    "raw_text": case(
                        (
                            func.length(stmt.excluded.raw_text) > func.length(BattleReport.raw_text),
                            stmt.excluded.raw_text,
                        ),
                        else_=BattleReport.raw_text,
                    ),
                },
            )
        )
        return len(payload)

    def list_raid_history(
        self,
        *,
        target_coords: str | None = None,
        since_ts: float | None = None,
        limit: int = 500,
    ) -> tuple[RaidHistoryRecord, ...]:
        """List recent raid history rows ordered newest first."""
        stmt = select(RaidHistory)
        if target_coords is not None:
            stmt = stmt.where(RaidHistory.target_coords == normalize_coords(target_coords))
        if since_ts is not None:
            stmt = stmt.where(RaidHistory.attacked_ts >= float(since_ts))
        rows = (
            self._session.execute(
                stmt.order_by(RaidHistory.attacked_ts.desc(), RaidHistory.id.desc()).limit(max(1, int(limit)))
            )
            .scalars()
            .all()
        )
        return tuple(raid_to_dto(r) for r in rows)

    def list_battle_reports(
        self,
        *,
        target_coords: str | None = None,
        limit: int = 200,
    ) -> tuple[BattleReportRecord, ...]:
        """List persisted battle reports ordered newest first."""
        stmt = select(BattleReport)
        if target_coords is not None:
            stmt = stmt.where(BattleReport.target_coords == normalize_coords(target_coords))
        rows = (
            self._session.execute(
                stmt.order_by(BattleReport.ts.desc(), BattleReport.id.desc()).limit(max(1, int(limit)))
            )
            .scalars()
            .all()
        )
        return tuple(battle_to_dto(r) for r in rows)

    def list_bash_blocks(
        self,
        *,
        target_coords: str | None = None,
        active_after_ts: float | None = None,
        limit: int = 200,
    ) -> tuple[BashBlockRecord, ...]:
        """List bash/protection cooldown blocks ordered chronologically."""
        stmt = select(BashBlock)
        if target_coords is not None:
            stmt = stmt.where(BashBlock.target_coords == normalize_coords(target_coords))
        if active_after_ts is not None:
            stmt = stmt.where(BashBlock.blocked_until_ts > float(active_after_ts))
        rows = (
            self._session.execute(
                stmt.order_by(BashBlock.blocked_ts.asc(), BashBlock.id.asc()).limit(max(1, int(limit)))
            )
            .scalars()
            .all()
        )
        return tuple(block_to_dto(r) for r in rows)


__all__ = ["BattleReportInput", "RaidsRepository"]
