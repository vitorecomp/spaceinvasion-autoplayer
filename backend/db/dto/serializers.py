"""Pure DTO-to-dict serializers preserving exact SpaceInvasion HTTP API JSON shapes."""

from __future__ import annotations

import json

from backend.core.catalog import cap_json_int
from backend.core.redaction import redact_sensitive, redact_text
from backend.db.dto.combat import (
    BattleReportRecord,
    GameMessageRecord,
    RecycleReportRecord,
)
from backend.db.dto.telemetry import (
    ActionHistoryRecord,
    ColonyAttemptRecord,
    HourlyProductionRecord,
    LootRecycleHourlyRecord,
    LootRecycleWindowSummary,
    RankSnapshotRecord,
    TutorialProgressRecord,
    UniverseResearchMaxRecord,
)
from backend.db.dto.world import (
    GalaxySlotRecord,
    SpyReportDTO,
    SpyTargetRecord,
)


def _rs(val: str | None) -> str:
    return redact_text(val) if val else ""


def _rs_opt(val: str | None) -> str | None:
    return redact_text(val) if val is not None else None


def _loads_json(raw: str) -> object:
    if not raw or not raw.strip():
        return None
    try:
        return redact_sensitive(json.loads(raw))
    except ValueError:
        return None


def _loads_dict(raw: str) -> dict[str, object]:
    clean = _loads_json(raw)
    return {redact_text(str(k)): v for k, v in clean.items()} if isinstance(clean, dict) else {}


def _loads_list(raw: str) -> list[object]:
    clean = _loads_json(raw)
    return list(clean) if isinstance(clean, list) else []


def rank_sample_to_dict(rec: RankSnapshotRecord) -> dict[str, object]:
    """Serialize `RankSnapshotRecord` to the 7-key API rank history shape (no PII)."""
    return {
        "ts": rec.ts,
        "server_points": cap_json_int(rec.server_points) if rec.server_points is not None else None,
        "server_rank": cap_json_int(rec.server_rank) if rec.server_rank is not None else None,
        "estimated_points": rec.estimated_points,
        "pts_per_hr": rec.pts_per_hr,
        "spice_value": rec.spice_value,
        "planets": cap_json_int(max(0, rec.planets)),
    }


def recent_action_to_dict(rec: ActionHistoryRecord) -> dict[str, object]:
    """Serialize `ActionHistoryRecord` to the exact 12-key `summary()['recent_actions']` shape."""
    return {
        "id": cap_json_int(rec.id),
        "ts": rec.ts,
        "source": _rs(rec.source),
        "command": _rs(rec.command),
        "priority": _rs_opt(rec.priority),
        "kind": _rs(rec.kind),
        "planet_coords": _rs_opt(rec.planet_coords),
        "target_name": _rs_opt(rec.target_name),
        "target_level": cap_json_int(rec.target_level) if rec.target_level is not None else None,
        "executed": 1 if rec.executed else 0,
        "dry_run": 1 if rec.dry_run else 0,
        "message": _rs_opt(rec.message),
    }


def tutorial_progress_to_dict(rec: TutorialProgressRecord) -> dict[str, object]:
    """Serialize `TutorialProgressRecord` with decoded `tasks` list."""
    return {
        "step": cap_json_int(rec.step),
        "total_steps": cap_json_int(rec.total_steps),
        "tasks": _loads_list(rec.tasks_json),
        "reward_text": _rs(rec.reward_text),
        "can_complete": bool(rec.can_complete),
        "first_seen_ts": rec.first_seen_ts,
        "last_seen_ts": rec.last_seen_ts,
        "completed_ts": rec.completed_ts,
    }


def hourly_production_to_dict(rec: HourlyProductionRecord) -> dict[str, object]:
    """Serialize `HourlyProductionRecord` for dashboard economy charts."""
    return {
        "id": cap_json_int(rec.id),
        "minute_bucket": cap_json_int(rec.minute_bucket),
        "recorded_at": rec.recorded_at,
        "pig_iron_per_hour": rec.pig_iron_per_hour,
        "metal_per_hour": rec.metal_per_hour,
        "kryptonite_per_hour": rec.kryptonite_per_hour,
        "spice_per_hour": rec.spice_per_hour,
        "total_per_hour": rec.total_per_hour,
        "speq_per_hour": rec.speq_per_hour,
        "planet_count": cap_json_int(rec.planet_count),
        "active_builders": cap_json_int(rec.active_builders),
    }


