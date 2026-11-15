"""Espionage targets/reports, debris ranking, and galaxy scans/slots/intel gateway mixin."""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
import time

from backend.core.catalog import cap_json_int
from backend.core.coords import normalize_coords
from backend.db.dto.serializers import (
    galaxy_slot_to_dict,
    spy_report_to_dict,
    spy_target_to_dict,
)
from backend.db.queries import (
    query_active_debris_targets_raw,
    query_battle_debris_fallbacks,
    query_galaxy_sector_intel_detailed,
    query_recycle_debris_candidates,
    query_spy_targets,
    rebuild_hot_tables_sql,
)
from backend.db.repos import GalaxySlotInput, SpyReportInput
from backend.parsing.inbox import is_non_recycle_fleet_notice_text
from backend.parsing.models import GalaxySlot, SpyReport
from backend.parsing.spy import is_non_spy_notice_text
from backend.runtime.bridge._db_gateway_common import _GatewayCore, _safe_float, _safe_int


def _parse_gsp(coords: str) -> tuple[int, int, int] | None:
    parts = normalize_coords(coords).split(":")
    if len(parts) != 3 or not all(p.isascii() and p.isdigit() and len(p) <= 9 for p in parts):
        return None
    return (int(parts[0]), int(parts[1]), int(parts[2]))


def _to_galaxy_slot_inputs(slots: Sequence[GalaxySlot]) -> list[GalaxySlotInput]:
    return [
        GalaxySlotInput(
            coords=str(s.coords),
            position=int(s.position),
            planet_name=str(s.name),
            owner=str(s.owner),
            status_color=str(s.status_color or "free"),
            is_spacia=bool(s.is_spacia),
            has_debris=bool(s.has_debris),
            has_asteroid=bool(s.has_asteroid),
            uninhabited_fields=s.uninhabited_fields,
            colonize_min_fields=s.colonize_min_fields,
            colonize_max_fields=s.colonize_max_fields,
            temperature=s.temperature,
            debris_pig_iron=int(s.debris_pig_iron),
            debris_metal=int(s.debris_metal),
            debris_kryptonite=int(s.debris_kryptonite),
            debris_spice=int(s.debris_spice),
        )
        for s in slots
    ]


