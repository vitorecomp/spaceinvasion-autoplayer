import React, { useCallback, useEffect, useMemo, useState } from "react";
import { feedbackKindForError, safeFiniteNumber } from "../../api/httpClient";
import {
  useActivePage,
  useApiOnline,
  useConsoleActions,
  useDashboard,
  usePlannerJobsStatus,
} from "../../store";
import type {
  AiPlannerAdaptiveStatus,
  AiPlannerColonyDiag,
  AiPlannerGetPayload,
  AiPlannerStepItem,
  FieldConflictItem,
  PlannerJobState,
} from "../../types/api";
import {
  buildAiParamsPayload,
  countFallbackActiveBuilders,
  deriveAiPlannerSummaryMetrics,
  draftFromAiParams,
} from "./aiPlannerHelpers";
import { AiPlannerParamPanel } from "./AiPlannerParamPanel";
import { AiPlannerStatusStrips } from "./AiPlannerStatusStrips";
import { AiPlannerSubView, AiPlannerSubviews } from "./AiPlannerSubviews";
import {
  AiPlannerDraft,
  DEFAULT_AI_PLANNER_DRAFT,
  computeColonyGateInfo,
  formatJobBadgeText,
  normalizeUiPlannerMode,
} from "./plannerHelpers";

export interface AiPlannerTabProps {
  hidden?: boolean;
}

