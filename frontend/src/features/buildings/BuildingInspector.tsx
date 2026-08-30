import React, { useMemo } from "react";
import { feedbackKindForError } from "../../api/httpClient";
import { SortableTh } from "../../components/SortableTh";
import { useConsole } from "../../context/ConsoleContext";
import {
  durationToSeconds,
  resourcesTotal,
  useTableSort,
  type SortColumnMap,
} from "../../hooks/useTableSort";
import {
  bottleneckTag,
  finiteNum,
  fmtInt,
  formatResourcesInline,
} from "../../utils/formatters";

export interface BuildingInspectorProps {
  etaData: Record<string, any> | null;
  selectedRow: Record<string, any> | null;
  bfPct: number;
}

const HORIZON_SORT_COLUMNS: SortColumnMap<
  Record<string, any>,
  "target" | "cost" | "build" | "reached"
> = {
  target: (h) => finiteNum(h.level, null),
  cost: (h) => resourcesTotal(h.cost),
  build: (h) => finiteNum(h.build_seconds, null) ?? durationToSeconds(h.build_time_text),
  reached: (h) => {
    if (h.reach_eta === "—") return null;
    const rh = finiteNum(h.reach_hours ?? h.total_hours, null);
    if (rh !== null) return rh * 3600;
    return durationToSeconds(h.reach_eta);
  },
};

