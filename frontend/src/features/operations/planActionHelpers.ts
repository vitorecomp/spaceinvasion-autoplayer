import {
  durationToSeconds,
  prioritySortKey,
  resourcesTotal,
  type SortColumnMap,
} from "../../hooks/useTableSort";
import type { PlanetData, PlannedActionData, QueueJobData } from "../../types/api";
import { finiteNum } from "../../utils/formatters";

export type PlanActionSortKey =
  | "priority"
  | "action"
  | "costMissing"
  | "waitEta"
  | "totalEta"
  | "reason";

export const PLAN_ACTION_SORT_COLUMNS: SortColumnMap<PlannedActionData, PlanActionSortKey> = {
  priority: (act) => prioritySortKey(act.priority),
  action: (act) =>
    `${act.target_name || act.kind || ""}${act.target_level != null ? ` ${act.target_level}` : ""}`,
  costMissing: (act) => resourcesTotal(act.cost) ?? resourcesTotal(act.missing),
  waitEta: (act) => {
    const rawWait =
      act.wait_eta !== undefined && act.wait_eta !== null ? String(act.wait_eta).trim() : "";
    if (rawWait === "—" || act.wait_hours === null) return null;
    const wh = finiteNum(act.wait_hours, null);
    if (wh !== null && (wh > 0 || act.executable_now || rawWait !== "")) {
      return wh * 3600;
    }
    if (rawWait !== "") return durationToSeconds(rawWait);
    return act.executable_now ? 0 : null;
  },
  totalEta: (act) => {
    const rawWait =
      act.wait_eta !== undefined && act.wait_eta !== null ? String(act.wait_eta).trim() : "";
    const rawTotal =
      act.total_eta !== undefined && act.total_eta !== null ? String(act.total_eta).trim() : "";
    if (
      rawWait === "—" ||
      rawTotal === "—" ||
      act.wait_hours === null ||
      act.total_hours === null
    ) {
      return null;
    }
    const ts = finiteNum(act.total_seconds, null);
    if (ts !== null) return ts;
    const th = finiteNum(act.total_hours, null);
    if (th !== null) return th * 3600;
    if (rawTotal !== "") return durationToSeconds(rawTotal);
    if (act.duration_text) return durationToSeconds(act.duration_text);
    return act.executable_now ? 0 : null;
  },
  reason: (act) => String(act.reason || "").trim() || null,
};

export const MQ_BUILDINGS_CATALOG = [
  { id: 1, name: "Pig iron mine" },
  { id: 2, name: "Blast furnace" },
  { id: 3, name: "Kryptonite mine" },
  { id: 4, name: "Spice mine" },
  { id: 5, name: "Fusion plant" },
  { id: 6, name: "Thermosolar plant" },
  { id: 7, name: "Development center" },
  { id: 8, name: "Research lab" },
  { id: 9, name: "Weapons factory" },
  { id: 10, name: "Fleet base" },
  { id: 11, name: "Pig iron warehouse" },
  { id: 12, name: "Metal warehouse" },
  { id: 13, name: "Kryptonite warehouse" },
  { id: 14, name: "Spice warehouse" },
  { id: 15, name: "Particle cannon" },
  { id: 16, name: "Particle shield" },
  { id: 17, name: "Microsystem accelerator" },
  { id: 18, name: "Teleporter" },
  { id: 19, name: "Galaxy scanner" },
  { id: 20, name: "Tunnel system" },
  { id: 21, name: "Quantum Array" },
] as const;

export const MQ_RESEARCH_CATALOG = [
  { id: 1, name: "Mining technology" },
  { id: 2, name: "Energy technology" },
  { id: 3, name: "Engine research" },
  { id: 4, name: "Rocket engine" },
  { id: 5, name: "Diffusion engine" },
  { id: 6, name: "Warp engine" },
  { id: 7, name: "Spy technology" },
  { id: 8, name: "Weapons technology" },
  { id: 9, name: "Shield technology" },
  { id: 10, name: "Defense systems" },
  { id: 11, name: "Targeting systems" },
  { id: 12, name: "Particle analyzer" },
  { id: 13, name: "Graviton technology" },
  { id: 14, name: "Teleportation" },
  { id: 15, name: "Black hole research" },
] as const;

