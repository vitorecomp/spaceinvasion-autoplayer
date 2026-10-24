"""Pure-Python technology prerequisite DAG and upgrade-cost helpers."""

from __future__ import annotations

from collections import deque
from collections.abc import Iterable, Mapping

from spaceinvasion.combat.fleet import DEFENSE_SLUG_BY_KEY, SHIP_SLUG_BY_KEY
from spaceinvasion.core import game_data
from spaceinvasion.core.coercion import coerce_finite_float, coerce_int
from spaceinvasion.planner.config_optimizer.catalog_math import (
    build_monotonic_base_times,
    building_cost_map,
    building_info,
    full_cost,
    research_catalog,
    research_info,
    ship_info,
    spice_eq,
    unit_keys,
)
from spaceinvasion.planner.config_optimizer.constants import RESOURCE_KEYS


def _defense_info(key: str) -> dict[str, object]:
    raw: object = game_data.DEFENSES[DEFENSE_SLUG_BY_KEY[key]]
    return dict(raw) if isinstance(raw, Mapping) else {}


def _extract_levels_map(info: Mapping[str, object]) -> dict[int, dict[str, object]]:
    raw = info.get("levels")
    if not isinstance(raw, Mapping):
        return {}
    return {int(k): dict(v) for k, v in raw.items() if isinstance(v, Mapping)}


def _extract_reqs_list(info: Mapping[str, object]) -> list[dict[str, object]]:
    raw = info.get("requirements")
    if not isinstance(raw, list):
        return []
    return [dict(item) for item in raw if isinstance(item, Mapping)]


class TechnologyRequirementGraph:
    """In-memory DAG of SpaceInvasion prerequisites across buildings, research, ships, and defenses."""

    def __init__(self) -> None:
        self.nodes: dict[str, dict[str, object]] = {}
        self._requirements: dict[str, list[dict[str, object]]] = {}
        self._base_times: dict[str, dict[int, float]] = {}
        for building_id in sorted(game_data.BUILDING_BY_ID):
            self._add_node(f"b{int(building_id)}", "buildings", building_info(int(building_id)))
        for key, info in research_catalog():
            self._add_node(key, "research", info)
        for key in unit_keys(SHIP_SLUG_BY_KEY):
            self._add_node(key, "ships", ship_info(key))
        for key in unit_keys(DEFENSE_SLUG_BY_KEY):
            self._add_node(key, "defense", _defense_info(key))

        self.prereqs_of, self.unlocks_of = self._link_requirements()
        self.topological_order, self.topological_layer = self._sort_topologically()
        self._topological_rank = {key: rank for rank, key in enumerate(self.topological_order)}
        self.ancestors_of, self.descendants_of = self._close_transitively()

    def _add_node(self, key: str, category: str, info: Mapping[str, object]) -> None:
        levels = _extract_levels_map(info)
        max_level = coerce_int(info.get("max_table_level"), len(levels)) or len(levels)
        self.nodes[key] = {
            "key": key,
            "category": category,
            "entity_id": int(key[1:]),
            "slug": str(info["slug"]),
            "name": str(info["name"]),
            "max_level": max_level,
        }
        self._requirements[key] = _extract_reqs_list(info)
        self._base_times[key] = build_monotonic_base_times(levels, max_level)

    def _link_requirements(
        self,
    ) -> tuple[dict[str, tuple[tuple[str, int], ...]], dict[str, tuple[tuple[str, int], ...]]]:
        key_by_slug = {(str(node["category"]), str(node["slug"])): key for key, node in self.nodes.items()}
        prereqs: dict[str, list[tuple[str, int]]] = {key: [] for key in self.nodes}
        unlocks: dict[str, list[tuple[str, int]]] = {key: [] for key in self.nodes}
        for target, requirements in self._requirements.items():
            for requirement in requirements:
                source = key_by_slug.get((str(requirement["category"]), str(requirement["slug"])))
                if source is None:
                    raise KeyError(f"Unresolved tech-tree requirement {requirement!r} on {target}")
                level = coerce_int(requirement["level"], 0)
                prereqs[target].append((source, level))
                unlocks[source].append((target, level))
        return (
            {key: tuple(edges) for key, edges in prereqs.items()},
            {key: tuple(edges) for key, edges in unlocks.items()},
        )

    def _sort_topologically(self) -> tuple[tuple[str, ...], dict[str, int]]:
        open_prereqs = {key: len(self.prereqs_of[key]) for key in self.nodes}
        layer = {key: 0 for key in self.nodes}
        queue: deque[str] = deque(key for key, count in open_prereqs.items() if count == 0)
        order: list[str] = []
        while queue:
            node = queue.popleft()
            order.append(node)
            for dependent, _level in self.unlocks_of[node]:
                layer[dependent] = max(layer[dependent], layer[node] + 1)
                open_prereqs[dependent] -= 1
                if open_prereqs[dependent] == 0:
                    queue.append(dependent)
        if len(order) != len(self.nodes):
            raise ValueError(f"Cycle detected in technology requirement graph: {sorted(set(self.nodes) - set(order))}")
        return tuple(order), layer

    def _close_transitively(self) -> tuple[dict[str, frozenset[str]], dict[str, frozenset[str]]]:
        ancestors: dict[str, set[str]] = {key: set() for key in self.nodes}
        for node in self.topological_order:
            for prerequisite, _level in self.prereqs_of[node]:
                ancestors[node].add(prerequisite)
                ancestors[node].update(ancestors[prerequisite])
        descendants: dict[str, set[str]] = {key: set() for key in self.nodes}
        for node in reversed(self.topological_order):
            for dependent, _level in self.unlocks_of[node]:
                descendants[node].add(dependent)
                descendants[node].update(descendants[dependent])
        return (
            {key: frozenset(found) for key, found in ancestors.items()},
            {key: frozenset(found) for key, found in descendants.items()},
        )

    def transitive_requirements(
        self,
        target_key: str,
        *,
        include_target_level: int = 0,
    ) -> dict[str, int]:
        """Return all transitive prerequisites needed for ``target_key`` in topological order."""
        if target_key not in self.nodes:
            raise KeyError(f"Unknown node key: {target_key!r}")
        required: dict[str, int] = {}
        if include_target_level > 0 and self.nodes[target_key]["category"] in ("buildings", "research"):
            required[target_key] = int(include_target_level)
        for source, level in self.prereqs_of[target_key]:
            required[source] = max(required.get(source, 0), int(level))
        for node in reversed(self.topological_order):
            if node not in required:
                continue
            for source, level in self.prereqs_of[node]:
                if level > required.get(source, 0):
                    required[source] = int(level)
        return {key: required[key] for key in sorted(required, key=self._topological_rank.__getitem__)}


