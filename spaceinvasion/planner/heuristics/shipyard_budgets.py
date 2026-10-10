"""Public facade for shipyard unlocks, fleet research, and spare-budget gating."""

from __future__ import annotations

from spaceinvasion.planner.heuristics._shipyard_budget_calc import compute_spare_ship_budget
from spaceinvasion.planner.heuristics._shipyard_budget_filter import filter_shipyard_actions_by_spare_budget
from spaceinvasion.planner.heuristics._shipyard_research import _plan_fleet_research_actions
from spaceinvasion.planner.heuristics._shipyard_targets import _plan_planet_shipyard_targets
from spaceinvasion.planner.heuristics._shipyard_unlocks import _plan_unlock_and_advisory_actions
from spaceinvasion.planner.heuristics._types import (
    AI_INFRA_CARGO_TAG,
    INFRA_TRANSPORT_INCOME_SHARE,
    INFRA_TRANSPORT_MAX_BATCH,
)

__all__ = [
    "AI_INFRA_CARGO_TAG",
    "INFRA_TRANSPORT_INCOME_SHARE",
    "INFRA_TRANSPORT_MAX_BATCH",
    "_plan_fleet_research_actions",
    "_plan_planet_shipyard_targets",
    "_plan_unlock_and_advisory_actions",
    "compute_spare_ship_budget",
    "filter_shipyard_actions_by_spare_budget",
]
