import React, { useCallback, useEffect, useLayoutEffect, useMemo, useState } from "react";
import { flushSync } from "react-dom";

const useClientSyncEffect = typeof window !== "undefined" ? useLayoutEffect : useEffect;
import { feedbackKindForError, safeFiniteNumber } from "../../api/httpClient";
import { SortableTh } from "../../components/SortableTh";
import { useConsole } from "../../context/ConsoleContext";
import { normalizeUiPlannerMode } from "../action-graph/plannerHelpers";
import {
  timestampSortKey,
  useTableSort,
  type SortColumnMap,
} from "../../hooks/useTableSort";
import { StrategyConfigData, UniverseResearchMaxRow } from "../../types/api";
import {
  CONFIG_BOOL_KEYS,
  CONFIG_GROUPS,
  CONFIG_NUMBER_DEFAULTS,
} from "./configSchema";

function fmtShortUpdated(tsSec: unknown): string {
  const ts = safeFiniteNumber(tsSec, null);
  if (ts === null || ts <= 0) return "—";
  const d = new Date(ts * 1000);
  if (Number.isNaN(d.getTime())) return "—";
  const mm = String(d.getMonth() + 1).padStart(2, "0");
  const dd = String(d.getDate()).padStart(2, "0");
  const hh = String(d.getHours()).padStart(2, "0");
  const mi = String(d.getMinutes()).padStart(2, "0");
  return `${mm}-${dd} ${hh}:${mi}`;
}

function buildFormStateFromConfig(cfg: StrategyConfigData | null | undefined): Record<string, any> {
  const out: Record<string, any> = {
    planner_mode: normalizeUiPlannerMode(cfg?.planner_mode),
    playstyle: String(cfg?.playstyle || "fleeter"),
    spy_dispatch_planet_id: String(
      Math.max(0, Math.round(safeFiniteNumber(cfg?.spy_dispatch_planet_id, 0) ?? 0))
    ),
    ga_seed: cfg?.ga_seed != null ? String(cfg.ga_seed) : "",
  };
  for (const bKey of CONFIG_BOOL_KEYS) {
    const defItem = CONFIG_GROUPS.flatMap((g) => g.items).find((i) => i.key === bKey);
    out[bKey] = cfg && bKey in cfg ? Boolean(cfg[bKey]) : Boolean(defItem?.defaultValue);
  }
  for (const [nKey, defVal] of Object.entries(CONFIG_NUMBER_DEFAULTS)) {
    const v = cfg && cfg[nKey] != null ? cfg[nKey] : defVal;
    out[nKey] = String(v);
  }
  return out;
}

