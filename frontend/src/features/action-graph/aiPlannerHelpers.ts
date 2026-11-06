import { safeFiniteNumber } from "../../api/httpClient";
import type {
  AiPlannerGetPayload,
  AiPlannerSmeltingToggleTarget,
  PlanetData,
  QueueJobData,
} from "../../types/api";
import {
  AiPlannerDraft,
  DEFAULT_AI_PLANNER_DRAFT,
  formatDurationSecondsCompact,
} from "./plannerHelpers";

export function formatAdaptiveColonyState(stateRaw: unknown): {
  label: string;
  tone: "is-building" | "is-ready" | "is-inbound" | "is-wait" | "is-blocked" | "is-idle";
} {
  const norm = String(stateRaw || "").trim().toLowerCase();
  switch (norm) {
    case "building":
      return { label: "Building", tone: "is-building" };
    case "ready_not_started":
    case "ready":
      return { label: "Ready", tone: "is-ready" };
    case "waiting_metal":
      return { label: "Wait Metal", tone: "is-wait" };
    case "waiting_pig_iron":
    case "waiting_pig":
      return { label: "Wait Pig", tone: "is-wait" };
    case "waiting_kryptonite":
    case "waiting_kry":
      return { label: "Wait Kry", tone: "is-wait" };
    case "waiting_spice":
      return { label: "Wait Spice", tone: "is-wait" };
    case "transport_inbound":
    case "cargo_inbound":
      return { label: "Cargo Inbound", tone: "is-inbound" };
    case "storage_blocked":
    case "storage_cap":
      return { label: "Storage Cap", tone: "is-blocked" };
    case "field_conflict":
      return { label: "Field Conflict", tone: "is-blocked" };
    case "idle_complete":
    case "idle":
    case "":
      return { label: "Idle", tone: "is-idle" };
    default:
      return {
        label: norm
          .split("_")
          .filter(Boolean)
          .map((w) => w.charAt(0).toUpperCase() + w.slice(1))
          .join(" "),
        tone: "is-idle",
      };
  }
}

export function formatSharePct(val: unknown): string {
  const n = safeFiniteNumber(val, 0) ?? 0;
  return String(Number(n.toFixed(1)));
}

export function countFallbackActiveBuilders(
  planets: PlanetData[],
  activeQueues: QueueJobData[]
): { active: number; total: number } {
  const normalPlanets = planets.filter(
    (p) => String(p?.planet_type || "normal").toLowerCase() === "normal"
  );
  const scoped = normalPlanets.length > 0 ? normalPlanets : planets;
  const knownPid = new Set<number>();
  const coordsToPid = new Map<string, number>();
  for (const pl of scoped) {
    const pid = Math.round(safeFiniteNumber(pl?.planet_id, 0) ?? 0);
    if (pid > 0) {
      knownPid.add(pid);
      const c = String(pl?.coords || "").trim();
      if (c) coordsToPid.set(c, pid);
    }
  }
  const nowSec = Math.floor(Date.now() / 1000);
  const activeKeys = new Set<string>();
  for (const job of activeQueues) {
    if (!job || String(job.category || "").toLowerCase() !== "building") continue;
    const finTs = safeFiniteNumber(job.finish_ts, null);
    const remSec = safeFiniteNumber(job.remaining_seconds, null);
    let active = false;
    if (finTs !== null && finTs > 0) {
      active = finTs > nowSec;
    } else if (remSec !== null) {
      active = remSec > 0;
    } else {
      const remTxt = String(job.remaining_text || "").trim().toLowerCase();
      active = remTxt.length > 0 && remTxt !== "0s" && remTxt !== "done" && remTxt !== "finished";
    }
    if (!active) continue;
    const jobPid = Math.round(safeFiniteNumber(job.planet_id, 0) ?? 0);
    const jobCoords = String(job.origin_coords || job.planet_coords || "").trim();
    if (jobPid > 0 && (knownPid.size === 0 || knownPid.has(jobPid))) {
      activeKeys.add(`pid:${jobPid}`);
    } else if (jobCoords && coordsToPid.has(jobCoords)) {
      activeKeys.add(`pid:${coordsToPid.get(jobCoords)!}`);
    } else if (jobCoords && knownPid.size === 0) {
      activeKeys.add(`coords:${jobCoords}`);
    }
  }
  return { active: activeKeys.size, total: scoped.length };
}

