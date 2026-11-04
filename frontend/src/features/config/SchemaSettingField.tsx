import React from "react";
import type { PlanetData } from "../../types/api";
import {
  ActionGraphParamRow,
  type ActionGraphParamLayout,
} from "../action-graph/ActionGraphParamRow";
import type { ActionGraphParamDoc } from "../action-graph/paramDocs";
import {
  type ConfigControlKind,
  type ConfigItemSpec,
  type SelectOptionSpec,
  getStrategySettingSpec,
} from "./configSchema";

export type SettingFieldVariant = "config" | "panel";

export interface PlanetSelectContext {
  nonRaidPlanets: PlanetData[];
  savedSpyPid: number;
  hasSavedPlanetOption: boolean;
}

export interface SchemaSettingFieldProps {
  /** StrategyConfig key auto-resolved from configSchema.ts */
  settingKey?: string;
  /** Explicit schema spec (e.g. ConfigView group item or panel-specific setting) */
  spec?: ConfigItemSpec;
  /** Explicit doc metadata for non-StrategyConfig settings (Config Optimizer, AI Planner, etc.) */
  doc?: ActionGraphParamDoc;
  /** Layout variant: "config" -> .config-item, "panel" -> ActionGraphParamRow (.ag-param-item) */
  variant: SettingFieldVariant;
  /** Prefix for parameterized DOM IDs: "cfg" | "ag-cfg" | "flt-cfg" | "co-param" | "ai-param" */
  idPrefix?: string;
  /** Explicit DOM ID override (takes precedence over `${idPrefix}-${key}`) */
  id?: string;
  /** Override control type (e.g. "number" for spy_dispatch_planet_id inside compact panel) */
  control?: ConfigControlKind;
  /** Controlled field value (string/number for inputs & selects, boolean for checkboxes) */
  value: string | number | boolean | null | undefined;
  /** Called with the new string or boolean value when edited */
  onChange: (nextValue: any) => void;
  /** Also forward native DOM `onInput` events (used by ConfigView for legacy input compatibility) */
  emitOnInput?: boolean;
  /** Optional select options override (falls back to spec.options) */
  options?: SelectOptionSpec[];
  /** Include current value as a fallback <option> if not present in options */
  includeUnknownOption?: boolean;
  /** Numeric/input overrides (fall back to schema spec bounds) */
  min?: number;
  max?: number;
  step?: number;
  placeholder?: string;
  /** Optional ActionGraphParamRow layout override */
  panelLayout?: ActionGraphParamLayout;
  /** Optional label override for dynamic counts (e.g. colony slot preview toggle) */
  titleOverride?: string;
  /** Colony options context when rendering a `planet-select` control */
  planetSelectContext?: PlanetSelectContext;
}

export function resolveSettingDomId(
  key: string,
  idPrefix?: string,
  explicitId?: string
): string {
  if (explicitId) return explicitId;
  if (idPrefix) return `${idPrefix}-${key}`;
  return `cfg-${key}`;
}

