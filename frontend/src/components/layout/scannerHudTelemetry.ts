import { safeFiniteNumber } from "../../api/httpClient";
import type { DashboardPayload, PlanetData, QueueJobData } from "../../types/api";

export interface HourlyProdPoint {
  minuteBucket: number;
  pig: number;
  met: number;
  kry: number;
  spi: number;
  total: number;
  speq: number;
}

export interface LootRecycleBucket {
  hourBucket: number;
  stolen: number;
  recycled: number;
  total: number;
}

export interface HourlyProdSummary {
  series: HourlyProdPoint[];
  latest: HourlyProdPoint;
  arcPct: number;
}

export interface LootRecycleSummary {
  buckets: LootRecycleBucket[];
  totalStolen: number;
  totalRecycled: number;
  grandTotal: number;
  windowHours: number;
  arcPct: number;
}

export function clampPct(val: unknown): number {
  const n = safeFiniteNumber(val, 0) ?? 0;
  return Math.max(0, Math.min(100, n));
}

export function fmtIntLocal(n: unknown): string {
  if (n === null || n === undefined || n === "") return "0";
  const v = safeFiniteNumber(n, null);
  if (v === null) return "—";
  const clean = Math.abs(v) < 1e-3 && v < 0 && v > -0.05 ? 0 : v === 0 ? 0 : v;
  const formatted = clean.toLocaleString("en-US");
  return formatted === "-0" ? "0" : formatted;
}

export function fmtCompactLocal(val: unknown): string {
  const n = safeFiniteNumber(val, null);
  if (n === null) return "—";
  const absN = Math.abs(n);
  if (absN >= 1e6) return `${(n / 1e6).toFixed(2)}M`;
  if (absN >= 1e4) return `${(n / 1e3).toFixed(1)}k`;
  if (Number.isInteger(n)) return fmtIntLocal(n);
  return absN < 0.05 ? "0.0" : n.toFixed(1);
}

export function fmtImprovementPct(rawPct: unknown, baseFit?: unknown, bestFit?: unknown): string {
  let pct = safeFiniteNumber(rawPct, null);
  const base = safeFiniteNumber(baseFit, null);
  const best = safeFiniteNumber(bestFit, null);
  if (pct === null && base !== null && best !== null) {
    if (Math.abs(base) > 1e-6) {
      pct = ((best - base) / Math.abs(base)) * 100;
    } else if (Math.abs(best) <= 1e-12) {
      pct = 0;
    } else {
      pct = best > 0 ? 999.9 : -999.9;
    }
  }
  if (pct === null) return "—";
  if (pct > 999.9) return ">+999.9%";
  if (pct < -999.9) return "<-999.9%";
  if (Math.abs(pct) < 0.05) pct = 0;
  return `${pct >= 0 ? "+" : ""}${pct.toFixed(1)}%`;
}

export function geneShortTag(kind: unknown): string {
  const k = String(kind || "").trim().toUpperCase();
  if (!k) return "";
  if (k === "BUILD") return "BLD";
  if (k === "DEMOLISH_BUILDING" || k === "DEMOLISH") return "DEM";
  if (k === "WAIT_RESOURCES") return "WAIT";
  if (k === "RESEARCH") return "RES";
  if (k === "BUILD_SHIP" || k === "SHIPS") return "SHP";
  if (k === "ATTACK" || k === "RAID") return "ATK";
  if (k === "SPY") return "SPY";
  if (k === "TRANSPORT") return "TRN";
  if (k === "STATION") return "STN";
  if (k === "RECYCLE") return "REC";
  if (k === "COLONIZE") return "COL";
  if (k === "SCAN_GALAXY" || k === "GALAXY_SCAN") return "SCN";
  if (k === "THROTTLE_BUILDING") return "B2%";
  if (k === "CLAIM_TUTORIAL") return "TUT";
  return k.slice(0, 4);
}

export function normalizeHourlyProdPoint(
  raw: Record<string, any> | null | undefined
): HourlyProdPoint | null {
  if (!raw || typeof raw !== "object") return null;
  const pig = Math.max(0, safeFiniteNumber(raw.pig_iron_per_hour ?? raw.pig_iron, 0) ?? 0);
  const met = Math.max(0, safeFiniteNumber(raw.metal_per_hour ?? raw.metal, 0) ?? 0);
  const kry = Math.max(0, safeFiniteNumber(raw.kryptonite_per_hour ?? raw.kryptonite, 0) ?? 0);
  const spi = Math.max(0, safeFiniteNumber(raw.spice_per_hour ?? raw.spice, 0) ?? 0);
  const sum = pig + met + kry + spi;
  const total = Math.max(0, safeFiniteNumber(raw.total_per_hour, sum) ?? sum);
  const speq = Math.max(0, safeFiniteNumber(raw.speq_per_hour, 0) ?? 0);
  const minuteBucket = Math.round(
    safeFiniteNumber(raw.minute_bucket ?? raw.recorded_at, 0) ?? 0
  );
  return { minuteBucket, pig, met, kry, spi, total, speq };
}

