"""Modular W-week AI-Planner package (telemetry, problem, simulator, path strategy, tuning, live reconcile, diagnostics)."""

from __future__ import annotations

from spaceinvasion.planner.ai._problem_in_progress import (
    _extract_live_in_progress_state,
    compute_in_progress_build_rem_by_planet,
    compute_in_progress_research_rem_seconds,
)
from spaceinvasion.planner.ai._problem_in_progress import (
    _extract_live_in_progress_state as extract_live_in_progress_state,
)
from spaceinvasion.planner.ai._problem_queues import (
    compute_inbound_friendly_cargo_by_planet,
    estimate_interplanet_transport_seconds,
    summarize_blast_furnace_entries,
    summarize_live_blast_furnace_state,
)
from spaceinvasion.planner.ai._runner_params import _normalize_ai_params
from spaceinvasion.planner.ai._runner_report import _build_factor_levels
from spaceinvasion.planner.ai._types import (
    DEFAULT_AI_PLANNER_PARAMS,
    DEFAULT_MEDIAN_ROUND_TRIP_SECONDS,
    PATH_ACCELERATOR_KEYS,
    PATH_FLOW_BUILDING_KEYS,
    PATH_GENE_RANGES,
    PATH_HOURS_PER_WEEK,
    PATH_MINE_KEYS,
    PATH_NEVER,
    REALIZED_INCOME_BASIS,
    safe_speedup,
)
from spaceinvasion.planner.ai._types import (
    safe_speedup as _safe_speedup,
)
from spaceinvasion.planner.ai.diagnostics import AI_COLONY_IDLE_STATES, diagnose_ai_colony_idle_states
from spaceinvasion.planner.ai.export import export_ai_planner_txt
from spaceinvasion.planner.ai.live_fill import (
    _AI_STEP_SEQ_RE,
    AI_FILL_MAX_PATIENCE,
    AI_FILL_MIN_PATIENCE,
    AI_FILL_POOL_LEAD_HOURS,
    AI_FILL_REASON_PREFIX,
    AI_STEP_SEQ_RE,
    _hours_until_paid,
    _plan_fill_patience,
    plan_fill_patience,
    plan_live_fill_actions,
)
from spaceinvasion.planner.ai.live_reconcile import reconcile_live_ai_actions
from spaceinvasion.planner.ai.path_strategy import PathStrategy
from spaceinvasion.planner.ai.problem import build_path_problem, planet_hourly_output
from spaceinvasion.planner.ai.runner import run_ai_planner
from spaceinvasion.planner.ai.simulator import EmpirePathSimulator
from spaceinvasion.planner.ai.telemetry import compute_attack_telemetry, fmt_path_time, is_realized_income_basis
from spaceinvasion.planner.ai.transport_needs import compute_early_transport_needs
from spaceinvasion.planner.ai.tuning import (
    _parse_warm_start_genes,
    path_fitness_key,
    strategy_from_genes,
    tune_path_strategy,
)

__all__ = [
    "AI_COLONY_IDLE_STATES",
    "AI_FILL_MAX_PATIENCE",
    "AI_FILL_MIN_PATIENCE",
    "AI_FILL_POOL_LEAD_HOURS",
    "AI_FILL_REASON_PREFIX",
    "AI_STEP_SEQ_RE",
    "DEFAULT_AI_PLANNER_PARAMS",
    "DEFAULT_MEDIAN_ROUND_TRIP_SECONDS",
    "PATH_ACCELERATOR_KEYS",
    "PATH_FLOW_BUILDING_KEYS",
    "PATH_GENE_RANGES",
    "PATH_HOURS_PER_WEEK",
    "PATH_MINE_KEYS",
    "PATH_NEVER",
    "REALIZED_INCOME_BASIS",
    "EmpirePathSimulator",
    "PathStrategy",
    "_AI_STEP_SEQ_RE",
    "_build_factor_levels",
    "_extract_live_in_progress_state",
    "_hours_until_paid",
    "_normalize_ai_params",
    "_parse_warm_start_genes",
    "_plan_fill_patience",
    "_safe_speedup",
    "build_path_problem",
    "compute_attack_telemetry",
    "compute_early_transport_needs",
    "compute_in_progress_build_rem_by_planet",
    "compute_in_progress_research_rem_seconds",
    "compute_inbound_friendly_cargo_by_planet",
    "diagnose_ai_colony_idle_states",
    "estimate_interplanet_transport_seconds",
    "export_ai_planner_txt",
    "extract_live_in_progress_state",
    "fmt_path_time",
    "is_realized_income_basis",
    "path_fitness_key",
    "plan_fill_patience",
    "plan_live_fill_actions",
    "planet_hourly_output",
    "reconcile_live_ai_actions",
    "run_ai_planner",
    "safe_speedup",
    "strategy_from_genes",
    "summarize_blast_furnace_entries",
    "summarize_live_blast_furnace_state",
    "tune_path_strategy",
]
