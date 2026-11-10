import { safeFiniteNumber } from "../../api/httpClient";
import type {
  DashboardPayload,
  FleetPlannerGetPayload,
  PlannedActionData,
  PlanetData,
  QueueJobData,
  StrategyConfigData,
} from "../../types/api";
import { fmtHms, fmtInt, formatResourcesInline } from "../../utils/formatters";
import { FLEET_RESEARCH_OPTIONS } from "./paramDocs";
import { SHIP_OPTIONS } from "./plannerHelpers";

const DEFAULT_SHIPYARD_SLUGS: readonly string[] = [
  "small-fighter",
  "small-transport",
  "large-transport",
  "spy-probe",
  "colony-ship",
  "recycler",
  "starcruiser",
  "phoenix",
  "transmitter",
  "battleship",
  "stealth-bomber",
  "destroyer",
];

const FLEET_BOOL_FIELDS: ReadonlyArray<{ key: string; def: boolean }> = [
  { key: "allow_raids", def: false },
  { key: "allow_pvp_in_az", def: false },
  { key: "allow_fleetsave", def: false },
  { key: "allow_colonize", def: false },
  { key: "allow_transport", def: false },
  { key: "allow_transmitter", def: false },
  { key: "allow_galaxy_scan", def: false },
  { key: "galaxy_scan_block_other_actions", def: true },
  { key: "galaxy_scan_explore_first", def: true },
  { key: "galaxy_scan_all_galaxies", def: true },
  { key: "spy_reserved_slots_only", def: false },
  { key: "allow_shipyard", def: true },
  { key: "allow_fleet_research", def: true },
];

const FLEET_NUM_DEFAULTS: Record<string, number> = {
  combat_safety_margin: 1.4,
  raid_min_loot_spice_eq: 1500,
  attack_min_res_per_hour: 0,
  recycle_min_res_per_hour: 0,
  fleet_save_fuel_reserve_spice: 500,
  ai_resource_spare_pct: 10,
  min_colony_fields: 300,
  max_planets: 11,
  galaxy_sweep_interval_seconds: 270,
  galaxy_sweep_pages: 100,
  galaxy_scan_click_delay_seconds: 1.5,
  galaxy_scan_click_jitter_seconds: 0.6,
  galaxy_scan_refresh_pct: 20,
  galaxy_scan_near_weight: 1.0,
  galaxy_scan_attack_weight: 1.0,
  reserved_spy_slots: 1,
  spy_dispatch_planet_id: 0,
};

export interface FleetDraftState {
  booleans: Record<string, boolean>;
  numbers: Record<string, string>;
  shipyard_targets: Record<string, string>;
  fleet_research_targets: Record<string, string>;
}

export interface FleetSlotBudgetRow {
  planet_id: number;
  planet_coords: string;
  planet_name: string;
  b10_level: number;
  slots_used: number;
  slots_max: number;
  active_flights: number;
  docked_ships_total: number;
  docked_summary: string;
  planned_actions: number;
  status: string;
}

export interface OngoingFlightCargoInfo {
  tag: "EXPECTED" | "DELIVERED" | "Loot" | "Harvest" | "Cargo";
  variant: "expected" | "delivered" | "loot" | "harvest" | "cargo";
  inline: string;
  total: number;
  multiRes: boolean;
  isEmpty: boolean;
}

export interface OngoingFlightViewModel {
  key: string;
  title: string;
  detail: string;
  originLabel: string;
  returning: boolean;
  cargoInfo: OngoingFlightCargoInfo | null;
  etaText: string;
}