def loot_recycle_window_to_dict(
    history: tuple[LootRecycleHourlyRecord, ...],
    summary: LootRecycleWindowSummary,
) -> dict[str, object]:
    """Serialize hourly loot/recycle history buckets + summary dict."""
    return {
        "history": [
            {
                "hour_bucket": cap_json_int(item.hour_bucket),
                "stolen_pig_iron": item.stolen_pig_iron,
                "stolen_metal": item.stolen_metal,
                "stolen_kryptonite": item.stolen_kryptonite,
                "stolen_spice": item.stolen_spice,
                "stolen_total": item.stolen_total,
                "recycled_pig_iron": item.recycled_pig_iron,
                "recycled_metal": item.recycled_metal,
                "recycled_kryptonite": item.recycled_kryptonite,
                "recycled_spice": item.recycled_spice,
                "recycled_total": item.recycled_total,
                "total": round(item.stolen_total + item.recycled_total, 1),
                "attack_count": cap_json_int(item.attack_count),
                "recycle_count": cap_json_int(item.recycle_count),
                "updated_at": item.updated_at,
            }
            for item in history
        ],
        "summary": {
            "total_stolen": summary.total_stolen,
            "total_recycled": summary.total_recycled,
            "grand_total": summary.grand_total,
            "attack_count": cap_json_int(summary.attack_count),
            "recycle_count": cap_json_int(summary.recycle_count),
            "window_hours": cap_json_int(summary.window_hours),
            "stolen_pig_iron": summary.stolen_pig_iron,
            "stolen_metal": summary.stolen_metal,
            "stolen_kryptonite": summary.stolen_kryptonite,
            "stolen_spice": summary.stolen_spice,
            "recycled_pig_iron": summary.recycled_pig_iron,
            "recycled_metal": summary.recycled_metal,
            "recycled_kryptonite": summary.recycled_kryptonite,
            "recycled_spice": summary.recycled_spice,
        },
    }


def research_max_to_dict(rec: UniverseResearchMaxRecord) -> dict[str, object]:
    """Serialize `UniverseResearchMaxRecord` to the API dictionary shape."""
    return {
        "research_id": cap_json_int(rec.research_id),
        "key": rec.research_key,
        "slug": rec.slug,
        "name": rec.name,
        "own_level": cap_json_int(rec.own_level),
        "universe_max_level": cap_json_int(rec.universe_max_level),
        "spied_max_level": cap_json_int(rec.spied_max_level),
        "server_record_level": cap_json_int(rec.server_record_level) if rec.server_record_level is not None else None,
        "catalog_max_level": cap_json_int(rec.catalog_max_level) if rec.catalog_max_level is not None else None,
        "gap_to_max": cap_json_int(rec.gap_to_max),
        "holder": rec.holder,
        "holder_coords": rec.holder_coords,
        "updated_ts": rec.updated_ts,
    }


def spy_target_to_dict(rec: SpyTargetRecord) -> dict[str, object]:
    """Serialize `SpyTargetRecord` to the exact 23-key espionage target API shape."""
    return {
        "coords": _rs(rec.coords),
        "owner": _rs(rec.owner),
        "planet_name": _rs(rec.planet_name),
        "status_color": _rs(rec.status_color),
        "is_inactive": bool(rec.is_inactive),
        "is_spacia": bool(rec.is_spacia),
        "has_debris": bool(rec.has_debris),
        "points": cap_json_int(rec.points) if rec.points is not None else None,
        "resources": {
            "pig_iron": cap_json_int(max(0, rec.pig_iron)),
            "metal": cap_json_int(max(0, rec.metal)),
            "kryptonite": cap_json_int(max(0, rec.kryptonite)),
            "spice": cap_json_int(max(0, rec.spice)),
        },
        "fleet": _loads_dict(rec.fleet_json),
        "defense": _loads_dict(rec.defense_json),
        "dug_in": bool(rec.dug_in),
        "last_scanned_ts": rec.last_scanned_ts,
        "last_spied_ts": rec.last_spied_ts,
        "has_spy_intel": rec.last_spied_ts > 0.0,
        "blocked_until_ts": rec.blocked_until_ts,
        "block_reason": _rs(rec.block_reason) if rec.blocked_until_ts > 0.0 else "",
        "bash_hits": cap_json_int(max(0, rec.bash_hits)),
        "last_blocked_ts": rec.last_blocked_ts,
        "uninhabited_fields": cap_json_int(rec.uninhabited_fields) if rec.uninhabited_fields is not None else None,
        "colonize_min_fields": cap_json_int(rec.colonize_min_fields) if rec.colonize_min_fields is not None else None,
        "colonize_max_fields": cap_json_int(rec.colonize_max_fields) if rec.colonize_max_fields is not None else None,
        "temperature": cap_json_int(rec.temperature) if rec.temperature is not None else None,
        "spy_count": cap_json_int(max(0, rec.spy_count)),
    }