export const AiPlannerTab: React.FC<AiPlannerTabProps> = ({ hidden = false }) => {
  const apiOnline = useApiOnline();
  const dashboard = useDashboard();
  const activePage = useActivePage();
  const plannerJobsStatus = usePlannerJobsStatus();
  const {
    setDashboard,
    setPlannerJobsStatus,
    apiCommand,
    loadDashboard,
    showFeedback,
    getSnapshot,
    bumpConfigHydration,
  } = useConsoleActions();

  const [aiData, setAiData] = useState<AiPlannerGetPayload | null>(null);
  const [draft, setDraft] = useState<AiPlannerDraft>(() =>
    draftFromAiParams(DEFAULT_AI_PLANNER_DRAFT)
  );
  const [draftDirty, setDraftDirty] = useState<boolean>(false);
  const [paramSubTab, setParamSubTab] = useState<"horizon" | "weights">("horizon");
  const [subView, setSubView] = useState<AiPlannerSubView>("dag");

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
  }, [
    aiData?.pending_field_conflicts,
    dashboard?.pending_field_conflicts,
    plannerJobsStatus?.pending_field_conflicts,
  ]);

  const rawSteps: AiPlannerStepItem[] = useMemo(() => {
    const items = aiData?.steps_page?.items;
    return Array.isArray(items) ? items : [];
  }, [aiData?.steps_page?.items]);

  const weeklyRows: Array<Record<string, any>> = useMemo(
    () => (Array.isArray(aiData?.weekly_breakdown) ? aiData!.weekly_breakdown! : []),
    [aiData]
  );

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
        const curDash = getSnapshot().dashboard || dashboard;
        if (curDash) {
          setDashboard({
            ...curDash,
            config: res.config,
            planner_mode: res.config.planner_mode || "ai_planner",
            effective_planner_mode:
              res.effective_planner_mode ?? res.config.planner_mode ?? "ai_planner",
            fallback_reason: res.fallback_reason ?? null,
            live_ai_unlocked: res.live_ai_unlocked ?? curDash.live_ai_unlocked,
          });
        }
        bumpConfigHydration();
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
        setPlannerJobsStatus({ ...(plannerJobsStatus || {}), ai_planner_job: res.job });
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
        const curDash = getSnapshot().dashboard || dashboard;
        setDashboard(curDash ? { ...curDash, config: res.config } : { config: res.config });
        bumpConfigHydration();
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

  const planets = Array.isArray(dashboard?.state?.planets) ? dashboard!.state!.planets! : [];
  const {
    summary,
    telemetry,
    rewardWeeks,
    discReward,
    totalNetOutput,
    stepsCompleted,
    stepsTotal,
    startSlots,
    endSlots,
    lootPerHr,
    medianLoot,
    medianRtSec,
    inProgBuilds,
    inProgRes,
    dispatchDelayMin,
    smeltingEvents,
    smeltingPlanets,
    initLiveMode,
    hasSmeltingTarget,
    initTargetEnabled,
    initToggleNeeded,
    metalSmelted,
    smeltingTimeText,
    smeltingTogglesCount,
    toggleByPlanetId,
    liveModeLabel,
  } = deriveAiPlannerSummaryMetrics(aiData, draft);

  const customPlanetNames = useMemo<Record<string, string>>(() => {
    const out: Record<string, string> = {};
    for (const src of [
      (dashboard as Record<string, any> | null | undefined)?.custom_planet_names,
      (aiData as Record<string, any> | null | undefined)?.custom_planet_names,
    ]) {
      if (!src || typeof src !== "object") continue;
      for (const [k, v] of Object.entries(src as Record<string, unknown>)) {
        const clean = String(v || "").trim();
        if (clean) out[String(k)] = clean;
      }
    }
    return out;
  }, [aiData, dashboard]);

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
              id="btn-ai-refresh"
              onClick={() => fetchAiState({ silent: false })}
            >
              Refresh
            </button>
          </div>
        </div>

        <AiPlannerStatusStrips
          gate={gate}
          isLiveAiActive={isLiveAiActive}
          conflicts={conflicts}
          selectedCandidateByConflict={selectedCandidateByConflict}
          onSelectConflictCandidate={(conflictKey, bldKey) =>
            setSelectedCandidateByConflict((prev) => ({ ...prev, [conflictKey]: bldKey }))
          }
          resolvingConflictKey={resolvingConflictKey}
          onResolveConflict={(c, dec) => void onResolveConflict(c, dec)}
          summary={summary}
          rewardWeeks={rewardWeeks}
          weeklyDiscount={draft.weekly_discount}
          discReward={discReward}
          totalNetOutput={totalNetOutput}
          stepsCompleted={stepsCompleted}
          stepsTotal={stepsTotal}
          inProgTotal={inProgBuilds + inProgRes}
          dispatchDelayMin={dispatchDelayMin}
          startSlots={startSlots}
          endSlots={endSlots}
          lootPerHr={lootPerHr}
          medianRtSec={medianRtSec}
          initLiveMode={initLiveMode}
          liveModeLabel={liveModeLabel}
          hasSmeltingTarget={hasSmeltingTarget}
          initTargetEnabled={initTargetEnabled}
          initToggleNeeded={initToggleNeeded}
          metalSmelted={metalSmelted}
          smeltingTimeText={smeltingTimeText}
          smeltingTogglesCount={smeltingTogglesCount}
          adaptiveEnabled={adaptiveEnabled}
          isTogglingAdaptive={isTogglingAdaptive}
          onToggleAdaptiveExecution={(nextVal) => void onToggleAdaptiveExecution(nextVal)}
          adaptiveActiveBuilders={adaptiveActiveBuilders}
          adaptiveTotalColonies={adaptiveTotalColonies}
          adaptiveStatus={adaptiveStatus}
          adaptiveColonies={adaptiveColonies}
          customPlanetNames={customPlanetNames}
          planetNameById={planetNameById}
        />

        <AiPlannerSubviews
          subView={subView}
          aiData={aiData}
          planets={planets}
          rewardWeeks={rewardWeeks}
          windowSize={windowSize}
          weekFilter={weekFilter}
          planetFilter={planetFilter}
          stepsSearch={stepsSearch}
          stepsPage={stepsPage}
          stepsPageSize={stepsPageSize}
          rawSteps={rawSteps}
          weeklyRows={weeklyRows}
          smeltingPlanets={smeltingPlanets}
          smeltingEvents={smeltingEvents}
          toggleByPlanetId={toggleByPlanetId}
          onChangeWindowSize={(next) => {
            setWindowSize(next);
            void fetchAiState({ windowOverride: next });
          }}
          onChangeWeekFilter={(nextWk) => {
            setWeekFilter(nextWk);
            setStepsPage(1);
            void fetchAiState({ weekOverride: nextWk, pageOverride: 1 });
          }}
          onChangePlanetFilter={(nextPl) => {
            setPlanetFilter(nextPl);
            setStepsPage(1);
            void fetchAiState({ planetOverride: nextPl, pageOverride: 1 });
          }}
          onChangeStepsSearch={(nextQ) => {
            setStepsSearch(nextQ);
            setStepsPage(1);
            void fetchAiState({ searchOverride: nextQ, pageOverride: 1, silent: true });
          }}
          onChangeStepsPage={(nextP) => {
            setStepsPage(nextP);
            void fetchAiState({ pageOverride: nextP });
          }}
          onChangeStepsPageSize={(nextSz) => {
            setStepsPageSize(nextSz);
            setStepsPage(1);
            void fetchAiState({ pageOverride: 1, pageSizeOverride: nextSz });
          }}
        />
      </article>

      <AiPlannerParamPanel
        draft={draft}
        draftDirty={draftDirty}
        paramSubTab={paramSubTab}
        onSelectParamSubTab={setParamSubTab}
        onUpdateDraft={(updater) => {
          setDraftDirty(true);
          setDraft(updater);
        }}
        telemetry={telemetry}
        medianLoot={medianLoot}
        medianRtSec={medianRtSec}
        gate={gate}
        isJobRunning={isJobRunning}
        activeJob={activeJob}
        isSubmitting={isSubmitting}
        isLiveAiActive={isLiveAiActive}
        onRunAiPlanner={() => void onRunAiPlanner()}
        onActivateAiMode={() => void onActivateAiMode()}
      />
    </div>
  );
};

export default AiPlannerTab;
