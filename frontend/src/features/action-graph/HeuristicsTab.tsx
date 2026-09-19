import React, { useCallback, useEffect, useLayoutEffect, useRef, useState } from "react";
import { feedbackKindForError, safeFiniteNumber } from "../../api/httpClient";
import { buildClientFallbackActionGraph, useConsole } from "../../context/ConsoleContext";
import { ActionGraphTabKey, StrategyConfigData } from "../../types/api";
import { ActionGraphParamRow } from "./ActionGraphParamRow";
import { DagCanvas } from "./DagCanvas";
import { getStrategyParamDoc } from "./paramDocs";
import { computeColonyGateInfo, normalizeUiPlannerMode, sanitizeMainWindowText } from "./plannerHelpers";

const useClientSyncEffect = typeof window !== "undefined" ? useLayoutEffect : useEffect;

const AG_BOOL_FIELDS = [
  "auto_throttle_blast_furnace",
  "merchant_mode",
  "allow_research",
  "allow_shipyard",
  "allow_multi_planet",
  "allow_fleetsave",
  "allow_galaxy_scan",
  "allow_raids",
  "allow_pvp_in_az",
  "allow_colonize",
  "allow_transport",
  "allow_transmitter",
  "forbid_planetary_solar",
];

export const AG_NUM_DEFAULTS: Record<string, number> = {
  storage_warn_ratio: 0.85,
  blast_pig_iron_max_share: 0.7,
  combat_safety_margin: 1.4,
  raid_min_loot_spice_eq: 1500,
  hot_planet_solar_temp_threshold: 40,
  min_free_fields_warning: 20,
  min_colony_fields: 300,
  max_planets: 11,
  galaxy_sweep_interval_seconds: 270,
  galaxy_sweep_pages: 100,
  galaxy_scan_click_delay_seconds: 1.5,
  galaxy_scan_click_jitter_seconds: 0.6,
  reserved_spy_slots: 1,
  fleet_save_fuel_reserve_spice: 500,
  action_batch_interval_seconds: 120,
  max_actions_per_hour: 60,
};

function buildDraftStateFromConfig(cfg: StrategyConfigData | null | undefined): Record<string, any> {
  const out: Record<string, any> = {
    planner_mode: normalizeUiPlannerMode(cfg?.planner_mode),
    playstyle: String(cfg?.playstyle || "fleeter"),
  };
  for (const [k, fallback] of Object.entries(AG_NUM_DEFAULTS)) {
    const v = safeFiniteNumber(cfg?.[k], fallback) ?? fallback;
    out[k] = String(v);
  }
  for (const bKey of AG_BOOL_FIELDS) {
    const defChecked =
      bKey === "auto_throttle_blast_furnace" ||
      bKey === "allow_research" ||
      bKey === "allow_shipyard" ||
      bKey === "allow_multi_planet";
    out[bKey] = cfg && bKey in cfg ? Boolean(cfg[bKey]) : defChecked;
  }
  return out;
}

export interface HeuristicsTabProps {
  hidden?: boolean;
  onSwitchTab?: (tab: ActionGraphTabKey) => void;
}