export const MQ_SHIPS_CATALOG = [
  { id: 1, name: "Small transport", key: "s1" },
  { id: 2, name: "Large transport", key: "s2" },
  { id: 3, name: "Transmitter", key: "s3" },
  { id: 4, name: "Small fighter", key: "s4" },
  { id: 5, name: "Warship", key: "s5" },
  { id: 6, name: "Starcruiser", key: "s6" },
  { id: 7, name: "Phoenix", key: "s7" },
  { id: 8, name: "Colony ship", key: "s8" },
  { id: 9, name: "Solar cells", key: "s9" },
  { id: 10, name: "Spy probe", key: "s10" },
  { id: 11, name: "Recycler", key: "s11" },
  { id: 13, name: "Battleship", key: "s13" },
  { id: 14, name: "Stealth bomber", key: "s14" },
  { id: 15, name: "Destroyer", key: "s15" },
  { id: 16, name: "Imperial starbase", key: "s16" },
] as const;

export const DASH_FLEET_KINDS = new Set([
  "BUILD_SHIP",
  "SHIP",
  "BUILD_DEFENSE",
  "DEFENSE",
  "SPY",
  "TRANSPORT",
  "STATION",
  "ATTACK",
  "RECYCLE",
  "COLONIZE",
  "SCAN_GALAXY",
  "ESCAPE_FLIGHT",
  "ALERT_HOSTILE",
]);

export function dashboardQueueDomainForAction(
  act: PlannedActionData | null | undefined
): "building" | "fleet" | "research" {
  const kind = String(act?.kind || "").toUpperCase();
  if (kind === "RESEARCH") return "research";
  if (DASH_FLEET_KINDS.has(kind)) return "fleet";
  return "building";
}

export function dashboardQueueDomainForJob(
  job: QueueJobData | null | undefined
): "building" | "fleet" | "research" {
  const cat = String(job?.category || "").toLowerCase();
  if (cat === "research") return "research";
  if (cat === "fleet" || cat === "ship" || cat === "shipyard" || cat === "defense") return "fleet";
  return "building";
}

export function normalizeCoordKey(raw?: string | null): string {
  const s = String(raw || "").trim();
  if (!s) return "";
  const m = s.match(/(\d{1,2})\s*:\s*(\d{1,3})\s*:\s*(\d{1,2})/);
  if (!m) return s;
  return `${Number(m[1])}:${Number(m[2])}:${Number(m[3])}`;
}

export function queueJobMatchesPlanet(
  job: QueueJobData,
  planet: PlanetData,
  activePlanetId: number | null | undefined,
  fallbackToActivePlanet: boolean
): boolean {
  const jPid = job.planet_id != null ? Number(job.planet_id) : NaN;
  if (Number.isFinite(jPid) && jPid > 0) {
    return jPid === Number(planet.planet_id);
  }
  const jCoords = normalizeCoordKey(job.origin_coords || job.planet_coords);
  if (jCoords) {
    const pCoords = normalizeCoordKey(planet.coords);
    return Boolean(pCoords) && jCoords === pCoords;
  }
  if (fallbackToActivePlanet && activePlanetId != null) {
    return Number(planet.planet_id) === Number(activePlanetId);
  }
  return false;
}

export function isActiveBuildingJob(
  job: QueueJobData | null | undefined,
  nowEpochSec?: number | null
): boolean {
  if (!job) return false;
  if (String(job.category || "").toLowerCase() !== "building") return false;
  const finTs = finiteNum(job.finish_ts, null);
  const remSec = finiteNum(job.remaining_seconds, null);
  const effNow = nowEpochSec != null && nowEpochSec > 0 ? nowEpochSec : null;
  if (finTs !== null && finTs > 0 && effNow !== null) {
    return finTs > effNow;
  }
  if (remSec !== null) {
    return remSec > 0;
  }
  return true;
}

export function isPlanetConstructing(
  planet: PlanetData,
  activeQueues: QueueJobData[] | null | undefined,
  activePlanetId: number | null | undefined,
  nowEpochSec?: number | null
): boolean {
  if (!Array.isArray(activeQueues) || activeQueues.length === 0) return false;
  return activeQueues.some(
    (job) =>
      isActiveBuildingJob(job, nowEpochSec) &&
      queueJobMatchesPlanet(job, planet, activePlanetId, true)
  );
}

const FLIGHT_MISSION_RE =
  /\b(attack|recycle|transport|transfer|spy|espionage|colonize|escape|save|station|expedition|flight)\b/i;

const NON_FLIGHT_DETAIL_PREFIXES = [
  "shipyard",
  "weapons factory",
  "defense",
  "defence",
  "in construction",
  "researching",
  "constructing",
];

