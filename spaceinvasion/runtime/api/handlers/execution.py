"""Registration entrypoint for all 20 browser execution API commands."""

from __future__ import annotations

from spaceinvasion.runtime.api.handlers._browser_proto import BrowserBridgeFacade
from spaceinvasion.runtime.api.handlers._exec_build import register_build_handlers
from spaceinvasion.runtime.api.handlers._exec_economy import register_economy_handlers
from spaceinvasion.runtime.api.handlers._exec_fleet import register_fleet_core_handlers
from spaceinvasion.runtime.api.handlers._exec_fleet_attack import register_fleet_combat_handlers
from spaceinvasion.runtime.api.handlers._exec_fleet_logistics import register_fleet_logistics_handlers
from spaceinvasion.runtime.api.handlers._exec_nav import register_nav_handlers
from spaceinvasion.runtime.api.registry import CommandRegistry


def register_execution_handlers(registry: CommandRegistry[BrowserBridgeFacade]) -> None:
    """Register all 20 browser execution commands onto `registry`."""
    register_nav_handlers(registry)
    register_build_handlers(registry)
    register_economy_handlers(registry)
    register_fleet_core_handlers(registry)
    register_fleet_logistics_handlers(registry)
    register_fleet_combat_handlers(registry)
