"""Top-level ``run_ai_planner`` orchestrator."""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
import datetime
import time

from spaceinvasion.core.coercion import coerce_finite_float, coerce_int, sanitize_json_tree
from spaceinvasion.planner.ai._runner_params import _normalize_ai_params, resolve_attack_income_basis
from spaceinvasion.planner.ai._runner_report import (
    _build_factor_levels,
    build_queue_and_weekly_breakdown,
    build_steps_output,
    build_transport_report,
)
from spaceinvasion.planner.ai._runner_summary import (
    build_smelting_report,
    build_summary_payload,
    enrich_attack_telemetry,
)
from spaceinvasion.planner.ai._types import PATH_GENE_RANGES, GameStateLike
from spaceinvasion.planner.ai.path_strategy import PathStrategy
from spaceinvasion.planner.ai.problem import build_path_problem
from spaceinvasion.planner.ai.simulator import EmpirePathSimulator
from spaceinvasion.planner.ai.telemetry import compute_attack_telemetry
from spaceinvasion.planner.ai.tuning import (
    _parse_warm_start_genes,
    path_fitness_key,
    strategy_from_genes,
    tune_path_strategy,
)
from spaceinvasion.planner.config_optimizer import normalize_planet_names_map


def _maybe_run_ga_tuning(
    simulator: EmpirePathSimulator,
    cfg: Mapping[str, object],
    warm_genes: tuple[float, ...] | None,
    best_strategy: PathStrategy,
    best_run: dict[str, object],
    search_summary: dict[str, object],
    progress_cb: Callable[[str, float], None] | None,
) -> tuple[PathStrategy, dict[str, object]]:
    reward_ub = float(simulator.reward_upper_bound)
    best_reward = coerce_finite_float(best_run.get("discounted_reward"), 0.0)
    headroom = max(0.0, 1.0 - best_reward / reward_ub) if reward_ub > 0.0 else 0.0
    ga_gen, ga_pop = coerce_int(cfg["ga_generations"], 6), coerce_int(cfg["ga_population_size"], 10)
    tol = coerce_finite_float(cfg["search_tolerance"], 0.01)
    if ga_gen <= 0 or ga_pop < 2 or len(simulator.steps) == 0:
        search_summary["why"] = "the genetic algorithm is switched off"
        return best_strategy, best_run
    if headroom <= tol:
        search_summary["why"] = (
            f"{best_strategy.name} is within {100.0 * headroom:.3f} % of the reward ceiling "
            f"(tolerance {100.0 * tol:.3f} %)"
        )
        return best_strategy, best_run
    reval = coerce_int(cfg["revalue_every_steps"], 25)
    start_genes = (
        warm_genes
        if warm_genes is not None
        else (
            coerce_finite_float(cfg["scarcity_focus"], 1.0),
            coerce_finite_float(cfg["critical_queue_share"], 0.9),
            coerce_finite_float(cfg["patience"], 0.25),
            coerce_finite_float(cfg["speed_weight"], 1.5),
            0.0,
            0.0,
            0.0,
            0.0,
        )
    )
    genes, tuned_run, ga_history = tune_path_strategy(
        simulator,
        start_genes,
        population_size=ga_pop,
        generations=ga_gen,
        tournament_size=coerce_int(cfg["ga_tournament_size"], 3),
        crossover_rate=coerce_finite_float(cfg["ga_crossover_rate"], 0.9),
        mutation_rate=coerce_finite_float(cfg["ga_mutation_rate"], 0.3),
        elite_count=coerce_int(cfg["ga_elite_count"], 2),
        random_seed=coerce_int(cfg["ga_random_seed"], 20261117),
        revalue_every_steps=reval,
        progress_cb=progress_cb,
        progress_range=(0.25, 0.90),
    )
    search_summary["genetic_algorithm_ran"] = True
    search_summary["simulations_of_the_genetic_algorithm"] = coerce_int(
        ga_history[-1].get("simulations_so_far") if ga_history else 0, 0
    )
    search_summary["tuned_genes"] = {name: round(float(g), 6) for name, g in zip(PATH_GENE_RANGES, genes, strict=False)}
    search_summary["tuned_reward"] = coerce_finite_float(tuned_run.get("discounted_reward"), 0.0)
    search_summary["ga_history"] = ga_history
    search_summary["why"] = f"{best_strategy.name} left {100.0 * headroom:.2f} % headroom below ceiling"
    if path_fitness_key(tuned_run) > path_fitness_key(best_run):
        return strategy_from_genes("discounted payback tuned by the genetic algorithm", genes, reval), tuned_run
    return best_strategy, best_run


