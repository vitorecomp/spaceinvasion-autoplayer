"""Phase 4 modular bridge collaborators and lock-free snapshot publication package."""

from __future__ import annotations

from spaceinvasion.runtime.bridge._context import BridgeContext
from spaceinvasion.runtime.bridge._helpers import (
    ALLOWED_MENU_ACTIONS,
    MAX_REQUEST_BYTES,
    UI_DIR,
    GalaxyScanInProgressError,
    ensure_debug_browser_ready,
    resolve_ui_dir,
)
from spaceinvasion.runtime.bridge._helpers_sig import compute_ui_assets_state
from spaceinvasion.runtime.bridge.bridge import GameApiBridge, ModularGameApiBridge
from spaceinvasion.runtime.bridge.execution import ActionExecutionService
from spaceinvasion.runtime.bridge.galaxy_sweep import GalaxySweepService
from spaceinvasion.runtime.bridge.intel_sync import IntelSyncService
from spaceinvasion.runtime.bridge.ledgers import LedgerTelemetryService
from spaceinvasion.runtime.bridge.lifecycle import BridgeLifecycleService
from spaceinvasion.runtime.bridge.plan_assembly import PlanAssemblyService
from spaceinvasion.runtime.bridge.planet_names import PlanetNameService
from spaceinvasion.runtime.bridge.planner_jobs import PlannerJobService
from spaceinvasion.runtime.bridge.snapshot import (
    PublishedBridgeSnapshot,
    SnapshotPublisher,
    build_dashboard_delta_response,
    compute_section_version,
)
from spaceinvasion.runtime.bridge.state_cache import StateCacheService
from spaceinvasion.runtime.bridge.views import DashboardViewService

__all__ = [
    "ALLOWED_MENU_ACTIONS",
    "ActionExecutionService",
    "BridgeContext",
    "BridgeLifecycleService",
    "DashboardViewService",
    "GalaxyScanInProgressError",
    "GalaxySweepService",
    "GameApiBridge",
    "IntelSyncService",
    "LedgerTelemetryService",
    "MAX_REQUEST_BYTES",
    "ModularGameApiBridge",
    "PlanAssemblyService",
    "PlanetNameService",
    "PlannerJobService",
    "PublishedBridgeSnapshot",
    "SnapshotPublisher",
    "StateCacheService",
    "UI_DIR",
    "build_dashboard_delta_response",
    "compute_section_version",
    "compute_ui_assets_state",
    "ensure_debug_browser_ready",
    "resolve_ui_dir",
]
