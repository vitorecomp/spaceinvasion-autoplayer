import React, { useEffect, useMemo, useState } from "react";
import { feedbackKindForError } from "../../api/httpClient";
import { useConsole } from "../../context/ConsoleContext";
import {
  ColonyAttemptCoordStats,
  ColonyAttemptItem,
  ColonyAttemptsSummary,
} from "../../types/api";
import { finiteNum, fmtInt, fmtShortDateTime, gradeClassFor } from "../../utils/formatters";

export interface ColoniesViewProps {
  hidden?: boolean;
}

export interface AttemptOutcome {
  label: string;
  cls: string;
}

/** Map a `colony_attempts.event_type` to a short outcome pill label + badge modifier. */
export function attemptOutcomeFor(eventType: unknown, minFields: number): AttemptOutcome {
  const ev = String(eventType ?? "").trim().toLowerCase();
  if (ev.includes("delet") || ev.includes("abandon") || ev.includes("undersized")) {
    return { label: `DELETED <${minFields}`, cls: "is-loss" };
  }
  if (ev.includes("kept") || ev.includes("keep")) {
    return { label: `KEPT ≥${minFields}`, cls: "is-win" };
  }
  if (
    ev.includes("fail") ||
    ev.includes("reject") ||
    ev.includes("abort") ||
    ev.includes("block") ||
    ev.includes("error")
  ) {
    return { label: "FAILED", cls: "is-loss" };
  }
  if (ev.includes("settl")) {
    return { label: "SETTLED", cls: "is-draw" };
  }
  if (ev.includes("dispatch") || ev.includes("launch") || ev.includes("sent")) {
    return { label: "DISPATCHED", cls: "is-pending" };
  }
  return { label: ev ? ev.toUpperCase().replace(/_/g, " ") : "UNKNOWN", cls: "is-pending" };
}

function attemptShortTag(stats: ColonyAttemptCoordStats, minFields: number): string {
  const tried = Math.max(0, Math.round(finiteNum(stats.attempts_count, 0)));
  const lastFields = finiteNum(stats.last_fields_rolled, null);
  const outcome = attemptOutcomeFor(stats.last_event_type, minFields);
  const lastBits: string[] = [];
  if (lastFields !== null && lastFields > 0) lastBits.push(`${Math.round(lastFields)}f`);
  if (stats.last_event_type) lastBits.push(outcome.label.split(" ")[0].toLowerCase());
  return lastBits.length > 0 ? `Tried ${tried}x · Last: ${lastBits.join(" ")}` : `Tried ${tried}x`;
}