export function buildFleetDraftFromConfig(
  cfg: StrategyConfigData | null | undefined
): FleetDraftState {
  const booleans: Record<string, boolean> = {};
  for (const spec of FLEET_BOOL_FIELDS) {
    booleans[spec.key] = cfg && spec.key in cfg ? Boolean(cfg[spec.key]) : spec.def;
  }

  const numbers: Record<string, string> = {};
  for (const [key, fallback] of Object.entries(FLEET_NUM_DEFAULTS)) {
    const val = safeFiniteNumber(cfg?.[key], fallback) ?? fallback;
    numbers[key] = String(val);
  }

  const rawTargets =
    cfg?.shipyard_targets && typeof cfg.shipyard_targets === "object"
      ? cfg.shipyard_targets
      : {};
  const shipyard_targets: Record<string, string> = {};
  for (const slug of DEFAULT_SHIPYARD_SLUGS) {
    shipyard_targets[slug] = String(
      Math.max(0, Math.round(safeFiniteNumber(rawTargets[slug], 0) ?? 0))
    );
  }
  for (const [slug, val] of Object.entries(rawTargets)) {
    if (!(slug in shipyard_targets)) {
      shipyard_targets[slug] = String(Math.max(0, Math.round(safeFiniteNumber(val, 0) ?? 0)));
    }
  }

  const rawFrTargets =
    cfg?.fleet_research_targets && typeof cfg.fleet_research_targets === "object"
      ? cfg.fleet_research_targets
      : {};
  const fleet_research_targets: Record<string, string> = {};
  for (const opt of FLEET_RESEARCH_OPTIONS) {
    fleet_research_targets[opt.key] = String(
      Math.max(0, Math.min(100, Math.round(safeFiniteNumber(rawFrTargets[opt.key], 0) ?? 0)))
    );
  }

  return { booleans, numbers, shipyard_targets, fleet_research_targets };
}

export function buildConfigPatchFromDraft(draft: FleetDraftState): Record<string, any> {
  const num = (key: string, min = 0, max = Number.MAX_SAFE_INTEGER) => {
    const fb = FLEET_NUM_DEFAULTS[key] ?? 0;
    const raw = safeFiniteNumber(draft.numbers[key], fb) ?? fb;
    return Math.max(min, Math.min(max, raw));
  };
  const intNum = (key: string, min = 0, max = Number.MAX_SAFE_INTEGER) =>
    Math.round(num(key, min, max));

  const shipyardTargets: Record<string, number> = {};
  for (const [slug, val] of Object.entries(draft.shipyard_targets)) {
    shipyardTargets[slug] = Math.max(0, Math.round(safeFiniteNumber(val, 0) ?? 0));
  }

  const fleetResearchTargets: Record<string, number> = {};
  for (const [rk, val] of Object.entries(draft.fleet_research_targets)) {
    fleetResearchTargets[rk] = Math.max(0, Math.min(100, Math.round(safeFiniteNumber(val, 0) ?? 0)));
  }

  return {
    allow_raids: Boolean(draft.booleans.allow_raids),
    allow_pvp_in_az: Boolean(draft.booleans.allow_pvp_in_az),
    allow_fleetsave: Boolean(draft.booleans.allow_fleetsave),
    combat_safety_margin: num("combat_safety_margin", 1.0, 10.0),
    raid_min_loot_spice_eq: Math.round(num("raid_min_loot_spice_eq", 0, 1e9)),
    attack_min_res_per_hour: num("attack_min_res_per_hour", 0, 1e12),
    recycle_min_res_per_hour: num("recycle_min_res_per_hour", 0, 1e12),
    fleet_save_fuel_reserve_spice: Math.round(num("fleet_save_fuel_reserve_spice", 0, 1e9)),
    ai_resource_spare_pct: num("ai_resource_spare_pct", 0, 100),
    allow_colonize: Boolean(draft.booleans.allow_colonize),
    min_colony_fields: intNum("min_colony_fields", 50, 500),
    max_planets: intNum("max_planets", 1, 20),
    allow_transport: Boolean(draft.booleans.allow_transport),
    allow_transmitter: Boolean(draft.booleans.allow_transmitter),
    allow_galaxy_scan: Boolean(draft.booleans.allow_galaxy_scan),
    galaxy_scan_block_other_actions: Boolean(draft.booleans.galaxy_scan_block_other_actions),
    galaxy_sweep_interval_seconds: intNum("galaxy_sweep_interval_seconds", 10, 86400),
    galaxy_sweep_pages: intNum("galaxy_sweep_pages", 1, 400),
    galaxy_scan_click_delay_seconds: num("galaxy_scan_click_delay_seconds", 0.1, 30),
    galaxy_scan_click_jitter_seconds: num("galaxy_scan_click_jitter_seconds", 0, 20),
    galaxy_scan_explore_first: Boolean(draft.booleans.galaxy_scan_explore_first),
    galaxy_scan_refresh_pct: intNum("galaxy_scan_refresh_pct", 0, 100),
    galaxy_scan_all_galaxies: Boolean(draft.booleans.galaxy_scan_all_galaxies),
    galaxy_scan_near_weight: num("galaxy_scan_near_weight", 0, 10),
    galaxy_scan_attack_weight: num("galaxy_scan_attack_weight", 0, 10),
    reserved_spy_slots: intNum("reserved_spy_slots", 0, 20),
    spy_reserved_slots_only: Boolean(draft.booleans.spy_reserved_slots_only),
    spy_dispatch_planet_id: intNum("spy_dispatch_planet_id", 0),
    allow_shipyard: Boolean(draft.booleans.allow_shipyard),
    allow_fleet_research: Boolean(draft.booleans.allow_fleet_research),
    shipyard_targets: shipyardTargets,
    fleet_research_targets: fleetResearchTargets,
  };
}

