import React, { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { feedbackKindForError } from "../../api/httpClient";
import { LoadingIndicator } from "../../components/LoadingIndicator";
import { SortableTh } from "../../components/SortableTh";
import { TablePagination, useTablePagination } from "../../components/TablePagination";
import { useConsole } from "../../context/ConsoleContext";
import {
  coordsSortKey,
  timestampSortKey,
  useTableSort,
  type SortColumnMap,
} from "../../hooks/useTableSort";
import { finiteNum, fmtInt, fmtShortDateTime } from "../../utils/formatters";

export interface RecyclersViewProps {
  hidden?: boolean;
}

const EMAIL_CATS: Array<{ key: string; label: string }> = [
  { key: "all", label: "All Non-Spy/Battle" },
  { key: "general", label: "User / Alliance / Admin" },
  { key: "fleet_return", label: "Fleet Return" },
  { key: "fleet_transport", label: "Transport" },
  { key: "colonize_report", label: "Colonization" },
  { key: "counter_spy", label: "Counter-Spy" },
  { key: "recycle_report", label: "Recycle Raw" },
];

export const RecyclersView: React.FC<RecyclersViewProps> = ({ hidden }) => {
  const {
    apiOnline,
    reconnectAttempts,
    activePage,
    activePlanet,
    recData,
    refreshRecyclersView,
    syncAllInboxFolders,
    showFeedback,
    apiCommand,
    uiPrefs,
    setUiPref,
  } = useConsole();

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

  const recycleSortCols = useMemo<
    SortColumnMap<
      Record<string, any>,
      "time" | "coords" | "recyclers" | "pigIron" | "metal" | "spiceEq" | "fieldBefore"
    >
  >(
    () => ({
      time: {
        accessor: (r) => timestampSortKey(r.ts, r.msg_date_text),
        defaultDirection: "desc",
      },
      coords: (r) => coordsSortKey(r.coords),
      recyclers: {
        accessor: (r) => {
          const c = finiteNum(r.recycler_count, null);
          return c !== null && c > 0 ? c : null;
        },
        defaultDirection: "desc",
      },
      pigIron: {
        accessor: (r) => finiteNum(r.harvested_pig_iron, 0),
        defaultDirection: "desc",
      },
      metal: {
        accessor: (r) => finiteNum(r.harvested_metal, 0),
        defaultDirection: "desc",
      },
      spiceEq: {
        accessor: (r) => finiteNum(r.spice_eq, 0),
        defaultDirection: "desc",
      },
      fieldBefore: {
        accessor: (r) => {
          const extra =
            finiteNum(r.harvested_kryptonite, 0) + finiteNum(r.harvested_spice, 0);
          if (extra > 0) return extra;
          const field = finiteNum(r.field_pig_iron, 0) + finiteNum(r.field_metal, 0);
          return field > 0 ? field : null;
        },
        defaultDirection: "desc",
      },
    }),
    []
  );

  const debrisSortCols = useMemo<
    SortColumnMap<Record<string, any>, "coords" | "owner" | "status" | "lastSeen">
  >(
    () => ({
      coords: (d) => coordsSortKey(d.coords),
      owner: (d) => [d.planet_name, d.owner].filter(Boolean).join(" · ") || "Orbital Debris",
      status: () => "RUBBLE FIELD",
      lastSeen: {
        accessor: (d) => timestampSortKey(d.last_scanned_ts),
        defaultDirection: "desc",
      },
    }),
    []
  );

  const emailSortCols = useMemo<
    SortColumnMap<
      Record<string, any>,
      "time" | "folderType" | "sender" | "subject" | "coords" | "body"
    >
  >(
    () => ({
      time: {
        accessor: (m) => timestampSortKey(m.ts ?? m.ingested_ts, m.msg_date_text),
        defaultDirection: "desc",
      },
      folderType: (m) => `${m.folder_type || "inbox"} · ${m.category || "general"}`,
      sender: (m) => String(m.sender || "System"),
      subject: (m) => String(m.subject || "").trim() || null,
      coords: (m) => coordsSortKey(m.coords),
      body: (m) => String(m.body_text || "").trim() || null,
    }),
    []
  );

  const recycleSort = useTableSort(filteredRecycles, recycleSortCols);
  const debrisSort = useTableSort(filteredDebris, debrisSortCols);
  const emailSort = useTableSort(filteredEmails, emailSortCols);

  const recyclePagination = useTablePagination(
    recycleSort.sortedRows,
    `${recTab}|${recSearch}|${recycleSort.sortKey ?? ""}|${recycleSort.sortDirection ?? ""}`,
    25
  );
  const debrisPagination = useTablePagination(
    debrisSort.sortedRows,
    `${recTab}|${recSearch}|${debrisSort.sortKey ?? ""}|${debrisSort.sortDirection ?? ""}`,
    25
  );
  const emailPagination = useTablePagination(
    emailSort.sortedRows,
    `${recTab}|${emailCat}|${recSearch}|${emailSort.sortKey ?? ""}|${emailSort.sortDirection ?? ""}`,
    25
  );

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

          <div
            className="intel-pane"
            id="rec-pane-harvests"
            hidden={usePanelLoading || recTab !== "harvests"}
          >
          <div className="table-wrap table-scroll">
            <table>
              <thead>
                <tr>
                  <SortableTh
                    label="Time"
                    sortKey="time"
                    activeKey={recycleSort.sortKey}
                    direction={recycleSort.sortDirection}
                    onSort={recycleSort.requestSort}
                  />
                  <SortableTh
                    label="Debris Coords"
                    sortKey="coords"
                    activeKey={recycleSort.sortKey}
                    direction={recycleSort.sortDirection}
                    onSort={recycleSort.requestSort}
                  />
                  <SortableTh
                    label="Recyclers / Cap"
                    sortKey="recyclers"
                    activeKey={recycleSort.sortKey}
                    direction={recycleSort.sortDirection}
                    onSort={recycleSort.requestSort}
                    align="right"
                  />
                  <SortableTh
                    label="Pig Iron"
                    sortKey="pigIron"
                    activeKey={recycleSort.sortKey}
                    direction={recycleSort.sortDirection}
                    onSort={recycleSort.requestSort}
                    align="right"
                  />
                  <SortableTh
                    label="Metal"
                    sortKey="metal"
                    activeKey={recycleSort.sortKey}
                    direction={recycleSort.sortDirection}
                    onSort={recycleSort.requestSort}
                    align="right"
                  />
                  <SortableTh
                    label="Harvest SpEq"
                    sortKey="spiceEq"
                    activeKey={recycleSort.sortKey}
                    direction={recycleSort.sortDirection}
                    onSort={recycleSort.requestSort}
                    align="right"
                  />
                  <SortableTh
                    label="Field Before"
                    sortKey="fieldBefore"
                    activeKey={recycleSort.sortKey}
                    direction={recycleSort.sortDirection}
                    onSort={recycleSort.requestSort}
                  />
                </tr>
              </thead>
              <tbody id="rec-harvests-tbody">
                {recyclePagination.pagedRows.length === 0 ? (
                  <tr>
                    <td colSpan={7} className="empty-cell">
                      {recycles.length > 0
                        ? "No recycler harvests match the active search query."
                        : "Click Sync All Folders to archive fleet recycling reports."}
                    </td>
                  </tr>
                ) : (
                  recyclePagination.pagedRows.map((r, idx) => {
                    const rCnt = Math.round(finiteNum(r.recycler_count, 0));
                    const rCap = Math.round(finiteNum(r.cargo_capacity, 0));
                    const bPig = Math.round(finiteNum(r.field_pig_iron, 0));
                    const bMet = Math.round(finiteNum(r.field_metal, 0));
                    const hKry = Math.round(finiteNum(r.harvested_kryptonite, 0));
                    const hSpi = Math.round(finiteNum(r.harvested_spice, 0));
                    const befText =
                      hKry > 0 || hSpi > 0
                        ? `+${fmtInt(hKry)} Kry · +${fmtInt(hSpi)} Spi`
                        : bPig > 0 || bMet > 0
                          ? `${fmtInt(bPig)} Pig · ${fmtInt(bMet)} Met`
                          : "—";

                    return (
                      <tr key={r.id ?? `${r.coords}-${idx}`}>
                        <td className="tabular">
                          {fmtShortDateTime(r.ts, r.msg_date_text)}
                        </td>
                        <td className="tabular">{String(r.coords || "—")}</td>
                        <td className="col-right tabular">
                          {rCnt > 0 ? `${fmtInt(rCnt)}x (${fmtInt(rCap)})` : "—"}
                        </td>
                        <td className="col-right tabular">
                          {fmtInt(r.harvested_pig_iron ?? 0)}
                        </td>
                        <td className="col-right tabular">
                          {fmtInt(r.harvested_metal ?? 0)}
                        </td>
                        <td className="col-right tabular">
                          {`${fmtInt(Math.round(finiteNum(r.spice_eq, 0)))} SpEq`}
                        </td>
                        <td className="tabular">{befText}</td>
                      </tr>
                    );
                  })
                )}
              </tbody>
            </table>
          </div>
          <TablePagination
            idPrefix="rec-harvests"
            totalItems={recyclePagination.totalItems}
            page={recyclePagination.page}
            pageSize={recyclePagination.pageSize}
            itemLabel="harvests"
            onPageChange={recyclePagination.setPage}
            onPageSizeChange={recyclePagination.setPageSize}
          />
        </div>

        <div
          className="intel-pane"
          id="rec-pane-debris"
          hidden={usePanelLoading || recTab !== "debris"}
        >
          <div className="table-wrap table-scroll">
            <table>
              <thead>
                <tr>
                  <SortableTh
                    label="Coords"
                    sortKey="coords"
                    activeKey={debrisSort.sortKey}
                    direction={debrisSort.sortDirection}
                    onSort={debrisSort.requestSort}
                  />
                  <SortableTh
                    label="Planet / Owner"
                    sortKey="owner"
                    activeKey={debrisSort.sortKey}
                    direction={debrisSort.sortDirection}
                    onSort={debrisSort.requestSort}
                  />
                  <SortableTh
                    label="Status"
                    sortKey="status"
                    activeKey={debrisSort.sortKey}
                    direction={debrisSort.sortDirection}
                    onSort={debrisSort.requestSort}
                  />
                  <SortableTh
                    label="Last Seen"
                    sortKey="lastSeen"
                    activeKey={debrisSort.sortKey}
                    direction={debrisSort.sortDirection}
                    onSort={debrisSort.requestSort}
                    align="right"
                  />
                  <th className="col-right">Dispatch</th>
                </tr>
              </thead>
              <tbody id="rec-debris-tbody">
                {debrisPagination.pagedRows.length === 0 ? (
                  <tr>
                    <td colSpan={5} className="empty-cell">
                      {debris.length > 0
                        ? "No debris fields match the active search query."
                        : "No active floating debris fields spotted in scanned systems."}
                    </td>
                  </tr>
                ) : (
                  debrisPagination.pagedRows.map((d, idx) => {
                    const coords = String(d.coords || "—");
                    return (
                      <tr key={`${coords}-${idx}`}>
                        <td className="tabular">{coords}</td>
                        <td>
                          {[d.planet_name, d.owner].filter(Boolean).join(" · ") ||
                            "Orbital Debris"}
                        </td>
                        <td>
                          <span className="status-word">RUBBLE FIELD</span>
                        </td>
                        <td className="col-right tabular">
                          {fmtShortDateTime(d.last_scanned_ts)}
                        </td>
                        <td className="col-right">
                          <button
                            type="button"
                            className="btn btn-primary btn-sm"
                            onClick={() => handleRecycleDebris(coords)}
                          >
                            Recycle
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
            idPrefix="rec-debris"
            totalItems={debrisPagination.totalItems}
            page={debrisPagination.page}
            pageSize={debrisPagination.pageSize}
            itemLabel="debris fields"
            onPageChange={debrisPagination.setPage}
            onPageSizeChange={debrisPagination.setPageSize}
          />
        </div>

        <div
          className="intel-pane"
          id="rec-pane-emails"
          hidden={usePanelLoading || recTab !== "emails"}
        >
          <div className="email-filter-bar">
            <div className="filter-chips" role="group" aria-label="Email category filters">
              {EMAIL_CATS.map((c) => (
                <button
                  key={c.key}
                  type="button"
                  className={`chip-btn${emailCat === c.key ? " active" : ""}`}
                  data-email-cat={c.key}
                  onClick={() => handleSelectEmailCat(c.key)}
                >
                  {c.label}
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
                    activeKey={emailSort.sortKey}
                    direction={emailSort.sortDirection}
                    onSort={emailSort.requestSort}
                  />
                  <SortableTh
                    label="Folder / Type"
                    sortKey="folderType"
                    activeKey={emailSort.sortKey}
                    direction={emailSort.sortDirection}
                    onSort={emailSort.requestSort}
                  />
                  <SortableTh
                    label="Sender"
                    sortKey="sender"
                    activeKey={emailSort.sortKey}
                    direction={emailSort.sortDirection}
                    onSort={emailSort.requestSort}
                  />
                  <SortableTh
                    label="Subject"
                    sortKey="subject"
                    activeKey={emailSort.sortKey}
                    direction={emailSort.sortDirection}
                    onSort={emailSort.requestSort}
                  />
                  <SortableTh
                    label="Coords"
                    sortKey="coords"
                    activeKey={emailSort.sortKey}
                    direction={emailSort.sortDirection}
                    onSort={emailSort.requestSort}
                  />
                  <SortableTh
                    label="Message Body"
                    sortKey="body"
                    activeKey={emailSort.sortKey}
                    direction={emailSort.sortDirection}
                    onSort={emailSort.requestSort}
                  />
                </tr>
              </thead>
              <tbody id="rec-emails-tbody">
                {emailPagination.pagedRows.length === 0 ? (
                  <tr>
                    <td colSpan={6} className="empty-cell">
                      {emails.length > 0
                        ? "No emails or fleet notices match the active search query."
                        : "No other emails or fleet notices archived yet."}
                    </td>
                  </tr>
                ) : (
                  emailPagination.pagedRows.map((m, idx) => {
                    const rawTxt = String(m.body_text || "").trim();
                    const bodyTxt =
                      rawTxt.length > 260 ? `${rawTxt.slice(0, 260)}...` : rawTxt || "—";
                    return (
                      <tr key={m.msg_uid ?? m.id ?? idx}>
                        <td className="tabular">
                          {fmtShortDateTime(m.ts ?? m.ingested_ts, m.msg_date_text)}
                        </td>
                        <td className="tabular">
                          {`${m.folder_type || "inbox"} · ${m.category || "general"}`}
                        </td>
                        <td>{String(m.sender || "System")}</td>
                        <td>{String(m.subject || "—")}</td>
                        <td className="tabular">{String(m.coords || "—")}</td>
                        <td className="msg-body-cell">{bodyTxt}</td>
                      </tr>
                    );
                  })
                )}
              </tbody>
            </table>
          </div>
          <TablePagination
            idPrefix="rec-emails"
            totalItems={emailPagination.totalItems}
            page={emailPagination.page}
            pageSize={emailPagination.pageSize}
            itemLabel="emails"
            onPageChange={emailPagination.setPage}
            onPageSizeChange={emailPagination.setPageSize}
          />
        </div>
        </div>
      </article>
    </section>
  );
};

export default RecyclersView;
