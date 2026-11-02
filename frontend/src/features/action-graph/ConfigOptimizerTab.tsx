import React, { useCallback, useEffect, useMemo, useState } from "react";
import { feedbackKindForError, safeFiniteNumber } from "../../api/httpClient";
import { useTableSort, type SortColumnMap } from "../../hooks/useTableSort";
import {
  useActivePage,
  useApiOnline,
  useConsoleActions,
  useDashboard,
  usePlannerJobsStatus,
} from "../../store";
import type {
  ConfigOptimizerGetPayload,
  ConfigOptimizerResultData,
  PlanetData,
  PlannerJobState,
} from "../../types/api";
import { ConfigOptimizerHeaderKpis } from "./ConfigOptimizerHeaderKpis";
import { ConfigOptimizerParamPanel } from "./ConfigOptimizerParamPanel";
import {
  ConfigOptimizerPlanetModal,
  type PlanetModalSavePayload,
} from "./ConfigOptimizerPlanetModal";
import { ConfigOptimizerPlanetsPanel, type PlanetSortKey } from "./ConfigOptimizerPlanetsPanel";
import { ConfigOptimizerResearchPanel, type ResearchSortKey } from "./ConfigOptimizerResearchPanel";
import { ConfigOptimizerYearlyGraphModal } from "./ConfigOptimizerYearlyGraphModal";
import {
  type NormalizedPlanetRow,
  type ResearchPlanRow,
  buildParamsPayload,
  deriveConfigOptimizerSummary,
  draftFromParams,
  sanitizeCustomNamesMap,
  sanitizeSpecializationsMap,
} from "./configOptimizerDraft";
import {
  buildEditableModalPlanet,
  buildOwnedDashboardPlanets,
  buildResearchPlanRows,
  normalizePlanetRows,
} from "./configOptimizerPlanets";
import {
  type ConfigOptimizerDraft,
  DEFAULT_CONFIG_OPTIMIZER_DRAFT,
  PLANET_SPECIALIZATION_OPTIONS,
  computeColonyGateInfo,
} from "./plannerHelpers";
import { useConfigOptimizerRename } from "./useConfigOptimizerRename";
import { useConfigOptimizerSpecs } from "./useConfigOptimizerSpecs";

export interface ConfigOptimizerTabProps {
  hidden?: boolean;
}

