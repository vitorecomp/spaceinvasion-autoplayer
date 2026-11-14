import { safeFiniteNumber } from "../../api/httpClient";
import type {
  ConfigOptimizerGetPayload,
  ConfigOptimizerPlanetRecord,
  ConfigOptimizerResultData,
  DashboardPayload,
  PlanetData,
} from "../../types/api";
import type { EditablePlanetModalModel } from "./ConfigOptimizerPlanetModal";
import {
  type NormalizedPlanetRow,
  type OwnedDashboardPlanetRow,
  type ResearchPlanRow,
  resolveLockedSpecForRow,
} from "./configOptimizerDraft";
import {
  BUILDING_NAMES,
  RESEARCH_KEYS,
  RESEARCH_NAMES,
  formatDurationSecondsCompact,
  normalizeSpecializationKey,
} from "./plannerHelpers";

export function normalizePlanetRows(
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
    const slot = Math.max(
      1,
      Math.round(safeFiniteNumber(p.slot ?? p.slot_index, idx + 1) ?? idx + 1)
    );
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
    let totalToBuild = 0;
    let totalToDemolish = 0;

    if (Array.isArray(p.buildings)) {
      for (const b of p.buildings) {
        const bk = String(b.key || b.building_key || "");
        if (!bk) continue;
        const built = Math.max(
          0,
          Math.round(safeFiniteNumber(b.built_level ?? b.current_level ?? rawBuilt[bk], 0) ?? 0)
        );
        const target = Math.max(
          0,
          Math.round(safeFiniteNumber(b.target_level ?? b.level ?? built, built) ?? built)
        );
        const toBuild = Math.max(
          0,
          Math.round(safeFiniteNumber(b.levels_to_build, target - built) ?? target - built)
        );
        const toDemolish = Math.max(
          0,
          Math.round(safeFiniteNumber(b.levels_to_demolish, built - target) ?? built - target)
        );
        const resLvl = reservedFloors[bk] || 0;
        builtLevels[bk] = built;
        targetLevels[bk] = target;
        builtSum += built;
        totalToBuild += toBuild;
        totalToDemolish += toDemolish;
        const ltSec = safeFiniteNumber(
          b.last_level_seconds ?? b.last_level_time_s ?? b.last_level_build_time_s,
          null
        );
        const ltText = String(
          b.last_level_time ||
            b.last_level_build_time ||
            b.last_level_build_time_text ||
            (ltSec !== null ? formatDurationSecondsCompact(ltSec) : "—")
        );
        if (toBuild > 0 && ltSec !== null && (maxLastSec === null || ltSec > maxLastSec)) {
          maxLastSec = ltSec;
          maxLastTxt = ltText;
        } else if (toBuild > 0 && !maxLastTxt && ltText && ltText !== "—") {
          maxLastTxt = ltText;
        }
        buildingsDetail.push({
          key: bk,
          name: String(b.name || b.building || BUILDING_NAMES[bk] || bk),
          built,
          target,
          toBuild,
          toDemolish,
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
              typeof entry === "object" && entry
                ? entry.built_level ?? entry.current_level
                : rawBuilt[bk],
              0
            ) ?? 0
          )
        );
        const target = Math.max(
          0,
          Math.round(
            safeFiniteNumber(
              typeof entry === "object" && entry ? entry.target_level ?? entry.level : entry,
              built
            ) ?? built
          )
        );
        const toBuild = Math.max(0, target - built);
        const toDemolish = Math.max(0, built - target);
        const resLvl = reservedFloors[bk] || 0;
        builtLevels[bk] = built;
        targetLevels[bk] = target;
        builtSum += built;
        totalToBuild += toBuild;
        totalToDemolish += toDemolish;
        buildingsDetail.push({
          key: bk,
          name: BUILDING_NAMES[bk] || bk,
          built,
          target,
          toBuild,
          toDemolish,
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
      rawGameName || (!customName ? resolvedName : String(dashPlanet?.name || p.name || coords));

    const prodLevelsToDemolish = Math.max(
      0,
      Math.round(safeFiniteNumber(prod.levels_to_demolish, totalToDemolish) ?? totalToDemolish)
    );
    const prodDemolishWhenFull = Math.max(
      0,
      Math.round(
        safeFiniteNumber(
          prod.demolitions_when_full,
          Math.min(prodLevelsToDemolish, Math.max(0, builtSum + totalToBuild - fieldsMax))
        ) ?? 0
      )
    );

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
      levelsToDemolish: prodLevelsToDemolish,
      demolitionsWhenFull: prodDemolishWhenFull,
      buildingsDetail,
    };
  });
}

export function buildOwnedDashboardPlanets(
  dashboardPlanets: PlanetData[],
  customPlanetNames: Record<string, string>
): OwnedDashboardPlanetRow[] {
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
}

export function buildResearchPlanRows(
  activeResult: ConfigOptimizerResultData | null,
  dashboard: DashboardPayload | null
): ResearchPlanRow[] {
  const ownMap = activeResult?.inputs?.own_research_levels || dashboard?.state?.researches || {};
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
}

