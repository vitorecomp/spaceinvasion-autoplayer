import React, { useCallback, useEffect, useMemo, useState } from "react";
import { feedbackKindForError, safeFiniteNumber } from "../../api/httpClient";
import { SortableTh } from "../../components/SortableTh";
import { useConsole } from "../../context/ConsoleContext";
import { coordsSortKey, useTableSort, type SortColumnMap } from "../../hooks/useTableSort";
import type {
  FleetPlannerGetPayload,
  PlannedActionData,
  PlanetData,
  QueueJobData,
  StrategyConfigData,
} from "../../types/api";
import { fmtCompact, fmtHms, fmtInt, formatResourcesInline } from "../../utils/formatters";
import { ActionGraphParamRow } from "./ActionGraphParamRow";
import { DagCanvas } from "./DagCanvas";
import {
  FLEET_PLANNER_GROUP_SUBTITLES,
  FLEET_RESEARCH_OPTIONS,
  getFleetResearchDoc,
  getShipyardHullDoc,
  getStrategyParamDoc,
} from "./paramDocs";
import {
  SHIP_OPTIONS,
  formatDurationSecondsCompact,
  normalizeUiPlannerMode,
  sanitizeMainWindowText,
} from "./plannerHelpers";

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

const FLEET_BOOL_FIELDS: ReadonlyArray<{
  key: string;
  def: boolean;
}> = [
  { key: "allow_raids", def: false },
  { key: "allow_pvp_in_az", def: false },
  { key: "allow_fleetsave", def: false },
  { key: "allow_colonize", def: false },
  { key: "allow_transport", def: false },
  { key: "allow_transmitter", def: false },
  { key: "allow_galaxy_scan", def: false },
  { key: "galaxy_scan_explore_first", def: true },
  { key: "galaxy_scan_all_galaxies", def: true },
  { key: "spy_reserved_slots_only", def: false },
  { key: "allow_shipyard", def: true },
  { key: "allow_fleet_research", def: true },
];

