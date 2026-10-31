import React, { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { feedbackKindForError } from "../../api/httpClient";
import { LoadingIndicator } from "../../components/LoadingIndicator";
import { SortableTh } from "../../components/SortableTh";
import { TablePagination, useTablePagination } from "../../components/TablePagination";
import {
  useActivePage,
  useActivePlanet,
  useApiStatus,
  useAtkData,
  useConsoleActions,
  useUiPrefs,
} from "../../store";
import {
  coordsSortKey,
  timestampSortKey,
  useTableSort,
  type SortColumnMap,
} from "../../hooks/useTableSort";
import {
  finiteNum,
  fmtDurationCountdown,
  fmtInt,
  fmtShortDateTime,
  formatResourcesInline,
} from "../../utils/formatters";

export interface AttacksViewProps {
  hidden?: boolean;
}

type BattleOutcomeFilter = "all" | "win" | "loss" | "draw" | "debris";
type BlockStatusFilter = "all" | "active" | "cleared";

const BATTLE_OUTCOME_FILTERS: ReadonlyArray<{ key: BattleOutcomeFilter; label: string }> = [
  { key: "all", label: "All Outcomes" },
  { key: "win", label: "Wins" },
  { key: "loss", label: "Losses" },
  { key: "draw", label: "Draws" },
  { key: "debris", label: "Debris Created" },
];

const BLOCK_STATUS_FILTERS: ReadonlyArray<{ key: BlockStatusFilter; label: string }> = [
  { key: "all", label: "All Blocks" },
  { key: "active", label: "Active Cooldown" },
  { key: "cleared", label: "Cleared" },
];

export const AttacksView: React.FC<AttacksViewProps> = ({ hidden }) => {
  const activePage = useActivePage();
  const activePlanet = useActivePlanet();
  const atkData = useAtkData();
  const uiPrefs = useUiPrefs();
  const { apiOnline, reconnectAttempts } = useApiStatus();
  const {
    refreshAttacksView,
    syncAllInboxFolders,
    showFeedback,
    apiCommand,
    setUiPref,
  } = useConsoleActions();

  const isHidden = hidden ?? activePage !== "attacks";
  const atkTab = uiPrefs.atkTab === "blocks" ? "blocks" : "battles";

  const [atkSearch, setAtkSearch] = useState<string>("");
  const [outcomeFilter, setOutcomeFilter] = useState<BattleOutcomeFilter>("all");
  const [blockFilter, setBlockFilter] = useState<BlockStatusFilter>("all");
  const [loadingCount, setLoadingCount] = useState<number>(0);
  const [loadingLabel, setLoadingLabel] = useState<string | null>(null);
  const mountedRef = useRef<boolean>(true);
  const activeReqCountRef = useRef<number>(0);

  useEffect(() => {
    mountedRef.current = true;
    return () => {
      mountedRef.current = false;
    };
  }, []);

  const runTrackedRequest = useCallback(async (label: string, fn: () => Promise<unknown>) => {
    activeReqCountRef.current += 1;
    if (mountedRef.current) {
      setLoadingCount(activeReqCountRef.current);
      setLoadingLabel(label);
    }
    try {
      await fn();
    } finally {
      activeReqCountRef.current = Math.max(0, activeReqCountRef.current - 1);
      if (mountedRef.current) {
        setLoadingCount(activeReqCountRef.current);
        if (activeReqCountRef.current === 0) {
          setLoadingLabel(null);
        }
      }
    }
  }, []);

  const isInitialLoading = atkData === null;
  const isRefreshing = loadingCount > 0;
  const showLoading = isInitialLoading || isRefreshing;

  const sum = atkData?.summary || {};
  const rStats = atkData?.raid_stats || {};
  const battles: Array<Record<string, any>> = useMemo(
    () => (Array.isArray(atkData?.battles) ? atkData!.battles : []),
    [atkData]
  );
  const blocks: Array<Record<string, any>> = useMemo(
    () => (Array.isArray(atkData?.blocked_targets) ? atkData!.blocked_targets : []),
    [atkData]
  );
  const raids24 = Math.round(finiteNum(rStats.total_24h, 0));

  const filteredBattles = useMemo(() => {
    const needle = atkSearch.trim().toLowerCase();
    return battles.filter((b) => {
      const outVal = String(b.outcome || "draw").toLowerCase();
      if (outcomeFilter === "win" && outVal !== "win") return false;
      if (outcomeFilter === "loss" && outVal !== "loss") return false;
      if (outcomeFilter === "draw" && outVal !== "draw") return false;
      if (
        outcomeFilter === "debris" &&
        finiteNum(b.debris?.pig_iron, 0) + finiteNum(b.debris?.metal, 0) <= 0
      ) {
        return false;
      }
      if (!needle) return true;
      const hay = [b.coords, b.defender_name, b.subject, outVal, b.msg_date_text]
        .filter(Boolean)
        .join(" ")
        .toLowerCase();
      return hay.includes(needle);
    });
  }, [battles, outcomeFilter, atkSearch]);

  const filteredBlocks = useMemo(() => {
    const needle = atkSearch.trim().toLowerCase();
    return blocks.filter((bt) => {
      const isActive = Boolean(bt.active_block);
      if (blockFilter === "active" && !isActive) return false;
      if (blockFilter === "cleared" && isActive) return false;
      if (!needle) return true;
      const hay = [bt.coords, bt.planet_name, bt.owner, bt.block_reason]
        .filter(Boolean)
        .join(" ")
        .toLowerCase();
      return hay.includes(needle);
    });
  }, [blocks, blockFilter, atkSearch]);

  const battleSortCols = useMemo<
    SortColumnMap<
      Record<string, any>,
      "time" | "target" | "defender" | "outcome" | "rounds" | "loot" | "losses" | "debris"
    >
  >(
    () => ({
      time: {
        accessor: (b) => timestampSortKey(b.ts, b.msg_date_text),
        defaultDirection: "desc",
      },
      target: (b) => coordsSortKey(b.coords),
      defender: (b) => String(b.defender_name || b.subject || "").trim() || null,
      outcome: (b) => String(b.outcome || "draw").toLowerCase(),
      rounds: {
        accessor: (b) => finiteNum(b.rounds, null),
        defaultDirection: "desc",
      },
      loot: {
        accessor: (b) => finiteNum(b.loot?.spice_eq, 0),
        defaultDirection: "desc",
      },
      losses: {
        accessor: (b) =>
          finiteNum(b.attacker_losses?.spice_eq, 0) + finiteNum(b.defender_losses?.spice_eq, 0),
        defaultDirection: "desc",
      },
      debris: {
        accessor: (b) =>
          finiteNum(b.debris?.pig_iron, 0) + finiteNum(b.debris?.metal, 0),
        defaultDirection: "desc",
      },
    }),
    []
  );

  const blockSortCols = useMemo<
    SortColumnMap<
      Record<string, any>,
      "coords" | "owner" | "bashHits" | "cooldown" | "reason"
    >
  >(
    () => ({
      coords: (bt) => coordsSortKey(bt.coords),
      owner: (bt) => [bt.planet_name, bt.owner].filter(Boolean).join(" · ") || null,
      bashHits: {
        accessor: (bt) => finiteNum(bt.bash_hits, 0),
        defaultDirection: "desc",
      },
      cooldown: {
        accessor: (bt) => (bt.active_block ? finiteNum(bt.remaining_seconds, 0) : 0),
        defaultDirection: "desc",
      },
      reason: (bt) => String(bt.block_reason || "").trim() || null,
    }),
    []
  );

  const battleSort = useTableSort(filteredBattles, battleSortCols);
  const blockSort = useTableSort(filteredBlocks, blockSortCols);

  const battlePagination = useTablePagination(
    battleSort.sortedRows,
    `${atkTab}|${outcomeFilter}|${atkSearch}|${battleSort.sortKey ?? ""}|${battleSort.sortDirection ?? ""}`,
    25
  );
  const blockPagination = useTablePagination(
    blockSort.sortedRows,
    `${atkTab}|${blockFilter}|${atkSearch}|${blockSort.sortKey ?? ""}|${blockSort.sortDirection ?? ""}`,
    25
  );

  const activeTabHasRows =
    atkTab === "blocks" ? blocks.length > 0 : battles.length > 0;
  const usePanelLoading = isInitialLoading || (isRefreshing && !activeTabHasRows);

  const handleRefreshAttacks = () => {
    void runTrackedRequest("Loading attack results...", () => refreshAttacksView());
  };

  const handleSyncInbox = () => {
    void runTrackedRequest("Syncing inbox folders & attack results...", () => syncAllInboxFolders());
  };

  const handleSpyBlockTarget = async (coords: string) => {
    try {
      const res = await apiCommand({
        command: "spy",
        target_coords: coords,
        quantity: 1,
        planet_id: activePlanet?.planet_id,
        dry_run: false,
      });
      showFeedback(
        res.message || `Spy probe sent to ${coords}.`,
        res.executed ? "ok" : "warn"
      );
    } catch (err: any) {
      showFeedback(`Spy failed: ${err.message}`, feedbackKindForError(err));
    }
  };

  return (
    <section
      id="view-attacks"
      className="page-view"
      aria-label="Battle reports, plunder, and bash protection status"
      hidden={isHidden}
    >
      <div className="combat-top-cards">
        <div className="kpi-card">
          <span className="kpi-label tabular">Recorded Battles</span>
          <strong className="kpi-value tabular" id="atk-kpi-total">
            {`${fmtInt(sum.total_battles ?? battles.length)} Battles`}
          </strong>
          <span className="kpi-sub tabular" id="atk-kpi-wl">
            {`W ${sum.wins ?? 0} · L ${sum.losses ?? 0} · D ${sum.draws ?? 0}`}
          </span>
        </div>
        <div className="kpi-card">
          <span className="kpi-label tabular">Plundered Resources</span>
          <strong className="kpi-value tabular" id="atk-kpi-loot">
            {`${fmtInt(Math.round(finiteNum(sum.total_loot_spice_eq, 0)))} SpEq`}
          </strong>
          <span className="kpi-sub tabular" id="atk-kpi-loss">
            {`Attacker losses: ${fmtInt(Math.round(finiteNum(sum.total_losses_spice_eq, 0)))} SpEq`}
          </span>
        </div>
        <div className="kpi-card">
          <span className="kpi-label tabular">24h Raid &amp; Bash Status</span>
          <strong className="kpi-value tabular" id="atk-kpi-raids24">
            {`${fmtInt(raids24)} / 24h`}
          </strong>
          <span className="kpi-sub tabular" id="atk-kpi-blocks">
            {atkData
              ? `${fmtInt(sum.active_blocks_count ?? 0)} active bash timeouts`
              : "0 active bash blocks"}
          </span>
        </div>
        <div className="kpi-card">
          <span className="kpi-label tabular">Battle Report Sync</span>
          <div className="combat-jumps">
            <button
              type="button"
              className="btn btn-primary btn-sm"
              id="btn-atk-sync"
              disabled={isRefreshing}
              aria-busy={isRefreshing}
              onClick={handleSyncInbox}
            >
              Sync All Folders
            </button>
            <button
              type="button"
              className="btn btn-secondary btn-sm"
              id="btn-atk-refresh"
              disabled={isRefreshing}
              aria-busy={isRefreshing}
              onClick={handleRefreshAttacks}
            >
              Refresh DB
            </button>
          </div>
        </div>
      </div>

      <article className="hud-window page-fill">
        <div className="window-bar window-bar-wrap">
          <div className="window-title-group">
            <span className="module-code tabular">ATK</span>
            <h2>Combat Reports, Plunder &amp; Bash Timeouts</h2>
            <span className="module-meta tabular" id="atk-meta-count">
              {atkData
                ? `${
                    filteredBattles.length !== battles.length
                      ? `${filteredBattles.length}/${battles.length}`
                      : String(battles.length)
                  } REPORTS · ${
                    filteredBlocks.length !== blocks.length
                      ? `${filteredBlocks.length}/${blocks.length}`
                      : String(blocks.length)
                  } BLOCKS`
                : "LOADING..."}
            </span>
          </div>
          <div className="filter-toolbar">
            <input
              type="search"
              id="atk-search"
              className="table-search-input"
              placeholder="Search coords / defender / reason..."
              aria-label="Search battle reports and blocks"
              value={atkSearch}
              onChange={(e) => setAtkSearch(e.target.value)}
            />
            <div className="tab-group" role="tablist" aria-label="Attack view subtabs">
              <button
                type="button"
                className={`tab-btn${atkTab === "battles" ? " active" : ""}`}
                data-atk-tab="battles"
                onClick={() => setUiPref("atkTab", "battles")}
              >
                Battle Reports
              </button>
              <button
                type="button"
                className={`tab-btn${atkTab === "blocks" ? " active" : ""}`}
                data-atk-tab="blocks"
                onClick={() => setUiPref("atkTab", "blocks")}
              >
                Bash &amp; Server Blocks
              </button>
            </div>
          </div>
        </div>

        <div className="intel-stage">
          {showLoading ? (
            <LoadingIndicator
              id="atk-loading-indicator"
              variant={usePanelLoading ? "panel" : "inline"}
              message={loadingLabel || "Loading attack results..."}
              detail={
                !apiOnline && reconnectAttempts > 0
                  ? "Waiting for Game API connection..."
                  : usePanelLoading
                    ? "Fetching archived battle reports and bash cooldown records from SQLite"
                    : "Refreshing battle reports and bash protection status"
              }
            />
          ) : null}

          <div
            className="intel-pane"
            id="atk-pane-battles"
            hidden={usePanelLoading || atkTab !== "battles"}
          >
          <div className="intel-filter-bar">
            <div className="filter-chips" role="group" aria-label="Battle outcome filters">
              {BATTLE_OUTCOME_FILTERS.map((f) => (
                <button
                  key={f.key}
                  type="button"
                  className={`chip-btn${outcomeFilter === f.key ? " active" : ""}`}
                  data-atk-outcome={f.key}
                  onClick={() => setOutcomeFilter(f.key)}
                >
                  {f.label}
                </button>
              ))}
            </div>
          </div>
          <div className="table-wrap table-scroll">
            <table>
              <thead>
                <tr>
                  <SortableTh
                    label="Time"
                    sortKey="time"
                    activeKey={battleSort.sortKey}
                    direction={battleSort.sortDirection}
                    onSort={battleSort.requestSort}
                  />
                  <SortableTh
                    label="Target"
                    sortKey="target"
                    activeKey={battleSort.sortKey}
                    direction={battleSort.sortDirection}
                    onSort={battleSort.requestSort}
                  />
                  <SortableTh
                    label="Defender"
                    sortKey="defender"
                    activeKey={battleSort.sortKey}
                    direction={battleSort.sortDirection}
                    onSort={battleSort.requestSort}
                  />
                  <SortableTh
                    label="Outcome"
                    sortKey="outcome"
                    activeKey={battleSort.sortKey}
                    direction={battleSort.sortDirection}
                    onSort={battleSort.requestSort}
                  />
                  <SortableTh
                    label="Rounds"
                    sortKey="rounds"
                    activeKey={battleSort.sortKey}
                    direction={battleSort.sortDirection}
                    onSort={battleSort.requestSort}
                    align="right"
                  />
                  <SortableTh
                    label="Plundered Loot"
                    sortKey="loot"
                    activeKey={battleSort.sortKey}
                    direction={battleSort.sortDirection}
                    onSort={battleSort.requestSort}
                  />
                  <SortableTh
                    label="Losses (Atk / Def)"
                    sortKey="losses"
                    activeKey={battleSort.sortKey}
                    direction={battleSort.sortDirection}
                    onSort={battleSort.requestSort}
                  />
                  <SortableTh
                    label="Debris Created"
                    sortKey="debris"
                    activeKey={battleSort.sortKey}
                    direction={battleSort.sortDirection}
                    onSort={battleSort.requestSort}
                  />
                </tr>
              </thead>
              <tbody id="atk-battles-tbody">
                {battlePagination.pagedRows.length === 0 ? (
                  <tr>
                    <td colSpan={8} className="empty-cell">
                      {battles.length > 0
                        ? "No battle reports match the active filter or search query."
                        : "Click Sync All Folders to archive in-game battle reports."}
                    </td>
                  </tr>
                ) : (
                  battlePagination.pagedRows.map((b, idx) => {
                    const outVal = String(b.outcome || "draw").toLowerCase();
                    const outCls =
                      outVal === "win" ? "is-win" : outVal === "loss" ? "is-loss" : "is-draw";
                    const lootSp = Math.round(finiteNum(b.loot?.spice_eq, 0));
                    const atkLoss = Math.round(finiteNum(b.attacker_losses?.spice_eq, 0));
                    const defLoss = Math.round(finiteNum(b.defender_losses?.spice_eq, 0));
                    const dPig = Math.round(finiteNum(b.debris?.pig_iron, 0));
                    const dMet = Math.round(finiteNum(b.debris?.metal, 0));

                    return (
                      <tr key={b.id ?? `${b.coords}-${idx}`}>
                        <td className="tabular">
                          {fmtShortDateTime(b.ts, b.msg_date_text)}
                        </td>
                        <td className="tabular">{String(b.coords || "—")}</td>
                        <td>{String(b.defender_name || b.subject || "Unknown")}</td>
                        <td>
                          <span className={`outcome-badge ${outCls}`}>
                            {outVal.toUpperCase()}
                          </span>
                        </td>
                        <td className="col-right tabular">
                          {String(Math.round(finiteNum(b.rounds, 0)))}
                        </td>
                        <td className="tabular">
                          {lootSp > 0
                            ? `${formatResourcesInline(b.loot)} (+${fmtInt(lootSp)} SpEq)`
                            : "0"}
                        </td>
                        <td className="tabular">
                          {`Atk ${fmtInt(atkLoss)} / Def ${fmtInt(defLoss)} SpEq`}
                        </td>
                        <td className="tabular">
                          {dPig > 0 || dMet > 0
                            ? `${fmtInt(dPig)} Pig · ${fmtInt(dMet)} Met`
                            : "None"}
                        </td>
                      </tr>
                    );
                  })
                )}
              </tbody>
            </table>
          </div>
          <TablePagination
            idPrefix="atk-battles"
            totalItems={battlePagination.totalItems}
            page={battlePagination.page}
            pageSize={battlePagination.pageSize}
            itemLabel="reports"
            onPageChange={battlePagination.setPage}
            onPageSizeChange={battlePagination.setPageSize}
          />
        </div>

        <div
          className="intel-pane"
          id="atk-pane-blocks"
          hidden={usePanelLoading || atkTab !== "blocks"}
        >
          <div className="intel-filter-bar">
            <div className="filter-chips" role="group" aria-label="Block status filters">
              {BLOCK_STATUS_FILTERS.map((f) => (
                <button
                  key={f.key}
                  type="button"
                  className={`chip-btn${blockFilter === f.key ? " active" : ""}`}
                  data-atk-block-filter={f.key}
                  onClick={() => setBlockFilter(f.key)}
                >
                  {f.label}
                </button>
              ))}
            </div>
          </div>
          <div className="table-wrap table-scroll">
            <table>
              <thead>
                <tr>
                  <SortableTh
                    label="Coords"
                    sortKey="coords"
                    activeKey={blockSort.sortKey}
                    direction={blockSort.sortDirection}
                    onSort={blockSort.requestSort}
                  />
                  <SortableTh
                    label="Owner / Planet"
                    sortKey="owner"
                    activeKey={blockSort.sortKey}
                    direction={blockSort.sortDirection}
                    onSort={blockSort.requestSort}
                  />
                  <SortableTh
                    label="Bash Hits"
                    sortKey="bashHits"
                    activeKey={blockSort.sortKey}
                    direction={blockSort.sortDirection}
                    onSort={blockSort.requestSort}
                    align="right"
                  />
                  <SortableTh
                    label="Cooldown Left"
                    sortKey="cooldown"
                    activeKey={blockSort.sortKey}
                    direction={blockSort.sortDirection}
                    onSort={blockSort.requestSort}
                    align="right"
                  />
                  <SortableTh
                    label="Server Protection Reason"
                    sortKey="reason"
                    activeKey={blockSort.sortKey}
                    direction={blockSort.sortDirection}
                    onSort={blockSort.requestSort}
                  />
                  <th className="col-right">Ops</th>
                </tr>
              </thead>
              <tbody id="atk-blocks-tbody">
                {blockPagination.pagedRows.length === 0 ? (
                  <tr>
                    <td colSpan={6} className="empty-cell">
                      {blocks.length > 0
                        ? "No bash or server blocks match the active filter or search query."
                        : "No active bash protection or server blocks recorded."}
                    </td>
                  </tr>
                ) : (
                  blockPagination.pagedRows.map((bt, idx) => {
                    const coords = String(bt.coords || "—");
                    return (
                      <tr key={`${coords}-${idx}`}>
                        <td className="tabular">{coords}</td>
                        <td>
                          {[bt.planet_name, bt.owner].filter(Boolean).join(" · ") || "Unknown"}
                        </td>
                        <td className="col-right tabular">
                          {String(Math.round(finiteNum(bt.bash_hits, 0)))}
                        </td>
                        <td className="col-right tabular">
                          {bt.active_block
                            ? fmtDurationCountdown(bt.remaining_seconds)
                            : "Cleared"}
                        </td>
                        <td>{String(bt.block_reason || "Bash / Server protection")}</td>
                        <td className="col-right">
                          <button
                            type="button"
                            className="btn btn-secondary btn-sm"
                            onClick={() => handleSpyBlockTarget(coords)}
                          >
                            Spy
                          </button>
                        </td>
                      </tr>
                    );
                  })
                )}
              </tbody>
            </table>
          </div>
          <TablePagination
            idPrefix="atk-blocks"
            totalItems={blockPagination.totalItems}
            page={blockPagination.page}
            pageSize={blockPagination.pageSize}
            itemLabel="blocks"
            onPageChange={blockPagination.setPage}
            onPageSizeChange={blockPagination.setPageSize}
          />
        </div>
        </div>
      </article>
    </section>
  );
};

export default AttacksView;
