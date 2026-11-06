import { safeFiniteNumber } from "../../api/httpClient";
import type {
  ConfigOptimizerGetPayload,
  ConfigOptimizerResultData,
  PlannerJobState,
} from "../../types/api";
import {
  ConfigOptimizerDraft,
  DEFAULT_CONFIG_OPTIMIZER_DRAFT,
  MISC_BUILDING_KEYS,
  OPTIMIZED_BUILDING_KEYS,
  normalizeSpecializationKey,
} from "./plannerHelpers";

export const MAX_PLANET_NAME_LENGTH = 48;

export interface RenamePlanetApiResponse {
  ok?: boolean;
  command?: string;
  planet_id?: number;
  name?: string;
  game_name?: string;
  cleared?: boolean;
  custom_planet_names?: Record<string, string>;
  planet?: {
    planet_id?: number;
    coords?: string;
    name?: string;
    game_name?: string;
    custom_name?: string | null;
  };
  message?: string;
}

export interface OwnedDashboardPlanetRow {
  slot: number;
  planetId: number;
  name: string;
  gameName: string;
  hasCustomName: boolean;
  coords: string;
}

export interface NormalizedPlanetRow {
  slot: number;
  planetId: number;
  name: string;
  gameName: string;
  hasCustomName: boolean;
  coords: string;
  isPlaceholder: boolean;
  isCentral: boolean;
  temperature: number | null;
  fieldsUsedCurrent: number | null;
  fieldsUsedTarget: number;
  fieldsMax: number;
  detectedSpecialization: string;
  lockedSpecialization: string;
  previousSpecialization: string | null;
  specializationChanged: boolean;
  warehouseDriver: string;
  lastLevelTimeSec: number | null;
  lastLevelTimeText: string;
  monthlyShips: number | null;
  monthlySpiceEq: number | null;
  builtLevels: Record<string, number>;
  targetLevels: Record<string, number>;
  reservedFloors: Record<string, number>;
  levelsToDemolish: number;
  demolitionsWhenFull: number;
  buildingsDetail: Array<{
    key: string;
    name: string;
    built: number;
    target: number;
    toBuild: number;
    toDemolish: number;
    reserved: number;
    lastTimeText: string;
  }>;
}

export interface ResearchPlanRow {
  key: string;
  name: string;
  current: number;
  universeMax: number;
  planned: number;
  gap: number;
}

export function sanitizeCustomNamesMap(raw: unknown): Record<string, string> {
  if (!raw || typeof raw !== "object") return {};
  const out: Record<string, string> = {};
  for (const [k, v] of Object.entries(raw as Record<string, unknown>)) {
    const cleanKey = String(k || "").trim();
    const cleanVal = String(v || "").trim();
    if (cleanKey && cleanVal) {
      out[cleanKey] = cleanVal;
    }
  }
  return out;
}

export function validatePlanetRename(raw: string): { trimmed: string; error: string | null } {
  const trimmed = String(raw ?? "").trim();
  if (trimmed.length > MAX_PLANET_NAME_LENGTH) {
    return {
      trimmed,
      error: `Planet name must be ${MAX_PLANET_NAME_LENGTH} characters or fewer.`,
    };
  }
  if (/[\u0000-\u001F\u007F]/.test(trimmed)) {
    return {
      trimmed,
      error: "Planet name cannot contain control characters.",
    };
  }
  return { trimmed, error: null };
}

export function sanitizeSpecializationsMap(raw: unknown): Record<string, string> {
  if (!raw || typeof raw !== "object") return {};
  const out: Record<string, string> = {};
  for (const [k, v] of Object.entries(raw as Record<string, unknown>)) {
    const cleanKey = String(k || "").trim();
    if (!cleanKey) continue;
    const norm = normalizeSpecializationKey(v);
    if (norm !== "auto") {
      out[cleanKey] = norm;
    }
  }
  return out;
}

export function planetSpecKeyForRow(row: Pick<NormalizedPlanetRow, "planetId" | "coords">): string {
  return row.planetId > 0 ? String(row.planetId) : row.coords;
}

