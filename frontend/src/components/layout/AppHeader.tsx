import React from "react";
import { feedbackKindForError, safeFiniteNumber } from "../../api/httpClient";
import { useConsole } from "../../context/ConsoleContext";
import { PAGE_TITLES } from "../../types/api";

function fmtClock(tsSec: unknown): string {
  if (!tsSec) return "SYNC --:--:--";
  const ts = safeFiniteNumber(tsSec, null);
  if (ts === null) return "SYNC ACTIVE";
  const d = new Date(ts * 1000);
  if (Number.isNaN(d.getTime())) return "SYNC ACTIVE";
  return `SYNC ${d.toTimeString().slice(0, 8)}`;
}

export function computeSelectedBlastFurnacePct(
  dash: Record<string, any> | null | undefined,
  activePlanet: Record<string, any> | null | undefined,
  buildingsEtaData?: Record<string, any> | null
): number {
  const eta =
    buildingsEtaData &&
    activePlanet &&
    buildingsEtaData.planet?.planet_id === activePlanet.planet_id
      ? buildingsEtaData
      : dash?.buildings_eta || null;
  const r2 = safeFiniteNumber(activePlanet?.economy_rates?.resource2, null);
  const etaPct = safeFiniteNumber(eta?.planet_economy?.blast_furnace_pct, null);
  const pct = r2 !== null ? r2 : etaPct !== null ? etaPct : 100;
  return Math.max(0, Math.min(100, Math.round(pct)));
}

