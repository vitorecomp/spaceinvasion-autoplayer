import { safeFiniteNumber } from "../../api/httpClient";
import type { StrategyConfigData } from "../../types/api";
import { normalizeUiPlannerMode } from "./plannerHelpers";

export const AG_BOOL_FIELDS = [
  "auto_throttle_blast_furnace",
  "merchant_mode",
  "allow_research",
  "allow_shipyard",
  "allow_multi_planet",
  "allow_fleetsave",
  "allow_galaxy_scan",
  "spy_reserved_slots_only",
  "allow_raids",
  "allow_pvp_in_az",
  "allow_colonize",
  "allow_transport",
  "allow_transmitter",
  "forbid_planetary_solar",
] as const;

export const AG_NUM_DEFAULTS: Record<string, number> = {
  storage_warn_ratio: 0.85,
  blast_pig_iron_max_share: 0.7,
  combat_safety_margin: 1.4,
  raid_min_loot_spice_eq: 1500,
  attack_min_res_per_hour: 0,
  recycle_min_res_per_hour: 0,
  hot_planet_solar_temp_threshold: 40,
  min_free_fields_warning: 20,
  min_colony_fields: 300,
  max_planets: 11,
  galaxy_sweep_interval_seconds: 270,
  galaxy_sweep_pages: 100,
  galaxy_scan_click_delay_seconds: 1.5,
  galaxy_scan_click_jitter_seconds: 0.6,
  reserved_spy_slots: 1,
  fleet_save_fuel_reserve_spice: 500,
  action_batch_interval_seconds: 120,
  max_actions_per_hour: 60,
};

export function buildDraftStateFromConfig(
  cfg: StrategyConfigData | null | undefined
): Record<string, any> {
  const out: Record<string, any> = {
    planner_mode: normalizeUiPlannerMode(cfg?.planner_mode),
    playstyle: String(cfg?.playstyle || "fleeter"),
  };
  for (const [k, fallback] of Object.entries(AG_NUM_DEFAULTS)) {
    const v = safeFiniteNumber(cfg?.[k], fallback) ?? fallback;
    out[k] = String(v);
  }
  for (const bKey of AG_BOOL_FIELDS) {
    const defChecked =
      bKey === "auto_throttle_blast_furnace" ||
      bKey === "allow_research" ||
      bKey === "allow_shipyard" ||
      bKey === "allow_multi_planet";
    out[bKey] = cfg && bKey in cfg ? Boolean(cfg[bKey]) : defChecked;
  }
  return out;
}

export function collectHeuristicsDraftPayload(
  draftValues: Record<string, any>,
  modeOverride?: "priority" | "ai_planner"
): Record<string, any> {
  const readEl = (id: string) =>
    (typeof document !== "undefined" ? document.getElementById(id) : null) as
      | HTMLInputElement
      | HTMLSelectElement
      | null;

  const modeSel = readEl("ag-cfg-planner_mode");
  const playSel = readEl("ag-cfg-playstyle");
  const payload: Record<string, any> = {
    planner_mode:
      modeOverride ??
      normalizeUiPlannerMode(modeSel ? modeSel.value : draftValues.planner_mode),
    playstyle: playSel
      ? String(playSel.value || "fleeter")
      : String(draftValues.playstyle || "fleeter"),
  };

  for (const boolField of AG_BOOL_FIELDS) {
    const box = readEl(`ag-cfg-${boolField}`) as HTMLInputElement | null;
    payload[boolField] = box ? Boolean(box.checked) : Boolean(draftValues[boolField]);
  }

  for (const numField of Object.keys(AG_NUM_DEFAULTS)) {
    const inp = readEl(`ag-cfg-${numField}`);
    const raw = inp ? String(inp.value ?? "").trim() : String(draftValues[numField] ?? "").trim();
    if (raw === "") {
      payload[numField] = "";
    } else if (/^[+-]?(\d+\.?\d*|\.\d+)([eE][+-]?\d+)?$/.test(raw)) {
      const parsed = Number(raw);
      payload[numField] = Number.isFinite(parsed) ? parsed : raw;
    } else {
      payload[numField] = raw;
    }
  }

  return payload;
}
