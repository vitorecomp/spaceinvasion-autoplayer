"""Galaxy/raid target classification into debris, colony, spy, and attack candidates."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

from backend.combat.cargo import extract_target_debris_resources
from backend.combat.flight import normalize_coords
from backend.combat.simulator import CombatReport
from backend.core import catalog
from backend.core.config import StrategyConfig
from backend.planner.heuristics._common import (
    _parse_gal_sys_pos,
    _resources_from_dict,
    _safe_strategy_float,
    _safe_strategy_int,
    _safe_strategy_str,
)
from backend.planner.heuristics._fleet_context import (
    _AttackEntry,
    _DebrisEntry,
    _FleetContext,
    _TargetSlot,
)
from backend.planner.heuristics._fleet_helper_builds import (
    _classify_free_colony_slot,
    _ColonizeSortKey,
    _ColonySpySortKey,
)
from backend.planner.heuristics._types import PlannedAction

_SpySortKey = tuple[int, int, int, float]


@dataclass(frozen=True)
class _TargetScanResults:
    spy_actions: list[tuple[_SpySortKey, PlannedAction]]
    colony_spy_actions: list[tuple[_ColonySpySortKey, PlannedAction]]
    colonize_actions: list[tuple[_ColonizeSortKey, PlannedAction]]
    debris_entries: list[_DebrisEntry]
    attack_entries: list[_AttackEntry]


def _enqueue_raid_spy_action(
    coords: str,
    t_gal: int,
    t_sys: int,
    pos: int,
    status_color: str,
    is_spacia: bool,
    tag: str,
    target_label: str,
    raw_scanned_ts: float,
    stale_intel: bool,
    cfg: StrategyConfig,
    ctx: _FleetContext,
    spy_actions: list[tuple[_SpySortKey, PlannedAction]],
) -> None:
    probe_planet = ctx.pools.probe_planet
    if probe_planet is None:
        return
    max_spy_slots = ctx.max_slots_for(probe_planet)
    used_spy = ctx.used_slots_for(probe_planet)
    p_gal_s, p_sys_s, p_pos_s = _parse_gal_sys_pos(probe_planet.coords)
    phys_dist_spy = abs(p_gal_s - t_gal) * 20000 + abs(p_sys_s - t_sys) * 95 + abs(p_pos_s - pos) * 5
    can_exec_spy = bool(cfg.allow_galaxy_scan and ctx.pools.has_probes and used_spy < max_spy_slots)
    spy_reason = (
        f"{'Tutorial spy: ' if ctx.tutorial_spy_pending else ''}Send Spy probe (s10) to scout {tag} {coords}"
        + (" (refresh stale intel)" if (stale_intel and not ctx.tutorial_spy_pending) else " before raiding")
    )
    if ctx.pools.designated_missing_probes:
        spy_reason += " [Hold: No Spy probes (s10) stationed on designated spy colony]"
    elif used_spy >= max_spy_slots:
        spy_reason += f" [Hold: Fleet slots full ({used_spy}/{max_spy_slots} in flight)]"
    elif not cfg.allow_galaxy_scan:
        spy_reason += " [Hold: allow_galaxy_scan=false in Config]"
    spy_actions.append(
        (
            (
                0 if can_exec_spy else 1,
                0 if (status_color == "yellow" or is_spacia) else 1,
                phys_dist_spy,
                -raw_scanned_ts,
            ),
            PlannedAction(
                priority="P1",
                kind="SPY",
                planet_id=probe_planet.planet_id,
                planet_coords=_safe_strategy_str(probe_planet.coords),
                target_id=10,
                target_level=1,
                target_name=target_label,
                reason=spy_reason,
                executable_now=can_exec_spy,
            ),
        )
    )


def _classify_occupied_target(
    raw: Mapping[str, object],
    coords: str,
    norm_coords: str,
    t_gal: int,
    t_sys: int,
    pos: int,
    owner: str,
    pname: str,
    status_color: str,
    is_spacia: bool,
    raw_scanned_ts: float,
    raw_blocked_until: float,
    cfg: StrategyConfig,
    ctx: _FleetContext,
    spy_actions: list[tuple[_SpySortKey, PlannedAction]],
    attack_entries: list[_AttackEntry],
) -> None:
    alt_name = catalog.safe_key(raw.get("name") or "")
    if not is_spacia and not owner and not pname and not alt_name:
        return
    res_map = raw.get("resources") if isinstance(raw.get("resources"), Mapping) else raw
    res = _resources_from_dict(res_map)
    spied_ts = _safe_strategy_float(raw.get("last_spied_ts"), 0.0)
    raw_fleet_obj = raw.get("fleet")
    raw_fleet: Mapping[object, object] = raw_fleet_obj if isinstance(raw_fleet_obj, Mapping) else {}
    raw_defense_obj = raw.get("defense")
    raw_defense: Mapping[object, object] = raw_defense_obj if isinstance(raw_defense_obj, Mapping) else {}
    clean_fleet = {
        catalog.safe_key(k): cnt for k, v in raw_fleet.items() if (cnt := max(0, _safe_strategy_int(v, 0))) > 0
    }
    clean_defense = {
        catalog.safe_key(k): cnt for k, v in raw_defense.items() if (cnt := max(0, _safe_strategy_int(v, 0))) > 0
    }
    has_intel = (
        bool(raw.get("has_spy_intel"))
        or spied_ts > 0.0
        or (not res.is_zero())
        or bool(clean_fleet)
        or bool(clean_defense)
    )
    stale_intel = spied_ts > 0.0 and (ctx.ref_now - spied_ts) > (4 * 3600.0)
    eligible_target = is_spacia or status_color == "yellow" or (cfg.allow_pvp_in_az and status_color == "light_red")
    can_attack_now = raw_blocked_until <= ctx.ref_now and ctx.bash_tracker.can_attack(
        norm_coords, is_inactive=(status_color == "yellow"), is_spacia=is_spacia, timestamp=ctx.ref_now
    )
    tag = "Spacia" if is_spacia else ("Inactive" if status_color == "yellow" else status_color.title())
    target_label = f"{coords} ({owner or pname or alt_name or tag})"

    if (
        ctx.pools.can_plan_spy
        and can_attack_now
        and (ctx.tutorial_spy_pending or not ctx.tutorial_fight_pending)
        and eligible_target
        and (ctx.tutorial_spy_pending or not has_intel or stale_intel)
    ):
        _enqueue_raid_spy_action(
            coords,
            t_gal,
            t_sys,
            pos,
            status_color,
            is_spacia,
            tag,
            target_label,
            raw_scanned_ts,
            stale_intel,
            cfg,
            ctx,
            spy_actions,
        )
        if ctx.tutorial_spy_pending or not has_intel:
            return

    slot = _TargetSlot(
        coords=norm_coords, position=pos, name=pname, owner=owner, status_color=status_color, is_spacia=is_spacia
    )
    report = CombatReport(
        target_coords=norm_coords,
        owner=owner,
        resources=res,
        fleet=clean_fleet,
        defense=clean_defense,
        is_spacia=is_spacia,
    )
    attack_entries.append(_AttackEntry(norm_coords, slot, report, tag, target_label, res))


def _collect_target_candidates(
    cfg: StrategyConfig,
    ctx: _FleetContext,
) -> _TargetScanResults:
    """Scan `state.raid_targets` to collect debris, colony, spy, and attack candidates."""
    spy_actions: list[tuple[_SpySortKey, PlannedAction]] = []
    colony_spy_actions: list[tuple[_ColonySpySortKey, PlannedAction]] = []
    colonize_actions: list[tuple[_ColonizeSortKey, PlannedAction]] = []
    debris_entries: list[_DebrisEntry] = []
    attack_entries: list[_AttackEntry] = []
    seen_recycle_coords: set[str] = set()
    seen_colony_coords: set[str] = set()

    for raw in ctx.raid_targets:
        if not isinstance(raw, Mapping):
            continue
        coords = catalog.safe_key(raw.get("coords") or raw.get("target_coords") or "").strip()
        if not coords:
            continue
        norm_coords = normalize_coords(coords)
        t_gal, t_sys, pos = _parse_gal_sys_pos(norm_coords)
        skey = f"{t_gal:02d}:{t_sys:03d}"
        raw_scanned_ts = _safe_strategy_float(raw.get("last_scanned_ts"), 0.0)
        if raw_scanned_ts > 0.0:
            ctx.scanned_map[skey] = max(ctx.scanned_map.get(skey, 0.0), raw_scanned_ts)
        raw_last_atk = max(
            _safe_strategy_float(raw.get("last_attack_ts"), 0.0),
            _safe_strategy_float(raw.get("last_attacked_ts"), 0.0),
        )
        if raw_last_atk > 0.0:
            ctx.latest_battle_by_sys[skey] = max(ctx.latest_battle_by_sys.get(skey, 0.0), raw_last_atk)
        raw_blocked_until = _safe_strategy_float(raw.get("blocked_until_ts"), 0.0)
        if raw_blocked_until > ctx.ref_now:
            ctx.bash_tracker.record_block(
                norm_coords, raw_blocked_until, reason=_safe_strategy_str(raw.get("block_reason"))
            )

        owner = catalog.safe_key(raw.get("owner") or "")
        pname = catalog.safe_key(raw.get("planet_name") or "")
        if (
            bool(raw.get("has_debris", False))
            and ctx.pools.has_recyclers
            and norm_coords not in seen_recycle_coords
            and norm_coords not in ctx.in_flight_recycle_coords
        ):
            seen_recycle_coords.add(norm_coords)
            debris_entries.append(_DebrisEntry(norm_coords, owner, pname, extract_target_debris_resources(raw)))
        if norm_coords in ctx.own_coords:
            continue

        is_spacia = bool(raw.get("is_spacia", False))
        explicit_color = catalog.safe_key(raw.get("status_color") or "").strip().lower()
        status_color = (
            explicit_color
            if explicit_color
            else ("purple" if is_spacia else ("light_red" if raw.get("is_inactive") is False else "yellow"))
        )
        is_free_slot = status_color == "free" or (
            not owner and (raw.get("uninhabited_fields") is not None or raw.get("colonize_max_fields") is not None)
        )
        if (
            ctx.want_colonize
            and not is_spacia
            and not bool(raw.get("discarded", False))
            and is_free_slot
            and norm_coords not in seen_colony_coords
        ):
            seen_colony_coords.add(norm_coords)
            _classify_free_colony_slot(
                raw, norm_coords, t_gal, t_sys, pos, cfg, ctx, colonize_actions, colony_spy_actions
            )
            continue
        if not is_spacia and status_color in ("free", "blue", "green", "white", "purple", "dark_red"):
            continue
        _classify_occupied_target(
            raw,
            coords,
            norm_coords,
            t_gal,
            t_sys,
            pos,
            owner,
            pname,
            status_color,
            is_spacia,
            raw_scanned_ts,
            raw_blocked_until,
            cfg,
            ctx,
            spy_actions,
            attack_entries,
        )

    return _TargetScanResults(
        spy_actions=spy_actions,
        colony_spy_actions=colony_spy_actions,
        colonize_actions=colonize_actions,
        debris_entries=debris_entries,
        attack_entries=attack_entries,
    )
