import {
  GENERATED_SETTINGS_SCHEMA,
  type SettingGroupSchemaPayload,
  type SettingItemSchemaPayload,
  type SettingOptionPayload,
} from "../../types/generated";

export type ConfigControlKind = "select" | "number" | "text-seed" | "checkbox" | "planet-select";

export type SelectOptionSpec = SettingOptionPayload;

export interface ConfigItemSpec {
  key: string;
  id: string;
  title: string;
  boundsLabel: string;
  description: string;
  control: ConfigControlKind;
  defaultValue: string | number | boolean | null;
  options?: SelectOptionSpec[];
  min?: number;
  max?: number;
  step?: number;
  placeholder?: string;
}

export interface ConfigGroupSpec {
  id: string;
  headingId: string;
  title: string;
  subtitle: string;
  items: ConfigItemSpec[];
}

function toConfigItemSpec(raw: SettingItemSchemaPayload): ConfigItemSpec {
  const control = (
    raw.control === "select" ||
    raw.control === "number" ||
    raw.control === "checkbox" ||
    raw.control === "planet-select" ||
    raw.control === "text-seed"
      ? raw.control
      : "number"
  ) as ConfigControlKind;
  const defaultValue: string | number | boolean | null =
    typeof raw.defaultValue === "string" ||
    typeof raw.defaultValue === "number" ||
    typeof raw.defaultValue === "boolean"
      ? raw.defaultValue
      : null;
  return {
    key: raw.key,
    id: raw.id,
    title: raw.title,
    boundsLabel: raw.boundsLabel,
    description: raw.description,
    control,
    defaultValue,
    ...(raw.options ? { options: raw.options } : {}),
    ...(raw.min !== undefined ? { min: raw.min } : {}),
    ...(raw.max !== undefined ? { max: raw.max } : {}),
    ...(raw.step !== undefined ? { step: raw.step } : {}),
  };
}

function toConfigGroupSpec(raw: SettingGroupSchemaPayload): ConfigGroupSpec {
  return {
    id: raw.id,
    headingId: raw.headingId,
    title: raw.title,
    subtitle: raw.subtitle,
    items: raw.items.map(toConfigItemSpec),
  };
}

/** Canonical StrategyConfig UI groups generated from `spaceinvasion.core.settings`. */
export const CONFIG_GROUPS: ConfigGroupSpec[] =
  GENERATED_SETTINGS_SCHEMA.groups.map(toConfigGroupSpec);

export const EXTRA_STRATEGY_SETTING_SPECS: Record<string, ConfigItemSpec> = {
  ai_resource_spare_pct: {
    key: "ai_resource_spare_pct",
    id: "cfg-ai_resource_spare_pct",
    title: "AI Spare Resource Budget (%)",
    boundsLabel: "default: 10% · 0–90%",
    description:
      "Soft-reserve percentage of rolling hourly empire income set aside for Fleet Planner ship-unlock/combat research and autonomous shipyard hull production while AI-Planner guides buildings and economy research. Urgent 0-count replacements for Spy probes (s10) and transports (s1/s2) remain exempt so scouting and hauling never stall. Accepts floats from 0 to 90%.",
    control: "number",
    defaultValue: 10,
    min: 0,
    max: 90,
    step: 1,
  },
};

export const CONFIG_ITEM_BY_KEY: Record<string, ConfigItemSpec> = Object.fromEntries(
  CONFIG_GROUPS.flatMap((g) => g.items.map((item) => [item.key, item]))
);

export const STRATEGY_SETTING_BY_KEY: Record<string, ConfigItemSpec> = {
  ...CONFIG_ITEM_BY_KEY,
  ...EXTRA_STRATEGY_SETTING_SPECS,
};

export function getStrategySettingSpec(key: string): ConfigItemSpec {
  const known = STRATEGY_SETTING_BY_KEY[key];
  if (known) return known;
  return {
    key,
    id: `cfg-${key}`,
    title: key,
    boundsLabel: "config parameter",
    description: `Configures the ${key} strategy setting persisted to StrategyConfig in PostgreSQL.`,
    control: "number",
    defaultValue: "",
  };
}

export const CONFIG_BOOL_KEYS: string[] = [...GENERATED_SETTINGS_SCHEMA.bool_keys];

export const CONFIG_NUMBER_DEFAULTS: Record<string, number> = {
  ...GENERATED_SETTINGS_SCHEMA.number_defaults,
};
