import { safeFiniteNumber } from "../../api/httpClient";

const AG_SEMANTIC_EDGE_TYPES = new Set([
  "priority_gate",
  "prerequisite",
  "level_chain",
  "energy_support",
  "throttle_control",
  "storage_cap",
  "resource_feed",
  "loot_feed",
]);

export const DAG_LAYOUT_CONSTANTS = {
  LANE_HEADER_W: 168,
  PAD_X: 20,
  PAD_Y: 14,
  CARD_W: 228,
  CARD_H: 92,
  COL_GAP: 68,
  STACK_GAP: 14,
  LANE_PAD_Y: 16,
} as const;

export function edgeVisualSpec(
  edge: Record<string, any>,
  markerPrefix: string
): { cls: string; marker: string } {
  const pType = String(edge?.primary_type || "execution_order");
  if (pType === "priority_gate") {
    return { cls: "is-gate", marker: `${markerPrefix}-arrow-gate` };
  }
  if (pType === "prerequisite" || pType === "level_chain") {
    return { cls: "is-prereq", marker: `${markerPrefix}-arrow-prereq` };
  }
  if (
    pType === "energy_support" ||
    pType === "throttle_control" ||
    pType === "storage_cap" ||
    pType === "resource_feed" ||
    pType === "loot_feed"
  ) {
    return { cls: "is-resource", marker: `${markerPrefix}-arrow-resource` };
  }
  if (pType === "queue_order") {
    return { cls: "is-queue", marker: `${markerPrefix}-arrow-queue` };
  }
  return { cls: "is-seq", marker: `${markerPrefix}-arrow-seq` };
}

export function edgeMatchesFilter(edge: Record<string, any>, filterMode: string): boolean {
  if (filterMode !== "deps") return true;
  const types = Array.isArray(edge?.types) ? edge.types : [edge?.primary_type];
  return types.some((t) => AG_SEMANTIC_EDGE_TYPES.has(String(t || "")));
}

export function formatDagNodeTargetLabel(node: Record<string, any>): string {
  const base = String(
    node?.target_name || node?.entity_name || node?.name || node?.kind || "Action"
  );
  const lvl = safeFiniteNumber(node?.target_level, null);
  const kind = String(node?.kind || "").toUpperCase();
  if (lvl === null) return base;
  if (kind === "THROTTLE_BUILDING") {
    return `${base} → ${Math.round(lvl)}%`;
  }
  if (kind === "BUILD_SHIP" || kind === "SHIPS") {
    if (/\(x\d+\)$/i.test(base)) return base;
    return `${base} (x${Math.max(1, Math.round(lvl))})`;
  }
  if (kind === "BUILD" || kind === "WAIT_RESOURCES" || kind === "RESEARCH") {
    return `${base} L${Math.round(lvl)}`;
  }
  if (kind === "DEMOLISH_BUILDING" || kind === "DEMOLISH") {
    return `Demolish ${base} → L${Math.round(lvl)}`;
  }
  return base;
}

export function formatNodeStatusShort(status: string): string {
  const norm = status.toLowerCase();
  if (norm === "ready" || norm === "active" || norm === "in_flight") return "READY";
  if (norm === "completed" || norm === "done") return "DONE";
  if (norm === "waiting_resources") return "WAIT";
  if (norm === "blocked_gate" || norm === "conflict") return "HOLD";
  return "QUEUE";
}

export function nodeStatusCssClass(status: string): string {
  const norm = status.toLowerCase();
  if (
    norm === "ready" ||
    norm === "active" ||
    norm === "in_flight" ||
    norm === "completed" ||
    norm === "done"
  ) {
    return "is-ready";
  }
  if (norm === "waiting_resources") return "is-waiting";
  if (norm === "blocked_gate" || norm === "conflict") return "is-blocked";
  return "is-queued";
}

export interface DagComputedLayout {
  activeLanes: Array<Record<string, any>>;
  laneTops: number[];
  laneHeights: number[];
  totalWidth: number;
  totalHeight: number;
  coordsById: Map<string, { x: number; y: number; colIdx: number; laneIdx: number }>;
  lanePlanetNameById: Map<string, string>;
}

export function formatDagNodePlanetBadge(
  n: Record<string, any>,
  lanePlanetNameById: Map<string, string>
): string {
  const coords = String(n.planet_coords || "").trim();
  const rawName = String(
    n.planet_name || lanePlanetNameById.get(String(n.lane_id || "")) || ""
  ).trim();
  const cleanName = rawName && rawName.toLowerCase() !== "colony" ? rawName : "";
  if (cleanName && coords) return `${cleanName} [${coords}]`;
  if (coords) return `[${coords}]`;
  if (cleanName) return cleanName;
  return "[GLOBAL]";
}

