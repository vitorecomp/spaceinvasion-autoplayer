"""SQLAlchemy 2.0 ORM repository for colonization attempts and hot colony candidates."""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass

from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from spaceinvasion.db.dto import ColonyAttemptRecord
from spaceinvasion.db.models import ColonyAttempt, ColonyCandidateHot, MetaEntry
from spaceinvasion.db.repos._helpers import (
    affected_rows,
    clamp_int64,
    coord_query_variants,
    dumps_json,
    loads_json_dict,
    normalize_coords,
    parse_coords_gsp,
    round_ts,
    sanitize_text,
)

_DISCARDED_META_KEY = "discarded_colony_coords"


@dataclass(frozen=True)
class ColonyCandidateHotInput:
    """Typed input row for bulk `colony_candidates_hot` upserts."""

    coords: str
    uninhabited_fields: int | None = None
    colonize_min_fields: int | None = None
    colonize_max_fields: int | None = None
    temperature: int | None = None
    last_spied_ts: float = 0.0
    last_scanned_ts: float = 0.0
    spy_count: int = 0
    blocked_until_ts: float = 0.0
    block_reason: str = ""
    is_discarded: bool = False
    is_active: bool = True


def _attempt_to_dto(row: ColonyAttempt) -> ColonyAttemptRecord:
    return ColonyAttemptRecord(
        id=row.id,
        ts=float(row.ts),
        event_type=row.event_type.strip().lower(),
        target_coords=normalize_coords(row.target_coords) if row.target_coords else "",
        origin_coords=normalize_coords(row.origin_coords) if row.origin_coords else "",
        planet_id=int(row.planet_id) if row.planet_id is not None else None,
        fields_rolled=int(row.fields_rolled) if row.fields_rolled is not None else None,
        min_fields_target=max(1, int(row.min_fields_target)),
        expected_min_fields=int(row.expected_min_fields) if row.expected_min_fields is not None else None,
        expected_max_fields=int(row.expected_max_fields) if row.expected_max_fields is not None else None,
        temperature=int(row.temperature) if row.temperature is not None else None,
        grade=row.grade.strip(),
        score=int(row.score) if row.score is not None else None,
        status_message=row.status_message,
        details_json=row.details_json,
    )


def _compute_candidate_row(item: ColonyCandidateHotInput, now_ts: float) -> dict[str, object] | None:
    nc = normalize_coords(item.coords)
    if not nc:
        return None
    parsed = parse_coords_gsp(nc)
    gal, sys_num, pos = parsed if parsed is not None else (0, 0, 0)
    uf, cmin, cmax = item.uninhabited_fields, item.colonize_min_fields, item.colonize_max_fields
    uf_pos = uf if (uf is not None and uf > 0) else None
    cmin_pos = cmin if (cmin is not None and cmin > 0) else None
    cmax_pos = cmax if (cmax is not None and cmax > 0) else None
    eff_cmax = cmin_pos if (cmin_pos is not None and cmax_pos is not None and cmin_pos > cmax_pos) else cmax_pos
    max_est = uf_pos if uf_pos is not None else (eff_cmax if eff_cmax is not None else 0)
    is_probed = 1 if (uf_pos is not None or eff_cmax is not None) else 0
    return {
        "coords": nc,
        "galaxy": gal,
        "system": sys_num,
        "position": pos,
        "uninhabited_fields": uf,
        "colonize_min_fields": cmin,
        "colonize_max_fields": cmax,
        "max_fields_est": max_est,
        "temperature": item.temperature,
        "is_probed": is_probed,
        "last_spied_ts": float(item.last_spied_ts),
        "last_scanned_ts": float(item.last_scanned_ts),
        "spy_count": max(0, int(item.spy_count)),
        "blocked_until_ts": float(item.blocked_until_ts),
        "block_reason": sanitize_text(item.block_reason),
        "is_discarded": 1 if item.is_discarded else 0,
        "is_active": 1 if item.is_active else 0,
        "updated_ts": now_ts,
    }


