import React from "react";
import type { PlanetData } from "../../types/api";
import type { ConfigGroupSpec } from "./configSchema";
import { formatGalaxyCoverageText } from "./configFormHelpers";
import { SchemaSettingField } from "./SchemaSettingField";

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
            Podman Compose runs exclusively on PostgreSQL 16 (<code>./data/postgres</code>). Active target:{" "}
            <code className="tabular">{activeDbTarget}</code>
          </p>
        </div>
        <div className="config-item-control">
          <span className="nav-badge tabular" id="badge-active-db-backend">
            {activeDbBackend === "postgres" ? "POSTGRESQL · PODMAN" : "POSTGRESQL"}
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
        <SchemaSettingField
          key={item.id}
          variant="config"
          idPrefix="cfg"
          spec={item}
          value={formValues[item.key]}
          emitOnInput
          planetSelectContext={
            item.control === "planet-select"
              ? { nonRaidPlanets, savedSpyPid, hasSavedPlanetOption }
              : undefined
          }
          onChange={(val) => onFieldChange(item.key, val)}
        />
      ))}
    </div>
  </section>
);
