"""Planet/research initialization, snapshot evaluation, and priority scoring for projections."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
import math

from backend.core import catalog as game_data
from backend.core.coercion import coerce_int
from backend.planner.config_optimizer._overrides_helpers import (
    StateInput,
    _extract_planned_building_map_from_entry,
    state_researches,
)
from backend.planner.config_optimizer._projection_helpers import (
    _HasResources,
    _ProjectionConfig,
    _ResourcesLike,
    _SimCaches,
    _SimPlanet,
    _Snapshot,
)
from backend.planner.config_optimizer._protocols import PlanetLike, as_mapping, map_int
from backend.planner.config_optimizer.catalog_math import (
    research_catalog,
    shipyard_time_divisor,
    spice_eq,
    technology_multiplier,
)
from backend.planner.config_optimizer.constants import RESOURCE_KEYS, WAREHOUSE_KEYS


def _init_research_levels(
    result: Mapping[str, object],
    state: StateInput | None,
) -> tuple[dict[str, int], dict[str, int]]:
    """Build ``(cur_res, tgt_res)`` from ``state`` and ``result['planned_research_levels']``."""
    inputs = as_mapping(result.get("inputs"))
    own_res_raw = as_mapping(inputs.get("own_research_levels"))
    state_res_raw: Mapping[str, int] = state_researches(state) if state is not None else {}

    planned_res_map: dict[str, int] = {}
    planned_res_raw = result.get("planned_research_levels")
    if isinstance(planned_res_raw, Mapping):
        for rk, rv in planned_res_raw.items():
            planned_res_map[str(rk)] = max(0, coerce_int(rv, 0))
    elif isinstance(planned_res_raw, (list, tuple)):
        for row in planned_res_raw:
            if isinstance(row, Mapping):
                rk = str(row.get("key") or row.get("research_key") or "")
                if rk:
                    planned_res_map[rk] = max(0, coerce_int(row.get("planned_level", row.get("level", 0)), 0))

    cur_res: dict[str, int] = {}
    tgt_res: dict[str, int] = {}
    for rk, _info in research_catalog():
        raw_c: object = state_res_raw[rk] if rk in state_res_raw else own_res_raw.get(rk, 0)
        c_lvl = max(0, coerce_int(raw_c, 0))
        cur_res[rk] = c_lvl
        tgt_res[rk] = max(c_lvl, planned_res_map.get(rk, c_lvl))
    return cur_res, tgt_res


def _init_sim_planets(
    result: Mapping[str, object],
    state: StateInput | None,
    all_bkeys: tuple[str, ...],
) -> tuple[list[_SimPlanet], float]:
    """Initialize simulated planets and starting SpiceEq resource stockpile."""
    meta_params = as_mapping(result.get("meta_parameters"))
    min_fleet_raw = as_mapping(meta_params.get("min_fleet_per_planet"))
    live_by_id: dict[int, PlanetLike] = (
        {int(p.planet_id): p for p in (state.planets or ()) if int(p.planet_id) > 0} if state is not None else {}
    )
    raw_planets = result.get("planets")
    entries = raw_planets if isinstance(raw_planets, (list, tuple)) else []

    sim_planets: list[_SimPlanet] = []
    initial_stock_sp = 0.0
    for entry in entries:
        if not isinstance(entry, Mapping):
            continue
        pid = coerce_int(entry.get("planet_id"), 0)
        is_ph = bool(entry.get("is_placeholder", False))
        live_p = live_by_id.get(pid) if (pid > 0 and not is_ph) else None
        raw_built = as_mapping(entry.get("built_floors"))
        cur_b: dict[str, int] = {}
        for bk in all_bkeys:
            if is_ph:
                cur_b[bk] = 0
            elif live_p is not None:
                cur_b[bk] = max(0, coerce_int(live_p.buildings.get(bk, 0), 0))
            else:
                cur_b[bk] = max(0, coerce_int(raw_built.get(bk, 0), 0))
        tgt_b = _extract_planned_building_map_from_entry(entry, cur_b)
        temp_c = (
            int(live_p.temperature)
            if live_p is not None
            else coerce_int(entry.get("temperature_c", entry.get("temperature", 0)), 0)
        )
        entry_fmax = coerce_int(entry.get("fields_max", 0), 0)
        f_max = max(
            1,
            int(live_p.fields_max)
            if (live_p is not None and entry_fmax <= int(live_p.fields_max))
            else coerce_int(entry.get("fields_max", 300), 300),
        )
        solar_cells = max(
            0,
            coerce_int(live_p.ships.get("s9", 0), 0) if live_p is not None else map_int(min_fleet_raw, "s9", 0),
        )
        if live_p is not None and isinstance(live_p, _HasResources) and isinstance(live_p.resources, _ResourcesLike):
            res_obj = live_p.resources
            initial_stock_sp += spice_eq(
                {
                    "pig_iron": max(0, coerce_int(res_obj.pig_iron, 0)),
                    "metal": max(0, coerce_int(res_obj.metal, 0)),
                    "kryptonite": max(0, coerce_int(res_obj.kryptonite, 0)),
                    "spice": max(0, coerce_int(res_obj.spice, 0)),
                }
            )
        sim_planets.append(
            _SimPlanet(
                idx=len(sim_planets),
                planet_id=pid,
                is_placeholder=is_ph,
                temperature_c=temp_c,
                fields_max=f_max,
                solar_cells=solar_cells,
                cur=cur_b,
                tgt=tgt_b,
            )
        )
    return sim_planets, initial_stock_sp


def _eval_snapshot(
    cfg: _ProjectionConfig,
    sim_planets: Sequence[_SimPlanet],
    cur_res: Mapping[str, int],
    caches: _SimCaches,
) -> _Snapshot:
    """Evaluate instantaneous empire weekly ship capacity, bottleneck, and SpiceEq/s income."""
    r1_lvl = int(cur_res.get("r1", 0))
    r2_lvl = int(cur_res.get("r2", 0))
    r2_mult = technology_multiplier("r2", r2_lvl)
    res_unlocked = all(int(cur_res.get(rk, 0)) >= int(rlvl) for rk, rlvl in cfg.ship_r_reqs.items())
    emp_net_hr = {r: 0.0 for r in RESOURCE_KEYS}
    shipyard_wk = 0.0
    power_deficits: list[bool] = []
    smelt_deficits: list[bool] = []

    for sp in sim_planets:
        t_c = sp.temperature_c
        cb = sp.cur
        t0 = t1 = t2 = t3 = t4 = t5 = t6 = t7 = 0.0
        for bk in cfg.all_bkeys:
            lvl = int(cb.get(bk, 0))
            if lvl > 0:
                fl = caches.flows(t_c, r1_lvl, r2_lvl, bk, lvl)
                t0 += fl[0]
                t1 += fl[1]
                t2 += fl[2]
                t3 += fl[3]
                t4 += fl[4]
                t5 += fl[5]
                t6 += fl[6]
                t7 += fl[7]
        if sp.solar_cells:
            t6 += float(sp.solar_cells) * caches.solar_unit_by_temp[t_c] * r2_mult
        p_ratio = min(1.0, t6 / t7) if t7 > 0.0 else 1.0
        power_deficits.append(t7 > t6 + 1e-6)

        pig_mined = t0 * p_ratio
        pig_wanted = t4 * p_ratio
        s_ratio = min(1.0, pig_mined / pig_wanted) if pig_wanted > 0.0 else 1.0
        smelt_deficits.append(pig_wanted > pig_mined + 1e-6)

        emp_net_hr["pig_iron"] += pig_mined - pig_wanted * s_ratio
        emp_net_hr["metal"] += t1 * p_ratio * s_ratio
        emp_net_hr["kryptonite"] += t2 * p_ratio
        emp_net_hr["spice"] += t3 * p_ratio - t5

        if res_unlocked and all(int(cb.get(bk, 0)) >= int(req_l) for bk, req_l in cfg.ship_b_reqs.items()):
            shipyard_wk += (
                cfg.seconds_per_week
                * shipyard_time_divisor(int(cb.get("b9", 0)), int(cb.get("b17", 0)), cfg.speedup)
                / cfg.base_ship_time_s
            )

    res_limits_wk = {r: max(0.0, emp_net_hr[r] * 168.0 / cfg.ship_cost_map[r]) for r in cfg.cost_resources}
    res_cap_wk = min(res_limits_wk.values()) if res_limits_wk else 0.0
    ships_wk = min(res_cap_wk, shipyard_wk)
    all_limits = {**res_limits_wk, "shipyards": shipyard_wk}
    bottleneck = min(all_limits, key=lambda k: all_limits[k]) if all_limits else "shipyards"
    merchant_sp = game_data.MERCHANT_RATES_IN_SPICE
    sp_per_hr = sum(max(0.0, emp_net_hr[r]) / float(merchant_sp[r]) for r in RESOURCE_KEYS)
    res_bottleneck = min(res_limits_wk, key=lambda k: res_limits_wk[k]) if res_limits_wk else "metal"
    return _Snapshot(
        ships_per_week=ships_wk,
        resource_cap_per_week=res_cap_wk,
        shipyard_cap_per_week=shipyard_wk,
        bottleneck=bottleneck,
        resource_bottleneck=res_bottleneck,
        sp_per_s=max(5.0 / 3600.0, sp_per_hr / 3600.0),
        power_deficits=power_deficits,
        smelt_deficits=smelt_deficits,
        res_unlocked=res_unlocked,
    )


def _score_building_weight(
    cfg: _ProjectionConfig,
    sp: _SimPlanet,
    bk: str,
    c_l: int,
    t_l: int,
    n_l: int,
    cost: float,
    cur_res: Mapping[str, int],
    tgt_res: Mapping[str, int],
    cur_snap: _Snapshot,
    caches: _SimCaches,
) -> float:
    """Compute priority weight for upgrading building ``bk`` on ``sp``."""
    bn = cur_snap.bottleneck
    eff_res_bn = (cur_snap.resource_bottleneck or bn) if cur_snap.shipyard_cap_per_week <= 0.0 else bn
    planet_builds_ship = all(int(sp.tgt.get(sbk, 0)) >= int(srl) for sbk, srl in cfg.ship_b_reqs.items())
    if bk in ("b5", "b6") and cur_snap.power_deficits[sp.idx]:
        return 80_000.0
    if planet_builds_ship and bk in cfg.ship_b_reqs and c_l < int(cfg.ship_b_reqs[bk]):
        return 30_000.0
    if (
        bk == "b8"
        and any(int(cur_res.get(srk, 0)) < int(srl) for srk, srl in cfg.ship_r_reqs.items())
        and c_l < cfg.req_b8_for_ship
    ):
        return 32_000.0
    if bk == "b7" and c_l < min(t_l, 12):
        return 1600.0
    if bk == "b17":
        return 2600.0 if bn == "shipyards" else 420.0
    if bk == "b8" and any(int(cur_res[rk]) < int(tgt_res[rk]) for rk in tgt_res):
        return 750.0
    if bk in ("b1", "b2", "b3", "b4"):
        bid_m = int(bk[1:])
        r1_m = int(cur_res.get("r1", 0))
        r2_m = int(cur_res.get("r2", 0))
        gain_sp_hr = max(
            0.0,
            caches.net_sp_hr(sp.temperature_c, r1_m, r2_m, bid_m, n_l)
            - caches.net_sp_hr(sp.temperature_c, r1_m, r2_m, bid_m, c_l),
        )
        payback_hr = (cost / gain_sp_hr) if gain_sp_hr > 1e-3 else 1e6
        roi_boost = 1800.0 / max(1.0, math.sqrt(payback_hr / 24.0))
        is_active_bn = (
            (bk == "b1" and (cur_snap.smelt_deficits[sp.idx] or eff_res_bn in ("pig_iron", "metal")))
            or (bk == "b2" and eff_res_bn == "metal")
            or (bk == "b3" and eff_res_bn == "kryptonite")
            or (bk == "b4" and eff_res_bn == "spice")
        )
        return (2400.0 + 2.0 * roi_boost) if is_active_bn else (180.0 + 0.4 * roi_boost)
    if bk == "b9":
        return 2400.0 if bn == "shipyards" else 140.0
    if bk in ("b5", "b6"):
        return 120.0
    if bk in WAREHOUSE_KEYS:
        return 15.0
    return 35.0


def _score_research_weight(
    cfg: _ProjectionConfig,
    rk: str,
    c_l: int,
    cost: float,
    sp_s: float,
) -> float:
    """Compute priority weight for upgrading research ``rk``."""
    afford_days = (cost / max(1e-3, sp_s)) / 86400.0
    if rk in cfg.ship_r_reqs and c_l < int(cfg.ship_r_reqs[rk]):
        unlock_damp = 1.0 / (1.0 + max(0.0, afford_days - 14.0) * 0.25)
        return 25_000.0 * unlock_damp
    afford_damp = 1.0 / (1.0 + max(0.0, afford_days - 7.0))
    if rk in ("r1", "r2"):
        return 650.0 * afford_damp
    if rk == "r3":
        return 600.0 * afford_damp
    return 80.0 * afford_damp
