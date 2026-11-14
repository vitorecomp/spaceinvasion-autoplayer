import type {
  BuildingResourceRequestItem,
  DashboardPayload,
  PlanetData,
  PlannedActionData,
  QueueJobData,
} from "../../types/api";
import { finiteNum } from "../../utils/formatters";
import {
  cleanQueueJobTitle,
  dashboardQueueDomainForJob,
  isActiveBuildingJob,
  normalizeCoordKey,
} from "../operations/planActions";

export function sumOptionalFinite(values: unknown[]): number | null {
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

export function computeScopedEcon(args: {
  isAllColoniesScope: boolean;
  planets: PlanetData[];
  resourcePlanet: PlanetData | null | undefined;
  planetEconMap: Record<string, any> | null;
  rnkRates: Record<string, any>;
  buildingsEtaData: Record<string, any> | null | undefined;
  dashboardBuildingsEta: Record<string, any> | null | undefined;
}): Record<string, any> | null {
  const {
    isAllColoniesScope,
    planets,
    resourcePlanet,
    planetEconMap,
    rnkRates,
    buildingsEtaData,
    dashboardBuildingsEta,
  } = args;

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
    dashboardBuildingsEta?.planet?.planet_id === resourcePlanet.planet_id &&
    dashboardBuildingsEta.planet_economy
  ) {
    return dashboardBuildingsEta.planet_economy;
  }
  if (planets.length <= 1) {
    return dashboardBuildingsEta?.planet_economy || null;
  }
  return null;
}

export function partitionActiveQueues(
  activeQueues: QueueJobData[],
  planets: PlanetData[],
  activePlanetId: number | null | undefined,
  effNowSec: number
): {
  execBuildingJobs: QueueJobData[];
  execFleetJobs: QueueJobData[];
  execResearchJobs: QueueJobData[];
} {
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
        (activePlanetId != null
          ? coordsByPid.get(Number(activePlanetId)) || `p${Number(activePlanetId)}`
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
}

export function deriveBuildingResourceRequests(
  dashboard: DashboardPayload | null | undefined,
  execFleetJobs: QueueJobData[],
  allPlanActions: PlannedActionData[],
  planets: PlanetData[]
): BuildingResourceRequestItem[] {
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
}