export function isOngoingFleetFlight(job: QueueJobData | null | undefined): boolean {
  if (!job) return false;
  const cat = String(job.category || "").toLowerCase();
  const detailLow = String(job.detail || "").trim().toLowerCase();
  if (NON_FLIGHT_DETAIL_PREFIXES.some((prefix) => detailLow.startsWith(prefix))) {
    return false;
  }
  if (cat === "fleet") return true;
  if (
    cat === "building" ||
    cat === "research" ||
    cat === "ship" ||
    cat === "shipyard" ||
    cat === "defense" ||
    cat === "milestone"
  ) {
    return false;
  }
  return FLIGHT_MISSION_RE.test(`${String(job.name || "")} ${String(job.detail || "")}`);
}

export function cleanQueueJobTitle(job: QueueJobData | null | undefined): string {
  const rawName = String(job?.name || "Active job")
    .replace(/^(?:cancel|abort)\s+/i, "")
    .replace(/\s*\(\s*Level\s+\d+\s*\)\s*$/i, "")
    .trim();
  const lvl = finiteNum(job?.target_level, null);
  if (lvl !== null && lvl > 0) {
    return `${rawName} → L${lvl}`;
  }
  return rawName || "Active job";
}

export function execJobBaseKey(job: QueueJobData | null | undefined): string {
  const cat = String(job?.category || "job").toLowerCase();
  const pid =
    job?.planet_id != null && Number.isFinite(Number(job.planet_id)) && Number(job.planet_id) > 0
      ? `p${Math.trunc(Number(job.planet_id))}`
      : "";
  const coords = normalizeCoordKey(job?.origin_coords || job?.planet_coords);
  const originKey = pid || coords || "active";
  const slot = Math.max(1, Math.trunc(finiteNum(job?.queue_slot, 1)));
  const bid =
    job?.building_id != null && Number.isFinite(Number(job.building_id))
      ? `b${Math.trunc(Number(job.building_id))}`
      : "";
  const name = String(job?.name || "job").trim();
  const lvl = job?.target_level != null ? `L${job.target_level}` : "";
  if (cat === "building" || cat === "research") {
    return `exec-${cat}-${originKey}-s${slot}-${bid || name}-${lvl}`;
  }
  const det = String(job?.detail || "").trim();
  return `exec-${cat}-${originKey}-s${slot}-${name}-${lvl}-${det}`;
}

export function actionPayloadFor(
  action: PlannedActionData | null | undefined,
  dryRun: boolean
): Record<string, any> | null {
  if (!action) return null;
  if (action.kind === "CLAIM_TUTORIAL") {
    return { command: "claim_tutorial", dry_run: dryRun };
  }
  if (action.kind === "BUILD" && action.target_id != null) {
    return {
      command: "build",
      building_id: action.target_id,
      planet_id: action.planet_id,
      dry_run: dryRun,
    };
  }
  if (action.kind === "DEMOLISH_BUILDING" && action.target_id != null) {
    return {
      command: "demolish_building",
      building_id: action.target_id,
      planet_id: action.planet_id,
      dry_run: dryRun,
    };
  }
  if (action.kind === "RESEARCH" && action.target_id != null) {
    return {
      command: "research",
      research_id: action.target_id,
      planet_id: action.planet_id,
      dry_run: dryRun,
    };
  }
  if (action.kind === "BUILD_SHIP" && action.target_id != null) {
    return {
      command: "build_ship",
      ship_id: action.target_id,
      quantity: Math.max(1, Math.round(finiteNum(action.target_level, 1))),
      planet_id: action.planet_id,
      dry_run: dryRun,
    };
  }
  if (action.kind === "THROTTLE_BUILDING" && action.target_id != null) {
    return {
      command: "throttle_building",
      building_id: action.target_id,
      percentage: action.target_level == null ? 0 : finiteNum(action.target_level, 0),
      planet_id: action.planet_id,
      dry_run: dryRun,
    };
  }
  if (action.kind === "ESCAPE_FLIGHT") {
    return { command: "fleetsave_plan", planet_id: action.planet_id };
  }
  if (action.kind === "SCAN_GALAXY") {
    const m = /(\d{1,2}):(\d{1,3})/.exec(action.target_name || action.planet_coords || "");
    return {
      command: "scan_galaxy",
      galaxy: m ? Number(m[1]) : undefined,
      system: m ? Number(m[2]) : undefined,
      planet_id: action.planet_id,
      dry_run: dryRun,
    };
  }
  if (action.kind === "ATTACK") {
    const m = /(\d{1,2}:\d{1,3}:\d{1,2})/.exec(action.target_name || "");
    return {
      command: "attack",
      target_coords: m ? m[1] : action.target_name,
      planet_id: action.planet_id,
      dry_run: dryRun,
    };
  }
  if (action.kind === "SPY") {
    const m = /(\d{1,2}:\d{1,3}:\d{1,2})/.exec(action.target_name || "");
    return {
      command: "spy",
      target_coords: m ? m[1] : action.target_name,
      quantity: Math.max(1, Math.round(finiteNum(action.target_level, 1))),
      planet_id: action.planet_id,
      dry_run: dryRun,
    };
  }
  if (action.kind === "RECYCLE") {
    const m = /(\d{1,2}:\d{1,3}:\d{1,2})/.exec(action.target_name || "");
    return {
      command: "recycle",
      target_coords: m ? m[1] : action.target_name,
      quantity: Math.max(1, Math.round(finiteNum(action.target_level, 1))),
      planet_id: action.planet_id,
      dry_run: dryRun,
    };
  }
  if (action.kind === "TRANSPORT") {
    const m = /(\d{1,2}:\d{1,3}:\d{1,2})/.exec(action.target_name || "");
    const c = action.cost || {};
    return {
      command: "transport",
      target_coords: m ? m[1] : action.target_name,
      dest_planet_id: action.target_id ?? undefined,
      planet_id: action.planet_id,
      resources: {
        pig_iron: Math.max(0, Math.round(finiteNum((c as any).pig_iron, 0))),
        metal: Math.max(0, Math.round(finiteNum((c as any).metal, 0))),
        kryptonite: Math.max(0, Math.round(finiteNum((c as any).kryptonite, 0))),
        spice: Math.max(0, Math.round(finiteNum((c as any).spice, 0))),
      },
      dry_run: dryRun,
    };
  }
  if (action.kind === "STATION") {
    const m = /(\d{1,2}:\d{1,3}:\d{1,2})/.exec(action.target_name || "");
    return {
      command: "station",
      target_coords: m ? m[1] : action.target_name,
      dest_planet_id: action.target_id ?? undefined,
      planet_id: action.planet_id,
      ships_spec: action.target_name || action.reason || "",
      dry_run: dryRun,
    };
  }
  if (action.kind === "COLONIZE") {
    const m = /(\d{1,2}:\d{1,3}:\d{1,2})/.exec(action.target_name || "");
    return {
      command: "colonize",
      target_coords: m ? m[1] : action.target_name,
      planet_id: action.planet_id,
      dry_run: dryRun,
    };
  }
  return null;
}

