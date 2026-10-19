"""8-gene Genetic Algorithm tuning and warm-start gene parsing for ``PathStrategy``."""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
import math

import numpy as np
import numpy.typing as npt

from spaceinvasion.core.coercion import coerce_finite_float
from spaceinvasion.planner.ai._types import GENE_NAMES, PATH_GENE_RANGES, VALUE_GENE_NAMES, WARM_GENE_DEFAULTS
from spaceinvasion.planner.ai.path_strategy import PathStrategy
from spaceinvasion.planner.ai.simulator import EmpirePathSimulator


def _raw_float(value: object) -> float:
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return float(value)
    return float(str(value))


def strategy_from_genes(
    name: str, genes: Sequence[float] | npt.NDArray[np.float64], revalue_every_steps: int
) -> PathStrategy:
    """Build a scarcity-valued ``PathStrategy`` from an 8-gene real vector."""
    focus, critical_share, patience, speed_weight, *value_genes = (float(gene) for gene in genes)
    return PathStrategy(
        name,
        rule="payback",
        values="scarcity",
        focus=focus,
        critical_share=critical_share,
        patience=patience,
        speed_weight=speed_weight,
        bias=tuple(2.0**gene for gene in value_genes),
        revalue_every_steps=revalue_every_steps,
    )


def path_fitness_key(run: Mapping[str, object]) -> tuple[float, float]:
    """Return GA comparison tuple ``(discounted_reward, -hours)`` preserving ``inf``."""
    return (_raw_float(run["discounted_reward"]), -_raw_float(run["hours"]))


def tune_path_strategy(
    simulator: EmpirePathSimulator,
    start_genes: Sequence[float] | npt.NDArray[np.float64],
    *,
    population_size: int,
    generations: int,
    tournament_size: int,
    crossover_rate: float,
    mutation_rate: float,
    elite_count: int,
    random_seed: int,
    revalue_every_steps: int,
    progress_cb: Callable[[str, float], None] | None = None,
    progress_range: tuple[float, float] = (0.25, 0.90),
) -> tuple[npt.NDArray[np.float64], dict[str, object], list[dict[str, object]]]:
    """8-gene real-coded GA maximizing W-week net ``discounted_reward``."""
    rng = np.random.default_rng(int(random_seed))
    low = np.array([bounds[0] for bounds in PATH_GENE_RANGES.values()], dtype=np.float64)
    high = np.array([bounds[1] for bounds in PATH_GENE_RANGES.values()], dtype=np.float64)
    size = max(2, int(population_size))
    elites = min(max(1, int(elite_count)), size - 1)
    results: dict[tuple[float, ...], dict[str, object]] = {}

    def simulate(genes: npt.NDArray[np.float64]) -> dict[str, object]:
        token = tuple(round(float(gene), 9) for gene in genes)
        if token not in results:
            results[token] = simulator.run(strategy_from_genes("tuned", genes, revalue_every_steps))
        return results[token]

    def pick_parent(population: npt.NDArray[np.float64], keys: list[tuple[float, float]]) -> npt.NDArray[np.float64]:
        contenders = rng.integers(0, size, size=max(1, int(tournament_size)))
        winner = max((int(idx) for idx in contenders), key=lambda idx: keys[idx])
        row: npt.NDArray[np.float64] = population[winner]
        return row

    first = np.clip(np.asarray(start_genes, dtype=np.float64), low, high)
    population = np.vstack([first, rng.uniform(low, high, (size - 1, len(low)))])
    history: list[dict[str, object]] = []
    best_genes: npt.NDArray[np.float64] = population[0].copy()
    best_result = simulate(population[0])
    p0, p1 = progress_range
    total_gens = max(1, int(generations))

    for generation in range(int(generations) + 1):
        runs = [simulate(genes) for genes in population]
        keys = [path_fitness_key(run) for run in runs]
        order = sorted(range(size), key=lambda idx: keys[idx], reverse=True)
        if keys[order[0]] > path_fitness_key(best_result):
            best_genes, best_result = population[order[0]].copy(), runs[order[0]]
        rewards = np.array([_raw_float(run["discounted_reward"]) for run in runs], dtype=np.float64)
        stuck = sum(1 for run in runs if run["status"] == "stuck")
        history.append(
            {
                "generation": generation,
                "best_reward": float(rewards[order[0]]),
                "mean_reward": float(rewards.mean()),
                "paths_that_got_stuck": int(stuck),
                "best_so_far_reward": _raw_float(best_result["discounted_reward"]),
                "simulations_so_far": len(results),
            }
        )
        if progress_cb is not None:
            progress_cb("tuning_ga", p0 + (p1 - p0) * (generation / float(total_gens)))
        if generation == int(generations):
            break
        children = [population[index].copy() for index in order[:elites]]
        while len(children) < size:
            mother, father = pick_parent(population, keys), pick_parent(population, keys)
            child = mother.copy()
            if rng.random() < crossover_rate:
                share = rng.random(len(low))
                child = share * mother + (1.0 - share) * father
            moves = rng.random(len(low)) < mutation_rate
            child = np.clip(child + moves * rng.normal(0.0, 0.15, len(low)) * (high - low), low, high)
            children.append(child)
        population = np.vstack(children)
    return best_genes, best_result, history


