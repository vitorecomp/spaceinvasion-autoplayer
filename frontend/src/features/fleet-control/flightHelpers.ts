import type { DashboardPayload, PlanetData, QueueJobData, ResourceBundle } from "../../types/api";
import {
  finiteNum,
  fmtClockTime,
  fmtHms,
  fmtInt,
  formatResourcesInline,
} from "../../utils/formatters";
import { MQ_SHIPS_CATALOG, cleanQueueJobTitle } from "../operations/planActions";

export interface FlightQueueJob extends QueueJobData {
  mission?: string | null;
  kind?: string | null;
  label?: string | null;
  direction?: string | null;
  is_returning?: boolean | null;
  returning?: boolean | null;
  ships?: Record<string, unknown> | string | null;
  target_coords?: string | null;
  dest_coords?: string | null;
  destination_coords?: string | null;
  target_name?: string | null;
  dest_name?: string | null;
  origin_name?: string | null;
  expected_cargo?: ResourceBundle | Record<string, unknown> | null;
  delivered_cargo?: ResourceBundle | Record<string, unknown> | null;
  delivered?: boolean | null;
}

export type FlightMission =
  | "ATTACK"
  | "RECYCLE"
  | "TRANSPORT"
  | "SPY"
  | "COLONIZE"
  | "ESCAPE"
  | "STATION"
  | "TRANSFER"
  | "OTHER";

export type FlightMissionFilter = "all" | "attack" | "recycle" | "transport" | "spy" | "other";

export type FlightDirection = "outbound" | "returning";
export type FlightDirectionFilter = "all" | FlightDirection;

export type FlightCargoKind = "EXPECTED" | "DELIVERED" | "LOOT" | "HARVEST" | "CARGO";

export interface NormalizedBundle {
  bundle: ResourceBundle;
  total: number;
  nonZeroCount: number;
}

export interface FlightCargoDisplay {
  kind: FlightCargoKind;
  bundle: ResourceBundle | null;
  total: number;
  inline: string;
  multiRes: boolean;
  isEmpty: boolean;
}

export interface PlanetLookupEntry {
  planet: PlanetData;
  displayName: string;
  rawCoords: string;
  normCoords: string;
}

export interface PlanetLookup {
  byId: Map<number, PlanetLookupEntry>;
  byNormCoords: Map<string, PlanetLookupEntry>;
  ordered: PlanetLookupEntry[];
}

export interface ParsedFlightRow {
  id: string;
  mission: FlightMission;
  missionFilter: Exclude<FlightMissionFilter, "all">;
  missionLabel: string;
  missionBadgeClass: string;
  direction: FlightDirection;
  originName: string;
  originCoords: string;
  originNormCoords: string;
  originPlanetId: number | null;
  originIsOwn: boolean;
  targetName: string;
  targetCoords: string;
  targetNormCoords: string;
  targetPlanetId: number | null;
  targetIsOwn: boolean;
  shipsText: string;
  labelText: string;
  cargo: FlightCargoDisplay | null;
  finishTs: number | null;
  remainingSec: number | null;
  countdownText: string;
  finishClockText: string;
  raw: FlightQueueJob;
}

export interface FlightSummary {
  total: number;
  outboundCount: number;
  returningCount: number;
  missionCounts: Record<FlightMissionFilter, number>;
  expectedBundle: ResourceBundle;
  expectedTotal: number;
  expectedCount: number;
  deliveredBundle: ResourceBundle;
  deliveredTotal: number;
  deliveredCount: number;
}

const NON_FLIGHT_CATEGORIES = new Set([
  "building",
  "research",
  "shipyard",
  "defense",
  "defence",
]);

const FLIGHT_MISSION_CATEGORIES = new Set([
  "ATTACK",
  "RAID",
  "RECYCLE",
  "TRANSPORT",
  "SPY",
  "SPIONAGE",
  "COLONIZE",
  "ESCAPE",
  "ESCAPE_FLIGHT",
  "SAVE",
  "FLEET_SAVE",
  "STATION",
  "TRANSFER",
  "EVENT",
  "PARTICLE",
  "FLEET_FLIGHT",
]);

