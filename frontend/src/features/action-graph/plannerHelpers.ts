import { safeFiniteNumber } from "../../api/httpClient";
import type { DashboardPayload, PlannerJobState, PlannerJobsStatusPayload } from "../../types/api";

export const BUILDING_NAMES: Record<string, string> = {
  b1: "Pig iron mine",
  b2: "Blast furnace",
  b3: "Kryptonite mine",
  b4: "Spice mine",
  b5: "Fusion plant",
  b6: "Thermosolar plant",
  b7: "Development center",
  b8: "Research lab",
  b9: "Weapons factory",
  b10: "Fleet base",
  b11: "Pig iron warehouse",
  b12: "Metal warehouse",
  b13: "Kryptonite warehouse",
  b14: "Spice warehouse",
  b15: "Particle cannon",
  b16: "Particle shield",
  b17: "Microsystem accelerator",
  b18: "Teleporter",
  b19: "Galaxy scanner",
  b20: "Tunnel system",
  b21: "Quantum Array",
};

export const BUILDING_SHORT_NAMES: Record<string, string> = {
  b1: "Pig",
  b2: "Blast",
  b3: "Kryp",
  b4: "Spice",
  b5: "Fus",
  b6: "Solar",
  b7: "Dev",
  b8: "Lab",
  b9: "Wep",
  b10: "FBase",
  b11: "WhPig",
  b12: "WhMet",
  b13: "WhKry",
  b14: "WhSpi",
  b15: "PCan",
  b16: "PShd",
  b17: "Micro",
  b18: "Tele",
  b19: "Scan",
  b20: "Tun",
  b21: "QArr",
};

export const KEY_PLANET_BUILDINGS: readonly string[] = [
  "b1",
  "b2",
  "b3",
  "b4",
  "b5",
  "b7",
  "b8",
  "b9",
  "b10",
  "b17",
  "b11",
  "b12",
  "b13",
  "b14",
];

export const MISC_BUILDING_KEYS: readonly string[] = [
  "b6",
  "b7",
  "b8",
  "b10",
  "b11",
  "b12",
  "b13",
  "b14",
  "b15",
  "b16",
  "b18",
  "b19",
  "b20",
  "b21",
];

export const OPTIMIZED_BUILDING_KEYS: readonly string[] = [
  "b1",
  "b2",
  "b3",
  "b4",
  "b5",
  "b9",
  "b17",
];

export const SHIP_OPTIONS: ReadonlyArray<{ key: string; slug: string; name: string }> = [
  { key: "s1", slug: "small-transport", name: "Small transport" },
  { key: "s2", slug: "large-transport", name: "Large transport" },
  { key: "s3", slug: "transmitter", name: "Transmitter" },
  { key: "s4", slug: "small-fighter", name: "Small fighter" },
  { key: "s5", slug: "warship", name: "Warship" },
  { key: "s6", slug: "starcruiser", name: "Starcruiser" },
  { key: "s7", slug: "phoenix", name: "Phoenix" },
  { key: "s8", slug: "colony-ship", name: "Colony ship" },
  { key: "s9", slug: "solar-cells", name: "Solar cells" },
  { key: "s10", slug: "spy-probe", name: "Spy probe" },
  { key: "s11", slug: "recycler", name: "Recycler" },
  { key: "s13", slug: "battleship", name: "Battleship" },
  { key: "s14", slug: "stealth-bomber", name: "Stealth bomber" },
  { key: "s15", slug: "destroyer", name: "Destroyer" },
  { key: "s16", slug: "imperial-starbase", name: "Imperial starbase" },
];

export const RESEARCH_KEYS: readonly string[] = [
  "r1",
  "r2",
  "r3",
  "r4",
  "r5",
  "r6",
  "r7",
  "r8",
  "r9",
  "r10",
  "r11",
  "r12",
  "r13",
  "r14",
  "r15",
];

export const RESEARCH_NAMES: Record<string, string> = {
  r1: "Mining technology",
  r2: "Energy technology",
  r3: "Engine research",
  r4: "Rocket engine",
  r5: "Diffusion engine",
  r6: "Warp engine",
  r7: "Spy technology",
  r8: "Weapons technology",
  r9: "Shield technology",
  r10: "Defense systems",
  r11: "Targeting systems",
  r12: "Particle analyzer",
  r13: "Graviton technology",
  r14: "Teleportation",
  r15: "Black hole research",
};

export interface ConfigOptimizerDraft {
  target_ship_key: string;
  production_month_days: string;
  research_percent_of_universe_max: string;
  warehouse_hours_of_production: string;
  central_planet_slot: string;
  central_planet_min_warehouse_level: string;
  min_fleet_per_planet: Record<string, string>;
  misc_building_levels: Record<string, string>;
  optimized_building_max_levels: Record<string, string>;
  pad_generic_colonies: boolean;
  ga_population_size: string;
  ga_generations: string;
  ga_random_seed: string;
}

export const DEFAULT_CONFIG_OPTIMIZER_DRAFT: ConfigOptimizerDraft = {
  target_ship_key: "s16",
  production_month_days: "30",
  research_percent_of_universe_max: "100",
  warehouse_hours_of_production: "24",
  central_planet_slot: "1",
  central_planet_min_warehouse_level: "15",
  min_fleet_per_planet: {
    s2: "10",
    s10: "10",
    s11: "10",
  },
  misc_building_levels: {
    b6: "0",
    b7: "12",
    b8: "0",
    b10: "5",
    b11: "0",
    b12: "0",
    b13: "0",
    b14: "0",
    b15: "0",
    b16: "0",
    b18: "0",
    b19: "0",
    b20: "0",
    b21: "0",
  },
  optimized_building_max_levels: {
    b1: "100",
    b2: "100",
    b3: "100",
    b4: "100",
    b5: "100",
    b9: "100",
    b17: "100",
  },
  pad_generic_colonies: false,
  ga_population_size: "200",
  ga_generations: "150",
  ga_random_seed: "20261117",
};