export function summarizeDockedShips(ships: Record<string, any> | undefined): {
  total: number;
  summary: string;
} {
  if (!ships || typeof ships !== "object") return { total: 0, summary: "None" };
  const entries = Object.entries(ships)
    .map(([k, v]) => [k, Math.max(0, Math.round(safeFiniteNumber(v, 0) ?? 0))] as const)
    .filter(([, count]) => count > 0)
    .sort((a, b) => b[1] - a[1]);

  const total = entries.reduce((acc, [, count]) => acc + count, 0);
  if (entries.length === 0) return { total: 0, summary: "None" };

  const labels = entries.slice(0, 3).map(([slug, count]) => {
    const found = SHIP_OPTIONS.find((s) => s.slug === slug || s.key === slug);
    return `${found ? found.name : slug}: ${fmtInt(count)}`;
  });
  return {
    total,
    summary:
      entries.length > 3 ? `${labels.join(" · ")} (+${entries.length - 3})` : labels.join(" · "),
  };
}

export function isFleetActionKind(kindRaw: unknown): boolean {
  const kind = String(kindRaw || "").toUpperCase();
  return (
    kind === "ATTACK" ||
    kind === "RAID" ||
    kind === "RECYCLE" ||
    kind === "SPY" ||
    kind === "GALAXY_SCAN" ||
    kind === "COLONIZE" ||
    kind === "TRANSPORT" ||
    kind === "STATION" ||
    kind === "FLEET_SAVE" ||
    kind === "BUILD_SHIP" ||
    kind === "SHIPS" ||
    kind === "TRANSMITTER"
  );
}

function isFleetPlanAction(act: PlannedActionData): boolean {
  const kind = String(act?.kind || "").toUpperCase();
  if (isFleetActionKind(kind)) return true;
  if (kind === "RESEARCH") {
    const tid = safeFiniteNumber(act?.target_id, 0) ?? 0;
    const reason = String(act?.reason || "").toLowerCase();
    return tid >= 3 || reason.includes("fleet research");
  }
  return false;
}

function extractResourceTotals(raw: unknown) {
  if (!raw || typeof raw !== "object") return null;
  const obj = raw as Record<string, unknown>;
  const pig_iron = Math.max(0, Math.round(safeFiniteNumber(obj.pig_iron, 0) ?? 0));
  const metal = Math.max(0, Math.round(safeFiniteNumber(obj.metal, 0) ?? 0));
  const kryptonite = Math.max(0, Math.round(safeFiniteNumber(obj.kryptonite, 0) ?? 0));
  const spice = Math.max(0, Math.round(safeFiniteNumber(obj.spice, 0) ?? 0));
  const total = pig_iron + metal + kryptonite + spice;
  const nonZeroCount = [pig_iron, metal, kryptonite, spice].filter((v) => v > 0).length;
  return { pig_iron, metal, kryptonite, spice, total, nonZeroCount };
}

