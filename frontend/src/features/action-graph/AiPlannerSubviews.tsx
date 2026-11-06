import React, { useMemo } from "react";
import { safeFiniteNumber } from "../../api/httpClient";
import { SortableTh } from "../../components/SortableTh";
import { TablePagination } from "../../components/TablePagination";
import { useTableSort, type SortColumnMap } from "../../hooks/useTableSort";
import type {
  AiPlannerGetPayload,
  AiPlannerSmeltingEvent,
  AiPlannerSmeltingPlanet,
  AiPlannerSmeltingToggleTarget,
  AiPlannerStepItem,
  PlanetData,
} from "../../types/api";
import { fmtCompact } from "../../utils/formatters";
import { DagCanvas } from "./DagCanvas";
import { formatDurationSecondsCompact } from "./plannerHelpers";

export type AiPlannerSubView = "dag" | "steps" | "weekly" | "smelting";

export interface AiPlannerSubviewsProps {
  subView: AiPlannerSubView;
  aiData: AiPlannerGetPayload | null;
  planets: PlanetData[];
  rewardWeeks: number;
  windowSize: number;
  weekFilter: string;
  planetFilter: string;
  stepsSearch: string;
  stepsPage: number;
  stepsPageSize: number;
  rawSteps: AiPlannerStepItem[];
  weeklyRows: Array<Record<string, any>>;
  smeltingPlanets: AiPlannerSmeltingPlanet[];
  smeltingEvents: AiPlannerSmeltingEvent[];
  toggleByPlanetId: Map<number, AiPlannerSmeltingToggleTarget>;
  onChangeWindowSize: (next: number) => void;
  onChangeWeekFilter: (next: string) => void;
  onChangePlanetFilter: (next: string) => void;
  onChangeStepsSearch: (next: string) => void;
  onChangeStepsPage: (next: number) => void;
  onChangeStepsPageSize: (next: number) => void;
}

