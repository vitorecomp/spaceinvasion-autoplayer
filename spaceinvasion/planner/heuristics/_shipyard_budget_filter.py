"""Spare-budget gating for Fleet Planner `RESEARCH` and `BUILD_SHIP` actions."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import replace

from spaceinvasion.core import catalog
from spaceinvasion.core.state import GameState
from spaceinvasion.planner.heuristics._common import (
    _norm_planet_type,
    _resources_from_dict,
    _safe_strategy_float,
    _safe_strategy_int,
    _safe_strategy_str,
)
from spaceinvasion.planner.heuristics._types import (
    _BUDGET_RES_KEYS,
    AI_INFRA_CARGO_TAG,
    SHIP_SLUG_BY_KEY,
    PlannedAction,
)

_SHIP_HOLD_TAG = " [Hold: reserved for next AI-Planner step / spare % ship budget]"
_RES_HOLD_TAG = " [Hold: reserved for next AI-Planner step / spare % fleet research budget]"
_RES_QUEUED_TAG = " (queued behind active fleet research slot)"
_INFRA_HOLD_TAG = " (held: 25%/h infra transport cap)"


def _extract_filter_budget_state(
    state: GameState,
    spare_budget: Mapping[str, object],
) -> tuple[dict[str, float], dict[str, float], dict[str, float], bool, bool]:
    avail_raw = spare_budget.get("available_budget")
    rem: dict[str, float] = {
        rk: max(0.0, _safe_strategy_float(avail_raw.get(rk, 0.0), 0.0)) if isinstance(avail_raw, Mapping) else 0.0
        for rk in _BUDGET_RES_KEYS
    }
    urgent_map = spare_budget.get("urgent_replacements")
    if isinstance(urgent_map, Mapping):
        urg_s10 = bool(urgent_map.get("s10", False))
        urg_tr = bool(urgent_map.get("s1", False) or urgent_map.get("s2", False))
    else:
        non_raid = [p for p in (getattr(state, "planets", ()) or ()) if _norm_planet_type(p) != "raid"]
        urg_s10 = sum(max(0, _safe_strategy_int((p.ships or {}).get("s10", 0), 0)) for p in non_raid) == 0
        urg_tr = (
            sum(
                max(0, _safe_strategy_int((p.ships or {}).get("s1", 0), 0))
                + max(0, _safe_strategy_int((p.ships or {}).get("s2", 0), 0))
                for p in non_raid
            )
            == 0
        )
    infra_raw = spare_budget.get("infra_transport_budget")
    infra_rem: dict[str, float] = {
        rk: (
            max(0.0, _safe_strategy_float(infra_raw.get(rk, 0.0), 0.0))
            if isinstance(infra_raw, Mapping) and rk in ("metal", "kryptonite")
            else 0.0
        )
        for rk in _BUDGET_RES_KEYS
    }
    free_raw = spare_budget.get("free_after_reserve")
    free_rem: dict[str, float] = {
        rk: (
            max(0.0, _safe_strategy_float(free_raw.get(rk, 0.0), 0.0))
            if isinstance(free_raw, Mapping)
            else rem[rk] + infra_rem[rk]
        )
        for rk in _BUDGET_RES_KEYS
    }
    return rem, infra_rem, free_rem, urg_s10, urg_tr


def _filter_research_actions(
    out: list[PlannedAction],
    rem: dict[str, float],
    free_rem: dict[str, float],
) -> None:
    approved_research = False
    for idx, act in enumerate(out):
        if act.kind != "RESEARCH" or act.manual_queue_id is not None or not act.executable_now:
            continue
        if approved_research:
            cur_r = _safe_strategy_str(act.reason)
            new_r = cur_r if _RES_QUEUED_TAG.strip() in cur_r else f"{cur_r}{_RES_QUEUED_TAG}"
            out[idx] = replace(act, executable_now=False, reason=new_r, action_href=None)
            continue

        cost_obj = act.cost
        if cost_obj.is_zero():
            rid = catalog.safe_optional_int(act.target_id, None)
            tlvl = catalog.safe_optional_int(act.target_level, None)
            if rid is not None and tlvl is not None:
                r_info = catalog.RESEARCH_BY_ID.get(f"r{rid}") or {}
                levels_raw = r_info.get("levels")
                lvl_row = levels_raw.get(tlvl) if isinstance(levels_raw, Mapping) else None
                cost_obj = _resources_from_dict(lvl_row.get("cost") if isinstance(lvl_row, Mapping) else {})
        need_c = {
            "pig_iron": float(max(0, _safe_strategy_int(cost_obj.pig_iron, 0))),
            "metal": float(max(0, _safe_strategy_int(cost_obj.metal, 0))),
            "kryptonite": float(max(0, _safe_strategy_int(cost_obj.kryptonite, 0))),
            "spice": float(max(0, _safe_strategy_int(cost_obj.spice, 0))),
        }
        if all(rem[rk] + 1e-6 >= need_c[rk] for rk in _BUDGET_RES_KEYS):
            for rk in _BUDGET_RES_KEYS:
                rem[rk] = max(0.0, rem[rk] - need_c[rk])
                free_rem[rk] = max(0.0, free_rem[rk] - need_c[rk])
            approved_research = True
        else:
            cur_r = _safe_strategy_str(act.reason)
            new_r = cur_r if _RES_HOLD_TAG.strip() in cur_r else f"{cur_r}{_RES_HOLD_TAG}"
            out[idx] = replace(act, executable_now=False, reason=new_r, action_href=None)


def _filter_ship_actions(
    out: list[PlannedAction],
    rem: dict[str, float],
    infra_rem: dict[str, float],
    free_rem: dict[str, float],
    *,
    urg_s10: bool,
    urg_tr: bool,
) -> None:
    infra_spent = False
    for idx, act in enumerate(out):
        if act.kind != "BUILD_SHIP" or act.manual_queue_id is not None or not act.executable_now:
            continue
        sid = catalog.safe_optional_int(act.target_id, None)
        if sid == 10 and urg_s10:
            continue
        if sid in (1, 2) and urg_tr:
            continue

        qty = max(1, _safe_strategy_int(act.target_level, 1))
        unit_obj = act.cost
        if unit_obj.is_zero() and sid is not None:
            slug = SHIP_SLUG_BY_KEY.get(f"s{sid}")
            if slug:
                info = catalog.resolve_unit("ships", slug) or {}
                unit_obj = _resources_from_dict(info.get("base_cost", {}))
        unit_c = {rk: float(max(0, _safe_strategy_int(getattr(unit_obj, rk, 0), 0))) for rk in _BUDGET_RES_KEYS}
        need_c = {rk: unit_c[rk] * qty for rk in _BUDGET_RES_KEYS}
        if AI_INFRA_CARGO_TAG in _safe_strategy_str(act.reason):
            fit_units = qty
            for rk in _BUDGET_RES_KEYS:
                if unit_c[rk] > 0.0:
                    fit_units = min(fit_units, int((min(infra_rem[rk], free_rem[rk]) + 1e-6) // unit_c[rk]))
            if fit_units >= 1:
                for rk in _BUDGET_RES_KEYS:
                    infra_rem[rk] = max(0.0, infra_rem[rk] - unit_c[rk] * fit_units)
                    free_rem[rk] = max(0.0, free_rem[rk] - unit_c[rk] * fit_units)
                infra_spent = True
                if fit_units != qty:
                    out[idx] = replace(act, target_level=fit_units)
            else:
                cur_r = _safe_strategy_str(act.reason)
                new_r = cur_r if _INFRA_HOLD_TAG.strip() in cur_r else f"{cur_r}{_INFRA_HOLD_TAG}"
                out[idx] = replace(act, executable_now=False, reason=new_r)
            continue

        fits = all(rem[rk] + 1e-6 >= need_c[rk] for rk in _BUDGET_RES_KEYS) and (
            not infra_spent or all(free_rem[rk] + 1e-6 >= need_c[rk] for rk in _BUDGET_RES_KEYS)
        )
        if fits:
            for rk in _BUDGET_RES_KEYS:
                rem[rk] = max(0.0, rem[rk] - need_c[rk])
                free_rem[rk] = max(0.0, free_rem[rk] - need_c[rk])
        else:
            cur_r = _safe_strategy_str(act.reason)
            new_r = cur_r if _SHIP_HOLD_TAG.strip() in cur_r else f"{cur_r}{_SHIP_HOLD_TAG}"
            out[idx] = replace(act, executable_now=False, reason=new_r)


def filter_shipyard_actions_by_spare_budget(
    state: GameState,
    actions: Sequence[PlannedAction],
    spare_budget: Mapping[str, object] | None,
) -> list[PlannedAction]:
    """Gate non-urgent `RESEARCH` (fleet tech) and `BUILD_SHIP` actions by `spare_budget`."""
    if not isinstance(spare_budget, Mapping):
        return list(actions)
    rem, infra_rem, free_rem, urg_s10, urg_tr = _extract_filter_budget_state(state, spare_budget)
    out: list[PlannedAction] = list(actions)
    _filter_research_actions(out, rem, free_rem)
    _filter_ship_actions(out, rem, infra_rem, free_rem, urg_s10=urg_s10, urg_tr=urg_tr)
    return out
