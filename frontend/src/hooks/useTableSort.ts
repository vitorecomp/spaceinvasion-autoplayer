import { useCallback, useMemo, useState } from "react";

export type SortDirection = "asc" | "desc";
export type SortValue = string | number | boolean | null | undefined;

export interface SortColumnSpec<Row> {
  accessor: (row: Row) => SortValue;
  defaultDirection?: SortDirection;
}

export type SortColumnConfig<Row> =
  | ((row: Row) => SortValue)
  | SortColumnSpec<Row>;

export type SortColumnMap<Row, Key extends string> = Record<Key, SortColumnConfig<Row>>;

export interface UseTableSortOptions<Key extends string> {
  initialKey?: Key | null;
  initialDirection?: SortDirection;
}

export interface TableSortController<Row, Key extends string> {
  sortedRows: Row[];
  sortKey: Key | null;
  sortDirection: SortDirection | null;
  requestSort: (key: Key) => void;
  resetSort: () => void;
}

function isMissingSortValue(val: SortValue): boolean {
  if (val === null || val === undefined) return true;
  if (typeof val === "number") return !Number.isFinite(val);
  if (typeof val === "string") return val.trim() === "";
  return false;
}

export function compareSortValues(
  a: SortValue,
  b: SortValue,
  direction: SortDirection
): number {
  const aMiss = isMissingSortValue(a);
  const bMiss = isMissingSortValue(b);
  if (aMiss && bMiss) return 0;
  // Always keep missing / empty / unprobed values at the bottom regardless of sort direction
  if (aMiss) return 1;
  if (bMiss) return -1;

  let cmp = 0;
  if (typeof a === "number" && typeof b === "number") {
    cmp = a - b;
  } else if (typeof a === "boolean" && typeof b === "boolean") {
    cmp = Number(a) - Number(b);
  } else {
    cmp = String(a).localeCompare(String(b), undefined, {
      numeric: true,
      sensitivity: "base",
    });
  }

  return direction === "asc" ? cmp : -cmp;
}

function resolveColumnSpec<Row>(
  config: SortColumnConfig<Row> | undefined
): SortColumnSpec<Row> | null {
  if (!config) return null;
  if (typeof config === "function") {
    return { accessor: config, defaultDirection: "asc" };
  }
  return {
    accessor: config.accessor,
    defaultDirection: config.defaultDirection ?? "asc",
  };
}

/**
 * Reusable client-side table sorting hook with 3-state header cycling:
 *   1st click -> column defaultDirection ("asc" by default, or "desc" if specified)
 *   2nd click -> opposite direction
 *   3rd click -> reset to natural/server order (null)
 *
 * Uses a stable Schwartzian transform and keeps null/empty values at the bottom.
 */
export function useTableSort<Row, Key extends string>(
  rows: readonly Row[],
  columns: SortColumnMap<Row, Key>,
  options?: UseTableSortOptions<Key>
): TableSortController<Row, Key> {
  const [sortState, setSortState] = useState<{
    key: Key | null;
    direction: SortDirection | null;
  }>(() => ({
    key: options?.initialKey ?? null,
    direction: options?.initialKey ? (options.initialDirection ?? "asc") : null,
  }));

  const requestSort = useCallback(
    (key: Key) => {
      const spec = resolveColumnSpec(columns[key]);
      const defaultDir: SortDirection = spec?.defaultDirection ?? "asc";
      const flippedDir: SortDirection = defaultDir === "asc" ? "desc" : "asc";

      setSortState((prev) => {
        if (prev.key !== key) {
          return { key, direction: defaultDir };
        }
        if (prev.direction === defaultDir) {
          return { key, direction: flippedDir };
        }
        return { key: null, direction: null };
      });
    },
    [columns]
  );

  const resetSort = useCallback(() => {
    setSortState({ key: null, direction: null });
  }, []);

  const sortedRows = useMemo(() => {
    const list = Array.isArray(rows) ? rows : [];
    if (!sortState.key || !sortState.direction || list.length <= 1) {
      return [...list];
    }
    const spec = resolveColumnSpec(columns[sortState.key]);
    if (!spec) {
      return [...list];
    }
    const dir = sortState.direction;
    const decorated = list.map((row, idx) => ({
      row,
      idx,
      val: spec.accessor(row),
    }));
    decorated.sort((a, b) => {
      const cmp = compareSortValues(a.val, b.val, dir);
      return cmp !== 0 ? cmp : a.idx - b.idx;
    });
    return decorated.map((item) => item.row);
  }, [rows, columns, sortState.key, sortState.direction]);

  return {
    sortedRows,
    sortKey: sortState.key,
    sortDirection: sortState.direction,
    requestSort,
    resetSort,
  };
}

/* ==================== DOMAIN-AWARE SORT KEY HELPERS ==================== */

/** Parse SpaceInvasion coordinates (GG:SSS:PP or GG:SSS) into a numeric key. */
export function coordsSortKey(raw: unknown): number | null {
  const txt = String(raw ?? "").trim();
  const m = txt.match(/(\d{1,2})\s*:\s*(\d{1,3})(?:\s*:\s*(\d{1,2}))?/);
  if (!m) return null;
  const g = Number.parseInt(m[1], 10);
  const s = Number.parseInt(m[2], 10);
  const p = m[3] !== undefined ? Number.parseInt(m[3], 10) : 0;
  if (!Number.isFinite(g) || !Number.isFinite(s) || !Number.isFinite(p)) return null;
  return g * 1_000_000 + s * 100 + p;
}

