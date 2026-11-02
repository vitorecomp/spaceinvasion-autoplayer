import React from "react";
import type { PlanetData } from "../../types/api";
import type { ConfigGroupSpec } from "./configSchema";
import { formatGalaxyCoverageText } from "./configFormHelpers";

export interface RuntimeExecutionSectionProps {
  modeVal: string;
  activeDbBackend: "postgres" | "sqlite";
  activeDbTarget: string;
  onModeChange: (mode: string) => void;
}

export const RuntimeExecutionSection: React.FC<RuntimeExecutionSectionProps> = ({
  modeVal,
  activeDbBackend,
  activeDbTarget,
  onModeChange,
}) => (
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
);

export interface ConfigGroupSectionProps {
  group: ConfigGroupSpec;
  galaxySweep?: Record<string, any> | null;
  formValues: Record<string, any>;
  nonRaidPlanets: PlanetData[];
  savedSpyPid: number;
  hasSavedPlanetOption: boolean;
  onFieldChange: (key: string, val: any) => void;
}

export const ConfigGroupSection: React.FC<ConfigGroupSectionProps> = ({
  group,
  galaxySweep,
  formValues,
  nonRaidPlanets,
  savedSpyPid,
  hasSavedPlanetOption,
  onFieldChange,
}) => (
  <section className="config-group" aria-labelledby={group.headingId}>
    <div className="config-group-head">
      <div className="config-group-title-row">
        <h3 id={group.headingId}>{group.title}</h3>
        {group.id === "galaxy" && galaxySweep ? (
          <span className="gal-cov-inline tabular" id="cfg-galaxy-coverage">
            {formatGalaxyCoverageText(galaxySweep)}
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
                onChange={(e) => onFieldChange(item.key, e.target.value)}
                onInput={(e) =>
                  onFieldChange(item.key, (e.target as HTMLSelectElement).value)
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
                onChange={(e) => onFieldChange(item.key, e.target.value)}
                onInput={(e) =>
                  onFieldChange(item.key, (e.target as HTMLSelectElement).value)
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
                onChange={(e) => onFieldChange(item.key, e.target.checked)}
              />
            ) : item.control === "text-seed" ? (
              <input
                type="text"
                inputMode="numeric"
                className="config-seed-input"
                id={item.id}
                placeholder={item.placeholder || "Auto"}
                value={String(formValues[item.key] ?? "")}
                onChange={(e) => onFieldChange(item.key, e.target.value)}
                onInput={(e) =>
                  onFieldChange(item.key, (e.target as HTMLInputElement).value)
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
                onChange={(e) => onFieldChange(item.key, e.target.value)}
                onInput={(e) =>
                  onFieldChange(item.key, (e.target as HTMLInputElement).value)
                }
              />
            )}
          </div>
        </div>
      ))}
    </div>
  </section>
);