export function draftFromAiParams(raw: Record<string, any> | null | undefined): AiPlannerDraft {
  if (!raw || typeof raw !== "object") {
    return {
      ...DEFAULT_AI_PLANNER_DRAFT,
      resource_spare_percent: { ...DEFAULT_AI_PLANNER_DRAFT.resource_spare_percent },
    };
  }
  const get = (snake: string, upper: string, def: any) =>
    raw[snake] !== undefined ? raw[snake] : raw[upper] !== undefined ? raw[upper] : def;

  const rawSpare = get("resource_spare_percent", "PATH_RESOURCE_SPARE_PERCENT", {}) || {};
  const spare = {
    pig_iron: String(safeFiniteNumber(rawSpare.pig_iron, 10) ?? 10),
    metal: String(safeFiniteNumber(rawSpare.metal, 10) ?? 10),
    kryptonite: String(safeFiniteNumber(rawSpare.kryptonite, 10) ?? 10),
    spice: String(safeFiniteNumber(rawSpare.spice, 10) ?? 10),
  };
  const b10Raw = get("fleet_command_center_level", "PATH_FLEET_COMMAND_CENTER_LEVEL", null);
  const apdRaw = get("attacks_per_day", "PATH_ATTACKS_PER_DAY", null);

  return {
    reward_weeks: String(
      Math.max(1, Math.round(safeFiniteNumber(get("reward_weeks", "PATH_REWARD_WEEKS", 26), 26) ?? 26))
    ),
    weekly_discount: String(
      safeFiniteNumber(get("weekly_discount", "PATH_WEEKLY_DISCOUNT", 0.9), 0.9) ?? 0.9
    ),
    transport_dispatch_delay_minutes: String(
      Math.max(
        0,
        Math.min(
          1440,
          safeFiniteNumber(
            get("transport_dispatch_delay_minutes", "PATH_TRANSPORT_DISPATCH_DELAY_MINUTES", 120),
            120
          ) ?? 120
        )
      )
    ),
    resource_spare_percent: spare,
    fleet_command_center_level:
      b10Raw !== null && b10Raw !== undefined && String(b10Raw).trim() !== ""
        ? String(Math.round(safeFiniteNumber(b10Raw, 0) ?? 0))
        : "",
    attacks_per_day:
      apdRaw !== null && apdRaw !== undefined && String(apdRaw).trim() !== ""
        ? String(safeFiniteNumber(apdRaw, 0) ?? 0)
        : "",
    pad_generic_colonies: Boolean(get("pad_generic_colonies", "PAD_GENERIC_COLONIES", false)),
    scarcity_focus: String(safeFiniteNumber(get("scarcity_focus", "SCARCITY_FOCUS", 1.0), 1.0) ?? 1.0),
    critical_queue_share: String(
      safeFiniteNumber(get("critical_queue_share", "CRITICAL_QUEUE_SHARE", 0.9), 0.9) ?? 0.9
    ),
    patience: String(safeFiniteNumber(get("patience", "PATIENCE", 0.25), 0.25) ?? 0.25),
    speed_weight: String(safeFiniteNumber(get("speed_weight", "SPEED_WEIGHT", 1.5), 1.5) ?? 1.5),
    ga_population_size: String(
      Math.max(4, Math.round(safeFiniteNumber(get("ga_population_size", "GA_POPULATION_SIZE", 10), 10) ?? 10))
    ),
    ga_generations: String(
      Math.max(1, Math.round(safeFiniteNumber(get("ga_generations", "GA_GENERATIONS", 6), 6) ?? 6))
    ),
    ga_random_seed: String(
      Math.round(safeFiniteNumber(get("ga_random_seed", "GA_RANDOM_SEED", 20261117), 20261117) ?? 20261117)
    ),
  };
}

