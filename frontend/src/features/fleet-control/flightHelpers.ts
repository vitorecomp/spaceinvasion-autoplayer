import type { ResourceBundle } from "../../types/api";
import { finiteNum, fmtClockTime, fmtHms } from "../../utils/formatters";
import { cleanQueueJobTitle } from "../operations/planActions";
import {
  classifyFlightDirection,
  classifyFlightMission,
  extractCoordsFromText,
  formatShipComposition,
  missionMeta,
  normalizeCoordKey,
  resolveFlightCargo,
} from "./flightClassification";
import {
  COORD_REGEX,
  type FlightMissionFilter,
  type FlightQueueJob,
  type FlightSummary,
  type ParsedFlightRow,
  type PlanetLookup,
} from "./flightTypes";

export type {
  FlightCargoDisplay,
  FlightCargoKind,
  FlightDirection,
  FlightDirectionFilter,
  FlightMission,
  FlightMissionFilter,
  FlightQueueJob,
  FlightSummary,
  NormalizedBundle,
  ParsedFlightRow,
  PlanetLookup,
  PlanetLookupEntry,
} from "./flightTypes";

export {
  buildPlanetLookup,
  classifyFlightDirection,
  classifyFlightMission,
  extractCoordsFromText,
  extractCustomPlanetNames,
  formatShipComposition,
  isOngoingFlightJob,
  normalizeCoordKey,
  normalizeResourceBundle,
  resolveFlightCargo,
  resolvePlanetDisplayName,
} from "./flightClassification";

function parseTargetFromTitle(title: string): { name: string; coords: string } {
  const trimmed = String(title || "").trim();
  if (!trimmed) return { name: "", coords: "" };

  const arrowParts = trimmed.split(/→|->|↩|←|<-/);
  const tail = arrowParts.length > 1 ? arrowParts.slice(1).join(" ").trim() : "";
  const searchIn = tail || trimmed;
  const coords = extractCoordsFromText(searchIn);

  let name = tail;
  if (name && coords) {
    name = name
      .replace(COORD_REGEX, "")
      .replace(/[()[\]]+/g, " ")
      .replace(/\s+/g, " ")
      .replace(/^[\s·\-:]+|[\s·\-:]+$/g, "")
      .trim();
  } else if (!tail && coords) {
    name = "";
  }
  return { name, coords };
}

export function parseFlightRow(
  job: FlightQueueJob,
  index: number,
  lookup: PlanetLookup,
  nowEpochSec: number
): ParsedFlightRow {
  const mission = classifyFlightMission(job);
  const direction = classifyFlightDirection(job);
  const meta = missionMeta(mission);

  // Origin resolution
  const rawOriginCoords = String(job.origin_coords || job.planet_coords || "").trim();
  const originNorm = normalizeCoordKey(rawOriginCoords);
  const jobPid = finiteNum(job.planet_id, null);
  const originEntry =
    (jobPid !== null && jobPid > 0 ? lookup.byId.get(Math.trunc(jobPid)) : undefined) ||
    (originNorm ? lookup.byNormCoords.get(originNorm) : undefined) ||
    null;

  const originCoords =
    originEntry?.rawCoords || extractCoordsFromText(rawOriginCoords) || rawOriginCoords;
  const originNormCoords = originEntry?.normCoords || originNorm;
  const originName =
    originEntry?.displayName ||
    String(job.origin_name || "").trim() ||
    (originCoords ? originCoords : "—");
  const originPlanetId = originEntry
    ? originEntry.planet.planet_id
    : jobPid !== null && jobPid > 0
      ? Math.trunc(jobPid)
      : null;
  const originIsOwn = Boolean(originEntry);

  // Target resolution
  const titleSrc = String(job.name || job.label || "").trim();
  const parsedFromTitle = parseTargetFromTitle(titleSrc);
  const explicitTargetCoords = String(
    job.target_coords || job.dest_coords || job.destination_coords || ""
  ).trim();
  const targetCoordsRaw = explicitTargetCoords || parsedFromTitle.coords;
  const targetNorm = normalizeCoordKey(targetCoordsRaw);
  const targetEntry = targetNorm ? lookup.byNormCoords.get(targetNorm) || null : null;

  const targetCoords =
    targetEntry?.rawCoords || extractCoordsFromText(targetCoordsRaw) || targetCoordsRaw;
  const targetNormCoords = targetEntry?.normCoords || targetNorm;
  const explicitTargetName = String(job.target_name || job.dest_name || "").trim();
  const targetName =
    targetEntry?.displayName ||
    explicitTargetName ||
    parsedFromTitle.name ||
    (targetCoords ? targetCoords : "—");
  const targetPlanetId = targetEntry ? targetEntry.planet.planet_id : null;
  const targetIsOwn = Boolean(targetEntry);

  // Ships & label
  const shipsText = formatShipComposition(job.ships, job.detail);
  const explicitLabel = String(job.label || "").trim();
  const fallbackTitle = cleanQueueJobTitle(job);
  const labelText = explicitLabel || (shipsText ? "" : fallbackTitle);

  // Cargo & Expected/Delivered contract
  const cargo = resolveFlightCargo(job, mission, direction);

  // Live ticking countdown ETA
  const finTsRaw = finiteNum(job.finish_ts, null);
  const finishTs = finTsRaw !== null && finTsRaw > 0 ? finTsRaw : null;
  const remFromField = finiteNum(job.remaining_seconds, null);
  let remainingSec: number | null = null;
  let countdownText = String(job.remaining_text || "").trim() || "In flight";
  let finishClockText = "In progress";

  if (finishTs !== null) {
    remainingSec = Math.max(0, Math.round(finishTs - nowEpochSec));
    countdownText = remainingSec > 0 ? fmtHms(remainingSec) : "Finishing...";
    finishClockText = fmtClockTime(finishTs, nowEpochSec);
  } else if (remFromField !== null) {
    remainingSec = Math.max(0, Math.round(remFromField));
    countdownText = remainingSec > 0 ? fmtHms(remainingSec) : "Finishing...";
  }

  const stableId = `flt-${mission}-${direction}-${originNormCoords || "x"}-${targetNormCoords || "x"}-${finishTs ?? remFromField ?? index}-${index}`;

  return {
    id: stableId,
    mission,
    missionFilter: meta.filter,
    missionLabel: meta.label,
    missionBadgeClass: meta.badgeClass,
    direction,
    originName,
    originCoords,
    originNormCoords,
    originPlanetId,
    originIsOwn,
    targetName,
    targetCoords,
    targetNormCoords,
    targetPlanetId,
    targetIsOwn,
    shipsText,
    labelText,
    cargo,
    finishTs,
    remainingSec,
    countdownText,
    finishClockText,
    raw: job,
  };
}