export const BuildingInspector: React.FC<BuildingInspectorProps> = ({
  etaData,
  selectedRow: selected,
  bfPct,
}) => {
  const {
    apiCommand,
    loadDashboard,
    showFeedback,
    enqueueManualCommand,
    toggleBlastFurnaceRate,
  } = useConsole();

  const horizons: Array<Record<string, any>> = useMemo(
    () => (Array.isArray(selected?.horizons) ? selected!.horizons : []),
    [selected]
  );
  const horizonSort = useTableSort(horizons, HORIZON_SORT_COLUMNS);

  if (!selected) {
    return (
      <aside className="inspector-pane" id="bld-inspector" aria-label="Selected level forecast">
        <div className="inspector-head">
          <span className="module-code tabular" id="insp-key">
            {etaData ? "—" : "B1"}
          </span>
          <div>
            <h3 id="insp-title">
              {etaData ? "Select a Building or Tech" : "Select a Building or Research"}
            </h3>
            <p className="action-sub tabular" id="insp-subtitle">
              {etaData
                ? "Click any row on the left."
                : "Click any row to inspect L+1 to L+4 arrival estimates"}
            </p>
          </div>
        </div>

        <div className="inspector-stats tabular" id="insp-meta-box">
          <span>{etaData ? "No data." : "No item selected."}</span>
        </div>

        <div className="inspector-bf-hint" id="insp-bf-hint" hidden />

        <div className="table-wrap">
          <table>
            <thead>
              <tr>
                <SortableTh
                  label="Target"
                  sortKey="target"
                  activeKey={horizonSort.sortKey}
                  direction={horizonSort.sortDirection}
                  onSort={horizonSort.requestSort}
                />
                <SortableTh
                  label="Cost"
                  sortKey="cost"
                  activeKey={horizonSort.sortKey}
                  direction={horizonSort.sortDirection}
                  onSort={horizonSort.requestSort}
                />
                <SortableTh
                  label="Build"
                  sortKey="build"
                  activeKey={horizonSort.sortKey}
                  direction={horizonSort.sortDirection}
                  onSort={horizonSort.requestSort}
                  align="right"
                />
                <SortableTh
                  label="Reached"
                  sortKey="reached"
                  activeKey={horizonSort.sortKey}
                  direction={horizonSort.sortDirection}
                  onSort={horizonSort.requestSort}
                  align="right"
                />
              </tr>
            </thead>
            <tbody id="insp-horizons-tbody">
              {!etaData ? (
                <tr>
                  <td colSpan={4} className="empty-cell">
                    Select a row for multi-level schedule.
                  </td>
                </tr>
              ) : null}
            </tbody>
          </table>
        </div>

        <div className="inspector-actions">
          <button type="button" className="btn btn-primary btn-sm" id="btn-insp-build" hidden>
            Queue Next Level Live
          </button>
          <button
            type="button"
            className="btn btn-secondary btn-sm"
            id="btn-insp-pin-queue"
            hidden
          >
            + Pin to Queue (P1)
          </button>
          <button
            type="button"
            className="btn btn-secondary btn-sm btn-danger-ghost"
            id="btn-insp-demolish"
            hidden
          >
            Demolish
          </button>
        </div>
      </aside>
    );
  }

  const keyStr = String(selected.key || "");
  const isResearch = keyStr.startsWith("r");
  const isTurret = Boolean(selected.is_defense_turret);
  const curLvl = finiteNum(selected.current_level, 0);
  const nextLvl = finiteNum(selected.next_level ?? selected.target_level, null);
  const unmet: string[] = Array.isArray(selected.unmet_prerequisites)
    ? selected.unmet_prerequisites
    : [];
  const pEcon = etaData?.planet_economy || {};
  const planetId = etaData?.planet?.planet_id;

  const selBnTag =
    typeof selected.bottleneck === "string" && selected.bottleneck.trim()
      ? bottleneckTag(selected.bottleneck)
      : "";

  const metaLines: string[] = isTurret
    ? [
        `Stationed Units: ${fmtInt(curLvl)}`,
        `Unit Cost: ${formatResourcesInline(selected.cost)}`,
        `Combat Stats: ATK ${fmtInt(selected.attack ?? 0)} · SHD ${fmtInt(selected.shield ?? 0)} · Hull ${fmtInt(selected.hull ?? 0)}`,
      ]
    : [
        `Prerequisites: ${unmet.length === 0 ? "Unlocked" : unmet.join(", ")}`,
        `Next L${selected.next_level ?? "—"} Cost: ${formatResourcesInline(selected.cost)}`,
        `Resources Ready: ${selected.wait_eta ?? "—"}${selBnTag ? ` (${selBnTag} bottleneck)` : ""}`,
        `Estimated Level Reached: ${selected.reach_eta ?? "—"}`,
      ];

  if (!isResearch && !isTurret && selected.energy_delta) {
    metaLines.push(
      `Energy Impact: ${selected.energy_delta >= 0 ? "+" : ""}${selected.energy_delta} E`
    );
  }

  // Blast furnace 0% counter-factual impact note when Pig iron slows down upgrade
  const bfUsed = finiteNum(pEcon.blast_furnace_pig_used_per_hr, 0);
  const econBfPct = finiteNum(pEcon.blast_furnace_pct, 100);
  const missPig = finiteNum(selected.missing?.pig_iron, 0);
  const showBfHint =
    selected.bottleneck === "pig_iron" && econBfPct > 0 && bfUsed > 0 && missPig > 0;
  let bfHintText = "";
  if (showBfHint) {
    const newRate = finiteNum(pEcon.pig_iron_per_hr, 0) + bfUsed;
    const oldWait = selected.wait_eta ?? "—";
    const altHours = newRate > 0 ? missPig / newRate : NaN;
    const altWait = Number.isFinite(altHours) ? `${altHours.toFixed(1)}h` : "Blocked";
    bfHintText = `Bucket tip: Pausing Blast furnace (b2 → 0%) frees +${fmtInt(Math.round(bfUsed))} Pig/h (Pig iron wait improves from ${oldWait} to ${altWait}).`;
  }

  const canQueueLive = isTurret
    ? false
    : Boolean(isResearch ? selected.can_research_live : selected.can_build_live);
  const unitId = isResearch ? selected.research_id : selected.building_id;
  const canPinQueue = !isTurret && unitId != null && nextLvl !== null && nextLvl > 0;
  const canDemolish = !isResearch && !isTurret && unitId != null && curLvl > 0;

  const handleBuildLive = async () => {
    try {
      showFeedback(`Submitting ${selected.name} L${selected.next_level}...`, "ok");
      const cmd = isResearch
        ? {
            command: "research",
            research_id: selected.research_id,
            planet_id: planetId,
            dry_run: false,
          }
        : {
            command: "build",
            building_id: selected.building_id,
            planet_id: planetId,
            dry_run: false,
          };
      const res = await apiCommand(cmd);
      showFeedback(res.message || "Queued.", "ok");
      await loadDashboard({ refresh: false, force: true });
    } catch (err: any) {
      showFeedback(`Build failed: ${err.message}`, feedbackKindForError(err));
    }
  };

  const handlePinToQueue = () => {
    if (!canPinQueue || nextLvl === null) return;
    void enqueueManualCommand({
      kind: isResearch ? "RESEARCH" : "BUILD",
      priority: "P1",
      position: "top",
      planet_id: planetId || 0,
      target_id: unitId,
      target_name: selected.name || "",
      target_level: nextLvl,
      deduplicate_pending: true,
    });
  };

  const handleDemolish = async () => {
    if (!canDemolish) return;
    try {
      showFeedback(`Demolishing ${selected.name} L${curLvl} → L${curLvl - 1}...`, "ok");
      const res = await apiCommand({
        command: "demolish_building",
        building_id: selected.building_id,
        planet_id: planetId,
        dry_run: false,
      });
      showFeedback(res.message || `Demolish submitted for ${selected.name}.`, "ok");
      await loadDashboard({ refresh: false, force: true });
    } catch (err: any) {
      showFeedback(`Demolish failed: ${err.message}`, feedbackKindForError(err));
    }
  };

  return (
    <aside className="inspector-pane" id="bld-inspector" aria-label="Selected level forecast">
      <div className="inspector-head">
        <span className="module-code tabular" id="insp-key">
          {keyStr ? keyStr.toUpperCase() : "UNIT"}
        </span>
        <div>
          <h3 id="insp-title">{selected.name || "Unit"}</h3>
          <p className="action-sub tabular" id="insp-subtitle">
            {isTurret
              ? `Owned ${fmtInt(curLvl)} · Planetary Defense`
              : `Current L${curLvl} · Next L${selected.next_level ?? "—"} · Build time ${selected.build_time_text ?? "—"}`}
          </p>
        </div>
      </div>

      <div className="inspector-stats tabular" id="insp-meta-box">
        {metaLines.map((line, i) => (
          <span key={i}>{line}</span>
        ))}
      </div>

      {selected.building_id === 2 ? (
        <div className="inline-actions" aria-label="Blast furnace throttle presets">
          <span className="action-sub tabular">{`B2 Throttle (${bfPct}%):`}</span>
          <button
            type="button"
            className="btn btn-warning btn-sm"
            disabled={bfPct === 0}
            onClick={() => toggleBlastFurnaceRate(0)}
          >
            0%
          </button>
          <button
            type="button"
            className="btn btn-secondary btn-sm"
            disabled={bfPct === 50}
            onClick={() => toggleBlastFurnaceRate(50)}
          >
            50%
          </button>
          <button
            type="button"
            className="btn btn-secondary btn-sm"
            disabled={bfPct === 100}
            onClick={() => toggleBlastFurnaceRate(100)}
          >
            100%
          </button>
        </div>
      ) : null}

      <div className="inspector-bf-hint" id="insp-bf-hint" hidden={!showBfHint}>
        {bfHintText}
      </div>

      <div className="table-wrap">
        <table>
          <thead>
            <tr>
              <SortableTh
                label="Target"
                sortKey="target"
                activeKey={horizonSort.sortKey}
                direction={horizonSort.sortDirection}
                onSort={horizonSort.requestSort}
              />
              <SortableTh
                label="Cost"
                sortKey="cost"
                activeKey={horizonSort.sortKey}
                direction={horizonSort.sortDirection}
                onSort={horizonSort.requestSort}
              />
              <SortableTh
                label="Build"
                sortKey="build"
                activeKey={horizonSort.sortKey}
                direction={horizonSort.sortDirection}
                onSort={horizonSort.requestSort}
                align="right"
              />
              <SortableTh
                label="Reached"
                sortKey="reached"
                activeKey={horizonSort.sortKey}
                direction={horizonSort.sortDirection}
                onSort={horizonSort.requestSort}
                align="right"
              />
            </tr>
          </thead>
          <tbody id="insp-horizons-tbody">
            {horizonSort.sortedRows.map((h, idx) => (
              <tr key={`hz-${h.level ?? idx}`}>
                <td className="tabular">{`L${h.level}`}</td>
                <td className="tabular">{formatResourcesInline(h.cost)}</td>
                <td className="col-right tabular">{h.build_time_text}</td>
                <td className="col-right tabular">
                  <span className={`eta-pill${h.reach_eta === "—" ? " is-blocked" : ""}`}>
                    {h.reach_eta}
                  </span>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      <div className="inspector-actions">
        <button
          type="button"
          className="btn btn-primary btn-sm"
          id="btn-insp-build"
          hidden={!canQueueLive}
          onClick={handleBuildLive}
        >
          {isResearch
            ? `Start Research L${selected.next_level}`
            : `Queue L${selected.next_level} Live`}
        </button>

        <button
          type="button"
          className="btn btn-secondary btn-sm"
          id="btn-insp-pin-queue"
          hidden={!canPinQueue}
          onClick={handlePinToQueue}
        >
          {nextLvl !== null ? `+ Pin L${nextLvl} to Queue (P1)` : "+ Pin to Queue (P1)"}
        </button>

        <button
          type="button"
          className="btn btn-secondary btn-sm btn-danger-ghost"
          id="btn-insp-demolish"
          hidden={!canDemolish}
          onClick={handleDemolish}
        >
          {`Demolish L${curLvl} → L${Math.max(0, curLvl - 1)}`}
        </button>
      </div>
    </aside>
  );
};

export default BuildingInspector;
