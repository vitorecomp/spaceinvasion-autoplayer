import React from "react";
import { feedbackKindForError, safeFiniteNumber } from "../../api/httpClient";
import { useConsole } from "../../context/ConsoleContext";
import { PageKey } from "../../types/api";

export const AppSidebar: React.FC = () => {
  const {
    dashboard,
    apiOnline,
    reconnectAttempts,
    activePage,
    setActivePage,
    activePlanet,
    setSelectedPlanetId,
    uiPrefs,
    setUiPref,
    espData,
    colonyData,
    atkData,
    recData,
    beginGaCalc,
    endGaCalc,
    loadDashboard,
    apiCommand,
    showFeedback,
  } = useConsole();

  const health = dashboard?.health || {};
  const gs = dashboard?.state || {};
  const planets = Array.isArray(gs.planets) ? gs.planets : [];
  const port = health.debug_port ? "8787" : (typeof window !== "undefined" && window.location.port) || "8787";

  const apiPipClass = apiOnline ? "status-pip online" : "status-pip offline";
  const apiStatusText = apiOnline
    ? `API :${port} ONLINE · HOT`
    : reconnectAttempts > 0
      ? `API :${port} RECONNECTING (#${reconnectAttempts})`
      : "API :8787 CONNECTING...";

  const cdSec = Math.ceil(safeFiniteNumber(health.retry_after, 0) ?? 0);
  const cdTag =
    health.rate_limited && cdSec > 0
      ? ` · COOLDOWN ${cdSec}s`
      : health.rate_limited
        ? " · COOLDOWN"
        : "";
  const chromePipClass = health.in_game
    ? "status-pip online"
    : health.debug_port_open
      ? "status-pip"
      : "status-pip offline";
  const chromeStatusText = dashboard
    ? health.in_game
      ? `CHROME :${health.debug_port || 9222} ONLINE${cdTag}`
      : health.debug_port_open
        ? `CHROME :${health.debug_port || 9222} STANDBY`
        : `CHROME :${health.debug_port || 9222} OFFLINE`
    : "Connecting Chrome :9222...";
  const showEnsureBtn = Boolean(dashboard && !health.in_game);

  const spdVal = safeFiniteNumber(gs.speedup_factor, null);
  const speedupText = spdVal !== null && spdVal > 0 ? `SPEED x${spdVal}` : dashboard ? "STD x1" : "SPEED x1";

  const botCtrl = dashboard?.bot_control || {};
  const paused = Boolean(botCtrl.paused);
  const modeVal = uiPrefs.modeOverride || botCtrl.mode_override || "auto";
  const runBadgeText = paused ? "PAUSED" : modeVal === "dry-run" ? "DRY" : "RUN";
  const runBadgeCls = paused
    ? "run-badge tabular is-paused"
    : modeVal === "dry-run"
      ? "run-badge tabular is-dry"
      : "run-badge tabular";

  const batchIv = safeFiniteNumber(
    health.action_batch_interval_seconds ?? dashboard?.config?.action_batch_interval_seconds,
    null
  );
  const budgetText = dashboard
    ? batchIv !== null && batchIv > 0
      ? `BATCH ${Math.round(batchIv)}s`
      : `${health.remaining_hourly_actions ?? "—"} OPS/HR`
    : "-- OPS/HR";

  const cfgMode = String(
    dashboard?.config?.planner_mode || dashboard?.planner_mode || "priority"
  ).toLowerCase();
  const isGaNn = cfgMode === "ga_nn";

  const counts = dashboard?.db_summary?.counts || {};
  const spyCnt = Math.max(
    0,
    Math.round(
      safeFiniteNumber(
        espData?.summary?.spied_targets ?? counts.spy_reports ?? counts.spy_targets ?? 0,
        0
      ) ?? 0
    )
  );
  const colCnt = Math.max(
    0,
    Math.round(
      safeFiniteNumber(
        colonyData
          ? colonyData.qualified_count ?? colonyData.probed_count ?? 0
          : counts.galaxy_slots ?? 0,
        0
      ) ?? 0
    )
  );
  const atkCnt = Math.max(
    0,
    Math.round(
      safeFiniteNumber(atkData?.summary?.total_battles ?? counts.battle_reports ?? 0, 0) ?? 0
    )
  );
  const recCnt = Math.max(
    0,
    Math.round(
      safeFiniteNumber(recData?.summary?.total_runs ?? counts.recycle_reports ?? 0, 0) ?? 0
    )
  );

  const planCount = Array.isArray(dashboard?.plan) ? dashboard!.plan!.length : 0;

  const rnkCur = dashboard?.ranking?.current || {};
  const srvRank = safeFiniteNumber(rnkCur.server_rank, null);
  const rankBadgeTxt =
    rnkCur.server_rank != null
      ? srvRank !== null
        ? `#${srvRank}`
        : "#—"
      : rnkCur.attack_zone?.label || "#—";

  const onSync = async () => {
    const useGa =
      String(dashboard?.config?.planner_mode || "").toLowerCase() === "ga_nn";
    if (useGa) beginGaCalc();
    try {
      showFeedback("Syncing live snapshot from Chrome...", "ok");
      const ok = await loadDashboard({ refresh: true, preserveConfig: true, force: true });
      if (ok) {
        const latestDash = (globalThis as any).state?.dashboard || dashboard;
        if (latestDash?.health?.rate_limited) {
          const rem = Math.ceil(safeFiniteNumber(latestDash.health.retry_after, 0) ?? 0);
          showFeedback(
            `Synced cached state — SpaceInvasion rate-limit cooldown active (${rem > 0 ? `${rem}s` : "wait"}).`,
            "warn"
          );
        } else {
          showFeedback("Synced with live Chrome session.", "ok");
        }
      }
    } finally {
      if (useGa) endGaCalc();
    }
  };

  const onEnsureSession = async () => {
    try {
      showFeedback("Attaching Chrome to SpaceInvasion universe...", "ok");
      const res = await apiCommand({ command: "ensure_session" });
      showFeedback(res.message || "Entered universe.", "ok");
      await loadDashboard({ refresh: true, preserveConfig: true, force: true });
    } catch (err: any) {
      showFeedback(`Enter universe failed: ${err.message}`, feedbackKindForError(err));
    }
  };

  const onPauseToggle = async () => {
    const currentPaused = Boolean(dashboard?.bot_control?.paused);
    try {
      await apiCommand({ command: "bot_control", paused: !currentPaused });
      await loadDashboard({ refresh: false, force: true });
      showFeedback(
        !currentPaused ? "Commander auto-loop paused." : "Commander auto-loop resumed.",
        "ok"
      );
    } catch (err: any) {
      showFeedback(`Pause update failed: ${err.message}`, feedbackKindForError(err));
    }
  };

  const onModeChange = async (mode: string) => {
    const val = mode === "auto" ? null : mode;
    try {
      await apiCommand({ command: "bot_control", mode_override: val });
      setUiPref("modeOverride", mode);
      await loadDashboard({ refresh: false, force: true });
      showFeedback(`Loop mode set to ${mode}.`, "ok");
    } catch (err: any) {
      showFeedback(`Mode update failed: ${err.message}`, feedbackKindForError(err));
    }
  };

  const onTickNow = async () => {
    const useGa =
      String(dashboard?.config?.planner_mode || "").toLowerCase() === "ga_nn";
    if (useGa) beginGaCalc();
    try {
      const dryRun = modeVal === "dry-run";
      showFeedback(`Running 1 ${dryRun ? "dry-run" : "live"} tick...`, "ok");
      const res = await apiCommand({ command: "tick_once", dry_run: dryRun });
      showFeedback(res.message || "Tick finished.", "ok");
      await loadDashboard({ refresh: false, force: true });
    } catch (err: any) {
      showFeedback(`Tick failed: ${err.message}`, feedbackKindForError(err));
    } finally {
      if (useGa) endGaCalc();
    }
  };

  const navItems: Array<{ page: PageKey; code: string; title: string; badgeId?: string; badgeText?: string; badgeExtraCls?: string }> = [
    { page: "dashboard", code: "DSH", title: "Dashboard" },
    { page: "buildings", code: "BLD", title: "Buildings & ETAs", badgeId: "nav-bld-badge", badgeText: "21" },
    { page: "combat", code: "CMB", title: "Combat & Fleet", badgeId: "nav-cmb-badge", badgeText: "1:1" },
    { page: "espionage", code: "SPY", title: "Espionage Intel", badgeId: "nav-spy-badge", badgeText: String(spyCnt) },
    { page: "colonies", code: "COL", title: "Possible Colonies", badgeId: "nav-col-badge", badgeText: String(colCnt) },
    { page: "attacks", code: "ATK", title: "Attack Results", badgeId: "nav-atk-badge", badgeText: String(atkCnt) },
    { page: "recyclers", code: "REC", title: "Recyclers & Inbox", badgeId: "nav-rec-badge", badgeText: String(recCnt) },
    { page: "operations", code: "OPS", title: "Operations Queue", badgeId: "nav-queue-count", badgeText: String(planCount) },
    { page: "action-graph", code: "DAG", title: "Action Graph", badgeId: "nav-graph-badge", badgeText: isGaNn ? "GA" : "PRI", badgeExtraCls: isGaNn ? " is-ga" : "" },
    { page: "ranking", code: "RNK", title: "Ranking Evolution", badgeId: "nav-rank-badge", badgeText: rankBadgeTxt },
    { page: "config", code: "CFG", title: "Strategy Config" },
    { page: "logs", code: "LOG", title: "Live Logs" },
  ];

  return (
    <aside
      className={`hud-sidebar${uiPrefs.sidebarOpen ? " is-open" : ""}`}
      id="app-sidebar"
      aria-label="Primary navigation and autopilot"
    >
      <div className="sidebar-head">
        <div className="sidebar-brand">
          <span className="module-code tabular">CMD</span>
          <div className="brand-titles">
            <h1>SpaceInvasion Control</h1>
            <span className="brand-sub tabular" id="val-speedup">
              {speedupText}
            </span>
          </div>
        </div>
        <button
          type="button"
          className="btn btn-secondary btn-sm sidebar-mobile-toggle"
          id="btn-sidebar-toggle"
          aria-expanded={uiPrefs.sidebarOpen ? "true" : "false"}
          aria-controls="sidebar-body"
          onClick={() => setUiPref("sidebarOpen", !uiPrefs.sidebarOpen)}
        >
          Menu
        </button>
      </div>

      <div className="sidebar-body" id="sidebar-body">
        <section className="sidebar-section" aria-label="Browser and API session status">
          <div className="session-card">
            <div className="session-row">
              <span className={apiPipClass} id="api-pip" aria-hidden="true" />
              <span id="api-status-text" className="tabular">
                {apiStatusText}
              </span>
            </div>
            <div className="session-row">
              <span className={chromePipClass} id="chrome-pip" aria-hidden="true" />
              <span id="chrome-status-text" className="tabular">
                {chromeStatusText}
              </span>
            </div>
            <button
              type="button"
              className="btn btn-warning btn-sm"
              id="btn-ensure-session"
              hidden={!showEnsureBtn}
              onClick={onEnsureSession}
            >
              Enter Universe
            </button>
          </div>
        </section>

        <nav className="sidebar-section sidebar-nav" aria-label="Console views">
          <div className="nav-label tabular">Navigation</div>
          {navItems.map((item) => {
            const isAct = activePage === item.page;
            return (
              <button
                key={item.page}
                type="button"
                className={`sidebar-link${isAct ? " active" : ""}`}
                data-page={item.page}
                aria-current={isAct ? "page" : undefined}
                onClick={() => setActivePage(item.page)}
              >
                <span className="nav-code tabular">{item.code}</span>
                <span className="nav-title">{item.title}</span>
                {item.badgeId ? (
                  <span
                    className={`nav-badge tabular${item.badgeExtraCls || ""}`}
                    id={item.badgeId}
                  >
                    {item.badgeText}
                  </span>
                ) : null}
              </button>
            );
          })}
        </nav>

        <section className="sidebar-section" aria-label="Colony selector">
          <div className="control-field">
            <label htmlFor="planet-select">Active Colony</label>
            <select
              id="planet-select"
              aria-label="Active planet"
              value={activePlanet ? String(activePlanet.planet_id) : ""}
              onChange={(e) => setSelectedPlanetId(Number(e.target.value))}
            >
              {planets.length === 0 ? (
                <option value="">Loading...</option>
              ) : (
                planets.map((p) => (
                  <option key={p.planet_id} value={String(p.planet_id)}>
                    {p.coords} {p.name}
                  </option>
                ))
              )}
            </select>
          </div>
        </section>

        <section className="sidebar-section sidebar-autopilot" aria-label="Commander autopilot">
          <div className="autopilot-status">
            <span className={runBadgeCls} id="loop-run-badge">
              {runBadgeText}
            </span>
            <span className="budget-readout tabular" id="val-budget">
              {budgetText}
            </span>
          </div>

          <div className="control-field">
            <label id="mode-group-label">Execution Mode</label>
            <div className="mode-stack" role="radiogroup" aria-labelledby="mode-group-label">
              {[
                { key: "auto", label: "CLI Default" },
                { key: "live", label: "Force Live" },
                { key: "dry-run", label: "Force Dry-Run" },
              ].map((m) => {
                const isAct = modeVal === m.key;
                return (
                  <button
                    key={m.key}
                    type="button"
                    className={`stack-item${isAct ? " active" : ""}`}
                    role="radio"
                    aria-checked={isAct ? "true" : "false"}
                    data-mode={m.key}
                    onClick={() => onModeChange(m.key)}
                  >
                    {m.label}
                  </button>
                );
              })}
            </div>
          </div>

          <div className="stack-actions">
            <button
              type="button"
              className={`btn btn-secondary btn-sm${paused ? " btn-warning" : ""}`}
              id="btn-pause-toggle"
              aria-pressed={paused ? "true" : "false"}
              onClick={onPauseToggle}
            >
              <span id="pause-label">{paused ? "Resume Auto" : "Pause Auto"}</span>
            </button>

            <button type="button" className="btn btn-secondary btn-sm" id="btn-sync" onClick={onSync}>
              <span>Sync</span>
            </button>

            <button
              type="button"
              className="btn btn-primary btn-sm btn-span"
              id="btn-tick-now"
              onClick={onTickNow}
            >
              <span>Run 1 Tick</span>
            </button>
          </div>
        </section>

        <div className="sidebar-foot tabular">
          <span>WIN // VIEWS</span>
          <span>API :8787</span>
        </div>
      </div>
    </aside>
  );
};

export default AppSidebar;
