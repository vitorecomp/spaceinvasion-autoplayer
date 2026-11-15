"""Decomposed P0-P3 heuristic planner package (`backend.planner.heuristics`)."""

from __future__ import annotations

from backend.planner.heuristics._actions import (
    _make_build_action as make_build_action,
)
from backend.planner.heuristics._actions import (
    _make_research_action as make_research_action,
)
from backend.planner.heuristics._actions import (
    _pick_best_power_building as pick_best_power_building,
)
from backend.planner.heuristics._assembly_rank import summarize_plan as summarize_plan
from backend.planner.heuristics._cargo_helpers import (
    _in_flight_ship_counts as in_flight_ship_counts,
)
from backend.planner.heuristics._cargo_helpers import (
    compute_infra_transport_targets as compute_infra_transport_targets,
)
from backend.planner.heuristics._common import (
    _is_building_allowed_on_planet as is_building_allowed_on_planet,
)
from backend.planner.heuristics._common import (
    _is_stale_remote_resource_wait as _is_stale_remote_resource_wait,
)
from backend.planner.heuristics._common import (
    _norm_planet_type as norm_planet_type,
)
from backend.planner.heuristics._common import (
    _offer_map as offer_map,
)
from backend.planner.heuristics._common import (
    _research_offer_map as research_offer_map,
)
from backend.planner.heuristics._common import (
    _resources_from_dict as _resources_from_dict,
)
from backend.planner.heuristics._common import (
    _safe_strategy_float as safe_strategy_float,
)
from backend.planner.heuristics._common import (
    _safe_strategy_int as safe_strategy_int,
)
from backend.planner.heuristics._common import (
    _ship_offer_map as ship_offer_map,
)
from backend.planner.heuristics._queues import (
    _count_planet_building_queues as _count_planet_building_queues,
)
from backend.planner.heuristics._queues import (
    _count_planet_building_queues as count_planet_building_queues,
)
from backend.planner.heuristics._queues import (
    _effective_building_levels_and_energy as effective_building_levels_and_energy,
)
from backend.planner.heuristics._queues import (
    _max_build_slots as max_build_slots,
)
from backend.planner.heuristics._queues import (
    _queued_building_counts as queued_building_counts,
)
from backend.planner.heuristics._shipyard_research import (
    _plan_fleet_research_actions as plan_fleet_research_actions,
)
from backend.planner.heuristics._shipyard_targets import (
    _plan_planet_shipyard_targets as plan_planet_shipyard_targets,
)
from backend.planner.heuristics._shipyard_unlocks import (
    _plan_unlock_and_advisory_actions as plan_unlock_and_advisory_actions,
)
from backend.planner.heuristics._tutorial_fleet import (
    _plan_tutorial_fleet_milestones as plan_tutorial_fleet_milestones,
)
from backend.planner.heuristics._tutorial_tasks import (
    _tutorial_colonize_task as _tutorial_colonize_task,
)
from backend.planner.heuristics._types import (
    AI_INFRA_CARGO_TAG as AI_INFRA_CARGO_TAG,
)
from backend.planner.heuristics._types import (
    AI_PLAN_ORDER_LOAN_TAG as AI_PLAN_ORDER_LOAN_TAG,
)
from backend.planner.heuristics._types import (
    AI_THROTTLE_COOLDOWN_SECONDS as AI_THROTTLE_COOLDOWN_SECONDS,
)
from backend.planner.heuristics._types import (
    BLAST_FURNACE as BLAST_FURNACE,
)
from backend.planner.heuristics._types import (
    DEVELOPMENT_CENTER as DEVELOPMENT_CENTER,
)
from backend.planner.heuristics._types import (
    EMPIRE_SMELT_HOLD_RATIO as EMPIRE_SMELT_HOLD_RATIO,
)
from backend.planner.heuristics._types import (
    EMPIRE_SMELT_MIN_SPARE_PIG as EMPIRE_SMELT_MIN_SPARE_PIG,
)
from backend.planner.heuristics._types import (
    FLEET_BASE as FLEET_BASE,
)
from backend.planner.heuristics._types import (
    FUSION_PLANT as FUSION_PLANT,
)
from backend.planner.heuristics._types import (
    INFRA_TRANSPORT_FLEET_CAP_RATIO as INFRA_TRANSPORT_FLEET_CAP_RATIO,
)
from backend.planner.heuristics._types import (
    INFRA_TRANSPORT_INCOME_SHARE as INFRA_TRANSPORT_INCOME_SHARE,
)
from backend.planner.heuristics._types import (
    INFRA_TRANSPORT_MAX_BATCH as INFRA_TRANSPORT_MAX_BATCH,
)
from backend.planner.heuristics._types import (
    INFRA_TRANSPORT_MIN_UTILIZATION as INFRA_TRANSPORT_MIN_UTILIZATION,
)
from backend.planner.heuristics._types import (
    INFRA_TRANSPORT_RAID_ALLOWANCE_CARGO as INFRA_TRANSPORT_RAID_ALLOWANCE_CARGO,
)
from backend.planner.heuristics._types import (
    KRYPTONITE_MINE as KRYPTONITE_MINE,
)
from backend.planner.heuristics._types import (
    KRYPTONITE_WAREHOUSE as KRYPTONITE_WAREHOUSE,
)
from backend.planner.heuristics._types import (
    METAL_OVERFLOW_RESUME_PCT as METAL_OVERFLOW_RESUME_PCT,
)
from backend.planner.heuristics._types import (
    METAL_WAREHOUSE as METAL_WAREHOUSE,
)
from backend.planner.heuristics._types import (
    PIG_IRON_MINE as PIG_IRON_MINE,
)
from backend.planner.heuristics._types import (
    PIG_IRON_WAREHOUSE as PIG_IRON_WAREHOUSE,
)
from backend.planner.heuristics._types import (
    PLAN_ORDER_LOAN_MIN_CARGO as PLAN_ORDER_LOAN_MIN_CARGO,
)
from backend.planner.heuristics._types import (
    PLAN_ORDER_LOAN_MIN_SHARE as PLAN_ORDER_LOAN_MIN_SHARE,
)
from backend.planner.heuristics._types import (
    RESEARCH_LAB as RESEARCH_LAB,
)
from backend.planner.heuristics._types import (
    SHIP_SLUG_BY_KEY as SHIP_SLUG_BY_KEY,
)
from backend.planner.heuristics._types import (
    SOLAR_CELLS_ID as SOLAR_CELLS_ID,
)
from backend.planner.heuristics._types import (
    SPICE_MINE as SPICE_MINE,
)
from backend.planner.heuristics._types import (
    SPICE_WAREHOUSE as SPICE_WAREHOUSE,
)
from backend.planner.heuristics._types import (
    THERMOSOLAR_PLANT as THERMOSOLAR_PLANT,
)
from backend.planner.heuristics._types import (
    WEAPONS_FACTORY as WEAPONS_FACTORY,
)
from backend.planner.heuristics._types import (
    PlannedAction as PlannedAction,
)
from backend.planner.heuristics.assembly import (
    attach_action_etas as attach_action_etas,
)
from backend.planner.heuristics.assembly import (
    plan_actions as plan_actions,
)
from backend.planner.heuristics.assembly import (
    plan_fleet_and_attack_actions as plan_fleet_and_attack_actions,
)
from backend.planner.heuristics.blast_furnace import (
    _plan_blast_furnace_throttle as plan_blast_furnace_throttle,
)
from backend.planner.heuristics.blast_furnace import (
    p1_metal_shortfall as p1_metal_shortfall,
)
from backend.planner.heuristics.blast_furnace import (
    plan_ai_blast_furnace_throttles as plan_ai_blast_furnace_throttles,
)
from backend.planner.heuristics.blast_furnace import (
    plan_p1_metal_resume_throttle as plan_p1_metal_resume_throttle,
)
from backend.planner.heuristics.manual_queue import (
    merge_manual_queue_actions as merge_manual_queue_actions,
)
from backend.planner.heuristics.shipyard_budgets import (
    compute_spare_ship_budget as compute_spare_ship_budget,
)
from backend.planner.heuristics.shipyard_budgets import (
    filter_shipyard_actions_by_spare_budget as filter_shipyard_actions_by_spare_budget,
)
from backend.planner.heuristics.transport_station import (
    plan_interplanet_stationing as plan_interplanet_stationing,
)
from backend.planner.heuristics.transport_station import (
    plan_interplanet_transports as plan_interplanet_transports,
)
from backend.planner.heuristics.tutorial import (
    _plan_tutorial_actions as plan_tutorial_actions,
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
    "count_planet_building_queues",
    "effective_building_levels_and_energy",
    "filter_shipyard_actions_by_spare_budget",
    "in_flight_ship_counts",
    "is_building_allowed_on_planet",
    "make_build_action",
    "make_research_action",
    "max_build_slots",
    "merge_manual_queue_actions",
    "norm_planet_type",
    "offer_map",
    "p1_metal_shortfall",
    "pick_best_power_building",
    "plan_actions",
    "plan_ai_blast_furnace_throttles",
    "plan_blast_furnace_throttle",
    "plan_fleet_and_attack_actions",
    "plan_fleet_research_actions",
    "plan_interplanet_stationing",
    "plan_interplanet_transports",
    "plan_p1_metal_resume_throttle",
    "plan_planet_shipyard_targets",
    "plan_tutorial_actions",
    "plan_tutorial_fleet_milestones",
    "plan_unlock_and_advisory_actions",
    "queued_building_counts",
    "research_offer_map",
    "safe_strategy_float",
    "safe_strategy_int",
    "ship_offer_map",
    "summarize_plan",
]
