"""Design catalogue construction, hill-climb moves, and Pareto scoring for ``config_opt``."""

from __future__ import annotations

from collections.abc import Callable

import numpy as np

from backend.planner.config_optimizer.constants import FloatArray, IntArray
from backend.planner.config_optimizer.model import EmpireProductionModel

ScoreFn = Callable[[FloatArray], FloatArray]
ProgressCallback = Callable[[str, float], None]


def balanced_score(limits: FloatArray, sharpness: float = 8.0) -> FloatArray:
    """Power-mean smooth stand-in for the smallest limit of each row."""
    floor: FloatArray = np.maximum(limits.min(axis=1, keepdims=True), 1e-9)
    ratio_pow: FloatArray = np.asarray((np.maximum(limits, 1e-9) / floor) ** -sharpness, dtype=np.float64)
    return np.asarray(floor[:, 0] * np.mean(ratio_pow, axis=1) ** (-1.0 / sharpness), dtype=np.float64)


def smallest_limit(limits: FloatArray) -> FloatArray:
    """Smallest limit of each row: target ships per month."""
    return np.asarray(limits.min(axis=1), dtype=np.float64)


def planet_moves(buildings: int, step_sizes: tuple[int, ...]) -> IntArray:
    """Level-change move set tried during hill-climb."""
    moves: set[tuple[int, ...]] = set()
    for size in step_sizes:
        for first in range(buildings):
            for first_step in (size, -size):
                moves.add(tuple(first_step if index == first else 0 for index in range(buildings)))
                for second in range(first + 1, buildings):
                    for second_step in (size, -size):
                        moves.add(
                            tuple(
                                first_step if index == first else second_step if index == second else 0
                                for index in range(buildings)
                            )
                        )
            for second in range(first + 1, buildings):
                for third in range(buildings):
                    if third not in (first, second):
                        moves.add(
                            tuple(
                                size if index in (first, second) else -2 * size if index == third else 0
                                for index in range(buildings)
                            )
                        )
    return np.array(sorted(moves), dtype=np.int64)


def improve_design(
    model: EmpireProductionModel,
    planet: int,
    design: IntArray,
    score: ScoreFn,
    moves: IntArray,
) -> IntArray:
    """NaN-safe hill-climb on one planet's design until no feasible neighbour improves rank."""
    current: IntArray = np.asarray(design, dtype=np.int64)
    built_vec = model.built_opt_levels[planet]
    sim0_limits = np.asarray(model.simulate(current[None, None, :], [planet])["planet_limits"], dtype=np.float64)
    current_score = float(score(sim0_limits[:, 0])[0])
    current_rank = (current_score, -int(np.abs(current - built_vec).sum()), -int(current.sum()))
    while True:
        candidates: IntArray = np.asarray(current + moves, dtype=np.int64)
        candidates = candidates[model.in_range(candidates[:, None, :], [planet])]
        if len(candidates) == 0:
            return current
        outcome = model.simulate(candidates[:, None, :], [planet])
        fits = np.asarray(outcome["planet_fits"], dtype=np.bool_)[:, 0]
        if not bool(fits.any()):
            return current
        candidates = candidates[fits]
        fit_limits = np.asarray(outcome["planet_limits"], dtype=np.float64)[fits, 0]
        scores: FloatArray = np.asarray(score(fit_limits), dtype=np.float64)
        finite_mask = np.isfinite(scores)
        if not bool(finite_mask.any()):
            return current
        if not bool(finite_mask.all()):
            candidates = candidates[finite_mask]
            scores = scores[finite_mask]
        change_totals = np.abs(candidates - built_vec).sum(axis=1)
        level_totals = candidates.sum(axis=1)
        best = int(np.lexsort((-level_totals, -change_totals, scores))[-1])
        best_rank = (float(scores[best]), -int(change_totals[best]), -int(level_totals[best]))
        if not best_rank > current_rank:
            return current
        current, current_rank = candidates[best], best_rank


