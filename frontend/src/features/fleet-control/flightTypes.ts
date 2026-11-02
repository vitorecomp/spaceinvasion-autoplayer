import type { PlanetData, QueueJobData, ResourceBundle } from "../../types/api";
import { MQ_SHIPS_CATALOG } from "../operations/planActions";

export interface FlightQueueJob extends QueueJobData {
  mission?: string | null;
  kind?: string | null;
  label?: string | null;
  direction?: string | null;
  is_returning?: boolean | null;
  returning?: boolean | null;
  ships?: Record<string, unknown> | string | null;
  target_coords?: string | null;
  dest_coords?: string | null;
  destination_coords?: string | null;
  target_name?: string | null;
  dest_name?: string | null;
  origin_name?: string | null;
  expected_cargo?: ResourceBundle | Record<string, unknown> | null;
  delivered_cargo?: ResourceBundle | Record<string, unknown> | null;
  delivered?: boolean | null;
}

export type FlightMission =
  | "ATTACK"
  | "RECYCLE"
  | "TRANSPORT"
  | "SPY"
  | "COLONIZE"
  | "ESCAPE"
  | "STATION"
  | "TRANSFER"
  | "OTHER";

export type FlightMissionFilter = "all" | "attack" | "recycle" | "transport" | "spy" | "other";

export type FlightDirection = "outbound" | "returning";
export type FlightDirectionFilter = "all" | FlightDirection;

export type FlightCargoKind = "EXPECTED" | "DELIVERED" | "LOOT" | "HARVEST" | "CARGO";

export interface NormalizedBundle {
  bundle: ResourceBundle;
  total: number;
  nonZeroCount: number;
}

export interface FlightCargoDisplay {
  kind: FlightCargoKind;
  bundle: ResourceBundle | null;
  total: number;
  inline: string;
  multiRes: boolean;
  isEmpty: boolean;
}

export interface PlanetLookupEntry {
  planet: PlanetData;
  displayName: string;
  rawCoords: string;
  normCoords: string;
}

export interface PlanetLookup {
  byId: Map<number, PlanetLookupEntry>;
  byNormCoords: Map<string, PlanetLookupEntry>;
  ordered: PlanetLookupEntry[];
}

export interface ParsedFlightRow {
  id: string;
  mission: FlightMission;
  missionFilter: Exclude<FlightMissionFilter, "all">;
  missionLabel: string;
  missionBadgeClass: string;
  direction: FlightDirection;
  originName: string;
  originCoords: string;
  originNormCoords: string;
  originPlanetId: number | null;
  originIsOwn: boolean;
  targetName: string;
  targetCoords: string;
  targetNormCoords: string;
  targetPlanetId: number | null;
  targetIsOwn: boolean;
  shipsText: string;
  labelText: string;
  cargo: FlightCargoDisplay | null;
  finishTs: number | null;
  remainingSec: number | null;
  countdownText: string;
  finishClockText: string;
  raw: FlightQueueJob;
}

export interface FlightSummary {
  total: number;
  outboundCount: number;
  returningCount: number;
  missionCounts: Record<FlightMissionFilter, number>;
  expectedBundle: ResourceBundle;
  expectedTotal: number;
  expectedCount: number;
  deliveredBundle: ResourceBundle;
  deliveredTotal: number;
  deliveredCount: number;
}

export const NON_FLIGHT_CATEGORIES = new Set([
  "building",
  "research",
  "shipyard",
  "defense",
  "defence",
]);

export const FLIGHT_MISSION_CATEGORIES = new Set([
  "ATTACK",
  "RAID",
  "RECYCLE",
  "TRANSPORT",
  "SPY",
  "SPIONAGE",
  "COLONIZE",
  "ESCAPE",
  "ESCAPE_FLIGHT",
  "SAVE",
  "FLEET_SAVE",
  "STATION",
  "TRANSFER",
  "EVENT",
  "PARTICLE",
  "FLEET_FLIGHT",
]);

export const SHIPYARD_DETAIL_PREFIXES = [
  "shipyard",
  "weapons factory",
  "defense",
  "defence",
  "in construction",
  "researching",
  "constructing",
];

export const COORD_REGEX = /(\d{1,2})\s*:\s*(\d{1,3})\s*:\s*(\d{1,2})/;

export const SHIP_NAME_BY_TOKEN: Record<string, string> = (() => {
  const map: Record<string, string> = {};
  for (const item of MQ_SHIPS_CATALOG) {
    map[item.key.toLowerCase()] = item.name;
    map[String(item.id)] = item.name;
    map[item.name.toLowerCase()] = item.name;
    map[item.name.toLowerCase().replace(/[^a-z0-9]+/g, "_")] = item.name;
  }
  return map;
})();
