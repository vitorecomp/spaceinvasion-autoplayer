import React, { useCallback, useMemo } from "react";
import { flushSync } from "react-dom";
import { feedbackKindForError } from "../../api/httpClient";
import { SortableTh } from "../../components/SortableTh";
import { GaNnScannerHud } from "../../components/layout/GaNnScannerHud";
import { useConsole } from "../../context/ConsoleContext";
import {
  coordsSortKey,
  useTableSort,
  type SortColumnMap,
} from "../../hooks/useTableSort";
import type { PlanetData, PlannedActionData, QueueJobData } from "../../types/api";
import {
  finiteNum,
  fmtClockTime,
  fmtHms,
  fmtInt,
  fmtRate,
  getSelectedBlastFurnacePct,
} from "../../utils/formatters";
import {
  actionPayloadFor,
  buildPinPayloadFromAction,
  cleanQueueJobTitle,
  dashboardQueueDomainForAction,
  dashboardQueueDomainForJob,
} from "../operations/planActions";
import { DashboardQueueCard } from "./DashboardQueueCard";

interface ResourceTrackSpec {
  idKey: "pig" | "metal" | "kryp" | "spice";
  code: string;
  name: string;
  amount: unknown;
  storage: unknown;
  hourlyRate: unknown;
  hasPlanet: boolean;
}

const ResourceTrackRow: React.FC<ResourceTrackSpec> = ({
  idKey,
  code,
  name,
  amount,
  storage,
  hourlyRate,
  hasPlanet,
}) => {

  if (!hasPlanet) {
    return (
      <div className="track-row">
        <div className="track-meta">
          <span className="track-code tabular">{code}</span>
          <span className="track-name">{name}</span>
        </div>
        <div className="track-bar-group">
          <div className="meter-track">
            <div className="meter-fill" id={`bar-${idKey}`} />
          </div>
        </div>
        <div className="track-values tabular">
          <span className="track-val" id={`val-${idKey}`}>
            —
          </span>
          <span className="track-cap" id={`cap-${idKey}`}>
            —
          </span>
          <span className="track-rate" id={`rate-${idKey}`}>
            —/h
          </span>
        </div>
      </div>
    );
  }

  const cap = finiteNum(storage, 0);
  const val = finiteNum(amount, null);
  const ratioPct = cap > 0 && val !== null ? (val / cap) * 100 : 0;
  const pct = Number.isFinite(ratioPct) ? Math.max(0, Math.min(100, Math.round(ratioPct))) : 0;

  const rateNum = hourlyRate == null ? null : finiteNum(hourlyRate, null);
  const rateText = hourlyRate == null ? "—/h" : fmtRate(hourlyRate);
  const rateNeg = hourlyRate != null && rateNum !== null && Math.round(rateNum) < 0;

  return (
    <div className="track-row">
      <div className="track-meta">
        <span className="track-code tabular">{code}</span>
        <span className="track-name">{name}</span>
      </div>
      <div className="track-bar-group">
        <div className="meter-track">
          <div
            className={`meter-fill${pct >= 85 ? " high" : ""}`}
            id={`bar-${idKey}`}
            style={{ transform: `scaleX(${pct / 100})` }}
          />
        </div>
      </div>
      <div className="track-values tabular">
        <span className="track-val" id={`val-${idKey}`}>
          {fmtInt(amount ?? 0)}
        </span>
        <span className="track-cap" id={`cap-${idKey}`}>
          {cap > 0 ? `/ ${fmtInt(cap)} (${pct}%)` : "—"}
        </span>
        <span className={`track-rate${rateNeg ? " is-neg" : ""}`} id={`rate-${idKey}`}>
          {rateText}
        </span>
      </div>
    </div>
  );
};

