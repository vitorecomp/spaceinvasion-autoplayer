"""Manual queue utility item builders (`SCAN_GALAXY`, `CLAIM_TUTORIAL`, `ACTIVATE_TRANSMITTER`, `ABANDON_COLONY`)."""

from __future__ import annotations

from collections.abc import Mapping
import re

from spaceinvasion.core.model import Resources, homeworld_planet_id, is_undersized_fresh_colony
from spaceinvasion.planner.heuristics._common import (
    _normalize_manual_coords,
    _safe_strategy_int,
)
from spaceinvasion.planner.heuristics._manual_builders import _ManualBuildResult, _ManualItemContext
from spaceinvasion.planner.heuristics._types import PlannedAction


def _build_manual_scan_item(ctx: _ManualItemContext, item: Mapping[str, object]) -> _ManualBuildResult:
    sys_c = _normalize_manual_coords(item.get("target_coords") or item.get("target_name") or ctx.planet.coords[:6])
    m_gs = re.search(r"(\d{1,2}):(\d{1,3})", sys_c, re.ASCII)
    lbl_gs = f"{int(m_gs.group(1)):02d}:{int(m_gs.group(2)):03d}" if m_gs else (ctx.planet.coords[:6] or "01:001")
    return _ManualBuildResult(
        action=PlannedAction(
            priority=ctx.prio,
            kind="SCAN_GALAXY",
            planet_id=ctx.planet.planet_id,
            planet_coords=ctx.planet.coords,
            target_id=None,
            target_name=f"Galaxy [{lbl_gs}]",
            target_level=None,
            cost=Resources(),
            missing=Resources(),
            reason=f"{ctx.base_tag}: Scan system [{lbl_gs}]{ctx.note_suffix}",
            executable_now=True,
            manual_queue_id=ctx.qid,
            manual_sort_rank=ctx.srank,
        )
    )


def _build_manual_claim_tutorial_item(ctx: _ManualItemContext) -> _ManualBuildResult:
    tut_ok = bool(ctx.state.tutorial is not None and getattr(ctx.state.tutorial, "can_complete", False))
    return _ManualBuildResult(
        action=PlannedAction(
            priority=ctx.prio,
            kind="CLAIM_TUTORIAL",
            planet_id=ctx.planet.planet_id,
            planet_coords=ctx.planet.coords,
            target_id=None,
            target_name="Claim Tutorial Reward",
            target_level=None,
            cost=Resources(),
            missing=Resources(),
            reason=f"{ctx.base_tag}: Claim completed tutorial step{ctx.note_suffix}"
            + ("" if tut_ok else " (tasks incomplete)"),
            executable_now=tut_ok,
            manual_queue_id=ctx.qid,
            manual_sort_rank=ctx.srank,
        )
    )


def _build_manual_transmitter_item(ctx: _ManualItemContext) -> _ManualBuildResult:
    can_tm = bool(getattr(ctx.cfg, "allow_transmitter", True))
    hw_pid_mq = homeworld_planet_id(ctx.state)
    tm_planet = ctx.planets_by_id[hw_pid_mq] if hw_pid_mq is not None and hw_pid_mq in ctx.planets_by_id else ctx.planet
    return _ManualBuildResult(
        action=PlannedAction(
            priority=ctx.prio,
            kind="ACTIVATE_TRANSMITTER",
            planet_id=tm_planet.planet_id,
            planet_coords=tm_planet.coords,
            target_id=None,
            target_name="Interdimensional Transmitter",
            target_level=None,
            cost=Resources(),
            missing=Resources(),
            reason=(
                f"{ctx.base_tag}: Collect Command Central daily bonus & claim free Interdimensional "
                f"Transmitter spin on Homeworld [{tm_planet.coords}]{ctx.note_suffix}"
            ),
            executable_now=can_tm,
            manual_queue_id=ctx.qid,
            manual_sort_rank=ctx.srank,
        )
    )


def _build_manual_abandon_item(
    ctx: _ManualItemContext,
    item: Mapping[str, object],
    *,
    pid_req: int,
) -> _ManualBuildResult:
    if pid_req > 0 and pid_req not in ctx.planets_by_id:
        return _ManualBuildResult(auto_satisfied=True)
    min_f_mq = max(50, _safe_strategy_int(item.get("target_level") or getattr(ctx.cfg, "min_colony_fields", 300), 300))
    safe_ab = is_undersized_fresh_colony(ctx.planet, ctx.state, min_f_mq)
    can_ab = bool(safe_ab and getattr(ctx.cfg, "allow_colonize", False))
    ab_rsn = (
        f"{ctx.base_tag}: Delete undersized colony {ctx.planet.coords} "
        f"({ctx.planet.fields_max}/{min_f_mq} fields){ctx.note_suffix}"
    )
    if not safe_ab:
        ab_rsn += " (blocked: Homeworld, developed, stationed fleet, or meets min_colony_fields)"
    elif not getattr(ctx.cfg, "allow_colonize", False):
        ab_rsn += " [Hold: allow_colonize=false in Config]"
    return _ManualBuildResult(
        action=PlannedAction(
            priority=ctx.prio,
            kind="ABANDON_COLONY",
            planet_id=ctx.planet.planet_id,
            planet_coords=ctx.planet.coords,
            target_id=None,
            target_name=f"Delete colony {ctx.planet.coords} ({ctx.planet.fields_max}/{min_f_mq} fields)",
            target_level=min_f_mq,
            cost=Resources(),
            missing=Resources(),
            reason=ab_rsn,
            executable_now=can_ab,
            manual_queue_id=ctx.qid,
            manual_sort_rank=ctx.srank,
        )
    )


def _build_manual_utility_item(
    ctx: _ManualItemContext,
    kind: str,
    item: Mapping[str, object],
    *,
    pid_req: int,
) -> _ManualBuildResult:
    if kind == "SCAN_GALAXY":
        return _build_manual_scan_item(ctx, item)
    if kind == "CLAIM_TUTORIAL":
        return _build_manual_claim_tutorial_item(ctx)
    if kind == "ACTIVATE_TRANSMITTER":
        return _build_manual_transmitter_item(ctx)
    if kind == "ABANDON_COLONY":
        return _build_manual_abandon_item(ctx, item, pid_req=pid_req)
    return _ManualBuildResult()
