import type { DashboardPayload, PlanetData, ResourceBundle } from "../types/api";

export function finiteNum(val: unknown): number | null;
export function finiteNum(val: unknown, fallback: number): number;
export function finiteNum<T>(val: unknown, fallback: T): number | T;
export function finiteNum<T = null>(val: unknown, fallback: T = null as unknown as T): number | T {
  if (
    val === null ||
    val === undefined ||
    typeof val === "boolean" ||
    typeof val === "object" ||
    typeof val === "symbol" ||
    (typeof val === "string" && val.trim() === "")
  ) {
    return fallback;
  }
  const n = Number(val);
  return Number.isFinite(n) ? n : fallback;
}

export function clampPct(val: unknown): number {
  const n = finiteNum(val, 0);
  return Math.max(0, Math.min(100, n));
}

export function pad2Digits(n: number): string {
  return String(Math.max(0, Math.trunc(n))).padStart(2, "0");
}

export function fmtInt(n: unknown): string {
  if (n === null || n === undefined || n === "") return "0";
  const v = finiteNum(n, null);
  if (v === null) return "—";
  const clean = v === 0 ? 0 : v;
  const formatted = clean.toLocaleString("en-US");
  return formatted === "-0" ? "0" : formatted;
}

export function fmtRate(n: unknown): string {
  if (n === null || n === undefined || n === "") return "0/h";
  const raw = finiteNum(n, null);
  if (raw === null) return "—/h";
  const rounded = Math.round(raw);
  const v = rounded === 0 ? 0 : rounded;
  const sign = v > 0 ? "+" : "";
  return `${sign}${fmtInt(v)}/h`;
}

export function fmtCompact(val: unknown): string {
  const n = finiteNum(val, null);
  if (n === null) return "—";
  const absN = Math.abs(n);
  if (absN >= 1e6) return `${(n / 1e6).toFixed(2)}M`;
  if (absN >= 1e4) return `${(n / 1e3).toFixed(1)}k`;
  if (Number.isInteger(n)) return fmtInt(n);
  return absN < 0.05 ? "0.0" : n.toFixed(1);
}

