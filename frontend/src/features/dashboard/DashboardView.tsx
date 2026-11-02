import React, { useCallback, useMemo } from "react";
import { flushSync } from "react-dom";
import { feedbackKindForError } from "../../api/httpClient";
import { ScannerHud } from "../../components/layout/ScannerHud";
import {
  useActivePage,
  useActivePlanet,
  useBuildingsEtaData,
  useConsoleActions,
  useDashboard,
  useNowEpochSec,
  useSelectedPlanetId,
  useUiPrefs,
} from "../../store";
import type { PlannedActionData } from "../../types/api";
import {
  finiteNum,
  fmtInt,
  getSelectedBlastFurnacePct,
} from "../../utils/formatters";
import {
  actionPayloadFor,
  buildPinPayloadFromAction,
  dashboardQueueDomainForAction,
  isPlanetConstructing,
  normalizeCoordKey,
  queueJobMatchesPlanet,
} from "../operations/planActions";
import { DashboardColonyPanel } from "./DashboardColonyPanel";
import {
  computeScopedEcon,
  deriveBuildingResourceRequests,
  partitionActiveQueues,
  sumOptionalFinite,
} from "./dashboardDerivations";
import { DashboardQueuesSection } from "./DashboardQueuesSection";
import { DashboardTelemetryPanel } from "./DashboardTelemetryPanel";