def spy_report_to_dict(rec: SpyReportDTO) -> dict[str, object]:
    """Serialize `SpyReportDTO` with both `coords` and `target_coords` alias keys."""
    tc = _rs(rec.target_coords)
    return {
        "id": cap_json_int(rec.id),
        "ts": rec.ts,
        "coords": tc,
        "target_coords": tc,
        "owner": _rs(rec.owner),
        "planet_name": _rs(rec.planet_name),
        "is_spacia": bool(rec.is_spacia),
        "resources": {
            "pig_iron": cap_json_int(max(0, rec.pig_iron)),
            "metal": cap_json_int(max(0, rec.metal)),
            "kryptonite": cap_json_int(max(0, rec.kryptonite)),
            "spice": cap_json_int(max(0, rec.spice)),
        },
        "loot_spice_eq": rec.loot_spice_eq,
        "buildings": _loads_dict(rec.buildings_json),
        "fleet": _loads_dict(rec.fleet_json),
        "defense": _loads_dict(rec.defense_json),
        "researches": _loads_dict(rec.researches_json),
        "dug_in": rec.dug_in,
        "uninhabited_fields": cap_json_int(rec.uninhabited_fields) if rec.uninhabited_fields is not None else None,
        "server_msg_id": _rs(rec.server_msg_id),
        "msg_date_text": _rs(rec.msg_date_text),
        "raw_text": _rs(rec.raw_text),
    }


def battle_report_to_dict(rec: BattleReportRecord) -> dict[str, object]:
    """Serialize `BattleReportRecord` with legacy compatibility alias keys."""
    att_lost = cap_json_int(max(0, rec.attacker_lost_spice))
    def_lost = cap_json_int(max(0, rec.defender_lost_spice))
    tc = _rs(rec.target_coords)
    att = _rs(rec.attacker)
    dfn = _rs(rec.defender)
    return {
        "id": cap_json_int(rec.id),
        "ts": rec.ts,
        "msg_uid": _rs(rec.msg_uid),
        "server_msg_id": _rs(rec.server_msg_id),
        "battle_id": _rs(rec.battle_id),
        "msg_date_text": _rs(rec.msg_date_text),
        "target_coords": tc,
        "coords": tc,
        "attacker": att,
        "attacker_name": att,
        "defender": dfn,
        "defender_name": dfn,
        "outcome": _rs(rec.outcome),
        "rounds": cap_json_int(rec.rounds),
        "loot": {
            "pig_iron": cap_json_int(max(0, rec.pig_iron)),
            "metal": cap_json_int(max(0, rec.metal)),
            "kryptonite": cap_json_int(max(0, rec.kryptonite)),
            "spice": cap_json_int(max(0, rec.spice)),
            "spice_eq": rec.loot_spice_eq,
        },
        "loot_spice_eq": rec.loot_spice_eq,
        "attacker_lost_spice": att_lost,
        "defender_lost_spice": def_lost,
        "attacker_losses": {"spice_eq": att_lost},
        "defender_losses": {"spice_eq": def_lost},
        "debris": {
            "pig_iron": cap_json_int(max(0, rec.debris_pig_iron)),
            "metal": cap_json_int(max(0, rec.debris_metal)),
        },
        "debris_spice_eq": rec.debris_spice_eq,
        "raw_text": _rs(rec.raw_text),
    }