class _IntelGatewayMixin(_GatewayCore):
    """Espionage targets/reports, debris enrichment & ranking, and galaxy scans/slots/sector intel."""

    def _build_debris_lookup(self, debris_coords: Sequence[str]) -> dict[str, tuple[int, int, int, int]]:
        if not debris_coords:
            return {}
        lookup: dict[str, tuple[int, int, int, int]] = {}
        raw_meta = self.load_meta_json("galaxy_debris_amounts")
        if isinstance(raw_meta, Mapping):
            for nc in debris_coords:
                entry = raw_meta.get(nc)
                if isinstance(entry, Sequence) and not isinstance(entry, (str, bytes)) and len(entry) >= 4:
                    dp, dm, dk, ds = (
                        cap_json_int(max(0, _safe_int(entry[0], 0))),
                        cap_json_int(max(0, _safe_int(entry[1], 0))),
                        cap_json_int(max(0, _safe_int(entry[2], 0))),
                        cap_json_int(max(0, _safe_int(entry[3], 0))),
                    )
                    if dp + dm + dk + ds > 0:
                        lookup[nc] = (dp, dm, dk, ds)
        missing = [c for c in debris_coords if c not in lookup]
        if not missing:
            return lookup
        with self._store.session() as session:
            lookup.update(query_battle_debris_fallbacks(session, missing))
            still_missing = [c for c in missing if c not in lookup]
            if still_missing:
                still_set = set(still_missing)
                for nc, ap, am, hp, hm, raw_txt in query_recycle_debris_candidates(session, still_missing):
                    if not nc or nc not in still_set or nc in lookup or is_non_recycle_fleet_notice_text(raw_txt):
                        continue
                    dp, dm = max(0, ap - hp), max(0, am - hm)
                    if dp + dm > 0:
                        lookup[nc] = (cap_json_int(dp), cap_json_int(dm), 0, 0)
        return lookup

    def _enrich_debris(self, targets: list[dict[str, object]]) -> None:
        coords = [str(t["coords"]) for t in targets if t.get("has_debris") and t.get("coords")]
        if not coords:
            return
        lookup = self._build_debris_lookup(coords)
        for t in targets:
            c = str(t.get("coords") or "")
            if t.get("has_debris") and c in lookup:
                dp, dm, dk, ds = lookup[c]
                t["debris_pig_iron"] = dp
                t["debris_metal"] = dm
                t["debris_kryptonite"] = dk
                t["debris_spice"] = ds
                t["debris_total"] = cap_json_int(dp + dm + dk + ds)

    def get_spy_targets(self, *, limit: int = 50, include_own: bool = False) -> list[dict[str, object]]:
        """Return prioritized espionage targets enriched with known debris amounts."""
        with self._store.session() as session:
            rows = query_spy_targets(session, limit=limit, include_own=include_own)
        targets = [spy_target_to_dict(r) for r in rows]
        self._enrich_debris(targets)
        return targets

    def get_spy_target_by_coords(self, coords: str) -> dict[str, object] | None:
        """Fetch one active non-own espionage target by coordinates."""
        with self._store.repositories() as repos:
            rec = repos.intel.get_target_by_coords_non_own(coords)
        return spy_target_to_dict(rec) if rec is not None else None

    def get_spy_reports(
        self, *, target_coords: str | None = None, coords: str | None = None, limit: int = 200
    ) -> list[dict[str, object]]:
        """List historical espionage reports (filtering non-spy inbox notices)."""
        eff_coords = target_coords if target_coords is not None else coords
        with self._store.repositories() as repos:
            rows = repos.intel.list_spy_reports(target_coords=eff_coords, limit=limit)
        return [spy_report_to_dict(r) for r in rows if not (r.raw_text and is_non_spy_notice_text(r.raw_text))]

    def record_spy_report(
        self,
        report: SpyReport,
        *,
        points: int | None = None,
        is_inactive: bool = False,
        status_color: str | None = None,
        ts: float | None = None,
    ) -> None:
        """Record one parsed espionage report and update `spy_targets` + hot tables."""
        norm_c = normalize_coords(str(report.target_coords).strip())
        if not norm_c or (report.raw_text and is_non_spy_notice_text(report.raw_text)):
            return
        rep_in = SpyReportInput(
            target_coords=norm_c,
            owner=report.owner,
            planet_name=report.planet_name,
            is_spacia=report.is_spacia,
            pig_iron=int(report.resources.pig_iron),
            metal=int(report.resources.metal),
            kryptonite=int(report.resources.kryptonite),
            spice=int(report.resources.spice),
            buildings=report.buildings,
            fleet=report.fleet,
            defense=report.defense,
            researches=report.researches,
            dug_in=report.dug_in,
            uninhabited_fields=report.uninhabited_fields,
            server_msg_id=report.server_msg_id,
            msg_date_text=report.msg_date_text,
            raw_text=report.raw_text,
        )
        with self._store.repositories() as repos:
            repos.intel.record_spy_report(
                rep_in, points=points, is_inactive=is_inactive, status_color=status_color, ts=ts
            )
        self._bump_change()

    def set_target_debris(self, target_coords: str, has_debris: bool) -> None:
        """Set or clear the debris flag on `spy_targets`, `galaxy_slots`, and debris metadata."""
        with self._store.repositories() as repos:
            repos.intel.set_target_debris(target_coords, has_debris=has_debris)
        self._bump_change()

    def get_active_debris_targets(
        self, *, origin_coords: Iterable[str] = (), limit: int = 100, include_own: bool = False
    ) -> list[dict[str, object]]:
        """Rank active debris fields by proximity to `origin_coords`, known debris total, and recency."""
        lim = max(1, _safe_int(limit, 100))
        parsed_origins = [gsp for c in origin_coords if (gsp := _parse_gsp(c)) is not None]
        with self._store.session() as session:
            rows = query_active_debris_targets_raw(session, include_own=include_own, limit=2000)
        lookup = self._build_debris_lookup([r.coords for r in rows if r.coords])

        def _sort_key(rec_idx: int) -> tuple[int, int, int, int, int, float]:
            rec = rows[rec_idx]
            d_tup = lookup.get(rec.coords)
            tot = sum(d_tup) if d_tup is not None else 0
            p_t = _parse_gsp(rec.coords)
            if parsed_origins and p_t is not None:
                tg, tsys, tp = p_t
                best = min(
                    (0 if tg == og else 1, abs(tg - og), abs(tsys - osys), abs(tp - opos))
                    for og, osys, opos in parsed_origins
                )
                return (best[0], best[1], best[2], best[3], -tot, -rec.last_scanned_ts)
            return (0, 0, 0, 0, -tot, -rec.last_scanned_ts)

        out: list[dict[str, object]] = []
        for idx in sorted(range(len(rows)), key=_sort_key)[:lim]:
            t = spy_target_to_dict(rows[idx])
            c = str(t["coords"])
            if t["has_debris"] and c in lookup:
                dp, dm, dk, ds = lookup[c]
                t["debris_pig_iron"] = dp
                t["debris_metal"] = dm
                t["debris_kryptonite"] = dk
                t["debris_spice"] = ds
                t["debris_total"] = cap_json_int(dp + dm + dk + ds)
            out.append(t)
        return out

    def get_galaxy_debris_amounts(self) -> dict[str, dict[str, int]]:
        """Return `{norm_coords: {pig_iron, metal, kryptonite, spice, total}}` from galaxy debris meta."""
        raw = self.load_meta_json("galaxy_debris_amounts")
        if not isinstance(raw, Mapping):
            return {}
        out: dict[str, dict[str, int]] = {}
        for k, v in raw.items():
            nc = normalize_coords(str(k))
            if not nc or not isinstance(v, Sequence) or isinstance(v, (str, bytes)) or len(v) < 4:
                continue
            dp, dm, dk, ds = (
                cap_json_int(max(0, _safe_int(v[0], 0))),
                cap_json_int(max(0, _safe_int(v[1], 0))),
                cap_json_int(max(0, _safe_int(v[2], 0))),
                cap_json_int(max(0, _safe_int(v[3], 0))),
            )
            if dp + dm + dk + ds > 0:
                out[nc] = {
                    "pig_iron": dp,
                    "metal": dm,
                    "kryptonite": dk,
                    "spice": ds,
                    "total": cap_json_int(dp + dm + dk + ds),
                }
        return out

    def get_galaxy_scans(
        self,
        *,
        max_age_seconds: float | None = None,
        now: float | None = None,
    ) -> dict[str, float]:
        """Return `{sector_key: last_scanned_ts}` for scanned galaxy systems."""
        with self._store.repositories() as repos:
            rows = repos.galaxy.list_galaxy_scans(max_age_seconds=max_age_seconds, now_ts=now)
        scans: dict[str, float] = {}
        for r in rows:
            if r.galaxy > 0 and r.system > 0:
                scans[r.sector_key] = max(scans.get(r.sector_key, r.last_scanned_ts), r.last_scanned_ts)
        return scans

    def get_galaxy_slots(
        self,
        *,
        galaxy: int | None = None,
        system: int | None = None,
        status_color: str | None = None,
        limit: int = 500,
    ) -> list[dict[str, object]]:
        """List scanned galaxy slots ordered by `(galaxy, system, position)`."""
        with self._store.repositories() as repos:
            rows = repos.galaxy.list_galaxy_slots(galaxy=galaxy, system=system, status_color=status_color, limit=limit)
        return [galaxy_slot_to_dict(r) for r in rows]

    def _should_append_slot_history(self, now_ts: float, slots: Sequence[GalaxySlot]) -> bool:
        sig = tuple(normalize_coords(str(s.coords).strip()) for s in slots)
        append = (
            self._last_slot_history_sig is None
            or abs(now_ts - self._last_slot_history_sig[0]) > 1.0
            or self._last_slot_history_sig[1] != sig
        )
        if slots and append:
            self._last_slot_history_sig = (now_ts, sig)
        return append

    def record_galaxy_slots(self, slots: Sequence[GalaxySlot], *, ts: float | None = None) -> int:
        """Persist scanned galaxy slots, sync `spy_targets` & debris amounts, and refresh hot tables."""
        now_ts = round(_safe_float(ts, time.time()) if ts is not None else time.time(), 3)
        append_hist = self._should_append_slot_history(now_ts, slots)
        with self._store.repositories() as repos:
            saved = repos.galaxy.record_galaxy_slots(
                _to_galaxy_slot_inputs(slots), append_slot_history=append_hist, ts=now_ts
            )
        self._bump_change()
        return saved

    def record_galaxy_scan(
        self, galaxy: int, system: int, slots: Sequence[GalaxySlot] = (), *, ts: float | None = None
    ) -> None:
        """Persist a full system scan with 1.0s slot-history deduplication."""
        now_ts = round(_safe_float(ts, time.time()) if ts is not None else time.time(), 3)
        append_hist = self._should_append_slot_history(now_ts, slots)
        with self._store.repositories() as repos:
            repos.galaxy.record_system_scan(
                galaxy, system, _to_galaxy_slot_inputs(slots), append_slot_history=append_hist, ts=now_ts
            )
        self._bump_change()

    def get_galaxy_sector_intel(
        self, *, galaxies: Sequence[int] | None = None, now_ts: float | None = None, max_galaxies: int = 14
    ) -> dict[str, object]:
        """Compute full G1-G14 sector intel, direct raids, and player attack affinity."""
        with self._store.session() as session:
            return query_galaxy_sector_intel_detailed(
                session, galaxies=galaxies, now_ts=now_ts, max_galaxies=max_galaxies
            )

    def rebuild_hot_tables(self, *, ts: float | None = None) -> dict[str, int]:
        """Rebuild hot/projection tables from append-only history tables."""
        with self._store.session() as session:
            counts = rebuild_hot_tables_sql(session, ts=ts)
        self._bump_change()
        return counts


__all__ = ["_IntelGatewayMixin"]
