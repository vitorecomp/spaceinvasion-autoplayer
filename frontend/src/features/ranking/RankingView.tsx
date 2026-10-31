import React, { useEffect, useMemo, useState } from "react";
import {
  feedbackKindForError } from "../../api/httpClient";
import { SortableTh } from "../../components/SortableTh";
import { useActivePage,
  useApiOnline,
  useConsoleActions,
  useDashboard,
} from "../../store";
import {
  durationToSeconds,
  useTableSort,
  type SortColumnMap,
} from "../../hooks/useTableSort";
import { UniverseResearchMaxRow } from "../../types/api";
import { clampPct, finiteNum, fmtInt } from "../../utils/formatters";
import { RankTrajectorySvg } from "./RankTrajectorySvg";

export interface RankingViewProps {
  hidden?: boolean;
}

function fmtSignedDeltaInt(val: number): string {
  const rounded = Math.round(val);
  const clean = rounded === 0 ? 0 : rounded;
  return `${clean >= 0 ? "+" : ""}${fmtInt(clean)}`;
}

export const RankingView: React.FC<RankingViewProps> = ({ hidden }) => {
  const apiOnline = useApiOnline();
  const dashboard = useDashboard();
  const activePage = useActivePage();
  const { apiCommand, loadDashboard, showFeedback } = useConsoleActions();

  const [localUniRows, setLocalUniRows] = useState<UniverseResearchMaxRow[] | null>(null);
  const [syncingUniRes, setSyncingUniRes] = useState<boolean>(false);

  const isHidden = hidden ?? activePage !== "ranking";
  const rnk = dashboard?.ranking || null;

  useEffect(() => {
    const dashRows = dashboard?.universe_research_max;
    if (!Array.isArray(dashRows) || dashRows.length === 0) return;
    setLocalUniRows((prev) => {
      if (!prev || prev.length === 0) return null;
      const prevMaxTs = Math.max(...prev.map((r) => finiteNum(r.updated_ts, 0)));
      const dashMaxTs = Math.max(...dashRows.map((r) => finiteNum(r.updated_ts, 0)));
      return dashMaxTs >= prevMaxTs ? null : prev;
    });
  }, [dashboard?.universe_research_max]);

  useEffect(() => {
    if (
      activePage === "ranking" &&
      apiOnline &&
      !Array.isArray(dashboard?.universe_research_max) &&
      localUniRows === null
    ) {
      let cancelled = false;
      apiCommand<{ rows?: UniverseResearchMaxRow[] }>({
        command: "universe_research_max",
        sync: false,
      })
        .then((res) => {
          if (!cancelled && Array.isArray(res?.rows)) {
            setLocalUniRows(res.rows);
          }
        })
        .catch(() => {
          // Ignore background fetch error when offline or unsupported
        });
      return () => {
        cancelled = true;
      };
    }
  }, [activePage, apiCommand, apiOnline, dashboard?.universe_research_max, localUniRows]);

  const handleSyncUniverseResearch = async () => {
    setSyncingUniRes(true);
    try {
      showFeedback("Syncing universe max research records...", "ok");
      const res = await apiCommand<{ rows?: UniverseResearchMaxRow[]; message?: string }>({
        command: "universe_research_max",
        sync: true,
      });
      if (Array.isArray(res?.rows)) {
        setLocalUniRows(res.rows);
      }
      showFeedback(
        res?.message || "Universe max research records synced.",
        "ok"
      );
      await loadDashboard({ refresh: false, force: true });
    } catch (err: any) {
      showFeedback(
        `Universe research sync failed: ${err.message}`,
        feedbackKindForError(err)
      );
    } finally {
      setSyncingUniRes(false);
    }
  };

  const cur = rnk?.current || {};
  const az = cur.attack_zone || {};
  const rates = rnk?.rates || {};
  const trend = rnk?.observed_trend || {};

  const srvRank = finiteNum(cur.server_rank, null);
  const srvPts = finiteNum(cur.server_points, null);

  let serverPosTxt = "—";
  let estPtsTxt = "—";
  let azLabelTxt = "—";
  let azRangeTxt = "—";
  let rateHrTxt = "—";
  let resHrTxt = "—";
  let breakdownTxt = "—";
  let spiceValTxt = "—";

  if (rnk) {
    const srvRankTxt =
      cur.server_rank != null ? (srvRank !== null ? `#${fmtInt(srvRank)}` : "#—") : "Rank pending";
    const srvPtsTxt =
      cur.server_points != null ? (srvPts !== null ? `${fmtInt(srvPts)} pts` : "— pts") : "— pts";
    serverPosTxt = `${srvRankTxt} · ${srvPtsTxt}`;

    const estPts = finiteNum(cur.estimated_points, 0);
    const cleanEstPts = Math.abs(estPts) < 0.05 ? 0 : estPts;
    estPtsTxt = `~${cleanEstPts.toFixed(1)} est. score (bld/res/flt)`;

    azLabelTxt = String(az.label || "—");
    const shield = cur.newbie_protected
      ? `Shielded (under 10k · ${cur.newbie_eta_compound || "—"})`
      : "Open AZ";
    azRangeTxt = `${az.range_text || ""} · ${shield}`;

    const steadyRate = finiteNum(rates.pts_per_hr_steady, 0);
    const netResRate = finiteNum(rates.net_resources_per_hr, 0);
    const spdFactor = finiteNum(rates.speedup_factor, 1);
    const cleanSteady = Math.abs(steadyRate) < 0.005 ? 0 : steadyRate;
    rateHrTxt = `${cleanSteady >= 0 ? "+" : ""}${cleanSteady.toFixed(2)} pts/hr`;
    resHrTxt = `${fmtInt(Math.round(netResRate))} res/hr · x${spdFactor}`;

    const estPtsRaw = finiteNum(cur.estimated_points, null);
    const bldPts = finiteNum(cur.buildings_points, null);
    const resPts = finiteNum(cur.research_points, null);
    let bPct = 0;
    let rPct = 0;
    if (estPtsRaw !== null && estPtsRaw > 0 && bldPts !== null && resPts !== null) {
      bPct = clampPct(Math.round((bldPts / estPtsRaw) * 100));
      rPct = clampPct(Math.round((resPts / estPtsRaw) * 100));
      if (bPct + rPct > 100) {
        rPct = Math.max(0, 100 - bPct);
      }
    }
    const fPct = Math.max(0, 100 - bPct - rPct);
    breakdownTxt = `Bld ${bPct}% · Res ${rPct}% · Flt ${fPct}%`;

    const spiceVal = finiteNum(cur.spice_value, 0);
    spiceValTxt = `${fmtInt(Math.round(spiceVal))} Spice Eq`;
  }

  const count = Math.max(0, Math.round(finiteNum(trend.sample_count, 0)));
  const deltaPts = finiteNum(trend.delta_points, null);
  const obsRate = finiteNum(trend.observed_pts_per_hr, null);
  const elapsedHrs = finiteNum(trend.elapsed_hours, null);
  const rankClimbed = finiteNum(trend.rank_climbed, null);

  let observedRateTxt = rnk ? "Collecting samples..." : "Trend collecting...";
  let observedSummaryTxt = rnk
    ? "Rate delta activates automatically after 2 distinct observations."
    : "Snapshots recorded in SQLite (rank_snapshots).";

  if (rnk && count >= 2 && deltaPts !== null) {
    const cleanDelta = deltaPts === 0 ? 0 : deltaPts;
    const deltaSign = cleanDelta >= 0 ? "+" : "";
    const cleanObsRate = obsRate !== null ? (obsRate === 0 ? 0 : obsRate) : null;
    const rateSign = cleanObsRate !== null && cleanObsRate >= 0 ? "+" : "";
    let rankStr = "";
    if (rankClimbed !== null) {
      const cleanRank = rankClimbed === 0 ? 0 : rankClimbed;
      if (cleanRank > 0) {
        rankStr = ` · Rank climbed +${fmtInt(cleanRank)}`;
      } else if (cleanRank < 0) {
        rankStr = ` · Rank dropped ${fmtInt(Math.abs(cleanRank))}`;
      } else {
        rankStr = " · Rank unchanged (0)";
      }
    }
    observedRateTxt =
      cleanObsRate !== null
        ? `${rateSign}${cleanObsRate} pts/hr observed`
        : `${deltaSign}${cleanDelta} pts`;
    observedSummaryTxt = `Window ${elapsedHrs !== null ? elapsedHrs : "—"}h: ${deltaSign}${cleanDelta} pts${rankStr}.`;
  }

  const horizons: Array<Record<string, any>> = Array.isArray(rnk?.horizons) ? rnk!.horizons : [];
  const milestones: Array<Record<string, any>> = Array.isArray(rnk?.milestones)
    ? rnk!.milestones
    : [];

  const uniRows: UniverseResearchMaxRow[] =
    localUniRows ??
    (Array.isArray(dashboard?.universe_research_max) ? dashboard!.universe_research_max! : []);

  const horizonSortCols = useMemo<
    SortColumnMap<
      Record<string, any>,
      "horizon" | "steady" | "steadyAz" | "reinvested" | "reinvestedAz"
    >
  >(
    () => ({
      horizon: (row) => durationToSeconds(row.label) ?? (String(row.label || "").trim() || null),
      steady: {
        accessor: (row) => finiteNum(row.steady_points, null),
        defaultDirection: "desc",
      },
      steadyAz: (row) => String(row.steady_az || "").trim() || null,
      reinvested: {
        accessor: (row) => finiteNum(row.compound_points, null),
        defaultDirection: "desc",
      },
      reinvestedAz: (row) => String(row.compound_az || "").trim() || null,
    }),
    []
  );

  const milestoneSortCols = useMemo<
    SortColumnMap<
      Record<string, any>,
      "milestone" | "remaining" | "steadyEta" | "compoundEta"
    >
  >(
    () => ({
      milestone: (m) => finiteNum(m.target_points, null) ?? (String(m.label || "").trim() || null),
      remaining: (m) => finiteNum(m.missing_points, null),
      steadyEta: (m) => durationToSeconds(m.steady_eta),
      compoundEta: (m) => durationToSeconds(m.compound_eta),
    }),
    []
  );

  const uniSortCols = useMemo<
    SortColumnMap<
      UniverseResearchMaxRow,
      "research" | "ownLevel" | "uniMax" | "gap" | "holder" | "catalogCap"
    >
  >(
    () => ({
      research: (r) => String(r.name || r.key || r.research_key || ""),
      ownLevel: {
        accessor: (r) => Math.max(0, Math.round(finiteNum(r.own_level, 0))),
        defaultDirection: "desc",
      },
      uniMax: {
        accessor: (r) => Math.max(0, Math.round(finiteNum(r.universe_max_level, 0))),
        defaultDirection: "desc",
      },
      gap: {
        accessor: (r) => {
          const ownLvl = Math.max(0, Math.round(finiteNum(r.own_level, 0)));
          const uniMax = Math.max(0, Math.round(finiteNum(r.universe_max_level, 0)));
          return Math.max(0, Math.round(finiteNum(r.gap_to_max, Math.max(0, uniMax - ownLvl))));
        },
        defaultDirection: "desc",
      },
      holder: (r) => {
        const ownLvl = Math.max(0, Math.round(finiteNum(r.own_level, 0)));
        const uniMax = Math.max(0, Math.round(finiteNum(r.universe_max_level, 0)));
        if (r.holder) {
          return r.holder_coords ? `${r.holder} [${r.holder_coords}]` : String(r.holder);
        }
        return uniMax > 0 && ownLvl >= uniMax ? "You" : null;
      },
      catalogCap: {
        accessor: (r) => {
          const cap = finiteNum(r.catalog_max_level, null);
          return cap !== null && cap > 0 ? cap : null;
        },
        defaultDirection: "desc",
      },
    }),
    []
  );

  const horizonSort = useTableSort(horizons, horizonSortCols);
  const milestoneSort = useTableSort(milestones, milestoneSortCols);
  const uniSort = useTableSort(uniRows, uniSortCols);

  return (
    <section
      id="view-ranking"
      className="page-view"
      aria-label="Ranking evolution and forecast"
      hidden={isHidden}
    >
      <article className="hud-window page-fill">
        <div className="window-bar">
          <div className="window-title-group">
            <span className="module-code tabular">RNK</span>
            <h2>Ranking Evolution, Attack Zone &amp; Growth Forecast</h2>
          </div>
          <span className="module-meta tabular" id="rnk-trend-meta">
            {`${count} sample${count === 1 ? "" : "s"}`}
          </span>
        </div>

        <div className="ranking-pane">
          <div className="ranking-kpi-grid">
            <div className="kpi-card">
              <span className="kpi-label tabular">Server Rank &amp; Score</span>
              <strong className="kpi-value tabular" id="rnk-server-pos">
                {serverPosTxt}
              </strong>
              <span className="kpi-sub tabular" id="rnk-est-pts">
                {estPtsTxt}
              </span>
            </div>
            <div className="kpi-card">
              <span className="kpi-label tabular">Attack Zone Tier</span>
              <strong className="kpi-value tabular" id="rnk-az-label">
                {azLabelTxt}
              </strong>
              <span className="kpi-sub tabular" id="rnk-az-range">
                {azRangeTxt}
              </span>
            </div>
            <div className="kpi-card">
              <span className="kpi-label tabular">Hourly Growth Pace</span>
              <strong className="kpi-value tabular" id="rnk-rate-hr">
                {rateHrTxt}
              </strong>
              <span className="kpi-sub tabular" id="rnk-res-hr">
                {resHrTxt}
              </span>
            </div>
            <div className="kpi-card">
              <span className="kpi-label tabular">Empire Breakdown</span>
              <strong className="kpi-value tabular" id="rnk-breakdown">
                {breakdownTxt}
              </strong>
              <span className="kpi-sub tabular" id="rnk-spice-val">
                {spiceValTxt}
              </span>
            </div>
          </div>

          <div className="ranking-grid">
            <div className="ranking-chart-card">
              <div className="card-subhead">
                <h3>Trajectory Curve</h3>
                <span className="tabular" id="rnk-observed-rate">
                  {observedRateTxt}
                </span>
              </div>
              <RankTrajectorySvg samples={trend.samples || []} />
              <p className="action-sub tabular" id="rnk-observed-summary">
                {observedSummaryTxt}
              </p>
            </div>

            <div className="ranking-tables-stack">
              <div className="table-wrap">
                <table>
                  <thead>
                    <tr>
                      <SortableTh
                        label="Horizon"
                        sortKey="horizon"
                        activeKey={horizonSort.sortKey}
                        direction={horizonSort.sortDirection}
                        onSort={horizonSort.requestSort}
                      />
                      <SortableTh
                        label="Steady"
                        sortKey="steady"
                        activeKey={horizonSort.sortKey}
                        direction={horizonSort.sortDirection}
                        onSort={horizonSort.requestSort}
                        align="right"
                      />
                      <SortableTh
                        label="AZ"
                        sortKey="steadyAz"
                        activeKey={horizonSort.sortKey}
                        direction={horizonSort.sortDirection}
                        onSort={horizonSort.requestSort}
                        align="right"
                      />
                      <SortableTh
                        label="Reinvested"
                        sortKey="reinvested"
                        activeKey={horizonSort.sortKey}
                        direction={horizonSort.sortDirection}
                        onSort={horizonSort.requestSort}
                        align="right"
                      />
                      <SortableTh
                        label="AZ"
                        sortKey="reinvestedAz"
                        activeKey={horizonSort.sortKey}
                        direction={horizonSort.sortDirection}
                        onSort={horizonSort.requestSort}
                        align="right"
                      />
                    </tr>
                  </thead>
                  <tbody id="rnk-horizons-tbody">
                    {horizonSort.sortedRows.length === 0 ? (
                      <tr>
                        <td colSpan={5} className="empty-cell">
                          Waiting for ranking forecast...
                        </td>
                      </tr>
                    ) : (
                      horizonSort.sortedRows.map((row, idx) => {
                        const sp = finiteNum(row.steady_points, null);
                        const sd = finiteNum(row.steady_delta, null);
                        const cp = finiteNum(row.compound_points, null);
                        const cd = finiteNum(row.compound_delta, null);
                        return (
                          <tr key={row.label || idx}>
                            <td>{String(row.label || "—")}</td>
                            <td className="col-right tabular">
                              {sp !== null && sd !== null
                                ? `${fmtInt(Math.round(sp))} (${fmtSignedDeltaInt(sd)})`
                                : "—"}
                            </td>
                            <td className="col-right tabular">{String(row.steady_az || "—")}</td>
                            <td className="col-right tabular">
                              {cp !== null && cd !== null
                                ? `${fmtInt(Math.round(cp))} (${fmtSignedDeltaInt(cd)})`
                                : "—"}
                            </td>
                            <td className="col-right tabular">{String(row.compound_az || "—")}</td>
                          </tr>
                        );
                      })
                    )}
                  </tbody>
                </table>
              </div>

              <div className="table-wrap">
                <table>
                  <thead>
                    <tr>
                      <SortableTh
                        label="Next Milestone"
                        sortKey="milestone"
                        activeKey={milestoneSort.sortKey}
                        direction={milestoneSort.sortDirection}
                        onSort={milestoneSort.requestSort}
                      />
                      <SortableTh
                        label="Remaining"
                        sortKey="remaining"
                        activeKey={milestoneSort.sortKey}
                        direction={milestoneSort.sortDirection}
                        onSort={milestoneSort.requestSort}
                        align="right"
                      />
                      <SortableTh
                        label="Steady ETA"
                        sortKey="steadyEta"
                        activeKey={milestoneSort.sortKey}
                        direction={milestoneSort.sortDirection}
                        onSort={milestoneSort.requestSort}
                        align="right"
                      />
                      <SortableTh
                        label="Compound ETA"
                        sortKey="compoundEta"
                        activeKey={milestoneSort.sortKey}
                        direction={milestoneSort.sortDirection}
                        onSort={milestoneSort.requestSort}
                        align="right"
                      />
                    </tr>
                  </thead>
                  <tbody id="rnk-milestones-tbody">
                    {milestoneSort.sortedRows.length === 0 ? (
                      <tr>
                        <td colSpan={4} className="empty-cell">
                          Calculating milestones...
                        </td>
                      </tr>
                    ) : (
                      milestoneSort.sortedRows.map((m, idx) => {
                        const tgtPts = finiteNum(m.target_points, null);
                        const missPts = finiteNum(m.missing_points, null);
                        return (
                          <tr key={m.label || idx}>
                            <td>{`${m.label} (${tgtPts !== null ? fmtInt(tgtPts) : "—"})`}</td>
                            <td className="col-right tabular">
                              {missPts !== null ? `+${fmtInt(Math.round(missPts))}` : "—"}
                            </td>
                            <td className="col-right tabular">{String(m.steady_eta || "—")}</td>
                            <td className="col-right tabular">{String(m.compound_eta || "—")}</td>
                          </tr>
                        );
                      })
                    )}
                  </tbody>
                </table>
              </div>
            </div>
          </div>

          <div className="ranking-chart-card">
            <div className="card-subhead">
              <h3>Universe Max Research Levels</h3>
              <button
                type="button"
                className="btn btn-secondary btn-sm"
                id="btn-sync-universe-research"
                onClick={handleSyncUniverseResearch}
                disabled={syncingUniRes}
              >
                {syncingUniRes ? "Syncing..." : "Sync Records"}
              </button>
            </div>
            <div className="table-wrap table-scroll">
              <table id="universe-research-max-table">
                <thead>
                  <tr>
                    <SortableTh
                      label="Research"
                      sortKey="research"
                      activeKey={uniSort.sortKey}
                      direction={uniSort.sortDirection}
                      onSort={uniSort.requestSort}
                    />
                    <SortableTh
                      label="Your Level"
                      sortKey="ownLevel"
                      activeKey={uniSort.sortKey}
                      direction={uniSort.sortDirection}
                      onSort={uniSort.requestSort}
                      align="right"
                    />
                    <SortableTh
                      label="Universe Max"
                      sortKey="uniMax"
                      activeKey={uniSort.sortKey}
                      direction={uniSort.sortDirection}
                      onSort={uniSort.requestSort}
                      align="right"
                    />
                    <SortableTh
                      label="Delta / Gap"
                      sortKey="gap"
                      activeKey={uniSort.sortKey}
                      direction={uniSort.sortDirection}
                      onSort={uniSort.requestSort}
                      align="right"
                    />
                    <SortableTh
                      label="Record Holder"
                      sortKey="holder"
                      activeKey={uniSort.sortKey}
                      direction={uniSort.sortDirection}
                      onSort={uniSort.requestSort}
                    />
                    <SortableTh
                      label="Catalog Cap"
                      sortKey="catalogCap"
                      activeKey={uniSort.sortKey}
                      direction={uniSort.sortDirection}
                      onSort={uniSort.requestSort}
                      align="right"
                    />
                  </tr>
                </thead>
                <tbody id="universe-research-max-tbody">
                  {uniSort.sortedRows.length === 0 ? (
                    <tr>
                      <td colSpan={6} className="empty-cell">
                        Click Sync Records to load universe max research levels.
                      </td>
                    </tr>
                  ) : (
                    uniSort.sortedRows.map((r) => {
                      const rKey = String(r.key || r.research_key || `r${r.research_id}`);
                      const ownLvl = Math.max(0, Math.round(finiteNum(r.own_level, 0)));
                      const uniMax = Math.max(0, Math.round(finiteNum(r.universe_max_level, 0)));
                      const gap = Math.max(
                        0,
                        Math.round(finiteNum(r.gap_to_max, Math.max(0, uniMax - ownLvl)))
                      );
                      const catCap = finiteNum(r.catalog_max_level, null);
                      const holderTxt = r.holder
                        ? r.holder_coords
                          ? `${r.holder} [${r.holder_coords}]`
                          : String(r.holder)
                        : uniMax > 0 && ownLvl >= uniMax
                          ? "You"
                          : "—";

                      return (
                        <tr key={rKey}>
                          <td>
                            {`${r.name} `}
                            <span className="action-sub tabular">{`(${rKey})`}</span>
                          </td>
                          <td className="col-right tabular">{`L${fmtInt(ownLvl)}`}</td>
                          <td className="col-right tabular">{`L${fmtInt(uniMax)}`}</td>
                          <td className="col-right tabular">
                            {gap <= 0 && uniMax > 0 ? (
                              <span className="status-word">LEADER (0)</span>
                            ) : gap > 0 ? (
                              <span className="status-word is-warn">{`-${fmtInt(gap)} lvls`}</span>
                            ) : (
                              "0"
                            )}
                          </td>
                          <td>{holderTxt}</td>
                          <td className="col-right tabular">
                            {catCap !== null && catCap > 0 ? `L${fmtInt(catCap)}` : "Uncapped"}
                          </td>
                        </tr>
                      );
                    })
                  )}
                </tbody>
              </table>
            </div>
          </div>
        </div>
      </article>
    </section>
  );
};

export default RankingView;