export function computeDagLayout(
  graph: Record<string, any> | null,
  nodes: Array<Record<string, any>>
): DagComputedLayout {
  const { LANE_HEADER_W, PAD_X, PAD_Y, CARD_W, CARD_H, COL_GAP, STACK_GAP, LANE_PAD_Y } =
    DAG_LAYOUT_CONSTANTS;

  const rawLanes = Array.isArray(graph?.lanes) ? graph!.lanes : [];
  const activeLanes: Array<Record<string, any>> = [];
  const laneIdxById = new Map<string, number>();

  rawLanes.forEach((ln) => {
    const lid = String(ln?.id || "");
    if (!lid) return;
    if (nodes.some((n) => n.lane_id === lid)) {
      laneIdxById.set(lid, activeLanes.length);
      activeLanes.push(ln);
    }
  });

  nodes.forEach((n) => {
    const lid = String(n.lane_id || "lane:global");
    if (!laneIdxById.has(lid)) {
      laneIdxById.set(lid, activeLanes.length);
      activeLanes.push({
        id: lid,
        scope: lid === "lane:global" ? "global" : "planet",
        label:
          lid === "lane:global"
            ? "Empire & Global Research"
            : n.planet_coords
              ? `${n.planet_name || "Colony"} [${n.planet_coords}]`
              : String(n.planet_name || lid),
        planet_id: safeFiniteNumber(n.planet_id, 0) ?? 0,
        coords: String(n.planet_coords || ""),
        energy: 0,
        node_count: 1,
      });
    }
  });

  const lanePlanetNameById = new Map<string, string>();
  activeLanes.forEach((ln) => {
    const lid = String(ln.id || "");
    if (!lid || lid === "lane:global") return;
    const rawLbl = String(ln.label || "")
      .replace(/\s*\[\s*\d{1,2}:\d{1,3}:\d{1,2}\s*\]\s*$/, "")
      .trim();
    if (rawLbl && rawLbl.toLowerCase() !== "colony") {
      lanePlanetNameById.set(lid, rawLbl);
    }
  });

  const cellStacks = new Map<string, number>();
  const maxStackByLane = new Array<number>(activeLanes.length).fill(1);
  let maxCol = 0;
  const nodePlacement = new Map<string, { laneIdx: number; colIdx: number; stackIdx: number }>();

  nodes.forEach((n, idx) => {
    const lIdx = laneIdxById.get(String(n.lane_id || "lane:global")) ?? 0;
    const colIdx = Math.max(0, Math.round(safeFiniteNumber(n.topological_layer, idx) ?? idx));
    if (colIdx > maxCol) maxCol = colIdx;
    const cellKey = `${lIdx}:${colIdx}`;
    const stackIdx = cellStacks.get(cellKey) || 0;
    cellStacks.set(cellKey, stackIdx + 1);
    if (stackIdx + 1 > (maxStackByLane[lIdx] || 1)) {
      maxStackByLane[lIdx] = stackIdx + 1;
    }
    nodePlacement.set(String(n.id), { laneIdx: lIdx, colIdx, stackIdx });
  });

  const laneTops: number[] = [];
  const laneHeights: number[] = [];
  let cursorY = PAD_Y;
  for (let i = 0; i < activeLanes.length; i += 1) {
    const stacks = maxStackByLane[i] || 1;
    const h = stacks * CARD_H + Math.max(0, stacks - 1) * STACK_GAP + LANE_PAD_Y * 2;
    laneTops.push(cursorY);
    laneHeights.push(h);
    cursorY += h;
  }

  const totalWidth = Math.max(
    680,
    LANE_HEADER_W + PAD_X * 2 + (maxCol + 1) * CARD_W + maxCol * COL_GAP + 24
  );
  const totalHeight = Math.max(240, cursorY + PAD_Y);

  const coordsById = new Map<string, { x: number; y: number; colIdx: number; laneIdx: number }>();
  nodes.forEach((node) => {
    const nid = String(node.id);
    const plc = nodePlacement.get(nid) || { laneIdx: 0, colIdx: 0, stackIdx: 0 };
    const x = LANE_HEADER_W + PAD_X + plc.colIdx * (CARD_W + COL_GAP);
    const y = (laneTops[plc.laneIdx] || PAD_Y) + LANE_PAD_Y + plc.stackIdx * (CARD_H + STACK_GAP);
    coordsById.set(nid, { x, y, colIdx: plc.colIdx, laneIdx: plc.laneIdx });
  });

  return {
    activeLanes,
    laneTops,
    laneHeights,
    totalWidth,
    totalHeight,
    coordsById,
    lanePlanetNameById,
  };
}
