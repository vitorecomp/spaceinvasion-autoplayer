import React, { useEffect, useMemo, useState } from "react";
import { BUILDING_NAMES } from "./plannerHelpers";

export interface EditablePlanetModalModel {
  slot: number;
  planetId: number;
  name: string;
  gameName: string;
  hasCustomName: boolean;
  coords: string;
  isPlaceholder: boolean;
  isCentral: boolean;
  temperature: number | null;
  fieldsUsedCurrent: number | null;
  fieldsMax: number;
  detectedSpecialization: string;
  lockedSpecialization: string;
  manualPinned?: boolean;
  builtLevels: Record<string, number>;
  targetLevels: Record<string, number>;
  reservedFloors: Record<string, number>;
}

export interface PlanetModalSavePayload {
  slot: number;
  planetId: number;
  coords: string;
  planetName: string;
  lockedSpecialization: string;
  fieldsMaxOverride: number;
  targetBuildings: Record<string, number>;
  lockForGa: boolean;
  autoWarehouses: boolean;
}

export interface ConfigOptimizerPlanetModalProps {
  planet: EditablePlanetModalModel | null;
  specializationOptions: ReadonlyArray<{ key: string; label: string }>;
  isSaving: boolean;
  isSyncingName: boolean;
  onClose: () => void;
  onSave: (payload: PlanetModalSavePayload) => Promise<void> | void;
  onSyncSingleName: (planetId: number, name: string) => Promise<void> | void;
}

interface BuildingFieldGroup {
  id: string;
  title: string;
  keys: ReadonlyArray<string>;
}

const NORMAL_PLANET_BUILDING_GROUPS: ReadonlyArray<BuildingFieldGroup> = [
  {
    id: "mines",
    title: "Mines & Power",
    keys: ["b1", "b2", "b3", "b4", "b5", "b6"],
  },
  {
    id: "shipyard",
    title: "Shipyard, Research & Speedup",
    keys: ["b7", "b8", "b9", "b10", "b17"],
  },
  {
    id: "warehouses",
    title: "Resource Warehouses",
    keys: ["b11", "b12", "b13", "b14"],
  },
  {
    id: "special",
    title: "Planetary Defense & Special Facilities",
    keys: ["b15", "b16", "b18", "b20", "b21"],
  },
];

const BUILDING_MAX_LEVELS: Record<string, number> = {
  b1: 100,
  b2: 100,
  b3: 100,
  b4: 100,
  b5: 100,
  b6: 100,
  b7: 60,
  b8: 40,
  b9: 60,
  b10: 55,
  b11: 60,
  b12: 60,
  b13: 60,
  b14: 60,
  b15: 50,
  b16: 45,
  b17: 55,
  b18: 40,
  b20: 40,
  b21: 30,
};

const ALL_EDITABLE_BUILDING_KEYS: ReadonlyArray<string> = NORMAL_PLANET_BUILDING_GROUPS.flatMap(
  (g) => g.keys
);

function clampInt(val: unknown, lo: number, hi: number, fallback = 0): number {
  const num = Number(val);
  if (!Number.isFinite(num)) return fallback;
  return Math.max(lo, Math.min(hi, Math.round(num)));
}

