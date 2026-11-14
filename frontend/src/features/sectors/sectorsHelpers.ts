import type {
  GalaxyStatsItem,
  ScanQueueItem,
  ScannedSectorItem,
  ScannedSectorsPayload,
} from "../../types/api";
import { finiteNum, fmtInt } from "../../utils/formatters";

export const ALL_GALAXIES: readonly number[] = [1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14];

export function formatShortAge(ageSeconds: unknown): string {
  const sec = finiteNum(ageSeconds, null);
  if (sec === null || sec < 0) return "—";
  const s = Math.round(sec);
  if (s < 60) return `${s}s ago`;
  const m = Math.floor(s / 60);
  if (m < 60) return `${m}m ago`;
  const h = Math.floor(m / 60);
  const remM = m % 60;
  if (h < 24) return remM > 0 ? `${h}h ${remM}m ago` : `${h}h ago`;
  const d = Math.floor(h / 24);
  const remH = h % 24;
  return remH > 0 ? `${d}d ${remH}h ago` : `${d}d ago`;
}

export function formatSectorDistance(row: {
  system_distance?: number | null;
  galaxy_delta?: number | null;
  system_delta?: number | null;
  circular_distance?: number | null;
}): { label: string; title: string } {
  const sysDist = finiteNum(row.system_distance, null);
  const gDiff = finiteNum(row.galaxy_delta, null);
  const sDiff = finiteNum(row.system_delta, null);
  const circ = finiteNum(row.circular_distance, null);

  if (sysDist === null && gDiff === null && sDiff === null) {
    return { label: "—", title: "Distance unavailable (no active origin colony)" };
  }
  const gInt = Math.max(0, Math.round(gDiff ?? 0));
  const sInt = Math.max(0, Math.round(sDiff ?? Math.max(0, (sysDist ?? 0) - gInt * 400)));

  if (gInt === 0 && sInt === 0) {
    return { label: "Same sys", title: "Same solar system as active colony" };
  }
  if (gInt === 0) {
    const circNote = circ !== null && circ !== sInt ? ` (wrap ${Math.round(circ)} sys)` : "";
    return {
      label: `${fmtInt(sInt)} sys`,
      title: `Same galaxy · ${fmtInt(sInt)} systems away${circNote}`,
    };
  }
  return {
    label: `${gInt}G · ${fmtInt(sInt)} sys`,
    title: `${gInt} galaxy jump(s), ${fmtInt(sInt)} systems delta`,
  };
}

export function isSectorRowScanned(sec: ScannedSectorItem): boolean {
  if (typeof sec.scanned === "boolean") return sec.scanned;
  return finiteNum(sec.last_scanned_ts, 0) > 0;
}

export function buildGalaxyStats(
  sectorsData: ScannedSectorsPayload | null,
  allSectors: ScannedSectorItem[],
  scanQueue: ScanQueueItem[]
): GalaxyStatsItem[] {
  if (Array.isArray(sectorsData?.galaxy_stats) && sectorsData!.galaxy_stats.length > 0) {
    const byGal = new Map<number, GalaxyStatsItem>();
    (sectorsData!.galaxy_stats as GalaxyStatsItem[]).forEach((st) => {
      const gi = Math.round(finiteNum(st?.galaxy, 0));
      if (gi >= 1 && gi <= 14) byGal.set(gi, st);
    });
    return ALL_GALAXIES.map(
      (g) =>
        byGal.get(g) || {
          galaxy: g,
          is_owned: false,
          in_sweep_scope: false,
          scanned_count: 0,
          unscanned_count: 400,
          total_systems: 400,
          coverage_pct: 0,
          occupied_count: 0,
          debris_count: 0,
          target_count: 0,
          prime_free_count: 0,
          queued_count: 0,
        }
    );
  }

  const scannedByGal = new Map<number, number>();
  const queuedByGal = new Map<number, number>();
  allSectors.forEach((s) => {
    const gi = Math.round(finiteNum(s.galaxy, 0));
    if (gi >= 1 && gi <= 14 && isSectorRowScanned(s)) {
      scannedByGal.set(gi, (scannedByGal.get(gi) ?? 0) + 1);
    }
  });
  scanQueue.forEach((q) => {
    const gi = Math.round(finiteNum(q.galaxy, 0));
    if (gi >= 1 && gi <= 14) {
      queuedByGal.set(gi, (queuedByGal.get(gi) ?? 0) + 1);
    }
  });
  return ALL_GALAXIES.map((g) => {
    const sc = scannedByGal.get(g) ?? 0;
    return {
      galaxy: g,
      is_owned: false,
      in_sweep_scope: false,
      scanned_count: sc,
      unscanned_count: Math.max(0, 400 - sc),
      total_systems: 400,
      coverage_pct: Math.round((sc / 400) * 10000) / 100,
      occupied_count: 0,
      debris_count: 0,
      target_count: 0,
      prime_free_count: 0,
      queued_count: queuedByGal.get(g) ?? 0,
    };
  });
}