export function flightMatchesPlanetFilter(
  row: ParsedFlightRow,
  selectedPlanetId: number,
  lookup: PlanetLookup
): boolean {
  if (row.originPlanetId === selectedPlanetId || row.targetPlanetId === selectedPlanetId) {
    return true;
  }
  const entry = lookup.byId.get(selectedPlanetId);
  if (!entry || !entry.normCoords) return false;
  return (
    (Boolean(row.originNormCoords) && row.originNormCoords === entry.normCoords) ||
    (Boolean(row.targetNormCoords) && row.targetNormCoords === entry.normCoords)
  );
}

export function summarizeFlights(rows: ParsedFlightRow[]): FlightSummary {
  const missionCounts: Record<FlightMissionFilter, number> = {
    all: rows.length,
    attack: 0,
    recycle: 0,
    transport: 0,
    spy: 0,
    other: 0,
  };
  let outboundCount = 0;
  let returningCount = 0;
  const expectedBundle: ResourceBundle = { pig_iron: 0, metal: 0, kryptonite: 0, spice: 0 };
  let expectedTotal = 0;
  let expectedCount = 0;
  const deliveredBundle: ResourceBundle = { pig_iron: 0, metal: 0, kryptonite: 0, spice: 0 };
  let deliveredTotal = 0;
  let deliveredCount = 0;

  for (const row of rows) {
    missionCounts[row.missionFilter] += 1;
    if (row.direction === "returning") returningCount += 1;
    else outboundCount += 1;

    if (row.cargo?.kind === "EXPECTED") {
      expectedCount += 1;
      if (row.cargo.bundle) {
        expectedBundle.pig_iron += row.cargo.bundle.pig_iron;
        expectedBundle.metal += row.cargo.bundle.metal;
        expectedBundle.kryptonite += row.cargo.bundle.kryptonite;
        expectedBundle.spice += row.cargo.bundle.spice;
        expectedTotal += row.cargo.total;
      }
    } else if (row.cargo?.kind === "DELIVERED") {
      deliveredCount += 1;
      if (row.cargo.bundle) {
        deliveredBundle.pig_iron += row.cargo.bundle.pig_iron;
        deliveredBundle.metal += row.cargo.bundle.metal;
        deliveredBundle.kryptonite += row.cargo.bundle.kryptonite;
        deliveredBundle.spice += row.cargo.bundle.spice;
        deliveredTotal += row.cargo.total;
      }
    }
  }

  return {
    total: rows.length,
    outboundCount,
    returningCount,
    missionCounts,
    expectedBundle,
    expectedTotal,
    expectedCount,
    deliveredBundle,
    deliveredTotal,
    deliveredCount,
  };
}
