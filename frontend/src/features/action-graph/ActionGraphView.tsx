import React, { useEffect } from "react";
import { safeFiniteNumber } from "../../api/httpClient";
import { useConsole } from "../../context/ConsoleContext";
import type { ActionGraphTabKey } from "../../types/api";
import { AiPlannerTab } from "./AiPlannerTab";
import { ConfigOptimizerTab } from "./ConfigOptimizerTab";
import { FleetPlannerTab } from "./FleetPlannerTab";
import { AG_NUM_DEFAULTS, HeuristicsTab } from "./HeuristicsTab";
import { computeColonyGateInfo, normalizeUiPlannerMode } from "./plannerHelpers";

const VALID_AG_TABS: readonly ActionGraphTabKey[] = [
  "heuristics",
  "config-optimizer",
  "ai-planner",
  "fleet-planner",
];

function normalizeAgTab(raw: unknown): ActionGraphTabKey {
  const s = String(raw || "heuristics").toLowerCase();
  if ((VALID_AG_TABS as readonly string[]).includes(s)) {
    return s as ActionGraphTabKey;
  }
  return "heuristics";
}

export { AG_NUM_DEFAULTS };

export const ActionGraphView: React.FC = () => {
  const {
    activePage,
    dashboard,
    apiOnline,
    plannerJobsStatus,
    refreshPlannerJobsStatus,
    uiPrefs,
    setUiPref,
  } = useConsole();

  const activeTab = normalizeAgTab(uiPrefs?.agActiveTab);
  const switchTab = (next: ActionGraphTabKey) => {
    setUiPref("agActiveTab", next);
  };

  const coJobStatus = String(
    plannerJobsStatus?.config_optimizer_job?.status || "idle"
  ).toLowerCase();
  const aiJobStatus = String(
    plannerJobsStatus?.ai_planner_job?.status || "idle"
  ).toLowerCase();
  const hasRunningJob = coJobStatus === "running" || aiJobStatus === "running";

  useEffect(() => {
    if (activePage !== "action-graph" || !apiOnline) return;
    void refreshPlannerJobsStatus();
    if (!hasRunningJob) return;
    const timer = window.setInterval(() => {
      void refreshPlannerJobsStatus();
    }, 2200);
    return () => window.clearInterval(timer);
  }, [activePage, apiOnline, hasRunningJob, refreshPlannerJobsStatus]);

  const gate = computeColonyGateInfo(dashboard, plannerJobsStatus);
  const configuredMode = normalizeUiPlannerMode(
    dashboard?.config?.planner_mode || dashboard?.planner_mode
  );
  const effectiveMode = normalizeUiPlannerMode(
    dashboard?.effective_planner_mode ||
      plannerJobsStatus?.effective_planner_mode ||
      configuredMode
  );
  const fallbackReason =
    dashboard?.fallback_reason || plannerJobsStatus?.fallback_reason || null;

  const pendingConflicts = Array.isArray(plannerJobsStatus?.pending_field_conflicts)
    ? plannerJobsStatus!.pending_field_conflicts!
    : Array.isArray(dashboard?.pending_field_conflicts)
      ? dashboard!.pending_field_conflicts!
      : [];
  const pendingConflictCount = pendingConflicts.filter(
    (c) => String(c?.status || "pending").toLowerCase() === "pending"
  ).length;

  const coPct = Math.round(
    safeFiniteNumber(plannerJobsStatus?.config_optimizer_job?.progress_pct, 0) ?? 0
  );
  const aiPct = Math.round(
    safeFiniteNumber(plannerJobsStatus?.ai_planner_job?.progress_pct, 0) ?? 0
  );

  return (
    <section
      className={`page-view ag-view${activePage === "action-graph" ? " active" : ""}`}
      id="view-action-graph"
      aria-label="Action Graph workspace"
      hidden={activePage !== "action-graph"}
    >
      {/* Top 4-Tab Workspace Navigation Bar */}
      <div className="ag-subtab-bar" id="ag-workspace-subbar">
        <div
          className="ag-subtab-tabs"
          id="ag-workspace-tablist"
          role="tablist"
          aria-label="Action Graph Workspace"
        >
          <button
            type="button"
            role="tab"
            id="ag-tab-btn-heuristics"
            data-ag-tab="heuristics"
            aria-selected={activeTab === "heuristics" ? "true" : "false"}
            aria-controls="ag-panel-heuristics"
            className={`ag-subtab-btn${activeTab === "heuristics" ? " active" : ""}`}
            onClick={() => switchTab("heuristics")}
          >
            <span className="ag-subtab-label">Heuristics</span>
            <span className="ag-subtab-tag">P0-P3</span>
          </button>

          <button
            type="button"
            role="tab"
            id="ag-tab-btn-config-optimizer"
            data-ag-tab="config-optimizer"
            aria-selected={activeTab === "config-optimizer" ? "true" : "false"}
            aria-controls="ag-pane-config-optimizer"
            className={`ag-subtab-btn${activeTab === "config-optimizer" ? " active" : ""}`}
            onClick={() => switchTab("config-optimizer")}
          >
            <span className="ag-subtab-label">Config Optimization</span>
            {coJobStatus === "running" ? (
              <span className="ag-subtab-badge is-running">{coPct > 0 ? `${coPct}%` : "RUN"}</span>
            ) : (
              <span className="ag-subtab-tag">TARGETS</span>
            )}
          </button>

          <button
            type="button"
            role="tab"
            id="ag-tab-btn-ai-planner"
            data-ag-tab="ai-planner"
            aria-selected={activeTab === "ai-planner" ? "true" : "false"}
            aria-controls="ag-pane-ai-planner"
            className={`ag-subtab-btn${activeTab === "ai-planner" ? " active" : ""}`}
            onClick={() => switchTab("ai-planner")}
          >
            <span className="ag-subtab-label">AI-Planner</span>
            {aiJobStatus === "running" ? (
              <span className="ag-subtab-badge is-running">{aiPct > 0 ? `${aiPct}%` : "RUN"}</span>
            ) : pendingConflictCount > 0 ? (
              <span className="ag-subtab-badge is-conflict" id="ag-tab-conflict-badge">
                {pendingConflictCount} CONFLICT
              </span>
            ) : (
              <span className={`ag-subtab-tag${gate.liveAiUnlocked ? " is-ok" : ""}`}>
                {gate.coloniesOwned}/{gate.maxPlanets}
              </span>
            )}
          </button>

          <button
            type="button"
            role="tab"
            id="ag-tab-btn-fleet-planner"
            data-ag-tab="fleet-planner"
            aria-selected={activeTab === "fleet-planner" ? "true" : "false"}
            aria-controls="ag-panel-fleet-planner"
            className={`ag-subtab-btn${activeTab === "fleet-planner" ? " active" : ""}`}
            onClick={() => switchTab("fleet-planner")}
          >
            <span className="ag-subtab-label">Fleet / Attack</span>
            <span className="ag-subtab-tag">SLOTS</span>
          </button>
        </div>

        <div className="ag-subtab-status">
          <span
            id="ag-global-gate-pill"
            className={`pill ${gate.liveAiUnlocked ? "ok" : "warn"}`}
          >
            {gate.liveAiUnlocked
              ? `Colonies ${gate.coloniesOwned}/${gate.maxPlanets} · AI Unlocked`
              : `Colonies ${gate.coloniesOwned}/${gate.maxPlanets} · Preview Mode`}
          </span>
          <span
            id="ag-global-mode-pill"
            className={`pill ${
              fallbackReason
                ? "warn"
                : effectiveMode === "ai_planner"
                  ? "ok"
                  : "neutral"
            }`}
          >
            {fallbackReason
              ? "LIVE: HEURISTIC (FALLBACK)"
              : effectiveMode === "ai_planner"
                ? "LIVE: AI-PLANNER"
                : "LIVE: HEURISTIC"}
          </span>
        </div>
      </div>

      {/* Tab 1: Heuristics (default active tab preserving #ag-main-window contracts) */}
      <HeuristicsTab
        hidden={activeTab !== "heuristics"}
        onSwitchTab={switchTab}
      />

      {/* Tab 2: Config Optimization */}
      <ConfigOptimizerTab hidden={activeTab !== "config-optimizer"} />

      {/* Tab 3: AI-Planner (Full Horizon Sequential Rollout + Field Conflict Gate) */}
      <AiPlannerTab hidden={activeTab !== "ai-planner"} />

      {/* Tab 4: Fleet / Attack Planner (Bidirectionally synced with Config) */}
      <FleetPlannerTab hidden={activeTab !== "fleet-planner"} />
    </section>
  );
};

export default ActionGraphView;