export function isFlightReturning(job: QueueJobData | null | undefined): boolean {
  if (!job) return false;
  const rec = job as Record<string, any>;
  if (Boolean(rec.returning) || Boolean(rec.delivered)) return true;
  const nameLow = String(job.name || "").toLowerCase();
  const detLow = String(job.detail || "").toLowerCase();
  return detLow.includes("returning") || nameLow.includes("↩");
}

export function formatOngoingFlightCargo(
  job: QueueJobData | null | undefined
): OngoingFlightCargoInfo | null {
  if (!job) return null;
  const rec = job as Record<string, any>;
  const nameLow = String(job.name || "").toLowerCase();
  const missionLow = String(rec.mission_type || rec.mission || "").toLowerCase();
  const returning = isFlightReturning(job);
  const isAttack =
    missionLow === "attack" ||
    missionLow === "raid" ||
    nameLow.includes("attack") ||
    nameLow.includes("raid");
  const isRecycle = missionLow === "recycle" || nameLow.includes("recycle");
  const isTransport = missionLow === "transport" || nameLow.includes("transport");
  const isDelivered = Boolean(rec.delivered) || (returning && isTransport);

  if (isDelivered) {
    const delivered = extractResourceTotals(rec.delivered_cargo);
    if (delivered && delivered.total > 0) {
      return {
        tag: "DELIVERED",
        variant: "delivered",
        inline: formatResourcesInline(delivered),
        total: delivered.total,
        multiRes: delivered.nonZeroCount > 1,
        isEmpty: false,
      };
    }
    return { tag: "DELIVERED", variant: "delivered", inline: "", total: 0, multiRes: false, isEmpty: true };
  }

  if (!returning) {
    const expected =
      extractResourceTotals(rec.expected_cargo) ||
      (isTransport ? extractResourceTotals(job.cargo) : null);
    if (expected && expected.total > 0) {
      return {
        tag: "EXPECTED",
        variant: "expected",
        inline: formatResourcesInline(expected),
        total: expected.total,
        multiRes: expected.nonZeroCount > 1,
        isEmpty: false,
      };
    }
  }

  const actual = extractResourceTotals(job.cargo);
  const tag: OngoingFlightCargoInfo["tag"] =
    returning && isAttack ? "Loot" : returning && isRecycle ? "Harvest" : "Cargo";
  const variant: OngoingFlightCargoInfo["variant"] =
    returning && isAttack ? "loot" : returning && isRecycle ? "harvest" : "cargo";

  if (actual && actual.total > 0) {
    return {
      tag,
      variant,
      inline: formatResourcesInline(actual),
      total: actual.total,
      multiRes: actual.nonZeroCount > 1,
      isEmpty: false,
    };
  }
  if (returning && (isAttack || isRecycle)) {
    return { tag, variant, inline: "0 (empty)", total: 0, multiRes: false, isEmpty: true };
  }
  return null;
}

export function isOngoingFleetFlight(job: QueueJobData | null | undefined): boolean {
  if (!job) return false;
  const cat = String(job.category || "").toLowerCase();
  if (cat === "shipyard" || cat === "build_ship" || cat === "ships" || cat === "defense") {
    return false;
  }
  if (cat === "fleet") return true;
  return isFleetActionKind(cat);
}