export const HeuristicsTab: React.FC<HeuristicsTabProps> = ({ hidden = false, onSwitchTab }) => {
  const {
    dashboard,
    setDashboard,
    apiOnline,
    actionGraphData,
    setActionGraphData,
    plannerJobsStatus,
    agSimDirty,
    setAgSimDirty,
    setAgSimRunning,
    agHydrationTick,
    setConfigDirty,
    setConfigSaving,
    bumpDashboardSeq,
    beginGaCalc,
    endGaCalc,
    uiPrefs,
    setUiPref,
    apiCommand,
    loadDashboard,
    showFeedback,
  } = useConsole();

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
    const gState = (globalThis as any).state;
    if (
      !gState?.agSimDirty &&
      !(gState?.agSimRunning > 0) &&
      !(gState?.configSaving > 0) &&
      dashboard?.config
    ) {
      populateControls(dashboard.config);
    }
  }, [agHydrationTick, dashboard?.config, populateControls]);

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
    (modeOverride?: "priority" | "ai_planner"): Record<string, any> => {
      const readEl = (id: string) =>
        (typeof document !== "undefined" ? document.getElementById(id) : null) as
          | HTMLInputElement
          | HTMLSelectElement
          | null;

      const modeSel = readEl("ag-cfg-planner_mode");
      const playSel = readEl("ag-cfg-playstyle");
      const payload: Record<string, any> = {
        planner_mode:
          modeOverride ??
          normalizeUiPlannerMode(modeSel ? modeSel.value : draftValues.planner_mode),
        playstyle: playSel
          ? String(playSel.value || "fleeter")
          : String(draftValues.playstyle || "fleeter"),
      };

      for (const boolField of AG_BOOL_FIELDS) {
        const box = readEl(`ag-cfg-${boolField}`) as HTMLInputElement | null;
        payload[boolField] = box ? Boolean(box.checked) : Boolean(draftValues[boolField]);
      }

      for (const numField of Object.keys(AG_NUM_DEFAULTS)) {
        const inp = readEl(`ag-cfg-${numField}`);
        const raw = inp ? String(inp.value ?? "").trim() : String(draftValues[numField] ?? "").trim();
        if (raw === "") {
          payload[numField] = "";
        } else if (/^[+-]?(\d+\.?\d*|\.\d+)([eE][+-]?\d+)?$/.test(raw)) {
          const parsed = Number(raw);
          payload[numField] = Number.isFinite(parsed) ? parsed : raw;
        } else {
          payload[numField] = raw;
        }
      }

      return payload;
    },
    [draftValues]
  );

  const refreshActiveActionGraph = useCallback(
    async (forceRefresh = false): Promise<boolean> => {
      const gState = (globalThis as any).state;
      const reqSeq = gState ? ++gState.agSimSeq : 1;
      try {
        const res = await apiCommand({
          command: "action_graph",
          simulated: false,
          refresh: Boolean(forceRefresh),
        });
        if (gState && (reqSeq !== gState.agSimSeq || gState.agSimDirty)) return false;
        if (res.action_graph && typeof res.action_graph === "object") {
          setActionGraphData(res.action_graph);
          const curDash = gState?.dashboard || dashboard;
          if (curDash) {
            const nextDash = { ...curDash, action_graph: res.action_graph };
            if (gState) gState.dashboard = nextDash;
            setDashboard(nextDash);
          }
          return true;
        }
      } catch (err: any) {
        if (!gState || reqSeq === gState.agSimSeq) {
          showFeedback(`Action graph load failed: ${err.message}`, feedbackKindForError(err));
        }
      }
      return false;
    },
    [apiCommand, dashboard, setActionGraphData, setDashboard, showFeedback]
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

      const gState = (globalThis as any).state;
      const reqSeq = gState ? ++gState.agSimSeq : 1;
      if (gState) gState.agSimRunning = (gState.agSimRunning || 0) + 1;
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
        if (gState && reqSeq !== gState.agSimSeq) return false;
        if (res.action_graph && typeof res.action_graph === "object") {
          const isDirty = Boolean(res.action_graph.is_dirty);
          setActionGraphData(res.action_graph);
          const curDash = gState?.dashboard || dashboard;
          if (!isDirty && curDash) {
            const nextDash = { ...curDash, action_graph: res.action_graph };
            if (gState) gState.dashboard = nextDash;
            setDashboard(nextDash);
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
        if (!gState || reqSeq === gState.agSimSeq) {
          showFeedback(`Simulation preview error: ${err.message}`, feedbackKindForError(err));
        }
        return false;
      } finally {
        if (gState) gState.agSimRunning = Math.max(0, (gState.agSimRunning || 0) - 1);
        setAgSimRunning((c) => Math.max(0, c - 1));
        if (useHud) endGaCalc();
      }
    },
    [
      apiCommand,
      beginGaCalc,
      collectDraftPayload,
      dashboard,
      endGaCalc,
      setActionGraphData,
      setAgSimDirty,
      setAgSimRunning,
      setDashboard,
      showFeedback,
      syncDraftValuesFromDom,
    ]
  );

  useClientSyncEffect(() => {
    (globalThis as any).runAgSimulation = runAgSimulation;
    (globalThis as any).populateAgParamControls = populateControls;
  }, [populateControls, runAgSimulation]);

  const scheduleDebouncedSim = useCallback(() => {
    const gState = (globalThis as any).state;
    if (gState) gState.agSimSeq = (gState.agSimSeq || 0) + 1;
    setAgSimDirty(true);
    if (debounceRef.current) {
      clearTimeout(debounceRef.current);
    }
    debounceRef.current = setTimeout(() => {
      debounceRef.current = null;
      runAgSimulation({ explicit: false });
    }, 180);
  }, [runAgSimulation, setAgSimDirty]);

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
    const gState = (globalThis as any).state;
    if (gState) gState.agSimSeq = (gState.agSimSeq || 0) + 1;
    setAgSimDirty(false);
    const activeCfg = (gState?.dashboard || dashboard)?.config || null;
    if (activeCfg) {
      populateControls(activeCfg);
    }
    const curDash = gState?.dashboard || dashboard;
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

    const gState = (globalThis as any).state;
    if (gState) {
      gState.agSimSeq = (gState.agSimSeq || 0) + 1;
      gState.configSaving = (gState.configSaving || 0) + 1;
    }
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
        (globalThis as any).renderConfigForm?.(res.config);
        const curDash = gState?.dashboard || dashboard;
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
        if (gState) gState.dashboard = nextDash;
        setDashboard(nextDash);
      }
      if (res.action_graph && typeof res.action_graph === "object") {
        setActionGraphData(res.action_graph);
      }
      showFeedback("Applied parameters to active StrategyConfig (config.yaml saved).", "ok");
      await loadDashboard({ refresh: false, preserveConfig: false, force: true });
    } catch (err: any) {
      showFeedback(`Failed applying parameters: ${err.message}`, feedbackKindForError(err));
    } finally {
      if (gState) gState.configSaving = Math.max(0, (gState.configSaving || 0) - 1);
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

      <aside
        className="hud-window ag-control-panel"
        id="ag-control-panel"
        aria-label="Planner parameter simulation and apply controls"
      >
        <div className="window-bar window-bar-wrap">
          <div className="window-title-group">
            <span className="module-code tabular">PRM</span>
            <h2>Heuristic Parameters</h2>
          </div>
          <span
            className={`ag-dirty-badge tabular ${agSimDirty ? "is-dirty" : "is-synced"}`}
            id="ag-dirty-badge"
          >
            {agSimDirty ? "MODIFIED (PREVIEW)" : "ACTIVE SYNCED"}
          </span>
        </div>

        <div className="ag-panel-tabs" role="tablist" aria-label="Parameter categories">
          <button
            type="button"
            className={`tab-btn${paramTab === "core" ? " active" : ""}`}
            role="tab"
            aria-selected={paramTab === "core" ? "true" : "false"}
            id="ag-ptab-btn-core"
            data-ag-ptab="core"
            onClick={() => {
              syncDraftValuesFromDom();
              setUiPref("agParamTab", "core");
            }}
          >
            Mode &amp; Thresholds
          </button>
          <button
            type="button"
            className={`tab-btn${paramTab === "scope" ? " active" : ""}`}
            role="tab"
            aria-selected={paramTab === "scope" ? "true" : "false"}
            id="ag-ptab-btn-scope"
            data-ag-ptab="scope"
            onClick={() => {
              syncDraftValuesFromDom();
              setUiPref("agParamTab", "scope");
            }}
          >
            Automation &amp; Limits
          </button>
        </div>

        <div className="ag-panel-body">
          <div className="ag-param-pane" id="ag-param-pane-core" hidden={paramTab !== "core"}>
            <div className="ag-param-group">
              <div className="ag-param-group-head">
                <h3>Planner Mode &amp; Doctrine</h3>
                <p className="ag-param-group-sub">
                  Choose the active planner engine and strategic doctrine used to preview and execute action sequences.
                </p>
              </div>
              <div className="ag-param-list">
                <ActionGraphParamRow
                  doc={getStrategyParamDoc("planner_mode")}
                  htmlFor="ag-cfg-planner_mode"
                  layout="stack"
                >
                  <select
                    id="ag-cfg-planner_mode"
                    value={draftModeNorm}
                    onChange={(e) => {
                      const v = normalizeUiPlannerMode(e.target.value);
                      syncDraftValuesFromDom(v);
                      void runAgSimulation({ explicit: false, modeOverride: v });
                    }}
                  >
                    <option value="priority">priority — Rule-Based Heuristic</option>
                    <option value="ai_planner">ai_planner — AI-Planner (W-Week NPV)</option>
                  </select>
                </ActionGraphParamRow>

                <ActionGraphParamRow
                  doc={getStrategyParamDoc("playstyle")}
                  htmlFor="ag-cfg-playstyle"
                  layout="stack"
                >
                  <select
                    id="ag-cfg-playstyle"
                    value={curPlaystyle}
                    onChange={(e) => {
                      const v = e.target.value;
                      setDraftValues((prev) => ({ ...prev, playstyle: v }));
                      setTimeout(() => runAgSimulation({ explicit: false }), 0);
                    }}
                  >
                    <option value="fleeter">fleeter — Combat &amp; Techs</option>
                    <option value="hybrid">hybrid — Balanced</option>
                    <option value="miner">miner — Economy ROI</option>
                    {!knownPlaystyles.includes(curPlaystyle) && curPlaystyle ? (
                      <option value={curPlaystyle}>{curPlaystyle}</option>
                    ) : null}
                  </select>
                </ActionGraphParamRow>
              </div>
            </div>

            <div className="ag-ai-handoff-card" id="ag-ai-handoff-card">
              <div className="ag-ai-handoff-head">
                <span className="ag-handoff-title">AI-Planner Workspace</span>
                <span
                  className={`ag-gate-chip tabular ${gate.liveAiUnlocked ? "is-unlocked" : "is-locked"}`}
                >
                  {gate.liveAiUnlocked
                    ? `${gate.coloniesOwned}/${gate.maxPlanets} UNLOCKED`
                    : `${gate.coloniesOwned}/${gate.maxPlanets} PREVIEW ONLY`}
                </span>
              </div>
              <p className="ag-sim-note">
                {gate.liveAiUnlocked
                  ? "All colonies founded. Configure monthly targets and W-week discounted builds in Tab 2 & Tab 3."
                  : `Live AI-Planner unlocks at ${gate.maxPlanets}/${gate.maxPlanets} colonies. Heuristic mode guides early expansion.`}
              </p>
              {onSwitchTab ? (
                <div className="ag-handoff-actions">
                  <button
                    type="button"
                    className="btn btn-secondary btn-sm"
                    id="btn-ag-goto-config-opt"
                    onClick={() => onSwitchTab("config-optimizer")}
                  >
                    Config Optimization →
                  </button>
                  <button
                    type="button"
                    className="btn btn-secondary btn-sm"
                    id="btn-ag-goto-ai-planner"
                    onClick={() => onSwitchTab("ai-planner")}
                  >
                    Open AI-Planner →
                  </button>
                </div>
              ) : null}
            </div>

            <div className="ag-param-group" id="ag-heuristic-params-group">
              <div className="ag-param-group-head">
                <h3>Economy &amp; Safety Thresholds</h3>
                <p className="ag-param-group-sub">
                  Resource warehouse thresholds, Blast furnace throttling, combat margins, and colony field constraints.
                </p>
              </div>
              <div className="ag-param-list">
                {(
                  [
                    ["storage_warn_ratio", 0.1, 0.99, 0.05],
                    ["blast_pig_iron_max_share", 0.1, 1.0, 0.05],
                    ["combat_safety_margin", 1.0, 1000.0, 0.1],
                    ["raid_min_loot_spice_eq", 0, undefined, 100],
                    ["hot_planet_solar_temp_threshold", -273, 1000, 5],
                    ["min_free_fields_warning", 1, 1000, 1],
                    ["min_colony_fields", 50, 600, 10],
                    ["max_planets", 1, 50, 1],
                  ] as const
                ).map(([key, minV, maxV, stepV]) => (
                  <ActionGraphParamRow
                    key={key}
                    doc={getStrategyParamDoc(key)}
                    htmlFor={`ag-cfg-${key}`}
                    layout="inline"
                  >
                    <input
                      type="number"
                      id={`ag-cfg-${key}`}
                      min={minV}
                      max={maxV}
                      step={stepV}
                      value={String(draftValues[key] ?? AG_NUM_DEFAULTS[key])}
                      onChange={(e) => {
                        setDraftValues((prev) => ({ ...prev, [key]: e.target.value }));
                        scheduleDebouncedSim();
                      }}
                    />
                  </ActionGraphParamRow>
                ))}
              </div>
            </div>
          </div>

          <div className="ag-param-pane" id="ag-param-pane-scope" hidden={paramTab !== "scope"}>
            <div className="ag-param-group">
              <div className="ag-param-group-head">
                <h3>Pacing, Reserves &amp; Limits</h3>
                <p className="ag-param-group-sub">
                  Batch refresh cadence, galaxy scanner humanization delays, hourly action rate limit, and spy/escape reserves.
                </p>
              </div>
              <div className="ag-param-list">
                {(
                  [
                    ["fleet_save_fuel_reserve_spice", 0, undefined, 50],
                    ["action_batch_interval_seconds", 5, 86400, 5],
                    ["galaxy_scan_click_delay_seconds", 0.2, 60, 0.1],
                    ["galaxy_scan_click_jitter_seconds", 0, 30, 0.1],
                    ["max_actions_per_hour", 1, 100000, 1],
                    ["reserved_spy_slots", 0, 10, 1],
                  ] as const
                ).map(([key, minV, maxV, stepV]) => (
                  <ActionGraphParamRow
                    key={key}
                    doc={getStrategyParamDoc(key)}
                    htmlFor={`ag-cfg-${key}`}
                    layout="inline"
                  >
                    <input
                      type="number"
                      id={`ag-cfg-${key}`}
                      min={minV}
                      max={maxV}
                      step={stepV}
                      value={String(draftValues[key] ?? AG_NUM_DEFAULTS[key])}
                      onChange={(e) => {
                        setDraftValues((prev) => ({ ...prev, [key]: e.target.value }));
                        scheduleDebouncedSim();
                      }}
                    />
                  </ActionGraphParamRow>
                ))}
              </div>
            </div>

            <div className="ag-param-group">
              <div className="ag-param-group-head">
                <h3>Automation Capabilities</h3>
                <p className="ag-param-group-sub">
                  Master switches controlling which autonomous build, research, scanning, combat, and logistics systems may run.
                </p>
              </div>
              <div className="ag-param-list">
                {AG_BOOL_FIELDS.map((bKey) => (
                  <ActionGraphParamRow
                    key={bKey}
                    doc={getStrategyParamDoc(bKey)}
                    htmlFor={`ag-cfg-${bKey}`}
                    layout="toggle"
                  >
                    <input
                      type="checkbox"
                      className="config-checkbox"
                      id={`ag-cfg-${bKey}`}
                      checked={Boolean(draftValues[bKey])}
                      onChange={(e) => {
                        const checked = e.target.checked;
                        setDraftValues((prev) => ({ ...prev, [bKey]: checked }));
                        setTimeout(() => runAgSimulation({ explicit: false }), 0);
                      }}
                    />
                  </ActionGraphParamRow>
                ))}
              </div>
            </div>
          </div>
        </div>

        <div className="ag-panel-footer">
          <p className="ag-sim-note" id="ag-sim-note">
            {agSimDirty
              ? "Preview mode active: active StrategyConfig and config.yaml remain unchanged until applied."
              : "Edits simulate a preview graph without modifying active config.yaml until applied."}
          </p>
          <div className="ag-footer-actions">
            <button
              type="button"
              className="btn btn-secondary btn-sm"
              id="btn-ag-simulate"
              onClick={() => runAgSimulation({ explicit: true })}
            >
              Simulate Graph
            </button>
            <button
              type="button"
              className="btn btn-secondary btn-sm"
              id="btn-ag-reset"
              onClick={onResetSim}
            >
              Reset to Active
            </button>
            <button
              type="button"
              className="btn btn-primary btn-sm btn-span"
              id="btn-ag-apply"
              onClick={onApplySim}
            >
              Apply to Active Config
            </button>
          </div>
        </div>
      </aside>
    </div>
  );
};

export default HeuristicsTab;