const SHIPYARD_DETAIL_PREFIXES = [
  "shipyard",
  "weapons factory",
  "defense",
  "defence",
  "in construction",
  "researching",
  "constructing",
];

const COORD_REGEX = /(\d{1,2})\s*:\s*(\d{1,3})\s*:\s*(\d{1,2})/;

const SHIP_NAME_BY_TOKEN: Record<string, string> = (() => {
  const map: Record<string, string> = {};
  for (const item of MQ_SHIPS_CATALOG) {
    map[item.key.toLowerCase()] = item.name;
    map[String(item.id)] = item.name;
    map[item.name.toLowerCase()] = item.name;
    map[item.name.toLowerCase().replace(/[^a-z0-9]+/g, "_")] = item.name;
  }
  return map;
})();

export function normalizeCoordKey(raw?: string | null): string {
  const s = String(raw || "").trim();
  if (!s) return "";
  const m = COORD_REGEX.exec(s);
  if (!m) return "";
  return `${Number(m[1])}:${Number(m[2])}:${Number(m[3])}`;
}

export function extractCoordsFromText(raw?: string | null): string {
  const s = String(raw || "").trim();
  if (!s) return "";
  const m = COORD_REGEX.exec(s);
  if (!m) return "";
  const g = m[1].padStart(2, "0");
  const sys = m[2].padStart(3, "0");
  const pos = m[3].padStart(2, "0");
  return `${g}:${sys}:${pos}`;
}

export function extractCustomPlanetNames(
  dashboard: DashboardPayload | null | undefined
): Record<string, string> {
  const merged: Record<string, string> = {};
  const stateMap = (dashboard?.state as Record<string, any> | undefined)?.custom_planet_names;
  const topMap = (dashboard as Record<string, any> | undefined)?.custom_planet_names;
  for (const src of [stateMap, topMap]) {
    if (src && typeof src === "object") {
      for (const [k, v] of Object.entries(src)) {
        const cleaned = String(v ?? "").trim();
        if (cleaned) {
          merged[String(k)] = cleaned;
        }
      }
    }
  }
  return merged;
}

export function resolvePlanetDisplayName(
  planet: PlanetData,
  customNames: Record<string, string>
): string {
  const pidKey = String(planet.planet_id);
  const custom = customNames[pidKey];
  if (custom && custom.trim()) return custom.trim();
  const rawName = String(planet.name || "").trim();
  if (rawName) return rawName;
  return `Colony ${planet.planet_id}`;
}

export function buildPlanetLookup(
  planets: PlanetData[] | null | undefined,
  customNames: Record<string, string>
): PlanetLookup {
  const byId = new Map<number, PlanetLookupEntry>();
  const byNormCoords = new Map<string, PlanetLookupEntry>();
  const ordered: PlanetLookupEntry[] = [];
  if (!Array.isArray(planets)) {
    return { byId, byNormCoords, ordered };
  }
  for (const planet of planets) {
    if (!planet || typeof planet !== "object") continue;
    const pid = Number(planet.planet_id);
    const displayName = resolvePlanetDisplayName(planet, customNames);
    const rawCoords = String(planet.coords || "").trim();
    const normCoords = normalizeCoordKey(rawCoords);
    const entry: PlanetLookupEntry = {
      planet,
      displayName,
      rawCoords,
      normCoords,
    };
    ordered.push(entry);
    if (Number.isFinite(pid) && pid > 0) {
      byId.set(pid, entry);
    }
    if (normCoords) {
      byNormCoords.set(normCoords, entry);
    }
  }
  return { byId, byNormCoords, ordered };
}

