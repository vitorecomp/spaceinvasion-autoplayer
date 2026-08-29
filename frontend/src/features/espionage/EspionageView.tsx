import React from "react";
import { feedbackKindForError } from "../../api/httpClient";
import { useConsole } from "../../context/ConsoleContext";
import {
  finiteNum,
  fmtInt,
  fmtShortDateTime,
  formatResourcesInline,
  formatUnitDictShort,
} from "../../utils/formatters";
import { dispatchPopulateSimEvent } from "../combat/simEvents";

export interface EspionageViewProps {
  hidden?: boolean;
}

export const EspionageView: React.FC<EspionageViewProps> = ({ hidden }) => {
  const {
    activePage,
    setActivePage,
    activePlanet,
    espData,
    refreshEspionageView,
    syncAllInboxFolders,
    showFeedback,
    apiCommand,
    loadDashboard,
    uiPrefs,
    setUiPref,
  } = useConsole();

  const isHidden = hidden ?? activePage !== "espionage";
  const espTab = uiPrefs.espTab === "reports" ? "reports" : "targets";

  const sum = espData?.summary || {};
  const targets: Array<Record<string, any>> = Array.isArray(espData?.targets)
    ? espData!.targets
    : [];
  const reports: Array<Record<string, any>> = Array.isArray(espData?.reports)
    ? espData!.reports
    : [];

  const bestSpEq = targets.reduce(
    (acc, t) => Math.max(acc, finiteNum(t?.spice_eq, 0)),
    0
  );

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
      await refreshEspionageView();
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
      await loadDashboard({ refresh: false, force: true });
      await refreshEspionageView();
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
              onClick={syncAllInboxFolders}
            >
              Sync All Folders
            </button>
            <button
              type="button"
              className="btn btn-secondary btn-sm"
              id="btn-esp-refresh"
              onClick={refreshEspionageView}
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
                ? `${targets.length} TARGETS · ${reports.length} REPORTS`
                : "0 TARGETS"}
            </span>
          </div>
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

        <div className="intel-pane" id="esp-pane-targets" hidden={espTab !== "targets"}>
          <div className="table-wrap table-scroll">
            <table>
              <thead>
                <tr>
                  <th>Coords</th>
                  <th>Player / Planet</th>
                  <th>Status</th>
                  <th className="col-right">Fields</th>
                  <th>Resources (Pig / Met / Kry / Spi)</th>
                  <th className="col-right">Loot SpEq</th>
                  <th>Fleet / Defense</th>
                  <th className="col-right">Ops</th>
                </tr>
              </thead>
              <tbody id="esp-targets-tbody">
                {targets.length === 0 ? (
                  <tr>
                    <td colSpan={8} className="empty-cell">
                      No espionage targets recorded yet. Run Scan Galaxy or Sync All Folders.
                    </td>
                  </tr>
                ) : (
                  targets.map((t, idx) => {
                    const coords = String(t.coords || t.target_coords || "—");
                    const isSpacia = Boolean(t.is_spacia);
                    const isInact =
                      Boolean(t.is_inactive) || String(t.status_color || "") === "yellow";
                    const tags = [
                      isSpacia
                        ? "SPACIA"
                        : isInact
                          ? "INACTIVE"
                          : String(t.status_color || "ACTIVE").toUpperCase(),
                    ];
                    if (t.has_debris) tags.push("DEBRIS");
                    if (t.dug_in) tags.push("DUG-IN");
                    const uf = finiteNum(t.uninhabited_fields, null);
                    const fStr = formatUnitDictShort(t.fleet, 3);
                    const dStr = formatUnitDictShort(t.defense, 3);
                    const fdTxt = t.has_spy_intel
                      ? fStr === "None" && dStr === "None"
                        ? "Clear (0)"
                        : `F:${fStr} / D:${dStr}`
                      : "—";

                    return (
                      <tr key={`${coords}-${idx}`}>
                        <td className="tabular">{coords}</td>
                        <td>
                          {[t.planet_name, t.owner].filter(Boolean).join(" · ") ||
                            "Uninhabited / Unknown"}
                        </td>
                        <td>
                          <span
                            className={`status-word${isSpacia || isInact ? "" : " is-warn"}`}
                          >
                            {tags.join(" · ")}
                          </span>
                        </td>
                        <td className="col-right tabular">
                          {uf !== null && uf > 0 ? String(Math.round(uf)) : "—"}
                        </td>
                        <td className="tabular">
                          {t.has_spy_intel ? formatResourcesInline(t.resources) : "Not spied yet"}
                        </td>
                        <td className="col-right tabular">
                          {t.has_spy_intel
                            ? fmtInt(Math.round(finiteNum(t.spice_eq, 0)))
                            : "—"}
                        </td>
                        <td className="tabular">{fdTxt}</td>
                        <td className="col-right">
                          <div className="row-actions">
                            <button
                              type="button"
                              className="btn btn-secondary btn-sm"
                              onClick={() => handleSpyTarget(coords)}
                            >
                              Spy
                            </button>
                            <button
                              type="button"
                              className="btn btn-secondary btn-sm"
                              onClick={() => handleSimTarget(t)}
                            >
                              Sim
                            </button>
                            <button
                              type="button"
                              className="btn btn-primary btn-sm"
                              onClick={() => handleAttackTarget(coords)}
                            >
                              Attack
                            </button>
                          </div>
                        </td>
                      </tr>
                    );
                  })
                )}
              </tbody>
            </table>
          </div>
        </div>

        <div className="intel-pane" id="esp-pane-reports" hidden={espTab !== "reports"}>
          <div className="table-wrap table-scroll">
            <table>
              <thead>
                <tr>
                  <th>Recorded</th>
                  <th>Coords</th>
                  <th>Owner / Planet</th>
                  <th className="col-right">Fields</th>
                  <th>Resources &amp; SpEq</th>
                  <th>Buildings / Techs</th>
                  <th>Fleet / Defense</th>
                </tr>
              </thead>
              <tbody id="esp-reports-tbody">
                {reports.length === 0 ? (
                  <tr>
                    <td colSpan={7} className="empty-cell">
                      No historical spy reports stored yet.
                    </td>
                  </tr>
                ) : (
                  reports.map((r, idx) => {
                    const uf = finiteNum(r.uninhabited_fields, null);
                    const spEq = Math.round(finiteNum(r.spice_eq ?? r.loot_spice_eq, 0));
                    const bStr = formatUnitDictShort(r.buildings, 3);
                    const rStr = formatUnitDictShort(r.researches, 3);
                    const fStr = formatUnitDictShort(r.fleet, 3);
                    const dStr = formatUnitDictShort(r.defense, 3);
                    return (
                      <tr key={r.id ?? `${r.coords}-${idx}`}>
                        <td className="tabular">
                          {fmtShortDateTime(r.ts, r.msg_date_text)}
                        </td>
                        <td className="tabular">{String(r.coords || "—")}</td>
                        <td>
                          {[r.planet_name, r.owner].filter(Boolean).join(" · ") ||
                            (r.uninhabited_fields ? "Uninhabited Slot" : "Unknown")}
                        </td>
                        <td className="col-right tabular">
                          {uf !== null && uf > 0 ? `${Math.round(uf)} fields` : "—"}
                        </td>
                        <td className="tabular">
                          {`${formatResourcesInline(r.resources)} (${fmtInt(spEq)} SpEq)`}
                        </td>
                        <td className="tabular">
                          {bStr === "None" && rStr === "None" ? "—" : `B:${bStr} · R:${rStr}`}
                        </td>
                        <td className="tabular">
                          {fStr === "None" && dStr === "None"
                            ? "Clear (0)"
                            : `F:${fStr} · D:${dStr}`}
                        </td>
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

export default EspionageView;
