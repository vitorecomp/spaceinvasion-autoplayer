import React, { useMemo } from "react";
import { useConsole } from "../../context/ConsoleContext";
import { fmtInt, fmtShortDateTime } from "../../utils/formatters";

export function formatDbSummary(db: Record<string, any> | null | undefined): string {
  if (!db) return "SQLite database disabled (no persistent state directory configured).";
  const sch = db.schema || null;
  const verTag = sch?.version_tag || `V${String(sch?.version || 0).padStart(3, "0")}`;
  const targetTag =
    sch?.target_version_tag || `V${String(sch?.target_version || 0).padStart(3, "0")}`;
  const driftPart =
    Array.isArray(sch?.checksum_drift) && sch.checksum_drift.length > 0
      ? ` · DRIFT(${sch.checksum_drift.length})`
      : "";
  const schemaLine = sch
    ? `Schema Version: ${verTag} / ${targetTag} (${sch.up_to_date ? "UP-TO-DATE" : `${sch.pending_count || 0} PENDING`}) · Checksum=${String(sch.schema_checksum || "").slice(0, 12) || "n/a"}${driftPart}`
    : "Schema Version: legacy";

  const lines: string[] = [
    `DB Path: ${db.db_path} (${fmtInt(db.size_bytes)} bytes)`,
    schemaLine,
    `Row Counts: ${Object.entries(db.counts || {})
      .map(([k, v]) => `${k}=${v}`)
      .join(" · ")}`,
  ];

  if (sch && Array.isArray(sch.applied_migrations) && sch.applied_migrations.length > 0) {
    lines.push("", "== Applied Schema Migrations ==");
    for (const m of sch.applied_migrations) {
      lines.push(`${m.version_tag} — ${m.name} (${fmtShortDateTime(m.applied_ts)})`);
    }
  }

  lines.push("", "== Tutorial Progress ==");
  const tutHistory = Array.isArray(db.tutorial_history) ? db.tutorial_history : [];
  for (const t of tutHistory) {
    const status = t.completed_ts ? "DONE" : t.can_complete ? "READY TO CLAIM" : "ACTIVE";
    const tStr =
      (Array.isArray(t.tasks) ? t.tasks : []).map((x: any) => x.description).join(" | ") ||
      "(no tasks parsed)";
    lines.push(`Step ${t.step}/${t.total_steps} [${status}] ${tStr}`);
  }
  if (tutHistory.length === 0) {
    lines.push("(no tutorial rows recorded)");
  }

  lines.push("", "== Recent Actions ==");
  const recentActions = Array.isArray(db.recent_actions) ? db.recent_actions : [];
  for (const a of recentActions) {
    const mode = a.dry_run ? "DRY" : a.executed ? "EXEC" : "SKIP";
    const lvlPart = a.target_level != null ? ` -> ${a.target_level}` : "";
    lines.push(
      `[${mode}] ${a.kind} ${a.target_name || a.command}${lvlPart} @ ${a.planet_coords || "-"} — ${a.message || ""}`
    );
  }

  return lines.join("\n");
}

export const LogsView: React.FC = () => {
  const { dashboard, activePage, loadDashboard, uiPrefs, setUiPref } = useConsole();
  const activeLogSource = uiPrefs.activeLogSource || "commander";

  const logText = useMemo(() => {
    if (!dashboard) {
      return "Waiting for runtime log output...";
    }
    if (activeLogSource === "sqlite") {
      return formatDbSummary(dashboard.db_summary);
    }
    const src =
      activeLogSource === "game_api" ? dashboard.logs?.game_api : dashboard.logs?.commander;
    return src || "(Log file empty or not created yet.)";
  }, [activeLogSource, dashboard]);

  return (
    <section
      id="view-logs"
      className="page-view"
      aria-label="Live runtime logs"
      hidden={activePage !== "logs"}
    >
      <article className="hud-window page-fill">
        <div className="window-bar window-bar-wrap">
          <div className="window-title-group">
            <span className="module-code tabular">LOG</span>
            <h2>Live Runtime Terminal Output</h2>
          </div>
          <div className="inline-actions">
            <div className="tab-group" role="tablist" aria-label="Log source tabs">
              <button
                type="button"
                role="tab"
                aria-selected={activeLogSource === "commander"}
                className={`tab-btn${activeLogSource === "commander" ? " active" : ""}`}
                data-log="commander"
                onClick={() => setUiPref("activeLogSource", "commander")}
              >
                commander.log
              </button>
              <button
                type="button"
                role="tab"
                aria-selected={activeLogSource === "game_api"}
                className={`tab-btn${activeLogSource === "game_api" ? " active" : ""}`}
                data-log="game_api"
                onClick={() => setUiPref("activeLogSource", "game_api")}
              >
                game_api.log
              </button>
              <button
                type="button"
                role="tab"
                aria-selected={activeLogSource === "sqlite"}
                className={`tab-btn${activeLogSource === "sqlite" ? " active" : ""}`}
                data-log="sqlite"
                onClick={() => setUiPref("activeLogSource", "sqlite")}
              >
                SQLite DB
              </button>
            </div>
            <button
              type="button"
              className="btn btn-secondary btn-sm"
              id="btn-refresh-logs"
              onClick={() => loadDashboard({ refresh: false, force: true })}
            >
              Refresh
            </button>
          </div>
        </div>

        <div className="logs-fill">
          <pre id="logs-pre" className="console-box console-fill">
            {logText}
          </pre>
        </div>
      </article>
    </section>
  );
};

export default LogsView;