def _parse_bias_sequence(raw_bias: object) -> list[float] | None:
    if (
        not isinstance(raw_bias, (Sequence, np.ndarray))
        or isinstance(raw_bias, (str, bytes, bytearray))
        or len(raw_bias) != 4
    ):
        return None
    parsed_bias: list[float] = []
    for item in raw_bias:
        if isinstance(item, bool):
            return None
        fv = coerce_finite_float(item, float("nan"))
        if not math.isfinite(fv) or fv <= 0.0:
            return None
        parsed_bias.append(fv)
    return parsed_bias if len(parsed_bias) == 4 else None


def _parse_warm_start_genes(raw: object) -> tuple[float, ...] | None:
    """Parse optional ``warm_start_genes`` (gene dict, ``best_parameters`` dict, or 8-float sequence) clamped to ``PATH_GENE_RANGES``."""
    if raw is None:
        return None
    bounds = [PATH_GENE_RANGES[name] for name in GENE_NAMES]

    def _clamp(idx: int, val: float) -> float:
        lo, hi = bounds[idx]
        return min(hi, max(lo, float(val)))

    if isinstance(raw, Mapping):
        if str(raw.get("rule") or "").strip().lower() == "cheapest":
            return None
        recognized = set(GENE_NAMES) | {"bias"}
        if not any(k in raw for k in recognized):
            return None
        bias_seq = _parse_bias_sequence(raw.get("bias"))
        vals: list[float] = []
        for idx, gname in enumerate(GENE_NAMES):
            raw_v = raw.get(gname)
            if raw_v is not None and not isinstance(raw_v, bool):
                fv = coerce_finite_float(raw_v, float("nan"))
                if not math.isfinite(fv):
                    return None
                vals.append(_clamp(idx, fv))
            elif gname in VALUE_GENE_NAMES and bias_seq is not None:
                v_pos = VALUE_GENE_NAMES.index(gname)
                vals.append(_clamp(idx, math.log2(max(1e-6, bias_seq[v_pos]))))
            else:
                vals.append(_clamp(idx, WARM_GENE_DEFAULTS[gname]))
        return tuple(vals)

    if isinstance(raw, (Sequence, np.ndarray)) and not isinstance(raw, (str, bytes, bytearray)):
        if len(raw) != len(GENE_NAMES):
            return None
        seq_vals: list[float] = []
        for idx, item in enumerate(raw):
            if isinstance(item, bool):
                return None
            fv = coerce_finite_float(item, float("nan"))
            if not math.isfinite(fv):
                return None
            seq_vals.append(_clamp(idx, fv))
        return tuple(seq_vals)

    return None
