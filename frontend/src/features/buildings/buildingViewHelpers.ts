import { finiteNum, fmtInt } from "../../utils/formatters";

export const BUILDING_CATEGORY_TABS = [
  { id: "all", bcat: "buildings", label: "All 21" },
  { id: "mines", bcat: "mines", label: "Mines" },
  { id: "energy", bcat: "energy", label: "Energy" },
  { id: "infra", bcat: "infra", label: "Infra" },
  { id: "storage", bcat: "storage", label: "Storage" },
  { id: "special", bcat: "special", label: "Special" },
  { id: "defense", bcat: "defense", label: "Defense" },
] as const;

export const VALID_BUILDING_CATEGORIES = new Set<string>(
  BUILDING_CATEGORY_TABS.map((t) => t.id)
);

const SPECIALIZATION_LABELS: Record<string, string> = {
  metal: "Metal",
  kryptonite: "Kryptonite",
  spice: "Spice",
  shipyard: "Shipyard",
  spice_shipyard: "Spice + Shipyard",
};

export function formatSpecializationLabel(raw?: string | null): string {
  const key = String(raw || "")
    .trim()
    .toLowerCase();
  if (!key || key === "auto") return "";
  return (
    SPECIALIZATION_LABELS[key] ||
    key.replace(/_/g, " ").replace(/\b\w/g, (c) => c.toUpperCase())
  );
}

export function resolvePlanetGoalEntry(
  coGoals: Record<string, any> | null | undefined,
  planetId: number | null | undefined,
  coords: string | null | undefined
): Record<string, any> | null {
  if (!coGoals || typeof coGoals !== "object") return null;
  const byPid = coGoals.by_planet_id;
  if (planetId != null && byPid && typeof byPid === "object" && byPid[String(planetId)]) {
    return byPid[String(planetId)];
  }
  const rawCoords = String(coords || "").trim();
  const byCoords = coGoals.by_coords;
  if (rawCoords && byCoords && typeof byCoords === "object") {
    if (byCoords[rawCoords]) return byCoords[rawCoords];
    const m = rawCoords.match(/^(\d{1,2}):(\d{1,3}):(\d{1,2})$/);
    if (m) {
      const norm = `${m[1].padStart(2, "0")}:${m[2].padStart(3, "0")}:${m[3].padStart(2, "0")}`;
      if (byCoords[norm]) return byCoords[norm];
    }
  }
  return null;
}

export function buildDefenseRows(
  etaData: Record<string, any> | null | undefined,
  cmbData: Record<string, any> | null | undefined
): Array<Record<string, any>> {
  const blds = Array.isArray(etaData?.buildings) ? etaData!.buildings : [];
  const defBuildings = blds.filter(
    (b: any) => b?.building_id === 15 || b?.building_id === 16 || b?.category === "defense"
  );
  const turrets = Array.isArray(cmbData?.defenses)
    ? cmbData!.defenses.map((d: any) => ({
        key: d.key,
        unit_id: d.unit_id,
        name: d.name,
        category: "defense",
        is_defense_turret: true,
        current_level: finiteNum(d.owned, 0),
        next_level: finiteNum(d.owned, 0) + 1,
        cost: d.base_cost || {},
        missing: {},
        missing_total: 0,
        build_time_text: "Shipyard",
        wait_eta: "—",
        reach_eta: `${fmtInt(d.owned ?? 0)} owned`,
        wait_hours: 0,
        attack: d.attack,
        shield: d.shield,
        hull: d.hull,
        horizons: [],
      }))
    : [];
  return [...defBuildings, ...turrets];
}