export const DashboardView: React.FC = () => {
  const dashboard = useDashboard();
  const activePage = useActivePage();
  const selectedPlanetId = useSelectedPlanetId();
  const activePlanet = useActivePlanet();
  const nowEpochSec = useNowEpochSec();
  const uiPrefs = useUiPrefs();
  const buildingsEtaData = useBuildingsEtaData();
  const {
    setActivePage,
    setSelectedPlanetId,
    showFeedback,
    apiCommand,
    loadDashboard,
    setUiPref,
    toggleBlastFurnaceRate,
    enqueueManualCommand,
  } = useConsoleActions();

  const gs = dashboard?.state || null;
  const planets = useMemo(() => gs?.planets || [], [gs?.planets]);
  const chosenId = activePlanet?.planet_id;

  const activeQueues = useMemo(
    () => (Array.isArray(gs?.active_queues) ? gs!.active_queues! : []),
    [gs]
  );
  const effNowSec =
    nowEpochSec != null && nowEpochSec > 0 ? nowEpochSec : Math.floor(Date.now() / 1000);

  const constructingPlanetIds = useMemo(() => {
    const ids = new Set<number>();
    for (const p of planets) {
      if (isPlanetConstructing(p, activeQueues, gs?.active_planet_id, effNowSec)) {
        ids.add(Number(p.planet_id));
      }
    }
    return ids;
  }, [activeQueues, effNowSec, gs?.active_planet_id, planets]);

  const rawDashScope = String(uiPrefs.dashQueuePlanetScope || "all");
  const scopedDashPlanet = useMemo(
    () =>
      rawDashScope !== "all"
        ? planets.find((p) => String(p.planet_id) === rawDashScope) || null
        : null,
    [planets, rawDashScope]
  );
  const activeDashScope = scopedDashPlanet ? String(scopedDashPlanet.planet_id) : "all";
  const isAllColoniesScope = planets.length > 1 && scopedDashPlanet === null;
  const resourcePlanet = scopedDashPlanet || activePlanet;
  const hasResourceScope = isAllColoniesScope ? planets.length > 0 : Boolean(resourcePlanet);

  const rnk = dashboard?.ranking || {};
  const curRnk = rnk.current || {};
  const rnkRates = rnk.rates || {};

  const planetEconMap =
    dashboard?.planet_economies && typeof dashboard.planet_economies === "object"
      ? dashboard.planet_economies
      : null;

  const scopedEcon = useMemo(
    () =>
      computeScopedEcon({
        isAllColoniesScope,
        planets,
        resourcePlanet,
        planetEconMap,
        rnkRates,
        buildingsEtaData,
        dashboardBuildingsEta: dashboard?.buildings_eta,
      }),
    [
      buildingsEtaData,
      dashboard?.buildings_eta,
      isAllColoniesScope,
      planetEconMap,
      planets,
      resourcePlanet,
      rnkRates,
    ]
  );

  const scopedResources = useMemo(() => {
    if (isAllColoniesScope) {
      return {
        pig_iron: sumOptionalFinite(planets.map((p) => p.resources?.pig_iron)),
        metal: sumOptionalFinite(planets.map((p) => p.resources?.metal)),
        kryptonite: sumOptionalFinite(planets.map((p) => p.resources?.kryptonite)),
        spice: sumOptionalFinite(planets.map((p) => p.resources?.spice)),
      };
    }
    return {
      pig_iron: resourcePlanet?.resources?.pig_iron,
      metal: resourcePlanet?.resources?.metal,
      kryptonite: resourcePlanet?.resources?.kryptonite,
      spice: resourcePlanet?.resources?.spice,
    };
  }, [isAllColoniesScope, planets, resourcePlanet]);

  const scopedStorage = useMemo(() => {
    if (isAllColoniesScope) {
      return {
        pig_iron: sumOptionalFinite(planets.map((p) => p.storage?.pig_iron)),
        metal: sumOptionalFinite(planets.map((p) => p.storage?.metal)),
        kryptonite: sumOptionalFinite(planets.map((p) => p.storage?.kryptonite)),
        spice: sumOptionalFinite(planets.map((p) => p.storage?.spice)),
      };
    }
    return {
      pig_iron: resourcePlanet?.storage?.pig_iron,
      metal: resourcePlanet?.storage?.metal,
      kryptonite: resourcePlanet?.storage?.kryptonite,
      spice: resourcePlanet?.storage?.spice,
    };
  }, [isAllColoniesScope, planets, resourcePlanet]);

  const b2Pct = getSelectedBlastFurnacePct(dashboard, selectedPlanetId, buildingsEtaData);

  // Energy readout (aggregated across all colonies when All Planets scope is active)
  const prodFinite = isAllColoniesScope
    ? sumOptionalFinite(
        planets.map((p) => (p.energy?.produced == null ? 0 : p.energy.produced))
      )
    : resourcePlanet?.energy?.produced == null
      ? 0
      : finiteNum(resourcePlanet.energy.produced, null);
  const usedFinite = isAllColoniesScope
    ? sumOptionalFinite(
        planets.map((p) => (p.energy?.used == null ? 0 : p.energy.used))
      )
    : resourcePlanet?.energy?.used == null
      ? 0
      : finiteNum(resourcePlanet.energy.used, null);
  const netRaw = prodFinite !== null && usedFinite !== null ? prodFinite - usedFinite : null;
  const netEnergy = netRaw === null ? null : netRaw === 0 ? 0 : netRaw;
  const energyValText = hasResourceScope
    ? netEnergy === null
      ? "—"
      : `${netEnergy >= 0 ? "+" : ""}${fmtInt(netEnergy)}`
    : "+0";
  const energyRatioText = hasResourceScope
    ? `${prodFinite === null ? "—" : fmtInt(prodFinite)} / ${usedFinite === null ? "—" : fmtInt(usedFinite)} E`
    : "0 / 0 E";

  const sectorTitleText = isAllColoniesScope
    ? `SECTOR // ALL COLONIES (${planets.length})`
    : resourcePlanet
      ? `SECTOR // [${resourcePlanet.coords || "--"}] ${String(resourcePlanet.name || "COLONY").toUpperCase()}`
      : "SECTOR // ACTIVE COLONY";

  // Colony overview meta text
  const pTemp = finiteNum(activePlanet?.temperature, null);
  const pFieldsUsed = finiteNum(activePlanet?.fields_used, null);
  const pFieldsMax = finiteNum(activePlanet?.fields_max, null);
  const pType = String(activePlanet?.planet_type || "normal").toUpperCase();
  const planetMetaText = activePlanet
    ? `${pType} · ${pTemp === null ? "—" : `${pTemp}°C`} · ${pFieldsUsed === null ? "—" : pFieldsUsed}/${pFieldsMax === null ? "—" : pFieldsMax}`
    : "COLONY STANDBY";

  // Ranking & hourly SpiceEq flow readouts
  const az = curRnk.attack_zone || {};
  const rankFinite = finiteNum(curRnk.server_rank, null);
  const dashRankText = dashboard
    ? curRnk.server_rank != null
      ? rankFinite !== null
        ? `#${fmtInt(rankFinite)}`
        : "#—"
      : az.label || "#—"
    : "#—";
  const baselinePts = finiteNum(curRnk.baseline_points, null);
  const dashAzText = dashboard
    ? `${az.label || "AZ —"} · ${baselinePts !== null ? fmtInt(Math.round(baselinePts)) : "—"} pts`
    : "AZ — · — pts";
  const steadyRate = finiteNum(rnkRates.pts_per_hr_steady, 0);
  const cleanSteady =
    Math.abs(steadyRate) < 0.005 || Number(steadyRate.toFixed(2)) === 0 ? 0 : steadyRate;
  const dashPtsHrText = dashboard
    ? `${cleanSteady >= 0 ? "+" : ""}${cleanSteady.toFixed(2)}/h`
    : "+0/h";
  const spiceEqRate = finiteNum(rnkRates.spice_eq_per_hr, null);
  const dashSpiceHrText = dashboard
    ? `${spiceEqRate !== null ? fmtInt(Math.round(spiceEqRate)) : "—"} SpiceEq/h`
    : "0 SpiceEq/h";

  // Tutorial banner
  const tut = gs?.tutorial || null;
  const incompleteTasks = useMemo(
    () =>
      Array.isArray(tut?.tasks)
        ? tut!.tasks!.filter((t) => (t?.current ?? 0) < (t?.target ?? 0))
        : [],
    [tut]
  );
  const tutSummaryText = tut
    ? incompleteTasks.length > 0
      ? incompleteTasks
          .map((t) => `${t.building_name || t.description} (${t.current}/${t.target})`)
          .join(" · ")
      : `Ready to claim: ${tut.reward_text || "Step reward"}`
    : "Loading tutorial steps...";

  const { execBuildingJobs, execFleetJobs, execResearchJobs } = useMemo(
    () => partitionActiveQueues(activeQueues, planets, gs?.active_planet_id, effNowSec),
    [activeQueues, effNowSec, gs?.active_planet_id, planets]
  );

  const allPlanActions = useMemo(() => dashboard?.plan || [], [dashboard?.plan]);
  const manualList = useMemo(() => {
    if (Array.isArray(dashboard?.manual_queue)) return dashboard!.manual_queue!;
    return allPlanActions.filter(
      (a) => a.manual_queue_id != null && Number(a.manual_queue_id) > 0
    );
  }, [allPlanActions, dashboard]);
  const pinnedCount = manualList.length;

  const planetQueueStats = useMemo(() => {
    const map = new Map<number, { total: number; ready: number }>();
    for (const p of planets) {
      map.set(p.planet_id, { total: 0, ready: 0 });
    }
    for (const act of allPlanActions) {
      if (dashboardQueueDomainForAction(act) === "research") continue;
      const cur = map.get(Number(act.planet_id));
      if (cur) {
        cur.total += 1;
        if (act.executable_now) cur.ready += 1;
      }
    }
    return map;
  }, [allPlanActions, planets]);

  const scopedExecBuildingJobs = useMemo(() => {
    if (!scopedDashPlanet) return execBuildingJobs;
    return execBuildingJobs.filter((job) =>
      queueJobMatchesPlanet(job, scopedDashPlanet, gs?.active_planet_id, true)
    );
  }, [execBuildingJobs, gs?.active_planet_id, scopedDashPlanet]);

  const scopedExecFleetJobs = useMemo(() => {
    if (!scopedDashPlanet) return execFleetJobs;
    return execFleetJobs.filter((job) =>
      queueJobMatchesPlanet(job, scopedDashPlanet, gs?.active_planet_id, false)
    );
  }, [execFleetJobs, gs?.active_planet_id, scopedDashPlanet]);

  const dashQueueMaxRows = planets.length <= 1 ? 10 : undefined;

  const { buildingActions, fleetActions, researchActions } = useMemo(() => {
    const bld: PlannedActionData[] = [];
    const flt: PlannedActionData[] = [];
    const res: PlannedActionData[] = [];
    const scopePid = scopedDashPlanet ? Number(scopedDashPlanet.planet_id) : null;
    for (const act of allPlanActions) {
      const dom = dashboardQueueDomainForAction(act);
      if (dom === "research") {
        res.push(act);
      } else if (scopePid !== null && Number(act.planet_id) !== scopePid) {
        continue;
      } else if (dom === "fleet") {
        flt.push(act);
      } else {
        bld.push(act);
      }
    }
    return { buildingActions: bld, fleetActions: flt, researchActions: res };
  }, [allPlanActions, scopedDashPlanet]);

  const allBuildingResourceRequests = useMemo(
    () => deriveBuildingResourceRequests(dashboard, execFleetJobs, allPlanActions, planets),
    [allPlanActions, dashboard, execFleetJobs, planets]
  );

  const scopedBuildingResourceRequests = useMemo(() => {
    if (!scopedDashPlanet) return allBuildingResourceRequests;
    const scopePid = Number(scopedDashPlanet.planet_id);
    const scopeCoordKey = normalizeCoordKey(scopedDashPlanet.coords);
    return allBuildingResourceRequests.filter((req) => {
      const rPid = req.planet_id != null ? Number(req.planet_id) : NaN;
      if (Number.isFinite(rPid) && rPid > 0) {
        return rPid === scopePid;
      }
      const rCoordKey = normalizeCoordKey(req.planet_coords);
      return Boolean(scopeCoordKey) && rCoordKey === scopeCoordKey;
    });
  }, [allBuildingResourceRequests, scopedDashPlanet]);

  const cfgMode = String(
    dashboard?.config?.planner_mode ||
      dashboard?.planner_mode ||
      dashboard?.effective_planner_mode ||
      "priority"
  ).toLowerCase();
  const isAiPlanner = cfgMode === "ai_planner";
  const isLegacyGa = cfgMode === "ga_nn";
  const modeLabel = isAiPlanner
    ? "MODE: AI-PLANNER"
    : isLegacyGa
      ? "MODE: AI-PLANNER"
      : "MODE: PRIORITY";

  const colonyCountTag = dashboard
    ? `${String(planets.length).padStart(2, "0")}-${String(gs?.universe_id || 31)}`
    : "01-31";
  const matrixAccountLine = dashboard
    ? `CMD // ${gs?.nickname || "OPERATOR"} · :${dashboard.health?.debug_port || 9222}`
    : "CMD // STANDBY";

  const handleClaimTutorial = useCallback(async () => {
    try {
      const res = await apiCommand({ command: "claim_tutorial", dry_run: false });
      showFeedback(res.message || "Tutorial claim submitted.", "ok");
      await loadDashboard({ refresh: true, force: true });
    } catch (err: any) {
      showFeedback(`Tutorial claim failed: ${err.message}`, feedbackKindForError(err));
    }
  }, [apiCommand, loadDashboard, showFeedback]);

  const openComposerWithKind = useCallback(
    (presetKind: string) => {
      flushSync(() => {
        setUiPref("mqKind", presetKind);
        setUiPref("mqPresetSeq", Number(uiPrefs.mqPresetSeq || 0) + 1);
        setActivePage("operations");
      });
      if (typeof document !== "undefined") {
        document.getElementById("mq-kind")?.focus?.();
      }
    },
    [setActivePage, setUiPref, uiPrefs.mqPresetSeq]
  );

  const handleGotoResearch = useCallback(() => {
    flushSync(() => {
      setActivePage("research");
    });
  }, [setActivePage]);

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

  const handlePinQueue = useCallback(
    (act: PlannedActionData, priority = "P1") => {
      void enqueueManualCommand(buildPinPayloadFromAction(act, priority));
    },
    [enqueueManualCommand]
  );

  const handleSelectColony = useCallback(
    (planetId: number) => {
      setSelectedPlanetId(planetId);
      if (planets.length > 1) {
        setUiPref("dashQueuePlanetScope", String(planetId));
      }
    },
    [planets.length, setSelectedPlanetId, setUiPref]
  );

  const handleSelectPlanetScope = useCallback(
    (planetId: number) => {
      setUiPref("dashQueuePlanetScope", String(planetId));
      setSelectedPlanetId(planetId);
    },
    [setSelectedPlanetId, setUiPref]
  );

  return (
    <section
      id="view-dashboard"
      className="page-view dashboard-view"
      aria-label="Dashboard overview"
      hidden={activePage !== "dashboard"}
    >
      {/* Tutorial Alert Bar */}
      <div className="tutorial-banner" id="tutorial-strip" hidden={!tut}>
        <div className="tutorial-copy">
          <span className="module-code tabular" id="tutorial-title">
            <span id="tutorial-step-badge">
              {tut ? `TUTORIAL ${tut.step}/${tut.total_steps}` : "TUTORIAL 1/11"}
            </span>
          </span>
          <span id="tutorial-tasks-summary" className="tabular">
            <span id="tutorial-tasks-list">{tutSummaryText}</span>
          </span>
        </div>
        <button
          type="button"
          className="btn btn-primary btn-sm"
          id="btn-claim-tutorial"
          hidden={!tut?.can_complete}
          onClick={handleClaimTutorial}
        >
          Claim Reward
        </button>
      </div>

      {/* Upper Row: Resources + Colony Matrix */}
      <div className="dash-upper-grid">
        <DashboardTelemetryPanel
          sectorTitleText={sectorTitleText}
          hasResourceScope={hasResourceScope}
          scopedResources={scopedResources}
          scopedStorage={scopedStorage}
          scopedEcon={scopedEcon}
          energyValText={energyValText}
          energyRatioText={energyRatioText}
          dashRankText={dashRankText}
          dashAzText={dashAzText}
          dashPtsHrText={dashPtsHrText}
          dashSpiceHrText={dashSpiceHrText}
        />

        <DashboardColonyPanel
          hasDashboard={Boolean(dashboard)}
          planets={planets}
          chosenId={chosenId}
          constructingPlanetIds={constructingPlanetIds}
          planetMetaText={planetMetaText}
          b2Pct={b2Pct}
          colonyCountTag={colonyCountTag}
          matrixAccountLine={matrixAccountLine}
          activeQueues={activeQueues}
          effNowSec={effNowSec}
          onToggleBlastFurnaceRate={toggleBlastFurnaceRate}
          onSelectColony={handleSelectColony}
        />
      </div>

      {/* GA + Neural Network Radial-Ring Calculation HUD */}
      <ScannerHud />

      <DashboardQueuesSection
        hasDashboard={Boolean(dashboard)}
        isAiPlanner={isAiPlanner}
        modeLabel={modeLabel}
        pinnedCount={pinnedCount}
        planets={planets}
        constructingPlanetIds={constructingPlanetIds}
        activeDashScope={activeDashScope}
        scopedDashPlanet={scopedDashPlanet}
        planetQueueStats={planetQueueStats}
        customPlanetNames={dashboard?.custom_planet_names}
        dashQueueMaxRows={dashQueueMaxRows}
        effNowSec={effNowSec}
        scopedExecBuildingJobs={scopedExecBuildingJobs}
        buildingActions={buildingActions}
        scopedBuildingResourceRequests={scopedBuildingResourceRequests}
        scopedExecFleetJobs={scopedExecFleetJobs}
        fleetActions={fleetActions}
        execResearchJobs={execResearchJobs}
        researchActions={researchActions}
        onSelectAllScope={() => setUiPref("dashQueuePlanetScope", "all")}
        onSelectPlanetScope={handleSelectPlanetScope}
        onOpenComposerWithKind={openComposerWithKind}
        onGotoBuildings={() => setActivePage("buildings")}
        onGotoCombat={() => setActivePage("combat")}
        onGotoResearch={handleGotoResearch}
        onDispatch={handleDispatch}
        onUpdateQueue={handleUpdateQueue}
        onCancelQueue={handleCancelQueue}
        onPinQueue={handlePinQueue}
      />
    </section>
  );
};

export default DashboardView;