export function countActiveBuildingPlanets(
  planets: PlanetData[],
  activeQueues: QueueJobData[],
  activePlanetId: number | null | undefined
): { active: number; total: number } {
  const normalPlanets = planets.filter(
    (p) => String(p?.planet_type || "normal").toLowerCase() === "normal"
  );
  const scopedPlanets = normalPlanets.length > 0 ? normalPlanets : planets;
  const knownPidSet = new Set<number>();
  const coordsToPid = new Map<string, number>();

  for (const pl of scopedPlanets) {
    const pid = Math.round(safeFiniteNumber(pl?.planet_id, 0) ?? 0);
    if (pid > 0) {
      knownPidSet.add(pid);
      const c = String(pl?.coords || "").trim();
      if (c) coordsToPid.set(c, pid);
    }
  }

  const nowSec = Math.floor(Date.now() / 1000);
  const activeBuilderKeys = new Set<string>();

  for (const job of activeQueues) {
    if (!job || String(job.category || "").toLowerCase() !== "building") continue;
    const finTs = safeFiniteNumber(job.finish_ts, null);
    const remSec = safeFiniteNumber(job.remaining_seconds, null);
    let isActive = false;
    if (finTs !== null && finTs > 0) {
      isActive = finTs > nowSec;
    } else if (remSec !== null) {
      isActive = remSec > 0;
    } else {
      const remTxt = String(job.remaining_text || "").trim().toLowerCase();
      isActive = remTxt.length > 0 && remTxt !== "0s" && remTxt !== "done" && remTxt !== "finished";
    }
    if (!isActive) continue;

    const jobPid = Math.round(safeFiniteNumber(job.planet_id, 0) ?? 0);
    const jobCoords = String(job.origin_coords || job.planet_coords || "").trim();

    if (jobPid > 0 && (knownPidSet.size === 0 || knownPidSet.has(jobPid))) {
      activeBuilderKeys.add(`pid:${jobPid}`);
    } else if (jobCoords && coordsToPid.has(jobCoords)) {
      activeBuilderKeys.add(`pid:${coordsToPid.get(jobCoords)!}`);
    } else if (jobCoords && knownPidSet.size === 0) {
      activeBuilderKeys.add(`coords:${jobCoords}`);
    } else if (jobPid === 0 && !jobCoords) {
      const fallbackPid = Math.round(safeFiniteNumber(activePlanetId, 0) ?? 0);
      if (fallbackPid > 0 && (knownPidSet.size === 0 || knownPidSet.has(fallbackPid))) {
        activeBuilderKeys.add(`pid:${fallbackPid}`);
      }
    }
  }

  const total = scopedPlanets.length;
  const active = total > 0 ? Math.min(total, activeBuilderKeys.size) : activeBuilderKeys.size;
  return { active, total };
}

