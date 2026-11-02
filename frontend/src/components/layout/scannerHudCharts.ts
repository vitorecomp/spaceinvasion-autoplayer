import type { HourlyProdPoint, LootRecycleBucket } from "./scannerHudTelemetry";

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

export function drawHourlyProductionLineChart(
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

export function drawLootRecycleBarChart(
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
