import React, { useCallback, useMemo } from "react";
import {
  feedbackKindForError } from "../../api/httpClient";
import { SortableTh } from "../../components/SortableTh";
import { useActivePage,
  useConsoleActions,
  useDashboard,
  useNowEpochSec,
  useUiPrefs,
} from "../../store";
import { useTableSort } from "../../hooks/useTableSort";
import type { PlannedActionData } from "../../types/api";
import { ManualQueueComposer } from "./ManualQueueComposer";
import {
  PLAN_ACTION_SORT_COLUMNS,
  PlanActionRow,
  actionPayloadFor,
  buildPinPayloadFromAction,
  buildPlanetNameMaps,
  dashboardQueueDomainForAction,
  isPlanetConstructing,
} from "./planActions";

const NAV_JUMP_TARGETS = [
  { label: "Home", action: "internalHome" },
  { label: "Buildings", action: "internalBuildings" },
  { label: "Economy", action: "internalEconomy" },
  { label: "Research", action: "internalResearch" },
  { label: "Galaxy", action: "internalGalaxy" },
] as const;

export const OperationsView: React.FC = () => {
  const dashboard = useDashboard();
  const activePage = useActivePage();
  const nowEpochSec = useNowEpochSec();
  const uiPrefs = useUiPrefs();
  const {
    setActivePage,
    showFeedback,
    apiCommand,
    loadDashboard,
    setUiPref,
    enqueueManualCommand,
  } = useConsoleActions();

  const planets = useMemo(() => dashboard?.state?.planets || [], [dashboard?.state?.planets]);
  const planetNameMaps = useMemo(
    () => buildPlanetNameMaps(planets, dashboard?.custom_planet_names),
    [dashboard?.custom_planet_names, planets]
  );
  const activeQueues = useMemo(
    () => (Array.isArray(dashboard?.state?.active_queues) ? dashboard!.state!.active_queues! : []),
    [dashboard?.state?.active_queues]
  );
  const effNowSec =
    nowEpochSec != null && nowEpochSec > 0 ? nowEpochSec : Math.floor(Date.now() / 1000);
  const constructingPlanetIds = useMemo(() => {
    const ids = new Set<number>();
    for (const p of planets) {
      if (isPlanetConstructing(p, activeQueues, dashboard?.state?.active_planet_id, effNowSec)) {
        ids.add(Number(p.planet_id));
      }
    }
    return ids;
  }, [activeQueues, dashboard?.state?.active_planet_id, effNowSec, planets]);

  const queueFilter = uiPrefs.queueFilter || "all";
  const rawPlanetScope = String(uiPrefs.opsPlanetScope || "all");
  const scopedPlanet = useMemo(
    () =>
      rawPlanetScope !== "all"
        ? planets.find((p) => String(p.planet_id) === rawPlanetScope) || null
        : null,
    [planets, rawPlanetScope]
  );
  const opsPlanetScope = scopedPlanet ? String(scopedPlanet.planet_id) : "all";

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

  const filterMatchedActions = useMemo(() => {
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

  const planetCounts = useMemo(() => {
    const map = new Map<number, { total: number; ready: number }>();
    for (const p of planets) {
      map.set(p.planet_id, { total: 0, ready: 0 });
    }
    for (const act of filterMatchedActions) {
      if (dashboardQueueDomainForAction(act) === "research") continue;
      const cur = map.get(Number(act.planet_id));
      if (cur) {
        cur.total += 1;
        if (act.executable_now) cur.ready += 1;
      }
    }
    return map;
  }, [filterMatchedActions, planets]);

  const filteredActions = useMemo(() => {
    if (!scopedPlanet) return filterMatchedActions;
    const pid = Number(scopedPlanet.planet_id);
    return filterMatchedActions.filter((a) => Number(a.planet_id) === pid);
  }, [filterMatchedActions, scopedPlanet]);

  const actionSort = useTableSort(filteredActions, PLAN_ACTION_SORT_COLUMNS);

  const cfgMode = String(
    dashboard?.config?.planner_mode ||
      dashboard?.planner_mode ||
      dashboard?.effective_planner_mode ||
      "priority"
  ).toLowerCase();
  const isAiPlanner = cfgMode === "ai_planner" || cfgMode === "ga_nn";
  const modeLabel = isAiPlanner ? "MODE: AI-PLANNER" : "MODE: PRIORITY";

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
              className={`module-meta tabular mode-badge${isAiPlanner ? " is-ga" : ""}`}
              id="ops-planner-mode-badge"
            >
              {modeLabel}
            </span>
          </div>

          <div className="filter-toolbar ops-filter-toolbar">
            <div className="ops-toolbar-row">
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

            {planets.length > 1 ? (
              <div
                className="filter-chips ops-planet-scope-chips"
                role="group"
                aria-label="Planet queue scope"
              >
                <button
                  type="button"
                  className={`chip-btn planet-scope-chip tabular${opsPlanetScope === "all" ? " active" : ""}`}
                  data-ops-planet="all"
                  title={`Show all ${planets.length} colonies (${constructingPlanetIds.size} currently constructing)`}
                  onClick={() => setUiPref("opsPlanetScope", "all")}
                >
                  <span className="planet-chip-top">All Planets</span>
                  <span className="planet-chip-sub">
                    {`${planets.length} colonies · ${constructingPlanetIds.size} bldg`}
                  </span>
                </button>
                {planets.map((p) => {
                  const pidKey = String(p.planet_id);
                  const st = planetCounts.get(p.planet_id) || { total: 0, ready: 0 };
                  const isConstructing = constructingPlanetIds.has(Number(p.planet_id));
                  const planetLabel = p.name || "Colony";
                  return (
                    <button
                      key={pidKey}
                      type="button"
                      className={`chip-btn planet-scope-chip tabular${opsPlanetScope === pidKey ? " active" : ""}${isConstructing ? " is-constructing" : ""}`}
                      data-ops-planet={pidKey}
                      data-constructing={isConstructing ? "true" : "false"}
                      title={`${planetLabel} [${p.coords}] — ${isConstructing ? "Building in progress" : "Builder idle"} · ${st.ready} ready / ${st.total} queued`}
                      onClick={() => setUiPref("opsPlanetScope", pidKey)}
                    >
                      <span className="planet-chip-top">{p.coords || planetLabel}</span>
                      <span className="planet-chip-sub">
                        {isConstructing
                          ? `${planetLabel} · Building`
                          : `${planetLabel} (${st.total})`}
                      </span>
                    </button>
                  );
                })}
              </div>
            ) : null}
          </div>
        </div>

        <ManualQueueComposer />

        <div className="table-wrap table-scroll">
          <table id="ops-plan-table">
            <thead>
              <tr>
                <SortableTh
                  label="Priority"
                  sortKey="priority"
                  activeKey={actionSort.sortKey}
                  direction={actionSort.sortDirection}
                  onSort={actionSort.requestSort}
                />
                <SortableTh
                  label="Action"
                  sortKey="action"
                  activeKey={actionSort.sortKey}
                  direction={actionSort.sortDirection}
                  onSort={actionSort.requestSort}
                />
                <SortableTh
                  label="Cost / Missing"
                  sortKey="costMissing"
                  activeKey={actionSort.sortKey}
                  direction={actionSort.sortDirection}
                  onSort={actionSort.requestSort}
                />
                <SortableTh
                  label="Wait ETA"
                  sortKey="waitEta"
                  activeKey={actionSort.sortKey}
                  direction={actionSort.sortDirection}
                  onSort={actionSort.requestSort}
                  align="right"
                />
                <SortableTh
                  label="Total ETA"
                  sortKey="totalEta"
                  activeKey={actionSort.sortKey}
                  direction={actionSort.sortDirection}
                  onSort={actionSort.requestSort}
                  align="right"
                />
                <SortableTh
                  label="Reason"
                  sortKey="reason"
                  activeKey={actionSort.sortKey}
                  direction={actionSort.sortDirection}
                  onSort={actionSort.requestSort}
                />
                <th className="col-right">Execute</th>
              </tr>
            </thead>
            <tbody id="plan-tbody">
              {actionSort.sortedRows.length === 0 ? (
                <tr>
                  <td colSpan={7} className="empty-cell">
                    {dashboard ? "No matching actions in queue." : "Waiting for plan..."}
                  </td>
                </tr>
              ) : (
                actionSort.sortedRows.map((act, idx) => (
                  <PlanActionRow
                    key={`${act.manual_queue_id ?? "auto"}-${act.kind}-${act.planet_id}-${act.target_id ?? act.target_name}-${idx}`}
                    action={act}
                    compact={false}
                    nowEpochSec={nowEpochSec}
                    planetNameById={planetNameMaps.byId}
                    planetNameByCoords={planetNameMaps.byCoords}
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
