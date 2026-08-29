import React, { useCallback, useMemo } from "react";
import { feedbackKindForError } from "../../api/httpClient";
import { useConsole } from "../../context/ConsoleContext";
import type { PlannedActionData } from "../../types/api";
import { ManualQueueComposer } from "./ManualQueueComposer";
import {
  PlanActionRow,
  actionPayloadFor,
  buildPinPayloadFromAction,
} from "./planActions";

const NAV_JUMP_TARGETS = [
  { label: "Home", action: "internalHome" },
  { label: "Buildings", action: "internalBuildings" },
  { label: "Economy", action: "internalEconomy" },
  { label: "Research", action: "internalResearch" },
  { label: "Galaxy", action: "internalGalaxy" },
] as const;

export const OperationsView: React.FC = () => {
  const {
    dashboard,
    activePage,
    setActivePage,
    nowEpochSec,
    showFeedback,
    apiCommand,
    loadDashboard,
    uiPrefs,
    setUiPref,
    enqueueManualCommand,
  } = useConsole();

  const queueFilter = uiPrefs.queueFilter || "all";
  const allActions = useMemo(() => dashboard?.plan || [], [dashboard?.plan]);

  const manualList = useMemo(() => {
    if (Array.isArray(dashboard?.manual_queue)) {
      return dashboard!.manual_queue!;
    }
    return allActions.filter(
      (a) => a.manual_queue_id != null && Number(a.manual_queue_id) > 0
    );
  }, [allActions, dashboard]);

  const pinnedCount = manualList.length;

  const filteredActions = useMemo(() => {
    if (queueFilter === "ready") {
      return allActions.filter((a) => Boolean(a.executable_now));
    }
    if (queueFilter === "manual") {
      return allActions.filter(
        (a) => a.manual_queue_id != null && Number(a.manual_queue_id) > 0
      );
    }
    return allActions;
  }, [allActions, queueFilter]);

  const cfgMode = String(
    dashboard?.config?.planner_mode || dashboard?.planner_mode || "priority"
  ).toLowerCase();
  const isGaNn = cfgMode === "ga_nn";
  const modeLabel = isGaNn ? "MODE: GA+NN" : "MODE: PRIORITY";

  const handleDispatch = useCallback(
    async (action: PlannedActionData, dryRun: boolean) => {
      const payload = actionPayloadFor(action, dryRun);
      if (!payload) return;
      try {
        showFeedback(
          `${dryRun ? "Simulating" : "Executing"} ${action.target_name || action.kind}...`,
          "ok"
        );
        const res = await apiCommand(payload);
        showFeedback(res.message || `Command ${payload.command} completed.`, "ok");
        await loadDashboard({ refresh: false, force: true });
      } catch (err: any) {
        showFeedback(`Action failed: ${err.message}`, feedbackKindForError(err));
      }
    },
    [apiCommand, loadDashboard, showFeedback]
  );

  const handleUpdateQueue = useCallback(
    async (queueId: number, patch: Record<string, any>) => {
      try {
        const res = await apiCommand({ command: "queue_update", queue_id: queueId, ...patch });
        showFeedback(res.message || `Updated queue item #${queueId}.`, "ok");
        await loadDashboard({ refresh: false, force: true });
      } catch (err: any) {
        showFeedback(`Queue update failed: ${err.message}`, feedbackKindForError(err));
      }
    },
    [apiCommand, loadDashboard, showFeedback]
  );

  const handleCancelQueue = useCallback(
    async (queueId: number) => {
      try {
        const res = await apiCommand({ command: "queue_cancel", queue_id: queueId });
        showFeedback(res.message || `Removed queue item #${queueId}.`, "ok");
        await loadDashboard({ refresh: false, force: true });
      } catch (err: any) {
        showFeedback(`Cancel failed: ${err.message}`, feedbackKindForError(err));
      }
    },
    [apiCommand, loadDashboard, showFeedback]
  );

  const handleClearQueue = useCallback(async () => {
    try {
      const res = await apiCommand({ command: "queue_clear" });
      showFeedback(res.message || "Cleared manual queue.", "ok");
      await loadDashboard({ refresh: false, force: true });
    } catch (err: any) {
      showFeedback(`Clear queue failed: ${err.message}`, feedbackKindForError(err));
    }
  }, [apiCommand, loadDashboard, showFeedback]);

  const handlePinQueue = useCallback(
    (act: PlannedActionData, priority = "P1") => {
      void enqueueManualCommand(buildPinPayloadFromAction(act, priority));
    },
    [enqueueManualCommand]
  );

  const handleNavJump = useCallback(
    async (action: string) => {
      try {
        await apiCommand({ command: "navigate", action });
        showFeedback(`Chrome switched to ${action}.`, "ok");
      } catch (err: any) {
        showFeedback(`Navigation failed: ${err.message}`, feedbackKindForError(err));
      }
    },
    [apiCommand, showFeedback]
  );

  return (
    <section
      id="view-operations"
      className="page-view"
      aria-label="Operations priority queue"
      hidden={activePage !== "operations"}
    >
      <article className="hud-window page-fill">
        <div className="window-bar window-bar-wrap">
          <div className="window-title-group">
            <span className="module-code tabular">OPS</span>
            <h2>Priority Action Queue (P0 → P3)</h2>
            <span className="module-meta tabular" id="plan-count-badge">
              {`${filteredActions.length} TASKS`}
            </span>
            <span className="module-meta tabular" id="ops-pinned-badge" hidden={pinnedCount === 0}>
              {`${pinnedCount} PINNED`}
            </span>
            <span
              className={`module-meta tabular mode-badge${isGaNn ? " is-ga" : ""}`}
              id="ops-planner-mode-badge"
            >
              {modeLabel}
            </span>
          </div>

          <div className="filter-toolbar">
            <div className="filter-chips" role="group" aria-label="Queue filters">
              <button
                type="button"
                className={`chip-btn${queueFilter === "all" ? " active" : ""}`}
                data-filter="all"
                onClick={() => setUiPref("queueFilter", "all")}
              >
                All Planned
              </button>
              <button
                type="button"
                className={`chip-btn${queueFilter === "ready" ? " active" : ""}`}
                data-filter="ready"
                onClick={() => setUiPref("queueFilter", "ready")}
              >
                Executable Only
              </button>
              <button
                type="button"
                className={`chip-btn${queueFilter === "manual" ? " active" : ""}`}
                data-filter="manual"
                id="chip-filter-manual"
                onClick={() => setUiPref("queueFilter", "manual")}
              >
                {`Pinned Only (${pinnedCount})`}
              </button>
            </div>

            <div className="inline-actions nav-matrix-inline">
              <button
                type="button"
                className="btn btn-secondary btn-sm btn-danger-ghost"
                id="btn-mq-clear"
                hidden={pinnedCount === 0}
                onClick={handleClearQueue}
              >
                {pinnedCount > 0 ? `Clear Pinned (${pinnedCount})` : "Clear Pinned"}
              </button>
              <button
                type="button"
                className="btn btn-secondary btn-sm"
                data-goto-page="action-graph"
                onClick={() => setActivePage("action-graph")}
              >
                Action Graph →
              </button>
              {NAV_JUMP_TARGETS.map((item) => (
                <button
                  key={item.action}
                  type="button"
                  className="btn btn-secondary btn-sm nav-jump"
                  data-action={item.action}
                  onClick={() => handleNavJump(item.action)}
                >
                  {item.label}
                </button>
              ))}
            </div>
          </div>
        </div>

        <ManualQueueComposer />

        <div className="table-wrap table-scroll">
          <table id="ops-plan-table">
            <thead>
              <tr>
                <th>Priority</th>
                <th>Action</th>
                <th>Cost / Missing</th>
                <th className="col-right">Wait ETA</th>
                <th className="col-right">Total ETA</th>
                <th>Reason</th>
                <th className="col-right">Execute</th>
              </tr>
            </thead>
            <tbody id="plan-tbody">
              {filteredActions.length === 0 ? (
                <tr>
                  <td colSpan={7} className="empty-cell">
                    {dashboard ? "No matching actions in queue." : "Waiting for plan..."}
                  </td>
                </tr>
              ) : (
                filteredActions.map((act, idx) => (
                  <PlanActionRow
                    key={`${act.manual_queue_id ?? "auto"}-${act.kind}-${act.planet_id}-${act.target_id ?? act.target_name}-${idx}`}
                    action={act}
                    compact={false}
                    nowEpochSec={nowEpochSec}
                    onDispatch={handleDispatch}
                    onUpdateQueue={handleUpdateQueue}
                    onCancelQueue={handleCancelQueue}
                    onPinQueue={handlePinQueue}
                  />
                ))
              )}
            </tbody>
          </table>
        </div>
      </article>
    </section>
  );
};

export default OperationsView;
