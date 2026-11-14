import React, { useCallback, useEffect, useRef, useState } from "react";
import { feedbackKindForError, safeFiniteNumber } from "../../api/httpClient";
import {
  buildClientFallbackActionGraph,
  useActionGraphData,
  useAgSimState,
  useApiOnline,
  useConsoleActions,
  useDashboard,
  usePlannerJobsStatus,
  useUiPrefs,
} from "../../store";
import { ActionGraphTabKey, StrategyConfigData } from "../../types/api";
import { DagCanvas } from "./DagCanvas";
import {
  AG_BOOL_FIELDS,
  AG_NUM_DEFAULTS,
  buildDraftStateFromConfig,
  collectHeuristicsDraftPayload,
} from "./heuristicsDraft";
import { HeuristicsParamPanel } from "./HeuristicsParamPanel";
import { computeColonyGateInfo, normalizeUiPlannerMode, sanitizeMainWindowText } from "./plannerHelpers";

export { AG_NUM_DEFAULTS };

export interface HeuristicsTabProps {
  hidden?: boolean;
  onSwitchTab?: (tab: ActionGraphTabKey) => void;
}

export const HeuristicsTab: React.FC<HeuristicsTabProps> = ({ hidden = false, onSwitchTab }) => {
  const apiOnline = useApiOnline();
  const dashboard = useDashboard();
  const actionGraphData = useActionGraphData();
  const plannerJobsStatus = usePlannerJobsStatus();
  const uiPrefs = useUiPrefs();
  const { agSimDirty, agHydrationTick } = useAgSimState();
  const {
    setDashboard,
    setActionGraphData,
    setAgSimDirty,
    setAgSimRunning,
    setConfigDirty,
    setConfigSaving,
    bumpDashboardSeq,
    beginGaCalc,
    endGaCalc,
    setUiPref,
    apiCommand,
    loadDashboard,
    showFeedback,
    getSnapshot,
    getRuntime,
    bumpAgSimSeq,
    bumpConfigHydration,
  } = useConsoleActions();

  const debounceRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const [draftValues, setDraftValues] = useState<Record<string, any>>(() =>
    buildDraftStateFromConfig(dashboard?.config)
  );

  const populateControls = useCallback((cfg: StrategyConfigData | null | undefined) => {
    if (!cfg || typeof cfg !== "object") return;
    const next = buildDraftStateFromConfig(cfg);
    setDraftValues(next);
    if (typeof document !== "undefined") {
      for (const [k, v] of Object.entries(next)) {
        const node = document.getElementById(`ag-cfg-${k}`) as
          | HTMLInputElement
          | HTMLSelectElement
          | null;
        if (!node) continue;
        if (node instanceof HTMLInputElement && node.type === "checkbox") {
          node.checked = Boolean(v);
        } else {
          node.value = String(v ?? "");
        }
      }
    }
  }, []);

  useEffect(() => {
    const rt = getRuntime();
    if (!rt.agSimDirty && !(rt.agSimRunning > 0) && !(rt.configSaving > 0) && dashboard?.config) {
      populateControls(dashboard.config);
    }
  }, [agHydrationTick, dashboard?.config, getRuntime, populateControls]);

  const syncDraftValuesFromDom = useCallback(
    (modeOverride?: "priority" | "ai_planner") => {
      const readEl = (id: string) =>
        (typeof document !== "undefined" ? document.getElementById(id) : null) as
          | HTMLInputElement
          | HTMLSelectElement
          | null;

      setDraftValues((prev) => {
        const modeSel = readEl("ag-cfg-planner_mode");
        const playSel = readEl("ag-cfg-playstyle");
        const next: Record<string, any> = {
          ...prev,
          planner_mode:
            modeOverride ??
            normalizeUiPlannerMode(modeSel ? modeSel.value : prev.planner_mode),
          playstyle: playSel
            ? String(playSel.value || "fleeter")
            : String(prev.playstyle || "fleeter"),
        };
        for (const boolField of AG_BOOL_FIELDS) {
          const box = readEl(`ag-cfg-${boolField}`) as HTMLInputElement | null;
          if (box) next[boolField] = Boolean(box.checked);
        }
        for (const numField of Object.keys(AG_NUM_DEFAULTS)) {
          const inp = readEl(`ag-cfg-${numField}`);
          if (inp) next[numField] = String(inp.value ?? "");
        }
        return next;
      });
    },
    []
  );

  const collectDraftPayload = useCallback(
    (modeOverride?: "priority" | "ai_planner"): Record<string, any> =>
      collectHeuristicsDraftPayload(draftValues, modeOverride),
    [draftValues]
  );

  const refreshActiveActionGraph = useCallback(
    async (forceRefresh = false): Promise<boolean> => {
      const reqSeq = bumpAgSimSeq();
      try {
        const res = await apiCommand({
          command: "action_graph",
          simulated: false,
          refresh: Boolean(forceRefresh),
        });
        const rt = getRuntime();
        if (reqSeq !== rt.agSimSeq || rt.agSimDirty) return false;
        if (res.action_graph && typeof res.action_graph === "object") {
          setActionGraphData(res.action_graph);
          const curDash = getSnapshot().dashboard || dashboard;
          if (curDash) {
            setDashboard({ ...curDash, action_graph: res.action_graph });
          }
          return true;
        }
      } catch (err: any) {
        if (reqSeq === getRuntime().agSimSeq) {
          showFeedback(`Action graph load failed: ${err.message}`, feedbackKindForError(err));
        }
      }
      return false;
    },
    [
      apiCommand,
      bumpAgSimSeq,
      dashboard,
      getRuntime,
      getSnapshot,
      setActionGraphData,
      setDashboard,
      showFeedback,
    ]
  );

  const runAgSimulation = useCallback(
    async ({
      explicit = false,
      modeOverride,
    }: {
      explicit?: boolean;
      modeOverride?: "priority" | "ai_planner";
    } = {}): Promise<boolean> => {
      if (debounceRef.current) {
        clearTimeout(debounceRef.current);
        debounceRef.current = null;
      }
      const draft = collectDraftPayload(modeOverride);
      const simMode: "priority" | "ai_planner" = normalizeUiPlannerMode(draft.planner_mode);
      syncDraftValuesFromDom(simMode);
      setAgSimDirty(true);

      const reqSeq = bumpAgSimSeq();
      setAgSimRunning((c) => c + 1);

      const useHud = simMode === "ai_planner";
      if (useHud) beginGaCalc();

      try {
        const res = await apiCommand({
          command: "action_graph",
          planner_mode: simMode,
          config: draft,
          simulated: true,
          refresh: false,
        });
        if (reqSeq !== getRuntime().agSimSeq) return false;
        if (res.action_graph && typeof res.action_graph === "object") {
          const isDirty = Boolean(res.action_graph.is_dirty);
          setActionGraphData(res.action_graph);
          const curDash = getSnapshot().dashboard || dashboard;
          if (!isDirty && curDash) {
            setDashboard({ ...curDash, action_graph: res.action_graph });
          }
          setAgSimDirty(isDirty);
          if (explicit) {
            const cnt = Array.isArray(res.action_graph.nodes)
              ? res.action_graph.nodes.length
              : 0;
            showFeedback(
              `Simulated ${simMode} action graph (${cnt} planned ${cnt === 1 ? "action" : "actions"} — active config unchanged).`,
              "ok"
            );
          }
          return true;
        }
        return false;
      } catch (err: any) {
        if (reqSeq === getRuntime().agSimSeq) {
          showFeedback(`Simulation preview error: ${err.message}`, feedbackKindForError(err));
        }
        return false;
      } finally {
        setAgSimRunning((c) => Math.max(0, c - 1));
        if (useHud) endGaCalc();
      }
    },
    [
      apiCommand,
      beginGaCalc,
      bumpAgSimSeq,
      collectDraftPayload,
      dashboard,
      endGaCalc,
      getRuntime,
      getSnapshot,
      setActionGraphData,
      setAgSimDirty,
      setAgSimRunning,
      setDashboard,
      showFeedback,
      syncDraftValuesFromDom,
    ]
  );

  const scheduleDebouncedSim = useCallback(() => {
    bumpAgSimSeq();
    setAgSimDirty(true);
    if (debounceRef.current) {
      clearTimeout(debounceRef.current);
    }
    debounceRef.current = setTimeout(() => {
      debounceRef.current = null;
      void runAgSimulation({ explicit: false });
    }, 180);
  }, [bumpAgSimSeq, runAgSimulation, setAgSimDirty]);

  const onModeSwitchClick = (targetMode: "priority" | "ai_planner") => {
    const modeSel =
      typeof document !== "undefined"
        ? (document.getElementById("ag-cfg-planner_mode") as HTMLSelectElement | null)
        : null;
    if (modeSel) {
      modeSel.value = targetMode;
    }
    syncDraftValuesFromDom(targetMode);
    void runAgSimulation({ explicit: false, modeOverride: targetMode });
  };

  const onResetSim = async () => {
    if (debounceRef.current) {
      clearTimeout(debounceRef.current);
      debounceRef.current = null;
    }
    bumpAgSimSeq();
    setAgSimDirty(false);
    const curDash = getSnapshot().dashboard || dashboard;
    const activeCfg = curDash?.config || null;
    if (activeCfg) {
      populateControls(activeCfg);
    }
    if (curDash?.action_graph) {
      setActionGraphData(curDash.action_graph);
    } else if (curDash) {
      setActionGraphData(buildClientFallbackActionGraph(curDash));
    }
    if (apiOnline) {
      await refreshActiveActionGraph(false);
    }
    showFeedback("Reset simulation controls to active StrategyConfig.", "ok");
  };

  const onApplySim = async () => {
    if (debounceRef.current) {
      clearTimeout(debounceRef.current);
      debounceRef.current = null;
    }
    syncDraftValuesFromDom();
    const payload = collectDraftPayload();
    const isAi = normalizeUiPlannerMode(payload.planner_mode) === "ai_planner";
    if (isAi) beginGaCalc();

    bumpAgSimSeq();
    bumpDashboardSeq();
    setConfigSaving((c) => c + 1);

    try {
      const res = await apiCommand({
        command: "update_config",
        config: payload,
        include_action_graph: true,
      });
      bumpDashboardSeq();
      setConfigDirty(false);
      setAgSimDirty(false);
      if (res.config && typeof res.config === "object") {
        populateControls(res.config);
        const curDash = getSnapshot().dashboard || dashboard;
        const nextDash = curDash
          ? {
              ...curDash,
              config: res.config,
              planner_mode: res.config.planner_mode || curDash.planner_mode,
              effective_planner_mode:
                res.effective_planner_mode ??
                res.config.planner_mode ??
                curDash.effective_planner_mode,
              fallback_reason: res.fallback_reason ?? null,
              live_ai_unlocked: res.live_ai_unlocked ?? curDash.live_ai_unlocked,
              action_graph: res.action_graph || curDash.action_graph,
            }
          : {
              config: res.config,
              planner_mode: res.config.planner_mode || "priority",
              effective_planner_mode:
                res.effective_planner_mode ?? res.config.planner_mode ?? "priority",
              fallback_reason: res.fallback_reason ?? null,
              live_ai_unlocked: res.live_ai_unlocked,
              action_graph: res.action_graph || null,
            };
        setDashboard(nextDash);
        bumpConfigHydration();
      }
      if (res.action_graph && typeof res.action_graph === "object") {
        setActionGraphData(res.action_graph);
      }
      showFeedback("Applied parameters to active StrategyConfig (config.yaml saved).", "ok");
      await loadDashboard({ refresh: false, preserveConfig: false, force: true });
    } catch (err: any) {
      showFeedback(`Failed applying parameters: ${err.message}`, feedbackKindForError(err));
    } finally {
      setConfigSaving((c) => Math.max(0, c - 1));
      if (isAi) endGaCalc();
    }
  };

  const graph =
    actionGraphData || (dashboard ? buildClientFallbackActionGraph(dashboard) : null);
  const nodes: Array<Record<string, any>> = Array.isArray(graph?.nodes) ? graph!.nodes : [];
  const allEdges: Array<Record<string, any>> = Array.isArray(graph?.edges) ? graph!.edges : [];
  const edgeFilter = uiPrefs.agEdgeFilter || "all";
  const paramTab = uiPrefs.agParamTab || "core";
  const visibleEdges = allEdges.filter((e) => {
    if (edgeFilter !== "deps") return true;
    const types = Array.isArray(e?.types) ? e.types : [e?.primary_type];
    return types.some((t) =>
      [
        "priority_gate",
        "prerequisite",
        "level_chain",
        "energy_support",
        "throttle_control",
        "storage_cap",
        "resource_feed",
        "loot_feed",
      ].includes(String(t || ""))
    );
  });
  const summary = graph?.summary || {};
  const graphMode = normalizeUiPlannerMode(graph?.mode || draftValues.planner_mode);

  const totalNodes = Math.max(
    0,
    Math.round(safeFiniteNumber(summary.total_nodes, nodes.length) ?? nodes.length)
  );
  const readyCount = Math.max(
    0,
    Math.round(
      safeFiniteNumber(
        summary.ready_count,
        nodes.filter((n) => n.status === "ready").length
      ) ?? 0
    )
  );
  const waitingCount = Math.max(
    0,
    Math.round(
      safeFiniteNumber(
        summary.waiting_count,
        nodes.filter((n) => n.status === "waiting_resources").length
      ) ?? 0
    )
  );
  const blockedCount = Math.max(
    0,
    Math.round(
      safeFiniteNumber(
        summary.blocked_count,
        nodes.filter((n) => n.status === "blocked_gate").length
      ) ?? 0
    )
  );

  const byEdgeType = summary.by_edge_type || {};
  const prereqCount =
    Math.round(safeFiniteNumber(byEdgeType.prerequisite, 0) ?? 0) +
    Math.round(safeFiniteNumber(byEdgeType.level_chain, 0) ?? 0) +
    Math.round(safeFiniteNumber(byEdgeType.priority_gate, 0) ?? 0);
  const resCount =
    Math.round(safeFiniteNumber(byEdgeType.energy_support, 0) ?? 0) +
    Math.round(safeFiniteNumber(byEdgeType.throttle_control, 0) ?? 0) +
    Math.round(safeFiniteNumber(byEdgeType.storage_cap, 0) ?? 0) +
    Math.round(safeFiniteNumber(byEdgeType.resource_feed, 0) ?? 0) +
    Math.round(safeFiniteNumber(byEdgeType.loot_feed, 0) ?? 0);

  const gate = computeColonyGateInfo(dashboard, plannerJobsStatus);
  const engineTitle =
    graphMode === "ai_planner" ? "AI-PLANNER · W-WEEK NPV" : "HEURISTIC · P0–P3";
  const engineSub = sanitizeMainWindowText(
    `${Boolean(graph?.is_dirty ?? graph?.simulated) ? "Simulated" : "Active"} · ${String(
      graph?.effective_config?.playstyle || "fleeter"
    )} · Colonies ${gate.coloniesOwned}/${gate.maxPlanets}`
  );

  const draftModeNorm = normalizeUiPlannerMode(draftValues.planner_mode);
  const knownPlaystyles = ["fleeter", "hybrid", "miner"];
  const curPlaystyle = String(draftValues.playstyle || "fleeter");

  return (
    <div className="ag-tab-split" id="ag-pane-heuristics" hidden={hidden}>
      <article className="hud-window ag-main-window" id="ag-main-window">
        <div className="window-bar window-bar-wrap">
          <div className="window-title-group">
            <span className="module-code tabular">DAG</span>
            <h2>Heuristics Action Sequence &amp; Dependency Graph</h2>
            <span
              className={`ag-status-pill tabular ${agSimDirty ? "is-sim" : "is-live"}`}
              id="ag-sim-status-pill"
              role="status"
            >
              {agSimDirty ? "SIMULATION PREVIEW · UNSAVED" : "LIVE ACTIVE CONFIG"}
            </span>
          </div>

          <div className="inline-actions">
            <div
              className="tab-group"
              id="ag-mode-switch"
              role="radiogroup"
              aria-label="Action graph planner mode"
            >
              <button
                type="button"
                className={`tab-btn${graphMode === "priority" ? " active" : ""}`}
                id="ag-mode-priority"
                role="radio"
                aria-checked={graphMode === "priority" ? "true" : "false"}
                data-ag-mode="priority"
                onClick={() => onModeSwitchClick("priority")}
              >
                Heuristic (priority)
              </button>
              <button
                type="button"
                className={`tab-btn${graphMode === "ai_planner" ? " active" : ""}`}
                id="ag-mode-ai_planner"
                role="radio"
                aria-checked={graphMode === "ai_planner" ? "true" : "false"}
                data-ag-mode="ai_planner"
                onClick={() => onModeSwitchClick("ai_planner")}
              >
                AI-Planner (ai_planner)
              </button>
            </div>
            <button
              type="button"
              className="btn btn-secondary btn-sm"
              id="btn-ag-refresh"
              onClick={async () => {
                if (agSimDirty) {
                  await runAgSimulation({ explicit: true });
                } else {
                  const ok = await refreshActiveActionGraph(false);
                  if (ok) {
                    showFeedback("Action graph refreshed from empire snapshot.", "ok");
                  }
                }
              }}
            >
              Refresh
            </button>
          </div>
        </div>

        <div className="ag-kpi-strip" aria-label="Action graph summary metrics">
          <div className="ag-kpi-cell">
            <span className="ag-kpi-label">Planned Actions</span>
            <strong className="ag-kpi-val tabular" id="ag-kpi-nodes">
              {`${totalNodes} ${totalNodes === 1 ? "node" : "nodes"}`}
            </strong>
            <span className="ag-kpi-sub tabular" id="ag-kpi-ready">
              {blockedCount > 0
                ? `${readyCount} ready · ${waitingCount} wait · ${blockedCount} hold`
                : `${readyCount} ready · ${waitingCount} waiting`}
            </span>
          </div>
          <div className="ag-kpi-cell">
            <span className="ag-kpi-label">Dependencies &amp; Flow</span>
            <strong className="ag-kpi-val tabular" id="ag-kpi-edges">
              {`${visibleEdges.length} ${visibleEdges.length === 1 ? "edge" : "edges"}`}
            </strong>
            <span className="ag-kpi-sub tabular" id="ag-kpi-dep-breakdown">
              {`${prereqCount} prereq · ${resCount} resource`}
            </span>
          </div>
          <div className="ag-kpi-cell">
            <span className="ag-kpi-label">Critical Path ETA</span>
            <strong className="ag-kpi-val tabular" id="ag-kpi-horizon">
              {sanitizeMainWindowText(summary.critical_path_eta || "Ready")}
            </strong>
            <span className="ag-kpi-sub tabular" id="ag-kpi-bottleneck">
              {summary.primary_bottleneck
                ? `Bottleneck: ${String(summary.primary_bottleneck).replace(/_/g, " ")}`
                : "No resource bottleneck"}
            </span>
          </div>
          <div className="ag-kpi-cell">
            <span className="ag-kpi-label">Engine Telemetry</span>
            <strong className="ag-kpi-val tabular" id="ag-kpi-engine">
              {engineTitle}
            </strong>
            <span className="ag-kpi-sub tabular" id="ag-kpi-engine-sub">
              {engineSub}
            </span>
          </div>
        </div>

        <DagCanvas
          graph={graph}
          idPrefix="ag"
          markerPrefix="ag"
          edgeFilter={edgeFilter}
          onEdgeFilterChange={(next) => setUiPref("agEdgeFilter", next)}
        />
      </article>

      <HeuristicsParamPanel
        agSimDirty={agSimDirty}
        paramTab={paramTab}
        draftValues={draftValues}
        gate={gate}
        onSwitchTab={onSwitchTab}
        onSelectParamTab={(tab) => {
          syncDraftValuesFromDom();
          setUiPref("agParamTab", tab);
        }}
        onDraftChange={(key, val) => setDraftValues((prev) => ({ ...prev, [key]: val }))}
        onModeSelectChange={(mode) => {
          syncDraftValuesFromDom(mode);
          void runAgSimulation({ explicit: false, modeOverride: mode });
        }}
        onImmediateSim={() => {
          setTimeout(() => runAgSimulation({ explicit: false }), 0);
        }}
        onDebouncedSim={scheduleDebouncedSim}
        onSimulateExplicit={() => {
          void runAgSimulation({ explicit: true });
        }}
        onResetSim={() => {
          void onResetSim();
        }}
        onApplySim={() => {
          void onApplySim();
        }}
      />
    </div>
  );
};

export default HeuristicsTab;
