import React, { useEffect, useRef } from "react";
import {
  drawHourlyProductionLineChart,
  drawLootRecycleBarChart,
} from "./scannerHudCharts";
import {
  fmtCompactLocal,
  fmtIntLocal,
  type HourlyProdSummary,
  type LootRecycleSummary,
} from "./scannerHudTelemetry";

export interface ScannerHudMetricsProps {
  isActiveHud: boolean;
  isAiMode: boolean;
  isLegacyGaSummaryMode: boolean;
  anyJobRunning: boolean;
  hasRunningJob: boolean;
  hasLegacyChrom: boolean;
  genPct: number;
  jobPct: number;
  genDone: number;
  genTarget: number;
  builderStats: { active: number; total: number };
  genSubText: string;
  nnPct: number;
  nnEvals: number;
  hourlyProdData: HourlyProdSummary;
  nnSubText: string;
  fitPct: number;
  impText: string;
  lootRecycleData: LootRecycleSummary;
  fitSubText: string;
  genomePct: number;
  gLen: number;
  genomeSubText: string;
}

export const ScannerHudMetrics: React.FC<ScannerHudMetricsProps> = ({
  isActiveHud,
  isAiMode,
  isLegacyGaSummaryMode,
  anyJobRunning,
  hasRunningJob,
  hasLegacyChrom,
  genPct,
  jobPct,
  genDone,
  genTarget,
  builderStats,
  genSubText,
  nnPct,
  nnEvals,
  hourlyProdData,
  nnSubText,
  fitPct,
  impText,
  lootRecycleData,
  fitSubText,
  genomePct,
  gLen,
  genomeSubText,
}) => {
  const prodCanvasRef = useRef<HTMLCanvasElement | null>(null);
  const lootCanvasRef = useRef<HTMLCanvasElement | null>(null);

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

  return (
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
                : hasRunningJob
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
                : hasLegacyChrom
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
  );
};
