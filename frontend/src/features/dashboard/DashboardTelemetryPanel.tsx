import React from "react";
import { finiteNum, fmtInt, fmtRate } from "../../utils/formatters";

interface ResourceTrackSpec {
  idKey: "pig" | "metal" | "kryp" | "spice";
  code: string;
  name: string;
  amount: unknown;
  storage: unknown;
  hourlyRate: unknown;
  hasPlanet: boolean;
}

const ResourceTrackRow: React.FC<ResourceTrackSpec> = ({
  idKey,
  code,
  name,
  amount,
  storage,
  hourlyRate,
  hasPlanet,
}) => {
  if (!hasPlanet) {
    return (
      <div className="track-row">
        <div className="track-meta">
          <span className="track-code tabular">{code}</span>
          <span className="track-name">{name}</span>
        </div>
        <div className="track-bar-group">
          <div className="meter-track">
            <div className="meter-fill" id={`bar-${idKey}`} />
          </div>
        </div>
        <div className="track-values tabular">
          <span className="track-val" id={`val-${idKey}`}>
            —
          </span>
          <span className="track-cap" id={`cap-${idKey}`}>
            —
          </span>
          <span className="track-rate" id={`rate-${idKey}`}>
            —/h
          </span>
        </div>
      </div>
    );
  }

  const cap = finiteNum(storage, 0);
  const val = finiteNum(amount, null);
  const ratioPct = cap > 0 && val !== null ? (val / cap) * 100 : 0;
  const pct = Number.isFinite(ratioPct) ? Math.max(0, Math.min(100, Math.round(ratioPct))) : 0;

  const rateNum = hourlyRate == null ? null : finiteNum(hourlyRate, null);
  const rateText = hourlyRate == null ? "—/h" : fmtRate(hourlyRate);
  const rateNeg = hourlyRate != null && rateNum !== null && Math.round(rateNum) < 0;

  return (
    <div className="track-row">
      <div className="track-meta">
        <span className="track-code tabular">{code}</span>
        <span className="track-name">{name}</span>
      </div>
      <div className="track-bar-group">
        <div className="meter-track">
          <div
            className={`meter-fill${pct >= 85 ? " high" : ""}`}
            id={`bar-${idKey}`}
            style={{ transform: `scaleX(${pct / 100})` }}
          />
        </div>
      </div>
      <div className="track-values tabular">
        <span className="track-val" id={`val-${idKey}`}>
          {fmtInt(amount ?? 0)}
        </span>
        <span className="track-cap" id={`cap-${idKey}`}>
          {cap > 0 ? `/ ${fmtInt(cap)} (${pct}%)` : "—"}
        </span>
        <span className={`track-rate${rateNeg ? " is-neg" : ""}`} id={`rate-${idKey}`}>
          {rateText}
        </span>
      </div>
    </div>
  );
};

export interface DashboardTelemetryPanelProps {
  sectorTitleText: string;
  hasResourceScope: boolean;
  scopedResources: {
    pig_iron: unknown;
    metal: unknown;
    kryptonite: unknown;
    spice: unknown;
  };
  scopedStorage: {
    pig_iron: unknown;
    metal: unknown;
    kryptonite: unknown;
    spice: unknown;
  };
  scopedEcon: Record<string, any> | null;
  energyValText: string;
  energyRatioText: string;
  dashRankText: string;
  dashAzText: string;
  dashPtsHrText: string;
  dashSpiceHrText: string;
}

export const DashboardTelemetryPanel: React.FC<DashboardTelemetryPanelProps> = ({
  sectorTitleText,
  hasResourceScope,
  scopedResources,
  scopedStorage,
  scopedEcon,
  energyValText,
  energyRatioText,
  dashRankText,
  dashAzText,
  dashPtsHrText,
  dashSpiceHrText,
}) => (
  <article className="hud-window">
    <div className="window-bar">
      <div className="window-title-group">
        <span className="module-code tabular">SEC</span>
        <h2>Resources &amp; Hourly Net Flow</h2>
      </div>
      <span className="module-meta tabular" id="command-subpath">
        <span id="planet-title">{sectorTitleText}</span>
      </span>
    </div>

    <div className="telemetry-core">
      <div className="track-list" aria-label="Planetary resource levels and net hourly flow">
        <ResourceTrackRow
          key="pig"
          idKey="pig"
          code="PIG"
          name="Pig Iron"
          amount={scopedResources.pig_iron}
          storage={scopedStorage.pig_iron}
          hourlyRate={scopedEcon?.pig_iron_per_hr}
          hasPlanet={hasResourceScope}
        />
        <ResourceTrackRow
          key="metal"
          idKey="metal"
          code="MET"
          name="Metal"
          amount={scopedResources.metal}
          storage={scopedStorage.metal}
          hourlyRate={scopedEcon?.metal_per_hr}
          hasPlanet={hasResourceScope}
        />
        <ResourceTrackRow
          key="kryp"
          idKey="kryp"
          code="KRY"
          name="Kryptonite"
          amount={scopedResources.kryptonite}
          storage={scopedStorage.kryptonite}
          hourlyRate={scopedEcon?.kryptonite_per_hr}
          hasPlanet={hasResourceScope}
        />
        <ResourceTrackRow
          key="spice"
          idKey="spice"
          code="SPI"
          name="Spice"
          amount={scopedResources.spice}
          storage={scopedStorage.spice}
          hourlyRate={scopedEcon?.spice_per_hr}
          hasPlanet={hasResourceScope}
        />
      </div>

      <div className="digital-readout-row">
        <div className="digital-counter">
          <div className="digital-counter-top">
            <span className="digital-tag tabular">ENG</span>
            <span className="digital-number tabular" id="val-energy">
              {energyValText}
            </span>
          </div>
          <span className="digital-sub tabular" id="energy-ratio">
            {energyRatioText}
          </span>
        </div>
        <div className="digital-counter">
          <div className="digital-counter-top">
            <span className="digital-tag tabular">RNK</span>
            <span className="digital-number tabular" id="dash-rank-val">
              {dashRankText}
            </span>
          </div>
          <span className="digital-sub tabular" id="dash-az-val">
            {dashAzText}
          </span>
        </div>
        <div className="digital-counter">
          <div className="digital-counter-top">
            <span className="digital-tag tabular">FLW</span>
            <span className="digital-number tabular" id="dash-pts-hr">
              {dashPtsHrText}
            </span>
          </div>
          <span className="digital-sub tabular" id="dash-spice-hr">
            {dashSpiceHrText}
          </span>
        </div>
      </div>
    </div>
  </article>
);