export const AiPlannerSubviews: React.FC<AiPlannerSubviewsProps> = ({
  subView,
  aiData,
  planets,
  rewardWeeks,
  windowSize,
  weekFilter,
  planetFilter,
  stepsSearch,
  stepsPage,
  stepsPageSize,
  rawSteps,
  weeklyRows,
  smeltingPlanets,
  smeltingEvents,
  toggleByPlanetId,
  onChangeWindowSize,
  onChangeWeekFilter,
  onChangePlanetFilter,
  onChangeStepsSearch,
  onChangeStepsPage,
  onChangeStepsPageSize,
}) => {
  const stepSortCols = useMemo<
    SortColumnMap<AiPlannerStepItem, "seq" | "week" | "planet" | "action" | "duration" | "status">
  >(
    () => ({
      seq: (s) => safeFiniteNumber(s.seq ?? s.order, 0),
      week: (s) => safeFiniteNumber(s.week, 0),
      planet: (s) => String(s.planet_coords || s.planet_name || "account"),
      action: (s) => `${s.kind || ""} ${s.entity_name || s.entity_key || ""}`,
      duration: { accessor: (s) => safeFiniteNumber(s.duration_sec, 0), defaultDirection: "desc" },
      status: (s) => String(s.status || "queued"),
    }),
    []
  );
  const stepSort = useTableSort(rawSteps, stepSortCols);

  const weekSortCols = useMemo<
    SortColumnMap<Record<string, any>, "week" | "weight" | "spiceEq" | "discounted" | "cumulative">
  >(
    () => ({
      week: (w) => safeFiniteNumber(w.week, 0),
      weight: { accessor: (w) => safeFiniteNumber(w.discount_weight, 0), defaultDirection: "desc" },
      spiceEq: { accessor: (w) => safeFiniteNumber(w.spice_eq, 0), defaultDirection: "desc" },
      discounted: {
        accessor: (w) => safeFiniteNumber(w.discounted_spice_eq, 0),
        defaultDirection: "desc",
      },
      cumulative: {
        accessor: (w) => safeFiniteNumber(w.cumulative_discounted_spice_eq ?? w.cumulative, 0),
        defaultDirection: "desc",
      },
    }),
    []
  );
  const weekSort = useTableSort(weeklyRows, weekSortCols);

  return (
    <>
      <div className="ag-ai-subpane" id="ai-view-dag" hidden={subView !== "dag"}>
        <div className="ag-ai-window-bar" id="ag-ai-window-controls">
          <div className="inline-actions">
            <div className="tab-group" role="group" aria-label="DAG window slice">
              <button
                type="button"
                className={`tab-btn${windowSize === 40 ? " active" : ""}`}
                id="ai-win-40"
                onClick={() => onChangeWindowSize(40)}
              >
                Next 40 Steps
              </button>
              <button
                type="button"
                className={`tab-btn${windowSize > 40 ? " active" : ""}`}
                id="ai-win-200"
                onClick={() => onChangeWindowSize(200)}
              >
                Extended (200)
              </button>
            </div>
            <label className="ag-inline-filter tabular" htmlFor="ai-filter-week">
              Week
              <select
                id="ai-filter-week"
                value={weekFilter}
                onChange={(e) => onChangeWeekFilter(e.target.value)}
              >
                <option value="all">{`All (W1–W${rewardWeeks})`}</option>
                {Array.from({ length: rewardWeeks }, (_, i) => i + 1).map((w) => (
                  <option key={w} value={String(w)}>
                    {`W${w}`}
                  </option>
                ))}
              </select>
            </label>
            <label className="ag-inline-filter tabular" htmlFor="ai-filter-planet">
              Planet
              <select
                id="ai-filter-planet"
                value={planetFilter}
                onChange={(e) => onChangePlanetFilter(e.target.value)}
              >
                <option value="all">All Planets</option>
                {planets.map((p) => (
                  <option key={p.planet_id} value={String(p.planet_id)}>
                    {`${p.name || "Colony"} [${p.coords || p.planet_id}]`}
                  </option>
                ))}
              </select>
            </label>
          </div>
        </div>

        <DagCanvas
          graph={aiData?.graph || null}
          idPrefix="ag-ai"
          markerPrefix="ag-ai"
          emptyMessage="No AI-Planner path generated yet. Run Config Optimization or click Run AI-Planner."
        />
      </div>

      <div className="ag-ai-subpane" id="ai-view-steps" hidden={subView !== "steps"}>
        <div className="ag-ai-window-bar">
          <div className="inline-actions">
            <input
              type="search"
              id="ag-ai-steps-search"
              className="table-search-input"
              placeholder="Search steps (planet, building, research)..."
              value={stepsSearch}
              onChange={(e) => onChangeStepsSearch(e.target.value)}
            />
          </div>
          <span className="module-meta tabular">
            {`${aiData?.steps_page?.total ?? stepSort.sortedRows.length} total actions`}
          </span>
        </div>

        <div className="table-wrap table-scroll">
          <table id="ag-ai-steps-table">
            <thead>
              <tr>
                <SortableTh
                  label="#"
                  sortKey="seq"
                  activeKey={stepSort.sortKey}
                  direction={stepSort.sortDirection}
                  onSort={stepSort.requestSort}
                />
                <SortableTh
                  label="Week"
                  sortKey="week"
                  activeKey={stepSort.sortKey}
                  direction={stepSort.sortDirection}
                  onSort={stepSort.requestSort}
                />
                <SortableTh
                  label="Planet"
                  sortKey="planet"
                  activeKey={stepSort.sortKey}
                  direction={stepSort.sortDirection}
                  onSort={stepSort.requestSort}
                />
                <SortableTh
                  label="Action"
                  sortKey="action"
                  activeKey={stepSort.sortKey}
                  direction={stepSort.sortDirection}
                  onSort={stepSort.requestSort}
                />
                <th>Start</th>
                <SortableTh
                  label="Duration"
                  sortKey="duration"
                  activeKey={stepSort.sortKey}
                  direction={stepSort.sortDirection}
                  onSort={stepSort.requestSort}
                  align="right"
                />
                <SortableTh
                  label="Status"
                  sortKey="status"
                  activeKey={stepSort.sortKey}
                  direction={stepSort.sortDirection}
                  onSort={stepSort.requestSort}
                />
              </tr>
            </thead>
            <tbody id="ag-ai-steps-tbody">
              {stepSort.sortedRows.length === 0 ? (
                <tr>
                  <td colSpan={7} className="empty-cell">
                    No AI-Planner steps matching current filter.
                  </td>
                </tr>
              ) : (
                stepSort.sortedRows.map((s, idx) => {
                  const seqNum = Math.round(safeFiniteNumber(s.seq ?? s.order, idx + 1) ?? idx + 1);
                  const wkNum = Math.round(safeFiniteNumber(s.week, 1) ?? 1);
                  const stStr = String(s.status || "queued").toUpperCase();
                  return (
                    <tr key={`${seqNum}-${s.entity_key || idx}`}>
                      <td className="tabular">{`#${seqNum}`}</td>
                      <td className="tabular">{`W${wkNum}`}</td>
                      <td className="tabular">
                        {s.planet_coords
                          ? `${s.planet_name || "Colony"} [${s.planet_coords}]`
                          : "Account"}
                      </td>
                      <td>
                        <span className="action-title">
                          {`${s.kind || "BUILD"} · ${s.entity_name || s.entity_key || "Step"}${s.target_level != null ? ` → L${s.target_level}` : ""}`}
                        </span>
                      </td>
                      <td className="tabular">
                        <div>{s.start || formatDurationSecondsCompact(s.start_sec)}</div>
                        {s.needs_transport ? (
                          <div className="action-sub">
                            {`Ship at ${s.transport_request_text || formatDurationSecondsCompact(s.transport_request_at_sec)}`}
                          </div>
                        ) : null}
                      </td>
                      <td className="col-right tabular">
                        {s.duration_text || formatDurationSecondsCompact(s.duration_sec)}
                      </td>
                      <td>
                        <span className={`eta-pill tabular${stStr === "COMPLETED" ? "" : " is-wait"}`}>
                          {stStr}
                        </span>
                      </td>
                    </tr>
                  );
                })
              )}
            </tbody>
          </table>
        </div>

        <TablePagination
          idPrefix="ag-ai-steps"
          totalItems={Math.max(
            stepSort.sortedRows.length,
            Math.round(safeFiniteNumber(aiData?.steps_page?.total, 0) ?? 0)
          )}
          page={stepsPage}
          pageSize={stepsPageSize}
          itemLabel="steps"
          onPageChange={onChangeStepsPage}
          onPageSizeChange={onChangeStepsPageSize}
        />
      </div>

      <div className="ag-ai-subpane" id="ai-view-weekly" hidden={subView !== "weekly"}>
        <div className="table-wrap table-scroll">
          <table id="ag-ai-weekly-table">
            <thead>
              <tr>
                <SortableTh
                  label="Week"
                  sortKey="week"
                  activeKey={weekSort.sortKey}
                  direction={weekSort.sortDirection}
                  onSort={weekSort.requestSort}
                />
                <SortableTh
                  label="Discount γ^(w-1)"
                  sortKey="weight"
                  activeKey={weekSort.sortKey}
                  direction={weekSort.sortDirection}
                  onSort={weekSort.requestSort}
                  align="right"
                />
                <th className="col-right">Pig Iron</th>
                <th className="col-right">Metal</th>
                <th className="col-right">Kryptonite</th>
                <th className="col-right">Spice</th>
                <SortableTh
                  label="Weekly SpiceEq"
                  sortKey="spiceEq"
                  activeKey={weekSort.sortKey}
                  direction={weekSort.sortDirection}
                  onSort={weekSort.requestSort}
                  align="right"
                />
                <SortableTh
                  label="Discounted"
                  sortKey="discounted"
                  activeKey={weekSort.sortKey}
                  direction={weekSort.sortDirection}
                  onSort={weekSort.requestSort}
                  align="right"
                />
                <SortableTh
                  label="Cumulative"
                  sortKey="cumulative"
                  activeKey={weekSort.sortKey}
                  direction={weekSort.sortDirection}
                  onSort={weekSort.requestSort}
                  align="right"
                />
              </tr>
            </thead>
            <tbody id="ag-ai-weekly-tbody">
              {weekSort.sortedRows.length === 0 ? (
                <tr>
                  <td colSpan={9} className="empty-cell">
                    No weekly production breakdown available yet.
                  </td>
                </tr>
              ) : (
                weekSort.sortedRows.map((w) => (
                  <tr key={String(w.week)}>
                    <td className="tabular">{`W${w.week}`}</td>
                    <td className="col-right tabular">
                      {Number(safeFiniteNumber(w.discount_weight, 1) ?? 1).toFixed(4)}
                    </td>
                    <td className="col-right tabular">{fmtCompact(w.pig_iron)}</td>
                    <td className="col-right tabular">{fmtCompact(w.metal)}</td>
                    <td className="col-right tabular">{fmtCompact(w.kryptonite)}</td>
                    <td className="col-right tabular">{fmtCompact(w.spice)}</td>
                    <td className="col-right tabular">{fmtCompact(w.spice_eq)}</td>
                    <td className="col-right tabular">{fmtCompact(w.discounted_spice_eq)}</td>
                    <td className="col-right tabular">
                      {fmtCompact(w.cumulative_discounted_spice_eq ?? w.cumulative)}
                    </td>
                  </tr>
                ))
              )}
            </tbody>
          </table>
        </div>
      </div>

      <div className="ag-ai-subpane" id="ai-view-smelting" hidden={subView !== "smelting"}>
        <div className="ag-smelt-planets-bar" id="ag-ai-smelting-planets">
          <span className="ag-smelt-label">Initial Colony b2 Snapshot:</span>
          {smeltingPlanets.length === 0 ? (
            <span className="action-sub">No colonized planets captured yet.</span>
          ) : (
            <div className="ag-smelt-planet-chips">
              {smeltingPlanets.map((pl) => {
                const pending = toggleByPlanetId.get(pl.planet_id);
                const chipTone =
                  pl.b2_level <= 0
                    ? "is-off"
                    : pending
                      ? "is-toggle"
                      : pl.initial_enabled
                        ? "is-on"
                        : "is-off";
                return (
                  <span
                    key={`${pl.planet_id}-${pl.coords}`}
                    className={`ag-smelt-pill tabular ${chipTone}`}
                  >
                    {pl.b2_level <= 0
                      ? `${pl.name || "Colony"} [${pl.coords}] · No b2`
                      : `${pl.name || "Colony"} [${pl.coords}] · b2 L${pl.b2_level} · ${pl.initial_pct}%${
                          pending ? ` → ${pending.to_pct}%` : ""
                        }`}
                  </span>
                );
              })}
            </div>
          )}
        </div>

        <div className="table-wrap table-scroll">
          <table id="ag-ai-smelting-table">
            <thead>
              <tr>
                <th>#</th>
                <th>Time</th>
                <th>Week</th>
                <th>Action</th>
                <th>From</th>
                <th>Driving Step</th>
                <th>Reason</th>
              </tr>
            </thead>
            <tbody id="ag-ai-smelting-tbody">
              {smeltingEvents.length === 0 ? (
                <tr>
                  <td colSpan={7} className="empty-cell">
                    No Blast furnace (b2) toggle transitions scheduled in this plan.
                  </td>
                </tr>
              ) : (
                smeltingEvents.map((ev) => {
                  const isOn = Boolean(ev.enabled);
                  return (
                    <tr key={`smelt-${ev.seq}-${ev.time_sec}`}>
                      <td className="tabular">{`#${ev.seq}`}</td>
                      <td className="tabular">{ev.time_text || "now"}</td>
                      <td className="tabular">{`W${ev.week}`}</td>
                      <td>
                        <span className={`ag-smelt-pill tabular ${isOn ? "is-on" : "is-toggle"}`}>
                          {isOn ? "ENABLE 100%" : "DISABLE 0%"}
                        </span>
                        {ev.is_initial ? (
                          <span className="ag-smelt-init-tag tabular">t=0 LIVE</span>
                        ) : null}
                      </td>
                      <td className="tabular">{String(ev.from_mode || "off").toUpperCase()}</td>
                      <td className="tabular">
                        {ev.step_label
                          ? `${ev.step_label}${
                              ev.step_planet_coords
                                ? ` · ${(ev as any).step_planet_name ? `${(ev as any).step_planet_name} ` : ""}[${ev.step_planet_coords}]`
                                : ""
                            }`
                          : "—"}
                      </td>
                      <td>{ev.reason || "—"}</td>
                    </tr>
                  );
                })
              )}
            </tbody>
          </table>
        </div>
      </div>
    </>
  );
};