def _run_strategies_and_ga(
    simulator: EmpirePathSimulator,
    cfg: Mapping[str, object],
    warm_genes: tuple[float, ...] | None,
    progress_cb: Callable[[str, float], None] | None,
) -> tuple[PathStrategy, dict[str, object], dict[str, object]]:
    patience, crit_share = (
        coerce_finite_float(cfg["patience"], 0.25),
        coerce_finite_float(cfg["critical_queue_share"], 0.9),
    )
    speed_w, scarcity_f = coerce_finite_float(cfg["speed_weight"], 1.5), coerce_finite_float(cfg["scarcity_focus"], 1.0)
    reval = coerce_int(cfg["revalue_every_steps"], 25)
    strategies: list[PathStrategy] = [
        PathStrategy("cheapest step first", rule="cheapest", patience=patience),
        PathStrategy(
            "discounted payback at Merchant values",
            rule="payback",
            values="merchant",
            critical_share=crit_share,
            patience=patience,
            speed_weight=speed_w,
            revalue_every_steps=reval,
        ),
        PathStrategy(
            "discounted payback at scarcity values",
            rule="payback",
            values="scarcity",
            focus=scarcity_f,
            critical_share=crit_share,
            patience=patience,
            speed_weight=speed_w,
            revalue_every_steps=reval,
        ),
    ]
    if warm_genes is not None:
        strategies.append(strategy_from_genes("warm-start tuned strategy", warm_genes, reval))

    baseline_runs: dict[str, dict[str, object]] = {}
    for idx, strat in enumerate(strategies):
        baseline_runs[strat.name] = simulator.run(strat)
        if progress_cb is not None:
            progress_cb("simulating_baselines", 0.12 + 0.13 * (idx + 1) / len(strategies))

    active = [s for s in strategies if coerce_int(baseline_runs[s.name].get("steps_started"), 0) > 0]
    search_summary: dict[str, object] = {
        "reward_upper_bound": float(simulator.reward_upper_bound),
        "genetic_algorithm_ran": False,
        "simulations_of_the_genetic_algorithm": 0,
        "tuned_genes": None,
        "tuned_reward": None,
        "warm_start_used": warm_genes is not None,
        "warm_start_genes": (
            {name: round(float(g), 6) for name, g in zip(PATH_GENE_RANGES, warm_genes, strict=False)}
            if warm_genes is not None
            else None
        ),
        "ga_history": [],
    }
    if not active and len(simulator.steps) > 0:
        best_strategy = strategies[-1]
        best_run = baseline_runs[best_strategy.name]
        search_summary["why"] = "no strategy can start any step"
    else:
        candidates = active if active else list(strategies)
        best_strategy = max(candidates, key=lambda s: path_fitness_key(baseline_runs[s.name]))
        best_run = baseline_runs[best_strategy.name]
        best_strategy, best_run = _maybe_run_ga_tuning(
            simulator, cfg, warm_genes, best_strategy, best_run, search_summary, progress_cb
        )

    search_summary["best_strategy"] = best_strategy.name
    search_summary["best_reward"] = coerce_finite_float(best_run.get("discounted_reward"), 0.0)
    search_summary["best_parameters"] = best_strategy.parameters()
    search_summary["strategy_comparisons"] = [
        {
            "strategy": s.name,
            "status": baseline_runs[s.name]["status"],
            "discounted_reward": round(coerce_finite_float(baseline_runs[s.name].get("discounted_reward"), 0.0), 2),
            "total_spice_eq_produced": round(
                coerce_finite_float(baseline_runs[s.name].get("total_spice_eq_produced"), 0.0), 2
            ),
            "steps_started": coerce_int(baseline_runs[s.name].get("steps_started"), 0),
            "steps_completed": coerce_int(baseline_runs[s.name].get("steps_completed"), 0),
        }
        for s in strategies
    ]
    return best_strategy, best_run, search_summary


