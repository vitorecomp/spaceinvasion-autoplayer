import React, { useCallback, useEffect, useMemo, useState } from "react";
import { feedbackKindForError, safeFiniteNumber } from "../../api/httpClient";
import { SortableTh } from "../../components/SortableTh";
import { TablePagination } from "../../components/TablePagination";
import { useConsole } from "../../context/ConsoleContext";
import { useTableSort, type SortColumnMap } from "../../hooks/useTableSort";
import type {
  AiPlannerAdaptiveStatus,
  AiPlannerColonyDiag,
  AiPlannerGetPayload,
  AiPlannerSmeltingToggleTarget,
  AiPlannerStepItem,
  FieldConflictItem,
  PlanetData,
  PlannerJobState,
  QueueJobData,
} from "../../types/api";
import { fmtCompact, fmtInt } from "../../utils/formatters";
import { ActionGraphParamRow } from "./ActionGraphParamRow";
import { DagCanvas } from "./DagCanvas";
import {
  AI_PLANNER_GROUP_SUBTITLES,
  AI_PLANNER_PARAM_DOCS,
  AI_PLANNER_SPARE_DOCS,
} from "./paramDocs";
import {
  AiPlannerDraft,
  DEFAULT_AI_PLANNER_DRAFT,
  computeColonyGateInfo,
  downloadTextFile,
  formatDurationSecondsCompact,
  formatJobBadgeText,
  normalizeUiPlannerMode,
} from "./plannerHelpers";

function formatAdaptiveColonyState(stateRaw: unknown): {
  label: string;
  tone: "is-building" | "is-ready" | "is-inbound" | "is-wait" | "is-blocked" | "is-idle";
} {
  const norm = String(stateRaw || "").trim().toLowerCase();
  switch (norm) {
    case "building":
      return { label: "Building", tone: "is-building" };
    case "ready_not_started":
    case "ready":
      return { label: "Ready", tone: "is-ready" };
    case "waiting_metal":
      return { label: "Wait Metal", tone: "is-wait" };
    case "waiting_pig_iron":
    case "waiting_pig":
      return { label: "Wait Pig", tone: "is-wait" };
    case "waiting_kryptonite":
    case "waiting_kry":
      return { label: "Wait Kry", tone: "is-wait" };
    case "waiting_spice":
      return { label: "Wait Spice", tone: "is-wait" };
    case "transport_inbound":
    case "cargo_inbound":
      return { label: "Cargo Inbound", tone: "is-inbound" };
    case "storage_blocked":
    case "storage_cap":
      return { label: "Storage Cap", tone: "is-blocked" };
    case "field_conflict":
      return { label: "Field Conflict", tone: "is-blocked" };
    case "idle_complete":
    case "idle":
    case "":
      return { label: "Idle", tone: "is-idle" };
    default:
      return {
        label: norm
          .split("_")
          .filter(Boolean)
          .map((w) => w.charAt(0).toUpperCase() + w.slice(1))
          .join(" "),
        tone: "is-idle",
      };
  }
}

function formatSharePct(val: unknown): string {
  const n = safeFiniteNumber(val, 0) ?? 0;
  return String(Number(n.toFixed(1)));
}

function countFallbackActiveBuilders(
  planets: PlanetData[],
  activeQueues: QueueJobData[]
): { active: number; total: number } {
  const normalPlanets = planets.filter(
    (p) => String(p?.planet_type || "normal").toLowerCase() === "normal"
  );
  const scoped = normalPlanets.length > 0 ? normalPlanets : planets;
  const knownPid = new Set<number>();
  const coordsToPid = new Map<string, number>();
  for (const pl of scoped) {
    const pid = Math.round(safeFiniteNumber(pl?.planet_id, 0) ?? 0);
    if (pid > 0) {
      knownPid.add(pid);
      const c = String(pl?.coords || "").trim();
      if (c) coordsToPid.set(c, pid);
    }
  }
  const nowSec = Math.floor(Date.now() / 1000);
  const activeKeys = new Set<string>();
  for (const job of activeQueues) {
    if (!job || String(job.category || "").toLowerCase() !== "building") continue;
    const finTs = safeFiniteNumber(job.finish_ts, null);
    const remSec = safeFiniteNumber(job.remaining_seconds, null);
    let active = false;
    if (finTs !== null && finTs > 0) {
      active = finTs > nowSec;
    } else if (remSec !== null) {
      active = remSec > 0;
    } else {
      const remTxt = String(job.remaining_text || "").trim().toLowerCase();
      active = remTxt.length > 0 && remTxt !== "0s" && remTxt !== "done" && remTxt !== "finished";
    }
    if (!active) continue;
    const jobPid = Math.round(safeFiniteNumber(job.planet_id, 0) ?? 0);
    const jobCoords = String(job.origin_coords || job.planet_coords || "").trim();
    if (jobPid > 0 && (knownPid.size === 0 || knownPid.has(jobPid))) {
      activeKeys.add(`pid:${jobPid}`);
    } else if (jobCoords && coordsToPid.has(jobCoords)) {
      activeKeys.add(`pid:${coordsToPid.get(jobCoords)!}`);
    } else if (jobCoords && knownPid.size === 0) {
      activeKeys.add(`coords:${jobCoords}`);
    }
  }
  return { active: activeKeys.size, total: scoped.length };
}

function draftFromAiParams(raw: Record<string, any> | null | undefined): AiPlannerDraft {
  if (!raw || typeof raw !== "object") {
    return {
      ...DEFAULT_AI_PLANNER_DRAFT,
      resource_spare_percent: { ...DEFAULT_AI_PLANNER_DRAFT.resource_spare_percent },
    };
  }
  const get = (snake: string, upper: string, def: any) =>
    raw[snake] !== undefined ? raw[snake] : raw[upper] !== undefined ? raw[upper] : def;

  const rawSpare =
    get("resource_spare_percent", "PATH_RESOURCE_SPARE_PERCENT", {}) || {};
  const spare = {
    pig_iron: String(safeFiniteNumber(rawSpare.pig_iron, 10) ?? 10),
    metal: String(safeFiniteNumber(rawSpare.metal, 10) ?? 10),
    kryptonite: String(safeFiniteNumber(rawSpare.kryptonite, 10) ?? 10),
    spice: String(safeFiniteNumber(rawSpare.spice, 10) ?? 10),
  };
  const b10Raw = get("fleet_command_center_level", "PATH_FLEET_COMMAND_CENTER_LEVEL", null);
  const apdRaw = get("attacks_per_day", "PATH_ATTACKS_PER_DAY", null);

  return {
    reward_weeks: String(
      Math.max(1, Math.round(safeFiniteNumber(get("reward_weeks", "PATH_REWARD_WEEKS", 26), 26) ?? 26))
    ),
    weekly_discount: String(
      safeFiniteNumber(get("weekly_discount", "PATH_WEEKLY_DISCOUNT", 0.9), 0.9) ?? 0.9
    ),
    transport_dispatch_delay_minutes: String(
      Math.max(
        0,
        Math.min(
          1440,
          safeFiniteNumber(
            get("transport_dispatch_delay_minutes", "PATH_TRANSPORT_DISPATCH_DELAY_MINUTES", 120),
            120
          ) ?? 120
        )
      )
    ),
    resource_spare_percent: spare,
    fleet_command_center_level:
      b10Raw !== null && b10Raw !== undefined && String(b10Raw).trim() !== ""
        ? String(Math.round(safeFiniteNumber(b10Raw, 0) ?? 0))
        : "",
    attacks_per_day:
      apdRaw !== null && apdRaw !== undefined && String(apdRaw).trim() !== ""
        ? String(safeFiniteNumber(apdRaw, 0) ?? 0)
        : "",
    pad_generic_colonies: Boolean(get("pad_generic_colonies", "PAD_GENERIC_COLONIES", false)),
    scarcity_focus: String(safeFiniteNumber(get("scarcity_focus", "SCARCITY_FOCUS", 1.0), 1.0) ?? 1.0),
    critical_queue_share: String(
      safeFiniteNumber(get("critical_queue_share", "CRITICAL_QUEUE_SHARE", 0.9), 0.9) ?? 0.9
    ),
    patience: String(safeFiniteNumber(get("patience", "PATIENCE", 0.25), 0.25) ?? 0.25),
    speed_weight: String(safeFiniteNumber(get("speed_weight", "SPEED_WEIGHT", 1.5), 1.5) ?? 1.5),
    ga_population_size: String(
      Math.max(4, Math.round(safeFiniteNumber(get("ga_population_size", "GA_POPULATION_SIZE", 10), 10) ?? 10))
    ),
    ga_generations: String(
      Math.max(1, Math.round(safeFiniteNumber(get("ga_generations", "GA_GENERATIONS", 6), 6) ?? 6))
    ),
    ga_random_seed: String(
      Math.round(safeFiniteNumber(get("ga_random_seed", "GA_RANDOM_SEED", 20261117), 20261117) ?? 20261117)
    ),
  };
}