def recycle_report_to_dict(rec: RecycleReportRecord) -> dict[str, object]:
    """Serialize `RecycleReportRecord` to the exact recycler API shape."""
    hpig, hmet = cap_json_int(max(0, rec.harvested_pig_iron)), cap_json_int(max(0, rec.harvested_metal))
    hkry, hspi = cap_json_int(max(0, rec.harvested_kryptonite)), cap_json_int(max(0, rec.harvested_spice))
    fpig, fmet = cap_json_int(max(0, rec.debris_avail_pig)), cap_json_int(max(0, rec.debris_avail_metal))
    return {
        "id": cap_json_int(rec.id),
        "ts": rec.ts,
        "msg_uid": _rs(rec.msg_uid),
        "server_msg_id": _rs(rec.server_msg_id),
        "msg_date_text": _rs(rec.msg_date_text),
        "coords": _rs(rec.coords),
        "recycler_count": cap_json_int(max(0, rec.recycler_count)),
        "cargo_capacity": cap_json_int(max(0, rec.cargo_capacity)),
        "field_pig_iron": fpig,
        "field_metal": fmet,
        "debris_available": {"pig_iron": fpig, "metal": fmet},
        "harvested_pig_iron": hpig,
        "harvested_metal": hmet,
        "harvested_kryptonite": hkry,
        "harvested_spice": hspi,
        "harvested": {"pig_iron": hpig, "metal": hmet, "kryptonite": hkry, "spice": hspi},
        "harvested_spice_eq": rec.harvested_spice_eq,
        "spice_eq": rec.harvested_spice_eq,
        "raw_text": _rs(rec.raw_text),
    }


def game_message_to_dict(rec: GameMessageRecord) -> dict[str, object]:
    """Serialize `GameMessageRecord` with decoded `parsed` dict."""
    return {
        "id": cap_json_int(rec.id),
        "ingested_ts": rec.ingested_ts,
        "msg_uid": _rs(rec.msg_uid),
        "server_msg_id": _rs(rec.server_msg_id),
        "folder_type": _rs(rec.folder_type),
        "category": _rs(rec.category),
        "msg_date_text": _rs(rec.msg_date_text),
        "sender": _rs(rec.sender),
        "subject": _rs(rec.subject),
        "coords": _rs(rec.coords),
        "body_text": _rs(rec.body_text),
        "raw_html": _rs(rec.raw_html),
        "parsed": _loads_dict(rec.parsed_json),
        "deleted_in_game": bool(rec.deleted_in_game),
        "deleted_ts": rec.deleted_ts,
    }


def galaxy_slot_to_dict(rec: GalaxySlotRecord) -> dict[str, object]:
    """Serialize `GalaxySlotRecord` to the exact galaxy catalog API shape."""
    return {
        "coords": _rs(rec.coords),
        "galaxy": cap_json_int(rec.galaxy),
        "system": cap_json_int(rec.system),
        "position": cap_json_int(rec.position),
        "planet_name": _rs(rec.planet_name),
        "owner": _rs(rec.owner),
        "status_color": _rs(rec.status_color),
        "is_inactive": bool(rec.is_inactive),
        "is_spacia": bool(rec.is_spacia),
        "has_debris": bool(rec.has_debris),
        "last_scanned_ts": rec.last_scanned_ts,
        "colonize_min_fields": cap_json_int(rec.colonize_min_fields) if rec.colonize_min_fields is not None else None,
        "colonize_max_fields": cap_json_int(rec.colonize_max_fields) if rec.colonize_max_fields is not None else None,
        "temperature": cap_json_int(rec.temperature) if rec.temperature is not None else None,
    }


def colony_attempt_to_dict(rec: ColonyAttemptRecord) -> dict[str, object]:
    """Serialize `ColonyAttemptRecord` to the exact colonization history API shape."""
    return {
        "id": cap_json_int(rec.id),
        "ts": rec.ts,
        "event_type": _rs(rec.event_type),
        "target_coords": _rs(rec.target_coords),
        "origin_coords": _rs(rec.origin_coords),
        "planet_id": cap_json_int(rec.planet_id) if rec.planet_id is not None else None,
        "fields_rolled": cap_json_int(rec.fields_rolled) if rec.fields_rolled is not None else None,
        "min_fields_target": cap_json_int(rec.min_fields_target),
        "expected_min_fields": cap_json_int(rec.expected_min_fields) if rec.expected_min_fields is not None else None,
        "expected_max_fields": cap_json_int(rec.expected_max_fields) if rec.expected_max_fields is not None else None,
        "temperature": cap_json_int(rec.temperature) if rec.temperature is not None else None,
        "grade": _rs(rec.grade),
        "score": cap_json_int(rec.score) if rec.score is not None else None,
        "status_message": _rs(rec.status_message),
        "details": _loads_dict(rec.details_json),
    }


__all__ = [
    "battle_report_to_dict",
    "colony_attempt_to_dict",
    "galaxy_slot_to_dict",
    "game_message_to_dict",
    "hourly_production_to_dict",
    "loot_recycle_window_to_dict",
    "rank_sample_to_dict",
    "recent_action_to_dict",
    "recycle_report_to_dict",
    "research_max_to_dict",
    "spy_report_to_dict",
    "spy_target_to_dict",
    "tutorial_progress_to_dict",
]
