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

interface ParsedCoords {
  g: number;
  s: number;
  p: number;
}

function parseCoordsTriple(raw: unknown): ParsedCoords | null {
  const txt = String(raw ?? "").trim();
  const m = txt.match(/(\d{1,2})\s*:\s*(\d{1,3})\s*:\s*(\d{1,2})/);
  if (!m) return null;
  const g = Number.parseInt(m[1], 10);
  const s = Number.parseInt(m[2], 10);
  const p = Number.parseInt(m[3], 10);
  if (!Number.isFinite(g) || !Number.isFinite(s) || !Number.isFinite(p)) return null;
  return { g, s, p };
}

export function formatEspionageDistance(
  row: Record<string, any>,
  originCoords: string,
  targetCoords: string
): { label: string; title: string } {
  const oParsed = parseCoordsTriple(originCoords);
  const tParsed = parseCoordsTriple(targetCoords);

  let gDiff = finiteNum(row?.galaxy_delta, null);
  let sDiff = finiteNum(row?.system_delta, null);
  let pDiff = finiteNum(row?.position_delta, null);
  let sysDist = finiteNum(row?.system_distance, null);
  let flightDist = finiteNum(row?.distance, null);

  if (oParsed && tParsed) {
    if (gDiff === null) gDiff = Math.abs(oParsed.g - tParsed.g);
    if (sDiff === null) sDiff = Math.abs(oParsed.s - tParsed.s);
    if (pDiff === null) pDiff = Math.abs(oParsed.p - tParsed.p);
    if (sysDist === null) sysDist = gDiff * 400 + sDiff;
    if (flightDist === null) {
      if (gDiff > 0) flightDist = 20000 * gDiff;
      else if (sDiff > 0) flightDist = 2700 + 95 * sDiff;
      else flightDist = 1000 + 5 * pDiff;
    }
  }

  if (sysDist === null && gDiff === null && sDiff === null) {
    return { label: "—", title: "Distance unavailable (missing origin or target coordinates)" };
  }

  const gInt = Math.max(0, Math.round(gDiff ?? 0));
  const sInt = Math.max(0, Math.round(sDiff ?? Math.max(0, (sysDist ?? 0) - gInt * 400)));
  const pInt = Math.max(0, Math.round(pDiff ?? 0));
  const distStr =
    flightDist !== null && flightDist >= 0 ? ` · Flight dist ${fmtInt(Math.round(flightDist))}` : "";
  const fromStr = originCoords ? `From ${originCoords} · ` : "";

  if (gInt > 0) {
    const label = sInt > 0 ? `${gInt}G · ${sInt} sys` : `${gInt}G`;
    return {
      label,
      title: `${fromStr}Δ${gInt} gal, Δ${sInt} sys${distStr}`,
    };
  }
  if (sInt === 0) {
    const posStr = pInt > 0 ? ` (pos Δ${pInt})` : "";
    return {
      label: "Same sys",
      title: `${fromStr}Same system${posStr}${distStr}`,
    };
  }
  return {
    label: `${sInt} sys`,
    title: `${fromStr}${sInt} solar systems away${distStr}`,
  };
}

export const EspionageView: React.FC<EspionageViewProps> = ({ hidden }) => {
  const {
    dashboard,
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
  const originCoords = String(
    espData?.origin_coords || activePlanet?.coords || dashboard?.state?.planets?.[0]?.coords || ""
  ).trim();
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
  const uniExplored = Math.max(0, Math.round(finiteNum(galSweep?.universe_explored_systems, galExplored)));
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
                ? `${targets.length} TARGETS · ${reports.length} REPORTS${originCoords ? ` · FROM ${originCoords}` : ""}`
                : "0 TARGETS"}
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
                  <th className="col-right">Distance</th>
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
                    <td colSpan={9} className="empty-cell">
                      No espionage targets recorded yet. Run Scan Galaxy or Sync All Folders.
                    </td>
                  </tr>
                ) : (
                  targets.map((t, idx) => {
                    const coords = String(t.coords || t.target_coords || "—");
                    const dist = formatEspionageDistance(t, originCoords, coords);
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
                        <td className="col-right tabular" title={dist.title}>
                          {dist.label}
                        </td>
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
                  <th className="col-right">Distance</th>
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
                    <td colSpan={8} className="empty-cell">
                      No historical spy reports stored yet.
                    </td>
                  </tr>
                ) : (
                  reports.map((r, idx) => {
                    const rCoords = String(r.coords || r.target_coords || "—");
                    const rDist = formatEspionageDistance(r, originCoords, rCoords);
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
                        <td className="tabular">{rCoords}</td>
                        <td className="col-right tabular" title={rDist.title}>
                          {rDist.label}
                        </td>
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
