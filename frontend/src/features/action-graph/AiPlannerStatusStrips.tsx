import React from "react";
import { safeFiniteNumber } from "../../api/httpClient";
import type {
  AiPlannerAdaptiveStatus,
  AiPlannerColonyDiag,
  FieldConflictItem,
} from "../../types/api";
import { fmtCompact, fmtInt } from "../../utils/formatters";
import { formatAdaptiveColonyState, formatSharePct } from "./aiPlannerHelpers";
import { ColonyGateInfo, formatDurationSecondsCompact } from "./plannerHelpers";

export interface AiPlannerStatusStripsProps {
  gate: ColonyGateInfo;
  isLiveAiActive: boolean;
  conflicts: FieldConflictItem[];
  selectedCandidateByConflict: Record<string, string>;
  onSelectConflictCandidate: (conflictKey: string, buildingKey: string) => void;
  resolvingConflictKey: string | null;
  onResolveConflict: (conflict: FieldConflictItem, decision: "demolish" | "keep_reserve") => void;
  summary: Record<string, any>;
  rewardWeeks: number;
  weeklyDiscount: string;
  discReward: number | null;
  totalNetOutput: number | null;
  stepsCompleted: number;
  stepsTotal: number;
  inProgTotal: number;
  dispatchDelayMin: number;
  startSlots: number;
  endSlots: number;
  lootPerHr: number | null;
  medianRtSec: number | null;
  initLiveMode: string;
  liveModeLabel: string;
  hasSmeltingTarget: boolean;
  initTargetEnabled: boolean;
  initToggleNeeded: boolean;
  metalSmelted: number | null;
  smeltingTimeText: string;
  smeltingTogglesCount: number;
  adaptiveEnabled: boolean;
  isTogglingAdaptive: boolean;
  onToggleAdaptiveExecution: (nextVal: boolean) => void;
  adaptiveActiveBuilders: number;
  adaptiveTotalColonies: number;
  adaptiveStatus: AiPlannerAdaptiveStatus | null;
  adaptiveColonies: AiPlannerColonyDiag[];
  customPlanetNames: Record<string, string>;
  planetNameById: Map<number, string>;
}