export function normalizeResourceBundle(
  raw: ResourceBundle | Record<string, unknown> | null | undefined
): NormalizedBundle | null {
  if (!raw || typeof raw !== "object") return null;
  const src = raw as Record<string, unknown>;
  const pig_iron = Math.max(0, Math.round(finiteNum(src.pig_iron, 0)));
  const metal = Math.max(0, Math.round(finiteNum(src.metal, 0)));
  const kryptonite = Math.max(0, Math.round(finiteNum(src.kryptonite, 0)));
  const spice = Math.max(0, Math.round(finiteNum(src.spice, 0)));
  const total = pig_iron + metal + kryptonite + spice;
  const nonZeroCount = [pig_iron, metal, kryptonite, spice].filter((v) => v > 0).length;
  return {
    bundle: { pig_iron, metal, kryptonite, spice },
    total,
    nonZeroCount,
  };
}

function matchMissionToken(raw?: string | null): FlightMission | null {
  const s = String(raw || "").trim();
  if (!s) return null;
  if (/\b(attack|raid|hostile)\b/i.test(s)) return "ATTACK";
  if (/\b(recycle|recycler|harvest|debris)\b/i.test(s)) return "RECYCLE";
  if (/\b(spionage|espionage|spy)\b/i.test(s)) return "SPY";
  if (/\b(escape|fleetsave|fleet[\s_-]?save|save)\b/i.test(s)) return "ESCAPE";
  if (/\bcoloniz\w*\b/i.test(s)) return "COLONIZE";
  if (/\b(station|deploy)\b/i.test(s)) return "STATION";
  if (/\btransfer\b/i.test(s)) return "TRANSFER";
  if (/\btransport\b/i.test(s)) return "TRANSPORT";
  return null;
}

export function isOngoingFlightJob(job: FlightQueueJob | null | undefined): boolean {
  if (!job || typeof job !== "object") return false;
  const catLow = String(job.category || "").trim().toLowerCase();
  if (NON_FLIGHT_CATEGORIES.has(catLow)) return false;

  const detailLow = String(job.detail || "").trim().toLowerCase();
  const catUp = catLow.toUpperCase();

  if (catLow === "fleet") {
    for (const prefix of SHIPYARD_DETAIL_PREFIXES) {
      if (detailLow.startsWith(prefix)) {
        return false;
      }
    }
    return true;
  }

  if (FLIGHT_MISSION_CATEGORIES.has(catUp)) {
    return true;
  }

  if (!catLow) {
    if (
      matchMissionToken(job.mission) ||
      matchMissionToken(job.kind) ||
      detailLow.startsWith("outbound") ||
      detailLow.startsWith("returning")
    ) {
      return true;
    }
  }

  return false;
}

export function classifyFlightMission(job: FlightQueueJob): FlightMission {
  const fromMission = matchMissionToken(job.mission);
  if (fromMission) return fromMission;

  const fromKind = matchMissionToken(job.kind);
  if (fromKind) return fromKind;

  const catRaw = String(job.category || "").trim();
  if (catRaw && catRaw.toLowerCase() !== "fleet") {
    const fromCat = matchMissionToken(catRaw);
    if (fromCat) return fromCat;
  }

  const titleCandidates = [String(job.name || ""), String(job.label || "")].filter(Boolean);
  for (const title of titleCandidates) {
    const prefix = title.split(/→|->|↩|←|<-/)[0] || "";
    const fromPrefix = matchMissionToken(prefix);
    if (fromPrefix) return fromPrefix;
  }
  for (const title of titleCandidates) {
    const fromTitle = matchMissionToken(title);
    if (fromTitle) return fromTitle;
  }

  // Only inspect detail BEFORE "·" so ship compositions ("Small transport") never misclassify Attacks.
  const detailPrefix = String(job.detail || "").split("·")[0] || "";
  const fromDetail = matchMissionToken(detailPrefix);
  if (fromDetail) return fromDetail;

  return "OTHER";
}

