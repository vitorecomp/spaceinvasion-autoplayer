import React, { useCallback, useMemo } from "react";
import { feedbackKindForError } from "../../api/httpClient";
import { SortableTh } from "../../components/SortableTh";
import { useConsole } from "../../context/ConsoleContext";
import { useTableSort } from "../../hooks/useTableSort";
import type { PlannedActionData } from "../../types/api";
import { ManualQueueComposer } from "./ManualQueueComposer";
import {
  PLAN_ACTION_SORT_COLUMNS,
  PlanActionRow,
  actionPayloadFor,
  buildPinPayloadFromAction,
  dashboardQueueDomainForAction,
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
    setSelectedPlanetId,
    nowEpochSec,
    showFeedback,
    apiCommand,
    loadDashboard,
    uiPrefs,
    setUiPref,
    enqueueManualCommand,
  } = useConsole();

  const planets = useMemo(() => dashboard?.state?.planets || [], [dashboard?.state?.planets]);
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
  const opsQueueLayout: "table" | "parallel" =
    planets.length > 1 && uiPrefs.opsQueueLayout === "parallel" ? "parallel" : "table";

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

  const parallelLanes = useMemo(() => {
    const visiblePlanets = scopedPlanet ? [scopedPlanet] : planets;
    const planetLanes = visiblePlanets.map((planet) => {
      const actions = filterMatchedActions.filter(
        (a) =>
          Number(a.planet_id) === Number(planet.planet_id) &&
          dashboardQueueDomainForAction(a) !== "research"
      );
      const readyCount = actions.filter((a) => Boolean(a.executable_now)).length;
      return { planet, actions, readyCount };
    });
    const researchActions = filterMatchedActions.filter(
      (a) => dashboardQueueDomainForAction(a) === "research"
    );
    return { planetLanes, researchActions };
  }, [filterMatchedActions, planets, scopedPlanet]);

  const actionSort = useTableSort(filteredActions, PLAN_ACTION_SORT_COLUMNS);

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

            {planets.length > 1 ? (
              <div className="filter-chips" role="group" aria-label="Planet queue scope">
                <button
                  type="button"
                  className={`chip-btn tabular${opsPlanetScope === "all" ? " active" : ""}`}
                  data-ops-planet="all"
                  onClick={() => setUiPref("opsPlanetScope", "all")}
                >
                  {`All Planets (${planets.length})`}
                </button>
                {planets.map((p) => {
                  const pidKey = String(p.planet_id);
                  const st = planetCounts.get(p.planet_id) || { total: 0, ready: 0 };
                  return (
                    <button
                      key={pidKey}
                      type="button"
                      className={`chip-btn tabular${opsPlanetScope === pidKey ? " active" : ""}${st.ready > 0 ? " has-ready" : ""}`}
                      data-ops-planet={pidKey}
                      onClick={() => setUiPref("opsPlanetScope", pidKey)}
                    >
                      {st.ready > 0
                        ? `${p.coords || p.name} · ${st.ready} ready`
                        : `${p.coords || p.name} (${st.total})`}
                    </button>
                  );
                })}
              </div>
            ) : null}

            {planets.length > 1 ? (
              <div className="filter-chips" role="group" aria-label="Queue layout mode">
                <button
                  type="button"
                  className={`chip-btn${opsQueueLayout === "table" ? " active" : ""}`}
                  data-ops-layout="table"
                  onClick={() => setUiPref("opsQueueLayout", "table")}
                >
                  Unified Table
                </button>
                <button
                  type="button"
                  className={`chip-btn${opsQueueLayout === "parallel" ? " active" : ""}`}
                  data-ops-layout="parallel"
                  onClick={() => setUiPref("opsQueueLayout", "parallel")}
                >
                  Parallel by Planet
                </button>
              </div>
            ) : null}

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

        {opsQueueLayout === "parallel" ? (
          <div className="ops-parallel-grid" id="ops-parallel-grid">
            {parallelLanes.planetLanes.map(({ planet, actions, readyCount }) => {
              const isActiveP =
                dashboard?.state?.active_planet_id != null &&
                Number(planet.planet_id) === Number(dashboard.state.active_planet_id);
              return (
                <article
                  key={`lane-${planet.planet_id}`}
                  className="hud-window ops-planet-lane-card"
                  data-planet-lane={String(planet.planet_id)}
                >
                  <div className="window-bar window-bar-wrap">
                    <div className="window-title-group">
                      <span className="module-code tabular">{planet.coords || "PLN"}</span>
                      <h3>{planet.name || "Colony"}</h3>
                      <span className="module-meta tabular dash-q-count">
                        {`${readyCount}/${actions.length}`}
                      </span>
                      <span className="module-meta tabular dash-q-scope">
                        {`${planet.fields_used ?? "—"}/${planet.fields_max ?? "—"} F${isActiveP ? " · ACTIVE" : " · PARALLEL"}`}
                      </span>
                    </div>
                    <div className="inline-actions">
                      <button
                        type="button"
                        className="btn btn-secondary btn-sm"
                        onClick={() => {
                          setSelectedPlanetId(planet.planet_id);
                          const mqPlanet = document.getElementById("mq-planet") as HTMLSelectElement | null;
                          if (mqPlanet) {
                            mqPlanet.value = String(planet.planet_id);
                            mqPlanet.dispatchEvent(new Event("change", { bubbles: true }));
                          }
                          document.getElementById("mq-target")?.focus();
                        }}
                      >
                        + Queue Here
                      </button>
                    </div>
                  </div>
                  <div className="table-wrap table-scroll">
                    <table>
                      <thead>
                        <tr>
                          <th>Prio</th>
                          <th>Action</th>
                          <th>Missing / Cost</th>
                          <th className="col-right">Wait ETA</th>
                          <th className="col-right">Total ETA / Finish</th>
                          <th className="col-right">Ops</th>
                        </tr>
                      </thead>
                      <tbody>
                        {actions.length === 0 ? (
                          <tr>
                            <td colSpan={6} className="empty-cell">
                              No matching planet actions.
                            </td>
                          </tr>
                        ) : (
                          actions.map((act, idx) => (
                            <PlanActionRow
                              key={`lane-${planet.planet_id}-${act.manual_queue_id ?? "auto"}-${act.kind}-${act.target_id ?? act.target_name}-${idx}`}
                              action={act}
                              compact={true}
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
              );
            })}

            {parallelLanes.researchActions.length > 0 ? (
              <article
                className="hud-window ops-planet-lane-card ops-global-research-card"
                data-planet-lane="global-research"
              >
                <div className="window-bar window-bar-wrap">
                  <div className="window-title-group">
                    <span className="module-code tabular">RES</span>
                    <h3>Global Research Queue</h3>
                    <span className="module-meta tabular dash-q-count">
                      {String(parallelLanes.researchActions.length)}
                    </span>
                    <span className="module-meta tabular dash-q-scope">
                      GLOBAL · 1 SHARED SLOT
                    </span>
                  </div>
                </div>
                <div className="table-wrap table-scroll">
                  <table>
                    <thead>
                      <tr>
                        <th>Prio</th>
                        <th>Action</th>
                        <th>Missing / Cost</th>
                        <th className="col-right">Wait ETA</th>
                        <th className="col-right">Total ETA / Finish</th>
                        <th className="col-right">Ops</th>
                      </tr>
                    </thead>
                    <tbody>
                      {parallelLanes.researchActions.map((act, idx) => (
                        <PlanActionRow
                          key={`lane-res-${act.manual_queue_id ?? "auto"}-${act.target_id ?? act.target_name}-${idx}`}
                          action={act}
                          compact={true}
                          nowEpochSec={nowEpochSec}
                          onDispatch={handleDispatch}
                          onUpdateQueue={handleUpdateQueue}
                          onCancelQueue={handleCancelQueue}
                          onPinQueue={handlePinQueue}
                        />
                      ))}
                    </tbody>
                  </table>
                </div>
              </article>
            ) : null}
          </div>
        ) : (
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
        )}
      </article>
    </section>
  );
};

export default OperationsView;
