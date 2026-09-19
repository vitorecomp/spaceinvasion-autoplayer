import React, { useRef, useState } from "react";
import { safeFiniteNumber } from "../../api/httpClient";
import { useDragScroll } from "../../hooks/useDragScroll";
import { sanitizeMainWindowText } from "./plannerHelpers";

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

function edgeVisualSpec(
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

function edgeMatchesFilter(edge: Record<string, any>, filterMode: string): boolean {
  if (filterMode !== "deps") return true;
  const types = Array.isArray(edge?.types) ? edge.types : [edge?.primary_type];
  return types.some((t) => AG_SEMANTIC_EDGE_TYPES.has(String(t || "")));
}

export function formatDagNodeTargetLabel(node: Record<string, any>): string {
  const base = String(node?.target_name || node?.entity_name || node?.name || node?.kind || "Action");
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

function formatNodeStatusShort(status: string): string {
  const norm = status.toLowerCase();
  if (norm === "ready" || norm === "active" || norm === "in_flight") return "READY";
  if (norm === "completed" || norm === "done") return "DONE";
  if (norm === "waiting_resources") return "WAIT";
  if (norm === "blocked_gate" || norm === "conflict") return "HOLD";
  return "QUEUE";
}

function nodeStatusCssClass(status: string): string {
  const norm = status.toLowerCase();
  if (norm === "ready" || norm === "active" || norm === "in_flight" || norm === "completed" || norm === "done") {
    return "is-ready";
  }
  if (norm === "waiting_resources") return "is-waiting";
  if (norm === "blocked_gate" || norm === "conflict") return "is-blocked";
  return "is-queued";
}

export interface DagCanvasProps {
  graph: Record<string, any> | null;
  idPrefix?: string;
  markerPrefix?: string;
  edgeFilter?: "all" | "deps";
  onEdgeFilterChange?: (next: "all" | "deps") => void;
  showLegendBar?: boolean;
  legendExtraControls?: React.ReactNode;
  emptyMessage?: string;
}

export const DagCanvas: React.FC<DagCanvasProps> = ({
  graph,
  idPrefix = "ag",
  markerPrefix,
  edgeFilter: controlledEdgeFilter,
  onEdgeFilterChange,
  showLegendBar = true,
  legendExtraControls,
  emptyMessage = "No planned actions for the current empire snapshot and simulation parameters.",
}) => {
  const scrollRef = useRef<HTMLDivElement | null>(null);
  const {
    isDragging,
    consumeSuppressClick,
    onMouseDown,
    onMouseMove,
    onMouseUp,
    onMouseLeave,
  } = useDragScroll(scrollRef, 4);

  const [localEdgeFilter, setLocalEdgeFilter] = useState<"all" | "deps">("all");
  const [selectedNodeId, setSelectedNodeId] = useState<string | null>(null);

  const edgeFilter = controlledEdgeFilter ?? localEdgeFilter;
  const setEdgeFilter = (next: "all" | "deps") => {
    if (onEdgeFilterChange) {
      onEdgeFilterChange(next);
    } else {
      setLocalEdgeFilter(next);
    }
  };

  const mPrefix = markerPrefix || idPrefix;
  const nodes: Array<Record<string, any>> = Array.isArray(graph?.nodes) ? graph!.nodes : [];
  const allEdges: Array<Record<string, any>> = Array.isArray(graph?.edges) ? graph!.edges : [];
  const visibleEdges = allEdges.filter((e) => edgeMatchesFilter(e, edgeFilter));

  const activeSelectedId =
    selectedNodeId && nodes.some((n) => n.id === selectedNodeId) ? selectedNodeId : null;

  // Deterministic coordinate layout
  const LANE_HEADER_W = 168;
  const PAD_X = 20;
  const PAD_Y = 14;
  const CARD_W = 228;
  const CARD_H = 92;
  const COL_GAP = 68;
  const STACK_GAP = 14;
  const LANE_PAD_Y = 16;

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

  const cellStacks = new Map<string, number>();
  const maxStackByLane = new Array<number>(activeLanes.length).fill(1);
  let maxCol = 0;
  const nodePlacement = new Map<string, { laneIdx: number; colIdx: number; stackIdx: number }>();

  nodes.forEach((n, idx) => {
    const lIdx = laneIdxById.get(String(n.lane_id || "lane:global")) ?? 0;
    const colIdx = Math.max(
      0,
      Math.round(safeFiniteNumber(n.topological_layer, idx) ?? idx)
    );
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

  const upstreamIds = new Set<string>();
  const downstreamIds = new Set<string>();
  if (activeSelectedId) {
    allEdges.forEach((e) => {
      if (e.target === activeSelectedId) upstreamIds.add(e.source);
      if (e.source === activeSelectedId) downstreamIds.add(e.target);
    });
  }

  const selectedObj = activeSelectedId
    ? nodes.find((n) => n.id === activeSelectedId) || null
    : null;
  const nodesById = new Map(nodes.map((n) => [String(n.id), n]));
  const incomingSel = selectedObj ? allEdges.filter((e) => e.target === selectedObj.id) : [];
  const outgoingSel = selectedObj ? allEdges.filter((e) => e.source === selectedObj.id) : [];

  return (
    <>
      {showLegendBar ? (
        <div className="ag-legend-bar">
          <div className="ag-legend-items" aria-label="Graph edge legend">
            <span className="ag-legend-item">
              <span className="ag-swatch is-seq" aria-hidden="true" />
              Sequence Flow
            </span>
            <span className="ag-legend-item">
              <span className="ag-swatch is-prereq" aria-hidden="true" />
              Prerequisite Unlock
            </span>
            <span className="ag-legend-item">
              <span className="ag-swatch is-resource" aria-hidden="true" />
              Resource / Power / B2%
            </span>
            <span className="ag-legend-item">
              <span className="ag-swatch is-queue" aria-hidden="true" />
              Queue / Slot Order
            </span>
          </div>
          <div className="inline-actions">
            {legendExtraControls}
            <div className="filter-chips">
              <button
                type="button"
                className={`chip-btn${edgeFilter === "all" ? " active" : ""}`}
                data-ag-edge-filter="all"
                onClick={() => setEdgeFilter("all")}
              >
                All Links
              </button>
              <button
                type="button"
                className={`chip-btn${edgeFilter === "deps" ? " active" : ""}`}
                data-ag-edge-filter="deps"
                onClick={() => setEdgeFilter("deps")}
              >
                Dependencies Only
              </button>
            </div>
          </div>
        </div>
      ) : null}

      <div
        ref={scrollRef}
        className={`ag-canvas-scroll${isDragging ? " is-dragging" : ""}`}
        id={`${idPrefix}-canvas-scroll`}
        onMouseDown={onMouseDown}
        onMouseMove={onMouseMove}
        onMouseUp={onMouseUp}
        onMouseLeave={onMouseLeave}
      >
        <div
          className="ag-canvas-stage"
          id={`${idPrefix}-canvas-stage`}
          hidden={nodes.length === 0}
          style={
            nodes.length > 0
              ? { width: `${totalWidth}px`, height: `${totalHeight}px` }
              : undefined
          }
        >
          <div
            className="ag-lanes-layer"
            id={`${idPrefix}-lanes-layer`}
            aria-hidden="true"
            style={
              nodes.length > 0
                ? { width: `${totalWidth}px`, height: `${totalHeight}px` }
                : undefined
            }
          >
            {activeLanes.map((ln, idx) => {
              const laneNodeCnt = nodes.filter((n) => n.lane_id === ln.id).length;
              const eNet = Math.round(safeFiniteNumber(ln.energy, 0) ?? 0);
              const slotsUsed = safeFiniteNumber(ln.fleet_slots_used ?? ln.slots_used, null);
              const slotsMax = safeFiniteNumber(ln.fleet_slots_max ?? ln.slots_max, null);
              const metaText =
                slotsUsed !== null && slotsMax !== null
                  ? `${laneNodeCnt} ${laneNodeCnt === 1 ? "op" : "ops"} · Slots ${Math.round(slotsUsed)}/${Math.round(slotsMax)}`
                  : ln.scope === "global"
                    ? `${laneNodeCnt} global ${laneNodeCnt === 1 ? "task" : "tasks"}`
                    : `${laneNodeCnt} ${laneNodeCnt === 1 ? "task" : "tasks"} · Energy ${eNet > 0 ? `+${eNet}` : String(eNet)}`;
              return (
                <div
                  key={String(ln.id || idx)}
                  className={idx % 2 === 1 ? "ag-lane-band is-alt" : "ag-lane-band"}
                  data-lane-id={String(ln.id || "")}
                  style={{
                    top: `${laneTops[idx]}px`,
                    width: `${totalWidth}px`,
                    height: `${laneHeights[idx]}px`,
                  }}
                >
                  <div className="ag-lane-header">
                    <span className="ag-lane-title">
                      {sanitizeMainWindowText(ln.label || ln.id || "Lane")}
                    </span>
                    <span className="ag-lane-meta tabular">{metaText}</span>
                  </div>
                </div>
              );
            })}
          </div>

          <div
            className="ag-edges-layer"
            id={`${idPrefix}-edges-layer`}
            aria-hidden="true"
            style={
              nodes.length > 0
                ? { width: `${totalWidth}px`, height: `${totalHeight}px` }
                : undefined
            }
          >
            {nodes.length > 0 ? (
              <svg
                id={`${idPrefix}-edges-svg`}
                viewBox={`0 0 ${totalWidth} ${totalHeight}`}
                width={totalWidth}
                height={totalHeight}
              >
                <defs>
                  {(
                    [
                      [`${mPrefix}-arrow-seq`, "#32e8ff"],
                      [`${mPrefix}-arrow-prereq`, "#16ec98"],
                      [`${mPrefix}-arrow-resource`, "#efb03b"],
                      [`${mPrefix}-arrow-queue`, "#9eb3b8"],
                      [`${mPrefix}-arrow-gate`, "#ff6873"],
                    ] as const
                  ).map(([mId, color]) => (
                    <marker
                      key={mId}
                      id={mId}
                      viewBox="0 0 10 10"
                      refX="9"
                      refY="5"
                      markerWidth="6"
                      markerHeight="6"
                      orient="auto-start-reverse"
                    >
                      <path d="M 0 1.5 L 9 5 L 0 8.5 z" fill={color} />
                    </marker>
                  ))}
                </defs>
                {visibleEdges.map((edge, idx) => {
                  const p1 = coordsById.get(String(edge.source));
                  const p2 = coordsById.get(String(edge.target));
                  if (!p1 || !p2) return null;
                  const x1 = p1.x + CARD_W;
                  const y1 = p1.y + CARD_H / 2;
                  const x2 = p2.x;
                  const y2 = p2.y + CARD_H / 2;
                  const dx = Math.max(36, Math.abs(x2 - x1) * 0.45);
                  const sameRowLongSpan = Math.abs(y2 - y1) < 6 && p2.colIdx - p1.colIdx > 1;
                  const arcLift = sameRowLongSpan ? -30 : 0;
                  const dStr = `M ${x1.toFixed(1)} ${y1.toFixed(1)} C ${(x1 + dx).toFixed(1)} ${(y1 + arcLift).toFixed(1)}, ${(x2 - dx).toFixed(1)} ${(y2 + arcLift).toFixed(1)}, ${x2.toFixed(1)} ${y2.toFixed(1)}`;
                  const spec = edgeVisualSpec(edge, mPrefix);
                  const connected = Boolean(
                    activeSelectedId &&
                      (edge.source === activeSelectedId || edge.target === activeSelectedId)
                  );
                  const dimmed = Boolean(activeSelectedId && !connected);
                  return (
                    <path
                      key={String(edge.id || idx)}
                      className={`ag-edge-path ${spec.cls}${connected ? " is-highlighted" : ""}${dimmed ? " is-dimmed" : ""}`}
                      d={dStr}
                      markerEnd={`url(#${spec.marker})`}
                      data-edge-id={String(edge.id || "")}
                      data-source={String(edge.source || "")}
                      data-target={String(edge.target || "")}
                      data-edge-type={String(edge.primary_type || "")}
                    />
                  );
                })}
              </svg>
            ) : null}
          </div>

          <div
            className="ag-nodes-layer"
            id={`${idPrefix}-nodes-layer`}
            role="list"
            aria-label="Planned action graph nodes"
            style={
              nodes.length > 0
                ? { width: `${totalWidth}px`, height: `${totalHeight}px` }
                : undefined
            }
          >
            {nodes.map((node, idx) => {
              const nid = String(node.id);
              const pos = coordsById.get(nid) || { x: 0, y: 0 };
              const status = String(node.status || "queued");
              const statusCls = nodeStatusCssClass(status);
              const prio = String(node.priority || (node.week != null ? `W${node.week}` : "P2")).toUpperCase();
              const prioCls =
                prio === "P0"
                  ? "is-p0"
                  : prio === "P1"
                    ? "is-p1"
                    : prio === "P2" || prio.startsWith("W")
                      ? "is-p2"
                      : "is-p3";
              const isSel = Boolean(activeSelectedId && nid === activeSelectedId);
              const isUp = Boolean(activeSelectedId && upstreamIds.has(nid));
              const isDown = Boolean(activeSelectedId && downstreamIds.has(nid));
              const isDim = Boolean(activeSelectedId && !isSel && !isUp && !isDown);
              const ord = Math.max(
                1,
                Math.round(safeFiniteNumber(node.seq ?? (node.order_index != null ? Number(node.order_index) + 1 : idx + 1), idx + 1) ?? idx + 1)
              );
              const weekNum = safeFiniteNumber(node.week, null);
              const stepTag = weekNum !== null && node.seq != null ? `#${ord} · W${Math.round(weekNum)}` : `#${ord}`;

              return (
                <button
                  key={nid}
                  type="button"
                  className={`ag-node-card ${statusCls}${isSel ? " is-selected" : ""}${!isSel && isUp ? " is-upstream" : ""}${!isSel && isDown ? " is-downstream" : ""}${isDim ? " is-dimmed" : ""}`}
                  role="listitem"
                  data-node-id={nid}
                  data-node-kind={String(node.kind || "")}
                  data-node-priority={prio}
                  data-node-status={status}
                  aria-pressed={isSel ? "true" : "false"}
                  style={{ left: `${pos.x}px`, top: `${pos.y}px` }}
                  title={sanitizeMainWindowText(String(node.reason || "").replace(/>/g, "to"))}
                  onClick={() => {
                    if (consumeSuppressClick()) return;
                    setSelectedNodeId((prev) => (prev === nid ? null : nid));
                  }}
                >
                  <div className="ag-node-head">
                    <div className="ag-node-badges">
                      <span className="ag-step-tag tabular">{stepTag}</span>
                      <span className={`ag-prio-tag tabular ${prioCls}`}>{prio}</span>
                    </div>
                    <span className={`ag-state-tag tabular ${statusCls}`}>
                      {formatNodeStatusShort(status)}
                    </span>
                  </div>
                  <div className="ag-node-title">
                    {sanitizeMainWindowText(formatDagNodeTargetLabel(node))}
                  </div>
                  <div className="ag-node-foot">
                    <span className="ag-node-coords">
                      {node.planet_coords ? `[${node.planet_coords}]` : "[GLOBAL]"}
                    </span>
                    <span className="ag-node-eta">
                      {sanitizeMainWindowText(`ETA ${node.total_eta || node.duration_text || "Ready"}`)}
                    </span>
                  </div>
                </button>
              );
            })}
          </div>
        </div>

        <div className="ag-empty-state" id={`${idPrefix}-empty-state`} hidden={nodes.length > 0}>
          {sanitizeMainWindowText(emptyMessage)}
        </div>
      </div>

      <div
        className="ag-inspector-bar"
        id={`${idPrefix}-node-inspector`}
        aria-label="Selected action node details"
      >
        <div className="ag-insp-main">
          <span className="module-code tabular" id={`${idPrefix}-insp-step`}>
            {selectedObj
              ? `#${Math.max(1, Math.round(safeFiniteNumber(selectedObj.seq ?? (selectedObj.order_index != null ? Number(selectedObj.order_index) + 1 : 1), 1) ?? 1))}`
              : "#—"}
          </span>
          <div className="ag-insp-copy">
            <strong id={`${idPrefix}-insp-title`}>
              {selectedObj
                ? sanitizeMainWindowText(
                    `${selectedObj.priority || (selectedObj.week != null ? `W${selectedObj.week}` : "P2")} · ${formatDagNodeTargetLabel(selectedObj)} ${selectedObj.planet_coords ? `[${selectedObj.planet_coords}]` : "[GLOBAL]"}`
                  )
                : "Select a node in the graph to inspect dependencies and ETAs"}
            </strong>
            <p className="ag-insp-sub tabular" id={`${idPrefix}-insp-meta`}>
              {selectedObj
                ? `Wait: ${String(selectedObj.wait_eta || "Ready")} · Duration: ${String(selectedObj.duration_text || "Instant")} · Total: ${String(selectedObj.total_eta || selectedObj.duration_text || "Ready")} — ${sanitizeMainWindowText(selectedObj.reason || "Planned empire operation.")}`
                : "Click any node card above to highlight upstream prerequisites and downstream dependents."}
            </p>
          </div>
        </div>
        <div className="ag-insp-deps tabular" id={`${idPrefix}-insp-deps`}>
          {selectedObj ? (
            incomingSel.length === 0 && outgoingSel.length === 0 ? (
              <span className="ag-dep-pill">Independent root action (0 links)</span>
            ) : (
              <>
                {incomingSel.slice(0, 4).map((edge, idx) => {
                  const src = nodesById.get(String(edge.source));
                  const srcNum = src
                    ? Math.round(
                        safeFiniteNumber(
                          src.seq ?? (src.order_index != null ? Number(src.order_index) + 1 : 1),
                          1
                        ) ?? 1
                      )
                    : "?";
                  return (
                    <span
                      key={`in-${idx}`}
                      className="ag-dep-pill is-up"
                      title={sanitizeMainWindowText(String(edge.label || "").replace(/>/g, "to"))}
                    >
                      {sanitizeMainWindowText(
                        `← #${srcNum} ${src ? src.target_name || src.entity_name || src.kind : edge.source} (${edge.primary_type})`
                      )}
                    </span>
                  );
                })}
                {outgoingSel.slice(0, 4).map((edge, idx) => {
                  const dst = nodesById.get(String(edge.target));
                  const dstNum = dst
                    ? Math.round(
                        safeFiniteNumber(
                          dst.seq ?? (dst.order_index != null ? Number(dst.order_index) + 1 : 1),
                          1
                        ) ?? 1
                      )
                    : "?";
                  return (
                    <span
                      key={`out-${idx}`}
                      className="ag-dep-pill is-down"
                      title={sanitizeMainWindowText(String(edge.label || "").replace(/>/g, "to"))}
                    >
                      {sanitizeMainWindowText(
                        `→ #${dstNum} ${dst ? dst.target_name || dst.entity_name || dst.kind : edge.target} (${edge.primary_type})`
                      )}
                    </span>
                  );
                })}
              </>
            )
          ) : null}
        </div>
      </div>
    </>
  );
};

export default DagCanvas;
