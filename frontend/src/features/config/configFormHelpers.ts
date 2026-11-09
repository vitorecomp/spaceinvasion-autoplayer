import { safeFiniteNumber } from "../../api/httpClient";
import type { StrategyConfigData } from "../../types/api";
import { normalizeUiPlannerMode } from "../action-graph/plannerHelpers";
import {
  CONFIG_BOOL_KEYS,
  CONFIG_GROUPS,
  CONFIG_NUMBER_DEFAULTS,
} from "./configSchema";

export function fmtShortUpdated(tsSec: unknown): string {
  const ts = safeFiniteNumber(tsSec, null);
  if (ts === null || ts <= 0) return "—";
  const d = new Date(ts * 1000);
  if (Number.isNaN(d.getTime())) return "—";
  const mm = String(d.getMonth() + 1).padStart(2, "0");
  const dd = String(d.getDate()).padStart(2, "0");
  const hh = String(d.getHours()).padStart(2, "0");
  const mi = String(d.getMinutes()).padStart(2, "0");
  return `${mm}-${dd} ${hh}:${mi}`;
}

export function buildFormStateFromConfig(
  cfg: StrategyConfigData | null | undefined
): Record<string, any> {
  const out: Record<string, any> = {
    planner_mode: normalizeUiPlannerMode(cfg?.planner_mode),
    playstyle: String(cfg?.playstyle || "fleeter"),
    spy_dispatch_planet_id: String(
      Math.max(0, Math.round(safeFiniteNumber(cfg?.spy_dispatch_planet_id, 0) ?? 0))
    ),
  };
  for (const bKey of CONFIG_BOOL_KEYS) {
    const defItem = CONFIG_GROUPS.flatMap((g) => g.items).find((i) => i.key === bKey);
    out[bKey] = cfg && bKey in cfg ? Boolean(cfg[bKey]) : Boolean(defItem?.defaultValue);
  }
  for (const [nKey, defVal] of Object.entries(CONFIG_NUMBER_DEFAULTS)) {
    const v = cfg && cfg[nKey] != null ? cfg[nKey] : defVal;
    out[nKey] = String(v);
  }
  return out;
}

export function syncDomFromFormState(next: Record<string, any>): void {
  if (typeof document === "undefined") return;
  for (const [k, v] of Object.entries(next)) {
    const node = document.getElementById(`cfg-${k}`) as
      | HTMLInputElement
      | HTMLSelectElement
      | null;
    if (!node) continue;
    if (node instanceof HTMLInputElement && node.type === "checkbox") {
      node.checked = Boolean(v);
    } else {
      node.value = String(v ?? "");
    }
  }
}

export function collectConfigSavePayload(formValues: Record<string, any>): {
  syncedForm: Record<string, any>;
  payload: Record<string, any>;
  plannerModeVal: string;
} {
  const readEl = (id: string) =>
    (typeof document !== "undefined" ? document.getElementById(id) : null) as
      | HTMLInputElement
      | HTMLSelectElement
      | null;

  const modeNode = readEl("cfg-planner_mode");
  const playNode = readEl("cfg-playstyle");
  const spyPidNode = readEl("cfg-spy_dispatch_planet_id");

  const plannerModeVal = modeNode ? modeNode.value : String(formValues.planner_mode || "priority");
  const playstyleVal = playNode ? playNode.value : String(formValues.playstyle || "fleeter");
  const rawSpyPid = spyPidNode
    ? spyPidNode.value
    : String(formValues.spy_dispatch_planet_id || "0");

  const syncedForm: Record<string, any> = {
    ...formValues,
    planner_mode: plannerModeVal,
    playstyle: playstyleVal,
    spy_dispatch_planet_id: String(
      Math.max(0, Math.round(safeFiniteNumber(rawSpyPid, 0) ?? 0))
    ),
  };

  const payload: Record<string, any> = {
    planner_mode: plannerModeVal,
    playstyle: playstyleVal,
    spy_dispatch_planet_id: Math.max(0, Math.round(safeFiniteNumber(rawSpyPid, 0) ?? 0)),
  };

  for (const bKey of CONFIG_BOOL_KEYS) {
    const box = readEl(`cfg-${bKey}`) as HTMLInputElement | null;
    const checkedVal = box ? Boolean(box.checked) : Boolean(formValues[bKey]);
    payload[bKey] = checkedVal;
    syncedForm[bKey] = checkedVal;
  }

  for (const numField of Object.keys(CONFIG_NUMBER_DEFAULTS)) {
    const inp = readEl(`cfg-${numField}`);
    const raw = inp ? String(inp.value ?? "").trim() : String(formValues[numField] ?? "").trim();
    syncedForm[numField] = inp ? String(inp.value ?? "") : String(formValues[numField] ?? "");
    if (raw !== "") {
      const parsed = safeFiniteNumber(raw, null);
      payload[numField] = parsed !== null ? parsed : raw;
    }
  }

  return { syncedForm, payload, plannerModeVal };
}

export function formatGalaxyCoverageText(galaxySweep: Record<string, any>): string {
  const galPct = Math.max(
    0,
    Math.min(100, safeFiniteNumber(galaxySweep.coverage_pct, 0) ?? 0)
  ).toFixed(1);
  const galExplored = Math.round(safeFiniteNumber(galaxySweep.explored_systems, 0) ?? 0);
  const galTotal = Math.round(safeFiniteNumber(galaxySweep.total_systems, 400) ?? 400);
  const uniExploredRaw =
    safeFiniteNumber(
      galaxySweep.universe_explored_systems ?? galaxySweep.explored_systems,
      0
    ) ?? 0;
  const uniTotalRaw = Math.max(
    1,
    safeFiniteNumber(galaxySweep.universe_total_systems, 5600) ?? 5600
  );
  const uniPct = Math.max(
    0,
    Math.min(
      100,
      safeFiniteNumber(
        galaxySweep.universe_coverage_pct,
        (uniExploredRaw / uniTotalRaw) * 100
      ) ?? 0
    )
  ).toFixed(1);
  const phaseLabel =
    String(galaxySweep.scan_phase || "").toLowerCase() === "targeted_refresh"
      ? "Targeted Refresh"
      : "Exploratory";
  return `GAL ${galPct}% (${galExplored}/${galTotal}) · UNI ${uniPct}% (${Math.round(uniExploredRaw)}/${Math.round(uniTotalRaw)}) · ${phaseLabel}`;
}
