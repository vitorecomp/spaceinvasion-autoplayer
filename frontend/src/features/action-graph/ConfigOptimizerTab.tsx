import React, { useCallback, useEffect, useMemo, useState } from "react";
import { feedbackKindForError, safeFiniteNumber } from "../../api/httpClient";
import { SortableTh } from "../../components/SortableTh";
import { TablePagination } from "../../components/TablePagination";
import { useConsole } from "../../context/ConsoleContext";
import { useTableSort, type SortColumnMap } from "../../hooks/useTableSort";
import type {
  ConfigOptimizerGetPayload,
  ConfigOptimizerPlanetRecord,
  ConfigOptimizerResultData,
  PlanetData,
  PlannerJobState,
} from "../../types/api";
import { fmtCompact, fmtInt, fmtShortDateTime } from "../../utils/formatters";
import { ActionGraphParamRow } from "./ActionGraphParamRow";
import {
  CONFIG_OPTIMIZER_GROUP_SUBTITLES,
  CONFIG_OPTIMIZER_MIN_FLEET_DOCS,
  CONFIG_OPTIMIZER_OPT_MAX_DOCS,
  CONFIG_OPTIMIZER_PARAM_DOCS,
  getConfigOptimizerMiscDoc,
} from "./paramDocs";
import {
  BUILDING_NAMES,
  BUILDING_SHORT_NAMES,
  ConfigOptimizerDraft,
  DEFAULT_CONFIG_OPTIMIZER_DRAFT,
  KEY_PLANET_BUILDINGS,
  MISC_BUILDING_KEYS,
  OPTIMIZED_BUILDING_KEYS,
  PLANET_SPECIALIZATION_OPTIONS,
  RESEARCH_KEYS,
  RESEARCH_NAMES,
  SHIP_OPTIONS,
  computeColonyGateInfo,
  downloadTextFile,
  formatDurationSecondsCompact,
  formatJobBadgeText,
  formatSpecializationLabel,
  normalizeSpecializationKey,
  shipNameForKey,
} from "./plannerHelpers";

const MAX_PLANET_NAME_LENGTH = 48;

