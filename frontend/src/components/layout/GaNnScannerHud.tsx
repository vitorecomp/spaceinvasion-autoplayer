import React, { useEffect, useMemo, useRef } from "react";
import { feedbackKindForError, safeFiniteNumber } from "../../api/httpClient";
import { useConsole } from "../../context/ConsoleContext";
import { computeColonyGateInfo } from "../../features/action-graph/plannerHelpers";
import type { PlanetData, QueueJobData } from "../../types/api";

function clampPct(val: unknown): number {
  const n = safeFiniteNumber(val, 0) ?? 0;
  return Math.max(0, Math.min(100, n));
}

function fmtIntLocal(n: unknown): string {
  if (n === null || n === undefined || n === "") return "0";
  const v = safeFiniteNumber(n, null);
  if (v === null) return "—";
  const clean = Math.abs(v) < 1e-3 && v < 0 && v > -0.05 ? 0 : v === 0 ? 0 : v;
  const formatted = clean.toLocaleString("en-US");
  return formatted === "-0" ? "0" : formatted;
}

function fmtCompactLocal(val: unknown): string {
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

function geneShortTag(kind: unknown): string {
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

interface HourlyProdPoint {
  minuteBucket: number;
  pig: number;
  met: number;
  kry: number;
  spi: number;
  total: number;
  speq: number;
}

interface LootRecycleBucket {
  hourBucket: number;
  stolen: number;
  recycled: number;
  total: number;
}

function normalizeHourlyProdPoint(raw: Record<string, any> | null | undefined): HourlyProdPoint | null {
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

function countActiveBuildingPlanets(
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

function safeGetCanvas2dContext(
  canvas: HTMLCanvasElement | null
): CanvasRenderingContext2D | null {
  if (!canvas || typeof canvas.getContext !== "function") return null;
  const isJsdom =
    typeof navigator !== "undefined" && /jsdom/i.test(String(navigator.userAgent || ""));
  if (isJsdom && !Object.prototype.hasOwnProperty.call(canvas, "getContext")) {
    return null;
  }
  try {
    return canvas.getContext("2d");
  } catch {
    return null;
  }
}

function drawHourlyProductionLineChart(
  canvas: HTMLCanvasElement | null,
  series: HourlyProdPoint[]
): void {
  const ctx = safeGetCanvas2dContext(canvas);
  if (!canvas || !ctx) return;

  const rect =
    typeof canvas.getBoundingClientRect === "function"
      ? canvas.getBoundingClientRect()
      : { width: 220, height: 38 };
  const cssW = Math.max(120, Math.round(rect.width || 220));
  const cssH = Math.max(30, Math.round(rect.height || 38));
  const dpr = Math.min(
    2,
    Math.max(1, typeof window !== "undefined" ? Number(window.devicePixelRatio || 1) : 1)
  );

  if (canvas.width !== cssW * dpr || canvas.height !== cssH * dpr) {
    canvas.width = cssW * dpr;
    canvas.height = cssH * dpr;
  }
  ctx.save();
  ctx.scale(dpr, dpr);

  // Dark HUD inset background
  ctx.fillStyle = "#081012";
  ctx.fillRect(0, 0, cssW, cssH);

  // Subtle horizontal reference rail
  ctx.strokeStyle = "#16262b";
  ctx.lineWidth = 1;
  ctx.beginPath();
  ctx.moveTo(0, Math.round(cssH * 0.5) + 0.5);
  ctx.lineTo(cssW, Math.round(cssH * 0.5) + 0.5);
  ctx.stroke();

  const padL = 4;
  const padR = 6;
  const padT = 5;
  const padB = 5;
  const plotW = Math.max(10, cssW - padL - padR);
  const plotH = Math.max(8, cssH - padT - padB);

  if (series.length === 0) {
    ctx.strokeStyle = "#1f343a";
    ctx.lineWidth = 1.5;
    ctx.beginPath();
    ctx.moveTo(padL, cssH - padB);
    ctx.lineTo(cssW - padR, cssH - padB);
    ctx.stroke();
    ctx.restore();
    return;
  }

  if (series.length === 1) {
    const midY = padT + plotH * 0.5;
    const grad = ctx.createLinearGradient(0, midY, 0, cssH - padB);
    grad.addColorStop(0, "rgba(22, 236, 152, 0.24)");
    grad.addColorStop(1, "rgba(22, 236, 152, 0.02)");
    ctx.fillStyle = grad;
    ctx.fillRect(padL, midY, plotW, cssH - padB - midY);

    ctx.strokeStyle = "#16ec98";
    ctx.lineWidth = 1.75;
    ctx.beginPath();
    ctx.moveTo(padL, midY);
    ctx.lineTo(cssW - padR, midY);
    ctx.stroke();

    ctx.fillStyle = "rgba(50, 232, 255, 0.35)";
    ctx.beginPath();
    ctx.arc(cssW - padR, midY, 4.2, 0, Math.PI * 2);
    ctx.fill();

    ctx.fillStyle = "#32e8ff";
    ctx.beginPath();
    ctx.arc(cssW - padR, midY, 2.2, 0, Math.PI * 2);
    ctx.fill();
    ctx.restore();
    return;
  }

  const values = series.map((pt) => pt.total);
  let minVal = Math.min(...values);
  let maxVal = Math.max(...values);
  const span = maxVal - minVal;
  const isFlat = span < 1e-6;
  if (isFlat) {
    minVal = Math.max(0, minVal - 1);
    maxVal = maxVal + 1;
  }

  const coords = values.map((v, idx) => {
    const x = padL + (idx / Math.max(1, values.length - 1)) * plotW;
    const norm = isFlat ? 0.5 : (v - minVal) / (maxVal - minVal);
    const y = padT + (1 - norm) * plotH;
    return { x, y, v };
  });

  // Translucent mint/cyan area gradient beneath curve
  const areaGrad = ctx.createLinearGradient(0, padT, 0, cssH - padB);
  areaGrad.addColorStop(0, "rgba(22, 236, 152, 0.30)");
  areaGrad.addColorStop(0.7, "rgba(50, 232, 255, 0.08)");
  areaGrad.addColorStop(1, "rgba(50, 232, 255, 0.01)");

  ctx.beginPath();
  ctx.moveTo(coords[0].x, cssH - padB);
  for (const pt of coords) {
    ctx.lineTo(pt.x, pt.y);
  }
  ctx.lineTo(coords[coords.length - 1].x, cssH - padB);
  ctx.closePath();
  ctx.fillStyle = areaGrad;
  ctx.fill();

  // Glowing polyline
  ctx.save();
  ctx.strokeStyle = "#16ec98";
  ctx.lineWidth = 1.75;
  ctx.lineJoin = "round";
  ctx.lineCap = "round";
  ctx.shadowColor = "rgba(50, 232, 255, 0.45)";
  ctx.shadowBlur = 4;
  ctx.beginPath();
  coords.forEach((pt, idx) => {
    if (idx === 0) ctx.moveTo(pt.x, pt.y);
    else ctx.lineTo(pt.x, pt.y);
  });
  ctx.stroke();
  ctx.restore();

  // Peak dot if distinct from latest
  if (!isFlat) {
    let peakIdx = 0;
    for (let i = 1; i < coords.length; i += 1) {
      if (coords[i].v > coords[peakIdx].v) peakIdx = i;
    }
    if (peakIdx < coords.length - 1) {
      ctx.fillStyle = "#efb03b";
      ctx.beginPath();
      ctx.arc(coords[peakIdx].x, coords[peakIdx].y, 1.8, 0, Math.PI * 2);
      ctx.fill();
    }
  }

  // Latest-point glow & dot
  const last = coords[coords.length - 1];
  ctx.fillStyle = "rgba(50, 232, 255, 0.35)";
  ctx.beginPath();
  ctx.arc(last.x, last.y, 4.2, 0, Math.PI * 2);
  ctx.fill();

  ctx.fillStyle = "#32e8ff";
  ctx.beginPath();
  ctx.arc(last.x, last.y, 2.2, 0, Math.PI * 2);
  ctx.fill();

  ctx.restore();
}

function drawLootRecycleBarChart(
  canvas: HTMLCanvasElement | null,
  buckets: LootRecycleBucket[]
): void {
  const ctx = safeGetCanvas2dContext(canvas);
  if (!canvas || !ctx) return;

  const rect =
    typeof canvas.getBoundingClientRect === "function"
      ? canvas.getBoundingClientRect()
      : { width: 220, height: 38 };
  const cssW = Math.max(120, Math.round(rect.width || 220));
  const cssH = Math.max(30, Math.round(rect.height || 38));
  const dpr = Math.min(
    2,
    Math.max(1, typeof window !== "undefined" ? Number(window.devicePixelRatio || 1) : 1)
  );

  if (canvas.width !== cssW * dpr || canvas.height !== cssH * dpr) {
    canvas.width = cssW * dpr;
    canvas.height = cssH * dpr;
  }
  ctx.save();
  ctx.scale(dpr, dpr);

  // Dark HUD inset background
  ctx.fillStyle = "#081012";
  ctx.fillRect(0, 0, cssW, cssH);

  const padL = 4;
  const padR = 4;
  const padT = 4;
  const padB = 4;
  const plotW = Math.max(12, cssW - padL - padR);
  const plotH = Math.max(8, cssH - padT - padB);
  const baseY = cssH - padB;

  // Baseline rail
  ctx.strokeStyle = "#16262b";
  ctx.lineWidth = 1;
  ctx.beginPath();
  ctx.moveTo(padL, baseY + 0.5);
  ctx.lineTo(cssW - padR, baseY + 0.5);
  ctx.stroke();

  const slice = buckets.length > 0 ? buckets.slice(-24) : [];
  if (slice.length === 0) {
    ctx.restore();
    return;
  }

  const maxTotal = Math.max(1, ...slice.map((b) => b.total));
  const count = slice.length;
  const gap = count > 16 ? 1.5 : 2.5;
  const barW = Math.max(2, (plotW - gap * Math.max(0, count - 1)) / count);

  slice.forEach((bucket, idx) => {
    const x = padL + idx * (barW + gap);
    if (bucket.total <= 0) {
      ctx.fillStyle = "#192a2f";
      ctx.fillRect(x, baseY - 1.5, barW, 1.5);
      return;
    }

    const totalH = Math.max(2, Math.min(plotH, (bucket.total / maxTotal) * plotH));
    let stolenH = bucket.stolen > 0 ? Math.max(1.5, (bucket.stolen / bucket.total) * totalH) : 0;
    let recycledH = bucket.recycled > 0 ? Math.max(1.5, totalH - stolenH) : 0;
    if (stolenH + recycledH > plotH) {
      const scale = plotH / (stolenH + recycledH);
      stolenH *= scale;
      recycledH *= scale;
    }

    // Bottom bar: Stolen (Attack) in amber/gold
    if (stolenH > 0) {
      ctx.fillStyle = "#efb03b";
      ctx.fillRect(x, baseY - stolenH, barW, stolenH);
    }
    // Top bar: Recycled (Debris) in cyan/mint
    if (recycledH > 0) {
      ctx.fillStyle = "#32e8ff";
      ctx.fillRect(x, baseY - stolenH - recycledH, barW, recycledH);
    }
  });

  ctx.restore();
}

export const GaNnScannerHud: React.FC = () => {
  const {
    dashboard,
    setDashboard,
    gaCalculating,
    beginGaCalc,
    endGaCalc,
    bumpDashboardSeq,
    lastOptimizerSummary,
    setLastOptimizerSummary,
    plannerJobsStatus,
    refreshPlannerJobsStatus,
    apiCommand,
    loadDashboard,
    showFeedback,
  } = useConsole();

  const cfgMode = String(
    dashboard?.config?.planner_mode ||
      dashboard?.planner_mode ||
      dashboard?.effective_planner_mode ||
      "priority"
  ).toLowerCase();
  const isAiMode = cfgMode === "ai_planner";
  const isLegacyGa = cfgMode === "ga_nn";

  const jobsPayload =
    plannerJobsStatus || dashboard?.planner_jobs_status || dashboard?.planner_status || null;
  const coJob = jobsPayload?.config_optimizer_job || null;
  const aiJob = jobsPayload?.ai_planner_job || null;
  const coRunning = String(coJob?.status || "").toLowerCase() === "running";
  const aiRunning = String(aiJob?.status || "").toLowerCase() === "running";
  const runningJob = aiRunning ? aiJob : coRunning ? coJob : null;
  const anyJobRunning = Boolean(runningJob);

  const isActiveHud = isAiMode || isLegacyGa || anyJobRunning;
  const isCalculating = Boolean(gaCalculating || anyJobRunning);

  const gate = computeColonyGateInfo(dashboard, jobsPayload);
  const aiSum =
    dashboard?.ai_planner_summary && typeof dashboard.ai_planner_summary === "object"
      ? dashboard.ai_planner_summary
      : null;

  const incomingSum =
    dashboard?.optimizer_summary ||
    dashboard?.ga_nn_telemetry ||
    dashboard?.optimizer_metrics ||
    null;
  const hasIncoming = Boolean(
    incomingSum && typeof incomingSum === "object" && Object.keys(incomingSum).length > 0
  );
  const rawSum: Record<string, any> = isActiveHud
    ? (hasIncoming ? incomingSum! : isLegacyGa ? lastOptimizerSummary : null) || {}
    : {};
  const hasLegacySum = Boolean(rawSum && Object.keys(rawSum).length > 0);
  const hasAiSummary = Boolean(aiSum && Object.keys(aiSum).length > 0);

  const cfgPop = Math.max(
    4,
    Math.round(safeFiniteNumber(dashboard?.config?.ga_population_size, 24) ?? 24)
  );
  const cfgGens = Math.max(
    1,
    Math.round(safeFiniteNumber(dashboard?.config?.ga_generations, 12) ?? 12)
  );
  const horizonSteps = Math.max(
    1,
    Math.round(safeFiniteNumber(dashboard?.config?.ga_horizon_steps, 6) ?? 6)
  );

  const statusPillText = !isActiveHud
    ? "STANDBY"
    : isCalculating
      ? "CALCULATING"
      : isAiMode && !gate.liveAiUnlocked
        ? "PREVIEW ONLY"
        : hasAiSummary || hasLegacySum
          ? "OPTIMIZED"
          : "ACTIVE";

  const popSize = Math.max(
    1,
    Math.round(safeFiniteNumber(rawSum.population_size ?? aiSum?.ga_population_size, cfgPop) ?? cfgPop)
  );
  const nnBackend = String(
    rawSum.nn_backend || (isAiMode ? `${gate.coloniesOwned}/${gate.maxPlanets} COL` : "MLP")
  ).toUpperCase();
  const elapsedMs = safeFiniteNumber(
    runningJob?.elapsed_ms ?? rawSum.elapsed_ms ?? aiSum?.elapsed_ms,
    null
  );
  const safeElapsed = elapsedMs !== null ? Math.max(0, Math.round(elapsedMs)) : null;
  const metaLineText = !isActiveHud
    ? "HEURISTIC STANDBY"
    : runningJob
      ? `${aiRunning ? "AI-PLANNER" : "CONFIG OPT"} ${Math.round(safeFiniteNumber(runningJob.progress_pct, 0) ?? 0)}%${safeElapsed !== null ? ` · ${safeElapsed}ms` : ""}`
      : isAiMode
        ? `COL ${gate.coloniesOwned}/${gate.maxPlanets} · W${Math.round(safeFiniteNumber(aiSum?.reward_weeks, 26) ?? 26)}${safeElapsed !== null ? ` · ${safeElapsed}ms` : ""}`
        : safeElapsed !== null
          ? `POP ${popSize} · ${nnBackend} · ${safeElapsed}ms`
          : `POP ${popSize} · ${nnBackend}`;

  const isLegacyGaSummaryMode = isLegacyGa && hasLegacySum;

  const prodCanvasRef = useRef<HTMLCanvasElement | null>(null);
  const lootCanvasRef = useRef<HTMLCanvasElement | null>(null);

  const histLen = Array.isArray(rawSum.generation_history) ? rawSum.generation_history.length : null;
  const genDone = Math.max(
    0,
    Math.round(safeFiniteNumber(rawSum.generation ?? rawSum.generations ?? histLen, 0) ?? 0)
  );
  const genTarget = Math.max(genDone, cfgGens);
  const jobPct = clampPct(runningJob?.progress_pct ?? 0);
  const rawAiStepsTotal = safeFiniteNumber(aiSum?.steps, null);
  const aiStepsTotal = Math.max(0, Math.round(rawAiStepsTotal ?? 0));

  // Metric 1: Active Builders across owned colonies
  const builderStats = useMemo(() => {
    const planets: PlanetData[] = Array.isArray(dashboard?.state?.planets)
      ? dashboard!.state!.planets!
      : [];
    const activeQueues: QueueJobData[] = Array.isArray(dashboard?.state?.active_queues)
      ? dashboard!.state!.active_queues!
      : [];
    const counted = countActiveBuildingPlanets(
      planets,
      activeQueues,
      dashboard?.state?.active_planet_id
    );
    const curProdObj = (dashboard as Record<string, any> | null | undefined)
      ?.current_hourly_production;
    const total =
      counted.total > 0
        ? counted.total
        : Math.max(
            0,
            Math.round(
              safeFiniteNumber(curProdObj?.planet_count ?? gate.coloniesOwned, 0) ?? 0
            )
          );
    const hasActiveQueuesField = Array.isArray(dashboard?.state?.active_queues);
    const activeRaw = hasActiveQueuesField
      ? counted.active
      : Math.max(0, Math.round(safeFiniteNumber(curProdObj?.active_builders, 0) ?? 0));
    const active = total > 0 ? Math.min(total, activeRaw) : activeRaw;
    return { active, total };
  }, [
    dashboard,
    gate.coloniesOwned,
  ]);

  const builderPct =
    builderStats.total > 0
      ? clampPct(Math.round((builderStats.active / builderStats.total) * 100))
      : 0;

  const genPct = !isActiveHud
    ? 0
    : runningJob
      ? jobPct
      : isLegacyGaSummaryMode
        ? clampPct(genTarget > 0 ? (genDone / genTarget) * 100 : 0)
        : builderPct;

  const seedVal = safeFiniteNumber(rawSum.seed ?? dashboard?.config?.ga_seed, null);
  const seedTxt = seedVal !== null ? `Seed ${Math.round(seedVal)}` : "Auto seed";
  const idleBuilders = Math.max(0, builderStats.total - builderStats.active);
  const genSubText = !isActiveHud
    ? "Waiting for builder telemetry"
    : runningJob
      ? String(runningJob.phase || "Running background optimizer...")
      : isLegacyGaSummaryMode
        ? gaCalculating
          ? "Evolving action sequences..."
          : `Horizon ${horizonSteps} steps · ${seedTxt}`
        : `${builderStats.active}/${builderStats.total} building · ${idleBuilders} idle · ${
            gate.liveAiUnlocked ? "Live Unlocked" : "Preview"
          }`;

  // Metric 2: Per-minute empire hourly production line chart
  const hourlyProdData = useMemo(() => {
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
  }, [dashboard]);

  const nnEvals = Math.max(
    0,
    Math.round(safeFiniteNumber(rawSum.nn_evaluations ?? rawSum.neural_evals, 0) ?? 0)
  );
  const lossRaw = safeFiniteNumber(rawSum.nn_final_loss, null);
  const loss = lossRaw !== null ? Math.max(0, lossRaw) : null;
  const nnPct = !isActiveHud
    ? 0
    : isLegacyGaSummaryMode
      ? isCalculating
        ? 72
        : nnEvals > 0
          ? clampPct(Math.max(14, Math.log10(nnEvals + 1) * 38))
          : 0
      : hourlyProdData.arcPct;

  const nnSubText = !isActiveHud
    ? "Waiting for production samples"
    : isLegacyGaSummaryMode
      ? loss !== null
        ? `MSE Loss ${loss.toFixed(4)} · ${nnBackend}`
        : `Backend ${nnBackend} · Horizon ${horizonSteps}`
      : hourlyProdData.latest.total > 0
        ? `PI ${fmtCompactLocal(hourlyProdData.latest.pig)} · M ${fmtCompactLocal(hourlyProdData.latest.met)} · K ${fmtCompactLocal(hourlyProdData.latest.kry)} · S ${fmtCompactLocal(hourlyProdData.latest.spi)}${
            hourlyProdData.series.length >= 2 ? ` · ${hourlyProdData.series.length}m` : ""
          }`
        : "Waiting for hourly production samples";

  // Metric 3: Stolen & Recycled per-hour bar chart
  const lootRecycleData = useMemo(() => {
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
  }, [dashboard]);

  const baseFit = safeFiniteNumber(
    rawSum.baseline_fitness ?? rawSum.initial_best_fitness ?? aiSum?.baseline_discounted_reward,
    null
  );
  const bestFit = safeFiniteNumber(
    rawSum.best_fitness ??
      rawSum.fitness_score ??
      rawSum.final_best_fitness ??
      aiSum?.discounted_reward_spice_eq,
    null
  );
  const impText = isActiveHud
    ? fmtImprovementPct(rawSum.improvement_pct ?? aiSum?.improvement_pct, baseFit, bestFit)
    : "--%";
  const rawImp = safeFiniteNumber(rawSum.improvement_pct ?? aiSum?.improvement_pct, null);
  const fitPct = !isActiveHud
    ? 0
    : isLegacyGaSummaryMode
      ? impText.startsWith(">")
        ? 100
        : clampPct(
            rawImp !== null
              ? Math.max(0, Math.min(100, rawImp + 15))
              : bestFit !== null && bestFit > 0
                ? 35
                : 0
          )
      : lootRecycleData.arcPct;

  const fitSubText = !isActiveHud
    ? "Stolen 0 · Recycled 0 (24h)"
    : isLegacyGaSummaryMode
      ? `Base ${fmtCompactLocal(baseFit)} · Best ${fmtCompactLocal(bestFit)}`
      : `Stolen ${fmtCompactLocal(lootRecycleData.totalStolen)} · Recycled ${fmtCompactLocal(
          lootRecycleData.totalRecycled
        )} (${lootRecycleData.windowHours}h)`;

  useEffect(() => {
    if (isLegacyGaSummaryMode) return;
    const drawAll = () => {
      drawHourlyProductionLineChart(prodCanvasRef.current, hourlyProdData.series);
      drawLootRecycleBarChart(lootCanvasRef.current, lootRecycleData.buckets);
    };
    drawAll();
    if (typeof ResizeObserver === "undefined") return;
    const ro = new ResizeObserver(() => drawAll());
    if (prodCanvasRef.current) ro.observe(prodCanvasRef.current);
    if (lootCanvasRef.current) ro.observe(lootCanvasRef.current);
    return () => ro.disconnect();
  }, [hourlyProdData.series, isLegacyGaSummaryMode, lootRecycleData.buckets]);

  const chrom = Array.isArray(rawSum.best_chromosome) ? rawSum.best_chromosome : [];
  const planLen = Array.isArray(dashboard?.plan) ? dashboard!.plan!.length : 0;
  const gLen = Math.max(
    0,
    Math.round(
      safeFiniteNumber(
        rawSum.genome_length ??
          rawSum.actions_count ??
          (isLegacyGaSummaryMode && chrom.length > 0 ? chrom.length : rawAiStepsTotal),
        chrom.length || planLen
      ) ?? 0
    )
  );
  const genomePct = !isActiveHud
    ? 0
    : clampPct(
        isAiMode && aiStepsTotal > 0
          ? (Math.min(gLen, aiStepsTotal) / aiStepsTotal) * 100
          : horizonSteps > 0
            ? (Math.min(gLen, horizonSteps) / horizonSteps) * 100
            : 0
      );
  const seqPreview = (chrom.length > 0 ? chrom : dashboard?.plan || [])
    .slice(0, 4)
    .map((g: any) => geneShortTag(typeof g === "string" ? g : g?.kind))
    .filter(Boolean)
    .join(" → ");
  const genomeSubText = !isActiveHud
    ? "Sequence standby"
    : seqPreview || (gLen > 0 ? `Top ${gLen} sequence steps` : "Sequence standby");

  const onRecalc = async () => {
    beginGaCalc();
    try {
      if (isAiMode) {
        showFeedback("Triggering AI-Planner recalculation...", "ok");
        await apiCommand({ command: "ai_planner_run", trigger_source: "hud_recalc" });
        await Promise.all([
          refreshPlannerJobsStatus(),
          loadDashboard({ refresh: false, preserveConfig: true, force: true }),
        ]);
        showFeedback("AI-Planner job triggered.", "ok");
      } else {
        showFeedback("Running planner recalculation...", "ok");
        const res = await apiCommand({
          command: "plan",
          planner_mode: isLegacyGa ? "ga_nn" : "priority",
          refresh: false,
        });
        bumpDashboardSeq();
        if (res.optimizer_summary) {
          setLastOptimizerSummary(res.optimizer_summary);
        }
        const curDash = (globalThis as any).state?.dashboard || dashboard;
        if (curDash) {
          const updated = {
            ...curDash,
            plan: Array.isArray(res.actions) ? res.actions : curDash.plan,
            optimizer_summary: res.optimizer_summary ?? curDash.optimizer_summary,
          };
          if ((globalThis as any).state) {
            (globalThis as any).state.dashboard = updated;
          }
          setDashboard(updated);
        } else {
          await loadDashboard({ refresh: false, preserveConfig: true, force: true });
        }
        showFeedback(
          `Evaluation complete (${(res.actions || []).length} planned actions).`,
          "ok"
        );
      }
    } catch (err: any) {
      showFeedback(`Recalculation failed: ${err.message}`, feedbackKindForError(err));
    } finally {
      endGaCalc();
    }
  };

  return (
    <article
      className={`hud-window gann-hud${isActiveHud ? " is-active" : ""}${isCalculating ? " is-calculating" : ""}`}
      id="gann-calc-hud"
      aria-label="AI-Planner and background optimizer telemetry"
      hidden={!isActiveHud}
    >
      <div className="window-bar window-bar-wrap">
        <div className="window-title-group">
          <span className="module-code tabular">{isAiMode || anyJobRunning ? "AIP" : "GNN"}</span>
          <h2>
            {anyJobRunning
              ? "Optimizer Background Engine"
              : isAiMode
                ? "AI-Planner & Colony Telemetry"
                : "Planner Search Scanner"}
          </h2>
          <span className="gann-state-pill tabular" id="gann-status-pill" role="status">
            {statusPillText}
          </span>
        </div>
        <div className="inline-actions">
          <span className="module-meta tabular" id="gann-meta-line">
            {metaLineText}
          </span>
          <button
            type="button"
            className="btn btn-secondary btn-sm"
            id="btn-gann-recalc"
            onClick={onRecalc}
          >
            Recalculate
          </button>
        </div>
      </div>

      <div className="gann-hud-body" id="gann-hud-body">
        <div className="gann-scanner-stage" aria-hidden="true">
          <svg
            id="gann-radial-svg"
            className="gann-radial-svg"
            viewBox="0 0 220 160"
            width="180"
            height="130"
            focusable="false"
          >
            <path
              className="gann-axis-rail"
              d="M 110 14 L 110 146"
              fill="none"
              stroke="#244249"
              strokeWidth="1"
              strokeDasharray="3 3"
            />
            <g transform="translate(110, 120) scale(1, 0.52)">
              <circle cx="0" cy="0" r="80" fill="none" stroke="#18343b" strokeWidth="1" />
              <g className="gann-ring-mid">
                <circle
                  cx="0"
                  cy="0"
                  r="66"
                  fill="none"
                  stroke="#16ec98"
                  strokeOpacity="0.48"
                  strokeWidth="2"
                  strokeDasharray="42 16 18 14"
                />
                <circle
                  cx="0"
                  cy="0"
                  r="46"
                  fill="none"
                  stroke="#32e8ff"
                  strokeOpacity="0.38"
                  strokeWidth="1.5"
                  strokeDasharray="20 12"
                />
              </g>
            </g>
            <g transform="translate(110, 80) scale(1, 0.52)">
              <circle cx="0" cy="0" r="84" fill="none" stroke="#243135" strokeWidth="1" />
              <g className="gann-ring-gold">
                <circle
                  cx="0"
                  cy="0"
                  r="52"
                  fill="none"
                  stroke="#efb03b"
                  strokeWidth="2.4"
                  strokeDasharray="22 8 10 8 34 10"
                />
                <circle cx="52" cy="0" r="3" fill="#efb03b" />
                <circle cx="-52" cy="0" r="3" fill="#efb03b" />
                <circle cx="0" cy="52" r="2.5" fill="#ffcf5c" />
                <circle cx="0" cy="-52" r="2.5" fill="#ffcf5c" />
              </g>
              <g className="gann-core-pulse">
                <circle cx="0" cy="0" r="26" fill="#091518" stroke="#16ec98" strokeWidth="1.8" />
                <circle
                  cx="0"
                  cy="0"
                  r="14"
                  fill="none"
                  stroke="#ffcf5c"
                  strokeOpacity="0.7"
                  strokeWidth="1.2"
                />
                <circle cx="0" cy="0" r="7" fill="#efb03b" />
              </g>
            </g>
            <g transform="translate(110, 42) scale(1, 0.52)">
              <circle
                cx="0"
                cy="0"
                r="84"
                fill="none"
                stroke="#2f6670"
                strokeWidth="4"
                strokeDasharray="1.5 6.0"
              />
              <g className="gann-ring-outer">
                <circle
                  cx="0"
                  cy="0"
                  r="72"
                  fill="none"
                  stroke="#32e8ff"
                  strokeWidth="2"
                  strokeDasharray="58 14 26 12 44 18"
                />
                <circle cx="0" cy="-72" r="3" fill="#32e8ff" />
                <circle cx="0" cy="72" r="3" fill="#16ec98" />
              </g>
              <g className="gann-scan-sweep">
                <path
                  d="M 0 -60 A 60 60 0 0 1 60 0"
                  fill="none"
                  stroke="#16ec98"
                  strokeWidth="3"
                  strokeLinecap="round"
                />
                <path
                  d="M 0 -46 L 0 -22 M 0 22 L 0 46 M -46 0 L -22 0 M 22 0 L 46 0"
                  fill="none"
                  stroke="#32e8ff"
                  strokeOpacity="0.72"
                  strokeWidth="1.2"
                />
              </g>
            </g>
          </svg>
        </div>

        <div className="gann-metrics-grid">
          <div className="gann-metric-cell">
            <div className="gann-metric-head">
              <div
                className="gann-mini-arc is-cyan"
                id="gann-arc-gen"
                aria-hidden="true"
                style={{ ["--arc-pct" as any]: String(genPct) }}
              />
              <div className="gann-metric-titles">
                <span className="gann-metric-label">
                  {anyJobRunning
                    ? "Background Job"
                    : isLegacyGaSummaryMode
                      ? "Cycle Progress"
                      : "Active Builders"}
                </span>
                <strong className="gann-metric-val tabular" id="gann-gen-val">
                  {!isActiveHud
                    ? "0 / 0"
                    : runningJob
                      ? `${Math.round(jobPct)}%`
                      : isLegacyGaSummaryMode
                        ? `${genDone} / ${genTarget}`
                        : `${builderStats.active} / ${builderStats.total}`}
                </strong>
              </div>
            </div>
            <div className="meter-track">
              <div
                className="meter-fill gann-bar-cyan"
                id="gann-gen-bar"
                style={{ transform: `scaleX(${genPct / 100})` }}
              />
            </div>
            <span className="gann-metric-sub tabular" id="gann-gen-sub">
              {genSubText}
            </span>
          </div>

          <div className="gann-metric-cell">
            <div className="gann-metric-head">
              <div
                className="gann-mini-arc is-mint"
                id="gann-arc-nn"
                aria-hidden="true"
                style={{ ["--arc-pct" as any]: String(nnPct) }}
              />
              <div className="gann-metric-titles">
                <span className="gann-metric-label">
                  {isLegacyGaSummaryMode ? "Search Eval Pass" : "Hourly Production"}
                </span>
                <strong className="gann-metric-val tabular" id="gann-nn-val">
                  {!isActiveHud
                    ? "0 /h"
                    : isLegacyGaSummaryMode
                      ? `${fmtIntLocal(nnEvals)} evals`
                      : `${fmtCompactLocal(hourlyProdData.latest.total)} /h`}
                </strong>
              </div>
            </div>
            {isLegacyGaSummaryMode ? (
              <div className="meter-track">
                <div
                  className="meter-fill gann-bar-mint"
                  id="gann-nn-bar"
                  style={{ transform: `scaleX(${nnPct / 100})` }}
                />
              </div>
            ) : (
              <div className="gann-mini-chart-wrap">
                <canvas
                  ref={prodCanvasRef}
                  id="gann-prod-chart"
                  className="gann-mini-chart"
                  width={220}
                  height={38}
                  aria-label="Empire hourly production per minute"
                />
              </div>
            )}
            <span className="gann-metric-sub tabular" id="gann-nn-sub">
              {nnSubText}
            </span>
          </div>

          <div className="gann-metric-cell">
            <div className="gann-metric-head">
              <div
                className="gann-mini-arc is-gold"
                id="gann-arc-fit"
                aria-hidden="true"
                style={{ ["--arc-pct" as any]: String(fitPct) }}
              />
              <div className="gann-metric-titles">
                <span className="gann-metric-label">
                  {isLegacyGaSummaryMode ? "Reward Gain" : "Stolen & Recycled"}
                </span>
                <strong className="gann-metric-val tabular" id="gann-fit-val">
                  {!isActiveHud
                    ? "0"
                    : isLegacyGaSummaryMode
                      ? impText
                      : fmtCompactLocal(lootRecycleData.grandTotal)}
                </strong>
              </div>
            </div>
            {isLegacyGaSummaryMode ? (
              <div className="meter-track">
                <div
                  className="meter-fill gann-bar-gold"
                  id="gann-fit-bar"
                  style={{ transform: `scaleX(${fitPct / 100})` }}
                />
              </div>
            ) : (
              <div className="gann-mini-chart-wrap">
                <canvas
                  ref={lootCanvasRef}
                  id="gann-loot-chart"
                  className="gann-mini-chart"
                  width={220}
                  height={38}
                  aria-label="Stolen and recycled resources per hour"
                />
              </div>
            )}
            <span className="gann-metric-sub tabular" id="gann-fit-sub">
              {fitSubText}
            </span>
          </div>

          <div className="gann-metric-cell">
            <div className="gann-metric-head">
              <div
                className="gann-mini-arc is-cyan"
                id="gann-arc-genome"
                aria-hidden="true"
                style={{ ["--arc-pct" as any]: String(genomePct) }}
              />
              <div className="gann-metric-titles">
                <span className="gann-metric-label">
                  {isAiMode ? "Horizon Sequence" : "Action Sequence"}
                </span>
                <strong className="gann-metric-val tabular" id="gann-genome-val">
                  {!isActiveHud
                    ? "0 steps"
                    : hasLegacySum && chrom.length > 0
                      ? `${gLen} genes`
                      : `${gLen} steps`}
                </strong>
              </div>
            </div>
            <div className="meter-track">
              <div
                className="meter-fill gann-bar-cyan"
                id="gann-genome-bar"
                style={{ transform: `scaleX(${genomePct / 100})` }}
              />
            </div>
            <span className="gann-metric-sub tabular" id="gann-genome-sub">
              {genomeSubText}
            </span>
          </div>
        </div>
      </div>
    </article>
  );
};

export default GaNnScannerHud;