TECH_GRAPH: TechnologyRequirementGraph = TechnologyRequirementGraph()


def same_planet_building_requirements(key: str, level: int = 0) -> dict[str, int]:
    """Building levels a planet needs before it can hold ``key``, following building prereqs only."""
    required: dict[str, int] = {}
    if level > 0 and TECH_GRAPH.nodes[key]["category"] == "buildings":
        required[key] = int(level)
    pending = [key]
    while pending:
        for source, source_level in TECH_GRAPH.prereqs_of[pending.pop()]:
            if TECH_GRAPH.nodes[source]["category"] == "buildings" and source_level > required.get(source, 0):
                required[source] = int(source_level)
                pending.append(source)
    return required


def research_requirements(keys: Iterable[str]) -> dict[str, int]:
    """Highest research level that any of ``keys`` needs, directly or indirectly."""
    required: dict[str, int] = {}
    for key in keys:
        for node, level in TECH_GRAPH.transitive_requirements(key).items():
            if TECH_GRAPH.nodes[node]["category"] == "research":
                required[node] = max(required.get(node, 0), int(level))
    return required


def largest_costs(
    costs: Iterable[tuple[str, Mapping[str, object]]],
) -> tuple[dict[str, float], dict[str, str]]:
    """Per resource, the largest amount requested by any labelled cost and its label."""
    amounts = {resource: 0.0 for resource in RESOURCE_KEYS}
    labels = {resource: "" for resource in RESOURCE_KEYS}
    for label, cost in costs:
        for resource in RESOURCE_KEYS:
            amount = coerce_finite_float(cost.get(resource, 0), 0.0)
            if amount > amounts[resource]:
                amounts[resource], labels[resource] = amount, label
    return amounts, labels


def building_upgrade_costs(key: str, from_level: int, to_level: int) -> list[tuple[str, dict[str, int]]]:
    """``(label, cost)`` for every level of building ``key`` from ``from_level + 1`` to ``to_level``."""
    return [
        (f"{key} L{level}", building_cost_map(int(key[1:]), level))
        for level in range(int(from_level) + 1, int(to_level) + 1)
    ]


def research_upgrade_costs(
    own_levels: Mapping[str, int],
    planned_levels: Mapping[str, int],
) -> list[tuple[str, dict[str, object]]]:
    """``(label, cost)`` for every research level between current and planned levels."""
    out: list[tuple[str, dict[str, object]]] = []
    for key, planned_level in planned_levels.items():
        levels = _extract_levels_map(research_info(key))
        start = max(0, coerce_int(own_levels.get(key, 0), 0)) + 1
        for level in range(start, int(planned_level) + 1):
            raw_cost = (levels.get(level) or {}).get("cost")
            cost_dict = dict(raw_cost) if isinstance(raw_cost, Mapping) else {}
            out.append((f"{key} L{level}", cost_dict))
    return out


def building_upgrade_cost_spice_eq(key: str, from_level: int, to_level: int) -> float:
    """SpiceEq cost of raising building ``key`` from ``from_level`` to ``to_level``."""
    return float(
        sum(spice_eq(building_cost_map(int(key[1:]), level)) for level in range(int(from_level) + 1, int(to_level) + 1))
    )


def research_upgrade_cost_spice_eq(key: str, from_level: int, to_level: int) -> float:
    """SpiceEq cost of raising research ``key`` from ``from_level`` to ``to_level``."""
    levels = _extract_levels_map(research_info(key))
    return float(
        sum(
            spice_eq(full_cost((levels.get(level) or {}).get("cost")))
            for level in range(int(from_level) + 1, int(to_level) + 1)
        )
    )