export function buildPinPayloadFromAction(
  act: PlannedActionData,
  priority = "P1"
): Record<string, any> {
  const normKind = act.kind === "WAIT_RESOURCES" ? "BUILD" : String(act.kind || "").toUpperCase();
  const mCoords = /(\d{1,2}:\d{1,3}(?::\d{1,2})?)/.exec(act.target_name || "");
  return {
    kind: normKind,
    priority,
    position: "top",
    planet_id: act.planet_id || 0,
    target_id: act.target_id ?? undefined,
    target_name: act.target_name || "",
    target_coords: mCoords ? mCoords[1] : undefined,
    target_level: act.target_level ?? undefined,
    deduplicate_pending: true,
  };
}

export function formatPlanetTag(
  planetName?: string | null,
  coords?: string | null
): string {
  const cleanName = String(planetName || "").trim();
  const cleanCoords = String(coords || "").trim();
  if (cleanName && cleanName.toLowerCase() !== "colony" && cleanCoords) {
    return `${cleanName} [${cleanCoords}]`;
  }
  if (cleanCoords) {
    return `[${cleanCoords}]`;
  }
  if (cleanName) {
    return cleanName;
  }
  return "[—]";
}

export function buildPlanetNameMaps(
  planets?: PlanetData[] | null,
  customPlanetNames?: Record<string, string> | null
): {
  byId: Map<number, string>;
  byCoords: Map<string, string>;
} {
  const byId = new Map<number, string>();
  const byCoords = new Map<string, string>();
  if (customPlanetNames && typeof customPlanetNames === "object") {
    for (const [rawPid, rawName] of Object.entries(customPlanetNames)) {
      const pid = Math.trunc(Number(rawPid));
      const nm = String(rawName || "").trim();
      if (Number.isFinite(pid) && pid > 0 && nm) {
        byId.set(pid, nm);
      }
    }
  }
  if (Array.isArray(planets)) {
    for (const p of planets) {
      if (!p || typeof p !== "object") continue;
      const pid = Math.trunc(Number(p.planet_id));
      const nm = String(
        (Number.isFinite(pid) && pid > 0 ? byId.get(pid) : "") ||
          p.custom_name ||
          p.name ||
          ""
      ).trim();
      if (Number.isFinite(pid) && pid > 0 && nm) {
        byId.set(pid, nm);
      }
      const ck = normalizeCoordKey(p.coords);
      if (ck && nm) {
        byCoords.set(ck, nm);
      }
    }
  }
  return { byId, byCoords };
}