export const ConfigOptimizerPlanetModal: React.FC<ConfigOptimizerPlanetModalProps> = ({
  planet,
  specializationOptions,
  isSaving,
  isSyncingName,
  onClose,
  onSave,
  onSyncSingleName,
}) => {
  const [nameDraft, setNameDraft] = useState<string>("");
  const [specDraft, setSpecDraft] = useState<string>("auto");
  const [fieldsMaxDraft, setFieldsMaxDraft] = useState<number>(300);
  const [lockForGa, setLockForGa] = useState<boolean>(true);
  const [autoWarehouses, setAutoWarehouses] = useState<boolean>(false);
  const [levelsDraft, setLevelsDraft] = useState<Record<string, number>>({});
  const [localError, setLocalError] = useState<string | null>(null);

  const planetIdentityKey = planet
    ? `${planet.slot}:${planet.planetId}:${planet.coords}`
    : "";

  useEffect(() => {
    if (!planet) return;
    setNameDraft(planet.name || "");
    setSpecDraft(planet.lockedSpecialization || "auto");
    setFieldsMaxDraft(Math.max(1, planet.fieldsMax || 300));
    setLockForGa(true);
    setAutoWarehouses(false);
    const nextLvls: Record<string, number> = {};
    for (const bk of ALL_EDITABLE_BUILDING_KEYS) {
      const maxL = BUILDING_MAX_LEVELS[bk] ?? 100;
      const built = clampInt(planet.builtLevels[bk], 0, maxL, 0);
      const tgt =
        bk in planet.targetLevels
          ? clampInt(planet.targetLevels[bk], 0, maxL, built)
          : built;
      nextLvls[bk] = tgt;
    }
    setLevelsDraft(nextLvls);
    setLocalError(null);
    // Only initialize draft inputs when opening a planet modal, never on background poll re-renders
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [planetIdentityKey]);

  useEffect(() => {
    if (!planet) return;
    const onKey = (ev: KeyboardEvent) => {
      if (ev.key === "Escape" && !isSaving && !isSyncingName) {
        onClose();
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [isSaving, isSyncingName, onClose, planet]);

  const metrics = useMemo(() => {
    if (!planet) {
      return {
        fieldsUsed: 0,
        fieldsFree: 0,
        builtTotal: 0,
        toBuild: 0,
        toDemolish: 0,
        overFields: 0,
      };
    }
    let fieldsUsed = 0;
    let builtTotal = 0;
    let toBuild = 0;
    let toDemolish = 0;
    for (const bk of ALL_EDITABLE_BUILDING_KEYS) {
      const built = clampInt(planet.builtLevels[bk], 0, 200, 0);
      const tgt = clampInt(levelsDraft[bk], 0, BUILDING_MAX_LEVELS[bk] ?? 100, 0);
      fieldsUsed += tgt;
      builtTotal += built;
      if (tgt > built) toBuild += tgt - built;
      if (tgt < built) toDemolish += built - tgt;
    }
    const fieldsFree = fieldsMaxDraft - fieldsUsed;
    return {
      fieldsUsed,
      fieldsFree,
      builtTotal,
      toBuild,
      toDemolish,
      overFields: Math.max(0, fieldsUsed - fieldsMaxDraft),
    };
  }, [fieldsMaxDraft, levelsDraft, planet]);

  if (!planet) return null;

  const trimmedName = nameDraft.replace(/\s+/g, " ").trim();
  const canSyncInGame = !planet.isPlaceholder && planet.planetId > 0;
  const isNameUnsynced =
    canSyncInGame &&
    Boolean(trimmedName) &&
    trimmedName !== (planet.gameName || "").trim();

  const updateBuildingLevel = (bk: string, rawVal: number) => {
    const maxL = BUILDING_MAX_LEVELS[bk] ?? 100;
    let nextVal = clampInt(rawVal, 0, maxL, 0);
    setLevelsDraft((prev) => {
      const updated = { ...prev, [bk]: nextVal };
      // Auto-satisfy basic same-planet Development Center prerequisites when raising b9 or b17
      if (bk === "b17" && nextVal >= 1 && (updated.b7 ?? 0) < 12) {
        updated.b7 = 12;
      }
      if (bk === "b9" && nextVal >= 1 && (updated.b7 ?? 0) < 2) {
        updated.b7 = 2;
      }
      return updated;
    });
    if (localError) setLocalError(null);
  };

  const onResetToBuilt = () => {
    const nextLvls: Record<string, number> = {};
    for (const bk of ALL_EDITABLE_BUILDING_KEYS) {
      nextLvls[bk] = clampInt(planet.builtLevels[bk], 0, BUILDING_MAX_LEVELS[bk] ?? 100, 0);
    }
    setLevelsDraft(nextLvls);
    setLocalError(null);
  };

  const onRestorePlanTargets = () => {
    const nextLvls: Record<string, number> = {};
    for (const bk of ALL_EDITABLE_BUILDING_KEYS) {
      const maxL = BUILDING_MAX_LEVELS[bk] ?? 100;
      const built = clampInt(planet.builtLevels[bk], 0, maxL, 0);
      nextLvls[bk] =
        bk in planet.targetLevels
          ? clampInt(planet.targetLevels[bk], 0, maxL, built)
          : built;
    }
    setLevelsDraft(nextLvls);
    setLocalError(null);
  };

  const handleSaveClick = async () => {
    if (!autoWarehouses && metrics.overFields > 0) {
      setLocalError(
        `Target configuration uses ${metrics.fieldsUsed}/${fieldsMaxDraft} fields (${metrics.overFields} over planet limit). Lower building levels or raise Max Fields.`
      );
      return;
    }
    if (trimmedName.length > 48) {
      setLocalError("Planet name must be 48 characters or fewer.");
      return;
    }
    setLocalError(null);
    await onSave({
      slot: planet.slot,
      planetId: planet.planetId,
      coords: planet.coords,
      planetName: trimmedName,
      lockedSpecialization: specDraft,
      fieldsMaxOverride: clampInt(fieldsMaxDraft, 1, 2000, planet.fieldsMax || 300),
      targetBuildings: levelsDraft,
      lockForGa,
      autoWarehouses,
    });
  };

  return (
    <div
      id="co-planet-edit-modal"
      className="notif-modal-backdrop"
      role="dialog"
      aria-modal="true"
      aria-labelledby="co-planet-modal-title"
      onClick={() => {
        if (!isSaving && !isSyncingName) onClose();
      }}
    >
      <div
        className="notif-modal-card ag-co-planet-modal-card"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="panel-head ag-co-modal-head">
          <div>
            <h2 id="co-planet-modal-title" className="tabular">
              {`Configure Planet #${planet.slot} — ${trimmedName || planet.name} [${planet.coords}]`}
            </h2>
            <span className="ag-toolbar-meta tabular">
              {planet.isPlaceholder
                ? "Preview Colony Slot"
                : `Planet ID ${planet.planetId}${planet.temperature !== null ? ` · ${planet.temperature}°C` : ""}${planet.isCentral ? " · ★ Central Hub" : ""}`}
            </span>
          </div>
          <button
            type="button"
            className="btn btn-secondary btn-sm"
            id="btn-co-modal-close"
            disabled={isSaving || isSyncingName}
            onClick={onClose}
          >
            Close
          </button>
        </div>

        <div className="ag-co-modal-body">
          {/* Identity, Specialization & Field Controls */}
          <div className="ag-co-modal-meta-grid">
            <div className="ag-co-modal-field">
              <label htmlFor="co-modal-name">Planet Name (UI &amp; In-Game Sync)</label>
              <div className="ag-co-modal-inline-row">
                <input
                  id="co-modal-name"
                  type="text"
                  maxLength={48}
                  value={nameDraft}
                  placeholder={planet.gameName || planet.coords}
                  disabled={planet.isPlaceholder || isSaving || isSyncingName}
                  onChange={(e) => {
                    setNameDraft(e.target.value);
                    if (localError) setLocalError(null);
                  }}
                />
                {canSyncInGame ? (
                  <button
                    type="button"
                    className={`btn ${isNameUnsynced ? "btn-primary" : "btn-secondary"} btn-sm`}
                    id="btn-co-modal-sync-name"
                    disabled={isSaving || isSyncingName || !trimmedName}
                    title="Update this planet's name inside SpaceInvasion Classic (internalSettings)"
                    onClick={() => void onSyncSingleName(planet.planetId, trimmedName)}
                  >
                    {isSyncingName ? "Syncing..." : "Sync to Game"}
                  </button>
                ) : null}
              </div>
              {canSyncInGame ? (
                <span
                  className={`ag-co-sync-hint tabular${isNameUnsynced ? " is-unsynced" : " is-synced"}`}
                  id="co-modal-game-name-status"
                >
                  {isNameUnsynced
                    ? `Unsynced · In-game name is currently "${planet.gameName || planet.coords}"`
                    : `In-game name: "${planet.gameName || trimmedName}"`}
                </span>
              ) : null}
            </div>

            <div className="ag-co-modal-field">
              <label htmlFor="co-modal-spec">Specialization Lock</label>
              <select
                id="co-modal-spec"
                value={specDraft}
                disabled={isSaving}
                onChange={(e) => setSpecDraft(e.target.value)}
              >
                {specializationOptions.map((opt) => (
                  <option key={opt.key} value={opt.key}>
                    {opt.label}
                  </option>
                ))}
              </select>
              <span className="ag-toolbar-meta tabular">
                {`Detected role: ${planet.detectedSpecialization || "balanced"}`}
              </span>
            </div>

            <div className="ag-co-modal-field">
              <label htmlFor="co-modal-fields-max">Max Planet Fields</label>
              <input
                id="co-modal-fields-max"
                type="number"
                min={50}
                max={2000}
                step={1}
                value={fieldsMaxDraft}
                disabled={isSaving}
                onChange={(e) =>
                  setFieldsMaxDraft(clampInt(e.target.value, 1, 2000, planet.fieldsMax || 300))
                }
              />
              <span className="ag-toolbar-meta tabular">
                {`Currently built: ${metrics.builtTotal} fields`}
              </span>
            </div>
          </div>

          {/* Live Field Budget & Options Bar */}
          <div className="ag-co-modal-kpi-bar tabular">
            <div className="ag-co-modal-kpi-group">
              <span
                className={`ag-co-spec-badge${metrics.overFields > 0 ? " is-changed" : " is-sticky"}`}
                id="co-modal-fields-summary"
              >
                {`Target Fields: ${metrics.fieldsUsed} / ${fieldsMaxDraft} (${metrics.fieldsFree >= 0 ? `${metrics.fieldsFree} free` : `${metrics.overFields} OVER`})`}
              </span>
              <span className="ag-co-spec-badge">
                {`+${metrics.toBuild} to build`}
              </span>
              {metrics.toDemolish > 0 ? (
                <span
                  className="ag-co-spec-badge is-changed"
                  title="Buildings set below built level are demolished only when the planet runs out of fields"
                >
                  {`-${metrics.toDemolish} below built (demolish @ full)`}
                </span>
              ) : null}
            </div>

            <div className="ag-co-modal-toggles">
              <label className="ag-co-modal-check">
                <input
                  id="co-modal-lock-ga"
                  type="checkbox"
                  checked={lockForGa}
                  onChange={(e) => setLockForGa(e.target.checked)}
                />
                <span>Pin config on future GA runs</span>
              </label>
              <label className="ag-co-modal-check">
                <input
                  id="co-modal-auto-wh"
                  type="checkbox"
                  checked={autoWarehouses}
                  onChange={(e) => setAutoWarehouses(e.target.checked)}
                />
                <span>Auto-size warehouses</span>
              </label>
              <button
                type="button"
                className="btn btn-secondary btn-sm"
                id="btn-co-modal-reset-built"
                disabled={isSaving}
                onClick={onResetToBuilt}
              >
                Set All to Built
              </button>
              <button
                type="button"
                className="btn btn-secondary btn-sm"
                id="btn-co-modal-restore-plan"
                disabled={isSaving}
                onClick={onRestorePlanTargets}
              >
                Revert Changes
              </button>
            </div>
          </div>

          {/* Building Groups */}
          <div className="ag-co-modal-groups">
            {NORMAL_PLANET_BUILDING_GROUPS.map((grp) => (
              <div key={grp.id} className="ag-co-modal-group">
                <h3 className="ag-co-modal-group-title">{grp.title}</h3>
                <div className="ag-co-modal-blds-grid">
                  {grp.keys.map((bk) => {
                    const maxL = BUILDING_MAX_LEVELS[bk] ?? 100;
                    const built = clampInt(planet.builtLevels[bk], 0, maxL, 0);
                    const tgt = clampInt(levelsDraft[bk], 0, maxL, 0);
                    const delta = tgt - built;
                    const reserved = clampInt(planet.reservedFloors[bk], 0, maxL, 0);
                    return (
                      <div
                        key={bk}
                        className={`ag-co-modal-bld-card${delta > 0 ? " is-up" : delta < 0 ? " is-down" : ""}`}
                      >
                        <div className="ag-co-modal-bld-top">
                          <span className="ag-co-modal-bld-name">
                            {BUILDING_NAMES[bk] || bk}
                          </span>
                          <span className="ag-param-key tabular">{bk}</span>
                        </div>
                        <div className="ag-co-modal-bld-sub tabular">
                          <span>{`Built L${built}`}</span>
                          {reserved > 0 ? <span>{`Floor ≥${reserved}`}</span> : null}
                          {delta !== 0 ? (
                            <span className={delta > 0 ? "text-mint" : "text-amber"}>
                              {delta > 0 ? `+${delta}` : `${delta}`}
                            </span>
                          ) : (
                            <span>—</span>
                          )}
                        </div>
                        <div className="ag-co-modal-stepper">
                          <button
                            type="button"
                            className="btn btn-secondary btn-sm"
                            id={`btn-co-bld-dec-${bk}`}
                            aria-label={`Decrease ${BUILDING_NAMES[bk] || bk}`}
                            disabled={isSaving || tgt <= 0}
                            onClick={() => updateBuildingLevel(bk, tgt - 1)}
                          >
                            -
                          </button>
                          <input
                            id={`co-modal-bld-${bk}`}
                            type="number"
                            className="tabular"
                            min={0}
                            max={maxL}
                            step={1}
                            aria-label={`Target level for ${BUILDING_NAMES[bk] || bk}`}
                            value={tgt}
                            disabled={isSaving}
                            onChange={(e) =>
                              updateBuildingLevel(bk, Number(e.target.value))
                            }
                          />
                          <button
                            type="button"
                            className="btn btn-secondary btn-sm"
                            id={`btn-co-bld-inc-${bk}`}
                            aria-label={`Increase ${BUILDING_NAMES[bk] || bk}`}
                            disabled={isSaving || tgt >= maxL}
                            onClick={() => updateBuildingLevel(bk, tgt + 1)}
                          >
                            +
                          </button>
                        </div>
                      </div>
                    );
                  })}
                </div>
              </div>
            ))}
          </div>

          {localError ? (
            <div className="ag-co-rename-error" id="co-modal-error" role="alert">
              {localError}
            </div>
          ) : null}
        </div>

        <div className="notif-modal-footer">
          <span className="ag-toolbar-meta">
            Saving updates the active Config Optimizer plan, Buildings tab goals, and AI-Planner queue.
          </span>
          <div className="inline-actions">
            <button
              type="button"
              className="btn btn-secondary"
              id="btn-co-modal-cancel"
              disabled={isSaving || isSyncingName}
              onClick={onClose}
            >
              Cancel
            </button>
            <button
              type="button"
              className="btn btn-primary"
              id="btn-co-modal-save"
              disabled={isSaving || isSyncingName || (!autoWarehouses && metrics.overFields > 0)}
              onClick={() => void handleSaveClick()}
            >
              {isSaving ? "Saving Config..." : "Save Planet Config"}
            </button>
          </div>
        </div>
      </div>
    </div>
  );
};
