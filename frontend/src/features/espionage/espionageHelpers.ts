import { finiteNum, fmtInt } from "../../utils/formatters";

interface ParsedCoords {
  g: number;
  s: number;
  p: number;
}

export type TargetFilterKey = "all" | "spied" | "spacia" | "inactive" | "debris";

export const TARGET_FILTERS: ReadonlyArray<{ key: TargetFilterKey; label: string }> = [
  { key: "all", label: "All Targets" },
  { key: "spied", label: "Spied Intel" },
  { key: "spacia", label: "Spacia" },
  { key: "inactive", label: "Inactive" },
  { key: "debris", label: "Has Debris" },
];

function parseCoordsTriple(raw: unknown): ParsedCoords | null {
  const txt = String(raw ?? "").trim();
  const m = txt.match(/(\d{1,2})\s*:\s*(\d{1,3})\s*:\s*(\d{1,2})/);
  if (!m) return null;
  const g = Number.parseInt(m[1], 10);
  const s = Number.parseInt(m[2], 10);
  const p = Number.parseInt(m[3], 10);
  if (!Number.isFinite(g) || !Number.isFinite(s) || !Number.isFinite(p)) return null;
  return { g, s, p };
}

export function espionageDistanceSortKey(
  row: Record<string, any>,
  originCoords: string,
  targetCoords: string
): number | null {
  const oParsed = parseCoordsTriple(originCoords);
  const tParsed = parseCoordsTriple(targetCoords);

  let gDiff = finiteNum(row?.galaxy_delta, null);
  let sDiff = finiteNum(row?.system_delta, null);
  let pDiff = finiteNum(row?.position_delta, null);
  let sysDist = finiteNum(row?.system_distance, null);
  const flightDist = finiteNum(row?.distance, null);

  if (oParsed && tParsed) {
    if (gDiff === null) gDiff = Math.abs(oParsed.g - tParsed.g);
    if (sDiff === null) sDiff = Math.abs(oParsed.s - tParsed.s);
    if (pDiff === null) pDiff = Math.abs(oParsed.p - tParsed.p);
    if (sysDist === null) sysDist = gDiff * 400 + sDiff;
  }

  if (sysDist === null && gDiff === null && sDiff === null) {
    return flightDist !== null && flightDist >= 0 ? flightDist : null;
  }

  const gInt = Math.max(0, Math.round(gDiff ?? 0));
  const sInt = Math.max(0, Math.round(sDiff ?? Math.max(0, (sysDist ?? 0) - gInt * 400)));
  const pInt = Math.max(0, Math.round(pDiff ?? 0));
  return (gInt * 400 + sInt) * 100 + pInt;
}

export function formatEspionageDistance(
  row: Record<string, any>,
  originCoords: string,
  targetCoords: string
): { label: string; title: string } {
  const oParsed = parseCoordsTriple(originCoords);
  const tParsed = parseCoordsTriple(targetCoords);

  let gDiff = finiteNum(row?.galaxy_delta, null);
  let sDiff = finiteNum(row?.system_delta, null);
  let pDiff = finiteNum(row?.position_delta, null);
  let sysDist = finiteNum(row?.system_distance, null);
  let flightDist = finiteNum(row?.distance, null);

  if (oParsed && tParsed) {
    if (gDiff === null) gDiff = Math.abs(oParsed.g - tParsed.g);
    if (sDiff === null) sDiff = Math.abs(oParsed.s - tParsed.s);
    if (pDiff === null) pDiff = Math.abs(oParsed.p - tParsed.p);
    if (sysDist === null) sysDist = gDiff * 400 + sDiff;
    if (flightDist === null) {
      if (gDiff > 0) flightDist = 20000 * gDiff;
      else if (sDiff > 0) flightDist = 2700 + 95 * sDiff;
      else flightDist = 1000 + 5 * pDiff;
    }
  }

  if (sysDist === null && gDiff === null && sDiff === null) {
    return { label: "—", title: "Distance unavailable (missing origin or target coordinates)" };
  }

  const gInt = Math.max(0, Math.round(gDiff ?? 0));
  const sInt = Math.max(0, Math.round(sDiff ?? Math.max(0, (sysDist ?? 0) - gInt * 400)));
  const pInt = Math.max(0, Math.round(pDiff ?? 0));
  const distStr =
    flightDist !== null && flightDist >= 0 ? ` · Flight dist ${fmtInt(Math.round(flightDist))}` : "";
  const fromStr = originCoords ? `From ${originCoords} · ` : "";

  if (gInt > 0) {
    const label = sInt > 0 ? `${gInt}G · ${sInt} sys` : `${gInt}G`;
    return {
      label,
      title: `${fromStr}Δ${gInt} gal, Δ${sInt} sys${distStr}`,
    };
  }
  if (sInt === 0) {
    const posStr = pInt > 0 ? ` (pos Δ${pInt})` : "";
    return {
      label: "Same sys",
      title: `${fromStr}Same system${posStr}${distStr}`,
    };
  }
  return {
    label: `${sInt} sys`,
    title: `${fromStr}${sInt} solar systems away${distStr}`,
  };
}