class ColoniesRepository:
    """SQLAlchemy ORM repository over `colony_attempts` and `colony_candidates_hot`."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def record_attempt(
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
    ) -> ColonyAttemptRecord | None:
        """Append one colonization attempt event to `colony_attempts`."""
        norm_tc = normalize_coords(target_coords)
        if not norm_tc:
            return None
        norm_oc = normalize_coords(origin_coords) if origin_coords.strip() else ""
        ev_type = sanitize_text(event_type, 40).strip().lower() or "dispatched"
        row = ColonyAttempt(
            ts=round_ts(ts),
            event_type=ev_type,
            target_coords=norm_tc,
            origin_coords=norm_oc,
            planet_id=clamp_int64(int(planet_id)) if planet_id is not None else None,
            fields_rolled=clamp_int64(int(fields_rolled)) if fields_rolled is not None else None,
            min_fields_target=max(1, clamp_int64(int(min_fields_target))),
            expected_min_fields=clamp_int64(int(expected_min_fields)) if expected_min_fields is not None else None,
            expected_max_fields=clamp_int64(int(expected_max_fields)) if expected_max_fields is not None else None,
            temperature=clamp_int64(int(temperature)) if temperature is not None else None,
            grade=sanitize_text(grade, 16).strip(),
            score=clamp_int64(int(score)) if score is not None else None,
            status_message=sanitize_text(status_message, 400).strip(),
            details_json=dumps_json(dict(details) if details is not None else {}),
        )
        self._session.add(row)
        self._session.flush()
        return _attempt_to_dto(row)

    def list_attempts(
        self,
        *,
        limit: int = 100,
        target_coords: str | None = None,
    ) -> tuple[ColonyAttemptRecord, ...]:
        """List colonization attempts ordered newest first (`ts DESC, id DESC`)."""
        lim = max(1, min(2000, int(limit)))
        stmt = select(ColonyAttempt)
        if target_coords is not None and target_coords.strip():
            variants = coord_query_variants(target_coords)
            if not variants:
                return ()
            stmt = stmt.where(ColonyAttempt.target_coords.in_(variants))
        stmt = stmt.order_by(ColonyAttempt.ts.desc(), ColonyAttempt.id.desc()).limit(lim)
        rows = self._session.execute(stmt).scalars().all()
        return tuple(_attempt_to_dto(r) for r in rows)

    def upsert_candidates_hot(
        self,
        candidates: Sequence[ColonyCandidateHotInput],
        *,
        ts: float | None = None,
    ) -> int:
        """Bulk-upsert hot colony candidates in a single PostgreSQL `ON CONFLICT DO UPDATE` statement."""
        now_ts = round_ts(ts)
        payload = [row for item in candidates if (row := _compute_candidate_row(item, now_ts)) is not None]
        if not payload:
            return 0
        stmt = pg_insert(ColonyCandidateHot).values(payload)
        stmt = stmt.on_conflict_do_update(
            index_elements=[ColonyCandidateHot.coords],
            set_={k: getattr(stmt.excluded, k) for k in payload[0] if k != "coords"},
        )
        self._session.execute(stmt)
        return len(payload)

    def deactivate_candidates(self, coords: Iterable[str], *, ts: float | None = None) -> int:
        """Mark coordinates inactive in `colony_candidates_hot` in 1 SQL statement."""
        norm = sorted({nc for raw in coords if (nc := normalize_coords(raw))})
        if not norm:
            return 0
        res = self._session.execute(
            update(ColonyCandidateHot)
            .where(ColonyCandidateHot.coords.in_(norm), ColonyCandidateHot.is_active == 1)
            .values(is_active=0, updated_ts=round_ts(ts))
        )
        return affected_rows(res)

    def _load_discarded_map(self) -> dict[str, dict[str, object]]:
        meta_row = self._session.get(MetaEntry, _DISCARDED_META_KEY)
        if meta_row is None or not meta_row.value:
            return {}
        parsed = loads_json_dict(meta_row.value)
        out: dict[str, dict[str, object]] = {}
        for k, v in parsed.items():
            nk = normalize_coords(k)
            if nk:
                out[nk] = {str(ik): iv for ik, iv in v.items()} if isinstance(v, Mapping) else {}
        return out

    def _save_discarded_map(self, data: Mapping[str, Mapping[str, object]], now_ts: float) -> None:
        stmt = pg_insert(MetaEntry).values(
            key=_DISCARDED_META_KEY,
            value=dumps_json(dict(data)),
            updated_ts=now_ts,
        )
        stmt = stmt.on_conflict_do_update(
            index_elements=[MetaEntry.key],
            set_={"value": stmt.excluded.value, "updated_ts": stmt.excluded.updated_ts},
        )
        self._session.execute(stmt)
        self._session.execute(
            update(ColonyCandidateHot)
            .where(ColonyCandidateHot.is_discarded == 1)
            .values(is_discarded=0, updated_ts=now_ts)
        )
        if data:
            self._session.execute(
                update(ColonyCandidateHot)
                .where(ColonyCandidateHot.coords.in_(sorted(data.keys())))
                .values(is_discarded=1, updated_ts=now_ts)
            )

    def get_discarded_coords(self) -> frozenset[str]:
        """Return normalized operator-discarded free colony coordinates."""
        return frozenset(self._load_discarded_map().keys())

    def discard_candidate(
        self,
        coords: str,
        *,
        reason: str = "operator_discard",
        ts: float | None = None,
    ) -> int:
        """Mark a colony coordinate discarded in `meta` and sync `colony_candidates_hot`."""
        norm_c = normalize_coords(coords)
        if not norm_c:
            raise ValueError(f"Invalid colony coordinates: {coords!r}")
        now_ts = round_ts(ts)
        cur_map = self._load_discarded_map()
        clean_reason = sanitize_text(reason or "operator_discard", 160) or "operator_discard"
        cur_map[norm_c] = {"discarded_ts": now_ts, "reason": clean_reason}
        self._save_discarded_map(cur_map, now_ts)
        return len(cur_map)

    def restore_candidate(self, coords: str, *, ts: float | None = None) -> bool:
        """Restore a previously discarded colony coordinate in `meta` and `colony_candidates_hot`."""
        norm_c = normalize_coords(coords)
        if not norm_c:
            return False
        cur_map = self._load_discarded_map()
        if norm_c not in cur_map:
            return False
        del cur_map[norm_c]
        self._save_discarded_map(cur_map, round_ts(ts))
        return True


__all__ = ["ColoniesRepository", "ColonyCandidateHotInput"]
