import React, { useCallback, useEffect, useLayoutEffect, useRef, useState } from "react";

const useClientSyncEffect = typeof window !== "undefined" ? useLayoutEffect : useEffect;
import { feedbackKindForError, safeFiniteNumber } from "../../api/httpClient";
import { fmtImprovementPct } from "../../components/layout/GaNnScannerHud";
import {
  buildClientFallbackActionGraph,
  useConsole,
} from "../../context/ConsoleContext";
import { useDragScroll } from "../../hooks/useDragScroll";
import { StrategyConfigData } from "../../types/api";

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
  "allow_transmitter",
  "forbid_planetary_solar",
];

export const AG_NUM_DEFAULTS: Record<string, number> = {
  ga_population_size: 24,
  ga_generations: 12,
  ga_horizon_steps: 6,
  ga_mutation_rate: 0.25,
  ga_nn_weight: 0.35,
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

const AG_SEMANTIC_EDGE_TYPES = new Set([
  "priority_gate",
  "prerequisite",
  "level_chain",
  "energy_support",
  "throttle_control",
  "storage_cap",
  "resource_feed",
  "loot_feed",
]);

function sanitizeMainWindowText(rawText: unknown): string {
  return String(rawText ?? "").replace(/generations?/gi, "cycles");
}

function edgeVisualSpec(edge: Record<string, any>): { cls: string; marker: string } {
  const pType = String(edge?.primary_type || "execution_order");
  if (pType === "priority_gate") {
    return { cls: "is-gate", marker: "ag-arrow-gate" };
  }
  if (pType === "prerequisite" || pType === "level_chain") {
    return { cls: "is-prereq", marker: "ag-arrow-prereq" };
  }
  if (
    pType === "energy_support" ||
    pType === "throttle_control" ||
    pType === "storage_cap" ||
    pType === "resource_feed" ||
    pType === "loot_feed"
  ) {
    return { cls: "is-resource", marker: "ag-arrow-resource" };
  }
  if (pType === "queue_order") {
    return { cls: "is-queue", marker: "ag-arrow-queue" };
  }
  return { cls: "is-seq", marker: "ag-arrow-seq" };
}

function edgeMatchesFilter(edge: Record<string, any>, filterMode: string): boolean {
  if (filterMode !== "deps") return true;
  const types = Array.isArray(edge?.types) ? edge.types : [edge?.primary_type];
  return types.some((t) => AG_SEMANTIC_EDGE_TYPES.has(String(t || "")));
}

function formatNodeTargetLabel(node: Record<string, any>): string {
  const base = String(node?.target_name || node?.kind || "Action");
  const lvl = safeFiniteNumber(node?.target_level, null);
  const kind = String(node?.kind || "");
  if (lvl === null) return base;
  if (kind === "THROTTLE_BUILDING") {
    return `${base} → ${Math.round(lvl)}%`;
  }
  if (kind === "BUILD_SHIP") {
    if (/\(x\d+\)$/i.test(base)) return base;
    return `${base} (x${Math.max(1, Math.round(lvl))})`;
  }
  if (kind === "BUILD" || kind === "WAIT_RESOURCES" || kind === "RESEARCH") {
    return `${base} L${Math.round(lvl)}`;
  }
  if (kind === "DEMOLISH_BUILDING") {
    return `Demolish ${base} → L${Math.round(lvl)}`;
  }
  return base;
}

function formatNodeStatusShort(status: string): string {
  if (status === "ready") return "READY";
  if (status === "waiting_resources") return "WAIT";
  if (status === "blocked_gate") return "HOLD";
  return "QUEUE";
}

function buildDraftStateFromConfig(cfg: StrategyConfigData | null | undefined): Record<string, any> {
  const out: Record<string, any> = {
    planner_mode:
      String(cfg?.planner_mode || "priority").toLowerCase() === "ga_nn" ? "ga_nn" : "priority",
    playstyle: String(cfg?.playstyle || "fleeter"),
    ga_seed: cfg?.ga_seed != null ? String(cfg.ga_seed) : "",
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

export const ActionGraphView: React.FC = () => {
  const {
    dashboard,
    setDashboard,
    activePage,
    apiOnline,
    actionGraphData,
    setActionGraphData,
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

  const scrollRef = useRef<HTMLDivElement | null>(null);
  const debounceRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const {
    isDragging,
    consumeSuppressClick,
    onMouseDown,
    onMouseMove,
    onMouseUp,
    onMouseLeave,
  } = useDragScroll(scrollRef, 4);

  const [selectedNodeId, setSelectedNodeId] = useState<string | null>(null);
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
    (modeOverride?: "priority" | "ga_nn") => {
      const readEl = (id: string) =>
        (typeof document !== "undefined" ? document.getElementById(id) : null) as
          | HTMLInputElement
          | HTMLSelectElement
          | null;

      setDraftValues((prev) => {
        const modeSel = readEl("ag-cfg-planner_mode");
        const playSel = readEl("ag-cfg-playstyle");
        const seedInp = readEl("ag-cfg-ga_seed");
        const next: Record<string, any> = {
          ...prev,
          planner_mode:
            modeOverride ??
            (modeSel ? String(modeSel.value || "priority") : String(prev.planner_mode || "priority")),
          playstyle: playSel
            ? String(playSel.value || "fleeter")
            : String(prev.playstyle || "fleeter"),
          ga_seed: seedInp ? String(seedInp.value ?? "") : String(prev.ga_seed ?? ""),
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
    (modeOverride?: "priority" | "ga_nn"): Record<string, any> => {
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
          (modeSel
            ? String(modeSel.value || "priority")
            : String(draftValues.planner_mode || "priority")),
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

    const seedInp = readEl("ag-cfg-ga_seed");
    const rawSeed = seedInp
      ? String(seedInp.value ?? "").trim()
      : String(draftValues.ga_seed ?? "").trim();
    if (rawSeed === "") {
      payload.ga_seed = null;
    } else if (/^-?\d+$/.test(rawSeed)) {
      const parsedSeed = Number(rawSeed);
      payload.ga_seed = Number.isSafeInteger(parsedSeed) ? parsedSeed : rawSeed;
    } else {
      payload.ga_seed = rawSeed;
    }

    return payload;
  }, [draftValues]);

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
      modeOverride?: "priority" | "ga_nn";
    } = {}): Promise<boolean> => {
      if (debounceRef.current) {
        clearTimeout(debounceRef.current);
        debounceRef.current = null;
      }
      const draft = collectDraftPayload(modeOverride);
      const simMode: "priority" | "ga_nn" =
        String(draft.planner_mode || "priority").toLowerCase() === "ga_nn" ? "ga_nn" : "priority";
      syncDraftValuesFromDom(simMode);
      setAgSimDirty(true);

      const gState = (globalThis as any).state;
      const reqSeq = gState ? ++gState.agSimSeq : 1;
      if (gState) gState.agSimRunning = (gState.agSimRunning || 0) + 1;
      setAgSimRunning((c) => c + 1);

      const useGaHud = simMode === "ga_nn";
      if (useGaHud) beginGaCalc();

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
        if (useGaHud) endGaCalc();
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

  // Expose runAgSimulation & populateAgParamControls on globalThis for existing unit tests
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

  const onModeSwitchClick = (targetMode: "priority" | "ga_nn") => {
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
    const isGa = String(payload.planner_mode || "").toLowerCase() === "ga_nn";
    if (isGa) beginGaCalc();

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
              action_graph: res.action_graph || curDash.action_graph,
            }
          : {
              config: res.config,
              planner_mode: res.config.planner_mode || "priority",
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
      if (isGa) endGaCalc();
    }
  };

  const graph =
    actionGraphData || (dashboard ? buildClientFallbackActionGraph(dashboard) : null);
  const nodes: Array<Record<string, any>> = Array.isArray(graph?.nodes) ? graph!.nodes : [];
  const allEdges: Array<Record<string, any>> = Array.isArray(graph?.edges) ? graph!.edges : [];
  const edgeFilter = uiPrefs.agEdgeFilter || "all";
  const paramTab = uiPrefs.agParamTab || "core";
  const visibleEdges = allEdges.filter((e) => edgeMatchesFilter(e, edgeFilter));
  const summary = graph?.summary || {};
  const graphMode =
    String(graph?.mode || draftValues.planner_mode || "priority").toLowerCase() === "ga_nn"
      ? "ga_nn"
      : "priority";

  const activeSelectedId =
    selectedNodeId && nodes.some((n) => n.id === selectedNodeId) ? selectedNodeId : null;

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

  const gaSum = graph?.ga_summary;
  const engineTitle =
    graphMode === "ga_nn" && gaSum && typeof gaSum === "object"
      ? sanitizeMainWindowText(
          `GA+NN · ${fmtImprovementPct(
            gaSum.improvement_pct,
            safeFiniteNumber(gaSum.baseline_fitness ?? gaSum.initial_best_fitness, null),
            safeFiniteNumber(gaSum.best_fitness ?? gaSum.final_best_fitness, null)
          )}`
        )
      : graphMode === "ga_nn"
        ? "GA+NN · EVOLUTIONARY"
        : "PRIORITY · P0–P3";

  const engineSub =
    graphMode === "ga_nn" && gaSum && typeof gaSum === "object"
      ? `Pop ${Math.max(1, Math.round(safeFiniteNumber(gaSum.population_size, 24) ?? 24))} · ${Math.max(1, Math.round(safeFiniteNumber(gaSum.generations, 12) ?? 12))} cycles · ${Math.max(0, Math.round(safeFiniteNumber(gaSum.elapsed_ms, 0) ?? 0))}ms`
      : sanitizeMainWindowText(
          `${Boolean(graph?.is_dirty ?? graph?.simulated) ? "Simulated" : "Active"} · ${String(
            graph?.effective_config?.playstyle || "fleeter"
          )}`
        );

  // Deterministic coordinate layout
  const LANE_HEADER_W = 168;
  const PAD_X = 20;
  const PAD_Y = 14;
  const CARD_W = 228;
  const CARD_H = 92;
  const COL_GAP = 68;
  const STACK_GAP = 14;
  const LANE_PAD_Y = 16;

  const rawLanes = Array.isArray(graph?.lanes) ? graph!.lanes : [];
  const activeLanes: Array<Record<string, any>> = [];
  const laneIdxById = new Map<string, number>();
  rawLanes.forEach((ln) => {
    const lid = String(ln?.id || "");
    if (!lid) return;
    if (nodes.some((n) => n.lane_id === lid)) {
      laneIdxById.set(lid, activeLanes.length);
      activeLanes.push(ln);
    }
  });
  nodes.forEach((n) => {
    const lid = String(n.lane_id || "lane:global");
    if (!laneIdxById.has(lid)) {
      laneIdxById.set(lid, activeLanes.length);
      activeLanes.push({
        id: lid,
        scope: lid === "lane:global" ? "global" : "planet",
        label:
          lid === "lane:global"
            ? "Empire & Global Research"
            : n.planet_coords
              ? `${n.planet_name || "Colony"} [${n.planet_coords}]`
              : String(n.planet_name || lid),
        planet_id: safeFiniteNumber(n.planet_id, 0) ?? 0,
        coords: String(n.planet_coords || ""),
        energy: 0,
        node_count: 1,
      });
    }
  });

  const cellStacks = new Map<string, number>();
  const maxStackByLane = new Array<number>(activeLanes.length).fill(1);
  let maxCol = 0;
  const nodePlacement = new Map<string, { laneIdx: number; colIdx: number; stackIdx: number }>();

  nodes.forEach((n) => {
    const lIdx = laneIdxById.get(String(n.lane_id || "lane:global")) ?? 0;
    const colIdx = Math.max(0, Math.round(safeFiniteNumber(n.topological_layer, 0) ?? 0));
    if (colIdx > maxCol) maxCol = colIdx;
    const cellKey = `${lIdx}:${colIdx}`;
    const stackIdx = cellStacks.get(cellKey) || 0;
    cellStacks.set(cellKey, stackIdx + 1);
    if (stackIdx + 1 > (maxStackByLane[lIdx] || 1)) {
      maxStackByLane[lIdx] = stackIdx + 1;
    }
    nodePlacement.set(n.id, { laneIdx: lIdx, colIdx, stackIdx });
  });

  const laneTops: number[] = [];
  const laneHeights: number[] = [];
  let cursorY = PAD_Y;
  for (let i = 0; i < activeLanes.length; i += 1) {
    const stacks = maxStackByLane[i] || 1;
    const h = stacks * CARD_H + Math.max(0, stacks - 1) * STACK_GAP + LANE_PAD_Y * 2;
    laneTops.push(cursorY);
    laneHeights.push(h);
    cursorY += h;
  }

  const totalWidth = Math.max(
    680,
    LANE_HEADER_W + PAD_X * 2 + (maxCol + 1) * CARD_W + maxCol * COL_GAP + 24
  );
  const totalHeight = Math.max(240, cursorY + PAD_Y);

  const coordsById = new Map<string, { x: number; y: number; colIdx: number; laneIdx: number }>();
  nodes.forEach((node) => {
    const plc = nodePlacement.get(node.id) || { laneIdx: 0, colIdx: 0, stackIdx: 0 };
    const x = LANE_HEADER_W + PAD_X + plc.colIdx * (CARD_W + COL_GAP);
    const y = (laneTops[plc.laneIdx] || PAD_Y) + LANE_PAD_Y + plc.stackIdx * (CARD_H + STACK_GAP);
    coordsById.set(node.id, { x, y, colIdx: plc.colIdx, laneIdx: plc.laneIdx });
  });

  const upstreamIds = new Set<string>();
  const downstreamIds = new Set<string>();
  if (activeSelectedId) {
    allEdges.forEach((e) => {
      if (e.target === activeSelectedId) upstreamIds.add(e.source);
      if (e.source === activeSelectedId) downstreamIds.add(e.target);
    });
  }

  const selectedObj = activeSelectedId
    ? nodes.find((n) => n.id === activeSelectedId) || null
    : null;
  const nodesById = new Map(nodes.map((n) => [n.id, n]));
  const incomingSel = selectedObj ? allEdges.filter((e) => e.target === selectedObj.id) : [];
  const outgoingSel = selectedObj ? allEdges.filter((e) => e.source === selectedObj.id) : [];

  const draftModeNorm =
    String(draftValues.planner_mode || "priority").toLowerCase() === "ga_nn"
      ? "ga_nn"
      : "priority";

  const knownPlaystyles = ["fleeter", "hybrid", "miner"];
  const curPlaystyle = String(draftValues.playstyle || "fleeter");

  return (
    <section
      id="view-action-graph"
      className="page-view ag-view"
      aria-label="Action sequence and dependency graph"
      hidden={activePage !== "action-graph"}
    >
      <article className="hud-window ag-main-window" id="ag-main-window">
        <div className="window-bar window-bar-wrap">
          <div className="window-title-group">
            <span className="module-code tabular">DAG</span>
            <h2>Action Sequence &amp; Dependency Graph</h2>
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
                className={`tab-btn${graphMode === "ga_nn" ? " active" : ""}`}
                id="ag-mode-ga_nn"
                role="radio"
                aria-checked={graphMode === "ga_nn" ? "true" : "false"}
                data-ag-mode="ga_nn"
                onClick={() => onModeSwitchClick("ga_nn")}
              >
                GA + Neural (ga_nn)
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

        <div className="ag-legend-bar">
          <div className="ag-legend-items" aria-label="Graph edge legend">
            <span className="ag-legend-item">
              <span className="ag-swatch is-seq" aria-hidden="true" />
              Sequence Flow
            </span>
            <span className="ag-legend-item">
              <span className="ag-swatch is-prereq" aria-hidden="true" />
              Prerequisite Unlock
            </span>
            <span className="ag-legend-item">
              <span className="ag-swatch is-resource" aria-hidden="true" />
              Resource / Power / B2%
            </span>
            <span className="ag-legend-item">
              <span className="ag-swatch is-queue" aria-hidden="true" />
              Queue / Slot Order
            </span>
          </div>
          <div className="filter-chips">
            <button
              type="button"
              className={`chip-btn${edgeFilter === "all" ? " active" : ""}`}
              data-ag-edge-filter="all"
              onClick={() => setUiPref("agEdgeFilter", "all")}
            >
              All Links
            </button>
            <button
              type="button"
              className={`chip-btn${edgeFilter === "deps" ? " active" : ""}`}
              data-ag-edge-filter="deps"
              onClick={() => setUiPref("agEdgeFilter", "deps")}
            >
              Dependencies Only
            </button>
          </div>
        </div>

        <div
          ref={scrollRef}
          className={`ag-canvas-scroll${isDragging ? " is-dragging" : ""}`}
          id="ag-canvas-scroll"
          onMouseDown={onMouseDown}
          onMouseMove={onMouseMove}
          onMouseUp={onMouseUp}
          onMouseLeave={onMouseLeave}
        >
          <div
            className="ag-canvas-stage"
            id="ag-canvas-stage"
            hidden={nodes.length === 0}
            style={
              nodes.length > 0
                ? { width: `${totalWidth}px`, height: `${totalHeight}px` }
                : undefined
            }
          >
            <div
              className="ag-lanes-layer"
              id="ag-lanes-layer"
              aria-hidden="true"
              style={
                nodes.length > 0
                  ? { width: `${totalWidth}px`, height: `${totalHeight}px` }
                  : undefined
              }
            >
              {activeLanes.map((ln, idx) => {
                const laneNodeCnt = nodes.filter((n) => n.lane_id === ln.id).length;
                const eNet = Math.round(safeFiniteNumber(ln.energy, 0) ?? 0);
                const metaText =
                  ln.scope === "global"
                    ? `${laneNodeCnt} global ${laneNodeCnt === 1 ? "task" : "tasks"}`
                    : `${laneNodeCnt} ${laneNodeCnt === 1 ? "task" : "tasks"} · Energy ${eNet > 0 ? `+${eNet}` : String(eNet)}`;
                return (
                  <div
                    key={String(ln.id || idx)}
                    className={idx % 2 === 1 ? "ag-lane-band is-alt" : "ag-lane-band"}
                    data-lane-id={String(ln.id || "")}
                    style={{
                      top: `${laneTops[idx]}px`,
                      width: `${totalWidth}px`,
                      height: `${laneHeights[idx]}px`,
                    }}
                  >
                    <div className="ag-lane-header">
                      <span className="ag-lane-title">
                        {sanitizeMainWindowText(ln.label || ln.id || "Lane")}
                      </span>
                      <span className="ag-lane-meta tabular">{metaText}</span>
                    </div>
                  </div>
                );
              })}
            </div>

            <div
              className="ag-edges-layer"
              id="ag-edges-layer"
              aria-hidden="true"
              style={
                nodes.length > 0
                  ? { width: `${totalWidth}px`, height: `${totalHeight}px` }
                  : undefined
              }
            >
              {nodes.length > 0 ? (
                <svg
                  id="ag-edges-svg"
                  viewBox={`0 0 ${totalWidth} ${totalHeight}`}
                  width={totalWidth}
                  height={totalHeight}
                >
                  <defs>
                    {(
                      [
                        ["ag-arrow-seq", "#32e8ff"],
                        ["ag-arrow-prereq", "#16ec98"],
                        ["ag-arrow-resource", "#efb03b"],
                        ["ag-arrow-queue", "#9eb3b8"],
                        ["ag-arrow-gate", "#ff6873"],
                      ] as const
                    ).map(([mId, color]) => (
                      <marker
                        key={mId}
                        id={mId}
                        viewBox="0 0 10 10"
                        refX="9"
                        refY="5"
                        markerWidth="6"
                        markerHeight="6"
                        orient="auto-start-reverse"
                      >
                        <path d="M 0 1.5 L 9 5 L 0 8.5 z" fill={color} />
                      </marker>
                    ))}
                  </defs>
                  {visibleEdges.map((edge, idx) => {
                    const p1 = coordsById.get(edge.source);
                    const p2 = coordsById.get(edge.target);
                    if (!p1 || !p2) return null;
                    const x1 = p1.x + CARD_W;
                    const y1 = p1.y + CARD_H / 2;
                    const x2 = p2.x;
                    const y2 = p2.y + CARD_H / 2;
                    const dx = Math.max(36, Math.abs(x2 - x1) * 0.45);
                    const sameRowLongSpan = Math.abs(y2 - y1) < 6 && p2.colIdx - p1.colIdx > 1;
                    const arcLift = sameRowLongSpan ? -30 : 0;
                    const dStr = `M ${x1.toFixed(1)} ${y1.toFixed(1)} C ${(x1 + dx).toFixed(1)} ${(y1 + arcLift).toFixed(1)}, ${(x2 - dx).toFixed(1)} ${(y2 + arcLift).toFixed(1)}, ${x2.toFixed(1)} ${y2.toFixed(1)}`;
                    const spec = edgeVisualSpec(edge);
                    const connected = Boolean(
                      activeSelectedId &&
                        (edge.source === activeSelectedId || edge.target === activeSelectedId)
                    );
                    const dimmed = Boolean(activeSelectedId && !connected);
                    return (
                      <path
                        key={String(edge.id || idx)}
                        className={`ag-edge-path ${spec.cls}${connected ? " is-highlighted" : ""}${dimmed ? " is-dimmed" : ""}`}
                        d={dStr}
                        markerEnd={`url(#${spec.marker})`}
                        data-edge-id={String(edge.id || "")}
                        data-source={String(edge.source || "")}
                        data-target={String(edge.target || "")}
                        data-edge-type={String(edge.primary_type || "")}
                      />
                    );
                  })}
                </svg>
              ) : null}
            </div>

            <div
              className="ag-nodes-layer"
              id="ag-nodes-layer"
              role="list"
              aria-label="Planned action graph nodes"
              style={
                nodes.length > 0
                  ? { width: `${totalWidth}px`, height: `${totalHeight}px` }
                  : undefined
              }
            >
              {nodes.map((node) => {
                const pos = coordsById.get(node.id) || { x: 0, y: 0 };
                const status = String(node.status || "queued");
                const statusCls =
                  status === "ready"
                    ? "is-ready"
                    : status === "waiting_resources"
                      ? "is-waiting"
                      : status === "blocked_gate"
                        ? "is-blocked"
                        : "is-queued";
                const prio = String(node.priority || "P2").toUpperCase();
                const prioCls =
                  prio === "P0"
                    ? "is-p0"
                    : prio === "P1"
                      ? "is-p1"
                      : prio === "P2"
                        ? "is-p2"
                        : "is-p3";
                const isSel = Boolean(activeSelectedId && node.id === activeSelectedId);
                const isUp = Boolean(activeSelectedId && upstreamIds.has(node.id));
                const isDown = Boolean(activeSelectedId && downstreamIds.has(node.id));
                const isDim = Boolean(activeSelectedId && !isSel && !isUp && !isDown);
                const ord = Math.max(
                  1,
                  Math.round(safeFiniteNumber(node.order_index, 0) ?? 0) + 1
                );
                const gaStepIdx = safeFiniteNumber(node?.ga_step?.step_index, null);

                return (
                  <button
                    key={String(node.id)}
                    type="button"
                    className={`ag-node-card ${statusCls}${isSel ? " is-selected" : ""}${!isSel && isUp ? " is-upstream" : ""}${!isSel && isDown ? " is-downstream" : ""}${isDim ? " is-dimmed" : ""}`}
                    role="listitem"
                    data-node-id={String(node.id)}
                    data-node-kind={String(node.kind || "")}
                    data-node-priority={prio}
                    data-node-status={status}
                    aria-pressed={isSel ? "true" : "false"}
                    style={{ left: `${pos.x}px`, top: `${pos.y}px` }}
                    title={sanitizeMainWindowText(node.reason || "")}
                    onClick={() => {
                      if (consumeSuppressClick()) return;
                      setSelectedNodeId((prev) => (prev === node.id ? null : node.id));
                    }}
                  >
                    <div className="ag-node-head">
                      <div className="ag-node-badges">
                        <span className="ag-step-tag tabular">
                          {gaStepIdx !== null
                            ? `#${ord} · G${Math.round(gaStepIdx) + 1}`
                            : `#${ord}`}
                        </span>
                        <span className={`ag-prio-tag tabular ${prioCls}`}>{prio}</span>
                      </div>
                      <span className={`ag-state-tag tabular ${statusCls}`}>
                        {formatNodeStatusShort(status)}
                      </span>
                    </div>
                    <div className="ag-node-title">
                      {sanitizeMainWindowText(formatNodeTargetLabel(node))}
                    </div>
                    <div className="ag-node-foot">
                      <span className="ag-node-coords">
                        {node.planet_coords ? `[${node.planet_coords}]` : "[GLOBAL]"}
                      </span>
                      <span className="ag-node-eta">
                        {sanitizeMainWindowText(`ETA ${node.total_eta || "Ready"}`)}
                      </span>
                    </div>
                  </button>
                );
              })}
            </div>
          </div>

          <div className="ag-empty-state" id="ag-empty-state" hidden={nodes.length > 0}>
            No planned actions for the current empire snapshot and simulation parameters.
          </div>
        </div>

        <div
          className="ag-inspector-bar"
          id="ag-node-inspector"
          aria-label="Selected action node details"
        >
          <div className="ag-insp-main">
            <span className="module-code tabular" id="ag-insp-step">
              {selectedObj
                ? `#${Math.max(1, Math.round(safeFiniteNumber(selectedObj.order_index, 0) ?? 0) + 1)}`
                : "#—"}
            </span>
            <div className="ag-insp-copy">
              <strong id="ag-insp-title">
                {selectedObj
                  ? sanitizeMainWindowText(
                      `${selectedObj.priority || "P2"} · ${formatNodeTargetLabel(selectedObj)} ${selectedObj.planet_coords ? `[${selectedObj.planet_coords}]` : "[GLOBAL]"}`
                    )
                  : "Select a node in the graph to inspect dependencies and ETAs"}
              </strong>
              <p className="ag-insp-sub tabular" id="ag-insp-meta">
                {selectedObj
                  ? `Wait: ${String(selectedObj.wait_eta || "Ready")} · Duration: ${String(selectedObj.duration_text || "Instant")} · Total: ${String(selectedObj.total_eta || "Ready")}${
                      selectedObj.ga_step && typeof selectedObj.ga_step === "object"
                        ? ` · GA Step #${Math.round(safeFiniteNumber(selectedObj.ga_step.step_index, 0) ?? 0) + 1} (${Math.round(safeFiniteNumber(selectedObj.ga_step.start_t, 0) ?? 0)}s → ${Math.round(safeFiniteNumber(selectedObj.ga_step.finish_t, 0) ?? 0)}s)`
                        : ""
                    } — ${sanitizeMainWindowText(selectedObj.reason || "Planned empire operation.")}`
                  : "Click any node card above to highlight upstream prerequisites and downstream dependents."}
              </p>
            </div>
          </div>
          <div className="ag-insp-deps tabular" id="ag-insp-deps">
            {selectedObj ? (
              incomingSel.length === 0 && outgoingSel.length === 0 ? (
                <span className="ag-dep-pill">Independent root action (0 links)</span>
              ) : (
                <>
                  {incomingSel.slice(0, 4).map((edge, idx) => {
                    const src = nodesById.get(edge.source);
                    const srcNum = src
                      ? Math.round(safeFiniteNumber(src.order_index, 0) ?? 0) + 1
                      : "?";
                    return (
                      <span
                        key={`in-${idx}`}
                        className="ag-dep-pill is-up"
                        title={sanitizeMainWindowText(edge.label || "")}
                      >
                        {sanitizeMainWindowText(
                          `← #${srcNum} ${src ? src.target_name : edge.source} (${edge.primary_type})`
                        )}
                      </span>
                    );
                  })}
                  {outgoingSel.slice(0, 4).map((edge, idx) => {
                    const dst = nodesById.get(edge.target);
                    const dstNum = dst
                      ? Math.round(safeFiniteNumber(dst.order_index, 0) ?? 0) + 1
                      : "?";
                    return (
                      <span
                        key={`out-${idx}`}
                        className="ag-dep-pill is-down"
                        title={sanitizeMainWindowText(edge.label || "")}
                      >
                        {sanitizeMainWindowText(
                          `→ #${dstNum} ${dst ? dst.target_name : edge.target} (${edge.primary_type})`
                        )}
                      </span>
                    );
                  })}
                </>
              )
            ) : null}
          </div>
        </div>
      </article>

      <aside
        className="hud-window ag-control-panel"
        id="ag-control-panel"
        aria-label="Planner parameter simulation and apply controls"
      >
        <div className="window-bar window-bar-wrap">
          <div className="window-title-group">
            <span className="module-code tabular">PRM</span>
            <h2>Planner Parameters</h2>
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
            Mode &amp; Tuning
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
            <div className="control-field">
              <label htmlFor="ag-cfg-planner_mode">Simulated Planner Mode</label>
              <select
                id="ag-cfg-planner_mode"
                value={draftModeNorm}
                onChange={(e) => {
                  const v = e.target.value === "ga_nn" ? "ga_nn" : "priority";
                  syncDraftValuesFromDom(v);
                  void runAgSimulation({ explicit: false, modeOverride: v });
                }}
              >
                <option value="priority">priority — Rule-Based Heuristic</option>
                <option value="ga_nn">ga_nn — GA + Neural Network</option>
              </select>
            </div>

            <div className="control-field">
              <label htmlFor="ag-cfg-playstyle">Playstyle Doctrine</label>
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
            </div>

            <div
              className={`ag-param-group${draftModeNorm !== "ga_nn" ? " is-muted" : ""}`}
              id="ag-ga-params-group"
            >
              <h3>GA + Neural Optimizer</h3>
              <div className="ag-param-grid">
                {(
                  [
                    ["ga_population_size", "Population (4–200)", 4, 200, 1],
                    ["ga_generations", "GA Generations (1–200)", 1, 200, 1],
                    ["ga_horizon_steps", "Horizon Steps (1–24)", 1, 24, 1],
                    ["ga_mutation_rate", "Mutation Rate (0–1)", 0, 1, 0.05],
                    ["ga_nn_weight", "Neural Weight (0–1)", 0, 1, 0.05],
                  ] as const
                ).map(([key, label, minV, maxV, stepV]) => (
                  <div key={key} className="control-field">
                    <label htmlFor={`ag-cfg-${key}`}>{label}</label>
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
                  </div>
                ))}
                <div className="control-field">
                  <label htmlFor="ag-cfg-ga_seed">GA Seed (Blank = Auto)</label>
                  <input
                    type="text"
                    inputMode="numeric"
                    id="ag-cfg-ga_seed"
                    placeholder="Auto"
                    value={String(draftValues.ga_seed ?? "")}
                    onChange={(e) => {
                      setDraftValues((prev) => ({ ...prev, ga_seed: e.target.value }));
                      scheduleDebouncedSim();
                    }}
                  />
                </div>
              </div>
            </div>

            <div className="ag-param-group" id="ag-heuristic-params-group">
              <h3>Economy &amp; Safety Thresholds</h3>
              <div className="ag-param-grid">
                {(
                  [
                    ["storage_warn_ratio", "Storage Warn Ratio", 0.1, 0.99, 0.05],
                    ["blast_pig_iron_max_share", "Max b2 Pig Share", 0.1, 1.0, 0.05],
                    ["combat_safety_margin", "Combat Margin (≥1.0)", 1.0, 1000.0, 0.1],
                    ["raid_min_loot_spice_eq", "Min Raid Loot (SpEq)", 0, undefined, 100],
                    ["hot_planet_solar_temp_threshold", "Solar Temp (°C)", -273, 1000, 5],
                    ["min_free_fields_warning", "Min Free Fields", 1, 1000, 1],
                    ["min_colony_fields", "Min Colony Fields", 50, 600, 10],
                  ] as const
                ).map(([key, label, minV, maxV, stepV]) => (
                  <div key={key} className="control-field">
                    <label htmlFor={`ag-cfg-${key}`}>{label}</label>
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
                  </div>
                ))}
              </div>
            </div>
          </div>

          <div className="ag-param-pane" id="ag-param-pane-scope" hidden={paramTab !== "scope"}>
            <div className="ag-param-grid">
              {(
                [
                  ["fleet_save_fuel_reserve_spice", "Escape Fuel Reserve", 0, undefined, 50],
                  ["action_batch_interval_seconds", "Batch & Refresh Interval (s)", 5, 86400, 5],
                  ["galaxy_scan_click_delay_seconds", "Galaxy Scan Click Delay (s)", 0.2, 60, 0.1],
                  ["galaxy_scan_click_jitter_seconds", "Galaxy Scan Jitter (s)", 0, 30, 0.1],
                  ["max_actions_per_hour", "Max Actions / Hour", 1, 100000, 1],
                  ["reserved_spy_slots", "Reserved Spy Slots", 0, 10, 1],
                ] as const
              ).map(([key, label, minV, maxV, stepV]) => (
                <div key={key} className="control-field">
                  <label htmlFor={`ag-cfg-${key}`}>{label}</label>
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
                </div>
              ))}
            </div>

            <div className="ag-toggles-stack">
              {(
                [
                  ["auto_throttle_blast_furnace", "Auto-pause Blast furnace (0%)"],
                  ["merchant_mode", "Merchant Mode (Skip b2)"],
                  ["allow_research", "Auto Research"],
                  ["allow_shipyard", "Auto Shipyard"],
                  ["allow_multi_planet", "Multi-Planet Planning"],
                  ["allow_fleetsave", "P0 Auto Escape Flight"],
                  ["allow_galaxy_scan", "Galaxy Scanner"],
                  ["allow_raids", "Auto Raids (Inactive/Spacia)"],
                  ["allow_pvp_in_az", "Allow PvP inside AZ"],
                  ["allow_colonize", "Auto Colonize"],
                  ["allow_transmitter", "Daily Free Transmitter"],
                  ["forbid_planetary_solar", "Forbid Solar on Planets (Asteroid Only)"],
                ] as const
              ).map(([bKey, label]) => (
                <label key={bKey} className="toggle-chip">
                  <input
                    type="checkbox"
                    id={`ag-cfg-${bKey}`}
                    checked={Boolean(draftValues[bKey])}
                    onChange={(e) => {
                      const checked = e.target.checked;
                      setDraftValues((prev) => ({ ...prev, [bKey]: checked }));
                      setTimeout(() => runAgSimulation({ explicit: false }), 0);
                    }}
                  />{" "}
                  {label}
                </label>
              ))}
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
    </section>
  );
};

export default ActionGraphView;