def _extract_in_progress_sections(
    problem: Mapping[str, object],
) -> tuple[list[dict[str, object]], list[dict[str, object]], list[dict[str, object]]]:
    raw_b, raw_r, raw_f = (
        problem.get("in_progress_builds"),
        problem.get("in_progress_research"),
        problem.get("in_flight_arrivals"),
    )
    in_b = [dict(r) for r in raw_b if isinstance(r, Mapping)] if isinstance(raw_b, Sequence) else []
    in_r = [dict(r) for r in raw_r if isinstance(r, Mapping)] if isinstance(raw_r, Sequence) else []
    in_f = (
        [{k: v for k, v in r.items() if k != "cargo_tuple"} for r in raw_f if isinstance(r, Mapping)]
        if isinstance(raw_f, Sequence)
        else []
    )
    return in_b, in_r, in_f


def _assemble_final_payload(
    simulator: EmpirePathSimulator,
    problem: Mapping[str, object],
    target_config: Mapping[str, object],
    cfg: Mapping[str, object],
    telemetry: Mapping[str, object],
    best_strategy: PathStrategy,
    best_run: Mapping[str, object],
    search_summary: Mapping[str, object],
    income_meta: tuple[dict[str, float], dict[str, float], dict[str, float], float, int, bool, float, bool, str],
    t0: float,
) -> dict[str, object]:
    fixed_inc, slot_inc, med_loot, med_rt, scnt, has_loot, per_slot, use_realized, pace_by = income_meta
    busiest_q, busiest_h, weekly_bd = build_queue_and_weekly_breakdown(simulator, best_run)
    factor_levels = _build_factor_levels(simulator, best_run)
    dispatch_delay_sec = round(simulator.dispatch_delay_hours * 3600.0, 2)
    steps_out = build_steps_output(simulator, best_run, dispatch_delay_sec)
    enriched_tel, tel_per_slot = enrich_attack_telemetry(
        telemetry,
        cfg,
        median_loot=med_loot,
        median_rt=med_rt,
        sample_count=scnt,
        has_active_loot=has_loot,
        per_slot_hour=per_slot,
        use_realized=use_realized,
        pace_set_by=pace_by,
        fixed_inc=fixed_inc,
        slot_inc=slot_inc,
        start_slots=coerce_int(best_run.get("start_fleet_slots"), 0),
    )
    tr_matrix, tr_demand = build_transport_report(simulator, problem, best_run, dispatch_delay_sec)
    smelting_out = build_smelting_report(simulator, best_run)
    in_b, in_r, in_f = _extract_in_progress_sections(problem)
    elapsed_ms = round((time.perf_counter() - t0) * 1000.0, 2)
    summary = build_summary_payload(
        simulator,
        problem,
        best_strategy.name,
        best_run,
        factor_levels=factor_levels,
        steps_out=steps_out,
        smelting_out=smelting_out,
        transport_demand_out=tr_demand,
        busiest_queue=busiest_q,
        busiest_hours=busiest_h,
        tel_per_slot=tel_per_slot,
        use_realized_income=use_realized,
        in_prog_counts=(len(in_b), len(in_r), len(in_f)),
        elapsed_ms=elapsed_ms,
    )
    raw_demo = problem.get("demolish_targets")
    payload = sanitize_json_tree(
        {
            "schema_version": 1,
            "generated_at": datetime.datetime.now().astimezone().isoformat(timespec="seconds"),
            "config_generated_at": str(target_config.get("generated_at") or ""),
            "allow_demolition": bool(problem.get("allow_demolition", False)),
            "demolish_targets": dict(raw_demo) if isinstance(raw_demo, Mapping) else {},
            "meta_parameters": cfg,
            "attack_telemetry": enriched_tel,
            "summary": summary,
            "search": search_summary,
            "in_progress": {"buildings": in_b, "research": in_r, "fleet_arrivals": in_f},
            "smelting": smelting_out,
            "transport_matrix": tr_matrix,
            "transport_demand": tr_demand,
            "weekly_breakdown": weekly_bd,
            "factor_levels": factor_levels,
            "steps": steps_out,
            "elapsed_ms": elapsed_ms,
        }
    )
    return payload if isinstance(payload, dict) else {}