export interface AiPlannerDraft {
  reward_weeks: string;
  weekly_discount: string;
  resource_spare_percent: {
    pig_iron: string;
    metal: string;
    kryptonite: string;
    spice: string;
  };
  fleet_command_center_level: string;
  attacks_per_day: string;
  pad_generic_colonies: boolean;
  scarcity_focus: string;
  critical_queue_share: string;
  patience: string;
  speed_weight: string;
  ga_population_size: string;
  ga_generations: string;
  ga_random_seed: string;
}

export const DEFAULT_AI_PLANNER_DRAFT: AiPlannerDraft = {
  reward_weeks: "26",
  weekly_discount: "0.9",
  resource_spare_percent: {
    pig_iron: "10",
    metal: "10",
    kryptonite: "10",
    spice: "10",
  },
  fleet_command_center_level: "",
  attacks_per_day: "",
  pad_generic_colonies: false,
  scarcity_focus: "1.0",
  critical_queue_share: "0.9",
  patience: "0.25",
  speed_weight: "1.5",
  ga_population_size: "10",
  ga_generations: "6",
  ga_random_seed: "20261117",
};

export function sanitizeMainWindowText(rawText: unknown): string {
  return String(rawText ?? "").replace(/generations?/gi, "cycles");
}

export function normalizeUiPlannerMode(mode: unknown): "priority" | "ai_planner" {
  const low = String(mode ?? "priority").trim().toLowerCase();
  if (low === "ai_planner") return "ai_planner";
  return "priority";
}

export function computeColonyGateInfo(
  dashboard: DashboardPayload | null | undefined,
  jobsStatus?: PlannerJobsStatusPayload | null,
  tabOverride?: { colonies_owned?: unknown; max_planets?: unknown; live_ai_unlocked?: unknown } | null
): {
  coloniesOwned: number;
  maxPlanets: number;
  liveAiUnlocked: boolean;
} {
  const normalPlanets = Array.isArray(dashboard?.state?.planets)
    ? dashboard!.state!.planets!.filter(
        (p) => String(p?.planet_type || "normal").toLowerCase() === "normal"
      ).length
    : 0;
  const liveStatus = jobsStatus || dashboard?.planner_jobs_status || dashboard?.planner_status;
  const maxPlanets = Math.max(
    1,
    Math.round(
      safeFiniteNumber(
        liveStatus?.max_planets ??
          dashboard?.max_planets ??
          dashboard?.config?.max_planets ??
          tabOverride?.max_planets,
        11
      ) ?? 11
    )
  );
  const coloniesOwned = Math.max(
    0,
    Math.round(
      safeFiniteNumber(
        dashboard?.colonies_owned ??
          liveStatus?.colonies_owned ??
          tabOverride?.colonies_owned,
        normalPlanets
      ) ?? normalPlanets
    )
  );
  const unlockedFlag =
    dashboard?.live_ai_unlocked ??
    liveStatus?.live_ai_unlocked ??
    tabOverride?.live_ai_unlocked;
  const liveAiUnlocked =
    typeof unlockedFlag === "boolean" ? unlockedFlag : coloniesOwned >= maxPlanets;
  return { coloniesOwned, maxPlanets, liveAiUnlocked };
}

export function formatDurationSecondsCompact(secRaw: unknown): string {
  const sec = safeFiniteNumber(secRaw, null);
  if (sec === null) return "—";
  const s = Math.max(0, Math.round(sec));
  if (s <= 0) return "Ready";
  const d = Math.floor(s / 86400);
  const h = Math.floor((s % 86400) / 3600);
  const m = Math.floor((s % 3600) / 60);
  const rem = s % 60;
  if (d > 0) return `${d}d ${h}h`;
  if (h > 0) return `${h}h ${m}m`;
  if (m > 0) return `${m}m ${rem}s`;
  return `${rem}s`;
}

export function formatJobBadgeText(job: PlannerJobState | null | undefined): string {
  if (!job || typeof job !== "object") return "IDLE";
  const st = String(job.status || "idle").toLowerCase();
  if (st === "running") {
    const pct = Math.max(0, Math.min(100, Math.round(safeFiniteNumber(job.progress_pct, 0) ?? 0)));
    return pct > 0 ? `RUNNING ${pct}%` : "RUNNING";
  }
  if (st === "completed") return "COMPLETED";
  if (st === "error") return "ERROR";
  return "IDLE";
}

export function downloadTextFile(filename: string, content: string, mimeType = "text/plain"): boolean {
  if (typeof document === "undefined") return false;
  try {
    const blob = new Blob([content], { type: mimeType });
    const url =
      typeof URL !== "undefined" && typeof URL.createObjectURL === "function"
        ? URL.createObjectURL(blob)
        : `data:${mimeType};charset=utf-8,${encodeURIComponent(content)}`;
    const link = document.createElement("a");
    link.href = url;
    link.download = filename;
    link.style.display = "none";
    document.body.appendChild(link);
    link.click();
    document.body.removeChild(link);
    if (typeof URL !== "undefined" && typeof URL.revokeObjectURL === "function" && url.startsWith("blob:")) {
      setTimeout(() => URL.revokeObjectURL(url), 100);
    }
    return true;
  } catch {
    return false;
  }
}

export function shipNameForKey(key: string): string {
  const found = SHIP_OPTIONS.find((s) => s.key === key || s.slug === key);
  return found ? found.name : key;
}
