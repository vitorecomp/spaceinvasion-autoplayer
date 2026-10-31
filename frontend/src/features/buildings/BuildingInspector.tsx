import React, { useMemo, useState } from "react";
import { feedbackKindForError } from "../../api/httpClient";
import { SortableTh } from "../../components/SortableTh";
import { useConsoleActions } from "../../store";
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
  mode?: "buildings" | "research";
  universeMaxRow?: Record<string, any> | null;
  goalLevel?: number | null;
  goalSpecialization?: string | null;
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
  mode = "buildings",
  universeMaxRow = null,
  goalLevel = null,
  goalSpecialization = null,
}) => {
  const {
    apiCommand,
    loadDashboard,
    showFeedback,
    enqueueManualCommand,
    toggleBlastFurnaceRate,
  } = useConsoleActions();

  const isResearchMode = mode === "research";
  const ids = useMemo(
    () =>
      isResearchMode
        ? {
            aside: "res-inspector",
            key: "res-insp-key",
            title: "res-insp-title",
            subtitle: "res-insp-subtitle",
            metaBox: "res-insp-meta-box",
            bfHint: "res-insp-bf-hint",
            targetSelector: "res-insp-target-selector",
            horizonsTbody: "res-insp-horizons-tbody",
            buildBtn: "btn-res-insp-research",
            pinBtn: "btn-res-insp-pin-queue",
            demolishBtn: "btn-res-insp-demolish",
          }
        : {
            aside: "bld-inspector",
            key: "insp-key",
            title: "insp-title",
            subtitle: "insp-subtitle",
            metaBox: "insp-meta-box",
            bfHint: "insp-bf-hint",
            targetSelector: "insp-target-selector",
            horizonsTbody: "insp-horizons-tbody",
            buildBtn: "btn-insp-build",
            pinBtn: "btn-insp-pin-queue",
            demolishBtn: "btn-insp-demolish",
          },
    [isResearchMode]
  );

  const horizons: Array<Record<string, any>> = useMemo(
    () => (Array.isArray(selected?.horizons) ? selected!.horizons : []),
    [selected]
  );
  const horizonSort = useTableSort(horizons, HORIZON_SORT_COLUMNS);

  const selectedKeySig = `${selected?.key ?? ""}::${selected?.next_level ?? selected?.target_level ?? ""}`;
  const [selectedTarget, setSelectedTarget] = useState<{ sig: string; level: number } | null>(null);
  const selectedTargetLvl =
    selectedTarget && selectedTarget.sig === selectedKeySig ? selectedTarget.level : null;
  const setSelectedTargetLvl = (lvl: number | null) => {
    setSelectedTarget(lvl === null ? null : { sig: selectedKeySig, level: lvl });
  };

  if (!selected) {
    return (
      <aside
        className="inspector-pane"
        id={ids.aside}
        aria-label={isResearchMode ? "Selected research level forecast" : "Selected level forecast"}
      >
        <div className="inspector-head">
          <span className="module-code tabular" id={ids.key}>
            {etaData ? "—" : isResearchMode ? "R1" : "B1"}
          </span>
          <div>
            <h3 id={ids.title}>
              {isResearchMode ? "Select a Research Technology" : "Select a Building"}
            </h3>
            <p className="action-sub tabular" id={ids.subtitle}>
              {etaData
                ? "Click any row on the left."
                : isResearchMode
                  ? "Click any research row to inspect L+1 to L+3 arrival estimates"
                  : "Click any row to inspect L+1 to L+4 arrival estimates"}
            </p>
          </div>
        </div>

        <div className="inspector-stats tabular" id={ids.metaBox}>
          <span>{etaData ? "No data." : "No item selected."}</span>
        </div>

        <div className="inspector-bf-hint" id={ids.bfHint} hidden />

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
                  label={isResearchMode ? "Research" : "Build"}
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
            <tbody id={ids.horizonsTbody}>
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
          <button type="button" className="btn btn-primary btn-sm" id={ids.buildBtn} hidden>
            {isResearchMode ? "Start Next Research Live" : "Queue Next Level Live"}
          </button>
          <button type="button" className="btn btn-secondary btn-sm" id={ids.pinBtn} hidden>
            + Pin to Queue (P1)
          </button>
          {!isResearchMode ? (
            <button
              type="button"
              className="btn btn-secondary btn-sm btn-danger-ghost"
              id={ids.demolishBtn}
              hidden
            >
              Demolish
            </button>
          ) : null}
        </div>
      </aside>
    );
  }

  const keyStr = String(selected.key || "");
  const isResearch = isResearchMode || keyStr.startsWith("r");
  const isTurret = Boolean(selected.is_defense_turret);
  const curLvl = finiteNum(selected.current_level, 0);
  const nextLvl = finiteNum(selected.next_level ?? selected.target_level, null);
  const unmet: string[] = Array.isArray(selected.unmet_prerequisites)
    ? selected.unmet_prerequisites
    : [];
  const pEcon = etaData?.planet_economy || {};
  const planetId = etaData?.planet?.planet_id;

  const activeHorizon =
    selectedTargetLvl !== null
      ? horizons.find((h) => finiteNum(h.level, null) === selectedTargetLvl) || null
      : null;
  const effectiveTargetLvl =
    activeHorizon !== null ? finiteNum(activeHorizon.level, nextLvl) : nextLvl;
  const isCustomHorizon =
    activeHorizon !== null && effectiveTargetLvl !== null && effectiveTargetLvl !== nextLvl;

  const selBnRaw = isCustomHorizon ? activeHorizon?.bottleneck : selected.bottleneck;
  const selBnTag =
    typeof selBnRaw === "string" && selBnRaw.trim() ? bottleneckTag(selBnRaw) : "";

  const displayCost = isCustomHorizon ? activeHorizon?.cost : selected.cost;
  const displayWaitEta = isCustomHorizon
    ? (activeHorizon?.wait_eta ?? "—")
    : (selected.wait_eta ?? "—");
  const displayReachEta = isCustomHorizon
    ? (activeHorizon?.reach_eta ?? "—")
    : (selected.reach_eta ?? "—");

  const metaLines: string[] = isTurret
    ? [
        `Stationed Units: ${fmtInt(curLvl)}`,
        `Unit Cost: ${formatResourcesInline(selected.cost)}`,
        `Combat Stats: ATK ${fmtInt(selected.attack ?? 0)} · SHD ${fmtInt(selected.shield ?? 0)} · Hull ${fmtInt(selected.hull ?? 0)}`,
      ]
    : [
        `Prerequisites: ${unmet.length === 0 ? "Unlocked" : unmet.join(", ")}`,
        isCustomHorizon
          ? `Target L${effectiveTargetLvl ?? "—"} Cost: ${formatResourcesInline(displayCost)}`
          : `Next L${selected.next_level ?? "—"} Cost: ${formatResourcesInline(displayCost)}`,
        ...(isCustomHorizon
          ? [
              `${isResearch ? "Research" : "Build"} Time (L${effectiveTargetLvl}): ${activeHorizon?.build_time_text ?? "—"}`,
            ]
          : []),
        `Resources Ready: ${displayWaitEta}${selBnTag ? ` (${selBnTag} bottleneck)` : ""}`,
        `Estimated Level Reached: ${displayReachEta}`,
      ];

  if (!isResearch && !isTurret && selected.energy_delta) {
    metaLines.push(
      `Energy Impact: ${selected.energy_delta >= 0 ? "+" : ""}${selected.energy_delta} E`
    );
  }

  if (!isResearch && !isTurret && goalLevel !== null && goalLevel !== undefined) {
    const goalGap = goalLevel - curLvl;
    const goalStatusTxt =
      goalLevel === 0 && curLvl === 0
        ? " (Not targeted)"
        : goalGap <= 0
          ? " (Reached)"
          : ` (+${goalGap} remaining)`;
    metaLines.push(
      `Config Optimizer Goal: L${goalLevel}${goalStatusTxt}${goalSpecialization ? ` · ${goalSpecialization}` : ""}`
    );
  }

  if (isResearch && universeMaxRow) {
    const uniMax = Math.max(
      curLvl,
      Math.round(finiteNum(universeMaxRow.universe_max_level, curLvl))
    );
    const gap = Math.max(0, finiteNum(universeMaxRow.gap_to_max, uniMax - curLvl));
    const isLeader = uniMax > 0 && curLvl >= uniMax;
    const holderTxt = universeMaxRow.holder
      ? `${universeMaxRow.holder}${universeMaxRow.holder_coords ? ` [${universeMaxRow.holder_coords}]` : ""}`
      : isLeader
        ? "You (Leader)"
        : "";
    metaLines.push(
      `Universe Max: L${uniMax}${gap > 0 ? ` (-${gap} Lvl)` : isLeader ? " (Leader)" : ""}${holderTxt ? ` · ${holderTxt}` : ""}`
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
  const pinTargetLvl = effectiveTargetLvl;
  const canPinQueue = !isTurret && unitId != null && pinTargetLvl !== null && pinTargetLvl > 0;
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
      showFeedback(
        `${isResearch ? "Research" : "Build"} failed: ${err.message}`,
        feedbackKindForError(err)
      );
    }
  };

  const handlePinToQueue = () => {
    if (!canPinQueue || pinTargetLvl === null) return;
    void enqueueManualCommand({
      kind: isResearch ? "RESEARCH" : "BUILD",
      priority: "P1",
      position: "top",
      planet_id: planetId || 0,
      target_id: unitId,
      target_name: selected.name || "",
      target_level: pinTargetLvl,
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
    <aside
      className="inspector-pane"
      id={ids.aside}
      aria-label={isResearchMode ? "Selected research level forecast" : "Selected level forecast"}
    >
      <div className="inspector-head">
        <span className="module-code tabular" id={ids.key}>
          {keyStr ? keyStr.toUpperCase() : "UNIT"}
        </span>
        <div>
          <h3 id={ids.title}>{selected.name || "Unit"}</h3>
          <p className="action-sub tabular" id={ids.subtitle}>
            {isTurret
              ? `Owned ${fmtInt(curLvl)} · Planetary Defense`
              : `Current L${curLvl} · Next L${selected.next_level ?? "—"} · ${isResearch ? "Research" : "Build"} time ${selected.build_time_text ?? "—"}`}
          </p>
        </div>
      </div>

      <div className="inspector-stats tabular" id={ids.metaBox}>
        {metaLines.map((line, i) => (
          <span key={i}>{line}</span>
        ))}
      </div>

      {!isTurret && horizons.length > 1 ? (
        <div
          className="inline-actions"
          id={ids.targetSelector}
          role="group"
          aria-label="Target level selector"
        >
          <span className="action-sub tabular">Target Level:</span>
          {horizons.map((h, idx) => {
            const hLvl = finiteNum(h.level, null);
            if (hLvl === null) return null;
            const isActiveTarget = effectiveTargetLvl === hLvl;
            return (
              <button
                key={`tgt-${hLvl}-${idx}`}
                type="button"
                className={`chip-btn tabular${isActiveTarget ? " active" : ""}`}
                data-target-level={String(hLvl)}
                onClick={() => setSelectedTargetLvl(hLvl)}
              >
                {`L${hLvl}`}
              </button>
            );
          })}
        </div>
      ) : null}

      {!isResearch && selected.building_id === 2 ? (
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

      <div className="inspector-bf-hint" id={ids.bfHint} hidden={!showBfHint}>
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
                label={isResearch ? "Research" : "Build"}
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
          <tbody id={ids.horizonsTbody}>
            {horizonSort.sortedRows.map((h, idx) => {
              const hLvl = finiteNum(h.level, null);
              const isSelectedHz = hLvl !== null && effectiveTargetLvl === hLvl;
              return (
                <tr
                  key={`hz-${h.level ?? idx}`}
                  className={`clickable-row${isSelectedHz ? " row-selected" : ""}`}
                  onClick={() => {
                    if (hLvl !== null) {
                      setSelectedTargetLvl(hLvl);
                    }
                  }}
                >
                  <td className="tabular">{`L${h.level}`}</td>
                  <td className="tabular">{formatResourcesInline(h.cost)}</td>
                  <td className="col-right tabular">{h.build_time_text}</td>
                  <td className="col-right tabular">
                    <span className={`eta-pill${h.reach_eta === "—" ? " is-blocked" : ""}`}>
                      {h.reach_eta}
                    </span>
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>

      <div className="inspector-actions">
        <button
          type="button"
          className="btn btn-primary btn-sm"
          id={ids.buildBtn}
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
          id={ids.pinBtn}
          hidden={!canPinQueue}
          onClick={handlePinToQueue}
        >
          {pinTargetLvl !== null ? `+ Pin L${pinTargetLvl} to Queue (P1)` : "+ Pin to Queue (P1)"}
        </button>

        {!isResearch ? (
          <button
            type="button"
            className="btn btn-secondary btn-sm btn-danger-ghost"
            id={ids.demolishBtn}
            hidden={!canDemolish}
            onClick={handleDemolish}
          >
            {`Demolish L${curLvl} → L${Math.max(0, curLvl - 1)}`}
          </button>
        ) : null}
      </div>
    </aside>
  );
};

export default BuildingInspector;
