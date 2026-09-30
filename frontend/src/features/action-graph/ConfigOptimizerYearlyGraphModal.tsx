import React, { useEffect, useRef, useState } from "react";
import type { ConfigOptimizerYearlyProjection } from "../../types/api";
import { fmtCompact, fmtInt } from "../../utils/formatters";

export interface ConfigOptimizerYearlyGraphModalProps {
  open: boolean;
  projection: ConfigOptimizerYearlyProjection | null;
  loading: boolean;
  error: string | null;
  horizonYears: number;
  onChangeHorizon: (years: number) => void;
  onClose: () => void;
}

const HORIZON_OPTIONS: ReadonlyArray<number> = [3, 5, 10, 15];

function fmtShipsRate(val: number): string {
  if (!Number.isFinite(val)) return "0";
  if (Math.abs(val) >= 1000) return fmtCompact(val);
  if (Math.abs(val) >= 100) return val.toFixed(1);
  return val.toFixed(2);
}

export const ConfigOptimizerYearlyGraphModal: React.FC<
  ConfigOptimizerYearlyGraphModalProps
> = ({
  open,
  projection,
  loading,
  error,
  horizonYears,
  onChangeHorizon,
  onClose,
}) => {
  const [chartMode, setChartMode] = useState<"yearly" | "weekly">("yearly");
  const [hoverIndex, setHoverIndex] = useState<number | null>(null);
  const canvasRef = useRef<HTMLCanvasElement | null>(null);

  useEffect(() => {
    if (!open) return;
    const onKey = (ev: KeyboardEvent) => {
      if (ev.key === "Escape") onClose();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose, open]);

  useEffect(() => {
    setHoverIndex(null);
  }, [chartMode, horizonYears, projection]);

  // Draw pure HTML5 <canvas> chart (strictly 0 <svg> elements!)
  useEffect(() => {
    if (!open || !projection || !canvasRef.current) return;
    const canvas = canvasRef.current;
    const ctx = canvas.getContext("2d");
    if (!ctx) return;

    const dpr = Math.max(1, Math.min(2, window.devicePixelRatio || 1));
    const cssWidth = Math.max(520, canvas.clientWidth || 780);
    const cssHeight = Math.max(260, canvas.clientHeight || 300);
    canvas.width = Math.round(cssWidth * dpr);
    canvas.height = Math.round(cssHeight * dpr);
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);

    ctx.fillStyle = "#081014";
    ctx.fillRect(0, 0, cssWidth, cssHeight);

    const padLeft = 64;
    const padRight = 24;
    const padTop = 28;
    const padBottom = 42;
    const plotW = Math.max(100, cssWidth - padLeft - padRight);
    const plotH = Math.max(100, cssHeight - padTop - padBottom);

    const years = projection.years || [];
    const weeks = projection.weeks || [];
    const targetRate = Math.max(0, Number(projection.target_ships_per_week || 0));
    const peakYearVal = Math.max(
      0,
      ...years.map((y) => Math.max(y.avg_ships_per_week, y.end_ships_per_week, y.peak_ships_per_week))
    );
    const maxVal = Math.max(1, targetRate * 1.08, peakYearVal * 1.12);

    const yFor = (v: number) =>
      padTop + plotH - Math.max(0, Math.min(1, v / maxVal)) * plotH;

    // Horizontal grid lines + Y-axis labels
    const yTicks = 4;
    ctx.font = "11px ui-monospace, SFMono-Regular, Menlo, monospace";
    ctx.textAlign = "right";
    ctx.textBaseline = "middle";
    for (let i = 0; i <= yTicks; i++) {
      const frac = i / yTicks;
      const val = maxVal * frac;
      const py = padTop + plotH - frac * plotH;
      ctx.strokeStyle = i === 0 ? "rgba(125, 165, 180, 0.35)" : "rgba(125, 165, 180, 0.14)";
      ctx.lineWidth = 1;
      ctx.beginPath();
      ctx.moveTo(padLeft, py);
      ctx.lineTo(padLeft + plotW, py);
      ctx.stroke();

      ctx.fillStyle = "#8aa4b0";
      ctx.fillText(fmtShipsRate(val), padLeft - 8, py);
    }

    // Steady-state 100% target dashed line
    if (targetRate > 0) {
      const ty = yFor(targetRate);
      ctx.save();
      ctx.setLineDash([6, 4]);
      ctx.strokeStyle = "#efb03b";
      ctx.lineWidth = 1.5;
      ctx.beginPath();
      ctx.moveTo(padLeft, ty);
      ctx.lineTo(padLeft + plotW, ty);
      ctx.stroke();
      ctx.restore();

      ctx.fillStyle = "#efb03b";
      ctx.textAlign = "right";
      ctx.textBaseline = "bottom";
      ctx.fillText(
        `Steady Target: ${fmtShipsRate(targetRate)}/wk`,
        padLeft + plotW,
        Math.max(14, ty - 4)
      );
    }

    if (chartMode === "yearly" && years.length > 0) {
      const n = years.length;
      const slotW = plotW / n;
      const barW = Math.max(14, Math.min(58, slotW * 0.54));

      // Draw avg Ships/Week bars + end-of-year trajectory line
      years.forEach((yr, idx) => {
        const cx = padLeft + slotW * (idx + 0.5);
        const bx = cx - barW / 2;
        const avgY = yFor(yr.avg_ships_per_week);
        const barH = Math.max(2, padTop + plotH - avgY);
        const isHovered = hoverIndex === idx;

        ctx.fillStyle = isHovered
          ? "rgba(22, 236, 152, 0.42)"
          : "rgba(50, 232, 255, 0.26)";
        ctx.strokeStyle = isHovered ? "#16ec98" : "#32e8ff";
        ctx.lineWidth = isHovered ? 2 : 1.25;
        ctx.fillRect(bx, avgY, barW, barH);
        ctx.strokeRect(bx, avgY, barW, barH);

        // Value label above bar
        ctx.fillStyle = "#e7f3f7";
        ctx.textAlign = "center";
        ctx.textBaseline = "bottom";
        const topY = Math.min(avgY, yFor(yr.end_ships_per_week));
        if (n <= 10 || idx % 2 === 0 || isHovered) {
          ctx.fillText(fmtShipsRate(yr.avg_ships_per_week), cx, Math.max(padTop + 10, topY - 6));
        }

        // X-axis year label
        ctx.fillStyle = isHovered ? "#16ec98" : "#9db5c0";
        ctx.textBaseline = "top";
        ctx.fillText(`Y${yr.year}`, cx, padTop + plotH + 10);
      });

      // End-of-year Ships/Week trajectory line
      ctx.strokeStyle = "#16ec98";
      ctx.lineWidth = 2.2;
      ctx.beginPath();
      years.forEach((yr, idx) => {
        const cx = padLeft + slotW * (idx + 0.5);
        const ey = yFor(yr.end_ships_per_week);
        if (idx === 0) ctx.moveTo(cx, ey);
        else ctx.lineTo(cx, ey);
      });
      ctx.stroke();

      years.forEach((yr, idx) => {
        const cx = padLeft + slotW * (idx + 0.5);
        const ey = yFor(yr.end_ships_per_week);
        ctx.fillStyle = "#16ec98";
        ctx.beginPath();
        ctx.arc(cx, ey, hoverIndex === idx ? 4.5 : 3.2, 0, Math.PI * 2);
        ctx.fill();
      });
    } else if (weeks.length > 1) {
      const maxWk = Math.max(1, weeks[weeks.length - 1].week);
      const xForWk = (w: number) => padLeft + (w / maxWk) * plotW;

      // Vertical Year dividers
      ctx.save();
      ctx.setLineDash([3, 4]);
      ctx.strokeStyle = "rgba(125, 165, 180, 0.22)";
      ctx.textAlign = "center";
      ctx.textBaseline = "top";
      for (let yr = 1; yr <= projection.horizon_years; yr++) {
        const xEnd = xForWk(yr * 52);
        ctx.beginPath();
        ctx.moveTo(xEnd, padTop);
        ctx.lineTo(xEnd, padTop + plotH);
        ctx.stroke();
        const xMid = xForWk((yr - 0.5) * 52);
        ctx.fillStyle = "#9db5c0";
        ctx.fillText(`Year ${yr}`, xMid, padTop + plotH + 10);
      }
      ctx.restore();

      // Area fill under weekly ships/week curve
      ctx.beginPath();
      weeks.forEach((pt, idx) => {
        const px = xForWk(pt.week);
        const py = yFor(pt.ships_per_week);
        if (idx === 0) ctx.moveTo(px, py);
        else ctx.lineTo(px, py);
      });
      ctx.lineTo(xForWk(weeks[weeks.length - 1].week), padTop + plotH);
      ctx.lineTo(xForWk(0), padTop + plotH);
      ctx.closePath();
      ctx.fillStyle = "rgba(22, 236, 152, 0.16)";
      ctx.fill();

      // Main Ships/Week curve
      ctx.strokeStyle = "#16ec98";
      ctx.lineWidth = 2.2;
      ctx.beginPath();
      weeks.forEach((pt, idx) => {
        const px = xForWk(pt.week);
        const py = yFor(pt.ships_per_week);
        if (idx === 0) ctx.moveTo(px, py);
        else ctx.lineTo(px, py);
      });
      ctx.stroke();

      if (hoverIndex !== null && weeks[hoverIndex]) {
        const hpt = weeks[hoverIndex];
        const hx = xForWk(hpt.week);
        const hy = yFor(hpt.ships_per_week);
        ctx.strokeStyle = "rgba(255, 255, 255, 0.35)";
        ctx.lineWidth = 1;
        ctx.beginPath();
        ctx.moveTo(hx, padTop);
        ctx.lineTo(hx, padTop + plotH);
        ctx.stroke();

        ctx.fillStyle = "#16ec98";
        ctx.beginPath();
        ctx.arc(hx, hy, 4.5, 0, Math.PI * 2);
        ctx.fill();
      }
    }
  }, [chartMode, hoverIndex, open, projection]);

  if (!open) return null;

  const handleCanvasMouseMove = (ev: React.MouseEvent<HTMLCanvasElement>) => {
    if (!projection || !canvasRef.current) return;
    const rect = canvasRef.current.getBoundingClientRect();
    const padLeft = 64;
    const padRight = 24;
    const plotW = Math.max(100, rect.width - padLeft - padRight);
    const relX = ev.clientX - rect.left - padLeft;
    if (relX < 0 || relX > plotW) {
      setHoverIndex(null);
      return;
    }
    if (chartMode === "yearly") {
      const n = projection.years.length;
      if (n <= 0) return;
      const idx = Math.max(0, Math.min(n - 1, Math.floor((relX / plotW) * n)));
      setHoverIndex(idx);
    } else {
      const n = projection.weeks.length;
      if (n <= 1) return;
      const idx = Math.max(0, Math.min(n - 1, Math.round((relX / plotW) * (n - 1))));
      setHoverIndex(idx);
    }
  };

  const shipLabel = projection
    ? `${projection.target_ship} (${projection.target_ship_key})`
    : "Target Ship";
  const lastYear =
    projection && projection.years.length > 0
      ? projection.years[projection.years.length - 1]
      : null;
  const firstYear =
    projection && projection.years.length > 0 ? projection.years[0] : null;

  const hoverSummary = (() => {
    if (!projection || hoverIndex === null) return null;
    if (chartMode === "yearly") {
      const yr = projection.years[hoverIndex];
      if (!yr) return null;
      return `${yr.label}: Avg ${fmtShipsRate(yr.avg_ships_per_week)}/wk · End ${fmtShipsRate(yr.end_ships_per_week)}/wk · Built ${fmtInt(Math.round(yr.total_ships_produced))} ships · Bottleneck: ${yr.bottleneck} (${yr.completion_pct.toFixed(1)}% built)`;
    }
    const wk = projection.weeks[hoverIndex];
    if (!wk) return null;
    return `Week ${wk.week} (Year ${wk.year}, W${wk.week_in_year}): ${fmtShipsRate(wk.ships_per_week)}/wk · Resource cap ${fmtShipsRate(wk.resource_cap_per_week)}/wk · Shipyard cap ${fmtShipsRate(wk.shipyard_cap_per_week)}/wk · Bottleneck: ${wk.bottleneck}`;
  })();

  return (
    <div
      id="co-yearly-graph-modal"
      className="notif-modal-backdrop"
      role="dialog"
      aria-modal="true"
      aria-labelledby="co-yearly-graph-title"
      onClick={onClose}
    >
      <div
        className="notif-modal-card ag-co-yearly-modal-card"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="panel-head ag-co-modal-head">
          <div>
            <h2 id="co-yearly-graph-title" className="tabular">
              {`Year-by-Year Ships / Week Projection — ${shipLabel}`}
            </h2>
            <span className="ag-toolbar-meta tabular">
              Simulated multi-colony construction &amp; research ramp from current built levels to Config Optimizer target.
            </span>
          </div>
          <div className="inline-actions">
            <div className="tab-group" role="group" aria-label="Projection horizon">
              {HORIZON_OPTIONS.map((yr) => (
                <button
                  key={yr}
                  type="button"
                  id={`btn-co-yr-${yr}`}
                  className={`tab-btn${horizonYears === yr ? " active" : ""}`}
                  disabled={loading}
                  onClick={() => onChangeHorizon(yr)}
                >
                  {`${yr}Y`}
                </button>
              ))}
            </div>
            <div className="tab-group" role="group" aria-label="Chart display mode">
              <button
                type="button"
                id="btn-co-yr-mode-bars"
                className={`tab-btn${chartMode === "yearly" ? " active" : ""}`}
                onClick={() => setChartMode("yearly")}
              >
                Yearly Bars
              </button>
              <button
                type="button"
                id="btn-co-yr-mode-curve"
                className={`tab-btn${chartMode === "weekly" ? " active" : ""}`}
                onClick={() => setChartMode("weekly")}
              >
                Weekly Curve
              </button>
            </div>
            <button
              type="button"
              className="btn btn-secondary btn-sm"
              id="btn-co-yearly-close"
              onClick={onClose}
            >
              Close
            </button>
          </div>
        </div>

        <div className="ag-co-modal-body">
          {error ? (
            <div className="ag-co-rename-error" role="alert">
              {error}
            </div>
          ) : null}

          {projection ? (
            <>
              <div className="ag-kpi-strip" aria-label="Yearly ships per week summary">
                <div className="ag-kpi-cell">
                  <span className="ag-kpi-label">Current (Week 0)</span>
                  <strong className="ag-kpi-val tabular" id="co-yr-kpi-current">
                    {`${fmtShipsRate(projection.current_ships_per_week)} / wk`}
                  </strong>
                  <span className="ag-kpi-sub tabular">{projection.target_ship}</span>
                </div>
                <div className="ag-kpi-cell">
                  <span className="ag-kpi-label">Year 1 Avg → End</span>
                  <strong className="ag-kpi-val tabular" id="co-yr-kpi-y1">
                    {firstYear
                      ? `${fmtShipsRate(firstYear.avg_ships_per_week)} → ${fmtShipsRate(firstYear.end_ships_per_week)} / wk`
                      : "—"}
                  </strong>
                  <span className="ag-kpi-sub tabular">
                    {firstYear
                      ? `${fmtInt(Math.round(firstYear.total_ships_produced))} ships in Year 1`
                      : "—"}
                  </span>
                </div>
                <div className="ag-kpi-cell">
                  <span className="ag-kpi-label">{`Year ${projection.horizon_years} End Rate`}</span>
                  <strong className="ag-kpi-val tabular" id="co-yr-kpi-end">
                    {lastYear ? `${fmtShipsRate(lastYear.end_ships_per_week)} / wk` : "—"}
                  </strong>
                  <span className="ag-kpi-sub tabular">
                    {lastYear
                      ? `Cumulative: ${fmtInt(Math.round(lastYear.cumulative_ships_produced))} ships`
                      : "—"}
                  </span>
                </div>
                <div className="ag-kpi-cell">
                  <span className="ag-kpi-label">100% Steady Target</span>
                  <strong className="ag-kpi-val tabular" id="co-yr-kpi-target">
                    {`${fmtShipsRate(projection.target_ships_per_week)} / wk`}
                  </strong>
                  <span className="ag-kpi-sub tabular">
                    {`${fmtShipsRate(projection.target_ships_per_month)} / mo steady-state`}
                  </span>
                </div>
              </div>

              <div className="ag-co-yearly-canvas-box">
                <div className="ag-co-yearly-legend tabular">
                  <span className="ag-co-legend-item is-cyan">■ Yearly Avg Ships / Week</span>
                  <span className="ag-co-legend-item is-mint">● End-of-Year / Weekly Rate</span>
                  <span className="ag-co-legend-item is-amber">-- Steady-State Target</span>
                  <span className="ag-co-yearly-hover-readout">
                    {hoverSummary || "Hover over the chart to inspect any year or week."}
                  </span>
                </div>
                <canvas
                  ref={canvasRef}
                  id="co-yearly-graph-canvas"
                  className="ag-co-yearly-canvas"
                  onMouseMove={handleCanvasMouseMove}
                  onMouseLeave={() => setHoverIndex(null)}
                />
              </div>

              <div className="table-wrap table-scroll ag-co-yearly-table-wrap">
                <table id="co-yearly-breakdown-table">
                  <thead>
                    <tr>
                      <th>Year</th>
                      <th className="col-right">Start (Ships/Wk)</th>
                      <th className="col-right">Avg (Ships/Wk)</th>
                      <th className="col-right">End (Ships/Wk)</th>
                      <th className="col-right">YoY Gain</th>
                      <th className="col-right">Ships Produced</th>
                      <th className="col-right">Cumulative Ships</th>
                      <th>Bottleneck</th>
                      <th className="col-right">Plan Built</th>
                    </tr>
                  </thead>
                  <tbody>
                    {projection.years.map((yr) => (
                      <tr key={yr.year}>
                        <td className="tabular">
                          <strong>{yr.label}</strong>
                        </td>
                        <td className="col-right tabular">{fmtShipsRate(yr.start_ships_per_week)}</td>
                        <td className="col-right tabular">
                          <strong>{fmtShipsRate(yr.avg_ships_per_week)}</strong>
                        </td>
                        <td className="col-right tabular text-mint">
                          {fmtShipsRate(yr.end_ships_per_week)}
                        </td>
                        <td className="col-right tabular">
                          {yr.delta_ships_per_week >= 0
                            ? `+${fmtShipsRate(yr.delta_ships_per_week)}`
                            : fmtShipsRate(yr.delta_ships_per_week)}
                        </td>
                        <td className="col-right tabular">
                          {fmtInt(Math.round(yr.total_ships_produced))}
                        </td>
                        <td className="col-right tabular">
                          {fmtInt(Math.round(yr.cumulative_ships_produced))}
                        </td>
                        <td className="tabular">{yr.bottleneck}</td>
                        <td className="col-right tabular">{`${yr.completion_pct.toFixed(1)}%`}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </>
          ) : loading ? (
            <div className="empty-cell tabular">
              Calculating year-by-year Ships / Week trajectory...
            </div>
          ) : (
            <div className="empty-cell">
              Run Config Optimizer first to project year-by-year ship production.
            </div>
          )}
        </div>
      </div>
    </div>
  );
};
