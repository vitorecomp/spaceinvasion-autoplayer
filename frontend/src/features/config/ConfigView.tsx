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
      dashboard?.db_backend?.backend || dashboard?.db_summary?.backend || "sqlite"
    ).toLowerCase() === "postgres"
      ? "postgres"
      : "sqlite";
  const activeDbTarget = String(
    dashboard?.db_backend?.target || dashboard?.db_summary?.db_path || "state/spaceinvasion.db"
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
              {activeDbBackend === "postgres" ? "PostgreSQL · Podman" : "SQLite"})
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

        <div className="config-body">
          <RuntimeExecutionSection
            modeVal={modeVal}
            activeDbBackend={activeDbBackend}
            activeDbTarget={activeDbTarget}
            onModeChange={onModeChange}
          />

          {CONFIG_GROUPS.map((group) => (
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

          {/* 6th Section: Persisted Universe Max Research Levels in SQLite (universe_research_max) */}
          <ConfigResearchTable researchRows={researchRows} />
        </div>
      </article>
    </section>
  );
};

export default ConfigView;