/** Order P0 < P1 < P2 < P3 (with EXEC / GAME slots before P0 if ever compared). */
export function prioritySortKey(raw: unknown): number | null {
  const txt = String(raw ?? "").trim().toUpperCase();
  if (!txt) return null;
  if (txt === "EXEC") return -2;
  if (txt.startsWith("GAME")) return -1;
  const m = txt.match(/^P(\d+)$/);
  if (m) return Number.parseInt(m[1], 10);
  return null;
}

const GRADE_RANK: Record<string, number> = {
  "S+": 0,
  S: 1,
  "A+": 2,
  A: 3,
  "B+": 4,
  B: 5,
  "C+": 6,
  C: 7,
  D: 8,
  F: 9,
};

/** Order colony grades S -> A+ -> A -> B -> C -> D (unprobed "?" returns null so it stays bottom). */
export function gradeSortKey(raw: unknown): number | null {
  const txt = String(raw ?? "").trim().toUpperCase();
  if (!txt || txt === "?" || txt === "—" || txt === "-") return null;
  if (Object.prototype.hasOwnProperty.call(GRADE_RANK, txt)) {
    return GRADE_RANK[txt];
  }
  return null;
}

/** Sum the 4 SpaceInvasion resources from a resource dictionary. */
export function resourcesTotal(res: unknown): number | null {
  if (!res || typeof res !== "object") return null;
  const obj = res as Record<string, unknown>;
  const keys = ["pig_iron", "metal", "kryptonite", "spice"] as const;
  let found = false;
  let total = 0;
  for (const k of keys) {
    const raw = obj[k];
    if (raw === null || raw === undefined || raw === "") continue;
    const n = Number(raw);
    if (Number.isFinite(n)) {
      found = true;
      total += n;
    }
  }
  return found ? total : null;
}

/** Sum numeric values in a unit/building/research dictionary (returns null if empty). */
export function unitDictTotal(obj: unknown): number | null {
  if (!obj || typeof obj !== "object") return null;
  let found = false;
  let total = 0;
  for (const val of Object.values(obj as Record<string, unknown>)) {
    const n = Number(val);
    if (Number.isFinite(n) && n > 0) {
      found = true;
      total += n;
    }
  }
  return found ? total : null;
}

/** Convert a numeric duration or human ETA label ("Ready", "2d 4h", "1h 15m", "00:12:30") to seconds. */
export function durationToSeconds(raw: unknown): number | null {
  if (typeof raw === "number") {
    return Number.isFinite(raw) ? raw : null;
  }
  const cleaned = String(raw ?? "")
    .replace(/\([^)]*\)/g, "")
    .trim()
    .toLowerCase();
  if (
    !cleaned ||
    cleaned === "—" ||
    cleaned === "-" ||
    cleaned === "--" ||
    cleaned === "blocked" ||
    cleaned === "never" ||
    cleaned === "n/a" ||
    cleaned === "unknown"
  ) {
    return null;
  }
  if (
    cleaned === "ready" ||
    cleaned === "now" ||
    cleaned === "instant" ||
    cleaned === "cleared" ||
    cleaned === "0" ||
    cleaned === "0s"
  ) {
    return 0;
  }
  // Colon-separated HH:MM:SS or MM:SS
  const colonMatch = cleaned.match(/^(\d+):(\d{1,2})(?::(\d{1,2}))?$/);
  if (colonMatch) {
    if (colonMatch[3] !== undefined) {
      return (
        Number(colonMatch[1]) * 3600 +
        Number(colonMatch[2]) * 60 +
        Number(colonMatch[3])
      );
    }
    return Number(colonMatch[1]) * 60 + Number(colonMatch[2]);
  }

  // Unit tokens like "3w 2d 4h 12m 5s" or "+24h"
  const tokenRe = /(\d+(?:\.\d+)?)\s*(w|d|h|m|s)\b/g;
  let total = 0;
  let matched = false;
  let m: RegExpExecArray | null;
  while ((m = tokenRe.exec(cleaned)) !== null) {
    matched = true;
    const val = Number(m[1]);
    const unit = m[2];
    if (unit === "w") total += val * 7 * 86400;
    else if (unit === "d") total += val * 86400;
    else if (unit === "h") total += val * 3600;
    else if (unit === "m") total += val * 60;
    else if (unit === "s") total += val;
  }
  if (matched) return total;

  const plainNum = Number(cleaned);
  return Number.isFinite(plainNum) ? plainNum : null;
}

/** Extract a comparable numeric epoch timestamp (seconds) from a numeric ts or date string. */
export function timestampSortKey(ts: unknown, fallbackText?: unknown): number | null {
  if (typeof ts === "number" && Number.isFinite(ts) && ts > 0) {
    return ts;
  }
  if (typeof ts === "string" && ts.trim() !== "") {
    const asNum = Number(ts.trim());
    if (Number.isFinite(asNum) && asNum > 0) return asNum;
    const parsed = Date.parse(ts.trim());
    if (Number.isFinite(parsed)) return parsed / 1000;
  }
  if (typeof fallbackText === "string" && fallbackText.trim() !== "") {
    const parsed = Date.parse(fallbackText.trim());
    if (Number.isFinite(parsed)) return parsed / 1000;
  }
  return null;
}