export function buildAiParamsPayload(draft: AiPlannerDraft): Record<string, any> {
  const num = (v: string, fb: number) => safeFiniteNumber(v, fb) ?? fb;
  const intNum = (v: string, fb: number, min = 0) => Math.max(min, Math.round(num(v, fb)));

  const b10Trim = draft.fleet_command_center_level.trim();
  const apdTrim = draft.attacks_per_day.trim();

  return {
    reward_weeks: Math.max(1, Math.min(104, intNum(draft.reward_weeks, 26, 1))),
    weekly_discount: Math.max(0.05, Math.min(1.0, num(draft.weekly_discount, 0.9))),
    transport_dispatch_delay_minutes: Math.max(
      0,
      Math.min(1440, num(draft.transport_dispatch_delay_minutes, 120))
    ),
    resource_spare_percent: {
      pig_iron: Math.max(0, Math.min(100, num(draft.resource_spare_percent.pig_iron, 10))),
      metal: Math.max(0, Math.min(100, num(draft.resource_spare_percent.metal, 10))),
      kryptonite: Math.max(0, Math.min(100, num(draft.resource_spare_percent.kryptonite, 10))),
      spice: Math.max(0, Math.min(100, num(draft.resource_spare_percent.spice, 10))),
    },
    fleet_command_center_level: b10Trim === "" ? null : intNum(b10Trim, 0, 0),
    attacks_per_day: apdTrim === "" ? null : Math.max(0, num(apdTrim, 0)),
    pad_generic_colonies: Boolean(draft.pad_generic_colonies),
    scarcity_focus: Math.max(0, num(draft.scarcity_focus, 1.0)),
    critical_queue_share: Math.max(0, Math.min(1.0, num(draft.critical_queue_share, 0.9))),
    patience: Math.max(0, num(draft.patience, 0.25)),
    speed_weight: Math.max(0, num(draft.speed_weight, 1.5)),
    ga_population_size: intNum(draft.ga_population_size, 10, 4),
    ga_generations: intNum(draft.ga_generations, 6, 1),
    ga_random_seed: Math.round(num(draft.ga_random_seed, 20261117)),
  };
}

