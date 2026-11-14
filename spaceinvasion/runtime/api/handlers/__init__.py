"""Phase 4.1 declarative `@command` handler registry assembling all 60 kept API commands."""

from __future__ import annotations

from spaceinvasion.runtime.api.handlers._bridge_proto import BridgeApiFacade
from spaceinvasion.runtime.api.handlers._browser_proto import BrowserBridgeFacade
from spaceinvasion.runtime.api.handlers.bot_control import register_bot_control_handlers
from spaceinvasion.runtime.api.handlers.colonies import register_colony_handlers
from spaceinvasion.runtime.api.handlers.config import register_config_handlers
from spaceinvasion.runtime.api.handlers.diagnostics import register_diagnostic_handlers
from spaceinvasion.runtime.api.handlers.execution import register_execution_handlers
from spaceinvasion.runtime.api.handlers.galaxy_intel import register_galaxy_intel_handlers
from spaceinvasion.runtime.api.handlers.planners import register_planner_handlers
from spaceinvasion.runtime.api.handlers.planning import register_planning_handlers
from spaceinvasion.runtime.api.handlers.queue import register_queue_handlers
from spaceinvasion.runtime.api.registry import CommandRegistry


def build_command_registry() -> CommandRegistry[BrowserBridgeFacade]:
    """Create and populate a `CommandRegistry` with all 60 kept SpaceInvasion API commands."""
    db_registry: CommandRegistry[BridgeApiFacade] = CommandRegistry()
    register_bot_control_handlers(db_registry)
    register_config_handlers(db_registry)
    register_queue_handlers(db_registry)
    register_galaxy_intel_handlers(db_registry)
    register_colony_handlers(db_registry)
    register_planner_handlers(db_registry)
    register_diagnostic_handlers(db_registry)

    registry: CommandRegistry[BrowserBridgeFacade] = CommandRegistry()
    for spec in db_registry.specs():
        registry.register(spec)  # type: ignore[arg-type]
    register_planning_handlers(registry)
    register_execution_handlers(registry)
    return registry


__all__ = [
    "BridgeApiFacade",
    "BrowserBridgeFacade",
    "build_command_registry",
    "register_bot_control_handlers",
    "register_colony_handlers",
    "register_config_handlers",
    "register_diagnostic_handlers",
    "register_execution_handlers",
    "register_galaxy_intel_handlers",
    "register_planner_handlers",
    "register_planning_handlers",
    "register_queue_handlers",
]