export const DashboardView: React.FC = () => {
  const {
    dashboard,
    activePage,
    setActivePage,
    selectedPlanetId,
    setSelectedPlanetId,
    activePlanet,
    nowEpochSec,
    showFeedback,
    apiCommand,
    loadDashboard,
    uiPrefs,
    setUiPref,
    buildingsEtaData,
    toggleBlastFurnaceRate,
    enqueueManualCommand,
  } = useConsole();

  const gs = dashboard?.state || null;
  const planets = useMemo(() => gs?.planets || [], [gs?.planets]);
  const chosenId = activePlanet?.planet_id;

  const colonySortCols = useMemo<
    SortColumnMap<PlanetData, "coords" | "colony" | "state" | "fields">
  >(
    () => ({
      coords: (p) => coordsSortKey(p.coords),
      colony: (p) => String(p.name || "Colony"),
      state: (p) => {
        const isActive = p.planet_id === chosenId;
        const pigStor = finiteNum(p.storage?.pig_iron, 0);
        const pigAmt = finiteNum(p.resources?.pig_iron, 0);
        const spiStor = finiteNum(p.storage?.spice, 0);
        const spiAmt = finiteNum(p.resources?.spice, 0);
        const nearFull =
          (pigStor > 0 && pigAmt / pigStor >= 0.85) ||
          (spiStor > 0 && spiAmt / spiStor >= 0.85);
        return nearFull ? "NEAR-CAP" : isActive ? "ONLINE" : "READY";
      },
      fields: {
        accessor: (p) => {
          const fMax = finiteNum(p.fields_max, null);
          const fUsed = finiteNum(p.fields_used, null);
          if (fMax !== null) return fMax * 10_000 + (fUsed ?? 0);
          return fUsed;
        },
        defaultDirection: "desc",
      },
    }),
    [chosenId]
  );

  const colonySort = useTableSort(planets, colonySortCols);

  const currentEta = useMemo(() => {
    if (
      buildingsEtaData &&
      activePlanet &&
      buildingsEtaData.planet?.planet_id === activePlanet.planet_id
    ) {
      return buildingsEtaData;
    }
    return dashboard?.buildings_eta || null;
  }, [activePlanet, buildingsEtaData, dashboard?.buildings_eta]);

  const pEcon = currentEta?.planet_economy || null;
  const b2Pct = getSelectedBlastFurnacePct(dashboard, selectedPlanetId, buildingsEtaData);

  // Energy readout
  const prodRaw = activePlanet?.energy?.produced;
  const usedRaw = activePlanet?.energy?.used;
  const prodFinite = prodRaw == null ? 0 : finiteNum(prodRaw, null);
  const usedFinite = usedRaw == null ? 0 : finiteNum(usedRaw, null);
  const netRaw = prodFinite !== null && usedFinite !== null ? prodFinite - usedFinite : null;
  const netEnergy = netRaw === null ? null : netRaw === 0 ? 0 : netRaw;
  const energyValText = activePlanet
    ? netEnergy === null
      ? "—"
      : `${netEnergy >= 0 ? "+" : ""}${fmtInt(netEnergy)}`
    : "+0";
  const energyRatioText = activePlanet
    ? `${prodFinite === null ? "—" : fmtInt(prodFinite)} / ${usedFinite === null ? "—" : fmtInt(usedFinite)} E`
    : "0 / 0 E";

  // Colony overview meta text
  const pTemp = finiteNum(activePlanet?.temperature, null);
  const pFieldsUsed = finiteNum(activePlanet?.fields_used, null);
  const pFieldsMax = finiteNum(activePlanet?.fields_max, null);
  const pType = String(activePlanet?.planet_type || "normal").toUpperCase();
  const planetMetaText = activePlanet
    ? `${pType} · ${pTemp === null ? "—" : `${pTemp}°C`} · ${pFieldsUsed === null ? "—" : pFieldsUsed}/${pFieldsMax === null ? "—" : pFieldsMax}`
    : "COLONY STANDBY";

  // Ranking & hourly SpiceEq flow readouts
  const rnk = dashboard?.ranking || {};
  const curRnk = rnk.current || {};
  const rnkRates = rnk.rates || {};
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

  // Active queues summary strip + 3 domain buckets
  const activeQueues = useMemo(
    () => (Array.isArray(gs?.active_queues) ? gs!.active_queues! : []),
    [gs]
  );
  const effNowSec =
    nowEpochSec != null && nowEpochSec > 0 ? nowEpochSec : Math.floor(Date.now() / 1000);

  const { execBuildingJobs, execFleetJobs, execResearchJobs } = useMemo(() => {
    const bld: QueueJobData[] = [];
    const flt: QueueJobData[] = [];
    const res: QueueJobData[] = [];
    for (const job of activeQueues) {
      const dom = dashboardQueueDomainForJob(job);
      if (dom === "research") res.push(job);
      else if (dom === "fleet") flt.push(job);
      else bld.push(job);
    }
    return { execBuildingJobs: bld, execFleetJobs: flt, execResearchJobs: res };
  }, [activeQueues]);

  const allPlanActions = useMemo(() => dashboard?.plan || [], [dashboard?.plan]);
  const manualList = useMemo(() => {
    if (Array.isArray(dashboard?.manual_queue)) return dashboard!.manual_queue!;
    return allPlanActions.filter(
      (a) => a.manual_queue_id != null && Number(a.manual_queue_id) > 0
    );
  }, [allPlanActions, dashboard]);
  const pinnedCount = manualList.length;

  const { buildingActions, fleetActions, researchActions } = useMemo(() => {
    const bld: PlannedActionData[] = [];
    const flt: PlannedActionData[] = [];
    const res: PlannedActionData[] = [];
    for (const act of allPlanActions) {
      const dom = dashboardQueueDomainForAction(act);
      if (dom === "research") res.push(act);
      else if (dom === "fleet") flt.push(act);
      else bld.push(act);
    }
    return { buildingActions: bld, fleetActions: flt, researchActions: res };
  }, [allPlanActions]);

  const cfgMode = String(
    dashboard?.config?.planner_mode || dashboard?.planner_mode || "priority"
  ).toLowerCase();
  const isGaNn = cfgMode === "ga_nn";
  const modeLabel = isGaNn ? "MODE: GA+NN" : "MODE: PRIORITY";

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
      setUiPref("bldCategory", "research");
      setActivePage("buildings");
    });
  }, [setActivePage, setUiPref]);

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
        <article className="hud-window">
          <div className="window-bar">
            <div className="window-title-group">
              <span className="module-code tabular">SEC</span>
              <h2>Resources &amp; Hourly Net Flow</h2>
            </div>
            <span className="module-meta tabular" id="command-subpath">
              <span id="planet-title">
                {activePlanet
                  ? `SECTOR // [${activePlanet.coords || "--"}] ${String(activePlanet.name || "COLONY").toUpperCase()}`
                  : "SECTOR // ACTIVE COLONY"}
              </span>
            </span>
          </div>

          <div className="telemetry-core">
            <div className="track-list" aria-label="Planetary resource levels and net hourly flow">
              <ResourceTrackRow
                key="pig"
                idKey="pig"
                code="PIG"
                name="Pig Iron"
                amount={activePlanet?.resources?.pig_iron}
                storage={activePlanet?.storage?.pig_iron}
                hourlyRate={pEcon?.pig_iron_per_hr}
                hasPlanet={Boolean(activePlanet)}
              />
              <ResourceTrackRow
                key="metal"
                idKey="metal"
                code="MET"
                name="Metal"
                amount={activePlanet?.resources?.metal}
                storage={activePlanet?.storage?.metal}
                hourlyRate={pEcon?.metal_per_hr}
                hasPlanet={Boolean(activePlanet)}
              />
              <ResourceTrackRow
                key="kryp"
                idKey="kryp"
                code="KRY"
                name="Kryptonite"
                amount={activePlanet?.resources?.kryptonite}
                storage={activePlanet?.storage?.kryptonite}
                hourlyRate={pEcon?.kryptonite_per_hr}
                hasPlanet={Boolean(activePlanet)}
              />
              <ResourceTrackRow
                key="spice"
                idKey="spice"
                code="SPI"
                name="Spice"
                amount={activePlanet?.resources?.spice}
                storage={activePlanet?.storage?.spice}
                hourlyRate={pEcon?.spice_per_hr}
                hasPlanet={Boolean(activePlanet)}
              />
            </div>

            <div className="digital-readout-row">
              <div className="digital-counter">
                <span className="digital-tag tabular">ENG</span>
                <span className="digital-number tabular" id="val-energy">
                  {energyValText}
                </span>
                <span className="digital-sub tabular" id="energy-ratio">
                  {energyRatioText}
                </span>
              </div>
              <div className="digital-counter">
                <span className="digital-tag tabular">RNK</span>
                <span className="digital-number tabular" id="dash-rank-val">
                  {dashRankText}
                </span>
                <span className="digital-sub tabular" id="dash-az-val">
                  {dashAzText}
                </span>
              </div>
              <div className="digital-counter">
                <span className="digital-tag tabular">FLW</span>
                <span className="digital-number tabular" id="dash-pts-hr">
                  {dashPtsHrText}
                </span>
                <span className="digital-sub tabular" id="dash-spice-hr">
                  {dashSpiceHrText}
                </span>
              </div>
            </div>
          </div>
        </article>

        <article className="hud-window">
          <div className="window-bar window-bar-wrap">
            <div className="window-title-group">
              <span className="module-code tabular">MAP</span>
              <h2>Empire Colonies &amp; Active Queues</h2>
              <span className="module-meta tabular" id="planet-meta">
                {planetMetaText}
              </span>
            </div>
            <div className="inline-actions">
              <span className="module-meta tabular" id="econ-bf-status">
                {b2Pct === 0 ? "B2: 0%" : `B2: ${b2Pct}%`}
              </span>
              <button
                type="button"
                className={b2Pct === 0 ? "btn btn-primary btn-sm" : "btn btn-secondary btn-sm"}
                id="btn-dash-b2-toggle"
                data-target-pct={b2Pct === 0 ? "100" : "0"}
                onClick={() => toggleBlastFurnaceRate(b2Pct === 0 ? 100 : 0)}
              >
                {b2Pct === 0 ? "Resume b2 (100%)" : "Pause b2 (0% +Pig)"}
              </button>
              <span className="module-meta tabular" id="colony-count-tag">
                {colonyCountTag}
              </span>
            </div>
          </div>

          <div className="matrix-body">
            <div className="table-wrap table-scroll">
              <table>
                <thead>
                  <tr>
                    <SortableTh
                      label="Coords"
                      sortKey="coords"
                      activeKey={colonySort.sortKey}
                      direction={colonySort.sortDirection}
                      onSort={colonySort.requestSort}
                    />
                    <SortableTh
                      label="Colony"
                      sortKey="colony"
                      activeKey={colonySort.sortKey}
                      direction={colonySort.sortDirection}
                      onSort={colonySort.requestSort}
                    />
                    <SortableTh
                      label="State"
                      sortKey="state"
                      activeKey={colonySort.sortKey}
                      direction={colonySort.sortDirection}
                      onSort={colonySort.requestSort}
                    />
                    <SortableTh
                      label="Fields"
                      sortKey="fields"
                      activeKey={colonySort.sortKey}
                      direction={colonySort.sortDirection}
                      onSort={colonySort.requestSort}
                      align="right"
                    />
                  </tr>
                </thead>
                <tbody id="colony-tbody">
                  {colonySort.sortedRows.length === 0 ? (
                    <tr>
                      <td colSpan={4} className="empty-cell">
                        {dashboard ? "No colonies discovered." : "Waiting for snapshot..."}
                      </td>
                    </tr>
                  ) : (
                    colonySort.sortedRows.map((p) => {
                      const isActive = p.planet_id === chosenId;
                      const pigStor = finiteNum(p.storage?.pig_iron, 0);
                      const pigAmt = finiteNum(p.resources?.pig_iron, 0);
                      const spiStor = finiteNum(p.storage?.spice, 0);
                      const spiAmt = finiteNum(p.resources?.spice, 0);
                      const nearFull =
                        (pigStor > 0 && pigAmt / pigStor >= 0.85) ||
                        (spiStor > 0 && spiAmt / spiStor >= 0.85);
                      const statusWord = nearFull ? "NEAR-CAP" : isActive ? "ONLINE" : "READY";
                      const fUsed = finiteNum(p.fields_used, null);
                      const fMax = finiteNum(p.fields_max, null);

                      return (
                        <tr
                          key={p.planet_id}
                          className={`clickable-row${isActive ? " is-active" : ""}`}
                          onClick={() => setSelectedPlanetId(p.planet_id)}
                        >
                          <td className="tabular">{p.coords || "--"}</td>
                          <td>{p.name || "Colony"}</td>
                          <td>
                            <span className={`status-word${nearFull ? " is-warn" : ""}`}>
                              {statusWord}
                            </span>
                          </td>
                          <td className="col-right tabular">
                            {`${fUsed === null ? "—" : fUsed}/${fMax === null ? "—" : fMax}`}
                          </td>
                        </tr>
                      );
                    })
                  )}
                </tbody>
              </table>
            </div>

            <div className="queue-strip">
              <div className="queue-strip-head tabular">
                <span>ACTIVE QUEUES</span>
                <span id="matrix-account-line">{matrixAccountLine}</span>
              </div>
              <div id="active-queues-list" className="queue-summary tabular">
                {activeQueues.length === 0
                  ? dashboard
                    ? "No active construction or research jobs."
                    : "No active construction jobs."
                  : activeQueues.map((q, idx) => {
                      const catTag = `[${String(q.category || "JOB").toUpperCase()}]`;
                      const titleTxt = cleanQueueJobTitle(q);
                      const finTs = finiteNum(q.finish_ts, null);
                      return (
                        <React.Fragment key={`qstrip-${idx}-${q.name || idx}`}>
                          {idx > 0 ? " · " : null}
                          <span>
                            {finTs !== null && finTs > 0 ? (
                              <>
                                {`${catTag} ${titleTxt} (`}
                                <span data-finish-ts={String(finTs)}>
                                  {fmtHms(Math.max(0, Math.round(finTs - effNowSec)))}
                                </span>
                                {` · ${fmtClockTime(finTs, effNowSec)})`}
                              </>
                            ) : q.remaining_text ? (
                              `${catTag} ${titleTxt} (${q.remaining_text})`
                            ) : (
                              `${catTag} ${titleTxt}`
                            )}
                          </span>
                        </React.Fragment>
                      );
                    })}
              </div>
            </div>
          </div>
        </article>
      </div>

      {/* GA + Neural Network Radial-Ring Calculation HUD */}
      <GaNnScannerHud />

      {/* Lower Row Toolbar */}
      <div className="dash-queues-toolbar" aria-label="Planned queues overview and navigation">
        <div className="window-title-group">
          <span
            className={`module-meta tabular mode-badge${isGaNn ? " is-ga" : ""}`}
            id="dash-planner-mode-badge"
          >
            {modeLabel}
          </span>
          <span
            className="module-meta tabular"
            id="dash-pinned-count-badge"
            hidden={pinnedCount === 0}
          >
            {`${pinnedCount} PINNED`}
          </span>
        </div>
        <div className="inline-actions">
          <button
            type="button"
            className="btn btn-secondary btn-sm"
            data-goto-page="action-graph"
            onClick={() => setActivePage("action-graph")}
          >
            Action Graph →
          </button>
          <button
            type="button"
            className="btn btn-secondary btn-sm"
            data-goto-page="operations"
            onClick={() => setActivePage("operations")}
          >
            Full Queue →
          </button>
        </div>
      </div>

      {/* Lower Row: 3 Side-by-Side Queues (Building, Fleet, Research) */}
      <div className="dash-lower-grid">
        <DashboardQueueCard
          cardId="dash-queue-card-building"
          moduleCode="NXQ"
          title="Building Queue"
          countId="dash-bld-q-count"
          addBtnId="btn-dash-add-queue"
          addBtnAriaLabel="Queue building command"
          onAddQueueClick={() => openComposerWithKind("BUILD")}
          gotoLabel="Buildings →"
          gotoPageAttr="buildings"
          onGotoClick={() => setActivePage("buildings")}
          execTbodyId="dash-exec-building-tbody"
          planTbodyId="dash-plan-tbody"
          execJobs={execBuildingJobs}
          plannedActions={buildingActions}
          emptyText={
            dashboard
              ? "No queued building actions."
              : "Run Sync to populate building actions..."
          }
          nowEpochSec={effNowSec}
          onDispatch={handleDispatch}
          onUpdateQueue={handleUpdateQueue}
          onCancelQueue={handleCancelQueue}
          onPinQueue={handlePinQueue}
        />

        <DashboardQueueCard
          cardId="dash-queue-card-fleet"
          moduleCode="FLT"
          title="Fleet Queue"
          countId="dash-fleet-q-count"
          addBtnId="btn-dash-add-fleet-queue"
          addBtnAriaLabel="Queue fleet or ship command"
          onAddQueueClick={() => openComposerWithKind("BUILD_SHIP")}
          gotoLabel="Fleet →"
          gotoPageAttr="combat"
          onGotoClick={() => setActivePage("combat")}
          execTbodyId="dash-exec-fleet-tbody"
          planTbodyId="dash-plan-fleet-tbody"
          execJobs={execFleetJobs}
          plannedActions={fleetActions}
          emptyText="No queued fleet or ship actions."
          nowEpochSec={effNowSec}
          onDispatch={handleDispatch}
          onUpdateQueue={handleUpdateQueue}
          onCancelQueue={handleCancelQueue}
          onPinQueue={handlePinQueue}
        />

        <DashboardQueueCard
          cardId="dash-queue-card-research"
          moduleCode="RES"
          title="Research Queue"
          countId="dash-research-q-count"
          addBtnId="btn-dash-add-res-queue"
          addBtnAriaLabel="Queue research command"
          onAddQueueClick={() => openComposerWithKind("RESEARCH")}
          gotoLabel="Research →"
          gotoBtnId="btn-dash-goto-research"
          onGotoClick={handleGotoResearch}
          execTbodyId="dash-exec-research-tbody"
          planTbodyId="dash-plan-research-tbody"
          execJobs={execResearchJobs}
          plannedActions={researchActions}
          emptyText="No queued research actions."
          nowEpochSec={effNowSec}
          onDispatch={handleDispatch}
          onUpdateQueue={handleUpdateQueue}
          onCancelQueue={handleCancelQueue}
          onPinQueue={handlePinQueue}
        />
      </div>
    </section>
  );
};

export default DashboardView;