export const SchemaSettingField: React.FC<SchemaSettingFieldProps> = ({
  settingKey,
  spec,
  doc,
  variant,
  idPrefix,
  id,
  control,
  value,
  onChange,
  emitOnInput = false,
  options,
  includeUnknownOption = false,
  min,
  max,
  step,
  placeholder,
  panelLayout,
  titleOverride,
  planetSelectContext,
}) => {
  const resolvedSpec: ConfigItemSpec | null =
    spec ?? (settingKey ? getStrategySettingSpec(settingKey) : null);

  const key = resolvedSpec?.key ?? doc?.paramKey ?? settingKey ?? "";
  const domId = resolveSettingDomId(key, idPrefix, id ?? (idPrefix ? undefined : resolvedSpec?.id));
  const resolvedControl: ConfigControlKind =
    control ??
    (resolvedSpec?.control === "planet-select" && !planetSelectContext
      ? "number"
      : resolvedSpec?.control ?? "number");

  const resolvedOptions = options ?? resolvedSpec?.options ?? [];
  const resolvedMin = min !== undefined ? min : resolvedSpec?.min;
  const resolvedMax = max !== undefined ? max : resolvedSpec?.max;
  const resolvedStep = step !== undefined ? step : resolvedSpec?.step;
  const resolvedPlaceholder =
    placeholder !== undefined ? placeholder : resolvedSpec?.placeholder;

  const resolvedDoc: ActionGraphParamDoc = doc ?? {
    title: resolvedSpec?.title ?? key,
    paramKey: key,
    boundsLabel: resolvedSpec?.boundsLabel ?? "config parameter",
    description: resolvedSpec?.description ?? "",
  };

  let controlEl: React.ReactNode;
  if (resolvedControl === "select") {
    const strVal = String(value ?? resolvedSpec?.defaultValue ?? "");
    const hasCurrentOpt = resolvedOptions.some((opt) => opt.value === strVal);
    controlEl = (
      <select
        id={domId}
        value={strVal}
        onChange={(e) => onChange(e.target.value)}
        onInput={
          emitOnInput
            ? (e) => onChange((e.target as HTMLSelectElement).value)
            : undefined
        }
      >
        {resolvedOptions.map((opt) => (
          <option key={opt.value} value={opt.value}>
            {opt.label}
          </option>
        ))}
        {includeUnknownOption && !hasCurrentOpt && strVal ? (
          <option value={strVal}>{strVal}</option>
        ) : null}
      </select>
    );
  } else if (resolvedControl === "planet-select" && planetSelectContext) {
    const { nonRaidPlanets, savedSpyPid, hasSavedPlanetOption } = planetSelectContext;
    controlEl = (
      <select
        id={domId}
        value={String(value ?? "0")}
        onChange={(e) => onChange(e.target.value)}
        onInput={
          emitOnInput
            ? (e) => onChange((e.target as HTMLSelectElement).value)
            : undefined
        }
      >
        <option value="0">0 — Auto (Active / Closest Colony with Probes)</option>
        {nonRaidPlanets.map((p) => (
          <option key={p.planet_id} value={String(p.planet_id)}>
            {`${p.name || "Colony"} [${p.coords || "--"}] (#${p.planet_id} · s10: ${p.ships?.s10 ?? 0})`}
          </option>
        ))}
        {!hasSavedPlanetOption ? (
          <option value={String(savedSpyPid)}>{`Colony #${savedSpyPid} (Saved)`}</option>
        ) : null}
      </select>
    );
  } else if (resolvedControl === "checkbox") {
    controlEl = (
      <input
        type="checkbox"
        className="config-checkbox"
        id={domId}
        checked={Boolean(value)}
        onChange={(e) => onChange(e.target.checked)}
      />
    );
  } else if (resolvedControl === "text-seed") {
    controlEl = (
      <input
        type="text"
        inputMode="numeric"
        className="config-seed-input"
        id={domId}
        placeholder={resolvedPlaceholder || "Auto"}
        value={String(value ?? "")}
        onChange={(e) => onChange(e.target.value)}
        onInput={
          emitOnInput
            ? (e) => onChange((e.target as HTMLInputElement).value)
            : undefined
        }
      />
    );
  } else {
    controlEl = (
      <input
        type="number"
        id={domId}
        min={resolvedMin}
        max={resolvedMax}
        step={resolvedStep}
        placeholder={resolvedPlaceholder}
        value={String(value ?? resolvedSpec?.defaultValue ?? "")}
        onChange={(e) => onChange(e.target.value)}
        onInput={
          emitOnInput
            ? (e) => onChange((e.target as HTMLInputElement).value)
            : undefined
        }
      />
    );
  }

  if (variant === "config") {
    return (
      <div className="config-item">
        <div className="config-item-info">
          <div className="config-item-head">
            <label className="config-item-label" htmlFor={domId}>
              {titleOverride || resolvedDoc.title}
            </label>
            <code className="config-item-key tabular">{resolvedDoc.paramKey}</code>
            <span className="config-item-bounds tabular">{resolvedDoc.boundsLabel}</span>
          </div>
          <p className="config-item-desc">{resolvedDoc.description}</p>
        </div>
        <div className="config-item-control">{controlEl}</div>
      </div>
    );
  }

  const inferredLayout: ActionGraphParamLayout =
    panelLayout ??
    (resolvedControl === "checkbox"
      ? "toggle"
      : resolvedControl === "select" || resolvedControl === "planet-select"
        ? "stack"
        : "inline");

  return (
    <ActionGraphParamRow
      doc={resolvedDoc}
      htmlFor={domId}
      layout={inferredLayout}
      titleOverride={titleOverride}
    >
      {controlEl}
    </ActionGraphParamRow>
  );
};

export default SchemaSettingField;
