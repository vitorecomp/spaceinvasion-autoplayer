"""Shared deterministic fixtures for Wave A `RuntimeDatabaseGateway` vs ORM parity tests."""

from __future__ import annotations

from typing import TypedDict

T0: float = 1700001000.0

CFG_OPT_RESULT: dict[str, object] = {
    "summary": {"ships_per_month": 42.5, "limiting_factor": "spice"},
    "meta_parameters": {"target_ship_key": "s16"},
}
CFG_OPT_PARAMS: dict[str, object] = {"target_ship_key": "s16", "planet_specializations": {"101": "spice"}}
CFG_FLOORS: dict[str, dict[str, int]] = {"101": {"b1": 18}}
AI_PLAN_RESULT: dict[str, object] = {
    "summary": {"discounted_reward_spice_eq": 9999.5, "total_net_spice_eq": 12000.0},
    "steps": [{"a": 1}, {"a": 2}],
}
AI_PLAN_PARAMS: dict[str, object] = {"reward_weeks": 26, "weekly_discount": 0.9}
CONFLICT_PAYLOAD: dict[str, object] = {
    "conflict_key": "101:b1:20",
    "planet_id": 101,
    "planet_coords": "2:380:12",
    "blocked_building_key": "1",
    "blocked_target_level": 20,
    "candidate_building_key": "b14",
    "candidate_level": 8,
    "created_ts": T0,
    "reason": "fields_full",
}


class NotifKwargs(TypedDict):
    title: str
    message: str
    severity: str
    action_kind: str
    planet_coords: str
    target_name: str
    dedupe_key: str
    details: dict[str, object] | None
    ts: float


class QueueKwargs(TypedDict):
    kind: str
    priority: str
    position: str
    planet_id: int
    planet_coords: str
    target_id: int | None
    target_name: str
    target_coords: str
    target_level: int | None
    note: str
    ts: float


class AttemptKwargs(TypedDict):
    target_coords: str
    event_type: str
    origin_coords: str
    planet_id: int | None
    fields_rolled: int | None
    min_fields_target: int
    temperature: int | None
    grade: str
    score: int
    status_message: str
    details: dict[str, object]
    ts: float


class ProdKwargs(TypedDict):
    pig_iron_per_hour: float
    metal_per_hour: float
    kryptonite_per_hour: float
    spice_per_hour: float
    total_per_hour: float
    speq_per_hour: float
    planet_count: int
    active_builders: int
    ts: float


def _mk_notif(
    title: str,
    message: str,
    severity: str,
    dedupe_key: str,
    ts: float,
    action_kind: str = "",
    planet_coords: str = "",
    target_name: str = "",
    details: dict[str, object] | None = None,
) -> NotifKwargs:
    return {
        "title": title,
        "message": message,
        "severity": severity,
        "action_kind": action_kind,
        "planet_coords": planet_coords,
        "target_name": target_name,
        "dedupe_key": dedupe_key,
        "details": details,
        "ts": ts,
    }


def _mk_queue(
    kind: str,
    priority: str,
    position: str,
    note: str,
    ts: float,
    target_id: int | None = None,
    target_name: str = "",
    target_coords: str = "",
    target_level: int | None = None,
) -> QueueKwargs:
    return {
        "kind": kind,
        "priority": priority,
        "position": position,
        "planet_id": 101,
        "planet_coords": "2:380:12",
        "target_id": target_id,
        "target_name": target_name,
        "target_coords": target_coords,
        "target_level": target_level,
        "note": note,
        "ts": ts,
    }


def _mk_attempt(
    ev: str,
    grade: str,
    score: int,
    msg: str,
    ats: float,
    pid: int | None = None,
    fields: int | None = None,
    temp: int | None = None,
) -> AttemptKwargs:
    return {
        "target_coords": "2:380:8",
        "event_type": ev,
        "origin_coords": "2:380:12",
        "planet_id": pid,
        "fields_rolled": fields,
        "min_fields_target": 300,
        "temperature": temp,
        "grade": grade,
        "score": score,
        "status_message": msg,
        "details": {"wave": 1},
        "ts": ats,
    }


def _mk_prod(pig: float, met: float, kry: float, spi: float, tot: float, speq: float, ts: float) -> ProdKwargs:
    return {
        "pig_iron_per_hour": pig,
        "metal_per_hour": met,
        "kryptonite_per_hour": kry,
        "spice_per_hour": spi,
        "total_per_hour": tot,
        "speq_per_hour": speq,
        "planet_count": 5,
        "active_builders": 2,
        "ts": ts,
    }


NOTIF_SPECS: tuple[NotifKwargs, ...] = (
    _mk_notif("Mine Blocked", "Need storage", "warning", "mine_1", T0, "build", "02:380:12", "Pig iron mine", {"n": 5}),
    _mk_notif("Mine Blocked", "Retry", "error", "mine_1", T0 + 10.0, "build", "02:380:12", "Pig iron mine", {"n": 6}),
    _mk_notif("Spy Ready", "Probes ready", "info", "spy_1", T0 + 15.0),
)
QUEUE_SPECS: tuple[QueueKwargs, ...] = (
    _mk_queue("build", "P1", "top", "first", T0, 1, "Pig iron mine", "", 16),
    _mk_queue("build", "P1", "bottom", "second", T0 + 1.0, 2, "Blast furnace", "", 12),
    _mk_queue("spy", "P2", "top", "scout", T0 + 2.0, None, "", "2:380:4", None),
)
ATTEMPT_SPECS: tuple[AttemptKwargs, ...] = (
    _mk_attempt("dispatched", "A", 92, "Sent", T0),
    _mk_attempt("kept", "S", 98, "Kept", T0 + 120.0, 202, 335, 32),
)
PROD_SAMPLES: tuple[ProdKwargs, ...] = (
    _mk_prod(1000.4, 500.2, 300.1, 200.6, 2001.3, 1500.5, T0),
    _mk_prod(9999.0, 9999.0, 9999.0, 9999.0, 9999.0, 9999.0, T0 + 5.0),
)
CFG_SNAPSHOTS: tuple[tuple[dict[str, object], float], ...] = (
    ({"planner_mode": "ai_planner", "raid_enabled": True}, 0.0),
    ({"planner_mode": "ai_planner", "raid_enabled": True}, 5.0),
    ({"planner_mode": "priority", "raid_enabled": False}, 10.0),
)