export function resolveLockedSpecForRow(
  row: Pick<NormalizedPlanetRow, "planetId" | "coords" | "slot" | "lockedSpecialization">,
  specMap: Record<string, string>
): string {
  const pidKey = row.planetId > 0 ? String(row.planetId) : "";
  if (pidKey && specMap[pidKey]) return normalizeSpecializationKey(specMap[pidKey]);
  if (row.coords && specMap[row.coords]) return normalizeSpecializationKey(specMap[row.coords]);
  const slotKey = `slot:${row.slot}`;
  if (specMap[slotKey]) return normalizeSpecializationKey(specMap[slotKey]);
  return "auto";
}

export function draftFromParams(
  raw: Record<string, any> | null | undefined,
  persistedSpecializations?: Record<string, string> | null
): ConfigOptimizerDraft {
  const persistedSpecs = sanitizeSpecializationsMap(persistedSpecializations);
  if (!raw || typeof raw !== "object") {
    return {
      ...DEFAULT_CONFIG_OPTIMIZER_DRAFT,
      planet_specializations: { ...persistedSpecs },
      min_fleet_per_planet: { ...DEFAULT_CONFIG_OPTIMIZER_DRAFT.min_fleet_per_planet },
      misc_building_levels: { ...DEFAULT_CONFIG_OPTIMIZER_DRAFT.misc_building_levels },
      optimized_building_max_levels: {
        ...DEFAULT_CONFIG_OPTIMIZER_DRAFT.optimized_building_max_levels,
      },
    };
  }
  const get = (snake: string, upper: string, def: any) =>
    raw[snake] !== undefined ? raw[snake] : raw[upper] !== undefined ? raw[upper] : def;

  const rawFleet = get("min_fleet_per_planet", "MIN_FLEET_PER_PLANET", {}) || {};
  const minFleet: Record<string, string> = {};
  for (const sk of ["s2", "s10", "s11"]) {
    minFleet[sk] = String(
      Math.max(
        0,
        Math.round(
          safeFiniteNumber(
            rawFleet[sk],
            Number(DEFAULT_CONFIG_OPTIMIZER_DRAFT.min_fleet_per_planet[sk])
          ) ?? 10
        )
      )
    );
  }

  const rawMisc = get("misc_building_levels", "MISC_BUILDING_LEVELS", {}) || {};
  const miscLevels: Record<string, string> = {};
  for (const bk of MISC_BUILDING_KEYS) {
    miscLevels[bk] = String(
      Math.max(
        0,
        Math.round(
          safeFiniteNumber(
            rawMisc[bk],
            Number(DEFAULT_CONFIG_OPTIMIZER_DRAFT.misc_building_levels[bk])
          ) ?? 0
        )
      )
    );
  }

  const rawOptMax = get("optimized_building_max_levels", "OPTIMIZED_BUILDING_MAX_LEVELS", {}) || {};
  const optMax: Record<string, string> = {};
  for (const bk of OPTIMIZED_BUILDING_KEYS) {
    optMax[bk] = String(
      Math.max(
        1,
        Math.round(
          safeFiniteNumber(
            rawOptMax[bk],
            Number(DEFAULT_CONFIG_OPTIMIZER_DRAFT.optimized_building_max_levels[bk])
          ) ?? 100
        )
      )
    );
  }

  const paramSpecs = sanitizeSpecializationsMap(
    get("planet_specializations", "PLANET_SPECIALIZATIONS", {})
  );
  const mergedSpecs =
    persistedSpecializations !== undefined && persistedSpecializations !== null
      ? persistedSpecs
      : paramSpecs;

  const rawPreserve = get(
    "preserve_previous_specializations",
    "PRESERVE_PREVIOUS_SPECIALIZATIONS",
    true
  );
  const rawSwitchThresh = safeFiniteNumber(
    get("specialization_switch_threshold_pct", "SPECIALIZATION_SWITCH_THRESHOLD_PCT", 0.5),
    0.5
  );

  return {
    target_ship_key: String(get("target_ship_key", "TARGET_SHIP_KEY", "s16") || "s16"),
    production_month_days: String(
      safeFiniteNumber(get("production_month_days", "PRODUCTION_MONTH_DAYS", 30), 30) ?? 30
    ),
    research_percent_of_universe_max: String(
      safeFiniteNumber(
        get("research_percent_of_universe_max", "RESEARCH_PERCENT_OF_UNIVERSE_MAX", 100),
        100
      ) ?? 100
    ),
    warehouse_hours_of_production: String(
      safeFiniteNumber(
        get("warehouse_hours_of_production", "WAREHOUSE_HOURS_OF_PRODUCTION", 24),
        24
      ) ?? 24
    ),
    min_warehouse_level: String(
      Math.max(
        0,
        Math.round(
          safeFiniteNumber(get("min_warehouse_level", "MIN_WAREHOUSE_LEVEL", 0), 0) ?? 0
        )
      )
    ),
    central_planet_slot: String(
      Math.max(
        1,
        Math.round(safeFiniteNumber(get("central_planet_slot", "CENTRAL_PLANET_SLOT", 1), 1) ?? 1)
      )
    ),
    central_planet_min_warehouse_level: String(
      Math.max(
        0,
        Math.round(
          safeFiniteNumber(
            get("central_planet_min_warehouse_level", "CENTRAL_PLANET_MIN_WAREHOUSE_LEVEL", 15),
            15
          ) ?? 15
        )
      )
    ),
    preserve_previous_specializations: rawPreserve === undefined ? true : Boolean(rawPreserve),
    specialization_switch_threshold_pct: String(
      Math.max(0, Math.min(25, rawSwitchThresh ?? 0.5))
    ),
    planet_specializations: mergedSpecs,
    min_fleet_per_planet: minFleet,
    misc_building_levels: miscLevels,
    optimized_building_max_levels: optMax,
    allow_demolition: Boolean(get("allow_demolition", "ALLOW_DEMOLITION", false)),
    pad_generic_colonies: Boolean(get("pad_generic_colonies", "PAD_GENERIC_COLONIES", false)),
    ga_population_size: String(
      Math.max(
        4,
        Math.round(
          safeFiniteNumber(get("ga_population_size", "GA_POPULATION_SIZE", 200), 200) ?? 200
        )
      )
    ),
    ga_generations: String(
      Math.max(
        1,
        Math.round(safeFiniteNumber(get("ga_generations", "GA_GENERATIONS", 150), 150) ?? 150)
      )
    ),
    ga_random_seed: String(
      Math.round(
        safeFiniteNumber(get("ga_random_seed", "GA_RANDOM_SEED", 20261117), 20261117) ?? 20261117
      )
    ),
  };
}

