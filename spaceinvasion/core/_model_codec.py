"""JSON dict serialization and deserialization codecs for ``spaceinvasion.core.model``."""

from __future__ import annotations

from collections.abc import Mapping
import dataclasses

from spaceinvasion.core._model_helpers import (
    deep_plain_value,
    normalize_planet_type,
    safe_optional_state_str,
    safe_state_str,
)
from spaceinvasion.core._model_types import (
    BuildingOffer,
    EnergyBalance,
    FleetAlert,
    GameState,
    PlanetState,
    QueueJob,
    ResearchOffer,
    Resources,
    TutorialStatus,
    TutorialTask,
    UnitOffer,
)
from spaceinvasion.core.coercion import (
    cap_json_int,
    coerce_int,
    coerce_optional_finite_float,
    coerce_optional_int,
    safe_key,
    safe_sequence,
)
from spaceinvasion.core.coords import extract_coords_from_text


def _resources_from_mapping(raw: object) -> Resources:
    if not isinstance(raw, Mapping):
        return Resources()
    return Resources(
        pig_iron=max(0, coerce_int(raw.get("pig_iron", 0), 0)),
        metal=max(0, coerce_int(raw.get("metal", 0), 0)),
        kryptonite=max(0, coerce_int(raw.get("kryptonite", 0), 0)),
        spice=max(0, coerce_int(raw.get("spice", 0), 0)),
    )


def _energy_from_mapping(raw: object) -> EnergyBalance:
    if not isinstance(raw, Mapping):
        return EnergyBalance()
    return EnergyBalance(
        produced=max(0, coerce_int(raw.get("produced", 0), 0)),
        used=max(0, coerce_int(raw.get("used", 0), 0)),
    )


def _parse_unit_offers(raw_seq: object, default_type: str) -> tuple[UnitOffer, ...]:
    out: list[UnitOffer] = []
    for item in safe_sequence(raw_seq):
        if not isinstance(item, Mapping):
            continue
        out.append(
            UnitOffer(
                unit_type=safe_state_str(item.get("unit_type") or default_type),
                unit_id=max(0, coerce_int(item.get("unit_id", 0), 0)),
                name=safe_state_str(item.get("name") or ""),
                owned=max(0, coerce_int(item.get("owned", 0), 0)),
                cost=_resources_from_mapping(item.get("cost")),
                can_build=bool(item.get("can_build", False)),
                input_name=safe_optional_state_str(item.get("input_name")),
                status_text=safe_state_str(item.get("status_text") or ""),
            )
        )
    return tuple(out)


def _planet_from_mapping(raw_p: Mapping[object, object]) -> PlanetState:
    offers: list[BuildingOffer] = []
    for o in safe_sequence(raw_p.get("building_offers")):
        if not isinstance(o, Mapping):
            continue
        cur_lvl = max(0, coerce_int(o.get("current_level", 0), 0))
        offers.append(
            BuildingOffer(
                building_id=max(0, coerce_int(o.get("building_id", 0), 0)),
                name=safe_state_str(o.get("name") or ""),
                current_level=cur_lvl,
                target_level=max(0, coerce_int(o.get("target_level", cur_lvl + 1), cur_lvl + 1)),
                cost=_resources_from_mapping(o.get("cost")),
                can_build=bool(o.get("can_build", False)),
                status_text=safe_state_str(o.get("status_text") or ""),
                build_href=safe_optional_state_str(o.get("build_href")),
            )
        )
    b_raw, bn_raw = raw_p.get("buildings"), raw_p.get("building_names")
    s_raw, d_raw, e_raw = raw_p.get("ships"), raw_p.get("defense"), raw_p.get("economy_rates")
    return PlanetState(
        planet_id=max(0, coerce_int(raw_p.get("planet_id", 0), 0)),
        name=safe_state_str(raw_p.get("name") or ""),
        coords=safe_state_str(raw_p.get("coords") or ""),
        planet_type=normalize_planet_type(raw_p),
        temperature=coerce_int(raw_p.get("temperature", 0), 0),
        fields_used=max(0, coerce_int(raw_p.get("fields_used", 0), 0)),
        fields_max=max(0, coerce_int(raw_p.get("fields_max", 0), 0)),
        resources=_resources_from_mapping(raw_p.get("resources")),
        storage=_resources_from_mapping(raw_p.get("storage")),
        energy=_energy_from_mapping(raw_p.get("energy")),
        buildings={safe_key(k): max(0, coerce_int(v, 0)) for k, v in b_raw.items()}
        if isinstance(b_raw, Mapping)
        else {},
        building_names={safe_key(k): safe_state_str(v) for k, v in bn_raw.items()}
        if isinstance(bn_raw, Mapping)
        else {},
        ships={safe_key(k): max(0, coerce_int(v, 0)) for k, v in s_raw.items()} if isinstance(s_raw, Mapping) else {},
        defense={safe_key(k): max(0, coerce_int(v, 0)) for k, v in d_raw.items()} if isinstance(d_raw, Mapping) else {},
        building_offers=tuple(offers),
        ship_offers=_parse_unit_offers(raw_p.get("ship_offers"), "ship"),
        defense_offers=_parse_unit_offers(raw_p.get("defense_offers"), "defense"),
        economy_rates={safe_key(k): max(0, coerce_int(v, 0)) for k, v in e_raw.items()}
        if isinstance(e_raw, Mapping)
        else {},
    )


