"""Genetic algorithm, coordinate-ascent polishing, and central-reserve search rounds."""

from __future__ import annotations

from collections.abc import Sequence

import numpy as np

from spaceinvasion.planner.config_optimizer._search_catalogue import (
    ProgressCallback,
    ScoreFn,
    balanced_score,
    build_design_catalogues,
    catalogue_limits,
    improve_design,
    planet_moves,
    smallest_limit,
)
from spaceinvasion.planner.config_optimizer._search_reserve import (
    GAHistoryRow,
    RoundRow,
    SearchRoundResult,
    SearchSettings,
    _build_round_row,
    _finalize_self_consistent_round,
    _polish_holding_central_reserve,
    _refine_overshot_central_reserve,
    central_reserve_needed,
)
from spaceinvasion.planner.config_optimizer.constants import RESOURCE_KEYS, FloatArray, IntArray
from spaceinvasion.planner.config_optimizer.model import EmpireProductionModel

SearchInRoundsResult = tuple[
    EmpireProductionModel,
    list[RoundRow],
    list[IntArray],
    IntArray,
    list[GAHistoryRow],
    IntArray,
    IntArray,
]


def run_genetic_algorithm(
    design_limits: FloatArray,
    design_counts: IntArray,
    *,
    population_size: int,
    generations: int,
    tournament_size: int,
    crossover_rate: float,
    mutation_rate: float,
    elite_count: int,
    seed: int,
    progress_cb: ProgressCallback | None = None,
    progress_range: tuple[float, float] = (0.5, 0.8),
) -> tuple[IntArray, list[GAHistoryRow]]:
    """Evolve catalogue-design choices across planets; return ``(best_choice, history_rows)``."""
    rng = np.random.default_rng(int(seed))
    planets = np.arange(len(design_counts))
    population: IntArray = np.asarray(
        rng.integers(0, design_counts, size=(population_size, len(design_counts))),
        dtype=np.int64,
    )
    history: list[GAHistoryRow] = []
    p0, p1 = progress_range
    stride = max(1, generations // 15) if generations > 0 else 1
    ranking = np.arange(population_size)
    for generation in range(generations + 1):
        fitness: FloatArray = np.asarray(design_limits[planets, population].sum(axis=1).min(axis=1), dtype=np.float64)
        ranking = np.argsort(-fitness, kind="stable")
        history.append(
            {
                "generation": generation,
                "best_ships_per_month": float(fitness[ranking[0]]),
                "mean_ships_per_month": float(fitness.mean()),
                "worst_ships_per_month": float(fitness[ranking[-1]]),
            }
        )
        if progress_cb is not None and (generation % stride == 0 or generation == generations):
            frac = (generation / float(max(1, generations))) if generations > 0 else 1.0
            progress_cb("evolving_ga", p0 + (p1 - p0) * frac)
        if generation == generations:
            break
        contenders = rng.integers(0, population_size, size=(2 * population_size, tournament_size))
        winners = contenders[np.arange(2 * population_size), fitness[contenders].argmax(axis=1)]
        mothers, fathers = population[winners[:population_size]], population[winners[population_size:]]
        from_father = (rng.random(mothers.shape) < 0.5) & (rng.random((population_size, 1)) < crossover_rate)
        children = np.where(from_father, fathers, mothers)
        mutated = rng.random(children.shape) < mutation_rate
        children = np.where(mutated, rng.integers(0, design_counts, size=children.shape), children)
        population = np.asarray(
            np.concatenate([population[ranking[:elite_count]], children[: population_size - elite_count]]),
            dtype=np.int64,
        )
    return np.asarray(population[ranking[0]].copy(), dtype=np.int64), history


def polish_configuration(
    model: EmpireProductionModel,
    levels: IntArray,
    score: ScoreFn,
    step_sizes: tuple[int, ...],
) -> IntArray:
    """Coordinate-ascent hill-climb over the whole empire, one planet at a time."""
    moves = planet_moves(levels.shape[1], step_sizes)
    cur: IntArray = np.array(levels, dtype=np.int64)
    planet_limits = np.asarray(model.simulate(cur[None])["planet_limits"], dtype=np.float64)[0]
    changed = True
    while changed:
        changed = False
        for planet in range(len(cur)):
            others: FloatArray = np.asarray(planet_limits.sum(axis=0) - planet_limits[planet], dtype=np.float64)

            def empire_score(own: FloatArray, others: FloatArray = others) -> FloatArray:
                return score(np.asarray(others + own, dtype=np.float64))

            design = improve_design(model, planet, cur[planet], empire_score, moves)
            if bool((design != cur[planet]).any()):
                cur[planet] = design
                planet_limits[planet] = np.asarray(
                    model.simulate(design[None, None, :], [planet])["planet_limits"], dtype=np.float64
                )[0, 0]
                changed = True
    return cur


def polish_both_ways(
    model: EmpireProductionModel,
    levels: IntArray,
    step_sizes: tuple[int, ...],
) -> IntArray:
    """Polish directly and via ``balanced_score``, keeping whichever builds more ships (fewer level changes on ties)."""
    direct = polish_configuration(model, levels, smallest_limit, step_sizes)
    rebalanced = polish_configuration(
        model,
        polish_configuration(model, levels, balanced_score, step_sizes),
        smallest_limit,
        step_sizes,
    )
    ships = model.ships_per_month(np.array([direct, rebalanced], dtype=np.int64))
    direct_changes = int(np.abs(direct - model.built_opt_levels).sum())
    rebal_changes = int(np.abs(rebalanced - model.built_opt_levels).sum())
    return (
        rebalanced
        if (float(ships[1]), -rebal_changes, -int(rebalanced.sum()))
        > (float(ships[0]), -direct_changes, -int(direct.sum()))
        else direct
    )


def search_best_configuration(
    model: EmpireProductionModel,
    *,
    grid_size: int,
    priority_steps: int,
    step_sizes: tuple[int, ...],
    population_size: int,
    generations: int,
    tournament_size: int,
    crossover_rate: float,
    mutation_rate: float,
    elite_count: int,
    seed: int,
    progress_cb: ProgressCallback | None = None,
    progress_range: tuple[float, float] = (0.1, 0.9),
) -> SearchRoundResult:
    """One search round: design catalogues -> GA -> ``polish_both_ways``."""
    p0, p1 = progress_range
    p_cat_end = p0 + 0.45 * (p1 - p0)
    p_ga_end = p0 + 0.80 * (p1 - p0)
    catalogues = build_design_catalogues(
        model,
        grid_size=grid_size,
        priority_steps=priority_steps,
        step_sizes=step_sizes,
        progress_cb=progress_cb,
        progress_range=(p0, p_cat_end),
    )
    design_limits, design_counts = catalogue_limits(model, catalogues)
    ga_designs, ga_history = run_genetic_algorithm(
        design_limits,
        design_counts,
        population_size=population_size,
        generations=generations,
        tournament_size=tournament_size,
        crossover_rate=crossover_rate,
        mutation_rate=mutation_rate,
        elite_count=elite_count,
        seed=seed,
        progress_cb=progress_cb,
        progress_range=(p_cat_end, p_ga_end),
    )
    if progress_cb is not None:
        progress_cb("polishing", p_ga_end)
    ga_levels: IntArray = np.array(
        [catalogues[planet][design] for planet, design in enumerate(ga_designs)],
        dtype=np.int64,
    )
    polished = polish_both_ways(model, ga_levels, step_sizes)
    if progress_cb is not None:
        progress_cb("polishing", p1)
    return catalogues, ga_designs, ga_history, ga_levels, polished


def _run_one_search(
    round_model: EmpireProductionModel,
    settings: SearchSettings,
    progress_cb: ProgressCallback | None,
    progress_range: tuple[float, float],
) -> SearchRoundResult:
    return search_best_configuration(
        round_model,
        grid_size=settings.grid_size,
        priority_steps=settings.priority_steps,
        step_sizes=settings.step_sizes,
        population_size=settings.population_size,
        generations=settings.generations,
        tournament_size=settings.tournament_size,
        crossover_rate=settings.crossover_rate,
        mutation_rate=settings.mutation_rate,
        elite_count=settings.elite_count,
        seed=settings.seed,
        progress_cb=progress_cb,
        progress_range=progress_range,
    )


def _evaluate_round_reserve(
    round_model: EmpireProductionModel,
    best_levels: IntArray,
) -> tuple[FloatArray, IntArray, FloatArray, bool]:
    cp = round_model.central_planet
    needed = central_reserve_needed(round_model, best_levels)
    warehouses = np.asarray(round_model.simulate(best_levels[None])["planet_warehouse_levels"], dtype=np.int64)[0]
    held: FloatArray = (
        round_model.warehouse_capacities[warehouses[cp]]
        if cp >= 0
        else np.full(len(RESOURCE_KEYS), np.inf, dtype=np.float64)
    )
    return needed, warehouses, held, not bool((needed > held).any())


def search_in_rounds(
    model: EmpireProductionModel,
    *,
    grid_size: int,
    priority_steps: int,
    step_sizes: Sequence[int] = (1, 2, 4, 8),
    population_size: int,
    generations: int,
    tournament_size: int,
    crossover_rate: float,
    mutation_rate: float,
    elite_count: int,
    seed: int,
    progress_cb: ProgressCallback | None = None,
) -> SearchInRoundsResult:
    """Repeat search rounds until the central planet's warehouses hold empire production without overshoot slack."""
    settings = SearchSettings(
        grid_size=grid_size,
        priority_steps=priority_steps,
        step_sizes=tuple(int(s) for s in step_sizes),
        population_size=population_size,
        generations=generations,
        tournament_size=tournament_size,
        crossover_rate=crossover_rate,
        mutation_rate=mutation_rate,
        elite_count=elite_count,
        seed=seed,
    )
    reserve: FloatArray = np.zeros(len(RESOURCE_KEYS), dtype=np.float64)
    rounds: list[RoundRow] = []
    max_rounds = 12
    cp = model.central_planet

    for round_idx in range(max_rounds):
        round_model = model.with_central_reserve(reserve)
        r_start = min(0.85, 0.10 + 0.35 * round_idx)
        r_end = min(0.95, r_start + 0.35)
        result = _run_one_search(round_model, settings, progress_cb, (r_start, r_end))
        best_levels = result[-1]
        needed, warehouses, held, holds_the_production = _evaluate_round_reserve(round_model, best_levels)
        rounds.append(
            _build_round_row(
                len(rounds) + 1,
                float(round_model.ships_per_month(best_levels[None])[0]),
                reserve,
                needed,
                held,
                holds_the_production=holds_the_production,
            )
        )
        if holds_the_production or round_idx + 1 >= max_rounds:
            final_model = model.with_central_reserve(needed)
            if cp >= 0 and holds_the_production and round_idx >= 1:
                wh_reserved = warehouses[cp].copy()
                wh_needed = np.asarray(
                    final_model.simulate(best_levels[None])["planet_warehouse_levels"],
                    dtype=np.int64,
                )[0, cp]
                if bool((wh_needed < wh_reserved).any()):
                    best_model, best_result = _refine_overshot_central_reserve(
                        model,
                        final_model,
                        result,
                        wh_reserved,
                        wh_needed,
                        rounds,
                        max_rounds=max_rounds,
                        settings=settings,
                        run_search=_run_one_search,
                        progress_cb=progress_cb,
                    )
                    return best_model, rounds, *best_result
            return final_model, rounds, *result
        next_reserve = np.maximum(reserve, needed)
        if bool((next_reserve <= reserve).all()):
            return model.with_central_reserve(needed), rounds, *result
        reserve = next_reserve
    raise RuntimeError("search_in_rounds terminated unexpectedly")  # pragma: no cover


__all__ = [
    "GAHistoryRow",
    "RoundRow",
    "SearchInRoundsResult",
    "SearchRoundResult",
    "_finalize_self_consistent_round",
    "_polish_holding_central_reserve",
    "central_reserve_needed",
    "polish_both_ways",
    "polish_configuration",
    "run_genetic_algorithm",
    "search_best_configuration",
    "search_in_rounds",
]
