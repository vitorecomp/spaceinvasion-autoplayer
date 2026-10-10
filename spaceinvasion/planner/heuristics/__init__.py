"""Decomposed P0-P3 heuristic planner package (`spaceinvasion.planner.heuristics`)."""

from __future__ import annotations

from spaceinvasion.planner.heuristics._assembly_rank import summarize_plan
from spaceinvasion.planner.heuristics._cargo_helpers import compute_infra_transport_targets
from spaceinvasion.planner.heuristics._types import (
    AI_INFRA_CARGO_TAG,
    AI_PLAN_ORDER_LOAN_TAG,
    AI_THROTTLE_COOLDOWN_SECONDS,
    BLAST_FURNACE,
    DEVELOPMENT_CENTER,
    EMPIRE_SMELT_HOLD_RATIO,
    EMPIRE_SMELT_MIN_SPARE_PIG,
    FLEET_BASE,
    FUSION_PLANT,
    INFRA_TRANSPORT_FLEET_CAP_RATIO,
    INFRA_TRANSPORT_INCOME_SHARE,
    INFRA_TRANSPORT_MAX_BATCH,
    INFRA_TRANSPORT_MIN_UTILIZATION,
    INFRA_TRANSPORT_RAID_ALLOWANCE_CARGO,
    KRYPTONITE_MINE,
    KRYPTONITE_WAREHOUSE,
    METAL_OVERFLOW_RESUME_PCT,
    METAL_WAREHOUSE,
    PIG_IRON_MINE,
    PIG_IRON_WAREHOUSE,
    PLAN_ORDER_LOAN_MIN_CARGO,
    PLAN_ORDER_LOAN_MIN_SHARE,
    RESEARCH_LAB,
    SHIP_SLUG_BY_KEY,
    SOLAR_CELLS_ID,
    SPICE_MINE,
    SPICE_WAREHOUSE,
    THERMOSOLAR_PLANT,
    WEAPONS_FACTORY,
    PlannedAction,
)
from spaceinvasion.planner.heuristics.assembly import (
    attach_action_etas,
    plan_actions,
    plan_fleet_and_attack_actions,
)
from spaceinvasion.planner.heuristics.blast_furnace import (
    p1_metal_shortfall,
    plan_ai_blast_furnace_throttles,
    plan_p1_metal_resume_throttle,
)
from spaceinvasion.planner.heuristics.manual_queue import merge_manual_queue_actions
from spaceinvasion.planner.heuristics.shipyard_budgets import (
    compute_spare_ship_budget,
    filter_shipyard_actions_by_spare_budget,
)
from spaceinvasion.planner.heuristics.transport_station import (
    plan_interplanet_stationing,
    plan_interplanet_transports,
)

__all__ = [
    "AI_INFRA_CARGO_TAG",
    "AI_PLAN_ORDER_LOAN_TAG",
    "AI_THROTTLE_COOLDOWN_SECONDS",
    "BLAST_FURNACE",
    "DEVELOPMENT_CENTER",
    "EMPIRE_SMELT_HOLD_RATIO",
    "EMPIRE_SMELT_MIN_SPARE_PIG",
    "FLEET_BASE",
    "FUSION_PLANT",
    "INFRA_TRANSPORT_FLEET_CAP_RATIO",
    "INFRA_TRANSPORT_INCOME_SHARE",
    "INFRA_TRANSPORT_MAX_BATCH",
    "INFRA_TRANSPORT_MIN_UTILIZATION",
    "INFRA_TRANSPORT_RAID_ALLOWANCE_CARGO",
    "KRYPTONITE_MINE",
    "KRYPTONITE_WAREHOUSE",
    "METAL_OVERFLOW_RESUME_PCT",
    "METAL_WAREHOUSE",
    "PIG_IRON_MINE",
    "PIG_IRON_WAREHOUSE",
    "PLAN_ORDER_LOAN_MIN_CARGO",
    "PLAN_ORDER_LOAN_MIN_SHARE",
    "RESEARCH_LAB",
    "SHIP_SLUG_BY_KEY",
    "SOLAR_CELLS_ID",
    "SPICE_MINE",
    "SPICE_WAREHOUSE",
    "THERMOSOLAR_PLANT",
    "WEAPONS_FACTORY",
    "PlannedAction",
    "attach_action_etas",
    "compute_infra_transport_targets",
    "compute_spare_ship_budget",
    "filter_shipyard_actions_by_spare_budget",
    "merge_manual_queue_actions",
    "p1_metal_shortfall",
    "plan_actions",
    "plan_ai_blast_furnace_throttles",
    "plan_fleet_and_attack_actions",
    "plan_interplanet_stationing",
    "plan_interplanet_transports",
    "plan_p1_metal_resume_throttle",
    "summarize_plan",
]
