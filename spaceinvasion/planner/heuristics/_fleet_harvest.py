"""Multi-planet unified RECYCLE and ATTACK origin assignment ranked by resources per hour."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
import math
import re

from spaceinvasion.combat.cargo import fleet_cargo_capacity
from spaceinvasion.combat.flight import (
    DEFAULT_UNKNOWN_DEBRIS_RESOURCES,
    estimate_fleet_flight_seconds,
    estimate_resource_per_hour,
    format_flight_duration,
)
from spaceinvasion.combat.simulator import RaidRecommendation, score_raid_target
from spaceinvasion.core import catalog
from spaceinvasion.core.config import StrategyConfig
from spaceinvasion.core.state import GameState, PlanetState, has_in_flight_fleet_from_planet
from spaceinvasion.planner.heuristics._common import (
    _safe_strategy_float,
    _safe_strategy_int,
    _safe_strategy_str,
)
from spaceinvasion.planner.heuristics._fleet_context import _AttackEntry, _DebrisEntry, _FleetContext
from spaceinvasion.planner.heuristics._fleet_slots import _SlotReservations
from spaceinvasion.planner.heuristics._types import PlannedAction

_HarvestSortKey = tuple[int, float, int, float]


@dataclass(frozen=True)
class _RecycleCandidate:
    planet: PlanetState
    qty: int
    exp_rubble: int
    flight_s: float
    rph: float
    can_exec: bool
    hold_inflight: bool


@dataclass(frozen=True)
class _AttackCandidate:
    planet: PlanetState
    rec: RaidRecommendation
    total_res: int
    flight_s: float
    rph: float
    can_exec: bool
    hold_tut: bool


@dataclass(frozen=True)
class _DebrisWorkPayload:
    entry: _DebrisEntry
    candidates: list[_RecycleCandidate]


@dataclass(frozen=True)
class _AttackWorkPayload:
    norm_coords: str
    tag: str
    target_label: str
    candidates: list[_AttackCandidate]


@dataclass(frozen=True)
class _HarvestWorkItem:
    neg_best_rph: float
    kind: str
    debris: _DebrisWorkPayload | None = None
    attack: _AttackWorkPayload | None = None


def _evaluate_debris_work_items(
    state: GameState,
    cfg: StrategyConfig,
    ctx: _FleetContext,
    debris_entries: Sequence[_DebrisEntry],
) -> list[_HarvestWorkItem]:
    debris_count = len(debris_entries)
    miner_spec_rec = _safe_strategy_int((getattr(state, "specs", None) or {}).get("miner", 0), 0)
    per_rec_cap = max(1, fleet_cargo_capacity({"s11": 1}, miner_spec_level=miner_spec_rec))
    min_recycle_rph_cfg = max(0.0, _safe_strategy_float(getattr(cfg, "recycle_min_res_per_hour", 0.0), 0.0))

    items: list[_HarvestWorkItem] = []
    for entry in debris_entries:
        rec_cands: list[_RecycleCandidate] = []
        best_rph_d = -1.0
        for rp in ctx.pools.recycler_planets:
            avail_s11_rp = max(1, _safe_strategy_int(rp.ships.get("s11", 0), 1))
            if ctx.tutorial_recycle_pending or debris_count <= 1:
                rec_qty_rp = avail_s11_rp
            elif entry.known_debris > 0:
                rec_qty_rp = max(1, min(avail_s11_rp, math.ceil(entry.known_debris / per_rec_cap)))
            else:
                rec_qty_rp = max(
                    1,
                    min(avail_s11_rp, max(2, math.ceil(DEFAULT_UNKNOWN_DEBRIS_RESOURCES / per_rec_cap))),
                )
            rec_cap_rp = fleet_cargo_capacity({"s11": rec_qty_rp}, miner_spec_level=miner_spec_rec)
            exp_rubble_rp = (
                min(entry.known_debris, rec_cap_rp)
                if entry.known_debris > 0
                else min(rec_cap_rp, DEFAULT_UNKNOWN_DEBRIS_RESOURCES)
            )
            rec_flight_rp = estimate_fleet_flight_seconds(
                rp.coords,
                entry.norm_coords,
                {"s11": rec_qty_rp},
                researches=state.researches,
                round_trip=True,
            )
            if not ctx.tutorial_recycle_pending and rec_flight_rp > 12.0 * 3600.0:
                rp_g_m = re.match(r"(\d{1,2}):", _safe_strategy_str(rp.coords).strip())
                tg_m = re.match(r"(\d{1,2}):", entry.norm_coords)
                if rp_g_m and tg_m and int(rp_g_m.group(1)) != int(tg_m.group(1)):
                    continue
            rec_rph_rp = estimate_resource_per_hour(exp_rubble_rp, rec_flight_rp)
            if (
                not ctx.tutorial_recycle_pending
                and min_recycle_rph_cfg > 0.0
                and rec_rph_rp < min_recycle_rph_cfg - 1e-6
            ):
                continue
            hold_rec_rp = bool(ctx.tutorial_recycle_pending and has_in_flight_fleet_from_planet(state, rp))
            can_exec_rp = bool((cfg.allow_raids or cfg.allow_galaxy_scan) and not hold_rec_rp)
            rec_cands.append(
                _RecycleCandidate(rp, rec_qty_rp, exp_rubble_rp, rec_flight_rp, rec_rph_rp, can_exec_rp, hold_rec_rp)
            )
            if rec_rph_rp > best_rph_d:
                best_rph_d = rec_rph_rp
        if rec_cands:
            items.append(_HarvestWorkItem(-best_rph_d, "RECYCLE", debris=_DebrisWorkPayload(entry, rec_cands)))
    return items


def _evaluate_attack_work_items(
    state: GameState,
    cfg: StrategyConfig,
    ctx: _FleetContext,
    attack_entries: Sequence[_AttackEntry],
) -> list[_HarvestWorkItem]:
    min_loot_cfg = max(0.0, _safe_strategy_float(getattr(cfg, "raid_min_loot_spice_eq", 1500.0), 1500.0))
    min_attack_rph_cfg = max(0.0, _safe_strategy_float(getattr(cfg, "attack_min_res_per_hour", 0.0), 0.0))
    safety_margin_val = _safe_strategy_float(getattr(cfg, "combat_safety_margin", 1.4), 1.4)
    if safety_margin_val <= 0.0:
        safety_margin_val = 1.4
    atk_origin_pool: list[tuple[PlanetState, dict[str, int]]] = (
        ctx.pools.attack_planets if ctx.pools.has_attack_fleet else [(ctx.pools.origin, {"s1": 1})]
    )

    items: list[_HarvestWorkItem] = []
    for entry in attack_entries:
        min_loot = 0.0 if (entry.res.is_zero() or ctx.tutorial_fight_pending) else min_loot_cfg
        atk_cands: list[_AttackCandidate] = []
        best_rph_a = -1.0
        for ap, ap_ships in atk_origin_pool:
            rec = score_raid_target(
                ap.coords,
                entry.slot,
                entry.report,
                ap_ships,
                attacker_researches=state.researches,
                bash_tracker=ctx.bash_tracker,
                safety_margin=safety_margin_val,
                min_loot_spice_eq=min_loot,
                allow_pvp_in_az=cfg.allow_pvp_in_az,
                timestamp=ctx.ref_now,
            )
            if rec is None:
                continue
            hold_tut_ap = bool(ctx.tutorial_fight_pending and has_in_flight_fleet_from_planet(state, ap))
            hold_esc_ap = bool(ctx.tutorial_escape_pending and not ctx.tutorial_fight_pending)
            can_exec_ap = bool(
                cfg.allow_raids
                and ctx.pools.has_attack_fleet
                and rec.combat.safe_to_attack
                and not hold_tut_ap
                and not hold_esc_ap
            )
            atk_total_res = max(0, int(rec.total_plunder_resources))
            if atk_total_res <= 0:
                el = rec.combat.expected_loot
                atk_total_res = max(0, el.pig_iron) + max(0, el.metal) + max(0, el.kryptonite) + max(0, el.spice)
            atk_flight_s = float(rec.flight_seconds or 0.0)
            if atk_flight_s <= 0.0:
                atk_flight_s = estimate_fleet_flight_seconds(
                    ap.coords, entry.norm_coords, ap_ships, researches=state.researches, round_trip=True
                )
            atk_rph = estimate_resource_per_hour(atk_total_res, atk_flight_s)
            if not ctx.tutorial_fight_pending and min_attack_rph_cfg > 0.0 and atk_rph < min_attack_rph_cfg - 1e-6:
                continue
            atk_cands.append(_AttackCandidate(ap, rec, atk_total_res, atk_flight_s, atk_rph, can_exec_ap, hold_tut_ap))
            if atk_rph > best_rph_a:
                best_rph_a = atk_rph
        if atk_cands:
            items.append(
                _HarvestWorkItem(
                    -best_rph_a,
                    "ATTACK",
                    attack=_AttackWorkPayload(entry.norm_coords, entry.tag, entry.target_label, atk_cands),
                )
            )
    return items


def _assign_recycle_item(
    cfg: StrategyConfig,
    ctx: _FleetContext,
    slots: _SlotReservations,
    soft_free_slots: dict[int, int],
    soft_s11: dict[int, int],
    payload: _DebrisWorkPayload,
) -> tuple[_HarvestSortKey, PlannedAction]:
    entry = payload.entry
    best_rc = min(
        payload.candidates,
        key=lambda c: (
            0 if (c.can_exec and slots.free_harvest_slots(ctx, c.planet) > 0) else 1,
            0 if soft_free_slots.get(c.planet.planet_id, 0) > 0 else 1,
            0 if soft_s11.get(c.planet.planet_id, 0) >= c.qty else 1,
            -c.rph,
            -c.exp_rubble,
            c.flight_s,
            c.planet.planet_id,
        ),
    )
    rp = best_rc.planet
    if best_rc.can_exec and slots.free_harvest_slots(ctx, rp) > 0:
        if soft_free_slots.get(rp.planet_id, 0) > 0:
            soft_free_slots[rp.planet_id] -= 1
        soft_s11[rp.planet_id] = max(0, soft_s11.get(rp.planet_id, 0) - best_rc.qty)

    rec_dur_txt = format_flight_duration(best_rc.flight_s)
    rec_rph_txt = catalog.fmt_compact_int(round(best_rc.rph), grouped=True)
    rec_res_txt = catalog.fmt_compact_int(best_rc.exp_rubble, grouped=True)
    rec_lbl = f"Rubble -> {entry.norm_coords}" + (
        f" ({entry.owner or entry.pname})" if (entry.owner or entry.pname) else ""
    )
    rec_prefix = "Tutorial recycle: " if ctx.tutorial_recycle_pending else ""
    est_tag = "est. " if entry.known_debris <= 0 else ""
    rec_reason = (
        f"{rec_prefix}Harvest ~{rec_res_txt} {est_tag}rubble at {entry.norm_coords} with {best_rc.qty} Recycler(s) "
        f"({rec_dur_txt} RT · {rec_rph_txt} res/h)"
    )
    if best_rc.hold_inflight:
        rec_reason += " [Hold: Own fleet in transit]"
    elif not (cfg.allow_raids or cfg.allow_galaxy_scan):
        rec_reason += " [Hold: allow_raids/allow_galaxy_scan disabled in Config]"
    return (
        (0 if best_rc.can_exec else 1, -best_rc.rph, -best_rc.exp_rubble, best_rc.flight_s),
        PlannedAction(
            priority="P2",
            kind="RECYCLE",
            planet_id=rp.planet_id,
            planet_coords=_safe_strategy_str(rp.coords),
            target_id=11,
            target_level=best_rc.qty,
            target_name=rec_lbl,
            reason=rec_reason,
            executable_now=best_rc.can_exec,
        ),
    )


def _assign_attack_item(
    cfg: StrategyConfig,
    ctx: _FleetContext,
    slots: _SlotReservations,
    soft_free_slots: dict[int, int],
    payload: _AttackWorkPayload,
) -> tuple[_HarvestSortKey, PlannedAction]:
    best_ac = min(
        payload.candidates,
        key=lambda c: (
            0 if (c.can_exec and slots.free_harvest_slots(ctx, c.planet) > 0) else 1,
            0 if soft_free_slots.get(c.planet.planet_id, 0) > 0 else 1,
            -c.rph,
            -c.total_res,
            c.flight_s,
            c.planet.planet_id,
        ),
    )
    ap = best_ac.planet
    rec = best_ac.rec
    if best_ac.can_exec and slots.free_harvest_slots(ctx, ap) > 0 and soft_free_slots.get(ap.planet_id, 0) > 0:
        soft_free_slots[ap.planet_id] -= 1
    hold_for_escape_tutorial = bool(ctx.tutorial_escape_pending and not ctx.tutorial_fight_pending)
    atk_dur_txt = format_flight_duration(best_ac.flight_s)
    atk_rph_txt = catalog.fmt_compact_int(round(best_ac.rph), grouped=True)
    atk_res_txt = catalog.fmt_compact_int(best_ac.total_res, grouped=True)
    if best_ac.total_res > 0:
        loot_note = f"~{atk_res_txt} res ({atk_dur_txt} RT · {atk_rph_txt} res/h)"
    elif rec.loot_spice_equivalent > 0:
        loot_disp = (
            catalog.fmt_compact_int(round(rec.loot_spice_equivalent), grouped=True)
            if math.isfinite(rec.loot_spice_equivalent)
            else "INF"
        )
        loot_note = f"~{loot_disp} SpEq plunder ({atk_dur_txt} RT)"
    else:
        loot_note = f"scouted inactive target ({atk_dur_txt} RT)"
    gates: list[str] = []
    if best_ac.hold_tut:
        gates.append("tutorial raid fleet already in transit")
    if hold_for_escape_tutorial:
        gates.append("tutorial escape flight pending")
    if not ctx.pools.has_attack_fleet:
        gates.append("need stationed attack/transport ships")
    if not cfg.allow_raids:
        gates.append("allow_raids=false in Config")
    if not rec.combat.safe_to_attack:
        gates.append(rec.combat.reason)
    gate_suffix = f" [Hold: {'; '.join(gates)}]" if gates else " [Safe >=1.4x]"
    tut_prefix = "Tutorial fight: " if ctx.tutorial_fight_pending else ""
    ratio_disp: float | str = rec.combat.power_ratio if rec.combat.power_ratio != float("inf") else "INF"
    return (
        (0 if best_ac.can_exec else 1, -best_ac.rph, -best_ac.total_res, best_ac.flight_s),
        PlannedAction(
            priority="P2",
            kind="ATTACK",
            planet_id=ap.planet_id,
            planet_coords=_safe_strategy_str(ap.coords),
            target_name=payload.target_label,
            reason=f"{tut_prefix}{payload.tag} raid {loot_note} (ratio {ratio_disp}x){gate_suffix}",
            executable_now=best_ac.can_exec,
        ),
    )


def _plan_unified_harvest(
    state: GameState,
    cfg: StrategyConfig,
    ctx: _FleetContext,
    slots: _SlotReservations,
    debris_entries: Sequence[_DebrisEntry],
    attack_entries: Sequence[_AttackEntry],
) -> tuple[list[tuple[_HarvestSortKey, PlannedAction]], list[tuple[_HarvestSortKey, PlannedAction]]]:
    """Evaluate and greedily assign multi-planet RECYCLE and ATTACK actions by top res/h."""
    soft_free_slots: dict[int, int] = {p.planet_id: slots.free_harvest_slots(ctx, p) for p in state.planets}
    soft_s11: dict[int, int] = {
        p.planet_id: max(0, _safe_strategy_int(p.ships.get("s11", 0), 0)) for p in ctx.pools.recycler_planets
    }
    work_items = _evaluate_debris_work_items(state, cfg, ctx, debris_entries)
    work_items.extend(_evaluate_attack_work_items(state, cfg, ctx, attack_entries))
    work_items.sort(key=lambda item: (item.neg_best_rph, item.kind))

    recycle_actions: list[tuple[_HarvestSortKey, PlannedAction]] = []
    scored_attacks: list[tuple[_HarvestSortKey, PlannedAction]] = []
    for item in work_items:
        if item.kind == "RECYCLE" and item.debris is not None:
            recycle_actions.append(_assign_recycle_item(cfg, ctx, slots, soft_free_slots, soft_s11, item.debris))
        elif item.attack is not None:
            scored_attacks.append(_assign_attack_item(cfg, ctx, slots, soft_free_slots, item.attack))
    return recycle_actions, scored_attacks