def _tutorial_from_mapping(raw_tut: object) -> TutorialStatus | None:
    if not isinstance(raw_tut, Mapping):
        return None
    tasks: list[TutorialTask] = []
    for t in safe_sequence(raw_tut.get("tasks")):
        if not isinstance(t, Mapping):
            continue
        t_bid = coerce_optional_int(t.get("building_id"), None)
        tasks.append(
            TutorialTask(
                description=safe_state_str(t.get("description") or ""),
                building_name=safe_optional_state_str(t.get("building_name")),
                building_id=t_bid if (t_bid is not None and t_bid >= 0) else None,
                current=max(0, coerce_int(t.get("current", 0), 0)),
                target=max(0, coerce_int(t.get("target", 0), 0)),
                category=safe_state_str(t.get("category") or "building"),
                research_id=safe_optional_state_str(t.get("research_id")),
            )
        )
    return TutorialStatus(
        step=max(0, coerce_int(raw_tut.get("step", 0), 0)),
        total_steps=max(0, coerce_int(raw_tut.get("total_steps", 0), 0)),
        tasks=tuple(tasks),
        reward_text=safe_state_str(raw_tut.get("reward_text") or ""),
        can_complete=bool(raw_tut.get("can_complete", False)),
    )


def _queues_and_offers_from_mapping(
    data: Mapping[str, object],
) -> tuple[tuple[QueueJob, ...], tuple[FleetAlert, ...], tuple[ResearchOffer, ...]]:
    queues: list[QueueJob] = []
    for q in safe_sequence(data.get("active_queues")):
        if not isinstance(q, Mapping):
            continue
        q_bid = coerce_optional_int(q.get("building_id"), None)
        q_tgt = coerce_optional_int(q.get("target_level"), None)
        q_rem = coerce_optional_finite_float(q.get("remaining_seconds"), None)
        q_fin = coerce_optional_finite_float(q.get("finish_ts"), None)
        rc, rec, rdc = q.get("cargo"), q.get("expected_cargo"), q.get("delivered_cargo")
        queues.append(
            QueueJob(
                category=safe_state_str(q.get("category") or ""),
                name=safe_state_str(q.get("name") or ""),
                building_id=q_bid if (q_bid is not None and q_bid >= 0) else None,
                target_level=max(0, q_tgt) if q_tgt is not None else None,
                remaining_text=safe_state_str(q.get("remaining_text") or ""),
                remaining_seconds=q_rem if (q_rem is not None and q_rem >= 0.0) else None,
                finish_ts=q_fin if (q_fin is not None and q_fin > 0.0) else None,
                queue_slot=max(1, coerce_int(q.get("queue_slot", 1), 1)),
                detail=safe_state_str(q.get("detail") or ""),
                origin_coords=extract_coords_from_text(safe_state_str(q.get("origin_coords") or "")),
                cargo=_resources_from_mapping(rc) if isinstance(rc, Mapping) else None,
                expected_cargo=_resources_from_mapping(rec) if isinstance(rec, Mapping) else None,
                delivered_cargo=_resources_from_mapping(rdc) if isinstance(rdc, Mapping) else None,
                delivered=bool(q.get("delivered", False)),
            )
        )
    alerts = tuple(
        FleetAlert(
            mission=safe_state_str(a.get("mission") or ""),
            hostile=bool(a.get("hostile", False)),
            summary=safe_state_str(a.get("summary") or ""),
        )
        for a in safe_sequence(data.get("fleet_alerts"))
        if isinstance(a, Mapping)
    )
    res_offers: list[ResearchOffer] = []
    for r in safe_sequence(data.get("research_offers")):
        if not isinstance(r, Mapping):
            continue
        rid = cap_json_int(max(0, coerce_int(r.get("research_id", 0), 0)))
        cur_r = max(0, coerce_int(r.get("current_level", 0), 0))
        raw_rk = safe_optional_state_str(r.get("research_key"))
        res_offers.append(
            ResearchOffer(
                research_id=rid,
                research_key=raw_rk if raw_rk else (f"r{rid}" if rid else ""),
                name=safe_state_str(r.get("name") or ""),
                current_level=cur_r,
                target_level=max(0, coerce_int(r.get("target_level", cur_r + 1), cur_r + 1)),
                cost=_resources_from_mapping(r.get("cost")),
                can_research=bool(r.get("can_research", False)),
                status_text=safe_state_str(r.get("status_text") or ""),
                research_href=safe_optional_state_str(r.get("research_href")),
                active=bool(r.get("active", False)),
            )
        )
    return tuple(queues), alerts, tuple(res_offers)


