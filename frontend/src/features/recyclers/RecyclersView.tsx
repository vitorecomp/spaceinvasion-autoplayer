import React from "react";
import { feedbackKindForError } from "../../api/httpClient";
import { useConsole } from "../../context/ConsoleContext";
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

  const sum = recData?.summary || {};
  const recycles: Array<Record<string, any>> = Array.isArray(recData?.recycles)
    ? recData!.recycles
    : [];
  const debris: Array<Record<string, any>> = Array.isArray(recData?.debris_targets)
    ? recData!.debris_targets
    : [];
  const emails: Array<Record<string, any>> = Array.isArray(recData?.other_emails)
    ? recData!.other_emails
    : [];
  const cats = sum.category_counts || {};

  const tPig = Math.round(finiteNum(sum.total_pig_iron, 0));
  const tMet = Math.round(finiteNum(sum.total_metal, 0));
  const tKry = Math.round(finiteNum(sum.total_kryptonite, 0));
  const tSpi = Math.round(finiteNum(sum.total_spice, 0));
  const rawKpiText =
    tKry > 0 || tSpi > 0
      ? `${fmtInt(tPig)} Pig · ${fmtInt(tMet)} Met · ${fmtInt(tKry)} Kry · ${fmtInt(tSpi)} Spi`
      : `${fmtInt(tPig)} Pig · ${fmtInt(tMet)} Met`;

  const userCnt = Math.round(finiteNum(cats.general, 0));
  const retCnt = Math.round(finiteNum(cats.fleet_return, 0));

  const handleSelectEmailCat = (cat: string) => {
    setUiPref("emailCatFilter", cat);
    refreshRecyclersView(cat);
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
      await refreshRecyclersView();
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
              onClick={syncAllInboxFolders}
            >
              Sync All Folders
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
                ? `${recycles.length} HARVESTS · ${debris.length} DEBRIS · ${emails.length} EMAILS`
                : "0 HARVESTS"}
            </span>
          </div>
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

        <div className="intel-pane" id="rec-pane-harvests" hidden={recTab !== "harvests"}>
          <div className="table-wrap table-scroll">
            <table>
              <thead>
                <tr>
                  <th>Time</th>
                  <th>Debris Coords</th>
                  <th className="col-right">Recyclers / Cap</th>
                  <th className="col-right">Pig Iron</th>
                  <th className="col-right">Metal</th>
                  <th className="col-right">Harvest SpEq</th>
                  <th>Field Before</th>
                </tr>
              </thead>
              <tbody id="rec-harvests-tbody">
                {recycles.length === 0 ? (
                  <tr>
                    <td colSpan={7} className="empty-cell">
                      Click Sync All Folders to archive fleet recycling reports.
                    </td>
                  </tr>
                ) : (
                  recycles.map((r, idx) => {
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
        </div>

        <div className="intel-pane" id="rec-pane-debris" hidden={recTab !== "debris"}>
          <div className="table-wrap table-scroll">
            <table>
              <thead>
                <tr>
                  <th>Coords</th>
                  <th>Planet / Owner</th>
                  <th>Status</th>
                  <th className="col-right">Last Seen</th>
                  <th className="col-right">Dispatch</th>
                </tr>
              </thead>
              <tbody id="rec-debris-tbody">
                {debris.length === 0 ? (
                  <tr>
                    <td colSpan={5} className="empty-cell">
                      No active floating debris fields spotted in scanned systems.
                    </td>
                  </tr>
                ) : (
                  debris.map((d, idx) => {
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
        </div>

        <div className="intel-pane" id="rec-pane-emails" hidden={recTab !== "emails"}>
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
                  <th>Time</th>
                  <th>Folder / Type</th>
                  <th>Sender</th>
                  <th>Subject</th>
                  <th>Coords</th>
                  <th>Message Body</th>
                </tr>
              </thead>
              <tbody id="rec-emails-tbody">
                {emails.length === 0 ? (
                  <tr>
                    <td colSpan={6} className="empty-cell">
                      No other emails or fleet notices archived yet.
                    </td>
                  </tr>
                ) : (
                  emails.map((m, idx) => {
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
        </div>
      </article>
    </section>
  );
};

export default RecyclersView;