function buildAiParamsPayload(draft: AiPlannerDraft): Record<string, any> {
  const num = (v: string, fb: number) => safeFiniteNumber(v, fb) ?? fb;
  const intNum = (v: string, fb: number, min = 0) => Math.max(min, Math.round(num(v, fb)));

  const b10Trim = draft.fleet_command_center_level.trim();
  const apdTrim = draft.attacks_per_day.trim();

  return {
    reward_weeks: Math.max(1, Math.min(104, intNum(draft.reward_weeks, 26, 1))),
    weekly_discount: Math.max(0.05, Math.min(1.0, num(draft.weekly_discount, 0.9))),
    transport_dispatch_delay_minutes: Math.max(
      0,
      Math.min(1440, num(draft.transport_dispatch_delay_minutes, 120))
    ),
    resource_spare_percent: {
      pig_iron: Math.max(0, Math.min(100, num(draft.resource_spare_percent.pig_iron, 10))),
      metal: Math.max(0, Math.min(100, num(draft.resource_spare_percent.metal, 10))),
      kryptonite: Math.max(0, Math.min(100, num(draft.resource_spare_percent.kryptonite, 10))),
      spice: Math.max(0, Math.min(100, num(draft.resource_spare_percent.spice, 10))),
    },
    fleet_command_center_level: b10Trim === "" ? null : intNum(b10Trim, 0, 0),
    attacks_per_day: apdTrim === "" ? null : Math.max(0, num(apdTrim, 0)),
    pad_generic_colonies: Boolean(draft.pad_generic_colonies),
    scarcity_focus: Math.max(0, num(draft.scarcity_focus, 1.0)),
    critical_queue_share: Math.max(0, Math.min(1.0, num(draft.critical_queue_share, 0.9))),
    patience: Math.max(0, num(draft.patience, 0.25)),
    speed_weight: Math.max(0, num(draft.speed_weight, 1.5)),
    ga_population_size: intNum(draft.ga_population_size, 10, 4),
    ga_generations: intNum(draft.ga_generations, 6, 1),
    ga_random_seed: Math.round(num(draft.ga_random_seed, 20261117)),
  };
}

export interface AiPlannerTabProps {
  hidden?: boolean;
}

