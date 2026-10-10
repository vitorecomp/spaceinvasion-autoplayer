"""Shared tutorial milestone task matchers used by tutorial, shipyard, and fleet modules."""

from __future__ import annotations

from spaceinvasion.core.state import GameState, TutorialTask
from spaceinvasion.planner.heuristics._common import _safe_strategy_str


def _match_milestone_task(state: GameState, keywords: tuple[str, ...]) -> TutorialTask | None:
    if state.tutorial is None or state.tutorial.can_complete:
        return None
    for task in state.tutorial.tasks:
        if task.completed or task.category != "milestone":
            continue
        desc = _safe_strategy_str(task.description).lower()
        if any(word in desc for word in keywords):
            return task
    return None


def _tutorial_fight_task(state: GameState) -> TutorialTask | None:
    """Return active incomplete tutorial combat milestone task, if any."""
    return _match_milestone_task(state, ("fight", "attack", "combat", "battle"))


def _tutorial_spy_task(state: GameState) -> TutorialTask | None:
    """Return active incomplete tutorial espionage milestone task, if any."""
    return _match_milestone_task(state, ("spy", "espionage", "spionage", "probe"))


def _tutorial_escape_task(state: GameState) -> TutorialTask | None:
    """Return active incomplete tutorial escape-flight milestone task, if any."""
    return _match_milestone_task(state, ("escape flight", "escape", "flucht", "fleetsave"))


def _tutorial_recycle_task(state: GameState) -> TutorialTask | None:
    """Return active incomplete tutorial space-rubble recycling milestone task, if any."""
    return _match_milestone_task(
        state,
        ("recycle", "recycling", "recycler", "debris", "rubble", "trümmer", "truemmer", "abbau", "harvest"),
    )


def _tutorial_colonize_task(state: GameState) -> TutorialTask | None:
    """Return active incomplete tutorial colonization milestone task, if any."""
    return _match_milestone_task(state, ("colonize", "colonisation", "colonization", "kolonisier"))


def _milestone_helper_ship_id(desc_lower: str) -> int | None:
    if any(word in desc_lower for word in ("spy", "espionage", "spionage", "probe")):
        return 10
    if any(
        word in desc_lower
        for word in ("recycle", "recycling", "recycler", "debris", "rubble", "trümmer", "truemmer", "abbau", "harvest")
    ):
        return 11
    if any(word in desc_lower for word in ("colonize", "colony", "colonisation", "colonization")):
        return 8
    return None