const FLEET_NUM_DEFAULTS: Record<string, number> = {
  combat_safety_margin: 1.4,
  raid_min_loot_spice_eq: 1500,
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

interface FleetDraftState {
  booleans: Record<string, boolean>;
  numbers: Record<string, string>;
  shipyard_targets: Record<string, string>;
  fleet_research_targets: Record<string, string>;
}

function buildFleetDraftFromConfig(cfg: StrategyConfigData | null | undefined): FleetDraftState {
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

function buildConfigPatchFromDraft(draft: FleetDraftState): Record<string, any> {
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
    fleet_save_fuel_reserve_spice: Math.round(num("fleet_save_fuel_reserve_spice", 0, 1e9)),
    ai_resource_spare_pct: num("ai_resource_spare_pct", 0, 100),
    allow_colonize: Boolean(draft.booleans.allow_colonize),
    min_colony_fields: intNum("min_colony_fields", 50, 500),
    max_planets: intNum("max_planets", 1, 20),
    allow_transport: Boolean(draft.booleans.allow_transport),
    allow_transmitter: Boolean(draft.booleans.allow_transmitter),
    allow_galaxy_scan: Boolean(draft.booleans.allow_galaxy_scan),
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

interface FleetSlotBudgetRow {
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

function summarizeDockedShips(ships: Record<string, any> | undefined): {
  total: number;
  summary: string;
} {
  if (!ships || typeof ships !== "object") {
    return { total: 0, summary: "None" };
  }
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

function isFleetActionKind(kindRaw: unknown): boolean {
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

interface OngoingFlightCargoInfo {
  tag: "EXPECTED" | "DELIVERED" | "Loot" | "Harvest" | "Cargo";
  variant: "expected" | "delivered" | "loot" | "harvest" | "cargo";
  inline: string;
  total: number;
  multiRes: boolean;
  isEmpty: boolean;
}

function extractResourceTotals(raw: unknown): {
  pig_iron: number;
  metal: number;
  kryptonite: number;
  spice: number;
  total: number;
  nonZeroCount: number;
} | null {
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

function isFlightReturning(job: QueueJobData | null | undefined): boolean {
  if (!job) return false;
  const rec = job as Record<string, any>;
  if (Boolean(rec.returning) || Boolean(rec.delivered)) return true;
  const nameLow = String(job.name || "").toLowerCase();
  const detLow = String(job.detail || "").toLowerCase();
  return detLow.includes("returning") || nameLow.includes("↩");
}

function formatOngoingFlightCargo(
  job: QueueJobData | null | undefined
): OngoingFlightCargoInfo | null {
  if (!job) return null;
  const rec = job as Record<string, any>;
  const nameLow = String(job.name || "").toLowerCase();
  const detLow = String(job.detail || "").toLowerCase();
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

  // Returning Transport: always show DELIVERED flag + delivered_cargo values when available
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
    return {
      tag: "DELIVERED",
      variant: "delivered",
      inline: "",
      total: 0,
      multiRes: false,
      isEmpty: true,
    };
  }

  // Outbound Attack / Recycle / Transport: show EXPECTED badge + expected_cargo (or transport cargo fallback)
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

  // Returning Attack / Recycle or generic cargo
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
    return {
      tag,
      variant,
      inline: "0 (empty)",
      total: 0,
      multiRes: false,
      isEmpty: true,
    };
  }
  return null;
}

function isOngoingFleetFlight(job: QueueJobData | null | undefined): boolean {
  if (!job) return false;
  const cat = String(job.category || "").toLowerCase();
  if (cat === "shipyard" || cat === "build_ship" || cat === "ships" || cat === "defense") {
    return false;
  }
  if (cat === "fleet") return true;
  return isFleetActionKind(cat);
}

function buildClientFallbackFleetGraph(
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
      edges.push({
        from: prevId,
        to: id,
        primary_type: "queue_order",
        types: ["queue_order"],
      });
    }
    lastByPlanet.set(pid, id);
    layerIdx += 1;
  }

  return {
    planner_mode: "fleet_planner",
    lanes,
    nodes,
    edges,
    summary: {
      node_count: nodes.length,
      edge_count: edges.length,
      layer_count: Math.max(1, layerIdx),
    },
  };
}

export interface FleetPlannerTabProps {
  hidden?: boolean;
}

export const FleetPlannerTab: React.FC<FleetPlannerTabProps> = ({ hidden = false }) => {
  const {
    dashboard,
    setDashboard,
    activePage,
    apiOnline,
    setConfigDirty,
    setConfigSaving,
    bumpDashboardSeq,
    apiCommand,
    loadDashboard,
    showFeedback,
  } = useConsole();

  const [fleetData, setFleetData] = useState<FleetPlannerGetPayload | null>(null);
  const [draft, setDraft] = useState<FleetDraftState>(() =>
    buildFleetDraftFromConfig(dashboard?.config)
  );
  const [draftDirty, setDraftDirty] = useState<boolean>(false);
  const [paramGroupTab, setParamGroupTab] = useState<
    "combat" | "logistics" | "scanner" | "shipyard"
  >("combat");
  const [edgeFilter, setEdgeFilter] = useState<"all" | "deps">("all");
  const [isApplying, setIsApplying] = useState<boolean>(false);
  const [isSimulating, setIsSimulating] = useState<boolean>(false);
  const [statusMessage, setStatusMessage] = useState<string>(
    "Fleet & attack controls stay synchronized with global Config."
  );

  // Bidirectionally sync from dashboard.config when Tab 4 does not have uncommitted edits
  useEffect(() => {
    if (!draftDirty && dashboard?.config) {
      setDraft(buildFleetDraftFromConfig(dashboard.config));
    }
  }, [dashboard?.config, draftDirty]);

  const fetchFleetPlanner = useCallback(
    async ({
      previewConfig = null,
      silent = false,
    }: { previewConfig?: Record<string, any> | null; silent?: boolean } = {}) => {
      try {
        const payload: Record<string, any> = { command: "fleet_planner_get" };
        if (previewConfig) payload.config = previewConfig;
        const data = (await apiCommand(payload, { timeoutMs: 20000 })) as FleetPlannerGetPayload;
        if (data && data.ok) {
          setFleetData(data);
          if (!previewConfig && !draftDirty && data.config) {
            setDraft(buildFleetDraftFromConfig(data.config));
          }
          if (!silent) {
            setStatusMessage(
              previewConfig
                ? "Simulated fleet plan with draft parameters."
                : "Fleet planner refreshed."
            );
          }
        }
      } catch (err) {
        if (!silent) {
          showFeedback(
            `Fleet planner load failed: ${(err as Error).message}`,
            feedbackKindForError(err)
          );
        }
      }
    },
    [apiCommand, draftDirty, showFeedback]
  );

  useEffect(() => {
    if (hidden || activePage !== "action-graph") return;
    void fetchFleetPlanner({ silent: true });
  }, [hidden, activePage, fetchFleetPlanner]);

  const customPlanetNames = useMemo<Record<string, string>>(() => {
    const raw = (dashboard as Record<string, any> | null | undefined)?.custom_planet_names;
    if (!raw || typeof raw !== "object") return {};
    const out: Record<string, string> = {};
    for (const [k, v] of Object.entries(raw as Record<string, unknown>)) {
      const clean = String(v || "").trim();
      if (clean) out[String(k)] = clean;
    }
    return out;
  }, [dashboard]);

  const slotRows: FleetSlotBudgetRow[] = useMemo(() => {
    const rawBudgets = fleetData?.slot_budgets;
    const planets: PlanetData[] = Array.isArray(dashboard?.state?.planets)
      ? dashboard!.state!.planets!
      : [];
    const activeQueues: QueueJobData[] = Array.isArray(dashboard?.state?.active_queues)
      ? dashboard!.state!.active_queues!
      : [];
    const plan: PlannedActionData[] = Array.isArray(dashboard?.plan) ? dashboard!.plan! : [];

    if (Array.isArray(rawBudgets) && rawBudgets.length > 0) {
      return rawBudgets.map((b, idx) => {
        const pid = Math.round(safeFiniteNumber(b.planet_id, idx + 1) ?? idx + 1);
        const matchedPlanet = planets.find((p) => Number(p.planet_id) === pid);
        const shipsObj =
          b.docked_ships && typeof b.docked_ships === "object"
            ? b.docked_ships
            : matchedPlanet?.ships;
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
          Math.round(
            safeFiniteNumber(
              b.used_slots ?? b.slots_used ?? b.in_flight_count ?? b.active_flights,
              0
            ) ?? 0
          )
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
          planet_name: String(
            customPlanetNames[String(pid)] || matchedPlanet?.name || b.planet_name || "Colony"
          ),
          b10_level: Math.round(
            safeFiniteNumber(b.b10_level ?? b.fleet_base_level ?? matchedPlanet?.buildings?.b10, 0) ??
              0
          ),
          slots_used: slotsUsed,
          slots_max: slotsMax,
          active_flights: Math.round(
            safeFiniteNumber(b.in_flight_count ?? b.active_flights ?? slotsUsed, slotsUsed) ??
              slotsUsed
          ),
          docked_ships_total: Math.round(
            safeFiniteNumber(b.total_docked_ships ?? b.docked_ships_total, docked.total) ??
              docked.total
          ),
          docked_summary: String(b.docked_summary || docked.summary),
          planned_actions: Math.round(
            safeFiniteNumber(b.planned_actions ?? b.planned_count, plannedFleetSum) ??
              plannedFleetSum
          ),
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
          const b10Lvl = Math.round(
            safeFiniteNumber(val?.b10_level ?? matchedPlanet?.buildings?.b10, 0) ?? 0
          );
          const slotsMax = Math.max(
            1,
            Math.round(safeFiniteNumber(val?.slots_max, 1 + b10Lvl) ?? 1)
          );
          const slotsUsed = Math.max(0, Math.round(safeFiniteNumber(val?.slots_used, 0) ?? 0));
          return {
            planet_id: pid,
            planet_coords: String(val?.planet_coords || matchedPlanet?.coords || pidKey),
            planet_name: String(
              customPlanetNames[String(pid)] || matchedPlanet?.name || val?.planet_name || "Colony"
            ),
            b10_level: b10Lvl,
            slots_used: slotsUsed,
            slots_max: slotsMax,
            active_flights: Math.round(
              safeFiniteNumber(val?.active_flights ?? slotsUsed, slotsUsed) ?? slotsUsed
            ),
            docked_ships_total: docked.total,
            docked_summary: docked.summary,
            planned_actions: Math.round(safeFiniteNumber(val?.planned_actions, 0) ?? 0),
            status: slotsUsed >= slotsMax ? "FULL" : "OPEN",
          };
        });
      }
    }

    // Client fallback derived from dashboard state
    const normalPlanets = planets.filter(
      (p) => String(p.planet_type || "normal").toLowerCase() === "normal"
    );
    return normalPlanets.map((p) => {
      const pid = Number(p.planet_id || 0);
      const b10 = Math.round(safeFiniteNumber(p.buildings?.b10, 0) ?? 0);
      const activeFlights = activeQueues.filter(
        (q) =>
          Number(q.planet_id) === pid &&
          String(q.category || "").toLowerCase() === "fleet"
      ).length;
      const plannedCount = plan.filter(
        (a) => Number(a.planet_id) === pid && isFleetActionKind(a.kind)
      ).length;
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
  }, [fleetData?.slot_budgets, dashboard, customPlanetNames]);

  const ongoingFlights = useMemo(() => {
    const activeQueues: QueueJobData[] = Array.isArray(dashboard?.state?.active_queues)
      ? dashboard!.state!.active_queues!
      : [];
    const planets: PlanetData[] = Array.isArray(dashboard?.state?.planets)
      ? dashboard!.state!.planets!
      : [];
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
        const coords = String(
          (job as Record<string, any>).origin_coords || job.planet_coords || ""
        ).trim();
        const pl = (pid > 0 ? planetByPid.get(pid) : undefined) || (coords ? planetByCoords.get(coords) : undefined);
        const resolvedPid = pid > 0 ? pid : Math.round(safeFiniteNumber(pl?.planet_id, 0) ?? 0);
        const originName =
          (resolvedPid > 0 ? customPlanetNames[String(resolvedPid)] : "") ||
          String(pl?.name || "").trim();
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
            ? originCoords
              ? `${originName} [${originCoords}]`
              : originName
            : originCoords
              ? `[${originCoords}]`
              : "Fleet",
          returning,
          cargoInfo,
          etaText,
        };
      });
  }, [customPlanetNames, dashboard?.state?.active_queues, dashboard?.state?.planets]);

  const activeGraph = useMemo(() => {
    if (
      fleetData?.graph &&
      typeof fleetData.graph === "object" &&
      Array.isArray(fleetData.graph.nodes)
    ) {
      return fleetData.graph;
    }
    const planets: PlanetData[] = Array.isArray(dashboard?.state?.planets)
      ? dashboard!.state!.planets!
      : [];
    const activeQueues: QueueJobData[] = Array.isArray(dashboard?.state?.active_queues)
      ? dashboard!.state!.active_queues!
      : [];
    const plan: PlannedActionData[] = Array.isArray(dashboard?.plan) ? dashboard!.plan! : [];
    return buildClientFallbackFleetGraph(planets, activeQueues, plan, slotRows);
  }, [fleetData?.graph, dashboard, slotRows]);

  const slotSortCols = useMemo<
    SortColumnMap<
      FleetSlotBudgetRow,
      "planet" | "b10" | "slots" | "flights" | "docked" | "planned"
    >
  >(
    () => ({
      planet: (r) => coordsSortKey(r.planet_coords),
      b10: { accessor: (r) => r.b10_level, defaultDirection: "desc" },
      slots: { accessor: (r) => r.slots_used / Math.max(1, r.slots_max), defaultDirection: "desc" },
      flights: { accessor: (r) => r.active_flights, defaultDirection: "desc" },
      docked: { accessor: (r) => r.docked_ships_total, defaultDirection: "desc" },
      planned: { accessor: (r) => r.planned_actions, defaultDirection: "desc" },
    }),
    []
  );
  const slotSort = useTableSort(slotRows, slotSortCols);

  // KPI computations
  const totalSlotsUsed = slotRows.reduce((acc, r) => acc + r.slots_used, 0);
  const totalSlotsMax = slotRows.reduce((acc, r) => acc + r.slots_max, 0);
  const totalDockedShips = slotRows.reduce((acc, r) => acc + r.docked_ships_total, 0);
  const graphNodes: Array<Record<string, any>> = Array.isArray(activeGraph?.nodes)
    ? activeGraph.nodes
    : [];
  const activeMissionsCount = graphNodes.length;

  const telemetry =
    fleetData?.attack_telemetry || dashboard?.ai_planner_summary?.attack_telemetry || {};
  const sparePct =
    safeFiniteNumber(
      draft.numbers.ai_resource_spare_pct,
      safeFiniteNumber(
        fleetData?.spare_budget?.spare_percent ?? fleetData?.spare_budget?.ai_resource_spare_pct,
        10
      ) ?? 10
    ) ?? 10;
  const effMode = normalizeUiPlannerMode(
    dashboard?.effective_planner_mode || dashboard?.config?.planner_mode || dashboard?.planner_mode
  );
  const lootPerHour = safeFiniteNumber(
    telemetry?.hourly_loot_spice_eq ??
      telemetry?.hourly_loot?.spice_eq ??
      telemetry?.median_loot_spice_eq_per_hour ??
      telemetry?.median_loot_per_hour ??
      telemetry?.median_loot?.spice_eq,
    null
  );
  const medianRtSec = safeFiniteNumber(
    telemetry?.median_round_trip_seconds ?? telemetry?.median_roundtrip_seconds,
    null
  );

  const handleBoolChange = (key: string, checked: boolean) => {
    setDraft((prev) => ({
      ...prev,
      booleans: { ...prev.booleans, [key]: checked },
    }));
    setDraftDirty(true);
    setConfigDirty(true);
  };

  const handleNumChange = (key: string, value: string) => {
    setDraft((prev) => ({
      ...prev,
      numbers: { ...prev.numbers, [key]: value },
    }));
    setDraftDirty(true);
    setConfigDirty(true);
  };

  const handleShipyardTargetChange = (slug: string, value: string) => {
    setDraft((prev) => ({
      ...prev,
      shipyard_targets: { ...prev.shipyard_targets, [slug]: value },
    }));
    setDraftDirty(true);
    setConfigDirty(true);
  };

  const handleFleetResearchTargetChange = (rKey: string, value: string) => {
    setDraft((prev) => ({
      ...prev,
      fleet_research_targets: { ...prev.fleet_research_targets, [rKey]: value },
    }));
    setDraftDirty(true);
    setConfigDirty(true);
  };

  const liveResearches: Record<string, number> =
    dashboard?.state?.researches && typeof dashboard.state.researches === "object"
      ? (dashboard.state.researches as Record<string, number>)
      : {};

  const handleSimulate = async () => {
    if (isSimulating) return;
    setIsSimulating(true);
    setStatusMessage("Simulating fleet & attack plan preview...");
    try {
      const previewPatch = buildConfigPatchFromDraft(draft);
      await fetchFleetPlanner({ previewConfig: previewPatch, silent: false });
    } finally {
      setIsSimulating(false);
    }
  };

  const handleReset = () => {
    setDraft(buildFleetDraftFromConfig(dashboard?.config));
    setDraftDirty(false);
    setConfigDirty(false);
    setStatusMessage("Fleet controls synced back to active Config.");
    void fetchFleetPlanner({ silent: true });
  };

  const handleApplyToConfig = async () => {
    if (isApplying) return;
    const gState = (globalThis as any).state;
    if (gState) {
      gState.configSaving = (gState.configSaving || 0) + 1;
    }
    setIsApplying(true);
    setConfigSaving((c) => c + 1);
    setStatusMessage("Applying fleet & logistics settings to active Config...");
    const patch = buildConfigPatchFromDraft(draft);
    try {
      const res = await apiCommand({ command: "update_config", config: patch });
      const updatedCfg = res?.config || { ...(dashboard?.config || {}), ...patch };
      if (gState?.dashboard) {
        gState.dashboard = { ...gState.dashboard, config: updatedCfg };
      }
      setDashboard((prev) => ({
        ...(prev || {}),
        config: updatedCfg,
      }));
      setDraft(buildFleetDraftFromConfig(updatedCfg));
      setDraftDirty(false);
      setConfigDirty(false);
      bumpDashboardSeq();
      if (typeof (globalThis as any).renderConfigForm === "function") {
        (globalThis as any).renderConfigForm(updatedCfg);
      }
      setStatusMessage("Saved to Config and synced bidirectionally.");
      showFeedback("Fleet planner settings applied to Config.", "ok");
      await Promise.all([
        fetchFleetPlanner({ silent: true }),
        loadDashboard({ refresh: false, preserveConfig: false, force: true }),
      ]);
    } catch (err) {
      setStatusMessage(`Save failed: ${(err as Error).message}`);
      showFeedback(
        `Failed to apply fleet config: ${(err as Error).message}`,
        feedbackKindForError(err)
      );
    } finally {
      if (gState) gState.configSaving = Math.max(0, (gState.configSaving || 0) - 1);
      setConfigSaving((c) => Math.max(0, c - 1));
      setIsApplying(false);
    }
  };

  return (
    <div className="ag-tab-split" id="ag-panel-fleet-planner" hidden={hidden}>
      {/* Left Main Pane: Live Fleet DAG + Per-Planet Slot Summary Table */}
      <article className="hud-window ag-main-window" id="ag-flt-main-window">
        <div className="window-bar window-bar-wrap">
          <div className="window-title-group">
            <span className="module-code tabular">FLT</span>
            <h2>Fleet, Raids &amp; Logistics Planner</h2>
            <span
              id="flt-sync-badge"
              className={`ag-status-pill tabular ${draftDirty ? "is-sim" : "is-live"}`}
              role="status"
            >
              {draftDirty ? "PREVIEW EDITED" : "CONFIG SYNCED"}
            </span>
          </div>
          <div className="inline-actions">
            <button
              className="btn btn-secondary btn-sm"
              id="btn-flt-refresh"
              type="button"
              disabled={!apiOnline}
              onClick={() => void fetchFleetPlanner({ silent: false })}
            >
              Refresh Fleet DAG
            </button>
          </div>
        </div>

        {/* KPI Strip */}
        <div className="ag-kpi-strip">
          <div className="ag-kpi-cell">
            <span className="ag-kpi-label">Empire Fleet Slots</span>
            <strong className="ag-kpi-val tabular" id="flt-kpi-slots">
              {fmtInt(totalSlotsUsed)} / {fmtInt(totalSlotsMax)}
            </strong>
            <span className="ag-kpi-sub tabular">
              {slotRows.length} active planets · {fmtInt(totalDockedShips)} docked ships
            </span>
          </div>
          <div className="ag-kpi-cell">
            <span className="ag-kpi-label">Active &amp; Planned Flights</span>
            <strong className="ag-kpi-val tabular" id="flt-kpi-missions">
              {fmtInt(activeMissionsCount)}
            </strong>
            <span className="ag-kpi-sub tabular">
              Raids: {draft.booleans.allow_raids ? "ON" : "OFF"} · Shipyard:{" "}
              {draft.booleans.allow_shipyard ? "ON" : "OFF"} · Tech:{" "}
              {draft.booleans.allow_fleet_research ? "ON" : "OFF"}
            </span>
          </div>
          <div className="ag-kpi-cell">
            <span className="ag-kpi-label">Soft Spare % Budget</span>
            <strong className="ag-kpi-val tabular" id="flt-kpi-budget">
              {fmtInt(sparePct)}%
            </strong>
            <span className="ag-kpi-sub tabular">
              Feeds {effMode === "ai_planner" ? "AI-Planner" : "Heuristic"} reserve budget
            </span>
          </div>
          <div className="ag-kpi-cell">
            <span className="ag-kpi-label">Median Attack Telemetry</span>
            <strong className="ag-kpi-val tabular" id="flt-kpi-loot">
              {lootPerHour !== null ? `${fmtCompact(lootPerHour)}/h` : "—"}
            </strong>
            <span className="ag-kpi-sub tabular">
              Median RT: {formatDurationSecondsCompact(medianRtSec)} · Reserve:{" "}
              {fmtInt(safeFiniteNumber(draft.numbers.fleet_save_fuel_reserve_spice, 500) ?? 500)}{" "}
              Spice
            </span>
          </div>
        </div>

        <DagCanvas
          graph={activeGraph}
          idPrefix="ag-flt"
          markerPrefix="ag-flt"
          edgeFilter={edgeFilter}
          onEdgeFilterChange={setEdgeFilter}
          emptyMessage="No active flights, raids, recyclers, or shipyard builds in the current queue."
        />

        {ongoingFlights.length > 0 ? (
          <div className="ag-flt-ongoing-box" id="flt-ongoing-flights" role="region" aria-label="Ongoing fleet flights">
            <div className="ag-flt-ongoing-head">
              <div className="window-title-group">
                <h3>Ongoing Flights</h3>
                <span className="sub">Live outbound expectations &amp; transport deliveries</span>
              </div>
              <span className="ag-toolbar-meta tabular">
                {`${ongoingFlights.length} ${ongoingFlights.length === 1 ? "flight" : "flights"}`}
              </span>
            </div>
            <div className="ag-flt-ongoing-list">
              {ongoingFlights.map((flight) => (
                <div key={flight.key} className="ag-flt-ongoing-row">
                  <div className="ag-flt-ongoing-main">
                    <span
                      className={`ag-flt-dir-pill tabular ${flight.returning ? "is-returning" : "is-outbound"}`}
                    >
                      {flight.returning ? "RETURNING" : "OUTBOUND"}
                    </span>
                    <strong className="ag-flt-ongoing-title">
                      {sanitizeMainWindowText(flight.title)}
                    </strong>
                    <span className="ag-cell-sub tabular">
                      {sanitizeMainWindowText(flight.originLabel)}
                    </span>
                    {flight.detail ? (
                      <span className="ag-cell-sub">
                        {sanitizeMainWindowText(flight.detail)}
                      </span>
                    ) : null}
                  </div>
                  <div className="ag-flt-ongoing-meta tabular">
                    {flight.cargoInfo ? (
                      <span
                        className={`ag-flt-cargo-line is-${flight.cargoInfo.variant}`}
                        title={
                          flight.cargoInfo.inline
                            ? `${flight.cargoInfo.tag}: ${flight.cargoInfo.inline}${flight.cargoInfo.total > 0 ? ` (${fmtInt(flight.cargoInfo.total)} total)` : ""}`
                            : flight.cargoInfo.tag
                        }
                      >
                        <span
                          className={`ag-flt-cargo-badge is-${flight.cargoInfo.variant}`}
                          data-cargo-kind={flight.cargoInfo.variant}
                        >
                          {flight.cargoInfo.tag}
                        </span>
                        {flight.cargoInfo.inline ? (
                          <span className="ag-flt-cargo-vals">
                            {flight.cargoInfo.inline}
                            {flight.cargoInfo.multiRes
                              ? ` (${fmtCompact(flight.cargoInfo.total)})`
                              : ""}
                          </span>
                        ) : null}
                      </span>
                    ) : null}
                    <span className="eta-pill tabular">{flight.etaText}</span>
                  </div>
                </div>
              ))}
            </div>
          </div>
        ) : null}

        <div className="window-bar ag-subtable-bar">
          <div className="window-title-group">
            <h3>Per-Planet Fleet Slot &amp; Docked Budget</h3>
            <span className="sub">1 + Fleet base (b10) slots per planet · Live &amp; planned load</span>
          </div>
          <span className="ag-toolbar-meta tabular">{slotRows.length} planets</span>
        </div>

        <div className="table-wrap table-scroll">
          <table id="flt-slots-table">
            <thead>
              <tr>
                <SortableTh
                  label="Planet"
                  sortKey="planet"
                  activeKey={slotSort.sortKey}
                  direction={slotSort.sortDirection}
                  onSort={slotSort.requestSort}
                />
                <SortableTh
                  label="Fleet Base"
                  sortKey="b10"
                  activeKey={slotSort.sortKey}
                  direction={slotSort.sortDirection}
                  onSort={slotSort.requestSort}
                  align="right"
                />
                <SortableTh
                  label="Fleet Slots"
                  sortKey="slots"
                  activeKey={slotSort.sortKey}
                  direction={slotSort.sortDirection}
                  onSort={slotSort.requestSort}
                  align="right"
                />
                <SortableTh
                  label="In Flight"
                  sortKey="flights"
                  activeKey={slotSort.sortKey}
                  direction={slotSort.sortDirection}
                  onSort={slotSort.requestSort}
                  align="right"
                />
                <SortableTh
                  label="Docked Ships"
                  sortKey="docked"
                  activeKey={slotSort.sortKey}
                  direction={slotSort.sortDirection}
                  onSort={slotSort.requestSort}
                  align="right"
                />
                <SortableTh
                  label="Planned"
                  sortKey="planned"
                  activeKey={slotSort.sortKey}
                  direction={slotSort.sortDirection}
                  onSort={slotSort.requestSort}
                  align="right"
                />
                <th>Status</th>
              </tr>
            </thead>
            <tbody>
              {slotSort.sortedRows.length === 0 ? (
                <tr>
                  <td colSpan={7} className="empty-note">
                    No planet fleet budget rows loaded yet.
                  </td>
                </tr>
              ) : (
                slotSort.sortedRows.map((row) => {
                  const isFull = row.slots_used >= row.slots_max;
                  return (
                    <tr key={`flt-slot-${row.planet_id}-${row.planet_coords}`}>
                      <td>
                        <strong>{sanitizeMainWindowText(row.planet_coords)}</strong>
                        <div className="ag-cell-sub">{sanitizeMainWindowText(row.planet_name)}</div>
                      </td>
                      <td className="num tabular">b10 L{row.b10_level}</td>
                      <td className="num tabular">
                        <strong>{row.slots_used}</strong> / {row.slots_max}
                      </td>
                      <td className="num tabular">{row.active_flights}</td>
                      <td className="num tabular">
                        <div>{fmtInt(row.docked_ships_total)}</div>
                        <div className="ag-cell-sub">
                          {sanitizeMainWindowText(row.docked_summary)}
                        </div>
                      </td>
                      <td className="num tabular">{row.planned_actions}</td>
                      <td>
                        <span className={`ag-status-pill ${isFull ? "is-sim" : "is-live"}`}>
                          {row.status}
                        </span>
                      </td>
                    </tr>
                  );
                })
              )}
            </tbody>
          </table>
        </div>
      </article>

      {/* Right Controls Pane: Bidirectional Sync with StrategyConfig */}
      <aside className="hud-window ag-control-panel" id="ag-flt-control-panel">
        <div className="window-bar window-bar-wrap">
          <div className="window-title-group">
            <span className="module-code tabular">CTL</span>
            <h2>Fleet &amp; Tactical Settings</h2>
          </div>
          <span
            className={`ag-status-pill tabular ${draftDirty ? "is-sim" : "is-live"}`}
            id="flt-panel-pill"
          >
            {draftDirty ? "EDITED" : "SYNCED"}
          </span>
        </div>

        <div className="ag-panel-tabs" role="tablist" aria-label="Fleet settings sections">
          <button
            type="button"
            className={`tab-btn${paramGroupTab === "combat" ? " active" : ""}`}
            data-flt-ptab="combat"
            onClick={() => setParamGroupTab("combat")}
          >
            Combat
          </button>
          <button
            type="button"
            className={`tab-btn${paramGroupTab === "logistics" ? " active" : ""}`}
            data-flt-ptab="logistics"
            onClick={() => setParamGroupTab("logistics")}
          >
            Logistics
          </button>
          <button
            type="button"
            className={`tab-btn${paramGroupTab === "scanner" ? " active" : ""}`}
            data-flt-ptab="scanner"
            onClick={() => setParamGroupTab("scanner")}
          >
            Scanner
          </button>
          <button
            type="button"
            className={`tab-btn${paramGroupTab === "shipyard" ? " active" : ""}`}
            data-flt-ptab="shipyard"
            onClick={() => setParamGroupTab("shipyard")}
          >
            Shipyard &amp; Tech
          </button>
        </div>

        <div className="ag-panel-body">
          <div className="ag-param-pane" hidden={paramGroupTab !== "combat"}>
            <div className="ag-param-group">
              <div className="ag-param-group-head">
                <h3>Combat, Raids &amp; Escape Flights</h3>
                <p className="ag-param-group-sub">{FLEET_PLANNER_GROUP_SUBTITLES.combat}</p>
              </div>
              <div className="ag-param-list">
                {(["allow_raids", "allow_pvp_in_az", "allow_fleetsave"] as const).map((bKey) => (
                  <ActionGraphParamRow
                    key={bKey}
                    doc={getStrategyParamDoc(bKey)}
                    htmlFor={`flt-cfg-${bKey}`}
                    layout="toggle"
                  >
                    <input
                      id={`flt-cfg-${bKey}`}
                      type="checkbox"
                      className="config-checkbox"
                      checked={Boolean(draft.booleans[bKey])}
                      onChange={(e) => handleBoolChange(bKey, e.target.checked)}
                    />
                  </ActionGraphParamRow>
                ))}

                <ActionGraphParamRow
                  doc={getStrategyParamDoc("combat_safety_margin")}
                  htmlFor="flt-cfg-combat_safety_margin"
                  layout="inline"
                >
                  <input
                    id="flt-cfg-combat_safety_margin"
                    type="number"
                    min={1}
                    max={10}
                    step={0.05}
                    value={draft.numbers.combat_safety_margin}
                    onChange={(e) => handleNumChange("combat_safety_margin", e.target.value)}
                  />
                </ActionGraphParamRow>

                <ActionGraphParamRow
                  doc={getStrategyParamDoc("raid_min_loot_spice_eq")}
                  htmlFor="flt-cfg-raid_min_loot_spice_eq"
                  layout="inline"
                >
                  <input
                    id="flt-cfg-raid_min_loot_spice_eq"
                    type="number"
                    min={0}
                    max={10000000}
                    step={100}
                    value={draft.numbers.raid_min_loot_spice_eq}
                    onChange={(e) => handleNumChange("raid_min_loot_spice_eq", e.target.value)}
                  />
                </ActionGraphParamRow>

                <ActionGraphParamRow
                  doc={getStrategyParamDoc("fleet_save_fuel_reserve_spice")}
                  htmlFor="flt-cfg-fleet_save_fuel_reserve_spice"
                  layout="inline"
                >
                  <input
                    id="flt-cfg-fleet_save_fuel_reserve_spice"
                    type="number"
                    min={0}
                    max={10000000}
                    step={50}
                    value={draft.numbers.fleet_save_fuel_reserve_spice}
                    onChange={(e) =>
                      handleNumChange("fleet_save_fuel_reserve_spice", e.target.value)
                    }
                  />
                </ActionGraphParamRow>

                <ActionGraphParamRow
                  doc={getStrategyParamDoc("ai_resource_spare_pct")}
                  htmlFor="flt-cfg-ai_resource_spare_pct"
                  layout="inline"
                >
                  <input
                    id="flt-cfg-ai_resource_spare_pct"
                    type="number"
                    min={0}
                    max={90}
                    step={1}
                    value={draft.numbers.ai_resource_spare_pct}
                    onChange={(e) => handleNumChange("ai_resource_spare_pct", e.target.value)}
                  />
                </ActionGraphParamRow>
              </div>
            </div>
          </div>

          <div className="ag-param-pane" hidden={paramGroupTab !== "logistics"}>
            <div className="ag-param-group">
              <div className="ag-param-group-head">
                <h3>Colonization &amp; Inter-Planet Transport</h3>
                <p className="ag-param-group-sub">{FLEET_PLANNER_GROUP_SUBTITLES.logistics}</p>
              </div>
              <div className="ag-param-list">
                {(["allow_colonize", "allow_transport", "allow_transmitter"] as const).map(
                  (bKey) => (
                    <ActionGraphParamRow
                      key={bKey}
                      doc={getStrategyParamDoc(bKey)}
                      htmlFor={`flt-cfg-${bKey}`}
                      layout="toggle"
                    >
                      <input
                        id={`flt-cfg-${bKey}`}
                        type="checkbox"
                        className="config-checkbox"
                        checked={Boolean(draft.booleans[bKey])}
                        onChange={(e) => handleBoolChange(bKey, e.target.checked)}
                      />
                    </ActionGraphParamRow>
                  )
                )}

                <ActionGraphParamRow
                  doc={getStrategyParamDoc("min_colony_fields")}
                  htmlFor="flt-cfg-min_colony_fields"
                  layout="inline"
                >
                  <input
                    id="flt-cfg-min_colony_fields"
                    type="number"
                    min={50}
                    max={500}
                    step={5}
                    value={draft.numbers.min_colony_fields}
                    onChange={(e) => handleNumChange("min_colony_fields", e.target.value)}
                  />
                </ActionGraphParamRow>

                <ActionGraphParamRow
                  doc={getStrategyParamDoc("max_planets")}
                  htmlFor="flt-cfg-max_planets"
                  layout="inline"
                >
                  <input
                    id="flt-cfg-max_planets"
                    type="number"
                    min={1}
                    max={20}
                    step={1}
                    value={draft.numbers.max_planets}
                    onChange={(e) => handleNumChange("max_planets", e.target.value)}
                  />
                </ActionGraphParamRow>
              </div>
            </div>
          </div>

          <div className="ag-param-pane" hidden={paramGroupTab !== "scanner"}>
            <div className="ag-param-group">
              <div className="ag-param-group-head">
                <h3>Smart Galaxy Scanner &amp; Spy Slots</h3>
                <p className="ag-param-group-sub">{FLEET_PLANNER_GROUP_SUBTITLES.scanner}</p>
              </div>
              <div className="ag-param-list">
                {(
                  [
                    "allow_galaxy_scan",
                    "galaxy_scan_explore_first",
                    "galaxy_scan_all_galaxies",
                    "spy_reserved_slots_only",
                  ] as const
                ).map((bKey) => (
                  <ActionGraphParamRow
                    key={bKey}
                    doc={getStrategyParamDoc(bKey)}
                    htmlFor={`flt-cfg-${bKey}`}
                    layout="toggle"
                  >
                    <input
                      id={`flt-cfg-${bKey}`}
                      type="checkbox"
                      className="config-checkbox"
                      checked={Boolean(draft.booleans[bKey])}
                      onChange={(e) => handleBoolChange(bKey, e.target.checked)}
                    />
                  </ActionGraphParamRow>
                ))}

                <ActionGraphParamRow
                  doc={getStrategyParamDoc("galaxy_sweep_interval_seconds")}
                  htmlFor="flt-cfg-galaxy_sweep_interval_seconds"
                  layout="inline"
                >
                  <input
                    id="flt-cfg-galaxy_sweep_interval_seconds"
                    type="number"
                    min={10}
                    max={86400}
                    step={10}
                    value={draft.numbers.galaxy_sweep_interval_seconds}
                    onChange={(e) =>
                      handleNumChange("galaxy_sweep_interval_seconds", e.target.value)
                    }
                  />
                </ActionGraphParamRow>

                <ActionGraphParamRow
                  doc={getStrategyParamDoc("galaxy_sweep_pages")}
                  htmlFor="flt-cfg-galaxy_sweep_pages"
                  layout="inline"
                >
                  <input
                    id="flt-cfg-galaxy_sweep_pages"
                    type="number"
                    min={1}
                    max={400}
                    step={5}
                    value={draft.numbers.galaxy_sweep_pages}
                    onChange={(e) => handleNumChange("galaxy_sweep_pages", e.target.value)}
                  />
                </ActionGraphParamRow>

                <ActionGraphParamRow
                  doc={getStrategyParamDoc("galaxy_scan_click_delay_seconds")}
                  htmlFor="flt-cfg-galaxy_scan_click_delay_seconds"
                  layout="inline"
                >
                  <input
                    id="flt-cfg-galaxy_scan_click_delay_seconds"
                    type="number"
                    min={0.1}
                    max={30}
                    step={0.1}
                    value={draft.numbers.galaxy_scan_click_delay_seconds}
                    onChange={(e) =>
                      handleNumChange("galaxy_scan_click_delay_seconds", e.target.value)
                    }
                  />
                </ActionGraphParamRow>

                <ActionGraphParamRow
                  doc={getStrategyParamDoc("galaxy_scan_click_jitter_seconds")}
                  htmlFor="flt-cfg-galaxy_scan_click_jitter_seconds"
                  layout="inline"
                >
                  <input
                    id="flt-cfg-galaxy_scan_click_jitter_seconds"
                    type="number"
                    min={0}
                    max={20}
                    step={0.1}
                    value={draft.numbers.galaxy_scan_click_jitter_seconds}
                    onChange={(e) =>
                      handleNumChange("galaxy_scan_click_jitter_seconds", e.target.value)
                    }
                  />
                </ActionGraphParamRow>

                <ActionGraphParamRow
                  doc={getStrategyParamDoc("galaxy_scan_refresh_pct")}
                  htmlFor="flt-cfg-galaxy_scan_refresh_pct"
                  layout="inline"
                >
                  <input
                    id="flt-cfg-galaxy_scan_refresh_pct"
                    type="number"
                    min={0}
                    max={100}
                    step={1}
                    value={draft.numbers.galaxy_scan_refresh_pct}
                    onChange={(e) => handleNumChange("galaxy_scan_refresh_pct", e.target.value)}
                  />
                </ActionGraphParamRow>

                <ActionGraphParamRow
                  doc={getStrategyParamDoc("reserved_spy_slots")}
                  htmlFor="flt-cfg-reserved_spy_slots"
                  layout="inline"
                >
                  <input
                    id="flt-cfg-reserved_spy_slots"
                    type="number"
                    min={0}
                    max={20}
                    step={1}
                    value={draft.numbers.reserved_spy_slots}
                    onChange={(e) => handleNumChange("reserved_spy_slots", e.target.value)}
                  />
                </ActionGraphParamRow>

                <ActionGraphParamRow
                  doc={getStrategyParamDoc("galaxy_scan_near_weight")}
                  htmlFor="flt-cfg-galaxy_scan_near_weight"
                  layout="inline"
                >
                  <input
                    id="flt-cfg-galaxy_scan_near_weight"
                    type="number"
                    min={0}
                    max={10}
                    step={0.05}
                    value={draft.numbers.galaxy_scan_near_weight}
                    onChange={(e) => handleNumChange("galaxy_scan_near_weight", e.target.value)}
                  />
                </ActionGraphParamRow>

                <ActionGraphParamRow
                  doc={getStrategyParamDoc("galaxy_scan_attack_weight")}
                  htmlFor="flt-cfg-galaxy_scan_attack_weight"
                  layout="inline"
                >
                  <input
                    id="flt-cfg-galaxy_scan_attack_weight"
                    type="number"
                    min={0}
                    max={10}
                    step={0.05}
                    value={draft.numbers.galaxy_scan_attack_weight}
                    onChange={(e) => handleNumChange("galaxy_scan_attack_weight", e.target.value)}
                  />
                </ActionGraphParamRow>

                <ActionGraphParamRow
                  doc={getStrategyParamDoc("spy_dispatch_planet_id")}
                  htmlFor="flt-cfg-spy_dispatch_planet_id"
                  layout="inline"
                >
                  <input
                    id="flt-cfg-spy_dispatch_planet_id"
                    type="number"
                    min={0}
                    step={1}
                    value={draft.numbers.spy_dispatch_planet_id}
                    onChange={(e) => handleNumChange("spy_dispatch_planet_id", e.target.value)}
                  />
                </ActionGraphParamRow>
              </div>
            </div>
          </div>

          <div className="ag-param-pane" hidden={paramGroupTab !== "shipyard"}>
            <div className="ag-param-group">
              <div className="ag-param-group-head">
                <h3>Fleet &amp; Ship Research (Spare Budget)</h3>
                <p className="ag-param-group-sub">{FLEET_PLANNER_GROUP_SUBTITLES.fleetResearch}</p>
              </div>
              <div className="ag-param-list">
                <ActionGraphParamRow
                  doc={getStrategyParamDoc("allow_fleet_research")}
                  htmlFor="flt-cfg-allow_fleet_research"
                  layout="toggle"
                >
                  <input
                    id="flt-cfg-allow_fleet_research"
                    type="checkbox"
                    className="config-checkbox"
                    checked={Boolean(draft.booleans.allow_fleet_research)}
                    onChange={(e) => handleBoolChange("allow_fleet_research", e.target.checked)}
                  />
                </ActionGraphParamRow>

                {FLEET_RESEARCH_OPTIONS.map((opt) => (
                  <ActionGraphParamRow
                    key={`flt-res-${opt.key}`}
                    doc={getFleetResearchDoc(
                      opt.key,
                      safeFiniteNumber(liveResearches[opt.key], null)
                    )}
                    htmlFor={`flt-research-${opt.key}`}
                    layout="inline"
                  >
                    <input
                      id={`flt-research-${opt.key}`}
                      type="number"
                      min={0}
                      max={100}
                      step={1}
                      value={draft.fleet_research_targets[opt.key] ?? "0"}
                      onChange={(e) => handleFleetResearchTargetChange(opt.key, e.target.value)}
                    />
                  </ActionGraphParamRow>
                ))}
              </div>
            </div>

            <div className="ag-param-group">
              <div className="ag-param-group-head">
                <h3>Shipyard Targets per Hull</h3>
                <p className="ag-param-group-sub">{FLEET_PLANNER_GROUP_SUBTITLES.shipyard}</p>
              </div>
              <div className="ag-param-list">
                <ActionGraphParamRow
                  doc={getStrategyParamDoc("allow_shipyard")}
                  htmlFor="flt-cfg-allow_shipyard"
                  layout="toggle"
                >
                  <input
                    id="flt-cfg-allow_shipyard"
                    type="checkbox"
                    className="config-checkbox"
                    checked={Boolean(draft.booleans.allow_shipyard)}
                    onChange={(e) => handleBoolChange("allow_shipyard", e.target.checked)}
                  />
                </ActionGraphParamRow>

                {Object.keys(draft.shipyard_targets).map((slug) => (
                  <ActionGraphParamRow
                    key={`flt-ship-${slug}`}
                    doc={getShipyardHullDoc(slug)}
                    htmlFor={`flt-shipyard-${slug}`}
                    layout="inline"
                  >
                    <input
                      id={`flt-shipyard-${slug}`}
                      type="number"
                      min={0}
                      max={9999999}
                      step={1}
                      value={draft.shipyard_targets[slug] ?? "0"}
                      onChange={(e) => handleShipyardTargetChange(slug, e.target.value)}
                    />
                  </ActionGraphParamRow>
                ))}
              </div>
            </div>
          </div>
        </div>

        <div className="ag-panel-footer">
          <p className="ag-sim-note" id="flt-status-text">
            {statusMessage}
          </p>
          <div className="ag-footer-actions">
            <button
              className="btn btn-secondary btn-sm"
              id="btn-flt-simulate"
              type="button"
              disabled={!apiOnline || isSimulating}
              onClick={() => void handleSimulate()}
            >
              {isSimulating ? "Simulating..." : "Preview Simulate"}
            </button>
            <button
              className="btn btn-secondary btn-sm"
              id="btn-flt-reset"
              type="button"
              disabled={isApplying}
              onClick={handleReset}
            >
              Reset
            </button>
            <button
              className="btn btn-primary btn-sm btn-span"
              id="btn-flt-apply"
              type="button"
              disabled={isApplying}
              onClick={() => void handleApplyToConfig()}
            >
              {isApplying ? "Saving..." : "Apply to Config"}
            </button>
          </div>
        </div>
      </aside>
    </div>
  );
};

export default FleetPlannerTab;
