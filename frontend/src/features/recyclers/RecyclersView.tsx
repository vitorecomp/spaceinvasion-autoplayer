import React, { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { feedbackKindForError } from "../../api/httpClient";
import { LoadingIndicator } from "../../components/LoadingIndicator";
import {
  useActivePage,
  useActivePlanet,
  useApiStatus,
  useConsoleActions,
  useRecData,
  useUiPrefs,
} from "../../store";
import { finiteNum, fmtInt } from "../../utils/formatters";
import { RecyclersDebrisPane } from "./RecyclersDebrisPane";
import { RecyclersEmailsPane } from "./RecyclersEmailsPane";
import { RecyclersHarvestsPane } from "./RecyclersHarvestsPane";

export interface RecyclersViewProps {
  hidden?: boolean;
}

export const RecyclersView: React.FC<RecyclersViewProps> = ({ hidden }) => {
  const activePage = useActivePage();
  const activePlanet = useActivePlanet();
  const recData = useRecData();
  const uiPrefs = useUiPrefs();
  const { apiOnline, reconnectAttempts } = useApiStatus();
  const {
    refreshRecyclersView,
    syncAllInboxFolders,
    showFeedback,
    apiCommand,
    setUiPref,
  } = useConsoleActions();

  const isHidden = hidden ?? activePage !== "recyclers";
  const recTab =
    uiPrefs.recTab === "debris" || uiPrefs.recTab === "emails"
      ? uiPrefs.recTab
      : "harvests";
  const emailCat = uiPrefs.emailCatFilter || "all";

  const [recSearch, setRecSearch] = useState<string>("");
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

  const isInitialLoading = recData === null;
  const isRefreshing = loadingCount > 0;
  const showLoading = isInitialLoading || isRefreshing;

  const sum = recData?.summary || {};
  const recycles: Array<Record<string, any>> = useMemo(
    () => (Array.isArray(recData?.recycles) ? recData!.recycles : []),
    [recData]
  );
  const debris: Array<Record<string, any>> = useMemo(
    () => (Array.isArray(recData?.debris_targets) ? recData!.debris_targets : []),
    [recData]
  );
  const emails: Array<Record<string, any>> = useMemo(
    () => (Array.isArray(recData?.other_emails) ? recData!.other_emails : []),
    [recData]
  );
  const cats = sum.category_counts || {};

  const filteredRecycles = useMemo(() => {
    const needle = recSearch.trim().toLowerCase();
    if (!needle) return recycles;
    return recycles.filter((r) => {
      const hay = [r.coords, r.msg_date_text].filter(Boolean).join(" ").toLowerCase();
      return hay.includes(needle);
    });
  }, [recycles, recSearch]);

  const filteredDebris = useMemo(() => {
    const needle = recSearch.trim().toLowerCase();
    if (!needle) return debris;
    return debris.filter((d) => {
      const hay = [d.coords, d.planet_name, d.owner].filter(Boolean).join(" ").toLowerCase();
      return hay.includes(needle);
    });
  }, [debris, recSearch]);

  const filteredEmails = useMemo(() => {
    const needle = recSearch.trim().toLowerCase();
    if (!needle) return emails;
    return emails.filter((m) => {
      const hay = [
        m.coords,
        m.folder_type,
        m.category,
        m.sender,
        m.subject,
        m.body_text,
        m.msg_date_text,
      ]
        .filter(Boolean)
        .join(" ")
        .toLowerCase();
      return hay.includes(needle);
    });
  }, [emails, recSearch]);

  const tPig = Math.round(finiteNum(sum.total_pig_iron, 0));
  const tMet = Math.round(finiteNum(sum.total_metal, 0));
  const tKry = Math.round(finiteNum(sum.total_kryptonite, 0));
  const tSpi = Math.round(finiteNum(sum.total_spice, 0));

  const activeTabHasRows =
    recTab === "debris"
      ? debris.length > 0
      : recTab === "emails"
        ? emails.length > 0
        : recycles.length > 0;
  const usePanelLoading = isInitialLoading || (isRefreshing && !activeTabHasRows);
  const rawKpiText =
    tKry > 0 || tSpi > 0
      ? `${fmtInt(tPig)} Pig · ${fmtInt(tMet)} Met · ${fmtInt(tKry)} Kry · ${fmtInt(tSpi)} Spi`
      : `${fmtInt(tPig)} Pig · ${fmtInt(tMet)} Met`;

  const userCnt = Math.round(finiteNum(cats.general, 0));
  const retCnt = Math.round(finiteNum(cats.fleet_return, 0));

  const handleRefreshRecyclers = () => {
    void runTrackedRequest("Loading recycler reports...", () => refreshRecyclersView());
  };

  const handleSyncInbox = () => {
    void runTrackedRequest("Syncing inbox folders & recycler reports...", () =>
      syncAllInboxFolders()
    );
  };

  const handleSelectEmailCat = (cat: string) => {
    setUiPref("emailCatFilter", cat);
    void runTrackedRequest("Loading recycler reports & emails...", () =>
      refreshRecyclersView(cat)
    );
  };

  const handleRecycleDebris = async (coords: string) => {
    try {
      showFeedback(`Dispatching Recyclers (s11) to ${coords}...`, "ok");
      const res = await apiCommand({
        command: "recycle",
        target_coords: coords,
        planet_id: activePlanet?.planet_id,
        dry_run: false,
      });
      showFeedback(
        res.message || `Recycler dispatched to ${coords}.`,
        res.executed ? "ok" : "warn"
      );
      await runTrackedRequest("Refreshing recycler reports...", () => refreshRecyclersView());
    } catch (err: any) {
      showFeedback(`Recycle failed: ${err.message}`, feedbackKindForError(err));
    }
  };

  return (
    <section
      id="view-recyclers"
      className="page-view"
      aria-label="Recycler harvests, floating debris, and archived emails"
      hidden={isHidden}
    >
      <div className="combat-top-cards">
        <div className="kpi-card">
          <span className="kpi-label tabular">Recycler Runs</span>
          <strong className="kpi-value tabular" id="rec-kpi-runs">
            {`${fmtInt(sum.total_runs ?? recycles.length)} Harvests`}
          </strong>
          <span className="kpi-sub tabular" id="rec-kpi-ships">
            {`${fmtInt(sum.recyclers_owned ?? 0)} Recyclers (s11) owned`}
          </span>
        </div>
        <div className="kpi-card">
          <span className="kpi-label tabular">Total Rubble Collected</span>
          <strong className="kpi-value tabular" id="rec-kpi-speq">
            {`${fmtInt(Math.round(finiteNum(sum.total_spice_eq, 0)))} SpEq`}
          </strong>
          <span className="kpi-sub tabular" id="rec-kpi-raw">
            {rawKpiText}
          </span>
        </div>
        <div className="kpi-card">
          <span className="kpi-label tabular">Active Debris Fields</span>
          <strong className="kpi-value tabular" id="rec-kpi-debris">
            {`${fmtInt(sum.active_debris_count ?? debris.length)} Fields`}
          </strong>
          <span className="kpi-sub tabular" id="rec-kpi-debris-note">
            Ready for s11 harvest
          </span>
        </div>
        <div className="kpi-card">
          <span className="kpi-label tabular">Other Emails &amp; Sync</span>
          <strong className="kpi-value tabular" id="rec-kpi-emails">
            {recData
              ? `${fmtInt(sum.other_email_count ?? emails.length)} Notices`
              : "0 Emails"}
          </strong>
          <div className="combat-jumps">
            <span className="kpi-sub tabular" id="rec-kpi-cats">
              {recData ? `User ${userCnt} · Return ${retCnt}` : "User 0 · Fleet 0"}
            </span>
            <button
              type="button"
              className="btn btn-primary btn-sm"
              id="btn-rec-sync"
              data-testid="btn-sync-all-messages"
              disabled={isRefreshing}
              aria-busy={isRefreshing}
              onClick={handleSyncInbox}
            >
              Sync All Folders
            </button>
            <button
              type="button"
              className="btn btn-secondary btn-sm"
              id="btn-rec-refresh"
              disabled={isRefreshing}
              aria-busy={isRefreshing}
              onClick={handleRefreshRecyclers}
            >
              Refresh DB
            </button>
          </div>
        </div>
      </div>

      <article className="hud-window page-fill">
        <div className="window-bar window-bar-wrap">
          <div className="window-title-group">
            <span className="module-code tabular">REC</span>
            <h2>Recycler Harvests, Debris &amp; Inbox Emails</h2>
            <span className="module-meta tabular" id="rec-meta-count">
              {recData
                ? `${
                    filteredRecycles.length !== recycles.length
                      ? `${filteredRecycles.length}/${recycles.length}`
                      : String(recycles.length)
                  } HARVESTS · ${
                    filteredDebris.length !== debris.length
                      ? `${filteredDebris.length}/${debris.length}`
                      : String(debris.length)
                  } DEBRIS · ${
                    filteredEmails.length !== emails.length
                      ? `${filteredEmails.length}/${emails.length}`
                      : String(emails.length)
                  } EMAILS`
                : "LOADING..."}
            </span>
          </div>
          <div className="filter-toolbar">
            <input
              type="search"
              id="rec-search"
              className="table-search-input"
              placeholder="Search coords / owner / subject..."
              aria-label="Search recycler harvests, debris, and emails"
              value={recSearch}
              onChange={(e) => setRecSearch(e.target.value)}
            />
            <div className="tab-group" role="tablist" aria-label="Recycler and email subtabs">
              <button
                type="button"
                className={`tab-btn${recTab === "harvests" ? " active" : ""}`}
                data-rec-tab="harvests"
                onClick={() => setUiPref("recTab", "harvests")}
              >
                Recycler Reports
              </button>
              <button
                type="button"
                className={`tab-btn${recTab === "debris" ? " active" : ""}`}
                data-rec-tab="debris"
                onClick={() => setUiPref("recTab", "debris")}
              >
                Active Debris
              </button>
              <button
                type="button"
                className={`tab-btn${recTab === "emails" ? " active" : ""}`}
                data-rec-tab="emails"
                onClick={() => setUiPref("recTab", "emails")}
              >
                Other Emails &amp; Fleet
              </button>
            </div>
          </div>
        </div>

        <div className="intel-stage">
          {showLoading ? (
            <LoadingIndicator
              id="rec-loading-indicator"
              variant={usePanelLoading ? "panel" : "inline"}
              message={loadingLabel || "Loading recycler reports..."}
              detail={
                !apiOnline && reconnectAttempts > 0
                  ? "Waiting for Game API connection..."
                  : usePanelLoading
                    ? "Fetching recycler harvests, orbital debris fields, and inbox emails from SQLite"
                    : "Refreshing recycler harvests, active debris, and inbox archive"
              }
            />
          ) : null}

          <RecyclersHarvestsPane
            filteredRecycles={filteredRecycles}
            totalRecyclesCount={recycles.length}
            recTab={recTab}
            recSearch={recSearch}
            hidden={usePanelLoading || recTab !== "harvests"}
          />

          <RecyclersDebrisPane
            filteredDebris={filteredDebris}
            totalDebrisCount={debris.length}
            recTab={recTab}
            recSearch={recSearch}
            hidden={usePanelLoading || recTab !== "debris"}
            onRecycleDebris={handleRecycleDebris}
          />

          <RecyclersEmailsPane
            filteredEmails={filteredEmails}
            totalEmailsCount={emails.length}
            emailCat={emailCat}
            onSelectEmailCat={handleSelectEmailCat}
            recTab={recTab}
            recSearch={recSearch}
            hidden={usePanelLoading || recTab !== "emails"}
          />
        </div>
      </article>
    </section>
  );
};

export default RecyclersView;
