import React, { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { feedbackKindForError } from "../../api/httpClient";
import { LoadingIndicator } from "../../components/LoadingIndicator";
import {
  useActivePage,
  useActivePlanet,
  useApiStatus,
  useConsoleActions,
  useDashboard,
  useEspData,
  useUiPrefs,
} from "../../store";
import { finiteNum, fmtInt } from "../../utils/formatters";
import { dispatchPopulateSimEvent } from "../combat/simEvents";
import { type TargetFilterKey } from "./espionageHelpers";
import { EspionageReportsPane } from "./EspionageReportsPane";
import { EspionageTargetsPane } from "./EspionageTargetsPane";

export { espionageDistanceSortKey, formatEspionageDistance } from "./espionageHelpers";

export interface EspionageViewProps {
  hidden?: boolean;
}

export const EspionageView: React.FC<EspionageViewProps> = ({ hidden }) => {
  const dashboard = useDashboard();
  const activePage = useActivePage();
  const activePlanet = useActivePlanet();
  const espData = useEspData();
  const uiPrefs = useUiPrefs();
  const { apiOnline, reconnectAttempts } = useApiStatus();
  const {
    setActivePage,
    refreshEspionageView,
    syncAllInboxFolders,
    showFeedback,
    apiCommand,
    loadDashboard,
    setUiPref,
  } = useConsoleActions();

  const isHidden = hidden ?? activePage !== "espionage";
  const espTab = uiPrefs.espTab === "reports" ? "reports" : "targets";

  const [espSearch, setEspSearch] = useState<string>("");
  const [targetFilter, setTargetFilter] = useState<TargetFilterKey>("all");
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

  const isInitialLoading = espData === null;
  const isRefreshing = loadingCount > 0;
  const showLoading = isInitialLoading || isRefreshing;

  const sum = espData?.summary || {};
  const originCoords = String(
    espData?.origin_coords || activePlanet?.coords || dashboard?.state?.planets?.[0]?.coords || ""
  ).trim();
  const targets: Array<Record<string, any>> = useMemo(
    () => (Array.isArray(espData?.targets) ? espData!.targets : []),
    [espData]
  );
  const reports: Array<Record<string, any>> = useMemo(
    () => (Array.isArray(espData?.reports) ? espData!.reports : []),
    [espData]
  );

  const filteredTargets = useMemo(() => {
    const needle = espSearch.trim().toLowerCase();
    return targets.filter((t) => {
      const isSpacia = Boolean(t.is_spacia);
      const isInact = Boolean(t.is_inactive) || String(t.status_color || "") === "yellow";
      if (targetFilter === "spied" && !t.has_spy_intel) return false;
      if (targetFilter === "spacia" && !isSpacia) return false;
      if (targetFilter === "inactive" && !isInact) return false;
      if (targetFilter === "debris" && !t.has_debris) return false;
      if (!needle) return true;
      const hay = [
        t.coords,
        t.target_coords,
        t.planet_name,
        t.owner,
        t.status_color,
        isSpacia ? "spacia" : "",
        isInact ? "inactive" : "",
        t.has_debris ? "debris" : "",
        t.dug_in ? "dug-in" : "",
      ]
        .filter(Boolean)
        .join(" ")
        .toLowerCase();
      return hay.includes(needle);
    });
  }, [targets, targetFilter, espSearch]);

  const filteredReports = useMemo(() => {
    const needle = espSearch.trim().toLowerCase();
    if (!needle) return reports;
    return reports.filter((r) => {
      const hay = [r.coords, r.target_coords, r.planet_name, r.owner, r.msg_date_text]
        .filter(Boolean)
        .join(" ")
        .toLowerCase();
      return hay.includes(needle);
    });
  }, [reports, espSearch]);

  const activeTabHasRows =
    espTab === "reports" ? reports.length > 0 : targets.length > 0;
  const usePanelLoading = isInitialLoading || (isRefreshing && !activeTabHasRows);

  const bestSpEq = targets.reduce((acc, t) => Math.max(acc, finiteNum(t?.spice_eq, 0)), 0);
  const galSweep = dashboard?.galaxy_sweep || null;
  const galTotal = Math.max(0, Math.round(finiteNum(galSweep?.total_systems, 0)));
  const galExplored = Math.max(0, Math.round(finiteNum(galSweep?.explored_systems, 0)));
  const galPct =
    galSweep && galSweep.coverage_pct != null
      ? Math.max(0, Math.min(100, finiteNum(galSweep.coverage_pct, 0)))
      : galTotal > 0
        ? Math.max(0, Math.min(100, (galExplored / galTotal) * 100))
        : null;
  const uniTotal = Math.max(1, Math.round(finiteNum(galSweep?.universe_total_systems, 5600)));
  const uniExplored = Math.max(
    0,
    Math.round(finiteNum(galSweep?.universe_explored_systems, galExplored))
  );
  const uniPct =
    galSweep && galSweep.universe_coverage_pct != null
      ? Math.max(0, Math.min(100, finiteNum(galSweep.universe_coverage_pct, 0)))
      : galPct !== null
        ? Math.max(0, Math.min(100, (uniExplored / uniTotal) * 100))
        : null;
  const galPhaseText =
    String(galSweep?.scan_phase || "").toLowerCase() === "targeted_refresh"
      ? "Targeted Refresh"
      : "Exploratory";

  const handleRefreshEspionage = () => {
    void runTrackedRequest("Loading espionage intel...", () => refreshEspionageView());
  };

  const handleSyncInbox = () => {
    void runTrackedRequest("Syncing inbox folders & espionage intel...", () =>
      syncAllInboxFolders()
    );
  };

  const handleSpyTarget = async (coords: string) => {
    try {
      showFeedback(`Dispatching spy probe to ${coords}...`, "ok");
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
      await runTrackedRequest("Refreshing espionage intel...", () => refreshEspionageView());
    } catch (err: any) {
      showFeedback(`Spy failed: ${err.message}`, feedbackKindForError(err));
    }
  };

  const handleSimTarget = (target: Record<string, any>) => {
    const resMap = target.resources || {};
    const totalLoot =
      finiteNum(resMap.pig_iron, 0) +
      finiteNum(resMap.metal, 0) +
      finiteNum(resMap.kryptonite, 0) +
      finiteNum(resMap.spice, 0);
    setActivePage("combat");
    dispatchPopulateSimEvent({
      defense: target.defense || {},
      fleet: target.fleet || {},
      totalLoot,
    });
  };

  const handleAttackTarget = async (coords: string) => {
    try {
      showFeedback(`Dispatching raid to ${coords}...`, "ok");
      const res = await apiCommand({
        command: "attack",
        target_coords: coords,
        planet_id: activePlanet?.planet_id,
        dry_run: false,
      });
      showFeedback(
        res.message || `Attack processed for ${coords}.`,
        res.executed ? "ok" : "warn"
      );
      await runTrackedRequest("Refreshing espionage intel...", async () => {
        await loadDashboard({ refresh: false, force: true });
        await refreshEspionageView();
      });
    } catch (err: any) {
      showFeedback(`Attack failed: ${err.message}`, feedbackKindForError(err));
    }
  };

  const handleNavigateMessages = async () => {
    try {
      await apiCommand({ command: "navigate", action: "internalMessages" });
      showFeedback("Chrome switched to internalMessages.", "ok");
    } catch (err: any) {
      showFeedback(`Navigation failed: ${err.message}`, feedbackKindForError(err));
    }
  };

  return (
    <section
      id="view-espionage"
      className="page-view"
      aria-label="Espionage targets and spy report archive"
      hidden={isHidden}
    >
      <div className="combat-top-cards">
        <div className="kpi-card">
          <span className="kpi-label tabular">Spied Targets</span>
          <strong className="kpi-value tabular" id="esp-kpi-targets">
            {espData
              ? `${fmtInt(sum.spied_targets ?? 0)} / ${fmtInt(sum.total_targets ?? targets.length)}`
              : "0"}
          </strong>
          <span className="kpi-sub tabular" id="esp-kpi-reports">
            {`${fmtInt(sum.total_reports ?? reports.length)} archived spy reports`}
          </span>
        </div>
        <div className="kpi-card">
          <span className="kpi-label tabular">Spacia &amp; Inactives</span>
          <strong className="kpi-value tabular" id="esp-kpi-spacia">
            {`${fmtInt(sum.spacia_count ?? 0)} Spacia`}
          </strong>
          <span className="kpi-sub tabular" id="esp-kpi-inactives">
            {`${fmtInt(sum.inactive_count ?? 0)} yellow inactive planets`}
          </span>
        </div>
        <div className="kpi-card">
          <span className="kpi-label tabular">Debris &amp; Top Loot</span>
          <strong className="kpi-value tabular" id="esp-kpi-debris">
            {`${fmtInt(sum.debris_count ?? 0)} Debris`}
          </strong>
          <span className="kpi-sub tabular" id="esp-kpi-top-loot">
            {espData
              ? `Peak target: ${fmtInt(Math.round(bestSpEq))} SpEq`
              : "Best loot: 0 SpEq"}
          </span>
        </div>
        <div className="kpi-card">
          <span className="kpi-label tabular">Espionage Sync</span>
          <div className="combat-jumps">
            <button
              type="button"
              className="btn btn-primary btn-sm"
              id="btn-esp-sync"
              data-testid="btn-sync-espionage"
              disabled={isRefreshing}
              aria-busy={isRefreshing}
              onClick={handleSyncInbox}
            >
              Sync All Folders
            </button>
            <button
              type="button"
              className="btn btn-secondary btn-sm"
              id="btn-esp-refresh"
              disabled={isRefreshing}
              aria-busy={isRefreshing}
              onClick={handleRefreshEspionage}
            >
              Refresh DB
            </button>
            <button
              type="button"
              className="btn btn-secondary btn-sm nav-jump"
              data-action="internalMessages"
              onClick={handleNavigateMessages}
            >
              Messages
            </button>
          </div>
        </div>
      </div>

      <article className="hud-window page-fill">
        <div className="window-bar window-bar-wrap">
          <div className="window-title-group">
            <span className="module-code tabular">SPY</span>
            <h2>Espionage Intelligence &amp; Spy Report Archive</h2>
            <span className="module-meta tabular" id="esp-meta-count">
              {espData
                ? `${
                    filteredTargets.length !== targets.length
                      ? `${filteredTargets.length}/${targets.length}`
                      : String(targets.length)
                  } TARGETS · ${
                    filteredReports.length !== reports.length
                      ? `${filteredReports.length}/${reports.length}`
                      : String(reports.length)
                  } REPORTS${originCoords ? ` · FROM ${originCoords}` : ""}`
                : "LOADING..."}
            </span>
            <span
              className="gal-cov-inline tabular"
              id="esp-galaxy-coverage"
              title={
                galPct !== null
                  ? `Galaxy coverage: ${galExplored}/${galTotal} (${galPct.toFixed(1)}%) · Universe coverage: ${uniExplored}/${uniTotal} (${(uniPct ?? 0).toFixed(2)}%) · ${galPhaseText}`
                  : "Galaxy and universe exploration percentage"
              }
            >
              {galPct !== null
                ? `GAL ${galPct.toFixed(1)}% (${galExplored}/${galTotal}) · UNI ${(uniPct ?? 0).toFixed(1)}% (${fmtInt(uniExplored)}/${fmtInt(uniTotal)}) · ${galPhaseText}`
                : "GAL --% · UNI --%"}
            </span>
          </div>
          <div className="filter-toolbar">
            <input
              type="search"
              id="esp-search"
              className="table-search-input"
              placeholder="Search coords / player / status..."
              aria-label="Search espionage targets and reports"
              value={espSearch}
              onChange={(e) => setEspSearch(e.target.value)}
            />
            <div className="tab-group" role="tablist" aria-label="Espionage subtabs">
              <button
                type="button"
                className={`tab-btn${espTab === "targets" ? " active" : ""}`}
                data-esp-tab="targets"
                onClick={() => setUiPref("espTab", "targets")}
              >
                Spied Targets
              </button>
              <button
                type="button"
                className={`tab-btn${espTab === "reports" ? " active" : ""}`}
                data-esp-tab="reports"
                onClick={() => setUiPref("espTab", "reports")}
              >
                Historical Spy Reports
              </button>
            </div>
          </div>
        </div>

        <div className="intel-stage">
          {showLoading ? (
            <LoadingIndicator
              id="esp-loading-indicator"
              variant={usePanelLoading ? "panel" : "inline"}
              message={loadingLabel || "Loading espionage intel..."}
              detail={
                !apiOnline && reconnectAttempts > 0
                  ? "Waiting for Game API connection..."
                  : usePanelLoading
                    ? "Fetching spied targets and historical spy archive from SQLite"
                    : "Refreshing espionage targets and spy reports"
              }
            />
          ) : null}

          <EspionageTargetsPane
            filteredTargets={filteredTargets}
            totalTargetsCount={targets.length}
            originCoords={originCoords}
            targetFilter={targetFilter}
            onTargetFilterChange={setTargetFilter}
            espTab={espTab}
            espSearch={espSearch}
            hidden={usePanelLoading || espTab !== "targets"}
            onSpyTarget={handleSpyTarget}
            onSimTarget={handleSimTarget}
            onAttackTarget={handleAttackTarget}
          />

          <EspionageReportsPane
            filteredReports={filteredReports}
            totalReportsCount={reports.length}
            originCoords={originCoords}
            espTab={espTab}
            espSearch={espSearch}
            hidden={usePanelLoading || espTab !== "reports"}
          />
        </div>
      </article>
    </section>
  );
};

export default EspionageView;
