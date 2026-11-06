import type { DashboardPayload, PlanetData, ResourceBundle } from "../../types/api";
import { finiteNum, fmtInt, formatResourcesInline } from "../../utils/formatters";
import {
  COORD_REGEX,
  FLIGHT_MISSION_CATEGORIES,
  NON_FLIGHT_CATEGORIES,
  SHIPYARD_DETAIL_PREFIXES,
  SHIP_NAME_BY_TOKEN,
  type FlightCargoDisplay,
  type FlightCargoKind,
  type FlightDirection,
  type FlightMission,
  type FlightMissionFilter,
  type FlightQueueJob,
  type NormalizedBundle,
  type PlanetLookup,
  type PlanetLookupEntry,
} from "./flightTypes";

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
  if (
    title.includes("↩") ||
    title.includes("←") ||
    title.includes("<-") ||
    /\breturning\b/i.test(title)
  ) {
    return "returning";
  }

  return "outbound";
}

export function missionMeta(mission: FlightMission): {
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