export function classifyFlightDirection(job: FlightQueueJob): FlightDirection {
  if (job.is_returning === true || job.returning === true) return "returning";
  if (job.is_returning === false || job.returning === false) {
    const dir = String(job.direction || "").trim().toLowerCase();
    if (dir === "returning" || dir === "return" || dir === "inbound") return "returning";
    return "outbound";
  }

  const dirStr = String(job.direction || "").trim().toLowerCase();
  if (
    dirStr === "returning" ||
    dirStr === "return" ||
    dirStr === "inbound" ||
    dirStr === "back"
  ) {
    return "returning";
  }
  if (dirStr === "outbound" || dirStr === "outgoing") {
    return "outbound";
  }

  if (job.delivered === true) return "returning";

  const detailLow = String(job.detail || "").toLowerCase();
  if (
    detailLow.includes("returning") ||
    detailLow.includes("return flight") ||
    detailLow.includes("↩")
  ) {
    return "returning";
  }

  const title = `${String(job.name || "")} ${String(job.label || "")}`;
  if (title.includes("↩") || title.includes("←") || title.includes("<-") || /\breturning\b/i.test(title)) {
    return "returning";
  }

  return "outbound";
}

function missionMeta(mission: FlightMission): {
  filter: Exclude<FlightMissionFilter, "all">;
  label: string;
  badgeClass: string;
} {
  switch (mission) {
    case "ATTACK":
      return { filter: "attack", label: "Attack", badgeClass: "outcome-badge is-loss" };
    case "RECYCLE":
      return { filter: "recycle", label: "Recycle", badgeClass: "outcome-badge is-win" };
    case "TRANSPORT":
      return { filter: "transport", label: "Transport", badgeClass: "outcome-badge is-pending" };
    case "SPY":
      return { filter: "spy", label: "Spy", badgeClass: "outcome-badge is-draw" };
    case "COLONIZE":
      return { filter: "other", label: "Colonize", badgeClass: "grade-badge grade-aplus" };
    case "ESCAPE":
      return { filter: "other", label: "Escape", badgeClass: "outcome-badge is-draw" };
    case "STATION":
      return { filter: "other", label: "Station", badgeClass: "outcome-badge" };
    case "TRANSFER":
      return { filter: "other", label: "Transfer", badgeClass: "outcome-badge" };
    default:
      return { filter: "other", label: "Fleet", badgeClass: "outcome-badge" };
  }
}

export function formatShipComposition(
  ships: FlightQueueJob["ships"],
  detail?: string | null
): string {
  if (typeof ships === "string" && ships.trim()) {
    return ships.trim();
  }
  if (ships && typeof ships === "object" && !Array.isArray(ships)) {
    const parts: string[] = [];
    for (const [k, v] of Object.entries(ships)) {
      const qty = Math.round(finiteNum(v, 0));
      if (qty <= 0) continue;
      const keyLow = String(k).trim().toLowerCase();
      const pretty =
        SHIP_NAME_BY_TOKEN[keyLow] ||
        SHIP_NAME_BY_TOKEN[keyLow.replace(/[^a-z0-9]+/g, "_")] ||
        String(k).replace(/_/g, " ");
      parts.push(`${fmtInt(qty)} ${pretty}`);
    }
    if (parts.length > 0) {
      return parts.join(", ");
    }
  }

  const rawDetail = String(detail || "").trim();
  if (!rawDetail) return "";
  const stripped = rawDetail
    .replace(/^(?:outbound|returning)\s*(?:·|-|\|)?\s*/i, "")
    .trim();
  return stripped;
}