interface RenamePlanetApiResponse {
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

function sanitizeCustomNamesMap(raw: unknown): Record<string, string> {
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

function validatePlanetRename(raw: string): { trimmed: string; error: string | null } {
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

interface NormalizedPlanetRow {
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
  buildingsDetail: Array<{
    key: string;
    name: string;
    built: number;
    target: number;
    toBuild: number;
    reserved: number;
    lastTimeText: string;
  }>;
}

interface ResearchPlanRow {
  key: string;
  name: string;
  current: number;
  universeMax: number;
  planned: number;
  gap: number;
}

function sanitizeSpecializationsMap(raw: unknown): Record<string, string> {
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

function planetSpecKeyForRow(row: Pick<NormalizedPlanetRow, "planetId" | "coords">): string {
  return row.planetId > 0 ? String(row.planetId) : row.coords;
}

function resolveLockedSpecForRow(
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

function draftFromParams(
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
      Math.max(0, Math.round(safeFiniteNumber(rawFleet[sk], Number(DEFAULT_CONFIG_OPTIMIZER_DRAFT.min_fleet_per_planet[sk])) ?? 10))
    );
  }

  const rawMisc = get("misc_building_levels", "MISC_BUILDING_LEVELS", {}) || {};
  const miscLevels: Record<string, string> = {};
  for (const bk of MISC_BUILDING_KEYS) {
    miscLevels[bk] = String(
      Math.max(
        0,
        Math.round(
          safeFiniteNumber(rawMisc[bk], Number(DEFAULT_CONFIG_OPTIMIZER_DRAFT.misc_building_levels[bk])) ?? 0
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

function buildParamsPayload(draft: ConfigOptimizerDraft): Record<string, any> {
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
    central_planet_slot: intNum(draft.central_planet_slot, 1, 1),
    central_planet_min_warehouse_level: intNum(draft.central_planet_min_warehouse_level, 15, 0),
    preserve_previous_specializations: Boolean(draft.preserve_previous_specializations),
    specialization_switch_threshold_pct: Math.max(
      0,
      Math.min(25, num(draft.specialization_switch_threshold_pct, 0.5))
    ),
    planet_specializations: sanitizeSpecializationsMap(draft.planet_specializations),
    min_fleet_per_planet: minFleet,
    misc_building_levels: miscLevels,
    optimized_building_max_levels: optMax,
    pad_generic_colonies: Boolean(draft.pad_generic_colonies),
    ga_population_size: intNum(draft.ga_population_size, 200, 4),
    ga_generations: intNum(draft.ga_generations, 150, 1),
    ga_random_seed: Math.round(num(draft.ga_random_seed, 20261117)),
  };
}

function normalizePlanetRows(
  result: ConfigOptimizerResultData | null | undefined,
  reservedFloorsMap: Record<string, Record<string, number>>,
  customPlanetNames: Record<string, string>,
  dashboardPlanets: PlanetData[]
): NormalizedPlanetRow[] {
  const rawPlanets: ConfigOptimizerPlanetRecord[] = Array.isArray(result?.planets)
    ? result!.planets!
    : [];
  const dashByPid = new Map<number, PlanetData>();
  for (const dp of dashboardPlanets) {
    const dpId = Math.round(safeFiniteNumber(dp?.planet_id, 0) ?? 0);
    if (dpId > 0) dashByPid.set(dpId, dp);
  }

  return rawPlanets.map((p, idx) => {
    const slot = Math.max(1, Math.round(safeFiniteNumber(p.slot ?? p.slot_index, idx + 1) ?? idx + 1));
    const pid = Math.round(safeFiniteNumber(p.planet_id, 0) ?? 0);
    const coords = String(p.coords || `Slot ${slot}`);
    const isPlaceholder = Boolean(
      p.is_placeholder ?? (String(p.planet_status || "").toLowerCase() === "generic")
    );
    const dashPlanet = !isPlaceholder && pid > 0 ? dashByPid.get(pid) : undefined;
    const isCentral = Boolean(p.is_central_planet);
    const temp = safeFiniteNumber(p.temperature ?? p.temperature_c, null);
    const fieldsMax = Math.max(0, Math.round(safeFiniteNumber(p.fields_max, 300) ?? 300));
    const fieldsUsedTarget = Math.max(
      0,
      Math.round(safeFiniteNumber(p.fields_used_target ?? p.fields_used, 0) ?? 0)
    );

    const builtLevels: Record<string, number> = {};
    const targetLevels: Record<string, number> = {};
    const buildingsDetail: NormalizedPlanetRow["buildingsDetail"] = [];

    const rawBuilt = p.built_floors && typeof p.built_floors === "object" ? p.built_floors : {};
    const rawRes =
      (p.reserved_floors && typeof p.reserved_floors === "object" ? p.reserved_floors : null) ||
      reservedFloorsMap[String(pid)] ||
      reservedFloorsMap[coords] ||
      {};
    const reservedFloors: Record<string, number> = {};
    for (const [bk, rv] of Object.entries(rawRes)) {
      const n = Math.round(safeFiniteNumber(rv, 0) ?? 0);
      if (n > 0) reservedFloors[bk] = n;
    }

    let maxLastSec: number | null = null;
    let maxLastTxt = "";
    let builtSum = 0;

    if (Array.isArray(p.buildings)) {
      for (const b of p.buildings) {
        const bk = String(b.key || b.building_key || "");
        if (!bk) continue;
        const built = Math.max(
          0,
          Math.round(safeFiniteNumber(b.built_level ?? b.current_level ?? rawBuilt[bk], 0) ?? 0)
        );
        const target = Math.max(
          built,
          Math.round(safeFiniteNumber(b.target_level ?? b.level ?? built, built) ?? built)
        );
        const toBuild = Math.max(0, Math.round(safeFiniteNumber(b.levels_to_build, target - built) ?? target - built));
        const resLvl = reservedFloors[bk] || 0;
        builtLevels[bk] = built;
        targetLevels[bk] = target;
        builtSum += built;
        const ltSec = safeFiniteNumber(b.last_level_seconds ?? b.last_level_time_s, null);
        const ltText = String(
          b.last_level_time || b.last_level_build_time || (ltSec !== null ? formatDurationSecondsCompact(ltSec) : "—")
        );
        if (toBuild > 0 && ltSec !== null && (maxLastSec === null || ltSec > maxLastSec)) {
          maxLastSec = ltSec;
          maxLastTxt = ltText;
        } else if (toBuild > 0 && !maxLastTxt && ltText && ltText !== "—") {
          maxLastTxt = ltText;
        }
        buildingsDetail.push({
          key: bk,
          name: String(b.name || BUILDING_NAMES[bk] || bk),
          built,
          target,
          toBuild,
          reserved: resLvl,
          lastTimeText: ltText,
        });
      }
    } else if (p.buildings && typeof p.buildings === "object") {
      for (const bk of Object.keys(BUILDING_NAMES)) {
        const entry = (p.buildings as Record<string, any>)[bk];
        const built = Math.max(
          0,
          Math.round(
            safeFiniteNumber(
              typeof entry === "object" && entry ? entry.built_level ?? entry.current_level : rawBuilt[bk],
              0
            ) ?? 0
          )
        );
        const target = Math.max(
          built,
          Math.round(
            safeFiniteNumber(
              typeof entry === "object" && entry ? entry.target_level ?? entry.level : entry,
              built
            ) ?? built
          )
        );
        const resLvl = reservedFloors[bk] || 0;
        builtLevels[bk] = built;
        targetLevels[bk] = target;
        builtSum += built;
        buildingsDetail.push({
          key: bk,
          name: BUILDING_NAMES[bk] || bk,
          built,
          target,
          toBuild: Math.max(0, target - built),
          reserved: resLvl,
          lastTimeText:
            typeof entry === "object" && entry?.last_level_time
              ? String(entry.last_level_time)
              : "—",
        });
      }
    }

    let whDriver = "—";
    if (Array.isArray(p.warehouses) && p.warehouses.length > 0) {
      const topWh = [...p.warehouses].sort(
        (a, b) =>
          (safeFiniteNumber(b.level ?? b.target_level, 0) ?? 0) -
          (safeFiniteNumber(a.level ?? a.target_level, 0) ?? 0)
      )[0];
      if (topWh) {
        const whKey = String(topWh.key || topWh.building_key || topWh.resource || "");
        const whLvl = Math.round(safeFiniteNumber(topWh.level ?? topWh.target_level, 0) ?? 0);
        const whSetBy = String(topWh.set_by || topWh.driver || "");
        whDriver = whSetBy ? `${whKey} L${whLvl} (${whSetBy})` : `${whKey} L${whLvl}`;
      }
    } else if (p.warehouses && typeof p.warehouses === "object") {
      const wObj = p.warehouses as Record<string, any>;
      if (wObj.driver) whDriver = String(wObj.driver);
    }

    const prod = p.production && typeof p.production === "object" ? p.production : {};
    const monthlyShips = safeFiniteNumber(
      prod.monthly_ships ?? prod.ships_per_month ?? prod.target_ships_per_month,
      null
    );
    const monthlySpiceEq = safeFiniteNumber(
      prod.monthly_spice_eq ?? prod.spice_eq_per_month ?? prod.spice_eq_per_hr,
      null
    );

    const detectedSpec = String(p.detected_specialization || "").trim().toLowerCase() || "balanced";
    const lockedSpec = normalizeSpecializationKey(p.locked_specialization);
    const prevSpec =
      p.previous_specialization && String(p.previous_specialization).trim()
        ? String(p.previous_specialization).trim().toLowerCase()
        : null;
    const specChanged = Boolean(p.specialization_changed);

    const customName =
      !isPlaceholder && pid > 0 ? String(customPlanetNames[String(pid)] || "").trim() : "";
    const rawGameName = String(
      (p as Record<string, any>).game_name ||
        (dashPlanet as Record<string, any> | undefined)?.game_name ||
        ""
    ).trim();
    const resolvedName =
      customName ||
      String(p.name || dashPlanet?.name || (isPlaceholder ? `Preview Slot ${slot}` : coords));
    const gameName =
      rawGameName ||
      (!customName ? resolvedName : String(dashPlanet?.name || p.name || coords));

    return {
      slot,
      planetId: pid,
      name: resolvedName,
      gameName,
      hasCustomName: Boolean(customName),
      coords,
      isPlaceholder,
      isCentral,
      temperature: temp !== null ? Math.round(temp) : null,
      fieldsUsedCurrent: builtSum > 0 ? builtSum : null,
      fieldsUsedTarget,
      fieldsMax,
      detectedSpecialization: detectedSpec,
      lockedSpecialization: lockedSpec,
      previousSpecialization: prevSpec,
      specializationChanged: specChanged,
      warehouseDriver: whDriver,
      lastLevelTimeSec: maxLastSec,
      lastLevelTimeText: maxLastTxt || "—",
      monthlyShips,
      monthlySpiceEq,
      builtLevels,
      targetLevels,
      reservedFloors,
      buildingsDetail,
    };
  });
}

export interface ConfigOptimizerTabProps {
  hidden?: boolean;
}

export const ConfigOptimizerTab: React.FC<ConfigOptimizerTabProps> = ({ hidden = false }) => {
  const {
    dashboard,
    setDashboard,
    activePage,
    apiOnline,
    plannerJobsStatus,
    setPlannerJobsStatus,
    apiCommand,
    loadDashboard,
    showFeedback,
  } = useConsole();

  const [optData, setOptData] = useState<ConfigOptimizerGetPayload | null>(null);
  const [draft, setDraft] = useState<ConfigOptimizerDraft>(() =>
    draftFromParams(DEFAULT_CONFIG_OPTIMIZER_DRAFT)
  );
  const [draftDirty, setDraftDirty] = useState<boolean>(false);
  const [paramSubTab, setParamSubTab] = useState<"core" | "buildings">("core");
  const [subView, setSubView] = useState<"planets" | "research">("planets");
  const [selectedSlot, setSelectedSlot] = useState<number | null>(null);
  const [planetPage, setPlanetPage] = useState<number>(1);
  const [planetPageSize, setPlanetPageSize] = useState<number>(25);
  const [researchPage, setResearchPage] = useState<number>(1);
  const [researchPageSize, setResearchPageSize] = useState<number>(25);
  const [isSubmitting, setIsSubmitting] = useState<boolean>(false);
  const [editingPlanetId, setEditingPlanetId] = useState<number | null>(null);
  const [renameDraft, setRenameDraft] = useState<string>("");
  const [renamingPlanetId, setRenamingPlanetId] = useState<number | null>(null);
  const [renameError, setRenameError] = useState<string | null>(null);

  const fetchOptimizerState = useCallback(
    async ({ silent = false }: { silent?: boolean } = {}) => {
      try {
        const res = await apiCommand<ConfigOptimizerGetPayload>({
          command: "config_optimizer_get",
        });
        if (res && typeof res === "object") {
          setOptData(res);
          if (!draftDirty) {
            const rawParams =
              res.active?.params ||
              res.active?.result?.meta_parameters ||
              res.default_params ||
              null;
            const persistedSpecs =
              res.planet_specializations ||
              res.active?.params?.planet_specializations ||
              null;
            if (rawParams || persistedSpecs) {
              setDraft(draftFromParams(rawParams, persistedSpecs));
            }
          }
          if (res.job && plannerJobsStatus) {
            setPlannerJobsStatus({
              ...plannerJobsStatus,
              config_optimizer_job: res.job,
            });
          }
        }
      } catch (err: any) {
        if (!silent) {
          showFeedback(`Config Optimizer load failed: ${err.message}`, feedbackKindForError(err));
        }
      }
    },
    [apiCommand, draftDirty, plannerJobsStatus, setPlannerJobsStatus, showFeedback]
  );

  useEffect(() => {
    if (!hidden && activePage === "action-graph" && !optData) {
      void fetchOptimizerState({ silent: true });
    }
  }, [activePage, apiOnline, fetchOptimizerState, hidden, optData]);

  const activeJob: PlannerJobState | null =
    optData?.job || plannerJobsStatus?.config_optimizer_job || null;
  const isJobRunning = String(activeJob?.status || "").toLowerCase() === "running";

  useEffect(() => {
    if (!isJobRunning || !apiOnline || hidden) return;
    const timer = setInterval(() => {
      void fetchOptimizerState({ silent: true });
    }, 1500);
    return () => clearInterval(timer);
  }, [apiOnline, fetchOptimizerState, hidden, isJobRunning]);

  const activeResult: ConfigOptimizerResultData | null = useMemo(() => {
    if (!optData?.active) return null;
    if (optData.active.result && typeof optData.active.result === "object") {
      return optData.active.result;
    }
    if ("planets" in optData.active || "summary" in optData.active) {
      return optData.active as unknown as ConfigOptimizerResultData;
    }
    return null;
  }, [optData?.active]);

  const reservedFloorsMap = useMemo<Record<string, Record<string, number>>>(() => {
    return (
      optData?.reserved_floors ||
      optData?.active?.reserved_floors ||
      {}
    );
  }, [optData]);

  const dashboardPlanets = useMemo<PlanetData[]>(
    () => (Array.isArray(dashboard?.state?.planets) ? dashboard!.state!.planets! : []),
    [dashboard?.state?.planets]
  );

  const customPlanetNames = useMemo<Record<string, string>>(() => {
    const dashMap = (dashboard as Record<string, any> | null | undefined)?.custom_planet_names;
    const optMap = (optData as Record<string, any> | null | undefined)?.custom_planet_names;
    if (dashMap && typeof dashMap === "object") {
      return sanitizeCustomNamesMap(dashMap);
    }
    if (optMap && typeof optMap === "object") {
      return sanitizeCustomNamesMap(optMap);
    }
    return {};
  }, [dashboard, optData]);

  const ownedDashboardPlanets = useMemo(() => {
    return dashboardPlanets
      .filter((p) => {
        const pid = Math.round(safeFiniteNumber(p?.planet_id, 0) ?? 0);
        const ptype = String(p?.planet_type || "normal").toLowerCase();
        return pid > 0 && ptype === "normal";
      })
      .map((p, idx) => {
        const pid = Math.round(safeFiniteNumber(p.planet_id, 0) ?? 0);
        const coords = String(p.coords || `Planet ${pid}`);
        const customName = customPlanetNames[String(pid)] || "";
        const rawGameName = String((p as Record<string, any>).game_name || "").trim();
        const displayName = customName || String(p.name || coords);
        const gameName = rawGameName || (!customName ? displayName : String(p.name || coords));
        return {
          slot: idx + 1,
          planetId: pid,
          name: displayName,
          gameName,
          hasCustomName: Boolean(customName),
          coords,
        };
      });
  }, [customPlanetNames, dashboardPlanets]);

  const planetRows = useMemo(
    () =>
      normalizePlanetRows(
        activeResult,
        reservedFloorsMap,
        customPlanetNames,
        dashboardPlanets
      ),
    [activeResult, customPlanetNames, dashboardPlanets, reservedFloorsMap]
  );

  const planetSortCols = useMemo<
    SortColumnMap<
      NormalizedPlanetRow,
      "planet" | "spec" | "temp" | "fields" | "time" | "output"
    >
  >(
    () => ({
      planet: (r) => r.slot,
      spec: (r) => r.detectedSpecialization,
      temp: { accessor: (r) => r.temperature, defaultDirection: "desc" },
      fields: { accessor: (r) => r.fieldsUsedTarget, defaultDirection: "desc" },
      time: { accessor: (r) => r.lastLevelTimeSec, defaultDirection: "desc" },
      output: {
        accessor: (r) => r.monthlyShips ?? r.monthlySpiceEq,
        defaultDirection: "desc",
      },
    }),
    []
  );
  const planetSort = useTableSort(planetRows, planetSortCols);
  const pagedPlanets = useMemo(() => {
    const start = (planetPage - 1) * planetPageSize;
    return planetSort.sortedRows.slice(start, start + planetPageSize);
  }, [planetPage, planetPageSize, planetSort.sortedRows]);

  const researchRows = useMemo<ResearchPlanRow[]>(() => {
    const ownMap =
      activeResult?.inputs?.own_research_levels || dashboard?.state?.researches || {};
    const uniMap =
      activeResult?.inputs?.universe_research_max_levels ||
      Object.fromEntries(
        (dashboard?.universe_research_max || []).map((r) => [
          String(r.key || r.research_key || `r${r.research_id}`),
          r.universe_max_level,
        ])
      );
    const plannedRaw = activeResult?.planned_research_levels;
    const plannedMap: Record<string, number> = {};
    if (Array.isArray(plannedRaw)) {
      for (const item of plannedRaw) {
        const k = String(item?.key || item?.research_key || "");
        if (k) plannedMap[k] = Math.round(safeFiniteNumber(item.target_level ?? item.level, 0) ?? 0);
      }
    } else if (plannedRaw && typeof plannedRaw === "object") {
      for (const [k, v] of Object.entries(plannedRaw)) {
        plannedMap[k] = Math.round(safeFiniteNumber(v, 0) ?? 0);
      }
    }

    return RESEARCH_KEYS.map((rk) => {
      const cur = Math.max(0, Math.round(safeFiniteNumber(ownMap[rk], 0) ?? 0));
      const uni = Math.max(0, Math.round(safeFiniteNumber(uniMap[rk], cur) ?? cur));
      const pln = Math.max(cur, Math.round(safeFiniteNumber(plannedMap[rk], cur) ?? cur));
      return {
        key: rk,
        name: RESEARCH_NAMES[rk] || rk,
        current: cur,
        universeMax: uni,
        planned: pln,
        gap: Math.max(0, pln - cur),
      };
    });
  }, [activeResult, dashboard?.state?.researches, dashboard?.universe_research_max]);

  const researchSortCols = useMemo<
    SortColumnMap<ResearchPlanRow, "name" | "current" | "uniMax" | "planned" | "gap">
  >(
    () => ({
      name: (r) => r.name,
      current: { accessor: (r) => r.current, defaultDirection: "desc" },
      uniMax: { accessor: (r) => r.universeMax, defaultDirection: "desc" },
      planned: { accessor: (r) => r.planned, defaultDirection: "desc" },
      gap: { accessor: (r) => r.gap, defaultDirection: "desc" },
    }),
    []
  );
  const researchSort = useTableSort(researchRows, researchSortCols);
  const pagedResearch = useMemo(() => {
    const start = (researchPage - 1) * researchPageSize;
    return researchSort.sortedRows.slice(start, start + researchPageSize);
  }, [researchPage, researchPageSize, researchSort.sortedRows]);

  const selectedPlanetDetail = useMemo(
    () =>
      planetRows.find((p) => p.slot === selectedSlot) ||
      (planetRows.length > 0 ? planetRows[0] : null),
    [planetRows, selectedSlot]
  );

  const onRunOptimizer = async () => {
    setIsSubmitting(true);
    try {
      const payload = buildParamsPayload(draft);
      showFeedback("Starting Config Optimization (Monthly Target & Floors)...", "ok");
      const res = await apiCommand({
        command: "config_optimizer_run",
        params: payload,
        auto_replan_ai: true,
      });
      setDraftDirty(false);
      if (res.result) {
        setOptData((prev) => ({
          ...(prev || {}),
          job: res.job || { status: "completed", progress_pct: 100 },
          active: {
            ...(prev?.active || {}),
            params: payload,
            result: res.result,
          },
        }));
      } else {
        await fetchOptimizerState({ silent: true });
      }
      if ((res.job || res.ai_planner_job) && plannerJobsStatus) {
        setPlannerJobsStatus({
          ...plannerJobsStatus,
          config_optimizer_job: res.job || plannerJobsStatus.config_optimizer_job,
          ai_planner_job: res.ai_planner_job || plannerJobsStatus.ai_planner_job,
        });
      }
      const st = String(res.job?.status || "completed").toLowerCase();
      showFeedback(
        st === "running"
          ? "Config Optimization running in background — progress updates live."
          : "Config Optimization completed and queued AI-Planner replan.",
        "ok"
      );
    } catch (err: any) {
      showFeedback(`Config Optimization failed: ${err.message}`, feedbackKindForError(err));
    } finally {
      setIsSubmitting(false);
    }
  };

  const onExportJson = async () => {
    try {
      const res = await apiCommand({ command: "config_optimizer_export" });
      const filename = String(res.filename || "config_optimizer_result.json");
      const content =
        typeof res.content === "string"
          ? res.content
          : JSON.stringify(res.result ?? activeResult ?? {}, null, 2);
      downloadTextFile(filename, content, "application/json");
      showFeedback(`Exported ${filename}.`, "ok");
    } catch (err: any) {
      if (activeResult) {
        downloadTextFile(
          "config_optimizer_result.json",
          JSON.stringify(activeResult, null, 2),
          "application/json"
        );
        showFeedback("Exported cached config_optimizer_result.json.", "ok");
      } else {
        showFeedback(`Export JSON failed: ${err.message}`, feedbackKindForError(err));
      }
    }
  };

  const onResetDefaults = () => {
    setDraftDirty(false);
    setDraft(draftFromParams(optData?.default_params || null, {}));
    showFeedback("Reset Config Optimizer inputs to defaults.", "ok");
  };

  const persistSpecializations = async (
    nextSpecs: Record<string, string>,
    successMsg: string
  ) => {
    const cleaned = sanitizeSpecializationsMap(nextSpecs);
    setDraft((prev) => ({ ...prev, planet_specializations: cleaned }));
    setDraftDirty(true);
    if (!apiOnline) {
      showFeedback(`${successMsg} (staged locally; will apply on next run).`, "ok");
      return;
    }
    try {
      const res = await apiCommand({
        command: "config_optimizer_set_specializations",
        planet_specializations: cleaned,
        replace: true,
      });
      const persisted = sanitizeSpecializationsMap(res?.planet_specializations ?? cleaned);
      setDraft((prev) => ({ ...prev, planet_specializations: persisted }));
      setOptData((prev) =>
        prev
          ? {
              ...prev,
              planet_specializations: persisted,
              active: prev.active
                ? {
                    ...prev.active,
                    params: {
                      ...(prev.active.params || {}),
                      planet_specializations: persisted,
                    },
                  }
                : prev.active,
            }
          : prev
      );
      showFeedback(successMsg, "ok");
    } catch (err: any) {
      showFeedback(
        `Specialization lock staged locally (API save failed: ${err.message}).`,
        feedbackKindForError(err)
      );
    }
  };

  const onPlanetSpecChange = async (row: NormalizedPlanetRow, rawValue: string) => {
    const nextRole = normalizeSpecializationKey(rawValue);
    const nextSpecs: Record<string, string> = { ...draft.planet_specializations };
    if (row.planetId > 0) delete nextSpecs[String(row.planetId)];
    if (row.coords) delete nextSpecs[row.coords];
    delete nextSpecs[`slot:${row.slot}`];
    const targetKey = planetSpecKeyForRow(row);
    if (nextRole !== "auto") {
      nextSpecs[targetKey] = nextRole;
    }
    const label = formatSpecializationLabel(nextRole);
    await persistSpecializations(
      nextSpecs,
      nextRole === "auto"
        ? `Unlocked ${row.name} [${row.coords}] to Auto specialization.`
        : `Locked ${row.name} [${row.coords}] specialization to ${label}. Click Run Config Optimizer to re-solve.`
    );
  };

  const onLockAllCurrentRoles = async () => {
    const nextSpecs: Record<string, string> = {};
    let locked = 0;
    for (const row of planetRows) {
      const detected = normalizeSpecializationKey(row.detectedSpecialization);
      if (detected !== "auto") {
        nextSpecs[planetSpecKeyForRow(row)] = detected;
        locked += 1;
      }
    }
    if (locked === 0) {
      showFeedback("No detected planet specializations to lock yet — run Config Optimizer first.", "warn");
      return;
    }
    await persistSpecializations(
      nextSpecs,
      `Locked ${locked} planet specialization${locked === 1 ? "" : "s"} to current roles.`
    );
  };

  const onUnlockAllRoles = async () => {
    await persistSpecializations({}, "Unlocked all planet specializations back to Auto.");
  };

  const startRenamingPlanet = (planetId: number, currentName: string) => {
    setEditingPlanetId(planetId);
    setRenameDraft(currentName);
    setRenameError(null);
  };

  const cancelRenamingPlanet = () => {
    setEditingPlanetId(null);
    setRenameDraft("");
    setRenameError(null);
  };

  const submitPlanetRename = async (
    planetId: number,
    rawName: string,
    fallbackGameName?: string
  ) => {
    if (planetId <= 0) return;
    const { trimmed, error } = validatePlanetRename(rawName);
    if (error) {
      setRenameError(error);
      return;
    }

    setRenamingPlanetId(planetId);
    setRenameError(null);
    try {
      const res = await apiCommand<RenamePlanetApiResponse>({
        command: "rename_planet",
        planet_id: planetId,
        name: trimmed,
      });

      const nextCustomNames: Record<string, string> =
        res && res.custom_planet_names && typeof res.custom_planet_names === "object"
          ? sanitizeCustomNamesMap(res.custom_planet_names)
          : (() => {
              const copy = { ...customPlanetNames };
              if (trimmed) {
                copy[String(planetId)] = trimmed;
              } else {
                delete copy[String(planetId)];
              }
              return copy;
            })();

      const cleared = Boolean(res?.cleared) || !trimmed;
      const resolvedGameName = String(
        res?.game_name || res?.planet?.game_name || fallbackGameName || ""
      ).trim();
      const resolvedDisplayName = String(
        res?.planet?.name ||
          (!cleared && res?.name ? res.name : "") ||
          trimmed ||
          resolvedGameName ||
          `Planet ${planetId}`
      ).trim();

      setOptData((prev) => {
        if (!prev) return prev;
        const patchList = (list: unknown): ConfigOptimizerPlanetRecord[] | undefined => {
          if (!Array.isArray(list)) return undefined;
          return list.map((entry: any) => {
            if (Math.round(safeFiniteNumber(entry?.planet_id, 0) ?? 0) !== planetId) {
              return entry;
            }
            return {
              ...entry,
              name: resolvedDisplayName,
              game_name: resolvedGameName || entry.game_name,
            };
          });
        };
        const nextActive = prev.active
          ? {
              ...prev.active,
              ...(prev.active.result
                ? {
                    result: {
                      ...prev.active.result,
                      planets: patchList(prev.active.result.planets) ?? prev.active.result.planets,
                    },
                  }
                : {}),
              ...(Array.isArray((prev.active as Record<string, any>).planets)
                ? { planets: patchList((prev.active as Record<string, any>).planets) }
                : {}),
            }
          : prev.active;

        return {
          ...prev,
          custom_planet_names: nextCustomNames,
          active: nextActive,
        } as ConfigOptimizerGetPayload;
      });

      const patchDashboardObj = (prevDash: any) => {
        if (!prevDash || typeof prevDash !== "object") return prevDash;
        const prevPlanets = Array.isArray(prevDash.state?.planets) ? prevDash.state.planets : null;
        const nextPlanets = prevPlanets
          ? prevPlanets.map((pl: any) => {
              if (Math.round(safeFiniteNumber(pl?.planet_id, 0) ?? 0) !== planetId) {
                return pl;
              }
              return {
                ...pl,
                name: resolvedDisplayName,
                game_name: resolvedGameName || pl.game_name,
              };
            })
          : prevDash.state?.planets;
        const prevActivePl = prevDash.state?.active_planet;
        const nextActivePl =
          prevActivePl && Math.round(safeFiniteNumber(prevActivePl.planet_id, 0) ?? 0) === planetId
            ? {
                ...prevActivePl,
                name: resolvedDisplayName,
                game_name: resolvedGameName || prevActivePl.game_name,
              }
            : prevActivePl;
        return {
          ...prevDash,
          custom_planet_names: nextCustomNames,
          state: prevDash.state
            ? {
                ...prevDash.state,
                planets: nextPlanets,
                ...(prevActivePl ? { active_planet: nextActivePl } : {}),
              }
            : prevDash.state,
        };
      };

      setDashboard((prev) => patchDashboardObj(prev));
      const gState = (globalThis as any).state;
      if (gState?.dashboard) {
        gState.dashboard = patchDashboardObj(gState.dashboard);
      }

      setEditingPlanetId(null);
      setRenameDraft("");
      setRenameError(null);
      showFeedback(
        String(
          res?.message ||
            (cleared
              ? `Reset planet name to "${resolvedDisplayName}".`
              : `Renamed planet to "${resolvedDisplayName}".`)
        ),
        "ok"
      );

      await Promise.all([
        fetchOptimizerState({ silent: true }),
        loadDashboard({ refresh: false, preserveConfig: true, force: true }),
      ]);
    } catch (err: any) {
      const msg = String(err?.message || "Failed to rename planet.");
      setRenameError(msg);
      showFeedback(`Planet rename failed: ${msg}`, feedbackKindForError(err));
    } finally {
      setRenamingPlanetId(null);
    }
  };

  const renderInlineRenameEditor = (
    planetId: number,
    coords: string,
    gameName: string
  ) => {
    const isSavingThis = renamingPlanetId === planetId;
    return (
      <div
        className="ag-co-rename-form"
        onClick={(e) => e.stopPropagation()}
      >
        <input
          type="text"
          id={`co-rename-input-${planetId}`}
          className="ag-co-rename-input"
          aria-label={`New name for planet ${coords}`}
          maxLength={64}
          value={renameDraft}
          placeholder={gameName || coords}
          autoFocus
          disabled={isSavingThis}
          onChange={(e) => {
            setRenameDraft(e.target.value);
            if (renameError) setRenameError(null);
          }}
          onKeyDown={(e) => {
            if (e.key === "Enter") {
              e.preventDefault();
              void submitPlanetRename(planetId, renameDraft, gameName);
            } else if (e.key === "Escape") {
              e.preventDefault();
              cancelRenamingPlanet();
            }
          }}
        />
        <div className="ag-co-rename-actions">
          <button
            type="button"
            className="btn btn-primary btn-sm"
            id={`btn-co-rename-save-${planetId}`}
            disabled={isSavingThis}
            onClick={() => void submitPlanetRename(planetId, renameDraft, gameName)}
          >
            {isSavingThis ? "Saving..." : "Save"}
          </button>
          <button
            type="button"
            className="btn btn-secondary btn-sm"
            id={`btn-co-rename-reset-${planetId}`}
            title="Clear custom name and restore in-game planet name"
            disabled={isSavingThis}
            onClick={() => void submitPlanetRename(planetId, "", gameName)}
          >
            Reset
          </button>
          <button
            type="button"
            className="btn btn-secondary btn-sm"
            id={`btn-co-rename-cancel-${planetId}`}
            disabled={isSavingThis}
            onClick={cancelRenamingPlanet}
          >
            Cancel
          </button>
        </div>
        {renameError ? (
          <div
            className="ag-co-rename-error"
            id={`co-rename-error-${planetId}`}
            role="alert"
          >
            {renameError}
          </div>
        ) : null}
      </div>
    );
  };

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
  const gate = computeColonyGateInfo(dashboard, plannerJobsStatus, optData);
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
  const elapsedMs = safeFiniteNumber(
    activeResult?.elapsed_ms ?? activeResult?.search?.elapsed_ms ?? activeJob?.elapsed_ms,
    null
  );
  const runTs = optData?.active?.updated_ts ?? activeResult?.generated_at;

  return (
    <div className="ag-tab-split" id="ag-pane-config-optimizer" hidden={hidden}>
      <article className="hud-window ag-main-window" id="ag-co-main-window">
        <div className="window-bar window-bar-wrap">
          <div className="window-title-group">
            <span className="module-code tabular">OPT</span>
            <h2>Config Optimization — Monthly Target &amp; Hard Built Floors</h2>
            <span
              className={`ag-status-pill tabular ${isJobRunning ? "is-sim" : activeResult ? "is-live" : ""}`}
              id="co-job-status-pill"
              role="status"
            >
              {formatJobBadgeText(activeJob)}
            </span>
          </div>

          <div className="inline-actions">
            <div className="tab-group" role="tablist" aria-label="Config optimizer tables">
              <button
                type="button"
                role="tab"
                aria-selected={subView === "planets"}
                className={`tab-btn${subView === "planets" ? " active" : ""}`}
                id="co-subtab-planets"
                onClick={() => setSubView("planets")}
              >
                {`Planets (${planetRows.length})`}
              </button>
              <button
                type="button"
                role="tab"
                aria-selected={subView === "research"}
                className={`tab-btn${subView === "research" ? " active" : ""}`}
                id="co-subtab-research"
                onClick={() => setSubView("research")}
              >
                {`Research (${researchRows.length})`}
              </button>
            </div>
            <button
              type="button"
              className="btn btn-secondary btn-sm"
              id="btn-co-export"
              onClick={onExportJson}
            >
              Export JSON
            </button>
            <button
              type="button"
              className="btn btn-secondary btn-sm"
              id="btn-co-refresh"
              onClick={() => fetchOptimizerState({ silent: false })}
            >
              Refresh
            </button>
          </div>
        </div>

        <div className="ag-kpi-strip" aria-label="Config optimization summary metrics">
          <div className="ag-kpi-cell">
            <span className="ag-kpi-label">Target Ships / Month</span>
            <strong className="ag-kpi-val tabular" id="co-kpi-ships">
              {shipsPerMonth !== null
                ? `${shipsPerMonth.toFixed(2)} ${shipKey}/mo`
                : `— ${shipKey}/mo`}
            </strong>
            <span className="ag-kpi-sub tabular">
              {wholeShips !== null
                ? `${fmtInt(Math.round(wholeShips))} whole · ${shipNameForKey(shipKey)}`
                : shipNameForKey(shipKey)}
            </span>
          </div>
          <div className="ag-kpi-cell">
            <span className="ag-kpi-label">Limiting Factor</span>
            <strong className="ag-kpi-val tabular" id="co-kpi-limit">
              {limitFactor}
            </strong>
            <span className="ag-kpi-sub tabular">
              {summary.investment_in_months_of_output != null
                ? `Payback ~${Number(summary.investment_in_months_of_output).toFixed(1)} mo`
                : `Colonies ${gate.coloniesOwned}/${gate.maxPlanets}`}
            </span>
          </div>
          <div className="ag-kpi-cell">
            <span className="ag-kpi-label">Total &amp; Warehouse Fields</span>
            <strong className="ag-kpi-val tabular" id="co-kpi-fields">
              {totalFieldsUsed !== null
                ? `${fmtInt(Math.round(totalFieldsUsed))} / ${totalFieldsMax !== null ? fmtInt(Math.round(totalFieldsMax)) : "—"}`
                : "— / —"}
            </strong>
            <span className="ag-kpi-sub tabular">
              {whFields !== null
                ? `Warehouse fields: ${fmtInt(Math.round(whFields))}`
                : "Hard built floors enforced"}
            </span>
          </div>
          <div className="ag-kpi-cell">
            <span className="ag-kpi-label">Last Run &amp; Elapsed</span>
            <strong className="ag-kpi-val tabular" id="co-kpi-elapsed">
              {elapsedMs !== null ? `${Math.round(elapsedMs)} ms` : "Not Run"}
            </strong>
            <span className="ag-kpi-sub tabular">
              {typeof runTs === "number"
                ? fmtShortDateTime(runTs)
                : typeof runTs === "string" && runTs
                  ? runTs.slice(0, 19).replace("T", " ")
                  : "Click Run Config Optimizer"}
            </span>
          </div>
        </div>

        <div className="ag-co-content" hidden={subView !== "planets"}>
          <div className="ag-co-spec-bar" id="co-spec-toolbar">
            <div className="ag-co-spec-summary tabular">
              <span className={`ag-co-spec-badge${lockedSpecsCount > 0 ? " is-locked" : ""}`}>
                {`Role Locks: ${lockedSpecsCount}/${planetRows.length}`}
              </span>
              {stickyPlanApplied ? (
                <span className="ag-co-spec-badge is-sticky">
                  {`Sticky Plan Kept (${rolesPreservedCount} role${rolesPreservedCount === 1 ? "" : "s"} preserved)`}
                </span>
              ) : rolesChangedCount > 0 ? (
                <span className="ag-co-spec-badge is-changed">
                  {`${rolesChangedCount} role${rolesChangedCount === 1 ? "" : "s"} switched`}
                </span>
              ) : (
                <span className="ag-toolbar-meta">
                  Lock any colony role below to guarantee it never flips on future optimizer runs.
                </span>
              )}
            </div>
            <div className="inline-actions">
              <button
                type="button"
                className="btn btn-secondary btn-sm"
                id="btn-co-lock-all"
                disabled={planetRows.length === 0 || isSubmitting}
                onClick={() => void onLockAllCurrentRoles()}
              >
                Lock Current Roles
              </button>
              <button
                type="button"
                className="btn btn-secondary btn-sm"
                id="btn-co-unlock-all"
                disabled={lockedSpecsCount === 0 || isSubmitting}
                onClick={() => void onUnlockAllRoles()}
              >
                Unlock All
              </button>
            </div>
          </div>

          <div className="table-wrap table-scroll">
            <table id="co-planets-table">
              <thead>
                <tr>
                  <SortableTh
                    label="Planet"
                    sortKey="planet"
                    activeKey={planetSort.sortKey}
                    direction={planetSort.sortDirection}
                    onSort={planetSort.requestSort}
                  />
                  <SortableTh
                    label="Specialization"
                    sortKey="spec"
                    activeKey={planetSort.sortKey}
                    direction={planetSort.sortDirection}
                    onSort={planetSort.requestSort}
                  />
                  <SortableTh
                    label="Temp"
                    sortKey="temp"
                    activeKey={planetSort.sortKey}
                    direction={planetSort.sortDirection}
                    onSort={planetSort.requestSort}
                    align="right"
                  />
                  <SortableTh
                    label="Fields Used / Max"
                    sortKey="fields"
                    activeKey={planetSort.sortKey}
                    direction={planetSort.sortDirection}
                    onSort={planetSort.requestSort}
                    align="right"
                  />
                  <th>Built → Target (Key Buildings)</th>
                  <th>Warehouse Driver</th>
                  <SortableTh
                    label="Last-Lvl Time"
                    sortKey="time"
                    activeKey={planetSort.sortKey}
                    direction={planetSort.sortDirection}
                    onSort={planetSort.requestSort}
                    align="right"
                  />
                  <SortableTh
                    label="Monthly Output"
                    sortKey="output"
                    activeKey={planetSort.sortKey}
                    direction={planetSort.sortDirection}
                    onSort={planetSort.requestSort}
                    align="right"
                  />
                  <th>Reserved Floors</th>
                </tr>
              </thead>
              <tbody id="co-planets-tbody">
                {pagedPlanets.length === 0 ? (
                  <tr>
                    <td colSpan={9} className="empty-cell">
                      {isJobRunning
                        ? "Optimizing planetary building targets..."
                        : "No Config Optimization run yet. Adjust parameters on the right and click Run Config Optimizer."}
                    </td>
                  </tr>
                ) : (
                  pagedPlanets.map((row) => {
                    const isSel = selectedPlanetDetail?.slot === row.slot;
                    const keyPills = KEY_PLANET_BUILDINGS.filter(
                      (bk) => (row.targetLevels[bk] ?? 0) > 0 || (row.builtLevels[bk] ?? 0) > 0
                    ).slice(0, 8);
                    const resEntries = Object.entries(row.reservedFloors);
                    const currentLock = resolveLockedSpecForRow(row, draft.planet_specializations);
                    const isLocked = currentLock !== "auto";
                    const canRename = !row.isPlaceholder && row.planetId > 0;
                    const isEditingName = canRename && editingPlanetId === row.planetId;
                    return (
                      <tr
                        key={`${row.slot}-${row.coords}`}
                        className={`clickable-row${isSel ? " row-selected" : ""}`}
                        onClick={() => setSelectedSlot(row.slot)}
                      >
                        <td className="ag-co-planet-cell">
                          {isEditingName ? (
                            renderInlineRenameEditor(row.planetId, row.coords, row.gameName)
                          ) : (
                            <div className="ag-co-planet-head">
                              <div className="action-title tabular">
                                {`#${row.slot} ${row.name}`}
                                {row.isCentral ? " ★" : ""}
                              </div>
                              {canRename ? (
                                <button
                                  type="button"
                                  className="btn btn-secondary btn-sm ag-co-rename-trigger"
                                  id={`btn-co-rename-${row.planetId}`}
                                  data-co-rename-planet={String(row.planetId)}
                                  aria-label={`Rename ${row.name} [${row.coords}]`}
                                  disabled={renamingPlanetId !== null}
                                  onClick={(e) => {
                                    e.stopPropagation();
                                    startRenamingPlanet(row.planetId, row.name);
                                  }}
                                >
                                  Rename
                                </button>
                              ) : null}
                            </div>
                          )}
                          <div className="action-sub tabular">
                            {`[${row.coords}]${row.isPlaceholder ? " · PREVIEW" : ""}${row.hasCustomName && row.gameName && row.gameName !== row.name ? ` · Game: ${row.gameName}` : ""}`}
                          </div>
                        </td>
                        <td
                          className="ag-co-spec-cell"
                          onClick={(e) => e.stopPropagation()}
                        >
                          <div className="ag-co-spec-tags">
                            <span
                              className={`ag-co-spec-badge tabular${isLocked ? " is-locked" : ""}${row.specializationChanged ? " is-changed" : ""}`}
                              title={
                                row.previousSpecialization && row.specializationChanged
                                  ? `Switched from ${formatSpecializationLabel(row.previousSpecialization)} to ${formatSpecializationLabel(row.detectedSpecialization)}`
                                  : `Current target specialization: ${formatSpecializationLabel(row.detectedSpecialization)}`
                              }
                            >
                              {formatSpecializationLabel(row.detectedSpecialization)}
                              {isLocked ? " · LOCKED" : ""}
                            </span>
                            {row.specializationChanged && row.previousSpecialization ? (
                              <span className="action-sub tabular">
                                {`was ${formatSpecializationLabel(row.previousSpecialization)}`}
                              </span>
                            ) : null}
                          </div>
                          <select
                            id={`co-spec-select-${row.slot}`}
                            className="ag-co-spec-select"
                            aria-label={`Specialization lock for ${row.name}`}
                            value={currentLock}
                            onClick={(e) => e.stopPropagation()}
                            onChange={(e) => {
                              e.stopPropagation();
                              void onPlanetSpecChange(row, e.target.value);
                            }}
                          >
                            {PLANET_SPECIALIZATION_OPTIONS.map((opt) => (
                              <option key={opt.key} value={opt.key}>
                                {opt.label}
                              </option>
                            ))}
                          </select>
                        </td>
                        <td className="col-right tabular">
                          {row.temperature !== null ? `${row.temperature}°C` : "—"}
                        </td>
                        <td className="col-right tabular">
                          {`${row.fieldsUsedCurrent ?? "—"}→${row.fieldsUsedTarget}/${row.fieldsMax}`}
                        </td>
                        <td>
                          <div className="ag-floor-pills">
                            {keyPills.map((bk) => {
                              const b = row.builtLevels[bk] ?? 0;
                              const t = row.targetLevels[bk] ?? b;
                              const delta = t - b;
                              return (
                                <span
                                  key={bk}
                                  className={`ag-mini-chip tabular${delta > 0 ? " is-upgrade" : ""}`}
                                  title={`${BUILDING_NAMES[bk] || bk}: built L${b}, target L${t}`}
                                >
                                  {`${BUILDING_SHORT_NAMES[bk] || bk} ${b}→${t}`}
                                </span>
                              );
                            })}
                          </div>
                        </td>
                        <td className="tabular">{row.warehouseDriver}</td>
                        <td className="col-right tabular">{row.lastLevelTimeText}</td>
                        <td className="col-right tabular">
                          {row.monthlyShips !== null
                            ? `${row.monthlyShips.toFixed(2)}/mo`
                            : row.monthlySpiceEq !== null
                              ? `${fmtCompact(row.monthlySpiceEq)} SpEq`
                              : "—"}
                        </td>
                        <td>
                          {resEntries.length === 0 ? (
                            <span className="action-sub">Built floor</span>
                          ) : (
                            <div className="ag-floor-pills">
                              {resEntries.map(([bk, lvl]) => (
                                <span key={bk} className="ag-mini-chip is-reserved tabular">
                                  {`${bk} ≥ ${lvl}`}
                                </span>
                              ))}
                            </div>
                          )}
                        </td>
                      </tr>
                    );
                  })
                )}
              </tbody>
            </table>
          </div>

          <TablePagination
            idPrefix="co-planets"
            totalItems={planetSort.sortedRows.length}
            page={planetPage}
            pageSize={planetPageSize}
            itemLabel="planets"
            onPageChange={setPlanetPage}
            onPageSizeChange={(sz) => {
              setPlanetPageSize(sz);
              setPlanetPage(1);
            }}
          />

          {planetRows.length === 0 && ownedDashboardPlanets.length > 0 ? (
            <div className="ag-co-pre-run-planets" id="co-owned-planets-rename">
              <div className="ag-co-detail-head tabular">
                <strong>Owned Planets — Quick Rename</strong>
                <span className="ag-toolbar-meta">
                  Custom names sync immediately across Config Optimizer and the console.
                </span>
              </div>
              <div className="ag-co-pre-run-grid">
                {ownedDashboardPlanets.map((pl) => {
                  const isEditingName = editingPlanetId === pl.planetId;
                  return (
                    <div key={pl.planetId} className="ag-co-pre-run-card">
                      {isEditingName ? (
                        renderInlineRenameEditor(pl.planetId, pl.coords, pl.gameName)
                      ) : (
                        <div className="ag-co-planet-head">
                          <div className="action-title tabular">{`#${pl.slot} ${pl.name}`}</div>
                          <button
                            type="button"
                            className="btn btn-secondary btn-sm ag-co-rename-trigger"
                            id={`btn-co-rename-${pl.planetId}`}
                            data-co-rename-planet={String(pl.planetId)}
                            aria-label={`Rename ${pl.name} [${pl.coords}]`}
                            disabled={renamingPlanetId !== null}
                            onClick={() => startRenamingPlanet(pl.planetId, pl.name)}
                          >
                            Rename
                          </button>
                        </div>
                      )}
                      <div className="action-sub tabular">
                        {`[${pl.coords}]${pl.hasCustomName && pl.gameName && pl.gameName !== pl.name ? ` · Game: ${pl.gameName}` : ""}`}
                      </div>
                    </div>
                  );
                })}
              </div>
            </div>
          ) : null}

          {selectedPlanetDetail ? (
            <div className="ag-co-detail-strip" id="co-planet-detail">
              <div className="ag-co-detail-head tabular">
                <strong>
                  {`Slot #${selectedPlanetDetail.slot} · ${selectedPlanetDetail.name} [${selectedPlanetDetail.coords}]`}
                </strong>
                <span>
                  {`Role: ${formatSpecializationLabel(selectedPlanetDetail.detectedSpecialization)} · Fields: ${selectedPlanetDetail.fieldsUsedTarget}/${selectedPlanetDetail.fieldsMax}`}
                </span>
              </div>
              <div className="ag-co-detail-grid">
                {selectedPlanetDetail.buildingsDetail.map((bd) => (
                  <div
                    key={bd.key}
                    className={`ag-co-bld-cell tabular${bd.toBuild > 0 ? " is-upgrade" : ""}${bd.reserved > 0 ? " is-reserved" : ""}`}
                  >
                    <span className="ag-co-bld-name">{`${bd.key} ${BUILDING_SHORT_NAMES[bd.key] || bd.name}`}</span>
                    <strong className="ag-co-bld-vals">
                      {`${bd.built} → ${bd.target}${bd.toBuild > 0 ? ` (+${bd.toBuild})` : ""}`}
                    </strong>
                    {bd.reserved > 0 ? (
                      <span className="ag-co-bld-res">{`Floor ≥ ${bd.reserved}`}</span>
                    ) : null}
                  </div>
                ))}
              </div>
            </div>
          ) : null}
        </div>

        <div className="ag-co-content" hidden={subView !== "research"}>
          <div className="table-wrap table-scroll">
            <table id="co-research-table">
              <thead>
                <tr>
                  <SortableTh
                    label="Research (r1–r15)"
                    sortKey="name"
                    activeKey={researchSort.sortKey}
                    direction={researchSort.sortDirection}
                    onSort={researchSort.requestSort}
                  />
                  <SortableTh
                    label="Current"
                    sortKey="current"
                    activeKey={researchSort.sortKey}
                    direction={researchSort.sortDirection}
                    onSort={researchSort.requestSort}
                    align="right"
                  />
                  <SortableTh
                    label="Universe Max"
                    sortKey="uniMax"
                    activeKey={researchSort.sortKey}
                    direction={researchSort.sortDirection}
                    onSort={researchSort.requestSort}
                    align="right"
                  />
                  <SortableTh
                    label="Planned Target"
                    sortKey="planned"
                    activeKey={researchSort.sortKey}
                    direction={researchSort.sortDirection}
                    onSort={researchSort.requestSort}
                    align="right"
                  />
                  <SortableTh
                    label="Levels to Build"
                    sortKey="gap"
                    activeKey={researchSort.sortKey}
                    direction={researchSort.sortDirection}
                    onSort={researchSort.requestSort}
                    align="right"
                  />
                  <th>Status</th>
                </tr>
              </thead>
              <tbody id="co-research-tbody">
                {pagedResearch.map((r) => (
                  <tr key={r.key}>
                    <td>
                      <span className="action-title">{r.name}</span>{" "}
                      <span className="action-sub tabular">{`(${r.key})`}</span>
                    </td>
                    <td className="col-right tabular">{`L${r.current}`}</td>
                    <td className="col-right tabular">{`L${r.universeMax}`}</td>
                    <td className="col-right tabular">{`L${r.planned}`}</td>
                    <td className="col-right tabular">{r.gap > 0 ? `+${r.gap}` : "0"}</td>
                    <td>
                      <span className={`eta-pill tabular${r.gap > 0 ? " is-wait" : ""}`}>
                        {r.gap > 0 ? `UPGRADE +${r.gap}` : "AT TARGET"}
                      </span>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <TablePagination
            idPrefix="co-research"
            totalItems={researchSort.sortedRows.length}
            page={researchPage}
            pageSize={researchPageSize}
            itemLabel="technologies"
            onPageChange={setResearchPage}
            onPageSizeChange={(sz) => {
              setResearchPageSize(sz);
              setResearchPage(1);
            }}
          />
        </div>
      </article>

      <aside
        className="hud-window ag-control-panel"
        id="ag-co-control-panel"
        aria-label="Config optimizer parameters"
      >
        <div className="window-bar window-bar-wrap">
          <div className="window-title-group">
            <span className="module-code tabular">CFG</span>
            <h2>Optimizer Parameters</h2>
          </div>
          <span
            className={`ag-dirty-badge tabular ${draftDirty ? "is-dirty" : "is-synced"}`}
            id="co-dirty-badge"
          >
            {draftDirty ? "MODIFIED" : "SYNCED"}
          </span>
        </div>

        <div className="ag-panel-tabs" role="tablist" aria-label="Config optimizer parameter tabs">
          <button
            type="button"
            className={`tab-btn${paramSubTab === "core" ? " active" : ""}`}
            role="tab"
            aria-selected={paramSubTab === "core"}
            id="co-ptab-core"
            onClick={() => setParamSubTab("core")}
          >
            Objective &amp; Fleet
          </button>
          <button
            type="button"
            className={`tab-btn${paramSubTab === "buildings" ? " active" : ""}`}
            role="tab"
            aria-selected={paramSubTab === "buildings"}
            id="co-ptab-buildings"
            onClick={() => setParamSubTab("buildings")}
          >
            Buildings &amp; GA
          </button>
        </div>

        <div className="ag-panel-body">
          <div className="ag-param-pane" hidden={paramSubTab !== "core"}>
            <div className="ag-param-group">
              <div className="ag-param-group-head">
                <h3>Objective &amp; Empire Constraints</h3>
                <p className="ag-param-group-sub">{CONFIG_OPTIMIZER_GROUP_SUBTITLES.objective}</p>
              </div>
              <div className="ag-param-list">
                <ActionGraphParamRow
                  doc={CONFIG_OPTIMIZER_PARAM_DOCS.target_ship_key}
                  htmlFor="co-param-target_ship_key"
                  layout="stack"
                >
                  <select
                    id="co-param-target_ship_key"
                    value={draft.target_ship_key}
                    onChange={(e) => {
                      setDraftDirty(true);
                      setDraft((prev) => ({ ...prev, target_ship_key: e.target.value }));
                    }}
                  >
                    {SHIP_OPTIONS.map((s) => (
                      <option key={s.key} value={s.key}>
                        {`${s.key} — ${s.name}`}
                      </option>
                    ))}
                  </select>
                </ActionGraphParamRow>

                <ActionGraphParamRow
                  doc={CONFIG_OPTIMIZER_PARAM_DOCS.production_month_days}
                  htmlFor="co-param-production_month_days"
                  layout="inline"
                >
                  <input
                    type="number"
                    id="co-param-production_month_days"
                    min={1}
                    max={365}
                    step={1}
                    value={draft.production_month_days}
                    onChange={(e) => {
                      setDraftDirty(true);
                      setDraft((prev) => ({ ...prev, production_month_days: e.target.value }));
                    }}
                  />
                </ActionGraphParamRow>

                <ActionGraphParamRow
                  doc={CONFIG_OPTIMIZER_PARAM_DOCS.research_percent_of_universe_max}
                  htmlFor="co-param-research_percent_of_universe_max"
                  layout="inline"
                >
                  <input
                    type="number"
                    id="co-param-research_percent_of_universe_max"
                    min={0}
                    max={200}
                    step={5}
                    value={draft.research_percent_of_universe_max}
                    onChange={(e) => {
                      setDraftDirty(true);
                      setDraft((prev) => ({
                        ...prev,
                        research_percent_of_universe_max: e.target.value,
                      }));
                    }}
                  />
                </ActionGraphParamRow>

                <ActionGraphParamRow
                  doc={CONFIG_OPTIMIZER_PARAM_DOCS.warehouse_hours_of_production}
                  htmlFor="co-param-warehouse_hours_of_production"
                  layout="inline"
                >
                  <input
                    type="number"
                    id="co-param-warehouse_hours_of_production"
                    min={0}
                    max={720}
                    step={1}
                    value={draft.warehouse_hours_of_production}
                    onChange={(e) => {
                      setDraftDirty(true);
                      setDraft((prev) => ({
                        ...prev,
                        warehouse_hours_of_production: e.target.value,
                      }));
                    }}
                  />
                </ActionGraphParamRow>

                <ActionGraphParamRow
                  doc={CONFIG_OPTIMIZER_PARAM_DOCS.central_planet_slot}
                  htmlFor="co-param-central_planet_slot"
                  layout="inline"
                >
                  <input
                    type="number"
                    id="co-param-central_planet_slot"
                    min={1}
                    max={50}
                    step={1}
                    value={draft.central_planet_slot}
                    onChange={(e) => {
                      setDraftDirty(true);
                      setDraft((prev) => ({ ...prev, central_planet_slot: e.target.value }));
                    }}
                  />
                </ActionGraphParamRow>

                <ActionGraphParamRow
                  doc={CONFIG_OPTIMIZER_PARAM_DOCS.central_planet_min_warehouse_level}
                  htmlFor="co-param-central_planet_min_warehouse_level"
                  layout="inline"
                >
                  <input
                    type="number"
                    id="co-param-central_planet_min_warehouse_level"
                    min={0}
                    max={50}
                    step={1}
                    value={draft.central_planet_min_warehouse_level}
                    onChange={(e) => {
                      setDraftDirty(true);
                      setDraft((prev) => ({
                        ...prev,
                        central_planet_min_warehouse_level: e.target.value,
                      }));
                    }}
                  />
                </ActionGraphParamRow>
              </div>
            </div>

            <div className="ag-param-group">
              <div className="ag-param-group-head">
                <h3>Specialization Stability</h3>
                <p className="ag-param-group-sub">
                  {CONFIG_OPTIMIZER_GROUP_SUBTITLES.stability}
                </p>
              </div>
              <div className="ag-param-list">
                <ActionGraphParamRow
                  doc={CONFIG_OPTIMIZER_PARAM_DOCS.preserve_previous_specializations}
                  htmlFor="co-param-preserve_previous_specializations"
                  layout="toggle"
                >
                  <input
                    type="checkbox"
                    className="config-checkbox"
                    id="co-param-preserve_previous_specializations"
                    checked={draft.preserve_previous_specializations}
                    onChange={(e) => {
                      const checked = e.target.checked;
                      setDraftDirty(true);
                      setDraft((prev) => ({
                        ...prev,
                        preserve_previous_specializations: checked,
                      }));
                    }}
                  />
                </ActionGraphParamRow>

                <ActionGraphParamRow
                  doc={CONFIG_OPTIMIZER_PARAM_DOCS.specialization_switch_threshold_pct}
                  htmlFor="co-param-specialization_switch_threshold_pct"
                  layout="inline"
                >
                  <input
                    type="number"
                    id="co-param-specialization_switch_threshold_pct"
                    min={0}
                    max={25}
                    step={0.1}
                    value={draft.specialization_switch_threshold_pct}
                    onChange={(e) => {
                      setDraftDirty(true);
                      setDraft((prev) => ({
                        ...prev,
                        specialization_switch_threshold_pct: e.target.value,
                      }));
                    }}
                  />
                </ActionGraphParamRow>
              </div>
            </div>

            <div className="ag-param-group">
              <div className="ag-param-group-head">
                <h3>Min Fleet per Planet</h3>
                <p className="ag-param-group-sub">{CONFIG_OPTIMIZER_GROUP_SUBTITLES.minFleet}</p>
              </div>
              <div className="ag-param-list">
                {(["s2", "s10", "s11"] as const).map((sk) => (
                  <ActionGraphParamRow
                    key={sk}
                    doc={CONFIG_OPTIMIZER_MIN_FLEET_DOCS[sk]}
                    htmlFor={`co-param-min_fleet-${sk}`}
                    layout="inline"
                  >
                    <input
                      type="number"
                      id={`co-param-min_fleet-${sk}`}
                      min={0}
                      max={10000}
                      step={1}
                      value={draft.min_fleet_per_planet[sk] ?? "10"}
                      onChange={(e) => {
                        setDraftDirty(true);
                        setDraft((prev) => ({
                          ...prev,
                          min_fleet_per_planet: {
                            ...prev.min_fleet_per_planet,
                            [sk]: e.target.value,
                          },
                        }));
                      }}
                    />
                  </ActionGraphParamRow>
                ))}
              </div>
            </div>

            <div className="ag-param-group">
              <div className="ag-param-group-head">
                <h3>Colony Slot Preview</h3>
                <p className="ag-param-group-sub">
                  {CONFIG_OPTIMIZER_GROUP_SUBTITLES.previewScope}
                </p>
              </div>
              <div className="ag-param-list">
                <ActionGraphParamRow
                  doc={CONFIG_OPTIMIZER_PARAM_DOCS.pad_generic_colonies}
                  htmlFor="co-param-pad_generic_colonies"
                  layout="toggle"
                  titleOverride={`Pad < ${gate.maxPlanets}/${gate.maxPlanets} Missing Slots (${gate.coloniesOwned}/${gate.maxPlanets} Owned)`}
                >
                  <input
                    type="checkbox"
                    className="config-checkbox"
                    id="co-param-pad_generic_colonies"
                    checked={draft.pad_generic_colonies}
                    onChange={(e) => {
                      const checked = e.target.checked;
                      setDraftDirty(true);
                      setDraft((prev) => ({ ...prev, pad_generic_colonies: checked }));
                    }}
                  />
                </ActionGraphParamRow>
              </div>
            </div>
          </div>

          <div className="ag-param-pane" hidden={paramSubTab !== "buildings"}>
            <div className="ag-param-group">
              <div className="ag-param-group-head">
                <h3>Optimized Building Max Caps</h3>
                <p className="ag-param-group-sub">
                  {CONFIG_OPTIMIZER_GROUP_SUBTITLES.optimizedCaps}
                </p>
              </div>
              <div className="ag-param-list">
                {OPTIMIZED_BUILDING_KEYS.map((bk) => (
                  <ActionGraphParamRow
                    key={bk}
                    doc={CONFIG_OPTIMIZER_OPT_MAX_DOCS[bk]}
                    htmlFor={`co-param-opt_max-${bk}`}
                    layout="inline"
                  >
                    <input
                      type="number"
                      id={`co-param-opt_max-${bk}`}
                      min={1}
                      max={100}
                      step={1}
                      value={draft.optimized_building_max_levels[bk] ?? "100"}
                      onChange={(e) => {
                        setDraftDirty(true);
                        setDraft((prev) => ({
                          ...prev,
                          optimized_building_max_levels: {
                            ...prev.optimized_building_max_levels,
                            [bk]: e.target.value,
                          },
                        }));
                      }}
                    />
                  </ActionGraphParamRow>
                ))}
              </div>
            </div>

            <div className="ag-param-group">
              <div className="ag-param-group-head">
                <h3>Fixed / Minimum Misc Levels</h3>
                <p className="ag-param-group-sub">{CONFIG_OPTIMIZER_GROUP_SUBTITLES.miscLevels}</p>
              </div>
              <div className="ag-param-list">
                {MISC_BUILDING_KEYS.map((bk) => (
                  <ActionGraphParamRow
                    key={bk}
                    doc={getConfigOptimizerMiscDoc(bk)}
                    htmlFor={`co-param-misc-${bk}`}
                    layout="inline"
                  >
                    <input
                      type="number"
                      id={`co-param-misc-${bk}`}
                      min={0}
                      max={100}
                      step={1}
                      value={draft.misc_building_levels[bk] ?? "0"}
                      onChange={(e) => {
                        setDraftDirty(true);
                        setDraft((prev) => ({
                          ...prev,
                          misc_building_levels: {
                            ...prev.misc_building_levels,
                            [bk]: e.target.value,
                          },
                        }));
                      }}
                    />
                  </ActionGraphParamRow>
                ))}
              </div>
            </div>

            <div className="ag-param-group">
              <div className="ag-param-group-head">
                <h3>GA Search Settings</h3>
                <p className="ag-param-group-sub">{CONFIG_OPTIMIZER_GROUP_SUBTITLES.gaSearch}</p>
              </div>
              <div className="ag-param-list">
                <ActionGraphParamRow
                  doc={CONFIG_OPTIMIZER_PARAM_DOCS.ga_population_size}
                  htmlFor="co-param-ga_population_size"
                  layout="inline"
                >
                  <input
                    type="number"
                    id="co-param-ga_population_size"
                    min={4}
                    max={1000}
                    step={10}
                    value={draft.ga_population_size}
                    onChange={(e) => {
                      setDraftDirty(true);
                      setDraft((prev) => ({ ...prev, ga_population_size: e.target.value }));
                    }}
                  />
                </ActionGraphParamRow>

                <ActionGraphParamRow
                  doc={CONFIG_OPTIMIZER_PARAM_DOCS.ga_generations}
                  htmlFor="co-param-ga_generations"
                  layout="inline"
                >
                  <input
                    type="number"
                    id="co-param-ga_generations"
                    min={1}
                    max={1000}
                    step={10}
                    value={draft.ga_generations}
                    onChange={(e) => {
                      setDraftDirty(true);
                      setDraft((prev) => ({ ...prev, ga_generations: e.target.value }));
                    }}
                  />
                </ActionGraphParamRow>

                <ActionGraphParamRow
                  doc={CONFIG_OPTIMIZER_PARAM_DOCS.ga_random_seed}
                  htmlFor="co-param-ga_random_seed"
                  layout="inline"
                >
                  <input
                    type="number"
                    id="co-param-ga_random_seed"
                    step={1}
                    value={draft.ga_random_seed}
                    onChange={(e) => {
                      setDraftDirty(true);
                      setDraft((prev) => ({ ...prev, ga_random_seed: e.target.value }));
                    }}
                  />
                </ActionGraphParamRow>
              </div>
            </div>
          </div>
        </div>

        <div className="ag-panel-footer">
          {isJobRunning ? (
            <div className="ag-job-progress" id="co-job-progress">
              <div className="ag-job-progress-meta tabular">
                <span>{String(activeJob?.phase || "Running Config Optimizer...")}</span>
                <span>
                  {`${Math.max(0, Math.min(100, Math.round(safeFiniteNumber(activeJob?.progress_pct, 0) ?? 0)))}%`}
                </span>
              </div>
              <div className="meter-track">
                <div
                  className="meter-fill gann-bar-mint"
                  style={{
                    transform: `scaleX(${Math.max(0, Math.min(100, safeFiniteNumber(activeJob?.progress_pct, 0) ?? 0)) / 100})`,
                  }}
                />
              </div>
            </div>
          ) : (
            <p className="ag-sim-note">
              Enforces all current built building levels and reserved floors as hard minimums; auto-triggers AI-Planner replan on completion.
            </p>
          )}
          <div className="ag-footer-actions">
            <button
              type="button"
              className="btn btn-secondary btn-sm"
              id="btn-co-reset"
              onClick={onResetDefaults}
            >
              Reset Defaults
            </button>
            <button
              type="button"
              className="btn btn-secondary btn-sm"
              id="btn-co-export-footer"
              onClick={onExportJson}
            >
              Export JSON
            </button>
            <button
              type="button"
              className="btn btn-primary btn-sm btn-span"
              id="btn-co-run"
              disabled={isSubmitting || isJobRunning}
              onClick={onRunOptimizer}
            >
              {isJobRunning ? "Running Config Optimizer..." : "Run Config Optimizer"}
            </button>
          </div>
        </div>
      </aside>
    </div>
  );
};

export default ConfigOptimizerTab;