export function deriveAiPlannerSummaryMetrics(
  aiData: AiPlannerGetPayload | null,
  draft: AiPlannerDraft
) {
  const summary = aiData?.active_summary || {};
  const telemetry = aiData?.live_attack_telemetry || aiData?.attack_telemetry || {};
  const rewardWeeks = Math.max(
    1,
    Math.round(safeFiniteNumber(summary.reward_weeks ?? draft.reward_weeks, 26) ?? 26)
  );
  const discReward = safeFiniteNumber(
    summary.discounted_reward_spice_eq ?? summary.discounted_reward,
    null
  );
  const totalNetOutput = safeFiniteNumber(
    summary.total_net_spice_eq ?? summary.total_spice_eq,
    null
  );
  const stepsCompleted = Math.max(
    0,
    Math.round(safeFiniteNumber(summary.steps_completed ?? summary.steps_started, 0) ?? 0)
  );
  const stepsTotal = Math.max(
    0,
    Math.round(safeFiniteNumber(summary.steps ?? aiData?.steps_page?.total, 0) ?? 0)
  );
  const startSlots = Math.round(
    safeFiniteNumber(summary.start_fleet_slots ?? telemetry.current_fleet_slots, 0) ?? 0
  );
  const endSlots = Math.round(
    safeFiniteNumber(summary.final_fleet_slots ?? startSlots, startSlots) ?? startSlots
  );
  const lootPerHr = safeFiniteNumber(
    telemetry.hourly_loot_spice_eq ?? telemetry.hourly_loot?.spice_eq,
    null
  );
  const medianLoot = safeFiniteNumber(
    telemetry.median_loot_spice_eq ?? telemetry.median_loot?.spice_eq,
    null
  );
  const medianRtSec = safeFiniteNumber(telemetry.median_round_trip_seconds, null);
  const inProgBuilds = Array.isArray(aiData?.in_progress?.buildings)
    ? aiData!.in_progress!.buildings.length
    : Math.max(0, Math.round(safeFiniteNumber(summary.in_progress_buildings_count, 0) ?? 0));
  const inProgRes = Array.isArray(aiData?.in_progress?.research)
    ? aiData!.in_progress!.research.length
    : Math.max(0, Math.round(safeFiniteNumber(summary.in_progress_research_count, 0) ?? 0));
  const dispatchDelayMin = Math.round(
    safeFiniteNumber(
      summary.transport_dispatch_delay_minutes ?? draft.transport_dispatch_delay_minutes,
      120
    ) ?? 120
  );

  const smelting = aiData?.smelting || null;
  const liveBf = aiData?.live_blast_furnace || null;
  const smeltingEvents = Array.isArray(smelting?.smelting_events) ? smelting!.smelting_events! : [];
  const smeltingPlanets =
    Array.isArray(smelting?.planets) && smelting!.planets!.length > 0
      ? smelting!.planets!
      : Array.isArray(liveBf?.planets)
        ? liveBf!.planets!
        : [];
  const initLiveMode = String(
    smelting?.initial_live_mode ?? liveBf?.mode ?? summary.initial_smelting_live_mode ?? "none"
  ).toLowerCase();
  const initLiveEnabledCount = Math.round(
    safeFiniteNumber(smelting?.initial_live_enabled_count ?? liveBf?.enabled_count, 0) ?? 0
  );
  const totalB2Planets = Math.round(
    safeFiniteNumber(smelting?.total_b2_planets ?? liveBf?.total_b2_planets, 0) ?? 0
  );
  const hasSmeltingTarget =
    typeof smelting?.initial_target_enabled === "boolean" ||
    typeof summary.initial_smelting_target_enabled === "boolean";
  const initTargetEnabled = Boolean(
    smelting?.initial_target_enabled ?? summary.initial_smelting_target_enabled ?? false
  );
  const initToggleNeeded = Boolean(
    smelting?.initial_toggle_needed ?? summary.initial_smelting_toggle_needed ?? false
  );
  const metalSmelted = safeFiniteNumber(smelting?.metal_smelted ?? summary.metal_smelted, null);
  const smeltingHours = safeFiniteNumber(
    smelting?.smelting_hours_total ?? summary.smelting_hours_total,
    null
  );
  const smeltingTimeText =
    smelting?.smelting_time_text ||
    (smeltingHours !== null ? formatDurationSecondsCompact(smeltingHours * 3600) : "0m");
  const smeltingTogglesCount = Math.round(
    safeFiniteNumber(
      smelting?.smelting_toggles_count ?? summary.smelting_toggles_count,
      smeltingEvents.length
    ) ?? 0
  );
  const toggleByPlanetId = new Map<number, AiPlannerSmeltingToggleTarget>();
  if (Array.isArray(smelting?.initial_planets_needing_toggle)) {
    for (const row of smelting!.initial_planets_needing_toggle!) {
      if (row && typeof row.planet_id === "number") toggleByPlanetId.set(row.planet_id, row);
    }
  }
  const liveModeLabel =
    totalB2Planets <= 0
      ? "NONE"
      : `${initLiveMode.toUpperCase()} (${initLiveEnabledCount}/${totalB2Planets} ON)`;

  return {
    summary,
    telemetry,
    rewardWeeks,
    discReward,
    totalNetOutput,
    stepsCompleted,
    stepsTotal,
    startSlots,
    endSlots,
    lootPerHr,
    medianLoot,
    medianRtSec,
    inProgBuilds,
    inProgRes,
    dispatchDelayMin,
    smeltingEvents,
    smeltingPlanets,
    initLiveMode,
    hasSmeltingTarget,
    initTargetEnabled,
    initToggleNeeded,
    metalSmelted,
    smeltingTimeText,
    smeltingTogglesCount,
    toggleByPlanetId,
    liveModeLabel,
  };
}