export function resolveFlightCargo(
  job: FlightQueueJob,
  mission: FlightMission,
  direction: FlightDirection
): FlightCargoDisplay | null {
  const expectedNorm = normalizeResourceBundle(job.expected_cargo);
  const deliveredNorm = normalizeResourceBundle(job.delivered_cargo);
  const cargoNorm = normalizeResourceBundle(job.cargo);

  // Returning TRANSPORT (or explicit delivered === true): DELIVERED flag + delivered_cargo (clean badge even if null)
  if (job.delivered === true || (direction === "returning" && mission === "TRANSPORT")) {
    if (deliveredNorm && deliveredNorm.total > 0) {
      return {
        kind: "DELIVERED",
        bundle: deliveredNorm.bundle,
        total: deliveredNorm.total,
        inline: formatResourcesInline(deliveredNorm.bundle),
        multiRes: deliveredNorm.nonZeroCount > 1,
        isEmpty: false,
      };
    }
    return {
      kind: "DELIVERED",
      bundle: null,
      total: 0,
      inline: "",
      multiRes: false,
      isEmpty: false,
    };
  }

  // Outbound ATTACK, RECYCLE, TRANSPORT: EXPECTED flag from expected_cargo (fallback to cargo for outbound transport)
  if (
    direction === "outbound" &&
    (mission === "ATTACK" || mission === "RECYCLE" || mission === "TRANSPORT")
  ) {
    if (expectedNorm && expectedNorm.total > 0) {
      return {
        kind: "EXPECTED",
        bundle: expectedNorm.bundle,
        total: expectedNorm.total,
        inline: formatResourcesInline(expectedNorm.bundle),
        multiRes: expectedNorm.nonZeroCount > 1,
        isEmpty: false,
      };
    }
    if (mission === "TRANSPORT" && cargoNorm && cargoNorm.total > 0) {
      return {
        kind: "EXPECTED",
        bundle: cargoNorm.bundle,
        total: cargoNorm.total,
        inline: formatResourcesInline(cargoNorm.bundle),
        multiRes: cargoNorm.nonZeroCount > 1,
        isEmpty: false,
      };
    }
    if (cargoNorm && cargoNorm.total > 0) {
      return {
        kind: "CARGO",
        bundle: cargoNorm.bundle,
        total: cargoNorm.total,
        inline: formatResourcesInline(cargoNorm.bundle),
        multiRes: cargoNorm.nonZeroCount > 1,
        isEmpty: false,
      };
    }
    return null;
  }

  // Returning ATTACK / RECYCLE: Loot / Harvest from job.cargo
  if (direction === "returning" && (mission === "ATTACK" || mission === "RECYCLE")) {
    const kind: FlightCargoKind = mission === "ATTACK" ? "LOOT" : "HARVEST";
    if (cargoNorm && cargoNorm.total > 0) {
      return {
        kind,
        bundle: cargoNorm.bundle,
        total: cargoNorm.total,
        inline: formatResourcesInline(cargoNorm.bundle),
        multiRes: cargoNorm.nonZeroCount > 1,
        isEmpty: false,
      };
    }
    if (job.cargo != null && typeof job.cargo === "object") {
      return {
        kind,
        bundle: { pig_iron: 0, metal: 0, kryptonite: 0, spice: 0 },
        total: 0,
        inline: "0 (empty)",
        multiRes: false,
        isEmpty: true,
      };
    }
    return null;
  }

  // Other missions (Escape, Station, Transfer, Colonize, Spy, Other)
  if (direction === "outbound" && expectedNorm && expectedNorm.total > 0) {
    return {
      kind: "EXPECTED",
      bundle: expectedNorm.bundle,
      total: expectedNorm.total,
      inline: formatResourcesInline(expectedNorm.bundle),
      multiRes: expectedNorm.nonZeroCount > 1,
      isEmpty: false,
    };
  }
  if (cargoNorm && cargoNorm.total > 0) {
    return {
      kind: "CARGO",
      bundle: cargoNorm.bundle,
      total: cargoNorm.total,
      inline: formatResourcesInline(cargoNorm.bundle),
      multiRes: cargoNorm.nonZeroCount > 1,
      isEmpty: false,
    };
  }
  return null;
}

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
    originEntry?.displayName || String(job.origin_name || "").trim() || (originCoords ? originCoords : "—");
  const originPlanetId = originEntry ? originEntry.planet.planet_id : jobPid !== null && jobPid > 0 ? Math.trunc(jobPid) : null;
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
