import React, { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { feedbackKindForError } from "../../api/httpClient";
import { LoadingIndicator } from "../../components/LoadingIndicator";
import {
  useActivePage,
  useActivePlanet,
  useApiStatus,
  useAtkData,
  useConsoleActions,
  useUiPrefs,
} from "../../store";
import { finiteNum, fmtInt } from "../../utils/formatters";
import { AttacksBattlesPane, type BattleOutcomeFilter } from "./AttacksBattlesPane";
import { AttacksBlocksPane, type BlockStatusFilter } from "./AttacksBlocksPane";

export interface AttacksViewProps {
  hidden?: boolean;
}

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

          <AttacksBattlesPane
            filteredBattles={filteredBattles}
            totalBattlesCount={battles.length}
            outcomeFilter={outcomeFilter}
            onOutcomeFilterChange={setOutcomeFilter}
            atkTab={atkTab}
            atkSearch={atkSearch}
            hidden={usePanelLoading || atkTab !== "battles"}
          />

          <AttacksBlocksPane
            filteredBlocks={filteredBlocks}
            totalBlocksCount={blocks.length}
            blockFilter={blockFilter}
            onBlockFilterChange={setBlockFilter}
            atkTab={atkTab}
            atkSearch={atkSearch}
            hidden={usePanelLoading || atkTab !== "blocks"}
            onSpyBlockTarget={handleSpyBlockTarget}
          />
        </div>
      </article>
    </section>
  );
};

export default AttacksView;