export const ColoniesView: React.FC<ColoniesViewProps> = ({ hidden }) => {
  const {
    dashboard,
    activePage,
    activePlanet,
    colonyData,
    refreshColoniesView,
    syncAllInboxFolders,
    showFeedback,
    apiCommand,
    loadDashboard,
    uiPrefs,
    setUiPref,
  } = useConsole();

  const isHidden = hidden ?? activePage !== "colonies";
  const colFilter = uiPrefs.colonyFilter || "all";

  const [minFieldsInput, setMinFieldsInput] = useState<string>("300");
  const [allowColonizeInput, setAllowColonizeInput] = useState<boolean>(false);

  useEffect(() => {
    const cfgMin = colonyData?.min_colony_fields ?? dashboard?.config?.min_colony_fields;
    if (cfgMin != null) {
      setMinFieldsInput(String(cfgMin));
    }
    const cfgAllow = colonyData?.allow_colonize ?? dashboard?.config?.allow_colonize;
    if (cfgAllow != null) {
      setAllowColonizeInput(Boolean(cfgAllow));
    }
  }, [
    colonyData?.allow_colonize,
    colonyData?.min_colony_fields,
    dashboard?.config?.allow_colonize,
    dashboard?.config?.min_colony_fields,
  ]);

  const gc = colonyData?.grade_counts || {};
  const allCands: Array<Record<string, any>> = Array.isArray(colonyData?.candidates)
    ? colonyData!.candidates
    : [];

  const filteredCands = useMemo(() => {
    if (colFilter === "probed") {
      return allCands.filter((c) => Boolean(c.probed));
    }
    if (colFilter === "qualified") {
      return allCands.filter((c) => Boolean(c.meets_target));
    }
    if (colFilter === "unprobed") {
      return allCands.filter((c) => !c.probed);
    }
    return allCands;
  }, [allCands, colFilter]);

  const minFieldsTarget = Math.max(
    1,
    Math.round(
      finiteNum(colonyData?.min_colony_fields ?? dashboard?.config?.min_colony_fields, 300)
    )
  );
  const attempts: ColonyAttemptItem[] = Array.isArray(colonyData?.colony_attempts)
    ? (colonyData!.colony_attempts as ColonyAttemptItem[]).filter(
        (a) => a && typeof a === "object"
      )
    : [];
  const attemptsSummary: ColonyAttemptsSummary =
    colonyData?.colony_attempts_summary && typeof colonyData.colony_attempts_summary === "object"
      ? (colonyData.colony_attempts_summary as ColonyAttemptsSummary)
      : {};
  const attemptsByCoords: Record<string, ColonyAttemptCoordStats> =
    colonyData?.attempts_by_coords && typeof colonyData.attempts_by_coords === "object"
      ? (colonyData.attempts_by_coords as Record<string, ColonyAttemptCoordStats>)
      : attemptsSummary.by_coords && typeof attemptsSummary.by_coords === "object"
        ? attemptsSummary.by_coords
        : {};
  const triedCount = Math.round(
    finiteNum(attemptsSummary.unique_coords_tried, Object.keys(attemptsByCoords).length)
  );
  const deletedCount = Math.round(
    finiteNum(
      attemptsSummary.deleted_undersized_count,
      attempts.filter((a) => attemptOutcomeFor(a.event_type, minFieldsTarget).label.startsWith("DELETED"))
        .length
    )
  );
  const keptCount = Math.round(finiteNum(attemptsSummary.kept_count, 0));
  const totalEvents = Math.round(finiteNum(attemptsSummary.total_events, attempts.length));

  const handleSavePolicy = async () => {
    const minVal = Math.max(50, Math.min(600, Math.round(finiteNum(minFieldsInput, 300))));
    try {
      await apiCommand({
        command: "update_config",
        config: { min_colony_fields: minVal, allow_colonize: allowColonizeInput },
      });
      showFeedback(
        `Colony policy saved: min ${minVal} fields, auto-colonize=${allowColonizeInput}.`,
        "ok"
      );
      await loadDashboard({ refresh: false, preserveConfig: false, force: true });
      await refreshColoniesView();
    } catch (err: any) {
      showFeedback(`Failed updating colony policy: ${err.message}`, feedbackKindForError(err));
    }
  };

  const handleScanGalaxy = async () => {
    try {
      showFeedback("Scanning Galaxy system for free slots...", "ok");
      const res = await apiCommand({
        command: "scan_galaxy",
        planet_id: activePlanet?.planet_id,
        dry_run: false,
      });
      showFeedback(res.message || "Galaxy scan completed.", "ok");
      await loadDashboard({ refresh: false, force: true });
      await refreshColoniesView();
    } catch (err: any) {
      showFeedback(`Galaxy scan failed: ${err.message}`, feedbackKindForError(err));
    }
  };

  const handleProbeCandidate = async (coords: string) => {
    try {
      showFeedback(`Probing free slot ${coords} for field count...`, "ok");
      const res = await apiCommand({
        command: "spy",
        target_coords: coords,
        quantity: 1,
        planet_id: activePlanet?.planet_id,
        dry_run: false,
      });
      showFeedback(
        res.message || `Spy probe dispatched to ${coords}.`,
        res.executed ? "ok" : "warn"
      );
      await refreshColoniesView();
    } catch (err: any) {
      showFeedback(`Probe failed: ${err.message}`, feedbackKindForError(err));
    }
  };

  const handleColonizeCandidate = async (coords: string) => {
    try {
      showFeedback(`Dispatching Colony ship (s8) to ${coords}...`, "ok");
      const res = await apiCommand({
        command: "colonize",
        target_coords: coords,
        planet_id: activePlanet?.planet_id,
        dry_run: false,
      });
      showFeedback(
        res.message || `Colonization processed for ${coords}.`,
        res.executed ? "ok" : "warn"
      );
      await loadDashboard({ refresh: false, force: true });
      await refreshColoniesView();
    } catch (err: any) {
      showFeedback(`Colonize failed: ${err.message}`, feedbackKindForError(err));
    }
  };

  const handleDiscardCandidate = async (coords: string) => {
    try {
      const res = await apiCommand({
        command: "discard_colony_candidate",
        target_coords: coords,
      });
      showFeedback(
        res.message || `Discarded colony candidate ${coords}.`,
        "ok"
      );
      await refreshColoniesView();
      await loadDashboard({ refresh: false, force: true });
    } catch (err: any) {
      showFeedback(`Discard failed: ${err.message}`, feedbackKindForError(err));
    }
  };

  return (
    <section
      id="view-colonies"
      className="page-view"
      aria-label="Possible colonies and planetary field grading"
      hidden={isHidden}
    >
      <div className="combat-top-cards col-top-cards">
        <div className="kpi-card">
          <span className="kpi-label tabular">Qualified 300+ Colonies</span>
          <strong className="kpi-value tabular" id="col-kpi-qualified">
            {`${fmtInt(colonyData?.qualified_count ?? 0)} Ready`}
          </strong>
          <span className="kpi-sub tabular" id="col-kpi-grades">
            {colonyData
              ? `S:${gc.S || 0} · A+:${gc["A+"] || 0} · A:${gc.A || 0} · B:${gc.B || 0} · C/D:${(gc.C || 0) + (gc.D || 0)}`
              : "S:0 · A+:0 · A:0 · B:0"}
          </span>
        </div>
        <div className="kpi-card">
          <span className="kpi-label tabular">Scouted Free Slots</span>
          <strong className="kpi-value tabular" id="col-kpi-probed">
            {colonyData ? `${fmtInt(colonyData.probed_count ?? 0)} Spied` : "0 Probed"}
          </strong>
          <span className="kpi-sub tabular" id="col-kpi-unprobed">
            {`${fmtInt(colonyData?.unprobed_count ?? 0)} unprobed free slots`}
          </span>
        </div>
        <div className="kpi-card">
          <span className="kpi-label tabular">Colonization Fleet</span>
          <strong className="kpi-value tabular" id="col-kpi-ships">
            {`${fmtInt(colonyData?.colony_ships ?? 0)} s8 · ${fmtInt(colonyData?.spy_probes ?? 0)} s10`}
          </strong>
          <span className="kpi-sub tabular" id="col-kpi-origin">
            {colonyData
              ? `Origin [${colonyData.origin_coords || "--"}] · ${fmtInt(colonyData.planet_count ?? 1)} colonies`
              : "Origin [--]"}
          </span>
        </div>
        <div className="kpi-card">
          <span className="kpi-label tabular">{`Tried / Deleted (<${minFieldsTarget})`}</span>
          <strong className="kpi-value tabular" id="col-kpi-tried">
            {`${fmtInt(triedCount)} Tried · ${fmtInt(deletedCount)} Del`}
          </strong>
          <span className="kpi-sub tabular" id="col-kpi-tried-sub">
            {`${fmtInt(keptCount)} kept · ${fmtInt(totalEvents)} logged events`}
          </span>
        </div>
        <div className="kpi-card">
          <span className="kpi-label tabular">Colony Policy</span>
          <div className="colony-policy-controls">
            <label className="colony-inline-field tabular" htmlFor="col-min-fields">
              Min Fields
              <input
                type="number"
                id="col-min-fields"
                min={50}
                max={600}
                step={10}
                value={minFieldsInput}
                onChange={(e) => setMinFieldsInput(e.target.value)}
              />
            </label>
            <label className="toggle-chip colony-inline-toggle">
              <input
                type="checkbox"
                id="col-allow-colonize"
                checked={allowColonizeInput}
                onChange={(e) => setAllowColonizeInput(e.target.checked)}
              />{" "}
              Auto Colonize
            </label>
            <button
              type="button"
              className="btn btn-primary btn-sm"
              id="btn-col-save"
              onClick={handleSavePolicy}
            >
              Apply
            </button>
            <button
              type="button"
              className="btn btn-secondary btn-sm"
              id="btn-col-scan"
              onClick={handleScanGalaxy}
            >
              Scan Galaxy
            </button>
          </div>
        </div>
      </div>

      <article className="hud-window page-fill col-candidates-window">
        <div className="window-bar window-bar-wrap">
          <div className="window-title-group">
            <span className="module-code tabular">COL</span>
            <h2>Possible Colonies &amp; Spied Field Grading</h2>
            <span className="module-meta tabular" id="col-meta-count">
              {colonyData ? `${filteredCands.length} / ${allCands.length} SLOTS` : "0 CANDIDATES"}
            </span>
            {dashboard?.galaxy_sweep ? (
              <span className="gal-cov-inline tabular" id="col-galaxy-coverage">
                {`GALAXY EXPLORED ${Math.max(
                  0,
                  Math.min(100, finiteNum(dashboard.galaxy_sweep.coverage_pct, 0))
                ).toFixed(1)}% (${fmtInt(dashboard.galaxy_sweep.explored_systems ?? 0)}/${fmtInt(
                  dashboard.galaxy_sweep.total_systems ?? 400
                )})`}
              </span>
            ) : null}
          </div>
          <div className="filter-toolbar">
            <div className="filter-chips" role="group" aria-label="Colony candidate filters">
              <button
                type="button"
                className={`chip-btn${colFilter === "all" ? " active" : ""}`}
                data-col-filter="all"
                onClick={() => setUiPref("colonyFilter", "all")}
              >
                All Slots
              </button>
              <button
                type="button"
                className={`chip-btn${colFilter === "probed" ? " active" : ""}`}
                data-col-filter="probed"
                onClick={() => setUiPref("colonyFilter", "probed")}
              >
                Spied &amp; Graded
              </button>
              <button
                type="button"
                className={`chip-btn${colFilter === "qualified" ? " active" : ""}`}
                data-col-filter="qualified"
                onClick={() => setUiPref("colonyFilter", "qualified")}
              >
                300+ Fields (S / A+)
              </button>
              <button
                type="button"
                className={`chip-btn${colFilter === "unprobed" ? " active" : ""}`}
                data-col-filter="unprobed"
                onClick={() => setUiPref("colonyFilter", "unprobed")}
              >
                Unprobed (?)
              </button>
            </div>
            <button
              type="button"
              className="btn btn-secondary btn-sm"
              id="btn-col-sync"
              onClick={syncAllInboxFolders}
            >
              Sync Spy Reports
            </button>
          </div>
        </div>

        <div className="table-wrap table-scroll">
          <table>
            <thead>
              <tr>
                <th>Grade</th>
                <th className="col-right">Score</th>
                <th>Coords</th>
                <th>Orbital Zone</th>
                <th className="col-right">Fields</th>
                <th className="col-right">Distance</th>
                <th>Assessment</th>
                <th className="col-right">Actions</th>
              </tr>
            </thead>
            <tbody id="col-candidates-tbody">
              {filteredCands.length === 0 ? (
                <tr>
                  <td colSpan={8} className="empty-cell">
                    {colonyData
                      ? "No matching colony slots. Click Scan Galaxy or probe free slots 7-12."
                      : "Scan neighbor systems or sync spy reports to grade free planet slots."}
                  </td>
                </tr>
              ) : (
                filteredCands.map((c) => {
                  const coords = String(c.coords || "—");
                  const grade = String(c.grade || "?");
                  const uf = finiteNum(c.uninhabited_fields, null);
                  const cMin = finiteNum(c.colonize_min_fields, null);
                  const cMax = finiteNum(c.colonize_max_fields, null);
                  const fieldsDisp =
                    typeof c.fields_display === "string" && c.fields_display.trim()
                      ? c.fields_display.trim()
                      : uf !== null && uf > 0
                        ? `${Math.round(uf)} fields`
                        : cMax !== null && cMax > 0
                          ? cMin !== null && cMin > 0 && cMin !== cMax
                            ? `${Math.round(cMin)}–${Math.round(cMax)} fields`
                            : `${Math.round(cMax)} fields`
                          : "";
                  const sysDist = Math.round(finiteNum(c.system_distance, 0));
                  const trialStats = attemptsByCoords[coords];
                  const trialDeleted =
                    trialStats && Math.round(finiteNum(trialStats.deleted_count, 0)) > 0;

                  return (
                    <tr key={coords}>
                      <td>
                        <span className={`grade-badge ${gradeClassFor(grade)}`}>{grade}</span>
                      </td>
                      <td className="col-right tabular">
                        {`${Math.round(finiteNum(c.score, 0))}/100`}
                      </td>
                      <td className="tabular">
                        {coords}
                        {trialStats ? (
                          <span
                            className={`rate-chip col-trial-chip${trialDeleted ? " is-deleted" : ""}`}
                            data-trial-coords={coords}
                            title="Colonization attempts recorded in SQLite colony_attempts"
                          >
                            {attemptShortTag(trialStats, minFieldsTarget)}
                          </span>
                        ) : null}
                      </td>
                      <td>{`Pos ${c.position || "?"} · ${c.orbital_band || "Unknown"}`}</td>
                      <td className="col-right tabular">
                        {fieldsDisp ? (
                          <span className={`status-word${c.meets_target ? "" : " is-warn"}`}>
                            {fieldsDisp}
                          </span>
                        ) : (
                          "Unprobed"
                        )}
                      </td>
                      <td className="col-right tabular">
                        {sysDist === 0 ? "Same sys" : `${sysDist} sys`}
                      </td>
                      <td>{String(c.reason || "")}</td>
                      <td className="col-right">
                        <div className="row-actions">
                          <button
                            type="button"
                            className="btn btn-secondary btn-sm"
                            onClick={() => handleProbeCandidate(coords)}
                          >
                            {c.probed ? "Re-Probe" : "Probe"}
                          </button>
                          <button
                            type="button"
                            className={
                              c.meets_target
                                ? "btn btn-primary btn-sm"
                                : "btn btn-secondary btn-sm"
                            }
                            onClick={() => handleColonizeCandidate(coords)}
                          >
                            Colonize
                          </button>
                          <button
                            type="button"
                            className="btn btn-warning btn-sm"
                            onClick={() => handleDiscardCandidate(coords)}
                          >
                            Discard
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
      </article>

      <article className="hud-window col-attempts-window" aria-labelledby="col-attempts-title">
        <div className="window-bar window-bar-wrap">
          <div className="window-title-group">
            <span className="module-code tabular">TRY</span>
            <h2 id="col-attempts-title">Tried Planets &amp; Colonization Attempts</h2>
            <span className="module-meta tabular" id="col-attempts-meta">
              {`${fmtInt(attempts.length)} EVENTS · ${fmtInt(triedCount)} COORDS`}
            </span>
          </div>
        </div>
        <div className="table-wrap table-scroll">
          <table id="colonies-attempts-table">
            <thead>
              <tr>
                <th>Coords</th>
                <th>Outcome</th>
                <th className="col-right">Rolled Fields</th>
                <th className="col-right">Target / Expected</th>
                <th className="col-right">Temp</th>
                <th>Origin</th>
                <th className="col-right">Time</th>
                <th>Details / Reason</th>
              </tr>
            </thead>
            <tbody id="colonies-attempts-tbody">
              {attempts.length === 0 ? (
                <tr>
                  <td colSpan={8} className="empty-cell">
                    No colonization attempts recorded yet. Every Colony ship dispatch, kept 300+ roll, and deleted undersized roll is logged here.
                  </td>
                </tr>
              ) : (
                attempts.map((a, idx) => {
                  const aCoords = String(a.target_coords || "—");
                  const aMin = Math.round(finiteNum(a.min_fields_target, minFieldsTarget));
                  const outcome = attemptOutcomeFor(a.event_type, aMin);
                  const rolled = finiteNum(a.fields_rolled, null);
                  const expMin = finiteNum(a.expected_min_fields, null);
                  const expMax = finiteNum(a.expected_max_fields, null);
                  const expTxt =
                    expMax !== null && expMax > 0
                      ? expMin !== null && expMin > 0 && expMin !== expMax
                        ? `${Math.round(expMin)}–${Math.round(expMax)}`
                        : `${Math.round(expMax)}`
                      : "";
                  const temp = finiteNum(a.temperature, null);
                  const reasonTxt =
                    String(a.status_message || "").trim() ||
                    (a.grade ? `Grade ${a.grade}` : "") ||
                    "—";
                  return (
                    <tr
                      key={`${a.id ?? idx}-${aCoords}`}
                      data-attempt-event={String(a.event_type || "")}
                    >
                      <td className="tabular">{aCoords}</td>
                      <td>
                        <span className={`outcome-badge ${outcome.cls}`}>{outcome.label}</span>
                      </td>
                      <td className="col-right tabular">
                        {rolled !== null && rolled > 0 ? (
                          <span className={`status-word${rolled >= aMin ? "" : " is-warn"}`}>
                            {`${Math.round(rolled)} f`}
                          </span>
                        ) : (
                          "—"
                        )}
                      </td>
                      <td className="col-right tabular">
                        {expTxt ? `≥${aMin} · exp ${expTxt}` : `≥${aMin}`}
                      </td>
                      <td className="col-right tabular">
                        {temp !== null ? `${Math.round(temp)} °C` : "—"}
                      </td>
                      <td className="tabular">{a.origin_coords ? String(a.origin_coords) : "—"}</td>
                      <td className="col-right tabular">{fmtShortDateTime(a.ts)}</td>
                      <td>{reasonTxt}</td>
                    </tr>
                  );
                })
              )}
            </tbody>
          </table>
        </div>
      </article>
    </section>
  );
};

export default ColoniesView;