export const AiPlannerTab: React.FC<AiPlannerTabProps> = ({ hidden = false }) => {
  const {
    dashboard,
    setDashboard,
    activePage,
    apiOnline,
    plannerJobsStatus,
    setPlannerJobsStatus,
    apiCommand,
    loadDashboard,
    showFeedback,
  } = useConsole();

  const [aiData, setAiData] = useState<AiPlannerGetPayload | null>(null);
  const [draft, setDraft] = useState<AiPlannerDraft>(() =>
    draftFromAiParams(DEFAULT_AI_PLANNER_DRAFT)
  );
  const [draftDirty, setDraftDirty] = useState<boolean>(false);
  const [paramSubTab, setParamSubTab] = useState<"horizon" | "weights">("horizon");
  const [subView, setSubView] = useState<"dag" | "steps" | "weekly" | "smelting">("dag");

  const [windowSize, setWindowSize] = useState<number>(40);
  const [weekFilter, setWeekFilter] = useState<string>("all");
  const [planetFilter, setPlanetFilter] = useState<string>("all");
  const [stepsSearch, setStepsSearch] = useState<string>("");
  const [stepsPage, setStepsPage] = useState<number>(1);
  const [stepsPageSize, setStepsPageSize] = useState<number>(25);

  const [isSubmitting, setIsSubmitting] = useState<boolean>(false);
  const [isTogglingAdaptive, setIsTogglingAdaptive] = useState<boolean>(false);
  const [resolvingConflictKey, setResolvingConflictKey] = useState<string | null>(null);
  const [selectedCandidateByConflict, setSelectedCandidateByConflict] = useState<
    Record<string, string>
  >({});

  const fetchAiState = useCallback(
    async ({
      silent = false,
      pageOverride,
      pageSizeOverride,
      searchOverride,
      weekOverride,
      planetOverride,
      windowOverride,
    }: {
      silent?: boolean;
      pageOverride?: number;
      pageSizeOverride?: number;
      searchOverride?: string;
      weekOverride?: string;
      planetOverride?: string;
      windowOverride?: number;
    } = {}) => {
      try {
        const wk = weekOverride ?? weekFilter;
        const pl = planetOverride ?? planetFilter;
        const payload: Record<string, any> = {
          command: "ai_planner_get",
          window_size: windowOverride ?? windowSize,
          page: pageOverride ?? stepsPage,
          page_size: pageSizeOverride ?? stepsPageSize,
          search: (searchOverride ?? stepsSearch).trim(),
        };
        if (wk !== "all") payload.week = Number(wk);
        if (pl !== "all") payload.planet_id = Number(pl) || pl;

        const res = await apiCommand<AiPlannerGetPayload>(payload);
        if (res && typeof res === "object") {
          setAiData(res);
          if (!draftDirty) {
            const rawParams = res.meta_parameters || res.default_params || null;
            if (rawParams) setDraft(draftFromAiParams(rawParams));
          }
          if (res.job && plannerJobsStatus) {
            setPlannerJobsStatus({
              ...plannerJobsStatus,
              ai_planner_job: res.job,
              pending_field_conflicts:
                res.pending_field_conflicts ?? plannerJobsStatus.pending_field_conflicts,
            });
          }
        }
      } catch (err: any) {
        if (!silent) {
          showFeedback(`AI-Planner load failed: ${err.message}`, feedbackKindForError(err));
        }
      }
    },
    [
      apiCommand,
      draftDirty,
      planetFilter,
      plannerJobsStatus,
      setPlannerJobsStatus,
      showFeedback,
      stepsPage,
      stepsPageSize,
      stepsSearch,
      weekFilter,
      windowSize,
    ]
  );

  useEffect(() => {
    if (!hidden && activePage === "action-graph" && !aiData) {
      void fetchAiState({ silent: true });
    }
  }, [activePage, aiData, apiOnline, fetchAiState, hidden]);

  const activeJob: PlannerJobState | null =
    aiData?.job || plannerJobsStatus?.ai_planner_job || null;
  const isJobRunning = String(activeJob?.status || "").toLowerCase() === "running";

  useEffect(() => {
    if (!isJobRunning || !apiOnline || hidden) return;
    const timer = setInterval(() => {
      void fetchAiState({ silent: true });
    }, 1500);
    return () => clearInterval(timer);
  }, [apiOnline, fetchAiState, hidden, isJobRunning]);

  const gate = computeColonyGateInfo(dashboard, plannerJobsStatus, aiData);
  const activeMode = normalizeUiPlannerMode(
    dashboard?.config?.planner_mode || dashboard?.planner_mode
  );
  const isLiveAiActive = activeMode === "ai_planner" && gate.liveAiUnlocked;

  const conflicts: FieldConflictItem[] = useMemo(() => {
    const raw =
      aiData?.pending_field_conflicts ??
      plannerJobsStatus?.pending_field_conflicts ??
      dashboard?.pending_field_conflicts ??
      [];
    return Array.isArray(raw)
      ? raw.filter((c) => c && String(c.status || "pending").toLowerCase() === "pending")
      : [];
  }, [aiData?.pending_field_conflicts, dashboard?.pending_field_conflicts, plannerJobsStatus?.pending_field_conflicts]);

  const rawSteps: AiPlannerStepItem[] = useMemo(() => {
    const items = aiData?.steps_page?.items;
    return Array.isArray(items) ? items : [];
  }, [aiData?.steps_page?.items]);

  const stepSortCols = useMemo<
    SortColumnMap<AiPlannerStepItem, "seq" | "week" | "planet" | "action" | "duration" | "status">
  >(
    () => ({
      seq: (s) => safeFiniteNumber(s.seq ?? s.order, 0),
      week: (s) => safeFiniteNumber(s.week, 0),
      planet: (s) => String(s.planet_coords || s.planet_name || "account"),
      action: (s) => `${s.kind || ""} ${s.entity_name || s.entity_key || ""}`,
      duration: { accessor: (s) => safeFiniteNumber(s.duration_sec, 0), defaultDirection: "desc" },
      status: (s) => String(s.status || "queued"),
    }),
    []
  );
  const stepSort = useTableSort(rawSteps, stepSortCols);

  const weeklyRows: Array<Record<string, any>> = useMemo(
    () => (Array.isArray(aiData?.weekly_breakdown) ? aiData!.weekly_breakdown! : []),
    [aiData]
  );
  const weekSortCols = useMemo<
    SortColumnMap<Record<string, any>, "week" | "weight" | "spiceEq" | "discounted" | "cumulative">
  >(
    () => ({
      week: (w) => safeFiniteNumber(w.week, 0),
      weight: { accessor: (w) => safeFiniteNumber(w.discount_weight, 0), defaultDirection: "desc" },
      spiceEq: { accessor: (w) => safeFiniteNumber(w.spice_eq, 0), defaultDirection: "desc" },
      discounted: {
        accessor: (w) => safeFiniteNumber(w.discounted_spice_eq, 0),
        defaultDirection: "desc",
      },
      cumulative: {
        accessor: (w) => safeFiniteNumber(w.cumulative_discounted_spice_eq ?? w.cumulative, 0),
        defaultDirection: "desc",
      },
    }),
    []
  );
  const weekSort = useTableSort(weeklyRows, weekSortCols);

  const onRunAiPlanner = async () => {
    setIsSubmitting(true);
    try {
      const payload = buildAiParamsPayload(draft);
      showFeedback("Running AI-Planner W-week NPV sequence search...", "ok");
      const res = await apiCommand({
        command: "ai_planner_run",
        params: payload,
        refresh: Boolean(apiOnline && dashboard?.health?.in_game),
      });
      setDraftDirty(false);
      await fetchAiState({ silent: true });
      const st = String(res.job?.status || "completed").toLowerCase();
      showFeedback(
        st === "running"
          ? "AI-Planner search running in background..."
          : "AI-Planner W-week path computed.",
        "ok"
      );
    } catch (err: any) {
      showFeedback(`AI-Planner run failed: ${err.message}`, feedbackKindForError(err));
    } finally {
      setIsSubmitting(false);
    }
  };

  const onActivateAiMode = async () => {
    if (!gate.liveAiUnlocked) return;
    try {
      const res = await apiCommand({
        command: "update_config",
        config: { planner_mode: "ai_planner" },
        include_action_graph: true,
      });
      if (res.config && typeof res.config === "object") {
        (globalThis as any).renderConfigForm?.(res.config);
        const curDash = (globalThis as any).state?.dashboard || dashboard;
        if (curDash) {
          const nextDash = {
            ...curDash,
            config: res.config,
            planner_mode: res.config.planner_mode || "ai_planner",
            effective_planner_mode:
              res.effective_planner_mode ?? res.config.planner_mode ?? "ai_planner",
            fallback_reason: res.fallback_reason ?? null,
            live_ai_unlocked: res.live_ai_unlocked ?? curDash.live_ai_unlocked,
          };
          if ((globalThis as any).state) (globalThis as any).state.dashboard = nextDash;
          setDashboard(nextDash);
        }
      }
      await loadDashboard({ refresh: false, preserveConfig: false, force: true });
      showFeedback("Live AI-Planner mode activated (config.yaml saved).", "ok");
    } catch (err: any) {
      showFeedback(`Activation failed: ${err.message}`, feedbackKindForError(err));
    }
  };

  const onResolveConflict = async (
    conflict: FieldConflictItem,
    decision: "demolish" | "keep_reserve"
  ) => {
    const chosenBldKey =
      selectedCandidateByConflict[conflict.conflict_key] ||
      conflict.candidate_building_key ||
      conflict.candidates?.[0]?.building_key ||
      "";
    setResolvingConflictKey(conflict.conflict_key);
    try {
      const res = await apiCommand({
        command: "resolve_field_conflict",
        conflict_key: conflict.conflict_key,
        decision,
        building_key: chosenBldKey || undefined,
      });
      showFeedback(
        decision === "demolish"
          ? `Queued DEMOLISH_BUILDING (${chosenBldKey}) on [${conflict.planet_coords || conflict.planet_id}] to free field.`
          : `Reserved ${chosenBldKey} floor on [${conflict.planet_coords || conflict.planet_id}] and triggered Config Optimizer -> AI-Planner rerun.`,
        "ok"
      );
      await fetchAiState({ silent: true });
      await loadDashboard({ refresh: false, preserveConfig: true, force: true });
      if (res.job?.status === "running") {
        setPlannerJobsStatus({
          ...(plannerJobsStatus || {}),
          ai_planner_job: res.job,
        });
      }
    } catch (err: any) {
      showFeedback(`Conflict resolution failed: ${err.message}`, feedbackKindForError(err));
    } finally {
      setResolvingConflictKey(null);
    }
  };

  const onToggleAdaptiveExecution = async (nextVal: boolean) => {
    setIsTogglingAdaptive(true);
    try {
      const res = await apiCommand({
        command: "update_config",
        config: { ai_adaptive_execution: nextVal },
      });
      if (res.config && typeof res.config === "object") {
        (globalThis as any).renderConfigForm?.(res.config);
        const curDash = (globalThis as any).state?.dashboard || dashboard;
        const nextDash = curDash
          ? {
              ...curDash,
              config: res.config,
            }
          : {
              config: res.config,
            };
        if ((globalThis as any).state) (globalThis as any).state.dashboard = nextDash;
        setDashboard(nextDash);
      }
      await loadDashboard({ refresh: false, preserveConfig: false, force: true });
      await fetchAiState({ silent: true });
      showFeedback(
        `Adaptive AI-Planner execution ${nextVal ? "enabled" : "disabled"} (config.yaml saved).`,
        "ok"
      );
    } catch (err: any) {
      showFeedback(
        `Adaptive execution update failed: ${err.message}`,
        feedbackKindForError(err)
      );
    } finally {
      setIsTogglingAdaptive(false);
    }
  };

  const onExportTxt = async () => {
    try {
      const res = await apiCommand({ command: "ai_planner_export" });
      const filename = String(res.filename || "full_planner_path.txt");
      const content = String(res.content || "");
      downloadTextFile(filename, content, "text/plain");
      showFeedback(`Exported ${filename}.`, "ok");
    } catch (err: any) {
      showFeedback(`Export .txt failed: ${err.message}`, feedbackKindForError(err));
    }
  };

  const summary = aiData?.active_summary || {};
  const telemetry = aiData?.live_attack_telemetry || aiData?.attack_telemetry || {};
  const planets = Array.isArray(dashboard?.state?.planets) ? dashboard!.state!.planets! : [];
  const rewardWeeks = Math.max(
    1,
    Math.round(safeFiniteNumber(summary.reward_weeks ?? draft.reward_weeks, 26) ?? 26)
  );
  const discReward = safeFiniteNumber(
    summary.discounted_reward_spice_eq ?? summary.discounted_reward,
    null
  );
  const totalNetOutput = safeFiniteNumber(
    summary.total_net_spice_eq ?? summary.total_spice_eq,
    null
  );
  const stepsCompleted = Math.max(
    0,
    Math.round(safeFiniteNumber(summary.steps_completed ?? summary.steps_started, 0) ?? 0)
  );
  const stepsTotal = Math.max(
    0,
    Math.round(safeFiniteNumber(summary.steps ?? aiData?.steps_page?.total, 0) ?? 0)
  );
  const startSlots = Math.round(
    safeFiniteNumber(summary.start_fleet_slots ?? telemetry.current_fleet_slots, 0) ?? 0
  );
  const endSlots = Math.round(
    safeFiniteNumber(summary.final_fleet_slots ?? startSlots, startSlots) ?? startSlots
  );
  const lootPerHr = safeFiniteNumber(
    telemetry.hourly_loot_spice_eq ?? telemetry.hourly_loot?.spice_eq,
    null
  );
  const medianLoot = safeFiniteNumber(
    telemetry.median_loot_spice_eq ?? telemetry.median_loot?.spice_eq,
    null
  );
  const medianRtSec = safeFiniteNumber(telemetry.median_round_trip_seconds, null);
  const inProgBuilds = Array.isArray(aiData?.in_progress?.buildings)
    ? aiData!.in_progress!.buildings.length
    : Math.max(0, Math.round(safeFiniteNumber(summary.in_progress_buildings_count, 0) ?? 0));
  const inProgRes = Array.isArray(aiData?.in_progress?.research)
    ? aiData!.in_progress!.research.length
    : Math.max(0, Math.round(safeFiniteNumber(summary.in_progress_research_count, 0) ?? 0));
  const inProgTotal = inProgBuilds + inProgRes;
  const dispatchDelayMin = Math.round(
    safeFiniteNumber(
      summary.transport_dispatch_delay_minutes ?? draft.transport_dispatch_delay_minutes,
      120
    ) ?? 120
  );
  const smelting = aiData?.smelting || null;
  const liveBf = aiData?.live_blast_furnace || null;
  const smeltingEvents = Array.isArray(smelting?.smelting_events)
    ? smelting!.smelting_events!
    : [];
  const smeltingPlanets =
    Array.isArray(smelting?.planets) && smelting!.planets!.length > 0
      ? smelting!.planets!
      : Array.isArray(liveBf?.planets)
        ? liveBf!.planets!
        : [];
  const initLiveMode = String(
    smelting?.initial_live_mode ?? liveBf?.mode ?? summary.initial_smelting_live_mode ?? "none"
  ).toLowerCase();
  const initLiveEnabledCount = Math.round(
    safeFiniteNumber(smelting?.initial_live_enabled_count ?? liveBf?.enabled_count, 0) ?? 0
  );
  const totalB2Planets = Math.round(
    safeFiniteNumber(smelting?.total_b2_planets ?? liveBf?.total_b2_planets, 0) ?? 0
  );
  const hasSmeltingTarget =
    typeof smelting?.initial_target_enabled === "boolean" ||
    typeof summary.initial_smelting_target_enabled === "boolean";
  const initTargetEnabled = Boolean(
    smelting?.initial_target_enabled ?? summary.initial_smelting_target_enabled ?? false
  );
  const initToggleNeeded = Boolean(
    smelting?.initial_toggle_needed ?? summary.initial_smelting_toggle_needed ?? false
  );
  const metalSmelted = safeFiniteNumber(smelting?.metal_smelted ?? summary.metal_smelted, null);
  const smeltingHours = safeFiniteNumber(
    smelting?.smelting_hours_total ?? summary.smelting_hours_total,
    null
  );
  const smeltingTimeText =
    smelting?.smelting_time_text ||
    (smeltingHours !== null ? formatDurationSecondsCompact(smeltingHours * 3600) : "0m");
  const smeltingTogglesCount = Math.round(
    safeFiniteNumber(
      smelting?.smelting_toggles_count ?? summary.smelting_toggles_count,
      smeltingEvents.length
    ) ?? 0
  );
  const toggleByPlanetId = new Map<number, AiPlannerSmeltingToggleTarget>();
  if (Array.isArray(smelting?.initial_planets_needing_toggle)) {
    for (const row of smelting!.initial_planets_needing_toggle!) {
      if (row && typeof row.planet_id === "number") {
        toggleByPlanetId.set(row.planet_id, row);
      }
    }
  }
  const liveModeLabel =
    totalB2Planets <= 0
      ? "NONE"
      : `${initLiveMode.toUpperCase()} (${initLiveEnabledCount}/${totalB2Planets} ON)`;

  const customPlanetNames = useMemo<Record<string, string>>(() => {
    const raw = (dashboard as Record<string, any> | null | undefined)?.custom_planet_names;
    if (!raw || typeof raw !== "object") return {};
    const out: Record<string, string> = {};
    for (const [k, v] of Object.entries(raw as Record<string, unknown>)) {
      const clean = String(v || "").trim();
      if (clean) out[String(k)] = clean;
    }
    return out;
  }, [dashboard]);

  const planetNameById = useMemo(() => {
    const map = new Map<number, string>();
    for (const p of planets) {
      const pid = Math.round(safeFiniteNumber(p?.planet_id, 0) ?? 0);
      const nm = String(p?.name || "").trim();
      if (pid > 0 && nm) map.set(pid, nm);
    }
    return map;
  }, [planets]);

  const adaptiveStatus: AiPlannerAdaptiveStatus | null = aiData?.adaptive_status || null;
  const adaptiveEnabled = Boolean(
    dashboard?.config?.ai_adaptive_execution ?? adaptiveStatus?.enabled ?? false
  );
  const fallbackBuilders = useMemo(
    () => countFallbackActiveBuilders(planets, dashboard?.state?.active_queues || []),
    [planets, dashboard?.state?.active_queues]
  );
  const adaptiveActiveBuilders =
    adaptiveStatus && typeof adaptiveStatus.active_builders === "number"
      ? Math.max(0, Math.round(adaptiveStatus.active_builders))
      : fallbackBuilders.active;
  const adaptiveTotalColonies =
    adaptiveStatus && typeof adaptiveStatus.total_colonies === "number"
      ? Math.max(0, Math.round(adaptiveStatus.total_colonies))
      : fallbackBuilders.total;
  const adaptiveColonies: AiPlannerColonyDiag[] = Array.isArray(adaptiveStatus?.colonies)
    ? adaptiveStatus!.colonies
    : [];
  const transportFleet = adaptiveStatus?.transport_fleet || null;
  const window24h = adaptiveStatus?.window_24h || null;
  const hasWindow24hSamples = Boolean(
    window24h && (safeFiniteNumber(window24h.colony_samples, 0) ?? 0) > 0
  );
  const autoReplan = adaptiveStatus?.auto_replan || null;

  return (
    <div className="ag-tab-split" id="ag-pane-ai-planner" hidden={hidden}>
      <article className="hud-window ag-main-window" id="ag-ai-main-window">
        <div className="window-bar window-bar-wrap">
          <div className="window-title-group">
            <span className="module-code tabular">AIP</span>
            <h2>AI-Planner — W-Week NPV Sequential Action Path</h2>
            <span
              className={`ag-status-pill tabular ${isLiveAiActive ? "is-live" : isJobRunning ? "is-sim" : ""}`}
              id="ai-job-status-pill"
              role="status"
            >
              {isJobRunning
                ? formatJobBadgeText(activeJob)
                : isLiveAiActive
                  ? "LIVE AI-PLANNER ACTIVE"
                  : aiData?.has_plan
                    ? "PREVIEW READY"
                    : "NO PATH"}
            </span>
          </div>

          <div className="inline-actions">
            <div className="tab-group" role="tablist" aria-label="AI-Planner subviews">
              <button
                type="button"
                role="tab"
                aria-selected={subView === "dag"}
                className={`tab-btn${subView === "dag" ? " active" : ""}`}
                id="ai-subtab-dag"
                onClick={() => setSubView("dag")}
              >
                Windowed DAG
              </button>
              <button
                type="button"
                role="tab"
                aria-selected={subView === "steps"}
                className={`tab-btn${subView === "steps" ? " active" : ""}`}
                id="ai-subtab-steps"
                onClick={() => setSubView("steps")}
              >
                {`Path (${aiData?.steps_page?.total ?? rawSteps.length})`}
              </button>
              <button
                type="button"
                role="tab"
                aria-selected={subView === "weekly"}
                className={`tab-btn${subView === "weekly" ? " active" : ""}`}
                id="ai-subtab-weekly"
                onClick={() => setSubView("weekly")}
              >
                {`Weekly (${weeklyRows.length})`}
              </button>
              <button
                type="button"
                role="tab"
                aria-selected={subView === "smelting"}
                className={`tab-btn${subView === "smelting" ? " active" : ""}`}
                id="ai-subtab-smelting"
                onClick={() => setSubView("smelting")}
              >
                {`Smelting (${smeltingTogglesCount})`}
              </button>
            </div>
            <button
              type="button"
              className="btn btn-secondary btn-sm"
              id="btn-ai-export"
              onClick={onExportTxt}
            >
              Export .txt
            </button>
            <button
              type="button"
              className="btn btn-secondary btn-sm"
              id="btn-ai-refresh"
              onClick={() => fetchAiState({ silent: false })}
            >
              Refresh
            </button>
          </div>
        </div>

        {/* Colony Gate Banner */}
        <div
          className={`ag-gate-banner ${gate.liveAiUnlocked ? "is-unlocked" : "is-locked"}`}
          id="ag-ai-colony-gate"
          role="status"
        >
          <span className="module-code tabular">
            {`${gate.coloniesOwned}/${gate.maxPlanets}`}
          </span>
          <span className="ag-gate-copy">
            {gate.liveAiUnlocked
              ? `${gate.coloniesOwned} / ${gate.maxPlanets} colonies founded — Live Bot Execution Unlocked${isLiveAiActive ? " · Currently Active" : " · Click Activate Live AI-Planner to switch"}`
              : `${gate.coloniesOwned} / ${gate.maxPlanets} colonies founded — Preview Simulation Active · Live Bot Execution Unlocks at ${gate.maxPlanets}/${gate.maxPlanets}`}
          </span>
        </div>

        {/* Field Conflict Decision Banner */}
        {conflicts.length > 0 ? (
          <div className="ag-conflict-stack" id="ag-ai-field-conflicts" role="region" aria-label="Pending planet field conflicts">
            {conflicts.map((c, idx) => {
              const candList =
                Array.isArray(c.candidates) && c.candidates.length > 0
                  ? c.candidates
                  : c.candidate_building_key
                    ? [
                        {
                          building_key: c.candidate_building_key,
                          level: c.candidate_level ?? 0,
                        },
                      ]
                    : [];
              const selKey =
                selectedCandidateByConflict[c.conflict_key] ||
                candList[0]?.building_key ||
                "";
              const busy = resolvingConflictKey === c.conflict_key;

              return (
                <div
                  key={c.conflict_key}
                  className="ag-conflict-card"
                  data-conflict-key={c.conflict_key}
                >
                  <div className="ag-conflict-info">
                    <span className="module-code tabular">FIELD CONFLICT</span>
                    <div className="ag-conflict-copy">
                      <strong>
                        {`${c.planet_name || "Colony"} [${c.planet_coords || c.planet_id}] full — ${c.blocked_building_name || c.blocked_building_key} → L${c.blocked_target_level ?? "?"} needs 1 free field`}
                      </strong>
                      <span className="action-sub">
                        Auto-demolition is disabled. Choose whether to demolish an existing building or keep &amp; reserve its floor in Config Optimizer.
                      </span>
                    </div>
                  </div>
                  <div className="ag-conflict-actions">
                    {candList.length > 0 ? (
                      <select
                        className="ag-conflict-select"
                        aria-label="Candidate building for field conflict"
                        value={selKey}
                        onChange={(e) =>
                          setSelectedCandidateByConflict((prev) => ({
                            ...prev,
                            [c.conflict_key]: e.target.value,
                          }))
                        }
                      >
                        {candList.map((cand) => (
                          <option key={cand.building_key} value={cand.building_key}>
                            {`${cand.name || cand.building_key} (L${cand.level ?? "?"})`}
                          </option>
                        ))}
                      </select>
                    ) : null}
                    <button
                      type="button"
                      className="btn btn-warning btn-sm"
                      id={`btn-conflict-demolish-${idx}`}
                      data-conflict-action="demolish"
                      disabled={busy}
                      onClick={() => onResolveConflict(c, "demolish")}
                    >
                      Demolish Building
                    </button>
                    <button
                      type="button"
                      className="btn btn-primary btn-sm"
                      id={`btn-conflict-reserve-${idx}`}
                      data-conflict-action="keep_reserve"
                      disabled={busy}
                      onClick={() => onResolveConflict(c, "keep_reserve")}
                    >
                      Keep &amp; Reserve Fields (Rerun Pipeline)
                    </button>
                  </div>
                </div>
              );
            })}
          </div>
        ) : null}

        <div className="ag-kpi-strip" aria-label="AI-Planner KPI summary">
          <div className="ag-kpi-cell">
            <span className="ag-kpi-label">Net Discounted SpiceEq</span>
            <strong className="ag-kpi-val tabular" id="ai-kpi-reward">
              {discReward !== null ? fmtCompact(discReward) : "—"}
            </strong>
            <span className="ag-kpi-sub tabular">
              {`γ=${draft.weekly_discount} · ${rewardWeeks}w NPV`}
            </span>
          </div>
          <div className="ag-kpi-cell">
            <span className="ag-kpi-label">{`Total ${rewardWeeks}-Week Net Output`}</span>
            <strong className="ag-kpi-val tabular" id="ai-kpi-output">
              {totalNetOutput !== null ? fmtCompact(totalNetOutput) : "—"}
            </strong>
            <span className="ag-kpi-sub tabular">
              {summary.status ? `Status: ${String(summary.status).toUpperCase()}` : "SpiceEq"}
            </span>
          </div>
          <div className="ag-kpi-cell">
            <span className="ag-kpi-label">Progress (Completed / Horizon)</span>
            <strong className="ag-kpi-val tabular" id="ai-kpi-progress">
              {`${stepsCompleted} / ${stepsTotal}`}
            </strong>
            <span className="ag-kpi-sub tabular">
              {`${summary.time ? `Reached in ${summary.time}` : `Horizon ${rewardWeeks}w`}${inProgTotal > 0 ? ` · ${inProgTotal} live` : ""} · +${dispatchDelayMin}m dispatch`}
            </span>
          </div>
          <div className="ag-kpi-cell">
            <span className="ag-kpi-label">Fleet Slots &amp; Median Loot/h</span>
            <strong className="ag-kpi-val tabular" id="ai-kpi-telemetry">
              {`${startSlots} → ${endSlots} slots`}
            </strong>
            <span className="ag-kpi-sub tabular">
              {lootPerHr !== null
                ? `${fmtCompact(lootPerHr)} SpEq/h · RT ${formatDurationSecondsCompact(medianRtSec)}`
                : "SQLite attack history"}
            </span>
          </div>
        </div>

        {/* Blast Furnace (b2) Initial State & Smelting Summary Strip */}
        <div
          className="ag-smelt-strip"
          id="ag-ai-smelting-strip"
          role="region"
          aria-label="Blast furnace smelting state"
        >
          <div className="ag-smelt-strip-main">
            <span className="module-code tabular">B2</span>
            <span className="ag-smelt-label">Blast furnace:</span>
            <span
              className={`ag-smelt-pill tabular ${
                initLiveMode === "on"
                  ? "is-on"
                  : initLiveMode === "mixed"
                    ? "is-toggle"
                    : "is-off"
              }`}
              id="ai-smelt-live-badge"
            >
              {`Initial ${liveModeLabel}`}
            </span>
            {hasSmeltingTarget ? (
              <>
                <span className="ag-smelt-arrow" aria-hidden="true">
                  →
                </span>
                <span
                  className={`ag-smelt-pill tabular ${
                    initToggleNeeded
                      ? "is-toggle"
                      : initTargetEnabled
                        ? "is-on"
                        : "is-off"
                  }`}
                  id="ai-smelt-target-badge"
                >
                  {`t=0 ${initTargetEnabled ? "ON 100%" : "OFF 0%"}${
                    initToggleNeeded ? " · TOGGLE NEEDED" : " · ALIGNED"
                  }`}
                </span>
              </>
            ) : null}
          </div>
          <div className="ag-smelt-strip-meta tabular">
            {metalSmelted !== null
              ? `Smelted ${fmtCompact(metalSmelted)} Metal · ${smeltingTimeText} · ${smeltingTogglesCount} toggles`
              : "Run AI-Planner to compute smelting schedule"}
          </div>
        </div>

        {/* Adaptive Execution (Plan v2) Switch & Per-Colony Idle Diagnostics Strip */}
        <div
          className="ag-adaptive-strip"
          id="ag-ai-adaptive-strip"
          role="region"
          aria-label="Adaptive execution switch and per-colony idle diagnostics"
        >
          <div className="ag-adaptive-strip-head">
            <div className="ag-adaptive-strip-controls">
              <label className="ag-adaptive-toggle" htmlFor="ai-adaptive-exec-toggle">
                <input
                  type="checkbox"
                  className="config-checkbox"
                  id="ai-adaptive-exec-toggle"
                  checked={adaptiveEnabled}
                  disabled={isTogglingAdaptive}
                  onChange={(e) => void onToggleAdaptiveExecution(e.target.checked)}
                />
                <span className="module-code tabular">V2</span>
                <span className="ag-smelt-label">Adaptive Execution</span>
                <span
                  className={`ag-smelt-pill tabular ${adaptiveEnabled ? "is-on" : "is-off"}`}
                  id="ai-adaptive-exec-state"
                >
                  {adaptiveEnabled ? "ON" : "OFF"}
                </span>
              </label>

              <span
                className={`ag-smelt-pill tabular ${
                  adaptiveTotalColonies > 0 && adaptiveActiveBuilders >= adaptiveTotalColonies
                    ? "is-on"
                    : adaptiveActiveBuilders > 0
                      ? "is-toggle"
                      : "is-off"
                }`}
                id="ai-adaptive-builders-badge"
              >
                {`Builders ${adaptiveActiveBuilders}/${adaptiveTotalColonies}`}
              </span>

              {transportFleet ? (
                <span
                  className={`ag-smelt-pill tabular ${
                    (safeFiniteNumber(transportFleet.target_fleet_cargo, 0) ?? 0) <= 0 ||
                    (safeFiniteNumber(transportFleet.total_fleet_cargo, 0) ?? 0) >=
                      (safeFiniteNumber(transportFleet.target_fleet_cargo, 0) ?? 0)
                      ? "is-on"
                      : "is-toggle"
                  }`}
                  id="ai-adaptive-fleet-badge"
                  title={`Parked: ${fmtInt(transportFleet.owned_parked_cargo)} · In-flight: ${fmtInt(
                    transportFleet.in_flight_cargo
                  )} · Planned 24h flow: ${fmtCompact(transportFleet.planned_cargo_per_hour_24h)}/h · Util: ${Math.round(
                    (safeFiniteNumber(transportFleet.realized_utilization, 0.5) ?? 0.5) * 100
                  )}%`}
                >
                  {`Cargo ${fmtCompact(transportFleet.total_fleet_cargo)} / ${fmtCompact(
                    transportFleet.target_fleet_cargo
                  )}`}
                </span>
              ) : null}

              {hasWindow24hSamples && window24h ? (
                <span
                  className={`ag-smelt-pill tabular ${
                    (safeFiniteNumber(window24h.idle_with_affordable_share_pct, 0) ?? 0) > 15 ||
                    (safeFiniteNumber(window24h.ready_not_started_share_pct, 0) ?? 0) > 5
                      ? "is-toggle"
                      : "is-on"
                  }`}
                  id="ai-adaptive-gate24h-badge"
                  title={`24h Step-5 diagnostics (${window24h.colony_samples} colony samples across ${window24h.bucket_count} x 10m buckets)`}
                >
                  {`Idle w/ affordable: ${formatSharePct(
                    window24h.idle_with_affordable_share_pct
                  )}% · Ready gap: ${formatSharePct(window24h.ready_not_started_share_pct)}%`}
                </span>
              ) : null}
            </div>

            {autoReplan ? (
              <div className="ag-smelt-strip-meta tabular" id="ai-adaptive-replan-badge">
                {`Auto-replan: ${autoReplan.drift_ready ? "Drift ready" : "Cooldown"}${
                  autoReplan.last_trigger_reason ? ` · Last: ${autoReplan.last_trigger_reason}` : ""
                }`}
              </div>
            ) : null}
          </div>

          <div className="ag-adaptive-diag-list" id="ag-ai-colony-diag-list">
            {adaptiveColonies.length === 0 ? (
              <span className="action-sub">
                Colony idle diagnostics appear once AI-Planner state is loaded.
              </span>
            ) : (
              adaptiveColonies.map((col) => {
                const pid = Math.round(safeFiniteNumber(col.planet_id, 0) ?? 0);
                const coords = String(col.coords || "").trim();
                const displayName =
                  customPlanetNames[String(pid)] ||
                  col.name ||
                  planetNameById.get(pid) ||
                  "Colony";
                const stateMeta = formatAdaptiveColonyState(col.state);
                const b2Lvl = Math.round(safeFiniteNumber(col.b2_level, 0) ?? 0);
                const b2Pct = Math.round(safeFiniteNumber(col.b2_pct, 0) ?? 0);
                const marginAt100 = safeFiniteNumber(col.b2_energy_margin_at_100, null);
                const lowEnergyMargin = marginAt100 !== null && marginAt100 < 100;
                const tooltipParts = [
                  col.reason || stateMeta.label,
                  col.next_label ? `Next: ${col.next_label}` : "",
                  col.idle_with_affordable_target_step
                    ? "Affordable target step waiting"
                    : "",
                  lowEnergyMargin
                    ? `Energy margin at 100% b2: ${Math.round(marginAt100!)}`
                    : "",
                ].filter(Boolean);

                return (
                  <div
                    key={`${pid}-${coords || displayName}`}
                    className={`ag-adaptive-chip ${stateMeta.tone}`}
                    data-planet-id={pid}
                    data-colony-state={col.state}
                    title={tooltipParts.join(" · ")}
                  >
                    <span className="ag-adaptive-chip-name tabular">
                      {coords ? `${displayName} [${coords}]` : displayName}
                    </span>
                    <span className={`ag-adaptive-state-pill tabular ${stateMeta.tone}`}>
                      {stateMeta.label}
                    </span>
                    <span className="ag-adaptive-b2-pill tabular">
                      {b2Lvl > 0 ? `b2 ${b2Pct}%` : "no b2"}
                    </span>
                    {lowEnergyMargin ? (
                      <span className="ag-adaptive-energy-warn tabular">
                        {`Low E (${Math.round(marginAt100!)})`}
                      </span>
                    ) : null}
                  </div>
                );
              })
            )}
          </div>
        </div>

        {/* Subview 1: Windowed DAG Canvas */}
        <div className="ag-ai-subpane" id="ai-view-dag" hidden={subView !== "dag"}>
          <div className="ag-ai-window-bar" id="ag-ai-window-controls">
            <div className="inline-actions">
              <div className="tab-group" role="group" aria-label="DAG window slice">
                <button
                  type="button"
                  className={`tab-btn${windowSize === 40 ? " active" : ""}`}
                  id="ai-win-40"
                  onClick={() => {
                    setWindowSize(40);
                    void fetchAiState({ windowOverride: 40 });
                  }}
                >
                  Next 40 Steps
                </button>
                <button
                  type="button"
                  className={`tab-btn${windowSize > 40 ? " active" : ""}`}
                  id="ai-win-200"
                  onClick={() => {
                    setWindowSize(200);
                    void fetchAiState({ windowOverride: 200 });
                  }}
                >
                  Extended (200)
                </button>
              </div>
              <label className="ag-inline-filter tabular" htmlFor="ai-filter-week">
                Week
                <select
                  id="ai-filter-week"
                  value={weekFilter}
                  onChange={(e) => {
                    const nextWk = e.target.value;
                    setWeekFilter(nextWk);
                    setStepsPage(1);
                    void fetchAiState({ weekOverride: nextWk, pageOverride: 1 });
                  }}
                >
                  <option value="all">{`All (W1–W${rewardWeeks})`}</option>
                  {Array.from({ length: rewardWeeks }, (_, i) => i + 1).map((w) => (
                    <option key={w} value={String(w)}>
                      {`W${w}`}
                    </option>
                  ))}
                </select>
              </label>
              <label className="ag-inline-filter tabular" htmlFor="ai-filter-planet">
                Planet
                <select
                  id="ai-filter-planet"
                  value={planetFilter}
                  onChange={(e) => {
                    const nextPl = e.target.value;
                    setPlanetFilter(nextPl);
                    setStepsPage(1);
                    void fetchAiState({ planetOverride: nextPl, pageOverride: 1 });
                  }}
                >
                  <option value="all">All Planets</option>
                  {planets.map((p) => (
                    <option key={p.planet_id} value={String(p.planet_id)}>
                      {`${p.name || "Colony"} [${p.coords || p.planet_id}]`}
                    </option>
                  ))}
                </select>
              </label>
            </div>
          </div>

          <DagCanvas
            graph={aiData?.graph || null}
            idPrefix="ag-ai"
            markerPrefix="ag-ai"
            emptyMessage="No AI-Planner path generated yet. Run Config Optimization or click Run AI-Planner."
          />
        </div>

        {/* Subview 2: Sequential Path Table */}
        <div className="ag-ai-subpane" id="ai-view-steps" hidden={subView !== "steps"}>
          <div className="ag-ai-window-bar">
            <div className="inline-actions">
              <input
                type="search"
                id="ag-ai-steps-search"
                className="table-search-input"
                placeholder="Search steps (planet, building, research)..."
                value={stepsSearch}
                onChange={(e) => {
                  const nextQ = e.target.value;
                  setStepsSearch(nextQ);
                  setStepsPage(1);
                  void fetchAiState({ searchOverride: nextQ, pageOverride: 1, silent: true });
                }}
              />
            </div>
            <span className="module-meta tabular">
              {`${aiData?.steps_page?.total ?? stepSort.sortedRows.length} total actions`}
            </span>
          </div>

          <div className="table-wrap table-scroll">
            <table id="ag-ai-steps-table">
              <thead>
                <tr>
                  <SortableTh
                    label="#"
                    sortKey="seq"
                    activeKey={stepSort.sortKey}
                    direction={stepSort.sortDirection}
                    onSort={stepSort.requestSort}
                  />
                  <SortableTh
                    label="Week"
                    sortKey="week"
                    activeKey={stepSort.sortKey}
                    direction={stepSort.sortDirection}
                    onSort={stepSort.requestSort}
                  />
                  <SortableTh
                    label="Planet"
                    sortKey="planet"
                    activeKey={stepSort.sortKey}
                    direction={stepSort.sortDirection}
                    onSort={stepSort.requestSort}
                  />
                  <SortableTh
                    label="Action"
                    sortKey="action"
                    activeKey={stepSort.sortKey}
                    direction={stepSort.sortDirection}
                    onSort={stepSort.requestSort}
                  />
                  <th>Start</th>
                  <SortableTh
                    label="Duration"
                    sortKey="duration"
                    activeKey={stepSort.sortKey}
                    direction={stepSort.sortDirection}
                    onSort={stepSort.requestSort}
                    align="right"
                  />
                  <SortableTh
                    label="Status"
                    sortKey="status"
                    activeKey={stepSort.sortKey}
                    direction={stepSort.sortDirection}
                    onSort={stepSort.requestSort}
                  />
                </tr>
              </thead>
              <tbody id="ag-ai-steps-tbody">
                {stepSort.sortedRows.length === 0 ? (
                  <tr>
                    <td colSpan={7} className="empty-cell">
                      No AI-Planner steps matching current filter.
                    </td>
                  </tr>
                ) : (
                  stepSort.sortedRows.map((s, idx) => {
                    const seqNum = Math.round(safeFiniteNumber(s.seq ?? s.order, idx + 1) ?? idx + 1);
                    const wkNum = Math.round(safeFiniteNumber(s.week, 1) ?? 1);
                    const stStr = String(s.status || "queued").toUpperCase();
                    return (
                      <tr key={`${seqNum}-${s.entity_key || idx}`}>
                        <td className="tabular">{`#${seqNum}`}</td>
                        <td className="tabular">{`W${wkNum}`}</td>
                        <td className="tabular">
                          {s.planet_coords
                            ? `${s.planet_name || "Colony"} [${s.planet_coords}]`
                            : "Account"}
                        </td>
                        <td>
                          <span className="action-title">
                            {`${s.kind || "BUILD"} · ${s.entity_name || s.entity_key || "Step"}${s.target_level != null ? ` → L${s.target_level}` : ""}`}
                          </span>
                        </td>
                        <td className="tabular">
                          <div>{s.start || formatDurationSecondsCompact(s.start_sec)}</div>
                          {s.needs_transport ? (
                            <div className="action-sub">
                              {`Ship at ${s.transport_request_text || formatDurationSecondsCompact(s.transport_request_at_sec)}`}
                            </div>
                          ) : null}
                        </td>
                        <td className="col-right tabular">
                          {s.duration_text || formatDurationSecondsCompact(s.duration_sec)}
                        </td>
                        <td>
                          <span className={`eta-pill tabular${stStr === "COMPLETED" ? "" : " is-wait"}`}>
                            {stStr}
                          </span>
                        </td>
                      </tr>
                    );
                  })
                )}
              </tbody>
            </table>
          </div>

          <TablePagination
            idPrefix="ag-ai-steps"
            totalItems={Math.max(
              stepSort.sortedRows.length,
              Math.round(safeFiniteNumber(aiData?.steps_page?.total, 0) ?? 0)
            )}
            page={stepsPage}
            pageSize={stepsPageSize}
            itemLabel="steps"
            onPageChange={(nextP) => {
              setStepsPage(nextP);
              void fetchAiState({ pageOverride: nextP });
            }}
            onPageSizeChange={(nextSz) => {
              setStepsPageSize(nextSz);
              setStepsPage(1);
              void fetchAiState({ pageOverride: 1, pageSizeOverride: nextSz });
            }}
          />
        </div>

        {/* Subview 3: Weekly Production Breakdown */}
        <div className="ag-ai-subpane" id="ai-view-weekly" hidden={subView !== "weekly"}>
          <div className="table-wrap table-scroll">
            <table id="ag-ai-weekly-table">
              <thead>
                <tr>
                  <SortableTh
                    label="Week"
                    sortKey="week"
                    activeKey={weekSort.sortKey}
                    direction={weekSort.sortDirection}
                    onSort={weekSort.requestSort}
                  />
                  <SortableTh
                    label="Discount γ^(w-1)"
                    sortKey="weight"
                    activeKey={weekSort.sortKey}
                    direction={weekSort.sortDirection}
                    onSort={weekSort.requestSort}
                    align="right"
                  />
                  <th className="col-right">Pig Iron</th>
                  <th className="col-right">Metal</th>
                  <th className="col-right">Kryptonite</th>
                  <th className="col-right">Spice</th>
                  <SortableTh
                    label="Weekly SpiceEq"
                    sortKey="spiceEq"
                    activeKey={weekSort.sortKey}
                    direction={weekSort.sortDirection}
                    onSort={weekSort.requestSort}
                    align="right"
                  />
                  <SortableTh
                    label="Discounted"
                    sortKey="discounted"
                    activeKey={weekSort.sortKey}
                    direction={weekSort.sortDirection}
                    onSort={weekSort.requestSort}
                    align="right"
                  />
                  <SortableTh
                    label="Cumulative"
                    sortKey="cumulative"
                    activeKey={weekSort.sortKey}
                    direction={weekSort.sortDirection}
                    onSort={weekSort.requestSort}
                    align="right"
                  />
                </tr>
              </thead>
              <tbody id="ag-ai-weekly-tbody">
                {weekSort.sortedRows.length === 0 ? (
                  <tr>
                    <td colSpan={9} className="empty-cell">
                      No weekly production breakdown available yet.
                    </td>
                  </tr>
                ) : (
                  weekSort.sortedRows.map((w) => (
                    <tr key={String(w.week)}>
                      <td className="tabular">{`W${w.week}`}</td>
                      <td className="col-right tabular">
                        {Number(safeFiniteNumber(w.discount_weight, 1) ?? 1).toFixed(4)}
                      </td>
                      <td className="col-right tabular">{fmtCompact(w.pig_iron)}</td>
                      <td className="col-right tabular">{fmtCompact(w.metal)}</td>
                      <td className="col-right tabular">{fmtCompact(w.kryptonite)}</td>
                      <td className="col-right tabular">{fmtCompact(w.spice)}</td>
                      <td className="col-right tabular">{fmtCompact(w.spice_eq)}</td>
                      <td className="col-right tabular">{fmtCompact(w.discounted_spice_eq)}</td>
                      <td className="col-right tabular">
                        {fmtCompact(w.cumulative_discounted_spice_eq ?? w.cumulative)}
                      </td>
                    </tr>
                  ))
                )}
              </tbody>
            </table>
          </div>
        </div>

        {/* Subview 4: Blast Furnace (b2) Initial State & Smelting Toggle Schedule */}
        <div className="ag-ai-subpane" id="ai-view-smelting" hidden={subView !== "smelting"}>
          <div className="ag-smelt-planets-bar" id="ag-ai-smelting-planets">
            <span className="ag-smelt-label">Initial Colony b2 Snapshot:</span>
            {smeltingPlanets.length === 0 ? (
              <span className="action-sub">No colonized planets captured yet.</span>
            ) : (
              <div className="ag-smelt-planet-chips">
                {smeltingPlanets.map((pl) => {
                  const pending = toggleByPlanetId.get(pl.planet_id);
                  const chipTone =
                    pl.b2_level <= 0
                      ? "is-off"
                      : pending
                        ? "is-toggle"
                        : pl.initial_enabled
                          ? "is-on"
                          : "is-off";
                  return (
                    <span
                      key={`${pl.planet_id}-${pl.coords}`}
                      className={`ag-smelt-pill tabular ${chipTone}`}
                    >
                      {pl.b2_level <= 0
                        ? `${pl.name || "Colony"} [${pl.coords}] · No b2`
                        : `${pl.name || "Colony"} [${pl.coords}] · b2 L${pl.b2_level} · ${pl.initial_pct}%${
                            pending ? ` → ${pending.to_pct}%` : ""
                          }`}
                    </span>
                  );
                })}
              </div>
            )}
          </div>

          <div className="table-wrap table-scroll">
            <table id="ag-ai-smelting-table">
              <thead>
                <tr>
                  <th>#</th>
                  <th>Time</th>
                  <th>Week</th>
                  <th>Action</th>
                  <th>From</th>
                  <th>Driving Step</th>
                  <th>Reason</th>
                </tr>
              </thead>
              <tbody id="ag-ai-smelting-tbody">
                {smeltingEvents.length === 0 ? (
                  <tr>
                    <td colSpan={7} className="empty-cell">
                      No Blast furnace (b2) toggle transitions scheduled in this plan.
                    </td>
                  </tr>
                ) : (
                  smeltingEvents.map((ev) => {
                    const isOn = Boolean(ev.enabled);
                    return (
                      <tr key={`smelt-${ev.seq}-${ev.time_sec}`}>
                        <td className="tabular">{`#${ev.seq}`}</td>
                        <td className="tabular">{ev.time_text || "now"}</td>
                        <td className="tabular">{`W${ev.week}`}</td>
                        <td>
                          <span className={`ag-smelt-pill tabular ${isOn ? "is-on" : "is-toggle"}`}>
                            {isOn ? "ENABLE 100%" : "DISABLE 0%"}
                          </span>
                          {ev.is_initial ? (
                            <span className="ag-smelt-init-tag tabular">t=0 LIVE</span>
                          ) : null}
                        </td>
                        <td className="tabular">{String(ev.from_mode || "off").toUpperCase()}</td>
                        <td className="tabular">
                          {ev.step_label
                            ? `${ev.step_label}${ev.step_planet_coords ? ` [${ev.step_planet_coords}]` : ""}`
                            : "—"}
                        </td>
                        <td>{ev.reason || "—"}</td>
                      </tr>
                    );
                  })
                )}
              </tbody>
            </table>
          </div>
        </div>
      </article>

      <aside
        className="hud-window ag-control-panel"
        id="ag-ai-control-panel"
        aria-label="AI-Planner parameters"
      >
        <div className="window-bar window-bar-wrap">
          <div className="window-title-group">
            <span className="module-code tabular">PTH</span>
            <h2>AI-Planner Parameters</h2>
          </div>
          <span
            className={`ag-dirty-badge tabular ${draftDirty ? "is-dirty" : "is-synced"}`}
            id="ai-dirty-badge"
          >
            {draftDirty ? "MODIFIED" : "SYNCED"}
          </span>
        </div>

        <div className="ag-panel-tabs" role="tablist" aria-label="AI-Planner parameter tabs">
          <button
            type="button"
            className={`tab-btn${paramSubTab === "horizon" ? " active" : ""}`}
            role="tab"
            aria-selected={paramSubTab === "horizon"}
            id="ai-ptab-horizon"
            onClick={() => setParamSubTab("horizon")}
          >
            Horizon &amp; Spare %
          </button>
          <button
            type="button"
            className={`tab-btn${paramSubTab === "weights" ? " active" : ""}`}
            role="tab"
            aria-selected={paramSubTab === "weights"}
            id="ai-ptab-weights"
            onClick={() => setParamSubTab("weights")}
          >
            Weights &amp; GA
          </button>
        </div>

        <div className="ag-panel-body">
          <div className="ag-param-pane" hidden={paramSubTab !== "horizon"}>
            <div className="ag-param-group">
              <div className="ag-param-group-head">
                <h3>Reward Horizon &amp; Discounting</h3>
                <p className="ag-param-group-sub">{AI_PLANNER_GROUP_SUBTITLES.horizon}</p>
              </div>
              <div className="ag-param-list">
                <ActionGraphParamRow
                  doc={AI_PLANNER_PARAM_DOCS.reward_weeks}
                  htmlFor="ai-param-reward_weeks"
                  layout="inline"
                >
                  <input
                    type="number"
                    id="ai-param-reward_weeks"
                    min={1}
                    max={104}
                    step={1}
                    value={draft.reward_weeks}
                    onChange={(e) => {
                      setDraftDirty(true);
                      setDraft((prev) => ({ ...prev, reward_weeks: e.target.value }));
                    }}
                  />
                </ActionGraphParamRow>

                <ActionGraphParamRow
                  doc={AI_PLANNER_PARAM_DOCS.weekly_discount}
                  htmlFor="ai-param-weekly_discount"
                  layout="inline"
                >
                  <input
                    type="number"
                    id="ai-param-weekly_discount"
                    min={0.05}
                    max={1.0}
                    step={0.05}
                    value={draft.weekly_discount}
                    onChange={(e) => {
                      setDraftDirty(true);
                      setDraft((prev) => ({ ...prev, weekly_discount: e.target.value }));
                    }}
                  />
                </ActionGraphParamRow>

                <ActionGraphParamRow
                  doc={AI_PLANNER_PARAM_DOCS.transport_dispatch_delay_minutes}
                  htmlFor="ai-param-transport_dispatch_delay_minutes"
                  layout="inline"
                >
                  <input
                    type="number"
                    id="ai-param-transport_dispatch_delay_minutes"
                    min={0}
                    max={1440}
                    step={5}
                    value={draft.transport_dispatch_delay_minutes}
                    onChange={(e) => {
                      setDraftDirty(true);
                      setDraft((prev) => ({
                        ...prev,
                        transport_dispatch_delay_minutes: e.target.value,
                      }));
                    }}
                  />
                </ActionGraphParamRow>
              </div>
            </div>

            <div className="ag-param-group">
              <div className="ag-param-group-head">
                <h3>Per-Resource Spare % (Fleet &amp; Raids)</h3>
                <p className="ag-param-group-sub">{AI_PLANNER_GROUP_SUBTITLES.sparePct}</p>
              </div>
              <div className="ag-param-list">
                {(["pig_iron", "metal", "kryptonite", "spice"] as const).map((rk) => (
                  <ActionGraphParamRow
                    key={rk}
                    doc={AI_PLANNER_SPARE_DOCS[rk]}
                    htmlFor={`ai-param-spare-${rk}`}
                    layout="inline"
                  >
                    <input
                      type="number"
                      id={`ai-param-spare-${rk}`}
                      min={0}
                      max={100}
                      step={1}
                      value={draft.resource_spare_percent[rk]}
                      onChange={(e) => {
                        setDraftDirty(true);
                        setDraft((prev) => ({
                          ...prev,
                          resource_spare_percent: {
                            ...prev.resource_spare_percent,
                            [rk]: e.target.value,
                          },
                        }));
                      }}
                    />
                  </ActionGraphParamRow>
                ))}
              </div>
            </div>

            <div className="ag-param-group">
              <div className="ag-param-group-head">
                <h3>Attack Telemetry &amp; Optional Overrides</h3>
                <p className="ag-param-group-sub">{AI_PLANNER_GROUP_SUBTITLES.telemetry}</p>
              </div>
              <div className="ag-telemetry-box tabular" id="ai-sqlite-telemetry-box">
                <div>
                  {`Median Loot: ${medianLoot !== null ? `${fmtInt(Math.round(medianLoot))} SpEq` : "Fallback defaults"}`}
                </div>
                <div>
                  {`Median RT: ${formatDurationSecondsCompact(medianRtSec)} · Samples: ${fmtInt(Math.round(safeFiniteNumber(telemetry.sample_count, 0) ?? 0))}`}
                </div>
              </div>
              <div className="ag-param-list">
                <ActionGraphParamRow
                  doc={AI_PLANNER_PARAM_DOCS.fleet_command_center_level}
                  htmlFor="ai-param-fleet_command_center_level"
                  layout="inline"
                >
                  <input
                    type="number"
                    id="ai-param-fleet_command_center_level"
                    min={0}
                    max={50}
                    step={1}
                    placeholder="Auto"
                    value={draft.fleet_command_center_level}
                    onChange={(e) => {
                      setDraftDirty(true);
                      setDraft((prev) => ({
                        ...prev,
                        fleet_command_center_level: e.target.value,
                      }));
                    }}
                  />
                </ActionGraphParamRow>

                <ActionGraphParamRow
                  doc={AI_PLANNER_PARAM_DOCS.attacks_per_day}
                  htmlFor="ai-param-attacks_per_day"
                  layout="inline"
                >
                  <input
                    type="number"
                    id="ai-param-attacks_per_day"
                    min={0}
                    step={1}
                    placeholder="Auto"
                    value={draft.attacks_per_day}
                    onChange={(e) => {
                      setDraftDirty(true);
                      setDraft((prev) => ({ ...prev, attacks_per_day: e.target.value }));
                    }}
                  />
                </ActionGraphParamRow>

                <ActionGraphParamRow
                  doc={AI_PLANNER_PARAM_DOCS.pad_generic_colonies}
                  htmlFor="ai-param-pad_generic_colonies"
                  layout="toggle"
                  titleOverride={`Pad < ${gate.maxPlanets}/${gate.maxPlanets} Missing Slots in Preview (${gate.coloniesOwned}/${gate.maxPlanets} Owned)`}
                >
                  <input
                    type="checkbox"
                    className="config-checkbox"
                    id="ai-param-pad_generic_colonies"
                    checked={draft.pad_generic_colonies}
                    onChange={(e) => {
                      const checked = e.target.checked;
                      setDraftDirty(true);
                      setDraft((prev) => ({ ...prev, pad_generic_colonies: checked }));
                    }}
                  />
                </ActionGraphParamRow>
              </div>
            </div>
          </div>

          <div className="ag-param-pane" hidden={paramSubTab !== "weights"}>
            <div className="ag-param-group">
              <div className="ag-param-group-head">
                <h3>Path Strategy Weights</h3>
                <p className="ag-param-group-sub">{AI_PLANNER_GROUP_SUBTITLES.weights}</p>
              </div>
              <div className="ag-param-list">
                <ActionGraphParamRow
                  doc={AI_PLANNER_PARAM_DOCS.scarcity_focus}
                  htmlFor="ai-param-scarcity_focus"
                  layout="inline"
                >
                  <input
                    type="number"
                    id="ai-param-scarcity_focus"
                    min={0}
                    max={10}
                    step={0.1}
                    value={draft.scarcity_focus}
                    onChange={(e) => {
                      setDraftDirty(true);
                      setDraft((prev) => ({ ...prev, scarcity_focus: e.target.value }));
                    }}
                  />
                </ActionGraphParamRow>

                <ActionGraphParamRow
                  doc={AI_PLANNER_PARAM_DOCS.critical_queue_share}
                  htmlFor="ai-param-critical_queue_share"
                  layout="inline"
                >
                  <input
                    type="number"
                    id="ai-param-critical_queue_share"
                    min={0}
                    max={1}
                    step={0.05}
                    value={draft.critical_queue_share}
                    onChange={(e) => {
                      setDraftDirty(true);
                      setDraft((prev) => ({ ...prev, critical_queue_share: e.target.value }));
                    }}
                  />
                </ActionGraphParamRow>

                <ActionGraphParamRow
                  doc={AI_PLANNER_PARAM_DOCS.patience}
                  htmlFor="ai-param-patience"
                  layout="inline"
                >
                  <input
                    type="number"
                    id="ai-param-patience"
                    min={0}
                    max={5}
                    step={0.05}
                    value={draft.patience}
                    onChange={(e) => {
                      setDraftDirty(true);
                      setDraft((prev) => ({ ...prev, patience: e.target.value }));
                    }}
                  />
                </ActionGraphParamRow>

                <ActionGraphParamRow
                  doc={AI_PLANNER_PARAM_DOCS.speed_weight}
                  htmlFor="ai-param-speed_weight"
                  layout="inline"
                >
                  <input
                    type="number"
                    id="ai-param-speed_weight"
                    min={0}
                    max={10}
                    step={0.1}
                    value={draft.speed_weight}
                    onChange={(e) => {
                      setDraftDirty(true);
                      setDraft((prev) => ({ ...prev, speed_weight: e.target.value }));
                    }}
                  />
                </ActionGraphParamRow>
              </div>
            </div>

            <div className="ag-param-group">
              <div className="ag-param-group-head">
                <h3>GA Path Search</h3>
                <p className="ag-param-group-sub">{AI_PLANNER_GROUP_SUBTITLES.gaSearch}</p>
              </div>
              <div className="ag-param-list">
                <ActionGraphParamRow
                  doc={AI_PLANNER_PARAM_DOCS.ga_population_size}
                  htmlFor="ai-param-ga_population_size"
                  layout="inline"
                >
                  <input
                    type="number"
                    id="ai-param-ga_population_size"
                    min={4}
                    max={200}
                    step={2}
                    value={draft.ga_population_size}
                    onChange={(e) => {
                      setDraftDirty(true);
                      setDraft((prev) => ({ ...prev, ga_population_size: e.target.value }));
                    }}
                  />
                </ActionGraphParamRow>

                <ActionGraphParamRow
                  doc={AI_PLANNER_PARAM_DOCS.ga_generations}
                  htmlFor="ai-param-ga_generations"
                  layout="inline"
                >
                  <input
                    type="number"
                    id="ai-param-ga_generations"
                    min={1}
                    max={100}
                    step={1}
                    value={draft.ga_generations}
                    onChange={(e) => {
                      setDraftDirty(true);
                      setDraft((prev) => ({ ...prev, ga_generations: e.target.value }));
                    }}
                  />
                </ActionGraphParamRow>

                <ActionGraphParamRow
                  doc={AI_PLANNER_PARAM_DOCS.ga_random_seed}
                  htmlFor="ai-param-ga_random_seed"
                  layout="inline"
                >
                  <input
                    type="number"
                    id="ai-param-ga_random_seed"
                    step={1}
                    value={draft.ga_random_seed}
                    onChange={(e) => {
                      setDraftDirty(true);
                      setDraft((prev) => ({ ...prev, ga_random_seed: e.target.value }));
                    }}
                  />
                </ActionGraphParamRow>
              </div>
            </div>
          </div>
        </div>

        <div className="ag-panel-footer">
          {isJobRunning ? (
            <div className="ag-job-progress" id="ai-job-progress">
              <div className="ag-job-progress-meta tabular">
                <span>{String(activeJob?.phase || "Running AI-Planner...")}</span>
                <span>
                  {`${Math.max(0, Math.min(100, Math.round(safeFiniteNumber(activeJob?.progress_pct, 0) ?? 0)))}%`}
                </span>
              </div>
              <div className="meter-track">
                <div
                  className="meter-fill gann-bar-cyan"
                  style={{
                    transform: `scaleX(${Math.max(0, Math.min(100, safeFiniteNumber(activeJob?.progress_pct, 0) ?? 0)) / 100})`,
                  }}
                />
              </div>
            </div>
          ) : (
            <p className="ag-sim-note" id="ai-activate-gate-note">
              {gate.liveAiUnlocked
                ? `Colony gate satisfied (${gate.coloniesOwned}/${gate.maxPlanets}). Ready for live bot execution.`
                : `Live execution locked until ${gate.maxPlanets}/${gate.maxPlanets} colonies are founded (${gate.coloniesOwned}/${gate.maxPlanets} owned).`}
            </p>
          )}
          <div className="ag-footer-actions">
            <button
              type="button"
              className="btn btn-secondary btn-sm"
              id="btn-ai-run"
              disabled={isSubmitting || isJobRunning}
              onClick={onRunAiPlanner}
            >
              {isJobRunning ? "Running AI-Planner..." : "Run AI-Planner"}
            </button>
            <button
              type="button"
              className="btn btn-secondary btn-sm"
              id="btn-ai-export-footer"
              onClick={onExportTxt}
            >
              Export .txt
            </button>
            <button
              type="button"
              className="btn btn-primary btn-sm btn-span"
              id="btn-ai-activate"
              disabled={!gate.liveAiUnlocked}
              title={
                gate.liveAiUnlocked
                  ? "Activate Live AI-Planner execution"
                  : `Unlocks at ${gate.maxPlanets}/${gate.maxPlanets} founded colonies`
              }
              onClick={onActivateAiMode}
            >
              {isLiveAiActive
                ? "Live AI-Planner Mode Active"
                : gate.liveAiUnlocked
                  ? "Activate Live AI-Planner Mode"
                  : `Activate Live AI-Planner (${gate.coloniesOwned}/${gate.maxPlanets} Locked)`}
            </button>
          </div>
        </div>
      </aside>
    </div>
  );
};

export default AiPlannerTab;