export const ConfigOptimizerTab: React.FC<ConfigOptimizerTabProps> = ({ hidden = false }) => {
  const apiOnline = useApiOnline();
  const dashboard = useDashboard();
  const activePage = useActivePage();
  const plannerJobsStatus = usePlannerJobsStatus();
  const { setDashboard, setPlannerJobsStatus, apiCommand, loadDashboard, showFeedback } =
    useConsoleActions();

  const [optData, setOptData] = useState<ConfigOptimizerGetPayload | null>(null);
  const [draft, setDraft] = useState<ConfigOptimizerDraft>(() =>
    draftFromParams(DEFAULT_CONFIG_OPTIMIZER_DRAFT)
  );
  const [draftDirty, setDraftDirty] = useState<boolean>(false);
  const [paramSubTab, setParamSubTab] = useState<"core" | "buildings">("core");
  const [subView, setSubView] = useState<"planets" | "research">("planets");
  const [selectedSlot, setSelectedSlot] = useState<number | null>(null);
  const [planetPage, setPlanetPage] = useState<number>(1);
  const [planetPageSize, setPlanetPageSize] = useState<number>(25);
  const [researchPage, setResearchPage] = useState<number>(1);
  const [researchPageSize, setResearchPageSize] = useState<number>(25);
  const [isSubmitting, setIsSubmitting] = useState<boolean>(false);
  const [modalPlanetSlot, setModalPlanetSlot] = useState<number | null>(null);
  const [isSavingPlanetModal, setIsSavingPlanetModal] = useState<boolean>(false);

  const patchDraft = useCallback((updater: (prev: ConfigOptimizerDraft) => ConfigOptimizerDraft) => {
    setDraftDirty(true);
    setDraft(updater);
  }, []);

  const fetchOptimizerState = useCallback(
    async ({ silent = false }: { silent?: boolean } = {}) => {
      try {
        const res = await apiCommand<ConfigOptimizerGetPayload>({
          command: "config_optimizer_get",
        });
        if (res && typeof res === "object") {
          setOptData(res);
          if (!draftDirty) {
            const rawParams =
              res.active?.params ||
              res.active?.result?.meta_parameters ||
              res.default_params ||
              null;
            const persistedSpecs =
              res.planet_specializations ||
              res.active?.params?.planet_specializations ||
              null;
            if (rawParams || persistedSpecs) {
              setDraft(draftFromParams(rawParams, persistedSpecs));
            }
          }
          if (res.job && plannerJobsStatus) {
            setPlannerJobsStatus({
              ...plannerJobsStatus,
              config_optimizer_job: res.job,
            });
          }
        }
      } catch (err: any) {
        if (!silent) {
          showFeedback(`Config Optimizer load failed: ${err.message}`, feedbackKindForError(err));
        }
      }
    },
    [apiCommand, draftDirty, plannerJobsStatus, setPlannerJobsStatus, showFeedback]
  );

  useEffect(() => {
    if (!hidden && activePage === "action-graph" && !optData) {
      void fetchOptimizerState({ silent: true });
    }
  }, [activePage, apiOnline, fetchOptimizerState, hidden, optData]);

  const activeJob: PlannerJobState | null =
    optData?.job || plannerJobsStatus?.config_optimizer_job || null;
  const isJobRunning = String(activeJob?.status || "").toLowerCase() === "running";

  useEffect(() => {
    if (!isJobRunning || !apiOnline || hidden) return;
    const timer = setInterval(() => {
      void fetchOptimizerState({ silent: true });
    }, 1500);
    return () => clearInterval(timer);
  }, [apiOnline, fetchOptimizerState, hidden, isJobRunning]);

  const activeResult: ConfigOptimizerResultData | null = useMemo(() => {
    if (!optData?.active) return null;
    if (optData.active.result && typeof optData.active.result === "object") {
      return optData.active.result;
    }
    if ("planets" in optData.active || "summary" in optData.active) {
      return optData.active as unknown as ConfigOptimizerResultData;
    }
    return null;
  }, [optData?.active]);

  const reservedFloorsMap = useMemo<Record<string, Record<string, number>>>(
    () => optData?.reserved_floors || optData?.active?.reserved_floors || {},
    [optData]
  );

  const dashboardPlanets = useMemo<PlanetData[]>(
    () => (Array.isArray(dashboard?.state?.planets) ? dashboard!.state!.planets! : []),
    [dashboard?.state?.planets]
  );

  const customPlanetNames = useMemo<Record<string, string>>(() => {
    const dashMap = (dashboard as Record<string, any> | null | undefined)?.custom_planet_names;
    const optMap = (optData as Record<string, any> | null | undefined)?.custom_planet_names;
    return {
      ...(dashMap && typeof dashMap === "object" ? sanitizeCustomNamesMap(dashMap) : {}),
      ...(optMap && typeof optMap === "object" ? sanitizeCustomNamesMap(optMap) : {}),
    };
  }, [dashboard, optData]);

  const ownedDashboardPlanets = useMemo(
    () => buildOwnedDashboardPlanets(dashboardPlanets, customPlanetNames),
    [customPlanetNames, dashboardPlanets]
  );

  const planetRows = useMemo(
    () =>
      normalizePlanetRows(activeResult, reservedFloorsMap, customPlanetNames, dashboardPlanets),
    [activeResult, customPlanetNames, dashboardPlanets, reservedFloorsMap]
  );

  const planetSortCols = useMemo<SortColumnMap<NormalizedPlanetRow, PlanetSortKey>>(
    () => ({
      planet: (r) => r.slot,
      spec: (r) => r.detectedSpecialization,
      temp: { accessor: (r) => r.temperature, defaultDirection: "desc" },
      fields: { accessor: (r) => r.fieldsUsedTarget, defaultDirection: "desc" },
      time: { accessor: (r) => r.lastLevelTimeSec, defaultDirection: "desc" },
      output: {
        accessor: (r) => r.monthlyShips ?? r.monthlySpiceEq,
        defaultDirection: "desc",
      },
    }),
    []
  );
  const planetSort = useTableSort(planetRows, planetSortCols);
  const pagedPlanets = useMemo(() => {
    const start = (planetPage - 1) * planetPageSize;
    return planetSort.sortedRows.slice(start, start + planetPageSize);
  }, [planetPage, planetPageSize, planetSort.sortedRows]);

  const researchRows = useMemo(
    () => buildResearchPlanRows(activeResult, dashboard),
    [activeResult, dashboard]
  );

  const researchSortCols = useMemo<SortColumnMap<ResearchPlanRow, ResearchSortKey>>(
    () => ({
      name: (r) => r.name,
      current: { accessor: (r) => r.current, defaultDirection: "desc" },
      uniMax: { accessor: (r) => r.universeMax, defaultDirection: "desc" },
      planned: { accessor: (r) => r.planned, defaultDirection: "desc" },
      gap: { accessor: (r) => r.gap, defaultDirection: "desc" },
    }),
    []
  );
  const researchSort = useTableSort(researchRows, researchSortCols);
  const pagedResearch = useMemo(() => {
    const start = (researchPage - 1) * researchPageSize;
    return researchSort.sortedRows.slice(start, start + researchPageSize);
  }, [researchPage, researchPageSize, researchSort.sortedRows]);

  const selectedPlanetDetail = useMemo(
    () =>
      planetRows.find((p) => p.slot === selectedSlot) ||
      (planetRows.length > 0 ? planetRows[0] : null),
    [planetRows, selectedSlot]
  );

  const onRunOptimizer = async () => {
    setIsSubmitting(true);
    try {
      const payload = buildParamsPayload(draft);
      showFeedback("Starting Config Optimization (Monthly Target & Floors)...", "ok");
      const res = await apiCommand({
        command: "config_optimizer_run",
        params: payload,
        auto_replan_ai: true,
      });
      setDraftDirty(false);
      if (res.result) {
        setOptData((prev) => ({
          ...(prev || {}),
          job: res.job || { status: "completed", progress_pct: 100 },
          active: { ...(prev?.active || {}), params: payload, result: res.result },
        }));
      } else {
        await fetchOptimizerState({ silent: true });
      }
      if ((res.job || res.ai_planner_job) && plannerJobsStatus) {
        setPlannerJobsStatus({
          ...plannerJobsStatus,
          config_optimizer_job: res.job || plannerJobsStatus.config_optimizer_job,
          ai_planner_job: res.ai_planner_job || plannerJobsStatus.ai_planner_job,
        });
      }
      const st = String(res.job?.status || "completed").toLowerCase();
      showFeedback(
        st === "running"
          ? "Config Optimization running in background — progress updates live."
          : "Config Optimization completed and queued AI-Planner replan.",
        "ok"
      );
    } catch (err: any) {
      showFeedback(`Config Optimization failed: ${err.message}`, feedbackKindForError(err));
    } finally {
      setIsSubmitting(false);
    }
  };

  const onResetDefaults = () => {
    setDraftDirty(false);
    setDraft(draftFromParams(optData?.default_params || null, {}));
    showFeedback("Reset Config Optimizer inputs to defaults.", "ok");
  };

  const {
    yearlyGraphOpen,
    setYearlyGraphOpen,
    yearlyHorizon,
    yearlyProjection,
    setYearlyProjection,
    yearlyLoading,
    yearlyError,
    onPlanetSpecChange,
    onLockAllCurrentRoles,
    onUnlockAllRoles,
    onOpenYearlyGraph,
    onChangeYearlyHorizon,
  } = useConfigOptimizerSpecs({
    apiOnline,
    draft,
    planetRows,
    apiCommand,
    setDraft,
    setDraftDirty,
    setOptData,
    showFeedback,
  });

  const {
    editingPlanetId,
    renameDraft,
    renamingPlanetId,
    renameError,
    isSyncingNames,
    onRenameDraftChange,
    startRenamingPlanet,
    cancelRenamingPlanet,
    submitPlanetRename,
    onSyncAllPlanetNames,
    onSyncSinglePlanetName,
  } = useConfigOptimizerRename({
    customPlanetNames,
    apiCommand,
    setOptData,
    setDashboard,
    fetchOptimizerState,
    loadDashboard,
    showFeedback,
  });

  const unsyncedNameCount = useMemo(() => {
    const sourceList =
      planetRows.length > 0
        ? planetRows.filter((r) => !r.isPlaceholder && r.planetId > 0)
        : ownedDashboardPlanets;
    let count = 0;
    for (const item of sourceList) {
      if (item.hasCustomName && item.name && item.gameName && item.name !== item.gameName) {
        count += 1;
      }
    }
    return count;
  }, [ownedDashboardPlanets, planetRows]);

  const editableModalPlanet = useMemo(
    () =>
      buildEditableModalPlanet({
        modalPlanetSlot,
        planetRows,
        dashboardPlanets,
        customPlanetNames,
        planetSpecializations: draft.planet_specializations,
        centralPlanetSlot: draft.central_planet_slot,
        reservedFloorsMap,
      }),
    [
      customPlanetNames,
      dashboardPlanets,
      draft.central_planet_slot,
      draft.planet_specializations,
      modalPlanetSlot,
      planetRows,
      reservedFloorsMap,
    ]
  );

  const onSavePlanetModal = async (payload: PlanetModalSavePayload) => {
    setIsSavingPlanetModal(true);
    try {
      const res = await apiCommand<Record<string, any>>({
        command: "config_optimizer_update_planet",
        slot: payload.slot,
        planet_id: payload.planetId,
        coords: payload.coords,
        planet_name: payload.planetName,
        locked_specialization: payload.lockedSpecialization,
        fields_max_override: payload.fieldsMaxOverride,
        target_buildings: payload.targetBuildings,
        lock_for_ga: payload.lockForGa,
        auto_warehouses: payload.autoWarehouses,
        auto_replan_ai: true,
      });
      if (res && typeof res === "object") {
        setOptData((prev) => ({
          ...(prev || {}),
          active: res.active || prev?.active || null,
          planet_specializations: res.planet_specializations || prev?.planet_specializations || {},
          custom_planet_names: res.custom_planet_names || prev?.custom_planet_names || {},
        }));
        if (res.planet_specializations && typeof res.planet_specializations === "object") {
          setDraft((prev) => ({
            ...prev,
            planet_specializations: sanitizeSpecializationsMap(res.planet_specializations),
          }));
        }
        if (res.ai_planner_job && plannerJobsStatus) {
          setPlannerJobsStatus({ ...plannerJobsStatus, ai_planner_job: res.ai_planner_job });
        }
      }
      setYearlyProjection(null);
      setModalPlanetSlot(null);
      showFeedback(
        `Saved desired configuration for Planet #${payload.slot} (${payload.planetName || payload.coords}).`,
        "ok"
      );
      await Promise.all([
        fetchOptimizerState({ silent: true }),
        loadDashboard({ refresh: false, preserveConfig: true, force: true }),
      ]);
    } catch (err: any) {
      showFeedback(
        `Failed to save planet configuration: ${err.message}`,
        feedbackKindForError(err)
      );
    } finally {
      setIsSavingPlanetModal(false);
    }
  };

  const gate = computeColonyGateInfo(dashboard, plannerJobsStatus, optData);
  const {
    lockedSpecsCount,
    summary,
    stickyPlanApplied,
    rolesPreservedCount,
    rolesChangedCount,
    shipKey,
    shipsPerMonth,
    wholeShips,
    limitFactor,
    totalFieldsUsed,
    totalFieldsMax,
    whFields,
    totalDemolishWhenFull,
    totalLevelsToDemolish,
    activeAllowDemolition,
    elapsedMs,
    runTs,
  } = deriveConfigOptimizerSummary({
    draft,
    activeResult,
    optData,
    activeJob,
    planetRows,
  });

  return (
    <div className="ag-tab-split" id="ag-pane-config-optimizer" hidden={hidden}>
      <article className="hud-window ag-main-window" id="ag-co-main-window">
        <ConfigOptimizerHeaderKpis
          activeJob={activeJob}
          isJobRunning={isJobRunning}
          hasActiveResult={Boolean(activeResult)}
          subView={subView}
          planetCount={planetRows.length}
          researchCount={researchRows.length}
          onChangeSubView={setSubView}
          onOpenYearlyGraph={onOpenYearlyGraph}
          onRefresh={() => void fetchOptimizerState({ silent: false })}
          shipKey={shipKey}
          shipsPerMonth={shipsPerMonth}
          wholeShips={wholeShips}
          limitFactor={limitFactor}
          investmentMonths={
            summary.investment_in_months_of_output != null
              ? Number(summary.investment_in_months_of_output)
              : null
          }
          coloniesOwned={gate.coloniesOwned}
          maxPlanets={gate.maxPlanets}
          totalFieldsUsed={totalFieldsUsed}
          totalFieldsMax={totalFieldsMax}
          whFields={whFields}
          totalDemolishWhenFull={totalDemolishWhenFull}
          activeAllowDemolition={activeAllowDemolition}
          elapsedMs={elapsedMs}
          runTs={runTs}
        />

        <ConfigOptimizerPlanetsPanel
          hidden={subView !== "planets"}
          isJobRunning={isJobRunning}
          isSubmitting={isSubmitting}
          isSyncingNames={isSyncingNames}
          lockedSpecsCount={lockedSpecsCount}
          stickyPlanApplied={stickyPlanApplied}
          rolesPreservedCount={rolesPreservedCount}
          rolesChangedCount={rolesChangedCount}
          unsyncedNameCount={unsyncedNameCount}
          planetRows={planetRows}
          pagedPlanets={pagedPlanets}
          sortedPlanetCount={planetSort.sortedRows.length}
          planetPage={planetPage}
          planetPageSize={planetPageSize}
          sortKey={planetSort.sortKey}
          sortDirection={planetSort.sortDirection}
          onRequestSort={planetSort.requestSort}
          onPageChange={setPlanetPage}
          onPageSizeChange={(sz) => {
            setPlanetPageSize(sz);
            setPlanetPage(1);
          }}
          ownedDashboardPlanets={ownedDashboardPlanets}
          selectedPlanetDetail={selectedPlanetDetail}
          planetSpecializations={draft.planet_specializations}
          editingPlanetId={editingPlanetId}
          renameDraft={renameDraft}
          renamingPlanetId={renamingPlanetId}
          renameError={renameError}
          onRenameDraftChange={onRenameDraftChange}
          onStartRename={startRenamingPlanet}
          onCancelRename={cancelRenamingPlanet}
          onSubmitRename={(pid, name, fallback) => void submitPlanetRename(pid, name, fallback)}
          onSyncSinglePlanetName={(pid, name) => void onSyncSinglePlanetName(pid, name)}
          onSyncAllPlanetNames={() => void onSyncAllPlanetNames()}
          onLockAllCurrentRoles={() => void onLockAllCurrentRoles()}
          onUnlockAllRoles={() => void onUnlockAllRoles()}
          onPlanetSpecChange={(row, val) => void onPlanetSpecChange(row, val)}
          onSelectPlanet={setSelectedSlot}
          onOpenPlanetModal={setModalPlanetSlot}
        />

        <ConfigOptimizerResearchPanel
          hidden={subView !== "research"}
          pagedResearch={pagedResearch}
          totalItems={researchSort.sortedRows.length}
          page={researchPage}
          pageSize={researchPageSize}
          sortKey={researchSort.sortKey}
          sortDirection={researchSort.sortDirection}
          onRequestSort={researchSort.requestSort}
          onPageChange={setResearchPage}
          onPageSizeChange={(sz) => {
            setResearchPageSize(sz);
            setResearchPage(1);
          }}
        />
      </article>

      <ConfigOptimizerParamPanel
        draft={draft}
        draftDirty={draftDirty}
        paramSubTab={paramSubTab}
        onChangeParamSubTab={setParamSubTab}
        onPatchDraft={patchDraft}
        gate={gate}
        activeJob={activeJob}
        isJobRunning={isJobRunning}
        isSubmitting={isSubmitting}
        hasActiveResult={Boolean(activeResult)}
        totalLevelsToDemolish={totalLevelsToDemolish}
        onResetDefaults={onResetDefaults}
        onOpenYearlyGraph={onOpenYearlyGraph}
        onRunOptimizer={() => void onRunOptimizer()}
      />

      <ConfigOptimizerPlanetModal
        planet={editableModalPlanet}
        specializationOptions={PLANET_SPECIALIZATION_OPTIONS}
        isSaving={isSavingPlanetModal}
        isSyncingName={isSyncingNames}
        onClose={() => setModalPlanetSlot(null)}
        onSave={onSavePlanetModal}
        onSyncSingleName={onSyncSinglePlanetName}
      />

      <ConfigOptimizerYearlyGraphModal
        open={yearlyGraphOpen}
        projection={yearlyProjection}
        loading={yearlyLoading}
        error={yearlyError}
        horizonYears={yearlyHorizon}
        onChangeHorizon={onChangeYearlyHorizon}
        onClose={() => setYearlyGraphOpen(false)}
      />
    </div>
  );
};

export default ConfigOptimizerTab;