def run_ai_planner(
    state: GameStateLike,
    target_config: Mapping[str, object],
    *,
    attack_telemetry: Mapping[str, object] | None = None,
    params: Mapping[str, object] | None = None,
    progress_cb: Callable[[str, float], None] | None = None,
) -> dict[str, object]:
    """Plan the W-week discounted-reward build/research sequence from live ``state`` to ``target_config``."""
    t0 = time.perf_counter()
    if progress_cb is not None:
        progress_cb("preparing", 0.04)

    cfg = _normalize_ai_params(params)
    param_names: dict[str, str] = {}
    if isinstance(params, Mapping):
        param_names.update(normalize_planet_names_map(params.get("custom_planet_names")))
        param_names.update(normalize_planet_names_map(params.get("planet_names")))
    problem = build_path_problem(
        state,
        target_config,
        pad_generic_colonies=bool(cfg["pad_generic_colonies"]),
        colony_founding_hours=coerce_finite_float(cfg.get("colony_founding_hours"), 0.0),
        planet_names=param_names or None,
    )
    telemetry = (
        {str(k): v for k, v in attack_telemetry.items()}
        if isinstance(attack_telemetry, Mapping)
        else compute_attack_telemetry([], None, state=state)
    )
    income_meta = resolve_attack_income_basis(telemetry, cfg)
    raw_spare = cfg["resource_spare_percent"]
    spare_map = (
        {str(k): coerce_finite_float(v, 10.0) for k, v in raw_spare.items()} if isinstance(raw_spare, Mapping) else {}
    )
    fixed_cc = (
        coerce_int(cfg["fleet_command_center_level"], 0) if cfg["fleet_command_center_level"] is not None else None
    )
    simulator = EmpirePathSimulator(
        problem,
        income_meta[0],
        slot_attack_income=income_meta[1],
        fleet_command_center_level=fixed_cc,
        spare_percent=spare_map,
        reward_weeks=coerce_int(cfg["reward_weeks"], 26),
        weekly_discount=coerce_finite_float(cfg["weekly_discount"], 0.90),
        transport_dispatch_delay_minutes=coerce_finite_float(cfg["transport_dispatch_delay_minutes"], 120.0),
        model_transport_travel=True,
    )
    if progress_cb is not None:
        progress_cb("simulating_baselines", 0.12)
    warm_genes = _parse_warm_start_genes(params.get("warm_start_genes") if isinstance(params, Mapping) else None)
    best_strategy, best_run, search_summary = _run_strategies_and_ga(simulator, cfg, warm_genes, progress_cb)
    if progress_cb is not None:
        progress_cb("finalizing", 0.94)
    result = _assemble_final_payload(
        simulator, problem, target_config, cfg, telemetry, best_strategy, best_run, search_summary, income_meta, t0
    )
    if progress_cb is not None:
        progress_cb("completed", 1.0)
    return result
