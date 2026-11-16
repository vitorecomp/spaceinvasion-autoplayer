import React, { useCallback, useEffect, useState } from "react";
import { feedbackKindForError, safeFiniteNumber } from "../../api/httpClient";
import {
  useActivePage,
  useConfigSyncState,
  useConsoleActions,
  useDashboard,
  useUiPrefs,
} from "../../store";
import type { StrategyConfigData, UniverseResearchMaxRow } from "../../types/api";
import {
  buildFormStateFromConfig,
  collectConfigSavePayload,
  syncDomFromFormState,
} from "./configFormHelpers";
import { ConfigGroupSection, RuntimeExecutionSection } from "./ConfigGroupSection";
import { ConfigResearchTable } from "./ConfigResearchTable";
import { CONFIG_GROUPS } from "./configSchema";

export const ConfigView: React.FC = () => {
  const dashboard = useDashboard();
  const activePage = useActivePage();
  const uiPrefs = useUiPrefs();
  const { configHydrationTick } = useConfigSyncState();
  const {
    setDashboard,
    setUiPref,
    setConfigDirty,
    setConfigSaving,
    bumpDashboardSeq,
    beginGaCalc,
    endGaCalc,
    apiCommand,
    loadDashboard,
    showFeedback,
    getSnapshot,
    getRuntime,
  } = useConsoleActions();

  const botCtrl = dashboard?.bot_control || {};
  const modeVal = uiPrefs.modeOverride || botCtrl.mode_override || "auto";
  const activeDbBackend: "postgres" | "sqlite" =
    String(
      dashboard?.db_backend?.backend || dashboard?.db_summary?.backend || "postgres"
    ).toLowerCase() === "sqlite"
      ? "sqlite"
      : "postgres";
  const activeDbTarget = String(
    dashboard?.db_backend?.target || dashboard?.db_summary?.target || "./data/postgres"
  );

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

  const [formValues, setFormValues] = useState<Record<string, any>>(() =>
    buildFormStateFromConfig(dashboard?.config)
  );
  const [localResearchRows, setLocalResearchRows] = useState<UniverseResearchMaxRow[] | null>(null);
  const [configFilter, setConfigFilter] = useState<string>("");

  const hydrateFromConfig = useCallback((cfg: StrategyConfigData | null | undefined) => {
    if (!cfg) return;
    const next = buildFormStateFromConfig(cfg);
    setFormValues(next);
    syncDomFromFormState(next);
  }, []);

  useEffect(() => {
    const rt = getRuntime();
    if (!rt.configDirty && !(rt.configSaving > 0) && dashboard?.config) {
      hydrateFromConfig(dashboard.config);
    }
  }, [configHydrationTick, dashboard?.config, getRuntime, hydrateFromConfig]);

  const markFieldChanged = (key: string, val: any) => {
    setConfigDirty(true);
    setFormValues((prev) => ({ ...prev, [key]: val }));
  };

  const nonRaidPlanets = (dashboard?.state?.planets || []).filter(
    (p) => String(p?.planet_type || "normal").toLowerCase() !== "raid"
  );
  const savedSpyPid = Math.max(
    0,
    Math.round(
      safeFiniteNumber(
        formValues.spy_dispatch_planet_id ?? dashboard?.config?.spy_dispatch_planet_id,
        0
      ) ?? 0
    )
  );
  const hasSavedPlanetOption =
    savedSpyPid === 0 || nonRaidPlanets.some((p) => Number(p.planet_id) === savedSpyPid);

  const onSaveConfig = async () => {
    const { syncedForm, payload, plannerModeVal } = collectConfigSavePayload(formValues);
    setFormValues(syncedForm);

    const isAiMode =
      String(plannerModeVal).toLowerCase() === "ai_planner" ||
      String(plannerModeVal).toLowerCase() === "ga_nn";
    if (isAiMode) beginGaCalc();

    bumpDashboardSeq();
    setConfigSaving((c) => c + 1);

    try {
      const res = await apiCommand({ command: "update_config", config: payload });
      bumpDashboardSeq();
      setConfigDirty(false);
      if (res.config && typeof res.config === "object") {
        const curDash = getSnapshot().dashboard || dashboard;
        const nextDash = curDash
          ? {
              ...curDash,
              config: res.config,
              planner_mode: res.config.planner_mode || curDash.planner_mode,
              effective_planner_mode:
                res.effective_planner_mode ??
                res.config.planner_mode ??
                curDash.effective_planner_mode,
              fallback_reason: res.fallback_reason ?? null,
              live_ai_unlocked: res.live_ai_unlocked ?? curDash.live_ai_unlocked,
            }
          : {
              config: res.config,
              planner_mode: res.config.planner_mode || "priority",
              effective_planner_mode:
                res.effective_planner_mode ?? res.config.planner_mode ?? "priority",
              fallback_reason: res.fallback_reason ?? null,
              live_ai_unlocked: res.live_ai_unlocked,
            };
        setDashboard(nextDash);
        hydrateFromConfig(res.config);
      }
      showFeedback("Saved strategy config to database — Commander & API updated.", "ok");
      await loadDashboard({ refresh: false, preserveConfig: false, force: true });
    } catch (err: any) {
      setConfigDirty(true);
      showFeedback(`Failed saving config: ${err.message}`, feedbackKindForError(err));
    } finally {
      setConfigSaving((c) => Math.max(0, c - 1));
      if (isAiMode) endGaCalc();
    }
  };

  const onClaimTransmitter = async () => {
    try {
      showFeedback("Checking daily free Interdimensional Transmitter spin...", "info");
      const res = await apiCommand({ command: "activate_transmitter", dry_run: false });
      showFeedback(
        res.message || "Interdimensional Transmitter check completed.",
        res.executed ? "ok" : "info"
      );
      await loadDashboard({ refresh: false, force: true });
    } catch (err: any) {
      showFeedback(`Transmitter check failed: ${err.message}`, feedbackKindForError(err));
    }
  };

  const onGalaxySweep = async () => {
    const pagesInp =
      typeof document !== "undefined"
        ? (document.getElementById("cfg-galaxy_sweep_pages") as HTMLInputElement | null)
        : null;
    const pagesVal =
      safeFiniteNumber(pagesInp ? pagesInp.value : formValues.galaxy_sweep_pages, 100) || 100;
    try {
      showFeedback(`Starting ${pagesVal}-page randomized galaxy sweep...`, "info");
      const res = await apiCommand(
        { command: "galaxy_sweep", pages: pagesVal, dry_run: false },
        { timeoutMs: 300000 }
      );
      showFeedback(res.message || "Galaxy sweep finished.", "ok");
      await loadDashboard({ refresh: false, force: true });
    } catch (err: any) {
      showFeedback(`Galaxy sweep failed: ${err.message}`, feedbackKindForError(err));
    }
  };

  const onRefreshUniverseResearch = async () => {
    try {
      showFeedback("Refreshing Universe Max Research levels and saving to SQLite...", "info");
      const res = await apiCommand({ command: "universe_research_max", sync: true });
      if (Array.isArray(res.rows)) {
        setLocalResearchRows(res.rows);
      }
      showFeedback(
        res.message || "Universe Max Research table refreshed and saved to SQLite.",
        "ok"
      );
      await loadDashboard({ refresh: false, force: true });
    } catch (err: any) {
      showFeedback(
        `Universe Max Research refresh failed: ${err.message}`,
        feedbackKindForError(err)
      );
    }
  };

  const researchRows: UniverseResearchMaxRow[] = Array.isArray(dashboard?.universe_research_max)
    ? dashboard!.universe_research_max!
    : localResearchRows || [];

  const filterNeedle = configFilter.trim().toLowerCase();
  const visibleGroups = CONFIG_GROUPS.map((group) => {
    if (!filterNeedle) return { group, matchedCount: group.items.length };
    const matched = group.items.filter((item) => {
      const hay = `${item.title} ${item.key} ${item.description} ${item.boundsLabel}`.toLowerCase();
      return hay.includes(filterNeedle);
    });
    return { group: { ...group, items: matched }, matchedCount: matched.length };
  });
  const totalMatchedSettings = visibleGroups.reduce((acc, g) => acc + g.matchedCount, 0);

  const jumpToSection = (headingId: string) => {
    if (typeof document === "undefined") return;
    const el = document.getElementById(headingId);
    if (el && typeof el.scrollIntoView === "function") {
      el.scrollIntoView({ behavior: "smooth", block: "start" });
    }
  };

  return (
    <section
      id="view-config"
      className="page-view"
      aria-label="Strategy configuration"
      hidden={activePage !== "config"}
    >
      <article className="hud-window page-fill">
        <div className="window-bar window-bar-wrap">
          <div className="window-title-group">
            <span className="module-code tabular">CFG</span>
            <h2>
              Strategy Profile &amp; Decision Buckets (
              {activeDbBackend === "postgres" ? "PostgreSQL" : "SQLite"})
            </h2>
          </div>
          <div className="inline-actions">
            <button
              type="button"
              className="btn btn-secondary btn-sm"
              id="btn-refresh-universe-research"
              onClick={onRefreshUniverseResearch}
            >
              Refresh Max Research
            </button>
            <button
              type="button"
              className="btn btn-secondary btn-sm"
              id="btn-claim-transmitter"
              onClick={onClaimTransmitter}
            >
              Claim Free IDT
            </button>
            <button
              type="button"
              className="btn btn-secondary btn-sm"
              id="btn-galaxy-sweep"
              onClick={onGalaxySweep}
            >
              Sweep 100 Pages
            </button>
            <button
              type="button"
              className="btn btn-primary btn-sm"
              id="btn-save-config"
              onClick={onSaveConfig}
            >
              Save &amp; Apply
            </button>
          </div>
        </div>

        <div className="config-nav-bar" aria-label="Filter and jump to configuration sections">
          <input
            type="search"
            id="cfg-search-input"
            className="table-search-input cfg-search-input"
            placeholder="Filter settings by name, key, or description..."
            aria-label="Filter strategy settings"
            value={configFilter}
            onChange={(e) => setConfigFilter(e.target.value)}
          />
          <div className="filter-chips cfg-jump-pills" role="navigation" aria-label="Jump to config section">
            <button
              type="button"
              className="chip-btn"
              data-cfg-jump="cfg-grp-execution-mode"
              onClick={() => jumpToSection("cfg-grp-execution-mode")}
            >
              Runtime
            </button>
            {CONFIG_GROUPS.map((g) => (
              <button
                key={g.id}
                type="button"
                className="chip-btn"
                data-cfg-jump={g.headingId}
                onClick={() => jumpToSection(g.headingId)}
              >
                {g.title.split("&")[0].trim()}
              </button>
            ))}
            <button
              type="button"
              className="chip-btn"
              data-cfg-jump="cfg-grp-univ-research"
              onClick={() => jumpToSection("cfg-grp-univ-research")}
            >
              Max Research
            </button>
          </div>
          {filterNeedle ? (
            <span className="module-meta tabular" id="cfg-filter-count">
              {`${totalMatchedSettings} MATCH${totalMatchedSettings === 1 ? "" : "ES"}`}
            </span>
          ) : null}
        </div>

        <div className="config-body">
          {!filterNeedle ? (
            <RuntimeExecutionSection
              modeVal={modeVal}
              activeDbBackend={activeDbBackend}
              activeDbTarget={activeDbTarget}
              onModeChange={onModeChange}
            />
          ) : null}

          {visibleGroups
            .filter((entry) => entry.matchedCount > 0)
            .map(({ group }) => (
              <ConfigGroupSection
                key={group.id}
                group={group}
                galaxySweep={dashboard?.galaxy_sweep}
                formValues={formValues}
                nonRaidPlanets={nonRaidPlanets}
                savedSpyPid={savedSpyPid}
                hasSavedPlanetOption={hasSavedPlanetOption}
                onFieldChange={markFieldChanged}
              />
            ))}

          {filterNeedle && totalMatchedSettings === 0 ? (
            <p className="muted" id="cfg-filter-empty">
              {`No settings match "${configFilter.trim()}".`}
            </p>
          ) : null}

          {/* Persisted Universe Max Research Levels in PostgreSQL (universe_research_max) */}
          {!filterNeedle ? <ConfigResearchTable researchRows={researchRows} /> : null}
        </div>
      </article>
    </section>
  );
};

export default ConfigView;
