"""State snapshots, rank history, economy rates, hourly production, and action history mixin."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import asdict, is_dataclass
import time

from spaceinvasion.combat._points_helpers import estimate_hourly_economy
from spaceinvasion.combat.points import estimate_account_points
from spaceinvasion.core import catalog as game_data
from spaceinvasion.core.coords import normalize_coords
from spaceinvasion.core.model import GameState, game_state_from_dict, normalize_planet_type
from spaceinvasion.db.dto.serializers import (
    hourly_production_to_dict,
    loot_recycle_window_to_dict,
    rank_sample_to_dict,
    recent_action_to_dict,
    tutorial_progress_to_dict,
)
from spaceinvasion.db.queries import query_last_throttle_timestamps, query_needs_loot_recycle_backfill
from spaceinvasion.db.repos import (
    PlanetSnapshotInput,
    RankSnapshotInput,
    StateSnapshotInput,
    refresh_colony_candidates_hot,
    refresh_galaxy_sectors_hot,
)
from spaceinvasion.runtime.bridge._db_gateway_common import (
    _clean_str,
    _GatewayCore,
    _loads_dict,
    _loads_list,
    _safe_float,
    _safe_int,
)


def _to_planet_inputs(state: GameState) -> list[PlanetSnapshotInput]:
    return [
        PlanetSnapshotInput(
            planet_id=int(p.planet_id),
            coords=str(p.coords),
            name=str(p.name),
            planet_type=normalize_planet_type(p),
            temperature=int(p.temperature),
            fields_used=int(p.fields_used),
            fields_max=int(p.fields_max),
            pig_iron=int(p.resources.pig_iron),
            metal=int(p.resources.metal),
            kryptonite=int(p.resources.kryptonite),
            spice=int(p.resources.spice),
            energy_produced=int(p.energy.produced),
            energy_used=int(p.energy.used),
            buildings={str(k): int(v) for k, v in p.buildings.items()},
            ships={str(k): int(v) for k, v in p.ships.items()},
            defense={str(k): int(v) for k, v in p.defense.items()},
            economy_rates={str(k): int(v) for k, v in p.economy_rates.items()},
        )
        for p in state.planets
    ]


class _StateGatewayMixin(_GatewayCore):
    """Empire snapshots, rank progression, economy rates, hourly production, and actions."""

    def record_state_snapshot(
        self,
        state: GameState,
        *,
        planet_min_interval_seconds: float = 120.0,
        rank_min_interval_seconds: float = 300.0,
    ) -> list[dict[str, object]]:
        """Persist rank, planet, economy, tutorial, hot empire, and latest GameState atomically."""
        est = estimate_account_points(state)
        econ = estimate_hourly_economy(state)
        now_ts = _safe_float(state.timestamp, 0.0)
        if now_ts <= 0.0:
            now_ts = time.time()
        planet_inputs = _to_planet_inputs(state)
        own_coords = [nc for p in state.planets if (nc := normalize_coords(str(p.coords).strip()))]
        latest_meta = asdict(state)
        latest_meta.pop("raid_stats", None)
        latest_meta.pop("raid_targets", None)

        with self._store.repositories() as repos:
            repos.snapshots.record_rank_snapshot(
                RankSnapshotInput(
                    universe_id=int(state.universe_id),
                    nickname=str(state.nickname),
                    server_points=state.account_points,
                    server_rank=state.account_rank,
                    estimated_points=_safe_float(est.get("estimated_points"), 0.0),
                    pts_per_hr=_safe_float(econ.get("pts_per_hr_steady"), 0.0),
                    spice_value=_safe_float(est.get("spice_value"), 0.0),
                    planets=len(state.planets),
                    ts=now_ts,
                ),
                min_interval_seconds=rank_min_interval_seconds,
            )
            repos.snapshots.record_planet_snapshots(
                planet_inputs,
                researches={str(k): int(v) for k, v in state.researches.items()},
                universe_id=int(state.universe_id),
                planet_min_interval_seconds=planet_min_interval_seconds,
                ts=now_ts,
            )
            if state.tutorial is not None:
                tut = state.tutorial
                tasks_payload = [
                    {
                        "description": t.description,
                        "category": t.category,
                        "current": game_data.cap_json_int(_safe_int(t.current, 0)),
                        "target": game_data.cap_json_int(_safe_int(t.target, 0)),
                        "completed": bool(t.completed),
                    }
                    for t in tut.tasks
                ]
                repos.telemetry.record_tutorial_progress(
                    step=int(tut.step),
                    total_steps=int(tut.total_steps),
                    tasks=tasks_payload,
                    reward_text=str(tut.reward_text),
                    can_complete=bool(tut.can_complete),
                    ts=now_ts,
                )
            else:
                repos.telemetry.complete_open_tutorial_steps(ts=now_ts)
            if own_coords:
                repos.intel.mark_own_planets(own_coords)
            touched = repos.snapshots.sync_empire_planets_hot(
                planet_inputs, universe_id=int(state.universe_id), ts=now_ts
            )
            if own_coords:
                refresh_colony_candidates_hot(repos.session, own_coords, ts=now_ts)
            if touched:
                refresh_galaxy_sectors_hot(repos.session, touched, ts=now_ts)
            repos.settings.upsert_meta_json("latest_game_state", latest_meta, ts=now_ts)

        self._bump_change()
        return self.get_rank_samples()

    def record_full_state_snapshot(
        self,
        state: GameState,
        plan: Sequence[object] = (),
        *,
        source: str = "advisor",
        min_interval_seconds: float = 0.0,
    ) -> int:
        """Persist a full `GameState` + action `plan` snapshot into `state_snapshots` and `latest_state_hot`."""
        now_ts = _safe_float(state.timestamp, 0.0)
        if now_ts <= 0.0:
            now_ts = time.time()
        plan_payload: list[object] = [
            asdict(a)
            if (is_dataclass(a) and not isinstance(a, type))
            else (dict(a) if isinstance(a, Mapping) else str(a))
            for a in plan
        ]
        snap_in = StateSnapshotInput(
            source=source,
            universe_id=int(state.universe_id),
            user_id=int(state.user_id),
            nickname=str(state.nickname),
            active_planet_id=int(state.active_planet_id or 0),
            planet_count=len(state.planets),
            state_payload=asdict(state),
            plan_payload=plan_payload,
            ts=now_ts,
        )
        with self._store.repositories() as repos:
            row_id = repos.snapshots.record_full_state_snapshot(snap_in, min_interval_seconds=min_interval_seconds)
        self._bump_change()
        return row_id

    def get_latest_state_hot(self, *, include_payload: bool = True) -> dict[str, object] | None:
        """Read latest operational state snapshot from `latest_state_hot` (fallback `state_snapshots`)."""
        with self._store.repositories() as repos:
            rec = repos.snapshots.get_latest_state_hot(include_payload=include_payload)
        if rec is None:
            return None
        out: dict[str, object] = {
            "id": game_data.cap_json_int(rec.source_snapshot_id),
            "ts": rec.ts,
            "source": rec.source,
            "universe_id": game_data.cap_json_int(rec.universe_id),
            "user_id": game_data.cap_json_int(rec.user_id),
            "nickname": rec.nickname,
            "active_planet_id": game_data.cap_json_int(rec.active_planet_id),
            "planet_count": game_data.cap_json_int(rec.planet_count),
            "planned_action_count": game_data.cap_json_int(rec.planned_action_count),
        }
        if include_payload:
            out["state"] = _loads_dict(rec.state_json or "{}")
            out["plan"] = _loads_list(rec.plan_json or "[]")
        return out

    def get_state_snapshots(self, *, limit: int = 20, include_payload: bool = False) -> list[dict[str, object]]:
        """List recent full state/plan snapshots newest first."""
        with self._store.repositories() as repos:
            rows = repos.snapshots.list_state_snapshots(limit=limit, include_payload=include_payload)
        out: list[dict[str, object]] = []
        for rec in rows:
            item: dict[str, object] = {
                "id": game_data.cap_json_int(rec.id),
                "ts": rec.ts,
                "source": rec.source,
                "universe_id": game_data.cap_json_int(rec.universe_id),
                "user_id": game_data.cap_json_int(rec.user_id),
                "nickname": rec.nickname,
                "active_planet_id": game_data.cap_json_int(rec.active_planet_id),
                "planet_count": game_data.cap_json_int(rec.planet_count),
                "planned_action_count": game_data.cap_json_int(rec.planned_action_count),
            }
            if include_payload:
                item["state"] = _loads_dict(rec.state_json or "{}")
                item["plan"] = _loads_list(rec.plan_json or "[]")
            out.append(item)
        return out

    def get_recent_actions(self, limit: int = 25) -> list[dict[str, object]]:
        """Return recent commander action audit records ordered newest first."""
        with self._store.repositories() as repos:
            rows = repos.telemetry.list_recent_actions(limit=limit)
        return [recent_action_to_dict(r) for r in rows]

    def get_tutorial_history(self) -> list[dict[str, object]]:
        """Return persisted tutorial milestone progression ordered by step."""
        with self._store.repositories() as repos:
            rows = repos.telemetry.list_tutorial_progress()
        return [tutorial_progress_to_dict(r) for r in rows]

    def load_latest_game_state(self) -> GameState | None:
        """Load the latest persisted `GameState` from `meta['latest_game_state']`."""
        raw = self.load_meta_json("latest_game_state")
        if not isinstance(raw, Mapping):
            return None
        try:
            return game_state_from_dict({str(k): v for k, v in raw.items()})
        except (KeyError, TypeError, ValueError):
            return None

    def get_rank_samples(self, *, limit: int = 240) -> list[dict[str, object]]:
        """Return chronological rank history samples (without PII)."""
        with self._store.repositories() as repos:
            rows = repos.snapshots.list_rank_history(limit=limit)
        return [rank_sample_to_dict(r) for r in rows]

    def save_economy_rates(
        self, planet_id: int, coords: str, rates: Mapping[str, int], ts: float | None = None
    ) -> None:
        """Persist cached building production throttle percentages for one planet."""
        if not rates:
            return
        clean = {game_data.safe_key(k): _safe_int(v, 0) for k, v in rates.items()}
        with self._store.repositories() as repos:
            repos.telemetry.upsert_economy_rates(planet_id, coords, clean, ts=ts)
        self._bump_change()

    def get_all_economy_rates(self) -> dict[int, dict[str, int]]:
        """Return `{planet_id: {building_key: pct}}` across all cached planets."""
        with self._store.repositories() as repos:
            records = repos.telemetry.get_all_economy_rates()
        return {
            game_data.cap_json_int(pid): {
                _clean_str(game_data.safe_key(k)): game_data.cap_json_int(v) for k, v in rec.rates.items()
            }
            for pid, rec in records.items()
        }

    def record_hourly_production(
        self,
        pig_iron_per_hour: float = 0.0,
        metal_per_hour: float = 0.0,
        kryptonite_per_hour: float = 0.0,
        spice_per_hour: float = 0.0,
        total_per_hour: float = 0.0,
        speq_per_hour: float = 0.0,
        planet_count: int = 0,
        active_builders: int = 0,
        ts: float | None = None,
    ) -> dict[str, object]:
        """Record at most one empire production sample per UTC minute bucket."""
        with self._store.repositories() as repos:
            rec, inserted = repos.telemetry.record_hourly_production(
                pig_iron_per_hour=pig_iron_per_hour,
                metal_per_hour=metal_per_hour,
                kryptonite_per_hour=kryptonite_per_hour,
                spice_per_hour=spice_per_hour,
                total_per_hour=total_per_hour,
                speq_per_hour=speq_per_hour,
                planet_count=planet_count,
                active_builders=active_builders,
                ts=ts,
            )
        if inserted:
            self._bump_change()
        out = hourly_production_to_dict(rec)
        out.pop("id", None)
        out["inserted"] = inserted
        return out

    def get_hourly_production_history(self, limit: int = 120) -> list[dict[str, object]]:
        """Return chronological per-minute empire production snapshots."""
        with self._store.repositories() as repos:
            rows = repos.telemetry.list_hourly_production(limit=limit)
        out: list[dict[str, object]] = []
        for rec in rows:
            item = hourly_production_to_dict(rec)
            item.pop("id", None)
            out.append(item)
        return out

    def get_loot_recycle_hourly(self, *, hours: int = 24, now_ts: float | None = None) -> dict[str, object]:
        """Return continuous chronological hourly loot + recycle series and KPI summary."""
        with self._store.repositories() as repos:
            if query_needs_loot_recycle_backfill(repos.session):
                repos.telemetry.refresh_loot_recycle_hourly(ts=now_ts)
            history, summary = repos.telemetry.get_loot_recycle_hourly(hours=hours, now_ts=now_ts)
        return loot_recycle_window_to_dict(history, summary)

    def get_last_throttle_timestamps(self, *, building_id: int = 2) -> dict[int, float]:
        """Return `{planet_id: last_ts}` for live executed `THROTTLE_BUILDING` actions."""
        with self._store.session() as session:
            return query_last_throttle_timestamps(session, building_id=building_id)

    def record_action(
        self,
        *,
        source: str,
        command: str,
        action: object = None,
        executed: bool = False,
        dry_run: bool = False,
        message: str = "",
        ts: float | None = None,
    ) -> None:
        """Append one commander action audit record to `action_history`."""
        act_map: dict[str, object]
        if is_dataclass(action) and not isinstance(action, type):
            act_map = asdict(action)
        elif isinstance(action, Mapping):
            act_map = {str(k): v for k, v in action.items()}
        else:
            act_map = {}
        with self._store.repositories() as repos:
            repos.telemetry.record_action(
                source=source,
                command=command,
                priority=_clean_str(act_map["priority"]) if act_map.get("priority") is not None else None,
                kind=_clean_str(act_map.get("kind")) or _clean_str(command).upper(),
                planet_id=_safe_int(act_map["planet_id"]) if act_map.get("planet_id") is not None else None,
                planet_coords=_clean_str(act_map["planet_coords"])
                if act_map.get("planet_coords") is not None
                else None,
                target_id=_safe_int(act_map["target_id"]) if act_map.get("target_id") is not None else None,
                target_name=_clean_str(act_map["target_name"]) if act_map.get("target_name") is not None else None,
                target_level=_safe_int(act_map["target_level"]) if act_map.get("target_level") is not None else None,
                executed=executed,
                dry_run=dry_run,
                reason=_clean_str(act_map.get("reason")),
                message=message,
                ts=ts,
            )
        self._bump_change()


__all__ = ["_StateGatewayMixin"]