export function buildEditableModalPlanet(opts: {
  modalPlanetSlot: number | null;
  planetRows: NormalizedPlanetRow[];
  dashboardPlanets: PlanetData[];
  customPlanetNames: Record<string, string>;
  planetSpecializations: Record<string, string>;
  centralPlanetSlot: string;
  reservedFloorsMap: Record<string, Record<string, number>>;
}): EditablePlanetModalModel | null {
  const {
    modalPlanetSlot,
    planetRows,
    dashboardPlanets,
    customPlanetNames,
    planetSpecializations,
    centralPlanetSlot,
    reservedFloorsMap,
  } = opts;
  if (modalPlanetSlot === null) return null;
  const fromPlan = planetRows.find((r) => r.slot === modalPlanetSlot);
  if (fromPlan) {
    return {
      slot: fromPlan.slot,
      planetId: fromPlan.planetId,
      name: fromPlan.name,
      gameName: fromPlan.gameName,
      hasCustomName: fromPlan.hasCustomName,
      coords: fromPlan.coords,
      isPlaceholder: fromPlan.isPlaceholder,
      isCentral: fromPlan.isCentral,
      temperature: fromPlan.temperature,
      fieldsUsedCurrent: fromPlan.fieldsUsedCurrent,
      fieldsMax: fromPlan.fieldsMax,
      detectedSpecialization: fromPlan.detectedSpecialization,
      lockedSpecialization: resolveLockedSpecForRow(fromPlan, planetSpecializations),
      builtLevels: fromPlan.builtLevels,
      targetLevels: fromPlan.targetLevels,
      reservedFloors: fromPlan.reservedFloors,
    };
  }
  const dashNorms = dashboardPlanets.filter((p) => {
    const pid = Math.round(safeFiniteNumber(p?.planet_id, 0) ?? 0);
    const ptype = String(p?.planet_type || "normal").toLowerCase();
    return pid > 0 && ptype === "normal";
  });
  const rawPl = dashNorms[modalPlanetSlot - 1];
  if (!rawPl) return null;
  const pid = Math.round(safeFiniteNumber(rawPl.planet_id, 0) ?? 0);
  const coords = String(rawPl.coords || `Planet ${pid}`);
  const cName = customPlanetNames[String(pid)] || "";
  const rawGame = String((rawPl as Record<string, any>).game_name || "").trim();
  const dispName = cName || String(rawPl.name || coords);
  const gName = rawGame || (!cName ? dispName : String(rawPl.name || coords));
  const builtLvls: Record<string, number> = {};
  if (rawPl.buildings && typeof rawPl.buildings === "object") {
    for (const [bk, lv] of Object.entries(rawPl.buildings)) {
      builtLvls[bk] = Math.max(0, Math.round(safeFiniteNumber(lv, 0) ?? 0));
    }
  }
  const fMax = Math.max(1, Math.round(safeFiniteNumber(rawPl.fields_max, 300) ?? 300));
  const fUsed = Math.max(0, Math.round(safeFiniteNumber(rawPl.fields_used, 0) ?? 0));
  const temp = safeFiniteNumber(rawPl.temperature, null);
  return {
    slot: modalPlanetSlot,
    planetId: pid,
    name: dispName,
    gameName: gName,
    hasCustomName: Boolean(cName),
    coords,
    isPlaceholder: false,
    isCentral: modalPlanetSlot === Number(centralPlanetSlot),
    temperature: temp !== null ? Math.round(temp) : null,
    fieldsUsedCurrent: fUsed > 0 ? fUsed : null,
    fieldsMax: fMax,
    detectedSpecialization: "balanced",
    lockedSpecialization: normalizeSpecializationKey(
      planetSpecializations[String(pid)] || planetSpecializations[coords] || "auto"
    ),
    builtLevels: builtLvls,
    targetLevels: { ...builtLvls },
    reservedFloors: reservedFloorsMap[String(pid)] || reservedFloorsMap[coords] || {},
  };
}

export function patchOptDataAfterRename(
  prev: ConfigOptimizerGetPayload | null,
  planetId: number,
  resolvedDisplayName: string,
  resolvedGameName: string,
  nextCustomNames: Record<string, string>
): ConfigOptimizerGetPayload | null {
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
}

export function patchDashboardAfterRename(
  prevDash: any,
  planetId: number,
  resolvedDisplayName: string,
  resolvedGameName: string,
  trimmed: string,
  cleared: boolean,
  nextCustomNames: Record<string, string>
): any {
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
  const prevBldEta = prevDash.buildings_eta;
  const nextBldEta =
    prevBldEta &&
    prevBldEta.planet &&
    Math.round(safeFiniteNumber(prevBldEta.planet.planet_id, 0) ?? 0) === planetId
      ? {
          ...prevBldEta,
          planet: {
            ...prevBldEta.planet,
            name: resolvedDisplayName,
            custom_name: cleared ? null : trimmed,
            game_name: resolvedGameName || prevBldEta.planet.game_name,
          },
        }
      : prevBldEta;
  return {
    ...prevDash,
    custom_planet_names: nextCustomNames,
    ...(prevBldEta ? { buildings_eta: nextBldEta } : {}),
    state: prevDash.state
      ? {
          ...prevDash.state,
          planets: nextPlanets,
          ...(prevActivePl ? { active_planet: nextActivePl } : {}),
        }
      : prevDash.state,
  };
}