export function buildParamsPayload(draft: ConfigOptimizerDraft): Record<string, any> {
  const num = (v: string, fb: number) => safeFiniteNumber(v, fb) ?? fb;
  const intNum = (v: string, fb: number, min = 0) => Math.max(min, Math.round(num(v, fb)));

  const minFleet: Record<string, number> = {};
  for (const sk of ["s2", "s10", "s11"]) {
    minFleet[sk] = intNum(draft.min_fleet_per_planet[sk] ?? "10", 10, 0);
  }
  const miscLevels: Record<string, number> = {};
  for (const bk of MISC_BUILDING_KEYS) {
    miscLevels[bk] = intNum(
      draft.misc_building_levels[bk] ?? DEFAULT_CONFIG_OPTIMIZER_DRAFT.misc_building_levels[bk],
      0,
      0
    );
  }
  const optMax: Record<string, number> = {};
  for (const bk of OPTIMIZED_BUILDING_KEYS) {
    optMax[bk] = intNum(draft.optimized_building_max_levels[bk] ?? "100", 100, 1);
  }
  return {
    target_ship_key: draft.target_ship_key || "s16",
    production_month_days: Math.max(1, num(draft.production_month_days, 30)),
    research_percent_of_universe_max: Math.max(
      0,
      Math.min(200, num(draft.research_percent_of_universe_max, 100))
    ),
    warehouse_hours_of_production: Math.max(0, num(draft.warehouse_hours_of_production, 24)),
    min_warehouse_level: Math.min(60, intNum(draft.min_warehouse_level, 0, 0)),
    central_planet_slot: intNum(draft.central_planet_slot, 1, 1),
    central_planet_min_warehouse_level: Math.min(
      60,
      intNum(draft.central_planet_min_warehouse_level, 15, 0)
    ),
    preserve_previous_specializations: Boolean(draft.preserve_previous_specializations),
    specialization_switch_threshold_pct: Math.max(
      0,
      Math.min(25, num(draft.specialization_switch_threshold_pct, 0.5))
    ),
    planet_specializations: sanitizeSpecializationsMap(draft.planet_specializations),
    min_fleet_per_planet: minFleet,
    misc_building_levels: miscLevels,
    optimized_building_max_levels: optMax,
    allow_demolition: Boolean(draft.allow_demolition),
    pad_generic_colonies: Boolean(draft.pad_generic_colonies),
    ga_population_size: intNum(draft.ga_population_size, 200, 4),
    ga_generations: intNum(draft.ga_generations, 150, 1),
    ga_random_seed: Math.round(num(draft.ga_random_seed, 20261117)),
  };
}