export const AiPlannerStatusStrips: React.FC<AiPlannerStatusStripsProps> = ({
  gate,
  isLiveAiActive,
  conflicts,
  selectedCandidateByConflict,
  onSelectConflictCandidate,
  resolvingConflictKey,
  onResolveConflict,
  summary,
  rewardWeeks,
  weeklyDiscount,
  discReward,
  totalNetOutput,
  stepsCompleted,
  stepsTotal,
  inProgTotal,
  dispatchDelayMin,
  startSlots,
  endSlots,
  lootPerHr,
  medianRtSec,
  initLiveMode,
  liveModeLabel,
  hasSmeltingTarget,
  initTargetEnabled,
  initToggleNeeded,
  metalSmelted,
  smeltingTimeText,
  smeltingTogglesCount,
  adaptiveEnabled,
  isTogglingAdaptive,
  onToggleAdaptiveExecution,
  adaptiveActiveBuilders,
  adaptiveTotalColonies,
  adaptiveStatus,
  adaptiveColonies,
  customPlanetNames,
  planetNameById,
}) => {
  const transportFleet = adaptiveStatus?.transport_fleet || null;
  const window24h = adaptiveStatus?.window_24h || null;
  const hasWindow24hSamples = Boolean(
    window24h && (safeFiniteNumber(window24h.colony_samples, 0) ?? 0) > 0
  );
  const autoReplan = adaptiveStatus?.auto_replan || null;

  return (
    <>
      <div
        className={`ag-gate-banner ${gate.liveAiUnlocked ? "is-unlocked" : "is-locked"}`}
        id="ag-ai-colony-gate"
        role="status"
      >
        <span className="module-code tabular">{`${gate.coloniesOwned}/${gate.maxPlanets}`}</span>
        <span className="ag-gate-copy">
          {gate.liveAiUnlocked
            ? `${gate.coloniesOwned} / ${gate.maxPlanets} colonies founded — Live Bot Execution Unlocked${isLiveAiActive ? " · Currently Active" : " · Click Activate Live AI-Planner to switch"}`
            : `${gate.coloniesOwned} / ${gate.maxPlanets} colonies founded — Preview Simulation Active · Live Bot Execution Unlocks at ${gate.maxPlanets}/${gate.maxPlanets}`}
        </span>
      </div>

      {conflicts.length > 0 ? (
        <div
          className="ag-conflict-stack"
          id="ag-ai-field-conflicts"
          role="region"
          aria-label="Pending planet field conflicts"
        >
          {conflicts.map((c, idx) => {
            const candList =
              Array.isArray(c.candidates) && c.candidates.length > 0
                ? c.candidates
                : c.candidate_building_key
                  ? [{ building_key: c.candidate_building_key, level: c.candidate_level ?? 0 }]
                  : [];
            const selKey =
              selectedCandidateByConflict[c.conflict_key] || candList[0]?.building_key || "";
            const busy = resolvingConflictKey === c.conflict_key;

            return (
              <div
                key={c.conflict_key}
                className="ag-conflict-card"
                data-conflict-key={c.conflict_key}
              >
                <div className="ag-conflict-info">
                  <span className="module-code tabular">FIELD CONFLICT</span>
                  <div className="ag-conflict-copy">
                    <strong>
                      {`${c.planet_name || "Colony"} [${c.planet_coords || c.planet_id}] full — ${c.blocked_building_name || c.blocked_building_key} → L${c.blocked_target_level ?? "?"} needs 1 free field`}
                    </strong>
                    <span className="action-sub">
                      Auto-demolition is disabled. Choose whether to demolish an existing building
                      or keep &amp; reserve its floor in Config Optimizer.
                    </span>
                  </div>
                </div>
                <div className="ag-conflict-actions">
                  {candList.length > 0 ? (
                    <select
                      className="ag-conflict-select"
                      aria-label="Candidate building for field conflict"
                      value={selKey}
                      onChange={(e) => onSelectConflictCandidate(c.conflict_key, e.target.value)}
                    >
                      {candList.map((cand) => (
                        <option key={cand.building_key} value={cand.building_key}>
                          {`${cand.name || cand.building_key} (L${cand.level ?? "?"})`}
                        </option>
                      ))}
                    </select>
                  ) : null}
                  <button
                    type="button"
                    className="btn btn-warning btn-sm"
                    id={`btn-conflict-demolish-${idx}`}
                    data-conflict-action="demolish"
                    disabled={busy}
                    onClick={() => onResolveConflict(c, "demolish")}
                  >
                    Demolish Building
                  </button>
                  <button
                    type="button"
                    className="btn btn-primary btn-sm"
                    id={`btn-conflict-reserve-${idx}`}
                    data-conflict-action="keep_reserve"
                    disabled={busy}
                    onClick={() => onResolveConflict(c, "keep_reserve")}
                  >
                    Keep &amp; Reserve Fields (Rerun Pipeline)
                  </button>
                </div>
              </div>
            );
          })}
        </div>
      ) : null}

      <div className="ag-kpi-strip" aria-label="AI-Planner KPI summary">
        <div className="ag-kpi-cell">
          <span className="ag-kpi-label">Net Discounted SpiceEq</span>
          <strong className="ag-kpi-val tabular" id="ai-kpi-reward">
            {discReward !== null ? fmtCompact(discReward) : "—"}
          </strong>
          <span className="ag-kpi-sub tabular">{`γ=${weeklyDiscount} · ${rewardWeeks}w NPV`}</span>
        </div>
        <div className="ag-kpi-cell">
          <span className="ag-kpi-label">{`Total ${rewardWeeks}-Week Net Output`}</span>
          <strong className="ag-kpi-val tabular" id="ai-kpi-output">
            {totalNetOutput !== null ? fmtCompact(totalNetOutput) : "—"}
          </strong>
          <span className="ag-kpi-sub tabular">
            {summary.status ? `Status: ${String(summary.status).toUpperCase()}` : "SpiceEq"}
          </span>
        </div>
        <div className="ag-kpi-cell">
          <span className="ag-kpi-label">Progress (Completed / Horizon)</span>
          <strong className="ag-kpi-val tabular" id="ai-kpi-progress">
            {`${stepsCompleted} / ${stepsTotal}`}
          </strong>
          <span className="ag-kpi-sub tabular">
            {`${summary.time ? `Reached in ${summary.time}` : `Horizon ${rewardWeeks}w`}${inProgTotal > 0 ? ` · ${inProgTotal} live` : ""} · +${dispatchDelayMin}m dispatch`}
          </span>
        </div>
        <div className="ag-kpi-cell">
          <span className="ag-kpi-label">Fleet Slots &amp; Median Loot/h</span>
          <strong className="ag-kpi-val tabular" id="ai-kpi-telemetry">
            {`${startSlots} → ${endSlots} slots`}
          </strong>
          <span className="ag-kpi-sub tabular">
            {lootPerHr !== null
              ? `${fmtCompact(lootPerHr)} SpEq/h · RT ${formatDurationSecondsCompact(medianRtSec)}`
              : "SQLite attack history"}
          </span>
        </div>
      </div>

      <div className="ag-ai-compact-strips" id="ag-ai-compact-strips">
        <div className="ag-ai-compact-strips-row">
          <div
            className="ag-smelt-strip"
            id="ag-ai-smelting-strip"
            role="region"
            aria-label="Blast furnace smelting state"
          >
            <div className="ag-smelt-strip-main">
              <span className="module-code tabular">B2</span>
              <span className="ag-smelt-label">Blast furnace:</span>
              <span
                className={`ag-smelt-pill tabular ${
                  initLiveMode === "on"
                    ? "is-on"
                    : initLiveMode === "mixed"
                      ? "is-toggle"
                      : "is-off"
                }`}
                id="ai-smelt-live-badge"
              >
                {`Initial ${liveModeLabel}`}
              </span>
              {hasSmeltingTarget ? (
                <>
                  <span className="ag-smelt-arrow" aria-hidden="true">
                    →
                  </span>
                  <span
                    className={`ag-smelt-pill tabular ${
                      initToggleNeeded ? "is-toggle" : initTargetEnabled ? "is-on" : "is-off"
                    }`}
                    id="ai-smelt-target-badge"
                  >
                    {`t=0 ${initTargetEnabled ? "ON 100%" : "OFF 0%"}${
                      initToggleNeeded ? " · TOGGLE NEEDED" : " · ALIGNED"
                    }`}
                  </span>
                </>
              ) : null}
            </div>
            <div className="ag-smelt-strip-meta tabular">
              {metalSmelted !== null
                ? `Smelted ${fmtCompact(metalSmelted)} Metal · ${smeltingTimeText} · ${smeltingTogglesCount} toggles`
                : "Run AI-Planner to compute smelting schedule"}
            </div>
          </div>

          <div
            className="ag-adaptive-strip"
            id="ag-ai-adaptive-strip"
            role="region"
            aria-label="Adaptive execution switch and per-colony idle diagnostics"
          >
            <div className="ag-adaptive-strip-head">
              <div className="ag-adaptive-strip-controls">
                <label className="ag-adaptive-toggle" htmlFor="ai-adaptive-exec-toggle">
                  <input
                    type="checkbox"
                    className="config-checkbox"
                    id="ai-adaptive-exec-toggle"
                    checked={adaptiveEnabled}
                    disabled={isTogglingAdaptive}
                    onChange={(e) => onToggleAdaptiveExecution(e.target.checked)}
                  />
                  <span className="module-code tabular">V2</span>
                  <span className="ag-smelt-label">Adaptive Execution</span>
                  <span
                    className={`ag-smelt-pill tabular ${adaptiveEnabled ? "is-on" : "is-off"}`}
                    id="ai-adaptive-exec-state"
                  >
                    {adaptiveEnabled ? "ON" : "OFF"}
                  </span>
                </label>

                <span
                  className={`ag-smelt-pill tabular ${
                    adaptiveTotalColonies > 0 && adaptiveActiveBuilders >= adaptiveTotalColonies
                      ? "is-on"
                      : adaptiveActiveBuilders > 0
                        ? "is-toggle"
                        : "is-off"
                  }`}
                  id="ai-adaptive-builders-badge"
                >
                  {`Builders ${adaptiveActiveBuilders}/${adaptiveTotalColonies}`}
                </span>

                {transportFleet
                  ? (() => {
                      const delivCph = safeFiniteNumber(
                        transportFleet.delivered_cargo_per_hour_24h ??
                          transportFleet.delivered_cargo_per_hour,
                        null
                      );
                      const delivPct = safeFiniteNumber(transportFleet.delivered_vs_planned_pct, null);
                      const delivNote =
                        delivCph !== null
                          ? ` · Delivered 24h: ${fmtCompact(delivCph)}/h${
                              delivPct !== null ? ` (${formatSharePct(delivPct)}% of plan)` : ""
                            }`
                          : "";
                      return (
                        <span
                          className={`ag-smelt-pill tabular ${
                            (safeFiniteNumber(transportFleet.target_fleet_cargo, 0) ?? 0) <= 0 ||
                            (safeFiniteNumber(transportFleet.total_fleet_cargo, 0) ?? 0) >=
                              (safeFiniteNumber(transportFleet.target_fleet_cargo, 0) ?? 0)
                              ? "is-on"
                              : "is-toggle"
                          }`}
                          id="ai-adaptive-fleet-badge"
                          title={`Parked: ${fmtInt(transportFleet.owned_parked_cargo)} · In-flight: ${fmtInt(
                            transportFleet.in_flight_cargo
                          )} · Planned 24h flow: ${fmtCompact(transportFleet.planned_cargo_per_hour_24h)}/h${delivNote} · Util: ${Math.round(
                            (safeFiniteNumber(transportFleet.realized_utilization, 0.5) ?? 0.5) * 100
                          )}%`}
                        >
                          {`Cargo ${fmtCompact(transportFleet.total_fleet_cargo)} / ${fmtCompact(
                            transportFleet.target_fleet_cargo
                          )}`}
                        </span>
                      );
                    })()
                  : null}

                {hasWindow24hSamples && window24h ? (
                  <span
                    className={`ag-smelt-pill tabular ${
                      (safeFiniteNumber(window24h.idle_with_affordable_share_pct, 0) ?? 0) > 15 ||
                      (safeFiniteNumber(window24h.ready_not_started_share_pct, 0) ?? 0) > 5
                        ? "is-toggle"
                        : "is-on"
                    }`}
                    id="ai-adaptive-gate24h-badge"
                    title={`24h Step-5 diagnostics (${window24h.colony_samples} colony samples across ${window24h.bucket_count} x 10m buckets)`}
                  >
                    {`Idle w/ affordable: ${formatSharePct(
                      window24h.idle_with_affordable_share_pct
                    )}% · Ready gap: ${formatSharePct(window24h.ready_not_started_share_pct)}%`}
                  </span>
                ) : null}
              </div>

              {autoReplan ? (
                <div className="ag-smelt-strip-meta tabular" id="ai-adaptive-replan-badge">
                  {`Auto-replan: ${autoReplan.drift_ready ? "Drift ready" : "Cooldown"}${
                    autoReplan.last_trigger_reason ? ` · Last: ${autoReplan.last_trigger_reason}` : ""
                  }`}
                </div>
              ) : null}
            </div>
          </div>
        </div>

        <div className="ag-adaptive-diag-list" id="ag-ai-colony-diag-list">
          {adaptiveColonies.length === 0 ? (
            <span className="action-sub">
              Colony idle diagnostics appear once AI-Planner state is loaded.
            </span>
          ) : (
            adaptiveColonies.map((col) => {
              const pid = Math.round(safeFiniteNumber(col.planet_id, 0) ?? 0);
              const coords = String(col.coords || "").trim();
              const displayName =
                customPlanetNames[String(pid)] || col.name || planetNameById.get(pid) || "Colony";
              const stateMeta = formatAdaptiveColonyState(col.state);
              const b2Lvl = Math.round(safeFiniteNumber(col.b2_level, 0) ?? 0);
              const b2Pct = Math.round(safeFiniteNumber(col.b2_pct, 0) ?? 0);
              const marginAt100 = safeFiniteNumber(col.b2_energy_margin_at_100, null);
              const lowEnergyMargin = marginAt100 !== null && marginAt100 < 100;
              const tooltipParts = [
                col.reason || stateMeta.label,
                col.next_label ? `Next: ${col.next_label}` : "",
                col.idle_with_affordable_target_step ? "Affordable target step waiting" : "",
                lowEnergyMargin ? `Energy margin at 100% b2: ${Math.round(marginAt100!)}` : "",
              ].filter(Boolean);

              return (
                <div
                  key={`${pid}-${coords || displayName}`}
                  className={`ag-adaptive-chip ${stateMeta.tone}`}
                  data-planet-id={pid}
                  data-colony-state={col.state}
                  title={tooltipParts.join(" · ")}
                >
                  <span className="ag-adaptive-chip-name tabular">
                    {coords ? `${displayName} [${coords}]` : displayName}
                  </span>
                  <span className={`ag-adaptive-state-pill tabular ${stateMeta.tone}`}>
                    {stateMeta.label}
                  </span>
                  <span className="ag-adaptive-b2-pill tabular">
                    {b2Lvl > 0 ? `b2 ${b2Pct}%` : "no b2"}
                  </span>
                  {lowEnergyMargin ? (
                    <span className="ag-adaptive-energy-warn tabular">
                      {`Low E (${Math.round(marginAt100!)})`}
                    </span>
                  ) : null}
                </div>
              );
            })
          )}
        </div>
      </div>
    </>
  );
};
