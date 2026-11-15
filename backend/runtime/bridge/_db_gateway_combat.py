"""Raids, Bash tracker, battle/recycle/inbox messages, and colonies gateway mixin."""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
import time

from backend.combat.colonies import grade_colony_candidate
from backend.combat.flight import BashTracker
from backend.core.catalog import cap_json_int
from backend.core.coords import normalize_coords
from backend.db.dto.serializers import (
    battle_report_to_dict,
    colony_attempt_to_dict,
    game_message_to_dict,
    recycle_report_to_dict,
)
from backend.db.queries import (
    query_active_blocked_targets,
    query_colony_attempts_summary,
    query_colony_candidates,
    query_raid_stats_detailed,
    query_recent_raids_for_bash,
)
from backend.db.repos import GameMessageInput
from backend.parsing.inbox import is_non_recycle_fleet_notice_text
from backend.parsing.models import InboxMessage
from backend.runtime.bridge._db_gateway_common import _clean_str, _GatewayCore, _safe_float, _safe_int


class _CombatGatewayMixin(_GatewayCore):
    """Raid history, 3/24h Bash tracker, battle/recycle reports, inbox messages, and colonies."""

    def get_raid_stats(
        self,
        *,
        window_seconds: float | None = None,
        now: float | None = None,
        include_telemetry_summary: bool = True,
    ) -> dict[str, object]:
        """Compute full raid KPIs, per-target stats, bash blocks, and telemetry summary."""
        with self._store.session() as session:
            return query_raid_stats_detailed(
                session,
                window_seconds=window_seconds,
                now=now,
                include_telemetry_summary=include_telemetry_summary,
            )

    def load_bash_tracker(self, *, now: float | None = None) -> BashTracker:
        """Load 24h non-exempt raids and active target cooldown blocks into a `BashTracker`."""
        ref = _safe_float(now, time.time()) if now is not None else time.time()
        cutoff, skew_max = ref - 24 * 3600, ref + 60.0
        with self._store.session() as session:
            raids = query_recent_raids_for_bash(session, cutoff_ts=cutoff)
            blocks = query_active_blocked_targets(session, now_ts=ref)
        tracker = BashTracker()
        for coords, ts_val, is_exempt in raids:
            if not is_exempt and cutoff <= ts_val <= skew_max:
                tracker.record_attack(coords, timestamp=ts_val)
        for coords, until_val, reason, _hits, last_b_ts in blocks:
            if last_b_ts > 0.0 and last_b_ts > skew_max:
                continue
            if until_val > ref:
                tracker.record_block(coords, until_val, reason=reason)
        return tracker

    def record_raid(
        self,
        target_coords: str,
        *,
        attacked_ts: float | None = None,
        is_exempt: bool = False,
        outcome: str = "",
        loot: Mapping[str, object] | None = None,
    ) -> None:
        """Append one raid row and clear expired target block cooldowns."""
        with self._store.repositories() as repos:
            repos.raids.record_raid(
                target_coords, attacked_ts=attacked_ts, is_exempt=is_exempt, outcome=outcome, loot=loot
            )
        self._bump_change()

    def record_target_block(
        self,
        target_coords: str,
        *,
        blocked_until_ts: float,
        reason: str = "",
        is_bash: bool = False,
        ts: float | None = None,
        blocked_ts: float | None = None,
    ) -> None:
        """Upsert target cooldown block and append a `bash_blocks` ledger row."""
        eff_ts = blocked_ts if blocked_ts is not None else ts
        with self._store.repositories() as repos:
            repos.raids.record_target_block(
                target_coords,
                blocked_until_ts=blocked_until_ts,
                reason=reason,
                is_bash=is_bash,
                blocked_ts=eff_ts,
            )
        self._bump_change()

    def get_battle_reports(
        self,
        *,
        target_coords: str | None = None,
        coords: str | None = None,
        limit: int = 200,
    ) -> list[dict[str, object]]:
        """List structured battle reports ordered newest first."""
        eff_coords = target_coords if target_coords is not None else coords
        with self._store.repositories() as repos:
            rows = repos.raids.list_battle_reports(target_coords=eff_coords, limit=limit)
        return [battle_report_to_dict(r) for r in rows]

    def get_recycle_reports(self, *, coords: str | None = None, limit: int = 200) -> list[dict[str, object]]:
        """List structured recycle reports (filtering non-recycle fleet notices)."""
        lim = max(1, _safe_int(limit, 200))
        fetch_lim = min(5000, max(lim * 3, lim + 100))
        with self._store.repositories() as repos:
            rows = repos.messages.list_recycle_reports(coords=coords, limit=fetch_lim)
        out: list[dict[str, object]] = []
        for r in rows:
            if is_non_recycle_fleet_notice_text(r.raw_text):
                continue
            out.append(recycle_report_to_dict(r))
            if len(out) >= lim:
                break
        return out

    def get_game_messages(
        self,
        *,
        category: str | None = None,
        exclude_categories: Sequence[str] = (),
        coords: str | None = None,
        folder_type: str | None = None,
        limit: int = 200,
    ) -> list[dict[str, object]]:
        """List persisted inbox messages ordered newest first."""
        with self._store.repositories() as repos:
            rows = repos.messages.list_messages(
                category=category,
                exclude_categories=exclude_categories,
                coords=coords,
                folder_type=folder_type,
                limit=limit,
            )
        return [game_message_to_dict(r) for r in rows]

    def record_game_messages(
        self,
        messages: Sequence[InboxMessage],
        *,
        ts: float | None = None,
    ) -> tuple[list[str], list[InboxMessage]]:
        """Batch-ingest inbox messages and return `(persisted_uids, newly_inserted_messages)`."""
        if not messages:
            return ([], [])
        by_uid: dict[str, InboxMessage] = {}
        inputs: list[GameMessageInput] = []
        for m in messages:
            uid = _clean_str(m.msg_uid, 120).strip()
            if uid and uid not in by_uid:
                by_uid[uid] = m
            inputs.append(
                GameMessageInput(
                    msg_uid=m.msg_uid,
                    server_msg_id=m.server_msg_id,
                    folder_type=m.folder_type,
                    category=m.category,
                    msg_date_text=m.msg_date_text,
                    sender=m.sender,
                    subject=m.subject,
                    coords=m.coords,
                    body_text=m.body_text,
                    raw_html=m.raw_html,
                    parsed_data=m.parsed_data,
                )
            )
        with self._store.repositories() as repos:
            persisted, newly_uids = repos.messages.ingest_messages(inputs, ts=ts)
        if persisted:
            self._bump_change()
        return (list(persisted), [by_uid[u] for u in newly_uids if u in by_uid])

    def mark_game_messages_deleted(self, msg_uids: Sequence[str], *, ts: float | None = None) -> int:
        """Mark inbox messages deleted in-game while preserving archive rows."""
        with self._store.repositories() as repos:
            cnt = repos.messages.mark_deleted_in_game(msg_uids, ts=ts)
        if cnt > 0:
            self._bump_change()
        return cnt

    def get_discarded_colony_coords(self) -> set[str]:
        """Return normalized operator-discarded free colony coordinates."""
        with self._store.repositories() as repos:
            return set(repos.colonies.get_discarded_coords())

    def discard_colony_candidate(
        self, coords: str, *, reason: str = "operator_discard", ts: float | None = None
    ) -> dict[str, object]:
        """Mark a colony candidate coordinate discarded and sync hot candidates."""
        eff_now = round(_safe_float(ts, time.time()) if ts is not None else time.time(), 3)
        norm_c = normalize_coords(str(coords).strip())
        clean_reason = _clean_str(reason or "operator_discard", 160) or "operator_discard"
        with self._store.repositories() as repos:
            cnt = repos.colonies.discard_candidate(norm_c, reason=clean_reason, ts=eff_now)
        self._bump_change()
        return {
            "coords": norm_c,
            "discarded": True,
            "discarded_ts": eff_now,
            "reason": clean_reason,
            "discarded_count": cnt,
        }

    def restore_colony_candidate(self, coords: str, *, ts: float | None = None) -> bool:
        """Restore a previously discarded colony candidate coordinate."""
        with self._store.repositories() as repos:
            restored = repos.colonies.restore_candidate(coords, ts=ts)
        if restored:
            self._bump_change()
        return restored

    def get_colony_candidates(
        self,
        *,
        origin_coords: str = "",
        min_colony_fields: int = 300,
        min_fields_target: int | None = None,
        own_coords: Iterable[str] = (),
        exclude_coords: Iterable[str] | None = None,
        include_discarded: bool = False,
        limit: int = 200,
    ) -> list[dict[str, object]]:
        """Rank and grade free colony candidates using `grade_colony_candidate`."""
        eff_own = exclude_coords if exclude_coords is not None else own_coords
        eff_min = max(
            50, min(600, _safe_int(min_fields_target if min_fields_target is not None else min_colony_fields, 300))
        )
        eff_limit = max(1, _safe_int(limit, 200))
        norm_origin = normalize_coords(origin_coords) if origin_coords else ""
        with self._store.session() as session:
            rows = query_colony_candidates(
                session,
                origin_coords=norm_origin,
                min_fields_target=eff_min,
                exclude_coords=eff_own,
                include_discarded=include_discarded,
                limit=eff_limit,
            )
        graded: list[dict[str, object]] = []
        for r in rows:
            g = dict(
                grade_colony_candidate(
                    coords=r.coords,
                    origin_coords=norm_origin,
                    uninhabited_fields=r.uninhabited_fields,
                    colonize_min_fields=r.colonize_min_fields,
                    colonize_max_fields=r.colonize_max_fields,
                    temperature=r.temperature,
                    spied=r.is_probed,
                    min_fields_target=eff_min,
                    last_spied_ts=r.last_spied_ts,
                    last_scanned_ts=r.last_scanned_ts,
                )
            )
            g["spy_count"] = cap_json_int(r.spy_count)
            g["blocked_until_ts"] = r.blocked_until_ts
            g["block_reason"] = r.block_reason
            g["discarded"] = r.is_discarded
            graded.append(g)
        return graded

    def record_colony_attempt(
        self,
        *,
        target_coords: str,
        event_type: str,
        origin_coords: str = "",
        planet_id: int | None = None,
        fields_rolled: int | None = None,
        min_fields_target: int = 300,
        expected_min_fields: int | None = None,
        expected_max_fields: int | None = None,
        temperature: int | None = None,
        grade: str = "",
        score: int | None = None,
        status_message: str = "",
        details: Mapping[str, object] | None = None,
        ts: float | None = None,
    ) -> dict[str, object]:
        """Append one colonization attempt event."""
        with self._store.repositories() as repos:
            rec = repos.colonies.record_attempt(
                target_coords=target_coords,
                event_type=event_type,
                origin_coords=origin_coords,
                planet_id=planet_id,
                fields_rolled=fields_rolled,
                min_fields_target=min_fields_target,
                expected_min_fields=expected_min_fields,
                expected_max_fields=expected_max_fields,
                temperature=temperature,
                grade=grade,
                score=score,
                status_message=status_message,
                details=details,
                ts=ts,
            )
        if rec is None:
            return {}
        self._bump_change()
        return colony_attempt_to_dict(rec)

    def get_colony_attempts(self, *, limit: int = 100, target_coords: str | None = None) -> list[dict[str, object]]:
        """List colonization attempts ordered newest first."""
        with self._store.repositories() as repos:
            rows = repos.colonies.list_attempts(limit=limit, target_coords=target_coords)
        return [colony_attempt_to_dict(r) for r in rows]

    def get_colony_attempts_summary(self) -> dict[str, object]:
        """Return aggregated colonization KPIs and per-coordinate attempt counters."""
        with self._store.session() as session:
            summary = query_colony_attempts_summary(session)
        return {
            "total_events": cap_json_int(summary.total_events),
            "unique_coords_tried": cap_json_int(summary.unique_coords_tried),
            "dispatched_count": cap_json_int(summary.dispatched_count),
            "deleted_undersized_count": cap_json_int(summary.deleted_undersized_count),
            "kept_count": cap_json_int(summary.kept_count),
            "failed_count": cap_json_int(summary.failed_count),
            "by_coords": {
                nc: {
                    "coords": item.coords,
                    "attempts_count": cap_json_int(item.attempts_count),
                    "events_count": cap_json_int(item.events_count),
                    "dispatched_count": cap_json_int(item.dispatched_count),
                    "deleted_count": cap_json_int(item.deleted_count),
                    "kept_count": cap_json_int(item.kept_count),
                    "failed_count": cap_json_int(item.failed_count),
                    "last_event_type": item.last_event_type,
                    "last_fields_rolled": cap_json_int(item.last_fields_rolled)
                    if item.last_fields_rolled is not None
                    else None,
                    "last_temperature": cap_json_int(item.last_temperature)
                    if item.last_temperature is not None
                    else None,
                    "last_origin_coords": item.last_origin_coords,
                    "last_status_message": item.last_status_message,
                    "last_ts": item.last_ts,
                }
                for nc, item in summary.by_coords.items()
            },
        }


__all__ = ["_CombatGatewayMixin"]