export function buildClientFallbackFleetGraph(
  planets: PlanetData[],
  activeQueues: QueueJobData[],
  plan: PlannedActionData[],
  slotRows: FleetSlotBudgetRow[]
): Record<string, any> {
  const lanes = slotRows.map((row) => ({
    id: `lane:planet:${row.planet_id}`,
    scope: "planet",
    label: row.planet_coords ? `${row.planet_name} [${row.planet_coords}]` : row.planet_name,
    planet_id: row.planet_id,
    coords: row.planet_coords,
    fleet_slots_used: row.slots_used,
    fleet_slots_max: row.slots_max,
  }));

  const nodes: Array<Record<string, any>> = [];
  const edges: Array<Record<string, any>> = [];
  const lastByPlanet = new Map<number, string>();
  let layerIdx = 0;

  for (const job of activeQueues) {
    const cat = String(job.category || "").toLowerCase();
    if (cat !== "fleet" && cat !== "shipyard" && !isFleetActionKind(cat)) continue;
    const pid = Math.round(safeFiniteNumber(job.planet_id, planets[0]?.planet_id ?? 0) ?? 0);
    const id = `flt_active_${nodes.length}`;
    const cargoInfo = formatOngoingFlightCargo(job);
    const baseReason = String(job.detail || job.remaining_text || "Live in-flight queue item");
    const reasonWithCargo = cargoInfo
      ? cargoInfo.inline
        ? `${baseReason} · ${cargoInfo.tag}: ${cargoInfo.inline}`
        : `${baseReason} · ${cargoInfo.tag}`
      : baseReason;
    nodes.push({
      id,
      order_index: nodes.length,
      topological_layer: 0,
      lane_id: pid > 0 ? `lane:planet:${pid}` : "lane:global",
      planet_id: pid,
      planet_coords: String(job.planet_coords || ""),
      kind: cat === "shipyard" ? "BUILD_SHIP" : "FLEET_SAVE",
      priority: "P1",
      status: "in_flight",
      target_name: String(job.name || job.detail || "Active Flight"),
      target_level: safeFiniteNumber(job.quantity ?? job.target_level, null),
      reason: reasonWithCargo,
      executable_now: false,
      wait_eta: String(job.remaining_text || "In Flight"),
      total_eta: String(job.remaining_text || "In Flight"),
    });
    lastByPlanet.set(pid, id);
    layerIdx = 1;
  }

  for (const act of plan) {
    if (!isFleetPlanAction(act)) continue;
    const isRes = String(act.kind || "").toUpperCase() === "RESEARCH";
    const pid = Math.round(safeFiniteNumber(act.planet_id, planets[0]?.planet_id ?? 0) ?? 0);
    const id = `flt_plan_${nodes.length}`;
    nodes.push({
      id,
      order_index: nodes.length,
      topological_layer: layerIdx,
      lane_id: isRes || pid <= 0 ? "lane:global" : `lane:planet:${pid}`,
      planet_id: pid,
      planet_coords: String(act.planet_coords || ""),
      kind: String(act.kind || "FLEET"),
      priority: String(act.priority || "P2"),
      status: act.executable_now ? "ready" : "queued",
      target_name: String(act.target_name || act.kind || "Fleet Mission"),
      target_level: safeFiniteNumber(act.target_level, null),
      reason: String(act.reason || "Planned fleet action"),
      executable_now: Boolean(act.executable_now),
      wait_eta: act.wait_eta || (act.executable_now ? "Now" : "Queued"),
      total_eta: act.total_eta || "—",
    });
    const prevId = lastByPlanet.get(pid);
    if (prevId) {
      edges.push({ from: prevId, to: id, primary_type: "queue_order", types: ["queue_order"] });
    }
    lastByPlanet.set(pid, id);
    layerIdx += 1;
  }

  return {
    planner_mode: "fleet_planner",
    lanes,
    nodes,
    edges,
    summary: { node_count: nodes.length, edge_count: edges.length, layer_count: Math.max(1, layerIdx) },
  };
}