def game_state_from_dict(data: Mapping[str, object]) -> GameState:
    """Reconstruct a typed ``GameState`` from serialized JSON (e.g. ``GET /state``)."""
    if not isinstance(data, Mapping):
        data = {}
    planets = tuple(_planet_from_mapping(p) for p in safe_sequence(data.get("planets")) if isinstance(p, Mapping))
    queues, alerts, res_offers = _queues_and_offers_from_mapping(data)
    raw_rs = data.get("raid_stats")
    plain_rs = deep_plain_value(raw_rs) if isinstance(raw_rs, Mapping) else {}
    raid_stats: dict[str, object] = plain_rs if isinstance(plain_rs, dict) else {}
    raid_targets: list[dict[str, object]] = []
    for t in safe_sequence(data.get("raid_targets")):
        if isinstance(t, Mapping):
            plain_t = deep_plain_value(t)
            if isinstance(plain_t, dict):
                raid_targets.append(plain_t)
    r_raw, rn_raw = data.get("researches"), data.get("research_names")
    sp_raw, um_raw = data.get("specs"), data.get("unread_messages")
    ap_id = coerce_optional_int(data.get("active_planet_id"), None)
    acc_pts = coerce_optional_int(data.get("account_points"), None)
    acc_rank = coerce_optional_int(data.get("account_rank"), None)
    ts_val = coerce_optional_finite_float(data.get("timestamp", 0.0), 0.0)
    return GameState(
        universe_id=max(0, coerce_int(data.get("universe_id", 0), 0)),
        user_id=max(0, coerce_int(data.get("user_id", 0), 0)),
        nickname=safe_state_str(data.get("nickname", "")),
        timestamp=0.0 if ts_val is None else ts_val,
        planets=planets,
        active_planet_id=ap_id if (ap_id is not None and ap_id >= 0) else None,
        researches={safe_key(k): max(0, coerce_int(v, 0)) for k, v in r_raw.items()}
        if isinstance(r_raw, Mapping)
        else {},
        research_names={safe_key(k): safe_state_str(v) for k, v in rn_raw.items()}
        if isinstance(rn_raw, Mapping)
        else {},
        specs={safe_key(k): max(0, coerce_int(v, 0)) for k, v in sp_raw.items()} if isinstance(sp_raw, Mapping) else {},
        unread_messages={safe_key(k): max(0, coerce_int(v, 0)) for k, v in um_raw.items()}
        if isinstance(um_raw, Mapping)
        else {},
        tutorial=_tutorial_from_mapping(data.get("tutorial")),
        active_queues=queues,
        fleet_alerts=alerts,
        research_offers=res_offers,
        speedup_factor=coerce_optional_finite_float(data.get("speedup_factor"), None),
        newbie_protection=bool(data.get("newbie_protection", False)),
        account_points=max(0, acc_pts) if acc_pts is not None else None,
        account_rank=max(0, acc_rank) if acc_rank is not None else None,
        raid_targets=tuple(raid_targets),
        raid_stats=raid_stats,
    )


def game_state_to_dict(state: GameState) -> dict[str, object]:
    """Serialize a ``GameState`` into a plain JSON-safe dictionary."""
    return {str(k): v for k, v in dataclasses.asdict(state).items()}