export function fmtImprovementPct(rawPct: unknown, baseFit?: unknown, bestFit?: unknown): string {
  let pct = finiteNum(rawPct, null);
  const base = finiteNum(baseFit, null);
  const best = finiteNum(bestFit, null);
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

export function fmtHms(seconds: unknown): string {
  const n = finiteNum(seconds, null);
  if (n === null) return "—";
  const s = Math.max(0, Math.round(n));
  if (s <= 0) return "00:00:00";
  if (s >= 86400) {
    const d = Math.floor(s / 86400);
    const rem = s % 86400;
    const h = Math.floor(rem / 3600);
    const m = Math.floor((rem % 3600) / 60);
    const sec = rem % 60;
    return `${d}d ${pad2Digits(h)}:${pad2Digits(m)}:${pad2Digits(sec)}`;
  }
  const h = Math.floor(s / 3600);
  const m = Math.floor((s % 3600) / 60);
  const sec = s % 60;
  return `${pad2Digits(h)}:${pad2Digits(m)}:${pad2Digits(sec)}`;
}

export const formatCountdownHms = fmtHms;

export function fmtDurationCountdown(secVal: unknown): string {
  const sec = Math.max(0, Math.round(finiteNum(secVal, 0)));
  if (sec <= 0) return "Expired";
  const h = Math.floor(sec / 3600);
  const m = Math.floor((sec % 3600) / 60);
  const s = sec % 60;
  if (h > 0) return `${h}h ${String(m).padStart(2, "0")}m`;
  if (m > 0) return `${m}m ${String(s).padStart(2, "0")}s`;
  return `${s}s`;
}

export function fmtClockTime(finishTs: unknown, nowEpochSec?: number): string {
  const ts = finiteNum(finishTs, null);
  if (ts === null || ts <= 0) return "—";
  const d = new Date(ts * 1000);
  if (Number.isNaN(d.getTime())) return "—";
  const refMs =
    nowEpochSec != null && Number.isFinite(nowEpochSec) && nowEpochSec > 0
      ? nowEpochSec * 1000
      : Date.now();
  const now = new Date(refMs);
  const hms = `${pad2Digits(d.getHours())}:${pad2Digits(d.getMinutes())}:${pad2Digits(d.getSeconds())}`;
  const sameDay =
    d.getFullYear() === now.getFullYear() &&
    d.getMonth() === now.getMonth() &&
    d.getDate() === now.getDate();
  if (sameDay) return hms;
  return `${pad2Digits(d.getMonth() + 1)}-${pad2Digits(d.getDate())} ${hms}`;
}

export const formatFinishClock = fmtClockTime;

export function fmtClock(tsSec: unknown): string {
  if (!tsSec) return "SYNC --:--:--";
  const ts = finiteNum(tsSec, null);
  if (ts === null) return "SYNC ACTIVE";
  const d = new Date(ts * 1000);
  if (Number.isNaN(d.getTime())) return "SYNC ACTIVE";
  return `SYNC ${d.toTimeString().slice(0, 8)}`;
}

export const fmtSyncClock = fmtClock;

export function fmtShortDateTime(tsSec: unknown, fallbackTxt = "—"): string {
  const ts = finiteNum(tsSec, null);
  if (ts !== null && ts > 0) {
    const d = new Date(ts * 1000);
    if (!Number.isNaN(d.getTime())) {
      const mm = String(d.getMonth() + 1).padStart(2, "0");
      const dd = String(d.getDate()).padStart(2, "0");
      const hh = String(d.getHours()).padStart(2, "0");
      const mi = String(d.getMinutes()).padStart(2, "0");
      return `${mm}-${dd} ${hh}:${mi}`;
    }
  }
  return fallbackTxt && String(fallbackTxt).trim() ? String(fallbackTxt).trim() : "—";
}

export function formatNotifAge(tsVal: unknown, nowEpochSec?: number): string {
  const ts = finiteNum(tsVal, 0);
  if (ts <= 0) return "just now";
  const refSec =
    nowEpochSec != null && Number.isFinite(nowEpochSec) && nowEpochSec > 0
      ? nowEpochSec
      : Date.now() / 1000;
  const sec = Math.max(0, Math.round(refSec - ts));
  if (sec < 60) return `${sec}s ago`;
  if (sec < 3600) return `${Math.floor(sec / 60)}m ago`;
  if (sec < 86400) return `${Math.floor(sec / 3600)}h ago`;
  return `${Math.floor(sec / 86400)}d ago`;
}

export function formatResourcesInline(res: ResourceBundle | Record<string, any> | null | undefined): string {
  if (!res) return "—";
  const parts: string[] = [];
  if (res.pig_iron) parts.push(`${fmtInt(res.pig_iron)} Pig`);
  if (res.metal) parts.push(`${fmtInt(res.metal)} Met`);
  if (res.kryptonite) parts.push(`${fmtInt(res.kryptonite)} Kryp`);
  if (res.spice) parts.push(`${fmtInt(res.spice)} Spi`);
  return parts.join(" · ") || "0";
}

export function formatUnitDictShort(
  obj: Record<string, unknown> | null | undefined,
  limit = 4
): string {
  if (!obj || typeof obj !== "object") return "None";
  const entries = Object.entries(obj)
    .map(([k, v]) => [String(k), Math.round(finiteNum(v, 0))] as const)
    .filter(([, v]) => v > 0);
  if (entries.length === 0) return "None";
  const head = entries
    .slice(0, limit)
    .map(([k, v]) => `${k}:${fmtInt(v)}`)
    .join(" · ");
  return entries.length > limit ? `${head} (+${entries.length - limit})` : head;
}

export function bottleneckTag(code: unknown): string {
  if (code == null) return "";
  const norm = String(code).trim().toLowerCase();
  if (!norm) return "";
  if (norm === "pig_iron" || norm === "pig") return "PIG";
  if (norm === "metal" || norm === "met") return "MET";
  if (norm === "kryptonite" || norm === "kryp" || norm === "kry") return "KRY";
  if (norm === "spice" || norm === "spi") return "SPI";
  return norm.toUpperCase();
}

export function gradeClassFor(gradeRaw: unknown): string {
  const g = String(gradeRaw || "?").trim().toUpperCase();
  if (g === "S") return "grade-s";
  if (g === "A+") return "grade-aplus";
  if (g === "A") return "grade-a";
  if (g === "B") return "grade-b";
  if (g === "C") return "grade-c";
  if (g === "D") return "grade-d";
  return "grade-unknown";
}

export function geneShortTag(kind: unknown): string {
  const k = String(kind || "").trim().toUpperCase();
  if (!k) return "";
  if (k === "BUILD") return "BLD";
  if (k === "DEMOLISH_BUILDING") return "DEM";
  if (k === "WAIT_RESOURCES") return "WAIT";
  if (k === "RESEARCH") return "RES";
  if (k === "BUILD_SHIP") return "SHP";
  if (k === "ATTACK") return "ATK";
  if (k === "SPY") return "SPY";
  if (k === "RECYCLE") return "REC";
  if (k === "COLONIZE") return "COL";
  if (k === "SCAN_GALAXY") return "SCN";
  if (k === "THROTTLE_BUILDING") return "B2%";
  if (k === "CLAIM_TUTORIAL") return "TUT";
  return k.slice(0, 4);
}

export function activePlanetFrom(
  dash: DashboardPayload | null | undefined,
  selectedPlanetId?: number | null
): PlanetData | null {
  const gs = dash?.state;
  if (!gs || !Array.isArray(gs.planets) || gs.planets.length === 0) return null;
  const fallbackSelected =
    selectedPlanetId !== undefined
      ? selectedPlanetId
      : finiteNum((globalThis as any)?.state?.selectedPlanetId, null);
  const targetId = fallbackSelected || gs.active_planet_id;
  return gs.planets.find((p) => p.planet_id === targetId) || gs.planets[0] || null;
}

export function getSelectedBlastFurnacePct(
  dash: DashboardPayload | null | undefined,
  selectedPlanetId?: number | null,
  buildingsEtaOverride?: Record<string, any> | null
): number {
  const planet = activePlanetFrom(dash, selectedPlanetId);
  let eta = dash?.buildings_eta || null;
  const globalDash = (globalThis as any)?.state?.dashboard;
  const cachedEta =
    buildingsEtaOverride !== undefined
      ? buildingsEtaOverride
      : (globalThis as any)?.state?.buildingsEtaData ?? null;
  if (dash && dash !== globalDash && dash.buildings_eta) {
    eta = dash.buildings_eta;
  } else if (cachedEta && planet && cachedEta.planet?.planet_id === planet.planet_id) {
    eta = cachedEta;
  }
  const r2 = finiteNum(planet?.economy_rates?.resource2, null);
  const etaPct = finiteNum(eta?.planet_economy?.blast_furnace_pct, null);
  const pct = r2 !== null ? r2 : etaPct !== null ? etaPct : 100;
  return clampPct(Math.round(pct));
}

export function renderResourceBarDom(
  key: string,
  amount: unknown,
  storage: unknown,
  hourlyRate: unknown
): void {
  if (typeof document === "undefined") return;
  const cap = finiteNum(storage, 0);
  const val = finiteNum(amount, null);
  const ratioPct = cap > 0 && val !== null ? (val / cap) * 100 : 0;
  const pct = Number.isFinite(ratioPct) ? Math.max(0, Math.min(100, Math.round(ratioPct))) : 0;
  const valNode = document.getElementById(`val-${key}`);
  if (valNode) valNode.textContent = fmtInt(amount ?? 0);
  const capNode = document.getElementById(`cap-${key}`);
  if (capNode) capNode.textContent = cap > 0 ? `/ ${fmtInt(cap)} (${pct}%)` : "—";
  const barNode = document.getElementById(`bar-${key}`);
  if (barNode) {
    barNode.style.transform = `scaleX(${pct / 100})`;
    barNode.classList.toggle("high", pct >= 85);
  }
  const rateNode = document.getElementById(`rate-${key}`);
  if (rateNode) {
    if (hourlyRate == null) {
      rateNode.textContent = "—/h";
      rateNode.className = "track-rate";
    } else {
      const rateNum = finiteNum(hourlyRate, null);
      rateNode.textContent = fmtRate(hourlyRate);
      rateNode.className = `track-rate${rateNum !== null && Math.round(rateNum) < 0 ? " is-neg" : ""}`;
    }
  }
}

if (typeof globalThis !== "undefined") {
  const g = globalThis as any;
  g.finiteNum = finiteNum;
  g.clampPct = clampPct;
  g.fmtInt = fmtInt;
  g.fmtRate = fmtRate;
  g.fmtCompact = fmtCompact;
  g.fmtImprovementPct = fmtImprovementPct;
  g.fmtHms = fmtHms;
  g.formatCountdownHms = formatCountdownHms;
  g.fmtDurationCountdown = fmtDurationCountdown;
  g.fmtClockTime = fmtClockTime;
  g.formatFinishClock = formatFinishClock;
  g.fmtClock = fmtClock;
  g.fmtShortDateTime = fmtShortDateTime;
  g.formatResourcesInline = formatResourcesInline;
  g.formatUnitDictShort = formatUnitDictShort;
  g.bottleneckTag = bottleneckTag;
  g.gradeClassFor = gradeClassFor;
  g.geneShortTag = geneShortTag;
  g.activePlanetFrom = activePlanetFrom;
  g.getSelectedBlastFurnacePct = getSelectedBlastFurnacePct;
  g.renderResourceBar = renderResourceBarDom;
}