def _shipyard_prereq_seeds(
    model: EmpireProductionModel,
    planet: int,
    lower: IntArray,
    upper: IntArray,
) -> tuple[IntArray, FloatArray] | None:
    """Build feasible shipyard prerequisite seed rows when tight field slack skipped them in the coarse grid."""
    seed = lower.copy()
    for key, req_lvl in model.target_requirements.items():
        if key in model.building_index:
            b_idx = model.building_index[key]
            if int(upper[b_idx]) < int(req_lvl):
                return None
            seed[b_idx] = max(int(seed[b_idx]), int(req_lvl))
        elif int(model.fixed_levels.get(key, np.zeros(len(model.free_fields), dtype=np.int64))[planet]) < int(req_lvl):
            return None

    extra_rows: list[IntArray] = [seed]
    if "b17" in model.building_index:
        b17_idx = model.building_index["b17"]
        max_slack = max(0, int(model.free_fields[planet]) - int(seed.sum()))
        b17_hi = min(int(upper[b17_idx]), int(seed[b17_idx]) + max_slack)
        for b17_lvl in range(int(seed[b17_idx]) + 1, b17_hi + 1):
            cand = seed.copy()
            cand[b17_idx] = b17_lvl
            extra_rows.append(cand)
    extra_arr: IntArray = np.array(extra_rows, dtype=np.int64)
    extra_out = model.simulate(extra_arr[:, None, :], [planet])
    extra_fits = np.asarray(extra_out["planet_fits"], dtype=np.bool_)[:, 0]
    if not bool(extra_fits.any()):
        return None
    extra_limits = np.asarray(extra_out["planet_limits"], dtype=np.float64)[extra_fits, 0]
    return extra_arr[extra_fits], extra_limits


def coarse_designs(model: EmpireProductionModel, planet: int, grid_size: int) -> tuple[IntArray, FloatArray]:
    """Coarse grid of at most ~``grid_size`` feasible designs on one planet and their limits."""
    lower, upper = model.min_levels[planet], model.max_levels[planet]
    active_dims = max(1, int((upper > lower).sum()))
    values_per_building = max(2, int(grid_size ** (1.0 / active_dims)))
    axes = [
        np.unique(np.linspace(low, high, values_per_building).round().astype(np.int64))
        for low, high in zip(lower, upper, strict=True)
    ]
    grid: IntArray = np.asarray(
        np.stack(np.meshgrid(*axes, indexing="ij"), axis=-1).reshape(-1, len(lower)),
        dtype=np.int64,
    )
    outcome = model.simulate(grid[:, None, :], [planet])
    fits = np.asarray(outcome["planet_fits"], dtype=np.bool_)[:, 0]
    feas_grid: IntArray = np.asarray(grid[fits], dtype=np.int64)
    feas_limits: FloatArray = np.asarray(outcome["planet_limits"], dtype=np.float64)[fits, 0]

    # On locked/constrained planets where `active_dims < len(lower)` (or empty feasible grid),
    # ensure feasible shipyard prerequisite seeds (`b9 >= req_lvl`) are included when tight field
    # slack caused `[low..high]` spacing to jump over the narrow feasible shipyard window.
    if len(feas_limits) == 0 or (active_dims < len(lower) and float(feas_limits[:, -1].max()) <= 0.0):
        extra = _shipyard_prereq_seeds(model, planet, lower, upper)
        if extra is not None:
            feas_grid = np.concatenate([feas_grid, extra[0]], axis=0)
            feas_limits = np.concatenate([feas_limits, extra[1]], axis=0)
    return feas_grid, feas_limits


def resource_peaks(
    model: EmpireProductionModel,
    planet: int,
    grid: IntArray,
    grid_limits: FloatArray,
    moves: IntArray,
) -> FloatArray:
    """Maximum contribution of one planet to each resource limit when specialized."""
    peaks: list[float] = []
    for column in range(len(model.cost_resources)):

        def this_limit(limits: FloatArray, column: int = column) -> FloatArray:
            return np.asarray(limits[:, column], dtype=np.float64)

        design = improve_design(model, planet, grid[int(grid_limits[:, column].argmax())], this_limit, moves)
        sim_limits = np.asarray(model.simulate(design[None, None, :], [planet])["planet_limits"], dtype=np.float64)
        peaks.append(float(sim_limits[0, 0, column]))
    return np.array(peaks, dtype=np.float64)