export const AppHeader: React.FC = () => {
  const {
    dashboard,
    activePage,
    setActivePage,
    activePlanet,
    buildingsEtaData,
    notifications,
    notifCenterOpen,
    openNotificationCenter,
    activeHelpNotif,
    ackActiveHelpPopup,
    apiCommand,
    loadDashboard,
    showFeedback,
  } = useConsole();

  const gs = dashboard?.state || {};
  const universeText = dashboard
    ? `U${gs.universe_id || 31} · ${gs.nickname || "COMMANDER"}`
    : "U31 · COMMANDER";
  const clockText = fmtClock(gs.timestamp);

  const tempVal = safeFiniteNumber(activePlanet?.temperature, null);
  const fUsedVal = safeFiniteNumber(activePlanet?.fields_used, null);
  const fMaxVal = safeFiniteNumber(activePlanet?.fields_max, null);
  const specsText = activePlanet
    ? `TEMP ${tempVal === null ? "—" : tempVal}°C · FIELDS ${fUsedVal === null ? "—" : fUsedVal}/${fMaxVal === null ? "—" : fMaxVal}`
    : "TEMP --°C · FIELDS --/--";

  const b2Pct = computeSelectedBlastFurnacePct(dashboard, activePlanet, buildingsEtaData);
  const bfText = b2Pct === 0 ? "B2: 0% PAUSED" : `B2: ${b2Pct}%`;

  const galSweep = dashboard?.galaxy_sweep || null;
  const galTotal = Math.max(0, Math.round(safeFiniteNumber(galSweep?.total_systems, 0) ?? 0));
  const galExplored = Math.max(
    0,
    Math.round(safeFiniteNumber(galSweep?.explored_systems, 0) ?? 0)
  );
  const galCovRaw = safeFiniteNumber(galSweep?.coverage_pct, null);
  const galCovPct =
    galCovRaw !== null
      ? Math.max(0, Math.min(100, galCovRaw))
      : galTotal > 0
        ? Math.max(0, Math.min(100, (galExplored / galTotal) * 100))
        : null;
  const galPhaseLabel =
    String(galSweep?.scan_phase || "").toLowerCase() === "targeted_refresh"
      ? "Targeted Refresh"
      : "Exploratory";
  const galList = Array.isArray(galSweep?.scan_galaxies) && galSweep!.scan_galaxies!.length > 0
    ? galSweep!.scan_galaxies!.map((g) => `G${g}`).join(",")
    : "Galaxy";
  const galCovText =
    galCovPct !== null
      ? `GAL ${galCovPct.toFixed(1)}%`
      : "GAL --%";
  const galCovTitle =
    galCovPct !== null
      ? `${galList} explored: ${galExplored}/${galTotal} systems (${galCovPct.toFixed(1)}%) · Phase: ${galPhaseLabel}${galSweep?.in_progress ? " · Sweeping..." : ""}`
      : "Galaxy exploration progress — click to open Espionage & Galaxy Scanner";

  const hostileCount = Array.isArray(gs.fleet_alerts)
    ? gs.fleet_alerts.filter((a) => a?.hostile).length
    : 0;
  const unreadCnt = notifications.unread_count || 0;

  const captchaBlocked = Boolean(dashboard?.bot_control?.captcha_blocked);
  const captchaMsg =
    dashboard?.bot_control?.captcha_message ||
    "CAPTCHA / Security verification active in Chrome (:9222). Automation is paused and waiting passively — solve it manually in Chrome, then click Resume.";
  const helpModalHasCaptchaBtn = Boolean(
    activeHelpNotif &&
      (activeHelpNotif.category === "captcha_gate" || activeHelpNotif.category === "antibot")
  );

  const onResumeFromCaptcha = async () => {
    try {
      await apiCommand({ command: "bot_control", paused: false, captcha_solved: true });
      if (activeHelpNotif) {
        await ackActiveHelpPopup("read");
      }
      await loadDashboard({ refresh: true, force: true });
      showFeedback("CAPTCHA solved confirmed — page refresh & Commander automation resumed.", "ok");
    } catch (err: any) {
      showFeedback(`Resume failed: ${err.message}`, feedbackKindForError(err));
    }
  };

  return (
    <>
      <header className="hud-ruler" aria-label="Session telemetry bar">
        <div className="ruler-cluster">
          <span className="ruler-page-tag tabular" id="hud-page-title">
            {PAGE_TITLES[activePage] || "DASHBOARD"}
          </span>
          <span className="ruler-sep" aria-hidden="true" />
          <span className="ruler-node tabular" id="hud-universe">
            {universeText}
          </span>
          <span className="ruler-sep" aria-hidden="true" />
          <span className="ruler-node tabular" id="hud-clock">
            {clockText}
          </span>
          <span className="ruler-sep" aria-hidden="true" />
          <span className="ruler-node tabular" id="planet-specs">
            {specsText}
          </span>
        </div>

        <div className="ruler-cluster">
          <button
            type="button"
            className={`gal-cov-pill tabular${galCovPct !== null && galCovPct >= 100 ? " is-complete" : ""}`}
            id="hud-galaxy-coverage"
            title={galCovTitle}
            onClick={() => setActivePage("espionage")}
          >
            {galCovText}
          </button>
          <button
            type="button"
            className={`bf-pill tabular${b2Pct === 0 ? " is-paused" : ""}`}
            id="hud-bf-pill"
            title="Blast furnace rate on selected planet — click to open Buildings & ETAs"
            onClick={() => setActivePage("buildings")}
          >
            {bfText}
          </button>
          <button
            type="button"
            className={`notif-bell-btn tabular${unreadCnt > 0 ? " has-unread" : ""}`}
            id="btn-notifications"
            aria-label={
              unreadCnt > 0
                ? `Open system notifications (${unreadCnt} unread)`
                : "Open system notifications"
            }
            aria-haspopup="dialog"
            aria-expanded={notifCenterOpen ? "true" : "false"}
            title="System notifications and validation alerts"
            onClick={() => openNotificationCenter()}
          >
            <img src="/bell.svg" alt="" width={15} height={15} className="notif-bell-img" />
            <span className="notif-count-badge" id="notif-unread-badge" hidden={unreadCnt <= 0}>
              {unreadCnt > 99 ? "99+" : String(unreadCnt)}
            </span>
          </button>
          <div
            className={`beacon-pill tabular${hostileCount > 0 || captchaBlocked ? " is-alert" : ""}`}
            id="hud-alert-beacon"
            role="status"
          >
            <span className="beacon-mark" aria-hidden="true" />
            <span id="val-alerts">
              {captchaBlocked
                ? "CAPTCHA PAUSE · SOLVE IN CHROME"
                : hostileCount > 0
                  ? `HOSTILE ALERT (${hostileCount})`
                  : "SECTOR CLEAR · 0 HOSTILES"}
            </span>
          </div>
        </div>
      </header>

      <div
        className="captcha-gate-banner"
        id="captcha-gate-banner"
        role="alert"
        hidden={!captchaBlocked}
      >
        <div className="captcha-gate-copy">
          <span className="module-code tabular">GATE</span>
          <span>{captchaMsg}</span>
        </div>
        <button
          type="button"
          className="btn btn-primary btn-sm"
          id={helpModalHasCaptchaBtn ? "btn-header-captcha-resume" : "btn-captcha-resume"}
          onClick={onResumeFromCaptcha}
        >
          Solved in Chrome — Resume Bot
        </button>
      </div>
    </>
  );
};

export default AppHeader;