export const ConfigView: React.FC = () => {
  const {
    dashboard,
    setDashboard,
    activePage,
    uiPrefs,
    setUiPref,
    configHydrationTick,
    setConfigDirty,
    setConfigSaving,
    bumpDashboardSeq,
    beginGaCalc,
    endGaCalc,
    apiCommand,
    loadDashboard,
    showFeedback,
  } = useConsole();

  const botCtrl = dashboard?.bot_control || {};
  const modeVal = uiPrefs.modeOverride || botCtrl.mode_override || "auto";
  const activeDbBackend = String(
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
    if (typeof document !== "undefined") {
      for (const [k, v] of Object.entries(next)) {
        const node = document.getElementById(`cfg-${k}`) as
          | HTMLInputElement
          | HTMLSelectElement
          | null;
        if (!node) continue;
        if (node instanceof HTMLInputElement && node.type === "checkbox") {
          node.checked = Boolean(v);
        } else {
          node.value = String(v ?? "");
        }
      }
    }
  }, []);

  useEffect(() => {
    const gState = (globalThis as any).state;
    if (!gState?.configDirty && !(gState?.configSaving > 0) && dashboard?.config) {
      hydrateFromConfig(dashboard.config);
    }
  }, [configHydrationTick, dashboard?.config, hydrateFromConfig]);

  // Expose legacy renderConfigForm onto globalThis for direct unit tests
  useClientSyncEffect(() => {
    (globalThis as any).renderConfigForm = (cfg: StrategyConfigData) => {
      flushSync(() => hydrateFromConfig(cfg));
    };
  }, [hydrateFromConfig]);

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
    const readEl = (id: string) =>
      (typeof document !== "undefined" ? document.getElementById(id) : null) as
        | HTMLInputElement
        | HTMLSelectElement
        | null;

    const modeNode = readEl("cfg-planner_mode");
    const playNode = readEl("cfg-playstyle");
    const spyPidNode = readEl("cfg-spy_dispatch_planet_id");

    const plannerModeVal = modeNode ? modeNode.value : String(formValues.planner_mode || "priority");
    const playstyleVal = playNode ? playNode.value : String(formValues.playstyle || "fleeter");
    const rawSpyPid = spyPidNode
      ? spyPidNode.value
      : String(formValues.spy_dispatch_planet_id || "0");

    const syncedForm: Record<string, any> = {
      ...formValues,
      planner_mode: plannerModeVal,
      playstyle: playstyleVal,
      spy_dispatch_planet_id: String(
        Math.max(0, Math.round(safeFiniteNumber(rawSpyPid, 0) ?? 0))
      ),
    };

    const payload: Record<string, any> = {
      planner_mode: plannerModeVal,
      playstyle: playstyleVal,
      spy_dispatch_planet_id: Math.max(0, Math.round(safeFiniteNumber(rawSpyPid, 0) ?? 0)),
    };

    for (const bKey of CONFIG_BOOL_KEYS) {
      const box = readEl(`cfg-${bKey}`) as HTMLInputElement | null;
      const checkedVal = box ? Boolean(box.checked) : Boolean(formValues[bKey]);
      payload[bKey] = checkedVal;
      syncedForm[bKey] = checkedVal;
    }

    for (const numField of Object.keys(CONFIG_NUMBER_DEFAULTS)) {
      const inp = readEl(`cfg-${numField}`);
      const raw = inp ? String(inp.value ?? "").trim() : String(formValues[numField] ?? "").trim();
      syncedForm[numField] = inp ? String(inp.value ?? "") : String(formValues[numField] ?? "");
      if (raw !== "") {
        const parsed = safeFiniteNumber(raw, null);
        payload[numField] = parsed !== null ? parsed : raw;
      }
    }

    const seedInp = readEl("cfg-ga_seed");
    const rawSeed = seedInp
      ? String(seedInp.value ?? "").trim()
      : String(formValues.ga_seed ?? "").trim();
    syncedForm.ga_seed = seedInp ? String(seedInp.value ?? "") : String(formValues.ga_seed ?? "");
    if (rawSeed === "") {
      payload.ga_seed = null;
    } else if (/^-?\d+$/.test(rawSeed)) {
      const parsedSeed = Number(rawSeed);
      payload.ga_seed = Number.isSafeInteger(parsedSeed) ? parsedSeed : rawSeed;
    } else {
      payload.ga_seed = rawSeed;
    }

    setFormValues(syncedForm);

    const isAiMode = String(plannerModeVal).toLowerCase() === "ai_planner" || String(plannerModeVal).toLowerCase() === "ga_nn";
    if (isAiMode) beginGaCalc();

    bumpDashboardSeq();
    const gState = (globalThis as any).state;
    if (gState) {
      gState.configSaving = (gState.configSaving || 0) + 1;
    }
    setConfigSaving((c) => c + 1);

    try {
      const res = await apiCommand({ command: "update_config", config: payload });
      bumpDashboardSeq();
      setConfigDirty(false);
      if (res.config && typeof res.config === "object") {
        const curDash = gState?.dashboard || dashboard;
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
        if (gState) {
          gState.dashboard = nextDash;
        }
        setDashboard(nextDash);
        hydrateFromConfig(res.config);
      }
      showFeedback("Saved strategy config to database — Commander & API updated.", "ok");
      await loadDashboard({ refresh: false, preserveConfig: false, force: true });
    } catch (err: any) {
      setConfigDirty(true);
      showFeedback(`Failed saving config: ${err.message}`, feedbackKindForError(err));
    } finally {
      if (gState) {
        gState.configSaving = Math.max(0, (gState.configSaving || 0) - 1);
      }
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

  const researchSortCols = useMemo<
    SortColumnMap<
      UniverseResearchMaxRow,
      "research" | "ownLevel" | "uniMax" | "holder" | "catalogCap" | "updated"
    >
  >(
    () => ({
      research: (row) => String(row.name || row.key || row.research_key || ""),
      ownLevel: {
        accessor: (row) => Math.max(0, Math.round(safeFiniteNumber(row.own_level, 0) ?? 0)),
        defaultDirection: "desc",
      },
      uniMax: {
        accessor: (row) =>
          Math.max(0, Math.round(safeFiniteNumber(row.universe_max_level, 0) ?? 0)),
        defaultDirection: "desc",
      },
      holder: (row) =>
        [row.holder, row.holder_coords ? `[${row.holder_coords}]` : ""]
          .filter(Boolean)
          .join(" ") || null,
      catalogCap: {
        accessor: (row) => {
          const cap = safeFiniteNumber(row.catalog_max_level, null);
          return cap !== null && cap > 0 ? Math.round(cap) : null;
        },
        defaultDirection: "desc",
      },
      updated: {
        accessor: (row) => timestampSortKey(row.updated_ts),
        defaultDirection: "desc",
      },
    }),
    []
  );

  const researchSort = useTableSort(researchRows, researchSortCols);

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
          <section className="config-group" aria-labelledby="cfg-grp-execution-mode">
            <div className="config-group-head">
              <h3 id="cfg-grp-execution-mode">Runtime &amp; Autopilot Execution</h3>
                <p className="config-group-sub">
                Override how the Commander loop executes ticks against Chrome without restarting the stack. Strategy configuration is persisted directly in the active database.
              </p>
            </div>
            <div className="config-list">
              <div className="config-item config-item-mode">
                <div className="config-item-info">
                  <div className="config-item-head">
                    <label className="config-item-label" id="mode-group-label">
                      Execution Mode
                    </label>
                    <code className="config-item-key tabular">mode_override</code>
                    <span className="config-item-bounds tabular">auto | live | dry-run</span>
                  </div>
                  <p className="config-item-desc">
                    Controls whether the Commander autopilot dispatches live game clicks or runs in simulation: CLI Default inherits launcher flags, Force Live executes real actions, and Force Dry-Run simulates every tick safely.
                  </p>
                </div>
                <div className="config-item-control">
                  <div
                    className="mode-stack config-mode-stack"
                    role="radiogroup"
                    aria-labelledby="mode-group-label"
                  >
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
              </div>

              <div className="config-item config-item-mode" id="cfg-item-db-backend">
                <div className="config-item-info">
                  <div className="config-item-head">
                    <span className="config-item-label">Active Database</span>
                    <code className="config-item-key tabular">
                      {activeDbBackend === "postgres" ? "postgresql (docker)" : "sqlite (local)"}
                    </code>
                  </div>
                  <p className="config-item-desc">
                    Docker / Podman Compose runs exclusively on PostgreSQL (<code>./data/postgres</code>), while containerless <code>./starter.sh</code> uses local SQLite (<code>state/spaceinvasion.db</code>). Sync both databases anytime with <code>./scripts/sync_db.py</code>. Active target:{" "}
                    <code className="tabular">{activeDbTarget}</code>
                  </p>
                </div>
                <div className="config-item-control">
                  <span className="nav-badge tabular" id="badge-active-db-backend">
                    {activeDbBackend === "postgres" ? "POSTGRESQL · DOCKER" : "SQLITE · LOCAL"}
                  </span>
                </div>
              </div>
            </div>
          </section>

          {CONFIG_GROUPS.map((group) => (
            <section
              key={group.id}
              className="config-group"
              aria-labelledby={group.headingId}
            >
              <div className="config-group-head">
                <div className="config-group-title-row">
                  <h3 id={group.headingId}>{group.title}</h3>
                  {group.id === "galaxy" && dashboard?.galaxy_sweep ? (
                    <span
                      className="gal-cov-inline tabular"
                      id="cfg-galaxy-coverage"
                    >
                      {`GAL ${Math.max(
                        0,
                        Math.min(100, safeFiniteNumber(dashboard.galaxy_sweep.coverage_pct, 0) ?? 0)
                      ).toFixed(1)}% (${Math.round(
                        safeFiniteNumber(dashboard.galaxy_sweep.explored_systems, 0) ?? 0
                      )}/${Math.round(
                        safeFiniteNumber(dashboard.galaxy_sweep.total_systems, 400) ?? 400
                      )}) · UNI ${Math.max(
                        0,
                        Math.min(
                          100,
                          safeFiniteNumber(
                            dashboard.galaxy_sweep.universe_coverage_pct,
                            ((safeFiniteNumber(
                              dashboard.galaxy_sweep.universe_explored_systems ??
                                dashboard.galaxy_sweep.explored_systems,
                              0
                            ) ?? 0) /
                              Math.max(
                                1,
                                safeFiniteNumber(dashboard.galaxy_sweep.universe_total_systems, 5600) ?? 5600
                              )) *
                              100
                          ) ?? 0
                        )
                      ).toFixed(1)}% (${Math.round(
                        safeFiniteNumber(
                          dashboard.galaxy_sweep.universe_explored_systems ??
                            dashboard.galaxy_sweep.explored_systems,
                          0
                        ) ?? 0
                      )}/${Math.round(
                        safeFiniteNumber(dashboard.galaxy_sweep.universe_total_systems, 5600) ?? 5600
                      )}) · ${
                        String(dashboard.galaxy_sweep.scan_phase || "").toLowerCase() ===
                        "targeted_refresh"
                          ? "Targeted Refresh"
                          : "Exploratory"
                      }`}
                    </span>
                  ) : null}
                </div>
                <p className="config-group-sub">{group.subtitle}</p>
              </div>
              <div className="config-list">
                {group.items.map((item) => (
                  <div key={item.id} className="config-item">
                    <div className="config-item-info">
                      <div className="config-item-head">
                        <label className="config-item-label" htmlFor={item.id}>
                          {item.title}
                        </label>
                        <code className="config-item-key tabular">{item.key}</code>
                        <span className="config-item-bounds tabular">{item.boundsLabel}</span>
                      </div>
                      <p className="config-item-desc">{item.description}</p>
                    </div>
                    <div className="config-item-control">
                      {item.control === "select" ? (
                        <select
                          id={item.id}
                          value={String(formValues[item.key] ?? item.defaultValue ?? "")}
                          onChange={(e) => markFieldChanged(item.key, e.target.value)}
                          onInput={(e) =>
                            markFieldChanged(item.key, (e.target as HTMLSelectElement).value)
                          }
                        >
                          {(item.options || []).map((opt) => (
                            <option key={opt.value} value={opt.value}>
                              {opt.label}
                            </option>
                          ))}
                        </select>
                      ) : item.control === "planet-select" ? (
                        <select
                          id={item.id}
                          value={String(formValues[item.key] ?? "0")}
                          onChange={(e) => markFieldChanged(item.key, e.target.value)}
                          onInput={(e) =>
                            markFieldChanged(item.key, (e.target as HTMLSelectElement).value)
                          }
                        >
                          <option value="0">
                            0 — Auto (Active / Closest Colony with Probes)
                          </option>
                          {nonRaidPlanets.map((p) => (
                            <option key={p.planet_id} value={String(p.planet_id)}>
                              {`${p.name || "Colony"} [${p.coords || "--"}] (#${p.planet_id} · s10: ${p.ships?.s10 ?? 0})`}
                            </option>
                          ))}
                          {!hasSavedPlanetOption ? (
                            <option value={String(savedSpyPid)}>
                              {`Colony #${savedSpyPid} (Saved)`}
                            </option>
                          ) : null}
                        </select>
                      ) : item.control === "checkbox" ? (
                        <input
                          type="checkbox"
                          className="config-checkbox"
                          id={item.id}
                          checked={Boolean(formValues[item.key])}
                          onChange={(e) => markFieldChanged(item.key, e.target.checked)}
                        />
                      ) : item.control === "text-seed" ? (
                        <input
                          type="text"
                          inputMode="numeric"
                          className="config-seed-input"
                          id={item.id}
                          placeholder={item.placeholder || "Auto"}
                          value={String(formValues[item.key] ?? "")}
                          onChange={(e) => markFieldChanged(item.key, e.target.value)}
                          onInput={(e) =>
                            markFieldChanged(item.key, (e.target as HTMLInputElement).value)
                          }
                        />
                      ) : (
                        <input
                          type="number"
                          id={item.id}
                          min={item.min}
                          max={item.max}
                          step={item.step}
                          placeholder={item.placeholder}
                          value={String(formValues[item.key] ?? item.defaultValue ?? "")}
                          onChange={(e) => markFieldChanged(item.key, e.target.value)}
                          onInput={(e) =>
                            markFieldChanged(item.key, (e.target as HTMLInputElement).value)
                          }
                        />
                      )}
                    </div>
                  </div>
                ))}
              </div>
            </section>
          ))}

          {/* 6th Section: Persisted Universe Max Research Levels in SQLite (universe_research_max) */}
          <section
            className="config-research-section"
            id="cfg-universe-research-section"
            aria-labelledby="cfg-grp-univ-research"
          >
            <div className="config-group-head">
              <h3 id="cfg-grp-univ-research">
                Universe Max Research Levels — Persisted in SQLite (universe_research_max)
              </h3>
              <p className="config-group-sub">
                Highest observed research levels across server statistics, archived espionage reports, and your empire, stored monotonically in SQLite table universe_research_max. Click Refresh Max Research above to sync and persist the latest records.
              </p>
            </div>
            <div className="table-wrap">
              <table id="cfg-universe-research-max-table">
                <thead>
                  <tr>
                    <SortableTh
                      label="Research"
                      sortKey="research"
                      activeKey={researchSort.sortKey}
                      direction={researchSort.sortDirection}
                      onSort={researchSort.requestSort}
                    />
                    <SortableTh
                      label="Your Level"
                      sortKey="ownLevel"
                      activeKey={researchSort.sortKey}
                      direction={researchSort.sortDirection}
                      onSort={researchSort.requestSort}
                      align="right"
                    />
                    <SortableTh
                      label="Universe Max"
                      sortKey="uniMax"
                      activeKey={researchSort.sortKey}
                      direction={researchSort.sortDirection}
                      onSort={researchSort.requestSort}
                      align="right"
                    />
                    <SortableTh
                      label="Record Holder"
                      sortKey="holder"
                      activeKey={researchSort.sortKey}
                      direction={researchSort.sortDirection}
                      onSort={researchSort.requestSort}
                    />
                    <SortableTh
                      label="Catalog Cap"
                      sortKey="catalogCap"
                      activeKey={researchSort.sortKey}
                      direction={researchSort.sortDirection}
                      onSort={researchSort.requestSort}
                      align="right"
                    />
                    <SortableTh
                      label="Updated"
                      sortKey="updated"
                      activeKey={researchSort.sortKey}
                      direction={researchSort.sortDirection}
                      onSort={researchSort.requestSort}
                      align="right"
                    />
                  </tr>
                </thead>
                <tbody id="cfg-universe-research-max-tbody">
                  {researchSort.sortedRows.length === 0 ? (
                    <tr>
                      <td colSpan={6} className="empty-cell">
                        Click Refresh Max Research to populate and persist universe research records in SQLite.
                      </td>
                    </tr>
                  ) : (
                    researchSort.sortedRows.map((row) => {
                      const rKey = String(row.key || row.research_key || `r${row.research_id}`);
                      const ownLvl = Math.max(0, Math.round(safeFiniteNumber(row.own_level, 0) ?? 0));
                      const maxLvl = Math.max(
                        0,
                        Math.round(safeFiniteNumber(row.universe_max_level, 0) ?? 0)
                      );
                      const capLvl = safeFiniteNumber(row.catalog_max_level, null);
                      const holderTxt = [row.holder, row.holder_coords ? `[${row.holder_coords}]` : ""]
                        .filter(Boolean)
                        .join(" ") || "—";
                      return (
                        <tr key={rKey}>
                          <td>
                            <span className="action-title">{row.name}</span>{" "}
                            <span className="action-sub tabular">({rKey})</span>
                          </td>
                          <td className="col-right tabular">{`L${ownLvl}`}</td>
                          <td className="col-right tabular">{`L${maxLvl}`}</td>
                          <td>{holderTxt}</td>
                          <td className="col-right tabular">
                            {capLvl !== null && capLvl > 0 ? `L${Math.round(capLvl)}` : "Uncapped"}
                          </td>
                          <td className="col-right tabular">{fmtShortUpdated(row.updated_ts)}</td>
                        </tr>
                      );
                    })
                  )}
                </tbody>
              </table>
            </div>
          </section>
        </div>
      </article>
    </section>
  );
};

export default ConfigView;