def planet_design_catalogue(
    model: EmpireProductionModel,
    planet: int,
    grid: IntArray,
    grid_limits: FloatArray,
    moves: IntArray,
    *,
    priority_steps: int,
    shipyard_ceiling: float,
) -> IntArray:
    """Candidate Pareto-front designs of one planet across priority mixes + balanced design."""
    ceiling: FloatArray = np.full(grid_limits.shape[1], np.inf, dtype=np.float64)
    ceiling[-1] = shipyard_ceiling
    scale: FloatArray = np.maximum(np.minimum(grid_limits, ceiling).max(axis=0), 1e-12)

    shares = np.stack(np.meshgrid(*[np.arange(priority_steps + 1)] * len(scale), indexing="ij"), axis=-1)
    shares = shares.reshape(-1, len(scale))
    designs: list[IntArray] = []
    priority_rows: FloatArray = np.asarray(
        shares[shares.sum(axis=1) == priority_steps] / float(priority_steps),
        dtype=np.float64,
    )
    for priorities in priority_rows:

        def priority_score(limits: FloatArray, priorities: FloatArray = priorities) -> FloatArray:
            return np.asarray((np.minimum(limits, ceiling) / scale) @ priorities, dtype=np.float64)

        start = grid[int(priority_score(grid_limits).argmax())]
        designs.append(improve_design(model, planet, start, priority_score, moves))
    start = grid[int(balanced_score(grid_limits).argmax())]
    designs.append(improve_design(model, planet, start, balanced_score, moves))
    return np.asarray(np.unique(np.array(designs, dtype=np.int64), axis=0), dtype=np.int64)


def build_design_catalogues(
    model: EmpireProductionModel,
    *,
    grid_size: int,
    priority_steps: int,
    step_sizes: tuple[int, ...],
    progress_cb: ProgressCallback | None = None,
    progress_range: tuple[float, float] = (0.1, 0.5),
) -> list[IntArray]:
    """Build one design catalogue per planet, deduplicating identical twin planets."""
    moves = planet_moves(len(model.optimized_keys), step_sizes)
    unique_planets = [planet for planet, twin in enumerate(model.twin_planets) if twin == planet]
    grids = {planet: coarse_designs(model, planet, grid_size) for planet in unique_planets}
    peaks = {planet: resource_peaks(model, planet, *grid, moves) for planet, grid in grids.items()}
    shipyard_ceiling = float(np.sum([peaks[twin] for twin in model.twin_planets], axis=0).min())
    catalogues: dict[int, IntArray] = {}
    p0, p1 = progress_range
    total_unique = max(1, len(unique_planets))
    for idx, planet in enumerate(unique_planets):
        catalogues[planet] = planet_design_catalogue(
            model,
            planet,
            *grids[planet],
            moves,
            priority_steps=priority_steps,
            shipyard_ceiling=shipyard_ceiling,
        )
        if progress_cb is not None:
            progress_cb("building_catalogues", p0 + (p1 - p0) * (idx + 1) / total_unique)
    return [catalogues[twin] for twin in model.twin_planets]


def catalogue_limits(model: EmpireProductionModel, catalogues: list[IntArray]) -> tuple[FloatArray, IntArray]:
    """``design_limits[planet, design, limit]`` of every catalogue design and count per planet."""
    design_counts: IntArray = np.array([len(catalogue) for catalogue in catalogues], dtype=np.int64)
    design_limits: FloatArray = np.zeros(
        (len(catalogues), int(design_counts.max()), len(model.limit_names)),
        dtype=np.float64,
    )
    for planet, catalogue in enumerate(catalogues):
        limits = np.asarray(model.simulate(catalogue[:, None, :], [planet])["planet_limits"], dtype=np.float64)[:, 0]
        design_limits[planet, : len(catalogue)] = limits
    return design_limits, design_counts


__all__ = [
    "ProgressCallback",
    "ScoreFn",
    "balanced_score",
    "build_design_catalogues",
    "catalogue_limits",
    "coarse_designs",
    "improve_design",
    "planet_design_catalogue",
    "planet_moves",
    "resource_peaks",
    "smallest_limit",
]