export function computeFleetSlotRows(
  fleetData: FleetPlannerGetPayload | null,
  dashboard: DashboardPayload | null,
  customPlanetNames: Record<string, string>
): FleetSlotBudgetRow[] {
  const rawBudgets = fleetData?.slot_budgets;
  const planets: PlanetData[] = Array.isArray(dashboard?.state?.planets) ? dashboard!.state!.planets! : [];
  const activeQueues: QueueJobData[] = Array.isArray(dashboard?.state?.active_queues)
    ? dashboard!.state!.active_queues!
    : [];
  const plan: PlannedActionData[] = Array.isArray(dashboard?.plan) ? dashboard!.plan! : [];

  if (Array.isArray(rawBudgets) && rawBudgets.length > 0) {
    return rawBudgets.map((b, idx) => {
      const pid = Math.round(safeFiniteNumber(b.planet_id, idx + 1) ?? idx + 1);
      const matchedPlanet = planets.find((p) => Number(p.planet_id) === pid);
      const shipsObj =
        b.docked_ships && typeof b.docked_ships === "object" ? b.docked_ships : matchedPlanet?.ships;
      const docked = summarizeDockedShips(shipsObj);
      const slotsMax = Math.max(
        1,
        Math.round(
          safeFiniteNumber(
            b.slots_max ?? b.max_slots,
            1 + (safeFiniteNumber(b.b10_level ?? matchedPlanet?.buildings?.b10, 0) ?? 0)
          ) ?? 1
        )
      );
      const slotsUsed = Math.max(
        0,
        Math.round(safeFiniteNumber(b.used_slots ?? b.slots_used ?? b.in_flight_count ?? b.active_flights, 0) ?? 0)
      );
      const plannedFleetSum =
        safeFiniteNumber(b.planned_fleet_count, null) !== null ||
        safeFiniteNumber(b.planned_shipyard_count, null) !== null
          ? Math.round(safeFiniteNumber(b.planned_fleet_count, 0) ?? 0) +
            Math.round(safeFiniteNumber(b.planned_shipyard_count, 0) ?? 0)
          : 0;
      return {
        planet_id: pid,
        planet_coords: String(b.planet_coords ?? b.coords ?? matchedPlanet?.coords ?? `P${pid}`),
        planet_name: String(customPlanetNames[String(pid)] || matchedPlanet?.name || b.planet_name || "Colony"),
        b10_level: Math.round(
          safeFiniteNumber(b.b10_level ?? b.fleet_base_level ?? matchedPlanet?.buildings?.b10, 0) ?? 0
        ),
        slots_used: slotsUsed,
        slots_max: slotsMax,
        active_flights: Math.round(safeFiniteNumber(b.in_flight_count ?? b.active_flights ?? slotsUsed, slotsUsed) ?? slotsUsed),
        docked_ships_total: Math.round(safeFiniteNumber(b.total_docked_ships ?? b.docked_ships_total, docked.total) ?? docked.total),
        docked_summary: String(b.docked_summary || docked.summary),
        planned_actions: Math.round(safeFiniteNumber(b.planned_actions ?? b.planned_count, plannedFleetSum) ?? plannedFleetSum),
        status: String(b.status || (slotsUsed >= slotsMax ? "FULL" : "OPEN")),
      };
    });
  }

  if (rawBudgets && !Array.isArray(rawBudgets) && typeof rawBudgets === "object") {
    const entries = Object.entries(rawBudgets);
    if (entries.length > 0) {
      return entries.map(([pidKey, val]: [string, any]) => {
        const pid = Math.round(safeFiniteNumber(val?.planet_id ?? pidKey, 0) ?? 0);
        const matchedPlanet = planets.find((p) => Number(p.planet_id) === pid);
        const docked = summarizeDockedShips(val?.docked_ships || matchedPlanet?.ships);
        const b10Lvl = Math.round(safeFiniteNumber(val?.b10_level ?? matchedPlanet?.buildings?.b10, 0) ?? 0);
        const slotsMax = Math.max(1, Math.round(safeFiniteNumber(val?.slots_max, 1 + b10Lvl) ?? 1));
        const slotsUsed = Math.max(0, Math.round(safeFiniteNumber(val?.slots_used, 0) ?? 0));
        return {
          planet_id: pid,
          planet_coords: String(val?.planet_coords || matchedPlanet?.coords || pidKey),
          planet_name: String(customPlanetNames[String(pid)] || matchedPlanet?.name || val?.planet_name || "Colony"),
          b10_level: b10Lvl,
          slots_used: slotsUsed,
          slots_max: slotsMax,
          active_flights: Math.round(safeFiniteNumber(val?.active_flights ?? slotsUsed, slotsUsed) ?? slotsUsed),
          docked_ships_total: docked.total,
          docked_summary: docked.summary,
          planned_actions: Math.round(safeFiniteNumber(val?.planned_actions, 0) ?? 0),
          status: slotsUsed >= slotsMax ? "FULL" : "OPEN",
        };
      });
    }
  }

  const normalPlanets = planets.filter((p) => String(p.planet_type || "normal").toLowerCase() === "normal");
  return normalPlanets.map((p) => {
    const pid = Number(p.planet_id || 0);
    const b10 = Math.round(safeFiniteNumber(p.buildings?.b10, 0) ?? 0);
    const activeFlights = activeQueues.filter(
      (q) => Number(q.planet_id) === pid && String(q.category || "").toLowerCase() === "fleet"
    ).length;
    const plannedCount = plan.filter((a) => Number(a.planet_id) === pid && isFleetActionKind(a.kind)).length;
    const docked = summarizeDockedShips(p.ships);
    const slotsMax = Math.max(1, 1 + b10);
    return {
      planet_id: pid,
      planet_coords: String(p.coords || "—"),
      planet_name: String(customPlanetNames[String(pid)] || p.name || "Colony"),
      b10_level: b10,
      slots_used: activeFlights,
      slots_max: slotsMax,
      active_flights: activeFlights,
      docked_ships_total: docked.total,
      docked_summary: docked.summary,
      planned_actions: plannedCount,
      status: activeFlights >= slotsMax ? "FULL" : "OPEN",
    };
  });
}