export function deriveConfigOptimizerSummary(opts: {
  draft: ConfigOptimizerDraft;
  activeResult: ConfigOptimizerResultData | null;
  optData: ConfigOptimizerGetPayload | null;
  activeJob: PlannerJobState | null;
  planetRows: NormalizedPlanetRow[];
}) {
  const { draft, activeResult, optData, activeJob, planetRows } = opts;
  const lockedSpecsCount = Object.keys(draft.planet_specializations).length;
  const summary = activeResult?.summary || {};
  const stickyPlanApplied = Boolean(summary.sticky_plan_applied);
  const rolesPreservedCount = Math.max(
    0,
    Math.round(safeFiniteNumber(summary.roles_preserved_count, 0) ?? 0)
  );
  const rolesChangedCount = Math.max(
    0,
    Math.round(safeFiniteNumber(summary.roles_changed_count, 0) ?? 0)
  );
  const shipKey = String(summary.target_ship_key || draft.target_ship_key || "s16");
  const shipsPerMonth = safeFiniteNumber(
    summary.ships_per_month ?? summary.whole_ships_per_month,
    null
  );
  const wholeShips = safeFiniteNumber(summary.whole_ships_per_month, null);
  const limitFactor = String(summary.bottleneck || summary.limiting_factor || "—");
  const totalFieldsUsed = safeFiniteNumber(
    summary.fields_used ?? summary.total_fields_used,
    null
  );
  const totalFieldsMax = safeFiniteNumber(
    summary.fields_max ??
      (planetRows.length > 0 ? planetRows.reduce((s, r) => s + r.fieldsMax, 0) : null),
    null
  );
  const whFields = safeFiniteNumber(summary.warehouse_fields ?? summary.warehouse_fields_used, null);
  const totalDemolishWhenFull = Math.max(
    0,
    Math.round(
      safeFiniteNumber(
        summary.demolitions_when_full,
        planetRows.reduce((s, r) => s + r.demolitionsWhenFull, 0)
      ) ?? 0
    )
  );
  const totalLevelsToDemolish = Math.max(
    0,
    Math.round(
      safeFiniteNumber(
        summary.levels_to_demolish,
        planetRows.reduce((s, r) => s + r.levelsToDemolish, 0)
      ) ?? 0
    )
  );
  const activeAllowDemolition = Boolean(
    summary.allow_demolition ??
      activeResult?.meta_parameters?.allow_demolition ??
      draft.allow_demolition
  );
  const elapsedMs = safeFiniteNumber(
    activeResult?.elapsed_ms ?? activeResult?.search?.elapsed_ms ?? activeJob?.elapsed_ms,
    null
  );
  const runTs = optData?.active?.updated_ts ?? activeResult?.generated_at;

  return {
    lockedSpecsCount,
    summary,
    stickyPlanApplied,
    rolesPreservedCount,
    rolesChangedCount,
    shipKey,
    shipsPerMonth,
    wholeShips,
    limitFactor,
    totalFieldsUsed,
    totalFieldsMax,
    whFields,
    totalDemolishWhenFull,
    totalLevelsToDemolish,
    activeAllowDemolition,
    elapsedMs,
    runTs,
  };
}
