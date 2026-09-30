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
import type {
  BuildingResourceRequestItem,
  PlanetData,
  PlannedActionData,
  QueueJobData,
} from "../../types/api";
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
  execJobBaseKey,
  formatQueueJobCargo,
  isActiveBuildingJob,
  isPlanetConstructing,
  normalizeCoordKey,
  queueJobMatchesPlanet,
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

function sumOptionalFinite(values: unknown[]): number | null {
  let total = 0;
  let found = false;
  for (const raw of values) {
    const n = finiteNum(raw, null);
    if (n !== null) {
      total += n;
      found = true;
    }
  }
  return found ? total : null;
}

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

  const colonySortCols = useMemo<
    SortColumnMap<PlanetData, "coords" | "colony" | "state" | "fields">
  >(
    () => ({
      coords: (p) => coordsSortKey(p.coords),
      colony: (p) => String(p.name || "Colony"),
      state: (p) => {
        if (constructingPlanetIds.has(Number(p.planet_id))) return "BUILDING";
        const isActive = p.planet_id === chosenId;
        const pigStor = finiteNum(p.storage?.pig_iron, 0);
        const pigAmt = finiteNum(p.resources?.pig_iron, 0);
        const spiStor = finiteNum(p.storage?.spice, 0);
        const spiAmt = finiteNum(p.resources?.spice, 0);
        const nearFull =
          (pigStor > 0 && pigAmt / pigStor >= 0.85) ||
          (spiStor > 0 && spiAmt / spiStor >= 0.85);
        return nearFull ? "NEAR-CAP" : isActive ? "ONLINE" : "IDLE";
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
    [chosenId, constructingPlanetIds]
  );

  const colonySort = useTableSort(planets, colonySortCols);

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

  const scopedEcon = useMemo(() => {
    if (isAllColoniesScope) {
      const allHavePerPlanetEcon =
        Boolean(planetEconMap) &&
        planets.length > 0 &&
        planets.every((p) => planetEconMap![String(p.planet_id)] != null);
      if (allHavePerPlanetEcon) {
        const econRows = planets.map((p) => planetEconMap![String(p.planet_id)]);
        return {
          pig_iron_per_hr: sumOptionalFinite(econRows.map((e) => e?.pig_iron_per_hr)),
          metal_per_hr: sumOptionalFinite(econRows.map((e) => e?.metal_per_hr)),
          kryptonite_per_hr: sumOptionalFinite(econRows.map((e) => e?.kryptonite_per_hr)),
          spice_per_hr: sumOptionalFinite(econRows.map((e) => e?.spice_per_hr)),
        };
      }
      if (rnkRates && Object.keys(rnkRates).length > 0) {
        return {
          pig_iron_per_hr: rnkRates.pig_iron_per_hr ?? null,
          metal_per_hr: rnkRates.metal_per_hr ?? null,
          kryptonite_per_hr: rnkRates.kryptonite_per_hr ?? null,
          spice_per_hr: rnkRates.spice_per_hr ?? null,
        };
      }
      return null;
    }

    if (!resourcePlanet) return null;
    const directEcon = planetEconMap?.[String(resourcePlanet.planet_id)];
    if (directEcon && typeof directEcon === "object") {
      return directEcon;
    }
    if (
      buildingsEtaData &&
      buildingsEtaData.planet?.planet_id === resourcePlanet.planet_id &&
      buildingsEtaData.planet_economy
    ) {
      return buildingsEtaData.planet_economy;
    }
    if (
      dashboard?.buildings_eta?.planet?.planet_id === resourcePlanet.planet_id &&
      dashboard.buildings_eta.planet_economy
    ) {
      return dashboard.buildings_eta.planet_economy;
    }
    if (planets.length <= 1) {
      return dashboard?.buildings_eta?.planet_economy || null;
    }
    return null;
  }, [
    buildingsEtaData,
    dashboard?.buildings_eta,
    isAllColoniesScope,
    planetEconMap,
    planets,
    resourcePlanet,
    rnkRates,
  ]);

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

  // Active queues 3 domain buckets (filtering expired cached builds & deduplicating per-planet slots)
  const { execBuildingJobs, execFleetJobs, execResearchJobs } = useMemo(() => {
    const bld: QueueJobData[] = [];
    const flt: QueueJobData[] = [];
    const res: QueueJobData[] = [];
    const coordsByPid = new Map<number, string>();
    for (const p of planets) {
      const ck = normalizeCoordKey(p.coords);
      if (ck) coordsByPid.set(Number(p.planet_id), ck);
    }
    const seenBuildingSlots = new Set<string>();
    for (const job of activeQueues) {
      const dom = dashboardQueueDomainForJob(job);
      if (dom === "research") {
        res.push(job);
      } else if (dom === "fleet") {
        flt.push(job);
      } else {
        if (!isActiveBuildingJob(job, effNowSec)) continue;
        const jPid =
          job.planet_id != null && Number.isFinite(Number(job.planet_id)) && Number(job.planet_id) > 0
            ? Math.trunc(Number(job.planet_id))
            : null;
        const jCoords = normalizeCoordKey(job.origin_coords || job.planet_coords);
        const canonOrigin =
          jCoords ||
          (jPid !== null ? coordsByPid.get(jPid) || `p${jPid}` : "") ||
          (gs?.active_planet_id != null
            ? coordsByPid.get(Number(gs.active_planet_id)) || `p${Number(gs.active_planet_id)}`
            : "");
        const slot = Math.max(1, Math.trunc(finiteNum(job.queue_slot, 1)));
        if (canonOrigin) {
          const slotKey = `${canonOrigin}:s${slot}`;
          if (seenBuildingSlots.has(slotKey)) continue;
          seenBuildingSlots.add(slotKey);
        }
        bld.push(job);
      }
    }
    return { execBuildingJobs: bld, execFleetJobs: flt, execResearchJobs: res };
  }, [activeQueues, effNowSec, gs?.active_planet_id, planets]);

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

  const allBuildingResourceRequests = useMemo<BuildingResourceRequestItem[]>(() => {
    if (Array.isArray(dashboard?.building_resource_requests)) {
      return dashboard!.building_resource_requests!;
    }
    const derived: BuildingResourceRequestItem[] = [];
    const planetById = new Map<number, PlanetData>();
    const planetByCoords = new Map<string, PlanetData>();
    for (const p of planets) {
      planetById.set(Number(p.planet_id), p);
      const ck = normalizeCoordKey(p.coords);
      if (ck) planetByCoords.set(ck, p);
    }

    for (const job of execFleetJobs) {
      const nameLow = String(job.name || "").toLowerCase();
      const detLow = String(job.detail || "").toLowerCase();
      const isReturning =
        Boolean(job.returning) || detLow.includes("returning") || nameLow.includes("↩");
      const isAttack = nameLow.includes("attack");
      const isRecycle = nameLow.includes("recycle");
      const isTransport =
        !isAttack &&
        !isRecycle &&
        (nameLow.includes("transport") || nameLow.includes("transfer"));
      if (!isTransport || isReturning || job.delivered) continue;
      const bundle = job.expected_cargo || job.cargo;
      if (!bundle) continue;
      const pig = Math.max(0, Math.round(finiteNum(bundle.pig_iron, 0)));
      const met = Math.max(0, Math.round(finiteNum(bundle.metal, 0)));
      const kry = Math.max(0, Math.round(finiteNum(bundle.kryptonite, 0)));
      const spi = Math.max(0, Math.round(finiteNum(bundle.spice, 0)));
      if (pig + met + kry + spi <= 0) continue;
      const mCoords = /(\d{1,2}:\d{1,3}:\d{1,2})/.exec(String(job.name || ""));
      const destCoords = mCoords ? mCoords[1] : "";
      const destPlanet = destCoords ? planetByCoords.get(normalizeCoordKey(destCoords)) : undefined;
      derived.push({
        id: `flight-${derived.length}-${job.origin_coords || ""}-${destCoords || job.name || ""}`,
        planet_id: destPlanet?.planet_id ?? null,
        planet_coords: destPlanet?.coords || destCoords,
        planet_name: destPlanet?.name,
        label: cleanQueueJobTitle(job),
        status: "in_flight",
        resources: { pig_iron: pig, metal: met, kryptonite: kry, spice: spi },
        source_coords: job.origin_coords || job.planet_coords,
        eta_seconds: finiteNum(job.remaining_seconds, null),
        finish_ts: finiteNum(job.finish_ts, null),
        reason: job.detail,
      });
    }

    for (const act of allPlanActions) {
      if (String(act.kind || "").toUpperCase() !== "TRANSPORT") continue;
      const bundle = act.cost;
      if (!bundle) continue;
      const pig = Math.max(0, Math.round(finiteNum(bundle.pig_iron, 0)));
      const met = Math.max(0, Math.round(finiteNum(bundle.metal, 0)));
      const kry = Math.max(0, Math.round(finiteNum(bundle.kryptonite, 0)));
      const spi = Math.max(0, Math.round(finiteNum(bundle.spice, 0)));
      if (pig + met + kry + spi <= 0) continue;
      const mCoords = /(\d{1,2}:\d{1,3}:\d{1,2})/.exec(String(act.target_name || ""));
      const destCoords = mCoords ? mCoords[1] : "";
      const destPlanet =
        (act.target_id != null ? planetById.get(Number(act.target_id)) : undefined) ||
        (destCoords ? planetByCoords.get(normalizeCoordKey(destCoords)) : undefined);
      const srcPlanet = act.planet_id != null ? planetById.get(Number(act.planet_id)) : undefined;
      derived.push({
        id: `plan-tr-${act.planet_id ?? 0}-${destPlanet?.planet_id ?? destCoords}-${derived.length}`,
        planet_id: destPlanet?.planet_id ?? (act.target_id ?? null),
        planet_coords: destPlanet?.coords || destCoords,
        planet_name: destPlanet?.name,
        label: act.target_name || "Resource transport",
        status: "planned",
        resources: { pig_iron: pig, metal: met, kryptonite: kry, spice: spi },
        source_planet_id: act.planet_id ?? null,
        source_coords: act.planet_coords || srcPlanet?.coords,
        source_name: srcPlanet?.name,
        eta_seconds: finiteNum(act.total_seconds ?? act.wait_seconds, null),
        reason: act.reason,
      });
    }

    return derived;
  }, [allPlanActions, dashboard?.building_resource_requests, execFleetJobs, planets]);

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
              <span id="planet-title">{sectorTitleText}</span>
            </span>
          </div>

          <div className="telemetry-core">
            <div className="track-list" aria-label="Planetary resource levels and net hourly flow">
              <ResourceTrackRow
                key="pig"
                idKey="pig"
                code="PIG"
                name="Pig Iron"
                amount={scopedResources.pig_iron}
                storage={scopedStorage.pig_iron}
                hourlyRate={scopedEcon?.pig_iron_per_hr}
                hasPlanet={hasResourceScope}
              />
              <ResourceTrackRow
                key="metal"
                idKey="metal"
                code="MET"
                name="Metal"
                amount={scopedResources.metal}
                storage={scopedStorage.metal}
                hourlyRate={scopedEcon?.metal_per_hr}
                hasPlanet={hasResourceScope}
              />
              <ResourceTrackRow
                key="kryp"
                idKey="kryp"
                code="KRY"
                name="Kryptonite"
                amount={scopedResources.kryptonite}
                storage={scopedStorage.kryptonite}
                hourlyRate={scopedEcon?.kryptonite_per_hr}
                hasPlanet={hasResourceScope}
              />
              <ResourceTrackRow
                key="spice"
                idKey="spice"
                code="SPI"
                name="Spice"
                amount={scopedResources.spice}
                storage={scopedStorage.spice}
                hourlyRate={scopedEcon?.spice_per_hr}
                hasPlanet={hasResourceScope}
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
                      const isConstructing = constructingPlanetIds.has(Number(p.planet_id));
                      const pigStor = finiteNum(p.storage?.pig_iron, 0);
                      const pigAmt = finiteNum(p.resources?.pig_iron, 0);
                      const spiStor = finiteNum(p.storage?.spice, 0);
                      const spiAmt = finiteNum(p.resources?.spice, 0);
                      const nearFull =
                        (pigStor > 0 && pigAmt / pigStor >= 0.85) ||
                        (spiStor > 0 && spiAmt / spiStor >= 0.85);
                      const statusWord = isConstructing
                        ? "BUILDING"
                        : nearFull
                          ? "NEAR-CAP"
                          : isActive
                            ? "ONLINE"
                            : "IDLE";
                      const statusClass = isConstructing
                        ? "status-word is-constructing"
                        : nearFull
                          ? "status-word is-warn"
                          : isActive
                            ? "status-word is-active-idle"
                            : "status-word is-idle";
                      const fUsed = finiteNum(p.fields_used, null);
                      const fMax = finiteNum(p.fields_max, null);

                      return (
                        <tr
                          key={p.planet_id}
                          className={`clickable-row${isActive ? " is-active" : ""}${isConstructing ? " is-constructing" : ""}`}
                          data-constructing={isConstructing ? "true" : "false"}
                          onClick={() => {
                            setSelectedPlanetId(p.planet_id);
                            if (planets.length > 1) {
                              setUiPref("dashQueuePlanetScope", String(p.planet_id));
                            }
                          }}
                        >
                          <td className="tabular">{p.coords || "--"}</td>
                          <td>{p.name || "Colony"}</td>
                          <td>
                            <span className={statusClass}>{statusWord}</span>
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
                {(() => {
                  const liveStripQueues = activeQueues.filter(
                    (q) =>
                      dashboardQueueDomainForJob(q) !== "building" ||
                      isActiveBuildingJob(q, effNowSec)
                  );
                  if (liveStripQueues.length === 0) {
                    return dashboard
                      ? "No active construction or research jobs."
                      : "No active construction jobs.";
                  }
                  return liveStripQueues.map((q, idx) => {
                    const catTag = `[${String(q.category || "JOB").toUpperCase()}]`;
                    const titleTxt = cleanQueueJobTitle(q);
                    const cargoInfo = formatQueueJobCargo(q);
                    const cargoSuffix =
                      cargoInfo && !cargoInfo.isEmpty
                        ? ` · ${cargoInfo.label}: ${cargoInfo.inline}`
                        : "";
                    const finTs = finiteNum(q.finish_ts, null);
                    return (
                      <React.Fragment key={`qstrip-${execJobBaseKey(q)}-${idx}`}>
                        {idx > 0 ? " · " : null}
                        <span>
                          {finTs !== null && finTs > 0 ? (
                            <>
                              {`${catTag} ${titleTxt}${cargoSuffix} (`}
                              <span data-finish-ts={String(finTs)}>
                                {fmtHms(Math.max(0, Math.round(finTs - effNowSec)))}
                              </span>
                              {` · ${fmtClockTime(finTs, effNowSec)})`}
                            </>
                          ) : q.remaining_text ? (
                            `${catTag} ${titleTxt}${cargoSuffix} (${q.remaining_text})`
                          ) : (
                            `${catTag} ${titleTxt}${cargoSuffix}`
                          )}
                        </span>
                      </React.Fragment>
                    );
                  });
                })()}
              </div>
            </div>
          </div>
        </article>
      </div>

      {/* GA + Neural Network Radial-Ring Calculation HUD */}
      <GaNnScannerHud />

      {/* Lower Row Toolbar */}
      <div className="dash-queues-toolbar" aria-label="Planned queues overview and planet scope">
        <div className="dash-queues-toolbar-meta">
          <span
            className={`module-meta tabular mode-badge${isAiPlanner ? " is-ga" : ""}`}
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
          {planets.length > 1 ? (
            <span className="module-meta tabular" id="dash-builders-summary-badge">
              {`${constructingPlanetIds.size}/${planets.length} BUILDING`}
            </span>
          ) : null}
        </div>
        {planets.length > 1 ? (
          <div
            className="filter-chips dash-planet-scope-chips"
            role="group"
            aria-label="Filter building and fleet queues by planet"
          >
            <button
              type="button"
              className={`chip-btn planet-scope-chip tabular${activeDashScope === "all" ? " active" : ""}`}
              data-dash-planet-scope="all"
              title={`Show all ${planets.length} colonies (${constructingPlanetIds.size} currently constructing)`}
              onClick={() => setUiPref("dashQueuePlanetScope", "all")}
            >
              <span className="planet-chip-top">All Planets</span>
              <span className="planet-chip-sub">
                {`${planets.length} colonies · ${constructingPlanetIds.size} bldg`}
              </span>
            </button>
            {planets.map((p) => {
              const pidKey = String(p.planet_id);
              const stats = planetQueueStats.get(p.planet_id) || { total: 0, ready: 0 };
              const isConstructing = constructingPlanetIds.has(Number(p.planet_id));
              const planetLabel = p.name || "Colony";
              return (
                <button
                  key={pidKey}
                  type="button"
                  className={`chip-btn planet-scope-chip tabular${activeDashScope === pidKey ? " active" : ""}${isConstructing ? " is-constructing" : ""}`}
                  data-dash-planet-scope={pidKey}
                  data-constructing={isConstructing ? "true" : "false"}
                  title={`${planetLabel} [${p.coords}] — ${isConstructing ? "Building in progress" : "Builder idle"} · ${stats.ready} ready / ${stats.total} queued`}
                  onClick={() => {
                    setUiPref("dashQueuePlanetScope", pidKey);
                    setSelectedPlanetId(p.planet_id);
                  }}
                >
                  <span className="planet-chip-top">{p.coords || planetLabel}</span>
                  <span className="planet-chip-sub">
                    {isConstructing
                      ? `${planetLabel} · Building`
                      : `${planetLabel} (${stats.total})`}
                  </span>
                </button>
              );
            })}
          </div>
        ) : null}
      </div>

      {/* Lower Row: 3 Side-by-Side Queues (Building, Fleet, Research) */}
      <div className="dash-lower-grid">
        <DashboardQueueCard
          cardId="dash-queue-card-building"
          moduleCode="NXQ"
          title="Building Queue"
          scopeBadge={
            planets.length > 1
              ? scopedDashPlanet
                ? scopedDashPlanet.name && scopedDashPlanet.name.toLowerCase() !== "colony"
                  ? `${scopedDashPlanet.name} [${scopedDashPlanet.coords || ""}]`
                  : scopedDashPlanet.coords || scopedDashPlanet.name
                : `ALL · ${planets.length} PLANETS`
              : undefined
          }
          countId="dash-bld-q-count"
          addBtnId="btn-dash-add-queue"
          addBtnAriaLabel="Queue building command"
          onAddQueueClick={() => openComposerWithKind("BUILD")}
          gotoLabel="Buildings →"
          gotoPageAttr="buildings"
          onGotoClick={() => setActivePage("buildings")}
          execTbodyId="dash-exec-building-tbody"
          planTbodyId="dash-plan-tbody"
          execJobs={scopedExecBuildingJobs}
          plannedActions={buildingActions}
          resourceRequests={scopedBuildingResourceRequests}
          planets={planets}
          customPlanetNames={dashboard?.custom_planet_names}
          maxRows={dashQueueMaxRows}
          emptyText={
            dashboard
              ? scopedDashPlanet
                ? `No queued building actions on ${scopedDashPlanet.name || scopedDashPlanet.coords}.`
                : "No queued building actions."
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
          scopeBadge={
            planets.length > 1
              ? scopedDashPlanet
                ? scopedDashPlanet.name && scopedDashPlanet.name.toLowerCase() !== "colony"
                  ? `${scopedDashPlanet.name} [${scopedDashPlanet.coords || ""}]`
                  : scopedDashPlanet.coords || scopedDashPlanet.name
                : "PER-PLANET"
              : undefined
          }
          countId="dash-fleet-q-count"
          addBtnId="btn-dash-add-fleet-queue"
          addBtnAriaLabel="Queue fleet or ship command"
          onAddQueueClick={() => openComposerWithKind("BUILD_SHIP")}
          gotoLabel="Fleet →"
          gotoPageAttr="combat"
          onGotoClick={() => setActivePage("combat")}
          execTbodyId="dash-exec-fleet-tbody"
          planTbodyId="dash-plan-fleet-tbody"
          execJobs={scopedExecFleetJobs}
          plannedActions={fleetActions}
          planets={planets}
          customPlanetNames={dashboard?.custom_planet_names}
          maxRows={dashQueueMaxRows}
          emptyText={
            scopedDashPlanet
              ? `No queued fleet or ship actions on ${scopedDashPlanet.name || scopedDashPlanet.coords}.`
              : "No queued fleet or ship actions."
          }
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
          scopeBadge={planets.length > 1 ? "GLOBAL · 1 SLOT" : undefined}
          countId="dash-research-q-count"
          addBtnId="btn-dash-add-res-queue"
          addBtnAriaLabel="Queue research command"
          onAddQueueClick={() => openComposerWithKind("RESEARCH")}
          gotoLabel="Research →"
          gotoBtnId="btn-dash-goto-research"
          gotoPageAttr="research"
          onGotoClick={handleGotoResearch}
          execTbodyId="dash-exec-research-tbody"
          planTbodyId="dash-plan-research-tbody"
          execJobs={execResearchJobs}
          plannedActions={researchActions}
          planets={planets}
          customPlanetNames={dashboard?.custom_planet_names}
          maxRows={dashQueueMaxRows}
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