export function computeOngoingFlights(
  dashboard: DashboardPayload | null,
  customPlanetNames: Record<string, string>
): OngoingFlightViewModel[] {
  const activeQueues: QueueJobData[] = Array.isArray(dashboard?.state?.active_queues)
    ? dashboard!.state!.active_queues!
    : [];
  const planets: PlanetData[] = Array.isArray(dashboard?.state?.planets) ? dashboard!.state!.planets! : [];
  const planetByPid = new Map<number, PlanetData>();
  const planetByCoords = new Map<string, PlanetData>();
  for (const pl of planets) {
    const pid = Math.round(safeFiniteNumber(pl?.planet_id, 0) ?? 0);
    if (pid > 0) planetByPid.set(pid, pl);
    const c = String(pl?.coords || "").trim();
    if (c) planetByCoords.set(c, pl);
  }

  const nowSec = Math.floor(Date.now() / 1000);
  return activeQueues
    .filter((job) => isOngoingFleetFlight(job))
    .map((job, idx) => {
      const pid = Math.round(safeFiniteNumber(job.planet_id, 0) ?? 0);
      const coords = String((job as Record<string, any>).origin_coords || job.planet_coords || "").trim();
      const pl = (pid > 0 ? planetByPid.get(pid) : undefined) || (coords ? planetByCoords.get(coords) : undefined);
      const resolvedPid = pid > 0 ? pid : Math.round(safeFiniteNumber(pl?.planet_id, 0) ?? 0);
      const originName = (resolvedPid > 0 ? customPlanetNames[String(resolvedPid)] : "") || String(pl?.name || "").trim();
      const originCoords = coords || String(pl?.coords || "").trim();
      const returning = isFlightReturning(job);
      const cargoInfo = formatOngoingFlightCargo(job);
      const finTs = safeFiniteNumber(job.finish_ts, null);
      const remSec =
        finTs !== null && finTs > 0
          ? Math.max(0, Math.round(finTs - nowSec))
          : safeFiniteNumber(job.remaining_seconds, null);
      const etaText =
        String(job.remaining_text || "").trim() ||
        (remSec !== null ? (remSec > 0 ? fmtHms(remSec) : "Arriving...") : "In Flight");

      return {
        key: `flt-ongoing-${idx}-${resolvedPid}-${String(job.name || "")}-${String(finTs ?? "")}`,
        title: String(job.name || "Active Flight"),
        detail: String(job.detail || "").trim(),
        originLabel: originName
          ? originCoords ? `${originName} [${originCoords}]` : originName
          : originCoords ? `[${originCoords}]` : "Fleet",
        returning,
        cargoInfo,
        etaText,
      };
    });
}