export function computeHourlyProdData(
  dashboard: DashboardPayload | null | undefined
): HourlyProdSummary {
  const dashRec = dashboard as Record<string, any> | null | undefined;
  const rawHist = Array.isArray(dashRec?.hourly_production_history)
    ? dashRec!.hourly_production_history
    : [];
  const series: HourlyProdPoint[] = [];
  for (const item of rawHist) {
    const pt = normalizeHourlyProdPoint(item);
    if (pt) series.push(pt);
  }

  const curPoint = normalizeHourlyProdPoint(dashRec?.current_hourly_production);
  if (curPoint) {
    const last = series[series.length - 1];
    if (!last) {
      if (curPoint.total > 0 || curPoint.speq > 0) {
        series.push(curPoint);
      }
    } else if (curPoint.minuteBucket > last.minuteBucket) {
      series.push(curPoint);
    } else if (curPoint.minuteBucket === last.minuteBucket && curPoint.total !== last.total) {
      series[series.length - 1] = curPoint;
    }
  }

  // Fallback to live planet hourly production if SQLite history is not populated yet
  if (series.length === 0 && Array.isArray(dashboard?.state?.planets)) {
    let pig = 0;
    let met = 0;
    let kry = 0;
    let spi = 0;
    for (const pl of dashboard!.state!.planets!) {
      const rates =
        (pl as Record<string, any>).production_per_hour ||
        (pl as Record<string, any>).production ||
        pl.economy_rates;
      if (!rates || typeof rates !== "object") continue;
      pig += Math.max(0, safeFiniteNumber(rates.pig_iron_per_hour ?? rates.pig_iron, 0) ?? 0);
      met += Math.max(0, safeFiniteNumber(rates.metal_per_hour ?? rates.metal, 0) ?? 0);
      kry += Math.max(0, safeFiniteNumber(rates.kryptonite_per_hour ?? rates.kryptonite, 0) ?? 0);
      spi += Math.max(0, safeFiniteNumber(rates.spice_per_hour ?? rates.spice, 0) ?? 0);
    }
    const total = pig + met + kry + spi;
    if (total > 0) {
      series.push({
        minuteBucket: Math.floor(Date.now() / 60000) * 60,
        pig,
        met,
        kry,
        spi,
        total,
        speq: 0,
      });
    }
  }

  const latest =
    curPoint ||
    (series.length > 0
      ? series[series.length - 1]
      : { minuteBucket: 0, pig: 0, met: 0, kry: 0, spi: 0, total: 0, speq: 0 });
  const maxTotal = series.reduce((m, pt) => Math.max(m, pt.total), latest.total);
  const arcPct =
    latest.total <= 0
      ? 0
      : maxTotal > 0
        ? clampPct(Math.max(18, Math.round((latest.total / maxTotal) * 100)))
        : 100;
  return { series, latest, arcPct };
}

export function computeLootRecycleData(
  dashboard: DashboardPayload | null | undefined
): LootRecycleSummary {
  const dashRec = dashboard as Record<string, any> | null | undefined;
  const rawBuckets = Array.isArray(dashRec?.loot_recycle_history)
    ? dashRec!.loot_recycle_history
    : [];
  const buckets: LootRecycleBucket[] = rawBuckets
    .filter((b: any) => b && typeof b === "object")
    .map((b: any) => {
      const stolen = Math.max(
        0,
        safeFiniteNumber(
          b.stolen_total,
          (safeFiniteNumber(b.stolen_pig_iron, 0) ?? 0) +
            (safeFiniteNumber(b.stolen_metal, 0) ?? 0) +
            (safeFiniteNumber(b.stolen_kryptonite, 0) ?? 0) +
            (safeFiniteNumber(b.stolen_spice, 0) ?? 0)
        ) ?? 0
      );
      const recycled = Math.max(
        0,
        safeFiniteNumber(
          b.recycled_total,
          (safeFiniteNumber(b.recycled_pig_iron, 0) ?? 0) +
            (safeFiniteNumber(b.recycled_metal, 0) ?? 0) +
            (safeFiniteNumber(b.recycled_kryptonite, 0) ?? 0) +
            (safeFiniteNumber(b.recycled_spice, 0) ?? 0)
        ) ?? 0
      );
      return {
        hourBucket: Math.round(safeFiniteNumber(b.hour_bucket, 0) ?? 0),
        stolen,
        recycled,
        total: stolen + recycled,
      };
    });

  const summaryObj =
    dashRec?.loot_recycle_summary && typeof dashRec.loot_recycle_summary === "object"
      ? dashRec.loot_recycle_summary
      : null;
  const sumStolen = buckets.reduce((acc, b) => acc + b.stolen, 0);
  const sumRecycled = buckets.reduce((acc, b) => acc + b.recycled, 0);
  const totalStolen = Math.max(
    0,
    safeFiniteNumber(summaryObj?.total_stolen, sumStolen) ?? sumStolen
  );
  const totalRecycled = Math.max(
    0,
    safeFiniteNumber(summaryObj?.total_recycled, sumRecycled) ?? sumRecycled
  );
  const grandTotal = Math.max(
    0,
    safeFiniteNumber(summaryObj?.grand_total, totalStolen + totalRecycled) ??
      totalStolen + totalRecycled
  );
  const windowHours = Math.max(
    1,
    Math.round(safeFiniteNumber(summaryObj?.window_hours, buckets.length || 24) ?? 24)
  );
  const arcPct =
    grandTotal <= 0
      ? 0
      : totalStolen > 0
        ? clampPct(Math.max(15, Math.round((totalStolen / grandTotal) * 100)))
        : 100;
  return { buckets, totalStolen, totalRecycled, grandTotal, windowHours, arcPct };
}
